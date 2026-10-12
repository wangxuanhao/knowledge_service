from contextlib import nullcontext
from types import SimpleNamespace
import pytest

from knowledge_service.integrations.milvus_store import VectorDimensionMismatch
from knowledge_service.services.job_handlers import job_handlers


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('unpublished', [False, True])
def test_dimension_migration_rehydrates_every_project_in_shared_collection(interrupt, unpublished):
    rows = {p: [{'id': p + '-chunk', 'kind': 'chunk', 'text': p,
                 'version_id': p + '-v1', 'project_id': p}] for p in ('p1','p2')}
    artifacts = {}

    class Data:
        def list_projects(self):
            return [{'id': p} for p in rows]

        def current_records(self, p, **kwargs):
            return [dict(r) for r in rows[p]]

        def get_ontology(self, p):
            if unpublished and p == 'p2':
                from knowledge_service.repository.core import OntologyNotPublished
                raise OntologyNotPublished(p)
            return {'id': p + '-ontology'}

        def list_artifacts(self, kind):
            return [v for (k, _), v in artifacts.items() if k == kind]

        def get_artifact(self, kind, key):
            if (kind, key) not in artifacts:
                raise KeyError(key)
            return artifacts[(kind, key)]

        def save_artifact(self, kind, value):
            artifacts[(kind, value['id'])] = value

        def unit_of_work(self):
            return nullcontext()

    class Store:
        dim = 2
        stored = {'p2': ['old vector']}
        migrated = False
        interrupted = False

        def ensure_collection(self):
            if not self.migrated:
                raise VectorDimensionMismatch('shared', 1, 2)

        def recreate_collection(self):
            self.stored.clear()
            self.migrated = True

        def rebuild_project(self, p, encoded):
            if interrupt and p == 'p2' and not self.interrupted:
                self.interrupted = True
                raise RuntimeError('simulated restart')
            self.stored[p] = list(encoded)
            return len(encoded)

    store = Store()
    encoder = SimpleNamespace(identity='test-model', dimension=2,
                              encode=lambda texts: [[1., 0.] for _ in texts])
    service = SimpleNamespace(data=Data(), encoder=encoder,
                              reconnect_vector_store=lambda: store)
    handler = job_handlers(service, None)['semantic_index']
    if interrupt:
        with pytest.raises(RuntimeError, match='simulated restart'):
            handler({}, 'p1', lambda *_: None, 'migration')
        assert artifacts[('ontology_index_state','ontology-index:p2')]['status'] == 'rebuilding'
    result = handler({}, 'p1', lambda *_: None, 'migration')
    assert set(store.stored) == {'p1','p2'}
    assert store.stored['p2'][0]['embedding_model'] == 'test-model'
    assert result['restored_projects'] == ['p1','p2']
    assert artifacts[('ontology_index_state','ontology-index:p2')]['status'] == 'completed'
