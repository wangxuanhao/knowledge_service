"""The compact graph transport must not walk every field twice in Python."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api import graph_sessions


class GraphPageTransportTests(unittest.TestCase):
    def test_connected_page_accepts_bounded_relations_and_endpoint_transport(self):
        app = FastAPI()
        payload = {'kind': 'connected', 'records': [{'id': 'r'}], 'entities': [{'id': 'a'}, {'id': 'b'}]}
        with patch.object(graph_sessions, 'GraphWorkspace') as factory:
            factory.return_value.sessions.page.return_value = payload
            graph_sessions.install(app, SimpleNamespace())
        client = TestClient(app)
        response = client.post('/api/projects/p/graph-sessions/session/page', json={'kind': 'connected'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(factory.return_value.sessions.page.call_args.kwargs['limit'], 400)
        response = client.post('/api/projects/p/graph-sessions/session/page', json={'kind': 'connected', 'limit': 501})
        self.assertEqual(response.status_code, 422)

    def test_compact_page_is_sent_without_recursive_framework_reencoding(self):
        rows = [{'id': f'r{i}', 'kind': 'relation', 'version': 1,
                 'version_id': f'v{i}', 'type': 'related', 'text': '关系',
                 'subject_id': 'a', 'object_id': 'b'} for i in range(5000)]
        payload = {'session_id': 'session', 'kind': 'relations', 'records': rows,
                   'next_cursor': None, 'complete': True, 'total': len(rows),
                   'watermark': 1, 'layout_version': 'stable-id-v1'}
        app = FastAPI()
        with patch.object(graph_sessions, 'GraphWorkspace') as factory:
            factory.return_value.sessions.page.return_value = payload
            graph_sessions.install(app, SimpleNamespace())
        with patch('fastapi.routing.jsonable_encoder', side_effect=AssertionError(
                'Compact JSON pages must not recursively reencode every record')):
            response = TestClient(app).post('/api/projects/p/graph-sessions/session/page',
                                            json={'kind': 'relations', 'limit': 5000})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(response.json(), payload)
