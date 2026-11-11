"""A failed graph conversion must leave a saved document searchable."""
import pytest
from test_document_reprocessing import env, saved_document, submit, run_item


def test_graph_conversion_failure_preserves_current_searchable_chunks(env,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    app,client=env
    project,document,_,_=saved_document(app)
    def fail(*args,**kwargs):
        raise RuntimeError('Model is temporarily unavailable')
    monkeypatch.setattr(SemanticaExtractor,'discover',fail)
    response=submit(client,project,document['id'],mode='discovery')
    assert response.status_code==202,response.text
    operation=response.json()
    repo=app.state.service.repository
    item=repo.ingest_batches.claim(operation['batch']['id'],'test-worker',120)
    with pytest.raises(Exception):run_item(app,item,operation['batch']['settings'])
    current=repo.document_chunks(project,document['id'])
    assert current, 'Graph conversion failure must not remove document retrieval'
    assert current[0]['text']==document['text']
    assert not current[0]['metadata'].get('_deleted')


def test_old_upload_job_cannot_overwrite_new_processing_generation(tmp_path):
    from types import SimpleNamespace
    from knowledge_service.services.document_uploads import DocumentUploads,StagedUpload
    from knowledge_service.models import UploadOptions
    class Repo:
        def get_record(self,*args):
            return {'id':'same-document','kind':'document','version':3,'metadata':{
                'document_generation':2,'status':'processing','source_document_version_id':'source'}}
    class Sources:
        def get(self,*args):
            pytest.fail('An old job must stop before touching the new processing generation')
    uploads=DocumentUploads(tmp_path/'uploads')
    with pytest.raises(ValueError,match='处理代际'):
        uploads.ingest(SimpleNamespace(repository=Repo()),'project',StagedUpload(tmp_path/'old.md','old.md',0,'hash'),
                       UploadOptions(extraction_mode='documents'),task_id='old-job',sources=Sources())
