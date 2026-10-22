"""Reclassification plans transfer grouped counts and bounded sample text."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from knowledge_service.core.time import utc_now
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.repository.page_reads import PageReads
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.reclassify import Reclassify, _SAMPLE_LIMIT, _truncate
from knowledge_service.services.service import KnowledgeService


OLD = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
<urn:Keep> a owl:Class . <urn:Retired> a owl:Class . <urn:Dropped> a owl:Class .
<urn:Edge> a owl:ObjectProperty . <urn:Amount> a owl:DatatypeProperty .'''
CURRENT = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
<urn:Keep> a owl:Class .
<urn:Retired> a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy <urn:Keep> .
<urn:Edge> a owl:ObjectProperty . <urn:Amount> a owl:DatatypeProperty .'''


@pytest.fixture
def plan_system(tmp_path):
    repo = Repository(tmp_path / 'bounded-reclassification')
    project = repo.create_project('plan')['id']
    service = KnowledgeService(repo, HashingEncoder())
    old = repo.bootstrap_ontology(project, OLD, Ontology(OLD).summary())
    current = repo.bootstrap_ontology(project, CURRENT, Ontology(CURRENT).summary())
    records = [
        {'id': f'a{index:02}', 'kind': 'entity', 'type': 'urn:Keep',
         'text': ('sample  text\n' + str(index) + ' ') * 100,
         'ontology_id': old['id'], 'metadata': {'region': 'east'}}
        for index in range(25)
    ]
    records += [
        {'id': 'rename' + str(index), 'kind': 'entity', 'type': 'urn:Retired',
         'text': 'rename', 'ontology_id': old['id']} for index in range(2)
    ]
    records += [
        {'id': 'dropped', 'kind': 'entity', 'type': 'urn:Dropped', 'text': 'missing', 'ontology_id': old['id']},
        {'id': 'unbound-a', 'kind': 'entity', 'type': None, 'text': 'no type'},
        {'id': 'unbound-b', 'kind': 'entity', 'type': '', 'text': 'empty type'},
        {'id': 'relation', 'kind': 'relation', 'type': 'urn:Edge', 'text': 'link',
         'subject_id': 'a00', 'object_id': 'a01', 'ontology_id': old['id'], 'metadata': {'region': 'east'}},
        {'id': 'attribute', 'kind': 'attribute', 'type': 'urn:Amount', 'text': 'amount',
         'subject_id': 'a00', 'value': 1, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer',
         'ontology_id': old['id'], 'metadata': {'region': 'east'}},
        {'id': 'hidden', 'kind': 'entity', 'type': 'urn:Keep', 'text': 'deleted',
         'ontology_id': old['id'], 'metadata': {'_deleted': True}},
        {'id': 'audit', 'kind': 'entity', 'type': 'urn:Keep', 'text': 'audit',
         'ontology_id': old['id'], 'metadata': {'_audit': True}},
        {'id': 'orphan', 'kind': 'relation', 'type': 'urn:Edge', 'text': 'hidden endpoint',
         'subject_id': 'a00', 'object_id': 'hidden', 'ontology_id': old['id']},
        {'id': 'document', 'kind': 'document', 'text': 'body' * 500},
        {'id': 'chunk', 'kind': 'chunk', 'text': 'chunk' * 500, 'source_id': 'document'},
    ]
    records += [{'id': 'current' + str(index), 'kind': 'entity', 'type': 'urn:Keep',
                 'text': 'current', 'ontology_id': current['id']} for index in range(3)]
    repo.put_batch(project, records)
    foreign = repo.create_project('foreign')['id']
    repo.put_record(foreign, {'id': 'foreign', 'kind': 'entity', 'type': 'urn:Keep',
        'text': 'foreign', 'ontology_id': old['id']})
    return repo, service, project, old, current


def test_plan_matches_existing_counts_order_and_samples_without_loading_record_bodies(plan_system, monkeypatch):
    repo, service, project, old, _ = plan_system
    reference = Reclassify(service).plan(project)
    assert reference['total_records'] == 35
    assert reference['stale_records'] == 32

    def full_scan(*args, **kwargs):
        raise AssertionError('reclassification plan materialized complete knowledge records')

    monkeypatch.setattr(service, 'scoped', full_scan)
    monkeypatch.setattr(repo, 'current_records', full_scan)
    monkeypatch.setattr(repo, '_record', full_scan)
    actual = Reclassify(service).plan(project)
    assert actual == reference
    keep = next(group for group in actual['groups'] if group['key'] == old['id'] + ':entity:urn:Keep')
    assert keep['record_count'] == 25
    assert len(keep['records']) == _SAMPLE_LIMIT
    assert [sample['id'] for sample in keep['records']] == [f'a{index:02}' for index in range(_SAMPLE_LIMIT)]
    assert keep['records'][0]['text'] == _truncate(('sample  text\n0 ') * 100)


def test_grouped_plan_inputs_keep_scoped_history_endpoints_and_exact_counts(plan_system):
    repo, service, project, old, current = plan_system
    known = utc_now()
    repo.put_record(project, {'id': 'a00', 'kind': 'entity', 'type': 'urn:Keep',
        'text': 'changed', 'ontology_id': current['id'], 'metadata': {'region': 'west'}}, expected_version=1)
    scope = {'known_at': known,
             'filters': {'field': 'metadata.region', 'op': 'eq', 'value': 'east'}}
    inputs = PageReads(repo).reclassify_inputs(project, scope, current['id'], sample_limit=3)
    expected = service.scoped(project, {**scope, 'kinds': ['entity', 'relation', 'attribute']})
    assert inputs['total_records'] == len(expected) == 27
    counts = {}
    for row in expected:
        key = Reclassify._key(row)
        counts[key] = counts.get(key, 0) + 1
    assert {Reclassify._key(group): group['record_count'] for group in inputs['groups']} == counts
    assert sum(len(group['records']) for group in inputs['groups']) == 5
    assert all(len(group['records']) <= 3 for group in inputs['groups'])
    assert next(group for group in inputs['groups'] if group['kind'] == 'entity')['ontology_id'] == old['id']


def test_complete_reclassification_plan_does_not_wait_for_writer_or_service_locks(plan_system):
    repo, service, project, _, _ = plan_system
    with ThreadPoolExecutor(max_workers=1) as executor:
        with repo._lock, repo._db.raw.lock, service.lock:
            future = executor.submit(Reclassify(service).plan, project)
            try:
                result = future.result(timeout=1)
            except TimeoutError:
                pytest.fail('reclassification plan queued behind writer/service connection')
    assert result['total_records'] == 35


def test_reclassification_plan_keeps_uncommitted_transaction_inputs(plan_system):
    repo, service, project, old, _ = plan_system
    with pytest.raises(RuntimeError, match='rollback'):
        with repo.unit_of_work():
            repo.put_record(project, {'id': 'pending', 'kind': 'entity', 'type': 'urn:Keep',
                'text': 'uncommitted', 'ontology_id': old['id']})
            assert Reclassify(service).plan(project)['total_records'] == 36
            raise RuntimeError('rollback')
    assert Reclassify(service).plan(project)['total_records'] == 35


@pytest.mark.parametrize('text', [
    (' \n\t' * 100) + ('actual body ' * 20),
    'first' + (' \n\t' * 100) + ('remaining words ' * 20),
], ids=['leading-whitespace', 'interword-whitespace'])
def test_bounded_plan_samples_preserve_legacy_whitespace_truncation(plan_system, text):
    repo, service, project, old, _ = plan_system
    repo.put_record(project, {'id': 'a00', 'kind': 'entity', 'type': 'urn:Keep',
        'text': text, 'ontology_id': old['id']}, expected_version=1)
    plan = Reclassify(service).plan(project)
    group = next(group for group in plan['groups'] if group['key'] == old['id'] + ':entity:urn:Keep')
    sample = next(sample for sample in group['records'] if sample['id'] == 'a00')
    assert sample['text'] == _truncate(text)
