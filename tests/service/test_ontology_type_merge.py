import pytest
from rdflib import URIRef, RDFS, Literal
from rdflib.namespace import OWL, DCTERMS, SH

from ontology_seed import seed_ontology
from knowledge_service.repository import Repository
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts import OntologyDrafts
from knowledge_service.services.ontology_operations import apply_operations
from test_ontology_draft_api import workbench, create_draft

EX = 'https://merge.test/'
BASE = '''
@prefix ex: <https://merge.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:Parent a owl:Class .
ex:Source a owl:Class ; rdfs:label "Source" ; rdfs:subClassOf ex:Parent .
ex:Target a owl:Class ; rdfs:label "Target" .
ex:Child a owl:Class ; rdfs:subClassOf ex:Source .
ex:rel a owl:ObjectProperty ; rdfs:domain [ owl:unionOf (ex:Source ex:Target) ] ; rdfs:range ex:Source .
ex:Shape a sh:NodeShape ; sh:targetClass ex:Source ; sh:property [ sh:path ex:rel ; sh:class ex:Source ] .
'''

def setup(tmp_path, turtle=BASE):
    repo = Repository(tmp_path / 'merge.sqlite')
    project = repo.create_project('merge')['id']
    base = seed_ontology(repo, project, turtle, {})
    service = OntologyDrafts(repo)
    draft = service.create(project, base['id'], 'manual', 'Merge', 'author')
    return repo, service, project, draft

def preview(service, project, draft, source='Source', target='Target'):
    return service.type_merge_preview(project, draft['id'], draft['revision'], EX + source, EX + target)

def merge(plan):
    return {'action': 'merge_class', 'target_iri': plan['source_iri'],
            'replacement_iri': plan['target_iri'], 'preview_fingerprint': plan['preview_fingerprint'], 'reason': 'Duplicate'}

def test_preview_and_merge_rewrite_structure_without_changing_records(tmp_path):
    repo, service, project, draft = setup(tmp_path)
    record = repo.put_record(project, {'id': 'entity', 'kind': 'entity', 'type': EX + 'Source', 'name': 'One', 'text': 'One'})
    plan = preview(service, project, draft)
    assert plan['can_merge'] and plan['operations']
    assert service.get(project, draft['id'])['revision'] == draft['revision']
    assert service.get(project, draft['id'])['operations'] == []
    updated = service.command(project, draft['id'], draft['revision'], merge(plan))
    graph = Ontology(updated['turtle']).graph
    source, target = URIRef(EX + 'Source'), URIRef(EX + 'Target')
    assert (source, OWL.deprecated, Literal(True)) in graph
    assert (source, DCTERMS.isReplacedBy, target) in graph
    assert (target, RDFS.label, Literal('Target')) in graph
    assert (target, RDFS.subClassOf, URIRef(EX + 'Parent')) in graph
    assert (URIRef(EX + 'Child'), RDFS.subClassOf, target) in graph
    assert not list(graph.subjects(SH['class'], source))
    assert not list(graph.subjects(SH.targetClass, source))
    assert repo.get_record(project, 'entity') == record
    assert updated['operations'][-1]['action'] == 'retire_term'

@pytest.mark.parametrize('source,target', [('Source','Source'), ('Source','rel'), ('Missing','Target')])
def test_invalid_merge_preview_is_explanatory_and_command_atomic(tmp_path, source, target):
    _, service, project, draft = setup(tmp_path)
    plan = preview(service, project, draft, source, target)
    assert not plan['can_merge'] and plan['warnings']
    with pytest.raises(ValueError):
        service.command(project, draft['id'], draft['revision'], merge(plan))
    assert service.get(project, draft['id'])['operations'] == []

def test_fingerprint_rejects_changed_record_version_and_missing_confirmation(tmp_path):
    repo, service, project, draft = setup(tmp_path)
    record = repo.put_record(project, {'id': 'entity', 'kind': 'entity', 'type': EX + 'Source', 'name': 'One', 'text': 'One'})
    plan = preview(service, project, draft)
    repo.put_record(project, {'id': record['id'], 'kind': 'entity', 'type': EX + 'Source', 'text': 'Changed'}, expected_version=record['version'])
    with pytest.raises(ValueError, match='preview|预览'):
        service.command(project, draft['id'], draft['revision'], merge(plan))
    command = merge(preview(service, project, draft))
    command.pop('preview_fingerprint')
    with pytest.raises(ValueError):
        service.command(project, draft['id'], draft['revision'], command)
    assert service.get(project, draft['id'])['operations'] == []

def test_unknown_semantic_reference_refuses_merge(tmp_path):
    _, service, project, draft = setup(tmp_path, BASE + '\nex:Target owl:disjointWith ex:Source .')
    plan = preview(service, project, draft)
    assert not plan['can_merge'] and plan['warnings']

def test_partial_retirement_cannot_leave_shacl_class_reference():
    turtle = BASE.replace('sh:targetClass ex:Source', 'sh:targetClass ex:Target').replace('rdfs:subClassOf ex:Source', 'rdfs:subClassOf ex:Target').replace('rdfs:range ex:Source', 'rdfs:range ex:Target').replace('(ex:Source ex:Target)', '(ex:Target ex:Parent)')
    with pytest.raises(ValueError, match='active dependency'):
        apply_operations(turtle, [{'action': 'retire_term', 'target_iri': EX + 'Source'}])


def test_preview_api_is_read_only_and_revision_guarded(workbench):
    client, project, base = workbench
    draft = create_draft(client, project, base)
    path = f"/api/projects/{project}/ontology-drafts/{draft['id']}"
    response = client.post(path + '/type-merge-preview', json={
        'expected_revision': draft['revision'],
        'source_iri': 'https://example.test/ns#Root',
        'target_iri': 'https://example.test/ns#AltRoot'})
    assert response.status_code == 200, response.text
    assert response.json()['can_merge']
    assert client.get(path).json()['operations'] == []
    stale = client.post(path + '/type-merge-preview', json={
        'expected_revision': draft['revision'] + 1,
        'source_iri': 'https://example.test/ns#Root',
        'target_iri': 'https://example.test/ns#AltRoot'})
    assert stale.status_code == 409


@pytest.mark.parametrize('extra', [
    'ex:Target owl:deprecated true .',
    'ex:Target rdfs:subClassOf ex:Child .',
    'ex:Source owl:disjointWith ex:Target .',
])
def test_retired_target_cycles_and_unknown_outgoing_structure_are_blocked(tmp_path, extra):
    _, service, project, draft = setup(tmp_path, BASE + extra)
    assert not preview(service, project, draft)['can_merge']


def test_same_count_different_members_and_pending_mapping_invalidate_preview(tmp_path):
    repo, service, project, draft = setup(tmp_path)
    one = repo.put_record(project, {'id': 'one', 'kind': 'entity', 'text': 'One', 'type': EX + 'Source'})
    plan = preview(service, project, draft)
    repo.put_record(project, {'id': 'one', 'kind': 'entity', 'text': 'One', 'type': EX + 'Target'}, expected_version=one['version'])
    repo.put_record(project, {'id': 'two', 'kind': 'entity', 'text': 'Two', 'type': EX + 'Source'})
    fresh = preview(service, project, draft)
    assert fresh['impact']['record_count'] == plan['impact']['record_count']
    assert fresh['preview_fingerprint'] != plan['preview_fingerprint']
    with pytest.raises(ValueError, match='预览'):
        service.command(project, draft['id'], draft['revision'], merge(plan))
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'Evidence', 'metadata': {
        'review_candidates': [{'id': 'candidate', 'status': 'pending', 'proposed_type': EX + 'Source'}]}})
    plan = preview(service, project, draft)
    repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'Evidence', 'metadata': {
        'review_candidates': [{'id': 'candidate', 'status': 'pending', 'proposed_type': EX + 'Target'}]}}, expected_version=document['version'])
    with pytest.raises(ValueError, match='预览'):
        service.command(project, draft['id'], draft['revision'], merge(plan))
    assert service.get(project, draft['id'])['operations'] == []


def test_local_name_record_and_linked_relation_impact_and_version_guard(tmp_path):
    repo, service, project, draft = setup(tmp_path)
    record = repo.put_record(project, {'id': 'short', 'kind': 'entity', 'text': 'Short', 'type': 'Source'})
    repo.put_record(project, {'id': 'other', 'kind': 'entity', 'text': 'Other', 'type': 'Target'})
    repo.put_record(project, {'id': 'link', 'kind': 'relation', 'text': 'Linked', 'type': EX + 'rel',
                              'subject_id': 'short', 'object_id': 'other'})
    plan = preview(service, project, draft)
    assert plan['impact']['record_count'] == 2
    assert plan['impact']['linked_relation_count'] == 1
    repo.put_record(project, {'id': 'short', 'kind': 'entity', 'text': 'Updated', 'type': 'Source'},
                    expected_version=record['version'])
    fresh = preview(service, project, draft)
    assert fresh['impact']['record_count'] == plan['impact']['record_count']
    assert fresh['preview_fingerprint'] != plan['preview_fingerprint']
    with pytest.raises(ValueError, match='预览'):
        service.command(project, draft['id'], draft['revision'], merge(plan))
    assert service.get(project, draft['id'])['operations'] == []


def test_merge_preserves_deprecated_predecessor_replacement_chain(tmp_path):
    turtle = BASE + '''
@prefix dcterms: <http://purl.org/dc/terms/> .
ex:Earlier a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:Source .
'''
    _, service, project, draft = setup(tmp_path, turtle)
    plan = preview(service, project, draft)
    assert plan['can_merge'], plan['warnings']
    updated = service.command(project, draft['id'], draft['revision'], merge(plan))
    graph = Ontology(updated['turtle']).graph
    assert (URIRef(EX + 'Earlier'), DCTERMS.isReplacedBy, URIRef(EX + 'Source')) in graph
    assert (URIRef(EX + 'Source'), DCTERMS.isReplacedBy, URIRef(EX + 'Target')) in graph


def test_active_predecessor_replacement_blocks_merge(tmp_path):
    turtle = BASE + '''
@prefix dcterms: <http://purl.org/dc/terms/> .
ex:Earlier a owl:Class ; dcterms:isReplacedBy ex:Source .
'''
    _, service, project, draft = setup(tmp_path, turtle)
    assert not preview(service, project, draft)['can_merge']
