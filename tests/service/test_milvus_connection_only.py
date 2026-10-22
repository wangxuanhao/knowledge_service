"""Startup/reconnect may connect to Milvus, but only page jobs create indexes."""
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from knowledge_service import api
from knowledge_service.services.service import KnowledgeService


class ConnectionOnlyTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.instances = []
        events, instances = self.events, self.instances

        class RecordingStore:
            def __init__(self, **settings):
                self.settings = settings
                instances.append(self)

            def connect(self):
                events.append(('connect', self))
                return self

            def ensure_collection(self):
                # This models the existing implicit shared-base creation path.
                events.append(('create_shared_collection_and_indexes', self))
                return self

            def close(self):
                events.append(('close', self))

        self.store_class = RecordingStore
        self.encoder = SimpleNamespace(identity='unit-model')
        self.environment = patch.dict(os.environ, {
            'KG_VECTOR_BACKEND': 'milvus', 'KG_MILVUS_DIM': '2',
            'KG_MILVUS_HOST': 'unit-host', 'KG_MILVUS_PORT': '19530',
            'KG_MILVUS_COLLECTION': 'unit_base',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store_patch = patch.object(api, 'MilvusStore', RecordingStore)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def test_startup_connection_never_creates_a_shared_collection_or_indexes(self):
        diagnostics = []
        store = api._build_milvus_store(self.encoder, diagnostics=diagnostics)

        self.assertIs(store, self.instances[0])
        self.assertEqual(self.events, [('connect', store)])
        self.assertEqual(diagnostics, [])
        self.assertEqual(store.settings, {
            'host': 'unit-host', 'port': '19530', 'dim': 2,
            'encoder_identity': 'unit-model', 'collection': 'knowledge_vectors',
        })

    def test_retry_after_unavailable_startup_connects_without_creating_indexes(self):
        service = KnowledgeService.__new__(KnowledgeService)
        service.lock = threading.RLock()
        service.milvus_store = None
        service.vector_connector = lambda: api._build_milvus_store(self.encoder)
        service.runtime_capabilities = SimpleNamespace(
            milvus_store=None, milvus_error='startup unavailable')

        store = service.reconnect_vector_store()

        self.assertIs(service.milvus_store, store)
        self.assertIs(service.runtime_capabilities.milvus_store, store)
        self.assertIsNone(service.runtime_capabilities.milvus_error)
        self.assertEqual(self.events, [('connect', store)])

    def test_forced_reconnect_closes_old_connection_without_creating_indexes(self):
        old_store = api._build_milvus_store(self.encoder)
        service = KnowledgeService.__new__(KnowledgeService)
        service.lock = threading.RLock()
        service.milvus_store = old_store
        service.vector_connector = lambda: api._build_milvus_store(self.encoder)

        new_store = service.reconnect_vector_store(force=True)

        self.assertIsNot(new_store, old_store)
        self.assertEqual(self.events, [
            ('connect', old_store), ('close', old_store), ('connect', new_store)])

    def test_disabled_backend_does_not_connect_or_create_indexes(self):
        with patch.dict(os.environ, {'KG_VECTOR_BACKEND': ''}):
            self.assertIsNone(api._build_milvus_store(self.encoder))
        self.assertEqual(self.instances, [])
        self.assertEqual(self.events, [])

    def test_connection_failure_stays_unavailable_without_attempting_creation(self):
        def unavailable(store):
            self.events.append(('connect_failed', store))
            raise ConnectionError('unit connection unavailable')

        diagnostics = []
        with patch.object(self.store_class, 'connect', unavailable):
            with self.assertLogs('knowledge_service', level='WARNING'):
                result = api._build_milvus_store(self.encoder, diagnostics=diagnostics)

        self.assertIsNone(result)
        self.assertEqual(self.events, [('connect_failed', self.instances[0])])
        self.assertEqual(diagnostics, ['unit connection unavailable'])


if __name__ == '__main__':
    unittest.main()
