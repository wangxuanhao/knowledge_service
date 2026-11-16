"""Durable preparation boundaries, verified against isolated PostgreSQL."""
import io
import json
import time
from fastapi.testclient import TestClient
import pytest
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder

@pytest.fixture
def prep(tmp_path):
    with TestClient(create_app(tmp_path/'prep',encoder=HashingEncoder(),upload_temp=tmp_path/'uploads')) as client:
        project=client.post('/api/projects',json={'name':'preparations'}).json()['id']
        root=f'/api/projects/{project}/preparations'
        response=client.post(root,json={'defaults':{'extraction_mode':'documents'}})
        assert response.status_code==201,response.text
        yield client,root+'/'+response.json()['id']

def upload(client,root,name='rules.md',data=b'# Rules\n\nHello preparation.',key='upload'):
    r=client.post(root+'/files',files={'file':(name,data)},headers={'Idempotency-Key':key})
    assert r.status_code==201,r.text
    return r.json()

def ready(client,root,item):
    r=client.post(root+'/parse',json={'item_ids':[item['id']],'versions':{item['id']:item['metadata_version']},'idempotency_key':'parse-'+item['id']})
    assert r.status_code==202,r.text
    for _ in range(150):
        value=client.get(root).json()['items'][0]
        if value['status'] in ('completed','reused','failed'):break
        time.sleep(.03)
    assert value['status'] in ('completed','reused'),value
    return value

def test_stage_preview_parse_publish_never_reparses(prep,monkeypatch):
    client,root=prep
    from knowledge_service.integrations.document_parser import DocumentParser
    original=DocumentParser.parse
    calls=[]
    def parse(self,*args,**kwargs):
        calls.append(1)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(DocumentParser,'parse',parse)
    item=upload(client,root)
    assert item['status']=='staged'
    assert calls==[]
    assert client.get(root+'/items/'+item['id']+'/original').content.startswith(b'# Rules')
    assert calls==[]
    item=ready(client,root,item)
    assert len(calls)==1
    result=client.get(root+'/items/'+item['id']+'/result').json()
    assert result['chunks'] and 'Hello' in result['text']
    body={'item_ids':[item['id']],'versions':{item['id']:item['metadata_version']},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'publish'}
    published=client.post(root+'/publish',json=body)
    assert published.status_code==202,published.text
    replay=client.post(root+'/publish',json={**body,'idempotency_key':'other'})
    assert replay.status_code==202,replay.text
    assert replay.json()['batch_id']==published.json()['batch_id']
    batch_root=root.split('/preparations')[0]+'/ingest-batches/'+published.json()['batch_id']
    for _ in range(150):
        batch=client.get(batch_root).json()
        if batch['status'].startswith('completed'):break
        time.sleep(.03)
    assert batch['status']=='completed',batch
    assert len(calls)==1
    operations=client.app.state.preparations
    published_item=operations.batches.store.list_items(item['project_id'],published.json()['batch_id'])['items'][0]
    source=operations.sources.get(item['project_id'],published_item['source_version_id'])
    assert source['document_id']==published_item['document_id']
    assert source['id']!=result['source_version_id']
    reprocessed=client.post(root.split('/preparations')[0]+'/documents/'+published_item['document_id']+'/reprocess',json={'extraction_mode':'documents','idempotency_key':'rebuild-preparation'})
    assert reprocessed.status_code==202,reprocessed.text
    assert len(calls)==1

def test_versions_bulk_and_shared_parse(prep):
    client,root=prep
    one=upload(client,root,key='one')
    two=upload(client,root,key='two')
    assert two['duplicate_of']==one['id']
    conflict=client.post(root+'/bulk',json={'item_ids':[one['id'],two['id']],'fields':{'title':'changed'},'versions':{one['id']:0,two['id']:1},'mode':'overwrite'})
    assert conflict.status_code==409
    assert client.get(root).json()['items'][1]['options'].get('title') is None
    item=ready(client,root,one)
    response=client.patch(root+'/items/'+one['id'],json={'options':{'title':'metadata only'},'expected_version':item['metadata_version']})
    assert response.status_code==200,response.text
    assert response.json()['status']==item['status']
    assert response.json()['parser_fingerprint']==item['parser_fingerprint']
    response=client.post(root+'/parse',json={'item_ids':[two['id']],'versions':{two['id']:1},'idempotency_key':'reuse'})
    assert response.status_code==202,response.text
    assert response.json()['items'][1]['status']=='reused'

def test_native_pdf_preview_does_not_parse_and_bad_zip_rejected(prep,monkeypatch):
    client,root=prep
    from knowledge_service.integrations.document_parser import DocumentParser
    monkeypatch.setattr(DocumentParser,'parse',lambda *args,**kwargs:pytest.fail('unexpected deep parse'))
    from pypdf import PdfWriter
    document=PdfWriter()
    document.add_blank_page(width=72,height=72)
    stream=io.BytesIO()
    document.write(stream)
    data=stream.getvalue()
    document.close()
    item=upload(client,root,'native.pdf',data)
    assert client.get(root+'/items/'+item['id']+'/original').content.startswith(b'%PDF')
    import zipfile
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:archive.writestr('main.pdf',b'%PDF-1.7')
    response=client.post(root+'/files',files={'file':('bad.zip',stream.getvalue())})
    assert response.status_code==422,response.text

def test_upload_reports_project_cached_parse_and_global_operation_conflict(prep):
    client,root=prep
    first=ready(client,root,upload(client,root))
    second=upload(client,root,key='new-upload')
    assert second['reusable'] is True
    other=client.post(root.rsplit('/',1)[0],json={'defaults':{'extraction_mode':'documents'}}).json()
    response=client.post(root.rsplit('/',1)[0]+'/'+other['id']+'/files',files={'file':('different.md',b'different')},headers={'Idempotency-Key':'upload'})
    assert response.status_code==409


def test_running_metadata_changes_shared_cancel_and_fenced_recovery(prep):
    client,root=prep
    runner=client.app.state.preparation_runner
    runner.close()
    one=upload(client,root,key='one')
    two=upload(client,root,key='two')
    ids=[one['id'],two['id']]
    response=client.post(root+'/parse',json={'item_ids':ids,'versions':{i:1 for i in ids},'idempotency_key':'shared'})
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    task=operations.store.claim()
    assert task is not None
    patched=client.patch(root+'/items/'+one['id'],json={'options':{'title':'edited while running'},'expected_version':1})
    assert patched.status_code==200,patched.text
    assert patched.json()['status']=='running'
    assert patched.json()['parser_fingerprint']==one['parser_fingerprint']
    response=client.post(root+'/cancel',json={'item_ids':[one['id']]})
    assert response.status_code==200,response.text
    assert operations.store.task(task['id'])['status']=='running'
    with operations.store.repo._transaction():
        operations.store.db.execute("UPDATE preparation_tasks SET lease_until=now()-interval '1 second' WHERE id=?",(task['id'],))
    recovered=operations.store.claim()
    assert recovered['id']==task['id'] and recovered['lease_token']!=task['lease_token']
    assert not operations.store.update(task,'completed','completed',result={'text':'old'})
    assert operations.store.task(task['id'])['status']=='running'
    response=client.post(root+'/cancel',json={'item_ids':[two['id']]})
    assert response.status_code==200,response.text
    assert operations.store.task(task['id'])['status']=='cancelling'
    operations.store.acknowledge_cancel(recovered)
    assert operations.store.task(task['id'])['status']=='cancelled'

def test_partial_scope_bulk_fill_missing_and_overwrite(prep):
    client,root=prep
    item=upload(client,root)
    response=client.patch(root+'/items/'+item['id'],json={'options':{'applicability':{'platforms':{'mode':'specific','values':['美团']}}},'expected_version':1})
    assert response.status_code==200,response.text
    fields={'applicability':{'platforms':{'mode':'specific','values':['抖音']},'businesses':{'mode':'specific','values':['到店']}}}
    response=client.post(root+'/bulk',json={'item_ids':[item['id']],'fields':fields,'mode':'fill_missing','versions':{item['id']:2}})
    assert response.status_code==200,response.text
    scope=response.json()['items'][0]['options']['applicability']
    assert scope['platforms']['values']==['美团']
    assert scope['businesses']['values']==['到店']
    response=client.post(root+'/bulk',json={'item_ids':[item['id']],'fields':{'applicability':{'audiences':{'mode':'all','values':[]}}},'mode':'overwrite','versions':{item['id']:3}})
    assert response.status_code==200,response.text
    scope=response.json()['items'][0]['options']['applicability']
    assert scope['platforms']['values']==['美团'] and scope['businesses']['values']==['到店']
    assert scope['audiences']['mode']=='all'

def test_invalid_pdf_rejected_without_deep_parser(prep,monkeypatch):
    client,root=prep
    from knowledge_service.integrations.document_parser import DocumentParser
    monkeypatch.setattr(DocumentParser,'parse',lambda *a,**k:pytest.fail('deep parse in upload'))
    response=client.post(root+'/files',files={'file':('broken.pdf',b'%PDF-1.7\nthis is not a PDF document')})
    assert response.status_code==422,response.text


def test_publication_settings_override_only_extraction_fields(prep,monkeypatch):
    client,root=prep
    item=upload(client,root)
    item=ready(client,root,item)
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'settings':{'extraction_mode':'documents','chunk_size':9000},'idempotency_key':'documents-mode'})
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    batch=operations.batches.store.list_items(root.split('/')[3],response.json()['batch_id'])
    assert batch['items'][0]['options']['extraction_mode']=='documents'
    assert batch['items'][0]['options']['chunk_size']==1800


def test_rechunk_does_not_call_media_processors(prep,monkeypatch):
    client,root=prep
    item=ready(client,root,upload(client,root,data=b'# Heading\n\n'+b'body '*60))
    sources=client.app.state.source_documents
    monkeypatch.setattr(sources.image_ocr,'process_asset',lambda *a,**k:pytest.fail('OCR repeated'))
    monkeypatch.setattr(sources.video_asr,'process_asset',lambda *a,**k:pytest.fail('ASR repeated'))
    response=client.patch(root+'/items/'+item['id'],json={'options':{'chunk_size':100,'chunk_overlap':10},'expected_version':1})
    assert response.status_code==200,response.text
    result=client.get(root+'/items/'+item['id']+'/result')
    assert result.status_code==200,result.text
    assert len(result.json()['chunks'])>1
    assert response.json()['parser_fingerprint']==item['parser_fingerprint']
    assert response.json()['chunk_version']!=item['chunk_version']

def test_expiry_reclaims_abandoned_sources_but_keeps_published(prep):
    client,root=prep
    first=ready(client,root,upload(client,root))
    operations=client.app.state.preparations
    abandoned=client.get(root+'/items/'+first['id']+'/result').json()['source_version_id']
    session_id=root.rsplit('/',1)[1]
    with operations.store.repo._transaction():
        operations.store.db.execute("UPDATE preparation_sessions SET expires_at=now()-interval '1 second' WHERE id=?",(session_id,))
    operations.store.cleanup()
    with pytest.raises(KeyError):operations.sources.get(first['project_id'],abandoned)
    newroot=root.rsplit('/',1)[0]
    session=client.post(newroot,json={'defaults':{'extraction_mode':'documents'}}).json()
    otherroot=newroot+'/'+session['id']
    item=ready(client,otherroot,upload(client,otherroot,key='other-expiry'))
    result=client.get(otherroot+'/items/'+item['id']+'/result').json()
    response=client.post(otherroot+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'publish-expiry'})
    assert response.status_code==202,response.text
    formal=operations.batches.store.list_items(item['project_id'],response.json()['batch_id'])['items'][0]
    with operations.store.repo._transaction():
        operations.store.db.execute("UPDATE preparation_sessions SET expires_at=now()-interval '1 second' WHERE id=?",(session['id'],))
    operations.store.cleanup()
    assert operations.sources.get(item['project_id'],formal['source_version_id'])['document_id']==formal['document_id']
    with pytest.raises(KeyError):operations.sources.get(item['project_id'],result['source_version_id'])


def test_resource_zip_previews_html_markdown_video_without_parser(prep,monkeypatch):
    client,root=prep
    from knowledge_service.integrations.document_parser import DocumentParser
    from source_test_images import png
    import zipfile
    monkeypatch.setattr(DocumentParser,'parse',lambda *a,**k:pytest.fail('preview deep parser'))
    for extension,body in [('md','# Main\n\n![pic](assets/a.png)\n\n<video src="assets/v.mp4"></video>'),('html','<h1>Main</h1><p>Article</p><img src="assets/a.png"><video src="assets/v.mp4"></video>')]:
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('outer/main.'+extension,body)
            archive.writestr('outer/assets/a.png',png())
            archive.writestr('outer/assets/v.mp4',b'resource-only-video')
        item=upload(client,root,'resources.zip',stream.getvalue(),key='zip-'+extension)
        response=client.get(root+'/items/'+item['id']+'/preview')
        assert response.status_code==200,response.text
        value=response.json()
        assert 'Main' in value['html']
        assert 'data:image/png;base64,' in value['html']
        assert 'data:video/mp4;base64,' in value['html']
        assert value['warnings']==[]

def test_shared_queued_task_survives_first_session_expiry(prep):
    client,root=prep
    client.app.state.preparation_runner.close()
    first=upload(client,root,key='expire-first')
    first_response=client.post(root+'/parse',json={'item_ids':[first['id']],'versions':{first['id']:1},'idempotency_key':'expire-first-parse'})
    assert first_response.status_code==202,first_response.text
    newroot=root.rsplit('/',1)[0]
    second_session=client.post(newroot,json={'defaults':{'extraction_mode':'documents'}}).json()
    otherroot=newroot+'/'+second_session['id']
    second=upload(client,otherroot,key='expire-second')
    response=client.post(otherroot+'/parse',json={'item_ids':[second['id']],'versions':{second['id']:1},'idempotency_key':'expire-second-parse'})
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    task_id=operations.store.get(first['project_id'],second_session['id'],'system:auth-disabled')['items'][0]['task_id']
    with operations.store.repo._transaction():
        operations.store.db.execute("UPDATE preparation_sessions SET expires_at=now()-interval '1 second' WHERE id=?",(root.rsplit('/',1)[1],))
    operations.store.cleanup()
    task=operations.store.claim()
    assert task['id']==task_id and task['file_id']==second['id']
    from knowledge_service.services.preparation_runner import PreparationRunner
    runner=PreparationRunner(operations)
    try:runner._run(task)
    finally:runner.close()
    assert operations.store.task(task_id)['status']=='completed'
    assert client.get(otherroot+'/items/'+second['id']+'/result').status_code==200


def test_saved_publication_settings_survive_reload(prep):
    client,root=prep
    response=client.patch(root+'/settings',json={'settings':{'extraction_mode':'documents','resolve_entities':False},'expected_version':0})
    assert response.status_code==200,response.text
    assert client.get(root).json()['publication_settings']['extraction_mode']=='documents'
    item=ready(client,root,upload(client,root))
    body={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'saved-mode','settings_version':1}
    response=client.post(root+'/publish',json=body)
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    rows=operations.batches.store.list_items(item['project_id'],response.json()['batch_id'])
    assert rows['items'][0]['options']['extraction_mode']=='documents'
    assert rows['items'][0]['options']['resolve_entities'] is False

def test_settings_cas_blocks_stale_publish_and_terminal_parse_requeues(prep):
    client,root=prep
    item=ready(client,root,upload(client,root))
    one=client.patch(root+'/settings',json={'settings':{'extraction_mode':'documents'},'expected_version':0})
    assert one.status_code==200 and one.json()['settings_version']==1
    conflict=client.patch(root+'/settings',json={'settings':{'extraction_mode':'ontology'},'expected_version':0})
    assert conflict.status_code==409
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'stale-settings','settings_version':0})
    assert response.status_code==409,response.text
    client.app.state.preparation_runner.close()
    other=upload(client,root,data=b'other reusable rule',key='cancel-queue')
    response=client.post(root+'/parse',json={'item_ids':[other['id']],'versions':{other['id']:1},'idempotency_key':'cancel-queue'})
    assert response.status_code==202,response.text
    assert client.post(root+'/cancel',json={'item_ids':[other['id']]}).status_code==200
    response=client.post(root+'/parse',json={'item_ids':[other['id']],'versions':{other['id']:1},'idempotency_key':'restart-queue'})
    assert response.status_code==202,response.text
    assert response.json()['items'][1]['status']=='queued'


def test_parser_runtime_cache_evicts_changed_model_configuration(prep,monkeypatch):
    client,root=prep
    client.app.state.preparation_runner.close()
    from knowledge_service.services.preparation_runner import PreparationRunner,CachedDocumentParser
    original=CachedDocumentParser.__init__
    constructions=[]
    def init(self,**kwargs):
        constructions.append(kwargs)
        original(self,**kwargs)
    monkeypatch.setattr(CachedDocumentParser,'__init__',init)
    operations=client.app.state.preparations
    runner=PreparationRunner(operations)
    try:
        for ordinal,language in enumerate(['ch,en','en','en']):
            monkeypatch.setenv('KG_IMAGE_OCR_LANGUAGES',language)
            item=upload(client,root,data=('body '+str(ordinal)).encode(),key='runtime-'+str(ordinal))
            response=client.post(root+'/parse',json={'item_ids':[item['id']],'versions':{item['id']:1},'idempotency_key':'runtime-'+str(ordinal)})
            assert response.status_code==202,response.text
            task=operations.store.claim()
            runner._run(task)
            assert operations.store.task(task['id'])['status']=='completed'
        assert len(constructions)==2
    finally:runner.close()
from test_video_asr import video


def test_publication_clones_shared_source_media_receipts_without_recognition(prep,video,monkeypatch):
    client,root=prep
    from test_video_asr import Adapter as ASRAdapter
    from source_test_images import png
    import zipfile
    class OCRAdapter:
        engine='test';languages='ch';configuration_fingerprint='clone-ocr';calls=0
        def recognize(self,data):
            self.calls+=1
            return dict(engine_version='1',preprocess_fingerprint='pre',width=8,height=8,
                blocks=[dict(text='mapped OCR evidence',normalized_text='mapped OCR evidence',confidence=.95,x=.1,y=.1,width=.5,height=.5,page_or_frame=1)])
    sources=client.app.state.source_documents
    image_adapter=OCRAdapter()
    asr_adapter=ASRAdapter()
    sources.image_ocr.adapter=image_adapter
    sources.video_asr.adapter=asr_adapter
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('main.md','# Source\n\n![image](assets/p.png)\n\n<video src="assets/v.mp4"></video>')
        archive.writestr('assets/p.png',png())
        archive.writestr('assets/v.mp4',video.read_bytes())
    one=ready(client,root,upload(client,root,'resources.zip',stream.getvalue(),key='media-one'))
    two=upload(client,root,'resources.zip',stream.getvalue(),key='media-two')
    parsed=client.post(root+'/parse',json={'item_ids':[two['id']],'versions':{two['id']:1},'idempotency_key':'media-reuse'})
    assert parsed.status_code==202,parsed.text
    assert parsed.json()['items'][1]['status']=='reused'
    two=parsed.json()['items'][1]
    assert image_adapter.calls==1 and asr_adapter.calls==1
    original=client.get(root+'/items/'+one['id']+'/result').json()
    assert len(original['chunks'])==1 and original['chunks'][0]['metadata']['source_kind']=='mixed'
    monkeypatch.setattr(sources.image_ocr,'process_asset',lambda *a,**k:pytest.fail('publish repeated OCR'))
    monkeypatch.setattr(sources.video_asr,'process_asset',lambda *a,**k:pytest.fail('publish repeated ASR'))
    ids=[one['id'],two['id']]
    response=client.post(root+'/publish',json={'item_ids':ids,'versions':{i:1 for i in ids},'result_versions':{one['id']:one['chunk_version'],two['id']:two['chunk_version']},'idempotency_key':'media-publish','independent_item_ids':[two['id']]})
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    for _ in range(200):
        batch=operations.batches.get(one['project_id'],response.json()['batch_id'])
        if batch['status']=='completed':break
        time.sleep(.03)
    assert batch['counts']['succeeded']==2,batch
    items=operations.batches.store.list_items(one['project_id'],batch['id'])['items']
    assert len({i['source_version_id'] for i in items})==2
    source_objects=set()
    for item in items:
        source=sources.get(one['project_id'],item['source_version_id'])
        assert source['document_id']==item['document_id']
        assert source['id']!=original['source_version_id']
        source_objects.add(source['source_object_id'])
        for asset in source['assets']:
            receipt=sources.asr(one['project_id'],source['id'],asset['id']) if asset.get('provenance',{}).get('media_type')=='video' else sources.ocr(one['project_id'],source['id'],asset['id'])
            assert receipt['run']['document_version_id']==source['id']
            assert receipt['run']['asset_id']==asset['id']
            assert receipt['run']['status']=='completed'
        assert len(source['chunks'])==1
        location=sources.locate(one['project_id'],source['id'],source['chunks'][0]['version_id'])
        assert len(location['media_locations'])==2
        assert {group['asset_id'] for group in location['media_locations']}=={a['id'] for a in source['assets']}
    assert len(source_objects)==1

def test_cancel_between_result_and_publish_prevents_any_formal_write(prep,monkeypatch):
    client,root=prep
    item=ready(client,root,upload(client,root))
    operations=client.app.state.preparations
    original=operations.result
    def cancel_after_result(project,session,owner,item_id):
        result=original(project,session,owner,item_id)
        operations.cancel(project,session,owner,[item_id])
        return result
    monkeypatch.setattr(operations,'result',cancel_after_result)
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'cancel-race'})
    assert response.status_code==409,response.text
    assert operations.batches.store.list_batches(item['project_id'])['total']==0


def test_system_default_ocr_disabled_is_applied_to_resource_assets(prep,monkeypatch):
    client,root=prep
    from source_test_images import png
    import zipfile
    class Adapter:
        engine='test';languages='ch';configuration_fingerprint='disabled-default';calls=0
        def recognize(self,data):
            self.calls+=1
            return dict(engine_version='1',preprocess_fingerprint='pre',width=8,height=8,
                blocks=[dict(text='OCR body',normalized_text='OCR body',confidence=.95,x=.1,y=.1,width=.5,height=.5,page_or_frame=1)])
    adapter=Adapter()
    client.app.state.source_documents.image_ocr.adapter=adapter
    monkeypatch.setenv('KG_DOCUMENT_OCR_MODE','disabled')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('main.md','# Native body\n\n![asset](assets/p.png)')
        archive.writestr('assets/p.png',png())
    item=ready(client,root,upload(client,root,'disabled.zip',stream.getvalue()))
    assert adapter.calls==0
    assert item['status']=='completed'


def test_restart_document_discards_preparation_receipt(prep,monkeypatch):
    client,root=prep
    item=ready(client,root,upload(client,root))
    operations=client.app.state.preparations
    service=client.app.state.service
    original=service.ingest
    monkeypatch.setattr(service,'ingest',lambda *a,**k:(_ for _ in ()).throw(ValueError('bounded fixture failure')))
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'restart-document'})
    assert response.status_code==202,response.text
    batch_id=response.json()['batch_id']
    for _ in range(150):
        rows=operations.batches.store.list_items(item['project_id'],batch_id)['items']
        if rows[0]['status']=='failed':break
        time.sleep(.03)
    assert rows[0]['status']=='failed',rows
    client.app.state.ingest_batch_runner.close()
    monkeypatch.setattr(service,'ingest',original)
    operations.batches.store.retry(item['project_id'],batch_id,rows[0]['id'],'restart_document')
    leased=operations.batches.store.claim(batch_id,'restart-test',120)
    assert leased['prepared_result'] is None
    from knowledge_service.services.document_ingest_runner import DocumentIngestRunner
    result=DocumentIngestRunner(service,operations.batches.store,service.repository.ingest_checkpoints,operations.sources).run(leased,{},'restart-test')
    assert result['document']['metadata']['status']=='ready'


def test_existing_formal_document_requires_explicit_independent_choice(prep):
    client,root=prep
    first=ready(client,root,upload(client,root))
    published=client.post(root+'/publish',json={'item_ids':[first['id']],'versions':{first['id']:1},'result_versions':{first['id']:first['chunk_version']},'idempotency_key':'identity-one'})
    assert published.status_code==202,published.text
    operations=client.app.state.preparations
    for _ in range(150):
        batch=operations.batches.get(first['project_id'],published.json()['batch_id'])
        if batch['status']=='completed':break
        time.sleep(.03)
    second=upload(client,root,key='identity-second')
    assert second['existing_documents']
    response=client.post(root+'/parse',json={'item_ids':[second['id']],'versions':{second['id']:1},'idempotency_key':'identity-second-parse'})
    assert response.status_code==202,response.text
    second=response.json()['items'][1]
    body={'item_ids':[second['id']],'versions':{second['id']:1},'result_versions':{second['id']:second['chunk_version']},'idempotency_key':'identity-second-publish'}
    assert client.post(root+'/publish',json=body).status_code==409
    allowed=client.post(root+'/publish',json={**body,'independent_item_ids':[second['id']]})
    assert allowed.status_code==202,allowed.text
    assert allowed.json()['batch_id']!=published.json()['batch_id']

def test_queued_formal_publication_is_already_an_existing_document(prep):
    client,root=prep
    first=ready(client,root,upload(client,root))
    client.app.state.ingest_batch_runner.close()
    published=client.post(root+'/publish',json={'item_ids':[first['id']],'versions':{first['id']:1},'result_versions':{first['id']:first['chunk_version']},'idempotency_key':'queued-first'})
    assert published.status_code==202,published.text
    operations=client.app.state.preparations
    first_job=operations.batches.store.list_items(first['project_id'],published.json()['batch_id'])['items'][0]
    assert first_job['status']=='pending'
    with pytest.raises(KeyError):operations.service.repository.get_record(first['project_id'],first_job['document_id'])
    second=upload(client,root,key='queued-second')
    assert second['existing_documents'][0]['id']==first_job['document_id']
    response=client.post(root+'/parse',json={'item_ids':[second['id']],'versions':{second['id']:1},'idempotency_key':'queued-second-parse'})
    assert response.status_code==202,response.text
    second=response.json()['items'][1]
    response=client.post(root+'/publish',json={'item_ids':[second['id']],'versions':{second['id']:1},'result_versions':{second['id']:second['chunk_version']},'idempotency_key':'queued-second-publish'})
    assert response.status_code==409,response.text
    assert operations.batches.store.list_batches(first['project_id'])['total']==1

def test_unstarted_legacy_batch_upload_is_an_existing_document(prep):
    client,root=prep
    item=upload(client,root)
    operations=client.app.state.preparations
    from knowledge_service.models import IngestBatchSettings
    from starlette.datastructures import UploadFile
    batch=operations.batches.create(item['project_id'],IngestBatchSettings(extraction_mode='documents'))
    legacy=operations.batches.upload(item['project_id'],batch['id'],UploadFile(io.BytesIO(b'# Rules\n\nHello preparation.'),filename='legacy.md'),'legacy-upload',{'extraction_mode':'documents'})
    assert legacy['source_version_id'] is None
    refreshed=client.get(root).json()['items'][0]
    assert any(doc['id']==legacy['document_id'] for doc in refreshed['existing_documents'])
    item=ready(client,root,item)
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'legacy-duplicate'})
    assert response.status_code==409,response.text
    assert operations.batches.store.list_batches(item['project_id'])['total']==1

def test_preview_reads_only_referenced_resources_and_warns_corrupt_images(prep,monkeypatch):
    client,root=prep
    from source_test_images import png
    import zipfile
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('outer/main.md','![good](./assets/valid%20space.png?v=1#x)\n\n![bad](assets/corrupt.png)')
        archive.writestr('outer/assets/valid space.png',png())
        archive.writestr('outer/assets/corrupt.png',b'broken image')
        archive.writestr('outer/assets/unused.png',b'not referenced')
    item=upload(client,root,'resources.zip',stream.getvalue())
    reads=[]
    original=zipfile.ZipFile.read
    def read(self,name,*args,**kwargs):
        reads.append(name.filename if isinstance(name,zipfile.ZipInfo) else name)
        return original(self,name,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'read',read)
    response=client.get(root+'/items/'+item['id']+'/preview')
    assert response.status_code==200,response.text
    value=response.json()
    assert not any('unused.png' in path for path in reads)
    assert 'data:image/png;base64,' in value['html']
    assert any('corrupt.png' in warning for warning in value['warnings'])
    assert 'YnJva2VuIGltYWdl' not in value['html']

def test_preparation_owner_project_and_expiry_are_enforced_for_all_reads(prep):
    client,root=prep
    item=upload(client,root)
    operations=client.app.state.preparations
    session=root.rsplit('/',1)[1]
    with pytest.raises(KeyError):operations.get(item['project_id'],session,'another-owner')
    other=client.post('/api/projects',json={'name':'other owner project'}).json()['id']
    wrong=f'/api/projects/{other}/preparations/{session}'
    assert client.get(wrong).status_code==404
    assert client.get(wrong+'/items/'+item['id']+'/original').status_code==404
    assert client.get(wrong+'/items/'+item['id']+'/result').status_code==404
    with operations.store.repo._transaction():
        operations.store.db.execute("UPDATE preparation_sessions SET expires_at=now()-interval '1 second' WHERE id=?",(session,))
    assert operations.store.file(item['id']).startswith(b'# Rules')
    assert client.get(root).status_code==404
    assert client.get(root+'/items/'+item['id']+'/original').status_code==404
    assert client.get(root+'/items/'+item['id']+'/result').status_code==404


@pytest.mark.parametrize('code', ['asr_runtime_error', 'asr_timeout', 'ocr_failed', 'source_asset_missing'])
def test_partial_recognition_error_fails_preparation(prep, monkeypatch, code):
    client, root = prep
    import knowledge_service.services.preparation_runner as runner
    monkeypatch.setattr(runner, 'prepare_source_ingest', lambda *a, **k:
        ([], '正文仍然存在', [dict(code=code, message='附件识别失败')]))
    item = upload(client, root)
    response = client.post(root+'/parse', json={'item_ids':[item['id']],
        'versions':{item['id']:1}, 'idempotency_key':'partial'})
    assert response.status_code == 202
    for _ in range(150):
        value = client.get(root).json()['items'][0]
        if value['status'] in ('failed', 'completed'): break
        time.sleep(.03)
    assert value['status'] == 'failed', value
    assert not value['result_ready']


def test_legacy_completed_result_with_media_error_cannot_publish(prep):
    client, root = prep
    item = ready(client, root, upload(client, root))
    store = client.app.state.preparations.store
    task = store.task(item['parse_task_id'])
    task['result']['warnings'] = [dict(code='asr_runtime_error', message='附件识别失败')]
    from knowledge_service.repository.preparation_store import encoded
    with store.repo._transaction():
        store.db.execute('UPDATE preparation_tasks SET result=?::jsonb WHERE id=?',
            (encoded(task['result']), task['id']))
    value = client.get(root).json()['items'][0]
    assert value['status'] == 'failed'
    assert not value['result_ready'] and not value['reusable']
    response = client.post(root+'/publish', json={'item_ids':[item['id']],
        'versions':{item['id']:1}, 'result_versions':{item['id']:item['chunk_version']},
        'idempotency_key':'blocked-partial'})
    assert response.status_code == 409, response.text


def test_retry_invalid_completed_cache_requeues_same_task(prep):
    client, root = prep
    item = ready(client, root, upload(client, root))
    client.app.state.preparation_runner.close()
    store = client.app.state.preparations.store
    task = store.task(item['parse_task_id'])
    task['result']['warnings'] = [dict(code='asr_runtime_error', message='附件识别失败')]
    from knowledge_service.repository.preparation_store import encoded
    with store.repo._transaction():
        store.db.execute('UPDATE preparation_tasks SET result=?::jsonb WHERE id=?',
            (encoded(task['result']), task['id']))
    response = client.post(root+'/retry', json={'item_ids':[item['id']],
        'versions':{item['id']:1}, 'idempotency_key':'retry-invalid-cache'})
    assert response.status_code == 202, response.text
    value = response.json()['items'][0]
    assert value['status'] == 'queued', value
    assert value['parse_task_id'] == task['id']
    assert store.task(task['id'])['result'] is None



def test_retry_cancelled_item_preserves_shared_healthy_completed_cache(prep):
    client, root = prep
    first = ready(client, root, upload(client, root))
    second = upload(client, root, key='second')
    response = client.post(root+'/parse', json={'item_ids':[second['id']],
        'versions':{second['id']:1}, 'idempotency_key':'reuse-shared'})
    assert response.status_code == 202
    client.app.state.preparation_runner.close()
    assert client.post(root+'/cancel', json={'item_ids':[second['id']]}).status_code == 200
    response = client.post(root+'/retry', json={'item_ids':[second['id']],
        'versions':{second['id']:1}, 'idempotency_key':'retry-shared'})
    assert response.status_code == 202, response.text
    assert response.json()['items'][1]['status'] == 'reused'
    task = client.app.state.preparations.store.task(first['parse_task_id'])
    assert task['status'] == 'completed' and task['result']
    assert client.get(root+'/items/'+first['id']+'/result').status_code == 200



def test_runtime_changed_result_is_stale_and_can_retry_directly(prep, monkeypatch):
    client, root = prep
    item = ready(client, root, upload(client, root))
    client.app.state.preparation_runner.close()
    import knowledge_service.services.preparations as module
    monkeypatch.setattr(module, 'fingerprint', lambda *args: 'updated-parser-runtime')
    value = client.get(root).json()['items'][0]
    assert value['status'] == 'stale', value
    assert not value['result_ready'] and not value['reusable']
    assert client.get(root+'/items/'+item['id']+'/result').status_code == 409
    response = client.post(root+'/retry', json={'item_ids':[item['id']],
        'versions':{item['id']:item['metadata_version']}, 'idempotency_key':'retry-updated-runtime'})
    assert response.status_code == 202, response.text
    updated = response.json()['items'][0]
    assert updated['status'] == 'queued'
    assert updated['parser_fingerprint'] == 'updated-parser-runtime'
    assert updated['metadata_version'] == item['metadata_version']
    assert updated['options'] == item['options']
    assert updated['parse_task_id'] != item['parse_task_id']
    assert client.app.state.preparations.store.task(item['parse_task_id'])['status'] == 'completed'



def test_filtered_ocr_notice_allows_normal_publication(prep):
    from source_test_images import png
    import zipfile
    client,root=prep
    class Adapter:
        engine='test';languages='ch';configuration_fingerprint='filtered-publish'
        def recognize(self,data):
            return dict(engine_version='1',preprocess_fingerprint='pre',width=8,height=8,blocks=[
                dict(text=text,normalized_text=text,confidence=score,x=0,y=0,width=.5,height=.5)
                for text,score in [('有效规则',.99),('可靠文字',.92),('noise',.47)]])
    client.app.state.source_documents.image_ocr.adapter=Adapter()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('rule.md','before ![](assets/a.png) after');archive.writestr('assets/a.png',png())
    item=ready(client,root,upload(client,root,'filtered.zip',stream.getvalue()))
    result=client.get(root+'/items/'+item['id']+'/result').json()
    assert '可靠文字' in result['text'] and 'noise' not in result['text']
    assert any(w.get('code')=='ocr_text_filtered' for w in result['warnings'])
    response=client.post(root+'/publish',json={'item_ids':[item['id']],'versions':{item['id']:1},
        'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'filtered-publish'})
    assert response.status_code==202,response.text
