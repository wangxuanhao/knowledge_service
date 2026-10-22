"""Isolated presentation and bounded-query tests; no PostgreSQL session needed."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from knowledge_service.api import graph_sessions
from knowledge_service.services import graph_workspace
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.graph_neighborhood import present_neighborhood
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

    def test_neighborhood_preserves_bound_release_hierarchy_and_formal_attribute_identity(self):
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
        self.assertEqual(by_id['e']['attributes'][0]['values'][0]['record_id'], 'a')
        virtual = [row for row in result['records'] if row['kind'] == 'attribute_value']
        self.assertEqual(len(virtual), 1)
        self.assertEqual(virtual[0]['record_id'], 'a')
        self.assertEqual(virtual[0]['version_id'], 'a-v1')
        self.assertTrue(result['attributes_truncated'])
        self.assertEqual(result['attribute_count'], 1)
        self.assertEqual(result['attribute_budget'], 200)
        self.assertEqual(result['budget_semantics'], 'entities_relations_plus_separate_attributes')
        reads.entity_attributes.assert_called_once()
        self.assertEqual(reads.entity_attributes.call_args.args[1]['known_at'], '2025-01-01')
        self.assertEqual(reads.entity_attributes.call_args.args[2], ['e', 'unknown'])

    def test_neighborhood_keeps_pending_warnings_and_endpoint_derivation_on_formal_attributes(self):
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
        self.assertEqual(by_id['e']['attributes'][0]['values'][0]['structure_pending']['via'], ['e'])
        self.assertEqual(set(annotated), {'e', 'other', 'r', 'a'})
        self.assertNotIn('embedding', by_id['e'])


if __name__ == '__main__':
    unittest.main()
