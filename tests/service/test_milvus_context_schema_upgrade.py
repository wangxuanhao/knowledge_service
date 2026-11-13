import unittest
from unittest.mock import Mock
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.milvus_store import MilvusStore,MilvusSchemaMismatch


class ContextSchemaUpgradeTests(unittest.TestCase):
    names={'owner_id','support_context_id','support_context_version','window_index','window_start','window_end'}

    def test_only_explicit_allowlisted_columns_may_be_added_to_valid_old_schema(self):
        store=MilvusStore(dim=2);store._client=Mock()
        info={'fields':[{'name':c.name} for c in default_catalog().column_defs() if c.name not in self.names]}
        store._schema_issues=Mock(return_value=['missing column '+name for name in self.names])
        self.assertTrue(store._upgrade_context_columns(default_catalog(),info,{}))
        calls=store._client.add_collection_field.call_args_list
        self.assertEqual({call.kwargs['field_name'] for call in calls},self.names)
        self.assertTrue(all(call.kwargs['nullable'] is True for call in calls))

    def test_ordinary_schema_checks_never_upgrade_and_bad_old_schema_rejects_before_mutation(self):
        store=MilvusStore(dim=2);store._client=Mock()
        store._schema_issues=Mock(return_value=['missing column owner_id','embedding dtype differs'])
        with self.assertRaises(MilvusSchemaMismatch):store._upgrade_context_columns(default_catalog(),{'fields':[]},{})
        store._client.add_collection_field.assert_not_called()

    def test_explicit_upgrade_materializes_sealed_history_before_waiting_for_new_indexes(self):
        store=MilvusStore(dim=2);store._client=Mock()
        store._client.get_collection_stats.side_effect=[
            {'schema_version_consistent_segments':'0','schema_version_total_segments':'4'},
            {'schema_version_consistent_segments':'4','schema_version_total_segments':'4'}]
        store._client.compact.return_value=17
        store._client.get_compaction_state.return_value='Completed'
        self.assertTrue(store._materialize_context_columns())
        store._client.compact.assert_called_once()
        store._client.get_compaction_state.assert_called_once_with(17,timeout=20)

    def test_failed_history_materialization_fails_closed(self):
        store=MilvusStore(dim=2);store._client=Mock()
        store._client.get_collection_stats.return_value={
            'schema_version_consistent_segments':'0','schema_version_total_segments':'4'}
        store._client.compact.return_value=17;store._client.get_compaction_state.return_value='Failed'
        with self.assertRaisesRegex(RuntimeError,'分段|升级'):
            store._materialize_context_columns()

    def test_current_segments_do_not_trigger_compaction(self):
        store=MilvusStore(dim=2);store._client=Mock()
        store._client.get_collection_stats.return_value={
            'schema_version_consistent_segments':'4','schema_version_total_segments':'4'}
        self.assertFalse(store._materialize_context_columns())
        store._client.compact.assert_not_called()

    def test_missing_progress_after_compaction_starts_is_not_completion(self):
        store=MilvusStore(dim=2);store._client=Mock()
        store._client.get_collection_stats.side_effect=[
            {'schema_version_consistent_segments':'0','schema_version_total_segments':'4'},{}]
        store._client.compact.return_value=17;store._client.get_compaction_state.return_value='Executing'
        with self.assertRaisesRegex(RuntimeError,'状态|分段'):
            store._materialize_context_columns()


if __name__=='__main__':unittest.main()
