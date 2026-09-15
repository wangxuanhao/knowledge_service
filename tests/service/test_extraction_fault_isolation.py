from knowledge_service.ontology import Ontology
from knowledge_service.semantica_adapter import SemanticaExtractor

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Person a owl:Class . :knows a owl:ObjectProperty .'''


def test_relation_with_missing_extracted_endpoint_is_skipped_not_fatal(monkeypatch):
    import knowledge_service.semantica_adapter as adapter
    from semantica.semantic_extract import methods
    from semantica.semantic_extract.types import Entity, Relation

    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:
        monkeypatch.setenv(key,'test')
    known=Entity('甲','Person',0,1)
    missing=Entity('乙','Person',2,3)
    monkeypatch.setattr(methods,'extract_entities_llm',lambda *args,**kwargs:[known])
    monkeypatch.setattr(adapter,'_extract_relations_guided',lambda *args,**kwargs:[Relation(known,'knows',missing)])

    extractor=SemanticaExtractor()
    rows=extractor.extract('甲认识乙',Ontology(TTL))

    assert [row['kind'] for row in rows]==['entity']
    assert extractor.extraction_diagnostics['skipped_missing_relation_endpoint']==1


def test_semantica_merge_advice_failure_does_not_abort_deterministic_alias_merge(monkeypatch):
    from semantica.deduplication import EntityMerger
    from knowledge_service.reconciliation import reconcile

    class Repository:
        def current_records(self,project_id):
            return [dict(id='old',kind='entity',type='https://test/Person',ontology_id='v1',
                text='甲',metadata={},properties={},version=1,version_id='old-v1',
                valid_from=None,valid_until=None)]
    class Service:
        repository=Repository()
    monkeypatch.setattr(EntityMerger,'merge_entity_group',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('bad advice')))
    derived=[dict(id='new',kind='entity',type='https://test/Person',ontology_id='v1',
        text='甲',metadata={},properties={},valid_from=None,valid_until=None)]
    rows,expected=reconcile(Service(),'project',derived,{'resolve_entities':True},
        {'id':'doc','version_id':'doc-v1'})
    assert expected=={'old':1}
    assert next(row for row in rows if row['id']=='new')['metadata']['merged_into']=='old'


def test_stable_type_iri_allows_entity_fusion_across_ontology_versions():
    from knowledge_service.reconciliation import reconcile

    class Repository:
        def current_records(self,project_id):
            return [dict(id='old',kind='entity',type='urn:stable:Merchant',ontology_id='v1',
                text='同一商户',metadata={},properties={},version=1,version_id='old-v1',
                valid_from=None,valid_until=None)]
    class Service:
        repository=Repository()

    derived=[dict(id='new',kind='entity',type='urn:stable:Merchant',ontology_id='v2',
        text='同一商户',metadata={},properties={},valid_from=None,valid_until=None)]
    rows,expected=reconcile(Service(),'project',derived,{'resolve_entities':True},
        {'id':'doc','version_id':'doc-v1'})

    canonical=next(row for row in rows if row['id']=='old')
    duplicate=next(row for row in rows if row['id']=='new')
    assert expected=={'old':1}
    assert canonical['ontology_id']=='v2'
    assert canonical['metadata']['ontology_versions']==['v1','v2']
    assert duplicate['metadata']['merged_into']=='old'


def test_same_relation_key_fuses_across_ontology_versions():
    from knowledge_service.reconciliation import reconcile

    class Repository:
        def current_records(self,project_id):
            return [dict(id='old-edge',kind='relation',type='urn:stable:uses',
                subject_id='merchant',object_id='platform',ontology_id='v1',text='使用',
                metadata={},version=1,version_id='edge-v1',valid_from=None,valid_until=None)]
    class Service:
        repository=Repository()

    derived=[dict(id='new-edge',kind='relation',type='urn:stable:uses',
        subject_id='merchant',object_id='platform',ontology_id='v2',text='使用',
        metadata={},valid_from=None,valid_until=None)]
    rows,expected=reconcile(Service(),'project',derived,{'resolve_entities':True},
        {'id':'doc','version_id':'doc-v1'})

    canonical=next(row for row in rows if row['id']=='old-edge')
    duplicate=next(row for row in rows if row['id']=='new-edge')
    assert expected=={'old-edge':1}
    assert canonical['ontology_id']=='v2'
    assert canonical['metadata']['ontology_versions']==['v1','v2']
    assert duplicate['metadata']['merged_into']=='old-edge'
