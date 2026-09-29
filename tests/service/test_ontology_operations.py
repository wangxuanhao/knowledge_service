import json

import pytest
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, SH, XSD

from knowledge_service.integrations.embeddings import HashingEncoder
import knowledge_service.services.ontology_operations as ontology_operations
from knowledge_service.repository import Repository
from knowledge_service.services.discovery_vocabulary import (
    DiscoveryVocabularyNormalizer,
    InvalidDiscoveryCandidate,
)
from knowledge_service.services.ontology import Ontology, absolute_iri, set_term_constraints
from knowledge_service.services.ontology_operations import (
    DCTERMS,
    RESTORE_TEMPLATE_FIELDS,
    apply_operations,
    build_operation,
    build_restore_operation,
    canonical_turtle_diff,
    is_batch_eligible,
    operation_fingerprint,
    retirement_dependencies,
    validate_ontology_invariants,
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

RDFS_CLASS_BASE = '''
@prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a rdfs:Class .
ex:A a rdfs:Class; rdfs:subClassOf ex:Root .
ex:B a rdfs:Class .
ex:rel a owl:ObjectProperty; rdfs:domain ex:A; rdfs:range ex:Root .
'''


def _apply(*operations, base=BASE):
    return Ontology(apply_operations(base, list(operations)))


@pytest.mark.parametrize("iri", [
    "ftp://example.test/Term",
    "did:example:term",
    "http://example.test:/Term",
    "http://example.test/%ZZ",
    "urn:x",
    "urn::Term",
    "urn:a:Term",
    "urn:knowledge:",
    "urn:-bad:Term",
    "urn:knowledge:/Term",
    f"urn:{'a' * 33}:Term",
    "urn:bad-:Term",
    "urn:knowledge:Term?bad",
    "urn:knowledge:Term?+",
    "urn:knowledge:Term?=",
])
def test_application_iri_policy_is_shared_across_entrypoints(iri):
    assert absolute_iri(iri) is False
    with pytest.raises(ValueError, match='target_iri'):
        build_operation('create_term', iri, after={'kind': 'class'})
    with pytest.raises(InvalidDiscoveryCandidate, match='valid absolute IRI'):
        DiscoveryVocabularyNormalizer('').normalize([{
            'id': 'candidate', 'kind': 'class', 'name': 'Term', 'iri': iri,
        }])


@pytest.mark.parametrize("iri", [
    "https://example.test/Term",
    "urn:knowledge:ontology:project:Term",
])
def test_application_iri_policy_accepts_operational_schemes(iri):
    assert absolute_iri(iri) is True
    assert build_operation(
        'create_term', iri, after={'kind': 'class'})['target_iri'] == iri


def test_rdfs_class_supports_annotation_add_and_reviewed_remove():
    add = build_operation('add_annotation', 'http://ex/A', after={
        'predicate': str(RDFS.label), 'value': 'A'})
    remove = build_operation('remove_annotation', 'http://ex/A', before={
        'predicate': str(RDFS.label), 'value': 'A'})
    result = Graph().parse(
        data=apply_operations(RDFS_CLASS_BASE, [add, remove]), format='turtle')
    assert (URIRef('http://ex/A'), RDF.type, RDFS.Class) in result
    assert not list(result.objects(URIRef('http://ex/A'), RDFS.label))


def test_rdfs_class_supports_parent_add_and_cycle_detection():
    added = Graph().parse(data=apply_operations(RDFS_CLASS_BASE, [build_operation(
        'add_parent', 'http://ex/B', after={'value': 'http://ex/A'})]),
        format='turtle')
    assert (URIRef('http://ex/B'), RDFS.subClassOf, URIRef('http://ex/A')) in added
    with pytest.raises(ValueError, match='cycle|循环'):
        apply_operations(RDFS_CLASS_BASE, [build_operation(
            'add_parent', 'http://ex/Root', after={'value': 'http://ex/A'})])


def test_rdfs_class_is_valid_domain_and_range_reference():
    result = Ontology(apply_operations(RDFS_CLASS_BASE, [
        build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/B'}),
        build_operation('add_range', 'http://ex/rel', after={'value': 'http://ex/B'}),
    ]))
    assert URIRef('http://ex/B') in result.constraint_types(
        URIRef('http://ex/rel'), RDFS.domain)
    assert URIRef('http://ex/B') in result.constraint_types(
        URIRef('http://ex/rel'), RDFS.range)


def test_create_rejects_duplicate_rdfs_class_declaration():
    with pytest.raises(ValueError, match='已存在|exists'):
        apply_operations(RDFS_CLASS_BASE, [build_operation(
            'create_term', 'http://ex/A', after={'kind': 'class'})])


def test_turtle_diff_compiles_rdfs_class_label_and_parent_changes():
    edited = RDFS_CLASS_BASE + '''
      ex:A rdfs:label "Class A" .
      ex:B rdfs:subClassOf ex:A .
    '''
    operations = canonical_turtle_diff(RDFS_CLASS_BASE, edited)
    assert [(item['action'], item['target_iri']) for item in operations] == [
        ('add_annotation', 'http://ex/A'),
        ('add_parent', 'http://ex/B'),
    ]
    assert isomorphic(
        Graph().parse(data=apply_operations(RDFS_CLASS_BASE, operations), format='turtle'),
        Graph().parse(data=edited, format='turtle'))


@pytest.mark.parametrize(('kind', 'iri', 'declaration'), [
    ('class', 'http://ex/C', OWL.Class),
    ('relation', 'http://ex/related', OWL.ObjectProperty),
    ('attribute', 'http://ex/score', OWL.DatatypeProperty),
])
def test_create_term_compiles_all_supported_kinds(kind, iri, declaration):
    operation = build_operation('create_term', iri, after={'kind': kind})
    ontology = _apply(operation)
    assert (URIRef(iri), RDF.type, declaration) in ontology.graph


def test_create_term_only_declares_identity_and_rejects_embedded_edges():
    operation = build_operation('create_term', 'http://ex/C', after={
        'kind': 'class', 'parents': ['http://ex/A']})
    with pytest.raises(ValueError, match='create_term|独立'):
        _apply(operation)


@pytest.mark.parametrize('operation', [
    build_operation('add_parent', 'http://ex/A', after={'value': 'http://ex/Missing'}),
    build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/Missing'}),
    build_operation('add_range', 'http://ex/rel', after={'value': 'http://ex/Missing'}),
    build_operation('set_datatype', 'http://ex/value', after={'datatype': 'http://ex/Anything'}),
    build_operation('add_annotation', 'http://ex/A', after={
        'predicate': str(DCTERMS.isReplacedBy), 'value': 'http://ex/Missing',
        'type': 'iri'}),
])
def test_operations_reject_nonexistent_references_and_arbitrary_datatypes(operation):
    with pytest.raises(ValueError, match='不存在|datatype|数据类型'):
        _apply(operation)


def test_class_supports_zero_or_many_parents_and_rejects_duplicate_self_and_multihop_cycles():
    operations = [
        build_operation('create_term', 'http://ex/C', after={'kind': 'class'}),
        build_operation('add_parent', 'http://ex/C', after={'value': 'http://ex/A'}),
        build_operation('add_parent', 'http://ex/C', after={'value': 'http://ex/B'}),
    ]
    ontology = _apply(*operations)
    assert set(ontology.graph.objects(URIRef('http://ex/C'), RDFS.subClassOf)) == {
        URIRef('http://ex/A'), URIRef('http://ex/B')}

    with pytest.raises(ValueError, match='重复'):
        _apply(*operations, build_operation(
            'add_parent', 'http://ex/C', after={'value': 'http://ex/A'}))
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


def test_diff_emits_exact_per_value_language_and_custom_annotation_operations():
    base = BASE + '''
      ex:A rdfs:label "Old"@en, "保留"@zh; rdfs:comment "before"; ex:note "x" .
    '''
    edited = BASE + '''
      ex:A rdfs:label "New"@en, "保留"@zh; rdfs:comment "after"; ex:note "y" .
    '''
    operations = canonical_turtle_diff(base, edited)
    assert [operation['action'] for operation in operations] == [
        'remove_annotation', 'remove_annotation', 'remove_annotation',
        'add_annotation', 'add_annotation', 'add_annotation']
    removed = [operation['before'] for operation in operations[:3]]
    added = [operation['after'] for operation in operations[3:]]
    assert {'predicate': str(RDFS.label), 'value': 'Old', 'language': 'en',
            'datatype': None} in removed
    assert {'predicate': str(RDFS.label), 'value': 'New', 'language': 'en',
            'datatype': None} in added
    assert isomorphic(
        Graph().parse(data=apply_operations(base, operations), format='turtle'),
        Graph().parse(data=edited, format='turtle'))


def test_diff_normalizes_deprecation_restore_and_replacement_operations():
    retired = BASE + 'ex:rel owl:deprecated true .'
    retirement = canonical_turtle_diff(BASE, retired)
    assert [operation['action'] for operation in retirement] == ['retire_term']
    assert isomorphic(
        Graph().parse(data=apply_operations(BASE, retirement), format='turtle'),
        Graph().parse(data=retired, format='turtle'))

    with pytest.raises(ValueError, match='source_ontology_id|structured restore|结构化恢复'):
        canonical_turtle_diff(retired, BASE)
    with pytest.raises(ValueError, match='source_ontology_id|structured restore|结构化恢复'):
        canonical_turtle_diff(
            retired, BASE, base_ontology_id='arbitrary-or-nonexistent')

    authoritative = build_operation('restore_term', 'http://ex/rel', after={
        'source_ontology_id': 'immutable-version-17',
        'selected_fields': sorted(RESTORE_TEMPLATE_FIELDS),
        'template': {
            'kind': 'relation', 'annotations': [], 'parents': [],
            'domain': ['http://ex/A'], 'range': ['http://ex/B'],
            'datatype': None,
        },
    }, impact={'preview': {'kind': 'relation', 'active': True}})
    resolved = []

    def resolver(**request):
        resolved.append(request)
        return authoritative

    restoration = canonical_turtle_diff(
        retired, BASE, source_ontology_id='immutable-version-17',
        restore_operation_builder=resolver)
    assert [operation['action'] for operation in restoration] == ['restore_term']
    assert restoration[0] is authoritative
    assert resolved == [{
        'target_iri': 'http://ex/rel',
        'source_ontology_id': 'immutable-version-17',
    }]
    assert isomorphic(
        Graph().parse(data=apply_operations(retired, restoration), format='turtle'),
        Graph().parse(data=BASE, format='turtle'))

    replacement = canonical_turtle_diff(
        BASE, BASE + 'ex:A <http://purl.org/dc/terms/isReplacedBy> ex:B .')
    assert [operation['action'] for operation in replacement] == ['add_annotation']
    assert replacement[0]['after'] == {
        'predicate': str(DCTERMS.isReplacedBy), 'value': 'http://ex/B', 'type': 'iri'}


def test_restore_diff_requires_requested_source_before_calling_resolver():
    retired = BASE + 'ex:rel owl:deprecated true .'
    calls = []

    def resolver(**request):
        calls.append(request)
        raise AssertionError('resolver must not run without a requested immutable source')

    with pytest.raises(ValueError, match='source_ontology_id'):
        canonical_turtle_diff(
            retired, BASE, base_ontology_id='current-only',
            restore_operation_builder=resolver)
    assert calls == []


def test_restore_diff_keeps_current_base_and_historical_source_ids_independent():
    retired = BASE + 'ex:rel owl:deprecated true .'
    authoritative = build_operation('restore_term', 'http://ex/rel', after={
        'source_ontology_id': 'historical-template-v3',
        'selected_fields': sorted(RESTORE_TEMPLATE_FIELDS),
        'template': {
            'kind': 'relation', 'annotations': [], 'parents': [],
            'domain': ['http://ex/A'], 'range': ['http://ex/B'],
            'datatype': None,
        },
    }, impact={'preview': {'kind': 'relation', 'active': True}})
    requests = []
    operations = canonical_turtle_diff(
        retired, BASE, base_ontology_id='current-base-v9',
        source_ontology_id='historical-template-v3',
        restore_operation_builder=lambda **request: requests.append(request) or authoritative)
    assert operations == [authoritative]
    assert requests == [{
        'target_iri': 'http://ex/rel',
        'source_ontology_id': 'historical-template-v3',
    }]


def test_restore_diff_rejects_resolver_source_mismatch():
    retired = BASE + 'ex:rel owl:deprecated true .'
    mismatched = build_operation('restore_term', 'http://ex/rel', after={
        'source_ontology_id': 'immutable-other',
        'selected_fields': sorted(RESTORE_TEMPLATE_FIELDS),
        'template': {
            'kind': 'relation', 'annotations': [], 'parents': [],
            'domain': ['http://ex/A'], 'range': ['http://ex/B'],
            'datatype': None,
        },
    }, impact={'preview': {'kind': 'relation', 'active': True}})
    with pytest.raises(ValueError, match='source_ontology_id|一致|match'):
        canonical_turtle_diff(
            retired, BASE, source_ontology_id='immutable-requested',
            restore_operation_builder=lambda **_: mismatched)


def test_datatype_and_domain_range_or_semantics_are_canonical():
    operations = [
        build_operation(
            'set_datatype', 'http://ex/value',
            before={'datatype': str(XSD.string)},
            after={'datatype': str(XSD.integer)}),
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


def test_diff_rejects_multiple_datatypes_instead_of_emitting_range_bypass():
    edited = BASE.replace(
        'rdfs:range xsd:string',
        'rdfs:range [ owl:unionOf (xsd:string xsd:integer) ]')
    with pytest.raises(ValueError, match='datatype|set_datatype|数据类型'):
        canonical_turtle_diff(BASE, edited)


def test_logical_remove_rebuilds_and_collapses_union_list_without_orphans():
    with_union = _apply(
        build_operation('add_domain', 'http://ex/rel', after={'value': 'http://ex/B'}))
    reduced = Ontology(apply_operations(with_union.graph.serialize(format='turtle'), [
        build_operation('remove_domain', 'http://ex/rel', before={'value': 'http://ex/A'})]))
    assert list(reduced.graph.objects(URIRef('http://ex/rel'), RDFS.domain)) == [URIRef('http://ex/B')]
    assert not list(reduced.graph.subjects(RDF.first, None))


def test_retire_term_keeps_definition_and_uses_structured_replacement():
    create = build_operation('create_term', 'http://ex/related', after={
        'kind': 'relation'})
    operation = build_operation('retire_term', 'http://ex/rel', after={
        'replacement': {'iri': 'http://ex/related', 'reason': 'merged'}})
    ontology = _apply(create, operation)
    term = URIRef('http://ex/rel')
    assert ontology.is_active_term(term) is False
    assert (term, RDF.type, OWL.ObjectProperty) in ontology.graph
    assert (term, RDFS.domain, URIRef('http://ex/A')) in ontology.graph
    assert ontology.graph.value(term, DCTERMS.isReplacedBy) == URIRef('http://ex/related')


def test_retire_rejects_active_dependencies_on_the_newly_deprecated_term():
    with pytest.raises(ValueError, match='依赖|dependency'):
        _apply(build_operation('retire_term', 'http://ex/A'))


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
        ex:Referrer a owl:Class; ex:pointsTo ex:A .
    ''', format='turtle')
    report = retirement_dependencies(
        Ontology(graph.serialize(format='turtle')), 'http://ex/A',
        active_records=[{'id': 'current'}], historical_records=[{'id': 'old'}])
    assert {item['code'] for item in report['errors']} == {
        'active_child_dependency', 'active_domain_dependency',
        'active_range_dependency', 'active_shacl_target_class_dependency',
        'active_replacement_dependency'}
    assert {item['term_iris'][0] for item in report['errors']
            if item['code'] == 'active_range_dependency'} == {
        'http://ex/usesA', 'http://ex/unionUses'}
    assert report['warnings'][0]['code'] == 'active_custom_annotation_dependency'
    assert report['info'][0]['code'] == 'deprecated_term_structure_retained'
    for issue in [*report['errors'], *report['warnings'], *report['info']]:
        assert set(issue) == {
            'code', 'severity', 'message', 'operation_ids', 'term_iris'}
    assert report['impact']['historical_records'] == 1
    assert report['impact']['active_records'] == 1


def test_retirement_issues_distinguish_supported_shacl_path_and_carry_operation_ids():
    turtle = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:S a sh:NodeShape; sh:targetClass ex:A;
        sh:property [ sh:path ex:value; sh:maxCount 1 ] .
    '''
    report = retirement_dependencies(
        Ontology(turtle), 'http://ex/value', operation_ids=['op-1'])
    issue = next(item for item in report['errors']
                 if item['code'] == 'active_shacl_path_dependency')
    assert issue['operation_ids'] == ['op-1']
    assert issue['term_iris'] == ['http://ex/S', 'http://ex/value']


@pytest.mark.parametrize('dependency', [
    'ex:Child a owl:Class; rdfs:subClassOf ex:New .',
    'ex:newRel a owl:ObjectProperty; rdfs:domain ex:New; rdfs:range ex:B .',
    'ex:S a <http://www.w3.org/ns/shacl#NodeShape>; '
    '<http://www.w3.org/ns/shacl#targetClass> ex:New .',
    'ex:A ex:semanticRef ex:New .',
])
def test_diff_computes_referenced_new_term_risk_from_complete_graph(dependency):
    edited = BASE + f'ex:New a owl:Class . {dependency}'
    operations = canonical_turtle_diff(BASE, edited)
    created = next(operation for operation in operations
                   if operation['action'] == 'create_term'
                   and operation['target_iri'] == 'http://ex/New')
    assert created['impact']['referenced'] is True
    assert created['risk'] == 'medium'
    assert is_batch_eligible(created) is False


def test_diff_keeps_unreferenced_leaf_create_low_risk():
    operations = canonical_turtle_diff(BASE, BASE + 'ex:New a owl:Class .')
    created = next(operation for operation in operations
                   if operation['target_iri'] == 'http://ex/New')
    assert created['impact'] == {'leaf': True, 'referenced': False}
    assert created['risk'] == 'low'
    assert is_batch_eligible(created) is True


def test_dependency_warning_raises_otherwise_low_operation_and_blocks_batch():
    graph = Graph().parse(data=BASE + '''
      ex:Referrer a owl:Class; ex:pointsTo ex:A .
    ''', format='turtle')
    report = retirement_dependencies(
        Ontology(graph.serialize(format='turtle')), 'http://ex/A')
    assert report['warnings'][0]['severity'] == 'warning'
    operation = build_operation(
        'add_annotation', 'http://ex/B', warnings=report['warnings'],
        after={'predicate': str(RDFS.label), 'value': 'B'})
    assert operation['risk'] == 'medium'
    assert is_batch_eligible(operation) is False


def test_added_custom_reference_to_existing_deprecated_term_gets_derived_warning():
    base = BASE + 'ex:Old a owl:Class; owl:deprecated true .'
    edited = base + 'ex:A ex:pointsTo ex:Old .'
    operation = canonical_turtle_diff(base, edited)[0]
    warnings = operation['validation']['warnings']
    assert operation['action'] == 'add_annotation'
    assert warnings[0]['code'] == 'active_custom_annotation_dependency'
    assert warnings[0]['term_iris'] == ['http://ex/A', 'http://ex/Old']
    assert operation['risk'] == 'medium'
    assert is_batch_eligible(operation) is False


def test_structured_manual_annotation_uses_same_derived_dependency_warning():
    ontology = Ontology(BASE + 'ex:Old a owl:Class; owl:deprecated true .')
    operation = build_operation(
        'add_annotation', 'http://ex/A', ontology=ontology,
        after={
            'predicate': 'http://ex/pointsTo',
            'value': 'http://ex/Old',
            'type': 'iri',
        })
    assert operation['validation']['warnings'][0]['code'] == (
        'active_custom_annotation_dependency')
    assert operation['risk'] == 'medium'
    assert is_batch_eligible(operation) is False


@pytest.mark.parametrize(('action', 'edge'), [
    ('add_annotation', 'after'),
    ('remove_annotation', 'before'),
])
def test_iri_annotation_without_ontology_context_is_validation_required(
        action, edge):
    operation = build_operation(action, 'http://ex/A', **{edge: {
        'predicate': 'http://ex/pointsTo',
        'value': 'http://ex/Old',
        'type': 'iri',
    }})
    assert operation['validation']['warnings'][0]['code'] == (
        'ontology_context_required')
    assert operation['risk'] == 'medium'
    assert is_batch_eligible(operation) is False


def test_batch_eligibility_fails_safe_for_unclassified_iri_annotation_shape():
    unclassified = {
        'action': 'add_annotation',
        'target_iri': 'http://ex/A',
        'after': {
            'predicate': 'http://ex/pointsTo',
            'value': 'http://ex/Old',
            'type': 'iri',
        },
        'source': 'manual',
        'impact': {},
        'validation': {'warnings': []},
        'risk': 'low',
    }
    assert is_batch_eligible(unclassified) is False


def test_external_custom_annotation_iri_is_valid_with_context_and_stays_low_risk():
    operation = build_operation(
        'add_annotation', 'http://ex/A', ontology=Ontology(BASE),
        after={
            'predicate': 'http://ex/documentation',
            'value': 'https://example.com/docs/a',
            'type': 'iri',
        })
    assert operation['validation']['warnings'] == []
    assert operation['validation']['ontology_context_validated'] is True
    assert operation['risk'] == 'low'
    assert is_batch_eligible(operation) is False
    assert is_batch_eligible(operation, ontology=Ontology(BASE)) is True
    result = Graph().parse(data=apply_operations(BASE, [operation]), format='turtle')
    assert (
        URIRef('http://ex/A'), URIRef('http://ex/documentation'),
        URIRef('https://example.com/docs/a')) in result


@pytest.mark.parametrize('marker', ['true', '"true"^^xsd:boolean'])
def test_deactivated_shacl_dependencies_are_info_not_blockers(marker):
    turtle = BASE + f'''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:C a owl:Class .
      ex:flag a owl:DatatypeProperty; rdfs:range xsd:boolean .
      ex:S a sh:NodeShape; sh:deactivated {marker}; sh:targetClass ex:C;
        sh:property [ sh:path ex:flag ] .
    '''
    class_report = retirement_dependencies(Ontology(turtle), 'http://ex/C')
    path_report = retirement_dependencies(Ontology(turtle), 'http://ex/flag')
    assert not class_report['errors']
    assert not path_report['errors']
    assert any(issue['code'] == 'deactivated_shacl_dependency'
               for issue in class_report['info'])
    assert any(issue['code'] == 'deactivated_shacl_dependency'
               for issue in path_report['info'])


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
    impacts = []
    operation = build_restore_operation(
        repo, project_id, 'http://ex/C', source['id'],
        selected_fields=['parents'],
        impact_provider=lambda p, term: impacts.append((p, term)) or {
            'formal_records': 2, 'historical_records': 3})
    assert operation['action'] == 'restore_term'
    assert operation['after']['source_ontology_id'] == source['id']
    assert operation['after']['template']['parents'] == ['http://ex/Root']
    assert set(operation['after']['template']) == {'kind', 'parents'}
    assert set(operation['impact']['preview']) >= {
        'kind', 'annotations', 'parents', 'domain', 'range', 'datatype', 'active'}
    assert operation['impact']['preview']['active'] is True
    assert operation['impact']['reactivated_constraints'] == 1
    assert operation['impact']['formal_records'] == 2
    assert operation['impact']['historical_records'] == 3
    assert impacts == [(project_id, 'http://ex/C')]
    restored = Ontology(apply_operations(current, [operation]))
    assert restored.is_active_term('http://ex/C') is True
    assert list(restored.graph.objects(URIRef('http://ex/C'), RDFS.subClassOf)) == [
        URIRef('http://ex/Root')]


@pytest.mark.parametrize('selected_fields', [
    [], ['parents', 'parents'], ['parents', 'unsupported'],
])
def test_restore_builder_rejects_empty_duplicate_or_unsupported_fields(
        tmp_path, selected_fields):
    repo = Repository(tmp_path / 'restore-fields.sqlite')
    project_id = repo.create_project('p')['id']
    source = repo.save_ontology(project_id, BASE, Ontology(BASE).summary())
    current = BASE + 'ex:rel owl:deprecated true .'
    repo.save_ontology(project_id, current, Ontology(current).summary())
    with pytest.raises(ValueError, match='显式|重复|字段|field'):
        build_restore_operation(
            repo, project_id, 'http://ex/rel', source['id'],
            selected_fields=selected_fields)
    assert RESTORE_TEMPLATE_FIELDS == frozenset({
        'annotations', 'parents', 'domain', 'range', 'datatype'})


def test_restore_rejects_selected_field_missing_from_frozen_template():
    current = BASE + 'ex:rel owl:deprecated true .'
    operation = build_operation('restore_term', 'http://ex/rel', after={
        'source_ontology_id': 'immutable-version',
        'selected_fields': ['domain'],
        'template': {'kind': 'relation'},
    })
    with pytest.raises(ValueError, match='domain|template|模板|字段'):
        apply_operations(current, [operation])


def test_restore_selected_none_datatype_clears_current_datatype():
    current = BASE + 'ex:value owl:deprecated true .'
    operation = build_operation('restore_term', 'http://ex/value', after={
        'source_ontology_id': 'ontology-no-datatype',
        'selected_fields': ['datatype'],
        'template': {'kind': 'attribute', 'datatype': None},
    })
    restored = Ontology(apply_operations(current, [operation]))
    assert list(restored.graph.objects(URIRef('http://ex/value'), RDFS.range)) == []


@pytest.mark.parametrize(('target', 'kind', 'field', 'value'), [
    ('http://ex/A', 'class', 'parents', ['ftp://example.test/Parent']),
    ('http://ex/rel', 'relation', 'domain', ['did:example:Domain']),
    ('http://ex/rel', 'relation', 'range', ['http://example.test:/Range']),
    ('http://ex/value', 'attribute', 'datatype', 'urn:a:string'),
])
def test_restore_template_definition_iris_use_shared_application_policy(
        target, kind, field, value):
    current = BASE + f'<{target}> owl:deprecated true .'
    operation = build_operation('restore_term', target, after={
        'source_ontology_id': 'immutable-source',
        'selected_fields': [field],
        'template': {'kind': kind, field: value},
    })

    with pytest.raises(ValueError, match='绝对 IRI|absolute IRI'):
        apply_operations(current, [operation])


def test_annotation_only_restore_preserves_class_node_shape_subgraph():
    current = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:A a sh:NodeShape; owl:deprecated true; rdfs:label "current";
        sh:targetClass ex:A; sh:property [ sh:path ex:value; sh:minCount 1 ] .
    '''
    operation = build_operation('restore_term', 'http://ex/A', after={
        'source_ontology_id': 'immutable-source',
        'selected_fields': ['annotations'],
        'template': {'kind': 'class', 'annotations': [{
            'predicate': str(RDFS.label), 'value': 'historical',
            'language': None, 'datatype': None,
        }]},
    })
    restored = Graph().parse(
        data=apply_operations(current, [operation]), format='turtle')
    shape = URIRef('http://ex/A')
    assert (shape, SH.targetClass, shape) in restored
    properties = list(restored.objects(shape, SH.property))
    assert len(properties) == 1
    assert (properties[0], SH.path, URIRef('http://ex/value')) in restored
    assert (properties[0], SH.minCount, Literal(1)) in restored
    assert list(restored.objects(shape, RDFS.label)) == [Literal('historical')]


def test_restore_annotation_template_rejects_shacl_predicates():
    current = BASE + 'ex:A owl:deprecated true .'
    operation = build_operation('restore_term', 'http://ex/A', after={
        'source_ontology_id': 'immutable-source',
        'selected_fields': ['annotations'],
        'template': {'kind': 'class', 'annotations': [{
            'predicate': str(SH.targetClass), 'value': 'http://ex/B',
            'type': 'iri',
        }]},
    })
    with pytest.raises(ValueError, match='SHACL|annotation|predicate'):
        apply_operations(current, [operation])


def test_restore_term_requires_deprecated_target_and_blocks_historical_cycle():
    template = {
        'kind': 'class', 'parents': ['http://ex/B'], 'annotations': [],
        'domain': [], 'range': [], 'datatype': None,
    }
    active_restore = build_operation('restore_term', 'http://ex/A', after={
        'source_ontology_id': 'source', 'selected_fields': ['parents'],
        'template': template})
    with pytest.raises(ValueError, match='停用|deprecated'):
        _apply(active_restore)

    cyclic_base = BASE + 'ex:C a owl:Class; owl:deprecated true . ex:B rdfs:subClassOf ex:C .'
    cyclic_restore = build_operation('restore_term', 'http://ex/C', after={
        'source_ontology_id': 'source', 'selected_fields': ['parents'],
        'template': {**template, 'parents': ['http://ex/B']}})
    with pytest.raises(ValueError, match='循环|cycle'):
        apply_operations(cyclic_base, [cyclic_restore])


def test_anonymous_owl_class_parent_is_valid_but_not_reusable_vocabulary():
    current = BASE + '''
      ex:AnonymousChild a owl:Class; rdfs:subClassOf [
        a owl:Class; rdfs:label "Anonymous parent"
      ] .
    '''
    operation = build_operation('add_annotation', 'http://ex/AnonymousChild', after={
        'predicate': str(RDFS.comment), 'value': 'keeps anonymous parent',
    })

    restored = Ontology(apply_operations(current, [operation]))
    child = URIRef('http://ex/AnonymousChild')
    parents = list(restored.graph.objects(child, RDFS.subClassOf))

    assert len(parents) == 1
    assert isinstance(parents[0], BNode)
    assert (parents[0], RDF.type, OWL.Class) in restored.graph
    assert parents[0] not in restored.classes
    with pytest.raises(ValueError):
        restored.resolve('Anonymous parent')


def test_anonymous_class_definition_rejects_missing_parent():
    current = BASE + '''
      ex:ChildWithAnonymousParent a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a owl:Class; rdfs:subClassOf ex:Missing .
    '''
    operation = build_operation(
        'add_annotation', 'http://ex/ChildWithAnonymousParent',
        after={'predicate': str(RDFS.comment), 'value': 'validate graph'})

    with pytest.raises(ValueError, match='父类|parent|不存在'):
        apply_operations(current, [operation])


@pytest.mark.parametrize(('subject', 'error'), [
    ('ex:Undeclared', 'subClassOf|声明|declared class'),
    ('_:undeclared', '空白节点|RDF'),
])
def test_subclass_subject_must_be_a_declared_class(subject, error):
    current = BASE + f'{subject} rdfs:subClassOf ex:Root .'
    operation = build_operation(
        'add_annotation', 'http://ex/A',
        after={'predicate': str(RDFS.comment), 'value': 'validate graph'})

    with pytest.raises(ValueError, match=error):
        apply_operations(current, [operation])


def test_anonymous_only_class_cycle_is_rejected():
    current = BASE + '''
      ex:ChildWithAnonymousCycle a owl:Class; rdfs:subClassOf _:first .
      _:first a owl:Class; rdfs:subClassOf _:second .
      _:second a rdfs:Class; rdfs:subClassOf _:first .
    '''
    operation = build_operation(
        'add_annotation', 'http://ex/ChildWithAnonymousCycle',
        after={'predicate': str(RDFS.comment), 'value': 'validate graph'})

    with pytest.raises(ValueError, match='循环|cycle'):
        apply_operations(current, [operation])


def test_named_anonymous_named_class_cycle_is_rejected():
    current = BASE + '''
      ex:NamedCycle a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a rdfs:Class; rdfs:subClassOf ex:NamedCycle .
    '''
    operation = build_operation(
        'add_annotation', 'http://ex/NamedCycle',
        after={'predicate': str(RDFS.comment), 'value': 'validate graph'})

    with pytest.raises(ValueError, match='循环|cycle'):
        apply_operations(current, [operation])


def test_valid_anonymous_class_parent_is_supported_by_canonical_diff():
    base = BASE + '''
      ex:ChildWithAnonymousParent a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a rdfs:Class; rdfs:subClassOf ex:Root .
    '''
    edited = base + 'ex:ChildWithAnonymousParent rdfs:comment "edited" .'

    operations = canonical_turtle_diff(base, edited)
    actual = Graph().parse(
        data=apply_operations(base, operations), format='turtle')
    expected = Graph().parse(data=edited, format='turtle')

    assert [operation['action'] for operation in operations] == ['add_annotation']
    assert isomorphic(actual, expected)


def test_apply_operations_rejects_restriction_with_no_operations():
    turtle = BASE + '''
      ex:RestrictedChild a owl:Class; rdfs:subClassOf [
        a owl:Restriction;
        owl:onProperty ex:rel;
        owl:someValuesFrom ex:B
      ] .
    '''

    with pytest.raises(ValueError, match='Restriction|复杂'):
        apply_operations(turtle, [])


def test_apply_operations_accepts_simple_anonymous_class_with_no_operations():
    turtle = BASE + '''
      ex:SimpleChild a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a rdfs:Class; rdfs:label "Simple parent";
        rdfs:subClassOf ex:Root .
    '''

    graph = Graph().parse(data=apply_operations(turtle, []), format='turtle')
    parent = graph.value(URIRef('http://ex/SimpleChild'), RDFS.subClassOf)

    assert isinstance(parent, BNode)
    assert (parent, RDF.type, RDFS.Class) in graph
    assert (parent, RDFS.subClassOf, URIRef('http://ex/Root')) in graph


def test_definition_validation_rejects_complex_anonymous_class_expression():
    current = BASE + '''
      ex:ComplexChild a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a owl:Class; owl:complementOf ex:B .
    '''
    operation = build_operation('add_annotation', 'http://ex/ComplexChild', after={
        'predicate': str(RDFS.comment), 'value': 'validate graph',
    })

    with pytest.raises(ValueError, match='OWL|复杂|unsupported'):
        apply_operations(current, [operation])


def test_canonical_diff_rejects_complex_typed_anonymous_class_expression():
    base = BASE + '''
      ex:ComplexChild a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a owl:Class; owl:complementOf ex:B .
    '''
    edited = base + 'ex:ComplexChild rdfs:comment "edited" .'

    with pytest.raises(ValueError, match='OWL|复杂|unsupported'):
        canonical_turtle_diff(base, edited)


def test_definition_validation_rejects_unsupported_incoming_anonymous_edge():
    current = BASE + '''
      ex:Equivalent a owl:Class; owl:equivalentClass _:anonymous .
      _:anonymous a owl:Class; rdfs:subClassOf ex:Root .
    '''
    operation = build_operation('add_annotation', 'http://ex/A', after={
        'predicate': str(RDFS.comment), 'value': 'validate graph',
    })

    with pytest.raises(ValueError, match='OWL|incoming|unsupported'):
        apply_operations(current, [operation])


def test_canonical_diff_rejects_unsupported_incoming_anonymous_edge():
    base = BASE + '''
      ex:Equivalent a owl:Class; owl:equivalentClass _:anonymous .
      _:anonymous a owl:Class; rdfs:subClassOf ex:Root .
    '''
    edited = base + 'ex:A rdfs:comment "edited" .'

    with pytest.raises(ValueError, match='OWL|incoming|unsupported'):
        canonical_turtle_diff(base, edited)


def test_declared_class_subclass_edge_is_valid_incoming_anonymous_reference():
    current = BASE + '''
      ex:SimpleChild a owl:Class; rdfs:subClassOf _:anonymous .
      _:anonymous a rdfs:Class; rdfs:label "Simple parent";
        rdfs:subClassOf ex:Root .
    '''
    operation = build_operation('add_annotation', 'http://ex/SimpleChild', after={
        'predicate': str(RDFS.comment), 'value': 'valid hierarchy',
    })

    restored = Ontology(apply_operations(current, [operation]))

    assert str(restored.graph.value(
        URIRef('http://ex/SimpleChild'), RDFS.comment)) == 'valid hierarchy'


@pytest.mark.parametrize('invalid_graph', [
    BASE + 'ex:Undeclared rdfs:subClassOf ex:Root .',
    BASE + '''
      ex:Equivalent a owl:Class; owl:equivalentClass _:anonymous .
      _:anonymous a owl:Class; rdfs:subClassOf ex:Root .
    ''',
])
def test_noop_canonical_diff_runs_full_definition_validation(invalid_graph):
    with pytest.raises(ValueError):
        apply_operations(invalid_graph, [])

    with pytest.raises(ValueError):
        canonical_turtle_diff(invalid_graph, invalid_graph)


def test_active_class_cannot_reach_retired_ancestor_through_anonymous_classes():
    turtle = BASE + '''
      ex:ActiveDescendant a owl:Class; rdfs:subClassOf _:first .
      _:first a owl:Class; rdfs:subClassOf _:second .
      _:second a rdfs:Class; rdfs:subClassOf ex:RetiredAncestor .
      ex:RetiredAncestor a owl:Class; owl:deprecated true .
    '''

    report = validate_ontology_invariants(Ontology(turtle))

    assert report['conforms'] is False
    dependency = next(
        issue for issue in report['errors']
        if issue['code'] == 'active_child_dependency')
    assert dependency['term_iris'] == [
        'http://ex/ActiveDescendant', 'http://ex/RetiredAncestor']
    with pytest.raises(ValueError, match='依赖|dependency'):
        apply_operations(turtle, [])


def test_retirement_preview_traverses_anonymous_class_intermediaries():
    preview_ontology = Ontology(BASE + '''
      ex:ActiveDescendant a owl:Class; rdfs:subClassOf _:first .
      _:first a owl:Class; rdfs:subClassOf _:second .
      _:second a rdfs:Class; rdfs:subClassOf ex:TargetAncestor .
      ex:TargetAncestor a owl:Class .
    ''')
    preview = retirement_dependencies(
        preview_ontology, 'http://ex/TargetAncestor')
    preview_dependency = next(
        issue for issue in preview['errors']
        if issue['code'] == 'active_child_dependency')
    assert preview_dependency['term_iris'] == [
        'http://ex/ActiveDescendant', 'http://ex/TargetAncestor']


def test_active_class_can_reach_active_ancestor_through_anonymous_classes():
    turtle = BASE + '''
      ex:ActiveDescendant a owl:Class; rdfs:subClassOf _:first .
      _:first a owl:Class; rdfs:subClassOf _:second .
      _:second a rdfs:Class; rdfs:subClassOf ex:ActiveAncestor .
      ex:ActiveAncestor a owl:Class .
    '''

    assert validate_ontology_invariants(Ontology(turtle))['conforms'] is True
    assert canonical_turtle_diff(turtle, turtle) == []


@pytest.mark.parametrize('operation', [
    build_operation('add_domain', 'http://ex/A', after={'value': 'http://ex/B'}),
    build_operation('add_range', 'http://ex/A', after={'value': 'http://ex/B'}),
])
def test_domain_and_range_actions_reject_class_targets(operation):
    with pytest.raises(ValueError, match='relation|attribute|关系|属性'):
        _apply(operation)


@pytest.mark.parametrize('action', ['add_range', 'remove_range'])
def test_datatype_property_rejects_range_bypass_operations(action):
    edge = 'after' if action == 'add_range' else 'before'
    operation = build_operation(action, 'http://ex/value', **{
        edge: {'value': str(XSD.string)}})
    with pytest.raises(ValueError, match='set_datatype|DatatypeProperty|数据类型'):
        _apply(operation)


@pytest.mark.parametrize('operation', [
    build_operation('add_annotation', 'http://ex/A', after={
        'predicate': 'relative', 'value': 'x'}),
    build_operation('add_annotation', 'http://ex/A', after={
        'predicate': 'http://ex/link', 'value': 'relative', 'type': 'iri'}),
    build_operation('add_annotation', 'http://ex/A', after={
        'predicate': 'http://ex/value', 'value': 'x', 'datatype': 'relative'}),
    build_operation('set_datatype', 'http://ex/value',
                    before={'datatype': str(XSD.string)},
                    after={'datatype': 'relative'}),
])
def test_operation_iris_and_literal_datatypes_must_be_absolute(operation):
    with pytest.raises(ValueError, match='绝对 IRI|absolute|datatype'):
        _apply(operation)


def test_annotation_operation_cannot_bypass_scoped_shacl_patch():
    operation = build_operation('add_annotation', 'http://ex/A', after={
        'predicate': str(SH.targetClass),
        'value': 'http://ex/B',
        'type': 'iri',
    }, ontology=Ontology(BASE))
    with pytest.raises(ValueError, match='SHACL|结构|annotation'):
        _apply(operation)


@pytest.mark.parametrize('operation', [
    build_operation('remove_parent', 'http://ex/A',
                    before={'value': 'http://ex/Missing'}),
    build_operation('remove_domain', 'http://ex/rel',
                    before={'value': 'http://ex/Missing'}),
    build_operation('remove_range', 'http://ex/rel',
                    before={'value': 'http://ex/Missing'}),
    build_operation('remove_annotation', 'http://ex/A', before={
        'predicate': str(RDFS.label), 'value': 'missing'}),
    build_operation('set_datatype', 'http://ex/value',
                    before={'datatype': str(XSD.integer)},
                    after={'datatype': str(XSD.boolean)}),
])
def test_destructive_operation_rejects_reviewed_before_mismatch(operation):
    with pytest.raises(ValueError, match='before|冲突|不存在|已变更'):
        _apply(operation)


def test_reviewed_before_state_is_checked_sequentially():
    removal = build_operation('remove_parent', 'http://ex/A', before={
        'value': 'http://ex/Root'})
    with pytest.raises(ValueError, match='before|冲突|不存在|已变更'):
        apply_operations(BASE, [removal, removal])


def test_apply_operations_does_not_reparse_full_graph_per_operation(monkeypatch):
    calls = 0
    original = ontology_operations.Ontology.__init__

    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ontology_operations.Ontology, '__init__', counted)
    operations = [
        build_operation('add_annotation', 'http://ex/A', after={
            'predicate': 'http://ex/tag', 'value': f'tag-{index}'})
        for index in range(100)
    ]
    apply_operations(BASE, operations)
    assert calls <= 2


def test_final_validation_rejects_wrong_kind_constraints_and_unknown_simple_shacl_refs():
    wrong_kind = BASE + 'ex:A rdfs:domain ex:B .'
    with pytest.raises(ValueError, match='domain|relation|attribute|关系|属性'):
        apply_operations(wrong_kind, [build_operation(
            'add_annotation', 'http://ex/A',
            after={'predicate': str(RDFS.label), 'value': 'A'})])

    invalid_shacl = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:S a sh:NodeShape; sh:targetClass ex:Missing;
        sh:property [ sh:path ex:missingProperty ] .
    '''
    with pytest.raises(ValueError, match='targetClass|path|SHACL'):
        apply_operations(invalid_shacl, [build_operation(
            'add_annotation', 'http://ex/A',
            after={'predicate': str(RDFS.label), 'value': 'A'})])


def test_fingerprint_is_sha256_of_canonical_json_and_ignores_mapping_order():
    first = {'action': 'add_parent', 'target_iri': 'http://ex/A',
             'after': {'value': 'http://ex/B', 'meta': {'b': 2, 'a': 1}}}
    second = json.loads(json.dumps(first))
    second['after']['meta'] = {'a': 1, 'b': 2}
    assert operation_fingerprint(first) == operation_fingerprint(second)
    assert len(operation_fingerprint(first)) == 64


def test_rdf_patch_fingerprint_ignores_turtle_order_and_bnode_labels():
    first = build_operation('advanced_rdf_patch', 'http://ex/Shape', after={'turtle': '''
      @prefix sh:<http://www.w3.org/ns/shacl#>. @prefix ex:<http://ex/>.
      ex:Shape a sh:NodeShape; sh:property _:a. _:a sh:path ex:value; sh:maxCount 1.
    '''})
    second = build_operation('advanced_rdf_patch', 'http://ex/Shape', after={'turtle': '''
      @prefix sh:<http://www.w3.org/ns/shacl#>. @prefix ex:<http://ex/>.
      _:different sh:maxCount 1; sh:path ex:value. ex:Shape sh:property _:different;
        a sh:NodeShape.
    '''})
    assert first['fingerprint'] == second['fingerprint']


def test_apply_rejects_an_operation_changed_after_it_was_fingerprinted():
    operation = build_operation(
        'add_parent', 'http://ex/B', after={'value': 'http://ex/Root'})
    operation['after']['value'] = 'http://ex/A'
    with pytest.raises(ValueError, match='fingerprint|指纹'):
        apply_operations(BASE, [operation])


def test_canonical_diff_ignores_bnode_ids_and_triple_order():
    left = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
              @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
              ex:A a owl:Class. ex:B a owl:Class.
              ex:p a owl:ObjectProperty; rdfs:domain [ owl:unionOf (ex:A ex:B) ].'''
    right = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
               @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
               ex:A a owl:Class. ex:B a owl:Class.
               _:different owl:unionOf (ex:A ex:B). ex:p rdfs:domain _:different;
               a owl:ObjectProperty.'''
    assert canonical_turtle_diff(left, right) == []


def test_canonical_diff_treats_union_member_order_as_semantically_equivalent():
    left = '''@prefix ex:<http://ex/>. @prefix owl:<http://www.w3.org/2002/07/owl#>.
              @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
              ex:A a owl:Class. ex:B a owl:Class.
              ex:p a owl:ObjectProperty; rdfs:domain [ owl:unionOf (ex:A ex:B) ].'''
    right = left.replace('(ex:A ex:B)', '(ex:B ex:A)')
    assert canonical_turtle_diff(left, right) == []


def test_canonical_diff_groups_union_and_shacl_changes_atomically_and_blocks_complex_owl():
    edited = BASE.replace('rdfs:domain ex:A', 'rdfs:domain [ owl:unionOf (ex:A ex:B) ]', 1)
    operations = canonical_turtle_diff(BASE, edited)
    assert [op['action'] for op in operations] == ['add_domain']

    shaped = BASE + '''
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        ex:PersonShape a sh:NodeShape; sh:targetClass ex:A;
          sh:property [ sh:path ex:value; sh:maxCount 1 ] .
    '''
    shacl = canonical_turtle_diff(BASE, shaped)
    assert len(shacl) == 1 and shacl[0]['action'] == 'advanced_rdf_patch'
    assert 'turtle' not in shacl[0]['after']
    patch_graph = Graph().parse(
        data=shacl[0]['after']['canonical_ntriples'], format='nt')
    assert not list(patch_graph.triples((URIRef('http://ex/A'), None, None)))
    assert isomorphic(
        Graph().parse(data=apply_operations(BASE, shacl), format='turtle'),
        Graph().parse(data=shaped, format='turtle'))

    restriction = BASE + '''ex:C a owl:Class; rdfs:subClassOf [
        a owl:Restriction; owl:onProperty ex:rel; owl:someValuesFrom ex:B ].'''
    with pytest.raises(ValueError, match='Restriction|复杂'):
        canonical_turtle_diff(BASE, restriction)


def test_shacl_patch_is_shape_scoped_and_unrelated_changes_are_separate():
    base = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:S a sh:NodeShape; sh:targetClass ex:A .
    '''
    edited = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:S a sh:NodeShape; sh:targetClass ex:A;
        sh:property [ sh:path ex:value; sh:maxCount 1 ] .
      ex:A rdfs:label "changed"@en .
    '''
    operations = canonical_turtle_diff(base, edited)
    assert [operation['action'] for operation in operations] == [
        'add_annotation', 'advanced_rdf_patch']
    patch = operations[1]
    fragment = Graph().parse(data=patch['after']['canonical_ntriples'], format='nt')
    assert (URIRef('http://ex/A'), RDFS.label, None) not in fragment
    assert isomorphic(
        Graph().parse(data=apply_operations(base, operations), format='turtle'),
        Graph().parse(data=edited, format='turtle'))


def test_shacl_patch_excludes_ontology_triples_when_term_is_also_a_shape():
    base = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:A a sh:NodeShape; sh:targetClass ex:A; rdfs:label "old" .
    '''
    edited = BASE + '''
      @prefix sh: <http://www.w3.org/ns/shacl#> .
      ex:A a sh:NodeShape; sh:targetClass ex:A; sh:closed true; rdfs:label "new" .
    '''
    operations = canonical_turtle_diff(base, edited)
    patch = next(operation for operation in operations
                 if operation['action'] == 'advanced_rdf_patch')
    fragment = Graph().parse(data=patch['after']['canonical_ntriples'], format='nt')
    assert (URIRef('http://ex/A'), RDF.type, OWL.Class) not in fragment
    assert not list(fragment.triples((URIRef('http://ex/A'), RDFS.label, None)))
    assert [operation['action'] for operation in operations].count('add_annotation') == 1
    assert [operation['action'] for operation in operations].count('remove_annotation') == 1


def test_advanced_shape_patch_rejects_unrelated_root_triples():
    operation = build_operation(
        'advanced_rdf_patch', 'http://ex/A',
        before={'canonical_ntriples': ''},
        after={'canonical_ntriples': (
            '<http://ex/A> <http://www.w3.org/2000/01/rdf-schema#label> "hidden" .\n')})
    with pytest.raises(ValueError, match='SHACL|unrelated|无关'):
        _apply(operation)


def test_diff_declares_new_parent_before_child_edges_even_when_child_sorts_first():
    edited = BASE + '''
      ex:AChild a owl:Class; rdfs:subClassOf ex:ZParent; rdfs:label "Child" .
      ex:ZParent a owl:Class; rdfs:label "Parent" .
    '''
    operations = canonical_turtle_diff(BASE, edited)
    assert [(operation['action'], operation['target_iri']) for operation in operations[:3]] == [
        ('create_term', 'http://ex/ZParent'),
        ('create_term', 'http://ex/AChild'),
        ('add_annotation', 'http://ex/ZParent'),
    ]
    add_parent = next(index for index, operation in enumerate(operations)
                      if operation['action'] == 'add_parent')
    parent_create = next(index for index, operation in enumerate(operations)
                         if operation['target_iri'] == 'http://ex/ZParent')
    assert parent_create < add_parent


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
    ontology = _apply(build_operation('retire_term', 'http://ex/rel'))
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
    ('retire_term', 'manual', {}, [], 'high'),
    ('restore_term', 'manual', {}, [], 'high'),
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
    'remove_range', 'set_datatype', 'retire_term', 'restore_term', 'advanced_rdf_patch',
])
def test_batch_eligibility_recomputes_server_floor_instead_of_trusting_claimed_risk(action):
    assert is_batch_eligible({
        'action': action, 'risk': 'low', 'source': 'manual', 'impact': {}}) is False
