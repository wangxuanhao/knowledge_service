from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from source_test_images import png

def test_capabilities_and_batch_file_contract(tmp_path):
    from knowledge_service.integrations.embeddings import HashingEncoder
    app=create_app(tmp_path/'api.sqlite',encoder=HashingEncoder())
    with TestClient(app) as client:
        cap=client.get('/api/ingest/capabilities').json()
        assert cap['max_batch_items']==1000
        assert cap['concurrency_choices']==['auto',1,2,4,8]
        project=client.post('/api/projects',json={'name':'batch','ontology_mode':'documents'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents'}})
        assert batch.status_code==201, batch.text
        bid=batch.json()['id']
        url=f'/api/projects/{project}/ingest-batches/{bid}/files'
        first=client.post(url,files={'file':('a.md',b'hello','text/markdown')},headers={'Idempotency-Key':'a'})
        assert first.status_code==201,first.text
        again=client.post(url,files={'file':('a.md',b'hello','text/markdown')},headers={'Idempotency-Key':'a'})
        assert first.json()['id']==again.json()['id']
        page=client.get(f'/api/projects/{project}/ingest-batches/{bid}/items?limit=1').json()
        assert page['total']==1 and 'next_cursor' in page


def test_zip_documents_ocr_spans_and_package_are_fixed(tmp_path):
    import io,zipfile,time
    from knowledge_service.integrations.embeddings import HashingEncoder
    app=create_app(tmp_path/'zip.sqlite',encoder=HashingEncoder())
    class OCR:
        engine='fake';languages='ch,en';configuration_fingerprint='fake-config';calls=0
        def available(self):return True
        def recognize(self,data):
            self.calls+=1
            return {'engine_version':'1','preprocess_fingerprint':'pre','width':100,'height':50,
                'blocks':[{'text':'图片安全规则','normalized_text':'图片安全规则','confidence':.99,
                    'x':.1,'y':.1,'width':.5,'height':.3,'page_or_frame':1}]}
    fake=OCR();app.state.source_documents.image_ocr.adapter=fake
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr('a.md','# First\n\n![rule](x.png)')
        archive.writestr('b.md','# Second\n\nText rules.')
        archive.writestr('x.png',png())
    raw=buffer.getvalue()
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'zip','ontology_mode':'documents'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents','requested_concurrency':2}}).json()
        base=f'/api/projects/{project}/ingest-batches/{batch["id"]}'
        response=client.post(base+'/files',files={'file':('rules.zip',raw)},headers={'Idempotency-Key':'zip'})
        assert response.status_code==201,response.text
        assert response.json()['total']==2
        assert client.post(base+'/files',files={'file':('rules.zip',raw)},headers={'Idempotency-Key':'zip'}).json()['total']==2
        assert client.get(base).json()['total_bytes']==len(raw)
        assert client.post(base+'/start').status_code==202
        for _ in range(150):
            items=client.get(base+'/items').json()['items']
            if all(item['status'] in ('completed','needs_attention') for item in items):break
            time.sleep(.1)
        assert all(item['status']=='completed' for item in items),items
        assert fake.calls==1
        item=next(item for item in items if item['original_name']=='a.md')
        source_id=item['source_version_id']
        document=item['result']['document']
        assert document['metadata']['source_document_version_id']==source_id
        ocr_chunk=next(row for row in item['result']['records'] if row['metadata'].get('source_kind')=='image_ocr')
        locate=app.state.source_documents.locate(project,source_id,ocr_chunk['version_id'])
        assert locate['status']=='available' and locate['boxes'] and locate['asset_id']
        assert client.get(f'/api/projects/{project}/source-documents/{source_id}/package').content==raw


def test_missing_image_does_not_block_reading_and_can_be_ignored(tmp_path):
    import time
    from knowledge_service.integrations.embeddings import HashingEncoder
    app=create_app(tmp_path/'missing.sqlite',encoder=HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'missing','ontology_mode':'documents'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents'}}).json()
        base=f'/api/projects/{project}/ingest-batches/{batch["id"]}'
        item=client.post(base+'/files',files={'file':('missing.md',b'# Rules\n\n![missing](missing.png)')}).json()
        client.post(base+'/start')
        for _ in range(100):
            item=client.get(base+'/items').json()['items'][0]
            if item['status'] in ('completed','needs_attention'):break
            time.sleep(.1)
        assert item['status']=='completed' and item['source_version_id']
        document=app.state.service.data.get_record(project,item['document_id'])
        assert document['metadata']['status']=='ready'
        assert document['metadata']['warnings'][0]['code']=='source_asset_missing'
        source_id=item['source_version_id']
        asset=client.get(f'/api/projects/{project}/source-documents/{source_id}').json()['assets'][0]
        ignored=client.post(f'/api/projects/{project}/source-documents/{source_id}/assets/{asset["id"]}/ocr/ignore',json={'reason':'No knowledge text'})
        assert ignored.status_code==200,ignored.text
        assert ignored.json()['status']=='ignored'


def test_zip_preflight_happens_before_any_source_publication(tmp_path,monkeypatch):
    import io,zipfile
    from knowledge_service.integrations.embeddings import HashingEncoder
    monkeypatch.setenv('KNOWLEDGE_INGEST_MAX_FILE_BYTES','300')
    app=create_app(tmp_path/'preflight.sqlite',encoder=HashingEncoder())
    def unexpected(*args,**kwargs):raise AssertionError('Preflight must happen before persist')
    monkeypatch.setattr(app.state.source_documents,'persist',unexpected)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('a.md','small')
        archive.writestr('b.md','b'*500)
    assert len(buffer.getvalue())<300
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'preflight'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents'}}).json()
        base=f'/api/projects/{project}/ingest-batches/{batch["id"]}'
        response=client.post(base+'/files',files={'file':('archive.zip',buffer.getvalue())})
        assert response.status_code==422,response.text
        assert client.get(base).json()['total_items']==0


def test_manifest_title_and_metadata_are_per_document(tmp_path):
    import io,zipfile,json
    from knowledge_service.integrations.embeddings import HashingEncoder
    app=create_app(tmp_path/'manifest.sqlite',encoder=HashingEncoder())
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr('a.md','first')
        archive.writestr('b.md','second')
        archive.writestr('manifest.json',json.dumps({'documents':[
            {'path':'a.md','title':'Title A','metadata':{'rank':1}},
            {'path':'b.md','title':'Title B','metadata':{'rank':2}}]}))
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'manifest'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents'}}).json()
        response=client.post(f'/api/projects/{project}/ingest-batches/{batch["id"]}/files',
            files={'file':('archive.zip',buffer.getvalue())},data={'options':json.dumps({'title':'Caller','metadata':{'rank':0,'shared':True}})})
        assert response.status_code==201,response.text
        items=response.json()['items']
        assert [row['options']['title'] for row in items]==['Title A','Title B']
        assert [row['options']['metadata']['rank'] for row in items]==[1,2]
        assert all(row['options']['metadata']['shared'] for row in items)


def test_ingest_failure_receipts_never_expose_provider_messages(tmp_path,monkeypatch):
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    def fail(self,text,ontology):raise RuntimeError('401 provider-response sk-private-secret https://private.example/request')
    monkeypatch.setattr(SemanticaExtractor,'extract',fail)
    app=create_app(tmp_path/'public-errors.sqlite',encoder=HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'errors'}).json()['id']
        response=client.post(f'/api/projects/{project}/documents',json={'title':'Error receipt','text':'Merchants must follow rules.'})
        assert response.status_code==503
        receipt=client.get(f'/api/projects/{project}/ingest-runs')
        assert receipt.status_code==200
        assert 'sk-private-secret' not in receipt.text and 'private.example' not in receipt.text
