import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock,patch
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput,build_projections,projection_fingerprint


class ProjectionContextAuditTests(unittest.TestCase):
    def system(self):
        value=ProjectionInput(dict(project_id='p',id='a',version_id='av1',kind='attribute',subject_id='e',type='p',value='body',datatype='string'))
        rows=list(build_projections(value,default_catalog(),index_version=1))
        expected={row['vector_id']:{'version_id':row['version_id'],'fingerprint':projection_fingerprint(row)} for row in rows}
        receipts=SimpleNamespace(for_vector_ids=lambda *args:{key:expected[key] for key in args[-1] if key in expected},manifest=lambda *args:dict(expected))
        reader=SimpleNamespace(projections=SimpleNamespace(receipts=receipts))
        repo=SimpleNamespace(independent_read_view=lambda:nullcontext(reader))
        return repo,value,expected

    def test_authority_windows_are_rechecked_before_native_receipt_audit(self):
        from knowledge_service.services.projection_audit import audit_milvus_authority
        repo,value,expected=self.system();adapter=Mock();adapter.validate.return_value={'ok':True}
        with patch('knowledge_service.repository.projection_inputs.ProjectionInputs') as inputs:
            inputs.return_value.batches.return_value=iter([(value,)])
            self.assertTrue(audit_milvus_authority(repo,'p',{'index_version':1},default_catalog(),adapter,lambda:True)['ok'])
        adapter.validate.assert_called_once()

    def test_context_fingerprint_drift_rejects_without_mutating_native(self):
        from knowledge_service.services.projection_audit import audit_milvus_authority
        repo,value,expected=self.system();expected[next(iter(expected))]['fingerprint']='wrong';adapter=Mock()
        with patch('knowledge_service.repository.projection_inputs.ProjectionInputs') as inputs:
            inputs.return_value.batches.return_value=iter([(value,)])
            with self.assertRaisesRegex(RuntimeError,'对账|authority'):
                audit_milvus_authority(repo,'p',{'index_version':1},default_catalog(),adapter,lambda:True)
        adapter.validate.assert_not_called()

    def test_background_audit_stops_at_record_budget_and_returns_continuation(self):
        from knowledge_service.services.projection_audit import audit_milvus_authority
        repo,value,expected=self.system();expected.clear()
        values=[ProjectionInput({**value.record,'id':'a'+str(i),'version_id':'v'+str(i)}) for i in range(3)]
        for value in values:
            for row in build_projections(value,default_catalog(),index_version=1):
                expected[row['vector_id']]={'version_id':row['version_id'],'fingerprint':projection_fingerprint(row)}
        adapter=Mock();adapter.validate_subset.return_value={'ok':True}
        with patch('knowledge_service.repository.projection_inputs.ProjectionInputs') as inputs:
            inputs.return_value.batches.return_value=iter([values])
            result=audit_milvus_authority(repo,'p',{'index_version':1},default_catalog(),adapter,lambda:True,max_records=2)
        self.assertFalse(result['complete']);self.assertEqual(result['after'],'a1')
        self.assertEqual(result['authority_vectors'],2);adapter.validate.assert_not_called()

    def test_readonly_audit_does_not_lease_and_interrupt_page_searches(self):
        from knowledge_service.services.projection_runtime import ProjectionWorker
        projections=Mock()
        projections.search_index.return_value={'index_version':1,'watermark':2}
        projections.project_watermark.return_value=2
        projections.cursor.return_value={'rebuild_state':{'phase':'ready','index_version':1,'catalog':default_catalog().to_dict()}}
        projections.search_index_is_current.return_value=True
        worker=ProjectionWorker(SimpleNamespace(projections=projections),SimpleNamespace(schema_version=4),cleanup_handlers={})
        with patch('knowledge_service.services.projection_audit.audit_milvus_authority',return_value=
                   {'ok':True,'complete':False,'after':'a','authority_vectors':1}):
            worker._audit_milvus('p',Mock())
        projections.claim_maintenance.assert_not_called()

    def test_bounded_terminal_pass_uses_count_after_subset_checks(self):
        from knowledge_service.services.projection_audit import audit_milvus_authority
        repo,value,expected=self.system();adapter=Mock()
        adapter.validate_subset.return_value={'ok':True}
        adapter.validate_count.return_value={'ok':True,'count':len(expected)}
        with patch('knowledge_service.repository.projection_inputs.ProjectionInputs') as inputs:
            inputs.return_value.batches.return_value=iter([(value,)])
            result=audit_milvus_authority(repo,'p',{'index_version':1},default_catalog(),adapter,lambda:True,max_records=128)
        self.assertTrue(result['complete']);adapter.validate.assert_not_called()
        adapter.validate_subset.assert_called_once()
        adapter.validate_count.assert_called_once_with('p',{'index_version':1},default_catalog(),expected_count=len(expected))

    def test_native_count_is_parameterized_and_does_not_fetch_payloads(self):
        from knowledge_service.integrations.milvus_store import MilvusStore
        store=MilvusStore(dim=2);store._client=Mock();store.check_collection=Mock()
        store._client.query.return_value=[{'count(*)':7}]
        result=store.validate_projection_count(7,project_id='p',index_version=4,catalog=default_catalog())
        self.assertTrue(result['ok']);self.assertEqual(result['count'],7)
        call=store._client.query.call_args
        self.assertEqual(call.kwargs['output_fields'],['count(*)'])
        self.assertEqual(call.kwargs['filter_params'],{'project':'p','index':4})
        self.assertIn('project_id == {project}',call.kwargs['filter'])
        self.assertIn('index_version == {index}',call.kwargs['filter'])
        self.assertEqual(call.kwargs['consistency_level'],'Strong')
        store._client.query_iterator.assert_not_called()
        store._client.query.return_value=[{'count(*)':8}]
        self.assertFalse(store.validate_projection_count(7,project_id='p',index_version=4,catalog=default_catalog())['ok'])


if __name__=='__main__':unittest.main()
