"""Confirmed intake batches keep sequential command semantics with bounded reads."""
from copy import deepcopy

import pytest
from rdflib import BNode, Graph, RDFS, URIRef
from rdflib.compare import isomorphic

from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts_core import RevisionConflict
from knowledge_service.services.ontology_intake import OntologyIntake, source_context
from knowledge_service.services.ontology_operations import apply_operations, build_operation
from test_ontology_intake import _scoped_source, _setup


def _selection(tmp_path, size):
    app, repo, project, _ = _setup(tmp_path)
    candidates = [{'id': f'entity-{index:03}', 'kind': 'entity', 'text': f'instance-{index}',
                   'proposed_type': f'Type-{index:03}'} for index in range(size)]
    candidates.extend([
        {'id': 'field', 'kind': 'attribute', 'entity_id': 'entity-000',
         'proposed_type': 'Code', 'value': 'ABC'},
        {'id': 'relation', 'kind': 'relation', 'subject_id': 'entity-000',
         'object_id': f'entity-{size-1:03}', 'proposed_type': 'Supports'},
    ])
    _scoped_source(repo, project, 'batch-source', candidates)
    intake = OntologyIntake(app.state.service)
    body = {'candidate_ids': [x['id'] for x in candidates], 'expected_ontology_id': None,
            'create_new_draft': True, 'name': 'batch equivalence'}
    return intake, repo, project, body


@pytest.mark.parametrize('size', [1, 5, 24])
def test_add_matches_sequential_commands_with_bounded_graph_and_record_work(tmp_path, monkeypatch, size):
    intake, repo, project, body = _selection(tmp_path, size)
    drafts = intake.drafts
    preview, _, _ = intake._plan(project, body)
    assert preview['can_add'], preview['conflicts']
    # Select the complete required definition but omit comments, proving that
    # batch compilation respects the explicitly confirmed suggestion subset.
    selected = [x for x in preview['suggestions'] if not (
        x['action'] == 'add_annotation' and x['after']['predicate'] == str(RDFS.comment))]
    request = {**body, 'source_fingerprint': preview['source_fingerprint'],
               'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'batch',
               'selected_suggestion_fingerprints': [x['fingerprint'] for x in reversed(selected)]}
    source_draft = {'source_kind': 'discovery', 'source_context': drafts._refresh_source_context(
        project, source_context(preview['associated_fact_scope']))}
    expected, turtle = [], ''
    for suggestion in selected:
        operations = drafts._compile_command(project, source_draft, {
            **suggestion, 'reason': '明确选择资料候选加入本轮修改'}, Ontology(turtle))
        turtle = apply_operations(turtle, operations)
        expected.extend(operations)
    counts = {'parse': 0, 'records': 0}
    repository_type = type(drafts.repository)
    original_parse, original_records = Graph.parse, repository_type.current_records
    original_append = drafts.store.append_operations
    compiled = []

    def parse(graph, *args, **kwargs):
        counts['parse'] += 1
        return original_parse(graph, *args, **kwargs)

    def records(repository, *args, **kwargs):
        counts['records'] += 1
        return original_records(repository, *args, **kwargs)

    def append(project_id, draft_id, operations):
        compiled.extend(deepcopy(operations))
        return original_append(project_id, draft_id, operations)

    with monkeypatch.context() as measured:
        measured.setattr(Graph, 'parse', parse)
        measured.setattr(repository_type, 'current_records', records)
        measured.setattr(drafts.store, 'append_operations', append)
        actual = intake.add(project, request, actor='author')
    operations = actual['draft']['operations']
    assert compiled == expected
    stored_fields = ('action', 'target_iri', 'before', 'after', 'fingerprint',
                     'evidence', 'risk', 'validation', 'impact', 'reason')
    assert [{key: item[key] for key in stored_fields} for item in operations] == [
        {key: item[key] for key in stored_fields} for item in expected]
    assert actual['suggestions'] == selected
    assert isomorphic(Ontology(actual['draft']['turtle']).graph, Ontology(turtle).graph)
    assert all(operation['evidence'] for operation in operations)
    assert len(repo.list_intake_runs(project)) == 1
    assert intake.add(project, request, actor='author')['receipt'] == actual['receipt']
    # These budgets include planning, final preview and frozen-source reads;
    # increasing the number of selected terms must not add full graph parses
    # or full-project record reads per suggestion.
    assert counts['parse'] <= 28, counts
    assert counts['records'] <= 10, counts


def test_intake_compiler_rejects_invalid_prefix_even_when_final_graph_is_valid(tmp_path):
    intake, _, project, _ = _selection(tmp_path, 1)
    original = Ontology('''@prefix sh: <http://www.w3.org/ns/shacl#> .
        <urn:test:Shape> a sh:PropertyShape ; sh:path <urn:test:Field> .''')
    commands = [
        {'action': 'create_term', 'target_iri': 'urn:test:Other', 'after': {'kind': 'class'}},
        {'action': 'create_term', 'target_iri': 'urn:test:Field', 'after': {'kind': 'attribute'}},
    ]
    raw = [build_operation(**command) for command in commands]
    # The string API validates at its call boundary; a whole-batch-only check
    # would accept this sequence, unlike the old per-command intake path.
    apply_operations(original.graph.serialize(format='turtle'), raw)
    before = set(original.graph)
    with pytest.raises(ValueError, match='SHACL path'):
        intake.drafts._compile_intake_commands(project, {'source_kind': 'discovery'}, commands, original)
    assert set(original.graph) == before


def test_intake_compiler_error_does_not_mutate_callers_graph(tmp_path):
    intake, _, project, _ = _selection(tmp_path, 1)
    original = Ontology('')
    commands = [
        {'action': 'create_term', 'target_iri': 'urn:test:Field', 'kind': 'attribute'},
        {'action': 'add_domain', 'target_iri': 'urn:test:Field', 'value': 'urn:test:Missing'},
    ]
    before = deepcopy(original.summary())
    with pytest.raises(ValueError, match='不存在'):
        intake.drafts._compile_intake_commands(project, {'source_kind': 'discovery'}, commands, original)
    assert original.summary() == before
    assert not original.graph


def test_intake_compiler_reuses_records_without_changing_nonempty_impacts(tmp_path, monkeypatch):
    intake, _, project, _ = _selection(tmp_path, 1)
    rows = [
        {'id': 'entity', 'kind': 'entity', 'type': 'urn:test:Class', 'text': 'a record'},
        {'id': 'linked', 'kind': 'relation', 'type': 'urn:test:Link',
         'subject_id': 'entity', 'object_id': 'entity'},
        {'id': 'pending', 'kind': 'document', 'metadata': {'discovery_candidates': [
            {'id': 'candidate', 'status': 'pending', 'proposed_type': 'Class'}]}},
    ]
    reads = []

    def records(*_args, **_kwargs):
        reads.append(True)
        return deepcopy(rows)

    monkeypatch.setattr(intake.drafts.repository, 'current_records', records)
    ontology = Ontology('''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Class> a owl:Class .
        <urn:test:Existing> a owl:ObjectProperty ; rdfs:domain <urn:test:Class> .''')
    commands = [
        {'action': 'create_term', 'target_iri': 'urn:test:AField', 'kind': 'attribute'},
        {'action': 'add_domain', 'target_iri': 'urn:test:AField', 'value': 'urn:test:Class'},
        {'action': 'set_datatype', 'target_iri': 'urn:test:AField',
         'datatype': 'http://www.w3.org/2001/XMLSchema#string'},
        {'action': 'create_term', 'target_iri': 'urn:test:Link', 'kind': 'relation'},
        {'action': 'add_domain', 'target_iri': 'urn:test:Link', 'value': 'urn:test:Class'},
        {'action': 'add_range', 'target_iri': 'urn:test:Link', 'value': 'urn:test:Class'},
        {'action': 'create_term', 'target_iri': 'urn:test:Child', 'kind': 'class'},
        {'action': 'add_parent', 'target_iri': 'urn:test:Child', 'parent_iri': 'urn:test:Class'},
        {'action': 'add_annotation', 'target_iri': 'urn:test:Class',
         'predicate': str(RDFS.label), 'value': 'Class', 'language': 'en'},
    ]
    draft = {'source_kind': 'discovery', 'source_context': {'documents': [
        {'document_id': 'doc', 'expected_document_version_id': 'doc-v1', 'candidate_ids': ['candidate']}]}}
    expected, turtle = [], ontology.graph.serialize(format='turtle')
    for command in commands:
        operations = intake.drafts._compile_command(project, draft, command, Ontology(turtle))
        expected.extend(operations)
        turtle = apply_operations(turtle, operations)
    reads.clear()
    actual = intake.drafts._compile_intake_commands(project, draft, commands, ontology)
    assert actual == expected
    assert len(reads) == 1
    assert actual[-1]['impact']['formal_records'] == 2
    assert actual[-1]['impact']['pending'] == 1
    assert actual[-1]['impact']['descendants'] == 1
    assert actual[-1]['impact']['constraints'] == 5


def test_intake_preserves_legacy_shacl_blank_node_impact_and_one_records_snapshot(tmp_path, monkeypatch):
    intake, _, project, _ = _selection(tmp_path, 1)
    ontology = Ontology('''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        <urn:test:Class> a owl:Class .
        <urn:test:Field> a owl:DatatypeProperty .
        <urn:test:Shape> a sh:NodeShape ; sh:targetClass <urn:test:Class> ;
            sh:property [ sh:path <urn:test:Field> ; sh:maxCount 1 ] .''')
    command = {'action': 'add_annotation', 'target_iri': 'urn:test:Field',
               'predicate': str(RDFS.label), 'value': 'Field'}
    draft = {'source_kind': 'discovery'}
    expected = intake.drafts._compile_command(project, draft, command, ontology)
    reads = []
    original = intake.drafts.repository.current_records

    def records(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(intake.drafts.repository, 'current_records', records)
    actual = intake.drafts._compile_intake_commands(project, draft, [command], ontology)
    assert len(reads) == 1
    # A Turtle round trip creates new opaque BNode IDs even in the legacy API.
    # Preserve all other operation fields and the positions of those references.
    for operations in (actual, expected):
        for operation in operations:
            operation.pop('fingerprint')
            for constraint in operation['impact']['constraints_detail']:
                if not constraint['subject'].startswith('urn:'):
                    constraint['subject'] = '_:shape-property'
    assert actual == expected
    assert actual[0]['impact']['constraints'] == 1
    assert actual[0]['impact']['constraints_detail'][0]['predicate'].endswith('#path')
    assert any(isinstance(subject, BNode) for subject in ontology.graph.subjects(
        None, URIRef('urn:test:Field')))


def test_from_graph_owns_mutations_and_refreshes_vocabulary():
    original = Ontology('<urn:test:Class> a <http://www.w3.org/2002/07/owl#Class> .')
    copied = Ontology.from_graph(original.graph)
    copied.graph.add((URIRef('urn:test:Child'),
                      URIRef('http://www.w3.org/1999/02/22-rdf-syntax-ns#type'),
                      URIRef('http://www.w3.org/2002/07/owl#Class')))
    assert copied.is_active_term('urn:test:Child')
    assert not original.is_active_term('urn:test:Child')
    assert URIRef('urn:test:Child') in copied.classes


def test_revision_change_after_compilation_rejects_final_cas_and_restores_exact_state(tmp_path, monkeypatch):
    intake, repo, project, body = _selection(tmp_path, 1)
    drafts = intake.drafts
    draft = drafts.create(project, None, 'manual', 'existing draft', 'author')
    body.update(create_new_draft=False, target_draft_id=draft['id'],
                expected_revision=draft['revision'])
    preview, _, _ = intake._plan(project, body)
    assert preview['can_add']
    request = {**body, 'source_fingerprint': preview['source_fingerprint'],
               'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'revision-race'}
    before = {
        'draft': drafts.store.get(project, draft['id']),
        'history': drafts.store.history(project, draft['id']),
        'runs': repo.list_intake_runs(project),
        'records': repo.current_records(project),
        'assertions': repo.list_assertions(project),
    }
    original_plan = intake._frozen_plan
    original_append = drafts.store.append_operations
    observed = {}

    def precheck(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        observed['precheck_passed'] = True
        return result

    def append_then_advance_revision(project_id, draft_id, operations):
        assert observed['precheck_passed']
        saved = original_append(project_id, draft_id, operations)
        assert saved
        observed['appended'] = len(drafts.store.history(project_id, draft_id)[0])
        raced = drafts.store.compare_and_set(project_id, draft_id, draft['revision'],
                                             {'title': 'concurrent revision'})
        observed['revision'] = raced['revision']
        return saved

    monkeypatch.setattr(intake, '_frozen_plan', precheck)
    monkeypatch.setattr(drafts.store, 'append_operations', append_then_advance_revision)
    # The normal final CAS sees a genuinely advanced revision; no CAS function
    # is replaced with an unconditional failure. The outer transaction must
    # roll back both the compiled operations and the injected revision update.
    with pytest.raises(RevisionConflict):
        intake.add(project, request, actor='author')
    assert observed['appended'] > 0
    assert observed['revision'] == draft['revision'] + 1
    assert {
        'draft': drafts.store.get(project, draft['id']),
        'history': drafts.store.history(project, draft['id']),
        'runs': repo.list_intake_runs(project),
        'records': repo.current_records(project),
        'assertions': repo.list_assertions(project),
    } == before
