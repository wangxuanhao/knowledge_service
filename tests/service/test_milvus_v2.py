import pytest

from knowledge_service.integrations.milvus_store import (
    MilvusSchemaMismatch, MilvusStore, V2_FIELDS,
)
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.services.projection_sync import ProjectionSync


def test_v2_filter_compiles_scope_and_half_open_business_time_without_candidates():
    store = MilvusStore(dim=2)
    expression = store._filter_expr(
        'project', ['chunk'],
        scope={
            'applicability': {
                'platforms': ['美团'],
                'businesses': {'mode': 'specific', 'values': ['直播']},
            },
            'valid_at': '2025-01-01T00:00:00Z',
            'include_unknown': False,
        })

    assert 'project_id == "project"' in expression
    assert 'kind in ["chunk"]' in expression
    assert 'platform_mode == "all"' in expression
    assert 'array_contains_any(platform_values, ["美团"])' in expression
    assert 'array_contains_any(business_values, ["直播"])' in expression
    assert 'valid_from_known == true' in expression
    assert 'valid_from_epoch_ms <= 1735689600000' in expression
    assert '(valid_until_known == false or valid_until_epoch_ms > 1735689600000)' in expression
    assert 'candidate' not in expression


def test_filter_quotes_hostile_ids_rejects_unknown_kinds_and_supports_exact_modes():
    store = MilvusStore(dim=2)
    expression = store._filter_expr(
        'p" or active == false or project_id == "x', ['chunk'], scope={
            'applicability': {
                'platforms': {'mode': 'specific_only', 'values': ['美团']},
                'businesses': {'mode': 'all_only'},
                'audiences': {'mode': 'unknown_only'},
            }})
    assert 'project_id == "p\\" or active == false' in expression
    assert '(platform_mode == "specific" and' in expression
    assert 'business_mode == "all"' in expression
    assert 'audience_mode == "unknown"' in expression
    with pytest.raises(ValueError, match='kinds'):
        store._filter_expr('p', ['chunk") or active == true'])


class Encoder:
    identity = 'test'
    semantic = True

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class NativeRepository:
    def __init__(self):
        self.requested = []
        self.current = True

    def projection_ready(self, project_id, target, version):
        return True

    def query(self, *args, **kwargs):
        raise AssertionError('Milvus V2 online search must not load project rows')

    def current_records_by_version_ids(self, project_id, version_ids):
        self.requested.append((project_id, version_ids))
        return ([{'id': 'record', 'version_id': version_ids[0], 'kind': 'chunk',
                  'text': '规则', 'metadata': {}}] if self.current else [])


class NativeMilvus:
    available = True

    def __init__(self):
        self.scope = None

    def search_dense(self, project_id, vector, *, kinds, k, scope):
        self.scope = scope
        return [{'version_id': 'version-1', 'score': .9}]


def test_online_milvus_search_uses_native_scope_then_small_version_backfill():
    repository = NativeRepository()
    milvus = NativeMilvus()
    engine = RetrievalEngine(repository, Encoder(), milvus)

    result = engine.search(
        'project', '规则', retrieval_mode='semantic', content_channels=['chunk'],
        scope={'applicability': {'platforms': ['美团']},
               'valid_at': '2025-01-01T00:00:00Z'}, k=5)

    assert repository.requested == [('project', ['version-1'])]
    assert milvus.scope['applicability'] == {'platforms': ['美团']}
    assert [row['id'] for row in result['hits']] == ['record']


def test_online_backfill_drops_historical_version_even_when_milvus_returns_it():
    repository = NativeRepository()
    repository.current = False
    engine = RetrievalEngine(repository, Encoder(), NativeMilvus())
    result = engine.search(
        'project', '规则', retrieval_mode='semantic',
        content_channels=['chunk'], scope={}, k=5)
    assert result['hits'] == []


class SchemaClient:
    def __init__(self):
        self.schema = None
        self.rows = None

    def create_collection(self, name, schema):
        self.schema = schema

    def create_index(self, *args):
        pass

    def load_collection(self, *args):
        pass

    def upsert(self, name, rows):
        self.rows = rows

    def flush(self, *args):
        pass


def test_v2_schema_and_rows_persist_native_scope_time_and_fence_fields():
    store = MilvusStore(dim=2)
    client = SchemaClient()
    store._client = client
    store._create_collection()
    assert {field.name for field in client.schema.fields} == V2_FIELDS

    store.ensure_collection = lambda **kwargs: store
    store.upsert([{
        'version_id': 'v1', 'project_id': 'p', 'id': 'r', 'kind': 'chunk',
        'text': 'rule', 'embedding': [1, 0],
        'metadata': {'applicability': {'platforms': ['美团']}},
        'valid_from': '2025-01-01T00:00:00Z', 'valid_until': None,
    }], projection_seq=7, fence='claim')
    row = client.rows[0]
    assert row['platform_mode'] == 'specific'
    assert row['platform_values'] == ['美团']
    assert row['valid_from_epoch_ms'] == 1735689600000
    assert row['valid_until_known'] is False
    assert row['projection_seq'] == 7 and row['fence'] == 'claim'
    assert row['active'] is True


def test_existing_non_v2_collection_is_rejected_for_postgres_fallback():
    class OldClient:
        def has_collection(self, name): return True
        def describe_collection(self, name):
            return {'fields': [{'name': 'embedding', 'params': {'dim': 2}}]}

    store = MilvusStore(dim=2)
    store._client = OldClient()
    with pytest.raises(MilvusSchemaMismatch):
        store.ensure_collection()


class ContractClient:
    def __init__(self):
        self.collections = {}
        self.indexes = {}
        self.rows = {}
        self.aliases = {}
        self.deleted = []
        self.loaded = []
        self.upsert_batches = []

    def has_collection(self, name): return name in self.collections
    def create_collection(self, name, schema):
        self.collections[name] = schema.to_dict()
        self.rows.setdefault(name, [])
    def describe_collection(self, name): return self.collections[name]
    def create_index(self, name, params):
        self.indexes[name] = [row.to_dict() for row in params]
    def list_indexes(self, collection_name):
        return [row['field_name'] for row in self.indexes.get(collection_name, [])]
    def describe_index(self, collection_name, index_name):
        return next(row for row in self.indexes[collection_name]
                    if row['field_name'] == index_name)
    def load_collection(self, name): self.loaded.append(name)
    def flush(self, *args): pass
    def upsert(self, name, rows):
        self.upsert_batches.append((name, len(rows)))
        key = 'marker_id' if name.endswith('__control') else 'version_id'
        values = {str(row[key]): dict(row) for row in self.rows[name]}
        values.update({str(row[key]): dict(row) for row in rows})
        self.rows[name] = list(values.values())
    def query(self, collection_name, filter, output_fields, limit=100,
              offset=0, **kwargs):
        rows = self.rows.get(collection_name, [])
        if output_fields == ['count(*)']:
            return [{'count(*)': len(rows)}]
        return [{field: row.get(field) for field in output_fields}
                for row in rows[offset:offset + limit]]
    def delete(self, name, filter):
        self.deleted.append((name, filter))
        self.rows[name] = []
        return {'delete_count': 1}
    def drop_collection(self, name):
        self.collections.pop(name, None); self.rows.pop(name, None)
    def list_collections(self): return list(self.collections)
    def describe_alias(self, alias):
        if alias not in self.aliases: raise KeyError(alias)
        return {'collection_name': self.aliases[alias]}
    def create_alias(self, collection_name, alias): self.aliases[alias] = collection_name
    def alter_alias(self, collection_name, alias): self.aliases[alias] = collection_name
    def drop_alias(self, alias): self.aliases.pop(alias, None)


def test_physical_schema_indexes_control_audit_and_cleanup_contract():
    client = ContractClient()
    store = MilvusStore(dim=2)
    store._client = client
    store._create_collection()
    assert store._schema_issues(store.collection_name) == []

    destination = store.generation_name('p', 'build')
    store._create_collection(destination)
    store.write_fence_marker('p', destination, 4, 'claim')
    control = store._control_name(destination)
    assert control in client.loaded
    assert {row['field_name'] for row in client.indexes[control]} == {
        'project_id', 'projection_seq'}
    assert store.read_fence_marker('p', destination) == {
        'projection_seq': 4, 'fences': {'claim'}}

    old = {'version_id': 'old', 'project_id': 'p', 'id': 'r',
           'kind': 'chunk', 'text': 'old', 'embedding': [1, 0]}
    current = {**old, 'version_id': 'current', 'text': 'current'}
    store.upsert([old], destination=destination)
    repair_ids = store.authority_repair_ids(
        'p', destination, {'r': 'current'})
    receipt = store.apply_authority_repairs(
        'p', destination, [current], repair_ids, projection_seq=5)
    assert receipt['repaired'] == 1
    assert [row['version_id'] for row in client.rows[destination]] == ['current']
    store.write_fence_marker('p', destination, 5, 'new')
    store.write_fence_marker('p', destination, 4, 'late-old')
    assert store.read_fence_marker('p', destination) == {
        'projection_seq': 5, 'fences': {'new'}}
    projection = ProjectionSync._projection_from_records([current])
    validation = store.validate_destination(
        'p', destination, expected_count=1,
        expected_version_ids=['current'], expected_projection=projection,
        expected_digest=ProjectionSync._expected_digest(projection),
        fence='new')
    assert validation['ok'] is True

    many = [{'record_id': f'r{i}', 'version_id': f'v{i}',
             'projection_seq': 5} for i in range(17001)]
    client.rows[destination] = many
    assert store.authority_repair_ids(
        'p', destination, {f'r{i}': f'v{i}' for i in range(17001)}) == set()

    alias = store.project_alias('p', store.collection_name)
    client.create_alias(destination, alias)
    store.write_fence_marker('p', store.collection_name, 5, 'shared')
    result = store.cleanup_project('p')
    assert result['dropped_generations'] == 1
    assert destination not in client.collections and alias not in client.aliases
    assert any(name == store._control_name(store.collection_name)
               and 'project_id == "p"' in expression
               for name, expression in client.deleted)


def test_authority_repair_stops_mutating_immediately_after_maintenance_loss():
    client = ContractClient()
    store = MilvusStore(dim=2)
    store._client = client
    destination = store.generation_name('p', 'lease')
    store._create_collection(destination)
    records = [
        {'version_id': f'v{i}', 'project_id': 'p', 'id': f'r{i}',
         'kind': 'chunk', 'text': str(i), 'embedding': [1, 0]}
        for i in range(2)]
    checks = iter([True, False])
    with pytest.raises(RuntimeError, match='lease was lost'):
        store.apply_authority_repairs(
            'p', destination, records, {'r0', 'r1'}, projection_seq=2,
            before_write=lambda: next(checks))
    assert len(client.deleted) == 1
    assert client.rows[destination] == []


def test_authority_repair_upserts_in_batches_of_at_most_64():
    client = ContractClient()
    store = MilvusStore(dim=2)
    store._client = client
    destination = store.generation_name('p', 'batch')
    store._create_collection(destination)
    records = [
        {'version_id': f'v{i}', 'project_id': 'p', 'id': f'r{i}',
         'kind': 'chunk', 'text': str(i), 'embedding': [1, 0]}
        for i in range(130)]
    store.apply_authority_repairs(
        'p', destination, records, {f'r{i}' for i in range(130)},
        projection_seq=3, before_write=lambda: True)
    batches = [size for name, size in client.upsert_batches
               if name == destination]
    assert batches == [64, 64, 2]
