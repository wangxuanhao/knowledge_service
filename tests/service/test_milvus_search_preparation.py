from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from threading import Event

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.milvus_store import COLLECTION, MilvusStore, _TYPES


def prepared_store():
    store = MilvusStore(dim=2)
    calls = []
    catalog = default_catalog()
    columns = catalog.column_defs()
    assert len(columns) == 21
    assert not any(column.name.startswith('bf_') for column in columns)
    fields = []
    indexes = {'dense': {'field_name': 'embedding', 'index_type': 'HNSW',
                         'metric_type': 'COSINE', 'state': 'Finished'},
               'bm25': {'field_name': 'sparse', 'index_type': 'SPARSE_INVERTED_INDEX',
                        'metric_type': 'BM25', 'state': 'Finished'}}
    for column in columns:
        params = {}
        if column.max_length is not None:
            params['max_length'] = column.max_length
        if column.max_capacity is not None:
            params['max_capacity'] = column.max_capacity
        if column.name == 'embedding':
            params['dim'] = store.dim
        if column.name == 'text':
            params.update(enable_analyzer=True, analyzer_params={'type': 'chinese'})
        fields.append({'name': column.name, 'type': _TYPES[column.field_type],
                       'nullable': column.nullable, 'is_primary': column.name == 'vector_id',
                       'is_partition_key': column.name == 'project_id',
                       'element_type': _TYPES['varchar'], 'params': params})
        if column.index:
            indexes['filter_' + column.name] = {'field_name': column.name,
                'index_type': 'INVERTED', 'state': 'Finished'}
    def metadata():
        calls.append(('validate', COLLECTION))
        return {'auto_id': False, 'enable_dynamic_field': False, 'fields': fields, 'functions': [
            {'name': 'text_bm25', 'type': 1, 'input_field_names': ['text'],
             'output_field_names': ['sparse']}]}, indexes
    store._metadata = metadata
    store._client = SimpleNamespace(
        load_collection=lambda name, **kwargs: calls.append(('load', name)),
        search=lambda **kwargs: [[]], close=lambda: None)
    return store, calls


def test_warm_queries_prepare_once_even_with_concurrent_platform_groups():
    store, calls = prepared_store()
    catalog = default_catalog()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: store._load_if_needed(catalog=catalog), range(8))) == [None] * 8
    assert calls == [('validate', COLLECTION), ('load', COLLECTION)]
    # A different project/build uses the same physically prepared collection,
    # while its native query must still carry its own authority scope.
    requests = []
    store._client.search = lambda **kwargs: requests.append(kwargs) or [[]]
    assert store.search('other', 'query', retrieval_mode='keyword', index_version=2,
                        catalog=default_catalog()) == []
    assert calls == [('validate', COLLECTION), ('load', COLLECTION)]
    assert requests[0]['collection_name'] == COLLECTION
    assert requests[0]['filter_params']['index_version'] == 2
    assert 'other' in requests[0]['filter_params'].values()
    assert requests[0]['consistency_level'] == 'Strong'


def test_failed_load_is_not_cached_or_hidden():
    store, calls = prepared_store()
    def fail(name, **kwargs):
        raise RuntimeError('temporarily unavailable')
    store._client.load_collection = fail
    with pytest.raises(RuntimeError, match='temporarily unavailable'):
        store._load_if_needed(catalog=default_catalog())
    assert store._loaded_at == 0
    assert not store._validated
    store._client.load_collection = lambda name, **kwargs: calls.append(('load', name))
    store._load_if_needed(catalog=default_catalog())
    assert calls == [('validate', COLLECTION), ('validate', COLLECTION), ('load', COLLECTION)]


def test_preparation_expires_and_close_discards_it(monkeypatch):
    from knowledge_service.integrations import milvus_store as module
    clock = [100.]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    store, calls = prepared_store()
    store._load_if_needed(catalog=default_catalog())
    clock[0] += 61
    store._load_if_needed(catalog=default_catalog())
    assert len(calls) == 4
    store.close()
    assert not store._validated
    assert store._loaded_at == 0
    assert store._schema_cache is None


def test_search_failure_discards_preparation_for_retry():
    store, calls = prepared_store()
    def fail(**kwargs):
        raise RuntimeError('collection unavailable')
    store._client.search = fail
    with pytest.raises(RuntimeError):
        store.search('p', 'query', retrieval_mode='keyword', index_version=1,
                     catalog=default_catalog())
    assert not store._validated
    assert store._loaded_at == 0
    store._client.search = lambda **kw: [[]]
    assert store.search('p', 'query', retrieval_mode='keyword', index_version=1,
                        catalog=default_catalog()) == []
    assert len(calls) == 4


def test_close_waits_for_preparation_before_invalidating():
    store, _ = prepared_store()
    entered, release, closed = Event(), Event(), Event()
    metadata = store._metadata
    def validate():
        entered.set()
        assert release.wait(5)
        return metadata()
    store._metadata = validate
    store._client.close = closed.set
    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(store._load_if_needed, catalog=default_catalog())
        assert entered.wait(5)
        closing = pool.submit(store.close)
        try:
            assert not closed.wait(.1), 'close interleaved with collection validation'
        finally:
            release.set()
        preparing.result()
        closing.result()
    assert not store._validated
    assert store._loaded_at == 0
