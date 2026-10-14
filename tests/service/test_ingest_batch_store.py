import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from knowledge_service.repository import Repository


def stores(tmp_path):
    from knowledge_service.repository.ingest_batch_store import IngestBatchStore
    from knowledge_service.repository.checkpoint_store import CheckpointStore
    repo = Repository(tmp_path / 'batch.sqlite')
    return repo, IngestBatchStore(repo), CheckpointStore(repo)


def test_batch_limits_idempotency_pagination_and_project_isolation(tmp_path):
    repo, store, _ = stores(tmp_path)
    project = repo.create_project('batch')['id']
    other = repo.create_project('other')['id']
    batch = store.create_batch(project, {'requested_concurrency': 2})
    item = store.add_item(project, batch['id'], 'a.txt', b'abc', 'hash', 'key', 2, 6)
    assert store.add_item(project, batch['id'], 'a.txt', b'abc', 'hash', 'key', 2, 6)['id'] == item['id']
    with pytest.raises(ValueError):
        store.add_item(project, batch['id'], 'a.txt', b'xyz', 'different', 'key', 2, 6)
    store.add_item(project, batch['id'], 'b.txt', b'abc', 'hash', 'key2', 2, 6)
    with pytest.raises(ValueError):
        store.add_item(project, batch['id'], 'c.txt', b'a', 'h', 'key3', 2, 6)
    assert store.list_items(project, batch['id'], 1, 1)['total'] == 2
    assert store.get_batch(project, batch['id'])['counts']['pending'] == 2
    assert store.get_batch(other, batch['id']) is None


def test_pause_cancel_and_expiry_fence_stale_workers(tmp_path):
    repo, store, _ = stores(tmp_path)
    project = repo.create_project('batch')['id']
    batch = store.create_batch(project, {})
    item = store.add_item(project, batch['id'], 'a.txt', b'a', 'h', 'key', 1000, 100)
    store.transition(project, batch['id'], 'start')
    first = store.claim(batch['id'], 'worker1', 60)
    assert first['id'] == item['id']
    assert store.claim(batch['id'], 'worker2', 60) is None
    store.transition(project, batch['id'], 'pause')
    assert not store.finish(item['id'], 'worker1', first['lease_token'], 'succeeded')
    assert store.claim(batch['id'], 'worker2', 60) is None
    store.release_paused(item['id'],'worker1',first['lease_token'])
    store.transition(project, batch['id'], 'resume')
    second = store.claim(batch['id'], 'worker2', 60)
    repo._db.execute("UPDATE ingest_batch_items SET lease_until=clock_timestamp()-interval '1 second' WHERE id=?", (item['id'],))
    assert not store.heartbeat(item['id'], 'worker2', second['lease_token'], 60)
    assert store.recover_expired() == 1
    third = store.claim(batch['id'], 'worker3', 60)
    assert third['lease_token'] != second['lease_token']
    store.transition(project, batch['id'], 'cancel')
    assert not store.finish(item['id'], 'worker3', third['lease_token'], 'succeeded')
    assert store.get_batch(project, batch['id'])['counts']['cancelled'] == 1


def test_checkpoint_success_is_immutable_and_fingerprint_scoped(tmp_path):
    _, _, checkpoints = stores(tmp_path)
    first = checkpoints.put('doc', 0, 'fp', {'facts': [1]})
    assert checkpoints.put('doc', 0, 'fp', {'facts': [1]})['id'] == first['id']
    assert checkpoints.get('doc', 0, 'different') is None
    with pytest.raises(ValueError):
        checkpoints.put('doc', 0, 'fp', {'facts': [2]})


def test_thousand_items_have_database_counts_and_bounded_page(tmp_path):
    repo, store, _ = stores(tmp_path)
    project = repo.create_project('large batch')['id']
    batch = store.create_batch(project, {})
    with repo._transaction():
        for ordinal in range(1000):
            store.add_item(project,batch['id'],f'{ordinal}.txt',b'x','h',str(ordinal),1000,1000)
    page = store.list_items(project,batch['id'],25,975)
    assert page['total'] == 1000
    assert len(page['items']) == 25
    assert all('content' not in item for item in page['items'])
    assert store.get_batch(project,batch['id'])['total_bytes'] == 1000


def test_guard_rolls_back_checkpoint_if_lease_expires_before_commit(tmp_path):
    repo, store, checkpoints = stores(tmp_path)
    project = repo.create_project('guard')['id']
    batch = store.create_batch(project,{})
    store.add_item(project,batch['id'],'a.txt',b'x','h','a',1000,1000)
    store.transition(project,batch['id'],'start')
    item = store.claim(batch['id'],'worker',60)
    with pytest.raises(ValueError,match='expired'):
        with store.commit_guard(item['id'],'worker',item['lease_token']):
            checkpoints.put(item['document_id'],0,'fp',{'facts':[1]})
            repo._db.execute("UPDATE ingest_batch_items SET lease_until=clock_timestamp()-interval '1 second' WHERE id=?",(item['id'],))
    assert checkpoints.get(item['document_id'],0,'fp') is None


def test_retry_retains_attempts_and_resets_only_explicit_restart_checkpoints(tmp_path):
    repo, store, checkpoints = stores(tmp_path)
    project = repo.create_project('retry')['id']
    batch = store.create_batch(project,{})
    store.add_item(project,batch['id'],'a.txt',b'x','h','a',1000,1000)
    store.transition(project,batch['id'],'start')
    item = store.claim(batch['id'],'worker',60)
    checkpoints.put(item['document_id'],0,'fp',{'facts':[1]})
    assert store.finish(item['id'],'worker',item['lease_token'],'failed',error_code='timeout')
    store.retry(project,batch['id'],item['id'])
    assert checkpoints.get(item['document_id'],0,'fp')
    second = store.claim(batch['id'],'worker',60)
    assert second['attempt'] == 2
    assert store.finish(item['id'],'worker',second['lease_token'],'failed')
    store.retry(project,batch['id'],item['id'],mode='restart_document')
    assert checkpoints.get(item['document_id'],0,'fp')
    assert store.list_items(project,batch['id'])['items'][0]['document_generation']==1
    assert repo._db.execute('SELECT COUNT(*) FROM ingest_item_attempts WHERE item_id=?',(item['id'],)).fetchone()[0] == 2


def test_options_and_transactional_batch_concurrency_limit(tmp_path):
    repo, store, _ = stores(tmp_path)
    project = repo.create_project('concurrent')['id']
    batch = store.create_batch(project,{})
    options = {'extraction_mode':'documents','chunk_size':200}
    store.add_item(project,batch['id'],'a.txt',b'x','h','a',1000,1000,options=options)
    store.add_item(project,batch['id'],'b.txt',b'x','h','b',1000,1000)
    assert store.list_items(project,batch['id'])['items'][0]['options'] == options
    store.set_concurrency(batch['id'],1)
    store.transition(project,batch['id'],'start')
    assert store.running_batches()[0]['id'] == batch['id']
    first = store.claim(batch['id'],'one',60)
    assert first
    assert store.claim(batch['id'],'two',60) is None
    store.set_concurrency(batch['id'],2)
    second = store.claim(batch['id'],'two',60)
    assert second
    assert store.finish(first['id'],'one',first['lease_token'],'succeeded')
    assert store.get_batch(project,batch['id'])['status'] == 'running'
    assert store.finish(second['id'],'two',second['lease_token'],'failed')
    assert store.get_batch(project,batch['id'])['status'] == 'completed'
    store.retry(project,batch['id'],second['id'])
    assert store.get_batch(project,batch['id'])['status'] == 'running'


def test_independent_workers_cannot_exceed_batch_cap(tmp_path):
    from knowledge_service.repository.ingest_batch_store import IngestBatchStore
    path = tmp_path / 'workers.sqlite'
    first_repo = Repository(path)
    second_repo = Repository(path)
    first_store,second_store = IngestBatchStore(first_repo),IngestBatchStore(second_repo)
    project = first_repo.create_project('workers')['id']
    batch = first_store.create_batch(project,{})
    for index in range(2):
        first_store.add_item(project,batch['id'],f'{index}.txt',b'x','h',str(index),1000,1000)
    first_store.set_concurrency(batch['id'],1)
    first_store.transition(project,batch['id'],'start')
    barrier = Barrier(2)

    def claim(store,owner):
        barrier.wait(timeout=10)
        return store.claim(batch['id'],owner,60)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim,first_store,'first'),executor.submit(claim,second_store,'second')]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert first_store.get_batch(project,batch['id'])['counts']['running'] == 1


def test_duplicate_manual_retry_is_idempotent(tmp_path):
    repo,store,checkpoints=stores(tmp_path)
    project=repo.create_project('retry-idempotence')['id'];batch=store.create_batch(project,{})
    row=store.add_item(project,batch['id'],'a.txt',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start');item=store.claim(batch['id'],'worker',60)
    store.finish(item['id'],'worker',item['lease_token'],'failed')
    store.retry(project,batch['id'],item['id'],'restart_document')
    store.retry(project,batch['id'],item['id'],'restart_document')
    page=store.list_items(project,batch['id'])
    assert page['items'][0]['document_generation']==1
    assert page['items'][0]['attempt']==1


def test_new_repository_keeps_another_live_batch_run(tmp_path):
    path=tmp_path/'live-run.sqlite'
    repo=Repository(path);store=repo.ingest_batches
    project=repo.create_project('live-run')['id'];batch=store.create_batch(project,{})
    row=store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start');leased=store.claim(batch['id'],'live-worker',60)
    document=repo.put_record(project,{'id':row['document_id'],'kind':'document','text':'text'})
    run=repo.create_ingest_run(project,row['document_id'],document['version_id'])
    run=repo.update_ingest_run(project,run['id'],run['version'],status='running')
    other=Repository(path)
    assert repo.get_ingest_run(project,run['id'])['status']=='running'
    other.close()
