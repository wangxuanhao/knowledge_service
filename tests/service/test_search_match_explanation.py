from knowledge_service.repository import Repository
from knowledge_service.repository.page_reads import PageReads


def test_keyword_summary_identifies_fields_and_excerpt_without_returning_full_body(tmp_path):
    repo=Repository(tmp_path)
    project=repo.create_project('解释')['id']
    repo.put_record(project,{'id':'doc','kind':'document','text':'背景。'*300+'退款需原路退回。'+'说明。'*300,
        'metadata':{'title':'退款规则','source_file':'service.md'}})
    result=PageReads(repo).sources(project,{},query='退款',summary_only=True)
    doc=result['documents'][0]
    assert 'text' not in doc
    assert doc['match']['fields']==['title','body']
    assert '退款' in doc['match']['text'] and len(doc['match']['text'])<=280
    assert doc['match']['mode']=='keyword'


def test_semantic_explanation_does_not_claim_keyword_hit_or_title_contribution():
    from knowledge_service.domain.search_match import explain_match
    result=explain_match({'metadata':{'title':'售后规定','source_file':'rules.md'}},'如何退款',
        text='款项将于三个工作日内退回原支付账户。',mode='semantic',channels=['semantic'])
    assert result['fields']==[] and result['channels']==['semantic']
    assert result['mode']=='semantic' and result['text']


def test_literal_title_presence_is_not_semantic_field_attribution():
    from knowledge_service.domain.search_match import explain_match
    result=explain_match({'metadata':{'title':'退款规定'}},'退款',text='款项原路退回。',mode='semantic',channels=['semantic'])
    assert result['fields']==['title']
    assert result['channels']==['semantic']


def test_title_only_and_filename_only_do_not_invent_a_body_match(tmp_path):
    repo=Repository(tmp_path);project=repo.create_project('字段')['id']
    for id,metadata in [('a',{'title':'退款规则','source_file':'rules.md'}),('b',{'title':'售后规则','source_file':'退款.md'})]:
        repo.put_record(project,{'id':id,'kind':'document','text':'款项原路退回。','metadata':metadata})
    docs=PageReads(repo).sources(project,{},query='退款',summary_only=True)['documents']
    values={doc['id']:doc['match'] for doc in docs}
    assert values['a']['fields']==['title'] and values['b']['fields']==['filename']
    assert not values['a']['text'] and not values['b']['text']


def test_fusion_preserves_actual_channels_instead_of_labelling_every_hit_both():
    from unittest.mock import Mock
    from knowledge_service.integrations.milvus_store import MilvusStore
    from knowledge_service.domain.search_filter_catalog import default_catalog
    store=MilvusStore(dim=2);store._load_if_needed=Mock();store._client=Mock()
    def search(**options):
        ids=['both','semantic'] if options['anns_field']=='embedding' else ['both','keyword']
        return [[{'distance':.9,'entity':{'record_id':id,'version_id':id+'-v'}} for id in ids]]
    store._client.search.side_effect=search
    hits=store.search('p','退款',retrieval_mode='hybrid',query_vector=[1.,0.],index_version=1,catalog=default_catalog())
    channels={hit['record_id']:hit['match_channels'] for hit in hits}
    assert channels=={'both':['keyword','semantic'],'semantic':['semantic'],'keyword':['keyword']}
