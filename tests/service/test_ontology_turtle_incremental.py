"""Named Turtle diff keeps atomic prefixes, review metadata and bounded graph work."""
from collections import Counter
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, XSD

from knowledge_service.services import ontology_drafts_commands as draft_commands
from knowledge_service.services import ontology_operations as rdf_ops
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts_commands import DraftCommandMixin
from knowledge_service.services.ontology_drafts_core import _fingerprint
from knowledge_service.services.ontology_operations import (
    _TurtleOperationReplay, apply_operations, build_operation, operation_fingerprint,
)

PREFIX = '''@prefix ex: <https://schema.example/terms#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
'''
NS = 'https://schema.example/terms#'
DRAFT = {'id': 'd', 'revision': 1, 'source_kind': 'manual', 'status': 'pending',
         'base_ontology_id': None}


def graph(ttl):
    return Graph().parse(data=ttl, format='turtle')


def compiler(records=()):
    service = DraftCommandMixin()
    service.repository = SimpleNamespace(current_records=Mock(return_value=list(records)),
                                         unit_of_work=nullcontext)
    service._authoritative_evidence = Mock(return_value=[{'source': 'review-evidence'}])
    return service


def test_named_diff_matches_serialized_prefix_metadata_and_rdf():
    base = PREFIX + '''ex:A a owl:Class .
ex:Top a rdfs:Class ; ex:z ex:A ; rdfs:label ex:A .
ex:B a owl:Class ; rdfs:subClassOf ex:A .
ex:r a owl:ObjectProperty ; rdfs:domain ex:A ; rdfs:range ex:B .
ex:amount a owl:DatatypeProperty ; rdfs:domain ex:A ; rdfs:range xsd:string .'''
    edited = base.replace('xsd:string', 'xsd:integer') + '''
ex:A rdfs:label "类别 A"@zh ; ex:typed "18"^^xsd:integer .
ex:amount rdfs:label "数量"@zh .
ex:Y a owl:Class ; rdfs:subClassOf ex:B .
ex:Z a owl:Class ; ex:reference ex:Y ; rdfs:subClassOf ex:Y .'''
    anonymous = {'status': 'pending', 'proposed_type': 'A'}
    records = [
        {'id': 'linked', 'kind': 'relation', 'type': NS+'r', 'subject_id': 'e', 'object_id': 'e'},
        {'id': 'e', 'kind': 'entity', 'type': NS+'A', 'text': '  name ', 'properties': {NS+'amount': '2'},
         'metadata': {'review_candidates': [{'id': 'p', 'status': 'pending', 'target_type': NS+'A'}],
                      'discovery_candidates': [anonymous, {'id': 'p', 'status': 'pending', 'predicate': 'A'}]}},
        {'id': 'direct', 'kind': 'relation', 'type': NS+'A', 'subject_id': 'e'},
    ]
    caller = Ontology(base)
    service = compiler(records)
    command = {'action': 'diff_turtle', 'edited_turtle': edited, 'reason': 'review', 'confidence': .99}
    actual = service._compile_command('p', DRAFT, command, caller)
    service.repository.current_records.assert_called_once_with('p')
    # The control follows precisely the old per-atom Turtle round trip.
    reference = service._rebuild_operations('p', DRAFT, rdf_ops.canonical_turtle_diff(
        caller.graph.serialize(format='turtle'), edited, published=False), caller,
        {**command, 'action': 'force-legacy-replay'})
    assert actual == reference
    assert all(operation_fingerprint(atom) == atom['fingerprint'] for atom in actual)
    annotated = next(o for o in actual if o['target_iri'] == NS+'A')
    impact = annotated['impact']
    assert annotated['risk'] == 'high' and annotated['reason'] == 'review'
    assert annotated['evidence'] == [{'source': 'review-evidence'}]
    assert impact['record_ids'] == ['e', 'direct', 'linked']
    assert [r['via'] for r in impact['record_preview']] == ['type', 'type', 'endpoint']
    assert impact['pending_candidate_ids'] == sorted(['p', _fingerprint(anonymous)])
    assert impact['descendants'] == 1 and impact['constraints_detail'][0]['subject'] == NS+'Top'
    datatype = next(o for o in actual if o['action'] == 'set_datatype')
    assert datatype['before'] == {'datatype': str(XSD.string)} and datatype['risk'] == 'high'
    result = graph(apply_operations(base, actual))
    assert isomorphic(result, graph(edited))
    assert (URIRef(NS+'amount'), RDF.type, OWL.DatatypeProperty) in result
    assert (URIRef(NS+'A'), RDFS.label, Literal('类别 A', lang='zh')) in result
    assert isomorphic(caller.graph, graph(base))


def test_failed_atom_cannot_change_next_prefix_even_after_partial_mutation():
    turtle = PREFIX + 'ex:A a owl:Class . ex:B a owl:Class ; rdfs:subClassOf ex:A .'
    replay = _TurtleOperationReplay(turtle)
    cycle = build_operation('add_parent', NS+'A', after={'value': NS+'B'})
    with pytest.raises(ValueError, match='cycle'):
        replay.apply(cycle)
    # retire_term sets owl:deprecated before checking the invalid replacement.
    retire = build_operation('retire_term', NS+'A', after={'replacement': NS+'Missing'})
    with pytest.raises(ValueError, match='不存在'):
        replay.apply(retire)
    assert isomorphic(replay.ontology.graph, graph(turtle))
    replay.apply(build_operation('add_annotation', NS+'A', after={
        'predicate': str(RDFS.label), 'value': 'still-active'}))
    replay.finish()
    assert replay.ontology.is_active_term(URIRef(NS+'A'))


def test_command_keeps_blocked_prefix_separate_and_rejects_invalid_diff(monkeypatch):
    base = PREFIX + 'ex:A a owl:Class . ex:B a owl:Class ; rdfs:subClassOf ex:A .'
    edited = PREFIX + 'ex:A a owl:Class . ex:B a owl:Class .'
    service = compiler()
    service.store = SimpleNamespace(get=Mock(return_value=DRAFT), append_operations=Mock())
    service._assert_revision = Mock()
    service._check_current = Mock()
    service._base_turtle = Mock(return_value=base)
    service._ontology_for_draft = Mock(return_value=(base, []))
    service._cas = Mock(return_value={**DRAFT, 'revision': 2})
    service._preview = Mock(side_effect=lambda _p, draft: draft)
    with pytest.raises(ValueError):
        service.command('p', 'd', 1, {'action': 'diff_turtle', 'edited_turtle': edited + 'invalid['})
    service.store.append_operations.assert_not_called()
    service._cas.assert_not_called()
    # Normally canonical diff rejects a cycle during its first replay. Inject
    # a bad atom after that gate to exercise the command's independent guard.
    original = draft_commands.canonical_turtle_diff
    monkeypatch.setattr(draft_commands, 'canonical_turtle_diff', lambda *a, **kw: [
        build_operation('add_parent', NS+'A', after={'value': NS+'B'}),
        *original(*a, **kw),
    ])
    service.command('p', 'd', 1, {'action': 'diff_turtle', 'edited_turtle': edited})
    atoms = service.store.append_operations.call_args.args[2]
    blocked, removes = atoms
    assert blocked['action'] == 'add_parent'
    assert blocked['validation']['errors'][0]['code'] == 'operation_blocked'
    assert operation_fingerprint(blocked) == blocked['fingerprint']
    assert removes['action'] == 'remove_parent' and not removes['validation'].get('errors')


def test_named_diff_does_not_round_trip_or_validate_whole_graph_per_atom(monkeypatch):
    calls = Counter()
    serialize, validate = Graph.serialize, rdf_ops._validate_definition_graph

    def counted_serialization(self, *args, **kwargs):
        calls['serialize'] += 1
        return serialize(self, *args, **kwargs)

    def counted_validation(*args, **kwargs):
        calls['validate'] += 1
        return validate(*args, **kwargs)

    monkeypatch.setattr(Graph, 'serialize', counted_serialization)
    monkeypatch.setattr(rdf_ops, '_validate_definition_graph', counted_validation)
    observations = []
    for size in (8, 32):
        calls.clear()
        edited = PREFIX + '\n'.join(f'ex:C{i} a owl:Class ; rdfs:label "{i}" .' for i in range(size))
        service = compiler()
        result = service._compile_command('p', DRAFT, {'action': 'diff_turtle',
            'edited_turtle': edited}, Ontology(''))
        assert len(result) == 2 * size
        service.repository.current_records.assert_called_once_with('p')
        observations.append(calls.copy())
    assert observations[0] == observations[1]
    assert observations[1]['serialize'] < 10 and observations[1]['validate'] < 10
