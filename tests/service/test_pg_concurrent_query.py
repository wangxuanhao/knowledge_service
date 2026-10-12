from threading import Thread
from time import monotonic, sleep

import psycopg

from knowledge_service.repository import Repository


def test_task_claim_waits_for_concurrent_autocommit_query(tmp_path):
    repo = Repository(tmp_path / 'concurrent-query')
    repo.save_artifact('job', {'id': 'task', 'status': 'queued'})
    errors = []

    def query():
        try:
            repo._db.execute('SELECT pg_sleep(0.5)').fetchone()
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=query)
    thread.start()
    deadline = monotonic() + 5
    while repo._db.raw.info.transaction_status != psycopg.pq.TransactionStatus.ACTIVE:
        assert monotonic() < deadline
        sleep(.001)
    try:
        claimed = repo.claim_task('job', 'worker')
        assert claimed['status'] == 'running'
        assert repo.update_task('job', claimed, {'status': 'completed'})
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
