"""FAISS-only runtime boundary for incompatible Windows OpenMP libraries."""
import json
import sys


def rank(payload):
    import numpy as np
    import faiss
    faiss.omp_set_num_threads(1)
    matrix=np.ascontiguousarray(payload['vectors'],dtype='float32')
    query=np.ascontiguousarray(payload['query'],dtype='float32')
    faiss.normalize_L2(matrix);faiss.normalize_L2(query)
    index=faiss.IndexFlatIP(matrix.shape[1]);index.add(matrix)
    scores,indices=index.search(query,payload['k'])
    return {'scores':scores.tolist(),'indices':indices.tolist()}


if __name__=='__main__':
    print(json.dumps(rank(json.load(sys.stdin))))
