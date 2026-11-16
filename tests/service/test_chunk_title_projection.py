"""Chunk projections carry their owning document title heading, so semantic
recall can hit the title/file even though chunks only store sliced prose."""
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projections
import pytest


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
