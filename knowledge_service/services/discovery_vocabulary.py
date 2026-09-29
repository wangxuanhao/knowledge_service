"""Deterministic vocabulary normalization for open ontology discovery."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from rdflib import Graph

from .ontology_vocabulary import GovernedVocabularyRecord, index_governed_vocabulary
_KIND_ALIASES = {
    "class": "class", "classes": "class",
    "relation": "relation", "relations": "relation",
    "attribute": "attribute", "attributes": "attribute",
}
_CANDIDATE_KIND_ALIASES = {
    "class": "class", "relation": "relation", "attribute": "attribute",
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
    digest = canonical_payload_fingerprint({
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
    return f"sha256:{digest}"


@dataclass(frozen=True)
class NormalizationResult:
    accepted_candidates: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    merged_groups: tuple[dict, ...]
    candidate_bindings: tuple[dict, ...]
    diagnostics: dict


class InvalidBaselineVocabulary(ValueError):
    """Raised when one baseline IRI has more than one governed RDF kind."""


class InvalidDiscoveryCandidate(ValueError):
    """Raised when a discovery candidate violates the normalization boundary."""


_ABSOLUTE_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$")
_INVALID_RAW_IRI_CHARACTERS = frozenset('<>"{}|^`')


def _valid_iri(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if (not _ABSOLUTE_IRI.fullmatch(value)
            or "\\" in value
            or any(character in _INVALID_RAW_IRI_CHARACTERS for character in value)
            or value.count("#") > 1
            or any(character.isspace() or unicodedata.category(character) == "Cc"
                   for character in value)
            or re.search(r"%(?![0-9A-Fa-f]{2})", value)):
        return False
    try:
        parsed = urlsplit(value)
        if "[" in value or "]" in value:
            _, userinfo_separator, host_port = parsed.netloc.rpartition("@")
            userinfo = parsed.netloc[:-len(host_port)] if userinfo_separator else ""
            if (parsed.scheme not in {"http", "https"}
                    or "[" in userinfo or "]" in userinfo
                    or not re.fullmatch(r"\[[^\[\]]+\](?::[0-9]+)?", host_port)
                    or any("[" in part or "]" in part
                           for part in (parsed.path, parsed.query, parsed.fragment))):
                return False
        if parsed.scheme in {"http", "https"}:
            parsed.port
            return bool(parsed.hostname)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.path)


def _validate_and_copy_candidates(candidates):
    rows = list(candidates)
    seen_ids = set()
    for position, candidate in enumerate(rows):
        if not isinstance(candidate, Mapping):
            raise InvalidDiscoveryCandidate(f"candidate {position} must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise InvalidDiscoveryCandidate(
                f"candidate {position} requires a non-empty string id")
        if candidate_id in seen_ids:
            raise InvalidDiscoveryCandidate(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)
        kind = candidate.get("kind")
        if not isinstance(kind, str) or kind not in _CANDIDATE_KIND_ALIASES:
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} requires a governed kind")
        name = candidate.get("name")
        if not isinstance(name, str):
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} requires a non-empty string name")
        if not canonical_name(name):
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} requires a non-empty name")
        if candidate.get("iri") is not None and not _valid_iri(candidate.get("iri")):
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} requires a valid absolute IRI")
        evidence = candidate.get("evidence_refs")
        if evidence is not None and not isinstance(evidence, (list, tuple, set, frozenset)):
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} requires a supported evidence_refs container")
        if evidence is not None and any(
                not isinstance(reference, str) or not reference.strip()
                for reference in evidence):
            raise InvalidDiscoveryCandidate(
                f"candidate {candidate_id} evidence_refs require non-empty string entries")
    try:
        copied = json.loads(_canonical_json(rows))
    except (TypeError, ValueError) as exc:
        raise InvalidDiscoveryCandidate("candidate payload must be JSON-safe") from exc
    for candidate in copied:
        candidate["kind"] = _CANDIDATE_KIND_ALIASES[candidate["kind"]]
    copied.sort(key=lambda candidate: (
        candidate["id"], candidate["kind"], canonical_name(candidate["name"]),
        _canonical_json(candidate),
    ))
    return copied


@dataclass(frozen=True)
class _BaselineTerm:
    iri: str
    kind: str
    names: tuple[str, ...]
    labels: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class _BaselineIndex:
    records: tuple[GovernedVocabularyRecord, ...]
    terms: tuple[_BaselineTerm, ...]
    aliases: dict[str, tuple[_BaselineTerm, ...]]
    terms_by_iri: dict[str, _BaselineTerm]
    invalid_iris: dict[str, tuple[str, ...]]


def _summary_names(baseline_summary):
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
    return summary_names


def _index_baseline_graph(graph: Graph, baseline_summary=None) -> _BaselineIndex:
    """Build all governed baseline lookup structures in one triple traversal."""
    summary_names = _summary_names(baseline_summary)
    records = index_governed_vocabulary(graph)
    terms = []
    aliases = {}
    terms_by_iri = {}
    invalid_iris = {}
    for record in records:
        if len(record.kinds) > 1:
            invalid_iris[record.iri] = record.kinds
        for kind in record.kinds:
            values = [record.local_name, *record.labels,
                      *summary_names.get((record.iri, kind), [])]
            names = tuple(dict.fromkeys(str(value) for value in values
                                        if canonical_name(value)))
            term = _BaselineTerm(
                record.iri, kind, names, record.labels, record.active)
            terms.append(term)
            for name in names:
                aliases.setdefault(canonical_name(name), {})[
                    (record.iri, kind)] = term
            if len(record.kinds) == 1:
                terms_by_iri[record.iri] = term

    frozen_aliases = {
        name: tuple(sorted(values.values(), key=lambda term: (term.iri, term.kind)))
        for name, values in aliases.items()
    }
    return _BaselineIndex(
        records, tuple(terms), frozen_aliases, terms_by_iri, invalid_iris)


def _baseline_index(baseline_turtle: str, baseline_summary=None) -> _BaselineIndex:
    graph = Graph()
    if baseline_turtle and str(baseline_turtle).strip():
        graph.parse(data=baseline_turtle, format="turtle")
    return _index_baseline_graph(graph, baseline_summary)


def _validate_baseline(index: _BaselineIndex) -> None:
    if not index.invalid_iris:
        return
    iri = sorted(index.invalid_iris)[0]
    raise InvalidBaselineVocabulary(
        f"baseline IRI {iri} has governed kinds {list(index.invalid_iris[iri])}")


def audit_formal_vocabulary(baseline_turtle: str) -> dict:
    """Report governed-kind collisions in Turtle without changing the vocabulary."""
    index = _baseline_index(baseline_turtle)
    name_terms = {}
    iri_collisions = []
    for record in index.records:
        names = list(dict.fromkeys([record.local_name, *record.labels]))
        details = {
            "iri": record.iri, "kinds": list(record.kinds), "names": names,
            "labels": list(record.labels), "active": record.active,
            "state": "active" if record.active else "retired",
        }
        if len(record.kinds) > 1:
            iri_collisions.append({
                "code": "iri_multiple_governed_kinds", **details,
            })
        for kind in record.kinds:
            term = {"iri": record.iri, "kind": kind, "names": names,
                    "labels": list(record.labels), "active": record.active,
                    "state": "active" if record.active else "retired"}
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


CONFLICT_PRIORITY = {
    "explicit_retired": 10,
    "explicit_iri_kind_collision": 20,
    "explicit_existing_kind_collision": 30,
    "explicit_iri_name_mismatch": 40,
    "name_ambiguity": 50,
    "name_existing_kind_collision": 60,
    "name_retired": 70,
    "batch_class_property_collision": 80,
    "batch_relation_attribute_collision": 90,
    "candidate_iri_target_collision": 100,
    "low_frequency_attribute": 110,
}


def _record_conflict(conflicts, priorities, candidates, index, rule, code, **details):
    priority = CONFLICT_PRIORITY[rule]
    if index in priorities and priorities[index] <= priority:
        return
    item = candidates[index]
    priorities[index] = priority
    conflicts[index] = {
        "candidate_id": item["id"], "kind": item["kind"],
        "name": item["name"], "iri": item.get("iri"),
        "evidence_refs": list(item.get("evidence_refs") or []),
        "code": code, **details,
    }


def _resolve_baseline_candidates(candidates, baseline):
    conflicts = {}
    priorities = {}
    resolved_iris = {}
    explicit_claims = {}
    for index, item in enumerate(candidates):
        if item.get("iri"):
            explicit_claims.setdefault(str(item["iri"]), []).append(index)
    for iri, indexes in explicit_claims.items():
        baseline_term = baseline.terms_by_iri.get(iri)
        if baseline_term and not baseline_term.active:
            continue
        kinds = sorted({candidates[index]["kind"] for index in indexes})
        if len(kinds) > 1:
            for index in indexes:
                _record_conflict(
                    conflicts, priorities, candidates, index,
                    "explicit_iri_kind_collision", "candidate_iri_kind_collision",
                    involved_iri=iri, involved_kinds=kinds)

    for index, item in enumerate(candidates):
        if index in conflicts or not item.get("iri"):
            continue
        term = baseline.terms_by_iri.get(str(item["iri"]))
        if not term:
            aliases = baseline.aliases.get(canonical_name(item["name"]), ())
            involved_iris = sorted({alias.iri for alias in aliases})
            if involved_iris:
                _record_conflict(
                    conflicts, priorities, candidates, index,
                    "explicit_iri_name_mismatch", "candidate_iri_name_mismatch",
                    involved_iris=involved_iris)
            continue
        if not term.active:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "explicit_retired", "retired_term_reuse_blocked")
        elif term.kind != item["kind"]:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "explicit_existing_kind_collision", "existing_term_kind_collision",
                existing_kind=term.kind)
        elif canonical_name(item["name"]) not in {
                canonical_name(name) for name in term.names}:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "explicit_iri_name_mismatch", "candidate_iri_name_mismatch",
                baseline_names=list(term.names))
        else:
            resolved_iris[index] = term.iri

    for index, item in enumerate(candidates):
        if index in conflicts or item.get("iri"):
            continue
        aliases = baseline.aliases.get(canonical_name(item["name"]), ())
        involved_iris = sorted({term.iri for term in aliases})
        if len(involved_iris) > 1:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "name_ambiguity", "ambiguous_baseline_name",
                involved_iris=involved_iris)
            continue
        if len(involved_iris) != 1:
            continue
        term = aliases[0]
        if term.kind != item["kind"]:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "name_existing_kind_collision", "existing_term_kind_collision",
                involved_iri=term.iri, existing_kind=term.kind)
        elif not term.active:
            _record_conflict(
                conflicts, priorities, candidates, index,
                "name_retired", "retired_term_reuse_blocked",
                involved_iri=term.iri)
        else:
            resolved_iris[index] = term.iri
    return conflicts, priorities, resolved_iris


def _detect_batch_conflicts(
        candidates, baseline, attribute_threshold, initial_conflicts,
        initial_priorities, initial_resolved_iris):
    conflicts = dict(initial_conflicts)
    priorities = dict(initial_priorities)
    resolved_iris = dict(initial_resolved_iris)
    by_name = {}
    for index, item in enumerate(candidates):
        if index not in conflicts:
            by_name.setdefault(canonical_name(item["name"]), []).append(index)
    for indexes in by_name.values():
        kinds = {candidates[index]["kind"] for index in indexes}
        if "class" in kinds and len(kinds) > 1:
            for index in indexes:
                if candidates[index]["kind"] != "class":
                    _record_conflict(
                        conflicts, priorities, candidates, index,
                        "batch_class_property_collision", "class_property_name_collision",
                        involved_kinds=sorted(kinds))
        elif {"relation", "attribute"}.issubset(kinds):
            for index in indexes:
                _record_conflict(
                    conflicts, priorities, candidates, index,
                    "batch_relation_attribute_collision", "relation_attribute_name_collision",
                    involved_kinds=["attribute", "relation"])

    same_kind_names = {}
    for index, item in enumerate(candidates):
        if index not in conflicts:
            key = (item["kind"], canonical_name(item["name"]))
            same_kind_names.setdefault(key, []).append(index)
    for indexes in same_kind_names.values():
        explicit_iris = sorted({str(candidates[index]["iri"]) for index in indexes
                                if candidates[index].get("iri")})
        if len(explicit_iris) > 1:
            for index in indexes:
                _record_conflict(
                    conflicts, priorities, candidates, index,
                    "candidate_iri_target_collision", "candidate_iri_target_collision",
                    involved_iris=explicit_iris)
        elif len(explicit_iris) == 1:
            for index in indexes:
                resolved_iris[index] = explicit_iris[0]

    if attribute_threshold > 1:
        for name, indexes in by_name.items():
            active = [index for index in indexes if index not in conflicts]
            attributes = [index for index in active
                          if candidates[index]["kind"] == "attribute"]
            existing = any(term.kind == "attribute" and term.active
                           for term in baseline.aliases.get(name, ()))
            if attributes and len(attributes) < attribute_threshold and not existing:
                for index in attributes:
                    _record_conflict(
                        conflicts, priorities, candidates, index,
                        "low_frequency_attribute", "low_frequency_attribute",
                        frequency=len(attributes), threshold=attribute_threshold)
    return conflicts, priorities, resolved_iris


@dataclass(frozen=True)
class _MergePlan:
    indexes: tuple[int, ...]
    kind: str
    canonical_name: str
    target_iri: str | None
    evidence_refs: tuple


def _plan_candidate_merges(candidates, conflicts, resolved_iris):
    groups = {}
    order = []
    for index, candidate in enumerate(candidates):
        if index in conflicts:
            continue
        target_iri = resolved_iris.get(index)
        key = (candidate["kind"], "iri", target_iri) if target_iri else (
            candidate["kind"], "name", canonical_name(candidate["name"]))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(index)

    plans = []
    for key in order:
        indexes = tuple(groups[key])
        evidence = []
        for index in indexes:
            for reference in candidates[index].get("evidence_refs") or []:
                if reference not in evidence:
                    evidence.append(reference)
        plans.append(_MergePlan(
            indexes=indexes,
            kind=key[0],
            canonical_name=canonical_name(candidates[indexes[0]]["name"]),
            target_iri=key[2] if key[1] == "iri" else None,
            evidence_refs=tuple(evidence),
        ))
    return tuple(plans)


def _assemble_normalization_result(candidates, conflicts, plans):
    accepted_by_index = {}
    binding_by_index = {}
    merged_groups = []
    for plan in plans:
        leader = candidates[plan.indexes[0]]
        for offset, index in enumerate(plan.indexes):
            payload = dict(candidates[index])
            if plan.target_iri:
                payload["iri"] = plan.target_iri
            accepted_by_index[index] = payload
            binding_by_index[index] = {
                "candidate_id": payload["id"],
                "accepted_candidate_id": leader["id"],
                "status": "accepted" if offset == 0 else "merged",
                "iri": plan.target_iri,
            }
        if len(plan.indexes) > 1:
            merged_groups.append({
                "kind": plan.kind,
                "canonical_name": plan.canonical_name,
                "candidate_ids": [candidates[index]["id"] for index in plan.indexes],
                "accepted_candidate_id": leader["id"],
                "evidence_refs": list(plan.evidence_refs),
            })

    for index in conflicts:
        binding_by_index[index] = {
            "candidate_id": candidates[index]["id"],
            "accepted_candidate_id": None, "status": "quarantined", "iri": None,
        }
    conflict_items = tuple(conflicts[index] for index in sorted(conflicts))
    bindings = tuple(binding_by_index[index] for index in sorted(binding_by_index))
    accepted = tuple(accepted_by_index[index] for index in sorted(accepted_by_index))
    merged_count = sum(len(plan.indexes) - 1 for plan in plans)
    low_frequency_count = sum(
        conflict["code"] == "low_frequency_attribute" for conflict in conflict_items)
    diagnostics = {
        "total_candidates": len(candidates),
        "accepted_count": len(plans),
        "merged_count": merged_count,
        "quarantined_count": len(conflict_items) - low_frequency_count,
        "low_frequency_attribute_count": low_frequency_count,
    }
    return NormalizationResult(
        tuple(_freeze_json(item) for item in accepted),
        tuple(_freeze_json(item) for item in conflict_items),
        tuple(_freeze_json(item) for item in merged_groups),
        tuple(_freeze_json(item) for item in bindings),
        _freeze_json(diagnostics),
    )


class DiscoveryVocabularyNormalizer:
    def __init__(self, baseline_turtle: str, *, baseline_summary=None, attribute_threshold: int = 2):
        self.baseline_turtle = baseline_turtle
        self.baseline_summary = baseline_summary
        self.attribute_threshold = attribute_threshold
        self._baseline = _baseline_index(baseline_turtle, baseline_summary)
        _validate_baseline(self._baseline)
        self._terms = self._baseline.terms
        self._aliases = self._baseline.aliases
        self._terms_by_iri = self._baseline.terms_by_iri

    def normalize(self, candidates) -> NormalizationResult:
        copied = _validate_and_copy_candidates(candidates)
        conflicts, priorities, resolved = _resolve_baseline_candidates(
            copied, self._baseline
        )
        conflicts, priorities, resolved = _detect_batch_conflicts(
            copied,
            self._baseline,
            self.attribute_threshold,
            conflicts,
            priorities,
            resolved,
        )
        plans = _plan_candidate_merges(copied, conflicts, resolved)
        return _assemble_normalization_result(copied, conflicts, plans)
