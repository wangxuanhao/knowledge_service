"""Explicit builds and native validation keep manifests in paged PG receipts."""
from types import SimpleNamespace
from unittest.mock import Mock

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection, projection_fingerprint
from knowledge_service.integrations.milvus_store import MilvusStore
from knowledge_service.repository.core import Repository
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync

from test_shared_milvus_pipeline import MemoryMilvus


def test_explicit_build_uses_receipts_without_persisting_whole_project_json(tmp_path):
    repo = Repository(tmp_path / 'receipt-build')
    project = repo.create_project('receipt build')['id']
    for index in range(3):
        repo.put_record(project, {'id': f'r{index}', 'kind': 'chunk', 'text': 'body'})
    encoder = SimpleNamespace(identity='model', dimension=2,
                              encode=lambda texts: [[1., 0.] for _ in texts])
    sync = ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(MemoryMilvus(),
        encoder=encoder)}, schema_version=3)
    result = sync.rebuild(project, 'milvus')
    cursor = repo.projections.cursor(project, 'milvus')
    assert 'expected_projection' not in cursor['rebuild_state']
    assert result['indexed'] == repo.projections.receipts.count(project, result['index_version']) == 3


def test_native_validation_looks_up_receipts_in_pages_instead_of_materializing_manifest():
    rows = [build_projection(ProjectionInput({'project_id': 'p', 'id': f'r{index}',
        'version_id': f'v{index}', 'kind': 'chunk', 'text': 'body'}),
        default_catalog(), index_version=2) for index in range(513)]
    expected_rows = {row['record_id']: {'version_id': row['version_id'],
        'fingerprint': projection_fingerprint(row)} for row in rows}
    lookups = []

    class PagedManifest:
        def __len__(self):
            return len(rows)
        def lookup(self, ids):
            lookups.append(ids)
            assert len(ids) <= 512
            return {record_id: expected_rows[record_id] for record_id in ids}
        def __eq__(self, _other):
            raise AssertionError('whole-project manifest comparison is forbidden')

    store = MilvusStore()
    store.check_collection = Mock()
    store._query_pages = Mock(return_value=iter(rows))
    result = store.validate_projection(PagedManifest(), project_id='p', index_version=2,
                                       catalog=default_catalog())
    assert result['ok']
    assert [len(page) for page in lookups] == [512, 1]


def test_preflight_returns_reiterable_rows_instead_of_a_project_sized_list():
    reads = []

    class CapturedInputs:
        def __iter__(self):
            reads.append('snapshot pass')
            for index in range(3):
                yield ProjectionInput({'project_id': 'p', 'id': f'r{index}',
                    'version_id': f'v{index}', 'kind': 'chunk', 'text': 'body'})

    store = Mock(dim=2)
    adapter = MilvusProjectionAdapter(lambda: store,
        encoder=SimpleNamespace(identity='model', dimension=2))
    _, rows = adapter.preflight('p', {'index_version': 2}, CapturedInputs())
    assert not isinstance(rows, list), 'preflight retained the complete project in memory'
    assert len(rows) == 3
    assert reads == ['snapshot pass']
    assert len(list(rows)) == 3
    assert reads == ['snapshot pass', 'snapshot pass']
