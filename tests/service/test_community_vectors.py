from unittest.mock import Mock

import pytest

from knowledge_service.integrations.community_vectors import COLLECTION, CommunityVectors
from knowledge_service.integrations.milvus_store import MilvusStore


def test_community_vector_search_uses_current_report_fingerprints_and_full_scope():
    store = MilvusStore(dim=2)
    client = store._client = Mock()
    client.has_collection.return_value = False
    vectors = CommunityVectors(store)
    first = {'id': 'c1', 'fingerprint': 'digest-1', 'embedding': [1.0, 0.0]}
    current = {**first, 'fingerprint': 'digest-2'}

    assert vectors.upsert('p', 'project', 'r1', 'model', [first]) == 1
    assert vectors.upsert('p', 'project', 'r1', 'model', [current]) == 1
    assert vectors.upsert('p', 'project', 'r1', 'model', [current]) == 1
    keys = [call.args[1][0]['vector_id'] for call in client.upsert.call_args_list]
    assert keys[0] != keys[1] == keys[2]
    create = client.create_collection.call_args
    assert create.args[0] == COLLECTION != store.collection_name
    assert {column.name for column in create.kwargs['schema'].fields} == {
        'vector_id', 'project_id', 'scope_key', 'revision', 'embedding_model',
        'community_id', 'fingerprint', 'embedding',
    }

    client.search.return_value = [[{
        'entity': {'community_id': 'c1', 'fingerprint': 'digest-2'}, 'distance': 0.9,
    }]]
    assert vectors.search('p', 'project', 'r1', 'model', [1.0, 0.0], 8,
                          expected={'c1': 'digest-2'}) == [
        {'id': 'c1', 'fingerprint': 'digest-2', 'score': 0.9},
    ]
    search = client.search.call_args.kwargs
    assert search['collection_name'] == COLLECTION
    assert search['filter_params'] == {
        'project_id': 'p', 'scope_key': 'project', 'revision': 'r1',
        'embedding_model': 'model', 'report_keys': [keys[1]],
    }
    assert search['filter'] == (
        'project_id == {project_id} and scope_key == {scope_key} and revision == {revision} '
        'and embedding_model == {embedding_model} and vector_id in {report_keys}'
    )

    client.has_collection.return_value = True
    client.delete.return_value = {'delete_count': 1}
    assert vectors.discard('p', 'project', 'r0', 'old-model') == 1
    assert client.delete.call_args.kwargs['filter_params'] == {
        'project_id': 'p', 'scope_key': 'project', 'revision': 'r0', 'embedding_model': 'old-model',
    }


def test_community_vector_reads_never_create_indexes_or_write_on_invalid_inputs():
    store = MilvusStore(dim=2)
    client = store._client = Mock()
    client.has_collection.return_value = False
    vectors = CommunityVectors(store)
    assert vectors.search('p', 'project', 'r1', 'model', [1.0, 0.0], 8) == []
    assert vectors.discard('p', 'project', 'r1', 'model') == 0
    with pytest.raises(ValueError, match='向量'):
        vectors.upsert('p', 'project', 'r1', 'model', [
            {'id': 'c1', 'fingerprint': 'digest', 'embedding': [1.0]},
        ])
    client.create_collection.assert_not_called()
    client.upsert.assert_not_called()
    client.delete.assert_not_called()


def test_deleted_projects_clean_community_vectors_even_when_fact_collection_is_absent():
    store = MilvusStore(dim=2)
    client = store._client = Mock()
    client.has_collection.side_effect = lambda name, **_: name == COLLECTION
    client.delete.return_value = {'delete_count': 3}
    assert store.cleanup_project('p') == {'deleted': 3}
    assert client.delete.call_args.args == (COLLECTION,)
    assert client.delete.call_args.kwargs['filter'] == 'project_id == {project_id}'
    assert client.delete.call_args.kwargs['filter_params'] == {'project_id': 'p'}
