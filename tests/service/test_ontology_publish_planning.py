import copy
from contextlib import contextmanager

import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.ontology_drafts_review import (
    DraftReviewPublishMixin,
)
from knowledge_service.services.ontology_operations import operation_fingerprint


BASE = '''
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Child a owl:Class ; rdfs:label "Child" .
'''


@pytest.fixture
def postgres_backend():
    """Publication planning is source-only and must not require PostgreSQL."""
    yield


class MemoryStore:
    def __init__(self, draft):
        self.draft = draft

    def get(self, project_id, draft_id):
        assert (project_id, draft_id) == ('project-1', 'draft-1')
        return copy.deepcopy(self.draft)


class PublishPlanner(DraftReviewPublishMixin):
    def __init__(self):
        self.store = MemoryStore({
            'id': 'draft-1', 'project_id': 'project-1',
            'base_ontology_id': 'ontology-1', 'revision': 4,
            'status': 'pending', 'submitted_at': '2026-10-12T00:00:00Z',
            'source_kind': 'manual', 'source_context': {},
        })
        self.operation = {
            'id': 'operation-1',
            'action': 'add_annotation',
            'target_iri': 'https://example.test/Child',
            'before': {},
            'after': {
                'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
                'value': 'Published label', 'language': 'en',
            },
        }
        self.operation['fingerprint'] = operation_fingerprint(self.operation)

    def _assert_revision(self, draft, expected_revision):
        assert draft['revision'] == expected_revision

    def _check_current(self, *_args, **_kwargs):
        return None

    def _revert_base(self, *_args):
        return None

    def _require_validation(self, *_args, **_kwargs):
        return {'errors': [], 'warnings': []}, 'validation-fingerprint', None

    def _active_operations(self, *_args):
        return [copy.deepcopy(self.operation)]

    def _history(self, *_args):
        return [], [{
            'operation_id': self.operation['id'],
            'operation_fingerprint': self.operation['fingerprint'],
            'action': 'approve',
        }]

    def _effective_decisions(self, decisions):
        return decisions

    def _base_turtle(self, *_args):
        return BASE

    def _publication_effect_plan(self, *_args):
        return {'kind': 'manual'}


def _publication_plan():
    return PublishPlanner().publish_preflight(
        'project-1', 'draft-1', 4, 'ontology-1',
        'validation-fingerprint', [], 'publish-once', 'publisher',
        'publish planning test')


def test_preflight_hashes_summary_and_service_owned_reuse_decision():
    plan = _publication_plan()

    assert plan['reuse_version'] is True
    hashed = {
        key: value for key, value in plan.items()
        if key not in {'draft', 'validation_report', 'request_hash'}
    }
    assert plan['request_hash'] == Repository._publish_fingerprint(hashed)


@pytest.mark.parametrize(('field', 'tampered'), [
    ('summary', {'forged': True}),
    ('reuse_version', False),
])
def test_repository_rejects_tampered_executed_plan_fields(field, tampered):
    plan = _publication_plan()
    plan[field] = tampered
    repository = object.__new__(Repository)

    @contextmanager
    def unexpected_transaction():
        raise AssertionError('tampered plan reached the write transaction')
        yield

    repository._publication_transaction = unexpected_transaction

    with pytest.raises(ValueError, match='hash'):
        repository._publish_governed_ontology_draft(plan)
