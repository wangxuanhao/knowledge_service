import pytest

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.answers import question_context
from knowledge_service.services.reconciliation import reconcile
from knowledge_service.services.service import KnowledgeService


@pytest.fixture
def system(tmp_path):
    repo = Repository(tmp_path / 'applicability-integration.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('同项目多平台规则')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Merchant a owl:Class .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    yield repo, service, project_id
    repo.close()


def _scope(platform):
    return {'applicability': {'platforms': [platform]}}


def test_scoped_and_search_keep_platforms_separate(system):
    _, service, project_id = system
    service.write(project_id, [
        {'id': 'meituan', 'kind': 'chunk', 'text': '美团直播规范：直播间不得虚假宣传',
         'metadata': _scope('美团')},
        {'id': 'jd', 'kind': 'chunk', 'text': '直播间不得虚假宣传',
         'metadata': _scope('京东')},
        {'id': 'legacy', 'kind': 'chunk', 'text': '直播间不得虚假宣传',
         'metadata': {}},
    ])

    scoped = service.scoped(project_id, {
        'kinds': ['chunk'], 'applicability': {'platforms': ['美团']}})
    assert [row['id'] for row in scoped] == ['meituan']

    result = service.search(project_id, {
        'query': '美团直播规范', 'retrieval_mode': 'keyword', 'k': 10})
    assert [row['id'] for row in result['hits']] == ['meituan']


def test_multi_platform_search_ranks_each_platform_and_keeps_empty_groups(system):
    _, service, project_id = system
    service.write(project_id, [
        {'id': 'mt-doc', 'kind': 'document', 'text': '美团规则', 'metadata': _scope('美团')},
        {'id': 'jd-doc', 'kind': 'document', 'text': '京东规则', 'metadata': _scope('京东')},
    ])
    service.write(project_id, [
        {'id': 'meituan-1', 'kind': 'chunk', 'source_id': 'mt-doc',
         'text': '直播管理规范', 'metadata': {'start_char': 0}},
        {'id': 'meituan-context', 'kind': 'chunk', 'source_id': 'mt-doc',
         'text': '美团例外条款', 'metadata': {'start_char': 10}},
        {'id': 'jd-1', 'kind': 'chunk', 'source_id': 'jd-doc',
         'text': '直播管理规范', 'metadata': {'start_char': 0}},
        {'id': 'jd-context', 'kind': 'chunk', 'source_id': 'jd-doc',
         'text': '京东例外条款', 'metadata': {'start_char': 10}},
    ])

    result = service.search(project_id, {
        'query': '直播管理', 'retrieval_mode': 'keyword', 'k': 1,
        'k_chunks': 1,
        'applicability': {'platforms': ['美团', '京东', '淘宝']},
    })

    groups = {group['platform']: group['hits'] for group in result['platform_groups']}
    assert [hit['id'] for hit in groups['美团']] == ['meituan-1']
    assert [hit['id'] for hit in groups['京东']] == ['jd-1']
    assert groups['淘宝'] == []
    assert {row['id'] for row in result['context_chunks']} == {
        'meituan-context', 'jd-context'}
    grouped = {group['platform']: group for group in result['platform_groups']}
    assert [row['id'] for row in grouped['美团']['context_chunks']] == ['meituan-context']
    assert [row['id'] for row in grouped['京东']['context_chunks']] == ['jd-context']


def test_document_scope_and_effective_time_are_inherited_by_chunks(system):
    _, service, project_id = system
    result = service.ingest(project_id, {
        'title': '美团直播规则', 'text': '第一条 直播间应如实宣传。',
        'extract': False, 'extraction_mode': 'documents',
        'applicability': {'platforms': ['美团'], 'businesses': ['直播']},
        'published_at': '2026-09-01',
    })

    rows = [result['document'], *result['records']]
    assert rows
    for row in rows:
        assert row['metadata']['applicability'] == {
            'platforms': {'mode': 'specific', 'values': ['美团']},
            'businesses': {'mode': 'specific', 'values': ['直播']},
            'audiences': {'mode': 'unknown', 'values': []},
        }
        assert row['metadata']['published_at'].startswith('2026-09-01')
        assert row['metadata']['effective_time_source'] == 'published_at'
        assert row['valid_from'].startswith('2026-09-01')
        assert row['valid_until'] is None


def test_reconciliation_does_not_merge_equal_names_across_platforms():
    class Repo:
        def current_records(self, _project_id):
            return [{
                'id': 'old', 'kind': 'entity', 'type': 'Merchant', 'text': '直播',
                'metadata': _scope('美团'), 'properties': {}, 'version': 1,
                'version_id': 'old-v1', 'valid_from': None, 'valid_until': None,
            }]

    class Service:
        repository = Repo()

    derived = [{
        'id': 'new', 'kind': 'entity', 'type': 'Merchant', 'text': '直播',
        'metadata': _scope('京东'), 'properties': {},
        'valid_from': None, 'valid_until': None,
    }]
    rows, expected = reconcile(
        Service(), 'project', derived, {'resolve_entities': True},
        {'id': 'doc', 'version_id': 'doc-v1'})

    assert expected == {}
    assert rows == derived
    assert 'merged_into' not in rows[0]['metadata']


def test_governance_rejects_cross_platform_entity_merge(system):
    _, service, project_id = system
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Merchant', 'text': '直播',
         'metadata': _scope('美团')},
        {'id': 'b', 'kind': 'entity', 'type': 'Merchant', 'text': '直播',
         'metadata': _scope('京东')},
    ])

    with pytest.raises(ValueError, match='适用范围'):
        Governance(service).merge(project_id, 'a', 'b', {'a': 1, 'b': 1})


def test_question_context_resolves_platform_before_graph_or_ranking(system):
    _, service, project_id = system
    service.write(project_id, [
        {'id': 'mt-rule', 'kind': 'chunk', 'text': '美团直播规范：禁止虚假宣传',
         'metadata': _scope('美团')},
        {'id': 'jd-rule', 'kind': 'chunk', 'text': '美团直播规范：京东侧规则文本',
         'metadata': _scope('京东')},
    ])

    context = question_context(service, project_id, {
        'query': '美团直播规范', 'retrieval_mode': 'keyword', 'k': 10,
        'k_entities': 0, 'k_chunks': 10, 'hops': 2,
    })

    assert [row['id'] for row in context['hits']] == ['mt-rule']
    assert context['applicability']['platforms'] == {
        'mode': 'specific', 'values': ['美团']}


def test_extractor_cannot_override_upload_applicability(monkeypatch, system):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    monkeypatch.setattr(SemanticaExtractor, 'extract', lambda self, text, ontology: [{
        'id': 'live', 'kind': 'entity', 'type': 'http://ex/Merchant', 'text': '直播',
        'metadata': _scope('京东'),
    }])
    _, service, project_id = system
    result = service.ingest(project_id, {
        'title': '美团直播规则', 'text': '直播应当合规', 'extraction_mode': 'ontology',
        'resolve_entities': False,
        'applicability': {'platforms': ['美团']}, 'published_at': '2026-09-01',
    })

    entity = next(row for row in result['records'] if row['kind'] == 'entity')
    assert entity['metadata']['applicability']['platforms'] == {
        'mode': 'specific', 'values': ['美团']}
    assert entity['valid_from'].startswith('2026-09-01')


def test_question_context_adds_same_version_neighbors_as_evidence(system):
    _, service, project_id = system
    document = service.write(project_id, [{
        'id': 'doc', 'kind': 'document', 'text': '总则。一般要求。例外条款。',
        'metadata': {'title': '美团规则', **_scope('美团')},
    }])[0]
    service.write(project_id, [
        {'id': 'c0', 'kind': 'chunk', 'source_id': 'doc', 'text': '总则',
         'metadata': {'start_char': 0}},
        {'id': 'c1', 'kind': 'chunk', 'source_id': 'doc', 'text': '美团直播一般要求',
         'metadata': {'start_char': 3}},
        {'id': 'c2', 'kind': 'chunk', 'source_id': 'doc', 'text': '但特定商户适用例外条款',
         'metadata': {'start_char': 12}},
    ])

    context = question_context(service, project_id, {
        'query': '美团直播一般要求', 'retrieval_mode': 'keyword', 'k': 1,
        'k_entities': 0, 'k_chunks': 1, 'hops': 0,
    })

    assert [row['id'] for row in context['hits']] == ['c1']
    assert {row['id'] for row in context['context_chunks']} == {'c0', 'c2'}
    assert {'c0', 'c1', 'c2'}.issubset({row['id'] for row in context['evidence_rows']})
    assert all(row['metadata']['source_version_id'] == document['version_id']
               for row in context['context_chunks'])


def test_multi_platform_question_context_keeps_evidence_groups_separate(system):
    _, service, project_id = system
    service.write(project_id, [
        {'id': 'mt', 'kind': 'chunk', 'text': '直播管理要求', 'metadata': _scope('美团')},
        {'id': 'jd', 'kind': 'chunk', 'text': '直播管理要求', 'metadata': _scope('京东')},
    ])
    context = question_context(service, project_id, {
        'query': '直播管理', 'retrieval_mode': 'keyword', 'k': 1,
        'k_entities': 0, 'k_chunks': 1, 'hops': 0,
        'applicability': {'platforms': ['美团', '京东', '淘宝']},
    })

    groups = {group['platform']: group for group in context['platform_groups']}
    assert [row['id'] for row in groups['美团']['evidence_rows']] == ['mt']
    assert [row['id'] for row in groups['京东']['evidence_rows']] == ['jd']
    assert groups['淘宝']['evidence_rows'] == []
    assert all(row['query_platform'] in {'美团', '京东'}
               for row in context['evidence_rows'])
