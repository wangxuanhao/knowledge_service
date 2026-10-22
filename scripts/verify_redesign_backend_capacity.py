"""Isolated real PostgreSQL/Milvus read-capacity check; never uses business data.

Synthetic corpus and precomputed real-model embeddings measure read/index scale,
not extraction throughput or semantic relevance. No destructive reset is offered.
"""
from __future__ import annotations

from argparse import ArgumentParser
import json
from math import ceil
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'service'))


def main():
    parser = ArgumentParser()
    parser.add_argument('--database', default='knowledge_redesign_capacity')
    parser.add_argument('--collection', default='knowledge_redesign_capacity')
    parser.add_argument('--original', default='D:/workspace/knowledge_service_v2/knowledge_service')
    parser.add_argument('--entities', type=int, default=150000)
    parser.add_argument('--relations', type=int, default=300000)
    parser.add_argument('--output', default='docs/research/2026-10-19-backend-capacity.json')
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument('--postgres-only', action='store_true',
                       help='Measure only the authoritative complete graph path; does not claim vector acceptance')
    phase.add_argument('--vector-only', action='store_true',
                       help='Prepare and measure the complete vector projection; does not claim complete graph latency')
    parser.add_argument('--reuse-ready-projection', action='store_true',
                        help='Reuse a ready projection only after verifying exact active/model/kind counts')
    args = parser.parse_args()
    if not args.database.startswith('knowledge_redesign_') or not args.collection.startswith('knowledge_redesign_'):
        raise ValueError('Capacity resources must use the knowledge_redesign_ namespace')
    if args.entities < 2 or args.relations < 0:
        raise ValueError('Invalid corpus size')
    os.environ['KG_SKIP_DOTENV'] = '1'
    os.environ['PGCONNECT_TIMEOUT'] = '5'
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    from pg_support import admin_dsn
    import psycopg
    from psycopg import sql
    from knowledge_service.core.config import load_environment
    from knowledge_service.integrations.embeddings import configured_encoder
    from knowledge_service.integrations.milvus_store import MilvusStore
    from knowledge_service.repository import Repository
    from knowledge_service.repository.migrate import apply_migrations
    from knowledge_service.services.graph_sessions import GraphSessions
    from knowledge_service.services.retrieval import RetrievalEngine, PROJECTION_SCHEMA_VERSIONS

    report = {'scope': 'real PostgreSQL/Milvus, synthetic read corpus, precomputed real-model embeddings', 'database': args.database,
              'collection': args.collection, 'required_entities': args.entities, 'required_relations': args.relations}
    report['keyset_plan_reason'] = ('OFFSET 0 keeps endpoint eligibility checks correlated so the outer keyset '
                                   'can stop at LIMIT instead of scanning and sorting the relation corpus.')
    plan_evidence = ROOT / 'docs/research/2026-10-19-scoped-read-query-plan.json'
    if plan_evidence.exists():
        measured = json.loads(plan_evidence.read_text(encoding='utf-8'))
        if (measured['fixture']['entities'], measured['fixture']['relations']) == (args.entities, args.relations):
            report['keyset_plan_reference_measurement'] = measured
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint(phase):
        report['last_completed_phase'] = phase
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(phase, flush=True)

    checkpoint('Preparing isolated capacity resources')
    with psycopg.connect(admin_dsn('postgres'), autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (args.database,)).fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(args.database)))
    dsn = admin_dsn(args.database)
    start = perf_counter()
    print('Applying capacity schema migrations', flush=True)
    apply_migrations(dsn, verbose=False)
    report['migration_seconds'] = perf_counter() - start
    checkpoint('Capacity schema ready')
    repo = Repository(dsn)
    repo._db.execute("SET application_name='knowledge-redesign-capacity-verifier'")
    checkpoint('Capacity repository ready')
    with psycopg.connect(dsn) as conn:
        row=conn.execute('SELECT id FROM projects WHERE name=%s',('容量验证 · 15 万实体',)).fetchone()
    project = row[0] if row else None
    if project is None:
        project = repo.create_project('容量验证 · 15 万实体')['id']
    report['project_id'] = project
    start = perf_counter()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET statement_timeout=0")
        # This explicit fixture bulk load does not claim to benchmark governed
        # ingestion. Normal API writes are tested separately.
        for kind, count, prefix in [('entity', args.entities, 'e'), ('relation', args.relations, 'r')]:
            print('Checking fixture count:', kind, flush=True)
            existing = conn.execute("SELECT count(*) FROM record_versions WHERE project_id=%s AND read_kind=%s AND superseded_at IS NULL",(project,kind)).fetchone()[0]
            print('Fixture count:', kind, existing, flush=True)
            if existing == count:
                continue
            if existing:
                raise ValueError('Existing capacity fixture has a different size; choose a fresh namespace')
            payload = ("jsonb_build_object('id',rid,'kind','entity','text','商户入驻 实体 '||i,'type','CapacityThing',"
                       "'metadata',jsonb_build_object('capacity_bucket',i%%10))" if kind == 'entity' else
                       "jsonb_build_object('id',rid,'kind','relation','text','关联','type','related',"
                       "'subject_id','e'||lpad(((i-1)%%"+str(args.entities)+")::text,6,'0'),"
                       "'object_id','e'||lpad((i%%"+str(args.entities)+")::text,6,'0'),'metadata','{}'::jsonb)")
            conn.execute("INSERT INTO record_versions(project_id,id,version,version_id,payload,recorded_at,"
                "platform_mode,platform_values,business_mode,business_values,audience_mode,audience_values,"
                "scope_identity,valid_from_known,valid_from_epoch_ms,valid_until_known,valid_until_epoch_ms) "
                "SELECT %s,rid,1,%s||':'||rid,("+payload+")::text,clock_timestamp(),"
                "'unknown','{}','unknown','{}','unknown','{}','',false,0,false,0 "
                "FROM (SELECT i,%s||lpad((i-1)::text,6,'0') rid FROM generate_series(1,%s) i) fixture "
                "ON CONFLICT(project_id,id,version) DO NOTHING", (project, project, prefix, count))
        print('Refreshing fixture query statistics', flush=True)
        conn.execute('ANALYZE record_versions')
    report['seed_seconds'] = perf_counter() - start
    checkpoint('PostgreSQL fixture ready')
    report['complete_graph_measured'] = not args.vector_only
    if not args.vector_only:
        sessions = GraphSessions(repo)
        start = perf_counter()
        print('Counting eligible complete graph manifest',flush=True)
        manifest = sessions.create(project, {}, actor_id='capacity-validation')
        report['manifest_seconds'] = perf_counter() - start
        report['manifest_counts'] = {k: manifest[k] for k in ('entity_count','relation_count')}
        assert manifest['entity_count'] == args.entities and manifest['relation_count'] == args.relations
        checkpoint('Manifest ready')
        report['enumeration'] = {}
        for kind, expected in [('entities', args.entities), ('relations', args.relations)]:
            cursor, ids, times = None, set(), []
            while True:
                start = perf_counter()
                page = sessions.page(project, manifest['id'], kind, actor_id='capacity-validation', cursor=cursor, limit=10000)
                times.append(perf_counter()-start)
                print('Graph page:', kind, len(times), 'records', len(page['records']),
                      'seconds', round(times[-1], 3), flush=True)
                for row in page['records']:
                    assert row['id'] not in ids
                    ids.add(row['id'])
                    assert 'embedding' not in row and 'metadata' not in row
                cursor = page['next_cursor']
                if not cursor:
                    break
            assert len(ids) == expected
            report['enumeration'][kind] = {'count': len(ids), 'pages': len(times), 'seconds': sum(times), 'max_page_seconds':max(times)}
            checkpoint(kind + ' complete')

        report['complete_graph_seconds'] = report['manifest_seconds'] + sum(
            item['seconds'] for item in report['enumeration'].values())
        report['complete_graph_within_30_seconds'] = report['complete_graph_seconds'] <= 30
    if args.postgres_only:
        report['vector_acceptance_checked'] = False
        checkpoint('PostgreSQL capacity measured')
        return

    load_environment(Path(args.original), keys={'KG_EMBEDDING_BACKEND','KG_EMBEDDING_PATH','KG_EMBEDDING_IDENTITY',
        'KG_EMBEDDING_DIMENSION','KG_EMBEDDING_MODEL','KG_EMBEDDING_BASE_URL','KG_EMBEDDING_API_KEY',
        'KG_MILVUS_HOST','KG_MILVUS_PORT','KG_EMBEDDING_BATCH_SIZE'})
    if os.environ.get('KG_EMBEDDING_BACKEND', 'local') == 'local':
        model = Path(os.environ.get('KG_EMBEDDING_PATH', 'data/model/embedding/bge-m3'))
        if not model.is_absolute():
            model = Path(args.original) / model
        os.environ['KG_EMBEDDING_PATH'] = str(model)
    start = perf_counter()
    print('Loading configured real embedding model', flush=True)
    encoder = configured_encoder()
    report['encoder_initialization_seconds'] = perf_counter() - start
    assert encoder.semantic, 'Capacity query uses the configured real embedding model'
    start = perf_counter()
    entity_vector, relation_vector, query_vector = encoder.encode(['商户入驻 实体', '关联', '商户入驻'])
    report['encoder_first_batch_seconds'] = perf_counter()-start
    start = perf_counter()
    query_vector = encoder.encode(['商户入驻'])[0]
    report['query_encoding_warm_seconds'] = perf_counter()-start
    report['dimension'] = len(query_vector)
    checkpoint('Real model vectors ready')
    print('Preparing complete Milvus projection', flush=True)
    store = MilvusStore(host=os.environ.get('KG_MILVUS_HOST','127.0.0.1'),port=os.environ.get('KG_MILVUS_PORT','19530'),
                        dim=len(query_vector), collection=args.collection, encoder_identity=encoder.identity).connect()
    store.ensure_collection()
    report['projection_reused'] = args.reuse_ready_projection
    if args.reuse_ready_projection and not repo.projection_ready(project, 'milvus', PROJECTION_SCHEMA_VERSIONS['milvus']):
        raise ValueError('Cannot reuse a missing or stale projection')
    start = perf_counter()
    # Every authoritative fixture kind is projected before marking the target
    # ready. Reusing one real-model vector per synthetic kind measures read and
    # index capacity; it makes no semantic relevance/embedding throughput claim.
    projection_kinds = [('entity', args.entities, 'e', entity_vector),
                        ('relation', args.relations, 'r', relation_vector)]
    for kind, size, prefix, vector in ([] if args.reuse_ready_projection else projection_kinds):
        kind_started = perf_counter()
        for offset in range(0, size, 1000):
            records = []
            for index in range(offset, min(offset + 1000, size)):
                record = {'id': f'{prefix}{index:06d}', 'version_id': f'{project}:{prefix}{index:06d}',
                          'project_id': project, 'kind': kind, 'embedding': vector,
                          'embedding_model': encoder.identity}
                if kind == 'entity':
                    record.update(type='CapacityThing', text=f'商户入驻 实体 {index+1}',
                                  metadata={'capacity_bucket': (index+1) % 10})
                else:
                    record.update(type='related', text='关联', metadata={},
                                  subject_id=f'e{index % args.entities:06d}',
                                  object_id=f'e{(index+1) % args.entities:06d}')
                records.append(record)
            store.upsert(records, flush=False)
            if offset % 10000 == 0:
                print('Milvus submitted', kind, offset + len(records),
                      'seconds', round(perf_counter() - kind_started, 3), flush=True)
        report.setdefault('projection_submissions', {})[kind] = {
            'count': size, 'seconds': perf_counter() - kind_started}
        checkpoint('Milvus ' + kind + ' submitted')
    if not args.reuse_ready_projection:
        print('Flushing the complete Milvus corpus', flush=True)
        store._client.flush(store.collection_name)
    report['index_seconds'] = perf_counter()-start
    count = store._client.query(store.collection_name, filter='project_id == '+json.dumps(project), output_fields=['count(*)'])
    report['milvus_count'] = int(count[0]['count(*)'])
    assert report['milvus_count'] == args.entities + args.relations
    count = store._client.query(store.collection_name,
        filter='project_id == ' + json.dumps(project) + ' and active == true and embedding_model == ' + json.dumps(encoder.identity),
        output_fields=['count(*)'])
    report['milvus_active_model_count'] = int(count[0]['count(*)'])
    assert report['milvus_active_model_count'] == args.entities + args.relations
    report['milvus_kind_counts'] = {}
    for kind, expected in [('entity', args.entities), ('relation', args.relations)]:
        count = store._client.query(store.collection_name,
            filter='project_id == ' + json.dumps(project) + ' and kind == ' + json.dumps(kind),
            output_fields=['count(*)'])
        report['milvus_kind_counts'][kind] = int(count[0]['count(*)'])
        assert report['milvus_kind_counts'][kind] == expected
    report['milvus_indexes'] = []
    for name in store._client.list_indexes(store.collection_name):
        detail = store._client.describe_index(store.collection_name, index_name=name)
        report['milvus_indexes'].append({key: detail[key] for key in (
            'field_name', 'index_type', 'state', 'total_rows', 'indexed_rows', 'pending_index_rows') if key in detail})
    watermark = repo.projections.project_watermark(project)
    if not args.reuse_ready_projection:
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO projection_cursors(project_id,target,schema_version,applied_seq,status) VALUES (%s,'milvus',%s,%s,'ready') "
                         "ON CONFLICT(project_id,target) DO UPDATE SET schema_version=excluded.schema_version,applied_seq=excluded.applied_seq,status='ready'",
                         (project, PROJECTION_SCHEMA_VERSIONS['milvus'], watermark))
    assert repo.projection_ready(project, 'milvus', PROJECTION_SCHEMA_VERSIONS['milvus'])
    checkpoint('Complete Milvus projection verified')
    engine = RetrievalEngine(repo,encoder,store)
    report['searches'] = []
    cases = [('first', 'semantic', {})]
    cases.extend(('warm', mode, {}) for mode in ('semantic', 'hybrid') for _ in range(5))
    cases.append(('advanced', 'semantic',
                  {'filters': {'field': 'metadata.capacity_bucket', 'op': 'eq', 'value': 7}}))
    for temperature, mode, scope in cases:
        channels = ['entity'] if scope else ['entity', 'relation']
        quotas = {kind: 10 for kind in channels}
        start = perf_counter()
        result = engine.search(project,'商户入驻',retrieval_mode=mode,scope=scope,
            content_channels=channels, content_k=quotas, k=10, query_vector_cache={'vector': query_vector})
        elapsed = perf_counter()-start
        assert len(result['hits']) == sum(quotas.values()) and result['active_mode'] == mode and not result.get('degraded')
        assert result.get('_backend') == 'milvus'
        channel_counts = {kind: sum(row['kind'] == kind for row in result['hits']) for kind in channels}
        assert channel_counts == quotas
        if scope:
            assert all(row['metadata']['capacity_bucket'] == 7 for row in result['hits'])
        report['searches'].append({'temperature':temperature,'mode':mode,'advanced_scope':bool(scope),
                                  'hits':len(result['hits']),'channel_counts':channel_counts,
                                  'seconds':elapsed,'excludes_query_encoding': True,'backend':result.get('_backend')})
        print('search', report['searches'][-1], flush=True)
        checkpoint(temperature + ' ' + mode + ' search complete')
    store.close()
    report['vector_acceptance_checked'] = True
    report['native_warm_p95_seconds'] = {}
    for mode in ('semantic', 'hybrid'):
        samples = sorted(item['seconds'] for item in report['searches']
                         if item['temperature'] == 'warm' and item['mode'] == mode)
        report['native_warm_p95_seconds'][mode] = samples[ceil(len(samples) * .95) - 1]
    report['native_warm_retrieval_within_2_seconds'] = all(
        elapsed <= 2 for elapsed in report['native_warm_p95_seconds'].values())
    if args.vector_only:
        report['accepted'] = None
        checkpoint('Milvus capacity measured')
        return
    report['accepted'] = (report['complete_graph_within_30_seconds']
                          and report['native_warm_retrieval_within_2_seconds'])
    checkpoint('Backend capacity measured')
    if not report['accepted']:
        raise ValueError('Capacity measurements exceed the complete-graph or native warm retrieval target')
    checkpoint('Backend capacity verified')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        from knowledge_service.utils.diagnostics import redact
        print(redact(exc),file=sys.stderr)
        print([(Path(frame.filename).name,frame.lineno,frame.name) for frame in traceback.extract_tb(exc.__traceback__)],file=sys.stderr)
        raise SystemExit(1)
