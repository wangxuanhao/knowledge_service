"""Detached formal plans must validate every effective accepted identity binding."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from knowledge_service.services.entity_resolution import EntityResolver
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.reconciliation import reconcile


TTL = '''@prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix kg: <https://knowledge.local/schema/> .
ex:Platform a owl:Class; kg:identityPolicy "reference"; kg:identityKey "code" .
ex:Event a owl:Class; kg:identityPolicy "scoped" .'''


def entity(id='canonical', key=None, *, type='http://ex/Platform', year='2020', scope=''):
    metadata = {'identity_policy': 'reference', 'identity_source_qualified': True,
                'identity_key_fields': ['code'], 'identity_namespace': '["",""]'}
    if key is not None:
        metadata['identity_keys'] = {'code': key}
    if scope:
        metadata['identity_scope'] = scope
    return {'id': id, 'kind': 'entity', 'type': type, 'text': 'Same',
            'valid_from': year+'-01-01', 'valid_until': str(int(year)+1)+'-01-01',
            'metadata': metadata}


def support(id='support', key=None, *, status='accepted', target='canonical', **kwargs):
    return {'id': id, 'kind': 'entity', 'payload': entity('mention', key, **kwargs),
            'status': status, 'decision_version': 2 if status=='accepted' else 1,
            'canonical_record_id': target if status=='accepted' else None}


def snapshot(records=(), assertions=()):
    return {'project_id': 'project', 'storage_namespace': 'test', 'ontology_id': 'onto',
            'ontology': {'id': 'onto', 'turtle': TTL}, 'fingerprint': 'frozen',
            'records': [{**copy.deepcopy(row), 'version': 1, 'version_id': row['id']+'-v1'} for row in records],
            'assertions': copy.deepcopy(list(assertions)), 'assertion_events': [],
            'resolution_reviews': [], 'merge_operations': [], 'fact_keys': [],
            'record_version_assertions': []}


class DetachedRepository:
    def __init__(self, value): self.value = value
    def formal_write_snapshot(self, project): return copy.deepcopy(self.value)
    def formal_write_context(self, value): return value
    def get_ontology(self, project): return {'turtle': TTL}
    def current_records(self, project, kinds=None): return copy.deepcopy(self.value['records'])


def plan(value, *, records=(), assertions=(), decisions=(), operation='manual_write', **policy):
    existing={row['id'] for row in value['records']}
    return FormalFactWriter(DetachedRepository(value)).prepare(operation, 'project',
        list(assertions), {row['id']: 1 if row['id'] in existing else 0 for row in records},
        {'records': list(records), 'assertion_decisions': list(decisions), **policy})


def remap_case():
    old=entity(type='http://ex/Event');old['ontology_id']='old'
    new=entity();new['ontology_id']='new'
    source=support(type='Event');source['payload']['ontology_id']='old'
    value=snapshot([old],[source]);value['ontology_id']='new'
    value['ontology']={'id':'new','turtle':TTL.replace('ex:Event a owl:Class; kg:identityPolicy "scoped" .','')}
    value['ontology_class_aliases']={
        'old':[{'id':'http://ex/Event','name':'Event','active':True}],
        'new':[{'id':'http://ex/Platform','name':'Platform','active':True}]}
    audit={'id':'audit:migrate','kind':'document','text':'approved migration','metadata':{
        '_audit':True,'operation':'reclassify','operation_id':'migrate','reclassify':{
            'to_ontology_id':'new','from_to':[{'from_type':'Event','to_type':'Platform'}]}}}
    ledger={'id':'migrate','operation':'ontology_remap','before_state':copy.deepcopy(value['records']),
            'expected_versions':{'canonical':1},'redirects':{}}
    return value,new,audit,ledger


def undone_remap_case():
    value,new,audit,ledger=remap_case()
    remapped={**new,'version':2,'version_id':'canonical-v2'}
    restored={**copy.deepcopy(ledger['before_state'][0]),'version':3,'version_id':'canonical-v3'}
    value['records']=[restored]
    value['merge_operations']=[
        {**ledger,'status':'applied','assertion_moves':[],'after_state':[remapped,audit]},
        {'id':'undo-migrate','operation':'merge_reversal','status':'applied',
         'reversal_of':ledger['id'],'before_state':[remapped],'after_state':[restored],
         'expected_versions':{'canonical':2},'assertion_moves':[],'redirects':{}}]
    return value,new,restored


class IdentityBindingValidationTests(unittest.TestCase):
    def test_reference_similarity_is_review_only(self):
        repo = DetachedRepository(snapshot([entity()]))
        resolver = EntityResolver(repo, None)
        resolver.semantic_candidates = lambda *args, **kwargs: [{**repo.value['records'][0], 'score': .99}]
        mention = {**entity('mention'), 'text': 'Different name'}
        outcome = resolver.resolve('project', mention, review_threshold=.7, merge_threshold=.9)
        self.assertEqual(outcome.status, 'review')
        self.assertIsNone(outcome.canonical_id)

    def test_reconciliation_learns_incoming_registered_key(self):
        service = SimpleNamespace(repository=DetachedRepository(snapshot([entity()])))
        with patch('semantica.deduplication.EntityMerger.merge_entity_group', return_value=SimpleNamespace(metadata={})):
            rows, _ = reconcile(service, 'project', [entity('mention', 'A')], {},
                                {'id': 'doc', 'version_id': 'doc-v1'})
        canonical = next(row for row in rows if row['id']=='canonical')
        self.assertEqual(canonical['metadata'].get('identity_keys'), {'code': 'A'})

    def test_accepted_transition_checks_key_type_and_identity_scope(self):
        for invalid in ({'key': 'B'}, {'type': 'http://ex/Event'}, {'scope': 'other'}):
            with self.subTest(invalid=invalid):
                source = support(status='pending', **invalid)
                with self.assertRaises(ValueError):
                    plan(snapshot([entity(key='A')], [source]), decisions=[{
                        'id': source['id'], 'expected_version': 1, 'status': 'accepted',
                        'canonical_record_id': 'canonical', 'reason': 'review', 'actor': 'reviewer'}])

    def test_accepted_transition_adds_cross_source_gate(self):
        source = support(status='pending', key='A', year='2022')
        result = plan(snapshot([entity(key='A')], [source]), decisions=[{
            'id': source['id'], 'expected_version': 1, 'status': 'accepted',
            'canonical_record_id': 'canonical', 'reason': 'review', 'actor': 'reviewer'}])
        self.assertIn('source_identity_projection_gate', [a['kind'] for a in result['actions']])

    def test_two_accepted_keys_cannot_conflict_in_one_batch(self):
        assertions = [{key: value for key,value in support(id=id, key=value).items()
                       if key not in {'status', 'decision_version'}}
                      for id,value in [('first','A'), ('second','B')]]
        with self.assertRaises(ValueError):
            plan(snapshot([entity()]), assertions=assertions)

    def test_acceptance_learns_key_in_final_revision_and_maps_to_it(self):
        assertion = {key:value for key,value in support(key='A', type='Platform').items()
                     if key not in {'status','decision_version'}}
        result = plan(snapshot([entity()]), assertions=[assertion])
        puts = [a for a in result['actions'] if a['kind']=='record_put']
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]['record']['metadata']['identity_keys'], {'code':'A'})
        accepted = next(a for a in result['actions'] if a['kind']=='assertion_accept')
        self.assertEqual(accepted['record_ref'], {'token':puts[0]['token']})
        self.assertLess(result['actions'].index(puts[0]), result['actions'].index(accepted))

    def test_ordinary_revision_preserves_still_accepted_key(self):
        result = plan(snapshot([entity(key='A')], [support(key='A')]), records=[entity()])
        final = [a for a in result['actions'] if a['kind']=='record_put'][-1]
        self.assertEqual(final['record']['metadata'].get('identity_keys'), {'code':'A'})

    def test_ordinary_revision_cannot_replace_still_accepted_key(self):
        with self.assertRaises(ValueError):
            plan(snapshot([entity(key='A')], [support(key='A')]), records=[entity(key='B')])

    def test_key_learning_does_not_retarget_replay_mapping(self):
        value = snapshot([entity()], [support(key='A')])
        assertion = {key:value for key,value in support(key='A').items()
                     if key not in {'status','decision_version'}}
        result = plan(value, assertions=[assertion])
        self.assertTrue(any(a['kind']=='record_put' for a in result['actions']))
        replay = next(a for a in result['actions'] if a['kind']=='assertion_accept')
        self.assertEqual(replay['mode'], 'replay')
        self.assertEqual(replay['record_ref'], {'version_id':'canonical-v1'})

    def test_same_batch_replay_uses_new_events_final_mapping(self):
        assertion = {key:value for key,value in support(key='A').items()
                     if key not in {'status','decision_version'}}
        result = plan(snapshot([entity()]), assertions=[assertion,copy.deepcopy(assertion)])
        put = next(action for action in result['actions'] if action['kind']=='record_put')
        accepts = [action for action in result['actions'] if action['kind']=='assertion_accept']
        self.assertEqual([action['mode'] for action in accepts], ['create','replay'])
        self.assertEqual([action['record_ref'] for action in accepts], [{'token':put['token']}]*2)

    def test_key_learning_updates_ledger_move_reference(self):
        value = snapshot([entity('source'), entity()], [support(key='A', target='source')])
        removed = entity('source')
        removed['metadata'].update(_deleted=True, merged_into='canonical')
        result = plan(value, records=[removed], operation='merge_rewrite',
                      ledger={'id':'merge', 'redirects':{'source':'canonical'}})
        canonical_puts = [a for a in result['actions'] if a['kind']=='record_put' and a['record']['id']=='canonical']
        self.assertEqual(len(canonical_puts), 1)
        canonical = canonical_puts[0]
        moved = next(a for a in result['actions'] if a['kind']=='assertion_move')
        self.assertEqual(moved['record_ref'], {'token':canonical['token']})

    def test_entity_binding_cannot_target_a_relation(self):
        edge = {'id':'edge','kind':'relation','type':'knows','text':'edge','metadata':{},
                'subject_id':'a','object_id':'b'}
        source = support(status='pending')
        with self.assertRaises(ValueError):
            plan(snapshot([edge], [source]), decisions=[{
                'id': source['id'], 'expected_version': 1, 'status': 'accepted',
                'canonical_record_id': 'edge', 'reason': 'review', 'actor': 'reviewer'}])

    def test_controlled_remap_uses_bound_release_and_exact_audit_proof(self):
        value,new,audit,ledger=remap_case()
        try:result=plan(value,records=[new,audit],operation='ontology_remap',ledger=ledger)
        except ValueError as exc:self.fail(f'Approved migration was rejected: {exc}')
        changed=next(a for a in result['actions'] if a['kind']=='record_put' and a['record']['id']=='canonical')
        self.assertEqual(changed['record']['type'],'http://ex/Platform')
        self.assertEqual(value['assertions'][0]['payload']['type'],'Event')

    def test_remap_operation_name_without_audit_does_not_authorize_type_change(self):
        value,new,_,ledger=remap_case()
        with self.assertRaises(ValueError):
            plan(value,records=[new],operation='ontology_remap',ledger=ledger)

    def test_forged_before_state_cannot_authorize_a_pending_type_mismatch(self):
        value,new,audit,ledger=remap_case()
        value['records']=[{**new,'version':1,'version_id':'canonical-v1'}]
        value['assertions']=[support(status='pending',type='Event')]
        value['assertions'][0]['payload']['ontology_id']='old'
        with self.assertRaises(ValueError):
            plan(value,records=[new,audit],operation='ontology_remap',ledger=ledger, decisions=[{
                'id':'support','expected_version':1,'status':'accepted','canonical_record_id':'canonical',
                'reason':'review','actor':'reviewer'}])

    def test_ordinary_write_after_remap_uses_persisted_audit(self):
        value,new,audit,ledger=remap_case()
        value['records']=[{**new,'version':2,'version_id':'canonical-v2'}]
        value['merge_operations']=[{**ledger,'status':'applied','after_state':copy.deepcopy(value['records'])+[audit]}]
        try:result=plan(value,records=[entity('unrelated')])
        except ValueError as exc:self.fail(f'Persisted migration proof was ignored: {exc}')
        self.assertFalse(any(a['kind']=='record_put' and a['record']['id']=='canonical' for a in result['actions']))

    def test_legacy_source_release_survives_remap_then_accepted_identity_move(self):
        value,new,audit,ledger=remap_case()
        source=value['assertions'][0]
        source['payload'].pop('ontology_id')
        original_payload=copy.deepcopy(source['payload'])
        remapped={**new,'version':2,'version_id':'canonical-v2'}
        removed=copy.deepcopy(remapped)
        removed.update(version=3,version_id='canonical-v3')
        removed['metadata'].update(_deleted=True,merged_into='keep')
        keep={**entity('keep'),'ontology_id':'new','version':1,'version_id':'keep-v1'}
        value['records']=[removed,keep]
        value['merge_operations']=[{**ledger,'status':'applied',
            'after_state':[remapped,audit]}]
        source.update(canonical_record_id='keep',decision_version=3)
        value['assertion_events']=[
            dict(id='accepted-old',assertion_id=source['id'],to_status='accepted',
                 decision_version=2,canonical_record_id='canonical'),
            dict(id='moved-new',assertion_id=source['id'],to_status='accepted',
                 decision_version=3,canonical_record_id='keep')]
        value['record_version_assertions']=[
            dict(assertion_id=source['id'],assertion_event_id='accepted-old',record_id='canonical',
                 record_version_id='canonical-v1',mapped_ontology_id='old'),
            dict(assertion_id=source['id'],assertion_event_id='moved-new',record_id='keep',
                 record_version_id='keep-v1',mapped_ontology_id='new')]

        try:result=plan(value,records=[entity('unrelated')])
        except ValueError as exc:self.fail(f'Identity move changed the immutable source release: {exc}')

        self.assertEqual(source['payload'],original_payload)
        self.assertEqual(source['payload']['type'],'Event')
        self.assertEqual([action['record']['id'] for action in result['actions']
                          if action['kind']=='record_put'],['unrelated'])

    def test_undone_remap_does_not_conflict_with_different_audited_remap(self):
        value,_,restored=undone_remap_case()
        value['ontology_id']='alternate'
        value['ontology']={'id':'alternate','turtle':value['ontology']['turtle']+
            '\n<http://ex/Organization> a <http://www.w3.org/2002/07/owl#Class>; '
            '<https://knowledge.local/schema/identityPolicy> "reference" .'}
        value['ontology_class_aliases']['alternate']=[
            {'id':'http://ex/Organization','name':'Organization','active':True}]
        replacement={**entity(type='http://ex/Organization'),'ontology_id':'alternate'}
        audit={'id':'audit:alternate','kind':'document','text':'approved alternate migration',
            'metadata':{'_audit':True,'operation':'reclassify','operation_id':'alternate-remap',
                'reclassify':{'to_ontology_id':'alternate',
                    'from_to':[{'from_type':'Event','to_type':'Organization'}]}}}
        ledger={'id':'alternate-remap','operation':'ontology_remap','before_state':[restored],
            'expected_versions':{'canonical':3},'redirects':{}}
        history=copy.deepcopy(value['merge_operations'])

        try:
            result=FormalFactWriter(DetachedRepository(value)).prepare('ontology_remap','project',[],
                {'canonical':3,audit['id']:0},{'records':[replacement,audit],'ledger':ledger})
        except ValueError as exc:self.fail(f'Undone remap still conflicts with the approved replacement: {exc}')
        self.assertTrue(any(action['kind']=='merge_ledger' and action['id']==ledger['id']
                            for action in result['actions']))
        final={**replacement,'version':4,'version_id':'canonical-v4'}
        value['records']=[final]
        value['merge_operations'].append({**ledger,'status':'applied','assertion_moves':[],
                                           'after_state':[final,audit]})

        try:plan(value,records=[entity('unrelated')])
        except ValueError as exc:self.fail(f'Approved alternate remap blocked a later write: {exc}')
        self.assertEqual(value['merge_operations'][:2],history)
        self.assertEqual(value['assertions'][0]['payload']['type'],'Event')

    def test_undone_remap_cannot_authorize_the_canceled_type_without_new_audit(self):
        value,canceled,_=undone_remap_case()
        with self.assertRaises(ValueError):
            FormalFactWriter(DetachedRepository(value)).prepare('manual_write','project',[],
                {'canonical':3},{'records':[canceled]})

    def test_undoing_the_reversal_restores_original_remap_authority(self):
        value,new,restored=undone_remap_case()
        final={**new,'version':4,'version_id':'canonical-v4'}
        value['records']=[final]
        value['merge_operations'].append({'id':'redo-migrate','operation':'merge_reversal',
            'status':'applied','reversal_of':'undo-migrate','before_state':[restored],
            'after_state':[final],'expected_versions':{'canonical':3},'assertion_moves':[],'redirects':{}})
        try:plan(value,records=[entity('unrelated')])
        except ValueError as exc:self.fail(f'Undoing the reversal failed to restore remap authority: {exc}')


if __name__ == '__main__':
    unittest.main()
