from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import projection_fingerprint
from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_sync import OntologySync
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter, ProjectionWorker
from knowledge_service.services.projection_sync import ProjectionSync
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
    # Incremental delivery reconnects a previously built generation; it never
    # builds a never-indexed project as a side effect of an authority write.
    fixture_sync = ProjectionSync(repo.projections, {}, schema_version=3)
    rebuild = repo.projections.begin_rebuild(project, 'milvus', 3, 'existing-empty-index',
        lease_guard=fixture_sync._rebuild_lease_guard,
        embedding_model=service.encoder.identity, dim=service.encoder.dimension)
    state = {**rebuild['state'], 'catalog': default_catalog().to_dict()}
    with repo.projections.activation_guard(project, rebuild['claim'], 3) as watermark:
        assert watermark == rebuild['snapshot_seq']
        repo.projections.activate_index(rebuild['claim'], 3, watermark, state)
    index = repo.projections.search_index(project, 3)
    assert index['embedding_model'] == service.encoder.identity
    assert index['dim'] == service.encoder.dimension
    assert repo.projections.receipts.count(project, index['index_version']) == 0
    written, write_scopes = [], []

    class Store:
        available = True
        dim = service.encoder.dimension

        def ensure_collection(self, **_options):
            raise AssertionError('incremental delivery must not create a collection')

        def check_collection(self, *, catalog):
            assert catalog == default_catalog()
            return self

        def upsert_rows(self, rows, *, project_id, index_version, catalog):
            assert (project_id, index_version) == (project, index['index_version'])
            assert catalog == default_catalog()
            write_scopes.append((project_id, index_version))
            written.extend(dict(row) for row in rows)
            return len(rows)

    # Startup failed, but the next durable attempt can establish a connection.
    service.vector_connector = Store
    adapter = MilvusProjectionAdapter(
        lambda: service.milvus_store, encoder=service.encoder)
    service.projection_worker = ProjectionWorker(
        repo, ProjectionSync(repo.projections, {'milvus': adapter}, schema_version=3),
        cleanup_handlers={})
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'text': '原文'})
    worker = OntologySync(service, configured=True)
    task = worker.repair(project)
    assert service.milvus_store is None
    assert not repo.projection_ready(project, 'milvus', 3)
    assert not worker.reconcile(project)['ready']
    assert worker.run_once()
    assert service.milvus_store is not None
    result = repo.get_artifact('ontology_sync_job', task['id'])
    assert result['status'] == 'completed', result
    assert [r['record_id'] for r in written] == ['chunk']
    assert write_scopes == [(project, index['index_version'])]
    assert written[0]['version_id'] == repo.get_record(project, 'chunk')['version_id']
    assert len(written[0]['embedding']) == service.encoder.dimension
    cursor = repo.projections.cursor(project, 'milvus')
    assert cursor['rebuild_state']['embedding_model'] == service.encoder.identity
    assert cursor['rebuild_state']['dim'] == service.encoder.dimension
    assert cursor['applied_seq'] == repo.projections.project_watermark(project)
    assert repo.projections.receipts.count(project, index['index_version']) == 1
    assert repo.projections.receipts.for_record_ids(project, index['index_version'], ['chunk']) == {
        'chunk': {'version_id': written[0]['version_id'],
                  'fingerprint': projection_fingerprint(written[0])}}
    assert repo.projection_ready(project, 'milvus', 3)
    assert worker.reconcile(project)['ready']
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'text': 'new source'},
                    expected_version=1)
    assert not repo.projection_ready(project, 'milvus', 3)
    assert not worker.reconcile(project)['ready']


def test_worker_return_without_reaching_projection_watermark_cannot_complete(tmp_path):
    repo, service, project = system(tmp_path)
    drained = []

    class Store:
        available = True

    class IncompleteWorker:
        def drain_project(self, project_id):
            drained.append(project_id)
            return 0

    service.vector_connector = Store
    service.projection_worker = IncompleteWorker()
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'text': '原文'})
    worker = OntologySync(service, configured=True)
    task = worker.repair(project)

    assert worker.run_once()

    result = repo.get_artifact('ontology_sync_job', task['id'])
    assert drained == [project]
    assert result['status'] == 'retry_wait'
    assert 'watermark' in result['error']
    assert not repo.projection_ready(project, 'milvus', 3)
    assert not worker.reconcile(project)['ready']
