import pytest

from knowledge_service.services.ingest_retry import classify_error, retry_delay, should_pause


class CodedError(RuntimeError):
    def __init__(self, code, message='secret provider response sk-secret'):
        self.code = code
        super().__init__(message)


@pytest.mark.parametrize('exc,code,retryable,stage',[
    (TimeoutError('secret'), 'llm_timeout', True, 'extraction'),
    (CodedError('429'), 'llm_rate_limited', True, 'extraction'),
    (CodedError('502'), 'llm_unavailable', True, 'extraction'),
    (CodedError('503'), 'llm_unavailable', True, 'extraction'),
    (CodedError('401'), 'llm_auth_failed', False, 'extraction'),
    (CodedError('403'), 'llm_auth_failed', False, 'extraction'),
    (CodedError('llm_model_not_found'), 'llm_model_not_found', False, 'extraction'),
    (CodedError('document_parse_failed'), 'document_parse_failed', False, 'parse'),
    (CodedError('ocr_engine_unavailable'), 'ocr_engine_unavailable', False, 'ocr'),
    (CodedError('ocr_low_confidence'), 'ocr_low_confidence', False, 'ocr'),
    (CodedError('ocr_runtime_error'), 'ocr_runtime_error', True, 'ocr'),
    (CodedError('ocr_timeout'), 'ocr_timeout', True, 'ocr'),
    (CodedError('source_asset_missing'), 'source_asset_missing', False, 'parse'),
    (CodedError('source_asset_missing'), 'source_asset_missing', False, 'parse'),
    (CodedError('ocr_failed'), 'ocr_failed', False, 'ocr'),
])
def test_classification_uses_stable_public_messages(exc,code,retryable,stage):
    result = classify_error(exc)
    assert (result.code,result.retryable,result.stage) == (code,retryable,stage)
    assert 'secret' not in result.message
    assert 'provider response' not in result.message


def test_outer_explicit_code_takes_priority_over_cause_text():
    exc = CodedError('ocr_engine_unavailable','timeout secret')
    exc.__cause__ = TimeoutError('timeout secret')
    assert classify_error(exc).code == 'ocr_engine_unavailable'


def test_nested_explicit_code_takes_priority_over_outer_message():
    exc = RuntimeError('401 timeout provider secret')
    exc.__cause__ = CodedError('source_asset_missing')
    assert classify_error(exc).code == 'source_asset_missing'


def test_cyclic_exception_chain_terminates():
    exc = CodedError('ocr_runtime_error')
    cause = RuntimeError('secret')
    exc.__cause__ = cause
    cause.__cause__ = exc
    assert classify_error(exc).code == 'ocr_runtime_error'


def test_status_code_and_response_status_are_structured_signals():
    exc = RuntimeError('sensitive response')
    exc.status_code = 429
    assert classify_error(exc).code == 'llm_rate_limited'
    wrapped = RuntimeError('sensitive outer response')
    wrapped.__cause__ = exc
    assert classify_error(wrapped).code == 'llm_rate_limited'
    response_error = RuntimeError('sensitive body')
    response_error.response = type('Response',(),{'status':503})()
    assert classify_error(response_error).code == 'llm_unavailable'


def test_backoff_exhausts_after_five_retries_with_injected_jitter():
    assert [retry_delay(index,jitter=lambda:0) for index in range(5)] == [5,15,45,120,300]
    assert retry_delay(5,jitter=lambda:0) is None
    assert retry_delay(1,jitter=lambda:1) == 18
    with pytest.raises(ValueError):
        retry_delay(-1)


def test_shared_llm_errors_pause_by_combined_ratio_and_observation_floor():
    assert not should_pause(['llm_timeout']*4,minimum=1)
    assert should_pause(['llm_timeout','llm_rate_limited','llm_unavailable',None,None],ratio=.6)
    assert not should_pause(['llm_timeout','llm_rate_limited','llm_unavailable',None,None],ratio=.8)
    assert not should_pause(['ocr_timeout']*5)
    assert not should_pause(['document_parse_failed']*5)
