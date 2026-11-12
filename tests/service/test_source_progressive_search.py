import pytest
from test_source_semantic_search import setup


def step(query, mode='keyword'):
    return {'query':query,'retrieval_mode':mode}


def test_keyword_prefix_intersects_beyond_first_page(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    for i in range(205):
        repo.put_record(p,{'id':f'bulk{i:03}','kind':'document','text':'alpha beta' if i%2 else 'alpha','metadata':{}})
    first=explorer.sources(p,{},query='beta',search_steps=[step('alpha')],limit=2)
    assert first['total']==102
    ids=[];result=first
    while True:
        ids.extend(d['id'] for d in result['documents'])
        if not result['next_cursor']:break
        result=explorer.sources(p,{},query='beta',search_steps=[step('alpha')],limit=2,cursor=result['next_cursor'])
    assert len(set(ids))==102 and not first['bounded_by_semantic'] and first['complete']
    with pytest.raises(ValueError,match='游标'):
        explorer.sources(p,{},query='beta',search_steps=[step('beta')],cursor=first['next_cursor'])


def test_semantic_prefix_searches_all_chunks_and_final_state(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    result=explorer.sources(p,{},query='last',retrieval_mode='semantic',state='failed',
        search_steps=[step('Title b'),step('meaning','semantic'),step('FULL')])
    assert [d['id'] for d in result['documents']]==['b']
    assert result['bounded_by_semantic'] and result['complete']
    assert set(calls[0]['eligible_version_ids'])=={rows['b1']['version_id']}
    assert set(calls[1]['eligible_version_ids'])=={rows['b1']['version_id']}
    previous_calls=len(calls)
    result=explorer.sources(p,{},query='FULL',search_steps=[step('nothing'),step('meaning','semantic')])
    assert result['total']==0 and len(calls)==previous_calls


def test_semantic_prefix_cursor_binds_recall_scores_and_propagates_failure(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    steps=[step('meaning','semantic')]
    result=explorer.sources(p,{},query='FULL',search_steps=steps,limit=1)
    rows['a1']['score']=.1
    with pytest.raises(ValueError,match='游标'):
        explorer.sources(p,{},query='FULL',search_steps=steps,limit=1,cursor=result['next_cursor'])
    explorer.service.milvus_store=None
    with pytest.raises(RuntimeError,match='Milvus'):
        explorer.sources(p,{},query='FULL',search_steps=steps)


def test_search_step_api_validation(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    with TestClient(create_app(tmp_path/'progressive-api',encoder=HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'progressive'}).json()['id']
        url=f'/api/projects/{p}/sources'
        assert client.post(url,json={'search_steps':[step('x')]},params={'query':'y'}).status_code==200
        assert client.post(url,json={'search_steps':[step('x')]*7},params={'query':'y'}).status_code==200
        assert client.post(url,json={'search_steps':[step('x')]}).status_code==422
        for steps in ([step('')],[step(' ')],[step('x','hybrid')],[step('x'*2001)],[step('x')]*8):
            assert client.post(url,json={'search_steps':steps}).status_code==422
        for key in ('_source_keyword_queries','_source_document_ids','_source_progressive_identity'):
            assert client.post(url,json={key:[]}).status_code==422


def test_semantic_prefix_reopens_sibling_chunks_in_same_snapshot(tmp_path, monkeypatch):
    from knowledge_service.services.source_semantic_search import SourceSemanticSearch
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    observed=[]
    original=SourceSemanticSearch.search
    def inspect(self,*args,**kwargs):
        observed.append(self.repo)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(SourceSemanticSearch,'search',inspect)
    store=explorer.service.milvus_store
    native=store.search
    def first_then_sibling(project,query,**kwargs):
        hits=native(project,query,**kwargs)
        target='a1' if query=='first' else 'a2'
        return [hit for hit in hits if hit['record_id']==target]
    store.search=first_then_sibling
    result=explorer.sources(p,{'filters':{'field':'metadata.region','op':'eq','value':'east'}},
        query='last',retrieval_mode='semantic',search_steps=[step('first','semantic'),step('FULL')])
    assert [d['id'] for d in result['documents']]==['a']
    assert result['documents'][0]['match']['chunk_id']=='a2'
    assert set(calls[-1]['eligible_version_ids'])=={rows[x]['version_id'] for x in ('a1','a2')}
    assert len(observed)==2 and observed[0] is observed[1] and observed[0]._is_read_view


def test_prefix_incompleteness_and_point_lookup_ignores_chain(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    store=explorer.service.milvus_store
    original=store.search
    store.search=lambda *args,**kwargs:[{**hit,'record_id':'stale'} for hit in original(*args,**kwargs)]
    result=explorer.sources(p,{},query='FULL',search_steps=[step('first','semantic')])
    assert not result['complete'] and result['total']==0 and result['bounded_by_semantic']
    explorer.service.milvus_store=None
    point=explorer.sources(p,{},document_id='a',query='anything',search_steps=[step('semantic','semantic')])
    assert point['documents'][0]['id']=='a'


def test_parent_prefix_gates_do_not_filter_derived_records(tmp_path, monkeypatch):
    repo,p,rows,explorer,calls=setup(tmp_path,monkeypatch)
    result=explorer.sources(p,{},query='FULL',search_steps=[step('Title a'),step('first','semantic')])
    assert [d['id'] for d in result['documents']]==['a']
    assert result['documents'][0]['derived_breakdown']['chunk']==2
