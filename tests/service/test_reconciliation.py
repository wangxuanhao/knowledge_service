from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_incremental_exact_fusion_retains_occurrence_and_source(monkeypatch,tmp_path):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'extract',lambda self,text,ontology:[
        {'id':'x','kind':'entity','type':'Merchant','text':'同一个商户','metadata':{}}])
    with TestClient(create_app(tmp_path/'fusion.sqlite',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'fusion'}).json()['id'];base='/api/projects/'+p
        for index in range(2):
            response=client.post(base+'/documents',json={'title':str(index),'text':'同一个商户的规则','extraction_mode':'ontology'})
            assert response.status_code==201,response.text
        rows=client.post(base+'/records/page',json={}).json()['records']
        entities=[r for r in rows if r['kind']=='entity']
        assert len(entities)==1
        assert len(entities[0]['metadata']['merged_sources'])==1
        assert entities[0]['metadata']['merge_backend']=='semantica.EntityMerger'
        assert len([r for r in rows if r['kind']=='chunk'])==2
        occurrence=entities[0]['metadata']['merged_sources'][0]
        assert occurrence['source_version_id']
        assert len(client.get(base+'/records/'+entities[0]['id']+'/history').json()['versions'])==2
        evidence=client.post(base+'/records/'+entities[0]['id']+'/evidence',json={}).json()
        assert {doc['title'] for doc in evidence['documents']}=={'0','1'}
        assert all(doc['integrity']['complete'] for doc in evidence['documents'])
        accepted=client.get(base+'/assertions',params={
            'status':'accepted','canonical_record_id':entities[0]['id']}).json()['assertions']
        assert len(accepted)==2
        assert len({item['document_id'] for item in accepted})==2
        assert all(item['document_version_id'] and item['chunk_id'] for item in accepted)


def test_same_document_repeated_mentions_preserve_each_chunk_support(monkeypatch,tmp_path):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'extract',lambda self,text,ontology:[
        {'id':'x','kind':'entity','type':'Merchant','text':'同一个商户','metadata':{}}])
    with TestClient(create_app(tmp_path/'repeated-mentions',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'repeated mentions'}).json()['id'];base='/api/projects/'+p
        response=client.post(base+'/documents',json={
            'title':'多切片规则','text':('同一个商户应遵守规则并支持售后服务。'*20),
            'extraction_mode':'ontology','chunk_size':100,'chunk_overlap':0})
        assert response.status_code==201,response.text
        rows=client.post(base+'/records/page',json={}).json()['records']
        entities=[row for row in rows if row['kind']=='entity']
        chunks=[row for row in rows if row['kind']=='chunk']
        assert len(entities)==1 and len(chunks)>1
        accepted=client.get(base+'/assertions',params={
            'status':'accepted','canonical_record_id':entities[0]['id']}).json()['assertions']
        assert {item['chunk_id'] for item in accepted}=={row['id'] for row in chunks}
        assert len(accepted)==len(chunks)
        evidence=client.post(base+'/records/'+entities[0]['id']+'/evidence',json={}).json()
        assert len(evidence['documents'])==1
        assert evidence['documents'][0]['integrity']['complete']
