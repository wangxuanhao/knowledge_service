from knowledge_service.services.ingest_retry import classify_error, retry_delay
from knowledge_service.services.ingest_checkpoints import checkpoint_fingerprint, IngestCheckpoints


def test_public_errors_and_backoff():
    error = classify_error(RuntimeError('HTTP 429 token=sk-secret https://private/provider'))
    assert error.retryable and error.code == 'llm_rate_limited'
    assert 'secret' not in error.message and 'private' not in error.message
    assert [retry_delay(i, jitter=lambda: 0) for i in range(5)] == [5,15,45,120,300]
    assert not classify_error(RuntimeError('401 unauthorized')).retryable
    assert not classify_error(RuntimeError('ocr_low_confidence')).retryable


def test_fingerprint_stable_and_invalidation():
    assert checkpoint_fingerprint(settings={'b':2,'a':1}) == checkpoint_fingerprint(settings={'a':1,'b':2})
    assert checkpoint_fingerprint(model='a') != checkpoint_fingerprint(model='b')


def test_raw_checkpoints_resume_only_incomplete_chunks():
    class Store:
        def __init__(self): self.rows={}
        def get(self,d,o,f): return self.rows.get((d,o,f))
        def put(self,d,o,f,output,**kwargs): self.rows[d,o,f]={'output':output}
    store=Store(); manager=IngestCheckpoints(store); calls=[]
    def extract(i):
        calls.append(i)
        if i==27 and calls.count(i)==1: raise TimeoutError('temporary')
        return {'records':[{'id':str(i),'kind':'entity'}], 'review_candidates':[]}
    import pytest
    with pytest.raises(TimeoutError):
        for i in range(30): manager.execute('doc',i,'fp',lambda i=i:extract(i))
    for i in range(30): manager.execute('doc',i,'fp',lambda i=i:extract(i))
    assert calls[:27] == list(range(27))
    assert calls[28:] == [27,28,29]


def test_execution_configuration_changes_are_fenced_before_formal_commit(monkeypatch):
    import pytest
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext
    class Store:
        def lease_valid(self,*args):return True
    monkeypatch.setenv('KG_LLM_MODEL','initial-model')
    context=DocumentCheckpointContext(Store(),None,{'id':'item','file_sha256':'hash','lease_token':'lease'},'worker',None,[{'text':'text'}])
    context.guard()
    monkeypatch.setenv('KG_LLM_MODEL','changed-model')
    with pytest.raises(ValueError) as error:context.guard()
    assert error.value.code=='checkpoint_incompatible'


def test_extraction_progress_is_visible_before_provider_and_reuses_checkpoints():
    from contextlib import nullcontext
    from unittest.mock import Mock, call
    from knowledge_service.services.document_ingest_runner import DocumentCheckpointContext

    store = Mock()
    store.lease_valid.return_value = True
    store.checkpoint_guard.return_value = nullcontext()
    checkpoints = Mock()
    checkpoints.get.return_value = None
    checkpoints.has_other_configuration.return_value = False
    item = dict(id='item', file_sha256='hash', lease_token='lease', document_id='document', attempt=1)
    chunks = [{'text': 'first'}, {'text': 'second'}]
    context = DocumentCheckpointContext(store, checkpoints, item, 'worker', None, chunks)
    output = {'records': [], 'review_candidates': [], 'diagnostics': {}}

    def first_extract():
        store.update_progress.assert_called_once_with('item', 'worker', 'lease', stage='extracting', progress=15)
        checkpoints.put.assert_not_called()
        return output

    context.execute(0, 'first', first_extract)
    store.update_progress.assert_called_with('item', 'worker', 'lease', stage='extracting', progress=45)
    checkpoints.put.assert_called_once()

    store.reset_mock()
    resumed = DocumentCheckpointContext(store, checkpoints, {**item, 'attempt': 2, 'progress': 45}, 'worker', None, chunks)
    resumed.update_progress('chunking', 10)
    resumed.update_progress('extracting', 15)
    checkpoints.get.side_effect = [{'output': output}, None]
    unexpected_extract = Mock(side_effect=AssertionError('saved checkpoint must be reused'))
    assert resumed.execute(0, 'first', unexpected_extract) == output
    unexpected_extract.assert_not_called()

    def second_extract():
        assert store.update_progress.call_args_list == [
            call('item', 'worker', 'lease', stage='chunking', progress=45),
            call('item', 'worker', 'lease', stage='extracting', progress=45),
        ]
        return output

    resumed.execute(1, 'second', second_extract)
    store.update_progress.assert_called_with('item', 'worker', 'lease', stage='extracting', progress=75)
