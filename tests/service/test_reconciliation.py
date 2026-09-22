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
            response=client.post(base+'/documents',json={'title':str(index),'text':'同一个商户的规则','extract':True})
            assert response.status_code==201,response.text
        rows=client.post(base+'/records/query',json={}).json()['records']
        entities=[r for r in rows if r['kind']=='entity']
        assert len(entities)==1
        assert len(entities[0]['metadata']['merged_sources'])==1
        assert entities[0]['metadata']['merge_backend']=='semantica.EntityMerger'
        assert len([r for r in rows if r['kind']=='chunk'])==2
        occurrence=entities[0]['metadata']['merged_sources'][0]
        assert occurrence['source_version_id']
        assert len(client.get(base+'/records/'+entities[0]['id']+'/history').json()['versions'])==2
