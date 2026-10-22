"""Destructive maintenance stays inside its reviewed derived-data boundary."""
from contextlib import nullcontext
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


reset = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/reset_milvus_indexes.py'))
module = reset['apply_reset'].__globals__
DATA = 'knowledge_records__p_0123456789abcdef__g_current_build'
CONTROL = DATA + '__control'
ALIAS = 'knowledge_records__p_0123456789abcdef'


class Client:
    def __init__(self):
        self.collections = {
            DATA: ['project_id', 'record_id', 'version_id', 'embedding'],
            CONTROL: ['project_id', 'marker_id', 'projection_seq', 'fence', 'marker_vector'],
            'another_app': ['id', 'embedding'],
        }
        self.dropped = []

    def list_collections(self, **_kwargs):
        return list(self.collections)

    def describe_collection(self, *, collection_name, **_kwargs):
        return {'fields': [{'name': value} for value in self.collections[collection_name]]}

    def list_aliases(self, *, collection_name, **_kwargs):
        return {'aliases': [ALIAS] if collection_name == DATA else []}

    def get_collection_stats(self, **_kwargs):
        return {'row_count': 2}

    def drop_alias(self, **_kwargs):
        pass

    def drop_collection(self, *, collection_name, **_kwargs):
        self.dropped.append(collection_name)
        del self.collections[collection_name]


class Database:
    def __init__(self):
        self.statements = []

    def execute(self, statement, *_args):
        self.statements.append(statement)
        if statement.startswith(('UPDATE ', 'DELETE ')):
            table = statement.split()[1 if statement.startswith('UPDATE') else 2]
            if table not in {'projection_cursors', 'projection_outbox', 'projection_cleanup_outbox'}:
                raise AssertionError('Maintenance tried to mutate a business table')
        return SimpleNamespace(fetchone=lambda: [2], rowcount=2)

    def transaction(self):
        return nullcontext()


class MilvusIndexResetTests(unittest.TestCase):
    def test_owned_name_does_not_match_nearby_or_other_application_names(self):
        self.assertTrue(reset['owned_name'](DATA))
        self.assertTrue(reset['owned_name'](CONTROL))
        for name in ('knowledge_records_backup', 'knowledge_records__p_bad__g_x',
                     'unrelated__p_0123456789abcdef__g_current_build'):
            self.assertFalse(reset['owned_name'](name))

    def test_schema_mismatch_refuses_inventory_before_any_delete(self):
        client = Client()
        client.collections[DATA] = ['id', 'embedding']
        with self.assertRaisesRegex(RuntimeError, 'unexpected schema'):
            reset['inventory'](client)
        self.assertEqual(client.dropped, [])

    def test_changed_manifest_refuses_all_mutations(self):
        client, db = Client(), Database()
        with patch.dict(module, {'require_stopped': lambda: None}):
            with self.assertRaisesRegex(RuntimeError, 'manifest changed'):
                reset['apply_reset'](client, db, {'digest': 'different'})
        self.assertEqual(client.dropped, [])
        self.assertEqual(db.statements, [])

    def test_reset_preserves_other_application_and_only_mutates_derived_pg_tables(self):
        client, db = Client(), Database()
        manifest = reset['inventory'](client)
        with TemporaryDirectory() as directory, patch.dict(module, {
                'require_stopped': lambda: None, 'ROOT': Path(directory)}):
            result = reset['apply_reset'](client, db, manifest)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(set(client.dropped), {DATA, CONTROL})
        self.assertEqual(set(client.collections), {'another_app'})
        self.assertTrue(result['business_counts_unchanged'])
        self.assertTrue(any("payload=payload-'embedding'" in sql for sql in db.statements))

    def test_retry_finishes_after_partial_milvus_drop(self):
        client, db = Client(), Database()
        manifest = reset['inventory'](client)
        drop = client.drop_collection
        def fail_control(**kwargs):
            if kwargs['collection_name'] == CONTROL:
                raise RuntimeError('simulated drop failure')
            drop(**kwargs)
        client.drop_collection = fail_control
        with TemporaryDirectory() as directory, patch.dict(module, {
                'require_stopped': lambda: None, 'ROOT': Path(directory)}):
            with self.assertRaisesRegex(RuntimeError, 'simulated drop'):
                reset['apply_reset'](client, db, manifest)
            self.assertEqual(client.dropped, [DATA])
            client.drop_collection = drop
            result = reset['apply_reset'](client, db, manifest)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['already_absent'], [DATA])
        self.assertEqual(client.dropped, [DATA, CONTROL])

    def test_retry_resets_pg_after_all_milvus_drops_already_succeeded(self):
        class FailingDatabase(Database):
            fail = True
            def transaction(self):
                if self.fail:
                    raise RuntimeError('simulated PG failure')
                return super().transaction()
        client, db = Client(), FailingDatabase()
        manifest = reset['inventory'](client)
        with TemporaryDirectory() as directory, patch.dict(module, {
                'require_stopped': lambda: None, 'ROOT': Path(directory)}):
            with self.assertRaisesRegex(RuntimeError, 'simulated PG'):
                reset['apply_reset'](client, db, manifest)
            db.fail = False
            result = reset['apply_reset'](client, db, manifest)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(set(result['already_absent']), {DATA, CONTROL})
        self.assertEqual(len(client.dropped), 2)

    def test_new_collection_after_review_refuses_any_drop(self):
        client, db = Client(), Database()
        manifest = reset['inventory'](client)
        client.collections[DATA + '_new'] = client.collections[DATA]
        with patch.dict(module, {'require_stopped': lambda: None}):
            with self.assertRaisesRegex(RuntimeError, 'inventory changed'):
                reset['apply_reset'](client, db, manifest)
        self.assertEqual(client.dropped, [])
        self.assertEqual(db.statements, [])


if __name__ == '__main__':
    unittest.main()
