from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_service.integrations.milvus_store import VectorDimensionMismatch
from knowledge_service.repository.core import OntologyNotPublished, Repository
from knowledge_service.services.job_handlers import job_handlers
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('unpublished', [False, True])
def test_dimension_migration_rebuilds_only_selected_project_generation(
        tmp_path, interrupt, unpublished):
    repo = Repository(tmp_path / 'generation-rebuild')
    p1, p2 = [repo.create_project(name)['id'] for name in ('p1', 'p2')]
    if not unpublished:
        repo.bootstrap_ontology(p2, '', {'classes': [], 'relations': [], 'attributes': []})
    for project in (p1, p2):
        repo.put_record(project, {
            'id': project + '-chunk', 'kind': 'chunk', 'text': project,
        })
    if unpublished:
        with pytest.raises(OntologyNotPublished):
            repo.get_ontology(p2)

    class Store:
        dim = 2
        collection_name = 'shared'

        def __init__(self):
            self.collections = {'shared': [
                {'project_id': p1, 'embedding': [1.]},
                {'project_id': p2, 'embedding': [2.]},
            ]}
            self.aliases = {p1: 'shared', p2: 'shared'}
            self.markers = {}
            self.interrupted = False
            self.switches = []

        def ensure_collection(self, destination=None):
            if destination is None or destination == 'shared':
                raise VectorDimensionMismatch('shared', 1, self.dim)
            self.collections.setdefault(destination, [])

        def generation_name(self, project, build_id):
            return f'generation_{project}_{build_id}'

        def recreate_collection(self, destination=None):
            assert destination and destination != 'shared'
            self.collections[destination] = []

        def upsert(self, records, *, destination, flush, projection_seq, fence):
            assert destination != 'shared' and flush and fence
            assert all(row['project_id'] == p1 for row in records)
            if interrupt and not self.interrupted:
                self.interrupted = True
                raise RuntimeError('simulated restart')
            self.collections[destination].extend(deepcopy(records))

        def write_fence_marker(self, project, destination, seq, fence):
            self.markers[destination] = (project, seq, fence)

        def validate_destination(self, project, destination, *, expected_count,
                                 expected_version_ids, expected_projection,
                                 expected_digest, fence):
            records = self.collections[destination]
            projection = ProjectionSync._projection_from_records(records)
            return {'ok': (
                len(records) == expected_count
                and sorted(row['version_id'] for row in records) == sorted(expected_version_ids)
                and projection == expected_projection
                and ProjectionSync._expected_digest(projection) == expected_digest
                and self.markers[destination] == (
                    project, repo.projections.project_watermark(project), fence)
            )}

        def current_destination(self, project):
            return self.aliases[project]

        def switch_alias(self, project, destination):
            previous = self.aliases[project]
            self.aliases[project] = destination
            self.switches.append((project, destination))
            return previous

    store = Store()
    legacy = deepcopy(store.collections['shared'])
    encoder = SimpleNamespace(identity='test-model', dimension=2,
                              encode=lambda texts: [[1., 0.] for _ in texts])

    def handler():
        # A new process-local coordinator must resume the persisted checkpoint.
        sync = ProjectionSync(repo.projections, {
            'milvus': MilvusProjectionAdapter(store, encoder=encoder),
        }, schema_version=3)
        service = SimpleNamespace(data=repo, encoder=encoder,
                                  reconnect_vector_store=lambda: store,
                                  projection_sync=sync)
        return job_handlers(service, None)['semantic_index']

    with pytest.raises(VectorDimensionMismatch):
        store.ensure_collection()
    failed_destination = None
    if interrupt:
        with pytest.raises(RuntimeError, match='simulated restart'):
            handler()({}, p1, lambda *_: None, 'migration')
        failed_destination = repo.projections.cursor(p1, 'milvus')['rebuild_state']['destination']
        assert store.aliases == {p1: 'shared', p2: 'shared'}
        assert not store.switches
        assert not repo.projection_ready(p1, 'milvus', 3)

    result = handler()({}, p1, lambda *_: None, 'migration')
    destination = result['destination']
    if interrupt:
        assert destination == failed_destination
    assert result['scope'] == 'project'
    assert result['restored_projects'] == [p1]
    assert result['indexed'] == 1
    assert store.aliases == {p1: destination, p2: 'shared'}
    assert store.switches == [(p1, destination)]
    assert set(store.collections) == {'shared', destination}
    assert store.collections['shared'] == legacy
    assert store.collections[destination][0]['embedding_model'] == 'test-model'
    assert store.collections[destination][0]['embedding'] == [1., 0.]
    assert repo.projection_ready(p1, 'milvus', 3)
    assert repo.projections.cursor(p2, 'milvus') is None
