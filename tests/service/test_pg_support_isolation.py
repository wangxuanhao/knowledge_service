"""Exercise database ownership on the explicitly configured test instance."""
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

import pg_support


@pytest.fixture
def postgres_backend():
    # These tests explicitly exercise the helper's complete lifecycle, without
    # changing the pytest session's real pg_support state or requiring it here.
    return None


def fresh_harness():
    spec = spec_from_file_location('_pg_support_under_test', pg_support.__file__)
    harness = module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


@pytest.fixture
def harness_endpoint(monkeypatch):
    try:
        dsn = pg_support.admin_dsn('postgres')
        admin = psycopg.connect(dsn, autocommit=True)
    except (RuntimeError, psycopg.OperationalError) as exc:
        pytest.skip(f'Explicit test PostgreSQL unavailable: {exc}')
    with admin:
        name = f'knowledge_test_isolation_{uuid4().hex}'
        monkeypatch.delenv('KG_TEST_POSTGRES_DB', raising=False)
        monkeypatch.setenv('KG_TEST_POSTGRES_ADMIN_DSN', psycopg.conninfo.make_conninfo(dsn, dbname=name))
        # The explicit DSN must suffice for both database and maintenance paths.
        monkeypatch.delenv('KG_DATABASE_HOST', raising=False)
        monkeypatch.delenv('KG_DATABASE_PORT', raising=False)
        harness = fresh_harness()
        try:
            yield harness, admin, name
        finally:
            harness.teardown()


@contextmanager
def sentinel_database(admin, name, dsn):
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute('CREATE SCHEMA kgtest_01')
            connection.execute('CREATE TABLE kgtest_01.sentinel (value text)')
            connection.execute("INSERT INTO kgtest_01.sentinel VALUES ('keep')")
            yield connection
    finally:
        admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def test_unconfigured_harness_does_not_connect(monkeypatch):
    for key in ('KG_TEST_POSTGRES_ADMIN_DSN', 'KG_TEST_POSTGRES_DB', 'KG_DATABASE_HOST',
                'KG_DATABASE_PORT', 'POSTGRES_USER', 'POSTGRES_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    harness = fresh_harness()
    other_session = fresh_harness()
    assert harness.TEST_DB != other_session.TEST_DB
    assert harness.TEST_DB.startswith('knowledge_test_')

    def unexpected_connect(*args, **kwargs):
        pytest.fail('Unconfigured setup or teardown attempted a PostgreSQL connection')

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, 'connect', unexpected_connect)
        ok, reason = harness.available()
        assert not ok and 'dedicated PostgreSQL test host' in reason
        harness.teardown()


def test_existing_database_is_not_adopted_or_dropped(harness_endpoint):
    harness, admin, name = harness_endpoint
    with sentinel_database(admin, name, harness.admin_dsn()) as sentinel:
        initial_extensions = sentinel.execute('SELECT extname FROM pg_extension ORDER BY 1').fetchall()
        initial_commit = sentinel.execute('SHOW synchronous_commit').fetchone()
        harness.teardown()  # no setup at all
        with pytest.raises(RuntimeError, match='already exists'):
            harness.ensure_test_database()
        harness.teardown()  # failed setup must not drop or evict the owner
        assert sentinel.execute('SELECT value FROM kgtest_01.sentinel').fetchone() == ('keep',)
        assert sentinel.execute('SELECT extname FROM pg_extension ORDER BY 1').fetchall() == initial_extensions
        with psycopg.connect(harness.admin_dsn()) as fresh:
            assert fresh.execute('SHOW synchronous_commit').fetchone() == initial_commit


def test_created_database_is_removed_even_after_setup_failure(harness_endpoint, monkeypatch):
    harness, admin, name = harness_endpoint

    def fail_setup():
        raise RuntimeError('injected setup failure')

    with monkeypatch.context() as patch:
        patch.setattr(harness, '_conn', fail_setup)
        with pytest.raises(RuntimeError, match='injected setup failure'):
            harness.ensure_test_database()
    assert harness._database_oid(admin, name) is not None
    harness.teardown()
    assert harness._database_oid(admin, name) is None


def test_teardown_leaves_a_same_named_replacement_alone(harness_endpoint):
    harness, admin, name = harness_endpoint
    harness.ensure_test_database()
    original = harness._database_oid(admin, name)
    assert original is not None
    harness._conn().close()
    admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
    with sentinel_database(admin, name, harness.admin_dsn()) as sentinel:
        assert harness._database_oid(admin, name) != original
        harness.teardown()
        assert sentinel.execute('SELECT value FROM kgtest_01.sentinel').fetchone() == ('keep',)
