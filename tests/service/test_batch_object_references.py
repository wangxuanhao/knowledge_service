import hashlib
import io
import zipfile
from types import SimpleNamespace

import pytest

from knowledge_service.repository import Repository
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.services.source_documents import SourceDocumentService
from knowledge_service.services.ingest_batches import IngestBatches, capabilities, public_item
from knowledge_service.services.document_ingest_runner import DocumentIngestRunner


def setup(tmp_path):
    repo = Repository(tmp_path / 'objects.sqlite')
    project = repo.create_project('object refs')['id']
    sources = SourceDocumentService(repo, SourceObjectStore(tmp_path / 'sources'))
    service = SimpleNamespace(repository=repo, data=repo)
    batches = IngestBatches(service, repo.ingest_batches, capabilities(), sources)
    batch = repo.ingest_batches.create_batch(project, {})
    return repo, project, sources, batches, batch['id']


def upload(name, data):
    return SimpleNamespace(filename=name, file=io.BytesIO(data))


def test_upload_keeps_only_durable_object_reference_and_gc_protects_queue(tmp_path):
    repo, project, sources, batches, bid = setup(tmp_path)
    raw = b'# original\r\n'
    item = batches.upload(project, bid, upload('a.md', raw), 'key')
    row = dict(repo._db.execute('SELECT * FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone())
    assert row['content'] is None, 'new queue uploads must not retain BYTEA'
    assert row['upload_object_id'] == hashlib.sha256(raw).hexdigest()
    assert 'upload_object_id' not in item
    assert sources.collect_unreferenced_objects() == []
    assert batches.upload(project, bid, upload('a.md', raw), 'key')['id'] == item['id']
    with pytest.raises(ValueError, match='idempotency'):
        batches.upload(project, bid, upload('a.md', b'changed'), 'key')
    repo.ingest_batches.transition(project, bid, 'start')
    claimed = repo.ingest_batches.claim(bid, 'worker', 60)
    assert claimed['content'] is None
    runner = DocumentIngestRunner(None, repo.ingest_batches, None, sources)
    source = runner._parse_source(claimed)
    assert sources.source(project, source['id'])['data'] == raw


def test_zip_reference_is_raw_archive_and_restart_retains_package(tmp_path):
    repo, project, sources, batches, bid = setup(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('a.md', '# First\n')
        archive.writestr('b.md', '# Second\n')
    raw = buffer.getvalue()
    result = batches.upload(project, bid, upload('book.zip', raw), 'package')
    rows = [dict(r) for r in repo._db.execute('SELECT * FROM ingest_batch_items WHERE batch_id=?', (bid,))]
    assert len(rows) == 2
    assert all(r['content'] is None for r in rows), 'ZIP members must share raw original object'
    assert {r['upload_object_id'] for r in rows} == {hashlib.sha256(raw).hexdigest()}
    assert sum(r['size_bytes'] for r in rows) == len(raw)
    assert batches.upload(project, bid, upload('book.zip', raw), 'package')['total'] == 2
    row = rows[0]
    row.update(content=None, source_version_id=None, source_restart_of=None,
               options={'metadata': {'package_entry_path': row['original_name']}})
    source = DocumentIngestRunner(None, repo.ingest_batches, None, sources)._parse_source(row)
    assert sources.source(project, source['id'])['package_data'] == raw


def test_legacy_add_item_and_null_invariant(tmp_path):
    repo, project, sources, batches, bid = setup(tmp_path)
    row = repo.ingest_batches.add_item(project, bid, 'old.txt', b'legacy', 'old-hash', 'legacy', 10, 100)
    assert row['content'] == b'legacy'
    assert repo.ingest_batches.add_item(project, bid, 'old.txt', b'legacy', 'old-hash', 'legacy', 10, 100)['id'] == row['id']
    with pytest.raises(Exception):
        with repo.unit_of_work():
            repo._db.execute('UPDATE ingest_batch_items SET content=NULL WHERE id=?', (row['id'],))


def test_public_item_hides_object_locators():
    row = public_item(dict(status='pending', content=b'x', upload_object_id='secret', storage_key='secret', upload_storage_key='secret'))
    assert set(row) == {'status'}
