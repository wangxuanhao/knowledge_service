"""Current Milvus schema is fixed; arbitrary metadata stays in PostgreSQL."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymilvus import DataType

from knowledge_service.domain.search_filter_catalog import (
    FieldSpec, default_catalog, discover_page_catalog,
)
from knowledge_service.domain.search_projection import (
    ProjectionInput, build_projection, projection_fingerprint,
)
from knowledge_service.integrations.milvus_store import MilvusStore, MilvusSchemaMismatch
from knowledge_service.repository.projection_store import milvus_delivery_state
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.runtime_capabilities import RuntimeCapabilities


def chunk(**changes):
    return {'project_id': 'p', 'id': 'c', 'version_id': 'v',
            'kind': 'chunk', 'text': '设备温度', **changes}


def test_project_catalog_does_not_scan_or_register_metadata():
    def unbounded_metadata():
        raise AssertionError('Fixed schema must not inspect project metadata')
        yield
    assert discover_page_catalog(unbounded_metadata()) == default_catalog()
    assert len(default_catalog().column_defs()) == 21
    assert not default_catalog().fields


def test_business_field_declaration_requires_an_explicit_readable_name():
    with pytest.raises(ValueError, match='列名'):
        FieldSpec('metadata.department', 'varchar')


def test_unused_metadata_changes_neither_projection_columns_nor_fingerprint():
    plain = build_projection(ProjectionInput(chunk()), default_catalog(), index_version=1)
    changed = build_projection(ProjectionInput(chunk(metadata={
        'model': 'internal', 'synthetic_fixture': True,
        'ontology_slice': {'debug': [object()]},
        'department': '采购部',
    })), default_catalog(), index_version=1)
    assert changed == plain
    assert projection_fingerprint(changed) == projection_fingerprint(plain)
    assert not any(name.startswith('bf_') for name in changed)


def test_explicit_build_uses_fixed_catalog_for_arbitrary_metadata():
    store = Mock(dim=2)
    encoder = SimpleNamespace(identity='model', dimension=2)
    adapter = MilvusProjectionAdapter(lambda: store, encoder=encoder)
    state = {'index_version': 1}
    catalog, rows = adapter.preflight('p', state, [ProjectionInput(chunk(metadata={
        'department': {'arbitrary_nested_data': object()},
    }))])
    assert catalog == default_catalog()
    assert len(rows) == 1
    assert not catalog.fields
    store.ensure_collection.assert_called_once_with(
        project_id='p', index_version=1, catalog=default_catalog())


def test_old_dynamic_catalog_requires_explicit_project_build():
    cursor = {'status': 'ready', 'schema_version': 3, 'applied_seq': 4,
              'active_destination': 'knowledge_vectors', 'rebuild_state': {
                  'phase': 'ready', 'index_version': 1,
                  'catalog': {'revision': 1, 'fields': [{'path': 'metadata.title',
                      'field_type': 'varchar', 'column_name': 'bf_old'}]}}}
    assert milvus_delivery_state(cursor, 3) == 'requires_rebuild'


def test_health_probe_reads_actual_dimension_without_preparing_collection():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store._client.has_collection.return_value = True
    store._client.describe_collection.return_value = {
        'fields': [{'name': 'embedding', 'type': DataType.FLOAT_VECTOR,
                    'params': {'dim': 2}}]}
    encoder = SimpleNamespace(identity='model', dimension=2, semantic=True,
                              runtime_ready=True)
    report = RuntimeCapabilities(encoder=encoder, milvus_store=store,
                                 milvus_configured=True).snapshot()
    assert report['vector']['collection_ready']
    assert report['vector']['dimension'] == 2
    assert report['vector']['error'] is None
    store._client.create_collection.assert_not_called()
    store._client.create_index.assert_not_called()
    store._client.load_collection.assert_not_called()
    store._client.upsert.assert_not_called()


def test_ordinary_upsert_does_not_force_segment_flush():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store.check_collection = Mock()
    row = build_projection(ProjectionInput(chunk()), default_catalog(), index_version=1)
    row['embedding'] = [1., 0.]
    assert store.upsert_rows([row], project_id='p', index_version=1,
                             catalog=default_catalog()) == 1
    store._client.upsert.assert_called_once()
    store._client.flush.assert_not_called()


def test_strong_validation_does_not_require_segment_flush():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store.check_collection = Mock()
    store._query_pages = Mock(return_value=iter([]))
    assert store.validate_projection({}, project_id='p', index_version=1,
                                      catalog=default_catalog())['ok']
    store._client.flush.assert_not_called()


def test_source_field_means_chunk_owner_not_entity_assertion_source():
    entity = build_projection(ProjectionInput(chunk(kind='entity', source_id='doc')),
                              default_catalog(), index_version=1)
    segment = build_projection(ProjectionInput(chunk(source_id='doc')),
                               default_catalog(), index_version=1)
    assert entity['chunk_owner_id'] is None
    assert 'source_id' not in entity['filter_present']
    assert segment['chunk_owner_id'] == 'doc'


def test_project_preparation_never_creates_missing_business_columns():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store._client.has_collection.return_value = True
    store._metadata = Mock(return_value=({'fields': []}, {}))
    with pytest.raises(MilvusSchemaMismatch):
        store.ensure_collection(project_id='p', index_version=1, catalog=default_catalog())
    store._client.add_collection_field.assert_not_called()


def test_initial_index_creation_submits_without_twenty_second_sync_wait():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store._wait_for_indexes = Mock()
    store._create_collection(catalog=default_catalog())
    assert store._client.create_index.call_args.kwargs.get('sync') is False
    store._wait_for_indexes.assert_called_once()


def test_missing_collection_rejects_incremental_before_query_model_encoding():
    store = MilvusStore(dim=2)
    store._client = Mock()
    store._client.has_collection.return_value = False
    encoder = SimpleNamespace(identity='model', encode=Mock(return_value=[[1., 0.]]))
    adapter = MilvusProjectionAdapter(lambda: store, encoder=encoder)
    event = {'seq': 1, 'operation': 'upsert', 'payload': {'_milvus_projection': {
        'index_version': 1, 'catalog_fingerprint': default_catalog().fingerprint,
        'embedding_model': 'model',
        'row': build_projection(ProjectionInput(chunk()), default_catalog(), index_version=1)}}}
    with pytest.raises(MilvusSchemaMismatch):
        adapter.apply('p', event, fence='owned', index_version=1, catalog=default_catalog())
    encoder.encode.assert_not_called()
    store._client.create_collection.assert_not_called()


@pytest.mark.parametrize('lease_checks', [(False,), (True, False)])
def test_false_rebuild_lease_never_delivers_encoded_rows(lease_checks):
    from knowledge_service.repository.projection_store import ProjectionLeaseLost
    store = Mock()
    adapter = MilvusProjectionAdapter(lambda: store,
        encoder=SimpleNamespace(identity='model', encode=Mock(return_value=[[1., 0.]])))
    row = build_projection(ProjectionInput(chunk()), default_catalog(), index_version=1)
    checks = iter(lease_checks)
    with pytest.raises(ProjectionLeaseLost):
        adapter.load_rows('p', [row], {'index_version': 1}, default_catalog(),
                          lease_is_current=lambda: next(checks, False))
    store.upsert_rows.assert_not_called()
    if lease_checks == (False,):
        adapter._encoder.encode.assert_not_called()
