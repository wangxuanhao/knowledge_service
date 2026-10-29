"""Reading media and failed OCR remain independently recoverable."""
import json
import time
import pytest
from source_test_images import png
from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.integrations.image_ocr import OCRError
from knowledge_service.services.source_documents import SourceDocumentService
from knowledge_service.services.document_ingest_runner import DocumentIngestRunner
from knowledge_service.models import IngestBatchSettings


def test_batch_documents_tolerates_failed_ocr_but_strict_mode_does_not(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    class Adapter:
        engine='test';languages='en';configuration_fingerprint='test'
        def recognize(self,data): raise OCRError('ocr_low_confidence','no readable text')
    repo=Repository(tmp_path/'reading.sqlite');project=repo.create_project('reading')['id']
    service=KnowledgeService(repo,HashingEncoder());sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),Adapter())
    for mode in ['documents','discovery']:
        settings=IngestBatchSettings(extraction_mode=mode).model_dump();batch=repo.ingest_batches.create_batch(project,settings)
        row=repo.ingest_batches.add_item(project,batch['id'],'a.md',b'Article text\n\n![image](x.png)','hash',mode,100,10000)
        source=sources.persist(project,row['document_id'],b'original','a.md',text='Article text\n\n![image](x.png)',assets=[dict(kind='local',status='available',original_path='x.png',data=png())])
        repo.ingest_batches.transition(project,batch['id'],'start');item=repo.ingest_batches.claim(batch['id'],'worker',60)
        item['source_version_id']=source['id']
        runner=DocumentIngestRunner(service,repo.ingest_batches,repo.ingest_checkpoints,sources)
        if mode=='documents':
            result=runner.run(item,settings,'worker')
            assert result['document']['metadata']['status']=='ready'
            assert result['document']['metadata']['warnings']
            assert all('no readable text' not in r['text'] for r in result['records'])
            assert sources.get(project,source['id'])['assets'][0]['status']=='needs_attention'
        else:
            with pytest.raises(OCRError): runner.run(item,settings,'worker')
        repo.ingest_batches.finish(item['id'],'worker',item['lease_token'],'succeeded' if mode=='documents' else 'failed')
    repo.close()


@pytest.mark.parametrize('asynchronous',[False,True])
def test_upload_paths_freeze_source_and_mapped_chunks(tmp_path,asynchronous):
    with TestClient(create_app(tmp_path/'uploads.sqlite',encoder=HashingEncoder(),upload_temp=tmp_path/'uploads')) as client:
        project=client.post('/api/projects',json={'name':'uploads'}).json()['id']
        endpoint='/jobs' if asynchronous else ''
        response=client.post(f'/api/projects/{project}/documents/upload'+endpoint,files={'file':('a.md',b'Article text')},data={'options':json.dumps({'extraction_mode':'documents'})})
        assert response.status_code==(202 if asynchronous else 201),response.text
        result=response.json()
        if asynchronous:
            for _ in range(250):
                job=client.get('/api/jobs/'+result['id']).json()
                if job['status'] not in ['queued','running']:break
                time.sleep(.02)
            assert job['status']=='completed',job
            records=client.app.state.service.data.current_records(project)
            document=next(r for r in records if r['kind']=='document')
        else: document=result['document']
        version=document['metadata'].get('source_document_version_id')
        assert version
        sources=client.app.state.source_documents
        assert sources.source(project,version)['data']==b'Article text'
        assert sources.get(project,version)['chunks']


def test_image_only_reading_has_no_invented_search_chunks(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.document_uploads import DocumentUploads,StagedUpload
    from knowledge_service.integrations.document_parser import ParsedDocument
    from knowledge_service.models import UploadOptions
    from hashlib import sha256
    class Parser:
        def parse(self,*args,**kwargs):return ParsedDocument('![](assets/image.png)',{},[dict(kind='local',status='missing',original_path='assets/image.png')])
    repo=Repository(tmp_path/'image-only.sqlite');project=repo.create_project('reading')['id']
    service=KnowledgeService(repo,HashingEncoder());sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    path=tmp_path/'x.pdf';path.write_bytes(b'%PDF')
    upload=DocumentUploads(tmp_path,Parser())
    result=upload.ingest(service,project,StagedUpload(path,'x.pdf',4,sha256(b'%PDF').hexdigest()),UploadOptions(extraction_mode='documents'),sources=sources)
    assert result['document']['metadata']['status']=='ready'
    assert not [r for r in result['records'] if r['kind']=='chunk']


def test_preview_does_not_import_remote_media_or_publish_source(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path/'preview.sqlite',encoder=HashingEncoder(),upload_temp=tmp_path/'uploads')) as client:
        project=client.post('/api/projects',json={'name':'preview'}).json()['id']
        monkeypatch.setenv('KG_SOURCE_REMOTE_IMAGES_ENABLED','true')
        monkeypatch.setattr(client.app.state.source_documents,'persist',lambda *a,**k:pytest.fail('preview source write'))
        from knowledge_service.integrations.remote_images import RemoteImageImporter
        monkeypatch.setattr(RemoteImageImporter,'fetch',lambda *a:pytest.fail('preview network request'))
        response=client.post(f'/api/projects/{project}/documents/upload/preview',files={'file':('a.md',b'Article\n\n![image](https://example.com/x)')},data={'options':json.dumps({'extraction_mode':'documents'})})
        assert response.status_code==200,response.text
        assert not client.app.state.service.data.current_records(project)


def test_single_upload_maps_indexed_chunks_even_when_extraction_fails(tmp_path,monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.document_uploads import DocumentUploads,StagedUpload
    from knowledge_service.models import UploadOptions
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    from hashlib import sha256
    monkeypatch.setattr(SemanticaExtractor,'discover',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('model_not_found')))
    repo=Repository(tmp_path/'failed-extract.sqlite');project=repo.create_project('upload')['id']
    service=KnowledgeService(repo,HashingEncoder());sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    path=tmp_path/'a.md';path.write_bytes(b'Article evidence')
    uploads=DocumentUploads(tmp_path)
    with pytest.raises(RuntimeError):uploads.ingest(service,project,StagedUpload(path,'a.md',16,sha256(path.read_bytes()).hexdigest()),UploadOptions(extraction_mode='discovery'),sources=sources)
    document=repo.current_records(project,kinds=['document'])[0]
    version=document['metadata']['source_document_version_id']
    assert sources.get(project,version)['chunks']


def test_durable_upload_retry_reuses_source_and_ready_replay_needs_no_staged_file(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.document_uploads import DocumentUploads,StagedUpload
    from knowledge_service.integrations.document_parser import ParsedDocument
    from knowledge_service.models import UploadOptions
    from hashlib import sha256
    class Parser:
        def parse(self,*a,**k):return ParsedDocument('Article\n\n![](image.png)',{},[dict(kind='local',status='missing',original_path='image.png')])
    repo=Repository(tmp_path/'retry.sqlite');project=repo.create_project('retry')['id']
    service=KnowledgeService(repo,HashingEncoder());sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    path=tmp_path/'a.md';path.write_bytes(b'Article')
    upload=DocumentUploads(tmp_path,Parser());staged=StagedUpload(path,'a.md',7,sha256(b'Article').hexdigest())
    with pytest.raises(OCRError):upload.ingest(service,project,staged,UploadOptions(extraction_mode='discovery'),task_id='task',sources=sources)
    document=repo.current_records(project,kinds=['document'])[0];version=document['metadata']['source_document_version_id']
    result=upload.ingest(service,project,staged,UploadOptions(extraction_mode='documents'),task_id='task',sources=sources)
    assert result['document']['metadata']['source_document_version_id']==version
    path.unlink()
    replay=upload.ingest(service,project,staged,UploadOptions(extraction_mode='documents'),task_id='task',sources=sources)
    assert replay['replayed'] and replay['document']['id']==document['id']


def test_async_upload_preserves_public_extraction_failure_contract(tmp_path):
    from types import SimpleNamespace
    from knowledge_service.services.job_handlers import job_handlers
    class Uploads:
        root=tmp_path
        def ingest(self,*a,**k):raise RuntimeError('model_not_found provider private detail')
        def discard(self,*a):pytest.fail('failed upload must remain staged')
    service=SimpleNamespace(data=SimpleNamespace(current_records=lambda p:[]))
    payload={'staged':dict(path=str(tmp_path/'a.md'),original_name='a.md',size=7,digest='sha'),'options':{'extraction_mode':'discovery'}}
    with pytest.raises(RuntimeError) as error:job_handlers(service,Uploads())['ingest_upload'](payload,'project',lambda *a:None,'task')
    assert getattr(error.value,'code',None)=='llm_model_not_found'
    assert 'private detail' not in str(error.value)
