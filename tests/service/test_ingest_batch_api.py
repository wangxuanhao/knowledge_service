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
        assert 'batch' not in page
        detail=client.get(f'/api/projects/{project}/ingest-batches/{bid}/items',
                          params={'include_batch':True,'item_id':first.json()['id']})
        assert detail.status_code==200, detail.text
        assert detail.json()['batch']['id']==bid
        assert detail.json()['batch']['counts']['queued']==1
        assert detail.json()['items']==page['items']
        other=client.post('/api/projects',json={'name':'other','ontology_mode':'documents'}).json()['id']
        assert client.get(f'/api/projects/{other}/ingest-batches/{bid}/items?include_batch=true').status_code==404


def test_batch_item_summary_preserves_legacy_links_without_full_result(tmp_path):
    import json
    from knowledge_service.integrations.embeddings import HashingEncoder

    app = create_app(tmp_path / 'batch-summary', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('批次摘要')['id']
    batch = repo.ingest_batches.create_batch(project, {})
    item = repo.ingest_batches.add_item(project, batch['id'], 'legacy.md', b'x', 'hash', 'legacy', 10, 100)
    saved = {'document': {'id': 'doc-old', 'text': 'x' * 100_000, 'metadata': {'run_id': 'run-old'}},
             'records': [{'text': 'y' * 100_000}]}
    with repo._transaction():
        repo._db.execute("UPDATE ingest_batch_items SET status='succeeded', document_id='', result=?::jsonb WHERE id=?",
                         (json.dumps(saved), item['id']))

    client = TestClient(app)
    url = f'/api/projects/{project}/ingest-batches/{batch["id"]}/items'
    summary = client.get(url, params={'summary_only': True, 'include_batch': True, 'status': 'completed'})
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload['batch']['counts']['completed'] == payload['total'] == 1
    (row,) = payload['items']
    assert (row['id'], row['document_id'], row['run_id'], row['status']) == (
        item['id'], 'doc-old', 'run-old', 'completed')
    assert 'result' not in row and 'options_json' not in row
    assert len(summary.content) < 3_000
    detail = client.get(url, params={'item_id': item['id'], 'limit': 1})
    assert detail.status_code == 200, detail.text
    assert detail.json()['items'][0]['result'] == saved


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
        archive.writestr('a.md','# First\n\n![rule](assets/x.png)')
        archive.writestr('assets/x.png',png())
    raw=buffer.getvalue()
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'zip','ontology_mode':'documents'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents','requested_concurrency':2}}).json()
        base=f'/api/projects/{project}/ingest-batches/{batch["id"]}'
        response=client.post(base+'/files',files={'file':('rules.zip',raw)},headers={'Idempotency-Key':'zip'})
        assert response.status_code==201,response.text
        assert response.json()['total']==1
        assert client.post(base+'/files',files={'file':('rules.zip',raw)},headers={'Idempotency-Key':'zip'}).json()['total']==1
        assert client.get(base).json()['total_bytes']==len(raw)
        added=client.post(base+'/files',files={'file':('b.md',b'# Second\n\nText rules.')})
        assert added.status_code==201,added.text
        assert client.get(base+'/items').json()['total']==2
        assert client.post(base+'/start').status_code==202
        for _ in range(150):
            items=client.get(base+'/items').json()['items']
            if all(item['status'] in ('completed','needs_attention') for item in items):break
            time.sleep(.1)
        assert all(item['status']=='completed' for item in items),items
        assert fake.calls==1
        item=next(item for item in items if item['original_name']=='rules.zip')
        source_id=item['source_version_id']
        document=item['result']['document']
        assert document['metadata']['source_document_version_id']==source_id
        # 内联图片的 OCR 文字会与正文合并在同一检索切片（source_kind=mixed），
        # 但 OCR 证据身份（ocr_block_ids / ocr_run_id）必须保留且可精确定位。
        ocr_chunk=next(row for row in item['result']['records']
                       if row['metadata'].get('ocr_block_ids') or row['metadata'].get('ocr_run_id'))
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
    packages=[]
    for name,title,rank in [('a.md','Title A',1),('b.md','Title B',2)]:
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr(name,'Rule body')
            archive.writestr('manifest.json',json.dumps({'schema_version':2,'document':{
                'entry':name,'title':title,'metadata':{'rank':rank}}}))
        packages.append(buffer.getvalue())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'manifest'}).json()['id']
        batch=client.post(f'/api/projects/{project}/ingest-batches',json={'settings':{'extraction_mode':'documents'}}).json()
        items=[]
        for index,raw in enumerate(packages):
            response=client.post(f'/api/projects/{project}/ingest-batches/{batch["id"]}/files',
                files={'file':(f'archive-{index}.zip',raw)},data={'options':json.dumps({'title':'Caller','metadata':{'rank':0,'shared':True}})})
            assert response.status_code==201,response.text
            items.extend(response.json()['items'])
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
