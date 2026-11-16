"""Actual parse/publish boundaries with deterministic encoder and Milvus adapter."""
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import build_projections
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository.projection_inputs import ProjectionInputs
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from test_file_preparations import upload, ready


@pytest.mark.parametrize('package', [False, True])
def test_parse_precomputes_but_only_publish_creates_formal_records_and_reuses_vectors(tmp_path, package):
    encoder = HashingEncoder()
    encoder.semantic = True
    encoder.encode = Mock(wraps=encoder.encode)
    with TestClient(create_app(tmp_path/'db',encoder=encoder,upload_temp=tmp_path/'uploads')) as client:
        project = client.post('/api/projects',json={'name':'索引流程','ontology_mode':'documents'}).json()['id']
        base = f'/api/projects/{project}'
        session = client.post(base+'/preparations',json={'defaults':{'extraction_mode':'documents'}}).json()['id']
        root = base+'/preparations/'+session
        data = '# 退款规则\n\n申请退款应核对办理平台。'.encode()
        name = 'rules.md'
        if package:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive,'w') as target:target.writestr('main.md',data)
            data,name = archive.getvalue(),'rules.zip'
        item = ready(client,root,upload(client,root,name,data))
        assert item['vectorization']['status']=='ready'
        service = client.app.state.service
        assert service.repository.current_records(project)==[]
        calls = encoder.encode.call_count
        result = client.post(root+'/publish',json={'item_ids':[item['id']],
            'versions':{item['id']:item['metadata_version']},
            'result_versions':{item['id']:item['chunk_version']},'idempotency_key':'publish'}).json()
        import time
        for _ in range(200):
            batch = client.get(base+'/ingest-batches/'+result['batch_id']).json()
            if batch['status'].startswith('completed'):break
            time.sleep(.03)
        assert batch['status'].startswith('completed'),batch
        rows = [row for values in ProjectionInputs(service.repository).batches(project) for value in values
            for row in build_projections(value,default_catalog(),index_version=1)]
        assert rows
        adapter = MilvusProjectionAdapter(SimpleNamespace(),encoder=service.document_encoder)
        adapter._encode(rows)
        assert encoder.encode.call_count==calls, 'formal projection must reuse the exact prepared text, including ZIP main filename'


def test_index_status_reports_actual_receipts_and_queued_task(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.api_workflows import ApplicationData
    from knowledge_service.services.jobs import Jobs
    from knowledge_service.services.projection_sync import ProjectionSync
    from test_shared_milvus_pipeline import MemoryMilvus
    repo = Repository(tmp_path)
    project = repo.create_project('索引状态')['id']
    repo.put_record(project, {'id':'c','kind':'chunk','text':'退款'})
    adapter = MilvusProjectionAdapter(MemoryMilvus(), encoder=SimpleNamespace(identity='m',dimension=2,encode=lambda texts:[[1.,0.] for _ in texts]))
    sync = ProjectionSync(repo.projections,{'milvus':adapter},schema_version=3)
    jobs = Jobs(repo,handlers={'semantic_index':lambda *args:None})
    jobs.start=Mock()
    job=jobs.enqueue_index(project)
    status=ApplicationData(repo).projection_status(project,sync)
    assert status['state']=='building' and status['job']['id']==job['id']
    sync.rebuild(project,'milvus')
    job.update(status='completed');repo.save_artifact('job',job)
    status=ApplicationData(repo).projection_status(project,sync)
    assert status['state']=='ready'
    assert status['record_counts']['chunk']==status['indexed_record_counts']['chunk']==1
    jobs.close();repo.close()
