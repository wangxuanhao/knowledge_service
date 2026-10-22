"""Pure regressions for reusing a detached planning snapshot, never execution state."""
import copy
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_service.repository import Repository
from knowledge_service.services.formal_writes import FormalFactWriter, _Planner, canonical_attribute_key
from knowledge_service.services.service import KnowledgeService


ONTOLOGY = ('@prefix ex: <http://ex/> . '
            '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
            'ex:Person a owl:Class .')


def snapshot():
    return {'project_id': 'p', 'storage_namespace': 'snapshot-test',
            'ontology_id': 'ont', 'ontology': {'id': 'ont', 'turtle': ONTOLOGY},
            'records': [], 'assertions': [], 'assertion_events': [],
            'record_version_assertions': [], 'fact_keys': [],
            'resolution_reviews': [], 'merge_operations': [], 'fingerprint': 'captured'}


class SnapshotRepository:
    backend_label = 'fake'
    storage_namespace = 'snapshot-test'

    def __init__(self, state):
        self.state, self.reads, self.plans = state, 0, []

    def get_project(self, project):
        assert project == 'p'

    def get_ontology(self, project, version=None):
        assert project == 'p'
        return self.state['ontology']

    def formal_write_snapshot(self, project):
        assert project == 'p'
        self.reads += 1
        return self.state

    def current_records(self, *_args, **_kwargs):
        raise AssertionError('write redundantly loaded the whole corpus')

    @staticmethod
    def formal_write_context(context):
        return context

    def execute_formal_write(self, plan):
        self.plans.append(plan)
        return {'accepted_records': [action['record'] for action in plan['actions']
                                     if action['kind'] == 'record_put']}


class WriteSnapshotReuseTests(unittest.TestCase):
    def test_service_uses_one_planning_snapshot_and_preserves_nested_input(self):
        state = snapshot()
        state['records'] = [{'id': 'old', 'kind': 'entity', 'version': 3, 'version_id': 'old-v3',
                             'text': 'old', 'metadata': {'nested': {'labels': ['keep']}},
                             'valid_until': '2020-01-01T00:00:00Z', 'ontology_id': 'ont'}]
        before = copy.deepcopy(state)
        repo = SnapshotRepository(state)
        service = KnowledgeService(repo, SimpleNamespace(identity='test', encode=lambda rows: [[1.0] for _ in rows]))
        service.annotate_structure_pending = lambda *_args: 0
        service._timeline_check = lambda project, version, rows, mode: (
            None, list(rows.values()), {}, set(), {'conforms': True})
        raw = {'id': 'new', 'kind': 'entity', 'type': 'http://ex/Person', 'text': 'new',
               'metadata': {'nested': {'labels': ['input']}}}
        original = copy.deepcopy(raw)
        result = service.write('p', [raw])
        self.assertEqual(repo.reads, 1)
        self.assertEqual([row['id'] for row in result], ['new'])
        self.assertEqual(repo.plans[0]['dependencies']['records'], {'old': 'old-v3'})
        self.assertEqual(state, before)
        self.assertEqual(raw, original)

    def test_deleted_ids_remain_existing_for_creation_conflict_checks(self):
        state = snapshot()
        state['records'] = [{'id': 'removed', 'kind': 'entity', 'version': 4,
                             'version_id': 'removed-v4', 'metadata': {'_deleted': True}}]
        repo = SnapshotRepository(state)
        service = KnowledgeService(repo, SimpleNamespace())
        with self.assertRaisesRegex(ValueError, '版本冲突'):
            service.write('p', [{'id': 'removed', 'kind': 'entity', 'text': 'new'}])
        self.assertEqual(repo.reads, 1)
        self.assertEqual(repo.plans, [])

    def test_inherited_source_marker_is_detached_before_real_pending_annotation(self):
        state = snapshot()
        state['records'] = [{'id': 'source', 'kind': 'document', 'version': 2,
                             'version_id': 'source-v2', 'text': 'source', 'metadata': {
                                 'structure_pending': {'terms': ['old'], 'reason': 'original'},
                                 'nested': {'labels': ['source']}}}]
        before = copy.deepcopy(state)
        repo = SnapshotRepository(state)
        service = KnowledgeService(repo, SimpleNamespace(identity='test', encode=lambda rows: [[1.0] for _ in rows]))
        service.annotate_structure_pending = lambda *_args: 0
        service._timeline_check = lambda project, version, rows, mode: (
            None, list(rows.values()), {'source': [], 'child': ['urn:test:Missing']},
            {'child'}, {'conforms': True})
        raw = {'id': 'child', 'kind': 'entity', 'type': 'urn:test:Missing',
               'text': 'child', 'source_id': 'source'}
        original = copy.deepcopy(raw)
        result = service.write('p', [raw])
        self.assertEqual(state, before)
        self.assertEqual(raw, original)
        marker = result[0]['metadata']['structure_pending']
        self.assertEqual(marker['terms'], ['old', 'urn:test:Missing'])
        self.assertEqual(marker['reason'], 'original')

    def test_writer_reuses_only_a_snapshot_of_the_same_project(self):
        state = snapshot()
        repo = SnapshotRepository(state)
        writer = FormalFactWriter(repo)
        plan = writer.prepare('manual_write', 'p', [], {}, {'records': []}, snapshot=state)
        self.assertEqual(repo.reads, 0)
        self.assertEqual(plan['dependencies']['snapshot_fingerprint'], 'captured')
        with self.assertRaisesRegex(ValueError, '快照|snapshot'):
            writer.prepare('manual_write', 'other', [], {}, {'records': []}, snapshot=state)

    def test_planner_copies_changed_rows_without_copying_or_mutating_the_corpus(self):
        class NoCorpusCopy(dict):
            def __deepcopy__(self, _memo):
                raise AssertionError('planner deep-copied the whole corpus')

        state = NoCorpusCopy(snapshot())
        old = {'id': 'attribute', 'kind': 'attribute', 'type': 'http://ex/value',
               'subject_id': 'entity', 'value': 1, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer',
               'version': 1, 'version_id': 'attribute-v1', 'metadata': {'nested': {'labels': ['keep']}}}
        state['records'] = [old]
        state['fact_keys'] = [{'fact_key': canonical_attribute_key(old),
                               'canonical_record_id': old['id'], 'retired_at': None}]
        before = copy.deepcopy(dict(state))
        changed = {**old, 'value': 2}
        planner = _Planner(state, 'manual_write', [], {'attribute': 1},
                           {'records': [changed], 'suppress_auto_assertions': True})
        actions, _, _ = planner.build()
        self.assertTrue(any(action['kind'] == 'fact_key_retire' for action in actions))
        action = next(item for item in actions if item['kind'] == 'record_put')
        action['record']['metadata']['nested']['labels'].append('changed')
        self.assertEqual(state, before)

    def test_repository_returns_its_fresh_detached_snapshot_without_a_json_roundtrip(self):
        state = snapshot()

        class Reader:
            formal_write_snapshot = Repository.formal_write_snapshot

            @contextmanager
            def _transaction(self, **kwargs):
                assert kwargs == {'repeatable_read_only': True}
                yield

            def _formal_write_snapshot_data(self, project):
                assert project == 'p'
                return state

        with patch('knowledge_service.repository.core._json', side_effect=AssertionError(
                'detached snapshot was serialized and decoded again')):
            self.assertIs(Reader().formal_write_snapshot('p'), state)

    def test_execution_still_reads_fresh_authority_and_rejects_changed_dependencies(self):
        state = snapshot()
        plan = FormalFactWriter(SnapshotRepository(state)).prepare(
            'manual_write', 'p', [], {}, {'records': []}, snapshot=state)

        class Guard:
            execute_formal_write = Repository.execute_formal_write
            _formal_write_dependencies = Repository._formal_write_dependencies
            _publish_fingerprint = staticmethod(Repository._publish_fingerprint)
            reads = 0

            @contextmanager
            def _formal_write_transaction(self):
                yield

            def _formal_write_snapshot_data(self, project):
                self.reads += 1
                return {**state, 'fingerprint': 'concurrent-change'}

        guard = Guard()
        with self.assertRaisesRegex(ValueError, '依赖.*变更'):
            guard.execute_formal_write(plan)
        self.assertEqual(guard.reads, 1)


if __name__ == '__main__':
    unittest.main()
