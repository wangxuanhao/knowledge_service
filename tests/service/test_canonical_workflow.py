"""Current discovery has one write protocol and no legacy read dependency."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from fastapi import FastAPI

from knowledge_service.api.ontology_discovery import install
from knowledge_service.services.ontology_discovery_workflow import OntologyDiscoveryWorkflow


class Repository:
    def __init__(self):
        self.run_reads = 0
        self.inputs = {
            'documents': [{'id': 'doc', 'kind': 'document', 'version_id': 'doc-v1',
                           'metadata': {'title': 'source', 'discovery_candidates': [
                               {'id': key, 'kind': 'entity', 'text': key, 'proposed_type': 'Thing'}
                               for key in ('pending', 'included', 'approved', 'materialized')]}}],
            'assertions': [],
            'materialized': [{'kind': 'entity', 'metadata': {
                'discovery_candidate_ids': ['materialized']}}],
        }
        self.runs = [
            {'id': 'obsolete', 'status': 'draft_created', 'unified_draft_id': 'old',
             'accepted_candidate_ids': ['pending']},
            {'id': 'current', 'intake_request_hash': 'current', 'status': 'draft_created',
             'unified_draft_id': 'draft', 'accepted_candidate_ids': ['included']},
            {'id': 'published', 'intake_request_hash': 'published', 'status': 'published',
             'unified_draft_id': 'published-draft', 'accepted_candidate_ids': ['approved']},
        ]

    def independent_read_view(self):
        return nullcontext(self)

    def list_intake_runs(self, project):
        self.run_reads += 1
        return [deepcopy(run) for run in self.runs if run.get('intake_request_hash')]

    def list_ontologies(self, project):
        return [{'id': 'ontology', 'turtle': ''}]

    def get_project(self, project):
        return {'id': project}

    def list_artifacts(self, *args, **kwargs):
        raise AssertionError('current page read obsolete discovery artifacts')

    def current_records(self, *args, **kwargs):
        raise AssertionError('current page scanned all project records')


def service(repository):
    return SimpleNamespace(repository=repository, data=repository,
                           ontology_drafts=lambda **kwargs: None)


class CanonicalWorkflowTests(unittest.TestCase):
    def test_review_history_requires_explicit_decision_supersession(self):
        from knowledge_service.services.ontology_drafts import OntologyDrafts
        first = {'id': 'first', 'operation_id': 'op'}
        second = {'id': 'second', 'operation_id': 'op'}
        with self.assertRaises(ValueError):
            OntologyDrafts._effective_decisions([first, second])
        self.assertEqual(OntologyDrafts._effective_decisions([
            first, {**second, 'supersedes_decision_id': 'first'}]),
            [{**second, 'supersedes_decision_id': 'first'}])

    def test_only_selected_intake_write_routes_are_installed(self):
        app = FastAPI()
        install(app, service(Repository()))
        routes = {(method.upper(), path) for path, methods in app.openapi()['paths'].items()
                  for method in methods}
        base = '/api/projects/{p}/ontology-discovery'
        for suffix in ('selection-preview', 'add-selection', 'confirm-reuse'):
            self.assertIn(('POST', base + '/' + suffix), routes)
        for method, suffix in [('POST', '/drafts'), ('POST', '/runs/{run_id}/finalize'),
                               ('POST', '/drafts/{draft_id}/publish'),
                               ('PUT', '/drafts/{draft_id}')]:
            self.assertNotIn((method, base + suffix), routes)

    def test_overview_ignores_obsolete_runs_without_reading_legacy_artifacts(self):
        repo = Repository()
        with patch('knowledge_service.repository.page_reads.PageReads.discovery_inputs',
                   return_value=deepcopy(repo.inputs)):
            result = OntologyDiscoveryWorkflow(service(repo)).overview('project')
        self.assertEqual(result['candidate_status_counts'], {
            'pending': 1, 'included_in_draft': 1, 'approved': 1, 'materialized': 1})
        self.assertEqual(result['latest_run']['id'], 'current')
        self.assertNotIn('drafts', result)
        self.assertEqual(repo.run_reads, 1)

    def test_mindmap_uses_same_narrow_snapshot_and_current_states(self):
        repo = Repository()
        with patch('knowledge_service.repository.page_reads.PageReads.discovery_inputs',
                   return_value=deepcopy(repo.inputs)):
            result = OntologyDiscoveryWorkflow(service(repo)).candidate_mindmap('project', 500)
        self.assertTrue(result['nodes'])
        self.assertEqual(repo.run_reads, 1)


if __name__ == '__main__':
    unittest.main()
