"""Deterministic, auditable ontology slices for controlled extraction."""
from __future__ import annotations

from rdflib import RDFS, URIRef

from .ontology_vocabulary import local_name


class OntologySliceSelector:
    def __init__(self, *, roots=(), selector_version='ontology-slice-v1'):
        self.roots = tuple(str(item) for item in roots)
        self.selector_version = selector_version

    def select_terms(self, text, ontology, *, ontology_id):
        graph = ontology.graph
        terms = ontology.classes | ontology.relations | ontology.attributes
        reasons = {}
        surfaces = {}

        def add(term, reason, surface=None):
            if term not in terms:
                return False
            reasons.setdefault(term, set()).add(reason)
            if surface:
                surfaces.setdefault(term, surface)
            return True

        needle = str(text or '').casefold()
        for term in sorted(terms, key=str):
            labels = [str(value).strip() for value in graph.objects(term, RDFS.label)
                      if str(value).strip()]
            labels.append(local_name(term))
            matches = sorted({label for label in labels
                              if label and label.casefold() in needle},
                             key=lambda value: (-len(value), value))
            if matches:
                add(term, 'lexical', matches[0])

        matched_classes = [term for term in reasons if term in ontology.classes]
        for term in matched_classes:
            stack = list(graph.objects(term, RDFS.subClassOf))
            seen = set()
            while stack:
                parent = stack.pop()
                if parent in seen:
                    continue
                seen.add(parent)
                if add(parent, 'ancestor'):
                    stack.extend(graph.objects(parent, RDFS.subClassOf))
            stack = list(graph.subjects(RDFS.subClassOf, term))
            seen = set()
            while stack:
                child = stack.pop()
                if child in seen:
                    continue
                seen.add(child)
                if add(child, 'child'):
                    stack.extend(graph.subjects(RDFS.subClassOf, child))

        # A selected property brings its contract; a selected class brings the
        # properties whose domain/range mention that class.
        selected = set(reasons)
        for prop in sorted(ontology.relations | ontology.attributes, key=str):
            domains = set(graph.objects(prop, RDFS.domain))
            ranges = set(graph.objects(prop, RDFS.range))
            if prop in selected or selected & (domains | ranges):
                add(prop, 'property_contract')
                for domain in domains:
                    add(domain, 'property_domain')
                for range_ in ranges:
                    add(range_, 'property_range')

        degraded = False
        if not reasons:
            degraded = True
            for root in self.roots:
                add(URIRef(root), 'configured_root')

        result = [{
            'term_iri': str(term),
            'surface_hit': surfaces.get(term),
            'reasons': sorted(values),
            'ontology_id': ontology_id,
        } for term, values in sorted(reasons.items(), key=lambda item: str(item[0]))]
        return {
            'ontology_id': ontology_id,
            'selector_version': self.selector_version,
            'degraded': degraded,
            'terms': result,
        }
