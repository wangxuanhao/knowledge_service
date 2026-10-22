"""Ontology presentation caches must not acquire the service writer lock.

This unittest harness exercises real service/graph presentation code without
PostgreSQL, so it can also reproduce lock queueing without a test database.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from copy import deepcopy
import unittest
from unittest.mock import patch

from knowledge_service.services import graph_workspace
from knowledge_service.services.service import KnowledgeService


TURTLE = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Thing> a owl:Class .'
RECORD = {'id': 'a', 'kind': 'entity', 'type': 'urn:Thing', 'text': 'first',
          'ontology_id': 'release', 'metadata': {}, 'version_id': 'a-v1', 'version': 1}


class ReadRepository:
    @contextmanager
    def independent_read_view(self):
        yield ReadRepository()

    def list_ontologies(self, project):
        return [{'id': 'release', 'turtle': TURTLE}]


class Reads:
    def __init__(self, repository):
        self.repo = repository

    def page(self, *args, **kwargs):
        return {'records': [deepcopy(RECORD)], 'total': 1, 'next_cursor': None}

    def by_ids(self, project, scope, ids, **kwargs):
        return [deepcopy(RECORD)] if 'a' in ids else []

    def neighbors(self, *args, **kwargs):
        return {'records': [deepcopy(RECORD)], 'reasoning_paths': [], 'truncated': False}

    def entity_attributes(self, *args, **kwargs):
        return {'records': [], 'truncated': False}


class OntologyCacheLockTests(unittest.TestCase):
    def setUp(self):
        self.service = KnowledgeService(ReadRepository(), encoder=None)

    def while_writer_locked(self, operation):
        blocked = False
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.service.lock:
                future = executor.submit(operation)
                try:
                    result = future.result(timeout=0.5)
                except TimeoutError:
                    blocked = True
            if blocked:
                self.fail('ontology presentation waited for the service writer lock')
        return result

    def graph(self, operation):
        values = ({'query': '', 'cursor': None, 'limit': 10, 'include_total': True, 'record_ids': None}
                  if operation == 'records_page' else
                  {'seeds': ['a'], 'hops': 1, 'direction': 'forward', 'max_nodes': 10, 'max_edges': 10})
        with patch.object(graph_workspace, 'ScopedReads', Reads):
            workspace = graph_workspace.GraphWorkspace(self.service)
            for _ in range(2):
                result = self.while_writer_locked(lambda: getattr(workspace, operation)('project', values))
                self.assertEqual([row['id'] for row in result['records']], ['a'])
        self.assertIn('project', self.service._structure_pending_cache)

    def test_records_page_avoids_service_writer_lock_with_cold_and_warm_caches(self):
        self.graph('records_page')

    def test_neighborhood_avoids_service_writer_lock_with_cold_and_warm_caches(self):
        self.graph('neighborhood')
        self.assertIn('project', self.service._ontology_label_cache)
        self.assertIn(('project', 'release'), self.service._ontology_family_cache)

    def test_each_cache_helper_avoids_service_writer_lock(self):
        operations = [
            lambda: self.service.structure_pending_ontology('project'),
            lambda: self.service.ontology_labels('project'),
            lambda: self.service.ontology_family('project', 'release'),
            lambda: self.service.ontology_expansion('project', 'Thing', enabled=True),
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.while_writer_locked(operation)
                self.while_writer_locked(operation)

    def test_read_views_share_cache_lock_and_cache_values(self):
        with self.service.independent_read_view() as reader:
            self.assertIsNot(reader, self.service)
            self.assertIs(reader.lock, self.service.lock)
            self.assertIs(reader._ontology_cache_lock, self.service._ontology_cache_lock)
            self.assertIsNot(reader._ontology_cache_lock, reader.lock)
            for name in ('_ontology_label_cache', '_ontology_family_cache',
                         '_ontology_expansion_cache', '_structure_pending_cache'):
                self.assertIs(getattr(reader, name), getattr(self.service, name))
            reader.ontology_labels('project')
        self.assertIn('project', self.service._ontology_label_cache)


if __name__ == '__main__':
    unittest.main()
