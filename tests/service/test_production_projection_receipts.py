"""PG receipts are per record; existing active generations survive migration."""
import json
from pathlib import Path

import pytest
from psycopg.errors import RaiseException

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection, projection_fingerprint
from knowledge_service.repository.core import Repository
from knowledge_service.repository.projection_store import ProjectionLeaseLost


MIGRATION = (Path(__file__).resolve().parents[2] / 'knowledge_service' /
             'repository' / 'migrations' / '0025_milvus_projection_receipts.sql')


def legacy_state(expected):
    return {'phase': 'ready', 'build_id': 'original-build', 'index_version': 2,
            'embedding_model': 'production-model', 'dim': 1024,
            'catalog': default_catalog().to_dict(), 'expected_projection': expected}


def activate_legacy(repo, project, expected, watermark=32):
    with repo._transaction():
        repo.projections._ensure_cursor(project, 'milvus', 3)
        repo._db.execute('INSERT INTO project_projection_sequences(project_id,projection_seq) '
            'VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET projection_seq=excluded.projection_seq',
            (project, watermark))
        repo._db.execute("UPDATE projection_cursors SET status='ready',applied_seq=?,"
            "active_destination='knowledge_vectors',rebuild_state=? WHERE project_id=? AND target='milvus'",
            (watermark, json.dumps(legacy_state(expected)), project))


def migration_sql():
    assert MIGRATION.is_file(), 'receipt migration has not been added'
    return MIGRATION.read_text(encoding='utf-8')


def assert_receipt_table(repo):
    assert repo._db.execute("SELECT to_regclass('milvus_projection_receipts') AS receipt_table").fetchone()['receipt_table'], (
        'receipts still live inside the whole-project cursor JSON')


def test_receipt_migration_preserves_both_active_g2_projects_and_watermarks(tmp_path):
    source = migration_sql()
    repo = Repository(tmp_path / 'legacy-active-g2')
    projects = [repo.create_project(name)['id'] for name in ('first', 'second')]
    for index, project in enumerate(projects):
        expected = {'record': {'version_id': f'v{index}', 'fingerprint': str(index) * 64}}
        activate_legacy(repo, project, expected, watermark=32 + index)
    before = [repo.projections.cursor(project, 'milvus') for project in projects]
    with repo._transaction():
        repo._db.execute(source)
    for project, original in zip(projects, before):
        current = repo.projections.cursor(project, 'milvus')
        for field in ('status', 'applied_seq', 'schema_version', 'active_destination'):
            assert current[field] == original[field]
        state = current['rebuild_state']
        assert 'expected_projection' not in state
        assert state == {key: value for key, value in original['rebuild_state'].items()
                         if key != 'expected_projection'}
        rows = repo._db.execute('SELECT index_version,record_id,version_id,fingerprint '
            'FROM milvus_projection_receipts WHERE project_id=?', (project,)).fetchall()
        assert [dict(row) for row in rows] == [{'index_version': 2, 'record_id': 'record',
            **original['rebuild_state']['expected_projection']['record']}]
        assert repo.projections.search_index(project, 3)['index_version'] == 2


def test_invalid_legacy_receipt_rolls_back_without_erasing_cursor_manifest(tmp_path):
    source = migration_sql()
    repo = Repository(tmp_path / 'bad-legacy-receipt')
    project = repo.create_project('invalid receipt')['id']
    expected = {'record': {'version_id': 'v1', 'fingerprint': 'invalid'}}
    activate_legacy(repo, project, expected)
    before = repo.projections.cursor(project, 'milvus')
    with pytest.raises(RaiseException, match='Invalid legacy Milvus projection receipt') as rejected:
        with repo._transaction():
            repo._db.execute(source)
    assert rejected.value.sqlstate == 'P0001'
    assert repo.projections.cursor(project, 'milvus') == before


def test_incremental_ack_changes_only_target_receipt_and_keeps_cursor_small(tmp_path):
    repo = Repository(tmp_path / 'incremental-receipt')
    assert_receipt_table(repo)
    project = repo.create_project('receipt increment')['id']
    first = repo.put_record(project, {'id': 'changed', 'kind': 'chunk', 'text': 'old'})
    keep = repo.put_record(project, {'id': 'untouched', 'kind': 'chunk', 'text': 'same'})
    rows = [build_projection(ProjectionInput(row), default_catalog(), index_version=2)
            for row in (first, keep)]
    expected = {row['record_id']: {'version_id': row['version_id'],
                                 'fingerprint': projection_fingerprint(row)} for row in rows}
    activate_legacy(repo, project, expected, watermark=2)
    with repo._transaction():
        repo._db.execute(migration_sql())
    before_keep = dict(repo._db.execute('SELECT * FROM milvus_projection_receipts '
        'WHERE project_id=? AND record_id=?', (project, 'untouched')).fetchone())
    changed = repo.put_record(project, {'id': 'changed', 'kind': 'chunk', 'text': 'new'},
                              expected_version=1)
    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    assert [event['operation'] for event in claim.events] == ['tombstone', 'upsert']
    repo.projections.ack_contiguous(claim, 3)
    cursor = repo.projections.cursor(project, 'milvus')
    assert 'expected_projection' not in cursor['rebuild_state']
    assert cursor['applied_seq'] == 4
    actual = repo._db.execute('SELECT version_id FROM milvus_projection_receipts '
        'WHERE project_id=? AND index_version=2 AND record_id=?', (project, 'changed')).fetchone()
    assert actual['version_id'] == changed['version_id']
    assert dict(repo._db.execute('SELECT * FROM milvus_projection_receipts '
        'WHERE project_id=? AND record_id=?', (project, 'untouched')).fetchone()) == before_keep


def test_lost_claim_does_not_modify_receipts_or_advance_cursor(tmp_path):
    repo = Repository(tmp_path / 'lost-receipt-claim')
    assert_receipt_table(repo)
    project = repo.create_project('lost receipt claim')['id']
    activate_legacy(repo, project, {}, watermark=0)
    with repo._transaction():
        repo._db.execute(migration_sql())
    repo.put_record(project, {'id': 'new', 'kind': 'chunk', 'text': 'new'})
    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    with repo._transaction():
        repo._db.execute("UPDATE projection_cursors SET claim_token='new-owner' "
                         "WHERE project_id=? AND target='milvus'", (project,))
    with pytest.raises(ProjectionLeaseLost):
        repo.projections.ack_contiguous(claim, 3)
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 0
    assert repo._db.execute('SELECT count(*) AS n FROM milvus_projection_receipts '
                           'WHERE project_id=?', (project,)).fetchone()['n'] == 0
