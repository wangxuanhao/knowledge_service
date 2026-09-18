"""对已过滤的候选集排序，绝不做全局 top-k 过滤。"""
import json
import sqlite3
import logging
import re
from time import perf_counter
import numpy as np

LOG = logging.getLogger('knowledge_service.retrieval')


def has_vector(row):
    """当一行带有可用向量值时返回 True。

    存储的向量要么是旧的 float 列表，要么是从 float32 列读出的 numpy 数组。
    两者都是定长容器，因此显式测试长度——一旦向量以 ndarray 形式到达，
    直接用 ``if row.get('embedding')`` 会抛出 “truth value of an array is ambiguous”。
    """
    vector = row.get('embedding')
    return vector is not None and len(vector) > 0


def rank_candidates(candidates, query, k, encoder, timings=None):
    if not candidates:
        return []
    if any(not has_vector(row) for row in candidates):
        raise RuntimeError('语义索引尚未就绪，请先构建语义索引；仍可使用关键词检索和图谱浏览')
    vectors = []
    started = perf_counter()
    query_vector = np.asarray(encoder.encode([query]), dtype='float32')
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
        if phrase or matched:
            ranked.append(({**{key: value for key, value in row.items() if key != 'embedding'},
                            'keyword_score': phrase * 10 + matched}, phrase * 10 + matched))
    ranked.sort(key=lambda item: (-item[1], item[0]['id']))
    return [item[0] for item in ranked[:limit]]


class RetrievalEngine:
    """先限定范围，再由独立后端各自排名，最后用 RRF 融合。"""
    MODES = frozenset({'hybrid', 'semantic', 'keyword'})

    def __init__(self, repository, encoder, milvus_store=None):
        self.repository = repository
        self.encoder = encoder
        self.milvus = milvus_store

    def _search_via_milvus(self, project_id, query, retrieval_mode, candidates,
                           channels, k, content_k, scope):
        """Milvus 排名快路径：dense(向量)/sparse(BM25关键词)/原生RRF(混合)。

        candidates 是已由 scope 过滤的候选 rows（含 version_id）。Milvus 只做排名，
        返回 version_id+score 后按 version_id 从 candidates 回填完整 row，不二次查
        SQLite。content_k 在回填后按 channel 分通道截断。
        """
        candidate_ids = [row["version_id"] for row in candidates if "version_id" in row]
        by_version = {row["version_id"]: row for row in candidates if "version_id" in row}
        milvus_kinds = [ch for ch in channels if ch in {"entity", "relation", "chunk"}]
        query_vector = self.encoder.encode([query])[0]

        if retrieval_mode == "hybrid":
            hits = self.milvus.search_hybrid(project_id, query, query_vector,
                                             kinds=milvus_kinds, k=k, candidate_ids=candidate_ids)
            active = "hybrid"
        elif retrieval_mode == "semantic":
            hits = self.milvus.search_dense(project_id, query_vector,
                                            kinds=milvus_kinds, k=k, candidate_ids=candidate_ids)
            active = "semantic"
        else:
            hits = self.milvus.search_bm25(project_id, query,
                                           kinds=milvus_kinds, k=k, candidate_ids=candidate_ids)
            active = "keyword"

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
            "candidate_count": len(candidates),
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
               content_channels=None, k=10, content_k=None, candidates=None):
        if retrieval_mode not in self.MODES:
            raise ValueError('retrieval_mode 必须是 hybrid、semantic 或 keyword')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('搜索查询不能为空')
        if type(k) is not int or not 1 <= k <= 100:
            raise ValueError('搜索结果数量必须在 1 到 100 之间')
        scope = dict(scope or {})
        candidate_ids = scope.pop('_candidate_ids', None)
        lexical_aliases = scope.pop('_lexical_aliases', {})
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
        # Milvus，SQLite 不再读 vector 列）。Milvus 失败会透明降级到下方本地逻辑。
        use_milvus = (self.milvus is not None and self.milvus.available
                      and scope.get('known_at') is None)
        include_embeddings = (retrieval_mode in {'hybrid', 'semantic'}
                              and not use_milvus) if channels else False
        scoped_rows = list(candidates) if candidates is not None else (
            self.repository.query(
                project_id, filters=scope.get('filters'), valid_at=scope.get('valid_at'),
                known_at=scope.get('known_at'),
                include_unknown=scope.get('include_unknown', True),
                include_embeddings=include_embeddings) if channels else [])
        # PERF：`candidates=` 是快路径——知识对话等调用方传入已经读过的行，
        # 因此每个请求只扫描一次范围，而不是两次。没有它，每次搜索都会重读
        # 整个项目（见 Repository.query）。keyword 模式最便宜，因为它还跳过
        # 嵌入向量，这就是为什么 /search 的 keyword 比 hybrid 快约 3 倍。
        scoped_rows = [row for row in scoped_rows if not row.get('metadata', {}).get('_deleted')
                       and not row.get('metadata', {}).get('_audit')]
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
        if use_milvus:
            try:
                LOG.debug('检索走 Milvus 快路径：mode=%s 候选=%d', retrieval_mode, len(candidates))
                return self._search_via_milvus(project_id, query, retrieval_mode,
                                               candidates, channels, k, channel_quotas, scope)
            except Exception:
                LOG.warning('Milvus 排名失败，降级到本地检索', exc_info=True)
        rankings = {}
        errors = {}
        keyword_backend = None
        per_backend_limit = (sum(max(limit * 5, 50) for limit in channel_quotas.values())
                             if channel_quotas else max(k * 5, 50))
        def _rank_keyword():
            """keyword 通道排名：历史视图走词法扫描，当前视图走 FTS5 + 词法兜底。"""
            if scope.get('known_at'):
                return _lexical_rank(candidates, query, per_backend_limit), 'historical_scan'
            indexed_channels = [kind for kind in channels if kind in {'entity', 'chunk'}]
            indexed = []
            for kind in indexed_channels:
                limit = max(channel_quotas[kind] * 5, 50) if channel_quotas else per_backend_limit
                indexed.extend(self.repository.keyword_candidates(
                    project_id, query, kinds=[kind], limit=limit,
                    candidate_ids=[row['id'] for row in candidates if row['kind'] == kind],
                    records=candidates,
                ))
            indexed_ids = {row['id'] for row in indexed}
            fallback = [row for row in _lexical_rank(candidates, query, per_backend_limit)
                        if row['id'] not in indexed_ids]
            return [*indexed, *fallback][:per_backend_limit], 'fts5_current'

        if retrieval_mode in {'hybrid', 'keyword'}:
            try:
                keyword, keyword_backend = _rank_keyword()
                rankings['keyword'] = keyword
            except (ValueError, RuntimeError, sqlite3.Error) as exc:
                errors['keyword'] = str(exc)
        if retrieval_mode in {'hybrid', 'semantic'}:
            try:
                if channel_quotas:
                    semantic = []
                    for kind in channels:
                        rows = [row for row in candidates if row['kind'] == kind]
                        semantic.extend(rank_candidates(
                            rows, query, min(max(channel_quotas[kind] * 5, 50), len(rows)), self.encoder))
                    rankings['semantic'] = semantic
                else:
                    rankings['semantic'] = rank_candidates(
                        candidates, query, min(per_backend_limit, len(candidates)), self.encoder)
            except (ValueError, RuntimeError) as exc:
                errors['semantic'] = str(exc)
        # semantic 模式失败时降级 keyword 兜底（向量已迁 Milvus，本地无向量时 semantic 不可用）
        if retrieval_mode == 'semantic' and 'semantic' in errors and 'keyword' not in rankings:
            try:
                keyword, keyword_backend = _rank_keyword()
                rankings['keyword'] = keyword
            except (ValueError, RuntimeError, sqlite3.Error) as exc:
                errors['keyword'] = str(exc)
        active = [backend for backend, rows in rankings.items() if rows or not candidates]
        if not rankings:
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
            active_mode = 'hybrid' if len(rankings) > 1 else next(iter(rankings))
        elif len(rankings) > 1:
            hits = rrf_fuse(rankings, k)
            active_mode = 'hybrid'
        else:
            active_mode, rows = next(iter(rankings.items()))
            hits = rows[:k]
        return {
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
