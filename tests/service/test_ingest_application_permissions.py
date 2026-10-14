import pytest
from knowledge_service.repository import Repository


def test_migration_grants_application_data_access_without_ddl(tmp_path):
    repo = Repository(tmp_path / 'app-permissions')
    role = 'knowledge_app'
    if not repo._db.execute('SELECT 1 FROM pg_roles WHERE rolname=?', (role,)).fetchone():
        pytest.skip('Application role is not installed in this PostgreSQL instance')
    tables = ('ingest_batches', 'ingest_batch_items', 'ingest_item_attempts',
              'ingest_chunk_checkpoints', 'source_objects', 'source_document_versions',
              'source_assets', 'parsed_blocks', 'source_asset_ocr_runs',
              'source_asset_ocr_blocks', 'chunk_source_spans')
    for table in tables:
        for permission in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
            allowed = repo._db.execute('SELECT has_table_privilege(?,?,?)',
                                       (role, table, permission)).fetchone()[0]
            assert allowed, (table, permission)
    assert not repo._db.execute('SELECT rolsuper FROM pg_roles WHERE rolname=?', (role,)).fetchone()[0]
