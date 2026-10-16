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
    if cache is None:
        return encoder.encode([query])[0]
    lock = cache.get('lock')
    if lock is None:
        if 'vector' not in cache:
            cache['vector'] = encoder.encode([query])[0]
        return cache['vector']
    with lock:
        if 'vector' not in cache:
            cache['vector'] = encoder.encode([query])[0]
        return cache['vector']


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
    """先限定范围，再由独立后端各自排名，最后用 RRF 融合。"""
    MODES = frozenset({'hybrid', 'semantic', 'keyword'})

    def __init__(self, repository, encoder, milvus_store=None, *,
                 projection_schema_versions=None, projection_readiness=None):
        self.repository = repository
        self.encoder = encoder
        self.milvus = milvus_store
        self.projection_schema_versions = {
            **PROJECTION_SCHEMA_VERSIONS,
            **(projection_schema_versions or {}),
        }
        self.projection_readiness = (
            projection_readiness or
            getattr(repository, 'projection_ready', None))

    def _projection_ready(self, project_id, target):
        schema_version = self.projection_schema_versions[target]
        if not callable(self.projection_readiness):
            return False
        try:
            return bool(self.projection_readiness(
                project_id, target, schema_version))
        except Exception:
            LOG.warning('%s 投影就绪检查失败，降级 PostgreSQL 本地检索',
                        target, exc_info=True)
            return False

    def _search_via_milvus(self, project_id, query, retrieval_mode,
                           channels, k, content_k, scope,
                           platform_match=None, query_vector_cache=None):
        """Milvus 排名快路径：dense(向量)/sparse(BM25关键词)/原生RRF(混合)。

        Milvus V2 原生执行范围和业务时间过滤。这里只把 top-k version_id 交给
        PostgreSQL 小量回填，在线请求不预载整个项目，也不传 candidate_ids。
        """
        milvus_kinds = [ch for ch in channels if ch in {"entity", "relation", "chunk"}]
        if not milvus_kinds:
            return {
                "hits": [], "candidate_count": 0, "filter_stage": "before_ranking",
                "requested_mode": retrieval_mode, "active_mode": retrieval_mode,
                "degraded": False, "_backend": "milvus",
                "backends": {"milvus": {"active": True, "hits": 0}},
                "content_channels": channels, "channel_quotas": content_k,
                "keyword_backend": "milvus_bm25" if retrieval_mode in {"hybrid", "keyword"} else None,
                "valid_at": scope.get("valid_at"), "known_at": scope.get("known_at"),
            }
        # BM25 does not consume an embedding. Computing one here used to make
        # pure keyword queries pay for the local model's cold start as well.
        query_vector = (cached_query_vector(self.encoder, query, query_vector_cache)
                        if retrieval_mode in {'semantic', 'hybrid'} else None)

        milvus_scope = dict(scope)
        if platform_match:
            applicability = normalize_applicability(
                milvus_scope.get('applicability'))
            applicability['platforms'] = {
                'mode': platform_match,
                'values': (dimension_values(applicability, 'platforms')
                           if platform_match == 'specific_only' else []),
            }
            milvus_scope['applicability'] = applicability

        if retrieval_mode == "hybrid":
            hits = self.milvus.search_hybrid(project_id, query, query_vector,
                                             kinds=milvus_kinds, k=k,
                                             scope=milvus_scope)
            active = "hybrid"
        elif retrieval_mode == "semantic":
            hits = self.milvus.search_dense(project_id, query_vector,
                                            kinds=milvus_kinds, k=k,
                                            scope=milvus_scope)
            active = "semantic"
        else:
            hits = self.milvus.search_bm25(project_id, query,
                                           kinds=milvus_kinds, k=k,
                                           scope=milvus_scope)
            active = "keyword"

        version_ids = [hit['version_id'] for hit in hits if hit.get('version_id')]
        rows = self.repository.current_records_by_version_ids(
            project_id, version_ids)
        by_version = {row['version_id']: row for row in rows}
        ranked = []
        for h in hits:
            row = by_version.get(h["version_id"])
            if row is None:
                continue
            ranked.append({**{key: value for key, value in row.items() if key != "embedding"},
                           "score": h["score"], "_milvus_score": h["score"]})

        if content_k is not None:
            per_channel = []
            for channel in channels:
                quota = content_k.get(channel, k)
                per_channel.extend([r for r in ranked if r["kind"] == channel][:quota])
            ranked = per_channel
        ranked = ranked[:k]

        return {
            "hits": ranked,
            "candidate_count": len(version_ids),
            "filter_stage": "before_ranking",
            "requested_mode": retrieval_mode,
            "active_mode": active,
            "degraded": False,
            "_backend": "milvus",
            "backends": {
                "milvus": {"active": True, "hits": len(ranked)},
                "keyword": {"active": retrieval_mode in {"hybrid", "keyword"},
                            "hits": len(ranked) if active == "keyword" else 0},
                "semantic": {"active": retrieval_mode in {"hybrid", "semantic"},
                             "hits": len(ranked) if active == "semantic" else 0},
            },
            "content_channels": channels,
            "channel_quotas": content_k,
            "keyword_backend": "milvus_bm25" if retrieval_mode in {"hybrid", "keyword"} else None,
            "valid_at": scope.get("valid_at"),
            "known_at": scope.get("known_at"),
        }

    def search(self, project_id, query, *, retrieval_mode='hybrid', scope=None,
               content_channels=None, k=10, content_k=None, candidates=None,
               platform_match=None, candidates_authoritative=False,
               fallback_cache=None, query_vector_cache=None):
        if retrieval_mode not in self.MODES:
            raise ValueError('retrieval_mode 必须是 hybrid、semantic 或 keyword')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('搜索查询不能为空')
        if type(k) is not int or not 1 <= k <= 100:
            raise ValueError('搜索结果数量必须在 1 到 100 之间')
        scope = dict(scope or {})
        if platform_match not in {None, 'specific_only', 'all_only',
                                  'unknown_only'}:
            raise ValueError('platform_match 必须是 specific_only、all_only 或 unknown_only')
        candidate_ids = scope.pop('_candidate_ids', None)
        lexical_aliases = scope.pop('_lexical_aliases', {})
        # P0-1 本体查询扩展：默认不存在 → 下面的所有分支一行都不会执行，
        # 关闭时的响应与改动前逐字段相同（回归基线的硬约束）。
        expansion = scope.pop('_ontology_expansion', None)
        expansion_type_iris = set((expansion or {}).get('type_iris') or ())
        expansion_terms = [term for term in (expansion or {}).get('labels') or ()
                           if term and term.casefold() not in query.casefold()]
        channels = list(content_channels or ['entity', 'relation', 'chunk'])
        if any(channel not in {'entity', 'relation', 'chunk'} for channel in channels):
            raise ValueError('不支持的内容通道')
        if content_k is not None:
            if not isinstance(content_k, dict) or any(
                    channel not in channels or type(limit) is not int or not 0 <= limit <= 100
                    for channel, limit in content_k.items()):
                raise ValueError('通道数量限制必须是 0 到 100 之间的整数')
            channel_quotas = {channel: content_k.get(channel, k) for channel in channels}
        else:
            channel_quotas = None
        requested_kinds = scope.pop('kinds', None)
        if requested_kinds is not None:
            channels = [kind for kind in channels if kind in requested_kinds]
        # Milvus 快路径：连接可用且非历史时间视图时，整个排名交给 Milvus（向量留在
        # Milvus，PostgreSQL 不再读 vector 列）。Milvus 失败会透明降级到下方本地逻辑。
        # 开了本体扩展就不走它：类型通道（子类实例召回）是本地按已过滤候选算出来的，
        # Milvus 只负责排名，交出去会静默丢掉子类命中。扩展是显式开关，默认不受影响。
        native_scope = ((scope.get('known_at') is None or scope.get('_qa_current_snapshot') is True)
                        and (scope.get('ontology_scope') or 'all') == 'all'
                        and scope.get('filters') is None
                        and candidate_ids is None
                        and not lexical_aliases
                        and not expansion_type_iris)
        projection_current = (self.milvus is not None and self.milvus.available
                              and (scope.get('known_at') is None or scope.get('_qa_current_snapshot') is True))
        milvus_ready = (self._projection_ready(project_id, 'milvus')
                        if projection_current else False)
        milvus_current = projection_current and native_scope
        projection_stale = projection_current and not milvus_ready
        use_milvus = milvus_current and milvus_ready
        if use_milvus:
            try:
                LOG.debug('检索走 Milvus V2 原生范围快路径：mode=%s', retrieval_mode)
                return self._search_via_milvus(
                    project_id, query, retrieval_mode, channels, k,
                    channel_quotas, scope, platform_match,
                    query_vector_cache)
            except Exception:
                LOG.warning('Milvus V2 排名失败，降级到 PostgreSQL', exc_info=True)
                use_milvus = False
        include_embeddings = (retrieval_mode in {'hybrid', 'semantic'}
                              and not use_milvus) if channels else False
        # A caller-provided candidate list may itself have come from a stale
        # projection. Once readiness fails, discard it and re-read authority.
        authoritative_candidates = (candidates if candidates_authoritative
                                    or not projection_stale else None)
        fallback_applicability = (
            (fallback_cache or {}).get('applicability')
            or scope.get('applicability'))
        if platform_match in {'all_only', 'unknown_only'}:
            fallback_applicability = normalize_applicability(
                fallback_applicability)
            fallback_applicability['platforms'] = {
                'mode': 'unknown', 'values': []}
        if authoritative_candidates is not None:
            scoped_rows = list(authoritative_candidates)
        elif fallback_cache is not None:
            lock = fallback_cache.get('lock')
            if lock is None:
                cached_rows = fallback_cache.get('rows')
                if cached_rows is None:
                    cached_rows = (self.repository.query(
                        project_id, filters=scope.get('filters'),
                        valid_at=scope.get('valid_at'),
                        known_at=scope.get('known_at'),
                        include_unknown=scope.get('include_unknown', True),
                        kinds=channels, applicability=fallback_applicability,
                        include_embeddings=include_embeddings) if channels else [])
                    fallback_cache['rows'] = list(cached_rows)
                scoped_rows = list(cached_rows)
            else:
                with lock:
                    cached_rows = fallback_cache.get('rows')
                    if cached_rows is None:
                        cached_rows = (self.repository.query(
                            project_id, filters=scope.get('filters'),
                            valid_at=scope.get('valid_at'),
                            known_at=scope.get('known_at'),
                            include_unknown=scope.get('include_unknown', True),
                            kinds=channels, applicability=fallback_applicability,
                            include_embeddings=include_embeddings) if channels else [])
                        fallback_cache['rows'] = list(cached_rows)
                scoped_rows = list(cached_rows)
        else:
            scoped_rows = (self.repository.query(
                project_id, filters=scope.get('filters'), valid_at=scope.get('valid_at'),
                known_at=scope.get('known_at'),
                include_unknown=scope.get('include_unknown', True),
                kinds=channels,
                applicability=fallback_applicability,
                include_embeddings=include_embeddings) if channels else [])
        # PERF：`candidates=` 是快路径——知识对话等调用方传入已经读过的行，
        # 因此每个请求只扫描一次范围，而不是两次。没有它，每次搜索都会重读
        # 整个项目（见 Repository.query）。keyword 模式最便宜，因为它还跳过
        # 嵌入向量，这就是为什么 /search 的 keyword 比 hybrid 快约 3 倍。
        scoped_rows = [row for row in scoped_rows if not row.get('metadata', {}).get('_deleted')
                       and not row.get('metadata', {}).get('_audit')]
        if platform_match:
            requested_platforms = set(dimension_values(
                normalize_applicability(scope.get('applicability')), 'platforms'))
            def platform_matches(row):
                applicability = (row.get('metadata') or {}).get('applicability')
                mode = dimension_mode(applicability, 'platforms')
                if platform_match == 'all_only':
                    return mode == 'all'
                if platform_match == 'unknown_only':
                    return mode == 'unknown'
                return (mode == 'specific' and bool(requested_platforms.intersection(
                    dimension_values(applicability, 'platforms'))))
            scoped_rows = [row for row in scoped_rows if platform_matches(row)]
        visible_entities = {row['id'] for row in scoped_rows if row['kind'] == 'entity'}
        scoped_rows = [row for row in scoped_rows if row['kind'] != 'relation' or
                       {row.get('subject_id'), row.get('object_id')}.issubset(visible_entities)]
        candidates = [row for row in scoped_rows if row['kind'] in channels]
        if candidate_ids is not None:
            allowed_ids = set(candidate_ids)
            candidates = [row for row in candidates if row['id'] in allowed_ids]
        if lexical_aliases:
            candidates = [{**row, '_retrieval_aliases': lexical_aliases.get(row['id'], [])}
                          for row in candidates]

        def _rank_ontology_types():
            """类型通道：命中类与其子类的实体，即使文本里没有查询词也算召回。"""
            rows = [row for row in candidates
                    if row['kind'] == 'entity' and row.get('type') in expansion_type_iris]
            return sorted(({key: value for key, value in row.items() if key != 'embedding'}
                           for row in rows), key=lambda row: row['id'])

        rankings = {}
        errors = {}
        keyword_backend = None
        per_backend_limit = (sum(max(limit * 5, 50) for limit in channel_quotas.values())
                             if channel_quotas else max(k * 5, 50))
        def _rank_keyword_once(term):
            """单次关键词排名：历史视图走词法扫描，当前视图走 FTS + 词法兜底。"""
            if scope.get('known_at'):
                return _lexical_rank(candidates, term, per_backend_limit), 'historical_scan'
            indexed_channels = [kind for kind in channels if kind in {'entity', 'chunk'}]
            indexed = []
            for kind in indexed_channels:
                limit = max(channel_quotas[kind] * 5, 50) if channel_quotas else per_backend_limit
                indexed.extend(self.repository.keyword_candidates(
                    project_id, term, kinds=[kind], limit=limit,
                    candidate_ids=[row['id'] for row in candidates if row['kind'] == kind],
                    records=candidates,
                ))
            indexed_ids = {row['id'] for row in indexed}
            fallback = [row for row in _lexical_rank(candidates, term, per_backend_limit)
                        if row['id'] not in indexed_ids]
            return [*indexed, *fallback][:per_backend_limit], 'fts5_current'

        def _rank_keyword():
            """keyword 通道排名：原查询，外加（开关打开时的）本体扩展词 OR 合并。"""
            ranking, backend = _rank_keyword_once(query)
            if not expansion_terms:
                # 默认路径：一次查询、原样返回 —— 开关关闭时行为与改动前一致。
                return ranking, backend
            per_term = [ranking]
            for term in expansion_terms:
                try:
                    per_term.append(_rank_keyword_once(term)[0])
                except (ValueError, RuntimeError, DatabaseError):
                    # 单个扩展词失败不该拖垮整条关键词通道：丢掉这个词，继续。
                    LOG.warning('本体扩展词 %r 的关键词检索失败，已忽略该词', term,
                                exc_info=True)
            return merge_keyword_rankings(per_term, per_backend_limit), backend

        if retrieval_mode in {'hybrid', 'keyword'}:
            try:
                keyword, keyword_backend = _rank_keyword()
                rankings['keyword'] = keyword
            except (ValueError, RuntimeError, DatabaseError) as exc:
                errors['keyword'] = str(exc)
        if retrieval_mode in {'hybrid', 'semantic'}:
            try:
                shared_query_vector = (
                    cached_query_vector(self.encoder, query,
                                        query_vector_cache)
                    if query_vector_cache is not None and candidates else None)
                if channel_quotas:
                    semantic = []
                    for kind in channels:
                        rows = [row for row in candidates if row['kind'] == kind]
                        semantic.extend(rank_candidates(
                            rows, query, min(max(channel_quotas[kind] * 5, 50), len(rows)),
                            self.encoder, query_vector=shared_query_vector))
                    rankings['semantic'] = semantic
                else:
                    rankings['semantic'] = rank_candidates(
                        candidates, query, min(per_backend_limit, len(candidates)),
                        self.encoder, query_vector=shared_query_vector)
            except (ValueError, RuntimeError) as exc:
                errors['semantic'] = str(exc)
        # semantic 模式失败时降级 keyword 兜底（向量已迁 Milvus，本地无向量时 semantic 不可用）
        if retrieval_mode == 'semantic' and 'semantic' in errors and 'keyword' not in rankings:
            try:
                keyword, keyword_backend = _rank_keyword()
                rankings['keyword'] = keyword
            except (ValueError, RuntimeError, DatabaseError) as exc:
                errors['keyword'] = str(exc)
        # P0-1 类型通道：只在开关打开且真的命中类型时加入（默认路径一行都不执行）。
        if expansion_type_iris:
            ontology_rows = _rank_ontology_types()
            if ontology_rows:
                rankings['ontology'] = ontology_rows
                LOG.debug('本体查询扩展命中类型 %d 个，类型通道候选 %d 条',
                          len(expansion_type_iris), len(ontology_rows))
        # 补充通道不参与 active_mode / degraded 判定：那两个字段描述的是"关键词/语义
        # 后端有没有按请求工作"，而本体类型通道是额外召回来源；掺进去会把纯 keyword
        # 请求误报成混合模式，也会把"语义降级"的告警淹掉。
        lexical_rankings = {name: rows for name, rows in rankings.items()
                            if name != 'ontology'}
        active = [backend for backend, rows in lexical_rankings.items() if rows or not candidates]
        if not lexical_rankings:
            raise RuntimeError('没有可用的检索后端：' + '; '.join(errors.values()))
        if channel_quotas is not None:
            hits = []
            for channel in channels:
                quota = channel_quotas[channel]
                if not quota:
                    continue
                channel_rankings = {
                    backend: [row for row in rows if row['kind'] == channel]
                    for backend, rows in rankings.items()
                }
                if len(channel_rankings) > 1:
                    hits.extend(rrf_fuse(channel_rankings, quota))
                else:
                    hits.extend(next(iter(channel_rankings.values()))[:quota])
            active_mode = 'hybrid' if len(lexical_rankings) > 1 else next(iter(lexical_rankings))
        elif len(rankings) > 1:
            # 融合用全部来源（含本体类型通道，否则纯 keyword 模式下类型召回会被丢掉），
            # 但 active_mode 只反映关键词/语义后端，避免把"额外召回"报成降级。
            hits = rrf_fuse(rankings, k)
            active_mode = 'hybrid' if len(lexical_rankings) > 1 else next(iter(lexical_rankings))
        else:
            active_mode, rows = next(iter(lexical_rankings.items()))
            hits = rows[:k]
        response = {
            'hits': hits,
            'candidate_count': len(candidates),
            'filter_stage': 'before_ranking',
            'requested_mode': retrieval_mode,
            'active_mode': active_mode,
            'degraded': active_mode != retrieval_mode,
            'backends': {name: {'active': name in rankings, 'hits': len(rankings.get(name, [])),
                                **({'error': errors[name]} if name in errors else {})}
                         for name in ('keyword', 'semantic')},
            'content_channels': channels,
            'channel_quotas': channel_quotas,
            'keyword_backend': keyword_backend,
            'valid_at': scope.get('valid_at'),
            'known_at': scope.get('known_at'),
        }
        if projection_stale:
            response.update(
                _backend='postgres_local', degraded=True,
                degraded_reason='milvus_projection_stale')
            response['backends']['milvus'] = {
                'active': False, 'hits': 0,
                'error': 'projection cursor is missing or behind authority',
            }
        if 'ontology' in rankings or expansion_type_iris:
            # 扩展的台账：命中了哪些类/子类、关键词通道 OR 了哪些词、类型通道召回多少条。
            # 没有它，命中数变化就成了"魔法"，排查时无从下手。
            response['backends']['ontology'] = {
                'active': 'ontology' in rankings,
                'hits': len(rankings.get('ontology', [])),
            }
            response['ontology_expansion'] = {
                'terms': list((expansion or {}).get('terms') or []),
                'subclasses': list((expansion or {}).get('subclasses') or []),
                'type_iris': sorted(expansion_type_iris),
                'keyword_terms': list(expansion_terms),
                'type_hits': len(rankings.get('ontology', [])),
            }
        return response
