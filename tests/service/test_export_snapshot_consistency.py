"""A project export must not split a committed write across its sections."""
from contextlib import ExitStack
from uuid import uuid4

import pytest

from knowledge_service.repository import Repository


def test_export_sees_one_committed_state(tmp_path, monkeypatch):
    path = tmp_path / 'export-consistency'
    with ExitStack() as cleanup:
        reader = Repository(path)
        cleanup.callback(reader.close)
        writer = Repository(path)
        cleanup.callback(writer.close)
        project = reader.create_project('export isolation')['id']
        marker = uuid4().hex
        first = writer.put_record(project, {'id': marker, 'kind': 'chunk', 'text': 'v1'})
        before = reader.export_projection(project)
        original = reader._assertions.export
        modes = []

        def concurrent_commit(project_id):
            # Record history was read; commit a new version and a reference
            # to it before the reader queries artifacts.
            row = reader._db.execute(
                "SELECT current_setting('transaction_isolation') AS isolation, "
                "current_setting('transaction_read_only') AS read_only").fetchone()
            modes.append((row['isolation'], row['read_only']))
            with writer.unit_of_work():
                second = writer.put_record(
                    project, {'id': marker, 'kind': 'chunk', 'text': 'v2'}, expected_version=1)
                writer.save_artifact('test-marker', {
                    'id': marker, 'project_id': project, 'version_id': second['version_id'],
                })
            return original(project_id)

        with monkeypatch.context() as scoped:
            scoped.setattr(reader._assertions, 'export', concurrent_commit)
            during = reader.export_projection(project)
        assert during == before
        assert modes == [('repeatable read', 'on')]
        assert not reader._db.in_transaction

        after = reader.export_projection(project)
        v1, v2 = after['records']
        assert v1['version_id'] == first['version_id']
        assert v1['superseded_at'] is not None
        assert (v2['version'], v2['text'], v2['superseded_at']) == (2, 'v2', None)
        assert after['artifacts'][0]['payload']['version_id'] == v2['version_id']


def test_export_never_inherits_a_write_transaction(tmp_path):
    repo = Repository(tmp_path / 'export-nesting')
    try:
        with repo.unit_of_work():
            project = repo.create_project('not yet committed')['id']
            with pytest.raises(RuntimeError, match='requires a top-level transaction'):
                repo.export_projection(project)
            assert repo.get_project(project)['id'] == project
        assert repo.export_projection(project)['project']['id'] == project
    finally:
        repo.close()


def test_export_releases_snapshot_on_error(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'export-failure')
    try:
        project = repo.create_project('export rollback')['id']

        def fail(_):
            raise ValueError('injected export failure')

        with monkeypatch.context() as scoped:
            scoped.setattr(repo._assertions, 'export', fail)
            with pytest.raises(ValueError, match='injected export failure'):
                repo.export_projection(project)
        assert not repo._db.in_transaction
        assert repo.export_projection(project)['project']['id'] == project
    finally:
        repo.close()
