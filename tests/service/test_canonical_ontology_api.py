"""Only the governed ontology command API accepts structural edits."""
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from knowledge_service.api import knowledge, workspace


class CanonicalOntologyRouteTests(unittest.TestCase):
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
