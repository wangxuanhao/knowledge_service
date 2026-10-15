"""Real-store capacity acceptance for native applicability filtering.

The default mode is a dry run. A passing report can only be produced by
``--execute`` after connecting to an explicitly configured PostgreSQL,
Milvus, and Neo4j environment. The script never substitutes fakes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from typing import Any
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


EXIT_SKIPPED = 3
REPORT_SCHEMA_VERSION = 1
MIN_DOCUMENTS = 5_000
MIN_PLATFORMS = 10
MIN_RECORDS = 100_000
THRESHOLDS_MS = {
    'scope_compile_p95': 5.0,
    'single_platform_hybrid_p95': 800.0,
    'ten_platform_grouped_p95': 3000.0,
    'two_hop_p95': 1500.0,
}
REQUIRED_ENVIRONMENT = (
    'KG_CAPACITY_POSTGRES_DSN',
    'KG_CAPACITY_MILVUS_URI',
    'KG_CAPACITY_MILVUS_COLLECTION',
    'KG_CAPACITY_NEO4J_URI',
    'KG_CAPACITY_NEO4J_USERNAME',
    'KG_CAPACITY_NEO4J_PASSWORD',
)
CHECK_NAMES = (
    'real_services_connected',
    'dataset_scale',
    'projection_watermarks_ready',
    'scope_compile_p95',
    'postgres_explain_no_seq_scan',
    'milvus_request_has_no_candidate_ids',
    'single_platform_hybrid_p95',
    'ten_platform_grouped_p95',
    'two_hop_p95',
    'scope_leakage_zero',
)

NEO4J_TWO_HOP_QUERY = '''
MATCH (pointer:KSProjectionPointer {namespace:$namespace,project_id:$project_id})
      -[:KS_ACTIVE]->(g:KSProjection)
MATCH (g)-[:KS_CONTAINS]->(seed:KSRecordV3)
WHERE seed.namespace=$namespace AND seed.project_id=$project_id
  AND seed.generation=g.generation AND seed.active=true
  AND (seed.platform_mode='all' OR
       (seed.platform_mode='specific' AND $platform IN seed.platform_values))
  AND (seed.business_mode='all' OR
       (seed.business_mode='specific' AND $business IN seed.business_values))
  AND (seed.audience_mode='all' OR
       (seed.audience_mode='specific' AND $audience IN seed.audience_values))
  AND (($valid_epoch_ms IS NULL) OR
       ((seed.valid_from_known OR $include_unknown)
        AND (NOT seed.valid_from_known OR seed.valid_from_epoch_ms <= $valid_epoch_ms)
        AND (NOT seed.valid_until_known OR seed.valid_until_epoch_ms > $valid_epoch_ms)))
WITH g,seed LIMIT 25
MATCH (seed)-[edge:KS_FACT]->(middle:KSRecordV3)
             -[edge2:KS_FACT]->(node:KSRecordV3)
WHERE edge.namespace=$namespace AND edge.project_id=$project_id
  AND edge.generation=g.generation AND edge.active=true
  AND middle.namespace=$namespace AND middle.project_id=$project_id
  AND middle.generation=g.generation AND middle.active=true
  AND edge2.namespace=$namespace AND edge2.project_id=$project_id
  AND edge2.generation=g.generation AND edge2.active=true
  AND node.namespace=$namespace AND node.project_id=$project_id
  AND node.generation=g.generation AND node.active=true
  AND all(item IN [edge,middle,edge2,node] WHERE
       (item.platform_mode='all' OR
        (item.platform_mode='specific' AND $platform IN item.platform_values))
       AND (item.business_mode='all' OR
        (item.business_mode='specific' AND $business IN item.business_values))
       AND (item.audience_mode='all' OR
        (item.audience_mode='specific' AND $audience IN item.audience_values))
       AND (($valid_epoch_ms IS NULL) OR
        ((item.valid_from_known OR $include_unknown)
         AND (NOT item.valid_from_known OR item.valid_from_epoch_ms <= $valid_epoch_ms)
         AND (NOT item.valid_until_known OR item.valid_until_epoch_ms > $valid_epoch_ms))))
RETURN g.generation AS generation,g.projection_seq AS projection_seq,
       g.schema_version AS schema_version,
       [{version_id:seed.version_id,mode:seed.platform_mode,
         values:seed.platform_values},
        {version_id:middle.version_id,mode:middle.platform_mode,
         values:middle.platform_values},
        {version_id:node.version_id,mode:node.platform_mode,
         values:node.platform_values}] AS nodes,
       [{version_id:edge.version_id,mode:edge.platform_mode,
         values:edge.platform_values},
        {version_id:edge2.version_id,mode:edge2.platform_mode,
         values:edge2.platform_values}] AS edges
LIMIT 100
'''


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _minimum(name: str, minimum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f'{name} must be an integer') from exc
        if parsed < minimum:
            raise argparse.ArgumentTypeError(
                f'{name} must be at least {minimum} for capacity acceptance')
        return parsed
    return parse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Verify native applicability capacity against real stores.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true',
                      help='write a skipped JSON plan without connecting')
    mode.add_argument('--execute', action='store_true',
                      help='connect to all three real stores and run acceptance')
    parser.add_argument('--report', type=Path,
                        default=Path('artifacts/native-applicability-capacity.json'))
    parser.add_argument('--project-id')
    parser.add_argument('--documents', type=_minimum('documents', MIN_DOCUMENTS),
                        default=MIN_DOCUMENTS)
    parser.add_argument('--platforms', type=_minimum('platforms', MIN_PLATFORMS),
                        default=MIN_PLATFORMS)
    parser.add_argument('--records', type=_minimum('records', MIN_RECORDS),
                        default=MIN_RECORDS)
    parser.add_argument('--query', default='平台规则')
    parser.add_argument('--samples', type=_minimum('samples', 1), default=5)
    parser.add_argument('--postgres-dsn')
    parser.add_argument('--milvus-uri')
    parser.add_argument('--milvus-collection')
    parser.add_argument('--milvus-token')
    parser.add_argument('--neo4j-uri')
    parser.add_argument('--neo4j-username')
    parser.add_argument('--neo4j-password')
    parser.add_argument('--expected-two-hop-count', type=int)
    args = parser.parse_args(argv)
    if not args.execute:
        args.dry_run = True
    return args


def _plan() -> list[str]:
    return [
        'Connect to explicitly configured real PostgreSQL, Milvus, and Neo4j.',
        'Verify at least 5000 source documents, 10 platforms, and 100000 current records.',
        'Verify Milvus and Neo4j projection cursors equal the authoritative watermark.',
        'Measure scope compilation p95 and inspect PostgreSQL EXPLAIN for Seq Scan.',
        'Run real single-platform hybrid and ten-platform grouped Milvus requests.',
        'Run a real scope-constrained Neo4j two-hop expansion.',
        'Hydrate returned version ids from PostgreSQL and require zero scope leakage.',
    ]


def new_report(args) -> dict[str, Any]:
    return {
        'schema_version': REPORT_SCHEMA_VERSION,
        'kind': 'native_applicability_capacity',
        'status': 'skipped',
        'executed': False,
        'previous_invalidated': False,
        'started_at': _utc_now(),
        'finished_at': None,
        'dataset': {
            'targets': {
                'documents': args.documents,
                'platforms': args.platforms,
                'records': args.records,
            },
            'observed': None,
        },
        'thresholds_ms': dict(THRESHOLDS_MS),
        'metrics_ms': {},
        'leakage_count': None,
        'checks': [
            {'name': name, 'status': 'skipped', 'detail': 'not executed'}
            for name in CHECK_NAMES
        ],
        'missing_requirements': [],
        'plan': _plan(),
        'limitations': [
            'A dry-run report is an execution plan, not capacity evidence.',
            'No fake client or in-memory backend can produce status=passed.',
        ],
    }


def validate_report(report: dict[str, Any]) -> None:
    required = {
        'schema_version', 'kind', 'status', 'executed', 'dataset',
        'thresholds_ms', 'metrics_ms', 'leakage_count', 'checks', 'plan',
    }
    missing = required.difference(report)
    if missing:
        raise ValueError(f'report fields missing: {sorted(missing)}')
    if report['schema_version'] != REPORT_SCHEMA_VERSION:
        raise ValueError('unsupported report schema_version')
    if report['kind'] != 'native_applicability_capacity':
        raise ValueError('unexpected report kind')
    if report['status'] not in {'skipped', 'running', 'passed', 'failed'}:
        raise ValueError('invalid report status')
    if report['status'] == 'passed' and not report['executed']:
        raise ValueError('passed report must be executed')
    if report['status'] == 'passed':
        if report.get('previous_invalidated') is not True:
            raise ValueError('passed report must invalidate previous evidence')
        if report['leakage_count'] != 0:
            raise ValueError('passed report requires zero leakage')
        if any(check.get('status') != 'passed' for check in report['checks']):
            raise ValueError('passed report requires every check to pass')
        recovery = report.get('rebuild_recovery') or {}
        if not (recovery.get('interruption_observed') is True
                and recovery.get('resumed') is True
                and recovery.get('ready') is True):
            raise ValueError('passed report requires interruption/resume evidence')
        counts = report.get('milvus_hit_counts_by_platform') or {}
        _require_platform_hits(counts, report.get('selected_platforms') or [])
        if not (report.get('projection_alignment') or {}).get('verified'):
            raise ValueError('passed report requires projection alignment evidence')


def _write_report(path: Path, report: dict[str, Any]) -> None:
    report['finished_at'] = None if report.get('status') == 'running' else _utc_now()
    validate_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=path.parent,
                prefix=f'.{path.name}.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_platform_hits(counts, expected_platforms):
    missing = [platform for platform in expected_platforms if platform not in counts]
    if missing:
        raise ValueError(f'missing platform groups: {missing}')
    empty = [platform for platform in expected_platforms
             if int(counts.get(platform) or 0) <= 0]
    if empty:
        raise ValueError(f'nonempty Milvus hits required for: {empty}')
    return counts


def _require_projection_alignment(*, target, watermark, cursor, destination,
                                  control_seq):
    if cursor.get('status') != 'ready':
        raise ValueError(f'{target} cursor is not ready')
    if int(cursor.get('applied_seq', -1)) != int(watermark):
        raise ValueError(f'{target} cursor watermark mismatch')
    if cursor.get('active_destination') != destination:
        raise ValueError(f'{target} active destination mismatch')
    if int(control_seq) != int(watermark):
        raise ValueError(f'{target} control watermark mismatch')
    return True


def _require_two_hop_count(actual, expected):
    actual = int(actual)
    if expected is None and actual <= 0:
        raise ValueError('nonempty two-hop result required')
    if expected is not None and actual != int(expected):
        raise ValueError(
            f'two-hop fixture count mismatch: expected {expected}, got {actual}')
    return actual


def _settings(args) -> tuple[dict[str, str], list[str]]:
    values = {
        'KG_CAPACITY_POSTGRES_DSN': args.postgres_dsn or os.getenv(
            'KG_CAPACITY_POSTGRES_DSN', ''),
        'KG_CAPACITY_MILVUS_URI': args.milvus_uri or os.getenv(
            'KG_CAPACITY_MILVUS_URI', ''),
        'KG_CAPACITY_MILVUS_COLLECTION': args.milvus_collection or os.getenv(
            'KG_CAPACITY_MILVUS_COLLECTION', ''),
        'KG_CAPACITY_NEO4J_URI': args.neo4j_uri or os.getenv(
            'KG_CAPACITY_NEO4J_URI', ''),
        'KG_CAPACITY_NEO4J_USERNAME': args.neo4j_username or os.getenv(
            'KG_CAPACITY_NEO4J_USERNAME', ''),
        'KG_CAPACITY_NEO4J_PASSWORD': args.neo4j_password or os.getenv(
            'KG_CAPACITY_NEO4J_PASSWORD', ''),
        'KG_CAPACITY_MILVUS_TOKEN': args.milvus_token or os.getenv(
            'KG_CAPACITY_MILVUS_TOKEN', ''),
        'KG_CAPACITY_PROJECT_ID': args.project_id or os.getenv(
            'KG_CAPACITY_PROJECT_ID', ''),
    }
    missing = [name for name in REQUIRED_ENVIRONMENT if not values[name]]
    if not values['KG_CAPACITY_PROJECT_ID']:
        missing.append('KG_CAPACITY_PROJECT_ID/--project-id')
    return values, missing


def percentile(values: list[float], percentile_value: float = 0.95) -> float:
    if not values:
        raise ValueError('cannot calculate percentile of an empty sample')
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def _set_check(report, name, passed, detail):
    for check in report['checks']:
        if check['name'] == name:
            check.update(status='passed' if passed else 'failed', detail=str(detail))
            return
    raise KeyError(name)


def _walk_plan(value):
    if isinstance(value, list):
        for item in value:
            yield from _walk_plan(item)
    elif isinstance(value, dict):
        yield value
        for child in value.get('Plans', ()):
            yield from _walk_plan(child)


def _hit_version_ids(result) -> list[str]:
    ids = []
    for batch in result or ():
        for hit in batch or ():
            if isinstance(hit, dict):
                value = hit.get('version_id', hit.get('id'))
            else:
                value = getattr(hit, 'id', None)
            if value is not None:
                ids.append(str(value))
    return ids


def _milvus_dimension(client, collection: str) -> int:
    info = client.describe_collection(collection_name=collection) or {}
    for field in info.get('fields', ()):
        if field.get('name') == 'embedding':
            raw = (field.get('params') or {}).get('dim')
            if raw is not None:
                return int(raw)
    raise RuntimeError('Milvus collection has no readable embedding dimension')


def _milvus_hybrid(client, collection, query, vector, expression, limit=20):
    from pymilvus import AnnSearchRequest, RRFRanker

    dense = AnnSearchRequest(
        data=[vector], anns_field='embedding',
        param={'metric_type': 'COSINE'}, limit=limit, expr=expression)
    sparse = AnnSearchRequest(
        data=[query], anns_field='sparse',
        param={'metric_type': 'BM25'}, limit=limit, expr=expression)
    return client.hybrid_search(
        collection_name=collection, reqs=[dense, sparse], ranker=RRFRanker(),
        limit=limit,
        output_fields=['record_id', 'kind', 'platform_mode', 'platform_values'])


def _scope_for(platform: str) -> dict[str, Any]:
    return {
        'applicability': {
            'platforms': {'mode': 'specific_only', 'values': [platform]},
        },
        'include_unknown': False,
    }


def _scope_leaks(connection, project_id, expected_by_version):
    if not expected_by_version:
        return 0
    version_ids = list(expected_by_version)
    with connection.cursor() as cursor:
        cursor.execute(
            '''SELECT version_id,platform_mode,platform_values
               FROM record_versions
               WHERE project_id=%s AND version_id=ANY(%s)''',
            (project_id, version_ids))
        rows = {str(row[0]): (row[1], list(row[2] or ()))
                for row in cursor.fetchall()}
    leaks = 0
    for version_id, expected_platform in expected_by_version.items():
        mode, values = rows.get(version_id, (None, ()))
        if mode != 'all' and not (
                mode == 'specific' and expected_platform in values):
            leaks += 1
    return leaks


class _InterruptAfterSnapshot:
    """Delegate to a real adapter and fail exactly once after snapshot S."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.interrupted = False

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def load_snapshot(self, *args, **kwargs):
        result = self.delegate.load_snapshot(*args, **kwargs)
        if not self.interrupted:
            self.interrupted = True
            raise RuntimeError('intentional capacity interruption after snapshot S')
        return result


def _drain_to_watermark(sync, repository, project_id, target):
    for _ in range(10000):
        cursor = repository.projections.cursor(project_id, target) or {}
        watermark = repository.projections.project_watermark(project_id)
        if (cursor.get('status') == 'ready'
                and int(cursor.get('applied_seq') or 0) == watermark):
            return cursor
        result = sync.drain_once(project_id, target, limit=1000)
        if result.count == 0:
            raise RuntimeError(f'{target} could not drain to watermark')
    raise RuntimeError(f'{target} drain exceeded acceptance bound')


def _exercise_rebuild_recovery(settings, milvus_client, neo4j_driver,
                               milvus_dimension):
    """Run a real S -> incremental -> W rebuild with one forced interruption."""
    from knowledge_service.integrations.embeddings import configured_encoder
    from knowledge_service.integrations.milvus_store import MilvusStore
    from knowledge_service.integrations.neo4j_store import Neo4jProjectionAdapter
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
    from knowledge_service.services.projection_sync import ProjectionSync

    repository = Repository(settings['KG_CAPACITY_POSTGRES_DSN'])
    base = settings['KG_CAPACITY_MILVUS_COLLECTION']
    encoder = configured_encoder()
    store = MilvusStore(
        dim=milvus_dimension, collection=base,
        encoder_identity=getattr(encoder, 'identity', ''))
    store._client = milvus_client
    milvus_adapter = MilvusProjectionAdapter(store, encoder=encoder)
    neo4j_adapter = Neo4jProjectionAdapter(
        neo4j_driver, namespace=repository.storage_namespace)
    interrupted = _InterruptAfterSnapshot(milvus_adapter)
    sync = ProjectionSync(repository.projections, {
        'milvus': interrupted, 'neo4j': neo4j_adapter}, schema_version=3)
    project = repository.create_project(
        f'capacity-rebuild-probe-{uuid4()}', {'acceptance_probe': True})
    project_id = project['id']
    record_id = f'capacity-probe-{uuid4()}'
    initial = {
        'id': record_id, 'kind': 'chunk', 'text': 'capacity rebuild S',
        'metadata': {'applicability': {
            'platforms': {'mode': 'all', 'values': []},
            'businesses': {'mode': 'all', 'values': []},
            'audiences': {'mode': 'all', 'values': []}}},
    }
    try:
        repository.put_record(project_id, initial)
        interrupted_error = None
        try:
            sync.rebuild(project_id, 'milvus')
        except RuntimeError as exc:
            interrupted_error = str(exc)
        if not interrupted.interrupted or 'intentional capacity interruption' not in (
                interrupted_error or ''):
            raise RuntimeError('rebuild interruption was not observed')
        failed_cursor = repository.projections.cursor(project_id, 'milvus') or {}
        snapshot_seq = int((failed_cursor.get('rebuild_state') or {}).get(
            'snapshot_seq', -1))

        updated = dict(initial, text='capacity rebuild incremental')
        repository.put_record(project_id, updated, expected_version=1)
        increment_watermark = repository.projections.project_watermark(project_id)
        milvus_result = sync.rebuild(project_id, 'milvus')
        neo4j_result = sync.rebuild(project_id, 'neo4j')

        tombstone = dict(updated, metadata={
            **updated['metadata'], '_deleted': True})
        repository.put_record(project_id, tombstone, expected_version=2)
        milvus_cursor = _drain_to_watermark(
            sync, repository, project_id, 'milvus')
        neo4j_cursor = _drain_to_watermark(
            sync, repository, project_id, 'neo4j')
        final_watermark = repository.projections.project_watermark(project_id)
        milvus_destination = store.current_destination(project_id)
        milvus_marker = store.read_fence_marker(project_id, milvus_destination)
        with neo4j_driver.session() as session:
            pointer = session.run(
                'MATCH (p:KSProjectionPointer {namespace:$ns,project_id:$pid})'
                '-[:KS_ACTIVE]->(g:KSProjection) '
                'RETURN g.generation AS generation,p.projection_seq AS pointer_seq,'
                'g.projection_seq AS generation_seq,p.schema_version AS schema_version',
                ns=repository.storage_namespace, pid=project_id).single()
        ready = bool(
            snapshot_seq >= 0 and increment_watermark > snapshot_seq
            and milvus_result.get('ready') and neo4j_result.get('ready')
            and milvus_cursor.get('active_destination') == milvus_destination
            and int(milvus_cursor.get('applied_seq') or -1) == final_watermark
            and int(milvus_marker.get('projection_seq') or -1) == final_watermark
            and neo4j_cursor.get('active_destination') == pointer.get('generation')
            and int(pointer.get('pointer_seq') or -1) == final_watermark
            and int(pointer.get('generation_seq') or -1) == final_watermark)
        return {
            'interruption_observed': True, 'resumed': True, 'ready': ready,
            'snapshot_seq': snapshot_seq,
            'increment_watermark': increment_watermark,
            'final_watermark': final_watermark,
            'milvus_destination': milvus_destination,
            'milvus_control_seq': milvus_marker.get('projection_seq'),
            'neo4j_generation': pointer.get('generation'),
            'neo4j_pointer_seq': pointer.get('pointer_seq'),
        }
    finally:
        try:
            try:
                store.cleanup_project(project_id)
            finally:
                with neo4j_driver.session() as session:
                    session.run(
                        'MATCH (n {namespace:$ns,project_id:$pid}) DETACH DELETE n',
                        ns=repository.storage_namespace, pid=project_id).consume()
            try:
                repository.delete_project(project_id)
            except KeyError:
                pass
            for target in ('milvus', 'neo4j'):
                cleanup = repository.claim_projection_cleanup(
                    project_id, target, 'capacity-rebuild-probe')
                if cleanup:
                    repository.ack_projection_cleanup(
                        project_id, target, 'capacity-rebuild-probe',
                        cleanup['claim_token'])
        finally:
            repository.close()


def _run_real(args, settings, report):
    try:
        import psycopg
        from neo4j import GraphDatabase
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RuntimeError(f'required real-store driver missing: {exc.name}') from None

    from knowledge_service.integrations.milvus_store import MilvusStore

    project_id = settings['KG_CAPACITY_PROJECT_ID']
    pg = psycopg.connect(settings['KG_CAPACITY_POSTGRES_DSN'], connect_timeout=10)
    milvus_args = {'uri': settings['KG_CAPACITY_MILVUS_URI']}
    if settings['KG_CAPACITY_MILVUS_TOKEN']:
        milvus_args['token'] = settings['KG_CAPACITY_MILVUS_TOKEN']
    milvus = MilvusClient(**milvus_args)
    neo4j = GraphDatabase.driver(
        settings['KG_CAPACITY_NEO4J_URI'], auth=(
            settings['KG_CAPACITY_NEO4J_USERNAME'],
            settings['KG_CAPACITY_NEO4J_PASSWORD']),
        connection_timeout=10, connection_acquisition_timeout=10)
    try:
        with pg.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        milvus.list_collections()
        neo4j.verify_connectivity()
        _set_check(report, 'real_services_connected', True,
                   'PostgreSQL, Milvus, and Neo4j responded')

        with pg.cursor() as cursor:
            cursor.execute(
                'SELECT count(DISTINCT document_id) FROM source_document_versions '
                'WHERE project_id=%s', (project_id,))
            documents = int(cursor.fetchone()[0])
            cursor.execute(
                'SELECT count(*) FROM record_versions '
                'WHERE project_id=%s AND superseded_at IS NULL', (project_id,))
            records = int(cursor.fetchone()[0])
            cursor.execute(
                '''SELECT DISTINCT value
                   FROM record_versions
                   CROSS JOIN LATERAL unnest(platform_values) AS value
                   WHERE project_id=%s AND superseded_at IS NULL
                     AND platform_mode='specific'
                   ORDER BY value''', (project_id,))
            platforms = [str(row[0]) for row in cursor.fetchall()]
        observed = {'documents': documents, 'platforms': len(platforms),
                    'records': records}
        report['dataset']['observed'] = observed
        scale_ok = (documents >= args.documents and len(platforms) >= args.platforms
                    and records >= args.records)
        _set_check(report, 'dataset_scale', scale_ok, observed)

        with pg.cursor() as cursor:
            cursor.execute(
                'SELECT projection_seq FROM project_projection_sequences '
                'WHERE project_id=%s', (project_id,))
            watermark_row = cursor.fetchone()
            cursor.execute(
                '''SELECT target,status,applied_seq,active_destination,
                          schema_version,rebuild_state
                   FROM projection_cursors
                   WHERE project_id=%s AND target=ANY(%s)''',
                (project_id, ['milvus', 'neo4j']))
            cursors = {row[0]: {
                'status': row[1], 'applied_seq': int(row[2]),
                'active_destination': row[3], 'schema_version': int(row[4]),
                'rebuild_state': row[5] or {}}
                       for row in cursor.fetchall()}
        watermark = None if watermark_row is None else int(watermark_row[0])
        projection_store = MilvusStore(
            collection=settings['KG_CAPACITY_MILVUS_COLLECTION'])
        projection_store._client = milvus
        milvus_destination = projection_store.current_destination(project_id)
        milvus_marker = (projection_store.read_fence_marker(
            project_id, milvus_destination) if milvus_destination else {})
        with neo4j.session() as session:
            neo_pointer = session.run(
                'MATCH (p:KSProjectionPointer {namespace:$namespace,project_id:$project_id})'
                '-[:KS_ACTIVE]->(g:KSProjection) '
                'RETURN g.generation AS generation,g.projection_seq AS generation_seq,'
                'g.schema_version AS generation_schema,p.projection_seq AS pointer_seq,'
                'p.schema_version AS pointer_schema',
                namespace=os.getenv('KG_CAPACITY_NEO4J_NAMESPACE', ''),
                project_id=project_id).single()
        # The namespace is repository-owned; an empty override cannot prove alignment.
        if not os.getenv('KG_CAPACITY_NEO4J_NAMESPACE'):
            from knowledge_service.repository.core import Repository
            namespace_repository = Repository(settings['KG_CAPACITY_POSTGRES_DSN'])
            try:
                namespace = namespace_repository.storage_namespace
            finally:
                namespace_repository.close()
            with neo4j.session() as session:
                neo_pointer = session.run(
                    'MATCH (p:KSProjectionPointer {namespace:$namespace,project_id:$project_id})'
                    '-[:KS_ACTIVE]->(g:KSProjection) '
                    'RETURN g.generation AS generation,g.projection_seq AS generation_seq,'
                    'g.schema_version AS generation_schema,p.projection_seq AS pointer_seq,'
                    'p.schema_version AS pointer_schema',
                    namespace=namespace, project_id=project_id).single()
        else:
            namespace = os.environ['KG_CAPACITY_NEO4J_NAMESPACE']
        milvus_aligned = False
        neo4j_aligned = False
        try:
            milvus_aligned = _require_projection_alignment(
                target='milvus', watermark=watermark,
                cursor=cursors.get('milvus') or {},
                destination=milvus_destination,
                control_seq=milvus_marker.get('projection_seq', -1))
            neo4j_aligned = _require_projection_alignment(
                target='neo4j', watermark=watermark,
                cursor=cursors.get('neo4j') or {},
                destination=None if neo_pointer is None else neo_pointer.get('generation'),
                control_seq=-1 if neo_pointer is None else neo_pointer.get('pointer_seq', -1))
            neo4j_aligned = bool(
                neo4j_aligned
                and int(neo_pointer.get('generation_seq') or -1) == watermark
                and int(neo_pointer.get('generation_schema') or -1)
                    == int(cursors['neo4j']['schema_version'])
                and int(neo_pointer.get('pointer_schema') or -1)
                    == int(cursors['neo4j']['schema_version']))
        except (TypeError, ValueError):
            pass
        ready = bool(milvus_aligned and neo4j_aligned)
        report['projection_alignment'] = {
            'verified': ready, 'watermark': watermark, 'cursors': cursors,
            'milvus': {'destination': milvus_destination,
                       'control_seq': milvus_marker.get('projection_seq')},
            'neo4j': dict(neo_pointer) if neo_pointer else None,
        }
        _set_check(report, 'projection_watermarks_ready', ready, {
            'watermark': watermark, 'cursors': cursors,
            'milvus_destination': milvus_destination,
            'neo4j_generation': None if neo_pointer is None else neo_pointer.get('generation')})

        selected_platforms = platforms[:args.platforms]
        if len(selected_platforms) < args.platforms:
            raise RuntimeError('dataset does not contain enough platforms')
        compiler = MilvusStore(collection=settings['KG_CAPACITY_MILVUS_COLLECTION'])
        compile_samples = []
        expressions = {}
        for index in range(max(100, args.samples * 20)):
            platform = selected_platforms[index % len(selected_platforms)]
            started = perf_counter()
            expression = compiler._filter_expr(
                project_id, kinds=['chunk'], scope=_scope_for(platform))
            compile_samples.append((perf_counter() - started) * 1000)
            expressions[platform] = expression
        compile_p95 = percentile(compile_samples)
        report['metrics_ms']['scope_compile_p95'] = compile_p95
        _set_check(report, 'scope_compile_p95',
                   compile_p95 <= THRESHOLDS_MS['scope_compile_p95'], compile_p95)

        with pg.cursor() as cursor:
            cursor.execute(
                '''EXPLAIN (FORMAT JSON)
                   SELECT version_id FROM record_versions
                   WHERE project_id=%s AND superseded_at IS NULL
                     AND (platform_mode='all' OR
                          (platform_mode='specific' AND platform_values && %s::text[]))
                   LIMIT 20''', (project_id, [selected_platforms[0]]))
            explain = cursor.fetchone()[0]
        nodes = list(_walk_plan(explain))
        sequential = [node.get('Relation Name') for node in nodes
                      if node.get('Node Type') == 'Seq Scan']
        report['postgres_explain'] = explain
        _set_check(report, 'postgres_explain_no_seq_scan', not sequential,
                   {'sequential_relations': sequential})

        signature = inspect.signature(MilvusStore.search_hybrid)
        no_candidates = ('candidate_ids' not in signature.parameters and all(
            'candidate_ids' not in expression and 'version_id' not in expression
            for expression in expressions.values()))
        report['milvus_filters'] = expressions
        _set_check(report, 'milvus_request_has_no_candidate_ids', no_candidates,
                   {'signature': str(signature)})

        collection = milvus_destination
        if not collection:
            raise RuntimeError('Milvus active alias has no generation')
        milvus.load_collection(collection_name=collection)
        dimension = _milvus_dimension(milvus, collection)
        component = 1.0 / math.sqrt(dimension)
        vector = [component] * dimension
        expected_by_version = {}
        hit_counts = {platform: 0 for platform in selected_platforms}

        single_samples = []
        first_platform = selected_platforms[0]
        for _ in range(args.samples):
            started = perf_counter()
            result = _milvus_hybrid(
                milvus, collection, args.query, vector,
                expressions[first_platform])
            single_samples.append((perf_counter() - started) * 1000)
            ids = _hit_version_ids(result)
            hit_counts[first_platform] += len(ids)
            for version_id in ids:
                expected_by_version[version_id] = first_platform
        single_p95 = percentile(single_samples)
        report['metrics_ms']['single_platform_hybrid_p95'] = single_p95
        _set_check(report, 'single_platform_hybrid_p95',
                   single_p95 <= THRESHOLDS_MS['single_platform_hybrid_p95'],
                   single_p95)

        grouped_samples = []
        for _ in range(args.samples):
            started = perf_counter()
            for platform in selected_platforms:
                result = _milvus_hybrid(
                    milvus, collection, args.query, vector,
                    expressions[platform])
                ids = _hit_version_ids(result)
                hit_counts[platform] += len(ids)
                for version_id in ids:
                    expected_by_version[version_id] = platform
            grouped_samples.append((perf_counter() - started) * 1000)
        _require_platform_hits(hit_counts, selected_platforms)
        report['selected_platforms'] = selected_platforms
        report['milvus_hit_counts_by_platform'] = hit_counts
        grouped_p95 = percentile(grouped_samples)
        report['metrics_ms']['ten_platform_grouped_p95'] = grouped_p95
        _set_check(report, 'ten_platform_grouped_p95',
                   grouped_p95 <= THRESHOLDS_MS['ten_platform_grouped_p95'],
                   grouped_p95)

        graph_samples = []
        graph_rows = []
        with neo4j.session() as session:
            for _ in range(args.samples):
                started = perf_counter()
                graph_rows = [record.data() for record in session.run(
                    NEO4J_TWO_HOP_QUERY, namespace=namespace,
                    project_id=project_id, platform=first_platform,
                    business='', audience='', include_unknown=False,
                    valid_epoch_ms=int(datetime.now(timezone.utc).timestamp() * 1000))]
                graph_samples.append((perf_counter() - started) * 1000)
        _require_two_hop_count(len(graph_rows), args.expected_two_hop_count)
        graph_p95 = percentile(graph_samples)
        report['metrics_ms']['two_hop_p95'] = graph_p95
        graph_count_ok = (bool(graph_rows) if args.expected_two_hop_count is None
                          else len(graph_rows) == args.expected_two_hop_count)
        _set_check(report, 'two_hop_p95',
                   graph_p95 <= THRESHOLDS_MS['two_hop_p95'] and graph_count_ok,
                   {'p95': graph_p95, 'paths': len(graph_rows)})

        leaks = _scope_leaks(pg, project_id, expected_by_version)
        for row in graph_rows:
            for item in [*(row.get('nodes') or ()), *(row.get('edges') or ())]:
                if item.get('mode') != 'all' and not (
                        item.get('mode') == 'specific'
                        and first_platform in (item.get('values') or ())):
                    leaks += 1
        report['leakage_count'] = leaks
        _set_check(report, 'scope_leakage_zero', leaks == 0,
                   {'leakage_count': leaks})

        report['rebuild_recovery'] = _exercise_rebuild_recovery(
            settings, milvus, neo4j, dimension)
    finally:
        try:
            neo4j.close()
        finally:
            try:
                milvus.close()
            finally:
                pg.close()


def main(argv=None) -> int:
    args = parse_args(argv)
    report = new_report(args)
    if args.dry_run:
        _write_report(args.report, report)
        print(f'DRY-RUN: wrote skipped acceptance plan to {args.report}')
        return 0

    report.update(status='running', previous_invalidated=True)
    _write_report(args.report, report)

    settings, missing = _settings(args)
    if missing:
        report['missing_requirements'] = missing
        report['limitations'].append(
            'Execution skipped because required real-service settings are absent.')
        report['status'] = 'skipped'
        _write_report(args.report, report)
        print(f'SKIP: missing real-service settings; report={args.report}')
        return EXIT_SKIPPED

    report['executed'] = True
    try:
        _run_real(args, settings, report)
        failed = [check for check in report['checks']
                  if check['status'] != 'passed']
        report['status'] = 'passed' if not failed else 'failed'
    except Exception as exc:
        report['status'] = 'failed'
        report['limitations'].append(
            f'Real acceptance stopped with {type(exc).__name__}; inspect service logs.')
    _write_report(args.report, report)
    print(f'{report["status"].upper()}: report={args.report}')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
