"""A reviewed proposal acquires its type from its exact accepted binding."""
import copy
import unittest

from test_identity_binding_validation_pure import entity, plan, remap_case, snapshot, support


def proposal(*, status='accepted', proposed='Unknown', target='canonical'):
    row = support(status=status, target=target)
    row['payload'].pop('type')
    row['payload']['proposed_type'] = proposed
    return row


def accepted_mapping(value, *, type='http://ex/Platform', release='onto',
                     decision=2, record='canonical', event='accepted'):
    value['assertion_events'].append({
        'id': event, 'assertion_id': 'support', 'to_status': 'accepted',
        'decision_version': decision, 'canonical_record_id': record})
    value['record_version_assertions'].append({
        'assertion_id': 'support', 'assertion_event_id': event,
        'record_id': record, 'record_version_id': record+'-v1',
        'mapped_type': type, 'mapped_ontology_id': release})


def decision():
    return {'id': 'support', 'expected_version': 1, 'status': 'accepted',
            'canonical_record_id': 'canonical', 'reason': 'classify proposal', 'actor': 'reviewer'}


class ReviewIdentityLineageTests(unittest.TestCase):
    def test_first_explicit_acceptance_classifies_the_proposal_without_mutating_it(self):
        value = snapshot(assertions=[proposal(status='pending')])
        original = copy.deepcopy(value['assertions'][0]['payload'])
        result = plan(value, records=[entity()], decisions=[decision()],
                      operation='approve_review', suppress_auto_assertions=True)
        accepted = next(a for a in result['actions'] if a['kind']=='assertion_transition')
        created = next(a for a in result['actions'] if a['kind']=='record_put')
        self.assertEqual(accepted['record_ref'], {'token': created['token']})
        self.assertEqual(value['assertions'][0]['payload'], original)

    def test_persisted_first_binding_preserves_classification_on_later_writes(self):
        value = snapshot([entity()], [proposal()])
        accepted_mapping(value)
        result = plan(value, records=[entity('unrelated')])
        self.assertEqual([a['record']['id'] for a in result['actions']
                          if a['kind']=='record_put'], ['unrelated'])
        self.assertEqual(value['assertions'][0]['payload']['proposed_type'], 'Unknown')

    def test_operation_name_without_an_exact_acceptance_does_not_classify(self):
        with self.assertRaises(ValueError):
            plan(snapshot([entity()], [proposal()]), records=[entity('unrelated')],
                 operation='approve_review')

    def test_first_binding_cannot_be_overridden_by_later_mapping_or_proposal(self):
        value = snapshot([entity()], [proposal(proposed='Platform')])
        accepted_mapping(value, type='http://ex/Event', record='original')
        accepted_mapping(value, decision=3, event='moved')
        value['assertions'][0]['decision_version'] = 3
        with self.assertRaises(ValueError):
            plan(value, records=[entity('unrelated')])

    def test_ambiguous_first_binding_fails_closed(self):
        value = snapshot([entity()], [proposal(proposed='Platform')])
        accepted_mapping(value)
        accepted_mapping(value, type='http://ex/Event', event='other')
        with self.assertRaises(ValueError):
            plan(value, records=[entity('unrelated')])

    def test_historical_mapping_without_a_release_cannot_use_todays_local_type(self):
        value = snapshot([entity()], [proposal(proposed='Platform')])
        accepted_mapping(value, type='Platform', release=None)
        with self.assertRaises(ValueError):
            plan(value, records=[entity('unrelated')])

    def test_explicit_source_type_remains_strict_even_with_an_accepted_mapping(self):
        source = proposal(proposed='Platform')
        source['payload']['type'] = 'http://ex/Event'
        value = snapshot([entity()], [source])
        accepted_mapping(value)
        with self.assertRaises(ValueError):
            plan(value, records=[entity('unrelated')])

    def test_proposal_binding_can_follow_an_exact_audited_reclassification(self):
        value, new, audit, ledger = remap_case()
        value['assertions'][0]['payload'].pop('type')
        value['assertions'][0]['payload']['proposed_type'] = 'Unknown'
        accepted_mapping(value, type='http://ex/Event', release='old')
        result = plan(value, records=[new, audit], operation='ontology_remap', ledger=ledger)
        changed = next(a for a in result['actions'] if a['kind']=='record_put'
                       and a['record']['id']=='canonical')
        self.assertEqual(changed['record']['type'], 'http://ex/Platform')

    def test_first_acceptance_cannot_classify_as_an_unpublished_type(self):
        value = snapshot(assertions=[proposal(status='pending')])
        with self.assertRaises(ValueError):
            plan(value, records=[entity(type='http://ex/NotPublished')],
                 decisions=[decision()], operation='approve_review', suppress_auto_assertions=True)


if __name__ == '__main__':
    unittest.main()
