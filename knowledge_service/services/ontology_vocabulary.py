"""Shared parsing primitives for governed RDF/OWL vocabulary terms."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL
from rdflib.term import Identifier


GOVERNED_RDF_KINDS = {
    OWL.Class: "class",
    RDFS.Class: "class",
    OWL.ObjectProperty: "relation",
    OWL.DatatypeProperty: "attribute",
}


class RevisionGraph(Graph):
    """An RDFLib graph with a monotonic revision for triple mutations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.revision = 0

    def add(self, triple):
        changed = triple not in self
        result = super().add(triple)
        if changed:
            self.revision += 1
        return result

    def addN(self, quads):
        quads = tuple(quads)
        before = len(self)
        result = super().addN(quads)
        if len(self) != before:
            self.revision += 1
        return result

    def remove(self, triple):
        before = len(self)
        result = super().remove(triple)
        if len(self) != before:
            self.revision += 1
        return result


@dataclass(frozen=True)
class GovernedVocabularyRecord:
    node: Identifier
    iri: str
    kinds: tuple[str, ...]
    local_name: str
    label_values: tuple[Identifier, ...]
    labels: tuple[str, ...]
    active: bool


def local_name(uri) -> str:
    """Return a decoded local name for HTTP(S) IRIs and project URNs."""
    iri = str(uri)
    _, separator, fragment = iri.rpartition("#")
    if separator:
        segment = fragment
    else:
        path = iri.split("?", 1)[0]
        segment = (path.rsplit("/", 1)[-1] if "/" in path
                   else path.rsplit(":", 1)[-1])
    return unquote(segment)


def deprecated_marker_is_true(marker) -> bool:
    """Return whether an ``owl:deprecated`` marker is explicitly truthy."""
    value = marker.toPython() if isinstance(marker, Literal) else str(marker)
    return value is True or str(value).strip().lower() in {"true", "1"}


def index_governed_vocabulary(graph: Graph) -> tuple[GovernedVocabularyRecord, ...]:
    """Index governed kinds, labels, and retirement state in one graph traversal."""
    rows = {}
    for subject, predicate, obj in graph:
        if not isinstance(subject, URIRef):
            continue
        if predicate == RDF.type and obj in GOVERNED_RDF_KINDS:
            row = rows.setdefault(
                subject, {"kinds": set(), "labels": set(), "retired": False})
            row["kinds"].add(GOVERNED_RDF_KINDS[obj])
        elif predicate == RDFS.label:
            row = rows.setdefault(
                subject, {"kinds": set(), "labels": set(), "retired": False})
            row["labels"].add(obj)
        elif predicate == OWL.deprecated and deprecated_marker_is_true(obj):
            row = rows.setdefault(
                subject, {"kinds": set(), "labels": set(), "retired": False})
            row["retired"] = True

    records = []
    for subject in sorted(rows, key=str):
        row = rows[subject]
        kinds = tuple(sorted(row["kinds"]))
        if not kinds:
            continue
        iri = str(subject)
        label_values = tuple(sorted(
            row["labels"],
            key=lambda value: (
                str(value),
                str(getattr(value, "language", "") or ""),
                str(getattr(value, "datatype", "") or ""),
            ),
        ))
        records.append(GovernedVocabularyRecord(
            node=subject,
            iri=iri,
            kinds=kinds,
            local_name=local_name(iri),
            label_values=label_values,
            labels=tuple(dict.fromkeys(str(value) for value in label_values)),
            active=not row["retired"],
        ))
    return tuple(records)
