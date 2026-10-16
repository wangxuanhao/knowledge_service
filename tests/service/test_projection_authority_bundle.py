from knowledge_service.repository.core import Repository


def test_repair_bundle_keeps_current_incident_facts_and_project_boundaries(tmp_path):
    repo = Repository(tmp_path / 'authority')
    project = repo.create_project('repair')['id']
    other = repo.create_project('other')['id']
    for project_id in (project, other):
        for entity_id in ('a', 'b', 'c'):
            repo.put_record(project_id, {
                'id': entity_id, 'kind': 'entity', 'type': 'Thing',
                'text': entity_id,
            })
    repo.put_record(project, {
        'id': 'ab', 'kind': 'relation', 'type': 'knows', 'text': 'old',
        'subject_id': 'a', 'object_id': 'b',
    })
    repo.put_record(project, {
        'id': 'ab', 'kind': 'relation', 'type': 'knows', 'text': 'current',
        'subject_id': 'a', 'object_id': 'b',
    })
    repo.put_record(project, {
        'id': 'age', 'kind': 'attribute', 'type': 'age', 'text': 'age',
        'subject_id': 'a', 'value': 3,
        'datatype': 'http://www.w3.org/2001/XMLSchema#integer',
    })
    bundle = repo.projections.authority_bundle(project, 'a')
    assert {row['id'] for row in bundle} == {'a', 'b', 'ab', 'age'}
    assert next(row for row in bundle if row['id'] == 'ab')['text'] == 'current'
    assert {row['id'] for row in repo.projections.authority_bundle(project, 'ab')} == {'a', 'b', 'ab'}
    assert repo.projections.authority_bundle(project, 'missing') == []
