from uuid import uuid4

import psycopg
from psycopg import sql

import pg_support
from knowledge_service.repository.migrate import MIGRATIONS_DIR


def test_projection_tables_allow_configured_app_dml_without_ddl():
    role = 'kgtest-projection-' + uuid4().hex
    schema = 'kgtest_projection_' + uuid4().hex
    with psycopg.connect(pg_support.admin_dsn()) as admin:
        try:
            admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            admin.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            tables = (
                'project_projection_sequences', 'projection_outbox',
                'projection_cursors', 'projection_rebuild_snapshots',
                'projection_cleanup_outbox',
            )
            for table in (*tables, 'unrelated'):
                admin.execute(sql.SQL('CREATE TABLE {} (id text)').format(sql.Identifier(table)))
            assert not admin.execute(
                'SELECT has_table_privilege(%s, %s, %s)',
                (role, f'{schema}.projection_outbox', 'SELECT'),
            ).fetchone()[0]
            admin.execute("SELECT set_config('knowledge_service.app_role', %s, true)", (role,))
            admin.execute((MIGRATIONS_DIR / '0018_projection_application_permissions.sql').read_text(encoding='utf-8'))
            for table in tables:
                for operation in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                    assert admin.execute(
                        'SELECT has_table_privilege(%s, %s, %s)',
                        (role, f'{schema}.{table}', operation),
                    ).fetchone()[0], (table, operation)
            assert not admin.execute(
                'SELECT has_schema_privilege(%s, %s, %s)',
                (role, schema, 'CREATE'),
            ).fetchone()[0]
            assert not admin.execute(
                'SELECT has_table_privilege(%s, %s, %s)',
                (role, f'{schema}.unrelated', 'SELECT'),
            ).fetchone()[0]
        finally:
            admin.rollback()


def test_application_role_can_enqueue_and_rollback(tmp_path):
    import pytest
    from knowledge_service.repository import Repository

    repo = Repository(tmp_path / 'restricted-enqueue')
    project = repo.create_project('restricted enqueue')['id']
    role = 'kgtest_projection_' + uuid4().hex
    db = repo._db
    with pytest.raises(RuntimeError, match='^abort enqueue$'):
        with repo._transaction():
            db.execute(f'CREATE ROLE {role} NOLOGIN')
            schema = sql.Identifier(db.execute('SELECT current_schema()').fetchone()[0]).as_string()
            db.execute(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
            db.execute("SELECT set_config('knowledge_service.app_role', ?, true)", (role,))
            db.execute((MIGRATIONS_DIR / '0018_projection_application_permissions.sql').read_text(encoding='utf-8'))
            db.execute(f'SET LOCAL ROLE {role}')
            assert db.execute('SELECT current_user').fetchone()[0] == role
            for n in (1, 2):
                assert repo.projections.enqueue(project, 'tombstone', str(n), {'record_id': 'r'}) == n
            raise RuntimeError('abort enqueue')
    assert repo.projections.project_watermark(project) == 0
    assert db.execute('SELECT count(*) FROM projection_outbox WHERE project_id=?', (project,)).fetchone()[0] == 0
