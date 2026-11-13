"""Reject-only conflict decisions still validate authoritative active identities."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from knowledge_service.services.service import KnowledgeService, OntologyValidationError


TTL = '''@prefix : <https://rejection.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
:Person a owl:Class . :count a owl:DatatypeProperty ;
rdfs:domain :Person ; rdfs:range xsd:integer .
:Shape a sh:NodeShape ; sh:targetClass :Person ;
sh:property [ sh:path :count ; sh:minCount 1 ; sh:maxCount 1 ] .'''


def rejection_case(*, withdrawn=False, forged_unsupported=False, legacy=False):
    owner = dict(id='owner', kind='entity', text='甲', type='https://rejection.test/Person',
                 ontology_id='onto', metadata={'_unsupported': True} if forged_unsupported else {},
                 version=2, version_id='owner-v2')
    attributes = [dict(id=id, kind='attribute', text=str(value), value=value,
        type='https://rejection.test/count', datatype='http://www.w3.org/2001/XMLSchema#integer',
        subject_id='owner', ontology_id='onto', metadata={'_quarantined': True, 'conflict_set_ids': ['set']},
        version=2, version_id=id+'-v2') for id,value in [('left',1),('right',2)]]
    document = dict(id='doc', kind='document', text='来源', metadata={'_deleted': True} if withdrawn else {},
                    version=2, version_id='doc-v2')
    assertions = [] if legacy else [dict(id='identity', kind='entity', canonical_record_id='owner',
        status='superseded' if withdrawn else 'accepted', document_id='doc', decision_version=3 if withdrawn else 2)]
    events = [] if legacy else [dict(assertion_id='identity', canonical_record_id='owner', to_status='accepted')]
    snapshot = dict(records=[owner, *attributes, document], assertions=assertions,
                    assertion_events=events, record_version_assertions=[])
    repository = SimpleNamespace(backend_label='detached', get_project=lambda project: None,
        formal_write_snapshot=lambda project: copy.deepcopy(snapshot),
        get_ontology=lambda *args: {'id': 'onto', 'turtle': TTL})
    service = KnowledgeService.__new__(KnowledgeService)
    service.lock = threading.RLock()
    service.repository = repository
    updates = [{key: value for key,value in row.items() if key not in {'version','version_id'}}
               for row in attributes]
    for row in updates:
        row['metadata'] = {'_deleted': True}
    action = dict(kind='conflict_set_resolve', id='set', decisions=[
        dict(attribute_id=row['id'], decision='reject') for row in attributes])
    return service, updates, action


def reject_all(service, updates, action):
    return service.write('project', updates, expected_versions={'left': 2, 'right': 2},
        operation='approve_review', suppress_auto_assertions=True,
        conflict_actions=[action], defer_milvus_sync=True)


class ConflictRejectionValidationTests(unittest.TestCase):
    def test_reject_all_active_owner_revalidates_required_property(self):
        service,updates,action = rejection_case()
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            with self.assertRaises(OntologyValidationError) as caught:
                reject_all(service,updates,action)
        persist.assert_not_called()
        self.assertEqual(caught.exception.errors[0]['constraint'], 'MinCountConstraintComponent')

    def test_forged_unsupported_metadata_cannot_skip_owner_validation(self):
        service,updates,action = rejection_case(forged_unsupported=True)
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            with self.assertRaises(OntologyValidationError):
                reject_all(service,updates,action)
        persist.assert_not_called()

    def test_legacy_owner_without_identity_history_remains_subject_to_constraints(self):
        service,updates,action = rejection_case(legacy=True,forged_unsupported=True)
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            with self.assertRaises(OntologyValidationError):
                reject_all(service,updates,action)
        persist.assert_not_called()

    def test_withdrawn_document_identity_can_close_by_rejecting_every_value(self):
        service,updates,action = rejection_case(withdrawn=True)
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            self.assertEqual(reject_all(service,updates,action), [])
        persist.assert_called_once()

    def test_remaining_manual_identity_support_keeps_owner_constraints_active(self):
        service,updates,action = rejection_case(withdrawn=True)
        snapshot = service.repository.formal_write_snapshot('project')
        snapshot['assertions'].append(dict(id='manual',kind='entity',status='accepted',
            canonical_record_id='owner',document_id=None,decision_version=2))
        service.repository.formal_write_snapshot = lambda project: copy.deepcopy(snapshot)
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            with self.assertRaises(OntologyValidationError):
                reject_all(service,updates,action)
        persist.assert_not_called()

    def test_withdrawn_owner_exemption_does_not_skip_another_owner(self):
        service,updates,action = rejection_case(withdrawn=True)
        snapshot = service.repository.formal_write_snapshot('project')
        snapshot['records'].append(dict(id='other',kind='entity',text='乙',
            type='https://rejection.test/Person',ontology_id='onto',metadata={},version=1,version_id='other-v1'))
        service.repository.formal_write_snapshot = lambda project: copy.deepcopy(snapshot)
        with patch('knowledge_service.services.formal_writes.FormalFactWriter.apply',
                   return_value={'accepted_records': []}) as persist:
            with self.assertRaises(OntologyValidationError) as caught:
                reject_all(service,updates,action)
        persist.assert_not_called()
        self.assertEqual(caught.exception.errors[0]['record_id'], 'other')


if __name__ == '__main__':
    unittest.main()
