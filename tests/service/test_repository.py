import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

import knowledge_service.repository as repository_module
from knowledge_service.repository import Repository
from knowledge_service.utils.filters import matches_filter, validate_filter
from knowledge_service.core.time import normalize_time


def test_versions_temporal_boundaries_and_restart(tmp_path):
    path = tmp_path / 'store.sqlite'
    repo = Repository(path)
    project = repo.create_project('甲', {'nested': {'a': 1}})['id']
    first = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': '旧', 'metadata': {'x': 1}, 'valid_from': '2020-01-01', 'valid_until': '2030-01-01'})
    second = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': '新', 'metadata': {'x': 2}, 'valid_from': '2021-01-01'}, expected_version=1)
    assert second['version'] == 2
    assert repo.get_record(project, 'a', valid_at='2022-01-01', known_at=first['recorded_at'])['text'] == '旧'
    assert repo.get_record(project, 'a', valid_at='2022-01-01', known_at=second['recorded_at'])['text'] == '新'
    with pytest.raises(ValueError):
        repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': '错'}, expected_version=1)
    assert Repository(path).history(project, 'a')[0]['metadata'] == {'x': 1}
    assert repo.history(project, 'a')[0]['superseded_at'] == second['recorded_at']
    assert repo.query(project, valid_at='2030-01-01', known_at=first['recorded_at']) == []


def test_atomicity_isolation_and_unknown_dates(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('甲')['id']
    q = repo.create_project('乙')['id']
    with pytest.raises(ValueError):
        repo.put_batch(p, [{'id': 'ok', 'kind': 'entity', 'text': 'ok'}, {'kind': 'invalid', 'text': 'bad'}])
    assert repo.query(p) == []
    repo.put_record(p, {'id': 'a', 'kind': 'entity', 'text': 'a', 'embedding': [1., 0.]})
    assert repo.query(p, include_unknown=False) == []
    assert len(repo.query(p)) == 1
    assert repo.query(q) == []
    with pytest.raises(KeyError):
        repo.get_record(q, 'a')
    with pytest.raises(ValueError):
        repo.put_record(p, {'kind': 'entity', 'text': 'a', 'recorded_at': '2020-01-01'})
    with pytest.raises(ValueError):
        repo.query(q, filters={'field': 'x', 'op': 'bad', 'value': 1})


def test_filters_typed_missing_nested_and_unsafe_paths():
    row = {'kind': 'entity', 'metadata': {'region': {'city': '北京'}, 'tags': ['a'], 'null': None, 'flag': True, 'n': 2}}
    assert matches_filter(row, {'and': [{'field': 'region.city', 'op': 'eq', 'value': '北京'}, {'field': 'tags', 'op': 'contains', 'value': 'a'}]})
    assert not matches_filter(row, {'field': 'missing', 'op': 'eq', 'value': None})
    assert not matches_filter(row, {'field': 'missing', 'op': 'ne', 'value': None})
    assert matches_filter(row, {'field': 'null', 'op': 'eq', 'value': None})
    assert not matches_filter(row, {'field': 'flag', 'op': 'eq', 'value': 1})
    assert matches_filter(row, {'field': 'n', 'op': 'gt', 'value': 1})
    with pytest.raises(ValueError):
        validate_filter({'field': "x'); DROP TABLE records;--", 'op': 'eq', 'value': 1})
    with pytest.raises(ValueError):
        validate_filter({'or': [None]})


def test_time_and_ontology(tmp_path):
    assert normalize_time('2020-01-01T08:00:00+08:00') == normalize_time('2020-01-01')
    with pytest.raises(ValueError):
        normalize_time('2020-01-01T08:00:00')
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    a = repo.save_ontology(p, 'one', {'classes': ['A']})
    b = repo.save_ontology(p, 'two', {'classes': ['B']})
    assert repo.get_ontology(p)['id'] == b['id']
    assert repo.get_ontology(p, a['id'])['turtle'] == 'one'
    assert len(repo.list_ontologies(p)) == 2
    assert repo.list_projects()[0]['id'] == p
    assert repo.get_project(p)['name'] == 'a'


def test_ontology_lineage_metadata_survives_restart(tmp_path):
    path = tmp_path / 'ontology-lineage.sqlite'
    repo = Repository(path)
    p = repo.create_project('a')['id']
    first = repo.save_ontology(p, 'one', {'classes': ['A']})
    metadata = {
        'parent_version_id': first['id'],
        'source_draft_id': 'draft-1',
        'diff': {'classes': {'added': ['B'], 'retained': ['A'], 'removed': []}},
    }
    second = repo.save_ontology(p, 'two', {'classes': ['A', 'B']}, metadata=metadata)
    repo.close()

    reopened = Repository(path)
    assert reopened.get_ontology(p, second['id'])['metadata'] == metadata


def test_correction_replaces_interval_but_history_retains_original(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    initial = repo.put_record(p, {'id': 'policy', 'kind': 'entity', 'text': 'policy', 'valid_from': '2020-01-01', 'valid_until': '2030-01-01', 'embedding': [1., 2.], 'embedding_model': 'model-a'}, recorded_at='2025-01-01T00:00:00Z')
    corrected = repo.put_record(p, {'id': 'policy', 'kind': 'entity', 'text': 'correction', 'valid_from': '2025-01-01', 'valid_until': '2028-01-01'}, expected_version=1, recorded_at='2025-01-01T08:00:00+08:00')
    assert corrected['recorded_at'] > initial['recorded_at']
    assert repo.query(p, valid_at='2022-01-01', known_at=corrected['recorded_at']) == []
    old = repo.get_record(p, 'policy', valid_at='2022-01-01', known_at=initial['recorded_at'])
    assert old['version'] == 1
    assert old['valid_until'] == normalize_time('2030-01-01')
    assert old['embedding_model'] == 'model-a'
    assert 'embedding' not in old  # 删列后向量不存 SQLite
    with pytest.raises(ValueError):
        repo.put_batch(p, [{'id': 'policy', 'kind': 'entity', 'text': 'uncommitted'}, {'kind': 'entity', 'text': 'bad date', 'valid_from': '2025-01-01T00:00:00'}])
    assert len(repo.history(p, 'policy')) == 2
    assert repo.history(p, 'policy')[-1]['superseded_at'] is None


def test_explicit_recorded_at_accepts_aware_datetime_and_stores_utc(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    local_time = datetime(2025, 1, 1, 8, 30, tzinfo=timezone(timedelta(hours=8)))

    saved = repo.put_record(
        p, {'id': 'aware', 'kind': 'entity', 'text': 'aware'}, recorded_at=local_time)

    assert saved['recorded_at'] == '2025-01-01T00:30:00.000000Z'
    assert repo.history(p, 'aware')[0]['recorded_at'] == saved['recorded_at']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '1'])
def test_rejects_invalid_embedding_values(tmp_path, value):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    with pytest.raises(ValueError):
        repo.put_record(p, {'kind': 'entity', 'text': 'a', 'embedding': [value]})


def test_filter_ranges_and_typed_collections():
    row = {'valid_from': normalize_time('2020-01-01'), 'metadata': {'values': [True], 'obj': {'v': True}}}
    assert matches_filter(row, {'field': 'valid_from', 'op': 'eq', 'value': '2020-01-01T08:00:00+08:00'})
    assert not matches_filter(row, {'field': 'values', 'op': 'contains', 'value': 1})
    assert not matches_filter(row, {'field': 'obj', 'op': 'eq', 'value': {'v': 1}})
    assert matches_filter(row, {'field': 'x', 'op': 'exists', 'value': False})
    with pytest.raises(ValueError):
        matches_filter(row, {'or': [{'field': 'x', 'op': 'exists', 'value': False}, {'field': 'x', 'op': 'in', 'value': 1}]})


def test_current_records_ignore_business_validity_and_keep_latest(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    q = repo.create_project('b')['id']
    repo.put_record(p, {'id': 'old', 'kind': 'document', 'text': 'old', 'valid_until': '2000-01-01'})
    repo.put_record(p, {'id': 'old', 'kind': 'document', 'text': 'corrected', 'valid_until': '2001-01-01'})
    repo.put_record(p, {'id': 'future', 'kind': 'document', 'text': 'future', 'valid_from': '2100-01-01'})
    assert repo.query(p) == []
    assert {r['text'] for r in repo.current_records(p)} == {'corrected', 'future'}
    assert repo.current_records(q) == []


def test_batch_expected_versions_conflict_rolls_back_and_shared_timestamp(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('a')['id']
    repo.put_record(p, {'id': 'doc', 'kind': 'document', 'text': 'uploaded'})
    batch = [{'id': 'entity', 'kind': 'entity', 'text': 'derived'}, {'id': 'doc', 'kind': 'document', 'text': 'complete'}]
    with pytest.raises(ValueError, match='冲突'):
        repo.put_batch(p, batch, expected_versions={'doc': 0, 'entity': 0})
    assert [r['text'] for r in repo.current_records(p)] == ['uploaded']
    saved = repo.put_batch(p, batch, expected_versions={'doc': 1, 'entity': 0})
    assert len({r['recorded_at'] for r in saved}) == 1
    assert repo.history(p, 'doc')[0]['superseded_at'] == saved[0]['recorded_at']
    with pytest.raises(ValueError):
        repo.put_batch(p, batch, expected_versions={'not-in-batch': 1})


@pytest.mark.parametrize('concurrent', [False, True])
def test_operation_reservations_are_atomic_across_repository_connections(tmp_path, concurrent):
    path = tmp_path / 'shared-operations.sqlite'
    first = Repository(path)
    project = first.create_project('shared')['id']
    other_project = first.create_project('other')['id']
    future = first.put_record(
        project, {'id': 'future', 'kind': 'document', 'text': 'future'},
        recorded_at='2030-01-01T00:00:00Z')
    second = Repository(path)

    if concurrent:
        barrier = threading.Barrier(2)

        def reserve(repository):
            barrier.wait()
            return repository._reserve_record_operation(project)

        with ThreadPoolExecutor(max_workers=2) as executor:
            operations = list(executor.map(reserve, (first, second)))
    else:
        operations = [
            first._reserve_record_operation(project),
            second._reserve_record_operation(project),
        ]
    (earlier_repository, first_operation), (later_repository, second_operation) = sorted(
        zip((first, second), operations), key=lambda pair: pair[1].recorded_at)

    assert first_operation.recorded_at > future['recorded_at']
    assert second_operation.recorded_at > first_operation.recorded_at
    validating_repository = second if earlier_repository is first else first
    assert validating_repository._record_operation_time(
        project, first_operation) == first_operation.recorded_at
    forged = type(first_operation)(
        first_operation.token, project, second_operation.recorded_at)
    with pytest.raises(ValueError, match='上下文无效'):
        first._record_operation_time(project, forged)
    with pytest.raises(ValueError, match='上下文无效'):
        second._put_record_for_operation(
            other_project, {'id': 'wrong-project', 'kind': 'document', 'text': 'wrong'},
            expected_version=0, operation=first_operation)

    version_one = earlier_repository._put_record_for_operation(
        project, {'id': 'versioned', 'kind': 'document', 'text': 'one'},
        expected_version=0, operation=first_operation)
    version_two = later_repository._put_record_for_operation(
        project, {'id': 'versioned', 'kind': 'document', 'text': 'two'},
        expected_version=1, operation=second_operation)

    assert version_one['recorded_at'] != version_two['recorded_at']
    exact = second.get_record(project, 'versioned', known_at=version_one['recorded_at'])
    assert exact['version'] == 1
    assert exact['text'] == 'one'


def test_operation_reservation_migration_upgrades_existing_database(tmp_path, monkeypatch):
    path = tmp_path / 'operation-reservation-migration.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    assert migrations[-1][0] == 13
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project = legacy.create_project('legacy')['id']
    legacy.close()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='record_operation_reservations'"
        ).fetchone() is None

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    upgraded = Repository(path)
    operation = upgraded._reserve_record_operation(project)
    assert operation.recorded_at
    assert upgraded._db.execute(
        'SELECT COUNT(*) FROM schema_migrations WHERE version=13'
    ).fetchone()[0] == 1
    assert upgraded._db.execute(
        'SELECT project_id,recorded_at FROM record_operation_reservations WHERE token=?',
        (operation.token,)
    ).fetchone()['project_id'] == project


def test_operation_reservation_migration_preserves_legacy_system_time(tmp_path, monkeypatch):
    path = tmp_path / 'operation-reservation-preserve-upgrade.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project = legacy.create_project('legacy timestamps')['id']
    with legacy._transaction():
        legacy._db.execute(
            '''INSERT INTO record_versions
               (project_id,id,version,version_id,payload,recorded_at,superseded_at)
               VALUES (?,?,?,?,?,?,?)''',
            (project, 'offset', 1, 'legacy-offset',
             json.dumps({'id': 'offset', 'kind': 'document', 'text': 'offset'}),
             '2030-01-01T08:00:00+08:00', '2030-01-01T09:00:00+08:00'))
        legacy._db.execute(
            '''INSERT INTO record_versions
               (project_id,id,version,version_id,payload,recorded_at,superseded_at)
               VALUES (?,?,?,?,?,?,?)''',
            (project, 'unparseable', 1, 'legacy-unparseable',
             json.dumps({'id': 'unparseable', 'kind': 'document', 'text': 'legacy'}),
             'legacy-recorded-at', 'legacy-superseded-at'))
    legacy_rows = [
        tuple(row) for row in legacy._db.execute(
            'SELECT * FROM record_versions WHERE project_id=? ORDER BY id',
            (project,)).fetchall()
    ]
    legacy.close()

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    upgraded = Repository(path)
    upgraded_rows = [
        tuple(row) for row in upgraded._db.execute(
            'SELECT * FROM record_versions WHERE project_id=? ORDER BY id',
            (project,)).fetchall()
    ]
    assert upgraded_rows == legacy_rows
    rows = upgraded._db.execute(
        '''SELECT id,recorded_at,superseded_at FROM record_versions
           WHERE project_id=? ORDER BY id''', (project,)).fetchall()
    assert [tuple(row) for row in rows] == [
        ('offset', '2030-01-01T08:00:00+08:00', '2030-01-01T09:00:00+08:00'),
        ('unparseable', 'legacy-recorded-at', 'legacy-superseded-at'),
    ]


def test_operation_reservation_uses_true_parseable_legacy_high_water_without_rewrite(
        tmp_path, monkeypatch):
    path = tmp_path / 'operation-reservation-offset-high-water.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project = legacy.create_project('legacy high water')['id']
    with legacy._transaction():
        for record_id, timestamp in (
                ('offset', '2030-01-01T08:00:00+08:00'),
                ('chronological-max', '2030-01-01T01:00:00Z'),
                ('unparseable', 'legacy-recorded-at')):
            legacy._db.execute(
                '''INSERT INTO record_versions
                   (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                   VALUES (?,?,?,?,?,?,NULL)''',
                (project, record_id, 1, f'legacy-{record_id}',
                 json.dumps({'id': record_id, 'kind': 'document', 'text': record_id}),
                 timestamp))
    legacy.close()

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    upgraded = Repository(path)
    operation = upgraded._reserve_record_operation(project)

    assert operation.recorded_at > normalize_time('2030-01-01T01:00:00Z')
    rows = upgraded._db.execute(
        '''SELECT id,recorded_at FROM record_versions
           WHERE project_id=? ORDER BY id''', (project,)).fetchall()
    assert [tuple(row) for row in rows] == [
        ('chronological-max', '2030-01-01T01:00:00Z'),
        ('offset', '2030-01-01T08:00:00+08:00'),
        ('unparseable', 'legacy-recorded-at'),
    ]


def test_revisions_compare_offset_legacy_current_time_chronologically(
        tmp_path, monkeypatch):
    path = tmp_path / 'legacy-current-offset-revision.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project = legacy.create_project('legacy revisions')['id']
    with legacy._transaction():
        for record_id in ('operation', 'explicit'):
            legacy._db.execute(
                '''INSERT INTO record_versions
                   (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                   VALUES (?,?,?,?,?,?,NULL)''',
                (project, record_id, 1, f'legacy-{record_id}',
                 json.dumps({'id': record_id, 'kind': 'document', 'text': 'legacy'}),
                 '2030-01-01T08:00:00+08:00'))
    legacy.close()

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    issuer = Repository(path)
    writer = Repository(path)
    operation = issuer._reserve_record_operation(project)
    operation_revision = writer._put_record_for_operation(
        project, {'id': 'operation', 'kind': 'document', 'text': 'operation revision'},
        expected_version=1, operation=operation)
    explicit_revision = writer.put_record(
        project, {'id': 'explicit', 'kind': 'document', 'text': 'explicit revision'},
        expected_version=1, recorded_at='2030-01-01T00:30:00Z')

    assert operation_revision['recorded_at'] > normalize_time(
        '2030-01-01T08:00:00+08:00')
    assert explicit_revision['recorded_at'] == '2030-01-01T00:30:00.000000Z'
    assert writer.history(project, 'operation')[0]['recorded_at'] == (
        '2030-01-01T08:00:00+08:00')
    assert writer.history(project, 'explicit')[0]['recorded_at'] == (
        '2030-01-01T08:00:00+08:00')
    with pytest.raises(ValueError, match='操作时间点已被'):
        issuer._put_record_for_operation(
            project, {'id': 'explicit', 'kind': 'document', 'text': 'stale'},
            expected_version=2, operation=operation)


def test_revision_rejects_unparseable_legacy_current_time_clearly(
        tmp_path, monkeypatch):
    path = tmp_path / 'legacy-current-unparseable-revision.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project = legacy.create_project('legacy unparseable revisions')['id']
    with legacy._transaction():
        for record_id in ('operation', 'explicit'):
            legacy._db.execute(
                '''INSERT INTO record_versions
                   (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                   VALUES (?,?,?,?,?,?,NULL)''',
                (project, record_id, 1, f'legacy-{record_id}',
                 json.dumps({'id': record_id, 'kind': 'document', 'text': 'legacy'}),
                 'legacy-recorded-at'))
    legacy.close()

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    issuer = Repository(path)
    writer = Repository(path)
    with pytest.raises(ValueError, match='旧系统时间戳无法解析'):
        writer.put_record(
            project, {'id': 'explicit', 'kind': 'document', 'text': 'revision'},
            expected_version=1, recorded_at='2030-01-01T00:30:00Z')

    operation = issuer._reserve_record_operation(project)
    with pytest.raises(ValueError, match='旧系统时间戳无法解析'):
        writer._put_record_for_operation(
            project, {'id': 'operation', 'kind': 'document', 'text': 'revision'},
            expected_version=1, operation=operation)


def test_schema_migration_receives_active_connection_and_runs_once(tmp_path, monkeypatch):
    path = tmp_path / 'migrations.sqlite'
    connections = []

    def add_probe_table(db):
        connections.append(db)
        db.execute('CREATE TABLE migration_probe (value TEXT NOT NULL)')
        db.execute("INSERT INTO migration_probe VALUES ('applied')")

    monkeypatch.setattr(
        repository_module,
        '_SCHEMA_MIGRATIONS',
        (*repository_module._SCHEMA_MIGRATIONS, (999, add_probe_table)),
    )

    first = Repository(path)
    assert connections == [first._db]
    first.close()
    second = Repository(path)
    assert connections == [connections[0]]
    assert [row['value'] for row in second._db.execute('SELECT value FROM migration_probe')] == ['applied']
    assert second._db.execute(
        'SELECT COUNT(*) FROM schema_migrations WHERE version=?', (999,)
    ).fetchone()[0] == 1
    second.close()


def test_failed_schema_migration_rolls_back_changes_and_version_marker(tmp_path, monkeypatch):
    path = tmp_path / 'failed-migration.sqlite'
    Repository(path).close()

    def fail_after_schema_and_data_changes(db):
        db.execute('CREATE TABLE failed_migration_probe (value TEXT NOT NULL)')
        db.execute("INSERT INTO failed_migration_probe VALUES ('uncommitted')")
        raise RuntimeError('migration failed')

    monkeypatch.setattr(
        repository_module,
        '_SCHEMA_MIGRATIONS',
        (*repository_module._SCHEMA_MIGRATIONS, (1000, fail_after_schema_and_data_changes)),
    )

    with pytest.raises(RuntimeError, match='migration failed'):
        Repository(path)

    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_migration_probe'"
        ).fetchone() is None
        assert db.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE version=?', (1000,)
        ).fetchone()[0] == 0
