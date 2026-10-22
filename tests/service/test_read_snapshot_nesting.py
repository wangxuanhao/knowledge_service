"""Borrowed adapters keep nested helper reads in their owning snapshot."""
import pytest

from knowledge_service.repository import Repository


def test_nested_driver_snapshot_reuses_borrowed_connection_and_budget(tmp_path):
    repo = Repository(tmp_path / 'nested-driver-snapshot')
    repo.create_project('first')
    with repo._db.independent_read_snapshot() as outer:
        assert outer.execute('SELECT count(*) FROM projects').fetchone()[0] == 1
        repo.create_project('committed later')
        with outer.independent_read_snapshot() as inner:
            assert inner is outer
            assert inner.execute('SELECT count(*) FROM projects').fetchone()[0] == 1
        assert outer.in_transaction
        assert outer._read_pool is None
        with pytest.raises(ValueError, match='nested failure'):
            with outer.independent_read_snapshot() as inner:
                raise ValueError('nested failure')
        assert outer.in_transaction
        assert outer.execute('SELECT count(*) FROM projects').fetchone()[0] == 1
    pool = repo._db._read_pool
    assert pool.get_stats()['pool_available'] == pool.get_stats()['pool_size']
    with repo._db.independent_read_snapshot() as current:
        assert current.execute('SELECT count(*) FROM projects').fetchone()[0] == 2
