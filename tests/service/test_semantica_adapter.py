import importlib.util

import pytest


def adapter():
    assert importlib.util.find_spec('knowledge_service.integrations.semantica_adapter'), 'Semantica adapter is not implemented'
    from knowledge_service.integrations import semantica_adapter
    return semantica_adapter


def test_missing_llm_configuration_is_explicit(monkeypatch):
    for key in ['KG_LLM_API_KEY', 'KG_LLM_BASE_URL', 'KG_LLM_MODEL']:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match='KG_LLM_API_KEY'):
        adapter().SemanticaExtractor().extract('Alice', None)


def test_real_snapshot_preserves_types_parallel_edges_and_end_boundary():
    pytest.importorskip('semantica.context.context_graph')
    records = [
        dict(id='a', kind='entity', type='Person', text='Alice', metadata={'type': 'untrusted'}),
        dict(id='b', kind='entity', type='Company', text='Acme', metadata={}),
        dict(id='old', kind='relation', type='worksAt', text='Alice at Acme', subject_id='a', object_id='b',
             valid_from='2020-01-01T00:00:00+00:00', valid_until='2025-01-01T00:00:00+00:00', metadata={}),
        dict(id='other', kind='relation', type='worksAt', text='parallel evidence', subject_id='a', object_id='b', metadata={}),
        dict(id='dangling', kind='relation', type='worksAt', text='', subject_id='a', object_id='missing', metadata={}),
    ]
    before = adapter().snapshot(records, '2024-01-01T00:00:00+00:00')
    after = adapter().snapshot(records, '2025-01-01T00:00:00+00:00')
    assert before['backend'] == 'semantica'
    assert {n['id']: n['type'] for n in before['nodes']} == {'a': 'Person', 'b': 'Company'}
    assert {e['id'] for e in before['edges']} == {'old', 'other'}
    assert {e['id'] for e in after['edges']} == {'other'}


def test_extraction_uses_ontology_ids_and_linked_stable_ids(monkeypatch):
    mod = adapter()
    from knowledge_service.services.ontology import Ontology
    ont = Ontology('@prefix ex: <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . ex:Person a owl:Class . ex:Company a owl:Class . ex:worksAt a owl:ObjectProperty .')
    for key, val in [('KG_LLM_API_KEY', 'test-key'), ('KG_LLM_BASE_URL', 'http://localhost:1/v1'), ('KG_LLM_MODEL', 'test')]:
        monkeypatch.setenv(key, val)
    methods = pytest.importorskip('semantica.semantic_extract.methods')
    from semantica.semantic_extract.types import Entity, Relation
    a = Entity('Alice', 'Person', 0, 5, .9)
    b = Entity('Acme', 'Company', 15, 19, .8)
    def entities(text, **kw):
        assert set(kw['entity_types']) == {'Person', 'Company'}
        assert kw['silent_fail'] is False
        return [a, b]
    def relations(text, entities, summary, relation_types, config):
        assert relation_types == ['worksAt']
        return [Relation(a, 'worksAt', b, .7)]
    monkeypatch.setattr(methods, 'extract_entities_llm', entities)
    monkeypatch.setattr(mod, '_extract_relations_guided', relations)
    results = mod.SemanticaExtractor().extract('Alice works at Acme', ont)
    assert results == mod.SemanticaExtractor().extract('Alice works at Acme', ont)
    assert [r['type'] for r in results] == ['https://test/Person', 'https://test/Company', 'https://test/worksAt']
    assert results[2]['subject_id'] == results[0]['id']
    assert results[2]['object_id'] == results[1]['id']
    assert results[2]['metadata']['confidence'] == .7
    monkeypatch.setattr(mod, '_extract_relations_guided', lambda *a, **k: [])
    assert len(mod.SemanticaExtractor().extract('Alice works at Acme', ont)) == 2
    def failure(*args, **kwargs):
        raise RuntimeError('provider failed')
    monkeypatch.setattr(methods, 'extract_entities_llm', failure)
    with pytest.raises(RuntimeError, match='抽取失败'):
        mod.SemanticaExtractor().extract('Alice works at Acme', ont)


def test_unknown_relation_queued_and_next_ontology_accepts_it(monkeypatch):
    mod=adapter()
    from knowledge_service.services.ontology import Ontology
    methods=pytest.importorskip('semantica.semantic_extract.methods')
    from semantica.semantic_extract.types import Entity,Relation
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:
        monkeypatch.setenv(key,'test')
    a,b=Entity('甲','Person',0,1,.9),Entity('乙','Person',3,4,.9)
    monkeypatch.setattr(methods,'extract_entities_llm',lambda *args,**kw:[a,b])
    monkeypatch.setattr(mod,'_extract_relations_guided',lambda *args,**kw:[Relation(a,'knows',b,.9),Relation(a,'isSubordinateTo',b,.8)])
    turtle='@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . :Person a owl:Class . :knows a owl:ObjectProperty .'
    extractor=mod.SemanticaExtractor()
    rows=extractor.extract('甲属于乙',Ontology(turtle))
    assert len(rows)==3 and len(extractor.review_candidates)==1
    assert extractor.review_candidates[0]['predicate']=='isSubordinateTo'
    assert extractor.review_candidates[0]['subject_id']==rows[0]['id']
    rows=extractor.extract('甲属于乙',Ontology(turtle+' :isSubordinateTo a owl:ObjectProperty .'))
    assert len(rows)==4 and extractor.review_candidates==[]


def test_open_discovery_uses_domain_specific_entity_types(monkeypatch):
    mod=adapter()
    for key,val in [('KG_LLM_API_KEY','test-key'),('KG_LLM_BASE_URL','http://localhost:1/v1'),('KG_LLM_MODEL','test')]:
        monkeypatch.setenv(key,val)
    methods=pytest.importorskip('semantica.semantic_extract.methods')
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    monkeypatch.setattr(methods,'extract_relations_llm',lambda *a,**k:[])
    captured={}
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            if schema.__name__=='_OpenEntities':
                captured['prompt']=prompt
                return schema(entities=[{'text':'美团直播平台','type':'直播平台','confidence':.95}])
            return schema(facts=[])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    candidates=mod.SemanticaExtractor().discover('美团直播平台开展直播业务')
    assert len(candidates)==1
    assert candidates[0]['kind']=='entity'
    assert candidates[0]['proposed_type']=='直播平台'
    assert candidates[0]['proposed_type']!='CONCEPT'
    assert candidates[0]['metadata']['extraction_method']=='llm_open_typed'
    assert 'DOMAIN-SPECIFIC' in captured['prompt']


def test_open_discovery_matches_relations_by_text_when_types_differ(monkeypatch):
    mod=adapter()
    for key,val in [('KG_LLM_API_KEY','test-key'),('KG_LLM_BASE_URL','http://localhost:1/v1'),('KG_LLM_MODEL','test')]:
        monkeypatch.setenv(key,val)
    methods=pytest.importorskip('semantica.semantic_extract.methods')
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    from semantica.semantic_extract.types import Entity, Relation
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            if schema.__name__=='_OpenEntities':
                return schema(entities=[{'text':'美团直播平台','type':'直播平台','confidence':.95},
                                        {'text':'主播','type':'主播','confidence':.9}])
            return schema(facts=[{'subject':'美团直播平台','predicate':'包含','object':'主播',
                'fact_kind':'relation','confidence':.8,'evidence':'美团直播平台包含主播'}])
    # Relation discovery resolves its endpoint back to the open entity candidates.
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    candidates=mod.SemanticaExtractor().discover('美团直播平台包含主播')
    kinds=[c['kind'] for c in candidates]
    assert kinds.count('relation')==1
    assert next(c for c in candidates if c['kind']=='relation')['proposed_type']=='包含'


def test_open_discovery_routes_entity_objects_and_scalar_values_once(monkeypatch):
    mod=adapter()
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:
        monkeypatch.setenv(key,'test')
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    calls=[]
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            calls.append(schema.__name__)
            if schema.__name__=='_OpenEntities':
                return schema(entities=[{'text':'账号甲','type':'账号','confidence':.95},
                    {'text':'抖音','type':'直播平台','confidence':.94}])
            return schema(facts=[
                {'subject':'账号甲','predicate':'使用平台','object':'抖音','fact_kind':'attribute',
                 'literal_type':'text','confidence':.9,'evidence':'账号甲使用抖音'},
                {'subject':'账号甲','predicate':'封禁期限','object':'7天','fact_kind':'attribute',
                 'literal_type':'duration','confidence':.88,'evidence':'封禁期限为7天'},
            ])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    result=mod.SemanticaExtractor().discover('账号甲使用抖音，封禁期限为7天',include_attributes=True)
    assert calls==['_OpenEntities','_OpenFacts']
    assert [item['kind'] for item in result].count('relation')==1
    assert [item['kind'] for item in result].count('attribute')==1
    relation=next(item for item in result if item['kind']=='relation')
    attribute=next(item for item in result if item['kind']=='attribute')
    assert relation['object']=='抖音'
    assert relation['metadata']['classification_reason']=='object_matches_entity'
    assert attribute['value']=='7天' and attribute['value_type']=='duration'
    assert all(item.get('evidence_status')=='exact' for item in (relation,attribute))


def test_open_discovery_isolates_missing_source_and_unresolved_facts(monkeypatch):
    mod=adapter()
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:
        monkeypatch.setenv(key,'test')
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            if schema.__name__=='_OpenEntities':
                return schema(entities=[{'text':'账号甲','type':'账号'},
                    {'text':'正文中不存在的人','type':'负责人'}])
            return schema(facts=[
                {'subject':'账号甲','predicate':'状态','object':'封禁','fact_kind':'attribute',
                 'literal_type':'status','evidence':'伪造的证据'},
                {'subject':'账号甲','predicate':'负责人','object':'张三','fact_kind':'relation',
                 'evidence':'账号甲当前封禁'},
            ])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    result=mod.SemanticaExtractor().discover('账号甲当前封禁',include_attributes=True)
    assert [item['kind'] for item in result].count('entity')==1
    assert not any(item['kind'] in {'relation','attribute'} for item in result)
    exceptions=[item for item in result if item['kind']=='exception']
    assert {item['reason_code'] for item in exceptions}=={
        'entity_not_in_source','evidence_not_in_source','relation_object_not_resolved'}


def test_ontology_induction_exposes_stable_attribute_fields_at_entity_top_level(monkeypatch):
    pytest.importorskip('semantica.ontology')
    from semantica.ontology import OntologyGenerator
    from knowledge_service.services.ontology_discovery import _induce

    captured={}
    original=OntologyGenerator.generate_ontology
    def capture(self,data,*args,**kwargs):
        captured['data']=data
        return original(self,data,*args,**kwargs)
    monkeypatch.setattr(OntologyGenerator,'generate_ontology',capture)

    _induce('project','属性本体',[
        {'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'商户'},
        {'id':'count','kind':'attribute','entity_id':'merchant','proposed_type':'员工数量','value':20},
        {'id':'count-again','kind':'attribute','entity_id':'merchant','proposed_type':'员工数量','value':21},
    ])

    sample=captured['data']['entities'][0]
    property_name=next(key for key in sample if key.startswith('AttributeType_'))
    assert sample[property_name]==20
    assert sample['properties'][property_name]==20


def test_guided_entities_receive_definitions_without_source_contamination(monkeypatch):
    mod=adapter()
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    captured={}
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            captured['prompt']=prompt
            return schema(entities=[{'text':'测试商户','type':'Merchant','confidence':.96,
                                     'evidence':'测试商户'}])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    summary={'classes':[{'id':'urn:test:Merchant','name':'Merchant','label':'Merchant',
                         'label_zh':'商户','label_en':'Merchant','description':'提供商品或服务的经营主体',
                         'parents':[]} ]}
    entities=mod._extract_entities_guided('平台审核测试商户',summary,['Merchant'],{
        'provider':'openai','llm_model':'test','api_key':'test','base_url':'http://test'})
    assert [(x.text,x.label) for x in entities]==[('测试商户','Merchant')]
    assert 'ONTOLOGY_ENTITY_GUIDANCE' in captured['prompt']
    assert '提供商品或服务的经营主体' in captured['prompt']
    assert entities[0].metadata['extraction_method']=='llm_typed_guided'


def test_guided_entities_keep_novel_type_for_human_review(monkeypatch):
    mod=adapter()
    providers=pytest.importorskip('semantica.semantic_extract.providers')
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            return schema(entities=[{'text':'直播封面','type':'NEW:直播内容要素',
                                     'confidence':.88,'evidence':'直播封面'}])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    result=mod._extract_entities_guided('直播封面不得包含二维码',{
        'classes':[{'id':'urn:test:Merchant','name':'Merchant','label_zh':'商户',
                    'description':'经营主体','parents':[]}]},['Merchant'],{
        'provider':'openai','llm_model':'test','api_key':'test','base_url':'http://test'})
    assert len(result)==1 and result[0].label=='直播内容要素'


def test_guided_relations_map_temporal_fields_into_metadata(monkeypatch):
    mod=adapter()
    from knowledge_service.services.ontology import Ontology
    from semantica.semantic_extract import providers
    from semantica.semantic_extract.types import Entity
    captured={}
    class FakeProvider:
        def generate_typed(self,prompt,schema,**kw):
            captured['prompt']=prompt
            return schema(relations=[{'subject':'张三','predicate':'worksAt','object':'甲公司',
                'confidence':.9,'evidence':'张三任职于甲公司',
                'valid_from':'2020-01-01','valid_until':'2022-01-01','temporal_confidence':.9,
                'temporal_source_text':'自2020年起'}])
    monkeypatch.setattr(providers,'create_provider',lambda *a,**k:FakeProvider())
    ttl='@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . :Person a owl:Class . :Company a owl:Class . :worksAt a owl:ObjectProperty .'
    entities=[Entity('张三','Person',0,2),Entity('甲公司','Company',5,8)]
    relations=mod._extract_relations_guided('张三任职于甲公司',entities,Ontology(ttl).summary(),['worksAt'],{
        'provider':'openai','llm_model':'test','api_key':'test','base_url':'http://test'})
    meta=relations[0].metadata
    assert meta['valid_from']=='2020-01-01'
    assert meta['valid_until']=='2022-01-01'
    assert meta['temporal_confidence']==.9
    assert meta['temporal_source_text']=='自2020年起'
    assert meta['extraction_method']=='llm_typed_guided'
    assert 'temporal_source_text' in captured['prompt']
    assert 'valid_from' in captured['prompt']


def test_extracted_validity_promotes_iso_and_guards_phrases_and_intervals():
    mod=adapter()
    iso=mod._extracted_validity({'valid_from':'2020-01-01','valid_until':'2022-01-01','temporal_confidence':.9})
    assert iso==('2020-01-01T00:00:00.000000Z','2022-01-01T00:00:00.000000Z')
    phrase={'valid_from':'自2020年起','valid_until':'2022-01-01','temporal_confidence':.9}
    vf,vu=mod._extracted_validity(phrase)
    assert vf is None and vu=='2022-01-01T00:00:00.000000Z'
    assert phrase['valid_from_text']=='自2020年起'
    inverted={'valid_from':'2022-01-01','valid_until':'2020-01-01','temporal_confidence':.9}
    assert mod._extracted_validity(inverted)==(None,None)
    low={'valid_from':'2020-01-01','valid_until':'2022-01-01','temporal_confidence':.4}
    assert mod._extracted_validity(low)==(None,None)
    assert mod._extracted_validity({})==(None,None)


def test_extract_relation_record_carries_extracted_validity(monkeypatch):
    mod=adapter()
    from knowledge_service.services.ontology import Ontology
    methods=pytest.importorskip('semantica.semantic_extract.methods')
    from semantica.semantic_extract.types import Entity,Relation
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:
        monkeypatch.setenv(key,'test')
    a,b=Entity('张三','Person',0,2,.9),Entity('甲公司','Company',5,8,.9)
    monkeypatch.setattr(methods,'extract_entities_llm',lambda *args,**kw:[a,b])
    def relations(text,entities,summary,relation_types,config):
        return [Relation(a,'worksAt',b,.9,metadata={
            'valid_from':'2020-01-01','valid_until':'2022-01-01','temporal_confidence':.9,
            'temporal_source_text':'自2020年起'})]
    monkeypatch.setattr(mod,'_extract_relations_guided',relations)
    ttl='@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . :Person a owl:Class . :Company a owl:Class . :worksAt a owl:ObjectProperty .'
    rows=mod.SemanticaExtractor().extract('张三任职于甲公司',Ontology(ttl))
    relation=next(r for r in rows if r['kind']=='relation')
    assert relation['valid_from']=='2020-01-01T00:00:00.000000Z'
    assert relation['valid_until']=='2022-01-01T00:00:00.000000Z'
    assert relation['metadata']['temporal_source_text']=='自2020年起'
    assert relation['metadata']['confidence']==.9
