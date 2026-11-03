"""Snapshot read models describe the full eligible scope before pagination."""
from contextlib import nullcontext
from types import SimpleNamespace

from knowledge_service.services.ontology_intake import OntologyIntake
from knowledge_service.services.ontology_discovery_workflow import OntologyDiscoveryWorkflow


def test_inventory_directory_counts_full_eligible_scope_before_pagination(monkeypatch):
    import knowledge_service.services.ontology_intake as module
    candidates = [
        {'id': 'a', 'kind': 'entity', 'document_id': 'one', 'document_title': '资料一'},
        {'id': 'b', 'kind': 'entity', 'document_id': 'two', 'document_title': '资料二'},
        {'id': 'c', 'kind': 'attribute', 'document_id': 'two', 'document_title': '资料二', 'entity_id': 'b'},
        {'id': 'draft', 'kind': 'entity', 'document_id': 'two', 'document_title': '资料二'},
        {'id': 'exception', 'kind': 'exception', 'document_id': 'exception-only'},
        {'id': 'done', 'kind': 'entity', 'document_id': 'done-only'},
    ]
    inputs = {'documents': [], 'assertions': [], 'materialized': [{'metadata': {'discovery_candidate_ids': ['b', 'done']}}]}
    monkeypatch.setattr(module, '_candidates', lambda *_: candidates)
    monkeypatch.setattr(module, 'PageReads', lambda _: SimpleNamespace(discovery_inputs=lambda _: inputs))
    repo = SimpleNamespace(read_snapshot=nullcontext, list_ontologies=lambda _: [], list_intake_runs=lambda _: [
        {'id': 'run', 'status': 'draft_created', 'unified_draft_id': 'draft-id', 'candidate_snapshot': [{'id': 'draft'}, {'id': 'b'}]},
    ])
    intake = object.__new__(OntologyIntake)
    intake.repository = repo
    result = intake.inventory('project', offset=1, limit=1)
    assert len(result['items']) == 1 and result['total'] == 4
    assert result['documents'] == [
        {'document_id': 'one', 'document_title': '资料一', 'total': 1, 'pending_count': 1, 'draft_count': 0, 'dependency_count': 0},
        {'document_id': 'two', 'document_title': '资料二', 'total': 3, 'pending_count': 2, 'draft_count': 1, 'dependency_count': 1},
    ]
    assert result['summary'] == {'document_count': 2, 'total': 4, 'pending_count': 3, 'draft_count': 1, 'dependency_count': 1}
    scoped = intake.inventory('project', document_id='two', offset=2, limit=1)
    assert scoped['documents'] == result['documents'][1:]
    assert scoped['summary']['total'] == 3


def test_overview_document_count_uses_pending_lifecycle_only(monkeypatch):
    import knowledge_service.services.ontology_discovery_workflow as module
    candidates = [{'id': identity, 'kind': kind, 'proposed_type': '类型', 'document_id': document}
        for identity, kind, document in [
            ('a', 'entity', 'one'), ('b', 'attribute', 'one'), ('c', 'relation', 'two'),
            ('draft', 'entity', 'draft-only'), ('approved', 'entity', 'approved-only'),
            ('done', 'entity', 'done-only'), ('exception', 'exception', 'exception-only')]]
    monkeypatch.setattr(module, '_candidates', lambda *_: candidates)
    monkeypatch.setattr(module, 'PageReads', lambda _: SimpleNamespace(discovery_inputs=lambda _: {
        'documents': [], 'assertions': [], 'materialized': [{'metadata': {'discovery_candidate_id': 'done'}}]}))
    repo = SimpleNamespace(list_ontologies=lambda _: [], list_intake_runs=lambda _: [
        {'id': 'draft-run', 'status': 'draft_created', 'accepted_candidate_ids': ['draft']},
        {'id': 'approved-run', 'status': 'published', 'accepted_candidate_ids': ['approved']},
    ])
    result = OntologyDiscoveryWorkflow(None)._overview_from(repo, 'project')
    assert result['candidate_status_counts'] == {'pending': 3, 'included_in_draft': 1, 'approved': 1, 'materialized': 1}
    assert result['pending_document_count'] == 2
