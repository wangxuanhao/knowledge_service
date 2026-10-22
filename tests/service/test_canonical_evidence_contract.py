"""Pure regressions for exact evidence identity and read-only provenance."""
import copy
import hashlib
import unittest

from knowledge_service.services.evidence import resolve_assertion_evidence
from knowledge_service.services.provenance import ProvenanceService, _TraceProjection


class EvidenceRepository:
    def __init__(self, *, source_version='doc-v1', recorded_at='same'):
        self.document = {
            'id': 'doc', 'version_id': 'doc-v1', 'version': 1,
            'kind': 'document', 'text': 'prefix Alpha suffix',
            'recorded_at': 'same', 'metadata': {},
        }
        self.chunk = {
            'id': 'chunk', 'version_id': 'chunk-v1', 'kind': 'chunk',
            'source_id': 'doc', 'text': self.document['text'],
            'recorded_at': recorded_at,
            'metadata': {'start_char': 0, 'end_char': len(self.document['text'])},
        }
        if source_version is not None:
            self.chunk['metadata']['source_version_id'] = source_version
        self.assertion = {
            'id': 'assertion', 'status': 'accepted', 'kind': 'entity',
            'document_id': 'doc', 'document_version_id': 'doc-v1', 'chunk_id': 'chunk',
            'start_char': 7, 'end_char': 12, 'quote': 'Alpha',
            'source_hash': hashlib.sha256(self.document['text'].encode()).hexdigest(),
            'payload': {'text': 'Alpha', 'evidence': 'Alpha', 'evidence_status': 'exact'},
        }

    def get_assertion(self, project, assertion):
        return copy.deepcopy(self.assertion)

    def get_record_version(self, project, version):
        if version != self.document['version_id']:
            raise KeyError(version)
        return copy.deepcopy(self.document)

    def history(self, project, record):
        return [copy.deepcopy(self.chunk)]


class AnswerRepository:
    def __init__(self, schema=None):
        self.activity = {
            'id': 'answer', 'kind': 'answer', 'status': 'completed',
            'started_at': 'same', 'payload': {
                'run_id': 'retrieval', 'offered': [], 'cited': [],
                'warnings': [], 'answer': '', 'mode': None,
            },
        }
        if schema is not None:
            self.activity['payload']['graph_schema'] = schema
        else:
            self.activity['payload']['graphs'] = {}
        self.edges = [{'activity_id': 'answer', 'source_ref': 'answer:answer',
                       'target_ref': 'retrieval-run:retrieval', 'relation': 'used',
                       'ordinal': 0, 'payload': {}, 'created_at': 'same'}]
        self.writes = []

    def get_provenance_activity(self, project, activity):
        return copy.deepcopy(self.activity)

    def list_provenance_edges(self, project, *, activity_id):
        return copy.deepcopy(self.edges if activity_id == 'answer' else [])

    def migrate_legacy_answer_graph(self, project, **migration):
        self.writes.append(migration)
        self.activity['payload'] = copy.deepcopy(migration['answer_payload'])
        self.edges = copy.deepcopy(migration['answer_edges'])
        return copy.deepcopy(self.activity)


class FrozenReviewRepository(AnswerRepository):
    def __init__(self):
        super().__init__('edge-v1')
        self.activity['payload']['offered'] = ['E1']
        self.retrieval = {
            'id': 'retrieval', 'kind': 'retrieval', 'status': 'completed',
            'started_at': 'same', 'payload': {'query': 'fact', 'requested_mode': 'keyword',
                                            'active_modes': ['keyword']},
        }
        self.record = {'id': 'fact', 'version_id': 'fact-v1', 'version': 1,
                       'kind': 'entity', 'text': 'fact', 'recorded_at': 'same'}
        self.assertion = {'id': 'assertion', 'kind': 'entity', 'status': 'rejected',
                          'created_at': 'same', 'quote': 'fact'}
        self.review_events = [{'id': 'later-event', 'to_status': 'rejected'}]
        audit = {'answer_id': 'answer', 'citation': 'E1', 'rank': 1, 'score': 1.0,
                 'selected': True, 'warnings': []}
        self.edges += [
            {'source_ref': 'answer:answer#E1', 'relation': 'offered',
             'target_ref': 'record-version:fact-v1', 'ordinal': 1, 'payload': audit},
            {'source_ref': 'record-version:fact-v1', 'relation': 'supported-by',
             'target_ref': 'assertion:assertion', 'ordinal': 2,
             'payload': {'citation': 'E1', 'support_origin': 'manual'}},
            {'source_ref': 'assertion:assertion', 'relation': 'decided-by',
             'target_ref': 'review-event:frozen-event', 'ordinal': 3,
             'payload': {'citation': 'E1'}},
        ]
        self.considered = [{'source_ref': 'retrieval-run:retrieval', 'relation': 'considered',
                            'target_ref': 'record-version:fact-v1', 'ordinal': 0, 'payload': audit}]

    def get_provenance_activity(self, project, activity):
        return copy.deepcopy(self.activity if activity == 'answer' else self.retrieval)

    def list_provenance_edges(self, project, *, activity_id):
        return copy.deepcopy(self.edges if activity_id == 'answer' else self.considered)

    def records_by_version_ids(self, project, versions):
        return [copy.deepcopy(self.record)] if 'fact-v1' in versions else []

    def get_assertion(self, project, assertion):
        return copy.deepcopy(self.assertion)

    def list_assertion_events(self, project, assertion):
        return copy.deepcopy(self.review_events)


class CanonicalEvidenceContractTests(unittest.TestCase):
    def test_unversioned_chunk_is_unresolved_even_with_matching_identity_and_time(self):
        repo = EvidenceRepository(source_version=None)
        result = resolve_assertion_evidence(repo, 'project', 'assertion')
        self.assertEqual(result['location']['mode'], 'unlocated')
        self.assertFalse(result['integrity']['complete'])
        self.assertEqual(result['integrity']['warnings'][0]['code'], 'chunk_history_mismatch')

    def test_wrong_pinned_chunk_version_is_not_replaced_by_timestamp_match(self):
        repo = EvidenceRepository(source_version='another-document-version')
        result = resolve_assertion_evidence(repo, 'project', 'assertion')
        self.assertEqual(result['location']['mode'], 'unlocated')
        self.assertEqual(result['chunk']['text'], None)

    def test_exact_chunk_source_version_does_not_depend_on_recording_time(self):
        repo = EvidenceRepository(recorded_at='later')
        result = resolve_assertion_evidence(repo, 'project', 'assertion')
        self.assertEqual(result['location']['highlight'], 'Alpha')
        self.assertTrue(result['integrity']['complete'])

    def test_old_chunk_anchor_does_not_reconstruct_a_smaller_quote(self):
        repo = EvidenceRepository()
        repo.assertion.update(start_char=0, end_char=len(repo.document['text']),
                              quote=repo.document['text'], payload={'text': 'Alpha'})
        result = resolve_assertion_evidence(repo, 'project', 'assertion')
        self.assertEqual(result['location']['mode'], 'chunk')
        self.assertEqual(result['location']['highlight'], '')
        self.assertIsNone(result['location']['start_char'])

    def test_current_whole_chunk_exact_quote_remains_exact(self):
        repo = EvidenceRepository()
        text = repo.document['text']
        repo.assertion.update(start_char=0, end_char=len(text), quote=text,
                              payload={'evidence': text, 'evidence_status': 'exact'})
        result = resolve_assertion_evidence(repo, 'project', 'assertion')
        self.assertEqual(result['location']['mode'], 'exact')
        self.assertEqual(result['location']['highlight'], text)

    def test_reading_old_answer_graph_never_migrates_or_mutates_it(self):
        repo = AnswerRepository()
        before = copy.deepcopy((repo.activity, repo.edges))
        try:
            ProvenanceService(repo).trace_answer_evidence('project', 'answer', 'E1')
        except (ValueError, KeyError):
            pass
        self.assertEqual((repo.activity, repo.edges), before)
        self.assertEqual(repo.writes, [])

    def test_unsupported_answer_schemas_are_rejected_explicitly(self):
        for schema in (None, 'edge-v0', 'edge-v2', 1):
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ValueError, '不支持的答案溯源图格式'):
                    ProvenanceService(AnswerRepository(schema)).trace_answer_evidence(
                        'project', 'answer', 'E1')

    def test_malformed_edge_v1_payloads_are_rejected_explicitly(self):
        for field, value in (
            ('graphs', {}), ('run_id', ''), ('offered', 'E1'),
            ('offered', ['E1', 'E1']), ('offered', [{}]),
            ('cited', ['E1']), ('warnings', {}), ('warnings', [{}]),
            ('answer', []), ('mode', 'unknown'),
        ):
            with self.subTest(field=field, value=value):
                repo = AnswerRepository('edge-v1')
                repo.activity['payload'][field] = value
                before = copy.deepcopy(repo.activity)
                with self.assertRaisesRegex(ValueError, '答案溯源图 payload 格式无效'):
                    ProvenanceService(repo).trace_answer_evidence('project', 'answer', 'E1')
                self.assertEqual(repo.activity, before)
                self.assertEqual(repo.writes, [])

    def test_deleted_frozen_source_is_diagnosed_without_current_record_fallback(self):
        repo = EvidenceRepository()
        projection = _TraceProjection(repo, 'project', [{
            'source_ref': 'answer:answer#E1', 'relation': 'offered',
            'target_ref': 'record-version:missing-version', 'payload': {},
        }])
        self.assertEqual(projection.nodes(), [])
        self.assertEqual(projection.warnings[0]['code'], 'frozen_source_missing')

    def test_missing_frozen_review_event_keeps_captured_status_unknown(self):
        repo = FrozenReviewRepository()
        before = copy.deepcopy((repo.activity, repo.edges, repo.review_events))

        trace = ProvenanceService(repo).trace_answer_evidence('project', 'answer', 'E1')

        self.assertFalse(trace['integrity']['complete'])
        codes = {warning['code'] for warning in trace['integrity']['warnings']}
        self.assertIn('assertion_event_missing', codes)
        self.assertIn('frozen_source_missing', codes)
        assertion = next(node for node in trace['nodes'] if node['type'] == 'assertion')
        self.assertIsNone(assertion['status'])
        self.assertIsNone(assertion['details']['status_at_capture'])
        self.assertEqual(assertion['details']['current_status'], 'rejected')
        self.assertFalse(any(node['type'] == 'review_event' for node in trace['nodes']))
        self.assertEqual((repo.activity, repo.edges, repo.review_events), before)
        self.assertEqual(repo.writes, [])

    def test_invalid_frozen_source_span_does_not_invent_a_document_excerpt(self):
        repo = EvidenceRepository()
        occurrence = 'source-occurrence:answer#E1:1'
        edges = [
            {'source_ref': 'chunk-version:chunk-v1', 'relation': 'sourced-from',
             'target_ref': occurrence, 'payload': {'start_char': -1, 'end_char': 5000}},
            {'source_ref': occurrence, 'relation': 'sourced-from',
             'target_ref': 'document-version:doc-v1', 'payload': {}},
        ]
        projection = _TraceProjection(repo, 'project', edges)
        node = projection._occurrence(occurrence)
        self.assertIsNone(node['details']['start_char'])
        self.assertIsNone(node['details']['end_char'])
        self.assertEqual(node['details']['highlight'], '')
        self.assertEqual(projection.warnings[0]['code'], 'source_span_invalid')


if __name__ == '__main__':
    unittest.main()
