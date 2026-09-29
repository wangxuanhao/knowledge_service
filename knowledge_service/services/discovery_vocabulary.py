"""Deterministic vocabulary normalization for open ontology discovery."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

from rdflib import Graph, Literal, RDF, RDFS
from rdflib.namespace import OWL


_RDF_KIND = {
    OWL.Class: "class",
    OWL.ObjectProperty: "relation",
    OWL.DatatypeProperty: "attribute",
}
_KIND_ALIASES = {
    "class": "class", "classes": "class",
    "relation": "relation", "relations": "relation",
    "attribute": "attribute", "attributes": "attribute",
}


def _immutable(*_args, **_kwargs):
    raise TypeError("normalized discovery vocabulary data is immutable")


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


class _FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _immutable
    reverse = sort = __iadd__ = __imul__ = _immutable


def _freeze_json(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


def canonical_name(value) -> str:
    """Return the comparison form used for discovered vocabulary names."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


def _canonical_json_value(value):
    if isinstance(value, dict):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_json_value(item) for item in value]
        return sorted(items, key=_canonical_json)
    return value


def _canonical_json(value) -> str:
    return json.dumps(_canonical_json_value(value), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def canonical_payload_fingerprint(payload) -> str:
    """Return a SHA-256 digest for a JSON-safe payload."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def discovery_source_fingerprint(
        project_id, base_ontology_id, candidates, *, normalizer_version="v1",
        generator_contract="semantica-0.6.7", runtime_version,
        attribute_threshold, generation_options, request_name=None) -> str:
    """Fingerprint every input that can affect discovery generation."""
    canonical_candidates = [_canonical_json_value(candidate) for candidate in candidates]
    canonical_candidates.sort(key=_canonical_json)
    return canonical_payload_fingerprint({
        "project_id": project_id,
        "base_ontology_id": base_ontology_id,
        "candidates": canonical_candidates,
        "normalizer_version": normalizer_version,
        "generator_contract": generator_contract,
        "runtime_version": runtime_version,
        "attribute_threshold": attribute_threshold,
        "request_name": request_name,
        "generation_options": _canonical_json_value(generation_options),
    })


@dataclass(frozen=True)
class NormalizationResult:
    accepted_candidates: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    merged_groups: tuple[dict, ...]
    candidate_bindings: tuple[dict, ...]
    diagnostics: dict


class InvalidBaselineVocabulary(ValueError):
    """Raised when one baseline IRI has more than one governed RDF kind."""


@dataclass(frozen=True)
class _BaselineTerm:
    iri: str
    kind: str
    names: tuple[str, ...]
    active: bool


def _local_name(iri: str) -> str:
    return unquote(iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1])


def _is_retired(graph: Graph, subject) -> bool:
    for marker in graph.objects(subject, OWL.deprecated):
        value = marker.toPython() if isinstance(marker, Literal) else str(marker)
        if value is True or str(value).strip().lower() in {"true", "1"}:
            return True
    return False


def _baseline_terms(baseline_turtle: str, baseline_summary=None, *, strict=True):
    graph = Graph()
    if baseline_turtle and str(baseline_turtle).strip():
        graph.parse(data=baseline_turtle, format="turtle")
    subjects = {}
    for rdf_kind, kind in _RDF_KIND.items():
        for subject in graph.subjects(RDF.type, rdf_kind):
            subjects.setdefault(str(subject), set()).add(kind)
    invalid = {iri: kinds for iri, kinds in subjects.items() if len(kinds) > 1}
    if invalid and strict:
        iri = sorted(invalid)[0]
        raise InvalidBaselineVocabulary(
            f"baseline IRI {iri} has governed kinds {sorted(invalid[iri])}")

    summary_names = {}
    for plural_kind, items in (baseline_summary or {}).items():
        kind = _KIND_ALIASES.get(plural_kind)
        if not kind or not isinstance(items, list):
            continue
        for item in items:
            iri = str(item.get("id") or item.get("iri") or "")
            if not iri:
                continue
            values = [item.get(key) for key in ("name", "label", "label_zh", "label_en")]
            summary_names.setdefault((iri, kind), []).extend(value for value in values if value)

    terms = []
    for iri in sorted(subjects):
        subject = next(node for node in graph.all_nodes() if str(node) == iri)
        labels = [str(label) for label in graph.objects(subject, RDFS.label)]
        for kind in sorted(subjects[iri]):
            values = [_local_name(iri), *labels, *summary_names.get((iri, kind), [])]
            names = tuple(dict.fromkeys(str(value) for value in values if canonical_name(value)))
            terms.append(_BaselineTerm(iri, kind, names, not _is_retired(graph, subject)))
    return tuple(terms)


def audit_formal_vocabulary(baseline_turtle: str) -> dict:
    """Report governed-kind collisions in Turtle without changing the vocabulary."""
    graph = Graph()
    if baseline_turtle and str(baseline_turtle).strip():
        graph.parse(data=baseline_turtle, format="turtle")
    subjects = {}
    for rdf_kind, kind in _RDF_KIND.items():
        for subject in graph.subjects(RDF.type, rdf_kind):
            entry = subjects.setdefault(str(subject), {"node": subject, "kinds": set()})
            entry["kinds"].add(kind)

    details = {}
    name_terms = {}
    iri_collisions = []
    for iri in sorted(subjects):
        entry = subjects[iri]
        labels = sorted({str(label) for label in graph.objects(entry["node"], RDFS.label)})
        names = list(dict.fromkeys([_local_name(iri), *labels]))
        active = not _is_retired(graph, entry["node"])
        kinds = sorted(entry["kinds"])
        details[iri] = {
            "iri": iri, "kinds": kinds, "names": names, "labels": labels,
            "active": active, "state": "active" if active else "retired",
        }
        if len(kinds) > 1:
            iri_collisions.append({
                "code": "iri_multiple_governed_kinds", **details[iri],
            })
        for kind in kinds:
            term = {"iri": iri, "kind": kind, "names": names, "labels": labels,
                    "active": active, "state": "active" if active else "retired"}
            for name in names:
                name_terms.setdefault(canonical_name(name), []).append(term)

    name_collisions = []
    for name in sorted(name_terms):
        terms = sorted(name_terms[name], key=lambda item: (item["iri"], item["kind"]))
        kinds = sorted({term["kind"] for term in terms})
        if len(kinds) > 1:
            name_collisions.append({
                "code": "canonical_name_multiple_governed_kinds",
                "canonical_name": name,
                "iris": sorted({term["iri"] for term in terms}),
                "kinds": kinds,
                "terms": terms,
            })
    return {"name_collisions": tuple(name_collisions),
            "iri_collisions": tuple(iri_collisions)}


class DiscoveryVocabularyNormalizer:
    def __init__(self, baseline_turtle: str, *, baseline_summary=None, attribute_threshold: int = 2):
        self.baseline_turtle = baseline_turtle
        self.baseline_summary = baseline_summary
        self.attribute_threshold = attribute_threshold
        self._terms = _baseline_terms(baseline_turtle, baseline_summary)
        aliases = {}
        for term in self._terms:
            for name in term.names:
                aliases.setdefault(canonical_name(name), []).append(term)
        self._aliases = {
            name: tuple(sorted({(term.iri, term.kind): term for term in terms}.values(),
                               key=lambda term: (term.iri, term.kind)))
            for name, terms in aliases.items()
        }
        self._terms_by_iri = {term.iri: term for term in self._terms}

    def normalize(self, candidates) -> NormalizationResult:
        copied = json.loads(_canonical_json(list(candidates)))
        for candidate in copied:
            candidate["kind"] = _KIND_ALIASES.get(candidate.get("kind"), candidate.get("kind"))

        conflict_by_index = {}

        def quarantine(index, code, **details):
            if index in conflict_by_index:
                return
            item = copied[index]
            conflict_by_index[index] = {
                "candidate_id": item.get("id"), "kind": item.get("kind"),
                "name": item.get("name"), "iri": item.get("iri"),
                "code": code, **details,
            }

        explicit_claims = {}
        for index, item in enumerate(copied):
            if item.get("iri"):
                explicit_claims.setdefault(str(item["iri"]), []).append(index)
        for iri, indexes in explicit_claims.items():
            kinds = sorted({copied[index].get("kind") for index in indexes})
            if len(kinds) > 1:
                for index in indexes:
                    quarantine(index, "candidate_iri_kind_collision",
                               involved_iri=iri, involved_kinds=kinds)

        for index, item in enumerate(copied):
            if index in conflict_by_index or not item.get("iri"):
                continue
            term = self._terms_by_iri.get(str(item["iri"]))
            if not term:
                continue
            if term.kind != item.get("kind"):
                quarantine(index, "existing_term_kind_collision",
                           existing_kind=term.kind)
            elif not term.active:
                quarantine(index, "retired_term_reuse_blocked")
            elif canonical_name(item.get("name")) not in {
                    canonical_name(name) for name in term.names}:
                quarantine(index, "candidate_iri_name_mismatch",
                           baseline_names=list(term.names))

        by_name = {}
        for index, item in enumerate(copied):
            if index not in conflict_by_index:
                by_name.setdefault(canonical_name(item.get("name")), []).append(index)
        for indexes in by_name.values():
            kinds = {copied[index].get("kind") for index in indexes}
            if "class" in kinds and len(kinds) > 1:
                for index in indexes:
                    if copied[index].get("kind") != "class":
                        quarantine(index, "class_property_name_collision",
                                   involved_kinds=sorted(kinds))
            elif {"relation", "attribute"}.issubset(kinds):
                for index in indexes:
                    quarantine(index, "relation_attribute_name_collision",
                               involved_kinds=["attribute", "relation"])

        if self.attribute_threshold > 1:
            for name, indexes in by_name.items():
                active = [index for index in indexes if index not in conflict_by_index]
                attributes = [index for index in active
                              if copied[index].get("kind") == "attribute"]
                existing = any(term.kind == "attribute" and term.active
                               for term in self._aliases.get(name, ()))
                if attributes and len(attributes) < self.attribute_threshold and not existing:
                    for index in attributes:
                        quarantine(index, "low_frequency_attribute",
                                   frequency=len(attributes), threshold=self.attribute_threshold)

        groups = {}
        order = []
        for index, candidate in enumerate(copied):
            if index in conflict_by_index:
                continue
            key = (candidate.get("kind"), canonical_name(candidate.get("name")))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(index)

        accepted = []
        merged = []
        binding_by_index = {}
        for key in order:
            indexes = groups[key]
            aliases = self._aliases.get(key[1], ())
            involved_iris = sorted({term.iri for term in aliases})
            if len(involved_iris) > 1:
                for index in indexes:
                    quarantine(index, "ambiguous_baseline_name", involved_iris=involved_iris)
                continue
            group = [copied[index] for index in indexes]
            leader = group[0]
            same_kind = [term for term in aliases if term.kind == key[0] and term.active]
            reused_iri = same_kind[0].iri if len(same_kind) == 1 else leader.get("iri")
            if reused_iri:
                leader["iri"] = reused_iri
            evidence = []
            for item in group:
                for reference in item.get("evidence_refs") or []:
                    if reference not in evidence:
                        evidence.append(reference)
            if evidence or "evidence_refs" in leader:
                leader["evidence_refs"] = evidence
            accepted.append(leader)
            if len(group) > 1:
                merged.append({
                    "kind": key[0], "canonical_name": key[1],
                    "candidate_ids": [item.get("id") for item in group],
                    "accepted_candidate_id": leader.get("id"),
                })
            for offset, index in enumerate(indexes):
                item = copied[index]
                binding_by_index[index] = {
                    "candidate_id": item.get("id"),
                    "accepted_candidate_id": leader.get("id"),
                    "status": "accepted" if offset == 0 else "merged",
                    "iri": reused_iri,
                }

        for index in conflict_by_index:
            binding_by_index[index] = {
                "candidate_id": copied[index].get("id"),
                "accepted_candidate_id": None, "status": "quarantined", "iri": None,
            }
        conflicts = tuple(conflict_by_index[index] for index in sorted(conflict_by_index))
        bindings = tuple(binding_by_index[index] for index in sorted(binding_by_index))
        merged_count = sum(binding["status"] == "merged" for binding in bindings)
        low_frequency_count = sum(
            conflict["code"] == "low_frequency_attribute" for conflict in conflicts)
        return NormalizationResult(
            tuple(_freeze_json(item) for item in accepted),
            tuple(_freeze_json(item) for item in conflicts),
            tuple(_freeze_json(item) for item in merged),
            tuple(_freeze_json(item) for item in bindings),
            _freeze_json({
                "total_candidates": len(copied), "accepted_candidates": len(accepted),
                "merged_candidates": merged_count, "conflict_candidates": len(conflicts),
                "low_frequency_attributes": low_frequency_count,
            }),
        )
