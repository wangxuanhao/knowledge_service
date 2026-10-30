"""Truthful, secret-safe runtime capability reporting."""
from __future__ import annotations
import os
from importlib.util import find_spec

from ..utils.diagnostics import redact


def _embedding_backend(encoder):
    name = type(encoder).__name__.lower()
    if 'hashing' in name:
        return 'demo'
    if 'local' in name:
        return 'local'
    if 'remote' in name:
        return 'openai'
    return name.removesuffix('encoder') or 'unknown'


class RuntimeCapabilities:
    def __init__(self, *, encoder, milvus_store=None,
                 milvus_configured=False, milvus_error=None):
        self.encoder = encoder
        self.milvus_store = milvus_store
        self.milvus_configured = bool(milvus_configured)
        self.milvus_error = milvus_error

    def snapshot(self):
        expected = getattr(self.encoder, 'dimension', None)
        embedding = {
            'backend': _embedding_backend(self.encoder),
            'identity': getattr(self.encoder, 'identity', ''),
            'semantic': bool(getattr(self.encoder, 'semantic', False)),
            'dimension': expected,
            'dimension_source': getattr(
                self.encoder, 'dimension_source', 'unavailable'),
            'ready': expected is not None and (
                _embedding_backend(self.encoder) == 'demo'
                or bool(getattr(self.encoder, 'runtime_ready', False))),
        }
        initialization = getattr(self.encoder, 'initialization_state', None)
        if initialization is not None:
            embedding['initialization_state'] = initialization
            error = getattr(self.encoder, 'initialization_error', None)
            embedding['initialization_error'] = redact(error) if error else None

        store = self.milvus_store
        connected = bool(store is not None and getattr(store, 'available', False))
        actual = None
        error = self.milvus_error
        if connected:
            try:
                actual = store.collection_dim()
            except Exception as exc:  # health must report probe failures, not fail itself
                error = str(exc)
        collection_ready = connected and actual is not None
        configured_dim = getattr(store, 'dim', expected) if store is not None else expected
        if collection_ready and configured_dim is not None and actual != configured_dim:
            error = f'Milvus collection 维度 {actual} 与编码器/配置维度 {configured_dim} 不一致'
            collection_ready = False
        if self.milvus_configured and not connected and not error:
            error = 'Milvus 已配置但未连接'

        vector = {
            'backend': 'milvus' if self.milvus_configured else 'disabled',
            'configured': self.milvus_configured,
            'connected': connected,
            'collection': getattr(store, 'collection_name', None),
            'collection_ready': collection_ready,
            'dimension': actual,
            'expected_dimension': configured_dim,
            'error': redact(error) if error else None,
        }
        semantic_ready = embedding['semantic'] and embedding['ready'] and collection_ready
        llm_configured = all(os.environ.get(key, '').strip() for key in
                             ('KG_LLM_BASE_URL','KG_LLM_API_KEY','KG_LLM_MODEL'))
        capabilities = {
            'keyword': {'status': 'ready'},
            'semantic': {'status': 'ready' if semantic_ready else 'unavailable',
                         'vector_backend': 'milvus', 'fallback': False,
                         'reason': None if semantic_ready else
                         '语义与混合检索不可用：编码模型或 Milvus 索引未就绪；不会自动更换检索模式'},
            'llm': {'status': 'degraded' if llm_configured else 'disabled',
                    'reason': '已配置，远程可用性尚未探测' if llm_configured else '未配置'},
            'ocr': {'status': 'degraded' if find_spec('docling') else 'disabled',
                    'reason': '需实际文档解析验证 OCR 模型' },
            'neo4j': {'status': 'degraded' if os.environ.get('KG_NEO4J_PASSWORD') else 'disabled',
                      'reason': '需连接探测' },
        }
        if not embedding['ready'] and initialization == 'loading':
            capabilities['semantic']['reason'] = '本地语义模型正在初始化，请稍后重试；关键词检索仍可使用。'
        elif not embedding['ready'] and initialization == 'failed':
            capabilities['semantic']['reason'] = '本地语义模型初始化失败：' + (
                embedding.get('initialization_error') or '模型未就绪')
        degraded = (not embedding['ready'] or
                    (self.milvus_configured and not semantic_ready))
        return {
            'status': 'degraded' if degraded else 'ok',
            'embedding': embedding,
            'vector': vector,
            'capability_matrix': capabilities,
        }
