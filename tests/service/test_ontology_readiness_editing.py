"""Standalone readiness contracts without application startup, PostgreSQL or encoders."""
import ast
from collections import Counter
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import APIRouter, FastAPI, Query
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def load_methods(relative, class_name, names, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    node = next(item for item in tree.body
                if isinstance(item, ast.ClassDef) and item.name == class_name)
    node.body = [item for item in node.body
                 if isinstance(item, ast.FunctionDef) and item.name in names]
    node.bases = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(ROOT / relative), 'exec'), namespace)
    return namespace[class_name]


class StaleSource(Exception):
    pass


class EditingReadinessTests(unittest.TestCase):
    def setUp(self):
        self.calls = Counter()
        self.draft = {'id': 'd', 'status': 'pending', 'source_kind': 'candidate',
                      'revision': 4, 'base_ontology_id': 'base', 'submitted_at': None,
                      'validation_fingerprint': 'current-fp'}
        self.operations = [{'id': 'op', 'fingerprint': 'op-fp', 'target_iri': 'urn:term',
                            'validation': {}}]
        self.decisions = []
        self.report = {'conforms': True, 'errors': [], 'warnings': []}
        self.fingerprint = 'current-fp'
        self.latest = 'base'
        self.revert = None
        self.stale_source = False

        def replay(turtle, operations):
            self.calls['approved_replay'] += 1
            return turtle

        namespace = {'FINAL_STATES': {'accepted', 'rejected'}, 'REVERT_WARNING': 'revert',
                     'StaleSource': StaleSource, 'apply_operations': replay,
                     '_semantic_operation': lambda operation: operation}
        review = load_methods('knowledge_service/services/ontology_drafts_review.py',
                              'DraftReviewPublishMixin', {'publish_readiness'}, namespace)
        helpers = load_methods('knowledge_service/services/ontology_drafts.py',
                               'OntologyDrafts', {'_effective_decisions', '_operation_issues'}, {})
        self.service = type('ReadinessService', (review, helpers), {})()
        self.service.store = SimpleNamespace(get=lambda *args: self.draft)
        self.service._latest_id = lambda *args: self.latest
        self.service._revert_base = lambda *args: self.revert
        self.service._version_number = lambda *args: 1
        self.service._active_operations = lambda *args: self.operations
        self.service._history = lambda *args: (self.operations, self.decisions)
        self.service._warning_codes = lambda report: {
            item['code'] for item in report.get('warnings', [])}
        self.service._base_turtle = lambda *args: 'base turtle'

        def snapshot(*args):
            self.calls['source_snapshot'] += 1
            if self.stale_source:
                raise StaleSource('source changed')
            return []

        def compute(*args):
            self.calls['full_validation'] += 1
            # Model the heavy dependencies behind the actual validation boundary;
            # the scoped-read suite separately executes the real computation.
            self.calls['quality'] += 1
            self.calls['current_records'] += 1
            snapshot(*args)
            return self.report, self.fingerprint, 'draft turtle'

        self.service._source_snapshot = snapshot
        self.service._compute_validation = compute

    def editing(self):
        self.assertIn('editing', inspect.signature(self.service.publish_readiness).parameters,
                      'readiness needs an explicit lightweight editing view')
        return self.service.publish_readiness('p', 'd', editing=True)

    def approve(self):
        self.draft['submitted_at'] = '2026-10-20T00:00:00Z'
        self.decisions = [{'id': 'decision', 'operation_id': 'op', 'action': 'approve',
                           'operation_fingerprint': 'op-fp'}]

    def test_unsubmitted_editing_draft_skips_full_validation_records_and_sources(self):
        result = self.editing()
        self.assertEqual(self.calls, {})
        self.assertFalse(result['ready'])
        self.assertIs(result.get('validation_checked'), False)
        self.assertEqual(result.get('validation_state'), 'not_evaluated')
        self.assertIs(result.get('requires_full_validation'), False)
        self.assertEqual(result['approval_counts'], {'approved': 0, 'pending': 1, 'total': 1})
        self.assertTrue({'not_submitted', 'decision_pending'}.issubset(
            {item['code'] for item in result['items']}))
        note = next(item for item in result['items']
                    if item['code'] == 'validation_not_evaluated')
        self.assertEqual(note['severity'], 'info')

    def test_fully_decided_editing_draft_requires_full_validation_but_cannot_be_ready(self):
        self.approve()
        result = self.editing()
        self.assertEqual(self.calls, {})
        self.assertIs(result.get('requires_full_validation'), True)
        self.assertFalse(result['ready'])
        self.assertIs(result.get('validation_checked'), False)
        self.assertEqual(result['counts']['error'], 0)

    def test_cheap_blockers_prevent_background_full_validation(self):
        self.approve()
        cases = [
            ('settled', lambda: self.draft.update(status='accepted'), 'draft_settled'),
            ('old base', lambda: setattr(self, 'latest', 'new-base'), 'base_outdated'),
            ('no operations', lambda: setattr(self, 'operations', []), 'no_operation'),
            ('stale decision', lambda: self.decisions[0].update(
                operation_fingerprint='old-op-fp'), 'decision_pending'),
            ('no approval', lambda: self.decisions[0].update(action='reject'), 'no_approval'),
            ('operation error', lambda: self.operations[0]['validation'].update(errors=[{
                'code': 'bad_parent', 'message': 'parent is missing'}]), 'bad_parent'),
            ('rebase conflict', lambda: self.operations[0]['validation'].update(
                rebase_status='conflict'), 'rebase_conflict'),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                self.setUp()
                self.approve()
                mutate()
                result = self.editing()
                self.assertIs(result.get('requires_full_validation'), False)
                self.assertFalse(result['ready'])
                self.assertIn(expected, {item['code'] for item in result['items']})
                self.assertEqual(self.calls, {})

    def test_zero_operation_revert_can_request_full_validation(self):
        self.approve()
        self.revert = 'historical-base'
        self.operations, self.decisions = [], []
        result = self.editing()
        self.assertIs(result.get('requires_full_validation'), True)
        self.assertFalse(result['ready'])
        self.assertEqual(self.calls, {})

    def test_default_full_evaluates_fresh_quality_records_sources_and_approved_subset(self):
        self.approve()
        result = self.service.publish_readiness('p', 'd')
        self.assertTrue(result['ready'])
        self.assertIs(result.get('validation_checked'), True)
        self.assertEqual(result.get('validation_state'), 'evaluated')
        self.assertIs(result.get('requires_full_validation'), False)
        self.assertEqual(self.calls, {'full_validation': 1, 'quality': 1, 'current_records': 1,
                                     'source_snapshot': 2, 'approved_replay': 1})

    def test_default_full_still_detects_changed_fingerprints_and_validation_errors(self):
        self.approve()
        self.fingerprint = 'fresh-but-unacknowledged'
        self.report['errors'] = [{'code': 'graph_integrity', 'message': 'invalid graph'}]
        result = self.service.publish_readiness('p', 'd')
        self.assertFalse(result['ready'])
        self.assertTrue({'validation_changed', 'graph_integrity'}.issubset(
            {item['code'] for item in result['items']}))
        self.assertEqual(self.calls['full_validation'], 1)
        self.assertEqual(self.calls['current_records'], 1)

    def test_full_source_change_remains_a_blocker_and_validation_failure_is_closed(self):
        self.approve()
        self.stale_source = True
        result = self.service.publish_readiness('p', 'd')
        self.assertFalse(result['ready'])
        self.assertTrue({'stale_source', 'validation_unavailable'}.issubset(
            {item['code'] for item in result['items']}))
        self.assertEqual(self.calls['full_validation'], 1)


class ReadinessRouteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def readiness(project, draft, **kwargs):
            self.calls.append((project, draft, kwargs.get('editing', False)))
            return {'ready': False}

        path = ROOT / 'knowledge_service/api/ontology_drafts.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        install = next(item for item in tree.body
                       if isinstance(item, ast.FunctionDef) and item.name == 'install')
        handler = next(item for item in install.body
                       if isinstance(item, ast.FunctionDef) and item.name == 'publish_readiness')
        router = APIRouter()
        namespace = {'router': router, 'Query': Query,
                     'drafts': SimpleNamespace(publish_readiness=readiness)}
        module = ast.Module(body=[handler], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
        app = FastAPI()
        app.include_router(router, prefix='/api/projects/{p}/ontology-drafts')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.url = '/api/projects/p/ontology-drafts/d/publish-readiness'

    def test_route_defaults_to_full_and_explicit_full_remains_full(self):
        for suffix in ['', '?view=full']:
            self.assertEqual(self.client.get(self.url + suffix).status_code, 200)
        self.assertEqual(self.calls, [('p', 'd', False), ('p', 'd', False)])

    def test_route_explicit_editing_uses_lightweight_projection(self):
        self.assertEqual(self.client.get(self.url + '?view=editing').status_code, 200)
        self.assertEqual(self.calls, [('p', 'd', True)])

    def test_route_rejects_unknown_views_before_service_calls(self):
        self.assertEqual(self.client.get(self.url + '?view=unknown').status_code, 422)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
