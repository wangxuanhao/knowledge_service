"""对已过滤的候选集排序，绝不做全局 top-k 过滤。"""
import json
import logging
import re
from time import perf_counter
import numpy as np

# 数据库错误的唯一出处：降级通道必须捕获驱动无关的 DatabaseError；否则一次
# 查询失败会让整个检索抛错，而不是按设计降级。
from ..repository.connection import DatabaseError
from ..domain.applicability import (
    dimension_mode, dimension_values, normalize_applicability,
)

LOG = logging.getLogger('knowledge_service.retrieval')

# Native projection readers must explicitly opt into the schema they understand.
# A missing cursor is deliberately stale; authority remains PostgreSQL.
PROJECTION_SCHEMA_VERSIONS = {'milvus': 3, 'neo4j': 3}


def has_vector(row):
    """当一行带有可用向量值时返回 True。

    存储的向量要么是旧的 float 列表，要么是从 float32 列读出的 numpy 数组。
    两者都是定长容器，因此显式测试长度——一旦向量以 ndarray 形式到达，
    直接用 ``if row.get('embedding')`` 会抛出 “truth value of an array is ambiguous”。
    """
    vector = row.get('embedding')
    return vector is not None and len(vector) > 0


def rank_candidates(candidates, query, k, encoder, timings=None,
                    query_vector=None):
    if not candidates:
        return []
    if any(not has_vector(row) for row in candidates):
        raise RuntimeError('语义索引尚未就绪，请先构建语义索引；仍可使用关键词检索和图谱浏览')
    vectors = []
    started = perf_counter()
    query_vector = np.asarray(
        encoder.encode([query]) if query_vector is None else [query_vector],
        dtype='float32')
    encoded_at = perf_counter()
    if timings is not None:
        timings['query_embedding'] = round((encoded_at-started)*1000, 1)
    if query_vector.ndim != 2 or query_vector.shape[0] != 1 or not np.isfinite(query_vector).all():
        raise ValueError('无效的查询向量')
    for row in candidates:
        if row.get('embedding_model') != encoder.identity:
            raise ValueError('嵌入模型与已存向量不一致；请显式重新编码记录')
        vector = row.get('embedding')
        # `not vector` 对 float32 列产出的 numpy 数组会抛异常，因此存在性检查走
        # `has_vector`，这里只比较长度。
        if not has_vector(row) or len(vector) != query_vector.shape[1]:
            raise ValueError('嵌入维度不匹配')
        vectors.append(vector)
    matrix = np.ascontiguousarray(vectors, dtype='float32')
    if not np.isfinite(matrix).all():
        raise ValueError('嵌入向量包含非有限数值')
    # 对这里的项目级候选集（尤其 Windows 上），精确余弦排序比启动独立 FAISS
    # 进程更快。它还把向量保持为 ndarray，无需经 JSON 序列化。
    matrix_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vector[0])
    if not query_norm:
        raise ValueError('查询向量长度为零')
    similarities = matrix.dot(query_vector[0]) / (np.where(matrix_norms == 0, 1.0, matrix_norms) * query_norm)
    limit = min(k, len(candidates))
    indices = np.argsort(-similarities, kind='stable')[:limit]
    if timings is not None:
        timings['vector_ranking'] = round((perf_counter()-encoded_at)*1000, 1)
    return [{**{key: value for key, value in candidates[int(i)].items() if key != 'embedding'},
             'score': float(similarities[int(i)])} for i in indices]


def cached_query_vector(encoder, query, cache=None):
    """Encode a request query once when callers share a cache across workers."""
    prepare = getattr(encoder, 'prepare_query', None)
    if callable(prepare):
        prepare()
    if cache is None:
        return encoder.encode([query])[0]
    lock = cache.get('lock')
    if lock is None:
        vectors = cache.setdefault('vectors', {})
        if query not in vectors:
            vectors[query] = encoder.encode([query])[0]
        return vectors[query]
    with lock:
        vectors = cache.setdefault('vectors', {})
        if query not in vectors:
            vectors[query] = encoder.encode([query])[0]
        return vectors[query]


def merge_entity_expansion(direct, related, limit, anchor_ids=()):
    """Reserve most slots for the original query and protect mentioned entities."""
    if not limit:
        return []
    def identity(row):
        return row.get('version_id') or row['id']
    by_version = {identity(row) for row in direct}
    additions, seen = [], set(by_version)
    for row in related:
        key = identity(row)
        if key not in seen:
            seen.add(key); additions.append(row)
    if not additions:
        return direct[:limit]
    anchors = [row for row in direct if row['id'] in anchor_ids]
    original = [*anchors, *[row for row in direct if row['id'] not in anchor_ids]]
    extra_budget = max(1, limit // 3) if limit > 1 else 0
    # Empty/short original rankings can use all remaining capacity.
    extra_budget = max(extra_budget, limit - len(original))
    extra_budget = min(extra_budget, max(0, limit - len(anchors)), len(additions))
    return [*original[:limit - extra_budget],
            *[{**row, 'retrieval_channel': 'ontology_entity_type'} for row in additions[:extra_budget]]]


def rrf_fuse(rankings, limit, constant=60):
    """融合各后端排名，而不假装它们的原始分数可相互比较。"""
    if type(limit) is not int or limit < 1:
        raise ValueError('RRF limit 必须为正数')
    fused = {}
    for backend, rows in rankings.items():
        seen = set()
        for rank, row in enumerate(rows, 1):
            record_id = row['id']
            if record_id in seen:
                continue
            seen.add(record_id)
            entry = fused.setdefault(record_id, {
                'row': {key: value for key, value in row.items() if key != 'score'},
                'score': 0.0, 'ranks': {}, 'best_rank': rank})
            entry['score'] += 1.0 / (constant + rank)
            entry['ranks'][backend] = rank
            entry['best_rank'] = min(entry['best_rank'], rank)
    ordered = sorted(fused.values(), key=lambda item: (-item['score'], item['best_rank'], item['row']['id']))
    return [{**item['row'], 'score': item['score'], 'rrf_score': item['score'],
             'backend_ranks': item['ranks']} for item in ordered[:limit]]


def _lexical_rank(candidates, query, limit):
    needle = query.casefold().strip()
    tokens = [token for token in re.findall(r'\w+', needle, flags=re.UNICODE) if token]
    ranked = []
    for row in candidates:
        haystack = (row.get('text', '') + ' ' + json.dumps(
            row.get('metadata', {}), ensure_ascii=False, sort_keys=True) + ' ' +
            ' '.join(row.get('_retrieval_aliases', []))).casefold()
        phrase = haystack.count(needle) if needle else 0
        matched = sum(haystack.count(token) for token in tokens)
        score = phrase * 10 + matched
        if not score:
            # Historical / degraded reads cannot rely on the vector index or
            # PostgreSQL tokenization. Chinese questions are often one token;
            # use lower-priority text bigrams only when exact matching fails.
            fragments = {part[index:index + 2]
                         for part in re.findall(r'[\u4e00-\u9fff]+', needle)
                         for index in range(len(part) - 1)}
            fragments -= {'什么', '哪些', '如何', '是否', '怎么', '多少'}
            text = row.get('text', '').casefold()
            if fragments:
                score = sum(fragment in text for fragment in fragments) / (len(fragments) + 1)
        if score:
            ranked.append(({**{key: value for key, value in row.items() if key != 'embedding'},
                            'keyword_score': score}, score))
    ranked.sort(key=lambda item: (-item[1], item[0]['id']))
    return [item[0] for item in ranked[:limit]]


def merge_keyword_rankings(rankings, limit):
    """合并多次关键词查询的结果（OR 语义，P0-1 查询扩展用）。

    同一条记录被多个词命中时取**最高分**，不累加：累加会让"被多个扩展词命中"
    压过"被原查询精确命中"，但扩展词只该负责补召回，不该改变相关性直觉。
    排序为分数降序、其次 id 升序（保证确定性），最后截断到 limit。
    """
    best = {}
    for rows in rankings:
        for row in rows:
            score = row.get('keyword_score') or 0
            current = best.get(row['id'])
            if current is None or score > current[0]:
                best[row['id']] = (score, row)
    ordered = sorted(best.values(), key=lambda item: (-item[0], item[1]['id']))
    return [row for _, row in ordered[:limit]]


class RetrievalEngine:
    """Rank the page scope in Milvus, then hydrate only bounded authority hits."""
    MODES = frozenset({'hybrid', 'semantic', 'keyword'})
    PAGE_SCOPE = frozenset({'applicability', 'filters', 'valid_at', 'known_at',
                           'include_unknown', 'ontology_scope', 'ontology_ids'})
    MAX_REFILLS = 4
    MAX_NATIVE_LIMIT = 512

    def __init__(self, repository, encoder, milvus_store=None, *, projection_schema_versions=None):
        import threading
        self.repository, self.encoder, self.milvus = repository, encoder, milvus_store
        self.schema_version = {**PROJECTION_SCHEMA_VERSIONS, **(projection_schema_versions or {})}['milvus']
        self._index = None
        self._index_lock = threading.Lock()

    def _current_index(self, project, expected=None):
        from ..domain.search_filter_catalog import FilterCatalog
        with self._index_lock:
            index = self.repository.projections.search_index(project, self.schema_version)
            if expected is not None and (expected.get('project_id') != project or any(
                    index[name] != expected.get(name) for name in ('index_version', 'watermark'))):
                raise RuntimeError('问答冻结期间项目索引已变化，请重新提问')
            if self._index is not None and any(index[name] != self._index[name]
                    for name in ('index_version', 'watermark')):
                raise RuntimeError('项目索引已变化，请重新检索')
            self._index = index
        return index, FilterCatalog.from_dict(index['catalog'])

    def _rank_current(self, project, query, mode, kinds, target, scope, pg_scope,
                      platform_match, index, catalog, vector):
        from ..repository.scoped_reads import ScopedReads
        if not target:
            return [], 0, True
        limit = min(self.MAX_NATIVE_LIMIT, max(16, target * 2))
        rows_by_version, seen = {}, set()
        ranked = []
        for attempt in range(self.MAX_REFILLS):
            hits = self.milvus.search(project, query, retrieval_mode=mode,
                query_vector=vector, kinds=kinds, k=limit, scope=scope,
                index_version=index['index_version'], catalog=catalog,
                platform_match=platform_match)
            fresh = [hit for hit in hits if hit.get('version_id') not in seen]
            ids = list(dict.fromkeys(hit['record_id'] for hit in fresh if hit.get('record_id')))
            if ids:
                rows = ScopedReads(self.repository).by_ids(project,
                    {**pg_scope, '_platform_match': platform_match}, ids, kinds=kinds)
                rows_by_version.update((row['version_id'], row) for row in rows)
            seen.update(hit.get('version_id') for hit in fresh)
            ranked = []
            for hit in hits:
                row = rows_by_version.get(hit.get('version_id'))
                if row is not None:
                    score = float(hit.get('score') or 0)
                    ranked.append({**{key: value for key, value in row.items() if key != 'embedding'},
                                   'score': score, **({'keyword_score': score} if mode == 'keyword' else {})})
            if len(ranked) >= target or len(hits) < limit:
                return ranked[:target], len(seen), True
            if limit == self.MAX_NATIVE_LIMIT:
                break
            limit = min(self.MAX_NATIVE_LIMIT, limit * 2)
        return ranked[:target], len(seen), False

    def search(self, project_id, query, *, retrieval_mode='hybrid', scope=None,
               content_channels=None, k=10, content_k=None, platform_match=None,
               query_vector_cache=None):
        from ..integrations.milvus_filters import compile_filter, compile_scope
        from ..repository.scoped_reads import ScopedReads
        from ..utils.filters import validate_filter
        from .scoped_ranking import ontology_type_rows, rank_qualified
        if retrieval_mode not in self.MODES:
            raise ValueError('retrieval_mode 必须是 hybrid、semantic 或 keyword')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('搜索查询不能为空')
        if type(k) is not int or not 1 <= k <= 100:
            raise ValueError('搜索结果数量必须在 1 到 100 之间')
        raw = dict(scope or {})
        expected_index = raw.get('_qa_index_snapshot')
        current_qa = (raw.get('_qa_current_snapshot') is True
                      and raw.get('_explicit_known_at') is False
                      and isinstance(expected_index, dict))
        channels = list(content_channels or ['entity', 'relation', 'chunk'])
        if any(kind not in {'entity', 'relation', 'chunk'} for kind in channels):
            raise ValueError('不支持的内容通道')
        if raw.get('kinds') is not None:
            channels = [kind for kind in channels if kind in raw['kinds']]
        if content_k is not None and (not isinstance(content_k, dict) or any(
                kind not in channels or type(value) is not int or not 0 <= value <= 100
                for kind, value in content_k.items())):
            raise ValueError('通道数量限制必须是 0 到 100 之间的整数')
        quotas = ({kind: content_k.get(kind, k) for kind in channels} if content_k is not None else None)
        page_scope = {key: value for key, value in raw.items() if key in self.PAGE_SCOPE}
        validate_filter(page_scope.get('filters'))
        expansion = raw.get('_ontology_expansion')
        entity_guided = bool((expansion or {}).get('entity_matches'))
        terms = [term for term in (expansion or {}).get('labels', [])
                 if term and term.casefold() not in query.casefold()]
        expanded_query = query if entity_guided else ' '.join([query, *terms])
        batches = [([kind], quotas[kind]) for kind in channels] if quotas is not None else [(channels, k)]
        qualified_batches = False
        if page_scope.get('known_at') is not None and not current_qa:
            if retrieval_mode != 'keyword':
                raise ValueError('历史时间视图不支持语义或混合检索')
            hits = []
            for kinds, target in batches:
                if target:
                    hits.extend(ScopedReads(self.repository).ranked_keyword(project_id,
                        {**page_scope, '_platform_match': platform_match}, expanded_query,
                        kinds=kinds, limit=target))
            complete, candidates, backend = True, len(hits), 'postgres_history'
        else:
            if self.milvus is None or not self.milvus.available:
                raise RuntimeError('Milvus 索引不可用，请连接服务并在项目页面向量化')
            index, catalog = self._current_index(project_id, expected_index if current_qa else None)
            native_scope = dict(page_scope)
            if current_qa:
                native_scope.pop('known_at', None)
            try:
                compile_filter(page_scope.get('filters'), catalog)
            except ValueError:
                # The complete valid AST remains a PG authority predicate.
                # Only its explicit eligible-version gate permits omitting it
                # from the native expression; no AST leaf is silently ignored.
                qualified_batches = True
                native_scope['filters'] = None
            compiled = compile_scope(project_id, native_scope, catalog, kinds=channels,
                                     platform_match=platform_match)
            pg_scope = (page_scope if qualified_batches else
                        {**page_scope, 'filters': compiled.canonical_filter})
            vector = None
            if retrieval_mode != 'keyword':
                if not getattr(self.encoder, 'semantic', False):
                    raise RuntimeError('语义和混合检索需要真实语义编码模型')
                if index['embedding_model'] != self.encoder.identity:
                    raise RuntimeError('索引模型与当前编码器不同，请重新向量化当前项目')
                vector = cached_query_vector(self.encoder, expanded_query, query_vector_cache)
            hits, candidates, complete = [], 0, True
            for kinds, target in batches:
                rank = rank_qualified if qualified_batches else RetrievalEngine._rank_current
                rows, checked, finished = rank(self, project_id, expanded_query,
                    retrieval_mode, kinds, target, native_scope, pg_scope, platform_match, index, catalog, vector)
                hits.extend(rows); candidates += checked; complete = complete and finished
            if not self.repository.projections.search_index_is_current(
                    project_id, index['index_version'], self.schema_version, index['watermark']):
                raise RuntimeError('检索期间项目索引已变化，请稍后重试')
            backend = 'milvus'
        direct_hits = list(hits)
        original_hits = direct_hits
        related_hits = []
        compare_original = bool(expansion and expanded_query != query)
        if (entity_guided and terms) or compare_original:
            related_query = ' '.join([query, *terms]) if entity_guided else query
            related_vector = (cached_query_vector(self.encoder, related_query, query_vector_cache)
                              if backend == 'milvus' and retrieval_mode != 'keyword' else None)
            secondary_hits = []
            for kinds, target in batches:
                if not target:
                    continue
                if backend == 'milvus':
                    rank = rank_qualified if qualified_batches else RetrievalEngine._rank_current
                    rows, checked, finished = rank(self, project_id, related_query,
                        retrieval_mode, kinds, target, native_scope, pg_scope, platform_match,
                        index, catalog, related_vector)
                else:
                    rows = ScopedReads(self.repository).ranked_keyword(project_id,
                        {**page_scope, '_platform_match': platform_match}, related_query,
                        kinds=kinds, limit=target)
                    checked, finished = len(rows), True
                secondary_hits.extend(rows)
                if entity_guided:
                    candidates += checked; complete = complete and finished
            if entity_guided:
                related_hits = secondary_hits
            else:
                # This bounded authority ranking is only a membership check.
                # The legacy expanded-query ranking and recall metrics stay intact.
                original_hits = secondary_hits
        type_hits = []
        type_iris = (expansion or {}).get('type_iris') or []
        entity_budget = quotas.get('entity', 0) if quotas is not None else k
        if type_iris and 'entity' in channels and entity_budget:
            type_hits = ontology_type_rows(self.repository, project_id, page_scope,
                type_iris, min(1000, max(50, entity_budget * 5)), platform_match)
            candidates += len(type_hits)
            if type_hits and not entity_guided:
                if quotas is None:
                    hits = rrf_fuse({'retrieval': hits, 'ontology': type_hits}, k)
                else:
                    entity_hits = rrf_fuse({'retrieval': [row for row in hits if row['kind'] == 'entity'],
                                            'ontology': type_hits}, entity_budget)
                    hits = [row for kind in channels for row in
                            (entity_hits if kind == 'entity' else [row for row in hits if row['kind'] == kind])]
        if entity_guided:
            anchors = {item['id'] for item in expansion['entity_matches']}
            if quotas is None:
                related = rrf_fuse({'query': related_hits, 'ontology': type_hits}, max(1, k * 3))
                hits = merge_entity_expansion(direct_hits, related, k, anchors)
            else:
                hits = []
                for kind in channels:
                    budget = quotas[kind]
                    if not budget:
                        continue
                    related = rrf_fuse({'query': [row for row in related_hits if row['kind'] == kind],
                        'ontology': type_hits if kind == 'entity' else []}, max(1, budget * 3))
                    hits.extend(merge_entity_expansion(
                        [row for row in direct_hits if row['kind'] == kind], related, budget, anchors))
        elif expansion:
            original_versions = {row.get('version_id') or row['id'] for row in original_hits}
            hits = [{**row, 'retrieval_channel': 'ontology_type'}
                    if (row.get('version_id') or row['id']) not in original_versions else row
                    for row in hits]
        if (type_iris or entity_guided or compare_original) and backend == 'milvus' and not self.repository.projections.search_index_is_current(
                project_id, index['index_version'], self.schema_version, index['watermark']):
            raise RuntimeError('本体补召回期间项目索引已变化，请稍后重试')
        result = {'hits': hits, 'candidate_count': candidates,
            'filter_stage': 'before_ranking', 'requested_mode': retrieval_mode,
            'active_mode': retrieval_mode, 'degraded': False, '_backend': backend,
            'complete': complete, 'content_channels': channels, 'channel_quotas': quotas,
            'ranking_strategy': ('milvus_scoped_batches' if qualified_batches else
                                 'milvus_native_rrf' if retrieval_mode == 'hybrid' else backend),
            'keyword_backend': ('milvus_bm25' if backend == 'milvus' else 'postgres_history')
                               if retrieval_mode in {'keyword', 'hybrid'} else None,
            'valid_at': page_scope.get('valid_at'), 'known_at': page_scope.get('known_at'),
            'backends': {backend: {'active': True, 'hits': len(hits)}}}
        if not complete:
            result['incomplete_reason'] = 'authority_refill_budget'
        if expansion:
            result['backends']['ontology'] = {'active': bool(type_hits or related_hits), 'hits': len(type_hits) + len(related_hits)}
            result['ontology_expansion'] = {
                'terms': list(expansion.get('terms') or []),
                'subclasses': list(expansion.get('subclasses') or []),
                'type_iris': list(expansion.get('type_iris') or []),
                'keyword_terms': terms, 'type_hits': len(type_hits),
                'used_hits': sum(row.get('retrieval_channel') in {'ontology_entity_type', 'ontology_type'}
                                 for row in hits),
                'strategy': 'ontology_type_channel_and_expanded_query'}
            if entity_guided:
                result['ontology_expansion']['strategy'] = 'entity_types_with_protected_direct_recall'
            for key in ('entity_matches', 'literal_terms', 'entity_terms'):
                if key in expansion:
                    result['ontology_expansion'][key] = expansion[key]
        return result
