from pathlib import Path
import re

import pytest

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
        elif normalized.startswith('CREATE INDEX CONCURRENTLY'):
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
