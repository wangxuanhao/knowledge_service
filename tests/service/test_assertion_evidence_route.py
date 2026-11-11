"""The evidence route keeps repository-only source reads behind its data port."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api import evidence as routes
from knowledge_service.services.api_workflows import ApplicationData


class AssertionEvidenceRoute(unittest.TestCase):
    def test_frozen_source_resolution_receives_repository_through_application_port(self):
        repository = object()
        expected = {'assertion_id': 'assertion', 'source_location': {'status': 'available'}}

        def resolve(target, project, assertion):
            if target is not repository:
                raise AttributeError('_lock')
            self.assertEqual((project, assertion), ('project', 'assertion'))
            return expected

        app = FastAPI()
        service = SimpleNamespace(data=ApplicationData(repository))
        routes.install(app, service)
        with patch('knowledge_service.services.evidence.resolve_assertion_evidence', side_effect=resolve), \
             patch.object(routes, 'resolve_assertion_evidence', side_effect=resolve, create=True), \
             TestClient(app, raise_server_exceptions=False) as client:
            response = client.get('/api/projects/project/assertions/assertion/evidence')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)


if __name__ == '__main__':
    unittest.main()
