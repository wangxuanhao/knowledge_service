"""Full PG qualification with bounded Milvus ranking state, never local vectors."""
from math import isfinite

from ..repository.scoped_reads import ScopedReads


def _best(rows, limit):
    by_version = {}
    for row in rows:
        score = float(row.get('score') or 0)
        if not isfinite(score):
            raise RuntimeError('Milvus 返回无效的排名分数')
        previous = by_version.get(row['version_id'])
        if previous is None or score > previous['score']:
            by_version[row['version_id']] = {**row, 'score': score}
    return sorted(by_version.values(), key=lambda row: (
        -row['score'], row['record_id'], row['version_id']))[:limit]


def rank_qualified(engine, project, query, mode, kinds, target, native_scope,
                   pg_scope, platform_match, index, catalog, vector):
    """Rank every qualified ID batch, then merge globally comparable backends.

    A batch-local hybrid rank is deliberately forbidden: local rank positions
    cannot be merged into a global RRF. Dense/BM25 scores each retain top-L over
    the entire qualified corpus before the single global fusion.
    """
    from .retrieval import rrf_fuse
    if not target:
        return [], 0, True
    depth = min(1000, max(50, target * 5))
    modes = ('semantic', 'keyword') if mode == 'hybrid' else (mode,)
    rankings = {backend: [] for backend in modes}
    readers = ScopedReads(engine.repository)
    authoritative_scope = {**pg_scope, '_platform_match': platform_match}
    candidates = 0
    for versions in readers.version_id_batches(project, authoritative_scope, kinds=kinds, batch_size=1000):
        if not versions:
            continue
        candidates += len(versions)
        eligible = set(versions)
        for backend in modes:
            hits = engine.milvus.search(project, query, retrieval_mode=backend,
                query_vector=vector if backend == 'semantic' else None, kinds=kinds,
                k=depth, scope=native_scope, index_version=index['index_version'],
                catalog=catalog, platform_match=platform_match, eligible_version_ids=versions)
            if any(hit.get('version_id') not in eligible or not hit.get('record_id') for hit in hits):
                raise RuntimeError('Milvus 返回了资格门禁以外的版本')
            rankings[backend] = _best([*rankings[backend], *hits], depth)
    if mode == 'hybrid':
        merged = rrf_fuse({backend: [{**hit, 'id': hit['record_id']} for hit in hits]
                           for backend, hits in rankings.items()}, depth * 2)
    else:
        merged = rankings[mode]
    ids = list(dict.fromkeys(hit['record_id'] for hit in merged))
    rows = readers.by_ids(project, authoritative_scope, ids, kinds=kinds) if ids else []
    by_version = {row['version_id']: row for row in rows}
    result = []
    for hit in merged:
        row = by_version.get(hit['version_id'])
        if row is None or row['id'] != hit['record_id']:
            continue
        result.append({**{key: value for key, value in row.items() if key != 'embedding'},
                       **{key: hit[key] for key in ('score', 'rrf_score', 'backend_ranks') if key in hit},
                       **({'keyword_score': hit['score']} if mode == 'keyword' else {})})
    return result[:target], candidates, len(result) == len(merged)


def ontology_type_rows(repository, project, scope, type_iris, limit, platform_match):
    """A business type channel, ordered by ID, independent of lexical matches."""
    if not type_iris or not limit:
        return []
    result = ScopedReads(repository).page(project,
        {**scope, '_platform_match': platform_match, '_type_iris': sorted(set(type_iris))},
        kinds=['entity'], limit=limit, include_total=False)
    return [{key: value for key, value in row.items() if key != 'embedding'}
            for row in result['records']]
