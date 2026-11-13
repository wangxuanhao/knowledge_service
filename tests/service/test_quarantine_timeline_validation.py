import unittest
from types import SimpleNamespace
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.attribute_conflicts import allow_quarantine_deficits
from knowledge_service.services.service import KnowledgeService


TTL='''@prefix ex: <http://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:Platform a owl:Class . ex:Target a owl:Class .
ex:p a owl:DatatypeProperty . ex:friend a owl:ObjectProperty .
ex:Shape a sh:NodeShape; sh:targetClass ex:Platform;
 sh:property [sh:path ex:p; sh:minCount 1; sh:maxCount 1];
 sh:property [sh:path ex:friend; sh:maxCount 1] .'''


def graphs():
    def entity(id,type):return dict(id=id,kind='entity',type='http://example.test/'+type,text=id)
    nodes=[entity('keep','Platform'),entity('drop','Platform'),entity('one','Target'),entity('two','Target')]
    values=[dict(id='p-'+id,kind='attribute',type='http://example.test/p',subject_id=id,text='p',value=value,
                 datatype='http://www.w3.org/2001/XMLSchema#integer') for id,value in [('keep',1),('drop',2)]]
    edges=[dict(id='r-'+id,kind='relation',type='http://example.test/friend',subject_id=id,object_id=target,text='friend',
                valid_from='2027-01-01') for id,target in [('keep','one'),('drop','two')]]
    return nodes+values+edges,[row for row in nodes if row['id']!='drop']+[{**r,'subject_id':'keep'} for r in edges]


class QuarantineTimelineTests(unittest.TestCase):
    def test_legacy_short_predicate_acknowledges_its_exact_published_path(self):
        before,_=graphs()
        before=[{**row,'ontology_id':'onto','type':row['type'].rsplit('/',1)[-1]}
                for row in before if row['kind']!='relation']
        after=[row for row in before if row['id']!='drop']
        after=[{**row,'subject_id':'keep','metadata':{'_quarantined':True}}
               if row['kind']=='attribute' else row for row in after]
        service=KnowledgeService.__new__(KnowledgeService)
        service.repository=SimpleNamespace(get_ontology=lambda *args:{'turtle':TTL})
        context=dict(entity_id='keep',predicates=['p'],member_ids=['p-keep','p-drop'],preview_fingerprint='preview')
        report=service._timeline_check('project','onto',{row['id']:row for row in after},
            quarantine_conflicts=context,before_graph={row['id']:row for row in before})[-1]
        self.assertTrue(report['conforms'],report)

    def test_acknowledged_early_deficit_does_not_mask_later_unrelated_constraint(self):
        ontology=Ontology(TTL);before,after=graphs()
        baseline=ontology.validate_timeline(before)
        self.assertTrue(baseline['conforms'],baseline)
        context=dict(entity_id='keep',predicates=['http://example.test/p'],member_ids=['p-keep','p-drop'],preview_fingerprint='preview')
        report=ontology.validate_timeline(after,acknowledge_report=lambda r:allow_quarantine_deficits(r,baseline,context))
        self.assertFalse(report['conforms'])
        self.assertTrue(any(v.get('path')=='http://example.test/friend' for v in report['violations']))

    def test_same_local_name_in_another_namespace_grants_no_quarantine_exemption(self):
        before,_=graphs()
        before=[{**row,'ontology_id':'onto'} for row in before if row['kind']!='relation']
        after=[row for row in before if row['id'] not in {'drop','p-keep','p-drop'}]
        service=KnowledgeService.__new__(KnowledgeService)
        service.repository=SimpleNamespace(get_ontology=lambda *args:{'turtle':TTL})
        context=dict(entity_id='keep',predicates=['http://other.test/p'],
                     member_ids=['p-keep','p-drop'],preview_fingerprint='preview')
        report=service._timeline_check('project','onto',{row['id']:row for row in after},
            quarantine_conflicts=context,before_graph={row['id']:row for row in before})[-1]
        self.assertFalse(report['conforms'])
        self.assertEqual(report['violations'][0]['path'],'http://example.test/p')


if __name__=='__main__':unittest.main()
