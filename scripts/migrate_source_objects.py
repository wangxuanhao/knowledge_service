"""Inventory/verify source objects and queue originals; writes require --apply.

The destination is the explicitly configured S3 backend. No bucket creation,
local deletion, schema migration, credential changes or config switching occurs.
"""
import argparse
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge_service.core.config import load_environment
from knowledge_service.integrations.source_object_store import SourceObjectStore, S3SourceObjectStore, source_object_store_from_environment
from knowledge_service.repository import Repository
from knowledge_service.repository.connection import resolve_dsn
from knowledge_service.repository.source_store import SourceStore
from knowledge_service.utils.diagnostics import redact


MAX_OBJECT_BYTES = 1_000_000_000


def _metadata_valid(obj):
    digest = obj['sha256']
    if (not re.fullmatch(r'[0-9a-f]{64}', digest)
            or obj['storage_key'] != digest[:2] + '/' + digest
            or not 0 <= int(obj['size_bytes']) <= MAX_OBJECT_BYTES):
        raise ValueError('Invalid source object metadata')


def _verified_stream(store, obj):
    """Recompute the entire SHA256 and size, with bounded memory/disk usage."""
    _metadata_valid(obj)
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    hasher, size = hashlib.sha256(), 0
    try:
        with store.open(obj['storage_key']) as source:
            while True:
                part = source.read(min(1024 * 1024, MAX_OBJECT_BYTES + 1 - size))
                if not part:
                    break
                size += len(part)
                if size > MAX_OBJECT_BYTES:
                    raise ValueError('Source object exceeds migration size limit')
                hasher.update(part)
                spool.write(part)
        if size != obj['size_bytes'] or hasher.hexdigest() != obj['sha256']:
            raise ValueError('Source object size or checksum mismatch')
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _candidate_stream(raw, obj):
    if len(raw) == obj['size_bytes'] and hashlib.sha256(raw).hexdigest() == obj['sha256']:
        return io.BytesIO(raw)
    return None


def _source_stream(repo, source, destination, obj, result):
    try:
        return _verified_stream(source, obj)
    except (FileNotFoundError, ValueError) as exc:
        result['source_issue'] = 'missing' if isinstance(exc, FileNotFoundError) else 'corrupt'
    # Only exact bytes are eligible for recovery. ZIP member text does not hash
    # to the archive and therefore cannot be substituted for an original ZIP.
    versions = repo._db.execute('''SELECT format,source_text FROM source_document_versions
        WHERE source_object_id=? AND format='markdown' ''', (obj['id'],)).fetchall()
    for version in versions:
        for encoding in ('utf-8', 'utf-8-sig'):
            stream = _candidate_stream(version['source_text'].encode(encoding), obj)
            if stream is not None:
                result['recovered_from'] = 'frozen_markdown'
                return stream
    # Read one historical original at a time rather than materializing all BYTEA.
    ids = repo._db.execute('SELECT id FROM ingest_batch_items WHERE file_sha256=? AND content IS NOT NULL', (obj['sha256'],)).fetchall()
    for item in ids:
        row = repo._db.execute('SELECT content FROM ingest_batch_items WHERE id=?', (item['id'],)).fetchone()
        if row is not None and row['content'] is not None:
            stream = _candidate_stream(bytes(row['content']), obj)
            if stream is not None:
                result['recovered_from'] = 'queue_original'
                return stream
    # Reruns may find the verified durable destination after an earlier transfer.
    try:
        stream = _verified_stream(destination, obj)
        result['recovered_from'] = 'verified_destination'
        return stream
    except (FileNotFoundError, ValueError):
        raise FileNotFoundError('No checksum-matching original is recoverable') from None


def _ensure_destination(destination, stream, obj, apply):
    if destination.exists(obj['storage_key']):
        with _verified_stream(destination, obj):
            pass
        return 'verified'
    if not apply:
        return 'would_upload'
    stream.seek(0)
    uploaded = destination.put(stream, obj['mime_type'], expected_sha256=obj['sha256'])
    if uploaded['storage_key'] != obj['storage_key']:
        raise ValueError('Destination changed the source storage key')
    with _verified_stream(destination, obj):
        pass
    return 'uploaded'


def _error(result, exc):
    # Vendor/connection exception strings can contain credentials or source text.
    # Keep a useful error category, never the provider response body or DSN.
    result.update(status='error', error=redact(type(exc).__name__))


def migrate_source_objects(repo, source, destination, *, apply=False):
    """Dependency-injected, resumable migration; dry-run performs no data writes.

    Metadata publication and per-row clearing share the existing DB GC lock.
    A failed transfer leaves its legacy BYTEA untouched and is reported; other
    rows remain independently resumable. The object key namespace is preserved.
    """
    store = SourceStore(repo)
    report = dict(mode='apply' if apply else 'dry-run', objects=[], queue=[])
    with repo._lock:
        ids = [row['id'] for row in repo._db.execute('SELECT id FROM source_objects ORDER BY id').fetchall()]
        queue_ids = [row['id'] for row in repo._db.execute('SELECT id FROM ingest_batch_items ORDER BY id').fetchall()]
    for object_id in ids:
        result = dict(id=object_id)
        try:
            with store.publication_guard():
                row = repo._db.execute('SELECT * FROM source_objects WHERE id=? FOR UPDATE', (object_id,)).fetchone()
                if row is None:
                    result['status'] = 'removed_during_inventory'
                else:
                    obj = dict(row)
                    _metadata_valid(obj)
                    result.update(sha256=obj['sha256'], size_bytes=obj['size_bytes'], storage_key=obj['storage_key'])
                    with _source_stream(repo, source, destination, obj, result) as stream:
                        result['destination'] = _ensure_destination(destination, stream, obj, apply)
                    result['status'] = 'verified' if apply else 'ready'
        except Exception as exc:
            _error(result, exc)
        report['objects'].append(result)
    for item_id in queue_ids:
        result = dict(id=item_id)
        try:
            with store.publication_guard():
                row = repo._db.execute('SELECT * FROM ingest_batch_items WHERE id=? FOR UPDATE', (item_id,)).fetchone()
                if row is None:
                    result['status'] = 'removed_during_inventory'
                elif row['content'] is None:
                    obj = repo._db.execute('SELECT * FROM source_objects WHERE id=?', (row['upload_object_id'],)).fetchone()
                    if obj is None:
                        raise ValueError('Queue reference metadata is missing')
                    # Referenced uploads are part of the registered-object inventory.
                    with _source_stream(repo, source, destination, dict(obj), result) as stream:
                        result['destination'] = _ensure_destination(destination, stream, dict(obj), apply)
                    result.update(status='already_referenced', upload_object_id=obj['id'])
                else:
                    raw, digest = bytes(row['content']), row['file_sha256']
                    if hashlib.sha256(raw).hexdigest() == digest:
                        obj = dict(id=digest, sha256=digest, size_bytes=len(raw),
                            storage_key=digest[:2] + '/' + digest,
                            mime_type=mimetypes.guess_type(row['original_name'])[0] or 'application/octet-stream')
                        stream = io.BytesIO(raw)
                    else:
                        # Historical ZIP items contain Markdown member BYTEA but
                        # file_sha256 names the raw package. Follow the source
                        # version only when its original object has that hash.
                        # A pending document restart moves the previous version
                        # to source_restart_of until a new version is parsed.
                        obj = repo._db.execute('''SELECT o.* FROM source_objects o
                            JOIN source_document_versions v ON v.source_object_id=o.id
                            WHERE v.id=COALESCE(?,?) AND v.project_id=? AND o.sha256=?''',
                            (row['source_version_id'], row['source_restart_of'], row['project_id'], digest)).fetchone()
                        if obj is None:
                            raise ValueError('Queue bytes do not match original checksum')
                        obj = dict(obj)
                        stream = _source_stream(repo, source, destination, obj, result)
                        result['content_kind'] = 'package_member'
                    with stream:
                        _metadata_valid(obj)
                        result['destination'] = _ensure_destination(destination, stream, obj, apply)
                    result.update(sha256=obj['sha256'], size_bytes=obj['size_bytes'])
                    if apply:
                        # Registration and clearing happen only after a full read
                        # from the destination has validated both size and SHA256.
                        metadata = {key: obj[key] for key in ('id','sha256','size_bytes','storage_key','mime_type')}
                        registered = store.register_object(metadata)
                        repo._db.execute('UPDATE ingest_batch_items SET upload_object_id=?,content=NULL WHERE id=?', (registered['id'],item_id))
                        result.update(status='migrated', upload_object_id=registered['id'])
                    else:
                        result['status'] = 'ready'
        except Exception as exc:
            _error(result, exc)
        report['queue'].append(result)
    report['error_count'] = sum(row['status'] == 'error' for row in report['objects'] + report['queue'])
    report['registered_objects'] = len(report['objects'])
    report['queue_items'] = len(report['queue'])
    report['legacy_items'] = sum(row['status'] in ('ready','migrated','error') for row in report['queue'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path('data/sources'))
    parser.add_argument('--apply', action='store_true', help='Copy verified objects and replace legacy queue BYTEA with references')
    parser.add_argument('--output', type=Path, help='Optional JSON report (contains hashes/IDs, never credentials or content)')
    args = parser.parse_args(argv)
    load_environment()
    repo = None
    try:
        destination = source_object_store_from_environment()
        if not isinstance(destination, S3SourceObjectStore):
            raise ValueError('Migration requires explicit KG_SOURCE_OBJECT_BACKEND=s3')
        # resolve_dsn uses application credentials; this command performs DML only.
        repo = Repository(resolve_dsn(), bootstrap=False)
        report = migrate_source_objects(repo, SourceObjectStore(args.source_root), destination, apply=args.apply)
        encoded = json.dumps(report, ensure_ascii=True, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + '\n', encoding='utf-8')
        print(encoded)
        return 2 if report['error_count'] else 0
    except Exception as exc:
        print(json.dumps({'error': redact(type(exc).__name__),
            'message': 'Migration failed; verify configuration, schema version and connectivity.'}))
        return 2
    finally:
        if repo is not None:
            repo.close()


if __name__ == '__main__':
    raise SystemExit(main())
