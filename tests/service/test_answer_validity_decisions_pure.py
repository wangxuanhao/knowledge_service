"""QA sources follow the exact assertion decision visible at knowledge time."""
from types import SimpleNamespace
import unittest

from knowledge_service.services.answer_validity import classify


CREATED = '2020-01-01T00:00:00.000000Z'
WITHDRAWN = '2021-01-01T00:00:00.000000Z'
REACCEPTED = '2022-01-01T00:00:00.000000Z'
NOW = '2025-01-01T00:00:00.000000Z'


def decision(event_id, version, at, status='accepted', canonical='fact'):
    return {'id': event_id, 'assertion_id': 'assertion', 'decision_version': version,
            'created_at': at, 'to_status': status, 'canonical_record_id': canonical}


class AnswerValidityDecisionTests(unittest.TestCase):
    def system(self, events, mapped_events, *, record_id='fact', context=None):
        base = {'recorded_at': CREATED, 'superseded_at': None,
                'valid_from': CREATED, 'valid_until': None,
                'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
        fact = {**base, 'id': record_id, 'version_id': record_id + '-v1',
                'kind': 'attribute', 'type': 'urn:description', 'subject_id': 'owner',
                'text': 'current fact', 'value': 'current fact'}
        document = {**base, 'id': 'document', 'version_id': 'document-v1',
                    'version': 1, 'kind': 'document', 'text': 'frozen source'}
        chunk = {**base, 'id': 'chunk', 'version_id': 'chunk-v1', 'kind': 'chunk',
                 'text': 'frozen source', 'source_id': 'document',
                 'metadata': {**base['metadata'], 'source_version_id': 'document-v1'}}
        latest = max(events, key=lambda event: event['decision_version'])
        assertion = {'id': 'assertion', 'created_at': CREATED, 'kind': 'attribute',
                     'status': latest['to_status'], 'canonical_record_id': latest['canonical_record_id'],
                     'document_id': 'document', 'document_version_id': 'document-v1', 'chunk_id': 'chunk'}
        mappings = [{'assertion_id': 'assertion', 'assertion_event_id': event_id}
                    for event_id in mapped_events]
        repository = SimpleNamespace(
            list_record_version_assertions=lambda *_args, **_kwargs: mappings,
            get_assertion=lambda *_args: assertion,
            list_assertion_events=lambda *_args: list(reversed(events)),
            get_record_version=lambda *_args: document,
            history=lambda _project, record: [document] if record == 'document' else [chunk])
        request = {'known_at': NOW, 'valid_at': NOW}
        if context is not None:
            fact.update(kind='entity', metadata={**base['metadata'], 'identity_policy': 'reference'})
            repository.current_source_contexts = lambda *_args, **_kwargs: {record_id: [context]}
            request['_source_read_cache'] = {('identity_contexts', record_id): [context]}
        return SimpleNamespace(repository=repository), fact, request

    def classify(self, events, mapped_events, *, known_at=NOW, record_id='fact', context=None):
        service, fact, request = self.system(events, mapped_events, record_id=record_id, context=context)
        return classify(service, 'project', fact, {**request, 'known_at': known_at})

    def test_withdrawn_occurrence_cannot_authorize_current_fact_sources(self):
        events = [decision('accept', 1, CREATED), decision('withdraw', 2, WITHDRAWN, 'withdrawn')]
        result = self.classify(events, ['accept'])
        self.assertFalse(result['answer_eligible'])
        self.assertEqual(result['_allowed_assertion_ids'], [])
        self.assertEqual(result['_allowed_source_version_ids'], [])

    def test_history_before_withdrawal_retains_original_accepted_source(self):
        events = [decision('accept', 1, CREATED), decision('withdraw', 2, WITHDRAWN, 'withdrawn')]
        result = self.classify(events, ['accept'], known_at='2020-06-01T00:00:00.000000Z')
        self.assertTrue(result['answer_eligible'])
        self.assertEqual(result['_allowed_source_version_ids'], ['document-v1'])

    def test_withdrawal_takes_effect_at_its_exact_knowledge_time(self):
        events = [decision('accept', 1, CREATED), decision('withdraw', 2, WITHDRAWN, 'withdrawn')]
        result = self.classify(events, ['accept'], known_at=WITHDRAWN)
        self.assertFalse(result['answer_eligible'])

    def test_reacceptance_requires_the_new_exact_mapping(self):
        events = [decision('accept', 1, CREATED), decision('withdraw', 2, WITHDRAWN, 'withdrawn'),
                  decision('reaccept', 3, REACCEPTED)]
        stale = self.classify(events, ['accept'])
        self.assertFalse(stale['answer_eligible'])
        current = self.classify(events, ['accept', 'reaccept'])
        self.assertTrue(current['answer_eligible'])
        self.assertEqual(len(current['source_eligibility']), 1)

    def test_reacceptance_does_not_fill_the_withdrawn_history_gap(self):
        events = [decision('accept', 1, CREATED), decision('withdraw', 2, WITHDRAWN, 'withdrawn'),
                  decision('reaccept', 3, REACCEPTED)]
        result = self.classify(events, ['accept', 'reaccept'], known_at='2021-06-01T00:00:00.000000Z')
        self.assertFalse(result['answer_eligible'])

    def test_canonical_move_closes_the_old_support_and_keeps_its_history(self):
        events = [decision('accept', 1, CREATED), decision('move', 2, WITHDRAWN, canonical='other')]
        self.assertFalse(self.classify(events, ['accept'])['answer_eligible'])
        self.assertTrue(self.classify(events, ['move'], record_id='other')['answer_eligible'])
        historical = self.classify(events, ['accept'], known_at='2020-06-01T00:00:00.000000Z')
        self.assertTrue(historical['answer_eligible'])

    def test_foreign_canonical_acceptance_cannot_authorize_the_mapped_record(self):
        events = [decision('accept', 1, CREATED, canonical='other')]
        self.assertFalse(self.classify(events, ['accept'])['answer_eligible'])

    def test_entity_incident_context_checks_the_fact_canonical_identity(self):
        events = [decision('accept', 1, CREATED)]
        context = {'id': 'accept', 'version': 1, 'assertion_id': 'assertion',
                   'support_fact_id': 'fact', 'record_version_id': 'fact-v1',
                   'valid_from': CREATED, 'valid_until': None,
                   'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
        result = self.classify(events, [], record_id='owner', context=context)
        self.assertTrue(result['answer_eligible'])
        self.assertEqual(result['_allowed_assertion_ids'], ['assertion'])


class AnswerValidityContextScopeTests(unittest.TestCase):
    @staticmethod
    def context(identity, platform, *, start=CREATED, end=None):
        return {'id': 'accept-' + identity, 'version': 1, 'assertion_id': identity,
                'record_version_id': 'owner-v1', 'valid_from': start, 'valid_until': end,
                'metadata': {'applicability': {
                    'platforms': {'mode': 'specific', 'values': [platform]}}}}

    def classify(self, contexts, *, known_at=NOW, later_events=None):
        base = {'recorded_at': CREATED, 'superseded_at': None,
                'valid_from': CREATED, 'valid_until': None,
                'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
        owner = {**base, 'id': 'owner', 'version_id': 'owner-v1', 'kind': 'entity',
                 'type': 'Platform', 'text': 'owner',
                 'metadata': {**base['metadata'], 'identity_policy': 'reference'}}
        assertions, events, documents, histories = {}, {}, {}, {}
        for context in contexts:
            identity = context['assertion_id']
            document_id, chunk_id = 'document-' + identity, 'chunk-' + identity
            document = {**base, 'id': document_id, 'version_id': document_id + '-v1',
                        'version': 1, 'kind': 'document', 'text': 'source ' + identity}
            chunk = {**base, 'id': chunk_id, 'version_id': chunk_id + '-v1',
                     'kind': 'chunk', 'source_id': document_id, 'text': 'source ' + identity,
                     'metadata': {**base['metadata'], 'source_version_id': document['version_id']}}
            assertions[identity] = {'id': identity, 'created_at': CREATED,
                'document_id': document_id, 'document_version_id': document['version_id'],
                'chunk_id': chunk_id}
            events[identity] = [{'id': context['id'], 'created_at': CREATED,
                'decision_version': 1, 'to_status': 'accepted', 'canonical_record_id': 'owner'},
                *(later_events or {}).get(identity, [])]
            documents[document['version_id']] = document
            histories[document_id], histories[chunk_id] = [document], [chunk]
        repository = SimpleNamespace(
            current_source_contexts=lambda *_args, **_kwargs: {'owner': contexts},
            get_assertion=lambda _project, identity: assertions[identity],
            list_assertion_events=lambda _project, identity: events[identity],
            get_record_version=lambda _project, version: documents[version],
            history=lambda _project, identity: histories[identity])
        request = {'known_at': known_at, 'valid_at': NOW,
                   'applicability': {'platforms': {'mode': 'specific', 'values': ['B']}},
                   '_source_read_cache': {('identity_contexts', 'owner'): contexts}}
        return classify(SimpleNamespace(repository=repository), 'project', owner, request)

    def test_only_matching_platform_context_can_authorize_frozen_sources(self):
        result = self.classify([self.context('a', 'A'), self.context('b', 'B')])
        self.assertTrue(result['answer_eligible'])
        self.assertEqual(result['_allowed_assertion_ids'], ['b'])
        self.assertEqual(result['_allowed_source_version_ids'], ['document-b-v1'])
        self.assertEqual(result['_allowed_chunk_version_ids'], ['chunk-b-v1'])
        self.assertEqual(result['matched_source_context']['id'], 'accept-b')

    def test_context_time_cannot_be_borrowed_from_another_platform(self):
        contexts = [self.context('a', 'A'), self.context('old-b', 'B', end=REACCEPTED),
                    self.context('current-b', 'B', start=REACCEPTED)]
        result = self.classify(contexts)
        self.assertTrue(result['answer_eligible'])
        self.assertEqual(result['_allowed_assertion_ids'], ['current-b'])
        self.assertEqual(result['matched_source_context']['id'], 'accept-current-b')
        self.assertIsNone(self.classify(contexts[:2]))

    def test_matching_context_retains_only_its_accepted_knowledge_interval(self):
        contexts = [self.context('a', 'A'), self.context('b', 'B')]
        withdrawal = {'b': [{'id': 'withdraw-b', 'created_at': REACCEPTED,
                            'decision_version': 2, 'to_status': 'withdrawn',
                            'canonical_record_id': 'owner'}]}
        historical = self.classify(contexts, known_at=WITHDRAWN, later_events=withdrawal)
        self.assertTrue(historical['answer_eligible'])
        self.assertEqual(historical['_allowed_assertion_ids'], ['b'])
        current = self.classify(contexts, later_events=withdrawal)
        self.assertFalse(current['answer_eligible'])
        self.assertEqual(current['_allowed_assertion_ids'], [])
        self.assertEqual(current['_allowed_source_version_ids'], [])


if __name__ == '__main__':
    unittest.main()
