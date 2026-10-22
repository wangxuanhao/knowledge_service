from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.runtime_capabilities import RuntimeCapabilities


class ReadyStore:
    available = True
    dim = 256
    collection_name = 'knowledge_records'

    def collection_dim(self):
        return 256


def test_snapshot_reports_encoder_and_ready_milvus_without_secrets():
    report = RuntimeCapabilities(
        encoder=HashingEncoder(), milvus_store=ReadyStore(),
        milvus_configured=True,
    ).snapshot()

    assert report['status'] == 'degraded'
    assert report['capability_matrix']['semantic']['status'] == 'unavailable'
    assert report['embedding'] == {
        'backend': 'demo',
        'identity': HashingEncoder.identity,
        'semantic': False,
        'dimension': 256,
        'dimension_source': 'built-in demo encoder',
        'ready': True,
    }
    assert report['vector'] == {
        'backend': 'milvus',
        'configured': True,
        'connected': True,
        'collection': 'knowledge_records',
        'collection_ready': True,
        'dimension': 256,
        'expected_dimension': 256,
        'error': None,
    }


def test_configured_but_unavailable_milvus_is_degraded_and_redacted():
    report = RuntimeCapabilities(
        encoder=HashingEncoder(), milvus_store=None,
        milvus_configured=True,
        milvus_error='connect failed api_key=super-secret',
    ).snapshot()

    assert report['status'] == 'degraded'
    assert report['vector']['configured'] is True
    assert report['vector']['connected'] is False
    assert report['vector']['collection_ready'] is False
    assert 'super-secret' not in report['vector']['error']


def test_collection_dimension_mismatch_is_degraded():
    store = ReadyStore()
    store.dim = 1024
    report = RuntimeCapabilities(
        encoder=HashingEncoder(), milvus_store=store,
        milvus_configured=True,
    ).snapshot()

    assert report['status'] == 'degraded'
    assert report['vector']['dimension'] == 256
    assert report['vector']['expected_dimension'] == 1024
    assert '维度' in report['vector']['error']
