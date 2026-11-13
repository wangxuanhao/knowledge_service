"""Explicit upgrades replace obsolete captures without taking a live lease."""
from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput
from knowledge_service.repository.core import Repository
from knowledge_service.repository.projection_snapshot import CapturedProjectionInputs
from knowledge_service.repository.projection_store import ProjectionLeaseLost, ProjectionStore
from knowledge_service.services.projection_sync import ProjectionSync


class CursorConnection:
    """Only the locked cursor read and claim publication used by begin_rebuild."""
    def __init__(self, cursor):
        self.cursor = deepcopy(cursor)
        self.writes = []

    def execute(self, statement, params):
        if statement.startswith('INSERT INTO project_projection_sequences'):
            return SimpleNamespace(rowcount=0)
        if 'SELECT * FROM projection_cursors' in statement:
            return SimpleNamespace(fetchone=lambda: deepcopy(self.cursor))
        if 'UPDATE projection_cursors SET schema_version=' in statement:
            self.writes.append(tuple(params))
            schema, applied, state, owner, token, expiry, start, end, now, project, target = params
            self.cursor.update(schema_version=schema, applied_seq=applied,
                rebuild_state=json.loads(state), status='rebuilding', claim_owner=owner,
                claim_token=token, claim_until=expiry, claimed_from_seq=start, claimed_to_seq=end)
            return SimpleNamespace(rowcount=1)
        raise AssertionError('unexpected database operation: ' + statement)


class CaptureStore(ProjectionStore):
    def __init__(self, repository):
        super().__init__(repository)
        self.captured, self.retired = [], []

    def _ensure_cursor(self, *args):
        pass

    @property
    def generation_cleanups(self):
        return SimpleNamespace(enqueue=lambda project, generation: self.retired.append((project, generation)))

    def _persist_rebuild_snapshot(self, claimed, state, records, snapshot_seq, lease_is_current):
        assert lease_is_current()
        self.captured.append((claimed.build_id, deepcopy(list(records))))
        state.update(phase='snapshot', snapshot_seq=snapshot_seq, applied_seq=snapshot_seq)


def obsolete_capture(*, leased=False):
    return {'project_id': 'project', 'target': 'milvus', 'schema_version': 3,
        'applied_seq': 5, 'status': 'failed', 'active_destination': None,
        'claim_owner': 'old-owner' if leased else None,
        'claim_token': 'old-token' if leased else None,
        'claim_until': '2099-01-01T00:00:00Z' if leased else None,
        'claimed_from_seq': 6 if leased else None,
        'claimed_to_seq': 5 if leased else None,
        'rebuild_state': {'build_id': 'obsolete-build', 'phase': 'snapshot',
            'snapshot_seq': 5, 'applied_seq': 5, 'index_version': 1,
            'embedding_model': 'test', 'dim': 1, 'catalog': default_catalog().to_dict()}}


class ProjectionSchemaUpgradeTests(unittest.TestCase):
    def system(self, *, leased=False):
        connection = CursorConnection(obsolete_capture(leased=leased))
        reader = SimpleNamespace(projections=SimpleNamespace(project_watermark=lambda project: 7))
        repository = SimpleNamespace(_db=connection, _transaction=lambda: nullcontext(),
            source_context_diagnostics=lambda project: {},
            independent_read_view=lambda: nullcontext(reader))
        return CaptureStore(repository), connection

    def begin(self, store, schema):
        return store.begin_rebuild('project', 'milvus', schema, 'new-owner',
            lease_guard=lambda claimed: nullcontext(lambda: True), embedding_model='test', dim=1)

    def test_explicit_upgrade_captures_current_inputs_in_a_new_generation(self):
        store, connection = self.system()
        current = ProjectionInput({'project_id': 'project', 'id': 'body',
            'version_id': 'current-v2', 'kind': 'chunk', 'text': 'current body'})
        with patch('knowledge_service.repository.projection_inputs.ProjectionInputs.batches',
                return_value=iter([(current,)])):
            try:
                rebuilt = self.begin(store, 4)
            except ProjectionLeaseLost as exc:
                self.fail('a released obsolete capture must allow an explicit schema upgrade: ' + str(exc))
        self.assertNotEqual(rebuilt['claim'].build_id, 'obsolete-build')
        self.assertEqual(rebuilt['state']['index_version'], 2)
        self.assertEqual(rebuilt['state']['snapshot_seq'], 7)
        self.assertEqual(store.captured, [(rebuilt['claim'].build_id, [current])])
        self.assertEqual(store.retired, [('project', 1)])
        self.assertEqual(connection.cursor['schema_version'], 4)

    def test_explicit_upgrade_preserves_an_active_old_schema_lease(self):
        store, connection = self.system(leased=True)
        before = deepcopy(connection.cursor)
        with self.assertRaises(ProjectionLeaseLost):
            self.begin(store, 4)
        self.assertEqual(connection.cursor, before)
        self.assertEqual(connection.writes, [])
        self.assertEqual(store.captured, [])
        self.assertEqual(store.retired, [])

    def test_same_schema_clean_retry_resumes_the_original_capture(self):
        store, connection = self.system()
        rebuilt = self.begin(store, 3)
        self.assertEqual(rebuilt['claim'].build_id, 'obsolete-build')
        self.assertEqual(rebuilt['state']['index_version'], 1)
        self.assertEqual(rebuilt['snapshot_seq'], 5)
        self.assertEqual(store.captured, [])
        self.assertEqual(store.retired, [])
        self.assertEqual(connection.cursor['schema_version'], 3)


def begin_capture(repo, project, schema):
    sync = ProjectionSync(repo.projections, {}, schema_version=schema)
    return repo.projections.begin_rebuild(project, 'milvus', schema, 'schema-upgrade',
        lease_guard=sync._rebuild_lease_guard, embedding_model='test', dim=1)


def test_pg_explicit_upgrade_replaces_released_obsolete_capture_and_keeps_frozen_bodies(tmp_path):
    repo = Repository(tmp_path / 'projection-schema-upgrade')
    project = repo.create_project('schema upgrade')['id']
    original = repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'old body'})
    old = begin_capture(repo, project, 3)
    repo.projections.mark_failed(old['claim'], RuntimeError('clean stop before vector writes'), 3)
    current = repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'current body'})

    rebuilt = begin_capture(repo, project, 4)
    assert rebuilt['claim'].build_id != old['claim'].build_id
    assert rebuilt['state']['index_version'] == old['state']['index_version'] + 1
    assert rebuilt['snapshot_seq'] == repo.projections.project_watermark(project)
    captured, = list(CapturedProjectionInputs(repo, rebuilt['claim']))
    assert captured.record['version_id'] == current['version_id']
    assert captured.record['text'] == 'current body'
    old_input, = list(CapturedProjectionInputs(repo, old['claim']))
    assert old_input.record['version_id'] == original['version_id']
    assert old_input.record['text'] == 'old body'
    repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'later body'})
    assert list(CapturedProjectionInputs(repo, rebuilt['claim'])) == [captured]
    assert repo.projections.cursor(project, 'milvus')['schema_version'] == 4
    assert repo.projections.cursor(project, 'milvus')['active_destination'] is None
    with pytest.raises(ProjectionLeaseLost):
        repo.projections.record_rebuild_progress(old['claim'], 3, old['state'])
    retired = repo._db.execute('SELECT index_version FROM milvus_generation_cleanup_outbox '
        'WHERE project_id=? ORDER BY index_version', (project,)).fetchall()
    assert [row['index_version'] for row in retired] == [old['state']['index_version']]


def test_pg_explicit_upgrade_cannot_replace_a_live_obsolete_schema_capture(tmp_path):
    repo = Repository(tmp_path / 'projection-schema-live-lease')
    project = repo.create_project('schema live lease')['id']
    repo.put_record(project, {'id': 'body', 'kind': 'chunk', 'text': 'body'})
    old = begin_capture(repo, project, 3)
    before = repo.projections.cursor(project, 'milvus')
    with pytest.raises(ProjectionLeaseLost):
        begin_capture(repo, project, 4)
    assert repo.projections.cursor(project, 'milvus') == before
    captured, = list(CapturedProjectionInputs(repo, old['claim']))
    assert captured.record['text'] == 'body'
    assert not repo._db.execute('SELECT 1 FROM milvus_generation_cleanup_outbox '
        'WHERE project_id=?', (project,)).fetchall()
