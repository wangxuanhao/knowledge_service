import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.evidence import evidence


def test_evidence_pins_assertion_source_version_and_scopes_records(tmp_path):
    repo = Repository(tmp_path / 'e.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('证据')['id']
    document = repo.put_record(project, {
        'id': 'doc', 'kind': 'document', 'text': '开始😀商户规则结束',
        'metadata': {'title': '规则原文'}})
    chunk = repo.put_record(project, {
        'id': 'chunk', 'kind': 'chunk', 'text': document['text'], 'source_id': 'doc',
        'metadata': {'source_version_id': document['version_id'],
                     'start_char': 0, 'end_char': len(document['text'])}})
    repo.put_record(project, {
        'id': 'e', 'kind': 'entity', 'text': '商户', 'metadata': {'region': '北京'}})
    assertion = repo.create_assertion(project, {
        'id': 'assertion', 'kind': 'entity', 'actor': 'test',
        'document_id': 'doc', 'document_version_id': document['version_id'],
        'chunk_id': chunk['id'], 'start_char': 3, 'end_char': 7, 'quote': '商户规则',
        'payload': {'text': '商户', 'evidence': '商户规则', 'evidence_status': 'exact'}})
    repo.transition_assertion(project, assertion['id'], 1, 'accepted',
                              'verified', 'test', canonical_record_id='e')
    repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': '已经变更的原文'})

    result = evidence(service, project, 'e', {})['documents'][0]

    assert result['version'] == 1
    assert result['highlight'] == '商户规则'
    assert result['before'] + result['highlight'] + result['after'] == '开始😀商户规则结束'
    assert result['mode'] == 'exact'
    with pytest.raises(KeyError):
        evidence(service, project, 'e', {
            'filters': {'field': 'region', 'op': 'eq', 'value': '上海'}})
    other = repo.create_project('隔离')['id']
    with pytest.raises(KeyError):
        evidence(service, other, 'e', {})
    repo.close()


def test_record_source_metadata_never_substitutes_for_a_source_assertion(tmp_path):
    repo = Repository(tmp_path / 'e.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('无来源断言')['id']
    document = repo.put_record(project, {
        'id': 'doc', 'kind': 'document', 'text': '商户应遵守规则。商户可以申诉。'})
    repo.put_record(project, {
        'id': 'e', 'kind': 'entity', 'text': '商户', 'source_id': 'doc',
        'metadata': {'source_version_id': document['version_id'],
                     'start_char': 0, 'end_char': 2,
                     'merged_sources': [{'kind': 'document', 'id': 'doc',
                                         'version_id': document['version_id']}]}})

    result = evidence(service, project, 'e', {})

    assert result['documents'] == []
    assert result['assertions'] == []
    assert '来源断言' in result['message']
    repo.put_record(project, {'id': 'm', 'kind': 'entity', 'text': '手工记录'})
    assert evidence(service, project, 'm', {})['documents'] == []
    repo.close()
