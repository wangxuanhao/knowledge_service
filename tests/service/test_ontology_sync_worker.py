from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_sync import OntologySync
from knowledge_service.integrations.embeddings import HashingEncoder


def system(tmp_path):
    repo = Repository(tmp_path / 'sync.sqlite')
    project = repo.create_project('sync')['id']
    ttl = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Person> a owl:Class .'
    repo.bootstrap_ontology(project, ttl, Ontology(ttl).summary())
    return repo, KnowledgeService(repo, HashingEncoder()), project


def test_disabled_vector_backend_never_reports_completed(tmp_path):
    repo, service, project = system(tmp_path)
    worker = OntologySync(service, configured=False)
    task = worker.repair(project)
    assert worker.run_once()
    result = repo.get_artifact('ontology_sync_job', task['id'])
    assert result['status'] == 'disabled'
    assert result['error']
    assert not worker.reconcile(project)['ready']


def test_unavailable_backend_records_error_and_bounded_retry(tmp_path):
    repo, service, project = system(tmp_path)
    worker = OntologySync(service, configured=True)
    task = worker.repair(project)
    for attempt in range(1, 6):
        assert worker.run_once()
        current = repo.get_artifact('ontology_sync_job', task['id'])
        assert current['attempts'] == attempt
        assert current['error']
        assert current['status'] == ('failed' if attempt == 5 else 'retry_wait')
        if attempt < 5:
            repo.save_artifact('ontology_sync_job', {**current, 'next_attempt_at': ''})
    assert not worker.run_once()


def test_index_is_ready_only_after_successful_worker_write(tmp_path):
    repo, service, project = system(tmp_path)
    written = []

    class Store:
        available = True

        def ensure_collection(self):
            pass

        def upsert(self, rows, flush=True):
            written.extend(rows)

    # Startup failed, but the next durable attempt can establish a connection.
    service.vector_connector = Store
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'text': '原文'})
    worker = OntologySync(service, configured=True)
    task = worker.repair(project)
    assert not worker.reconcile(project)['ready']
    assert worker.run_once()
    assert service.milvus_store is not None
    assert repo.get_artifact('ontology_sync_job', task['id'])['status'] == 'completed'
    assert [r['id'] for r in written] == ['chunk']
    assert worker.reconcile(project)['ready']
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'text': 'new source'},
                    expected_version=1)
    assert not worker.reconcile(project)['ready']
