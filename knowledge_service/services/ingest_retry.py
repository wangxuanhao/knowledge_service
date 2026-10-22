"""Public, provider-independent failures and bounded retry policy."""
from dataclasses import dataclass
import random
import re

@dataclass(frozen=True)
class IngestError:
    code: str
    message: str
    retryable: bool
    stage: str = 'extraction'

_ERRORS = {
    'timeout': ('服务连接超时，稍后自动重试', True, 'extraction'),
    'rate_limit': ('模型请求限流，稍后自动重试', True, 'extraction'),
    'provider_unavailable': ('模型服务暂时不可用，稍后自动重试', True, 'extraction'),
    'authentication': ('模型认证或访问失败，请检查服务配置', False, 'extraction'),
    'model_not_found': ('模型不存在，请检查服务配置', False, 'extraction'),
    'parse_error': ('文档解析失败，请检查文件格式', False, 'parse'),
    'ocr_engine_unavailable': ('OCR 引擎不可用，请安装并校验模型', False, 'ocr'),
    'ocr_low_confidence': ('图片文字置信度不足，请人工确认', False, 'ocr'),
    'ocr_timeout': ('图片文字识别超时，稍后自动重试', True, 'ocr'),
    'ocr_runtime_error': ('图片文字识别暂时失败，稍后自动重试', True, 'ocr'),
    'ocr_failed': ('图片文字识别失败，请检查图片或识别配置', False, 'ocr'),
    'missing_asset': ('文档引用的本地附件不存在，请补齐附件', False, 'parse'),
    'source_asset_missing': ('文档引用的本地附件不存在，请补齐附件', False, 'parse'),
    'checkpoint_incompatible': ('配置已变化，请选择整份文档重跑', False, 'extraction'),
    'checkpoint_pending': ('失败切片已重试；其余未完成切片请从检查点继续', False, 'extraction'),
    'ontology_version_changed': ('本体版本已变化，请选择整份文档重跑', False, 'extraction'),
    'commit_failed': ('写入暂时失败，保留检查点后重试', True, 'committing'),
    'entity_resolution_unavailable': ('实体语义解析暂不可用，请恢复 Milvus 连接或等待索引同步后重试；也可人工复核', True, 'extraction'),
    'entity_resolution_configuration': ('实体语义解析配置不匹配，请修复 Milvus 模型、维度或索引后重试；也可人工复核', False, 'extraction'),
    'ingest_failed': ('文档处理失败，请检查配置后重试', False, 'extraction'),
}
_ALIASES = {
    'llm_timeout':'timeout','llm_rate_limited':'rate_limit','llm_unavailable':'provider_unavailable',
    'llm_auth_failed':'authentication','llm_model_not_found':'model_not_found',
    'document_parse_failed':'parse_error','unknown_failure':'ingest_failed','stale_snapshot':'commit_failed',
    '401': 'authentication', '403': 'authentication', 'unauthorized': 'authentication',
    'forbidden': 'authentication', 'authentication_error': 'authentication',
    '404': 'model_not_found', '429': 'rate_limit', 'rate_limit_exceeded': 'rate_limit',
    '502': 'provider_unavailable', '503': 'provider_unavailable',
    'ocr_unavailable': 'ocr_engine_unavailable', 'ocr_runtime_failed': 'ocr_runtime_error',
    'document_parse_error': 'parse_error',
}
_CANONICAL={'timeout':'llm_timeout','rate_limit':'llm_rate_limited','provider_unavailable':'llm_unavailable',
            'authentication':'llm_auth_failed','model_not_found':'llm_model_not_found',
            'parse_error':'document_parse_failed','ingest_failed':'unknown_failure','missing_asset':'source_asset_missing'}
_COMMON_LLM_ERRORS = {'rate_limit','provider_unavailable','authentication','model_not_found','timeout'}


def _public(code):
    message, retryable, stage = _ERRORS[code]
    return IngestError(_CANONICAL.get(code,code), message, retryable, stage)


def _code(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    normalized = _ALIASES.get(normalized, normalized)
    return normalized if normalized in _ERRORS else None


def classify_error(exc):
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = getattr(current, '__cause__', None)
    # Outer domain codes preserve manual OCR setup failures around network errors.
    for error in chain:
        code = _code(getattr(error, 'code', None))
        if code:
            return _public(code)
    for error in chain:
        response = getattr(error, 'response', None)
        for value in (getattr(error, 'status_code', None), getattr(error, 'status', None),
                      getattr(response, 'status_code', None), getattr(response, 'status', None)):
            code = _code(value)
            if code:
                return _public(code)
    for error in chain:
        text = str(error).lower()
        for name in _ERRORS:
            if name != 'ingest_failed' and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', text):
                return _public(name)
        for alias, code in _ALIASES.items():
            if re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)', text):
                return _public(code)
        if (isinstance(error, (TimeoutError, ConnectionError))
                or any(word in type(error).__name__.lower() for word in ('timeout','connecterror','connectionerror'))) or any(
                word in text for word in ('timeout','timed out','connection','connecterror')):
            return _public('timeout')
        if 'parse' in type(error).__name__.lower() or 'corrupt' in text:
            return _public('parse_error')
    return _public('ingest_failed')

def retry_delay(count, jitter=None):
    """Zero-based retry index; None signals the five-retry budget is spent."""
    if not isinstance(count, int) or count < 0:
        raise ValueError('retry count must be a nonnegative integer')
    delays = (5,15,45,120,300)
    if count >= len(delays):
        return None
    sample = float((random.random if jitter is None else jitter)())
    sample = max(0., min(1., sample))
    return delays[count] * (1 + .2 * sample)

def should_pause(errors, minimum=5, ratio=.6):
    """Shared LLM failures divided by all observations, including successes."""
    if not 0 < ratio <= 1:
        raise ValueError('failure ratio must be greater than zero and at most one')
    observations = list(errors)
    if len(observations) < max(5, minimum):
        return False
    failures = sum(_code(error.code if isinstance(error, IngestError) else error)
                   in _COMMON_LLM_ERRORS for error in observations)
    return failures / len(observations) >= ratio

def public_failure(failure):
    """Legacy persisted failures are untrusted provider text at the read boundary."""
    if not failure:return None
    exception=RuntimeError(str(failure.get('message','')))
    exception.code=failure.get('code')
    error=classify_error(exception)
    message=('服务已重启，请从检查点继续' if failure.get('type')=='ProcessRestart' else error.message)
    return {'type':failure.get('type','IngestFailure'),'code':error.code,
            'message':message,'retryable':error.retryable}
