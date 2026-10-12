"""显式 embedding 后端；演示哈希被标记为非语义。"""
import hashlib
import json
import os
import threading
from pathlib import Path

import httpx

from ..core.net import external_client
from ..utils.diagnostics import redact


class EmbeddingDimensionConfigurationError(ValueError):
    """无法在 Milvus 初始化前得到一致、有效的 embedding 维度。"""


class EmbeddingDimensionMismatch(RuntimeError):
    """模型权重的实际输出维度与轻量配置声明不一致。"""


def _positive_dimension(value):
    if isinstance(value, bool):
        return None
    try:
        dimension = int(value)
    except (TypeError, ValueError):
        return None
    return dimension if dimension > 0 else None


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _dimension_from(config, keys):
    if not isinstance(config, dict):
        return None, None
    for key in keys:
        dimension = _positive_dimension(config.get(key))
        if dimension is not None:
            return dimension, key
    return None, None


def _local_model_dimension_info(path):
    """从 SentenceTransformer 的轻量配置推导最终输出维度及来源。

    根 ``config.json`` 只提供 transformer 的兜底维度。``modules.json`` 才描述
    SentenceTransformer 的实际流水线，因此按其顺序读取各模块 ``config.json``；
    后置 Pooling/Dense/输出投影声明的维度会覆盖前面的声明。

    为什么要提前知道：Milvus 的 collection 维度在**建表时固定**，必须和编码器一致；
    这里只读 KB 级 JSON，绝不为了建表加载数 GB 模型权重。
    """
    root = Path(path)
    dimension, key = _dimension_from(
        _read_json(root / 'config.json'),
        ('sentence_embedding_dimension', 'hidden_size', 'd_model'),
    )
    source = f'config.json:{key}' if key else 'unavailable'

    modules = _read_json(root / 'modules.json')
    if not isinstance(modules, list):
        return dimension, source

    for module in modules:
        if not isinstance(module, dict):
            continue
        relative = module.get('path')
        if not isinstance(relative, str):
            continue
        config_path = (root / relative / 'config.json').resolve()
        try:
            config_path.relative_to(root.resolve())
        except ValueError:
            continue
        module_dimension, module_key = _dimension_from(
            _read_json(config_path),
            ('output_dimension', 'out_features', 'word_embedding_dimension'),
        )
        if module_dimension is not None:
            dimension = module_dimension
            relative_config = (Path(relative) / 'config.json').as_posix()
            source = f'{relative_config}:{module_key}'
    return dimension, source


def _local_model_dimension(path):
    return _local_model_dimension_info(path)[0]


def encoder_dimension(encoder=None):
    """当前编码器产出的向量维度；无法在编码前确定时返回 None。

    这是"维度"的**唯一出处**：Milvus 建表、索引重建、错误提示都读它，
    别再在别处抄一个 1024。
    """
    return getattr(encoder if encoder is not None else configured_encoder(), 'dimension', None)


class HashingEncoder:
    identity = 'demo-character-bigrams-sha256-256-v1'
    semantic = False
    dimension = 256   # 与 encode() 里的 256 桶一致；Milvus 建表维度据此推导
    dimension_source = 'built-in demo encoder'

    def encode(self, texts):
        output = []
        for text in texts:
            vector = [0.] * 256
            text = text.lower().strip()
            for token in [text[i:i + 2] for i in range(max(1, len(text) - 1))]:
                vector[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], 'big') % 256] += 1
            output.append(vector)
        return output


class LocalEncoder:
    semantic = True

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.identity = f'sentence-transformers:{self.path}:normalized-v1'
        self._model = None
        self._lock = threading.RLock()
        self.dimension, self.dimension_source = _local_model_dimension_info(self.path)

    def __repr__(self):
        return f'LocalEncoder({self.path}, dim={self.dimension})'

    def encode(self, texts):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.path, local_files_only=True)
                except Exception as exc:
                    raise RuntimeError(f'本地 embedding 模型不可用：{redact(exc)}') from exc
                reported = None
                get_dimension = getattr(self._model, 'get_sentence_embedding_dimension', None)
                if callable(get_dimension):
                    reported = _positive_dimension(get_dimension())
                if self.dimension is not None and reported is not None and reported != self.dimension:
                    raise EmbeddingDimensionMismatch(
                        f'embedding 配置声明 {self.dimension} 维（{self.dimension_source}），'
                        f'实际模型 {reported} 维；拒绝继续，以免写入错误维度的 Milvus collection。')
            batch_size=int(os.environ.get('KG_EMBEDDING_BATCH_SIZE','4'))
            if not 1<=batch_size<=64: raise ValueError('KG_EMBEDDING_BATCH_SIZE 必须在 1..64 之间')
            encoded = self._model.encode(
                texts, normalize_embeddings=True, batch_size=batch_size)
            output = encoded.tolist() if hasattr(encoded, 'tolist') else list(encoded)
            if self.dimension is not None:
                for vector in output:
                    try:
                        actual = len(vector)
                    except TypeError as exc:
                        raise RuntimeError('本地 embedding 模型返回了无效向量') from exc
                    if actual != self.dimension:
                        raise EmbeddingDimensionMismatch(
                            f'embedding 配置声明 {self.dimension} 维（{self.dimension_source}），'
                            f'实际向量 {actual} 维；拒绝继续，以免写入错误维度的 Milvus collection。')
            return output


class RemoteEncoder:
    semantic = True

    def __init__(self):
        self.url = os.environ.get('KG_EMBEDDING_BASE_URL', '').rstrip('/')
        self.model = os.environ.get('KG_EMBEDDING_MODEL', '')
        self.key = os.environ.get('KG_EMBEDDING_API_KEY', '')
        self.identity = f'openai:{self.url}:{self.model}'
        # 远端模型的维度无法在建 Milvus collection 前探测，必须显式声明。
        declared = os.environ.get('KG_EMBEDDING_DIMENSION', '').strip()
        if declared:
            self.dimension = _positive_dimension(declared)
            if self.dimension is None:
                raise EmbeddingDimensionConfigurationError(
                    'KG_EMBEDDING_DIMENSION 必须是正整数。')
            self.dimension_source = 'KG_EMBEDDING_DIMENSION'
        else:
            self.dimension = None
            self.dimension_source = 'unavailable'

    def encode(self, texts):
        if not all([self.url, self.model, self.key]):
            raise RuntimeError('请配置 KG_EMBEDDING_BASE_URL、KG_EMBEDDING_MODEL、KG_EMBEDDING_API_KEY')
        try:
            with external_client(120) as client:
                response = client.post(self.url + '/embeddings', headers={'Authorization': f'Bearer {self.key}'},
                                       json={'model': self.model, 'input': texts})
                response.raise_for_status()
                data = sorted(response.json()['data'], key=lambda row: row['index'])
                if len(data) != len(texts):
                    raise RuntimeError('embedding 提供商返回的向量数量不正确')
                return [row['embedding'] for row in data]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # 保留底层原因（经 redact 脱敏）：只报异常类型会让「模型名不存在」
            # 这类可自愈的配置错误看起来和网络故障一样，无法自查。
            raise RuntimeError(f'embedding 提供商不可用或返回无效响应：{redact(exc)}') from exc


def configured_encoder():
    backend = os.environ.get('KG_EMBEDDING_BACKEND', 'local')
    if backend == 'local':
        default = Path(__file__).resolve().parents[2] / 'data' / 'model'
        return LocalEncoder(os.environ.get('KG_EMBEDDING_PATH', str(default)))
    if backend == 'openai':
        return RemoteEncoder()
    if backend == 'demo':
        return HashingEncoder()
    raise ValueError('KG_EMBEDDING_BACKEND 必须是 local、openai 或 demo')
