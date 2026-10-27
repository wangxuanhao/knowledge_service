"""Entity mentions seed only published classes, inside authoritative read scope."""
import unittest
from contextlib import nullcontext
from unittest.mock import Mock

from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.ontology import Ontology, match_query_expansion


PARTNER = 'urn:entity-expansion:Partner'
PLATFORM = 'urn:entity-expansion:Platform'
SHOP = 'urn:entity-expansion:Shop'
OTHER = 'urn:entity-expansion:Other'
TURTLE = f'''
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{PARTNER}> a owl:Class; rdfs:label "合作方"@zh .
<{PLATFORM}> a owl:Class; rdfs:label "平台"@zh; rdfs:subClassOf <{PARTNER}> .
<{SHOP}> a owl:Class; rdfs:label "商户"@zh; rdfs:subClassOf <{PLATFORM}> .
<{OTHER}> a owl:Class; rdfs:label "组织"@zh .
'''


def _expand(query, entities=(), *, index=None, max_terms=8):
    from knowledge_service.services.entity_ontology_expansion import expand_entity_query
    return expand_entity_query(index or Ontology(TURTLE).expansion_index(), query,
                               entities, max_terms=max_terms)


def _entity(id='meituan', *, text='美团', type=PLATFORM, matched_name=None, match_kind='name'):
    return {'id': id, 'version_id': id + ':1', 'text': text, 'type': type,
            'matched_name': matched_name or text, 'match_kind': match_kind}


class EntityOntologyExpansionTests(unittest.TestCase):
    def test_entity_type_expands_to_published_descendants_and_retains_path(self):
        expansion = _expand('美团有哪些规则', [_entity()])
        self.assertEqual(expansion['terms'], [PLATFORM])
        self.assertEqual(expansion['subclasses'], [SHOP])
        self.assertEqual(expansion['type_iris'], [PLATFORM, SHOP])
        self.assertEqual(expansion['literal_terms'], [])
        self.assertEqual(expansion['entity_terms'], [PLATFORM])
        self.assertEqual(expansion['entity_matches'], [{**_entity(), 'type_label': '平台'}])
        self.assertTrue(expansion['has_hierarchy'])
        self.assertTrue(expansion['matched'])

    def test_alias_path_resolves_an_exact_class_surface(self):
        entity = _entity(text='美团公司', type='平台', matched_name='Meituan', match_kind='alias')
        expansion = _expand('Meituan 的规则', [entity])
        self.assertEqual(expansion['entity_matches'], [{**entity, 'type': PLATFORM, 'type_label': '平台'}])

    def test_literal_and_entity_seeds_are_unioned_without_duplicate_descendants(self):
        expansion = _expand('美团合作方规则', [_entity()])
        self.assertEqual(expansion['literal_terms'], [PARTNER])
        self.assertEqual(expansion['entity_terms'], [PLATFORM])
        self.assertEqual(expansion['terms'], sorted([PARTNER, PLATFORM]))
        self.assertEqual(expansion['subclasses'], [SHOP])
        self.assertEqual(len(expansion['type_iris']), len(set(expansion['type_iris'])))

    def test_unknown_generic_and_ambiguous_types_do_not_seed_expansion(self):
        index = Ontology(TURTLE).expansion_index()
        index['surfaces'].extend([('共享类型', PLATFORM), ('共享类型', OTHER)])
        index['labels'][OTHER] = '平台'  # labels participate in ambiguity checks too.
        for value in ['Entity', 'unknown', '平台', '共享类型', 'http://unpublished/type']:
            with self.subTest(type=value):
                expansion = _expand('美团规则', [_entity(type=value)], index=index)
                self.assertEqual(expansion['entity_terms'], [])
                self.assertEqual(expansion['entity_matches'], [])
                self.assertFalse(expansion['matched'])

    def test_known_iri_is_authoritative_even_if_its_label_is_ambiguous(self):
        index = Ontology(TURTLE).expansion_index()
        index['surfaces'].append(('平台', OTHER))
        self.assertEqual(_expand('美团规则', [_entity()], index=index)['entity_terms'], [PLATFORM])

    def test_inactive_class_referenced_as_an_ancestor_cannot_seed_expansion(self):
        inactive = 'urn:entity-expansion:Inactive'
        index = Ontology(TURTLE + f'''
            <{inactive}> a owl:Class; rdfs:label "停用类"; owl:deprecated true .
            <{PLATFORM}> rdfs:subClassOf <{inactive}> .
        ''').expansion_index()
        self.assertIn(inactive, index['ancestors'][PLATFORM])
        self.assertEqual(_expand('美团规则', [_entity(type=inactive)], index=index)['entity_terms'], [])

    def test_duplicate_names_keep_both_explicit_entity_paths(self):
        expansion = _expand('美团规则', [_entity('b', type=OTHER), _entity('a')])
        self.assertEqual([row['id'] for row in expansion['entity_matches']], ['a', 'b'])
        self.assertEqual(expansion['entity_terms'], sorted([OTHER, PLATFORM]))

    def test_no_entity_keeps_every_legacy_expansion_field_identical(self):
        index = Ontology(TURTLE).expansion_index()
        for query in ['合作方有哪些', '平台规则', '无关问题', '', None]:
            with self.subTest(query=query):
                expected = match_query_expansion(index, query)
                actual = _expand(query, index=index)
                self.assertEqual({key: actual[key] for key in expected}, expected)
                self.assertEqual(actual['literal_terms'], expected['terms'])

    def test_seed_budget_preserves_literal_priority_and_is_input_order_independent(self):
        entities = [_entity('z', type=OTHER), _entity('a')]
        forward = _expand('合作方美团规则', entities, max_terms=2)
        reverse = _expand('合作方美团规则', reversed(entities), max_terms=2)
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward['terms']), 2)
        self.assertIn(PARTNER, forward['terms'])
        self.assertEqual({row['type'] for row in forward['entity_matches']}, set(forward['entity_terms']))
        self.assertEqual(len(forward['entity_matches']), 1)
        limited = _expand('合作方美团规则', entities, max_terms=1)
        self.assertEqual(limited['entity_matches'], [])
        self.assertEqual(limited['entity_terms'], [])

    def test_invalid_seed_budget_is_rejected(self):
        for value in [0, -1, True, 9, 1.5]:
            with self.subTest(limit=value), self.assertRaises(ValueError):
                _expand('美团', [_entity()], max_terms=value)


class MentionedEntitiesValidationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.repo.independent_read_view = lambda: nullcontext(self.repo)
        self.reads = ScopedReads(self.repo)

    def test_empty_query_does_not_decode_or_execute(self):
        self.assertEqual(self.reads.mentioned_entities('p', {}, '  '), [])
        self.repo._db.execute.assert_not_called()
        self.repo._record.assert_not_called()

    def test_invalid_query_and_limit_are_rejected(self):
        for query, limit in [(None, 8), (42, 8), ('美团', 0), ('美团', 9), ('美团', True)]:
            with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                self.reads.mentioned_entities('p', {}, query, limit=limit)


def _record(repo, project, id, text='美团', **kwargs):
    return repo.put_record(project, {'id': id, 'kind': 'entity', 'text': text,
                                     'type': PLATFORM, 'metadata': {}, **kwargs})


def test_mentioned_entities_names_aliases_boundaries_and_longest_match(tmp_path):
    repo = Repository(tmp_path / 'entity-mentions')
    project = repo.create_project('实体命中')['id']
    _record(repo, project, 'name', '美团公司')
    _record(repo, project, 'short', '美团')
    _record(repo, project, 'alias', '另一家公司', metadata={'aliases': ['Meituan', 'M', 42]})
    _record(repo, project, 'ascii', 'Acme', metadata={'aliases': ['Acme']})
    _record(repo, project, 'one-char', '团')
    reads = ScopedReads(repo)
    matches = reads.mentioned_entities(project, {}, '美团公司、MEITUAN 和 Acme 的规则')
    assert [(row['id'], row['matched_name'], row['match_kind']) for row in matches] == [
        ('alias', 'Meituan', 'alias'), ('ascii', 'Acme', 'name'), ('name', '美团公司', 'name')]
    assert reads.mentioned_entities(project, {}, 'AcmeCorp 的规则') == []
    assert reads.mentioned_entities(project, {}, 'XMeituanY 的规则') == []
    assert reads.mentioned_entities(project, {}, '团') == []
    assert [row['id'] for row in reads.mentioned_entities(project, {}, 'AcmeCorp 和 Acme')] == ['ascii']
    assert [row['id'] for row in reads.mentioned_entities(project, {}, '美团和美团公司')] == ['name', 'short']


def test_mentioned_entities_preserve_ambiguous_names_and_decode_at_most_limit(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'entity-mention-limit')
    project = repo.create_project('有界实体命中')['id']
    for number in range(12):
        _record(repo, project, f'entity-{number:02d}')
    decoded = []
    original = repo._record
    monkeypatch.setattr(repo, '_record', lambda row: (decoded.append(row['id']), original(row))[1])
    matches = ScopedReads(repo).mentioned_entities(project, {}, '美团', limit=8)
    assert [row['id'] for row in matches] == [f'entity-{number:02d}' for number in range(8)]
    assert decoded == [row['id'] for row in matches]


def test_mentioned_entities_respect_scope_visibility_and_history(tmp_path):
    repo = Repository(tmp_path / 'entity-mention-scope')
    project = repo.create_project('实体范围')['id']
    other_project = repo.create_project('另一个项目')['id']
    ontology = repo.bootstrap_ontology(project, TURTLE, {})['id']
    other_ontology = 'other-published-version'
    metadata = {'region': '北京',
                'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}}
    common = {'ontology_id': ontology, 'metadata': metadata}
    old = _record(repo, project, 'visible', **common)
    _record(repo, project, 'deleted', **{**common, 'metadata': {**metadata, '_deleted': True}})
    _record(repo, project, 'audit', **{**common, 'metadata': {**metadata, '_audit': True}})
    _record(repo, project, 'expired', **common, valid_until='2025-01-01T00:00:00Z')
    _record(repo, project, 'filter', **{**common, 'metadata': {'region': '上海'}})
    _record(repo, project, 'platform', **{**common, 'metadata': {**metadata,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['淘宝']}}}})
    _record(repo, project, 'ontology', **{**common, 'ontology_id': other_ontology})
    _record(repo, other_project, 'other-project')
    scope = {'valid_at': '2026-10-27T00:00:00Z', 'ontology_scope': 'ids',
             'ontology_ids': [ontology], 'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '北京'},
             'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}}
    reads = ScopedReads(repo)
    assert [row['id'] for row in reads.mentioned_entities(project, scope, '美团')] == ['visible']
    _record(repo, project, 'visible', text='已更名公司', **common)
    assert reads.mentioned_entities(project, scope, '美团') == []
    historical = reads.mentioned_entities(project, {**scope, 'known_at': old['recorded_at']}, '美团')
    assert [row['version_id'] for row in historical] == [old['version_id']]


def test_mentioned_entities_uses_full_names_and_literal_query_parameters(tmp_path):
    repo = Repository(tmp_path / 'entity-mention-literals')
    project = repo.create_project('实体原文')['id']
    long_name = '很长公司名称' * 30
    _record(repo, project, 'long', long_name)
    _record(repo, project, 'punctuation', "ACME%'_公司")
    reads = ScopedReads(repo)
    assert [row['id'] for row in reads.mentioned_entities(project, {}, long_name + '规则')] == ['long']
    assert [row['id'] for row in reads.mentioned_entities(project, {}, "ACME%'_公司规则")] == ['punctuation']
    assert reads.mentioned_entities(project, {}, "' OR 1=1 --") == []
