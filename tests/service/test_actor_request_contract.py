import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from knowledge_service.api import ontology_drafts
from knowledge_service.api.ontology_drafts import (
    BatchApproveRequest,
    CloseRequest,
    CommandRequest,
    DecisionsRequest,
    DraftCreate,
    PublishRequest,
    RebaseRequest,
    RevisionRequest,
)
from knowledge_service.api.parity import ReclassifyApply
from knowledge_service.models import ResolutionReviewDecision


class ActorRequestContractTests(unittest.TestCase):
    def request_types(self):
        revision = {'expected_revision': 1}
        checked = {**revision, 'expected_ontology_id': None, 'validation_fingerprint': 'fingerprint'}
        return (
            (ResolutionReviewDecision, {'decision': 'rejected', 'expected_version': 1, 'reason': 'review'}),
            (ReclassifyApply, {'groups': []}),
            (DraftCreate, {'title': 'Draft'}),
            (RevisionRequest, revision),
            (CommandRequest, {**revision, 'command': {}}),
            (DecisionsRequest, {**checked, 'decisions': []}),
            (RebaseRequest, {**revision, 'expected_ontology_id': None}),
            (BatchApproveRequest, checked),
            (CloseRequest, {**revision, 'reason': 'closed'}),
            (PublishRequest, {**checked, 'idempotency_key': 'publish', 'note': 'Publish draft'}),
        )

    def test_canonical_requests_need_no_client_actor(self):
        for model, payload in self.request_types():
            with self.subTest(model=model.__name__):
                request = model.model_validate(payload)
                self.assertNotIn('actor', request.model_dump())
                self.assertNotIn('actor', model.model_json_schema()['properties'])

    def test_obsolete_client_actor_is_rejected(self):
        for model, payload in self.request_types():
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError) as caught:
                    model.model_validate({**payload, 'actor': 'forged'})
                self.assertEqual(caught.exception.errors()[0]['loc'], ('actor',))
                self.assertEqual(caught.exception.errors()[0]['type'], 'extra_forbidden')

    def test_draft_http_routes_reject_actor_before_any_service_call(self):
        drafts = Mock()
        app = FastAPI()
        ontology_drafts.install(app, SimpleNamespace(ontology_drafts=lambda **kwargs: drafts))
        paths = ('', '/draft/validate', '/draft/commands', '/draft/decisions',
                 '/draft/rebase', '/draft/decisions/batch-approve', '/draft/close', '/draft/publish')
        with TestClient(app) as client:
            for (model, payload), path in zip(self.request_types()[2:], paths):
                with self.subTest(model=model.__name__):
                    response = client.post('/api/projects/project/ontology-drafts' + path,
                                           json={**payload, 'actor': 'forged'})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(['body', 'actor'], [error['loc'] for error in response.json()['detail']])
                    self.assertEqual(drafts.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
