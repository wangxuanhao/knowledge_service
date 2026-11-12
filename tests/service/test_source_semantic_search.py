from types import SimpleNamespace
import pytest
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.explorer import Explorer
from knowledge_service.domain.search_filter_catalog import FilterCatalog


def setup(tmp_path, monkeypatch):
    repo=Repository(tmp_path/'semantic');p=repo.create_project('semantic')['id']
    rows={}
    for id,status in [('a','completed'),('b','failed'),('c','processing')]:
        rows[id]=repo.put_record(p,{'id':id,'kind':'document','text':'FULL BODY'*1000,
            'metadata':{'title':'Title '+id,'status':status,'region':'east' if id!='c' else 'west'}})
    for id,source,score in [('a1','a',.8),('a2','a',.9),('b1','b',.7),('c1','c',.99),('orphan','missing',1)]:
        row=repo.put_record(p,{'id':id,'kind':'chunk','source_id':source,'text':'<script>'+id*200,
            'metadata':{}})
        rows[id]={**row,'score':score}
    encoder=SimpleNamespace(identity='semantic-test',semantic=True,encode=lambda texts:[[1.,0.] for _ in texts])
    service=KnowledgeService(repo,encoder)
    index={'index_version':7,'catalog':FilterCatalog(1,()).to_dict(),'embedding_model':encoder.identity,'dim':2,'watermark':1}
    monkeypatch.setattr(type(repo.projections),'search_index',lambda *args:index)
    monkeypatch.setattr(type(repo.projections),'search_index_is_current',lambda *args:True)
    calls=[]
    def search(project,query,**kwargs):
        calls.append(kwargs)
        eligible=kwargs.get('eligible_version_ids',[])
        return [{'record_id':r['id'],'version_id':r['version_id'],'score':r['score']}
                for r in sorted([r for r in rows.values() if 'score' in r],key=lambda r:-r['score'])
                if r['version_id'] in eligible][:kwargs['k']]
    service.milvus_store=SimpleNamespace(available=True,search=search)
    return repo,p,rows,Explorer(service),calls


def test_parent_scope_is_applied_before_chunk_ranking_and_summary_is_bounded(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path, monkeypatch)
    scope={'filters':{'field':'metadata.region','op':'eq','value':'east'}}
    result=explorer.sources(p,scope,query='meaning',retrieval_mode='semantic',limit=1)
    assert [d['id'] for d in result['documents']]==['a']
    assert result['documents'][0]['match']['chunk_id']=='a2'
    assert len(result['documents'][0]['match']['text'])<=280
    assert 'text' not in result['documents'][0]
    assert result['counts']=={'all':2,'ready':1,'failed':1,'processing':0}
    assert result['total']==2 and result['matched_chunk_count']==3
    assert result['total_is_exact'] is False and result['result_limit']==100 and result['complete']
    assert all(call['kinds']==['chunk'] and '_source_document_scope' not in call['scope'] for call in calls)
    assert set(calls[0]['eligible_version_ids'])=={rows[x]['version_id'] for x in ('a1','a2','b1')}
    second=explorer.sources(p,scope,query='meaning',retrieval_mode='semantic',limit=1,cursor=result['next_cursor'])
    assert second['documents'][0]['id']=='b'
    failed=explorer.sources(p,scope,query='meaning',retrieval_mode='semantic',state='failed')
    assert failed['total']==1 and failed['counts']['all']==2
    with pytest.raises(ValueError,match='游标'):
        explorer.sources(p,scope,query='changed',retrieval_mode='semantic',cursor=result['next_cursor'])
    rows['a2']['score']=.1
    with pytest.raises(ValueError,match='游标'):
        explorer.sources(p,scope,query='meaning',retrieval_mode='semantic',cursor=result['next_cursor'])


def test_parent_id_gate_qualifies_document_id_instead_of_chunk_id(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path, monkeypatch)
    result=explorer.sources(p,{'filters':{'field':'id','op':'eq','value':'a'}},query='meaning',retrieval_mode='semantic')
    assert [d['id'] for d in result['documents']]==['a']
    assert set(calls[0]['eligible_version_ids'])=={rows[x]['version_id'] for x in ('a1','a2')}


def test_empty_and_point_queries_use_directory_but_nonempty_failure_is_explicit(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path, monkeypatch)
    explorer.service.milvus_store=None
    assert explorer.sources(p,{},query='',retrieval_mode='semantic')['total']==3
    assert explorer.sources(p,{},query='unrelated',document_id='a',retrieval_mode='semantic')['documents'][0]['id']=='a'
    with pytest.raises(RuntimeError,match='Milvus'):
        explorer.sources(p,{},query='meaning',retrieval_mode='semantic')
    with pytest.raises(ValueError,match='历史'):
        explorer.sources(p,{'known_at':rows['a']['recorded_at']},query='meaning',retrieval_mode='semantic')


def test_excluding_documents_returns_empty_without_vector_calls(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    explorer.service.milvus_store=None
    result=explorer.sources(p,{'kinds':['chunk']},query='meaning',retrieval_mode='semantic')
    assert result['documents']==[] and result['counts']['all']==0
    assert result['matched_chunk_count']==0 and result['complete']
    assert not calls


def test_deleted_parent_is_never_ranked_and_stale_projection_is_incomplete(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    repo.put_record(p,{'id':'a','kind':'document','text':'deleted','metadata':{'_deleted':True}},expected_version=1)
    result=explorer.sources(p,{},query='meaning',retrieval_mode='semantic')
    assert 'a' not in [d['id'] for d in result['documents']]
    assert not {rows[x]['version_id'] for x in ('a1','a2')} & set(calls[0]['eligible_version_ids'])
    store=explorer.service.milvus_store
    original=store.search
    store.search=lambda *args,**kwargs:[{**hit,'record_id':'stale'} for hit in original(*args,**kwargs)]
    result=explorer.sources(p,{},query='meaning',retrieval_mode='semantic')
    assert not result['complete'] and not result['documents']


def test_public_scope_rejects_private_parent_gate(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    with TestClient(create_app(tmp_path/'public-scope',encoder=HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'public'}).json()['id']
        url=f'/api/projects/{p}/sources'
        assert client.post(url,json={'_source_document_scope':{}}).status_code==422
        assert client.post(url,json={},params={'retrieval_mode':'hybrid'}).status_code==422
