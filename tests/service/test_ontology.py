import pytest
from pathlib import Path

from knowledge_service.ontology import Ontology

TTL = '''@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Actor a owl:Class . ex:Merchant a owl:Class; rdfs:subClassOf ex:Actor .
ex:Rule a owl:Class .
ex:appliesTo a owl:ObjectProperty; rdfs:domain ex:Rule; rdfs:range ex:Actor .
'''


def test_ontology_accepts_subclass_and_rejects_wrong_relation():
    ontology = Ontology(TTL)
    records = [dict(id='r', kind='entity', type='Rule', text='规则'),
               dict(id='m', kind='entity', type='Merchant', text='商户'),
               dict(id='a', kind='relation', type='appliesTo', subject_id='r', object_id='m')]
    assert ontology.validate(records)['conforms']
    records[-1]['subject_id'] = 'm'
    assert not ontology.validate(records)['conforms']


def test_local_sparql_and_no_remote_service():
    ontology = Ontology(TTL)
    result = ontology.query([], 'SELECT ?c WHERE { ?c a <http://www.w3.org/2002/07/owl#Class> }')
    assert len(result['rows']) == 3
    with pytest.raises(ValueError):
        ontology.query([], 'SELECT * WHERE { SERVICE <https://example.org> { ?s ?p ?o } }')
    with pytest.raises(ValueError):
        ontology.query([], 'DELETE WHERE { ?s ?p ?o }')


def test_invalid_turtle_and_undeclared_types():
    with pytest.raises(ValueError):
        Ontology('this is not turtle')
    assert not Ontology(TTL).validate([dict(id='x', kind='entity', type='Alien')])['conforms']


def test_shacl_constraints_enforced():
    ttl = TTL + '''@prefix sh: <http://www.w3.org/ns/shacl#> .
    ex:NameShape a sh:NodeShape; sh:targetClass ex:Merchant;
    sh:property [sh:path ex:name; sh:minCount 1] .'''
    ontology = Ontology(ttl)
    assert not ontology.validate([dict(id='m', kind='entity', type='Merchant')])['conforms']
    assert ontology.validate([dict(id='m', kind='entity', type='Merchant', properties={'https://example.org/name':'店铺'})])['conforms']


def test_shacl_violation_exposes_record_constraint_path_and_message():
    ttl = TTL + '''@prefix sh: <http://www.w3.org/ns/shacl#> .
    ex:NameShape a sh:NodeShape; sh:targetClass ex:Merchant;
    sh:property [sh:path ex:name; sh:minCount 1] .'''
    result=Ontology(ttl).validate([dict(id='merchant-1',kind='entity',type='Merchant',text='店铺')])
    assert result['violations']==[{
        'record_id':'merchant-1','constraint':'MinCountConstraintComponent',
        'path':'https://example.org/name','path_label':'name','severity':'Violation',
        'message':'Less than 1 values on <urn:knowledge:merchant-1>->ex:name',
        'value':None,
    }]


def test_shacl_cardinality_respects_nonoverlapping_business_versions():
    ontology = Ontology(TTL + '''@prefix sh: <http://www.w3.org/ns/shacl#> .
    ex:Shape a sh:NodeShape; sh:targetClass ex:Rule;
    sh:property [sh:path ex:appliesTo; sh:maxCount 1] .''')
    records = [dict(id='rule',kind='entity',type='Rule'),
               dict(id='a',kind='entity',type='Merchant'),dict(id='b',kind='entity',type='Merchant'),
               dict(id='r1',kind='relation',type='appliesTo',subject_id='rule',object_id='a',valid_from='2020-01-01',valid_until='2022-01-01'),
               dict(id='r2',kind='relation',type='appliesTo',subject_id='rule',object_id='b',valid_from='2022-01-01',valid_until='2024-01-01')]
    assert ontology.validate_timeline(records)['conforms']
    records[-1]['valid_from'] = '2021-01-01'
    assert not ontology.validate_timeline(records)['conforms']


def test_default_extraction_ontology_does_not_require_relations_from_every_mention():
    turtle=(Path(__file__).resolve().parents[2]/'knowledge_service'/'resources'/'default_ontology.ttl').read_text(encoding='utf-8')
    ontology=Ontology(turtle)
    records=[
        dict(id='document-mention',kind='entity',type='http://meituan.com/kg#RuleDocument',text='规则文档'),
        dict(id='violation-mention',kind='entity',type='http://meituan.com/kg#Violation',text='违规行为'),
    ]
    assert ontology.validate(records)['conforms']


def test_summary_exposes_label_zh_and_keeps_existing_keys():
    ttl = TTL + '''ex:Merchant rdfs:label "Merchant" ; rdfs:label "商家"@zh ; rdfs:comment "商家/商户（含主播、店铺经营者）" .
    ex:Rule rdfs:comment "平台规则" .'''
    summary = Ontology(ttl).summary()
    merchant = next(c for c in summary['classes'] if c['name'] == 'Merchant')
    assert merchant['id'] == 'https://example.org/Merchant'
    assert merchant['name'] == 'Merchant'
    assert merchant['label'] == 'Merchant'
    assert merchant['label_zh'] == '商家'
    assert merchant['label_en'] == ''
    assert merchant['description'] == '商家/商户（含主播、店铺经营者）'
    # No label at all: label falls back to the local name, Chinese lives in description.
    rule = next(c for c in summary['classes'] if c['name'] == 'Rule')
    assert rule['label'] == 'Rule' and rule['label_zh'] == '' and rule['description'] == '平台规则'


def test_summary_zh_only_label_falls_back_to_local_name():
    ttl = TTL + '''ex:Merchant rdfs:label "商家"@zh .'''
    merchant = next(c for c in Ontology(ttl).summary()['classes'] if c['name'] == 'Merchant')
    assert merchant['label'] == 'Merchant'
    assert merchant['label_zh'] == '商家'
