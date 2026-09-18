from knowledge_service.embeddings import HashingEncoder
from knowledge_service.governance import Governance
from knowledge_service.ontology import Ontology
from knowledge_service.repository import Repository
from knowledge_service.service import KnowledgeService


def test_fact_survives_one_source_and_retires_after_last_support(tmp_path):
    repo = Repository(tmp_path / 'support.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('甲')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
    ])
    docs = [repo.put_record(project_id, {'id': f'doc-{n}', 'kind': 'document', 'text': '张三认识李四'})
            for n in (1, 2)]
    for index, doc in enumerate(docs, 1):
        service.write(project_id, [{
            'id': f'relation-{index}', 'kind': 'relation', 'type': 'knows',
            'text': '张三认识李四', 'subject_id': 'a', 'object_id': 'b',
            'source_id': doc['id'], 'metadata': {
                'source_version_id': doc['version_id'], 'chunk_id': f'chunk-{index}',
                'start_char': 0, 'end_char': 8},
        }], operation='extract')
    governance = Governance(service)

    governance.delete(project_id, 'doc-1', 1)
    assert [row['id'] for row in service.scoped(project_id, {}) if row['kind'] == 'relation'] == ['relation-1']
    assert len(repo.list_assertions(project_id, status='accepted', canonical_record_id='relation-1')) == 1

    governance.delete(project_id, 'doc-2', 1)
    assert [row for row in service.scoped(project_id, {}) if row['kind'] == 'relation'] == []
    assert repo.list_assertions(project_id, status='accepted', canonical_record_id='relation-1') == []
    assert repo.list_fact_keys(project_id) == []
