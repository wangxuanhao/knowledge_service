"""Read-only deployment checkpoint; compare authority, generations and receipts.

Loads credentials internally. Never starts the application or rebuilds vectors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx
import psycopg
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--compare')
    args = parser.parse_args()
    cfg = {**dotenv_values(ROOT / '.env', interpolate=False), **os.environ}
    dsn = cfg.get('KG_DATABASE_URL') or make_conninfo(
        host=cfg.get('KG_DATABASE_HOST', '127.0.0.1'),
        port=cfg.get('KG_DATABASE_PORT', '5432'), dbname=cfg['POSTGRES_DB'],
        user=cfg['POSTGRES_APP_USER'], password=cfg['POSTGRES_APP_PASSWORD'])
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as db:
        db.execute('SET default_transaction_read_only=on')
        db.execute("SET statement_timeout='30s'")
        report = {
            'schema_version': db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0],
            'authority_counts': db.execute('''SELECT project_id,read_kind,count(*) FROM record_versions
                WHERE superseded_at IS NULL GROUP BY project_id,read_kind ORDER BY project_id,read_kind''').fetchall(),
            'projects': db.execute('SELECT id,name FROM projects ORDER BY id').fetchall(),
            'cursors': [], 'strong_counts': {},
        }
        has_receipts = db.execute("SELECT to_regclass('milvus_projection_receipts') IS NOT NULL").fetchone()[0]
        for project, status, seq, destination, state in db.execute('''SELECT project_id,status,
            applied_seq,active_destination,rebuild_state FROM projection_cursors
            WHERE target='milvus' ORDER BY project_id'''):
            state = json.loads(state) if isinstance(state, str) else dict(state or {})
            generation = state.get('index_version')
            if has_receipts:
                receipts = db.execute('''SELECT record_id,version_id,fingerprint
                    FROM milvus_projection_receipts WHERE project_id=%s AND index_version=%s
                    ORDER BY record_id''', (project, generation)).fetchall() if generation else []
            else:
                receipts = [(key,value['version_id'],value['fingerprint'])
                            for key,value in sorted(state.get('expected_projection',{}).items())]
            receipt_hash = hashlib.sha256(json.dumps(receipts, separators=(',', ':')).encode()).hexdigest()
            report['cursors'].append({'project_id':project,'status':status,'applied_seq':seq,
                'destination':destination,'state':{k:v for k,v in state.items() if k!='expected_projection'},
                'receipt_count':len(receipts),'receipt_sha256':receipt_hash})
    with httpx.Client(trust_env=False, timeout=30) as client:
        describe = client.post('http://127.0.0.1:19530/v2/vectordb/collections/describe',
                               json={'collectionName':'knowledge_vectors'}).json()
        assert describe['code']==0, 'Milvus describe failed'
        report['milvus_schema'] = describe['data']
        for cursor in report['cursors']:
            generation = cursor['state'].get('index_version')
            if not generation:
                continue
            project = cursor['project_id']
            result = client.post('http://127.0.0.1:19530/v2/vectordb/entities/query', json={
                'collectionName':'knowledge_vectors',
                'filter':f'project_id == {json.dumps(project)} and index_version == {int(generation)}',
                'outputFields':['count(*)'],'consistencyLevel':'Strong'}).json()
            assert result['code']==0, 'Milvus strong count failed'
            report['strong_counts'][project] = result['data'][0]['count(*)']
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),encoding='utf-8')
    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding='utf-8'))
        after = json.loads(Path(args.output).read_text(encoding='utf-8'))
        for value in (before,after):
            value['milvus_schema']['indexes'] = sorted(value['milvus_schema'].get('indexes',[]),
                                                       key=lambda row:row['indexName'])
        for key in ('authority_counts','projects','cursors','strong_counts','milvus_schema'):
            assert before[key]==after[key], f'Deployment checkpoint mismatch: {key}'
    print(json.dumps({'schema_version':report['schema_version'],
                      'projects':len(report['projects']), 'strong_counts':report['strong_counts'],
                      'receipt_counts':[c['receipt_count'] for c in report['cursors']],
                      'compared':bool(args.compare)},ensure_ascii=False))


if __name__=='__main__':
    main()
