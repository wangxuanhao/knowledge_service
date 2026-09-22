import knowledge_service.services.service as service_module

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


TTL_ONE = '''
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:One> a owl:Class ; rdfs:label "一"@zh .
'''

TTL_TWO = '''
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:Two> a owl:Class ; rdfs:label "二"@zh .
'''


def test_ontology_labels_are_cached_per_project_and_invalidated_by_new_version(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'labels.sqlite', HashingEncoder())
    service = app.state.service
    repository = service.repository
    first = repository.create_project('first')['id']
    second = repository.create_project('second')['id']
    repository.save_ontology(first, TTL_ONE, {})
    repository.save_ontology(second, TTL_TWO, {})
    original = service_module.Ontology
    parsed = []

    def counted(turtle):
        parsed.append(turtle)
        return original(turtle)

    monkeypatch.setattr(service_module, 'Ontology', counted)
    assert service.ontology_labels(first)['urn:test:One'] == '一'
    assert service.ontology_labels(first)['urn:test:One'] == '一'
    assert len(parsed) == 1
    assert service.ontology_labels(second)['urn:test:Two'] == '二'
    assert len(parsed) == 2

    repository.save_ontology(first, TTL_TWO, {})
    labels = service.ontology_labels(first)

    assert labels['urn:test:One'] == '一' and labels['urn:test:Two'] == '二'
    assert len(parsed) == 4
