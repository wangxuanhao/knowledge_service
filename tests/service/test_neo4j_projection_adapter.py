from datetime import datetime, timezone

import pytest

from knowledge_service.integrations.neo4j_store import Neo4jProjectionAdapter
from knowledge_service.services.explorer import Explorer


class Result(list):
    def consume(self):
        return self

    def single(self):
        return self[0] if self else None


class Driver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def session(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def run(self, query, **params):
        self.calls.append((query, params))
        return Result(self.responses.pop(0) if self.responses else [])

    def execute_write(self, fn, *args):
        return fn(self, *args)

    def execute_read(self, fn, *args):
        return fn(self, *args)


def record(**overrides):
    row = {
        'id': 'entity-1', 'version_id': 'version-1', 'kind': 'entity',
        'text': 'one', 'metadata': {
            'applicability': {
                'platforms': {'mode': 'specific', 'values': ['美团', '京东']},
                'businesses': {'mode': 'all', 'values': []},
                'audiences': {'mode': 'unknown', 'values': []},
            }
        },
        'valid_from': '2026-01-01T00:00:00Z', 'valid_until': None,
    }
    row.update(overrides)
    return row


def test_snapshot_projects_native_scope_time_and_generation_properties():
    driver = Driver()
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')

    adapter.load_snapshot('project-1', 'generation-1', [record()], 17,
                          fence='claim-1', lease_is_current=lambda: True)

    rows = [params['rows'] for query, params in driver.calls
            if 'UNWIND $rows AS row' in query and 'KSRecordV3' in query]
    assert len(rows) == 1
    props = rows[0][0]['props']
    assert props['generation'] == 'generation-1'
    assert props['projection_seq'] == 17
    assert props['platform_mode'] == 'specific'
    assert props['platform_values'] == ['京东', '美团']
    assert props['business_mode'] == 'all'
    assert props['audience_mode'] == 'unknown'
    assert props['scope_identity']
    assert props['valid_from_known'] is True
    assert props['valid_from_epoch_ms'] == 1767225600000
    assert props['valid_until_known'] is False
    assert props['valid_until_epoch_ms'] == 0
    scope_rows = [params['rows'] for query, params in driver.calls
                  if 'KSScopeValue' in query and 'UNWIND $rows AS row' in query]
    assert {item['value'] for item in scope_rows[0]} == {'京东', '美团'}


def test_rebuild_lifecycle_uses_generation_and_transactional_active_pointer():
    driver = Driver()
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')
    destination = adapter.begin_rebuild(
        'project-1', 3, {'build_id': 'build-1'}, fence='claim-1')
    assert destination == 'build-1'
    adapter.reset_rebuild('project-1', destination, 'build-1', fence='claim-1')
    adapter.switch_alias('project-1', destination, build_id='build-1',
                         fence='claim-1')

    text = '\n'.join(query for query, _ in driver.calls)
    assert 'KSScopeValue' in text
    assert 'KSProjectionPointer' in text
    assert 'DELETE old' in text
    assert 'KS_ACTIVE' in text
    assert all('project-1' not in query and 'build-1' not in query
               for query, _ in driver.calls)


def test_rebuild_validation_checks_native_projection_not_only_version_ids():
    driver = Driver()
    projected = {
        'id': 'entity-1', 'version_id': 'version-1',
        'platform': {'mode': 'specific', 'values': ['美团']},
        'business': {'mode': 'all', 'values': []},
        'audience': {'mode': 'unknown', 'values': []},
        'scope_identity': 'scope', 'valid_from': [True, 1],
        'valid_until': [False, 0],
    }
    driver.responses = [[{
        'schema_version': 3,
        'versions': [['entity-1', 'version-1']],
        'projections': [projected],
    }]]
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')
    expected = {key: value for key, value in projected.items() if key != 'id'}

    result = adapter.validate_rebuild(
        'project-1', 'generation-1', state={
            'expected_versions': {'entity-1': 'version-1'},
            'expected_projection': {'entity-1': expected},
        })

    assert result['ok'] is True
    assert result['scope_ok'] is True


def test_apply_tombstone_and_upsert_are_ordered_and_parameterized():
    driver = Driver()
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')

    adapter.apply('project-1', {'seq': 4, 'operation': 'upsert',
                               'payload': record()},
                  destination='generation-1', fence='claim-1')
    adapter.apply('project-1', {'seq': 5, 'operation': 'tombstone',
                               'payload': {'record_id': 'entity-1'}},
                  destination='generation-1', fence='claim-1')

    text = '\n'.join(query for query, _ in driver.calls)
    assert 'projection_seq' in text
    assert 'DETACH DELETE' in text
    assert all('entity-1' not in query for query, _ in driver.calls)


def test_expand_applies_scope_and_time_to_every_node_and_relationship():
    driver = Driver()
    driver.responses = [[{'generation': 'generation-1'}],
                        [{'version_ids': ['v-node', 'v-relation']}]]
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')

    result = adapter.expand(
        'project-1', ['seed` MATCH (n) DETACH DELETE n'], 2,
        applicability={
            'platforms': {'mode': 'specific', 'values': ['美团', '京东']},
            'businesses': {'mode': 'specific', 'values': ['外卖']},
            'audiences': {'mode': 'unknown', 'values': []},
        },
        valid_at='2026-02-01T00:00:00Z', include_unknown=False)

    assert result == ['v-node', 'v-relation']
    query, params = driver.calls[-1]
    assert query.count('valid_from_known') >= 3
    assert query.count('platform_mode') >= 3
    assert query.count('KS_FACT') >= 2
    assert 'n1.valid_from_epoch_ms <= $valid_epoch_ms' in query
    assert 'e1.valid_until_epoch_ms > $valid_epoch_ms' in query
    assert 'KSScopeValue' in query
    assert 'allowed_platform_scope_ids' in query
    for alias in ('n0', 'n1', 'n2', 'e1', 'e2'):
        assert f'{alias}.namespace=$ns' in query
        assert f'{alias}.project_id=$pid' in query
        assert f'{alias}.generation=$generation' in query
    assert 'KSProjectionPointer' in query and 'KS_ACTIVE' in query
    assert params['platform_values'] == ['京东', '美团']
    assert params['business_values'] == ['外卖']
    assert params['seed_ids'] == ['seed` MATCH (n) DETACH DELETE n']
    assert 'DETACH DELETE' not in query
    assert params['valid_epoch_ms'] == int(datetime(
        2026, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_expand_defaults_missing_valid_at_to_current_time():
    driver = Driver()
    driver.responses = [[{'generation': 'generation-1'}],
                        [{'version_ids': []}]]
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')

    before = int(datetime.now(timezone.utc).timestamp() * 1000)
    adapter.expand('project-1', ['seed'], 0)
    after = int(datetime.now(timezone.utc).timestamp() * 1000)

    assert before <= driver.calls[-1][1]['valid_epoch_ms'] <= after


def test_expand_rejects_more_than_two_hops_and_historical_reads():
    adapter = Neo4jProjectionAdapter(Driver(), namespace='test-ns')
    with pytest.raises(ValueError, match='0 到 2'):
        adapter.expand('project-1', ['seed'], 3)
    with pytest.raises(ValueError, match='known_at'):
        adapter.expand('project-1', ['seed'], 1,
                       known_at='2026-01-01T00:00:00Z')


@pytest.mark.parametrize('attribute_mode', ['none', 'summary', 'expanded'])
def test_explorer_backfills_native_versions_with_one_current_only_read(
        attribute_mode):
    class Repository:
        def __init__(self):
            self.calls = []

        def projection_ready(self, project_id, target, schema_version):
            return (project_id, target, schema_version) == ('p', 'neo4j', 3)

        def current_records_by_version_ids(self, project_id, version_ids):
            self.calls.append((project_id, version_ids))
            return [{'id': 'seed', 'version_id': 'v1'}]

    class Projection:
        def expand(self, *args, **kwargs):
            return ['v1']

    repo = Repository()
    service = type('Service', (), {
        'repository': repo, 'neo4j_projection': Projection()})()

    result = Explorer(service)._native_graph_rows(
        'p', {'applicability': None}, 'seed', 2, attribute_mode)

    assert result['rows'] == [{'id': 'seed', 'version_id': 'v1'}]
    assert result['backend'] == 'neo4j_native'
    assert result['degraded_reason'] is None
    assert repo.calls == [('p', ['v1'])]


def test_explorer_uses_local_fallback_when_neo4j_readiness_is_stale():
    class Repository:
        def projection_ready(self, *args):
            return False

        def current_records_by_version_ids(self, *args):
            raise AssertionError('stale projection must not be read')

    class Projection:
        def expand(self, *args, **kwargs):
            raise AssertionError('stale projection must not be queried')

    service = type('Service', (), {
        'repository': Repository(), 'neo4j_projection': Projection()})()
    result = Explorer(service)._native_graph_rows(
        'p', {}, 'seed', 1, 'summary')
    assert result['rows'] is None
    assert result['backend'] == 'postgres_bfs'
    assert result['degraded_reason'] == 'readiness_stale'


def test_authority_repair_bundle_restores_incident_relation_edges():
    driver = Driver()
    adapter = Neo4jProjectionAdapter(driver, namespace='test-ns')
    bundle = [
        record(id='entity-1', version_id='entity-v2'),
        record(id='other', version_id='other-v1'),
        record(id='relation-1', version_id='relation-v1', kind='relation',
               subject_id='entity-1', object_id='other'),
    ]

    adapter.repair_record(
        'project-1', 'entity-1', bundle, 'generation-1', 22)

    relation_calls = [params for query, params in driver.calls
                      if 'MERGE (a)-[e:KS_FACT' in query]
    assert relation_calls
    assert relation_calls[-1]['rows'][0]['props']['id'] == 'relation-1'


@pytest.mark.parametrize(
    ('ready', 'backend', 'reason'),
    [(True, 'neo4j_native', None),
     (False, 'postgres_bfs', 'readiness_stale')])
def test_graph_response_reports_native_backend_or_explicit_fallback(
        ready, backend, reason):
    entity = record(id='seed', version_id='v1', type='Thing')

    class Repository:
        def projection_ready(self, *args):
            return ready

        def current_records_by_version_ids(self, *args):
            return [entity]

    class Projection:
        def expand(self, *args, **kwargs):
            return ['v1']

    class Service:
        repository = Repository()
        neo4j_projection = Projection()

        def scoped(self, *args, **kwargs):
            return [entity]

        def record_family(self, *args):
            return {}

    result = Explorer(Service()).graph(
        'p', {}, node_id='seed', hops=1, attribute_mode='none')

    assert result['backend'] == backend
    assert result['degraded_reason'] == reason
