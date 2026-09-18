from knowledge_service.embeddings import HashingEncoder
from knowledge_service.ontology import Ontology
from knowledge_service.repository import Repository
from knowledge_service.service import KnowledgeService


def _system(tmp_path):
    repo = Repository(tmp_path / 'formal.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('正式图谱')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    return repo, service, project_id


def test_same_relation_key_reuses_immutable_fact_and_adds_support(tmp_path):
    repo, service, project_id = _system(tmp_path)
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'fact-z', 'kind': 'relation', 'type': 'knows', 'text': '张三认识李四',
         'subject_id': 'a', 'object_id': 'b'},
    ])
    replay = service.write(project_id, [
        {'id': 'fact-a', 'kind': 'relation', 'type': 'knows', 'text': '另一个来源也说他们认识',
         'subject_id': 'a', 'object_id': 'b'},
    ])

    assert replay[0]['id'] == 'fact-z'
    relations = [row for row in repo.current_records(project_id) if row['kind'] == 'relation']
    assert [row['id'] for row in relations] == ['fact-z']
    support = repo.list_assertions(project_id, status='accepted', canonical_record_id='fact-z')
    assert len(support) == 2
    assert repo.list_fact_keys(project_id)[0]['canonical_record_id'] == 'fact-z'


def test_source_anchor_is_stored_on_assertion_not_canonical_relation(tmp_path):
    repo, service, project_id = _system(tmp_path)
    document = repo.put_record(project_id, {'id': 'doc', 'kind': 'document', 'text': '张三认识李四'})
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'r', 'kind': 'relation', 'type': 'knows', 'text': '张三认识李四',
         'subject_id': 'a', 'object_id': 'b', 'source_id': 'doc',
         'metadata': {'source_version_id': document['version_id'], 'chunk_id': 'chunk-1',
                      'start_char': 0, 'end_char': 8}},
    ])

    relation = repo.get_record(project_id, 'r')
    assert 'source_id' not in relation
    assert 'chunk_id' not in relation['metadata']
    assertion = repo.list_assertions(project_id, canonical_record_id='r')[0]
    assert assertion['document_id'] == 'doc'
    assert assertion['document_version_id'] == document['version_id']
    assert assertion['chunk_id'] == 'chunk-1'
    assert assertion['start_char'] == 0
    assert assertion['end_char'] == 8


def test_stale_formal_write_rolls_back_record_and_assertion(tmp_path):
    repo, service, project_id = _system(tmp_path)
    first = service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'}])[0]

    try:
        service.write(project_id, [
            {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '新张三'}],
            expected_versions={'a': first['version'] - 1})
    except ValueError as exc:
        assert '冲突' in str(exc)
    else:
        raise AssertionError('stale write should fail')

    assert repo.get_record(project_id, 'a')['text'] == '张三'
    assert len(repo.list_assertions(project_id, canonical_record_id='a')) == 1
