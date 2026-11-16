"""Disposable document-vector cache; it never publishes searchable records."""
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import numpy as np

LOG = logging.getLogger(__name__)


class CachedDocumentEncoder:
    def __init__(self, encoder, root, *, max_bytes=512 * 1024 * 1024, ttl=7 * 86400):
        self.encoder, self.root = encoder, Path(root)
        self.max_bytes, self.ttl = max_bytes, ttl
        self.lock = threading.RLock()
        self.next_cleanup = 0

    def __getattr__(self, name):
        return getattr(self.encoder, name)

    def _key(self, text):
        return hashlib.sha256((self.identity + '\0' + str(getattr(self.encoder, 'dimension', ''))
                              + '\0' + text).encode('utf-8')).hexdigest()

    def _valid(self, vector):
        value = np.asarray(vector, dtype='float32')
        dimension = getattr(self.encoder, 'dimension', None)
        if (value.ndim != 1 or not value.size or (dimension is not None and value.size != dimension)
                or not np.isfinite(value).all() or not np.linalg.norm(value)):
            raise RuntimeError('文档编码器返回无效向量')
        return value

    def _read(self, key):
        path = self.root / (key + '.npy')
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            return self._valid(np.load(path, allow_pickle=False))
        except (OSError, ValueError, RuntimeError, EOFError):
            return None

    def _save(self, key, vector):
        temporary = self.root / (key + '.' + uuid4().hex + '.tmp')
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with temporary.open('wb') as stream:
                np.save(stream, vector, allow_pickle=False)
            os.replace(temporary, self.root / (key + '.npy'))
        except OSError:
            # Cache availability must not determine formal indexing success.
            LOG.warning('文档向量缓存暂不可写，正式索引继续使用本次编码结果')
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup(self):
        if time.monotonic() < self.next_cleanup:
            return
        self.next_cleanup = time.monotonic() + 300
        try:
            files = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in self.root.glob('*.npy')))
            total, now = sum(size for _, size, _ in files), time.time()
            for modified, size, path in files:
                if now - modified > self.ttl or total > self.max_bytes:
                    path.unlink(missing_ok=True)
                    total -= size
        except OSError:
            pass

    def encode(self, texts):
        texts = list(texts)
        with self.lock:
            keys = [self._key(text) for text in texts]
            values = {key: self._read(key) for key in dict.fromkeys(keys)}
            missing = {key: text for key, text in zip(keys, texts) if values[key] is None}
            pairs = list(missing.items())
            for start in range(0, len(pairs), 64):
                batch = pairs[start:start + 64]
                vectors = self.encoder.encode([text for _, text in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError('文档编码器返回向量数量不符')
                checked = [self._valid(vector) for vector in vectors]
                for (key, _), vector in zip(batch, checked):
                    values[key] = vector
                    self._save(key, vector)
            self._cleanup()
            return [values[key].copy().tolist() for key in keys]
