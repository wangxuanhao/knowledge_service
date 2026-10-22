"""Selected-project model rebuilds use generations in one fixed-dimension index."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from knowledge_service.integrations.milvus_store import COLLECTION, VectorDimensionMismatch
from knowledge_service.repository.core import OntologyNotPublished, Repository
from knowledge_service.services.job_handlers import job_handlers
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync
from test_shared_milvus_pipeline import MemoryMilvus


def handler(repo, store, encoder):
    # A fresh coordinator must recover from the persisted PG checkpoint.
    sync = ProjectionSync(repo.projections, {
        'milvus': MilvusProjectionAdapter(store, encoder=encoder),
    }, schema_version=3)
    service = SimpleNamespace(data=repo, encoder=encoder,
                              reconnect_vector_store=lambda: store, projection_sync=sync)
    return job_handlers(service, None)['semantic_index']


def setup_indexes(repo, store, projects):
    original_encoder = SimpleNamespace(identity='original-model', dimension=2,
                                      encode=lambda texts: [[1., 0.] for _ in texts])
    for project in projects:
        repo.put_record(project, {'id': project+'-chunk', 'kind': 'chunk', 'text': project})
        assert handler(repo, store, original_encoder)({}, project, lambda *_: None, 'initial')['index_version'] == 1


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('unpublished', [False, True])
def test_model_change_rebuilds_only_selected_project_generation(tmp_path, interrupt, unpublished):
    repo = Repository(tmp_path / 'generation-rebuild')
    first, other = [repo.create_project(name)['id'] for name in ('selected', 'untouched')]
    if not unpublished:
        repo.bootstrap_ontology(other, '', {'classes': [], 'relations': [], 'attributes': []})
    else:
        with pytest.raises(OntologyNotPublished):
            repo.get_ontology(other)
    store = MemoryMilvus()
    setup_indexes(repo, store, (first, other))
    before_rows = deepcopy(store.rows)
    other_cursor = deepcopy(repo.projections.cursor(other, 'milvus'))
    before_preparations = len(store.creations)
    interrupted = False

    def encode(texts):
        nonlocal interrupted
        # A clean interruption precedes the RPC; its generation can resume.
        # Unknown/late writes have separate explicit rebuild and cleanup tests.
        if interrupt and not interrupted:
            interrupted = True
            raise RuntimeError('simulated restart before vector RPC')
        return [[0., 1.] for _ in texts]

    encoder = SimpleNamespace(identity='replacement-model', dimension=2, encode=Mock(side_effect=encode))
    failed_build = None
    if interrupt:
        with pytest.raises(RuntimeError, match='simulated restart'):
            handler(repo, store, encoder)({}, first, lambda *_: None, 'replacement')
        failed_state = repo.projections.cursor(first, 'milvus')['rebuild_state']
        failed_build = failed_state['build_id']
        assert failed_state['index_version'] == 2
        assert store.rows == before_rows, 'a failed replacement changed active rows'
        assert not repo.projection_ready(first, 'milvus', 3)
        assert repo.projections.cursor(other, 'milvus') == other_cursor

    result = handler(repo, store, encoder)({}, first, lambda *_: None, 'replacement')
    assert result['scope'] == 'project' and result['restored_projects'] == [first]
    assert result['destination'] == COLLECTION and result['recreated'] is False
    assert result['index_version'] == 2 and result['indexed'] == 1
    assert result['dim'] == 2 and result['embedding_model'] == 'replacement-model'
    current = repo.projections.cursor(first, 'milvus')['rebuild_state']
    if failed_build:
        assert current['build_id'] == failed_build, 'a clean retry started a different build'
    active = [row for row in store.rows.values()
              if row['project_id'] == first and row['index_version'] == result['index_version']]
    assert len(active) == 1 and active[0]['embedding'] == [0., 1.]
    assert repo.projections.receipts.count(first, 2) == 1
    assert repo.projections.search_index(first, 3)['embedding_model'] == 'replacement-model'
    assert repo.projection_ready(first, 'milvus', 3)
    assert repo.projection_ready(other, 'milvus', 3)
    assert repo.projections.cursor(other, 'milvus') == other_cursor
    assert {key: row for key, row in store.rows.items() if row['project_id'] == other} == {
        key: row for key, row in before_rows.items() if row['project_id'] == other}
    assert all(project == first and generation == 2
               for project, generation, _catalog in store.creations[before_preparations:])
    retired = repo._db.execute('SELECT index_version FROM milvus_generation_cleanup_outbox '
                              'WHERE project_id=?', (first,)).fetchall()
    assert [row['index_version'] for row in retired] == [1]
    repo.close()


def test_dimension_conflict_cannot_recreate_shared_index_or_change_either_project(tmp_path):
    repo = Repository(tmp_path / 'dimension-conflict')
    projects = [repo.create_project(name)['id'] for name in ('selected', 'untouched')]
    store = MemoryMilvus()
    setup_indexes(repo, store, projects)
    before_rows, before_preparations = deepcopy(store.rows), deepcopy(store.creations)
    before_cursors = [deepcopy(repo.projections.cursor(project, 'milvus')) for project in projects]
    encoder = SimpleNamespace(identity='wrong-dimension-model', dimension=3, encode=Mock())
    with pytest.raises(VectorDimensionMismatch):
        handler(repo, store, encoder)({}, projects[0], lambda *_: None, 'invalid')
    encoder.encode.assert_not_called()
    assert store.rows == before_rows and store.creations == before_preparations
    assert [repo.projections.cursor(project, 'milvus') for project in projects] == before_cursors
    assert all(repo.projections.receipts.count(project, 1) == 1 for project in projects)
    assert all(repo.projection_ready(project, 'milvus', 3) for project in projects)
    repo.close()
