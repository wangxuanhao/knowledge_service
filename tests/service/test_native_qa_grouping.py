import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, RLock
from types import SimpleNamespace

import pytest

from knowledge_service.models import Search
from knowledge_service.repository.core import Repository
from knowledge_service.services import service as service_module
from knowledge_service.services.answers import answer_messages, _retrieved_question_context as question_context
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.services.service import KnowledgeService


class VocabularyRepository:
    def distinct_applicability_values(self, _project_id):
        return {
            'platforms': ['美团', '京东'],
            'businesses': ['直播'],
            'audiences': [],
        }


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
    repository._record = lambda row, vectors='list': dict(row)

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


def test_platform_free_query_enumerates_dictionary_with_one_common_group(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 3,
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 3,
        'include_unknown': False,
    })

    groups = result['platform_groups']
    assert [(group['platform'], group['source_scope']) for group in groups] == [
        ('美团', 'specific'), ('京东', 'specific'), (None, 'all')]
    assert sum(call['platform_match'] == 'all_only' for call in calls) == 1
    assert len(result['hits']) == 15
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


def test_group_plan_is_bounded_and_per_platform_limit_is_configurable(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    service.repository.distinct_applicability_values = lambda _project_id: {
        'platforms': [f'平台{index:02d}' for index in range(30)],
        'businesses': ['直播'], 'audiences': [],
    }

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 100,
        'k_per_platform': 50, 'include_unknown': False,
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 50,
    })

    specific = [call for call in calls
                if call['platform_match'] == 'specific_only']
    assert len(specific) == 20
    assert all(call['k'] == 20 for call in specific)
    assert sum(call['platform_match'] == 'all_only' for call in calls) == 1
    assert len(result['hits']) <= 100


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
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: []

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'include_unknown': False})

    assert len(result['hits']) == 1
    assert [len(group['hits']) for group in result['platform_groups']] == [1, 1, 1]


def test_common_and_unknown_groups_exist_without_specific_platform_vocabulary(
        monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    service.repository.distinct_applicability_values = lambda _project_id: {
        'platforms': [], 'businesses': [], 'audiences': []}

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword', 'k': 10})

    assert [(group['platform'], group['source_scope'])
            for group in result['platform_groups']] == [
                (None, 'all'), (None, 'unknown')]


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


def test_native_backfill_filters_current_versions_without_per_hit_get_record():
    class Repository:
        def projection_ready(self, *_args):
            return True

        def current_records_by_version_ids(self, _project_id, version_ids):
            rows = {
                'current-v': {'id': 'current', 'version_id': 'current-v',
                              'kind': 'chunk', 'text': 'current', 'metadata': {},
                              'superseded_at': None},
                'old-v': {'id': 'old', 'version_id': 'old-v', 'kind': 'chunk',
                          'text': 'old', 'metadata': {},
                          'superseded_at': '2026-01-01T00:00:00Z'},
            }
            return [rows[item] for item in version_ids
                    if rows[item]['superseded_at'] is None]

        def get_record(self, *_args):
            raise AssertionError('native backfill must not perform per-hit reads')

    class Milvus:
        available = True

        def search_bm25(self, *_args, **_kwargs):
            return [{'version_id': 'current-v', 'score': 0.9},
                    {'version_id': 'old-v', 'score': 0.8}]

    encoder = SimpleNamespace(identity='test', semantic=True,
                              encode=lambda texts: [[1.0] for _ in texts])
    result = RetrievalEngine(Repository(), encoder, Milvus()).search(
        'project', '直播', retrieval_mode='keyword')

    assert [row['id'] for row in result['hits']] == ['current']


def test_mixed_group_backend_reuses_fallback_rows_for_source_and_context(
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
            fallback = kwargs['fallback_cache']
            if values == ['美团']:
                fallback.setdefault('rows', authority)
                backend, hits = 'postgres_local', [authority[0]]
            else:
                backend, hits = 'milvus', []
            return {'hits': hits, 'candidate_count': len(hits),
                    'active_mode': 'keyword', 'degraded': backend != 'milvus',
                    '_backend': backend, 'backends': {}}

    monkeypatch.setattr(service_module, 'RetrievalEngine', Engine)
    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = VocabularyRepository()
    service.encoder = SimpleNamespace(identity='test', semantic=True)
    service.milvus_store = object()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}
    service.scoped = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('group fallback must reuse its authority cache'))

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': 'keyword',
        'include_unknown': False})

    assert result['_backend'] == 'mixed'
    group = next(group for group in result['platform_groups']
                 if group['platform'] == '美团')
    assert [row['id'] for row in group['source_chunks']] == ['c1']
    assert [row['id'] for row in group['context_chunks']] == ['c2']


def test_concurrent_platform_fallbacks_share_one_authority_query():
    class Repository:
        def __init__(self):
            self.calls = 0
            self.calls_lock = Lock()

        def query(self, *_args, **_kwargs):
            with self.calls_lock:
                self.calls += 1
            time.sleep(0.05)
            return []

        def keyword_candidates(self, *_args, **_kwargs):
            return []

    repository = Repository()
    encoder = SimpleNamespace(identity='test', semantic=False)
    engine = RetrievalEngine(repository, encoder)
    fallback_cache = {'applicability': {}, 'lock': Lock()}

    def search(platform):
        return engine.search(
            'project', '直播', retrieval_mode='keyword',
            scope={'applicability': {
                'platforms': {'mode': 'specific', 'values': [platform]}}},
            platform_match='specific_only', fallback_cache=fallback_cache)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(search, ['美团', '京东', '抖音', '快手']))

    assert repository.calls == 1
    assert all(result['hits'] == [] for result in results)


def test_grouped_mixed_question_context_does_not_rescan_scoped_rows():
    hit = {
        'id': 'mt', 'kind': 'chunk', 'text': '美团规则', 'metadata': {},
        'query_platform': '美团', 'source_scope': 'specific'}

    class Service:
        encoder = SimpleNamespace(identity='test', semantic=True)

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
        search = lambda *_args, **_kwargs: result
        scoped = service.scoped
        annotate_structure_pending = service.annotate_structure_pending

    context = question_context(ContextService(), 'project', {
        'query': '直播', 'retrieval_mode': 'keyword'})

    assert context['retrieval_errors'] == result['retrieval_errors']


@pytest.mark.parametrize('retrieval_mode', ['semantic', 'hybrid'])
def test_grouped_semantic_search_encodes_query_once_for_all_platforms(
        retrieval_mode):
    class Repository(VocabularyRepository):
        def distinct_applicability_values(self, _project_id):
            return {
                'platforms': [f'平台{index:02d}' for index in range(20)],
                'businesses': [], 'audiences': [],
            }

        def projection_ready(self, *_args):
            return True

        def current_records_by_version_ids(self, _project_id, _version_ids):
            return []

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

    class Milvus:
        available = True

        def search_dense(self, *_args, **_kwargs):
            return []

        def search_hybrid(self, *_args, **_kwargs):
            return []

    service = KnowledgeService.__new__(KnowledgeService)
    service.repository = Repository()
    service.encoder = Encoder()
    service.milvus_store = Milvus()
    service.ontology_expansion = lambda *_args, **_kwargs: None
    service.ontology_labels = lambda _project_id: {}

    result = service.search('project', {
        'query': '直播', 'retrieval_mode': retrieval_mode,
        'include_unknown': True})

    assert len(result['platform_groups']) == 22
    assert service.encoder.calls == 1
