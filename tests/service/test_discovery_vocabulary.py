import json
import copy
from dataclasses import FrozenInstanceError

import pytest
from rdflib import Graph

from knowledge_service.services.discovery_vocabulary import (
    DiscoveryVocabularyNormalizer,
    InvalidBaselineVocabulary,
    InvalidDiscoveryCandidate,
    audit_formal_vocabulary,
    canonical_name,
    discovery_source_fingerprint,
    _index_baseline_graph,
)


BASELINE = """
@prefix ex: <http://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Person a owl:Class; rdfs:label "Human"@en, " PERSON ", "人"@zh .
ex:OtherPerson a owl:Class; rdfs:label "人"@zh .
ex:worksFor a owl:ObjectProperty; rdfs:label "employed by"@en .
"""


def test_canonical_name_applies_nfkc_whitespace_collapse_and_casefold():
    assert canonical_name("  Ｓtraße\t\n  Name  ") == "strasse name"
    assert canonical_name(None) == ""


def test_normalize_merges_same_kind_without_mutating_payloads_or_losing_evidence():
    candidates = [
        {"id": "c1", "kind": "class", "name": "  Person", "evidence_refs": ["e1"],
         "metadata": {"source": "one"}},
        {"id": "c2", "kind": "class", "name": "ＰＥＲＳＯＮ", "evidence_refs": ["e2", "e1"]},
        {"id": "c3", "kind": "relation", "name": "knows", "evidence_refs": ["e3"]},
    ]
    original = json.loads(json.dumps(candidates))

    result = DiscoveryVocabularyNormalizer("").normalize(candidates)

    assert [item["id"] for item in result.accepted_candidates] == ["c1", "c2", "c3"]
    assert result.accepted_candidates[0]["evidence_refs"] == ["e1"]
    assert result.merged_groups == ({
        "kind": "class", "canonical_name": "person", "candidate_ids": ["c1", "c2"],
        "accepted_candidate_id": "c1",
        "evidence_refs": ["e1", "e2"],
    },)
    assert result.candidate_bindings == (
        {"candidate_id": "c1", "accepted_candidate_id": "c1", "status": "accepted", "iri": None},
        {"candidate_id": "c2", "accepted_candidate_id": "c1", "status": "merged", "iri": None},
        {"candidate_id": "c3", "accepted_candidate_id": "c3", "status": "accepted", "iri": None},
    )
    assert result.diagnostics == {
        "total_candidates": 3,
        "accepted_count": 2,
        "merged_count": 1,
        "quarantined_count": 0,
        "low_frequency_attribute_count": 0,
    }
    assert candidates == original
    assert isinstance(result.accepted_candidates, tuple)
    json.dumps(result.accepted_candidates)
    with pytest.raises(FrozenInstanceError):
        result.conflicts = ()
    with pytest.raises(TypeError):
        result.accepted_candidates[0]["name"] = "changed"
    with pytest.raises(TypeError):
        result.accepted_candidates[0]["evidence_refs"].append("e3")
    with pytest.raises(TypeError):
        result.diagnostics["accepted_count"] = 0


def test_normalize_converts_nested_payload_containers_to_json_safe_copies():
    candidate = {"id": "c1", "kind": "class", "name": "Person",
                 "metadata": {"tags": ("human", "actor")}}

    result = DiscoveryVocabularyNormalizer("").normalize([candidate])

    assert result.accepted_candidates[0]["metadata"]["tags"] == ["human", "actor"]
    assert candidate["metadata"]["tags"] == ("human", "actor")
    json.dumps(result.accepted_candidates)


def test_baseline_registry_uses_local_all_labels_and_summary_names_without_overwrite():
    summary = {"relations": [{"id": "http://example.test/worksFor", "name": "worksFor",
                               "label": "Employment", "label_zh": "任职于", "label_en": ""}]}
    candidates = [
        {"id": "local", "kind": "class", "name": "Person"},
        {"id": "label", "kind": "class", "name": " HUMAN "},
        {"id": "summary", "kind": "relation", "name": "Employment"},
        {"id": "ambiguous", "kind": "class", "name": "人"},
    ]

    result = DiscoveryVocabularyNormalizer(BASELINE, baseline_summary=summary).normalize(candidates)

    assert [item["id"] for item in result.accepted_candidates] == ["label", "local", "summary"]
    assert [item["iri"] for item in result.accepted_candidates] == [
        "http://example.test/Person", "http://example.test/Person",
        "http://example.test/worksFor"]
    assert result.merged_groups[0]["candidate_ids"] == ["label", "local"]
    assert result.conflicts == ({
        "candidate_id": "ambiguous", "kind": "class", "name": "人", "iri": None,
        "evidence_refs": [],
        "code": "ambiguous_baseline_name",
        "involved_iris": ["http://example.test/OtherPerson", "http://example.test/Person"],
    },)
    assert result.diagnostics["accepted_count"] == 2
    assert result.diagnostics["merged_count"] == 1
    assert result.diagnostics["quarantined_count"] == 1


def test_baseline_iri_declared_as_two_governed_kinds_is_invalid():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    ex:bad a owl:Class, owl:ObjectProperty .
    """
    with pytest.raises(InvalidBaselineVocabulary, match="http://example.test/bad"):
        DiscoveryVocabularyNormalizer(baseline)


def test_explicit_iri_precedence_blocks_retired_wrong_kind_and_name_mismatch():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Old a owl:Class; rdfs:label "Old"; owl:deprecated true .
    ex:rel a owl:ObjectProperty; rdfs:label "works for" .
    ex:Person a owl:Class; rdfs:label "Human" .
    """
    candidates = [
        {"id": "retired", "kind": "class", "name": "Old", "iri": "http://example.test/Old"},
        {"id": "kind", "kind": "attribute", "name": "works for", "iri": "http://example.test/rel"},
        {"id": "name", "kind": "class", "name": "Organization", "iri": "http://example.test/Person"},
    ]

    result = DiscoveryVocabularyNormalizer(baseline).normalize(candidates)

    assert [(item["candidate_id"], item["code"]) for item in result.conflicts] == [
        ("kind", "existing_term_kind_collision"),
        ("name", "candidate_iri_name_mismatch"),
        ("retired", "retired_term_reuse_blocked"),
    ]
    assert result.diagnostics["quarantined_count"] == 3
    assert result.accepted_candidates == ()


def test_explicit_retired_iri_precedes_kind_mismatch():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    ex:Old a owl:Class; owl:deprecated true .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "relation", "name": "Old",
         "iri": "http://example.test/Old"},
    ])

    assert result.conflicts[0]["code"] == "retired_term_reuse_blocked"

    multiple = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "class", "kind": "class", "name": "Old",
         "iri": "http://example.test/Old"},
        {"id": "relation", "kind": "relation", "name": "Old",
         "iri": "http://example.test/Old"},
    ])
    assert [item["code"] for item in multiple.conflicts] == [
        "retired_term_reuse_blocked", "retired_term_reuse_blocked"]


def test_name_resolution_blocks_unique_retired_and_different_kind_aliases():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Old a owl:Class; rdfs:label "legacy person"; owl:deprecated true .
    ex:worksFor a owl:ObjectProperty; rdfs:label "employment" .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "retired", "kind": "class", "name": "legacy person"},
        {"id": "wrong-kind", "kind": "class", "name": "employment"},
    ])

    assert [(item["candidate_id"], item["code"]) for item in result.conflicts] == [
        ("retired", "retired_term_reuse_blocked"),
        ("wrong-kind", "existing_term_kind_collision"),
    ]


def test_unknown_explicit_iri_cannot_claim_an_existing_formal_name():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Person a owl:Class; rdfs:label "Human" .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "Human",
         "iri": "http://candidate.test/Person"},
    ])

    assert result.accepted_candidates == ()
    assert result.conflicts[0]["code"] == "candidate_iri_name_mismatch"
    assert result.conflicts[0]["iri"] == "http://candidate.test/Person"
    assert result.conflicts[0]["involved_iris"] == ["http://example.test/Person"]


def test_validated_known_explicit_iri_disambiguates_a_shared_alias():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Person a owl:Class; rdfs:label "Human" .
    ex:Actor a owl:Class; rdfs:label "Human" .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "Human",
         "iri": "http://example.test/Person"},
    ])

    assert result.conflicts == ()
    assert result.accepted_candidates[0]["iri"] == "http://example.test/Person"


def test_quarantined_conflicts_retain_all_evidence_references():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    ex:Old a owl:Class; owl:deprecated true .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "Old",
         "iri": "http://example.test/Old", "evidence_refs": ["e2", "e1"]},
    ])

    assert result.conflicts[0]["evidence_refs"] == ["e2", "e1"]


def test_multiple_candidate_kinds_claiming_same_explicit_iri_are_all_quarantined():
    candidates = [
        {"id": "c1", "kind": "class", "name": "Person", "iri": "urn:shared"},
        {"id": "c2", "kind": "relation", "name": "employs", "iri": "urn:shared"},
        {"id": "c3", "kind": "class", "name": "Human", "iri": "urn:other"},
    ]

    result = DiscoveryVocabularyNormalizer("").normalize(candidates)

    assert [item["id"] for item in result.accepted_candidates] == ["c3"]
    assert [(item["candidate_id"], item["code"]) for item in result.conflicts] == [
        ("c1", "candidate_iri_kind_collision"),
        ("c2", "candidate_iri_kind_collision"),
    ]


def test_cross_kind_precedence_and_low_frequency_diagnostics_are_exclusive():
    candidates = [
        {"id": "class", "kind": "class", "name": "Status"},
        {"id": "class-property", "kind": "attribute", "name": " status "},
        {"id": "relation", "kind": "relation", "name": "Owner"},
        {"id": "relation-attribute", "kind": "attribute", "name": "owner"},
        {"id": "rare", "kind": "attribute", "name": "serial number"},
    ]

    result = DiscoveryVocabularyNormalizer("", attribute_threshold=2).normalize(candidates)

    assert [item["id"] for item in result.accepted_candidates] == ["class"]
    assert {item["candidate_id"]: item["code"] for item in result.conflicts} == {
        "class-property": "class_property_name_collision",
        "relation": "relation_attribute_name_collision",
        "relation-attribute": "relation_attribute_name_collision",
        "rare": "low_frequency_attribute",
    }
    assert result.diagnostics == {
        "total_candidates": 5,
        "accepted_count": 1,
        "merged_count": 0,
        "quarantined_count": 3,
        "low_frequency_attribute_count": 1,
    }


def test_default_attribute_threshold_quarantines_a_single_new_attribute():
    result = DiscoveryVocabularyNormalizer("").normalize([
        {"id": "rare", "kind": "attribute", "name": "serial number"},
    ])

    assert result.conflicts[0]["code"] == "low_frequency_attribute"
    assert result.conflicts[0]["threshold"] == 2
    assert result.diagnostics["quarantined_count"] == 0
    assert result.diagnostics["low_frequency_attribute_count"] == 1


def test_normalization_is_deterministic_when_candidate_order_changes():
    candidates = [
        {"id": "z", "kind": "class", "name": " Person ",
         "evidence_refs": ["e3", "e1"]},
        {"id": "r", "kind": "relation", "name": "knows",
         "evidence_refs": ["e4"]},
        {"id": "a", "kind": "class", "name": "ＰＥＲＳＯＮ",
         "evidence_refs": ["e2", "e1"]},
    ]
    normalizer = DiscoveryVocabularyNormalizer("")

    forward = normalizer.normalize(candidates)
    backward = normalizer.normalize(list(reversed(candidates)))

    assert forward == backward
    assert [item["id"] for item in forward.accepted_candidates] == ["a", "r", "z"]
    assert forward.merged_groups[0]["accepted_candidate_id"] == "a"
    assert [item["candidate_id"] for item in forward.candidate_bindings] == ["a", "r", "z"]


def test_ambiguous_baseline_alias_precedes_low_frequency_attribute():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:first a owl:Class; rdfs:label "code" .
    ex:second a owl:ObjectProperty; rdfs:label "code" .
    """

    result = DiscoveryVocabularyNormalizer(baseline, attribute_threshold=2).normalize([
        {"id": "candidate", "kind": "attribute", "name": "code"},
    ])

    assert result.conflicts[0]["code"] == "ambiguous_baseline_name"
    assert result.diagnostics["low_frequency_attribute_count"] == 0


def test_explicit_and_name_only_candidates_merge_when_resolving_same_baseline_iri():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Person a owl:Class; rdfs:label "Human" .
    """
    candidates = [
        {"id": "z", "kind": "class", "name": "Human",
         "iri": "http://example.test/Person", "evidence_refs": ["explicit"]},
        {"id": "a", "kind": "class", "name": "Human",
         "evidence_refs": ["name-only"]},
    ]

    result = DiscoveryVocabularyNormalizer(baseline).normalize(candidates)

    assert [item["id"] for item in result.accepted_candidates] == ["a", "z"]
    assert [item["iri"] for item in result.accepted_candidates] == [
        "http://example.test/Person", "http://example.test/Person"]
    assert [item["evidence_refs"] for item in result.accepted_candidates] == [
        ["name-only"], ["explicit"]]
    assert result.merged_groups[0]["candidate_ids"] == ["a", "z"]
    assert result.candidate_bindings == (
        {"candidate_id": "a", "accepted_candidate_id": "a", "status": "accepted",
         "iri": "http://example.test/Person"},
        {"candidate_id": "z", "accepted_candidate_id": "a", "status": "merged",
         "iri": "http://example.test/Person"},
    )


def test_baseline_meaning_precedes_current_batch_cross_kind_rules():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:status a owl:ObjectProperty; rdfs:label "status" .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "class", "kind": "class", "name": "status"},
        {"id": "relation", "kind": "relation", "name": "status"},
    ])

    assert [(item["id"], item["iri"]) for item in result.accepted_candidates] == [
        ("relation", "http://example.test/status")]
    assert [(item["candidate_id"], item["code"]) for item in result.conflicts] == [
        ("class", "existing_term_kind_collision")]


def test_name_only_kind_collision_precedes_retirement():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:legacy a owl:ObjectProperty; rdfs:label "legacy"; owl:deprecated true .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "legacy"},
    ])

    assert result.conflicts[0]["code"] == "existing_term_kind_collision"


def test_urn_local_name_reuses_baseline_term_without_labels():
    baseline = """
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    <urn:knowledge:ontology:project:Term> a owl:Class .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "Term"},
    ])

    assert result.accepted_candidates[0]["iri"] == "urn:knowledge:ontology:project:Term"


def test_audit_uses_urn_local_name_without_labels():
    baseline = """
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    <urn:knowledge:ontology:project:Term> a owl:Class .
    <urn:knowledge:ontology:other:Term> a owl:ObjectProperty .
    """

    audit = audit_formal_vocabulary(baseline)

    assert audit["name_collisions"][0]["canonical_name"] == "term"
    assert audit["name_collisions"][0]["iris"] == [
        "urn:knowledge:ontology:other:Term",
        "urn:knowledge:ontology:project:Term",
    ]


def test_same_kind_merge_retains_single_explicit_new_iri_when_leader_has_none():
    result = DiscoveryVocabularyNormalizer("").normalize([
        {"id": "a", "kind": "class", "name": "NovelTerm",
         "evidence_refs": ["implicit"]},
        {"id": "z", "kind": "class", "name": "novelterm",
         "iri": "urn:knowledge:ontology:project:NovelTerm",
         "evidence_refs": ["explicit"]},
    ])

    assert [(item["id"], item["iri"]) for item in result.accepted_candidates] == [
        ("a", "urn:knowledge:ontology:project:NovelTerm"),
        ("z", "urn:knowledge:ontology:project:NovelTerm")]
    assert [item["evidence_refs"] for item in result.accepted_candidates] == [
        ["implicit"], ["explicit"]]
    assert [item["iri"] for item in result.candidate_bindings] == [
        "urn:knowledge:ontology:project:NovelTerm",
        "urn:knowledge:ontology:project:NovelTerm",
    ]


def test_same_kind_merge_quarantines_multiple_distinct_explicit_new_iris():
    result = DiscoveryVocabularyNormalizer("").normalize([
        {"id": "a", "kind": "class", "name": "Concept"},
        {"id": "b", "kind": "class", "name": "concept", "iri": "urn:new:Concept"},
        {"id": "c", "kind": "class", "name": "ＣＯＮＣＥＰＴ", "iri": "urn:other:Concept"},
    ])

    assert result.accepted_candidates == ()
    assert [item["candidate_id"] for item in result.conflicts] == ["a", "b", "c"]
    assert {item["code"] for item in result.conflicts} == {"candidate_iri_target_collision"}
    assert all(item["involved_iris"] == ["urn:new:Concept", "urn:other:Concept"]
               for item in result.conflicts)


def test_vocabulary_merge_preserves_every_relation_and_attribute_payload():
    candidates = [
        {"id": "rel-b", "kind": "relation", "name": "owns",
         "subject_id": "s2", "object_id": "o2", "evidence_refs": ["r2"]},
        {"id": "attr-b", "kind": "attribute", "name": "code",
         "entity_id": "e2", "value": "B", "evidence_refs": ["a2"]},
        {"id": "rel-a", "kind": "relation", "name": "OWNS",
         "subject_id": "s1", "object_id": "o1", "evidence_refs": ["r1"]},
        {"id": "attr-a", "kind": "attribute", "name": "ＣＯＤＥ",
         "entity_id": "e1", "value": "A", "evidence_refs": ["a1"]},
    ]

    forward = DiscoveryVocabularyNormalizer("").normalize(candidates)
    backward = DiscoveryVocabularyNormalizer("").normalize(list(reversed(candidates)))

    assert forward == backward
    assert [item["id"] for item in forward.accepted_candidates] == [
        "attr-a", "attr-b", "rel-a", "rel-b"]
    assert [(item["entity_id"], item["value"], list(item["evidence_refs"]))
            for item in forward.accepted_candidates[:2]] == [
        ("e1", "A", ["a1"]), ("e2", "B", ["a2"])]
    assert [(item["subject_id"], item["object_id"], list(item["evidence_refs"]))
            for item in forward.accepted_candidates[2:]] == [
        ("s1", "o1", ["r1"]), ("s2", "o2", ["r2"])]
    assert [group["candidate_ids"] for group in forward.merged_groups] == [
        ["attr-a", "attr-b"], ["rel-a", "rel-b"]]
    assert [group["evidence_refs"] for group in forward.merged_groups] == [
        ["a1", "a2"], ["r1", "r2"]]


def test_rdfs_class_is_reused_and_included_in_formal_vocabulary_audit():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:Person a rdfs:Class .
    ex:personRelation a owl:ObjectProperty; rdfs:label "Person" .
    """

    result = DiscoveryVocabularyNormalizer(baseline).normalize([
        {"id": "candidate", "kind": "class", "name": "Person",
         "iri": "http://example.test/Person"},
    ])
    audit = audit_formal_vocabulary(baseline)

    assert result.accepted_candidates[0]["iri"] == "http://example.test/Person"
    collision = next(item for item in audit["name_collisions"]
                     if item["canonical_name"] == "person")
    assert collision["kinds"] == ["class", "relation"]
    assert collision["iris"] == [
        "http://example.test/Person", "http://example.test/personRelation"]


@pytest.mark.parametrize(("candidate", "message"), [
    ({"id": "", "kind": "class", "name": "Person"}, "non-empty string id"),
    ({"id": "c", "kind": None, "name": "Person"}, "governed kind"),
    ({"id": "c", "kind": "unknown", "name": "Person"}, "governed kind"),
    ({"id": "c", "kind": "class", "name": "  \t"}, "non-empty name"),
    ({"id": "c", "kind": "class", "name": "Person", "iri": "not an iri"},
     "valid absolute IRI"),
    ({"id": "c", "kind": "class", "name": "Person", "evidence_refs": "e1"},
     "evidence_refs container"),
    ({"id": "c", "kind": "class", "name": "Person", "evidence_refs": {"e1": 1}},
     "evidence_refs container"),
])
def test_candidate_boundary_rejects_malformed_rows(candidate, message):
    with pytest.raises(InvalidDiscoveryCandidate, match=message):
        DiscoveryVocabularyNormalizer("").normalize([candidate])


def test_candidate_boundary_rejects_duplicate_ids_deterministically():
    candidates = [
        {"id": "same", "kind": "class", "name": "Person"},
        {"id": "same", "kind": "class", "name": "Organization"},
    ]

    with pytest.raises(InvalidDiscoveryCandidate, match="duplicate candidate id: same"):
        DiscoveryVocabularyNormalizer("").normalize(candidates)


def test_baseline_index_traverses_graph_once_and_retains_term_metadata():
    class CountingGraph(Graph):
        def __init__(self):
            super().__init__()
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    graph = CountingGraph()
    graph.parse(data="""
        @prefix ex: <http://example.test/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:Person a rdfs:Class; rdfs:label "Human"; owl:deprecated true .
    """, format="turtle")

    index = _index_baseline_graph(graph)

    assert graph.iterations == 1
    term = index.terms_by_iri["http://example.test/Person"]
    assert term.kind == "class"
    assert term.names == ("Person", "Human")
    assert term.active is False


def test_audit_reports_cross_kind_names_and_multi_kind_iris_without_candidates():
    baseline = """
    @prefix ex: <http://example.test/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    ex:shared a owl:Class; rdfs:label "Thing"@en .
    ex:property a owl:ObjectProperty; rdfs:label "ＴＨＩＮＧ"@en; owl:deprecated true .
    ex:both a owl:Class, owl:DatatypeProperty; rdfs:label "Double" .
    """

    audit = audit_formal_vocabulary(baseline)

    thing = next(item for item in audit["name_collisions"]
                 if item["canonical_name"] == "thing")
    assert thing["code"] == "canonical_name_multiple_governed_kinds"
    assert thing["iris"] == ["http://example.test/property", "http://example.test/shared"]
    assert thing["kinds"] == ["class", "relation"]
    assert thing["terms"][0]["state"] == "retired"
    iri = next(item for item in audit["iri_collisions"]
               if item["iri"] == "http://example.test/both")
    assert iri == {
        "code": "iri_multiple_governed_kinds",
        "iri": "http://example.test/both",
        "kinds": ["attribute", "class"],
        "names": ["both", "Double"],
        "labels": ["Double"],
        "active": True,
        "state": "active",
    }
    json.dumps(audit)


def test_discovery_fingerprint_is_order_independent_and_binds_every_generation_input():
    candidates = [
        {"id": "b", "kind": "relation", "name": "knows", "document_version_id": "dv2"},
        {"id": "a", "kind": "class", "name": "Person", "document_version_id": "dv1"},
    ]
    options = {"language": "zh", "model": {"temperature": 0, "top_p": 1}}
    kwargs = {
        "runtime_version": "semantica-runtime-0.6.7",
        "attribute_threshold": 2,
        "request_name": "initial discovery",
        "generation_options": options,
    }

    fingerprint = discovery_source_fingerprint("project", "ontology-v1", candidates, **kwargs)

    assert fingerprint.startswith("sha256:")
    assert len(fingerprint) == 71
    assert fingerprint == discovery_source_fingerprint(
        "project", "ontology-v1", list(reversed(candidates)), **kwargs)
    assert fingerprint == discovery_source_fingerprint(
        "project", "ontology-v1", candidates,
        generator_contract="semantica-0.6.7", **kwargs)
    reordered_options = {"model": {"top_p": 1, "temperature": 0}, "language": "zh"}
    assert fingerprint == discovery_source_fingerprint(
        "project", "ontology-v1", candidates,
        **{**kwargs, "generation_options": reordered_options})

    variants = [
        (("other-project", "ontology-v1", candidates), kwargs),
        (("project", "ontology-v2", candidates), kwargs),
        (("project", "ontology-v1", [{**candidates[0], "document_version_id": "dv3"}, candidates[1]]), kwargs),
        (("project", "ontology-v1", [{**candidates[0], "confidence": .9}, candidates[1]]), kwargs),
        (("project", "ontology-v1", candidates), {**kwargs, "normalizer_version": "v2"}),
        (("project", "ontology-v1", candidates), {**kwargs, "generator_contract": "semantica-0.7"}),
        (("project", "ontology-v1", candidates), {**kwargs, "runtime_version": "runtime-other"}),
        (("project", "ontology-v1", candidates), {**kwargs, "attribute_threshold": 3}),
        (("project", "ontology-v1", candidates), {**kwargs, "request_name": "renamed"}),
        (("project", "ontology-v1", candidates),
         {**kwargs, "generation_options": {**copy.deepcopy(options), "seed": 7}}),
    ]
    assert all(discovery_source_fingerprint(*args, **variant) != fingerprint
               for args, variant in variants)
