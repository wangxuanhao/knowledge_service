"""PostgreSQL checks for the bounded attribute side of local graph reads."""
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads


def test_local_graph_attributes_keep_scope_seed_priority_and_historical_version(tmp_path):
    repo = Repository(tmp_path / 'local-graph-attributes')
    project = repo.create_project('局部图属性')['id']
    repo.put_batch(project, [
        {'id': 'a', 'kind': 'entity', 'text': '甲', 'metadata': {'region': '北京'}},
        {'id': 'b', 'kind': 'entity', 'text': '乙', 'metadata': {'region': '北京'}},
        {'id': 'c', 'kind': 'entity', 'text': '丙', 'metadata': {'region': '上海'}},
        {'id': 'attribute-a', 'kind': 'attribute', 'subject_id': 'a', 'type': 'urn:amount', 'text':'金额1',
         'value': 1, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'metadata': {'region': '北京'}},
        {'id': 'attribute-b', 'kind': 'attribute', 'subject_id': 'b', 'type': 'urn:amount', 'text':'金额2',
         'value': 2, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'metadata': {'region': '北京'}},
        {'id': 'attribute-c', 'kind': 'attribute', 'subject_id': 'c', 'type': 'urn:amount', 'text':'金额3',
         'value': 3, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'metadata': {'region': '北京'}},
    ])
    reads = ScopedReads(repo)
    scope = {'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '北京'}}
    limited = reads.entity_attributes(project, scope, ['b', 'a', 'c'], limit=1)
    assert [row['id'] for row in limited['records']] == ['attribute-b']
    assert limited['truncated'] is True
    complete = reads.entity_attributes(project, scope, ['a', 'b', 'c'])
    assert [row['id'] for row in complete['records']] == ['attribute-a', 'attribute-b']
    assert complete['truncated'] is False
    original = repo.get_record(project, 'attribute-a')
    editable = {key:value for key,value in original.items()
                if key not in {'project_id','version','version_id','recorded_at','superseded_at'}}
    repo.put_record(project, {**editable, 'value': 10}, expected_version=original['version'])
    historical = reads.entity_attributes(project, {**scope, 'known_at': original['recorded_at']}, ['a'])
    assert historical['records'][0]['version_id'] == original['version_id']
    assert historical['records'][0]['value'] == 1
