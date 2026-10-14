from knowledge_service.models import IngestBatchSettings, IngestCapabilities

def test_batch_settings_reuse_validation():
    import pytest
    with pytest.raises(ValueError): IngestBatchSettings(extraction_mode='documents',chunk_size=100,chunk_overlap=100)
    assert IngestBatchSettings(extraction_mode='documents').requested_concurrency=='auto'


def test_capability_feature_flags_can_be_disabled(monkeypatch):
    from knowledge_service.services.ingest_batches import capabilities
    monkeypatch.setenv('KNOWLEDGE_INGEST_MARKDOWN_PACKAGE','0')
    monkeypatch.setenv('KNOWLEDGE_INGEST_SOURCE_READER','0')
    result=capabilities()
    assert not result.markdown_package and not result.source_reader
    assert '.zip' not in result.supported_extensions
