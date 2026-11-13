from collections import Counter
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, RLock
from types import SimpleNamespace

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.models import Search
from knowledge_service.repository.core import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services import service as service_module
from knowledge_service.services.answers import answer_messages, _retrieved_question_context as question_context
from knowledge_service.services.projection_sync import ProjectionSync
from knowledge_service.services.retrieval import PROJECTION_SCHEMA_VERSIONS, RetrievalEngine
from knowledge_service.services.service import KnowledgeService


class VocabularyRepository:
    def distinct_applicability_values(self, _project_id):
        return {
            'platforms': ['美团', '京东'],
            'businesses': ['直播'],
            'audiences': [],
        }


def _activate_native_index(repository, project, *, embedding_model, dim):
    schema_version = PROJECTION_SCHEMA_VERSIONS['milvus']
    sync = ProjectionSync(repository.projections, {}, schema_version=schema_version)
    rebuild = repository.projections.begin_rebuild(
        project, 'milvus', schema_version, 'native-grouping-fixture',
        lease_guard=sync._rebuild_lease_guard, embedding_model=embedding_model, dim=dim)
    state = {**rebuild['state'], 'catalog': default_catalog().to_dict(),
        'search_capabilities': ['attributes_v1', 'attribute_windows_v1', 'source_contexts_v1',
            'entity_source_contexts_v1', 'relation_source_contexts_v1', 'entity_fact_contexts_v1']}
    with repository.projections.activation_guard(project, rebuild['claim'], schema_version) as watermark:
        assert watermark == rebuild['snapshot_seq']
        repository.projections.activate_index(rebuild['claim'], schema_version, watermark, state)
    cursor = repository.projections.cursor(project, 'milvus')
    assert cursor['status'] == 'ready' and cursor['schema_version'] == schema_version
    assert cursor['applied_seq'] == repository.projections.project_watermark(project)
    assert cursor['rebuild_state']['embedding_model'] == embedding_model
    assert cursor['rebuild_state']['dim'] == dim
    index = repository.projections.search_index(project, schema_version)
    assert (index['embedding_model'], index['dim'], index['watermark']) == (
        embedding_model, dim, watermark)
    assert index['search_capabilities'] == state['search_capabilities']
    return index


def test_platform_group_limits_are_validated_at_the_request_boundary():
    request = Search(query='直播', k_per_platform=20, k_unknown=3)
    assert request.k_per_platform == 20
    assert request.k_unknown == 3
    with pytest.raises(ValueError):
        Search(query='直播', k_per_platform=21)


def test_repository_current_version_batch_filters_in_sql_and_preserves_order():
    class Cursor:
        def fetchall(self):
            return [
                {'version_id': 'v2', 'id': 'two'},
                {'version_id': 'v1', 'id': 'one'},
            ]

    class Database:
        def execute(self, statement, params):
            self.statement = ' '.join(statement.split())
            self.params = params
            return Cursor()

    repository = Repository.__new__(Repository)
    repository._db = Database()
    repository._lock = RLock()
    repository.get_project = lambda _project_id: None
    repository._record = lambda row: dict(row)

    rows = repository.current_records_by_version_ids(
        'project', ['v1', 'v2', 'v1'])

    assert 'superseded_at IS NULL' in repository._db.statement
    assert repository._db.params == ('project', ['v1', 'v2'])
    assert [row['version_id'] for row in rows] == ['v1', 'v2', 'v1']


def native_service(monkeypatch, calls):
    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _project_id, _query, **kwargs):
            calls.append(kwargs)
            match = kwargs.get('platform_match')
            scope = kwargs['scope']
            platform = ((scope.get('applicability') or {}).get('platforms') or {})
            platform = (platform.get('values') or [None])[0]
            prefix = {'specific_only': platform, 'all_only': 'common',
                      'unknown_only': 'unknown'}.get(match, 'plain')
            count = kwargs['k']
            return {
                'hits': [{
                    'id': f'{prefix}-{index}', 'version_id': f'v-{prefix}-{index}',
                    'kind': 'chunk', 'text': f'{prefix} rule', 'metadata': {},
                } for index in range(count)],
                'candidate_count': count, 'active_mode': 'keyword',
                'degraded': False, '_backend': 'milvus', 'backends': {},
            }

    monkeypatch.setattr(service_module, 'RetrievalEngine', Engine)
    monkeypatch.setattr('knowledge_service.repository.scoped_reads.ScopedReads.source_context',
                        lambda *_args, **_kwargs: {'source_chunks': [], 'context_chunks': []})
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('exact-ready native retrieval must not preload scoped rows'))
    return service


def test_single_platform_search_splits_specific_common_and_unknown(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)

    result = service.search('project', {
        'query': '美团直播', 'retrieval_mode': 'keyword', 'k': 20,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}},
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 20,
    })

    assert [call['platform_match'] for call in calls] == [
        'specific_only', 'all_only']
    groups = {group['source_scope']: group for group in result['platform_groups']}
    assert len(groups['specific']['hits']) == 5
    assert len(groups['all']['hits']) == 5
    assert all(row['query_platform'] == '美团'
               for row in groups['specific']['hits'])
    assert all(row['source_scope'] == 'all' for row in groups['all']['hits'])
    assert 'unknown' not in groups
    assert len(result['hits']) == 10


def test_platform_free_query_uses_one_global_ranking_without_dictionary_limits(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 3,
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 3,
        'include_unknown': False,
    })

    groups = result['platform_groups']
    assert [(group['platform'], group['source_scope']) for group in groups] == [(None, 'unknown')]
    assert len(calls) == 1 and calls[0]['platform_match'] is None
    assert len(result['hits']) == 3
    assert len(result['hits']) <= 100
    assert all(call.get('candidates') is None for call in calls)


def test_question_context_reuses_grouped_native_results_and_excludes_unknown_evidence():
    specific = {
        'id': 'mt', 'kind': 'chunk', 'text': '美团规则', 'metadata': {},
        'query_platform': '美团', 'source_scope': 'specific'}
    common = {
        'id': 'all', 'kind': 'chunk', 'text': '通用规则', 'metadata': {},
        'query_platform': None, 'source_scope': 'all'}
    unknown = {
        'id': 'unknown', 'kind': 'chunk', 'text': '范围未知', 'metadata': {},
        'query_platform': None, 'source_scope': 'unknown'}

    class Service:
        encoder = SimpleNamespace(identity='test', semantic=True)
        repository = VocabularyRepository()

        def search(self, *_args, **_kwargs):
            return {
                'hits': [specific, common, unknown], 'candidate_count': 3,
                'active_mode': 'keyword', 'degraded': False,
                '_backend': 'milvus', 'backends': {}, 'applicability': {},
                'platform_groups': [
                    {'platform': '美团', 'source_scope': 'specific',
                     'hits': [specific], 'candidate_count': 1},
                    {'platform': None, 'source_scope': 'all',
                     'hits': [common], 'candidate_count': 1},
                    {'platform': None, 'source_scope': 'unknown',
                     'hits': [unknown], 'candidate_count': 1},
                ],
            }

        def scoped(self, *_args, **_kwargs):
            raise AssertionError('native QA must not preload scoped rows')

        def annotate_structure_pending(self, _project_id, _rows):
            return 0

    context = question_context(Service(), 'project', {
        'query': '美团直播', 'retrieval_mode': 'keyword'})

    assert [row['id'] for row in context['evidence_rows']] == ['mt', 'all']
    assert context['platform_groups'][2]['evidence_rows'] == []
    assert context['platform_groups'][2]['unknown_scope_rows'] == [unknown]
    messages = answer_messages('美团直播', [
        {'citation': f'E{index}', **row}
        for index, row in enumerate(context['evidence_rows'], 1)])
    payload = json.loads(messages[1]['content'])
    assert [(group['platform'], group['source_scope'])
            for group in payload['platform_evidence']] == [
                ('美团', 'specific'), (None, 'all')]
    assert '禁止把不同平台证据合并' in messages[0]['content']


def test_group_plan_keeps_every_platform_and_bounds_each_group(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    service.repository.distinct_applicability_values = lambda _project_id: {
        'platforms': [f'平台{index:02d}' for index in range(30)],
        'businesses': ['直播'], 'audiences': [],
    }

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 100,
        'applicability': {'platforms': {'mode': 'specific', 'values': [f'平台{index:02d}' for index in range(30)]}},
        'k_per_platform': 50, 'include_unknown': False,
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 50,
    })

    specific = [call for call in calls
                if call['platform_match'] == 'specific_only']
    assert len(specific) == 30
    assert all(call['k'] == 20 for call in specific)
    assert sum(call['platform_match'] == 'all_only' for call in calls) == 1
    groups = result['platform_groups']
    assert len(groups) == 31
    assert all(len(group['hits']) == 20 for group in groups)
    assert {group['platform'] for group in groups if group['source_scope'] == 'specific'} == {
        f'平台{index:02d}' for index in range(30)}
    # Each platform and the common group retain their independent budget.
    assert len(result['hits']) == 30 * 20 + 20
    assert len({row['version_id'] for row in result['hits']}) == len(result['hits'])


def test_flat_hits_deduplicate_versions_but_groups_keep_membership(monkeypatch):
    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _project_id, _query, **kwargs):
            platform = (((kwargs['scope'].get('applicability') or {})
                         .get('platforms') or {}).get('values') or [None])[0]
            return {
                'hits': [{'id': 'shared', 'version_id': 'same-version',
                          'kind': 'chunk', 'text': 'shared', 'metadata': {}}],
                'candidate_count': 1, 'active_mode': 'keyword',
                'degraded': False, '_backend': 'milvus', 'backends': {},
                'platform': platform,
            }

    monkeypatch.setattr(service_module, 'RetrievalEngine', Engine)
    monkeypatch.setattr('knowledge_service.repository.scoped_reads.ScopedReads.source_context',
                        lambda *_args, **_kwargs: {'source_chunks': [], 'context_chunks': []})
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: []

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'include_unknown': False,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}}})

    assert len(result['hits']) == 1
    assert [len(group['hits']) for group in result['platform_groups']] == [1, 1, 1]


def test_unrestricted_groups_follow_returned_metadata_without_vocabulary_queries(
        monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    service.repository.distinct_applicability_values = lambda _project_id: {
        'platforms': [], 'businesses': [], 'audiences': []}

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 10})

    assert [(group['platform'], group['source_scope'])
            for group in result['platform_groups']] == [
                (None, 'unknown')]


def test_ungrouped_unknown_scope_never_enters_answer_evidence():
    all_row = {
        'id': 'all', 'kind': 'chunk', 'text': '通用',
        'metadata': {'applicability': {
            'platforms': {'mode': 'all', 'values': []}}}}
    unknown_row = {
        'id': 'unknown', 'kind': 'chunk', 'text': '待确认',
        'metadata': {'applicability': {
            'platforms': {'mode': 'unknown', 'values': []}}}}

    class Service:
        encoder = SimpleNamespace(identity='test', semantic=True)
        repository = VocabularyRepository()

        def search(self, *_args, **_kwargs):
            return {
                'hits': [all_row, unknown_row], 'candidate_count': 2,
                'active_mode': 'keyword', 'degraded': False,
                '_backend': 'milvus', 'backends': {}, 'applicability': {}}

        def scoped(self, *_args, **_kwargs):
            raise AssertionError('native QA must not preload scoped rows')

        def annotate_structure_pending(self, _project_id, _rows):
            return 0

    context = question_context(Service(), 'project', {
        'query': '直播', 'retrieval_mode': 'keyword'})

    assert [row['id'] for row in context['evidence_rows']] == ['all']
    assert [row['id'] for row in context['unknown_scope_rows']] == ['unknown']


def test_native_backfill_filters_current_versions_without_per_hit_get_record(tmp_path, monkeypatch):
    repository = Repository(tmp_path / 'native-backfill')
    project = repository.create_project('native backfill')['id']
    current = repository.put_record(project, {'id': 'current', 'kind': 'chunk', 'text': 'current'})
    old = repository.put_record(project, {'id': 'old', 'kind': 'chunk', 'text': 'old'})
    repository.put_record(project, {'id': 'old', 'kind': 'chunk', 'text': 'new'}, expected_version=1)
    index = _activate_native_index(repository, project, embedding_model='test', dim=1)
    native_calls, hydration_calls = [], []
    by_ids = ScopedReads.by_ids

    def forbidden(*_args, **_kwargs):
        raise AssertionError('native backfill must use one bounded authority read, never broad or per-hit reads')

    def hydrate(reader, project_id, scope, ids, *, kinds=None):
        hydration_calls.append((project_id, scope, ids, kinds))
        return by_ids(reader, project_id, scope, ids, kinds=kinds)

    monkeypatch.setattr(repository, 'get_record', forbidden)
    monkeypatch.setattr(repository, 'query', forbidden)
    monkeypatch.setattr(ScopedReads, 'page', forbidden)
    monkeypatch.setattr(ScopedReads, 'by_ids', hydrate)

    class Milvus:
        available = True

        def search(self, project_id, query, **options):
            native_calls.append((project_id, query, options))
            return [{'record_id': current['id'], 'version_id': current['version_id'], 'score': 0.9},
                    {'record_id': old['id'], 'version_id': old['version_id'], 'score': 0.8}]

    encoder = SimpleNamespace(identity='test', semantic=True,
                              encode=lambda texts: [[1.0] for _ in texts])
    result = RetrievalEngine(repository, encoder, Milvus()).search(
        project, '直播', retrieval_mode='keyword')

    assert [row['id'] for row in result['hits']] == ['current']
    assert result['hits'][0]['version_id'] == current['version_id']
    assert len(native_calls) == 1
    assert native_calls[0][:2] == (project, '直播')
    assert native_calls[0][2]['retrieval_mode'] == 'keyword'
    assert native_calls[0][2]['query_vector'] is None
    assert native_calls[0][2]['index_version'] == index['index_version']
    assert len(hydration_calls) == 1
    assert hydration_calls[0][0] == project
    assert hydration_calls[0][2:] == (['current', 'old'], ['entity', 'relation', 'attribute', 'chunk'])


def test_mixed_group_backend_uses_bounded_authority_for_source_and_context(
        monkeypatch):
    authority = [
        {'id': 'entity', 'version_id': 'entity-v', 'kind': 'entity',
         'text': '美团主体', 'metadata': {'chunk_id': 'c1'}},
        {'id': 'c1', 'version_id': 'c1-v', 'kind': 'chunk', 'text': '来源',
         'source_id': 'doc', 'metadata': {
             'start_char': 0, 'source_version_id': 'doc-v'}},
        {'id': 'c2', 'version_id': 'c2-v', 'kind': 'chunk', 'text': '相邻',
         'source_id': 'doc', 'metadata': {
             'start_char': 10, 'source_version_id': 'doc-v'}},
    ]

    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _project_id, _query, **kwargs):
            values = (((kwargs['scope'].get('applicability') or {})
                      .get('platforms') or {}).get('values') or [])
            assert 'fallback_cache' not in kwargs
            if values == ['美团']:
                backend, hits = 'postgres_history', [authority[0]]
            else:
                backend, hits = 'milvus', []
            return {'hits': hits, 'candidate_count': len(hits),
                    'active_mode': 'keyword', 'degraded': False,
                    '_backend': backend, 'backends': {}}

    source_reads = []

    class Reader:
        def __init__(self, repository):
            assert repository is service.repository

        def source_context(self, project, scope, hits):
            source_reads.append((project, scope, hits))
            return ({'source_chunks': [{**authority[1], '_source_anchor_versions': ['entity-v']}],
                     'context_chunks': [{**authority[2], '_source_anchor_versions': ['entity-v']}]}
                    if hits else {'source_chunks': [], 'context_chunks': []})

    monkeypatch.setattr(service_module, 'RetrievalEngine', Engine)
    monkeypatch.setattr('knowledge_service.repository.scoped_reads.ScopedReads', Reader)
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.repository._db = object()
    service.repository.query = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('source context must use bounded authority reads'))
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('grouped source context must not preload authority rows'))

    request = {
        'query': '直播', 'retrieval_mode': 'keyword',
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}},
        'filters': {'field': 'metadata.release', 'op': 'eq', 'value': 'stable'},
        'known_at': '2026-10-18T00:00:00Z', 'valid_at': '2026-10-17T00:00:00Z',
        'include_unknown': False}
    result = service.search('project', request)

    assert result['_backend'] == 'mixed'
    assert len(source_reads) == 3
    assert [scope['_platform_match'] for _, scope, _ in source_reads] == [
        'specific_only', 'specific_only', 'all_only']
    assert {tuple(scope['applicability']['platforms']['values']) for _, scope, _ in source_reads[:2]} == {
        ('美团',), ('京东',)}
    populated = [(scope, hits) for _, scope, hits in source_reads if hits]
    assert len(populated) == 1
    assert populated[0][0]['applicability']['platforms'] == {'mode': 'specific', 'values': ['美团']}
    assert [row['id'] for row in populated[0][1]] == ['entity']
    for project, scope, _ in source_reads:
        assert project == 'project'
        for key in ('filters', 'known_at', 'valid_at', 'include_unknown'):
            assert scope[key] == request[key]
    group = next(group for group in result['platform_groups']
                 if group['platform'] == '美团')
    assert [row['id'] for row in group['source_chunks']] == ['c1']
    assert [row['id'] for row in group['context_chunks']] == ['c2']
    authorizations = result['_source_context_authorizations']
    assert len(authorizations) == 2
    assert {(proof['artifact_id'], proof['artifact_version_id'], proof['anchor_id'], proof['anchor_version_id'])
            for proof in authorizations.values()} == {
                ('c1', 'c1-v', 'entity', 'entity-v'), ('c2', 'c2-v', 'entity', 'entity-v')}
    for row in [*group['source_chunks'], *group['context_chunks']]:
        proof = authorizations[row['_source_context_authorization']]
        assert proof['query_platform'] == '美团' and proof['source_scope'] == 'specific'
        assert '_source_anchor_versions' not in row


def test_concurrent_platform_searches_without_milvus_fail_before_authority_reads(monkeypatch):
    # The 2026-10-19 redesign retired automatic local fallback. Its old one-read
    # cache budget is now zero: an unavailable index cannot authorize a preload.
    calls, lock = Counter(), Lock()

    def forbidden(name):
        def read(*_args, **_kwargs):
            with lock:
                calls[name] += 1
            raise AssertionError('missing Milvus must fail before any authority read')
        return read

    repository = SimpleNamespace(query=forbidden('query'),
        keyword_candidates=forbidden('keyword_candidates'),
        current_records_by_version_ids=forbidden('current_records_by_version_ids'))
    monkeypatch.setattr(ScopedReads, 'by_ids', forbidden('by_ids'))
    monkeypatch.setattr(ScopedReads, 'page', forbidden('page'))
    monkeypatch.setattr(ScopedReads, 'ranked_keyword', forbidden('ranked_keyword'))
    encoder = SimpleNamespace(identity='test', semantic=False)
    engine = RetrievalEngine(repository, encoder)
    with pytest.raises(TypeError, match='fallback_cache'):
        engine.search('project', '直播', retrieval_mode='keyword',
                      fallback_cache={'applicability': {}, 'lock': Lock()})
    barrier = Barrier(4)

    def search(platform):
        barrier.wait()
        with pytest.raises(RuntimeError, match='Milvus') as error:
            engine.search('project', '直播', retrieval_mode='keyword',
                scope={'applicability': {
                    'platforms': {'mode': 'specific', 'values': [platform]}}},
                platform_match='specific_only')
        return error.value

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(search, ['美团', '京东', '抖音', '快手']))

    assert len(results) == 4 and all(isinstance(error, RuntimeError) for error in results)
    assert calls == Counter()


def test_grouped_mixed_question_context_does_not_rescan_scoped_rows():
    hit = {
        'id': 'mt', 'kind': 'chunk', 'text': '美团规则', 'metadata': {},
        'query_platform': '美团', 'source_scope': 'specific'}

    class Service:
        encoder = SimpleNamespace(identity='test', semantic=True)
        repository = VocabularyRepository()

        def search(self, *_args, **_kwargs):
            return {
                'hits': [hit], 'candidate_count': 1, 'active_mode': 'keyword',
                'degraded': True, '_backend': 'mixed', 'backends': {},
                'applicability': {}, 'platform_groups': [{
                    'platform': '美团', 'source_scope': 'specific',
                    'hits': [hit], 'candidate_count': 1,
                    'active_mode': 'keyword', '_backend': 'postgres_local',
                    'backends': {}, 'degraded': True,
                    'retrieval_errors': {},
                }]}

        def scoped(self, *_args, **_kwargs):
            raise AssertionError('grouped mixed QA must reuse enriched groups')

        def annotate_structure_pending(self, _project_id, _rows):
            return 0

    context = question_context(Service(), 'project', {
        'query': '美团直播', 'retrieval_mode': 'keyword'})

    assert [row['id'] for row in context['evidence_rows']] == ['mt']
    assert context['_backend'] == 'mixed'


def test_group_backend_errors_are_preserved_and_exposed_to_qa(monkeypatch):
    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _project_id, _query, **kwargs):
            platform = (((kwargs['scope'].get('applicability') or {})
                         .get('platforms') or {}).get('values') or [None])[0]
            if kwargs['platform_match'] == 'specific_only':
                backend = 'semantic' if platform == '美团' else 'keyword'
                error = f'{platform}-{backend}-failed'
                return {
                    'hits': [], 'candidate_count': 0,
                    'active_mode': 'keyword', 'degraded': True,
                    '_backend': 'postgres_local',
                    'backends': {backend: {
                        'active': False, 'hits': 0, 'error': error}},
                }
            return {
                'hits': [], 'candidate_count': 0,
                'active_mode': 'keyword', 'degraded': False,
                '_backend': 'milvus',
                'backends': {'milvus': {'active': True, 'hits': 0}},
            }

    monkeypatch.setattr(service_module, 'RetrievalEngine', Engine)
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('grouped mixed QA must not rescan scoped rows'))
    service.annotate_structure_pending = lambda *_args, **_kwargs: 0

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword',
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}},
        'include_unknown': False})

    groups = {group['platform']: group for group in result['platform_groups']}
    assert groups['美团']['backends']['semantic']['error'] == '美团-semantic-failed'
    assert groups['京东']['backends']['keyword']['error'] == '京东-keyword-failed'
    assert groups['美团']['retrieval_errors'] == {
        'semantic': '美团-semantic-failed'}
    assert groups['京东']['retrieval_errors'] == {
        'keyword': '京东-keyword-failed'}
    assert groups['美团']['degraded'] is True
    assert groups['京东']['degraded'] is True
    assert result['retrieval_errors'] == {
        '美团:semantic': '美团-semantic-failed',
        '京东:keyword': '京东-keyword-failed',
    }

    class ContextService:
        encoder = service.encoder
        repository = service.repository
        search = lambda *_args, **_kwargs: result
        scoped = service.scoped
        annotate_structure_pending = service.annotate_structure_pending

    context = question_context(ContextService(), 'project', {
        'query': '直播', 'retrieval_mode': 'keyword'})

    assert context['retrieval_errors'] == result['retrieval_errors']


@pytest.mark.parametrize('retrieval_mode', ['semantic', 'hybrid'])
def test_grouped_semantic_search_encodes_query_once_for_all_platforms(
        retrieval_mode, tmp_path):
    class Encoder:
        identity = 'test'
        semantic = True

        def __init__(self):
            self.calls = 0
            self.lock = Lock()

        def encode(self, texts):
            with self.lock:
                self.calls += 1
            return [[1.0, 0.0] for _ in texts]

    calls, calls_lock = [], Lock()

    class Milvus:
        available = True

        def search(self, project_id, query, **options):
            with calls_lock:
                calls.append((project_id, query, options))
            return []

    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = Repository(tmp_path / 'grouped-query-vector')
    project = service.repository.create_project('grouped query vector')['id']
    service.encoder = Encoder()
    index = _activate_native_index(service.repository, project,
        embedding_model=service.encoder.identity, dim=2)
    service.milvus_store = Milvus()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}

    result = service.search(project, {
        'query': '直播', 'retrieval_mode': retrieval_mode,
        'applicability': {'platforms': {'mode': 'specific', 'values': [f'平台{index:02d}' for index in range(20)]}},
        'include_unknown': True})

    assert len(result['platform_groups']) == 21
    assert service.encoder.calls == 1
    assert len(calls) == 84
    assert Counter((tuple(call[2]['kinds']), call[2]['platform_match']) for call in calls) == Counter({
        ((kind,), match): count for kind in ('entity', 'chunk', 'relation', 'attribute')
        for match, count in (('specific_only', 20), ('all_only', 1))})
    platform_calls = Counter()
    for project_id, query, options in calls:
        assert (project_id, query) == (project, '直播')
        assert options['retrieval_mode'] == retrieval_mode
        assert options['query_vector'] == [1.0, 0.0]
        assert options['index_version'] == index['index_version']
        assert options['catalog'] == default_catalog()
        assert 5 <= options['k'] <= RetrievalEngine.MAX_NATIVE_LIMIT
        if options['platform_match'] == 'specific_only':
            platform_calls.update(options['scope']['applicability']['platforms']['values'])
    assert platform_calls == Counter({f'平台{index:02d}': 4 for index in range(20)})
    assert all(group['hits'] == [] for group in result['platform_groups'])
    assert result['channel_quotas'] == {'entity': 5, 'chunk': 5, 'relation': 5, 'attribute': 5}
    assert [(group['platform'], group['source_scope']) for group in result['platform_groups']] == [
        *[(f'平台{index:02d}', 'specific') for index in range(20)], (None, 'all')]
