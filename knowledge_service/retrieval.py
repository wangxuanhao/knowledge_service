"""FAISS ranks an already filtered candidate set. Never filter global top-k."""
import sys
import json
import subprocess
from time import perf_counter
import numpy as np


def rank_candidates(candidates, query, k, encoder, timings=None):
    if not candidates:
        return []
    if any(not row.get('embedding') for row in candidates):
        raise RuntimeError('语义索引尚未就绪，请先构建语义索引；仍可使用关键词检索和图谱浏览')
    vectors = []
    started = perf_counter()
    query_vector = np.asarray(encoder.encode([query]), dtype='float32')
    encoded_at = perf_counter()
    if timings is not None:
        timings['query_embedding'] = round((encoded_at-started)*1000, 1)
    if query_vector.ndim != 2 or query_vector.shape[0] != 1 or not np.isfinite(query_vector).all():
        raise ValueError('Invalid query embedding')
    for row in candidates:
        if row.get('embedding_model') != encoder.identity:
            raise ValueError('Embedding model differs from stored vectors; explicitly re-embed records')
        vector = row.get('embedding')
        if not vector or len(vector) != query_vector.shape[1]:
            raise ValueError('Embedding dimension mismatch')
        vectors.append(vector)
    matrix = np.ascontiguousarray(vectors, dtype='float32')
    if not np.isfinite(matrix).all():
        raise ValueError('Embedding contains non-finite numbers')
    if sys.platform == 'win32':
        # Conda torch and FAISS 1.9 load incompatible OpenMP runtimes even with
        # import-order changes. Keep FAISS in a clean process, never set the
        # unsafe KMP_DUPLICATE_LIB_OK or replace shared DLLs.
        payload=json.dumps({'vectors':matrix.tolist(),'query':query_vector.tolist(),'k':min(k,len(candidates))})
        result=subprocess.run([sys.executable,'-m','knowledge_service.faiss_worker'],input=payload,
                              capture_output=True,text=True,timeout=120,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Isolated FAISS ranking failed; check the configured Python runtime')
        output=json.loads(result.stdout)
        scores,indices=output['scores'],output['indices']
    else:
        from .faiss_worker import rank
        output=rank({'vectors':matrix.tolist(),'query':query_vector.tolist(),'k':min(k,len(candidates))})
        scores,indices=output['scores'],output['indices']
    if timings is not None:
        timings['vector_ranking'] = round((perf_counter()-encoded_at)*1000, 1)
    return [{**{key: value for key, value in candidates[int(i)].items() if key != 'embedding'},
             'score': float(score)} for score, i in zip(scores[0], indices[0]) if i >= 0]
