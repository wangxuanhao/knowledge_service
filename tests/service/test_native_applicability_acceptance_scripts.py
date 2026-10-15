import json
import re

import pytest

from scripts import rehearse_migration_0013 as rehearsal
from scripts import verify_native_applicability_capacity as capacity


def test_capacity_dry_run_writes_honest_skipped_report(tmp_path):
    report_path = tmp_path / 'capacity.json'

    exit_code = capacity.main([
        '--dry-run', '--report', str(report_path),
        '--documents', '5000', '--platforms', '10', '--records', '100000',
    ])

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert report['schema_version'] == 1
    assert report['kind'] == 'native_applicability_capacity'
    assert report['status'] == 'skipped'
    assert report['executed'] is False
    assert report['dataset']['targets'] == {
        'documents': 5000, 'platforms': 10, 'records': 100000}
    assert report['thresholds_ms'] == {
        'scope_compile_p95': 5.0,
        'single_platform_hybrid_p95': 800.0,
        'ten_platform_grouped_p95': 3000.0,
        'two_hop_p95': 1500.0,
    }
    assert all(item['status'] == 'skipped' for item in report['checks'])
    capacity.validate_report(report)


def test_capacity_execute_requires_all_real_service_settings(tmp_path, monkeypatch):
    for name in capacity.REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    report_path = tmp_path / 'capacity.json'

    exit_code = capacity.main([
        '--execute', '--report', str(report_path), '--project-id', 'capacity-p1'])

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert exit_code == capacity.EXIT_SKIPPED
    assert report['status'] == 'skipped'
    assert report['executed'] is False
    assert set(report['missing_requirements']) == set(capacity.REQUIRED_ENVIRONMENT)
    serialized = json.dumps(report)
    assert 'postgresql://' not in serialized
    assert 'neo4j://' not in serialized


@pytest.mark.parametrize(
    ('option', 'value'),
    [('--documents', '4999'), ('--platforms', '9'), ('--records', '99999')],
)
def test_capacity_parser_rejects_below_acceptance_scale(option, value):
    with pytest.raises(SystemExit) as raised:
        capacity.parse_args(['--dry-run', option, value])
    assert raised.value.code == 2


def test_capacity_report_schema_rejects_false_pass():
    report = capacity.new_report(capacity.parse_args(['--dry-run']))
    report['status'] = 'passed'
    with pytest.raises(ValueError, match='passed report must be executed'):
        capacity.validate_report(report)


def test_capacity_rejects_empty_milvus_platform_hits():
    counts = {f'platform-{index}': 1 for index in range(10)}
    counts['platform-4'] = 0

    with pytest.raises(ValueError, match='nonempty Milvus hits'):
        capacity._require_platform_hits(counts, list(counts))


def test_capacity_requires_every_expected_platform_group():
    counts = {f'platform-{index}': 2 for index in range(9)}

    with pytest.raises(ValueError, match='missing platform groups'):
        capacity._require_platform_hits(
            counts, [f'platform-{index}' for index in range(10)])


def test_capacity_rejects_projection_destination_mismatch():
    with pytest.raises(ValueError, match='active destination mismatch'):
        capacity._require_projection_alignment(
            target='milvus', watermark=12,
            cursor={'status': 'ready', 'applied_seq': 12,
                    'active_destination': 'generation-a'},
            destination='generation-b', control_seq=12)


def test_capacity_pass_requires_rebuild_recovery_and_nonempty_evidence():
    report = capacity.new_report(capacity.parse_args(['--dry-run']))
    report.update(status='passed', executed=True, previous_invalidated=True,
                  leakage_count=0)
    for check in report['checks']:
        check['status'] = 'passed'

    with pytest.raises(ValueError, match='interruption/resume'):
        capacity.validate_report(report)


def test_capacity_neo4j_query_uses_v3_pointer_and_no_legacy_label():
    query = capacity.NEO4J_TWO_HOP_QUERY
    assert 'KSProjectionPointer' in query
    assert 'KSRecordV3' in query
    assert 'KS_FACT' in query
    assert re.search(r':KSRecord(?!V3)', query) is None
    for alias in ('seed', 'edge', 'node'):
        assert f'{alias}.namespace=$namespace' in query
        assert f'{alias}.project_id=$project_id' in query
        assert f'{alias}.generation=g.generation' in query


def test_capacity_two_hop_defaults_to_nonempty_but_allows_explicit_zero_fixture():
    with pytest.raises(ValueError, match='nonempty two-hop'):
        capacity._require_two_hop_count(0, None)
    assert capacity._require_two_hop_count(0, 0) == 0


def test_capacity_execute_invalidates_previous_report_before_real_work(
        tmp_path, monkeypatch):
    report_path = tmp_path / 'capacity.json'

    def stop_after_observing_running(_args, _settings, _report):
        current = json.loads(report_path.read_text(encoding='utf-8'))
        assert current['status'] == 'running'
        assert current['previous_invalidated'] is True
        raise RuntimeError('intentional test stop')

    monkeypatch.setattr(capacity, '_run_real', stop_after_observing_running)
    exit_code = capacity.main([
        '--execute', '--report', str(report_path), '--project-id', 'p1',
        '--postgres-dsn', 'postgresql://example/db',
        '--milvus-uri', 'http://milvus.example:19530',
        '--milvus-collection', 'records',
        '--neo4j-uri', 'neo4j://neo4j.example',
        '--neo4j-username', 'neo4j', '--neo4j-password', 'secret',
    ])

    assert exit_code == 1
    assert json.loads(report_path.read_text(encoding='utf-8'))['status'] == 'failed'


@pytest.mark.parametrize('module', [capacity, rehearsal])
def test_report_write_is_atomic_and_replacement_sees_complete_json(
        module, tmp_path, monkeypatch):
    report = (module.new_report(module.parse_args(['--dry-run']))
              if module is capacity else module.new_report())
    destination = tmp_path / 'report.json'
    observed = {}
    real_replace = module.os.replace

    def inspect_replace(source, target):
        observed['source'] = str(source)
        observed['payload'] = json.loads(
            module.Path(source).read_text(encoding='utf-8'))
        real_replace(source, target)

    monkeypatch.setattr(module.os, 'replace', inspect_replace)
    module._write_report(destination, report)

    assert observed['source'] != str(destination)
    assert observed['payload']['kind'] == report['kind']
    assert json.loads(destination.read_text(encoding='utf-8')) == observed['payload']


def test_migration_dry_run_lists_0013_through_latest_and_skips(tmp_path):
    report_path = tmp_path / 'migration.json'

    exit_code = rehearsal.main(['--dry-run', '--report', str(report_path)])

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert report['kind'] == 'native_applicability_migration_rehearsal'
    assert report['status'] == 'skipped'
    versions = [item['version'] for item in report['migrations']]
    assert versions[:5] == [13, 14, 15, 16, 17]
    assert versions == sorted(versions)
    assert report['checks']['idempotent_second_run'] == 'skipped'
    assert report['checks']['outbox_rebuild_cleanup'] == 'skipped'
    rehearsal.validate_report(report)


def test_migration_execute_without_disposable_dsn_is_explicit_skip(
        tmp_path, monkeypatch):
    monkeypatch.delenv('KG_ACCEPTANCE_MIGRATION_DSN', raising=False)
    report_path = tmp_path / 'migration.json'

    exit_code = rehearsal.main(['--execute', '--report', str(report_path)])

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert exit_code == rehearsal.EXIT_SKIPPED
    assert report['status'] == 'skipped'
    assert report['missing_requirements'] == ['KG_ACCEPTANCE_MIGRATION_DSN']
    assert 'postgresql://' not in json.dumps(report).lower()


def test_migration_rehearsal_does_not_forge_projection_state_with_sql():
    source = rehearsal.Path(rehearsal.__file__).read_text(encoding='utf-8')
    forbidden = (
        'projection_outbox', 'projection_cursors',
        'projection_rebuild_snapshots', 'projection_cleanup_outbox',
    )
    for table in forbidden:
        assert re.search(rf'INSERT\s+INTO\s+{table}\b', source, re.I) is None


def test_migration_report_delegates_external_rebuild_to_capacity_script():
    report = rehearsal.new_report()
    assert report['external_rebuild'] == 'delegated_to_capacity_script'
