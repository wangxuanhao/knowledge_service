from knowledge_service.services.evidence import _source_location


def test_legacy_evidence_never_uses_latest_source():
    class Repo:
        def get_record(self, *args):
            raise AssertionError('Must use frozen document')
    assert _source_location(Repo(), 'p', {'metadata': {}}, {}) == {
        'code': 'source_mapping_unavailable'}


def test_source_location_uses_frozen_version_and_chunk(monkeypatch):
    calls = []
    class Store:
        def __init__(self, repo):
            pass
        def locate(self, project_id, version_id, chunk_version_id=None, **kwargs):
            calls.append((project_id, version_id, chunk_version_id))
            return {'document_version_id': version_id, 'status': 'available', 'spans': []}
    monkeypatch.setattr('knowledge_service.repository.source_store.SourceStore', Store)
    result = _source_location(object(), 'p', {'metadata': {'source_document_version_id': 'frozen-v'}},
                              {'version_id': 'chunk-v'})
    assert calls == [('p', 'frozen-v', 'chunk-v')]
    assert result['document_version_id'] == 'frozen-v'
