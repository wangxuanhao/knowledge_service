from pathlib import Path
import re
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

import pg_support
from knowledge_service.repository import migrate


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=()):
        normalized = ' '.join(statement.split())
        self.connection.executed.append((normalized, params))
        if normalized.startswith('SELECT version, name, checksum'):
            self._rows = [
                (version, name, checksum)
                for version, (name, checksum) in self.connection.applied.items()
            ]
        elif normalized.startswith('INSERT INTO schema_migrations'):
            version, name, checksum = params
            self.connection.applied[version] = (name, checksum)
        elif normalized == 'SELECT pg_advisory_unlock(%s)':
            self._rows = [(True,)]
        elif 'FROM pg_class c JOIN pg_index i' in normalized:
            state = self.connection.indexes.get(params[0])
            self._rows = [] if state is None else [state]
        elif normalized.startswith('DROP INDEX CONCURRENTLY IF EXISTS'):
            name = re.search(r'"([A-Za-z0-9_]+)"', normalized).group(1)
            self.connection.indexes.pop(name, None)
        elif 'CREATE INDEX CONCURRENTLY fail_once' in normalized:
            if self.connection.fail_once:
                self.connection.fail_once = False
                raise RuntimeError('injected concurrent-index failure')
            self.connection.completed.add('fail_once')
            self.connection.indexes['fail_once'] = (True, True)
        elif 'CREATE INDEX CONCURRENTLY' in normalized and 'already_done' in normalized:
            self.connection.completed.add('already_done')
            self.connection.indexes['already_done'] = (True, True)
        elif 'CREATE INDEX CONCURRENTLY' in normalized:
            name = re.search(
                r'CREATE INDEX CONCURRENTLY(?: IF NOT EXISTS)? ([A-Za-z0-9_]+)',
                normalized).group(1)
            self.connection.indexes[name] = (
                self.connection.created_indexes_valid,
                self.connection.created_indexes_valid,
            )
        if not (normalized.startswith('SELECT version, name, checksum') or
                'FROM pg_class c JOIN pg_index i' in normalized):
            self._rows = []
        return self

    def fetchall(self):
        return list(self._rows)

    def executemany(self, statement, params):
        for values in params:
            self.execute(statement, values)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self):
        self.applied = {}
        self.completed = set()
        self.executed = []
        self.fail_once = True
        self.indexes = {}
        self.created_indexes_valid = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self)


def test_non_transactional_migration_is_statement_bounded_and_retryable(
        tmp_path: Path, monkeypatch):
    migration = tmp_path / '0001_concurrent_indexes.sql'
    migration.write_text(
        '-- knowledge-service-migration: no-transaction\n'
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS already_done ON records (project_id);\n'
        '-- knowledge-service-migration: statement-break\n'
        'CREATE INDEX CONCURRENTLY fail_once ON records (recorded_at);\n',
        encoding='utf-8',
    )
    connection = _FakeConnection()
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)

    with pytest.raises(migrate.MigrationError, match='injected concurrent-index failure'):
        migrate.apply_migrations('postgresql://test', directory=tmp_path, verbose=False)

    assert connection.completed == {'already_done'}
    assert connection.applied == {}
    assert not any(sql in {'BEGIN', 'COMMIT'} for sql, _ in connection.executed)

    migrate.apply_migrations('postgresql://test', directory=tmp_path, verbose=False)

    assert connection.completed == {'already_done', 'fail_once'}
    assert set(connection.applied) == {1}


def test_non_transactional_migration_requires_explicit_statement_break(tmp_path: Path):
    migration = tmp_path / '0001_bad.sql'
    migration.write_text(
        '-- knowledge-service-migration: no-transaction\n'
        'CREATE INDEX CONCURRENTLY first_idx ON records (project_id);\n'
        'CREATE INDEX CONCURRENTLY second_idx ON records (recorded_at);\n',
        encoding='utf-8',
    )

    with pytest.raises(migrate.MigrationError, match='statement-break'):
        migrate.discover_migrations(tmp_path)[0].execution()


def test_concurrent_index_migration_repairs_invalid_index_before_retry(
        tmp_path: Path, monkeypatch):
    (tmp_path / '0001_index.sql').write_text(
        '-- knowledge-service-migration: no-transaction\n'
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS stale_idx ON records (project_id);\n',
        encoding='utf-8',
    )
    connection = _FakeConnection()
    connection.indexes['stale_idx'] = (False, False)
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)

    migrate.apply_migrations('postgresql://test', directory=tmp_path, verbose=False)

    sql = [statement for statement, _ in connection.executed]
    assert next(i for i, item in enumerate(sql) if item.startswith('DROP INDEX CONCURRENTLY')) < next(
        i for i, item in enumerate(sql) if item.startswith('CREATE INDEX CONCURRENTLY'))
    assert connection.indexes['stale_idx'] == (True, True)
    assert set(connection.applied) == {1}


def test_concurrent_index_migration_verifies_index_before_bookkeeping(
        tmp_path: Path, monkeypatch):
    (tmp_path / '0001_index.sql').write_text(
        '-- knowledge-service-migration: no-transaction\n'
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS broken_idx ON records (project_id);\n',
        encoding='utf-8',
    )
    connection = _FakeConnection()
    connection.created_indexes_valid = False
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)

    with pytest.raises(migrate.MigrationError, match='broken_idx.*无效'):
        migrate.apply_migrations('postgresql://test', directory=tmp_path, verbose=False)

    assert connection.applied == {}


def test_migration_sets_configurable_lock_timeout_before_locking(
        tmp_path: Path, monkeypatch):
    (tmp_path / '0001_empty.sql').write_text('SELECT 1;\n', encoding='utf-8')
    connection = _FakeConnection()
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)

    migrate.apply_migrations(
        'postgresql://test', directory=tmp_path, verbose=False, lock_timeout_ms=2750)

    assert connection.executed[0] == (
        "SELECT set_config('lock_timeout', %s, false)", ('2750ms',))
    assert connection.executed[1][0] == 'SELECT pg_advisory_lock(%s)'


def test_default_fresh_database_uses_one_baseline_transaction(monkeypatch):
    connection = _FakeConnection()
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)

    first = migrate.apply_migrations('postgresql://test', verbose=False)
    migrations = migrate.discover_migrations()
    historical = {item.version: (item.name, item.checksum)
                  for item in migrations}
    assert connection.applied == historical
    assert first['current'] == max(historical)
    followup_transactions = sum(item.execution().transactional for item in migrations
                                if item.version > migrate.BASELINE_VERSION)
    assert sum(statement == 'BEGIN' for statement, _ in connection.executed) == 1 + followup_transactions
    assert sum(statement == 'COMMIT' for statement, _ in connection.executed) == 1 + followup_transactions
    baseline = next(statement for statement, _ in connection.executed if 'CREATE TABLE projects' in statement)
    assert 'CREATE INDEX CONCURRENTLY' not in baseline

    second = migrate.apply_migrations('postgresql://test', verbose=False)
    assert second['applied'] == []


def test_default_existing_database_applies_only_missing_migrations(monkeypatch):
    connection = _FakeConnection()
    monkeypatch.setattr(migrate.psycopg, 'connect', lambda *args, **kwargs: connection)
    migrations = migrate.discover_migrations()
    history = {item.version: (item.name, item.checksum) for item in migrations
               if item.version < migrate.BASELINE_VERSION}
    missing = {item.version: (item.name, item.checksum) for item in migrations
               if item.version >= migrate.BASELINE_VERSION}
    connection.applied = history.copy()
    result = migrate.apply_migrations('postgresql://test', verbose=False)
    assert result['applied'] == list(missing)
    assert connection.applied == {**history, **missing}
    assert not any('CREATE TABLE projects' in statement for statement, _ in connection.executed)

    version = migrations[0].version
    name, _ = connection.applied[version]
    connection.applied[version] = (name, 'invalid-checksum')
    with pytest.raises(migrate.MigrationError, match='已应用，但内容已变化'):
        migrate.apply_migrations('postgresql://test', verbose=False)


def test_community_permissions_upgrade_allows_configured_role_graph_writes(monkeypatch):
    schema = sql.Identifier('kgtest_community_' + uuid4().hex)
    role_name = 'kgtest-community-' + uuid4().hex
    role = sql.Identifier(role_name)
    dsn = psycopg.conninfo.make_conninfo(
        pg_support.admin_dsn(), options=f'-csearch_path={schema.as_string()},public')
    discover = migrate.discover_migrations

    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(role))
        try:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(schema))
            with monkeypatch.context() as version_47:
                version_47.delenv('POSTGRES_APP_USER', raising=False)
                version_47.setattr(migrate, 'discover_migrations', lambda directory: [
                    item for item in discover(directory) if item.version <= 47])
                assert migrate.apply_migrations(dsn, verbose=False)['current'] == 47
            db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(schema, role))
            db.execute(sql.SQL('GRANT SELECT ON projects TO {}').format(role))
            db.execute(sql.SQL('GRANT INSERT ON record_versions TO {}').format(role))
            db.execute(sql.SQL(
                'GRANT SELECT, DELETE ON entity_duplicate_keys, entity_identity_keys TO {}'
            ).format(role))
            db.execute("INSERT INTO projects VALUES ('p', 'Project', '{}', now())")

            monkeypatch.setenv('POSTGRES_APP_USER', role_name)
            migrate.apply_migrations(dsn, verbose=False)
            with db.transaction():
                db.execute(sql.SQL('SET LOCAL ROLE {}').format(role))
                db.execute("""
                    INSERT INTO record_versions (
                        project_id, id, version, version_id, payload, recorded_at,
                        platform_mode, platform_values, business_mode, business_values,
                        audience_mode, audience_values, scope_identity,
                        valid_from_known, valid_from_epoch_ms, valid_until_known, valid_until_epoch_ms
                    ) VALUES ('p', 'entity', 1, 'entity-v1', '{"kind":"entity"}', now(),
                              'unknown', '{}', 'unknown', '{}', 'unknown', '{}', '',
                              false, 0, false, 0)
                """)
                assert db.execute('SELECT generation FROM community_graph_changes').fetchone() == (1,)
                for table in ('community_graph_changes', 'community_scopes', 'knowledge_communities'):
                    for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                        assert db.execute('SELECT has_table_privilege(%s, %s)',
                                          (table, privilege)).fetchone()[0], (table, privilege)
        finally:
            db.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(schema))
            db.execute(sql.SQL('DROP ROLE {}').format(role))
