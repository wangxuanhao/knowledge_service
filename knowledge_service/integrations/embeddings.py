"""显式 embedding 后端；演示哈希被标记为非语义。"""
import hashlib
import os
import threading
from pathlib import Path

import httpx

from ..core.net import external_client
from ..utils.diagnostics import redact


def _local_model_dimension(path):
    """不加载模型就猜出输出维度：读 config.json 的 hidden_size（sentence-transformers
    的池化输出维度就是它）。猜不到返回 None —— 让调用方回落到显式配置，而不是瞎猜一个数。

    为什么要提前知道：Milvus 的 collection 维度在**建表时固定**，必须和编码器一致；
    以前这个数字被硬编码成 1024，换个 demo/小模型就变成"写入时才发现维度不对"。
    """
    import json
    try:
        config = json.loads((Path(path) / 'config.json').read_text(encoding='utf-8'))
    except Exception:
        return None
    for key in ('hidden_size', 'sentence_embedding_dimension', 'd_model'):
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


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
        self.dimension = _local_model_dimension(self.path)

    def __repr__(self):
        return f'LocalEncoder({self.path}, dim={self.dimension})'

    def encode(self, texts):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.path, local_files_only=True)
                except Exception as exc:
                    raise RuntimeError(f'本地 embedding 模型不可用：{exc}') from exc
            batch_size=int(os.environ.get('KG_EMBEDDING_BATCH_SIZE','4'))
            if not 1<=batch_size<=64: raise ValueError('KG_EMBEDDING_BATCH_SIZE 必须在 1..64 之间')
            return self._model.encode(texts, normalize_embeddings=True,batch_size=batch_size).tolist()


class RemoteEncoder:
    semantic = True

    def __init__(self):
        self.url = os.environ.get('KG_EMBEDDING_BASE_URL', '').rstrip('/')
        self.model = os.environ.get('KG_EMBEDDING_MODEL', '')
        self.key = os.environ.get('KG_EMBEDDING_API_KEY', '')
        self.identity = f'openai:{self.url}:{self.model}'
        # 远端模型的维度无法在调用前问出来：要么显式声明，要么留给首次响应发现。
        declared = os.environ.get('KG_EMBEDDING_DIMENSION', '').strip()
        self.dimension = int(declared) if declared.isdigit() and int(declared) > 0 else None

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