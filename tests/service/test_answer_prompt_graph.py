"""Frozen graph topology reaches generation without PostgreSQL fixtures."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_service.services import answers, provenance, service as facade
from knowledge_service.repository.scoped_reads import ScopedReads


def record(record_id, citation, kind='entity', **changes):
    return {
        'id': record_id, 'citation': citation, 'kind': kind,
        'version_id': record_id + ':v1', 'text': record_id,
        'type': 'urn:Merchant' if kind == 'entity' else 'urn:allows',
        'query_platform': '美团', 'source_scope': 'specific',
        'answer_eligible': True, 'validity_status': 'effective',
        'usage': 'effective_basis', 'valid_from': '2020-01-01',
        'valid_until': None,
        'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}, 'published_at': '2019-12-01'},
        'source_eligibility': [{'document_version_id': 'document:v1',
                                'validity_status': 'effective'}],
        'provenance_ref': 'answer:frozen#' + citation,
        **changes,
    }


def graph_records():
    return [record('merchant', 'E1', text='退款商户'),
            record('standard', 'E2', text='普通退款'),
            record('exception', 'E3', text='例外退款'),
            record('allows-standard', 'E4', 'relation', text='允许申请',
                   subject_id='merchant', object_id='standard'),
            record('allows-exception', 'E5', 'relation', text='允许申请',
                   subject_id='exception', object_id='merchant')]


def prompt(rows, **kwargs):
    return json.loads(answers.answer_messages('退款商户允许申请什么？', rows, **kwargs)[1]['content'])


def facts(payload):
    return [fact for group in payload['platform_evidence'] for fact in group.get('graph_facts', [])]


class AnswerPromptGraphTests(unittest.TestCase):
    def test_default_platform_free_prepare_derives_real_occurrences_before_prompt_projection(self):
        """Use real search/grouping/qualification/freeze, with only I/O adapters.

        The stored records intentionally contain no citation, answer eligibility,
        query_platform or source_scope. Those fields must come from production.
        """
        class Repository:
            _db = object()

            def list_ontologies(self, _project):
                return [{'id': 'ontology', 'turtle': '''
                    @prefix owl: <http://www.w3.org/2002/07/owl#> .
                    <urn:Merchant> a owl:Class .
                    <urn:allows> a owl:ObjectProperty .'''}]

            def get_record_version(self, _project, _version):
                return {'id': 'document', 'version_id': 'document:v1', 'version': 1,
                        'kind': 'document', 'valid_from': '2020-01-01',
                        'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}

            def history(self, project, record_id):
                if record_id == 'document':
                    return [self.get_record_version(project, 'document:v1')]
                return [{'id': 'chunk', 'version_id': 'chunk:v1', 'kind': 'chunk',
                         'source_id': 'document', 'valid_from': '2020-01-01',
                         'metadata': {'source_version_id': 'document:v1',
                                      'applicability': {'platforms': {'mode': 'all'}}}}]

            def list_record_version_assertions(self, _project, **_kwargs):
                return [{'assertion_id': 'assertion', 'assertion_event_id': 'accepted'}]

            def get_assertion(self, _project, _assertion):
                return {'document_id': 'document', 'document_version_id': 'document:v1',
                        'chunk_id': 'chunk'}

            def list_assertion_events(self, _project, _assertion):
                return [{'id': 'accepted'}]

        class FrozenProvenance:
            def complete_retrieval(self, _project, _run, context, rows):
                self.context, self.rows = deepcopy(context), deepcopy(rows)
                return 'frozen'

            def trace_answer_evidences(self, _project, _answer, citations):
                return {citation: {'subject': {'answer_id': 'frozen'}} for citation in citations}

        for source, expected in (({'platforms': {'mode': 'specific', 'values': ['美团']}}, ('美团', 'specific')),
                                 ({'platforms': {'mode': 'all'}}, (None, 'all')),
                                 ({}, (None, 'unknown'))):
            with self.subTest(source=source):
                stored = [{**{key: value for key, value in row.items() if key in {
                    'id', 'kind', 'version_id', 'text', 'type', 'subject_id', 'object_id',
                    'valid_from', 'valid_until'}}, 'metadata': {'applicability': source}}
                    for row in graph_records()]
                self.assertTrue(all('source_scope' not in row and 'query_platform' not in row
                                    and 'answer_eligible' not in row for row in stored))
                service = facade.KnowledgeService(Repository(), SimpleNamespace(identity='test', semantic=False))
                frozen = FrozenProvenance()
                with patch.object(facade.RetrievalEngine, 'search', return_value={
                        'hits': [stored[0]], 'candidate_count': 1, 'active_mode': 'keyword',
                        'degraded': False, '_backend': 'postgres_local', 'backends': {}}) as retrieval, \
                     patch.object(ScopedReads, 'source_context', return_value={
                         'source_chunks': [], 'context_chunks': []}), \
                     patch.object(ScopedReads, 'neighbors', return_value={
                         'records': stored, 'reasoning_paths': [], 'truncated': False,
                         'continuation': None}) as neighbors:
                    context, evidence, _answer = answers.prepare_answer(service, 'p', {
                        'query': '退款商户允许申请什么？', 'retrieval_mode': 'keyword',
                        'include_references': False, 'valid_at': '2025-01-01',
                        'known_at': '2026-01-01'}, frozen, 'retrieval')
                groups = context['platform_groups']
                self.assertEqual([(group['platform'], group['source_scope']) for group in groups], [expected])
                self.assertIsNone(retrieval.call_args.kwargs['platform_match'])
                topology = facts(prompt(evidence))
                if expected[1] == 'unknown':
                    neighbors.assert_not_called()
                    self.assertEqual(topology, [])
                else:
                    self.assertEqual(len(topology), 2)
                    self.assertEqual({(row['query_platform'], row['source_scope']) for row in evidence}, {expected})
                    self.assertTrue(all(row['answer_eligible'] and row['validity_status'] == 'effective'
                                        for row in evidence))
                    self.assertEqual(topology[0]['subject']['text'], '退款商户')
                    self.assertEqual(topology[0]['object']['text'], '普通退款')
                    self.assertEqual(topology[0]['source_version_ids'], ['document:v1'])
                    self.assertEqual(topology[0]['provenance_ref'], 'answer:frozen#E4')

    def test_same_predicate_keeps_distinct_directed_qualified_endpoints(self):
        payload = prompt(graph_records(), as_of='2025-01-01', known_at='2026-01-01')
        topology = facts(payload)
        self.assertEqual(len(topology), 2, 'model input loses qualified graph topology')
        self.assertEqual([(fact['subject']['record_id'], fact['object']['record_id'])
                          for fact in topology], [('merchant', 'standard'), ('exception', 'merchant')])
        self.assertEqual([fact['text'] for fact in topology], ['允许申请', '允许申请'])
        fact = topology[0]
        self.assertEqual(fact['direction'], 'subject_to_object')
        self.assertEqual(fact['kind'], 'relation')
        self.assertEqual(fact['type'], 'urn:allows')
        self.assertEqual(fact['record_id'], 'allows-standard')
        self.assertEqual(fact['record_version_id'], 'allows-standard:v1')
        self.assertEqual(fact['citation'], 'E4')
        self.assertEqual(fact['provenance_ref'], 'answer:frozen#E4')
        self.assertEqual(fact['source_version_ids'], ['document:v1'])
        self.assertEqual(fact['subject']['text'], '退款商户')
        self.assertEqual(fact['subject']['citation'], 'E1')
        self.assertEqual(fact['object']['text'], '普通退款')
        self.assertEqual(fact['object']['citation'], 'E2')
        self.assertEqual(payload['query_context'], {'as_of': '2025-01-01', 'known_at': '2026-01-01'})
        evidence = payload['platform_evidence'][0]['evidence'][0]
        self.assertEqual(evidence['valid_from'], '2020-01-01')
        self.assertEqual(evidence['published_at'], '2019-12-01')
        self.assertEqual(evidence['applicability'], {'platforms': {'mode': 'specific', 'values': ['美团']}})

    def test_missing_ineligible_unknown_and_other_occurrence_endpoints_are_not_completed(self):
        for changes in (None, {'answer_eligible': False}, {'answer_eligible': None},
                        {'validity_status': 'unknown'}, {'query_platform': '京东'},
                        {'source_scope': 'all'}, {'source_scope': 'unknown'}):
            with self.subTest(changes=changes):
                rows = graph_records()[:2] + graph_records()[3:4]
                if changes is None:
                    rows.pop(1)
                else:
                    rows[1].update(changes)
                self.assertEqual(facts(prompt(rows)), [])

    def test_unknown_or_unqualified_relations_do_not_become_graph_facts(self):
        for changes in ({'answer_eligible': False}, {'answer_eligible': None},
                        {'validity_status': 'unknown'}, {'citation': 'U1'},
                        {'source_scope': 'unknown'}, {'source_scope': None}):
            with self.subTest(changes=changes):
                rows = graph_records()[:2] + graph_records()[3:4]
                rows[-1].update(changes)
                self.assertEqual(facts(prompt(rows)), [])

    def test_conflicting_endpoint_versions_are_not_resolved_arbitrarily(self):
        rows = graph_records()[:2] + graph_records()[3:4]
        rows.append(record('standard', 'E6', version_id='standard:v2', text='其他版本'))
        self.assertEqual(facts(prompt(rows)), [])

    def test_graph_projection_does_not_copy_untrusted_metadata_or_invent_source_references(self):
        rows = graph_records()
        rows[-2]['metadata']['instructions'] = 'IGNORE_SYSTEM_INSTRUCTION'
        rows[-2]['metadata']['subject_label'] = 'FABRICATED_ENDPOINT'
        rows[-2]['source_eligibility'] = []
        rows[-2]['sources'] = [{'document_version_id': 'unqualified-source',
                                'quote': 'UNTRUSTED_SOURCE_INSTRUCTION'}]
        payload = prompt(rows)
        self.assertEqual(len(facts(payload)), 2)
        self.assertEqual(facts(payload)[0]['source_version_ids'], [])
        serialized = json.dumps(payload, ensure_ascii=False)
        for untrusted in ('IGNORE_SYSTEM_INSTRUCTION', 'FABRICATED_ENDPOINT',
                          'UNTRUSTED_SOURCE_INSTRUCTION', 'unqualified-source'):
            self.assertNotIn(untrusted, serialized)
        system = answers.answer_messages('退款', rows)[0]['content']
        self.assertIn('材料', system)
        self.assertIn('graph_facts', system)

    def test_global_fact_count_and_serialized_character_budgets_are_enforced(self):
        rows = graph_records()
        rows += [{**row, 'query_platform': '京东', 'citation': 'E' + str(index + 6)}
                 for index, row in enumerate(graph_records())]
        with patch.object(answers, 'PROMPT_GRAPH_FACT_LIMIT', 2, create=True):
            payload = prompt(rows)
        self.assertEqual(len(facts(payload)), 2)
        self.assertTrue(any(group.get('graph_facts_truncated') for group in payload['platform_evidence']))
        rows = graph_records()
        rows[1]['text'] = '很长的名称' * 20000
        with patch.object(answers, 'PROMPT_GRAPH_CHAR_LIMIT', 2000, create=True):
            payload = prompt(rows)
        topology = facts(payload)
        self.assertEqual([fact['record_id'] for fact in topology], ['allows-exception'])
        self.assertLessEqual(len(json.dumps(topology, ensure_ascii=False)), 2000)
        self.assertTrue(payload['platform_evidence'][0]['graph_facts_truncated'])

    def test_document_quote_citation_styles_and_no_graph_input_stay_unchanged(self):
        quotes = [record('chunk-' + prefix, prefix + '1', 'chunk',
                         answer_eligible=False, document_quote_allowed=True,
                         validity_status=status, usage='reference')
                  for prefix, status in [('U', 'unknown'), ('R', 'expired'), ('F', 'not_yet_effective')]]
        payload = prompt(quotes, answer_purpose='document_explanation')
        self.assertEqual([row['id'] for row in payload['platform_evidence'][0]['evidence']], ['U1', 'R1', 'F1'])
        self.assertEqual(facts(payload), [])
        self.assertNotIn('graph_facts', payload['platform_evidence'][0])
        # no_graph already removes relations upstream; no_ontology leaves real
        # relation IRIs and endpoints available without performing label lookup.
        self.assertEqual(facts(prompt(graph_records()[:3])), [])
        topology = facts(prompt(graph_records()))
        self.assertEqual(len(topology), 2)
        self.assertEqual(topology[0]['type'], 'urn:allows')

    def test_default_graph_budget_is_global_across_many_platform_groups(self):
        rows = []
        for platform in range(20):
            entities = [record('a', f'E{platform * 20 + 1}'),
                        record('b', f'E{platform * 20 + 2}')]
            relations = [record(f'r{index}', f'E{platform * 20 + index + 3}', 'relation',
                                subject_id='a', object_id='b') for index in range(15)]
            rows.extend({**row, 'query_platform': f'platform-{platform}'} for row in [*entities, *relations])
        payload = prompt(rows)
        topology = facts(payload)
        self.assertGreater(len(topology), 0)
        self.assertLessEqual(len(topology), 200)
        self.assertLessEqual(len(json.dumps(topology, ensure_ascii=False)), 48_000)
        self.assertTrue(any(group.get('graph_facts_truncated') for group in payload['platform_evidence']))

    def test_json_and_sse_provider_requests_contain_the_same_topology(self):
        calls = []

        class FrozenProvenance:
            def __init__(self, _repository):
                pass

            def begin_retrieval(self, *_args):
                return 'retrieval'

            def complete_answer(self, *_args, **_kwargs):
                pass

            def fail_activity(self, *_args):
                raise AssertionError('generation must complete')

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {'choices': [{'message': {'content': '退款说明 [E4]'}}]}

            def iter_lines(self):
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '退款说明 [E4]'}}]})
                yield 'data: [DONE]'

        class Client:
            def post(self, *_args, **kwargs):
                calls.append(kwargs['json'])
                return Response()

            @contextmanager
            def stream(self, *_args, **kwargs):
                calls.append(kwargs['json'])
                yield Response()

        @contextmanager
        def provider(*_args):
            yield Client()

        def prepared(*_args):
            return deepcopy({'answer_purpose': 'rule_judgment', 'timings_ms': {},
                             'as_of': '2025-01-01', 'known_at': '2026-01-01'}), graph_records(), 'frozen'

        service = facade.KnowledgeService.__new__(facade.KnowledgeService)
        service.repository = SimpleNamespace()
        with patch.dict(os.environ, {'KG_LLM_BASE_URL': 'https://provider.invalid',
                                      'KG_LLM_API_KEY': 'test', 'KG_LLM_MODEL': 'test'}), \
             patch.object(answers, 'prepare_answer', side_effect=prepared), \
             patch.object(answers, 'ProvenanceService', FrozenProvenance), \
             patch.object(provenance, 'ProvenanceService', FrozenProvenance), \
             patch.object(answers, 'external_client', provider), \
             patch.object(facade, 'external_client', provider):
            sync = service.answer('p', {'query': '退款允许申请什么？', 'generate': True})
            events = list(answers.stream_events(service, 'p', {'query': '退款允许申请什么？', 'generate': True}))
        self.assertEqual(sync['mode'], 'llm')
        self.assertTrue(any(event.startswith('event: done') for event in events))
        self.assertEqual(len(calls), 2)
        prompts = [json.loads(call['messages'][1]['content']) for call in calls]
        self.assertEqual(len(facts(prompts[0])), 2)
        self.assertEqual(prompts[0], prompts[1])


if __name__ == '__main__':
    unittest.main()
