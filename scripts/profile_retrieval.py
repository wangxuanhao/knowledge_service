"""Read-only local retrieval profiling. Outputs timings/counts, never credentials.

Uses production configuration; it is not a pytest fixture. The first semantic
query may load a local embedding model. Nested stage durations overlap.
"""
import argparse
import json
import logging
from pathlib import Path
from threading import Lock
from time import perf_counter

from knowledge_service.core.config import load_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--mode', choices=['keyword', 'semantic', 'hybrid'], default='keyword')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    load_environment()
    from knowledge_service.api import _resolve_milvus_dim
    from knowledge_service.integrations.milvus_store import MilvusStore
    from knowledge_service.integrations.embeddings import configured_encoder
    from knowledge_service.repository.connection import resolve_dsn
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.service import KnowledgeService
    import os

    encoder = configured_encoder()
    # Connect without startup ensure/create: destination validation is measured
    # on the existing project's query path. Do not rebuild projections here.
    store = MilvusStore(host=os.getenv('KG_MILVUS_HOST', 'localhost'),
                        port=os.getenv('KG_MILVUS_PORT', '19530'),
                        dim=_resolve_milvus_dim(encoder), encoder_identity=encoder.identity).connect()
    repo = Repository(resolve_dsn())
    service = KnowledgeService(repo, encoder, store)
    metrics, guard = {}, Lock()

    def wrap(obj, name, label=None):
        original = getattr(obj, name)
        def measured(*a, **kw):
            started = perf_counter()
            try:
                return original(*a, **kw)
            finally:
                with guard:
                    item = metrics.setdefault(label or name, {'calls': 0, 'ms': 0})
                    item['calls'] += 1
                    item['ms'] += (perf_counter() - started) * 1000
        setattr(obj, name, measured)

    for name in ['ensure_collection', '_load_if_needed', 'current_destination']:
        wrap(store, name)
    for name in ['describe_index', 'load_collection', 'search', 'hybrid_search']:
        wrap(store._client, name, 'rpc.' + name)
    for name in ['projection_ready', 'distinct_applicability_values', 'current_records_by_version_ids']:
        wrap(repo, name)
    wrap(encoder, 'encode')
    samples = []
    try:
        for i in range(args.repeats):
            metrics.clear()
            start = perf_counter()
            result = service.search(args.project, {'query': args.query, 'retrieval_mode': args.mode})
            item = {'iteration': i, 'wall_ms': round((perf_counter() - start) * 1000, 2),
                    'backend': result.get('_backend'), 'hits': len(result['hits']),
                    'stages': {key: {**value, 'ms': round(value['ms'], 2)} for key, value in metrics.items()}}
            samples.append(item)
            print(json.dumps(item), flush=True)
        if args.output:
            args.output.write_text(json.dumps(samples, indent=2), encoding='utf-8')
    finally:
        store.close()
        repo.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    main()
