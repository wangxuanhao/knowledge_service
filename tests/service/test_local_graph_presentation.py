"""Isolated presentation and bounded-query tests; no PostgreSQL session needed."""
from contextlib import nullcontext
from inspect import signature
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from knowledge_service.api import graph_sessions
from knowledge_service.services import graph_workspace
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.graph_neighborhood import present_neighborhood
from knowledge_service.services.explorer import Explorer
from knowledge_service.services.ontology import Ontology
from knowledge_service.services import structure_pending


class LocalGraphPresentationTests(unittest.TestCase):
    def test_attribute_read_is_scoped_to_returned_entities_and_limit(self):
        rows = [{'id': f'a{i}', 'kind': 'attribute'} for i in range(3)]
        database = Mock()
        database.execute.return_value.fetchall.return_value = rows
        repository = SimpleNamespace(_db=database, _lock=nullcontext(),
                                     get_project=Mock(), _record=lambda row: row)
        reads = ScopedReads(repository)
        reads._qualified = Mock(return_value=('qualified_scope', ['scope-value']))
        self.assertTrue(hasattr(reads, 'entity_attributes'), 'bounded entity attribute read is missing')
        scope = {'known_at': '2025-01-01', 'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '华东'}}
        result = reads.entity_attributes('project', scope, ['e1', 'e2'], limit=2)
        reads._qualified.assert_called_once_with('project', scope, kinds=['attribute'])
        sql, params = database.execute.call_args.args
        self.assertIn("r.payload::jsonb->>'subject_id'=ANY(?::text[])", sql)
        self.assertIn('LIMIT ?', sql)
        self.assertIn("ORDER BY array_position(?::text[],r.payload::jsonb->>'subject_id')", sql)
        self.assertEqual(params[-2:], [['e1', 'e2'], 3])
        self.assertEqual(len(result['records']), 2)
        self.assertTrue(result['truncated'])

    def test_empty_attribute_subjects_skip_database_read(self):
        reads = ScopedReads(SimpleNamespace(get_project=Mock(), _db=Mock()))
        self.assertTrue(hasattr(reads, 'entity_attributes'), 'bounded entity attribute read is missing')
        self.assertEqual(reads.entity_attributes('project', {}, []), {'records': [], 'truncated': False})
        reads.repo._db.execute.assert_not_called()

    def test_neighborhood_preserves_bound_release_hierarchy_without_reading_attributes(self):
        entity = {'id': 'e', 'kind': 'entity', 'type': 'urn:Merchant', 'text': '商户', 'ontology_id': 'release-old'}
        unknown = {'id': 'unknown', 'kind': 'entity', 'type': 'urn:Unknown', 'text': '未绑定'}
        attribute = {'id': 'a', 'kind': 'attribute', 'subject_id': 'e', 'type': 'urn:amount',
                     'value': 7, 'datatype': 'integer', 'version_id': 'a-v1', 'version': 1}
        relation = {'id': 'r', 'kind': 'relation', 'type': 'urn:knows', 'subject_id': 'e', 'object_id': 'unknown'}
        reads = Mock()
        reads.neighbors.return_value = {'records': [unknown, entity, relation], 'truncated': False, 'continuation': None}
        reads.entity_attributes.return_value = {'records': [attribute], 'truncated': True}
        service = SimpleNamespace(repository=object(), annotate_structure_pending=Mock(), ontology_labels=lambda project: {'urn:amount': '额度', 'urn:knows': '关联'},
            record_family=lambda project, row: {'label': '商户旧版', 'parents': [{'id': 'urn:Party', 'label': '交易方'}],
                'ancestors': [{'id': 'urn:Party', 'label': '交易方'}], 'ontology_id': 'release-old'} if row['id'] == 'e' else {})
        routes = []
        app = SimpleNamespace(include_router=lambda router: routes.extend(router.routes))
        with patch.object(graph_workspace, 'ScopedReads', return_value=reads):
            graph_sessions.install(app, service)
            endpoint = next(route.endpoint for route in routes if route.path.endswith('/graph-neighborhood'))
            result = endpoint('project', graph_sessions.GraphNeighborhood(seeds=['e'], known_at='2025-01-01'))
        by_id = {row['id']: row for row in result['records']}
        self.assertEqual(by_id['e'].get('class_label'), '商户旧版')
        self.assertEqual(by_id['e']['class_parents'][0]['id'], 'urn:Party')
        self.assertIsNone(by_id['unknown']['class_ontology_id'])
        self.assertEqual(set(by_id), {'e', 'unknown', 'r'})
        self.assertNotIn('attributes', by_id['e'])
        self.assertEqual(result['attribute_mode'], 'summary')
        self.assertEqual(result['attribute_display'], 'sidebar')
        self.assertEqual(result['attribute_count'], 0)
        self.assertEqual(result['attribute_budget'], 0)
        self.assertEqual(result['budget_semantics'], 'entities_relations_only')
        reads.entity_attributes.assert_not_called()

    def test_neighborhood_keeps_entity_and_relation_pending_warnings(self):
        ontology = Ontology('''@prefix owl: <http://www.w3.org/2002/07/owl#> .
            <urn:Known> a owl:Class . <urn:knows> a owl:ObjectProperty .
            <urn:amount> a owl:DatatypeProperty .''')
        marker = {'structure_pending': {'terms': ['urn:Retired']}}
        rows = [{'id': 'e', 'kind': 'entity', 'type': 'urn:Retired', 'text': '待建模实体', 'embedding': [1]},
                {'id': 'other', 'kind': 'entity', 'type': 'urn:Known', 'text': '正常实体'},
                {'id': 'r', 'kind': 'relation', 'type': 'urn:knows', 'subject_id': 'e', 'object_id': 'other', 'metadata': marker}]
        attribute = {'id': 'a', 'kind': 'attribute', 'type': 'urn:amount', 'subject_id': 'e',
                     'value': 1, 'datatype': 'integer', 'metadata': marker}
        annotated = []
        def annotate(project, records):
            annotated.extend(row['id'] for row in records)
            return structure_pending.annotate(ontology, records)
        service = SimpleNamespace(ontology_labels=lambda project: {}, record_family=lambda project, row: {},
                                  annotate_structure_pending=annotate)
        reads = Mock()
        reads.entity_attributes.return_value = {'records': [attribute], 'truncated': False}
        result = present_neighborhood(service, reads, 'p', {}, {'records': rows, 'truncated': False}, ['e'])
        by_id = {row['id']: row for row in result['records']}
        self.assertEqual(by_id['e'].get('structure_pending', {}).get('state'), 'pending')
        self.assertEqual(by_id['r']['structure_pending']['via'], ['e'])
        self.assertEqual(set(by_id), {'e', 'other', 'r'})
        self.assertNotIn('attributes', by_id['e'])
        self.assertEqual(set(annotated), {'e', 'other', 'r'})
        self.assertNotIn('embedding', by_id['e'])
        reads.entity_attributes.assert_not_called()

    def test_legacy_expanded_subgraph_uses_bounded_neighbors_and_shared_sidebar(self):
        entity = {'id': 'e', 'kind': 'entity', 'type': 'urn:Known', 'text': '主体'}
        reads = Mock()
        reads.neighbors.return_value = {'records': [entity], 'truncated': True,
                                        'continuation': {'reason': 'budget_exhausted'}}
        repository = Mock()
        repository.get_ontology.side_effect = KeyError('no ontology')
        repository.list_assertions.return_value = []
        repository.list_record_version_assertions.return_value = []
        service = SimpleNamespace(repository=repository, scoped=Mock(return_value=[entity]),
            annotate_structure_pending=Mock(), ontology_labels=lambda project: {},
            record_family=lambda project, row: {})
        scope = {'known_at': '2025-01-01', 'valid_at': '2024-01-01',
                 'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '华东'},
                 'entity_type': 'urn:Known', 'predicate': 'urn:related'}
        with patch('knowledge_service.repository.scoped_reads.ScopedReads', return_value=reads):
            result = Explorer(service).graph('p', scope, 'e', hops=5, attribute_mode='expanded')
        self.assertEqual([row['id'] for row in result['nodes']], ['e'])
        self.assertEqual(result['edges'], [])
        self.assertNotIn('attributes', result['nodes'][0])
        self.assertEqual(result['attribute_mode'], 'summary')
        self.assertEqual(result['attribute_display'], 'sidebar')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['continuation'], {'reason': 'budget_exhausted'})
        reads.neighbors.assert_called_once_with('p', {key: value for key, value in scope.items()
            if key not in {'entity_type', 'predicate'}}, ['e'], hops=5, direction='both',
            max_nodes=500, max_edges=1000, entity_type='urn:Known', predicate='urn:related')
        reads.entity_attributes.assert_not_called()
        service.scoped.assert_not_called()

    def test_whole_subgraph_reads_only_real_network_kinds(self):
        entity = {'id': 'e', 'kind': 'entity', 'type': 'urn:Known', 'text': '主体'}
        repository = Mock()
        repository.get_ontology.side_effect = KeyError('no ontology')
        repository.list_assertions.return_value = []
        repository.list_record_version_assertions.return_value = []
        service = SimpleNamespace(repository=repository, scoped=Mock(return_value=[entity]),
            annotate_structure_pending=Mock(), ontology_labels=lambda project: {},
            record_family=lambda project, row: {})
        result = Explorer(service).graph('p', {}, attribute_mode='summary')
        self.assertEqual([row['id'] for row in result['nodes']], ['e'])
        self.assertNotIn('attributes', result['nodes'][0])
        service.scoped.assert_called_once_with('p', {'kinds': ['entity', 'relation']})
        service.repository.list_assertions.assert_not_called()
        service.repository.list_record_version_assertions.assert_not_called()

    def test_seeded_subgraph_uses_authoritative_budget_even_with_ready_native_projection(self):
        entity = {'id': 'e', 'kind': 'entity', 'type': 'urn:Known', 'text': '主体'}
        reads = Mock()
        reads.neighbors.return_value = {'records': [entity], 'truncated': False, 'continuation': None}
        repository = Mock()
        repository.current_records_by_version_ids.return_value = [entity]
        projection = Mock()
        projection.expand.return_value = ['e-v1']
        service = SimpleNamespace(repository=repository, neo4j_projection=projection,
            scoped=Mock(), annotate_structure_pending=Mock(), ontology_labels=lambda project: {},
            record_family=lambda project, row: {})
        with patch('knowledge_service.repository.scoped_reads.ScopedReads', return_value=reads):
            result = Explorer(service).graph('p', {}, 'e')
        self.assertEqual(result['backend'], 'postgres_bfs')
        self.assertEqual(result['degraded_reason'], 'bounded_authority_neighborhood')
        reads.neighbors.assert_called_once()
        projection.expand.assert_not_called()
        service.scoped.assert_not_called()

    def test_seeded_subgraph_preserves_empty_legacy_type_controls(self):
        entity = {'id': 'e', 'kind': 'entity', 'type': 'urn:Known', 'text': '主体'}
        reads = Mock()
        reads.neighbors.return_value = {'records': [entity], 'truncated': False, 'continuation': None}
        service = SimpleNamespace(repository=Mock(), annotate_structure_pending=Mock(),
            ontology_labels=lambda project: {}, record_family=lambda project, row: {})
        with patch('knowledge_service.repository.scoped_reads.ScopedReads', return_value=reads):
            Explorer(service).graph('p', {'entity_type': '', 'predicate': ''}, 'e')
        self.assertIsNone(reads.neighbors.call_args.kwargs['entity_type'])
        self.assertIsNone(reads.neighbors.call_args.kwargs['predicate'])

    def test_native_graph_falls_back_for_source_qualified_identity_policy(self):
        ontology = Ontology('''@prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix ks: <https://knowledge.local/schema/> .
            <urn:Known> a owl:Class ; ks:identityPolicy "reference" .''')
        projection = Mock()
        projection.expand.return_value = ['v1']
        repository = Mock()
        repository.current_records_by_version_ids.return_value = [{'id': 'e'}]
        service = SimpleNamespace(repository=repository, neo4j_projection=projection,
                                  structure_pending_ontology=lambda project: ontology)
        result = Explorer(service)._native_graph_rows('p', {}, 'e', 1, 'summary')
        self.assertIsNone(result['rows'])
        self.assertEqual(result['degraded_reason'], 'source_contexts_unsupported')
        projection.expand.assert_not_called()

    def test_neighbors_apply_entity_and_relation_types_independently(self):
        database = Mock()
        database.execute.return_value.fetchall.return_value = []
        repository = SimpleNamespace(_db=database, _lock=nullcontext())
        reads = ScopedReads(repository)
        reads.by_ids = Mock(return_value=[{'id': 'e', 'kind': 'entity', 'version_id': 'e-v1'}])
        reads._qualified = Mock(return_value=('qualified_scope', ['scope-value']))
        self.assertIn('entity_type', signature(reads.neighbors).parameters)
        result = reads.neighbors('p', {'known_at': '2025-01-01'}, ['e'],
                                 entity_type='urn:Known', predicate='urn:related')
        self.assertEqual([row['id'] for row in result['records']], ['e'])
        self.assertEqual(reads.by_ids.call_args.args[1],
                         {'known_at': '2025-01-01', '_type_iris': ['urn:Known']})
        self.assertEqual(reads._qualified.call_args.args[1],
                         {'known_at': '2025-01-01', '_type_iris': ['urn:related']})
        self.assertEqual(reads._qualified.call_args.kwargs, {'kinds': ['relation'], 'keyset': True})


if __name__ == '__main__':
    unittest.main()
