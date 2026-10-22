"""A borrowed adapter must never inherit callables bound to its writer."""
from knowledge_service.repository.core import Repository


def test_reader_does_not_copy_writer_bound_instance_overrides(tmp_path, monkeypatch):
    repository = Repository(tmp_path / 'adapter-overrides')
    original = repository._db.execute
    writer_statements = []

    def writer_execute(statement, *args, **kwargs):
        writer_statements.append(statement)
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(repository._db, 'execute', writer_execute)
    try:
        with repository.independent_read_view() as reader:
            assert reader._db.raw is not repository._db.raw
            assert reader._db.execute('SELECT 1').fetchone()[0] == 1
            assert not repository._db.in_transaction
        assert not writer_statements
        repository._db.execute('CREATE TEMP TABLE adapter_writer_check (value int)')
        repository._db.execute('INSERT INTO adapter_writer_check VALUES (1)')
    finally:
        repository.close()
