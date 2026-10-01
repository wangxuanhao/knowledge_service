import pytest

import knowledge_service.repository as repository_module
from knowledge_service.utils.assertions import occurrence_id
from knowledge_service.repository import Repository


def _assertion(**overrides):
    item = {
        'id': 'assertion-1',
        'kind': 'relation',
        'document_id': 'doc-1',
        'document_version_id': 'doc-version-1',
        'chunk_id': 'chunk-1',
        'source_hash': 'sha256:abc',
        'start_char': 8,
        'end_char': 20,
        'quote': '北京位于中国',
        'payload': {'subject': '北京', 'predicate': '位于', 'object': '中国'},
    }
    item.update(overrides)
    return item


def test_occurrence_id_is_deterministic_and_source_scoped():
    first = occurrence_id('store-a', 'version-1', 'chunk-1', 'relation', 0, [' 北京 ', '位于', '中国'])
    replay = occurrence_id('store-a', 'version-1', 'chunk-1', 'relation', 0, ['北京', '位于', '中国'])
    changed_source = occurrence_id('store-a', 'version-2', 'chunk-1', 'relation', 0, ['北京', '位于', '中国'])

    assert first == replay
    assert first != changed_source
    assert first.startswith('ast_')


def test_assertion_create_is_idempotent_but_rejects_occurrence_collision(tmp_path):
    repo = Repository(tmp_path / 'db.sqlite')
    project_id = repo.create_project('甲')['id']

    created = repo.create_assertion(project_id, _assertion())
    replay = repo.create_assertion(project_id, _assertion())

    assert replay == created
    assert created['status'] == 'pending'
    assert created['decision_version'] == 1
    assert len(repo.list_assertions(project_id)) == 1
    with pytest.raises(ValueError, match='冲突'):
        repo.create_assertion(project_id, _assertion(quote='不同原文'))


def test_assertion_transition_is_cas_guarded_and_audited(tmp_path):
    repo = Repository(tmp_path / 'db.sqlite')
    project_id = repo.create_project('甲')['id']
    repo.create_assertion(project_id, _assertion())

    accepted = repo.transition_assertion(
        project_id,
        'assertion-1',
        expected_version=1,
        status='accepted',
        reason='人工核验',
        actor='reviewer-1',
        canonical_record_id='fact-1',
    )

    assert accepted['decision_version'] == 2
    assert accepted['canonical_record_id'] == 'fact-1'
    with pytest.raises(ValueError, match='冲突'):
        repo.transition_assertion(
            project_id, 'assertion-1', expected_version=1, status='rejected',
            reason='旧界面误操作', actor='reviewer-2')
    with pytest.raises(ValueError, match='操作者'):
        repo.transition_assertion(
            project_id, 'assertion-1', expected_version=2, status='rejected',
            reason='无执行人', actor='')

    events = repo.list_assertion_events(project_id, 'assertion-1')
    assert [(event['from_status'], event['to_status']) for event in events] == [
        (None, 'pending'), ('pending', 'accepted')]
    assert events[-1]['actor'] == 'reviewer-1'


def test_rejected_can_only_return_to_pending_via_explicit_reprocess(tmp_path):
    repo = Repository(tmp_path / 'db.sqlite')
    project_id = repo.create_project('甲')['id']
    repo.create_assertion(project_id, _assertion())
    rejected = repo.transition_assertion(
        project_id, 'assertion-1', 1, 'rejected', '不支持', 'reviewer')

    with pytest.raises(ValueError, match='转换'):
        repo.transition_assertion(
            project_id, 'assertion-1', rejected['decision_version'], 'pending',
            '重试', 'reviewer')
    pending = repo.reprocess_assertion(
        project_id, 'assertion-1', rejected['decision_version'], '源文本已更新', 'operator')
    assert pending['status'] == 'pending'
    assert pending['decision_version'] == 3


def test_superseded_is_terminal_and_assertions_are_project_isolated(tmp_path):
    repo = Repository(tmp_path / 'db.sqlite')
    first_project = repo.create_project('甲')['id']
    second_project = repo.create_project('乙')['id']
    repo.create_assertion(first_project, _assertion())
    superseded = repo.transition_assertion(
        first_project, 'assertion-1', 1, 'superseded', '重复抽取', 'system')

    with pytest.raises(ValueError, match='终态'):
        repo.transition_assertion(
            first_project, 'assertion-1', superseded['decision_version'], 'accepted',
            '恢复', 'reviewer')
    with pytest.raises(KeyError):
        repo.get_assertion(second_project, 'assertion-1')

    repo.delete_project(first_project)
    assert repo._db.execute('SELECT COUNT(*) FROM assertions').fetchone()[0] == 0
    assert repo._db.execute('SELECT COUNT(*) FROM assertion_events').fetchone()[0] == 0
