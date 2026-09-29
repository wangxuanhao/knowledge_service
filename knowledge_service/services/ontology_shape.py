"""Shared OWL shape declarations and structural graph helpers."""

from __future__ import annotations

from rdflib import BNode, Graph, RDF, RDFS, URIRef
from rdflib.namespace import OWL, XSD


CLASS_DECLARATIONS = frozenset({OWL.Class, RDFS.Class})
SUPPORTED_XSD_DATATYPES = frozenset({
    XSD.string, XSD.boolean, XSD.integer, XSD.decimal, XSD.float, XSD.double,
    XSD.date, XSD.dateTime,
})
STRUCTURAL_SHAPE_PREDICATES = frozenset({
    RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range,
})
_SIMPLE_ANONYMOUS_CLASS_PREDICATES = frozenset({
    RDF.type, RDFS.subClassOf, RDFS.label, RDFS.comment,
})


def is_graph_class(graph: Graph, node) -> bool:
    """Return whether ``node`` has a supported OWL/RDFS class declaration."""
    return any((node, RDF.type, declaration) in graph
               for declaration in CLASS_DECLARATIONS)


def is_simple_anonymous_class(graph: Graph, node: BNode) -> bool:
    """Return whether a blank-node class uses only the supported simple shape."""
    for predicate, value in graph.predicate_objects(node):
        if predicate not in _SIMPLE_ANONYMOUS_CLASS_PREDICATES:
            return False
        if predicate == RDF.type and value not in CLASS_DECLARATIONS:
            return False
        if predicate in {RDFS.label, RDFS.comment} and isinstance(value, BNode):
            return False
    for subject, predicate in graph.subject_predicates(node):
        if predicate != RDFS.subClassOf or not is_graph_class(graph, subject):
            return False
    return True


def structurally_changed_iris(before: Graph, after: Graph) -> frozenset[URIRef]:
    """Return IRIs whose outgoing ontology-shape triples differ between graphs."""
    subjects = {
        subject
        for graph in (before, after)
        for subject, predicate, _ in graph
        if isinstance(subject, URIRef) and predicate in STRUCTURAL_SHAPE_PREDICATES
    }
    return frozenset(
        subject for subject in subjects
        if {
            (predicate, value) for predicate, value in before.predicate_objects(subject)
            if predicate in STRUCTURAL_SHAPE_PREDICATES
        } != {
            (predicate, value) for predicate, value in after.predicate_objects(subject)
            if predicate in STRUCTURAL_SHAPE_PREDICATES
        }
    )
