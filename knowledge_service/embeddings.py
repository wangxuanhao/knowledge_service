"""Explicit embedding backends; demonstration hashing is labelled as non-semantic."""
import hashlib
import os
import threading
from pathlib import Path

import httpx


class HashingEncoder:
    identity = 'demo-character-bigrams-sha256-256-v1'
    semantic = False

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

    def encode(self, texts):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.path, local_files_only=True)
                except Exception as exc:
                    raise RuntimeError(f'Local embedding model unavailable: {exc}') from exc
            batch_size=int(os.environ.get('KG_EMBEDDING_BATCH_SIZE','4'))
            if not 1<=batch_size<=64: raise ValueError('KG_EMBEDDING_BATCH_SIZE must be 1..64')
            return self._model.encode(texts, normalize_embeddings=True,batch_size=batch_size).tolist()


class RemoteEncoder:
    semantic = True

    def __init__(self):
        self.url = os.environ.get('KG_EMBEDDING_BASE_URL', '').rstrip('/')
        self.model = os.environ.get('KG_EMBEDDING_MODEL', '')
        self.key = os.environ.get('KG_EMBEDDING_API_KEY', '')
        self.identity = f'openai:{self.url}:{self.model}'

    def encode(self, texts):
        if not all([self.url, self.model, self.key]):
            raise RuntimeError('Configure KG_EMBEDDING_BASE_URL, KG_EMBEDDING_MODEL, KG_EMBEDDING_API_KEY')
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(self.url + '/embeddings', headers={'Authorization': f'Bearer {self.key}'},
                                       json={'model': self.model, 'input': texts})
                response.raise_for_status()
                data = sorted(response.json()['data'], key=lambda row: row['index'])
                if len(data) != len(texts):
                    raise RuntimeError('Embedding provider returned wrong number of vectors')
                return [row['embedding'] for row in data]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f'Embedding provider unavailable or invalid response ({type(exc).__name__})') from exc


def configured_encoder():
    backend = os.environ.get('KG_EMBEDDING_BACKEND', 'local')
    if backend == 'local':
        default = Path(__file__).resolve().parents[1] / 'data' / 'model'
        return LocalEncoder(os.environ.get('KG_EMBEDDING_PATH', str(default)))
    if backend == 'openai':
        return RemoteEncoder()
    if backend == 'demo':
        return HashingEncoder()
    raise ValueError('KG_EMBEDDING_BACKEND must be local, openai or demo')
