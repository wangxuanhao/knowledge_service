from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


TTL='''@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:RuleDocument a owl:Class .
ex:title a owl:DatatypeProperty .
ex:RuleShape a sh:NodeShape; sh:targetClass ex:RuleDocument;
  sh:property [sh:path ex:title; sh:minCount 1] .
'''


def test_ingest_commits_knowledge_and_creates_review_for_shacl_exception(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'extract',lambda self,text,ontology:[
        dict(id='rule',kind='entity',type='https://example.org/RuleDocument',text='规则')])
    app=create_app(tmp_path/'shacl-review.sqlite',HashingEncoder())
    with TestClient(app) as client:
        service=app.state.service
        project=client.post('/api/projects',json={'name':'shacl-review','use_default_ontology':False}).json()['id']
        service.repository.save_ontology(project,TTL,Ontology(TTL).summary())
        base=f'/api/projects/{project}'
        response=client.post(base+'/documents',json={
            'title':'原文','text':'规则正文','resolve_entities':False,'relation_constraint_mode':'review'})
        assert response.status_code==201,response.text
        records=service.repository.current_records(project)
        assert any(row['kind']=='entity' and row['text']=='规则' for row in records)
        review=client.get(base+'/reviews').json()['reviews'][0]
        assert review['kind']=='validation'
        assert review['constraint']=='MinCountConstraintComponent'
        assert review['path']=='https://example.org/title'
        assert review['path_label']=='title'
        assert review['status']=='pending'
        decision=client.post(f"{base}/reviews/{review['document_id']}/{review['id']}",json={
            'action':'approve','note':'确认该片段允许缺少标题属性','target_type':'',
            'expected_version':review['document_version'],'expected_ontology_id':review['ontology_id']})
        assert decision.status_code==200,decision.text
        assert decision.json()['status']=='approved'
        assert decision.json()['resolution']=='accepted_exception'
        assert any(row['kind']=='entity' and row['text']=='规则' for row in service.repository.current_records(project))
