from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.milvus_store import MilvusStore


def test_first_index_is_queued_once_and_failed_automatic_jobs_back_off(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.jobs import Jobs
    repo = Repository(tmp_path)
    project = repo.create_project('自动索引')['id']
    repo.put_record(project, {'id':'chunk','kind':'chunk','text':'退款说明'})
    jobs = Jobs(repo, handlers={'semantic_index': lambda *args: None})
    jobs.start = Mock()
    first = jobs.enqueue_index(project, automatic=True)
    second = jobs.enqueue_index(project, automatic=True)
    assert first['id'] == second['id']
    first.update(status='failed', updated_at=__import__('knowledge_service.core.time', fromlist=['utc_now']).utc_now())
    repo.save_artifact('job', first)
    assert jobs.enqueue_index(project, automatic=True) is None
    manual = jobs.enqueue_index(project)
    assert manual['id'] != first['id']
    assert len(repo.list_artifacts('job', project)) == 2
    jobs.close()
    repo.close()


def test_write_nudge_does_not_run_embedding_in_caller_thread():
    from knowledge_service.services.projection_runtime import ProjectionWorker
    worker = ProjectionWorker(Mock(), SimpleNamespace(adapters={'milvus':Mock()}), cleanup_handlers={})
    worker.drain_project = Mock()
    worker.wake('project')
    worker.drain_project.assert_not_called()


def test_background_loop_builds_first_index_then_updates_incrementally(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.jobs import Jobs
    from knowledge_service.services.projection_runtime import MilvusProjectionAdapter, ProjectionWorker
    from knowledge_service.services.projection_sync import ProjectionSync
    from knowledge_service.services.retrieval import PROJECTION_SCHEMA_VERSIONS
    from test_shared_milvus_pipeline import MemoryMilvus
    repo=Repository(tmp_path)
    project=repo.create_project('自动流程')['id']
    empty=repo.create_project('新空项目')['id']
    store=MemoryMilvus()
    encoder=SimpleNamespace(identity='model',dimension=2,encode=lambda texts:[[1.,0.] for _ in texts])
    sync=ProjectionSync(repo.projections,{'milvus':MilvusProjectionAdapter(store,encoder=encoder)},schema_version=PROJECTION_SCHEMA_VERSIONS)
    jobs=Jobs(repo,handlers={'semantic_index':lambda *args:None});jobs.start=Mock()
    worker=ProjectionWorker(repo,sync,cleanup_handlers={});worker._next_audit=float('inf')
    worker.auto_rebuild=lambda p:jobs.enqueue_index(p,automatic=True)
    repo.put_record(project,{'id':'c','kind':'chunk','text':'退款说明'})
    worker.run_once();worker.run_once()
    assert len(repo.list_artifacts('job',project))==1
    assert not repo.list_artifacts('job',empty)
    job=repo.list_artifacts('job',project)[0]
    sync.rebuild(project,'milvus')
    job.update(status='completed');repo.save_artifact('job',job)
    repo.put_record(project,{'id':'c','kind':'chunk','text':'退款新说明'},expected_version=1)
    worker.run_once()
    assert repo.projection_ready(project,'milvus',PROJECTION_SCHEMA_VERSIONS['milvus'])
    assert len(repo.list_artifacts('job',project))==1
    assert [row['text'] for row in store.rows.values() if row['record_id']=='c']==['退款新说明']
    jobs.close();repo.close()


def test_document_vectors_reuse_across_parse_and_projection_and_model_changes(tmp_path):
    from knowledge_service.integrations.embedding_cache import CachedDocumentEncoder
    encoder = SimpleNamespace(identity='model-a', dimension=2,
        encode=Mock(side_effect=lambda texts: [[1., 0.] for _ in texts]))
    cached = CachedDocumentEncoder(encoder, tmp_path)
    assert np.allclose(cached.encode(['标题\n正文', '标题\n正文']), [[1, 0], [1, 0]])
    encoder.encode.assert_called_once_with(['标题\n正文'])
    CachedDocumentEncoder(encoder, tmp_path).encode(['标题\n正文'])
    assert encoder.encode.call_count == 1
    cached.encode(['新标题\n正文'])
    assert encoder.encode.call_count == 2
    encoder.identity = 'model-b'
    cached.encode(['标题\n正文'])
    assert encoder.encode.call_count == 3


def test_invalid_embedding_is_never_cached(tmp_path):
    from knowledge_service.integrations.embedding_cache import CachedDocumentEncoder
    import pytest
    encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(return_value=[[float('nan'), 0]]))
    cached = CachedDocumentEncoder(encoder, tmp_path)
    with pytest.raises(RuntimeError, match='向量'):
        cached.encode(['正文'])
    encoder.encode.return_value = [[1., 0.]]
    assert np.allclose(cached.encode(['正文']), [[1, 0]])
    assert encoder.encode.call_count == 2


def test_cache_directory_failure_does_not_block_valid_vector_output(tmp_path):
    from knowledge_service.integrations.embedding_cache import CachedDocumentEncoder
    root = tmp_path / 'not-a-directory'
    root.write_text('file', encoding='utf-8')
    encoder = SimpleNamespace(identity='model', dimension=2, encode=lambda texts: [[1., 0.] for _ in texts])
    assert CachedDocumentEncoder(encoder, root).encode(['正文']) == [[1., 0.]]


def test_semantic_threshold_filters_grouped_hits_without_unsupported_range_search():
    store = MilvusStore(dim=2)
    store._load_if_needed = Mock()
    store._client = Mock()
    store._client.search.return_value = [[
        {'distance': .8, 'entity': {'record_id': 'relevant', 'version_id': 'v1'}},
        {'distance': .1, 'entity': {'record_id': 'irrelevant', 'version_id': 'v2'}}]]
    hits = store.search('p', '退款', retrieval_mode='semantic', query_vector=[1., 0.],
        scope={'min_semantic_score': .5}, index_version=1, catalog=default_catalog())
    assert [h['record_id'] for h in hits] == ['relevant']
    assert 'radius' not in store._client.search.call_args.kwargs['search_params'].get('params',{})
    assert store._client.search.call_args.kwargs['group_by_field']=='record_id'


def test_fusion_filters_semantic_noise_before_rrf_but_keeps_keyword_matches():
    store = MilvusStore(dim=2)
    store._load_if_needed = Mock()
    store._client = Mock()
    def search(**kwargs):
        if kwargs['anns_field']=='sparse':return [[{'distance':2.,'entity':{'record_id':'keyword','version_id':'v1'}}]]
        return [[{'distance':.1,'entity':{'record_id':'noise','version_id':'v2'}}]]
    store._client.search.side_effect=search
    hits=store.search('p','退款',retrieval_mode='hybrid',query_vector=[1.,0.],
        scope={'min_semantic_score':.5},index_version=1,catalog=default_catalog())
    assert [hit['record_id'] for hit in hits]==['keyword']


def test_keyword_rerank_cannot_add_semantic_only_documents():
    store = MilvusStore(dim=2)
    store._load_if_needed = Mock()
    store._client = Mock()
    def search(**kwargs):
        if kwargs['anns_field'] == 'sparse':
            return [[{'distance': 2., 'entity': {'record_id': 'literal', 'version_id': 'v1'}}]]
        assert kwargs['filter_params']['eligible_versions'] == ['v1']
        return [[{'distance': .8, 'entity': {'record_id': 'literal', 'version_id': 'v1'}}]]
    store._client.search.side_effect = search
    hits = store.search('p', '退款', retrieval_mode='hybrid', query_vector=[1., 0.],
        scope={'hybrid_strategy': 'keyword_rerank', 'min_semantic_score': .35},
        index_version=1, catalog=default_catalog())
    assert [h['record_id'] for h in hits] == ['literal']
    store._client.hybrid_search.assert_not_called()
