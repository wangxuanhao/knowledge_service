import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

from knowledge_service.repository import Repository
from knowledge_service.integrations.source_object_store import SourceObjectStore, S3SourceObjectStore
from knowledge_service.services.source_documents import SourceDocumentService


def migration_module():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'migrate_source_objects.py'
    assert path.exists(), 'explicit verified source migration command is missing'
    spec = importlib.util.spec_from_file_location('migrate_source_objects', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrator():
    return migration_module().migrate_source_objects


class CountingStore(SourceObjectStore):
    puts = 0

    def put(self, *args, **kwargs):
        self.puts += 1
        return super().put(*args, **kwargs)


def setup(tmp_path):
    repo = Repository(tmp_path / 'migration.sqlite')
    project = repo.create_project('migration')['id']
    local = SourceObjectStore(tmp_path / 'old')
    target = CountingStore(tmp_path / 'new')
    sources = SourceDocumentService(repo, local)
    batch = repo.ingest_batches.create_batch(project, {})
    return repo, project, local, target, sources, batch['id']


def queue(repo, project, bid, raw, name='old.md', digest=None):
    return repo.ingest_batches.add_item(project, bid, name, raw,
        digest or hashlib.sha256(raw).hexdigest(), name, 100, 100000)


def test_dry_run_inventories_without_upload_or_changing_legacy_bytes(tmp_path):
    run = migrator()
    repo, project, local, target, sources, bid = setup(tmp_path)
    raw = b'# original\r\n'
    version = sources.persist(project, 'doc', raw, 'old.md')
    item = queue(repo, project, bid, raw)
    report = run(repo, local, target)
    assert report['mode'] == 'dry-run'
    assert report['error_count'] == 0
    assert len(report['objects']) == 1 and len(report['queue']) == 1
    assert target.puts == 0
    row = repo._db.execute('SELECT content,upload_object_id FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert bytes(row['content']) == raw and row['upload_object_id'] is None
    assert local.read(version['storage_key']) == raw


def test_apply_recovers_exact_missing_original_and_clears_only_verified_bytes(tmp_path):
    run = migrator()
    repo, project, local, target, sources, bid = setup(tmp_path)
    raw = '# 原始\r\n'.encode('utf-8-sig')
    version = sources.persist(project, 'doc', raw, 'old.md')
    item = queue(repo, project, bid, raw)
    (local.root / version['storage_key']).unlink()
    report = run(repo, local, target, apply=True)
    assert report['error_count'] == 0, report
    assert report['objects'][0]['source_issue'] == 'missing'
    assert report['objects'][0]['recovered_from'] in ('frozen_markdown', 'queue_original')
    assert target.read(version['storage_key']) == raw
    row = repo._db.execute('SELECT content,upload_object_id FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert row['content'] is None and row['upload_object_id'] == version['source_object_id']
    assert not (local.root / version['storage_key']).exists(), 'migration must not repair/delete local files'
    assert run(repo, local, target, apply=True)['error_count'] == 0


def test_corrupt_destination_or_nonmatching_frozen_text_never_clears_bytes(tmp_path):
    run = migrator()
    repo, project, local, target, sources, bid = setup(tmp_path)
    raw = b'# original'
    version = sources.persist(project, 'doc', raw, 'old.md')
    item = queue(repo, project, bid, raw)
    obj = target.put(raw)
    (target.root / obj['storage_key']).write_bytes(b'corrupt')
    report = run(repo, local, target, apply=True)
    assert report['error_count'] >= 1
    assert bytes(repo._db.execute('SELECT content FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()[0]) == raw
    (local.root / version['storage_key']).unlink()
    repo._db.execute('DELETE FROM ingest_batch_items WHERE id=?', (item['id'],))
    repo._db.execute('UPDATE source_document_versions SET source_text=? WHERE id=?', ('changed', version['id']))
    report = run(repo, local, SourceObjectStore(tmp_path / 'empty'), apply=True)
    assert report['objects'][0]['status'] == 'error'
    assert report['objects'][0]['source_issue'] == 'missing'


def test_zip_legacy_member_references_verified_original_archive(tmp_path):
    run = migrator()
    repo, project, local, target, sources, bid = setup(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('member.md', '# member\n')
    raw = buffer.getvalue()
    version = sources.persist(project, 'doc', raw, 'member.md', text='# member\n')
    item = queue(repo, project, bid, b'# member\n', 'member.md', hashlib.sha256(raw).hexdigest())
    repo.ingest_batches.set_source(project, bid, item['id'], version['id'])
    report = run(repo, local, target, apply=True)
    assert report['error_count'] == 0, report
    row = repo._db.execute('SELECT content,upload_object_id FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert row['content'] is None and row['upload_object_id'] == version['source_object_id']
    assert target.read(version['storage_key']) == raw


def test_migration_reports_failure_without_exception_credentials(tmp_path):
    run = migrator()
    repo, project, local, target, sources, bid = setup(tmp_path)
    queue(repo, project, bid, b'# secret')
    def unavailable(*args, **kwargs):
        raise RuntimeError('https://user:super-secret@example.test password=super-secret')
    target.put = unavailable
    report = run(repo, local, target, apply=True)
    assert report['error_count'] == 1
    assert 'super-secret' not in str(report)


def paused_zip_restart(tmp_path):
    repo, project, local, target, sources, bid = setup(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('member.md', '# member\n')
    raw = buffer.getvalue()
    version = sources.persist(project, 'doc', raw, 'member.md', text='# member\n')
    item = queue(repo, project, bid, b'# member\n', 'member.md', hashlib.sha256(raw).hexdigest())
    batches = repo.ingest_batches
    batches.set_source(project, bid, item['id'], version['id'])
    batches.transition(project, bid, 'start')
    leased = batches.claim(bid, 'worker', 60)
    assert batches.finish(item['id'], 'worker', leased['lease_token'], 'failed')
    batches.retry(project, bid, item['id'], mode='restart_document')
    batches.transition(project, bid, 'pause')
    restarted = repo._db.execute('SELECT * FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert restarted['status'] == 'pending' and restarted['source_version_id'] is None
    assert restarted['source_restart_of'] == version['id']
    assert batches.get_batch(project, bid)['status'] == 'paused'
    return repo, project, local, target, sources, item, version, raw


def test_paused_zip_restart_migrates_original_through_previous_source_version(tmp_path):
    run = migrator()
    repo, project, local, target, sources, item, version, raw = paused_zip_restart(tmp_path)
    report = run(repo, local, target)
    assert report['error_count'] == 0, report
    assert report['queue'][0]['status'] == 'ready'
    assert target.puts == 0
    report = run(repo, local, target, apply=True)
    assert report['error_count'] == 0, report
    row = repo._db.execute('SELECT * FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert row['content'] is None and row['upload_object_id'] == version['source_object_id']
    assert row['source_restart_of'] == version['id'] and row['source_version_id'] is None
    assert row['status'] == 'pending' and row['document_generation'] == 1
    assert target.read(version['storage_key']) == raw


@pytest.mark.parametrize('mismatch', ['project', 'archive_sha256'])
def test_zip_restart_never_substitutes_another_project_or_archive(tmp_path, mismatch):
    run = migrator()
    repo, project, local, target, sources, item, version, raw = paused_zip_restart(tmp_path)
    source_project = repo.create_project('other')['id'] if mismatch == 'project' else project
    wrong_version = sources.persist(source_project, 'other-doc', raw if mismatch == 'project' else raw + b'different',
                                    'member.md', text='# member\n')
    repo._db.execute('UPDATE ingest_batch_items SET source_restart_of=? WHERE id=?', (wrong_version['id'], item['id']))
    report = run(repo, local, target, apply=True)
    assert report['queue'][0]['status'] == 'error'
    row = repo._db.execute('SELECT content,upload_object_id FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
    assert bytes(row['content']) == b'# member\n' and row['upload_object_id'] is None


@pytest.mark.parametrize('apply', [False, True], ids=['inventory', 'apply'])
def test_cli_does_not_bootstrap_settings_or_interrupt_unrelated_ingest_runs(tmp_path, monkeypatch, capsys, apply):
    module = migration_module()
    path = tmp_path / 'cli.sqlite'
    repo = Repository(path)
    project = repo.create_project('unrelated')['id']
    for status in ('queued', 'running'):
        doc = repo.put_record(project, {'id': status, 'kind': 'document', 'text': status})
        run = repo.create_ingest_run(project, doc['id'], doc['version_id'])
        if status == 'running':
            repo.update_ingest_run(project, run['id'], run['version'], status='running')
    repo._db.execute('DELETE FROM service_settings WHERE key=?', ('storage_namespace',))
    before_runs = [dict(row) for row in repo._db.execute('SELECT * FROM ingest_runs ORDER BY id').fetchall()]
    before_settings = [dict(row) for row in repo._db.execute('SELECT * FROM service_settings ORDER BY key').fetchall()]
    # Exercise the real CLI and Repository constructor against the same isolated
    # database. With an empty object inventory this S3 client is never called.
    destination = S3SourceObjectStore(bucket='sources', client=object())
    monkeypatch.setattr(module, 'source_object_store_from_environment', lambda: destination)
    monkeypatch.setattr(module, 'resolve_dsn', lambda: path)
    args = ['--source-root', str(tmp_path / 'local')]
    if apply:
        args.append('--apply')
    assert module.main(args) == 0
    assert json.loads(capsys.readouterr().out)['mode'] == ('apply' if apply else 'dry-run')
    after_runs = [dict(row) for row in repo._db.execute('SELECT * FROM ingest_runs ORDER BY id').fetchall()]
    after_settings = [dict(row) for row in repo._db.execute('SELECT * FROM service_settings ORDER BY key').fetchall()]
    assert after_runs == before_runs, 'a maintenance connection must not recover unrelated runs'
    assert after_settings == before_settings, 'a maintenance connection must not bootstrap settings'


def test_normal_repository_open_still_bootstraps_and_recovers_abandoned_runs(tmp_path):
    path = tmp_path / 'normal.sqlite'
    repo = Repository(path)
    project = repo.create_project('normal startup')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'queued'})
    run = repo.create_ingest_run(project, document['id'], document['version_id'])
    repo._db.execute('DELETE FROM service_settings WHERE key=?', ('storage_namespace',))
    reopened = Repository(path)
    assert reopened.get_ingest_run(project, run['id'])['status'] == 'interrupted'
    assert reopened._db.execute('SELECT value FROM service_settings WHERE key=?', ('storage_namespace',)).fetchone() is not None
