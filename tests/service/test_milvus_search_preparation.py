from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from threading import Event

import pytest

from knowledge_service.integrations.milvus_store import MilvusStore


def prepared_store():
    store = MilvusStore(dim=2)
    calls = []
    store._client = SimpleNamespace(load_collection=lambda name: calls.append(('load', name)), close=lambda: None)
    store.ensure_collection = lambda **kw: calls.append(('validate', kw['destination']))
    return store, calls


def test_warm_queries_prepare_once_even_with_concurrent_platform_groups():
    store, calls = prepared_store()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(store._load_if_needed, ['generation'] * 8)) == ['generation'] * 8
    assert calls == [('validate', 'generation'), ('load', 'generation')]
    store._load_if_needed('next-generation')
    assert calls[-2:] == [('validate', 'next-generation'), ('load', 'next-generation')]


def test_failed_load_is_not_cached_or_hidden():
    store, calls = prepared_store()
    def fail(name):
        raise RuntimeError('temporarily unavailable')
    store._client.load_collection = fail
    with pytest.raises(RuntimeError, match='temporarily unavailable'):
        store._load_if_needed('g')
    store._client.load_collection = lambda name: calls.append(('load', name))
    store._load_if_needed('g')
    assert calls == [('validate', 'g'), ('validate', 'g'), ('load', 'g')]


def test_preparation_expires_and_close_discards_it(monkeypatch):
    from knowledge_service.integrations import milvus_store as module
    clock = [100.]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0], raising=False)
    store, calls = prepared_store()
    store._load_if_needed('g')
    clock[0] += 61
    store._load_if_needed('g')
    assert len(calls) == 4
    store.close()
    assert not store._prepared_collections


def test_search_failure_discards_preparation_for_retry():
    store, calls = prepared_store()
    store._search_destination = lambda p: 'g'
    def fail(**kwargs):
        raise RuntimeError('collection unavailable')
    store._client.search = fail
    with pytest.raises(RuntimeError):
        store.search_bm25('p', 'query')
    assert not store._prepared_collections
    store._client.search = lambda **kw: [[]]
    assert store.search_bm25('p', 'query') == []
    assert len(calls) == 4


def test_close_waits_for_preparation_before_invalidating():
    store, _ = prepared_store()
    entered, release, closed = Event(), Event(), Event()
    def validate(**kwargs):
        entered.set()
        assert release.wait(5)
    store.ensure_collection = validate
    store._client.close = closed.set
    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(store._load_if_needed, 'g')
        assert entered.wait(5)
        closing = pool.submit(store.close)
        try:
            assert not closed.wait(.1), 'close interleaved with collection validation'
        finally:
            release.set()
        preparing.result()
        closing.result()
    assert not store._prepared_collections
