"""Chunk projections carry their owning document title heading, so semantic
recall can hit the title/file even though chunks only store sliced prose."""
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projections
import pytest
from test_file_preparations import prep, upload, ready
import time


def chunk(id='chunk1', *, label=None):
    record = {'project_id': 'p', 'id': id, 'version_id': 'v1', 'kind': 'chunk',
              'text': '这是切片正文，描述授信审批流程。', 'source_id': 'doc1', 'metadata': {}}
    return ProjectionInput(record=record, ontology_known=False, document_label=label or '')


def test_chunk_vector_text_prefixes_owning_document_title():
    rows = list(build_projections(chunk(label='智能体招标文件（bid.pdf）'),
                                  default_catalog(), index_version=1))
    assert rows and rows[0]['text'].startswith('智能体招标文件（bid.pdf）\n这是切片正文')


def test_chunk_without_document_label_keeps_plain_prose():
    rows = list(build_projections(chunk(), default_catalog(), index_version=1))
    assert rows and rows[0]['text'] == '这是切片正文，描述授信审批流程。'


def test_reused_preparation_keeps_each_uploaded_filename_in_vector_heading(prep):
    client,root=prep
    first=ready(client,root,upload(client,root,'first.md',key='first'))
    second=upload(client,root,'second.md',key='second')
    parsed=client.post(root+'/parse',json={'item_ids':[second['id']],
        'versions':{second['id']:1},'idempotency_key':'second-parse'})
    assert parsed.status_code==202,parsed.text
    second=parsed.json()['items'][1]
    assert second['status']=='reused'
    selected=[]
    for item,title in [(first,'业务规则甲'),(second,'业务规则乙')]:
        changed=client.patch(root+'/items/'+item['id'],json={'expected_version':item['metadata_version'],'options':{'title':title}})
        assert changed.status_code==200,changed.text
        selected.append(changed.json())
    response=client.post(root+'/publish',json={'item_ids':[i['id'] for i in selected],
        'versions':{i['id']:i['metadata_version'] for i in selected},
        'result_versions':{i['id']:i['chunk_version'] for i in selected},
        'independent_item_ids':[second['id']],'idempotency_key':'publish-names'})
    assert response.status_code==202,response.text
    operations=client.app.state.preparations
    for _ in range(200):
        batch=operations.batches.get(first['project_id'],response.json()['batch_id'])
        if batch['status']=='completed':break
        time.sleep(.03)
    assert batch['counts']['succeeded']==2,batch
    from knowledge_service.repository.projection_inputs import ProjectionInputs
    repo=client.app.state.service.repository
    items=operations.batches.store.list_items(first['project_id'],batch['id'])['items']
    for item in items:
        doc=repo.get_record(first['project_id'],item['document_id'])
        source=operations.sources.get(first['project_id'],item['source_version_id'])
        assert source['original_name']==item['original_name']
        assert doc['metadata']['source_file']==item['original_name']
        inputs=ProjectionInputs(repo).collect(first['project_id'],[c['id'] for c in source['chunks']])
        assert inputs and all(item['original_name'] in value.document_label for value in inputs)


@pytest.mark.parametrize(('field', 'replacement', 'heading'), [
    ('title', '新标题', '新标题（old.pdf）'),
    ('source_file', 'new.pdf', '旧标题（new.pdf）'),
])
def test_document_heading_revision_reprojects_existing_chunks(tmp_path, field, replacement, heading):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.projection_inputs import ProjectionInputs
    from knowledge_service.services.retrieval import PROJECTION_SCHEMA_VERSIONS
    from keyword_index_fixture import activate_keyword_index

    repo = Repository(tmp_path / 'document-heading')
    project = repo.create_project('document heading')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'document',
        'metadata': {'title': '旧标题', 'source_file': 'old.pdf'}})
    original = repo.put_record(project, {'id': 'chunk', 'kind': 'chunk',
        'source_id': 'doc', 'text': 'unchanged prose'})
    repo.put_record(project, {'id': 'unrelated', 'kind': 'chunk',
        'source_id': 'other', 'text': 'unrelated prose'})
    collected, = ProjectionInputs(repo).collect(project, ['chunk'])
    assert collected.document_label == '旧标题（old.pdf）'
    activate_keyword_index(repo, project)

    repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'document',
        'metadata': {**document['metadata'], field: replacement}}, expected_version=document['version'])

    collected, = ProjectionInputs(repo).collect(project, ['chunk'])
    assert collected.document_label == heading
    claim = repo.projections.claim_contiguous_batch(project, 'milvus', PROJECTION_SCHEMA_VERSIONS['milvus'])
    rows = [row for event in claim.events if event['operation'] == 'upsert'
        for row in (event['payload'].get('_milvus_projection') or {}).get('rows', [])]
    assert [(row['record_id'], row['version_id'], row['text']) for row in rows] == [
        ('chunk', original['version_id'], heading + '\nunchanged prose')]
    assert repo.get_record(project, 'chunk')['version_id'] == original['version_id']
