"""Shared parsing primitives for governed RDF/OWL vocabulary terms."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from rdflib import Graph, Literal, RDF, RDFS
from rdflib.namespace import OWL
from rdflib.term import Identifier


GOVERNED_RDF_KINDS = {
    OWL.Class: "class",
    RDFS.Class: "class",
    OWL.ObjectProperty: "relation",
    OWL.DatatypeProperty: "attribute",
}


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
    if iri.lower().startswith("urn:"):
        return unquote(iri.rsplit(":", 1)[-1])
    return unquote(iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1])


def deprecated_marker_is_true(marker) -> bool:
    """Return whether an ``owl:deprecated`` marker is explicitly truthy."""
    value = marker.toPython() if isinstance(marker, Literal) else str(marker)
    return value is True or str(value).strip().lower() in {"true", "1"}


def index_governed_vocabulary(graph: Graph) -> tuple[GovernedVocabularyRecord, ...]:
    """Index governed kinds, labels, and retirement state in one graph traversal."""
    rows = {}
    for subject, predicate, obj in graph:
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
