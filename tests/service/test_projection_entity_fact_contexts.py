from knowledge_service.repository.projection_inputs import ProjectionInputs
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval_hits import qualified_hits
from test_identity_source_contexts import system,entity


def test_entity_native_context_includes_qualified_incident_fact_and_withdrawal(tmp_path,monkeypatch):
    repo,service,p=system(tmp_path)
    monkeypatch.setattr(repo,'source_identity_projection_readiness',lambda p:{'ready':True})
    service.write(p,[entity('platform',start='2020-01-01',end='2021-01-01'),
                     entity('other',kind='Event',start='2025-01-01',end='2026-01-01')])
    service.write(p,[dict(id='edge',kind='relation',type='knows',text='关联',subject_id='platform',object_id='other',
                         valid_from='2025-01-01',valid_until='2026-01-01')])
    scope={'valid_at':'2025-06-01'}
    records=ScopedReads(repo).by_ids(p,scope,['platform'])
    assert records
    inputs=ProjectionInputs(repo).collect(p,['platform'])
    assert any(context.get('support_fact_id')=='edge' for context in inputs[0].source_contexts)
    from knowledge_service.domain.search_projection import build_projections
    from knowledge_service.domain.search_filter_catalog import default_catalog
    vectors=list(build_projections(inputs[0],default_catalog(),index_version=1))
    matched=[dict(row,score=1) for row in vectors if row['valid_from_us']>1700000000000000]
    assert qualified_hits(repo,p,scope,records,matched)
    support=repo.list_assertions(p,canonical_record_id='edge',status='accepted')[0]
    repo.transition_assertion(p,support['id'],support['decision_version'],'superseded','撤回来源','test')
    assert not ScopedReads(repo).by_ids(p,scope,['platform'])
    assert not any(context.get('support_fact_id')=='edge' for context in ProjectionInputs(repo).collect(p,['platform'])[0].source_contexts)


def test_historical_attribute_keyword_reads_value_only_tail(tmp_path):
    repo,service,p=system(tmp_path)
    repo.put_record(p,entity('platform'))
    value='正文。'*4000+'只在尾部出现的退款条件'
    fact=repo.put_record(p,dict(id='attr',kind='attribute',type='description',text='标签不包含关键词',
        subject_id='platform',value=value,datatype='http://www.w3.org/2001/XMLSchema#string'))
    result=ScopedReads(repo).ranked_keyword(p,{'known_at':fact['recorded_at']},'退款条件',kinds=['attribute'],limit=5)
    assert result and result[0]['id']=='attr'
    assert result[0]['matched_value'].endswith('退款条件')
    assert len(result[0]['value'])<=6000 and result[0]['value_truncated']


def test_historical_attribute_matches_owner_and_declared_property_label(tmp_path):
    from ontology_seed import seed_ontology
    from knowledge_service.services.ontology import Ontology
    from test_identity_source_contexts import TTL
    repo,service,p=system(tmp_path)
    turtle=TTL+'''\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:opaque123 a owl:DatatypeProperty; rdfs:label "业务说明"@zh .'''
    ontology=seed_ontology(repo,p,turtle,Ontology(turtle).summary())
    repo.put_record(p,dict(entity('platform'),text='主人品牌',ontology_id=ontology['id']))
    fact=repo.put_record(p,dict(id='a',kind='attribute',type='http://ex/opaque123',subject_id='platform',
        ontology_id=ontology['id'],text='irrelevant',value='不含名称的具体段落',datatype='http://www.w3.org/2001/XMLSchema#string'))
    reads=ScopedReads(repo)
    for query in ('主人品牌','业务说明'):
        result=reads.ranked_keyword(p,{'known_at':fact['recorded_at']},query,kinds=['attribute'],limit=5)
        assert result and result[0]['id']=='a'


def test_old_attribute_value_support_cannot_authorize_changed_literal(tmp_path):
    from knowledge_service.services.governance import writable
    repo,service,p=system(tmp_path)
    service.write(p,[entity('platform')])
    fact=service.write(p,[dict(id='a',kind='attribute',subject_id='platform',type='description',
        text='原始段落',value='原始段落',datatype='http://www.w3.org/2001/XMLSchema#string')])[0]
    assert repo.current_source_contexts(p,['a'])['a']
    changed=writable(fact);changed.update(value='另一个具体值',text='另一个具体值')
    repo.put_record(p,changed,expected_version=fact['version'])
    assert not ScopedReads(repo).by_ids(p,{},['a'],kinds=['attribute'])
    assert not repo.current_source_contexts(p,['a'])['a']
    assert ScopedReads(repo).by_ids(p,{'known_at':fact['recorded_at']},['a'],kinds=['attribute'])[0]['value']=='原始段落'


def test_entity_projection_uses_only_incident_support_matching_current_literal(tmp_path):
    from knowledge_service.services.governance import writable
    repo,service,p=system(tmp_path)
    service.write(p,[entity('platform')])
    fact=service.write(p,[dict(id='a',kind='attribute',subject_id='platform',type='description',
        text='原始段落',value='原始段落',datatype='http://www.w3.org/2001/XMLSchema#string')])[0]
    old_support=repo.list_assertions(p,canonical_record_id='a',status='accepted')[0]['id']
    changed=writable(fact);changed.update(value='新段落',text='新段落')
    service.write(p,[changed],revision=True,expected_version=fact['version'])
    assert ScopedReads(repo).by_ids(p,{},['a'],kinds=['attribute'])
    contexts=ProjectionInputs(repo).collect(p,['platform'])[0].source_contexts
    incident=[context for context in contexts if context.get('support_fact_id')=='a']
    assert incident
    assert old_support not in {context['assertion_id'] for context in incident}
