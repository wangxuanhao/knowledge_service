"""Frozen QA evidence keeps exact-version retrieval attribution. No I/O."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services import answers


def entity(record_id, version_id, **fields):
    return {'id': record_id, 'version_id': version_id, 'kind': 'entity',
            'type': 'urn:Platform', 'text': record_id, 'source_scope': 'all',
            'metadata': {'applicability': {'platforms': {'mode': 'all'}}}, **fields}


class QaExpansionChannelsTest(TestCase):
    def context(self, *, grouped, graph_enabled, channel='ontology_entity_type',
                graph_version='seed-v1', graph_id='seed'):
        self.ranked = entity('seed', 'seed-v1', retrieval_channel=channel,
            score=0.7, keyword_score=4.0, rrf_score=0.03,
            backend_ranks={'query': 2, 'ontology': 1}, text='ranked text',
            metadata={'applicability': {'platforms': {'mode': 'all'}}, 'origin': 'retrieval'})
        self.authority = entity(graph_id, graph_version, text='authority text',
            metadata={'applicability': {'platforms': {'mode': 'all'}}, 'origin': 'authority'},
            valid_from='2025-01-01', score=99)
        neighbor = entity('neighbor', 'neighbor-v1', text='graph neighbor')
        result = {'hits': [deepcopy(self.ranked)], 'source_scope': 'all',
                  'active_mode': 'hybrid', '_backend': 'milvus',
                  'ontology_expansion': {'used_hits': 1}}
        if grouped:
            result['platform_groups'] = [{'hits': [deepcopy(self.ranked)],
                'source_scope': 'all', 'active_mode': 'hybrid'}]
        service = SimpleNamespace(search=lambda *_: result,
            repository=SimpleNamespace(_db=object()),
            encoder=SimpleNamespace(identity='unit', semantic=True),
            annotate_structure_pending=lambda *_: None)
        graph = {'records': [self.authority, neighbor], 'reasoning_paths': [],
                 'truncated': False, 'continuation': None}
        with patch.object(ScopedReads, 'neighbors', return_value=deepcopy(graph)) as neighbors:
            context = answers._retrieved_question_context(service, 'p', {
                'query': 'seed', 'retrieval_mode': 'hybrid', 'graph_context': graph_enabled,
                'hops': 2, 'ontology_expansion': True})
        self.assertEqual(neighbors.call_count, int(graph_enabled))
        return context

    def test_same_version_graph_rows_keep_rank_and_channel_with_authority_fields(self):
        for grouped in (False, True):
            for graph_enabled in (False, True):
                for channel in ('ontology_entity_type', 'ontology_type'):
                    with self.subTest(grouped=grouped, graph=graph_enabled, channel=channel):
                        context = self.context(grouped=grouped, graph_enabled=graph_enabled, channel=channel)
                        seed = next(row for row in context['evidence_rows'] if row['id'] == 'seed')
                        for field in ('retrieval_channel', 'score', 'keyword_score', 'rrf_score', 'backend_ranks'):
                            self.assertEqual(seed.get(field), self.ranked[field], field)
                        self.assertEqual(seed['text'], 'authority text' if graph_enabled else 'ranked text')
                        self.assertEqual(seed['metadata']['origin'], 'authority' if graph_enabled else 'retrieval')
                        self.assertEqual(seed['source_scope'], 'all')
                        self.assertEqual(seed.get('valid_from'), '2025-01-01' if graph_enabled else None)
                        self.assertEqual(context['ontology_expansion']['used_hits'], 1)
                        self.assertEqual(context['hits'][0]['retrieval_channel'], channel)
                        for row in context['evidence_rows']:
                            if row['id'] == 'neighbor':
                                self.assertNotIn('retrieval_channel', row)
                        if grouped:
                            self.assertEqual(context['platform_groups'][0]['evidence_rows'], context['evidence_rows'])

    def test_different_versions_do_not_inherit_the_seed_channel_or_rank(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                context = self.context(grouped=grouped, graph_enabled=True, graph_version='seed-v2')
                authority = next(row for row in context['evidence_rows'] if row['version_id'] == 'seed-v2')
                self.assertNotIn('retrieval_channel', authority)
                self.assertEqual(authority['score'], 99)
                self.assertNotIn('backend_ranks', authority)
                self.assertEqual(authority['metadata']['origin'], 'authority')
                if grouped:
                    seed = next(row for row in context['evidence_rows'] if row['version_id'] == 'seed-v1')
                    self.assertEqual(seed['retrieval_channel'], 'ontology_entity_type')

    def test_matching_version_on_another_record_does_not_inherit_seed_metadata(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                context = self.context(grouped=grouped, graph_enabled=True, graph_id='other-record')
                authority = next(row for row in context['evidence_rows'] if row['id'] == 'other-record')
                self.assertNotIn('retrieval_channel', authority)
                self.assertEqual(authority['score'], 99)
                self.assertNotIn('backend_ranks', authority)
