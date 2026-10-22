"""Only the governed ontology command API accepts structural edits."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_service.api import knowledge, workspace


class CanonicalOntologyRouteTests(unittest.TestCase):
    def test_version_reads_use_persisted_current_summary_without_reparsing_turtle(self):
        item = {'id': 'version', 'turtle': 'immutable RDF', 'summary': {'classes': ['stored']},
                'version': 1, 'version_reused': False}
        repo = SimpleNamespace(get_ontology=lambda *args: item, list_ontologies=lambda *args: [item])
        app = FastAPI()
        knowledge.install(app, SimpleNamespace(data=repo))
        with TestClient(app) as client, patch.object(knowledge, 'Ontology', side_effect=AssertionError('redundant RDF parse')):
            self.assertEqual(client.get('/api/projects/p/ontology').json()['summary'], item['summary'])
            self.assertEqual(client.get('/api/projects/p/ontologies').json()['versions'][0]['summary'], item['summary'])

    def test_old_write_and_full_record_query_routes_are_absent(self):
        app = FastAPI()
        service = SimpleNamespace(data=object())
        knowledge.install(app, service)
        workspace.install(app, service)
        routes = {(method, path) for path, methods in app.openapi()['paths'].items()
                  for method in methods}
        for method, path in [
            ('post', '/api/projects/{project_id}/ontologies'),
            ('post', '/api/projects/{project_id}/records/query'),
            ('post', '/api/projects/{p}/ontology/terms'),
            ('put', '/api/projects/{p}/ontology/term'),
            ('post', '/api/projects/{p}/ontology/term-retire'),
        ]:
            self.assertNotIn((method, path), routes)
        self.assertIn(('get', '/api/projects/{project_id}/ontologies'), routes)
        self.assertIn(('get', '/api/projects/{p}/ontology/relation-usage'), routes)


if __name__ == '__main__':
    unittest.main()
