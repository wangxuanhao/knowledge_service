"""Discard this application's derived Milvus indexes; preserve PG business data.

Default is a read-only inventory. Apply requires its exact manifest and a
stopped application, so a changed inventory cannot be deleted accidentally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


BASES = ('knowledge_records', 'knowledge_redesign_capacity', 'knowledge_redesign_ui')


def owned_name(name):
    return any(re.fullmatch(re.escape(base) +
        r'(?:__p_[a-f0-9]{16}__g_[A-Za-z0-9_]{1,32})?(?:__control)?', name)
        for base in BASES)


def owned_alias(alias):
    return any(re.fullmatch(re.escape(base) + r'__p_[a-f0-9]{16}', alias)
               for base in BASES)


def manifest_digest(items):
    # Counts may change as Milvus finishes a flush. Identity and aliases must
    # remain exactly the reviewed set; stats are informational only.
    identities = sorted((item['name'], sorted(item['aliases'])) for item in items)
    return hashlib.sha256(json.dumps(identities, separators=(',', ':')).encode()).hexdigest()


def inventory(client):
    items = []
    for name in sorted(client.list_collections(timeout=20)):
        if not owned_name(name):
            continue
        fields = {field['name'] for field in client.describe_collection(
            collection_name=name, timeout=20)['fields']}
        required = ({'project_id', 'marker_id', 'projection_seq', 'fence', 'marker_vector'}
                    if name.endswith('__control') else
                    {'project_id', 'record_id', 'version_id', 'embedding'})
        if not required <= fields:
            raise RuntimeError('An application-named collection has an unexpected schema; leave all data intact')
        aliases = sorted(client.list_aliases(collection_name=name, timeout=20)['aliases'])
        if any(not owned_alias(alias) for alias in aliases):
            raise RuntimeError('A collection has an unowned alias; leave all data intact')
        stats = client.get_collection_stats(collection_name=name, timeout=20)
        items.append({'name': name, 'aliases': aliases, 'row_count': stats.get('row_count')})
    return {'collections': items, 'digest': manifest_digest(items),
            'scope': 'application-owned Milvus indexes and corresponding PG derived state only'}


def require_stopped():
    import psutil
    if any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == 8100
           for connection in psutil.net_connections(kind='tcp')):
        raise RuntimeError('Stop the local application on 8100 before clearing indexes')
    for process in psutil.process_iter(['name', 'cmdline']):
        args = process.info['cmdline'] or []
        if ('-m' in args and args[args.index('-m') + 1:][:1] == ['knowledge_service']
                and Path(process.cwd()).resolve() == ROOT):
            raise RuntimeError('An application worker is still running; leave indexes intact')


def business_counts(db):
    tables = ('projects', 'record_versions', 'assertions', 'assertion_events',
              'record_version_assertions', 'ontologies', 'ontology_drafts',
              'ontology_operations', 'ontology_review_decisions', 'artifacts')
    return {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            for table in tables}


def require_idle(db):
    active = ['pending', 'queued', 'running', 'retry_wait', 'cancelling']
    counts = [
        db.execute("SELECT count(*) FROM artifacts WHERE kind='job' AND payload::jsonb->>'status'=ANY(%s::text[])", (active,)).fetchone()[0],
        db.execute('SELECT count(*) FROM ingest_batches WHERE status=ANY(%s::text[])', (active,)).fetchone()[0],
        db.execute("SELECT count(*) FROM ingest_batch_items WHERE status='running' AND lease_until>clock_timestamp()").fetchone()[0],
        db.execute("SELECT count(*) FROM ingest_runs WHERE status IN ('queued','running')").fetchone()[0],
    ]
    if any(counts):
        raise RuntimeError('Active durable business work prevents clearing indexes')


def apply_reset(client, db, expected):
    require_stopped()
    current = inventory(client)
    reviewed = {item['name']: set(item['aliases']) for item in expected.get('collections', [])}
    if expected['digest'] != manifest_digest(expected.get('collections', [])):
        raise RuntimeError('Reviewed manifest changed; leave all data intact')
    # A retry may observe already removed collections or aliases, including a
    # drop whose acknowledgement was lost. Only the monotonically shrinking
    # subset of the exact reviewed set is authorized; additions/rebindings fail.
    if any(item['name'] not in reviewed or not set(item['aliases']) <= reviewed[item['name']]
           for item in current['collections']):
        raise RuntimeError('Collection inventory changed; create and review a fresh manifest')
    # Milvus has no cross-database transaction. Retain the exact manifest and
    # progress so a failed drop can be resumed; PG is reset only after all drops.
    report = {'reviewed_digest': expected['digest'], 'before': current, 'dropped': [],
              'already_absent': sorted(set(reviewed) - {item['name'] for item in current['collections']}),
              'status': 'running'}
    output = ROOT / '.benchmarks/milvus-index-reset-result.json'
    output.parent.mkdir(exist_ok=True)
    def save():
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    save()
    before = business_counts(db)
    try:
        for item in current['collections']:
            for alias in item['aliases']:
                client.drop_alias(alias=alias, timeout=20)
            client.drop_collection(collection_name=item['name'], timeout=60)
            report['dropped'].append(item['name'])
            save()
        with db.transaction():
            db.execute("LOCK TABLE projection_cursors,projection_rebuild_snapshots,projection_cleanup_outbox,projection_outbox IN EXCLUSIVE MODE")
            cursors = db.execute("DELETE FROM projection_cursors WHERE target='milvus'").rowcount
            cleanups = db.execute("DELETE FROM projection_cleanup_outbox WHERE target='milvus'").rowcount
            # The shared outbox still serves any other projection target.
            # Only remove its redundant dense copy, preserving event identity,
            # sequence, model identity and all non-vector payload fields.
            vectors = db.execute("UPDATE projection_outbox SET payload=payload-'embedding' WHERE payload ? 'embedding'").rowcount
            after = business_counts(db)
            if before != after:
                raise RuntimeError('Business table counts changed during maintenance; PG reset rolled back')
        remaining = inventory(client)
        if remaining['collections']:
            raise RuntimeError('Application collections unexpectedly reappeared; keep the application stopped')
        report.update(status='completed', business_counts_unchanged=True,
            business_counts=after, removed_milvus_cursors=cursors,
            removed_milvus_cleanup_events=cleanups, removed_dense_copies=vectors,
            remaining_application_collections=0)
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        save()
    return report


def main():
    from knowledge_service.core.config import load_environment
    from knowledge_service.repository.connection import resolve_dsn
    from pymilvus import MilvusClient
    import psycopg
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-manifest', type=Path)
    parser.add_argument('--manifest', type=Path, default=ROOT / '.benchmarks/milvus-index-reset-manifest.json')
    args = parser.parse_args()
    client = MilvusClient(uri='http://' + os.getenv('KG_MILVUS_HOST', 'localhost') + ':' +
                          os.getenv('KG_MILVUS_PORT', '19530'))
    try:
        if args.apply_manifest:
            expected = json.loads(args.apply_manifest.read_text(encoding='utf-8'))
            require_stopped()
            with psycopg.connect(resolve_dsn(), connect_timeout=5, autocommit=True) as db:
                # Full audit; never infer idleness from a truncated API list.
                require_idle(db)
                result = apply_reset(client, db, expected)
            print(json.dumps({key: result[key] for key in ('status', 'business_counts_unchanged',
                'removed_milvus_cursors', 'removed_dense_copies', 'remaining_application_collections')}))
        else:
            result = inventory(client)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps({'status': 'dry_run', 'collections': len(result['collections']),
                              'digest': result['digest']}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
