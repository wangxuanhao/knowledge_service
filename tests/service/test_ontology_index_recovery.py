"""Previously exhausted watermark waits resume after the schema-check fix."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from knowledge_service.services.ontology_sync import OntologySync


class LegacyIndexRecovery(TestCase):
    def test_start_recovers_obsolete_wait_failures_without_retrying_other_errors(self):
        repo=Mock()
        repo.list_artifacts.return_value=[
            {'id':'old-wait','status':'failed','error':'Milvus projection has not reached its watermark'},
            {'id':'model-error','status':'failed','error':'模型配置无效'},
            {'id':'disabled','status':'disabled'}]
        worker=OntologySync(SimpleNamespace(data=repo),configured=True)
        worker._loop=lambda:None
        worker.start();worker.close()
        retried=[call.args[1] for call in repo.control_task.call_args_list]
        self.assertEqual(set(retried),{'old-wait','disabled'})
