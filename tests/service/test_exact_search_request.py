"""HTTP filters retain source decimals before the native compiler sees them."""
from decimal import Decimal
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api.knowledge import install


class ExactSearchRequestTests(unittest.TestCase):
    def test_search_and_qa_do_not_round_large_fractional_filter_constants(self):
        captured = []
        def retrieve(project, request):
            captured.append(request['filters']['value'])
            return {'hits': []}
        service = SimpleNamespace(data=SimpleNamespace(), search=retrieve, answer=retrieve)
        app = FastAPI()
        install(app, service)
        with TestClient(app) as client:
            for route in ('search', 'qa'):
                response = client.post('/api/projects/p/' + route,
                    content='{"query":"退款","filters":{"field":"metadata.amount","op":"gt","value":9007199254740990.5}}',
                    headers={'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(captured, [Decimal('9007199254740990.5')] * 2)


if __name__ == '__main__':
    unittest.main()
