"""Independent channel budgets and bounded authority context contracts."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval import RetrievalEngine
from test_rule_document_oag import _ready_keyword_index


class Encoder:
    identity = 'test-v1'
    semantic = True
    dimension = 2

    def encode(self, texts):
        return [[1., 0.] for _ in texts]


def _ready_repository(*, embedding_model=Encoder.identity):
    index = {'index_version': 1, 'watermark': 9, 'dim': 2,
             'embedding_model': embedding_model, 'catalog': default_catalog().to_dict()}
    return SimpleNamespace(projections=SimpleNamespace(
        search_index=lambda *_: dict(index),
        search_index_is_current=lambda _project, version, _schema, watermark:
            (version, watermark) == (index['index_version'], index['watermark'])))


def _forbid_pg_ranking(*_args, **_kwargs):
    raise AssertionError('current retrieval must not fall back to PostgreSQL ranking')


def _unbuilt_index(*_args):
    raise RuntimeError('项目向量索引不可用: not_built')


def test_native_explicit_channel_budgets_are_independent_of_total_k(monkeypatch):
    rows = [{'id': f'{kind}-{i}', 'version_id': f'{kind}-{i}-v1',
             'kind': kind, 'text': '退款', 'metadata': {}}
            for kind in ('entity', 'relation', 'chunk') for i in range(4)]
    calls = []

    def search(project, query, *, kinds, k, **options):
        assert options['retrieval_mode'] == 'keyword'
        assert options['index_version'] == 1
        calls.append((tuple(kinds), k))
        return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': 10-i}
                for i, row in enumerate(rows) if row['kind'] in kinds][:k]

    monkeypatch.setattr(ScopedReads, 'by_ids', lambda _self, _p, _scope, ids, *, kinds=None: [
        row for row in rows if row['id'] in ids and row['kind'] in kinds])
    store = SimpleNamespace(available=True, search=search)
    result = RetrievalEngine(_ready_repository(), Encoder(), store).search(
            'p', '退款', retrieval_mode='keyword', k=1,
            content_k={'entity': 2, 'relation': 1, 'chunk': 2})

    assert {kind: sum(row['kind'] == kind for row in result['hits'])
            for kind in ('entity', 'relation', 'chunk')} == {
                'entity': 2, 'relation': 1, 'chunk': 2}
    assert result['channel_quotas'] == {'entity': 2, 'relation': 1, 'chunk': 2}
    assert [kinds for kinds, _ in calls] == [('entity',), ('relation',), ('chunk',)]
    assert all(1 < k <= RetrievalEngine.MAX_NATIVE_LIMIT for _, k in calls)
    assert result['_backend'] == 'milvus' and result['complete'] is True


def test_native_failure_preserves_the_real_error_without_postgres_fallback(monkeypatch):
    repository = _ready_repository()
    repository.query = repository.keyword_candidates = _forbid_pg_ranking
    monkeypatch.setattr(ScopedReads, 'ranked_keyword', _forbid_pg_ranking)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('native unavailable')

    with pytest.raises(RuntimeError, match='native unavailable'):
        RetrievalEngine(repository, Encoder(), SimpleNamespace(
            available=True, search=unavailable)).search(
            'p', '退款', retrieval_mode='keyword', content_channels=['chunk'])
    assert len(calls) == 1
    assert calls[0]['retrieval_mode'] == 'keyword'


def test_vector_modes_fail_closed_without_milvus_and_never_query_local_vectors():
    repository = _ready_repository()
    repository.query = _forbid_pg_ranking
    for mode in ('semantic', 'hybrid'):
        with pytest.raises(RuntimeError, match='Milvus'):
            RetrievalEngine(repository, Encoder()).search('p', '退款', retrieval_mode=mode)
        repository.projections.search_index = _unbuilt_index
        with pytest.raises(RuntimeError, match='not_built'):
            RetrievalEngine(repository, Encoder(), SimpleNamespace(
                available=True, search=_forbid_pg_ranking)).search('p', '退款', retrieval_mode=mode)


def test_typed_filters_compile_as_parameterized_sql():
    from knowledge_service.repository.scoped_reads import _filter_sql
    value = "quote' or 1=1 --"
    sql, params = _filter_sql({'and': [
        {'field': 'metadata.region', 'op': 'contains', 'value': value},
        {'field': 'amount', 'op': 'gt', 'value': 12},
        {'field': 'metadata.optional', 'op': 'exists', 'value': False},
    ]}, 'r')
    assert value not in sql
    assert value in params
    assert 'CASE' in sql and 'IS NULL' in sql
    assert '12' in params
    assert '> ?::numeric' in sql


def test_scoped_typed_filters_match_public_filter_semantics(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.utils.filters import matches_filter
    repo = Repository(tmp_path / 'typed-filter.sqlite')
    project = repo.create_project('typed predicates')['id']
    records = repo.put_batch(project, [
        {'id': 'one', 'kind': 'entity', 'text': 'one', 'metadata': {
            'amount': 12, 'flag': True, 'optional': None, 'items': [1, {'a': 1, 'b': 2}]}},
        {'id': 'two', 'kind': 'entity', 'text': 'two', 'metadata': {
            'amount': '12', 'flag': 1, 'items': [{'a': 1}]}},
    ])
    filters = [
        {'field': 'amount', 'op': 'gte', 'value': 12},
        {'field': 'flag', 'op': 'eq', 'value': True},
        {'field': 'optional', 'op': 'exists', 'value': True},
        {'field': 'optional', 'op': 'eq', 'value': None},
        {'field': 'items', 'op': 'contains', 'value': {'a': 1}},
        {'field': 'missing', 'op': 'ne', 'value': 1},
        {'or': [{'field': 'id', 'op': 'eq', 'value': 'one'},
                {'field': 'flag', 'op': 'in', 'value': [1, False]}]},
    ]
    for predicate in filters:
        expected = [row['id'] for row in records if matches_filter(row, predicate)]
        actual = ScopedReads(repo).page(project, {'filters': predicate})
        assert [row['id'] for row in actual['records']] == expected, predicate


def test_scoped_history_and_ontology_qualify_before_paging(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'scoped-history.sqlite')
    project = repo.create_project('history scope')['id']
    old = repo.put_record(project, {'id': 'record', 'kind': 'entity', 'text': 'old',
        'ontology_id': 'release-a', 'valid_from': '2020-01-01', 'metadata': {}})
    repo.put_record(project, {'id': 'record', 'kind': 'entity', 'text': 'new',
        'ontology_id': 'release-b', 'valid_from': '2030-01-01', 'metadata': {}}, expected_version=1)
    result = ScopedReads(repo).page(project, {'known_at': old['recorded_at'],
        'valid_at': '2025-01-01', 'ontology_scope': 'ids', 'ontology_ids': ['release-a']})
    assert [row['version_id'] for row in result['records']] == [old['version_id']]
    assert ScopedReads(repo).page(project, {'valid_at': '2025-01-01'})['total'] == 0


def test_bounded_neighbors_report_truncation_and_preserve_direction(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'bounded-neighbors.sqlite')
    project = repo.create_project('direction scope')['id']
    repo.put_batch(project, [{'id': id, 'kind': 'entity', 'text': id} for id in ('a', 'b', 'c')] + [
        {'id': 'ab', 'kind': 'relation', 'text': 'ab', 'subject_id': 'a', 'object_id': 'b'},
        {'id': 'ac', 'kind': 'relation', 'text': 'ac', 'subject_id': 'a', 'object_id': 'c'}])
    backward = ScopedReads(repo).neighbors(project, {}, ['b'], direction='backward')
    assert backward['reasoning_paths'][0]['steps'][0]['direction'] == 'backward'
    assert backward['reasoning_paths'][0]['steps'][0]['subject_id'] == 'a'
    bounded = ScopedReads(repo).neighbors(project, {}, ['a'], max_nodes=2, max_edges=1)
    assert bounded['truncated'] is True and bounded['continuation']['seeds'] == ['a']
    assert len([row for row in bounded['records'] if row['kind'] == 'relation']) == 1


def test_scoped_pages_filter_before_decoding_and_keep_relation_endpoints(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'scoped-pages.sqlite')
    project = repo.create_project('范围分页')['id']
    scope = {'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}},
             'valid_at': '2026-01-01', 'filters': {'or': [
                 {'field': 'region', 'op': 'eq', 'value': '华东'},
                 {'field': 'kind', 'op': 'eq', 'value': 'relation'}]}}
    def item(id, kind='entity', platform='美团', **extra):
        return {'id': id, 'kind': kind, 'text': id, 'valid_from': '2025-01-01',
                'metadata': {'region': '华东', 'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}, **extra}
    repo.put_batch(project, [item('a'), item('b'), item('c', platform='淘宝'),
        item('ab', 'relation', subject_id='a', object_id='b'),
        item('bc', 'relation', subject_id='b', object_id='c')])
    reads = ScopedReads(repo)
    first = reads.page(project, scope, kinds=['entity'], limit=1)
    second = reads.page(project, scope, kinds=['entity'], limit=1, after=first['next_cursor'])
    assert first['total'] == second['total'] == 2
    assert [row['id'] for row in first['records'] + second['records']] == ['a', 'b']
    assert second['next_cursor'] is None
    edges = reads.page(project, scope, kinds=['relation'])
    assert [row['id'] for row in edges['records']] == ['ab']
    neighbors = reads.neighbors(project, scope, ['a'], hops=2)
    assert {row['id'] for row in neighbors['records']} == {'a', 'b', 'ab'}
    assert neighbors['reasoning_paths'][0]['steps'][0]['relation_id'] == 'ab'
    assert neighbors['truncated'] is False


def test_scoped_cursor_is_bound_to_the_project_and_scope(tmp_path):
    import pytest
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'cursor-binding.sqlite')
    project = repo.create_project('cursor')['id']
    repo.put_batch(project, [{'id': str(i), 'kind': 'entity', 'text': str(i)} for i in range(3)])
    reads = ScopedReads(repo)
    cursor = reads.page(project, {}, limit=1)['next_cursor']
    with pytest.raises(ValueError, match='cursor|游标'):
        reads.page(project, {'kinds': ['chunk']}, after=cursor)


def test_native_and_historical_reads_get_the_same_versioned_source_context(tmp_path, monkeypatch):
    from knowledge_service.core.time import utc_now
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    repo = Repository(tmp_path / 'source-context.sqlite')
    project = repo.create_project('来源补取')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': '来源'})
    rows = repo.put_batch(project, [
        {'id': 'anchor', 'kind': 'chunk', 'source_id': 'doc', 'text': '退款',
         'metadata': {'source_version_id': document['version_id'], 'start_char': 0}},
        {'id': 'adjacent', 'kind': 'chunk', 'source_id': 'doc', 'text': '例外条件',
         'metadata': {'source_version_id': document['version_id'], 'start_char': 10}},
        {'id': 'entity', 'kind': 'entity', 'text': '退款实体',
         'metadata': {}},
    ])
    fact = rows[2]
    assertion = repo.create_assertion(project, {
        'id': 'support', 'kind': 'entity', 'payload': {},
        'document_id': document['id'], 'document_version_id': document['version_id'],
        'chunk_id': rows[0]['id'], 'quote': rows[0]['text'], 'start_char': 0, 'end_char': 2})
    repo.transition_assertion(project, assertion['id'], 1, 'accepted',
        'approved', 'reviewer', canonical_record_id=fact['id'])
    event = repo.list_assertion_events(project, assertion['id'])[-1]
    repo.add_record_version_assertion(project, fact['id'], fact['version_id'], assertion['id'], event['id'])
    service = KnowledgeService(repo, Encoder())
    _ready_keyword_index(service, monkeypatch)
    known_at = utc_now()
    request = {'query': '退款', 'retrieval_mode': 'keyword', 'k_entities': 1, 'k_chunks': 0,
               'k_relations': 0, 'kinds': ['entity']}
    native = service.search(project, request)
    monkeypatch.setattr(repo.projections, 'search_index', _unbuilt_index)
    with pytest.raises(RuntimeError, match='not_built'):
        service.search(project, request)
    historical = service.search(project, {**request, 'known_at': known_at})
    assert native['_backend'] == 'milvus'
    assert historical['_backend'] == 'postgres_history'
    for result in (native, historical):
        assert result['source_chunks'][0]['version_id'] == rows[0]['version_id']
        assert [row['id'] for row in result['context_chunks']] == ['adjacent']
        assert result['context_chunks'][0]['metadata']['source_version_id'] == document['version_id']


def test_grouped_qa_contains_bounded_real_graph_endpoints_and_paths(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.answers import question_context
    repo = Repository(tmp_path / 'grouped-graph.sqlite')
    project = repo.create_project('分组图依据')['id']
    repo.bootstrap_ontology(project, '''
        @prefix ex: <http://example.org/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        ex:Rule a owl:Class .
        ex:allows a owl:ObjectProperty .
    ''', {'purpose': '图证据资格测试本体'})
    def item(id, text, kind='entity', platform='美团', **extra):
        return {'id': id, 'text': text, 'kind': kind, 'valid_from': '2025-01-01',
                'type': 'http://example.org/allows' if kind == 'relation' else 'http://example.org/Rule',
                'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}, **extra}
    facts = repo.put_batch(project, [item('a', '退款规则'), item('b', '申请人'),
        item('c', '外平台', platform='淘宝'),
        item('ab', '允许申请', 'relation', subject_id='a', object_id='b'),
        item('bc', '不可拼链', 'relation', subject_id='b', object_id='c')])
    doc = repo.put_record(project, item('doc', '依据原文', 'document'))
    chunk = repo.put_record(project, {**item('source', '依据原文', 'chunk'),
        'source_id': doc['id'], 'metadata': {**doc['metadata'],
            'source_version_id': doc['version_id'], 'start_char': 0, 'end_char': 4}})
    for fact in facts:
        if fact['id'] not in {'a', 'b', 'ab'}:
            continue
        assertion = repo.create_assertion(project, {
            'id': 'support-' + fact['id'], 'kind': fact['kind'], 'payload': {},
            'document_id': doc['id'], 'document_version_id': doc['version_id'],
            'chunk_id': chunk['id'], 'quote': chunk['text'], 'start_char': 0, 'end_char': 4})
        repo.transition_assertion(project, assertion['id'], 1, 'accepted',
            'approved', 'reviewer', canonical_record_id=fact['id'])
        event = repo.list_assertion_events(project, assertion['id'])[-1]
        repo.add_record_version_assertion(project, fact['id'], fact['version_id'], assertion['id'], event['id'])
    service = KnowledgeService(repo, Encoder())
    _ready_keyword_index(service, monkeypatch)
    context = question_context(service, project, {
        'query': '退款', 'retrieval_mode': 'keyword', 'valid_at': '2026-01-01',
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}, 'include_references': False,
        'k_entities': 1, 'k_chunks': 0, 'hops': 2})
    assert {'a', 'b', 'ab'}.issubset({row['id'] for row in context['evidence_rows']})
    assert not {'c', 'bc'}.intersection(row['id'] for row in context['evidence_rows'])
    assert context['reasoning_paths'][0]['steps'][0]['relation_id'] == 'ab'
    assert context['reasoning_paths'][0]['query_platform'] == '美团'
    assert context['channels']['graph_evidence'] == 1


def test_advanced_vector_scope_streams_all_candidates_before_global_ranking(monkeypatch):
    from knowledge_service.repository.scoped_reads import ScopedReads
    calls = []
    all_rows = {f'v{i}': {'id': f'r{i}', 'version_id': f'v{i}',
        'kind': 'chunk', 'text': '退款', 'metadata': {}} for i in range(5002)}
    def batches(self, project, scope, *, kinds, ids=None, batch_size=1000):
        assert scope['filters']['value'] == '华东'
        for offset in range(0, 5002, 1000):
            yield list(all_rows)[offset:offset + 1000]
    monkeypatch.setattr(ScopedReads, 'version_id_batches', batches, raising=False)
    monkeypatch.setattr(ScopedReads, 'page', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('advanced vector scope must not preload payload pages')))
    monkeypatch.setattr(ScopedReads, 'by_ids', lambda self, p, s, ids, **kwargs: [
        row for row in all_rows.values() if row['id'] in ids])
    def search(p, query, *, kinds, k, scope, eligible_version_ids, **options):
        assert options['retrieval_mode'] == 'semantic'
        assert options['query_vector'] == [1., 0.]
        assert options['index_version'] == 1
        assert scope.get('filters') is None
        versions = eligible_version_ids
        calls.append(list(versions))
        return [{'record_id': all_rows[id]['id'], 'version_id': id, 'score': float(id[1:])}
                for id in sorted(versions, key=lambda id: int(id[1:]), reverse=True)[:k]]
    result = RetrievalEngine(_ready_repository(), Encoder(), SimpleNamespace(available=True, search=search)).search(
        'p', '退款', retrieval_mode='semantic',
        scope={'filters': {'field': 'region', 'op': 'eq', 'value': '华东'}},
        content_channels=['chunk'], content_k={'chunk': 2})
    assert [row['id'] for row in result['hits']] == ['r5001', 'r5000']
    assert sum(map(len, calls)) == 5002 and max(map(len, calls)) <= 1000
    assert result['ranking_strategy'] == 'milvus_scoped_batches'


def test_native_hybrid_uses_rrf_by_default():
    from knowledge_service.integrations.milvus_store import MilvusStore
    captured = {}
    store = MilvusStore(dim=2)
    store._client = SimpleNamespace(hybrid_search=object())
    store._load_if_needed = lambda **_kwargs: None
    def rpc(*args, **kwargs):
        captured.update(kwargs)
        return [[]]
    store._search_rpc = rpc
    store.search('p', '退款', retrieval_mode='hybrid', query_vector=[1., 0.],
                 kinds=['chunk'], index_version=7, catalog=default_catalog())
    assert type(captured['ranker']).__name__ == 'RRFRanker'
    assert len(captured['reqs']) == 2
    for request in captured['reqs']:
        assert 'project_id ==' in request.expr and 'index_version ==' in request.expr
        assert 'p' in request.expr_params.values() and 7 in request.expr_params.values()


def test_large_bounded_neighbors_batch_exact_endpoint_reads(monkeypatch):
    from contextlib import nullcontext
    from knowledge_service.repository.scoped_reads import ScopedReads
    edges = [{'id': f'ab{i}', 'version_id': f'ab{i}v1', 'kind': 'relation',
        'text': 'allows', 'subject_id': 'a', 'object_id': f'b{i}'} for i in range(5001)]
    class Cursor:
        def fetchall(self):
            return edges
    repository = SimpleNamespace(_lock=nullcontext(), _db=SimpleNamespace(execute=lambda *args: Cursor()), _record=dict)
    reader = ScopedReads(repository)
    batches = []
    def by_ids(project, scope, ids, **kwargs):
        assert len(ids) <= 5000
        batches.append(len(ids))
        return [{'id': id, 'version_id': id + 'v1', 'kind': 'entity', 'text': id} for id in ids]
    monkeypatch.setattr(reader, 'by_ids', by_ids)
    monkeypatch.setattr(reader, '_qualified', lambda *args, **kwargs: ('TRUE', []))
    graph = reader.neighbors('p', {}, ['a'], max_nodes=6000, max_edges=6000)
    assert len(graph['records']) == 10003
    assert batches == [1, 5000, 1]


def test_exact_identity_read_preserves_tombstones_and_orphaned_relation(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'identity-read.sqlite')
    project = repo.create_project('identity read')['id']
    repo.put_batch(project, [
        {'id': 'a', 'kind': 'entity', 'text': 'a'},
        {'id': 'b', 'kind': 'entity', 'text': 'b'},
        {'id': 'ab', 'kind': 'relation', 'text': 'ab', 'subject_id': 'a', 'object_id': 'b'}])
    deleted = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'deleted',
        'metadata': {'_deleted': True}}, expected_version=1)
    monkeypatch.setattr(repo, 'current_records', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('exact identity reads must remain bounded')))
    assert repo.get_record(project, 'a')['version_id'] == deleted['version_id']
    assert repo.get_record(project, 'ab')['id'] == 'ab'
    assert ScopedReads(repo).by_ids(project, {}, ['a', 'ab']) == []


def test_vector_search_rejects_a_different_encoder_before_ranking(monkeypatch):
    encoder = Encoder()
    monkeypatch.setattr(encoder, 'encode', Mock(side_effect=AssertionError('model mismatch must precede encoding')))
    search = Mock(side_effect=AssertionError('model mismatch must precede ranking'))
    store = SimpleNamespace(available=True, search=search)
    with pytest.raises(RuntimeError, match='编码器|模型'):
        RetrievalEngine(_ready_repository(embedding_model='different-model'), encoder, store).search(
            'p', '退款', retrieval_mode='semantic')
    encoder.encode.assert_not_called()
    search.assert_not_called()


def test_projection_delivery_rejects_mixed_model_rows_before_encoding_or_io():
    from knowledge_service.domain.search_projection import ProjectionInput, build_projection
    from knowledge_service.integrations.milvus_store import MilvusSchemaMismatch
    from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
    # The shared native schema has no per-row model field. A frozen project
    # generation identifies its model, and delivery rejects a different encoder
    # before an old-model row can enter that generation.
    catalog = default_catalog()
    row = build_projection(ProjectionInput({'project_id': 'p', 'id': 'chunk',
        'version_id': 'old-vector', 'kind': 'chunk', 'text': '退款'}), catalog, index_version=1)
    event = {'seq': 1, 'operation': 'upsert', 'payload': {'_milvus_projection': {
        'index_version': 1, 'catalog_fingerprint': catalog.fingerprint,
        'embedding_model': 'model-v1', 'row': row}}}
    encoder = SimpleNamespace(identity='model-v2', encode=Mock())
    store = Mock()
    with pytest.raises(MilvusSchemaMismatch, match='模型|编码器'):
        MilvusProjectionAdapter(lambda: store, encoder=encoder).apply(
            'p', event, fence='claim', index_version=1, catalog=catalog)
    encoder.encode.assert_not_called()
    assert store.mock_calls == []


def test_fact_source_context_stays_on_asserted_source_revision_after_chunk_replacement(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'asserted-source-revision.sqlite')
    project = repo.create_project('asserted source revision')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'old source'})
    chunks = repo.put_batch(project, [
        {'id': 'anchor', 'kind': 'chunk', 'source_id': 'doc', 'text': 'old anchor',
            'metadata': {'source_version_id': document['version_id'], 'start_char': 0}},
        {'id': 'adjacent', 'kind': 'chunk', 'source_id': 'doc', 'text': 'old adjacent',
            'metadata': {'source_version_id': document['version_id'], 'start_char': 10}}])
    fact = repo.put_record(project, {'id': 'fact', 'kind': 'entity', 'text': 'fact'})
    assertion = repo.create_assertion(project, {'id': 'support', 'kind': 'entity', 'payload': {},
        'document_id': 'doc', 'document_version_id': document['version_id'], 'chunk_id': 'anchor',
        'quote': 'old anchor', 'start_char': 0, 'end_char': 10})
    repo.transition_assertion(project, 'support', 1, 'accepted', 'approved', 'reviewer', canonical_record_id='fact')
    event = repo.list_assertion_events(project, 'support')[-1]
    repo.add_record_version_assertion(project, 'fact', fact['version_id'], assertion['id'], event['id'])
    overlap = ScopedReads(repo).source_context(project, {}, [fact, chunks[0]])
    assert overlap['source_chunks'] == []
    assert [row['version_id'] for row in overlap['context_chunks']] == [chunks[1]['version_id']]
    newer = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'new source'}, expected_version=1)
    for chunk in chunks:
        repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'source_id': 'doc', 'text': 'new ' + chunk['id'],
            'metadata': {**chunk['metadata'], 'source_version_id': newer['version_id']}}, expected_version=1)
    context = ScopedReads(repo).source_context(project, {}, [fact])
    assert [row['version_id'] for row in context['source_chunks']] == [chunks[0]['version_id']]
    assert [row['version_id'] for row in context['context_chunks']] == [chunks[1]['version_id']]


def test_unmapped_fact_metadata_cannot_authorize_source_context(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'unmapped-source-hint.sqlite')
    project = repo.create_project('unmapped source hint')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'source'})
    repo.put_record(project, {'id': 'anchor', 'kind': 'chunk', 'source_id': 'doc', 'text': 'anchor',
        'metadata': {'source_version_id': document['version_id'], 'start_char': 0}})
    fact = repo.put_record(project, {'id': 'fact', 'kind': 'entity', 'text': 'fact',
        'metadata': {'chunk_id': 'anchor'}})
    assert ScopedReads(repo).source_context(project, {}, [fact]) == {
        'source_chunks': [], 'context_chunks': []}


def test_version_identity_batches_apply_scope_without_decoding_payload(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'version-batches.sqlite')
    project = repo.create_project('version batches')['id']
    rows = repo.put_batch(project, [{'id': id, 'kind': 'chunk', 'text': id,
        'metadata': {'region': region}} for id, region in [('a', 'east'), ('b', 'west'), ('c', 'east')]])
    monkeypatch.setattr(repo, '_record', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('vector candidate paging must not decode record payloads')))
    batches = list(ScopedReads(repo).version_id_batches(project, {'filters': {
        'field': 'region', 'op': 'eq', 'value': 'east'}}, kinds=['chunk'], batch_size=1))
    assert batches == [[rows[0]['version_id']], [rows[2]['version_id']]]


def test_ablation_flags_skip_assistance_and_keep_structure_governance(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.answers import question_context
    repo = Repository(tmp_path / 'real-ablation-stages.sqlite')
    project = repo.create_project('real ablation stages')['id']
    doc = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': '退款',
        'valid_from': '2020-01-01', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    repo.put_batch(project, [
        {'id': 'chunk', 'kind': 'chunk', 'text': '退款依据', 'source_id': 'doc', 'valid_from': '2020-01-01',
            'metadata': {**doc['metadata'], 'source_version_id': doc['version_id']}},
        {'id': 'pending', 'kind': 'entity', 'type': 'MissingType', 'text': '退款待建模',
            'valid_from': '2020-01-01', 'metadata': doc['metadata']}])
    service = KnowledgeService(repo, Encoder())
    _ready_keyword_index(service, monkeypatch)
    monkeypatch.setattr(service, 'ontology_labels', lambda *args: (_ for _ in ()).throw(
        AssertionError('no ontology context must skip labels')))
    monkeypatch.setattr(ScopedReads, 'neighbors', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('no graph must skip neighbor/path reads')))
    body = {'query': '退款', 'retrieval_mode': 'keyword', 'valid_at': '2026-01-01',
        'include_references': False, 'ontology_context': False, 'ontology_expansion': False,
        'graph_context': False, 'k_entities': 5, 'k_chunks': 5}
    result = question_context(service, project, body)
    assert {row['id'] for row in result['evidence_rows']} == {'chunk'}
    assert {row['id'] for row in result['structure_pending_rows']} == {'pending'}
    assert result['reasoning_paths'] == []
    document_only = question_context(service, project, {**body, 'kinds': ['chunk'], 'k_entities': 0})
    assert {row['kind'] for row in document_only['hits']} == {'chunk'}


def test_service_does_not_drop_explicit_per_kind_budgets_at_flattening(monkeypatch):
    from knowledge_service.services.service import KnowledgeService
    import knowledge_service.services.retrieval_context as context
    rows = [{'id': f'{kind}{index}', 'version_id': f'{kind}{index}v1', 'kind': kind,
        'text': '退款', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
        for kind in ('entity', 'relation', 'chunk') for index in range(50)]
    monkeypatch.setattr(RetrievalEngine, 'search', lambda *args, **kwargs: {
        'hits': rows, 'candidate_count': 150, 'active_mode': 'semantic',
        'degraded': False, '_backend': 'milvus', 'backends': {}})
    monkeypatch.setattr(context, 'enrich_source_context', lambda *args: None)
    service = KnowledgeService(SimpleNamespace(), Encoder())
    result = service.search('p', {'query': '退款', 'retrieval_mode': 'semantic',
        'ontology_context': False, 'k_entities': 50, 'k_chunks': 50, 'k_relations': 50})
    assert len(result['hits']) == 150
    assert all(sum(row['kind'] == kind for row in result['hits']) == 50
               for kind in ('entity', 'relation', 'chunk'))
