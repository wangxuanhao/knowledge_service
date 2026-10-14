import time
from knowledge_service.api import create_app
from knowledge_service.services.document_ingest_runner import DocumentIngestRunner
from knowledge_service.services.ingest_batch_runner import IngestBatchRunner
from knowledge_service.models import IngestBatchSettings


def test_real_documents_batch_provenance_and_lifecycle(tmp_path,monkeypatch):
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.repository.ingest_batch_store import IngestBatchStore
    from knowledge_service.repository.checkpoint_store import CheckpointStore
    from knowledge_service.integrations.embeddings import HashingEncoder
    app=create_app(tmp_path/'runner.sqlite',encoder=HashingEncoder())
    service=app.state.service
    sources=SourceDocumentService(service.repository,SourceObjectStore(tmp_path/'objects'))
    store=IngestBatchStore(service.repository)
    project=service.data.create_project('documents',{'ontology_mode':'documents'})['id']
    batch=store.create_batch(project,IngestBatchSettings(extraction_mode='documents',chunk_size=100,chunk_overlap=20).model_dump())
    original_text='a'*70+'\n\n'+'b'*70
    item=store.add_item(project,batch['id'],'a.md',original_text.encode(),'hash','key',1000,1000)
    store.transition(project,batch['id'],'start')
    runner=IngestBatchRunner(service,store,CheckpointStore(service.repository),sources,document_limit=2,llm_limit=1)
    errors=[]
    original_run=runner.document_runner.run
    def observed(*args,**kwargs):
        try:return original_run(*args,**kwargs)
        except Exception:
            import traceback
            errors.append(traceback.format_exc())
            raise
    runner.document_runner.run=observed
    runner.run_once()
    for _ in range(100):
        row=store.list_items(project,batch['id'])['items'][0]
        if row['status'] in ('succeeded','failed'):break
        time.sleep(.05)
    runner.close()
    assert row['status']=='succeeded','\n'.join(errors)
    doc=row['result']['document']
    assert doc['metadata']['source_document_version_id']
    chunks=[r for r in row['result']['records'] if r['kind']=='chunk']
    assert doc['text']==original_text
    assert [chunk['metadata']['start_char'] for chunk in chunks]==[0,80]
    assert chunks[0]['text'][-20:]==chunks[1]['text'][:20]
    assert sources.locate(project,doc['metadata']['source_document_version_id'],chunks[0]['version_id'])['status']=='available'


def test_real_thirty_chunk_checkpoint_resume_only_remaining(tmp_path,monkeypatch):
    from collections import Counter
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.services.ontology import Ontology
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    from pathlib import Path
    repo=Repository(tmp_path/'resume.sqlite')
    project=repo.create_project('resume')['id']
    ttl=Path('knowledge_service/resources/default_ontology.ttl').read_text(encoding='utf-8')
    repo.bootstrap_ontology(project,ttl,Ontology(ttl).summary(),{'actor':'test'})
    service=KnowledgeService(repo,HashingEncoder())
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    calls=Counter()
    def extract(self,text,ontology):
        index=int(text.split()[0][1:-1]);calls[index]+=1
        if index==27 and calls[index]==1:raise RuntimeError('model_not_found')
        return []
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    settings=IngestBatchSettings(extraction_mode='ontology',chunk_size=200,chunk_overlap=0,chunk_strategy='structural',resolve_entities=False).model_dump()
    batch=repo.ingest_batches.create_batch(project,settings)
    text='\n\n'.join(f'第{i}条 '+'rule '*24 for i in range(30))
    item=repo.ingest_batches.add_item(project,batch['id'],'rules.md',text.encode(),'hash','key',1000,100000)
    repo.ingest_batches.transition(project,batch['id'],'start')
    runner=IngestBatchRunner(service,repo.ingest_batches,repo.ingest_checkpoints,sources)
    runner.run_once()
    for _ in range(200):
        row=repo.ingest_batches.list_items(project,batch['id'])['items'][0]
        if row['status']=='failed':break
        time.sleep(.05)
    assert row['status']=='failed',row
    assert len(repo.ingest_checkpoints.list_for_document(item['document_id'],None))==27
    repo.ingest_batches.retry(project,batch['id'],item['id'])
    runner.run_once()
    for _ in range(200):
        row=repo.ingest_batches.list_items(project,batch['id'])['items'][0]
        if row['status']=='succeeded':break
        time.sleep(.05)
    runner.close()
    assert row['status']=='succeeded',row
    assert all(calls[i]==1 for i in range(27)) and calls[27]==2 and calls[28]==calls[29]==1
    assert len(repo.current_records(project,kinds=['document']))==1
    assert len(repo.current_records(project,kinds=['chunk']))==30


def test_paused_inflight_output_is_saved_and_reused(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext
    repo=Repository(tmp_path/'pause.sqlite');store=repo.ingest_batches
    project=repo.create_project('pause')['id'];batch=store.create_batch(project,{})
    row=store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start');item=store.claim(batch['id'],'worker',60)
    context=DocumentCheckpointContext(store,repo.ingest_checkpoints,item,'worker',None,[{'text':'text'}])
    calls=[]
    def extract():
        calls.append('call');store.transition(project,batch['id'],'pause')
        return {'records':[],'review_candidates':[],'diagnostics':{}}
    context.execute(0,'fingerprint',extract)
    assert repo.ingest_checkpoints.get(row['document_id'],0,'fingerprint')
    store.transition(project,batch['id'],'resume')
    assert store.claim(batch['id'],'next',60) is None
    store.release_paused(item['id'],'worker',item['lease_token'])
    new=store.claim(batch['id'],'next',60)
    resumed=DocumentCheckpointContext(store,repo.ingest_checkpoints,new,'next',None,[{'text':'text'}])
    resumed.execute(0,'fingerprint',extract)
    assert calls==['call']


def test_changed_configuration_requires_explicit_restart(tmp_path):
    import pytest
    from knowledge_service.repository import Repository
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext
    repo=Repository(tmp_path/'incompatible.sqlite');store=repo.ingest_batches
    project=repo.create_project('config')['id'];batch=store.create_batch(project,{})
    row=store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start');item=store.claim(batch['id'],'worker',60)
    context=DocumentCheckpointContext(store,repo.ingest_checkpoints,item,'worker',None,[{'text':'text'}])
    output={'records':[],'review_candidates':[],'diagnostics':{}}
    context.execute(0,'old',lambda:output)
    with pytest.raises(ValueError) as error:context.execute(0,'new',lambda:(_ for _ in ()).throw(AssertionError('LLM must not run')))
    assert error.value.code=='checkpoint_incompatible'
    store.finish(item['id'],'worker',item['lease_token'],'failed')
    store.retry(project,batch['id'],item['id'],'restart_document')
    restarted=store.claim(batch['id'],'worker2',60)
    assert restarted['document_generation']==1
    assert repo.ingest_checkpoints.get(row['document_id'],0,'old')
    context=DocumentCheckpointContext(store,repo.ingest_checkpoints,restarted,'worker2',None,[{'text':'text'}])
    context.execute(0,'new',lambda:output)


def test_failed_only_never_executes_unstarted_chunks(tmp_path):
    import pytest
    from knowledge_service.repository import Repository
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext
    repo=Repository(tmp_path/'failed-only.sqlite');store=repo.ingest_batches
    project=repo.create_project('failed-only')['id'];batch=store.create_batch(project,{})
    row=store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start');item=store.claim(batch['id'],'worker',60)
    store.finish(item['id'],'worker',item['lease_token'],'failed',failed_chunk_ordinal=1)
    store.retry(project,batch['id'],item['id'],'retry_failed_chunks');item=store.claim(batch['id'],'worker',60)
    context=DocumentCheckpointContext(store,repo.ingest_checkpoints,item,'worker',None,[{'text':'a'},{'text':'b'},{'text':'c'}])
    output={'records':[],'review_candidates':[],'diagnostics':{}}
    repo.ingest_checkpoints.put(row['document_id'],0,'fp',output)
    calls=[]
    context.execute(0,'fp',lambda:calls.append(0))
    context.execute(1,'fp',lambda:(calls.append(1) or output))
    with pytest.raises(ValueError) as error:context.execute(2,'fp',lambda:calls.append(2))
    assert error.value.code=='checkpoint_pending' and calls==[1]


def test_shutdown_fences_provider_return_without_waiting_forever(tmp_path,monkeypatch):
    import threading
    from concurrent.futures import wait
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext
    repo=Repository(tmp_path/'shutdown.sqlite');store=repo.ingest_batches
    project=repo.create_project('shutdown')['id'];batch=store.create_batch(project,{})
    item=store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start')
    service=KnowledgeService(repo,HashingEncoder())
    runner=IngestBatchRunner(service,store,repo.ingest_checkpoints,None)
    started=threading.Event();release=threading.Event()
    def process(row,settings,owner):
        context=DocumentCheckpointContext(store,repo.ingest_checkpoints,row,owner,None,[{'text':'text'}],runner.stop)
        def provider():
            started.set();release.wait(3)
            return {'records':[],'review_candidates':[],'diagnostics':{}}
        context.execute(0,'fp',provider)
        return {}
    runner.document_runner.run=process
    monkeypatch.setenv('KNOWLEDGE_INGEST_SHUTDOWN_SECONDS','.1')
    runner.run_once();assert started.wait(3)
    started_at=time.monotonic();runner.close()
    assert time.monotonic()-started_at<1
    assert store.list_items(project,batch['id'])['items'][0]['status']=='interrupted'
    assert repo.ingest_checkpoints.get(item['document_id'],0,'fp') is None
    repo.close();release.set()
    assert not wait([future for future,row in runner.active.values()],timeout=3).not_done


def test_shacl_metadata_cannot_write_after_cancel(tmp_path,monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    repo=Repository(tmp_path/'shacl-fence.sqlite');store=repo.ingest_batches
    project=repo.create_project('fence')['id']
    settings=IngestBatchSettings(extraction_mode='documents').model_dump()
    batch=store.create_batch(project,settings)
    item=store.add_item(project,batch['id'],'a.md',b'Fixed evidence','hash','key',1000,10000)
    store.transition(project,batch['id'],'start')
    service=KnowledgeService(repo,HashingEncoder());sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    runner=IngestBatchRunner(service,store,repo.ingest_checkpoints,sources)
    original=service.write;committed=[]
    def write(*args,**kwargs):
        result=original(*args,**kwargs)
        if kwargs.get('completion'):
            committed.append(repo.get_record(project,item['document_id'])['version'])
            kwargs['shacl_review_out'].append({'record_id':'synthetic','constraint':'test','message':'test'})
            store.transition(project,batch['id'],'cancel')
        return result
    monkeypatch.setattr(service,'write',write)
    runner.run_once()
    future=next(iter(runner.active.values()))[0];future.result(timeout=10)
    assert committed
    document=repo.get_record(project,item['document_id'])
    assert document['version']==committed[0]
    assert document['metadata']['review_candidates']==[]
    assert store.list_items(project,batch['id'])['items'][0]['status']=='cancelled'
    runner.close()


def test_closed_repository_stops_dispatcher_and_fences_late_worker(tmp_path,monkeypatch,caplog):
    import threading
    from concurrent.futures import wait
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    repo=Repository(tmp_path/'closed-runner.sqlite');store=repo.ingest_batches
    project=repo.create_project('closed')['id']
    batch=store.create_batch(project,IngestBatchSettings(extraction_mode='documents').model_dump())
    store.add_item(project,batch['id'],'a.md',b'text','hash','key',1000,10000)
    store.transition(project,batch['id'],'start')
    runner=IngestBatchRunner(KnowledgeService(repo,HashingEncoder()),store,repo.ingest_checkpoints,None)
    entered=threading.Event();release=threading.Event()
    def provider(*args):entered.set();release.wait(5);return {}
    runner.document_runner.run=provider
    runner.start();assert entered.wait(3)
    repo.close()
    def forbidden(*args,**kwargs):raise AssertionError('database operation after close')
    for name in ('heartbeat','recover_expired','running_batches','finish','interrupt_owner','release_paused','lease_valid'):
        monkeypatch.setattr(store,name,forbidden)
    runner.thread.join(timeout=3)
    assert not runner.thread.is_alive()
    assert runner.stop.is_set()
    runner.run_once()
    release.set()
    pending=[future for future,item in runner.active.values()]
    assert not wait(pending,timeout=3).not_done
    assert all(future.exception() is None for future in pending)
    runner.close()
    assert 'Batch dispatcher failed' not in caplog.text
    import pytest
    with pytest.raises(RuntimeError):runner.pool.submit(lambda:None)
