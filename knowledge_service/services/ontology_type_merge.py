"""Pure class merge planner; changes remain reviewable ontology operations."""

from rdflib import Graph, URIRef, RDF, RDFS, Literal
from rdflib.namespace import OWL, SH, SKOS, DCTERMS
from rdflib.compare import isomorphic, to_canonical_graph

from .ontology import Ontology
from .ontology_operations import (
    canonical_turtle_diff, apply_operations, _constraint_values, _set_constraint,
    _validate_supported_bnodes, _is_annotation_predicate,
)


def canonical_graph_snapshot(graph):
    return '\n'.join(sorted(' '.join(term.n3() for term in triple)
                            for triple in to_canonical_graph(graph)))


def plan_class_merge(ontology, source_iri, target_iri, *, published=True):
    source, target = URIRef(source_iri), URIRef(target_iri)
    if source == target:
        raise ValueError('源类型与目标类型不能相同')
    for node in (source, target):
        if node not in ontology.classes or not ontology.is_active_term(node):
            raise ValueError(f'合并仅支持活动的 class：{node}')
    graph = Graph()
    for triple in ontology.graph:
        graph.add(triple)
    _validate_supported_bnodes(graph)
    if any(graph.triples((None, source, None))):
        raise ValueError('源类型被用作 RDF 谓词，无法安全重写')
    supported = {RDFS.subClassOf, RDFS.domain, RDFS.range,
                 SH['class'], SH.targetClass}
    # Resolve list membership through its domain/range owner, never by a
    # blanket IRI substitution that could corrupt arbitrary RDF structures.
    supported_list_cells = set()
    for predicate in (RDFS.domain, RDFS.range):
        for owner in list(graph.subjects(predicate, None)):
            for union in graph.objects(owner, predicate):
                head = graph.value(union, OWL.unionOf)
                if head is not None:
                    supported_list_cells.update(graph.transitive_objects(head, RDF.rest))
    for subject, predicate in graph.subject_predicates(source):
        if (predicate == DCTERMS.isReplacedBy
                and subject in ontology.classes
                and not ontology.is_active_term(subject)):
            # Preserve recorded replacement history: Earlier -> Source -> Target
            # is resolved by the controlled migration chain resolver.
            continue
        if predicate in supported or (predicate == RDF.first and subject in supported_list_cells):
            continue
        raise ValueError(f'无法安全重写语义引用：{subject} {predicate}')
    for predicate, value in graph.predicate_objects(source):
        if (predicate not in {RDF.type, RDFS.subClassOf, OWL.deprecated}
                and (str(predicate).startswith(str(OWL))
                     or not _is_annotation_predicate(predicate))):
            raise ValueError(f'源类型包含不支持的语义结构：{predicate}')
    for child in list(graph.subjects(RDFS.subClassOf, source)):
        graph.remove((child, RDFS.subClassOf, source))
        if child != target:
            graph.add((child, RDFS.subClassOf, target))
    for parent in list(graph.objects(source, RDFS.subClassOf)):
        if parent != target:
            graph.add((target, RDFS.subClassOf, parent))
    for predicate in (RDFS.domain, RDFS.range):
        for owner in set(graph.subjects(predicate, None)):
            values = _constraint_values(graph, owner, predicate)
            if source in values:
                _set_constraint(graph, owner, predicate,
                                {target if value == source else value for value in values})
    for predicate in (SH['class'], SH.targetClass):
        for subject in list(graph.subjects(predicate, source)):
            graph.remove((subject, predicate, source))
            graph.add((subject, predicate, target))
    for label in graph.objects(source, RDFS.label):
        graph.add((target, SKOS.altLabel, label))
    graph.set((source, OWL.deprecated, Literal(True)))
    graph.set((source, DCTERMS.isReplacedBy, target))
    original = ontology.graph.serialize(format='turtle')
    operations = canonical_turtle_diff(original, graph.serialize(format='turtle'),
                                        published=published, staged_retirement=True)
    # Validate each review atom's replay, not merely the final union of edits.
    working = original
    for operation in operations:
        working = apply_operations(working, [operation])
    if not isomorphic(Ontology(working).graph, graph):
        raise ValueError('合并原子操作与目标结构不一致')
    return operations, graph
