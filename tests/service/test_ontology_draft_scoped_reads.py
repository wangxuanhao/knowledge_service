"""Standalone read regressions; run directly without the PostgreSQL pytest fixture.

Compile the real store and service methods without importing application adapters,
so the latency contract can be checked without a database or encoder runtime.
"""
import ast
import copy
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load_class(relative, name, namespace, methods=None):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    node = next(item for item in tree.body
                if isinstance(item, ast.ClassDef) and item.name == name)
    if methods is not None:
        node.body = [item for item in node.body
                     if isinstance(item, ast.FunctionDef) and item.name in methods]
    node.bases = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), 'exec'), namespace)
    return namespace[name]


Store = load_class('knowledge_service/repository/ontology_draft_store.py',
                   'OntologyDraftStore', {'json': json})


class DraftDatabase:
    def __init__(self, draft, operations, decisions):
        self.draft = draft
        self.operations = operations
        self.decisions = decisions
        self.queries = []

    def execute(self, sql, params):
        normalized = ' '.join(sql.split())
        self.queries.append((normalized, tuple(params)))
        # Every history query must qualify both project and draft. An operation
        # with the same draft ID in another project must never enter the preview.
        if 'FROM ontology_drafts' in normalized:
            if tuple(params) != ('p', 'd'):
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(fetchone=lambda: copy.deepcopy(self.draft))
        assert 'project_id=?' in normalized and 'draft_id=?' in normalized, normalized
        assert tuple(params) == ('p', 'd'), params
        rows = self.operations if 'ontology_operations' in normalized else self.decisions
        rows = [row for row in rows if row['project_id'] == params[0]
                and row['draft_id'] == params[1]]
        if 'NOT EXISTS' in normalized:
            replaced = {row.get('supersedes_operation_id') for row in rows}
            rows = [row for row in rows if row['id'] not in replaced]
        else:
            assert 'ORDER BY seq' in normalized, normalized
        rows = sorted(rows, key=lambda row: row['seq'])
        return SimpleNamespace(fetchall=lambda: copy.deepcopy(rows))


def operation(identifier, seq, **values):
    return {'id': identifier, 'project_id': 'p', 'draft_id': 'd', 'seq': seq,
            'created_at': '2026-10-20T00:00:00Z', 'fingerprint': identifier + '-fp',
            'before_json': '{}', 'after_json': '{}', 'evidence_json': '[]',
            'impact_json': '{}', 'validation_json': '{}', **values}


def decision(identifier, operation_id, seq, **values):
    return {'id': identifier, 'project_id': 'p', 'draft_id': 'd', 'seq': seq,
            'operation_id': operation_id, 'operation_fingerprint': operation_id + '-fp',
            'action': 'approve', **values}


def fixture():
    draft = {'id': 'd', 'project_id': 'p', 'status': 'pending', 'source_kind': 'manual',
             'revision': 4, 'base_ontology_id': 'base', 'source_context': '{}',
             'validation_report': None}
    operations = [operation('first', 1), operation('second', 2),
                  operation('first-v2', 3, supersedes_operation_id='first'),
                  operation('first-v3', 4, supersedes_operation_id='first-v2'),
                  operation('withdrawn', 5, validation_json='{"withdrawn":true}'),
                  operation('other-draft', 6, draft_id='other'),
                  operation('foreign', 7, project_id='foreign')]
    decisions = [decision('old', 'first-v3', 1, action='reject'),
                 decision('current', 'first-v3', 2, supersedes_decision_id='old'),
                 decision('stale', 'second', 3, operation_fingerprint='outdated'),
                 decision('unrelated', 'other-draft', 4, draft_id='other'),
                 decision('foreign-decision', 'foreign', 5, project_id='foreign')]
    db = DraftDatabase(draft, operations, decisions)
    repo = SimpleNamespace(_db=db, _lock=RLock(),
                           get_project=lambda project: {'id': project})
    store = Store(repo)
    store.export = lambda *args: (_ for _ in ()).throw(
        AssertionError('canvas read exported every draft in the project'))
    return store, db


def service_class(extra=None):
    namespace = {'Ontology': lambda turtle: SimpleNamespace(summary=lambda: {'turtle': turtle}),
                 '_semantic_operation': lambda row: row,
                 'apply_operations': lambda turtle, rows: turtle + ':' + ','.join(
                     row['id'] for row in rows),
                 'PENDING_STATES': {'pending'}, 'FINAL_STATES': {'accepted', 'rejected'},
                 '_STATUS_LABEL': {}, '_STATUS_COPY': {}, **(extra or {})}
    return load_class('knowledge_service/services/ontology_drafts.py', 'OntologyDrafts',
                      namespace, {'_history', '_effective_operations', '_active_operations',
                                  '_effective_decisions', '_ontology_for_draft', '_preview',
                                  'get', 'stage_state', '_next_action', '_compute_validation',
                                  '_require_validation'})


class ScopedDraftReadTests(unittest.TestCase):
    def test_history_qualifies_draft_keeps_supersession_ancestors_and_seq_order(self):
        store, db = fixture()
        self.assertTrue(callable(getattr(store, 'history', None)),
                        'the store needs draft-qualified audit history')
        operations, decisions = store.history('p', 'd')
        self.assertEqual([row['id'] for row in operations],
                         ['first', 'second', 'first-v2', 'first-v3', 'withdrawn'])
        self.assertEqual([row['id'] for row in decisions], ['old', 'current', 'stale'])
        self.assertEqual(operations[3]['supersedes_operation_id'], 'first-v2')
        self.assertEqual(operations[0]['evidence'], [])
        self.assertEqual(len(db.queries), 3)

    def test_history_rejects_unknown_and_cross_project_drafts(self):
        store, db = fixture()
        self.assertTrue(callable(getattr(store, 'history', None)),
                        'the store needs draft-qualified audit history')
        for project, draft in [('p', 'unknown'), ('foreign', 'd')]:
            with self.subTest(project=project, draft=draft):
                before = len(db.queries)
                with self.assertRaises(KeyError):
                    store.history(project, draft)
                self.assertEqual(len(db.queries) - before, 1)

    def test_canvas_uses_scoped_history_and_preserves_logical_order_and_decisions(self):
        store, _ = fixture()
        service = service_class()()
        service.store = store
        service._base_turtle = lambda *args: 'base'
        result = service.get('p', 'd', canvas=True)
        self.assertEqual([row['id'] for row in result['operations']], ['first-v3', 'second'])
        self.assertEqual([row['id'] for row in result['decisions']], ['current'])
        self.assertEqual(result['ontology'], {'turtle': 'base:first-v3,second'})
        self.assertNotIn('source_context', result)
        self.assertTrue(all('evidence' not in row for row in result['operations']))

    def test_stage_uses_one_derived_state_per_call_without_caching_across_calls(self):
        service = service_class()()
        draft = {'id': 'd', 'status': 'pending', 'revision': 4}
        service.store = SimpleNamespace(list=lambda project: [draft])
        calls = {'rebase': 0, 'phase': 0}
        current = {'rebase': None, 'phase': 'editing'}

        def rebase(*args):
            calls['rebase'] += 1
            return current['rebase']

        def phase(*args):
            calls['phase'] += 1
            return current['phase']

        service.needs_rebase, service.pending_phase = rebase, phase
        service._active_operations = lambda *args: []
        result = service.stage_state('p', 'd')
        self.assertEqual(calls, {'rebase': 1, 'phase': 1})
        self.assertEqual(result['next_action']['kind'], 'design')
        current.update(rebase='stale_source', phase='reviewing')
        result = service.stage_state('p', 'd')
        self.assertEqual(calls, {'rebase': 2, 'phase': 2})
        self.assertEqual(result['next_action']['kind'], 'reload')
        # Direct callers retain fresh derivation when no state is supplied.
        self.assertEqual(service._next_action('p', draft, True)['kind'], 'reload')
        self.assertEqual(calls, {'rebase': 3, 'phase': 2})

    def test_validation_reads_all_formal_kinds_and_still_checks_quality_and_sources(self):
        counts = {'quality': 0, 'source': 0, 'timeline': 0}
        records = [{'id': 'e', 'kind': 'entity'}, {'id': 'r', 'kind': 'relation'},
                   {'id': 'a', 'kind': 'attribute'}]

        def quality(payload):
            counts['quality'] += 1
            return {'passed': True, 'issues': []}

        def timeline(rows):
            counts['timeline'] += 1
            self.assertEqual(rows, records)
            return {'conforms': True, 'errors': []}

        def read(project, **kwargs):
            self.assertEqual(project, 'p')
            self.assertEqual(set(kwargs.get('kinds') or []), {'entity', 'relation', 'attribute'})
            return records

        def sources(*args):
            counts['source'] += 1
            return [{'document_id': 'source', 'version_id': 'source-v%d' % (
                counts['source'] + 1)}]

        service = service_class({
            'Ontology': lambda turtle: SimpleNamespace(validate_timeline=timeline),
            'ontology_payload': lambda ontology: {},
            'validate_ontology_invariants': lambda ontology: {'conforms': True, 'errors': []},
            'QualityEngineUnavailable': type('QualityEngineUnavailable', (Exception,), {}),
            '_fingerprint': lambda value: value,
        })()
        service._active_operations = lambda *args: [{'id': 'op', 'fingerprint': 'fresh'}]
        service._operation_issues = lambda *args: ([], [], [])
        service._base_turtle = lambda *args: 'base'
        service._overlay_dependency_issues = lambda *args: ([], [])
        service._extend_unique = lambda target, values: target.extend(values)
        service._source_snapshot = sources
        service.quality_engine = SimpleNamespace(check=quality)
        service.repository = SimpleNamespace(current_records=read)
        service.validation_rule_version = 'test-rule'
        report, fingerprint, _ = service._compute_validation(
            'p', {'id': 'd', 'base_ontology_id': 'base'})
        self.assertEqual(counts, {'quality': 1, 'source': 1, 'timeline': 1})
        self.assertTrue(report['conforms'])
        self.assertEqual(fingerprint['source_versions'][0]['version_id'], 'source-v2')
        self.assertEqual(fingerprint['operation_fingerprints'], ['fresh'])
        _, changed, _ = service._compute_validation(
            'p', {'id': 'd', 'base_ontology_id': 'base'})
        self.assertEqual(counts, {'quality': 2, 'source': 2, 'timeline': 2})
        self.assertNotEqual(changed, fingerprint)

    def test_required_validation_recomputes_and_rejects_newer_fingerprints(self):
        class ValidationChanged(Exception):
            def __init__(self, message, *, details):
                super().__init__(message)
                self.details = details

        service = service_class({'ValidationChanged': ValidationChanged})()
        service._warning_codes = lambda report: set()
        current = {'fingerprint': 'validated', 'calls': 0}

        def compute(*args):
            current['calls'] += 1
            return {'conforms': True}, current['fingerprint'], 'turtle'

        service._compute_validation = compute
        draft = {'validation_fingerprint': 'validated'}
        service._require_validation('p', draft, 'validated', [])
        current['fingerprint'] = 'source-or-operation-changed'
        with self.assertRaises(ValidationChanged) as caught:
            service._require_validation('p', draft, 'validated', [])
        self.assertEqual(current['calls'], 2)
        self.assertEqual(caught.exception.details['validation_fingerprint'],
                         'source-or-operation-changed')


if __name__ == '__main__':
    unittest.main()
