"""Bind one immutable model configuration to each operation and stream step."""
from functools import wraps
import json

from ..core.llm_config import LLMConfig, bound_llm, bound_llm_config, current_llm, runtime_options


def _operation_config(owner, task_id=None):
    if task_id is None and bound_llm_config() is not None:
        return bound_llm_config()
    service = getattr(owner, 'service', owner)
    manager = getattr(service, 'llm_models', None)
    if manager is None and hasattr(service, 'repository'):
        from .llm_models import LLMModels
        manager = LLMModels(service.repository)
    if task_id is not None and manager is not None:
        return manager.pin(task_id)
    return bound_llm_config() or (manager.resolve() if manager else current_llm())


def llm_operation(function=None, *, task_key=None, uses_llm=None):
    """Resolve at the outer boundary, retaining bindings in nested operations."""
    def decorate(operation):
        @wraps(operation)
        def run(owner, *args, **kwargs):
            required = uses_llm is None or uses_llm(owner, *args, **kwargs)
            task_id = task_key(owner, *args, **kwargs) if required and task_key else None
            config = _operation_config(owner, task_id) if required else LLMConfig(**runtime_options())
            with bound_llm(config):
                return operation(owner, *args, **kwargs)
        return run
    return decorate(function) if function is not None else decorate


def llm_stream(function=None, *, uses_llm=None):
    """Restore the caller context at every yield, including thread-pool iteration."""
    def decorate(operation):
        @wraps(operation)
        def stream(owner, *args, **kwargs):
            try:
                required = uses_llm is None or uses_llm(owner, *args, **kwargs)
                config = _operation_config(owner) if required else LLMConfig(**runtime_options())
            except (RuntimeError, ValueError):
                detail = '模型配置不可用，请由超级管理员检查默认模型及加密密钥'
                yield 'event: error\ndata: ' + json.dumps({'detail': detail}, ensure_ascii=False) + '\n\n'
                return
            iterator = operation(owner, *args, **kwargs)
            try:
                while True:
                    with bound_llm(config):
                        try:
                            item = next(iterator)
                        except StopIteration:
                            return
                    yield item
            finally:
                with bound_llm(config):
                    iterator.close()
        return stream
    return decorate(function) if function is not None else decorate
