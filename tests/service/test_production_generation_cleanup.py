"""Retired generation deletion is durable, scoped, leased and retryable."""
from types import SimpleNamespace

from knowledge_service.repository.core import Repository
from knowledge_service.services.projection_runtime import ProjectionWorker

from test_production_projection_receipts import activate_legacy


def cleanup_store(repo):
    assert hasattr(repo.projections, 'generation_cleanups'), 'retired generations have no durable cleanup store'
    return repo.projections.generation_cleanups


def test_failed_generation_delete_remains_durable_and_retries_only_its_scope(tmp_path):
    repo = Repository(tmp_path / 'generation-retry')
    project = repo.create_project('generation retry')['id']
    activate_legacy(repo, project, {}, watermark=0)
    cleanup = cleanup_store(repo)
    with repo._transaction():
        cleanup.enqueue(project, 1)
    calls = []

    def unavailable(project_id, versions):
        calls.append((project_id, versions))
        raise RuntimeError('Milvus unavailable')

    assert cleanup.drain('worker', unavailable)['failed'] == 1
    retained = repo._db.execute('SELECT * FROM milvus_generation_cleanup_outbox '
        'WHERE project_id=? AND index_version=1', (project,)).fetchone()
    assert retained and retained['attempts'] == 1 and retained['last_error']
    with repo._transaction():
        repo._db.execute('UPDATE milvus_generation_cleanup_outbox '
            'SET not_before=clock_timestamp() WHERE project_id=?', (project,))
    result = cleanup.drain('worker', lambda p, versions: calls.append((p, versions)))
    assert result['cleaned'] == 1
    assert calls == [(project, [1]), (project, [1])]


def test_current_generation_is_protected_even_if_cleanup_was_misqueued(tmp_path):
    repo = Repository(tmp_path / 'generation-protection')
    project = repo.create_project('generation protection')['id']
    activate_legacy(repo, project, {}, watermark=0)
    cleanup = cleanup_store(repo)
    with repo._transaction():
        cleanup.enqueue(project, 2)
    deleted = []
    result = cleanup.drain('worker', lambda *args: deleted.append(args))
    assert result['cleaned'] == 0
    assert not deleted


def test_cleanup_old_token_cannot_ack_after_another_worker_reclaims(tmp_path):
    repo = Repository(tmp_path / 'cleanup-token')
    project = repo.create_project('cleanup token')['id']
    activate_legacy(repo, project, {}, watermark=0)
    cleanup = cleanup_store(repo)
    with repo._transaction():
        cleanup.enqueue(project, 1)
    old = cleanup.claim('worker')[0]
    with repo._transaction():
        repo._db.execute('UPDATE milvus_generation_cleanup_outbox '
            'SET claim_until=clock_timestamp()-interval \'1 second\' WHERE project_id=?', (project,))
    fresh = cleanup.claim('worker')[0]
    assert old['claim_token'] != fresh['claim_token']
    assert not cleanup.ack(old)
    assert cleanup.ack(fresh)


def test_success_keeps_retirement_marker_to_remove_late_old_rpc_rows(tmp_path):
    repo = Repository(tmp_path / 'cleanup-late-rpc')
    project = repo.create_project('cleanup late rpc')['id']
    activate_legacy(repo, project, {}, watermark=0)
    cleanup = cleanup_store(repo)
    with repo._transaction():
        cleanup.enqueue(project, 1)
    deleted = []
    assert cleanup.drain('worker', lambda *args: deleted.append(args))['cleaned'] == 1
    with repo._transaction():
        repo._db.execute('UPDATE milvus_generation_cleanup_outbox '
            'SET not_before=clock_timestamp() WHERE project_id=?', (project,))
    assert cleanup.drain('worker', lambda *args: deleted.append(args))['cleaned'] == 1
    assert deleted == [(project, [1]), (project, [1])]


def test_expired_cleanup_lease_is_checked_before_external_delete(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'cleanup-expired-before-rpc')
    project = repo.create_project('expired cleanup')['id']
    activate_legacy(repo, project, {}, watermark=0)
    cleanup = cleanup_store(repo)
    with repo._transaction():
        cleanup.enqueue(project, 1)
    event = cleanup.claim('worker')[0]
    with repo._transaction():
        repo._db.execute('UPDATE milvus_generation_cleanup_outbox '
            'SET claim_until=clock_timestamp()-interval \'1 second\' WHERE project_id=?', (project,))
    monkeypatch.setattr(cleanup, 'claim', lambda *_args, **_kwargs: [event])
    deleted = []
    result = cleanup.drain('worker', lambda *args: deleted.append(args))
    assert not deleted, 'an expired claim reached external deletion'
    assert result['failed'] == 1


def test_project_delete_preserves_known_generation_markers_after_cleanup_ack_for_late_rpc(tmp_path):
    repo = Repository(tmp_path / 'deleted-project-late-rpc')
    project = repo.create_project('deleted project late RPC')['id']
    record = repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'body'})
    activate_legacy(repo, project, {}, watermark=1)
    with repo._transaction():
        repo._db.execute("UPDATE projection_cursors SET rebuild_state=jsonb_set("
            "rebuild_state,'{previous_index_version}','1'::jsonb) WHERE project_id=? AND target='milvus'", (project,))
        repo._db.execute('INSERT INTO milvus_projection_receipts '
            '(project_id,index_version,record_id,version_id,fingerprint) VALUES (?,3,?,?,?)',
            (project, 'r', record['version_id'], '0' * 64))
        repo._db.execute('INSERT INTO projection_rebuild_snapshots '
            '(project_id,target,build_id,ordinal,version_id) VALUES (?,?,?,0,?)',
            (project, 'milvus', 'original-build', record['version_id']))
    repo.delete_project(project)
    generations = {row['index_version'] for row in repo._db.execute(
        'SELECT index_version FROM milvus_generation_cleanup_outbox WHERE project_id=?', (project,)).fetchall()}
    assert generations == {1, 2, 3}, 'project deletion lost current or known generation identities'
    claimed = repo.claim_projection_cleanup(project, 'milvus', 'delete-worker')
    assert claimed
    rows = {(project, 2): 'old vector'}
    rows.clear()  # The initial whole-project native cleanup completed.
    assert repo.ack_projection_cleanup(project, 'milvus', 'delete-worker', claimed['claim_token'])
    rows[(project, 2)] = 'late upsert after project cleanup ack'
    cleanup = repo.projections.generation_cleanups
    def delete(project_id, versions):
        for generation in versions:
            rows.pop((project_id, generation), None)
    assert cleanup.drain('retirement-worker', delete)['cleaned'] == 3
    assert not rows
    # A second late completion is also covered after the first retirement pass.
    rows[(project, 2)] = 'second late upsert'
    with repo._transaction():
        repo._db.execute('UPDATE milvus_generation_cleanup_outbox SET not_before=clock_timestamp() '
                         'WHERE project_id=?', (project,))
    assert cleanup.drain('retirement-worker', delete)['cleaned'] == 3
    assert not rows


def test_existing_background_loop_drains_generation_cleanup_without_rebuilding():
    called = []
    sync = SimpleNamespace(adapters={'milvus': object()},
                           maintain_generations=lambda: called.append('generation-cleanup'))
    repo = SimpleNamespace(list_project_directory=lambda: [],
        claim_projection_cleanups=lambda *_args, **_kwargs: [])
    ProjectionWorker(repo, sync, cleanup_handlers={}).run_once()
    assert called == ['generation-cleanup']
