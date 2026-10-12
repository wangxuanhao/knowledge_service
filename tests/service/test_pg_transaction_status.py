from threading import RLock
from types import SimpleNamespace

import psycopg
import pytest

from knowledge_service.repository.pg_engine import SqliteishConnection


@pytest.mark.parametrize('status,expected', [
    (psycopg.pq.TransactionStatus.IDLE, False),
    (psycopg.pq.TransactionStatus.ACTIVE, False),
    (psycopg.pq.TransactionStatus.UNKNOWN, False),
    (psycopg.pq.TransactionStatus.INTRANS, True),
    (psycopg.pq.TransactionStatus.INERROR, True),
])
def test_active_query_is_not_a_nested_transaction(status, expected):
    connection = SqliteishConnection.__new__(SqliteishConnection)
    connection._conn = SimpleNamespace(lock=RLock(),
        info=SimpleNamespace(transaction_status=status))
    assert connection.in_transaction is expected
