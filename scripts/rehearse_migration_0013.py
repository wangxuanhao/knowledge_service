"""Rehearse migrations 0013 through the latest migration on a disposable DB.

The script requires an explicitly named disposable PostgreSQL database. It
builds the pre-0013 schema, inserts a legacy applicability row, applies the
remaining migrations, probes outbox/rebuild/cleanup state, and runs the full
migrator again to prove idempotence. It never treats a dry run as a pass.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from knowledge_service.repository.migrate import (  # noqa: E402
    MIGRATIONS_DIR, apply_migrations, discover_migrations,
)


EXIT_SKIPPED = 3
REPORT_SCHEMA_VERSION = 1
DISPOSABLE_PREFIX = 'kg_acceptance_'


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Rehearse applicability/projection migrations on a disposable DB.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--dsn')
    parser.add_argument('--report', type=Path,
                        default=Path('artifacts/migration-0013-rehearsal.json'))
    parser.add_argument('--lock-timeout-ms', type=int, default=5_000)
    parser.add_argument(
        '--allow-disposable-database', action='store_true',
        help=f'required for execute; database name must start with {DISPOSABLE_PREFIX}')
    args = parser.parse_args(argv)
    if not args.execute:
        args.dry_run = True
    if args.lock_timeout_ms <= 0:
        parser.error('--lock-timeout-ms must be positive')
    return args


def _migration_rows() -> list[dict[str, Any]]:
    return [
        {'version': migration.version, 'name': migration.name,
         'checksum': migration.checksum}
        for migration in discover_migrations(MIGRATIONS_DIR)
        if migration.version >= 13
    ]


def new_report() -> dict[str, Any]:
    return {
        'schema_version': REPORT_SCHEMA_VERSION,
        'kind': 'native_applicability_migration_rehearsal',
        'status': 'skipped',
        'executed': False,
        'previous_invalidated': False,
        'started_at': _utc_now(),
        'finished_at': None,
        'migrations': _migration_rows(),
        'checks': {
            'legacy_scope_backfill': 'skipped',
            'native_indexes': 'skipped',
            'outbox_rebuild_cleanup': 'skipped',
            'idempotent_second_run': 'skipped',
        },
        'missing_requirements': [],
        'external_rebuild': 'delegated_to_capacity_script',
        'limitations': [
            'Dry-run only lists the exact migration set; it is not DB evidence.',
            'Execution is refused unless the database name has the disposable prefix.',
        ],
    }


def validate_report(report: dict[str, Any]) -> None:
    required = {
        'schema_version', 'kind', 'status', 'executed', 'migrations', 'checks',
    }
    missing = required.difference(report)
    if missing:
        raise ValueError(f'report fields missing: {sorted(missing)}')
    if report['schema_version'] != REPORT_SCHEMA_VERSION:
        raise ValueError('unsupported report schema_version')
    if report['kind'] != 'native_applicability_migration_rehearsal':
        raise ValueError('unexpected report kind')
    if report['status'] not in {'skipped', 'running', 'passed', 'failed'}:
        raise ValueError('invalid report status')
    versions = [row['version'] for row in report['migrations']]
    if versions[:5] != [13, 14, 15, 16, 17] or versions != sorted(versions):
        raise ValueError('report must cover migrations 0013 through latest')
    if report['status'] == 'passed':
        if not report['executed']:
            raise ValueError('passed report must be executed')
        if report.get('previous_invalidated') is not True:
            raise ValueError('passed report must invalidate previous evidence')
        if any(value != 'passed' for value in report['checks'].values()):
            raise ValueError('passed report requires every check to pass')


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


def _database_name(dsn: str) -> str:
    parsed = urlsplit(dsn)
    if parsed.scheme not in {'postgresql', 'postgres'} or not parsed.hostname:
        raise ValueError('acceptance DSN must be a PostgreSQL URI')
    return unquote(parsed.path.lstrip('/').split('/', 1)[0])


def _copy_baseline(destination: Path) -> None:
    for migration in discover_migrations(MIGRATIONS_DIR):
        if migration.version < 13:
            shutil.copy2(migration.path, destination / migration.path.name)


def _insert_legacy_samples(dsn: str) -> tuple[str, dict[str, str]]:
    import psycopg

    project_id = 'migration-0013-rehearsal'
    fixtures = {
        'old_list': ('legacy-list', 1, 'legacy-list-v1', None, {
            'id': 'legacy-list', 'kind': 'chunk', 'text': 'legacy list',
            'valid_from': '2026-01-01T00:00:00Z', 'valid_until': None,
            'metadata': {'applicability': {
                'platforms': ['meituan'], 'businesses': [], 'audiences': []}}}),
        'empty_scope': ('legacy-empty', 1, 'legacy-empty-v1', None, {
            'id': 'legacy-empty', 'kind': 'chunk', 'text': 'legacy empty',
            'valid_from': None, 'valid_until': None,
            'metadata': {'applicability': {
                'platforms': [], 'businesses': [], 'audiences': []}}}),
        'historical': ('legacy-history', 1, 'legacy-history-v1',
                       '2026-01-02T00:00:00Z', {
            'id': 'legacy-history', 'kind': 'chunk', 'text': 'historical v1',
            'valid_from': '2025-01-01T00:00:00Z', 'valid_until': None,
            'metadata': {'applicability': {
                'platforms': ['taobao'], 'businesses': ['retail'],
                'audiences': ['staff']}}}),
        'current': ('legacy-history', 2, 'legacy-history-v2', None, {
            'id': 'legacy-history', 'kind': 'chunk', 'text': 'current v2',
            'valid_from': '2026-01-02T00:00:00Z', 'valid_until': None,
            'metadata': {'applicability': {
                'platforms': ['jd'], 'businesses': ['retail'],
                'audiences': ['staff']}}}),
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''INSERT INTO projects(id,name,metadata,created_at)
                   VALUES (%s,%s,%s,clock_timestamp())''',
                (project_id, 'migration rehearsal', '{}'))
            cursor.executemany(
                '''INSERT INTO record_versions
                   (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                   VALUES (%s,%s,%s,%s,%s,clock_timestamp(),%s)''', [
                (project_id, record_id, version, version_id,
                 json.dumps(payload, ensure_ascii=False), superseded_at)
                for record_id, version, version_id, superseded_at, payload
                in fixtures.values()])
        connection.commit()
    return project_id, {name: row[2] for name, row in fixtures.items()}


def _probe_schema_and_state(dsn: str, project_id: str, version_ids, report):
    import psycopg
    from knowledge_service.repository.core import Repository

    expected_indexes = {
        'versions_current_platform_mode',
        'versions_current_platform_values_gin',
        'versions_current_business_values_gin',
        'versions_current_audience_values_gin',
        'versions_current_valid_from',
        'versions_current_valid_until',
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT version_id,platform_mode,platform_values,business_mode,
                          business_values,audience_mode,audience_values,
                          valid_from_known,valid_until_known,superseded_at
                   FROM record_versions
                   WHERE project_id=%s ORDER BY id,version''', (project_id,))
            rows = {str(row[0]): row for row in cursor.fetchall()}
            listed = rows.get(version_ids['old_list'])
            empty = rows.get(version_ids['empty_scope'])
            historical = rows.get(version_ids['historical'])
            current = rows.get(version_ids['current'])
            backfill_ok = bool(
                listed and listed[1] == 'specific'
                and list(listed[2]) == ['美团']
                and listed[3] == 'unknown' and listed[5] == 'unknown'
                and listed[7] is True and listed[8] is False
                and empty and empty[1] == empty[3] == empty[5] == 'unknown'
                and historical and historical[1] == 'specific'
                and list(historical[2]) == ['淘宝'] and historical[9] is not None
                and current and current[1] == 'specific'
                and list(current[2]) == ['京东'] and current[9] is None)
            report['checks']['legacy_scope_backfill'] = (
                'passed' if backfill_ok else 'failed')
            report['legacy_fixtures'] = {
                'old_list': bool(listed), 'empty_scope': bool(empty),
                'historical_revision': bool(historical),
                'current_revision': bool(current)}

            cursor.execute(
                '''SELECT indexrelid::regclass::text, indisvalid, indisready
                   FROM pg_index
                   WHERE indexrelid::regclass::text = ANY(%s)''',
                (sorted(expected_indexes),))
            index_rows = {str(row[0]): bool(row[1] and row[2])
                          for row in cursor.fetchall()}
            indexes_ok = all(index_rows.get(name) for name in expected_indexes)
            report['checks']['native_indexes'] = (
                'passed' if indexes_ok else 'failed')
            report['native_indexes'] = index_rows

        connection.commit()

    repository = Repository(dsn)
    try:
        api_project = repository.create_project(
            'migration API rehearsal', {'acceptance_probe': True})
        api_project_id = api_project['id']
        record = {
            'id': 'api-probe', 'kind': 'chunk', 'text': 'put',
            'metadata': {'applicability': {
                'platforms': ['meituan'], 'businesses': [], 'audiences': []}}}
        first = repository.put_record(api_project_id, record)
        second = repository.put_record(
            api_project_id, dict(record, text='update'), expected_version=1)
        tombstone = repository.put_record(
            api_project_id,
            dict(record, text='delete', metadata={
                **record['metadata'], '_deleted': True}), expected_version=2)
        watermark = repository.projections.project_watermark(api_project_id)
        claimed = repository.projections.claim_contiguous_batch(
            api_project_id, 'milvus', 3, limit=100, owner='migration-rehearsal')
        cursor_ok = bool(claimed and len(claimed.events) == watermark)
        if claimed:
            repository.projections.ack_contiguous(claimed, 3)
        ready_cursor = repository.projections.cursor(api_project_id, 'milvus') or {}
        repository.delete_project(api_project_id)
        released = repository.claim_projection_cleanup(
            api_project_id, 'milvus', 'migration-rehearsal')
        release_ok = bool(released and repository.release_projection_cleanup(
            api_project_id, 'milvus', 'migration-rehearsal',
            released['claim_token'], 'rehearsal retry'))
        reclaimed = repository.claim_projection_cleanup(
            api_project_id, 'milvus', 'migration-rehearsal')
        ack_milvus = bool(reclaimed and repository.ack_projection_cleanup(
            api_project_id, 'milvus', 'migration-rehearsal',
            reclaimed['claim_token']))
        neo4j_cleanup = repository.claim_projection_cleanup(
            api_project_id, 'neo4j', 'migration-rehearsal')
        ack_neo4j = bool(neo4j_cleanup and repository.ack_projection_cleanup(
            api_project_id, 'neo4j', 'migration-rehearsal',
            neo4j_cleanup['claim_token']))
        state_ok = bool(
            first['version'] == 1 and second['version'] == 2
            and tombstone['version'] == 3 and cursor_ok
            and int(ready_cursor.get('applied_seq') or -1) == watermark
            and release_ok and ack_milvus and ack_neo4j)
        report['checks']['outbox_rebuild_cleanup'] = (
            'passed' if state_ok else 'failed')
        report['projection_probe'] = {
            'repository_put_update_delete': True,
            'watermark': watermark,
            'claimed_events': 0 if claimed is None else len(claimed.events),
            'cursor_status': ready_cursor.get('status'),
            'cleanup_release_reclaim_ack': release_ok and ack_milvus,
            'cleanup_neo4j_ack': ack_neo4j,
        }
    finally:
        repository.close()


def _run_real(dsn: str, args, report):
    with TemporaryDirectory(prefix='ks-migrations-before-0013-') as temporary:
        baseline = Path(temporary)
        _copy_baseline(baseline)
        baseline_result = apply_migrations(
            dsn, directory=baseline, verbose=False,
            lock_timeout_ms=args.lock_timeout_ms)
    project_id, version_ids = _insert_legacy_samples(dsn)
    first = apply_migrations(
        dsn, directory=MIGRATIONS_DIR, verbose=False,
        lock_timeout_ms=args.lock_timeout_ms)
    _probe_schema_and_state(dsn, project_id, version_ids, report)
    second = apply_migrations(
        dsn, directory=MIGRATIONS_DIR, verbose=False,
        lock_timeout_ms=args.lock_timeout_ms)
    expected = [row['version'] for row in report['migrations']]
    applied_versions = [int(version) for version in first['applied']]
    idempotent = (baseline_result['current'] == 12
                  and applied_versions == expected
                  and second['applied'] == [])
    report['checks']['idempotent_second_run'] = (
        'passed' if idempotent else 'failed')
    report['runs'] = {
        'baseline_current': baseline_result['current'],
        'first_applied': applied_versions,
        'second_applied_count': len(second['applied']),
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    report = new_report()
    if args.dry_run:
        _write_report(args.report, report)
        print(f'DRY-RUN: wrote skipped migration plan to {args.report}')
        return 0

    report.update(status='running', previous_invalidated=True)
    _write_report(args.report, report)

    dsn = args.dsn or os.getenv('KG_ACCEPTANCE_MIGRATION_DSN', '')
    if not dsn:
        report['missing_requirements'] = ['KG_ACCEPTANCE_MIGRATION_DSN']
        report['status'] = 'skipped'
        _write_report(args.report, report)
        print(f'SKIP: missing disposable migration database; report={args.report}')
        return EXIT_SKIPPED
    if not args.allow_disposable_database:
        report['missing_requirements'] = ['--allow-disposable-database']
        report['status'] = 'skipped'
        _write_report(args.report, report)
        print(f'SKIP: disposable database acknowledgement required; report={args.report}')
        return EXIT_SKIPPED
    try:
        database = _database_name(dsn)
    except ValueError:
        database = ''
    if not database.startswith(DISPOSABLE_PREFIX):
        report['status'] = 'failed'
        report['limitations'].append(
            f'Execution refused: database name must start with {DISPOSABLE_PREFIX}.')
        _write_report(args.report, report)
        print(f'FAILED: refused non-disposable database; report={args.report}')
        return 1

    report['executed'] = True
    try:
        _run_real(dsn, args, report)
        report['status'] = ('passed' if all(
            value == 'passed' for value in report['checks'].values())
                            else 'failed')
    except Exception as exc:
        report['status'] = 'failed'
        report['limitations'].append(
            f'Rehearsal stopped with {type(exc).__name__}; inspect PostgreSQL logs.')
    _write_report(args.report, report)
    print(f'{report["status"].upper()}: report={args.report}')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
