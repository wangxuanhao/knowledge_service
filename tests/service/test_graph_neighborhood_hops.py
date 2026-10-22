"""Run the real bounded traversal and input contract without a database session."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from knowledge_service.api.graph_sessions import GraphNeighborhood
from knowledge_service.repository.scoped_reads import FIELDS, ScopedReads
from knowledge_service.services.explorer import Explorer


class ScopedChain:
    """Supply scoped rows while exercising neighbors' frontier, paths and budgets.

    SQL qualification remains production code. The tiny query harness checks the
    full compiled scope, then evaluates only the bounded frontier predicate on a
    fixture's already-qualified relation rows. It does not emulate PostgreSQL.
    """

    def __init__(self, length=12):
        self.project = 'selected-project'
        self.scope = {
            'known_at': '2025-01-01T00:00:00Z',
            'valid_at': '2024-12-01T00:00:00Z',
            'ontology_scope': 'ids', 'ontology_ids': ['release-old'],
            'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '华东'},
        }
        self.entities = {
            f'n{i}': {'id': f'n{i}', 'kind': 'entity', 'text': f'实体{i}', 'type': 'urn:Entity',
                      'version_id': f'n{i}-v1'}
            for i in range(length + 1)
        }
        self.relations = [
            {'id': f'r{i:02}', 'kind': 'relation', 'text': '关联', 'type': 'urn:related',
             'subject_id': f'n{i - 1}', 'object_id': f'n{i}', 'version_id': f'r{i}-v1'}
            for i in range(1, length + 1)
        ]
        self.queries = []
        self.entity_reads = []
        repository = SimpleNamespace(_db=self, _lock=nullcontext(), _record=dict)
        self.reads = ScopedReads(repository)
        self.reads.by_ids = self.by_ids

    def by_ids(self, project, scope, ids, *, kinds=None):
        assert project == self.project
        assert scope == self.scope
        assert kinds == ['entity']
        self.entity_reads.append(list(ids))
        return [self.entities[id] for id in sorted(ids) if id in self.entities]

    def execute(self, sql, params):
        qualified, scope_params = self.reads._qualified(self.project, self.scope, kinds=['relation'], keyset=True)
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        prefix = f'SELECT * FROM (SELECT {fields} FROM record_versions r WHERE {qualified} AND ('
        suffix = ') OFFSET 0) adjacent ORDER BY id,version LIMIT ?'
        assert sql.startswith(prefix)
        assert sql.endswith(suffix)
        predicate = sql[len(prefix):-len(suffix)]
        columns = [column for column in ('subject_id', 'object_id')
                   if f"r.payload::jsonb->>'{column}'=ANY(?::text[])" in predicate]
        assert columns
        assert params[:len(scope_params)] == scope_params
        assert len(params) == len(scope_params) + len(columns) + 1
        frontiers, limit = params[len(scope_params):-1], params[-1]
        assert 1 <= limit <= 10001
        rows = [row for row in self.relations
                if any(row[column] in ids for column, ids in zip(columns, frontiers))][:limit]
        self.queries.append({'frontiers': frontiers, 'limit': limit, 'rows': rows})
        return SimpleNamespace(fetchall=lambda: rows)

    def neighborhood(self, **options):
        return self.reads.neighbors(self.project, self.scope, ['n0', 'out-of-scope-seed'], **options)


class GraphNeighborhoodHopsTests(unittest.TestCase):
    def test_four_hops_reaches_fourth_endpoint_with_real_path(self):
        chain = ScopedChain()
        result = chain.neighborhood(hops=4)
        self.assertEqual({row['id'] for row in result['records']},
                         {'n0', 'n1', 'n2', 'n3', 'n4', 'r01', 'r02', 'r03', 'r04'})
        path = next(path for path in result['reasoning_paths'] if path['end'] == 'n4')
        self.assertEqual([step['relation_id'] for step in path['steps']], ['r01', 'r02', 'r03', 'r04'])
        self.assertEqual(path['steps'][-1]['to_version_id'], 'n4-v1')
        self.assertEqual(len(chain.queries), 4)
        self.assertFalse(result['truncated'])
        self.assertIsNone(result['continuation'])

    def test_zero_hops_returns_only_the_scoped_seed_without_relation_queries(self):
        chain = ScopedChain()
        result = chain.neighborhood(hops=0)
        self.assertEqual([row['id'] for row in result['records']], ['n0'])
        self.assertEqual(result['reasoning_paths'], [])
        self.assertFalse(result['truncated'])
        self.assertEqual(chain.queries, [])

    def test_ten_hops_reaches_tenth_endpoint_without_reading_eleventh(self):
        chain = ScopedChain()
        result = chain.neighborhood(hops=10)
        self.assertEqual(len(result['records']), 21)
        self.assertIn('n10', {row['id'] for row in result['records']})
        self.assertNotIn('n11', {row['id'] for row in result['records']})
        path = next(path for path in result['reasoning_paths'] if path['end'] == 'n10')
        self.assertEqual(len(path['steps']), 10)
        self.assertEqual(len(chain.queries), 10)

    def test_node_budget_still_truncates_extended_traversal(self):
        chain = ScopedChain()
        result = chain.neighborhood(hops=10, max_nodes=4, max_edges=20)
        self.assertEqual({row['id'] for row in result['records'] if row['kind'] == 'entity'},
                         {'n0', 'n1', 'n2', 'n3'})
        self.assertEqual(len([row for row in result['records'] if row['kind'] == 'relation']), 3)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['continuation'], {
            'seeds': ['n0'], 'hops': 10, 'direction': 'forward',
            'max_nodes': 4, 'max_edges': 20, 'reason': 'budget_exhausted',
        })

    def test_edge_budget_still_limits_each_query_and_reports_truncation(self):
        chain = ScopedChain()
        result = chain.neighborhood(hops=10, max_nodes=20, max_edges=2)
        self.assertEqual({row['id'] for row in result['records']}, {'n0', 'n1', 'n2', 'r01', 'r02'})
        self.assertEqual([query['limit'] for query in chain.queries], [3, 2, 1])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['continuation']['hops'], 10)
        self.assertEqual(result['continuation']['reason'], 'budget_exhausted')

    def test_direction_remains_backward_and_both_beyond_three_hops(self):
        for direction in ('backward', 'both'):
            with self.subTest(direction=direction):
                chain = ScopedChain()
                result = chain.reads.neighbors(chain.project, chain.scope, ['n4'],
                                              hops=4, direction=direction)
                path = next(path for path in result['reasoning_paths'] if path['end'] == 'n0')
                self.assertEqual(len(path['steps']), 4)
                self.assertTrue(all(step['direction'] == 'backward' for step in path['steps']))

    def test_repository_rejects_invalid_hops_before_any_read(self):
        for hops in (-1, 11, 1.5, 3.0, '4', True, None):
            with self.subTest(hops=hops):
                chain = ScopedChain()
                with self.assertRaisesRegex(ValueError, '邻域跳数必须是 0 到 10 之间的整数'):
                    chain.neighborhood(hops=hops)
                self.assertEqual(chain.entity_reads, [])
                self.assertEqual(chain.queries, [])

    def test_request_accepts_entire_zero_to_ten_integer_range(self):
        for hops in range(11):
            with self.subTest(hops=hops):
                self.assertEqual(GraphNeighborhood(seeds=['n0'], hops=hops).hops, hops)

    def test_request_rejects_invalid_hops_with_precise_field_errors(self):
        cases = [(-1, 'greater_than_equal', 'greater than or equal to 0'),
                 (11, 'less_than_equal', 'less than or equal to 10'),
                 (1.5, 'int_type', 'valid integer'), (3.0, 'int_type', 'valid integer'),
                 ('4', 'int_type', 'valid integer'), (True, 'int_type', 'valid integer'),
                 (None, 'int_type', 'valid integer')]
        for hops, code, message in cases:
            with self.subTest(hops=hops):
                with self.assertRaises(ValidationError) as caught:
                    GraphNeighborhood(seeds=['n0'], hops=hops)
                error = caught.exception.errors()[0]
                self.assertEqual(error['loc'], ('hops',))
                self.assertEqual(error['type'], code)
                self.assertIn(message, error['msg'])


class BoundedMindmapTests(unittest.TestCase):
    def mindmap(self, chain, root_id='n0', depth=3, scope=None):
        explorer = Explorer(SimpleNamespace(repository=chain.reads.repo))
        explorer.graph = Mock(side_effect=AssertionError('mindmap must not use whole-corpus graph()'))
        with patch('knowledge_service.repository.scoped_reads.ScopedReads', return_value=chain.reads):
            return explorer.mindmap(chain.project, chain.scope if scope is None else scope,
                                    root_id, depth)

    @staticmethod
    def flatten(tree):
        return [tree, *(node for child in tree['children'] for node in BoundedMindmapTests.flatten(child))]

    def test_five_depth_mindmap_uses_bounded_scoped_neighbors_and_strips_controls(self):
        chain = ScopedChain()
        scope = {**chain.scope, 'root_id': 'n0', 'depth': 5}
        result = self.mindmap(chain, depth=5, scope=scope)
        self.assertEqual([node['id'] for node in self.flatten(result['tree'])],
                         ['n0', 'n1', 'n2', 'n3', 'n4', 'n5'])
        self.assertEqual(len(chain.queries), 5)
        self.assertEqual([query['limit'] for query in chain.queries], [1001, 1000, 999, 998, 997])
        self.assertTrue(all(len(query['frontiers']) == 2 for query in chain.queries))
        self.assertFalse(result['truncated'])
        self.assertFalse(result['neighborhood_truncated'])
        self.assertFalse(result['tree_truncated'])
        self.assertEqual(result['budgets'], {'max_nodes': 500, 'max_edges': 1000,
                                           'max_tree_nodes': 500, 'max_children': 40})

    def test_zero_depth_mindmap_keeps_root_and_skips_relations(self):
        chain = ScopedChain()
        result = self.mindmap(chain, depth=0)
        self.assertEqual(result['tree']['id'], 'n0')
        self.assertEqual(result['tree']['children'], [])
        self.assertEqual(chain.queries, [])
        self.assertFalse(result['truncated'])

    def test_missing_or_out_of_scope_root_is_a_clear_value_error(self):
        for root in ('missing', 'out-of-scope-seed'):
            with self.subTest(root=root):
                with self.assertRaisesRegex(ValueError, '脑图根实体不存在或不在当前范围内'):
                    self.mindmap(ScopedChain(), root_id=root)

    def test_invalid_depth_is_rejected_before_reading(self):
        for depth in (-1, 6, 2.5, '3', True):
            with self.subTest(depth=depth):
                chain = ScopedChain()
                with self.assertRaisesRegex(ValueError, '脑图深度必须是 0 到 5 之间的整数'):
                    self.mindmap(chain, depth=depth)
                self.assertEqual(chain.entity_reads, [])
                self.assertEqual(chain.queries, [])

    def test_tree_child_limit_reports_omitted_qualified_children(self):
        chain = ScopedChain(length=50)
        for index, row in enumerate(chain.relations, 1):
            row['subject_id'], row['object_id'] = 'n0', f'n{index}'
        result = self.mindmap(chain, depth=1)
        self.assertEqual(len(result['tree']['children']), 40)
        self.assertEqual(len(self.flatten(result['tree'])), 41)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['tree_truncated'])
        self.assertFalse(result['neighborhood_truncated'])

    def test_neighborhood_node_budget_reports_truncation_at_five_hundred(self):
        chain = ScopedChain(length=550)
        for index, row in enumerate(chain.relations, 1):
            row['subject_id'], row['object_id'] = 'n0', f'n{index}'
        result = self.mindmap(chain, depth=1)
        self.assertTrue(result['neighborhood_truncated'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['continuation']['reason'], 'budget_exhausted')
        self.assertEqual(result['continuation']['max_nodes'], 500)
        self.assertEqual(result['continuation']['max_edges'], 1000)

    def test_tree_node_budget_and_cycle_guards_remain_bounded(self):
        chain = ScopedChain(length=25)
        chain.relations = [
            {'id': f'r-{start:02}-{end:02}', 'kind': 'relation', 'type': 'urn:related',
             'text': '关联', 'subject_id': f'n{start}', 'object_id': f'n{end}',
             'version_id': f'r-{start}-{end}-v1'}
            for start in range(26) for end in range(start + 1, 26)
        ]
        result = self.mindmap(chain, depth=5)
        self.assertEqual(len(self.flatten(result['tree'])), 500)
        self.assertTrue(result['tree_truncated'])

        def check_path(node, ancestors):
            self.assertNotIn(node['id'], ancestors)
            for child in node['children']:
                check_path(child, ancestors | {node['id']})
        check_path(result['tree'], set())


if __name__ == '__main__':
    unittest.main()
