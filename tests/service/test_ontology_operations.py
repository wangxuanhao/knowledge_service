import json

import pytest
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, SH, XSD

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.ontology import Ontology, set_term_constraints
from knowledge_service.services.ontology_operations import (
    DCTERMS,
    apply_operations,
    build_operation,
    build_restore_operation,
    canonical_turtle_diff,
    is_batch_eligible,
    operation_fingerprint,
    retirement_dependencies,
)
from knowledge_service.services.service import KnowledgeService


BASE = '''
@prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Root a owl:Class .
ex:A a owl:Class; rdfs:subClassOf ex:Root .
ex:B a owl:Class .
ex:rel a owl:ObjectProperty; rdfs:domain ex:A; rdfs:range ex:B .
ex:value a owl:DatatypeProperty; rdfs:domain ex:A; rdfs:range xsd:string .
'''


def _apply(*operations, base=BASE):
    return Ontology(apply_operations(base, list(operations)))


@pytest.mark.parametrize(('kind', 'iri', 'declaration'), [
    ('class', 'http://ex/C', OWL.Class),
    ('relation', 'http://ex/related', OWL.ObjectProperty),
    ('attribute', 'http://ex/score', OWL.DatatypeProperty),
])
def test_create_term_compiles_all_supported_kinds(kind, iri, declaration):
    operation = build_operation('create_term', iri, after={'kind': kind})
    ontology = _apply(operation)
    assert (URIRef(iri), RDF.type, declaration) in ontology.graph


def test_class_supports_zero_or_many_parents_and_rejects_duplicate_self_and_multihop_cycles():
    created = build_operation('create_term', 'http://ex/C', after={
        'kind': 'class', 'parents': ['http://ex/A', 'http://ex/B']})
    ontology = _apply(created)
    assert set(ontology.graph.objects(URIRef('http://ex/C'), RDFS.subClassOf)) == {
        URIRef('http://ex/A'), URIRef('http://ex/B')}

    with pytest.raises(ValueError, match='重复'):
        _apply(build_operation('create_term', 'http://ex/C', after={
            'kind': 'class', 'parents': ['http://ex/A', 'http://ex/A']}))
    with pytest.raises(ValueError, match='自身|循环'):
        _apply(build_operation('add_parent', 'http://ex/A', after={'value': 'http://ex/A'}))
    with pytest.raises(ValueError, match='循环'):
        _apply(build_operation('add_parent', 'http://ex/Root', after={'value': 'http://ex/A'}))


def test_legacy_constraint_wrapper_delegates_to_multi_parent_operation_semantics():
    ontology = Ontology(BASE)
    set_term_constraints(
        ontology, URIRef('http://ex/A'), 'class',
        parents=['http://ex/Root', 'http://ex/B'])
    assert set(ontology.graph.objects(URIRef('http://ex/A'), RDFS.subClassOf)) == {
        URIRef('http://ex/Root'), URIRef('http://ex/B')}


def test_annotations_are_removed_by_exact_value_and_language():
    iri = 'http://ex/A'
    add_en = build_operation('add_annotation', iri, after={
        'predicate': str(RDFS.label), 'value': 'Name', 'language': 'en'})
    add_zh = build_operation('add_annotation', iri, after={
        'predicate': str(RDFS.label), 'value': '名称', 'language': 'zh'})
    remove_en = build_operation('remove_annotation', iri, before={
        'predicate': str(RDFS.label), 'value': 'Name', 'language': 'en'})
    ontology = _apply(add_en, add_zh, remove_en)
    labels = set(ontology.graph.objects(URIRef(iri), RDFS.label))
    assert labels == {Literal('名称', lang='zh')}


def test_datatype_and_domain_range_or_semantics_are_canonical():
    operations = [
        build_operation('set_datatype', 'http://ex/value', after={'datatype': str(XSD.integer)}),
        build_operation('remove_domain', 'http://ex/rel', before={'value': 'http://ex/A'}),
        build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/B'}),
        build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/A'}),
        build_operation('remove_range', 'http://ex/rel', before={'value': 'http://ex/B'}),
    ]
    ontology = _apply(*operations)
    assert list(ontology.graph.objects(URIRef('http://ex/value'), RDFS.range)) == [XSD.integer]
    domains = list(ontology.graph.objects(URIRef('http://ex/rel'), RDFS.domain))
    assert len(domains) == 1
    union = ontology.graph.value(domains[0], OWL.unionOf)
    assert list(Collection(ontology.graph, union)) == [URIRef('http://ex/A'), URIRef('http://ex/B')]
    assert list(ontology.graph.objects(URIRef('http://ex/rel'), RDFS.range)) == []


def test_logical_remove_rebuilds_and_collapses_union_list_without_orphans():
    with_union = _apply(
        build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/B'}))
    reduced = Ontology(apply_operations(with_union.graph.serialize(format='turtle'), [
        build_operation('remove_domain', 'http://ex/rel', before={'value': 'http://ex/A'})]))
    assert list(reduced.graph.objects(URIRef('http://ex/rel'), RDFS.domain)) == [URIRef('http://ex/B')]
    assert not list(reduced.graph.subjects(RDF.first, None))


def test_retire_keeps_definition_and_uses_structured_replacement():
    create = build_operation('create_term', 'http://ex/related', after={
        'kind': 'relation'})
    operation = build_operation('retire', 'http://ex/rel', after={
        'replacement': {'iri': 'http://ex/related', 'reason': 'merged'}})
    ontology = _apply(create, operation)
    term = URIRef('http://ex/rel')
    assert ontology.is_active_term(term) is False
    assert (term, RDF.type, OWL.ObjectProperty) in ontology.graph
    assert (term, RDFS.domain, URIRef('http://ex/A')) in ontology.graph
    assert ontology.graph.value(term, DCTERMS.isReplacedBy) == URIRef('http://ex/related')


def test_retire_rejects_active_dependencies_on_the_newly_deprecated_term():
    with pytest.raises(ValueError, match='依赖|dependency'):
        _apply(build_operation('retire', 'http://ex/A'))


def test_retirement_dependency_matrix_blocks_active_semantic_dependencies_but_only_reports_history():
    graph = Graph().parse(data=BASE + '''
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix dcterms: <http://purl.org/dc/terms/> .
        ex:Child a owl:Class; rdfs:subClassOf ex:A .
        ex:usesA a owl:ObjectProperty; rdfs:domain ex:A; rdfs:range ex:A .
        ex:unionUses a owl:ObjectProperty; rdfs:domain ex:B;
          rdfs:range [ owl:unionOf (ex:A ex:B) ] .
        ex:S a sh:NodeShape; sh:targetClass ex:A .
        ex:Old a owl:Class; dcterms:isReplacedBy ex:A .
        ex:A ex:note "custom" .
    ''', format='turtle')
    report = retirement_dependencies(
        Ontology(graph.serialize(format='turtle')), 'http://ex/A',
        active_records=[{'id': 'current'}], historical_records=[{'id': 'old'}])
    assert {item['kind'] for item in report['errors']} == {
        'active_child', 'active_domain', 'active_range', 'active_shacl',
        'active_replacement'}
    assert {item['term'] for item in report['errors'] if item['kind'] == 'active_range'} == {
        'http://ex/usesA', 'http://ex/unionUses'}
    assert report['warnings'][0]['kind'] == 'custom_annotation'
    assert report['impact']['historical_records'] == 1
    assert report['impact']['active_records'] == 1


def test_restore_builder_requires_same_project_immutable_source_and_captures_template(tmp_path):
    repo = Repository(tmp_path / 'restore.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('p')['id']
    other_id = repo.create_project('other')['id']
    source_ttl = BASE + 'ex:C a owl:Class; rdfs:subClassOf ex:Root .'
    source = repo.save_ontology(project_id, source_ttl, Ontology(source_ttl).summary())
    other_source = repo.save_ontology(other_id, source_ttl, Ontology(source_ttl).summary())
    current = source_ttl + 'ex:C owl:deprecated true .'
    repo.save_ontology(project_id, current, Ontology(current).summary())

    with pytest.raises(ValueError, match='source_ontology_id'):
        build_restore_operation(repo, project_id, 'http://ex/C', None, selected_fields=['parents'])
    with pytest.raises(ValueError, match='项目|source'):
        build_restore_operation(repo, project_id, 'http://ex/C', other_source['id'],
                                selected_fields=['parents'])
    operation = build_restore_operation(
        repo, project_id, 'http://ex/C', source['id'],
        selected_fields=['parents'])
    assert operation['after']['source_ontology_id'] == source['id']
    assert operation['after']['template']['parents'] == ['http://ex/Root']
    assert set(operation['after']['template']) == {'kind', 'parents'}
    assert set(operation['impact']['preview']) >= {
        'kind', 'annotations', 'parents', 'domain', 'range', 'datatype', 'active'}
    assert operation['impact']['preview']['active'] is True
    restored = Ontology(apply_operations(current, [operation]))
    assert restored.is_active_term('http://ex/C') is True
    assert list(restored.graph.objects(URIRef('http://ex/C'), RDFS.subClassOf)) == [
        URIRef('http://ex/Root')]


def test_fingerprint_is_sha256_of_canonical_json_and_ignores_mapping_order():
    first = {'action': 'add_parent', 'target_iri': 'http://ex/A',
             'after': {'value': 'http://ex/B', 'meta': {'b': 2, 'a': 1}}}
    second = json.loads(json.dumps(first))
    second['after']['meta'] = {'a': 1, 'b': 2}
    assert operation_fingerprint(first) == operation_fingerprint(second)
    assert len(operation_fingerprint(first)) == 64


def test_apply_rejects_an_operation_changed_after_it_was_fingerprinted():
    operation = build_operation(
        'add_parent', 'http://ex/B', after={'value': 'http://ex/Root'})
    operation['after']['value'] = 'http://ex/A'
    with pytest.raises(ValueError, match='fingerprint|指纹'):
        apply_operations(BASE, [operation])


def test_canonical_diff_ignores_bnode_ids_and_triple_order():
    left = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
              @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
              ex:p a owl:ObjectProperty; rdfs:domain [ owl:unionOf (ex:A ex:B) ].'''
    right = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
               @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
               _:different owl:unionOf (ex:A ex:B). ex:p rdfs:domain _:different;
               a owl:ObjectProperty.'''
    assert canonical_turtle_diff(left, right) == []


def test_canonical_diff_treats_union_member_order_as_semantically_equivalent():
    left = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
              @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
              ex:p a owl:ObjectProperty; rdfs:domain [ owl:unionOf (ex:A ex:B) ].'''
    right = left.replace('(ex:A ex:B)', '(ex:B ex:A)')
    assert canonical_turtle_diff(left, right) == []


def test_canonical_diff_groups_union_and_shacl_changes_atomically_and_blocks_complex_owl():
    edited = BASE.replace('rdfs:domain ex:A', 'rdfs:domain [ owl:unionOf (ex:A ex:B) ]', 1)
    operations = canonical_turtle_diff(BASE, edited)
    assert [op['action'] for op in operations] == ['replace_domain']

    shaped = BASE + '''
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        ex:PersonShape a sh:NodeShape; sh:targetClass ex:A;
          sh:property [ sh:path ex:value; sh:maxCount 1 ] .
    '''
    shacl = canonical_turtle_diff(BASE, shaped)
    assert len(shacl) == 1 and shacl[0]['action'] == 'replace_shacl_shape'
    assert isomorphic(
        Graph().parse(data=apply_operations(BASE, shacl), format='turtle'),
        Graph().parse(data=shaped, format='turtle'))

    restriction = BASE + '''ex:C a owl:Class; rdfs:subClassOf [
        a owl:Restriction; owl:onProperty ex:rel; owl:someValuesFrom ex:B ].'''
    with pytest.raises(ValueError, match='Restriction|复杂'):
        canonical_turtle_diff(BASE, restriction)


def test_diff_requires_retirement_instead_of_physical_declaration_removal():
    edited = BASE.replace('ex:B a owl:Class .', '')
    with pytest.raises(ValueError, match='停用|retire'):
        canonical_turtle_diff(BASE, edited, published=True)


@pytest.mark.parametrize('forbidden', [
    (RDF.type, OWL.Class),
    (OWL.deprecated, Literal(True)),
    (DCTERMS.isReplacedBy, URIRef('http://ex/B')),
])
def test_advanced_patch_cannot_change_lifecycle_invariants(forbidden):
    predicate, value = forbidden
    operation = build_operation('advanced_rdf_patch', 'http://ex/A', after={
        'remove': [{'subject': 'http://ex/A', 'predicate': str(predicate),
                    'object': {'type': 'iri', 'value': str(value)}}]})
    with pytest.raises(ValueError, match='advanced|高级|不允许'):
        _apply(operation)


def test_summary_can_filter_active_terms_without_erasing_history_view():
    ontology = _apply(build_operation('retire', 'http://ex/rel'))
    assert {row['id'] for row in ontology.summary()['relations']} >= {'http://ex/rel'}
    assert 'http://ex/rel' not in {
        row['id'] for row in ontology.summary(active_only=True)['relations']}


@pytest.mark.parametrize(('action', 'source', 'impact', 'warnings', 'expected'), [
    ('add_annotation', 'manual', {}, [], 'low'),
    ('remove_annotation', 'manual', {}, [], 'low'),
    ('create_term', 'manual', {'referenced': False, 'leaf': True}, [], 'low'),
    ('create_term', 'manual', {'referenced': True, 'leaf': True}, [], 'medium'),
    ('add_parent', 'manual', {}, [], 'medium'),
    ('add_domain', 'manual', {}, [], 'medium'),
    ('add_range', 'manual', {}, [], 'medium'),
    ('add_annotation', 'discovery', {}, [], 'medium'),
    ('add_annotation', 'ai', {}, [], 'medium'),
    ('add_annotation', 'import', {}, [], 'medium'),
    ('add_annotation', 'candidate', {}, [], 'medium'),
    ('add_annotation', 'manual', {}, ['ambiguous'], 'medium'),
    ('remove_parent', 'manual', {}, [], 'high'),
    ('remove_domain', 'manual', {}, [], 'high'),
    ('remove_range', 'manual', {}, [], 'high'),
    ('set_datatype', 'manual', {}, [], 'high'),
    ('retire', 'manual', {}, [], 'high'),
    ('restore', 'manual', {}, [], 'high'),
    ('advanced_rdf_patch', 'manual', {}, [], 'high'),
    ('add_annotation', 'manual', {'formal_records': 1}, [], 'high'),
    ('add_annotation', 'manual', {'descendants': 51}, [], 'high'),
    ('add_annotation', 'manual', {'constraints': 11}, [], 'high'),
    ('add_annotation', 'manual', {'pending': 21}, [], 'high'),
])
def test_server_risk_matrix_and_batch_eligibility(action, source, impact, warnings, expected):
    operation = build_operation(
        action, 'http://ex/A', after={'kind': 'class'}, source=source,
        impact=impact, warnings=warnings, confidence=1.0)
    assert operation['risk'] == expected
    assert is_batch_eligible(operation) is (expected == 'low')


def test_confidence_never_reduces_source_minimum():
    operation = build_operation(
        'add_annotation', 'http://ex/A', source='ai', confidence=1.0)
    assert operation['risk'] == 'medium'


@pytest.mark.parametrize('action', [
    'add_parent', 'add_domain', 'add_range', 'remove_parent', 'remove_domain',
    'remove_range', 'set_datatype', 'retire', 'restore', 'advanced_rdf_patch',
])
def test_batch_eligibility_recomputes_server_floor_instead_of_trusting_claimed_risk(action):
    assert is_batch_eligible({
        'action': action, 'risk': 'low', 'source': 'manual', 'impact': {}}) is False
