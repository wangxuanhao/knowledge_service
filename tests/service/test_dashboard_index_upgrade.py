"""The dashboard's optional read index must not reject valid relation IDs."""
import hashlib
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

import pg_support
from knowledge_service.repository.migrate import MIGRATIONS_DIR, apply_migrations


def test_dashboard_index_upgrade_preserves_rows_and_accepts_long_endpoints(tmp_path):
    schema = 'dashboard_upgrade_' + uuid4().hex
    migration = '0031_dashboard_index_identity_compatibility.sql'
    # Stage a contiguous historical ledger for this minimal index-only schema.
    # The real repository regression separately exercises all production DDL.
    for version in range(1, 31):
        (tmp_path / f'{version:04d}_fixture.sql').write_text('SELECT 1;\n', encoding='utf-8')
    dsn = make_conninfo(pg_support.admin_dsn(), options=f'-csearch_path={schema},public')
    with psycopg.connect(pg_support.admin_dsn(), autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute('''CREATE TABLE record_versions (
                    project_id text, read_kind text, read_type text, id text,
                    valid_from_known boolean, read_subject_id text, read_object_id text,
                    superseded_at text, read_visible boolean)''')
                conn.execute((MIGRATIONS_DIR / '0027_dashboard_aggregate_index.sql').read_text())
                assert apply_migrations(dsn, directory=tmp_path, verbose=False)['current'] == 30
                original = ('project', 'relation', 'https://test/knows', 'original',
                            True, 'a', 'b', None, True)
                conn.execute('INSERT INTO record_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', original)
                identifiers = [''.join(hashlib.sha256(f'{kind}:{i}'.encode()).hexdigest()
                                       for i in range(35))[:2200] for kind in ('subject', 'object')]
                relation = ('project', 'relation', 'https://test/knows', 'long-endpoints',
                            False, *identifiers, None, True)
                with pytest.raises(psycopg.errors.ProgramLimitExceeded):
                    conn.execute('INSERT INTO record_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', relation)

                (tmp_path / migration).write_bytes((MIGRATIONS_DIR / migration).read_bytes())
                result = apply_migrations(dsn, directory=tmp_path, verbose=False)
                assert result['current'] == 31
                conn.execute('INSERT INTO record_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', relation)
                assert conn.execute('SELECT * FROM record_versions WHERE id=%s', ('original',)).fetchone() == original
                assert conn.execute('SELECT * FROM record_versions WHERE id=%s', ('long-endpoints',)).fetchone() == relation
                assert conn.execute("SELECT to_regclass('versions_visible_summary')").fetchone()[0] is None
                assert conn.execute('''SELECT i.indisvalid, i.indisready FROM pg_index i
                    WHERE i.indexrelid=to_regclass('versions_visible_summary_compact')''').fetchone() == (True, True)
                assert apply_migrations(dsn, directory=tmp_path, verbose=False)['applied'] == []
                assert conn.execute('SELECT count(*) FROM record_versions').fetchone()[0] == 2
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
