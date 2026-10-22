"""Remove only this application's obsolete bf_ scalar fields in Milvus 3.

Read-only by default. --apply requires the normal application to be stopped,
persists a recoverable scalar/schema checkpoint before changes, and never drops
the collection or touches authoritative records. No app or encoder imports.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import runpy
import socket

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import make_conninfo
from pymilvus import DataType, MilvusClient

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'knowledge_vectors'
CAPACITY = '46249548-2189-4ad7-8f23-70ee2bcc7f6f'
SELECTED = frozenset({'eeb3a4dc-7475-4937-9966-74aa261334e6',
                      '828887db-e1f3-4f5d-b2b3-4b3dc97d2040'})


def atomic_save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2,
                                     default=str), encoding='utf-8')
    temporary.replace(path)


def counts(db):
    return dict(db.execute('''SELECT project_id,count(*) FROM record_versions
        WHERE superseded_at IS NULL GROUP BY project_id''').fetchall())


def scoped_count(client, project, version):
    result = client.query(COLLECTION,
        filter='project_id == {project} and index_version == {version}',
        filter_params={'project': project, 'version': version},
        output_fields=['count(*)'], consistency_level='Strong', timeout=30)
    return int(result[0]['count(*)'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--checkpoint', default='.benchmarks/2026-10-21-fixed-field-migration.json')
    args = parser.parse_args()
    if args.apply:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8100)) == 0:
                raise RuntimeError('Stop the normal 8100 application before applying schema cleanup')
    cfg = {**dotenv_values(ROOT / '.env', interpolate=False), **os.environ}
    dsn = cfg.get('KG_DATABASE_URL') or make_conninfo(
        host=cfg.get('KG_DATABASE_HOST', '127.0.0.1'),
        port=cfg.get('KG_DATABASE_PORT', '5432'), dbname=cfg['POSTGRES_DB'],
        user=cfg['POSTGRES_APP_USER'], password=cfg['POSTGRES_APP_PASSWORD'])
    expected = {c.name for c in runpy.run_path(str(ROOT / 'knowledge_service/domain/search_filter_catalog.py'))['SYSTEM_COLUMN_DEFS']}
    checkpoint = ROOT / args.checkpoint
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute("SET statement_timeout='15s'")
        cursors = db.execute("SELECT project_id,status,claim_token,rebuild_state FROM projection_cursors WHERE target='milvus'").fetchall()
        catalog_fields = {f['column_name'] for _, _, _, state in cursors
                          for f in (state.get('catalog') or {}).get('fields', [])}
        client = MilvusClient(uri='http://' + cfg.get('KG_MILVUS_HOST', 'localhost') + ':' + cfg.get('KG_MILVUS_PORT', '19530'))
        try:
            version = client.get_server_version(timeout=15)
            if not version.lstrip('v').startswith('3.') or not callable(getattr(client, 'drop_collection_field', None)):
                raise RuntimeError('Milvus 3 and an SDK exposing drop_collection_field are required')
            info = client.describe_collection(COLLECTION, timeout=20)
            fields = {f['name']: f for f in info['fields']}
            obsolete = sorted(name for name in fields if re.fullmatch(r'bf_[0-9a-f]{20}', name))
            unknown = set(fields) - expected - set(obsolete)
            if unknown or expected - set(fields):
                raise RuntimeError('Unexpected fixed collection schema; no mutations performed')
            if set(obsolete) - catalog_fields and not checkpoint.exists():
                raise RuntimeError('Unowned bf_ columns; refusing unrecorded field deletion')
            if any(f.get('is_primary') or f.get('is_partition_key') for n,f in fields.items() if n in obsolete):
                raise RuntimeError('Refusing identity field deletion')
            scalar_types = {DataType.BOOL, DataType.INT64, DataType.DOUBLE,
                            DataType.VARCHAR, DataType.ARRAY}
            if any(f['type'] not in scalar_types for n,f in fields.items() if n in obsolete):
                raise RuntimeError('Refusing deletion of non-business-scalar fields')
            selected = [(p, int(state['index_version'])) for p,_,_,state in cursors
                        if state.get('index_version')]
            if any(p not in SELECTED for p,_ in selected):
                raise RuntimeError('Unexpected indexed project; this migration only covers the reviewed two-project snapshot')
            assert all(p != CAPACITY for p,_ in selected)
            # An interrupted field drop leaves the collection released. Restore
            # loading before any Strong query on an explicitly stopped apply.
            if args.apply:
                client.load_collection(COLLECTION, timeout=60)
            physical = {p: scoped_count(client,p,v) for p,v in selected}
            total = client.query(COLLECTION, filter='', output_fields=['count(*)'],
                                 consistency_level='Strong', timeout=30)
            if int(total[0]['count(*)']) != sum(physical.values()):
                raise RuntimeError('Unreviewed project or generation rows; refusing incomplete scalar backup')
            indexes = {name: client.describe_index(COLLECTION,index_name=name,timeout=20)
                       for name in client.list_indexes(COLLECTION,timeout=20)}
            print(json.dumps({'server_version':version,'mode':'apply' if args.apply else 'read-only',
                'columns':len(fields),'obsolete_bf_fields':obsolete,
                'physical_counts':physical,'capacity_not_selected':True},ensure_ascii=False),flush=True)
            if not args.apply:
                return
            if any(token for _,_,token,_ in cursors):
                raise RuntimeError('Outstanding projection lease; no schema changes performed')
            if checkpoint.exists():
                state = json.loads(checkpoint.read_text(encoding='utf-8'))
                if state['collection_id'] != info.get('collection_id'):
                    raise RuntimeError('Collection identity changed; refusing checkpoint reuse')
                original_fields = {f['name'] for f in state['schema_before']['fields']
                                   if f['name'].startswith('bf_')}
                if set(obsolete) - original_fields:
                    raise RuntimeError('New fields appeared after checkpoint; refusing deletion')
                if not obsolete and state['status'] == 'complete_requires_explicit_build':
                    print(json.dumps({'already_complete':True,'columns':len(fields)}),flush=True)
                    return
                if state['physical_counts'] != physical:
                    raise RuntimeError('Projection counts changed since checkpoint; review before continuing')
                state['removed_fields'] = sorted(original_fields - set(obsolete))
            else:
                if sum(physical.values()) > 2000:
                    raise RuntimeError('Reviewed scalar backup budget exceeded; no changes performed')
                snapshots = {}
                for p,v in selected:
                    snapshots[p] = client.query(COLLECTION,
                        filter='project_id == {project} and index_version == {version}',
                        filter_params={'project':p,'version':v},
                        output_fields=['vector_id','project_id','index_version','record_id','version_id',*obsolete],
                        limit=2000, consistency_level='Strong',timeout=30)
                    if len(snapshots[p]) != physical[p]:
                        raise RuntimeError('Incomplete scalar backup; refusing schema changes')
                state = {'started_at':datetime.now(timezone.utc).isoformat(),
                         'server_version':version,'collection_id':info.get('collection_id'),
                         'schema_before':info,'indexes_before':indexes,
                         'authority_counts':counts(db),'cursors_before':cursors,
                         'physical_counts':physical,'scalar_backups':snapshots,
                         'removed_fields':[],'status':'checkpoint_saved'}
                atomic_save(checkpoint,state)
            # Commit requires_rebuild before removing any scalar. Startup and
            # reads may not activate or automatically rebuild these generations.
            with db.transaction():
                for p,_ in selected:
                    db.execute("""UPDATE projection_cursors SET status='requires_rebuild',
                        last_error='固定字段迁移：请显式重新向量化本项目',updated_at=clock_timestamp()
                        WHERE project_id=%s AND target='milvus'""",(p,))
            state['status']='requires_rebuild'; atomic_save(checkpoint,state)
            client.release_collection(COLLECTION,timeout=30)
            total_fields = len(state['removed_fields']) + len(obsolete)
            for name in obsolete:
                for index, definition in indexes.items():
                    if definition.get('field_name') == name or index == 'filter_' + name:
                        client.drop_index(COLLECTION,index_name=index,timeout=30)
                client.drop_collection_field(COLLECTION,field_name=name,timeout=30)
                state['removed_fields'].append(name);atomic_save(checkpoint,state)
                print(json.dumps({'removed_field':name,'remaining':total_fields-len(state['removed_fields'])}),flush=True)
            client.load_collection(COLLECTION,timeout=60)
            after = client.describe_collection(COLLECTION,timeout=20)
            assert {f['name'] for f in after['fields']} == expected
            assert all(scoped_count(client,p,v)==physical[p] for p,v in selected)
            assert counts(db)==state['authority_counts']
            state.update(status='complete_requires_explicit_build',schema_after=after,
                         completed_at=datetime.now(timezone.utc).isoformat())
            atomic_save(checkpoint,state)
            print(json.dumps({'final_columns':len(after['fields']),'vectors_preserved':True,
                              'authority_counts_preserved':True,'checkpoint':str(checkpoint)}),flush=True)
        finally:
            client.close()


if __name__ == '__main__':
    main()
