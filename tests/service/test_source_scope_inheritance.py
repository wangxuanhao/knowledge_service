from copy import deepcopy
from types import SimpleNamespace
import pytest
from knowledge_service.domain.applicability import normalize_applicability
from knowledge_service.services.ontology_discovery import _materialize_candidates

SCOPE = normalize_applicability({'platforms': ['美团'], 'businesses': ['外卖']})

class Sources:
    def get_record_version(self, project_id, version_id):
        assert version_id == 'frozen'
        return {'id': 'doc', 'version_id': 'frozen', 'kind': 'document', 'metadata': {'applicability': SCOPE}}

def test_materialization_inherits_frozen_scope_without_changing_candidates_or_dates():
    candidates = [
        {'id': 'a', 'kind': 'entity', 'text': 'A', 'proposed_type': 'Thing'},
        {'id': 'b', 'kind': 'entity', 'text': 'B', 'proposed_type': 'Thing'},
        {'id': 'r', 'kind': 'relation', 'subject_id': 'a', 'object_id': 'b', 'proposed_type': 'link'},
        {'id': 'v', 'kind': 'attribute', 'entity_id': 'a', 'value': 12, 'proposed_type': 'price'},
    ]
    for row in candidates:
        row.update(document_id='doc', document_version_id='frozen', valid_from=None)
    draft = {'candidate_snapshot': candidates, 'mappings': {'entity_types': {'Thing':'urn:Thing'}, 'relation_types': {'link':'urn:link'}, 'attributes': {'price':'urn:price'}}}
    frozen = deepcopy(draft)
    records, skipped = _materialize_candidates('p', draft, 'ont', repository=Sources())
    assert not skipped
    assert len(records) == 4
    assert all(row['metadata']['applicability'] == SCOPE for row in records)
    assert all(row['valid_from'] is None for row in records)
    assert draft == frozen

def test_inheritance_preserves_explicit_scope_and_rejects_wrong_document():
    from knowledge_service.services.source_scope import inherit_source_scope
    row = {'metadata': {'applicability': {'platforms': ['京东']}}}
    inherit_source_scope(row, {'document_id':'doc', 'document_version_id':'frozen'}, Sources(), 'p')
    assert row['metadata']['applicability']['platforms'] == ['京东']
    with pytest.raises(ValueError, match='source document'):
        inherit_source_scope({'metadata': {}}, {'document_id':'other', 'document_version_id':'frozen'}, Sources(), 'p')

def test_unknown_source_is_not_guessed_from_candidate_name():
    from knowledge_service.services.source_scope import inherit_source_scope
    row = {'text':'美团', 'metadata':{}}
    inherit_source_scope(row, {}, Sources(), 'p')
    assert row['metadata']['applicability'] == normalize_applicability(None)

class RepairSources(Sources):
    def current_records(self, project_id, **kwargs):
        return [{'id':'fact','kind':'entity','version':1,'version_id':'f1','text':'Fact','metadata':{},'valid_from':None}]
    def list_assertions(self, project_id):
        return [{'id':'assertion','canonical_record_id':'fact','status':'accepted','decision_version':1,'document_id':'doc','document_version_id':'frozen'}]

def test_repair_proves_sources_and_keeps_expected_revisions():
    from knowledge_service.services.source_scope import build_scope_repair
    plan = build_scope_repair(RepairSources(), 'p')
    assert len(plan['repairs']) == 1
    repair = plan['repairs'][0]
    assert repair['expected_version_id'] == 'f1'
    assert repair['expected_version'] == 1
    assert repair['sources'][0]['document_version_id'] == 'frozen'
    assert repair['applicability'] == SCOPE

def test_repair_does_not_union_conflicting_or_unproven_sources():
    from knowledge_service.services.source_scope import build_scope_repair
    class Conflicting(RepairSources):
        def list_assertions(self, project_id):
            first = super().list_assertions(project_id)[0]
            return [first, {**first,'id':'second','document_version_id':'different'}]
        def get_record_version(self, project_id, version_id):
            if version_id == 'different':
                return {'id':'doc','kind':'document','metadata':{'applicability':{'platforms':['京东']}}}
            return super().get_record_version(project_id, version_id)
    plan = build_scope_repair(Conflicting(), 'p')
    assert plan['repairs'] == []
    assert plan['conflicts'][0]['reason'] == 'source_scope_conflict'

def test_repair_apply_is_versioned_and_stale_plan_is_rejected(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.source_scope import build_scope_repair, prepare_scope_repair, apply_scope_repair
    repo = Repository(tmp_path / 'scope.sqlite')
    p = repo.create_project('scope')['id']
    doc = repo.put_record(p, {'id':'doc','kind':'document','text':'source','metadata':{'applicability':SCOPE}})
    repo.put_record(p, {'id':'fact','kind':'entity','text':'Fact','metadata':{'discovery_candidate_id':'assertion'}})
    repo.create_assertion(p, {'id':'assertion','kind':'entity','document_id':'doc','document_version_id':doc['version_id'],'payload':{},'quote':'Fact'})
    report = build_scope_repair(repo, p)
    assert len(report['repairs']) == 1
    assert report['repairs'][0]['sources'][0]['assertion_status'] == 'pending'
    drained=[]
    rows = apply_scope_repair(SimpleNamespace(repository=repo, _drain_projections=drained.append), report)
    assert rows[0]['id'] == 'fact'
    assert rows[0]['version'] == 2
    assert rows[0]['valid_from'] is None
    assert rows[0]['metadata']['applicability'] == SCOPE
    assert repo.get_assertion(p, 'assertion')['status'] == 'pending'
    assert drained == [p]
    with pytest.raises(ValueError, match='version conflict'):
        prepare_scope_repair(repo, report)

def test_source_dates_are_copied_only_when_candidate_dates_are_absent():
    from knowledge_service.services.source_scope import inherit_source_scope
    class Dated(Sources):
        def get_record_version(self, project_id, version_id):
            return {**super().get_record_version(project_id, version_id), 'valid_from':'2024-01-01T00:00:00Z','valid_until':'2025-01-01T00:00:00Z'}
    reference = {'document_id':'doc','document_version_id':'frozen'}
    row = inherit_source_scope({'metadata':{}}, reference, Dated(), 'p')
    assert row['valid_from'] == '2024-01-01T00:00:00Z'
    explicit = inherit_source_scope({'metadata':{},'valid_from':None}, {**reference, 'valid_from':None}, Dated(), 'p')
    assert explicit['valid_from'] is None


def test_unknown_source_and_explicit_target_are_never_repaired():
    from knowledge_service.services.source_scope import build_scope_repair
    class Unknown(RepairSources):
        def get_record_version(self, project_id, version_id):
            return {'id':'doc','kind':'document','metadata':{}}
    plan = build_scope_repair(Unknown(), 'p')
    assert plan['repairs'] == []
    assert plan['conflicts'][0]['reason'] == 'unknown_source_scope'
    class Explicit(RepairSources):
        def current_records(self, project_id, **kwargs):
            row = super().current_records(project_id)[0]
            row['metadata']['applicability'] = {'platforms':['京东']}
            return [row]
    assert build_scope_repair(Explicit(), 'p')['preserved'] == ['fact']

def test_governed_draft_publication_inherits_frozen_source_scope():
    from knowledge_service.services.ontology_drafts_review import DraftReviewPublishMixin
    class Repo(Sources):
        def get_discovery_run(self, project_id, run_id):
            return {'status':'draft_created','unified_draft_id':'draft','candidate_snapshot':[
                {'id':'a','kind':'entity','text':'A','proposed_type':'Thing', 'document_id':'doc','document_version_id':'frozen'}],
                'mappings':{'entity_types':{'Thing':'urn:Thing'}}, 'accepted_candidate_ids':['a']}
        def current_records(self, project_id, **kwargs):
            return []
    class Drafts(DraftReviewPublishMixin):
        repository = Repo()
        def _active_operations(self, *args):return []
        def _history(self, *args):return [], []
    draft = {'id':'draft','source_kind':'discovery','source_context':{'publication_effects':{'kind':'discovery','discovery_run_id':'run'}}}
    effects = Drafts()._publication_effect_plan('p', draft,
        '<urn:Thing> a <http://www.w3.org/2002/07/owl#Class> .', [])
    assert len(effects['records']) == 1
    assert effects['records'][0]['metadata']['applicability'] == SCOPE
