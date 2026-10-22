"""Production checks validate real Milvus 3 metadata, without modifying it."""
from unittest.mock import Mock
from copy import deepcopy

import pytest
from pymilvus import DataType

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.milvus_store import MilvusStore, MilvusSchemaMismatch, _TYPES


def production_metadata():
    fields, indexes = [], {}
    for column in default_catalog().column_defs():
        params = {}
        for name in ('max_length', 'max_capacity'):
            if getattr(column, name) is not None:
                params[name] = getattr(column, name)
        if column.name == 'embedding':
            params['dim'] = 1024
        if column.name == 'text':
            params.update(enable_analyzer='true', analyzer_params='{"type":"chinese"}')
        field = {'name': column.name, 'type': _TYPES[column.field_type],
                 'params': params, 'nullable': column.nullable}
        if column.name == 'vector_id':
            field['is_primary'] = True
        if column.name == 'project_id':
            field['is_partition_key'] = True
        if column.field_type == 'array_varchar':
            field['element_type'] = DataType.VARCHAR
        fields.append(field)
        if column.name in {'embedding', 'sparse'} or column.index:
            index_name = ('dense' if column.name == 'embedding' else
                          'bm25' if column.name == 'sparse' else 'filter_' + column.name)
            indexes[index_name] = {'field_name': column.name, 'index_name': index_name,
                'index_type': ('HNSW' if column.name == 'embedding' else
                               'SPARSE_INVERTED_INDEX' if column.name == 'sparse' else 'INVERTED'),
                'state': 'Finished'}
            if column.name in {'embedding', 'sparse'}:
                indexes[index_name]['metric_type'] = 'COSINE' if column.name == 'embedding' else 'BM25'
    return {'auto_id': False, 'enable_dynamic_field': False, 'fields': fields,
            'functions': [{'name': 'text_bm25', 'type': 1,
                'input_field_names': "['text']", 'output_field_names': "['sparse']"}]}, indexes


def field(info, name):
    return next(value for value in info['fields'] if value['name'] == name)


def test_existing_production_sdk_string_metadata_is_accepted():
    info, indexes = production_metadata()
    assert MilvusStore()._schema_issues(default_catalog(), info, indexes) == []


def test_existing_production_protobuf_field_lists_are_accepted():
    from pymilvus.grpc_gen.schema_pb2 import FunctionSchema
    info, indexes = production_metadata()
    native = FunctionSchema(input_field_names=['text'], output_field_names=['sparse'])
    info['functions'][0].update(input_field_names=native.input_field_names,
                                output_field_names=native.output_field_names)
    assert MilvusStore()._schema_issues(default_catalog(), info, indexes) == []
    native.input_field_names[0] = 'version_id'
    assert 'BM25 function differs' in MilvusStore()._schema_issues(default_catalog(), info, indexes)


def test_query_rejects_unfinished_index_without_caching_collection_ready():
    info, indexes = production_metadata()
    indexes['dense']['state'] = 'InProgress'
    store = MilvusStore()
    store._metadata = Mock(return_value=(info, indexes))
    with pytest.raises(RuntimeError, match='索引.*就绪|not ready'):
        store.check_collection(catalog=default_catalog())
    assert not store._validated


def test_pending_sdk_metadata_is_refetched_when_index_finishes_without_sixty_second_delay():
    info, indexes = production_metadata()
    indexes['dense']['state'] = 'InProgress'
    store = MilvusStore()
    store._client = Mock()
    store._client.has_collection.return_value = True
    store._client.describe_collection.return_value = info
    store._client.list_indexes.return_value = list(indexes)
    store._client.describe_index.side_effect = lambda **kwargs: deepcopy(indexes[kwargs['index_name']])
    with pytest.raises(RuntimeError, match='索引.*就绪|not ready'):
        store.check_collection(catalog=default_catalog())
    indexes['dense']['state'] = 'Finished'
    assert store.check_collection(catalog=default_catalog()) is store
    assert store._client.describe_collection.call_count == 2


def test_explicit_build_waits_for_existing_pending_indexes_before_ready():
    info, indexes = production_metadata()
    indexes['dense']['state'] = 'InProgress'
    waited = []
    store = MilvusStore()
    store._client = Mock()
    store._client.has_collection.return_value = True
    store._metadata = Mock(return_value=(info, indexes))
    def wait(names):
        waited.extend(names)
        indexes['dense']['state'] = 'Finished'
    store._wait_for_indexes = wait
    store.ensure_collection(project_id='p', index_version=2, catalog=default_catalog())
    assert waited == ['dense']
    store._client.create_index.assert_not_called()


@pytest.mark.parametrize('corrupt', [
    lambda info, indexes: info['fields'].append({'name': 'test_fixture_leak', 'type': DataType.BOOL}),
    lambda info, indexes: field(info, 'project_id').update(is_partition_key=False),
    lambda info, indexes: info.update(auto_id=True),
    lambda info, indexes: field(info, 'text')['params'].update(analyzer_params='{"type":"english"}'),
    lambda info, indexes: field(info, 'text')['params'].update(enable_analyzer='false'),
    lambda info, indexes: info['functions'][0].update(type=2),
    lambda info, indexes: info['functions'][0].update(input_field_names="['version_id']"),
    lambda info, indexes: indexes['dense'].update(field_name='sparse'),
    lambda info, indexes: indexes['dense'].update(metric_type='NOT_COSINE'),
    lambda info, indexes: indexes['filter_project_id'].update(index_type='HNSW'),
])
def test_incompatible_existing_collection_fails_before_any_mutation(corrupt):
    info, indexes = production_metadata()
    corrupt(info, indexes)
    store = MilvusStore()
    store._client = Mock()
    store._client.has_collection.return_value = True
    store._metadata = Mock(return_value=(info, indexes))
    with pytest.raises(MilvusSchemaMismatch):
        store.ensure_collection(project_id='p', index_version=2, catalog=default_catalog())
    store._client.create_collection.assert_not_called()
    store._client.create_index.assert_not_called()
    store._client.add_collection_field.assert_not_called()
    store._client.drop_collection.assert_not_called()


@pytest.mark.parametrize('mode', ['keyword', 'semantic', 'hybrid'])
def test_pg_eligibility_batch_is_parameterized_inside_native_scope(mode):
    store = MilvusStore(dim=2)
    store._client = Mock()
    store._client.search.return_value = [[]]
    store._client.hybrid_search.return_value = [[]]
    store._load_if_needed = Mock()
    hostile = 'v\" or project_id != \"p'
    store.search('p', 'text', retrieval_mode=mode, query_vector=[1., 0.],
        index_version=2, catalog=default_catalog(), eligible_version_ids=[hostile, 'v2'])
    if mode == 'hybrid':
        requests = store._client.hybrid_search.call_args.kwargs['reqs']
        scopes = [(request.expr, request.expr_params) for request in requests]
    else:
        kwargs = store._client.search.call_args.kwargs
        scopes = [(kwargs['filter'], kwargs['filter_params'])]
    for expression, params in scopes:
        assert 'project_id ==' in expression and 'index_version ==' in expression
        assert 'version_id in' in expression
        assert hostile not in expression
        assert [hostile, 'v2'] in params.values()


def test_empty_pg_eligibility_batch_does_no_native_preparation_or_search():
    store = MilvusStore()
    store._client = Mock()
    assert store.search('p', 'text', retrieval_mode='semantic',
        index_version=2, catalog=default_catalog(), eligible_version_ids=[]) == []
    assert not store._client.mock_calls


@pytest.mark.parametrize('values', [['v'] * 1001, [''], [None], 'v'])
def test_pg_eligibility_batch_rejects_invalid_or_unbounded_values(values):
    store = MilvusStore()
    store._client = Mock()
    with pytest.raises(ValueError):
        store.search('p', 'text', retrieval_mode='keyword', index_version=2,
                     catalog=default_catalog(), eligible_version_ids=values)
    assert not store._client.mock_calls
