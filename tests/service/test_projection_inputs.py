"""Collector precision/snapshot contract, plus PostgreSQL mapping integration."""
from contextlib import contextmanager
from decimal import Decimal
import json
from threading import RLock
from types import SimpleNamespace
import unittest

from knowledge_service.repository.projection_inputs import ProjectionInputs


def raw_row(identity='row', *, kind='entity', payload=None, **changes):
    result = {'project_id': 'project', 'id': identity, 'version_id': identity + '-v1',
              'version': 1, 'recorded_at': '2026-10-21T00:00:00.000001Z',
              'superseded_at': None,
              'payload': payload or json.dumps({'id': identity, 'kind': kind}),
              'ontology_known': False, 'document_ids': [],
              'subject__version_id': None, 'object__version_id': None,
              'subject_ontology_known': False, 'object_ontology_known': False,
              'subject_document_ids': [], 'object_document_ids': []}
    result.update(changes)
    return result


class SnapshotRepository:
    def __init__(self, batches):
        self.batches = list(batches)
        self.views = 0
        self.open_views = 0
        self.statements = []
        self.reader = SimpleNamespace(_db=SimpleNamespace(execute=self.execute), _lock=RLock())

    @contextmanager
    def independent_read_view(self):
        self.views += 1
        self.open_views += 1
        try:
            yield self.reader
        finally:
            self.open_views -= 1

    def execute(self, sql, params):
        if self.open_views != 1:
            raise AssertionError('read escaped its snapshot')
        self.statements.append((sql, params))
        return SimpleNamespace(fetchall=lambda: self.batches.pop(0))


class ProjectionInputsTests(unittest.TestCase):
    def test_raw_pg_decimal_is_preserved_and_envelope_remains_authoritative(self):
        row = raw_row(payload='{"id":"stale","kind":"entity","metadata":{"confidence":9007199254740990.5}}')
        repo = SnapshotRepository([[row]])
        value, = ProjectionInputs(repo).collect('project', ['row'])
        self.assertEqual(value.record['metadata']['confidence'], Decimal('9007199254740990.5'))
        self.assertIsInstance(value.record['metadata']['confidence'], Decimal)
        self.assertEqual(value.record['id'], 'row')
        self.assertEqual(repo.views, 1)
        self.assertEqual(len(repo.statements), 1)

    def test_collector_has_no_endpoint_or_assertion_joins(self):
        repo = SnapshotRepository([[raw_row(kind='relation', ontology_known=True)]])
        value, = ProjectionInputs(repo).collect('project', ['row'])
        self.assertTrue(value.ontology_known)
        self.assertFalse(hasattr(value, 'subject'))
        statement = repo.statements[0][0]
        self.assertNotIn('JOIN', statement.upper())
        self.assertNotIn('assertion', statement)
        self.assertIn('r.read_visible', statement)

    def test_selection_order_is_explicit_and_never_silently_truncated(self):
        repo = SnapshotRepository([[raw_row('a'), raw_row('b')]])
        values = ProjectionInputs(repo).collect('project', ['b', 'missing', 'a'])
        self.assertEqual([value.record['id'] for value in values], ['b', 'a'])
        with self.assertRaises(ValueError):
            ProjectionInputs(repo).collect('project', ['a', 'b'], limit=1)
        with self.assertRaises(ValueError):
            ProjectionInputs(repo).collect('project', ['a'], version_ids=['a-v1'])
        overflowing = SnapshotRepository([[raw_row('a'), raw_row('b')]])
        with self.assertRaises(ValueError):
            ProjectionInputs(overflowing).collect('project', limit=1)

    def test_selected_versions_are_exact_and_keep_requested_order(self):
        repo = SnapshotRepository([[raw_row('a'), raw_row('b')]])
        values = ProjectionInputs(repo).collect('project', version_ids=['b-v1', 'a-v1'])
        self.assertEqual([value.record['version_id'] for value in values], ['b-v1', 'a-v1'])
        self.assertIn('r.superseded_at IS NULL', repo.statements[0][0])

    def test_keyset_batches_share_one_snapshot_until_generator_closes(self):
        repo = SnapshotRepository([[raw_row('a')], [raw_row('b')], []])
        batches = ProjectionInputs(repo).batches('project', batch_size=1)
        first = next(batches)
        self.assertEqual(first[0].record['id'], 'a')
        self.assertEqual(repo.open_views, 1)
        second = next(batches)
        self.assertEqual(second[0].record['id'], 'b')
        self.assertEqual(repo.views, 1)
        self.assertEqual(repo.statements[1][1][-2:], ['a', 1])
        batches.close()
        self.assertEqual(repo.open_views, 0)

    def test_batches_refuses_business_transaction_before_opening_snapshot(self):
        repo = SnapshotRepository([[raw_row('a')], []])
        repo._transaction_state = SimpleNamespace(depth=1)
        repo._is_read_view = False
        with self.assertRaisesRegex(RuntimeError, 'business transaction'):
            list(ProjectionInputs(repo).batches('project'))
        self.assertEqual(repo.views, 0)
        self.assertEqual(repo.statements, [])

    def test_batches_accepts_an_existing_independent_read_view(self):
        repo = SnapshotRepository([[raw_row('a')], []])
        repo._transaction_state = SimpleNamespace(depth=1)
        repo._is_read_view = True
        batches = list(ProjectionInputs(repo).batches('project'))
        self.assertEqual(batches[0][0].record['id'], 'a')

    def test_collect_remains_available_inside_a_business_transaction(self):
        repo = SnapshotRepository([[raw_row('uncommitted')]])
        repo._transaction_state = SimpleNamespace(depth=1)
        repo._is_read_view = False
        value, = ProjectionInputs(repo).collect('project', ['uncommitted'])
        self.assertEqual(value.record['id'], 'uncommitted')


def test_projection_inputs_pg_own_write_raw_decimal_and_current_visibility(tmp_path):
    from knowledge_service.repository.core import Repository
    repo = Repository(tmp_path / 'projection-inputs')
    project = repo.create_project('projection-inputs')['id']
    with repo._transaction():
        value = repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'body'})
        repo._db.execute('UPDATE record_versions SET payload=? WHERE project_id=? AND version_id=?',
                         ('{"id":"body","kind":"chunk","text":"body",'
                          '"metadata":{"price":9007199254740990.5}}', project, value['version_id']))
        collected, = ProjectionInputs(repo).collect(project, ['body'])
        assert collected.record['metadata']['price'] == Decimal('9007199254740990.5')
        assert isinstance(collected.record['metadata']['price'], Decimal)
    repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'new'})
    assert ProjectionInputs(repo).collect(project, version_ids=[value['version_id']]) == ()
    repo.put_record(project, {'id': 'hidden', 'kind': 'chunk', 'text': 'hidden', 'metadata': {'_audit': True}})
    assert ProjectionInputs(repo).collect(project, ['hidden']) == ()
    repo.close()


if __name__ == '__main__':
    unittest.main()
