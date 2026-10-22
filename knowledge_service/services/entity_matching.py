"""Milvus ranks entity candidates; PostgreSQL owns eligibility and payloads."""
from math import isfinite


class EntityMatchingUnavailable(RuntimeError):
    """Expose a stable ingestion failure without leaking backend diagnostics."""
    def __init__(self, *, configuration=False):
        self.code = ('entity_resolution_configuration' if configuration
                     else 'entity_resolution_unavailable')
        message = ('实体语义解析配置不匹配，请修复 Milvus 模型、维度或索引后重试；也可人工复核'
                   if configuration else
                   '实体语义解析暂不可用，请恢复 Milvus 连接或等待索引同步后重试；也可人工复核')
        super().__init__(message)


def _configuration_failure(retrieval, scope, error):
    from ..integrations.embeddings import EmbeddingDimensionMismatch
    from ..integrations.milvus_store import VectorDimensionMismatch

    encoder = getattr(retrieval, 'encoder', None)
    model = getattr(getattr(retrieval, 'milvus', None), 'encoder_identity', '')
    if encoder is not None and (not getattr(encoder, 'semantic', False)
            or (model and model != encoder.identity)):
        return True
    if (scope or {}).get('known_at') is not None and not (scope or {}).get('_qa_current_snapshot'):
        return True
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if (isinstance(error, (ValueError, EmbeddingDimensionMismatch, VectorDimensionMismatch))
                or getattr(error, 'requires_rebuild', False)):
            return True
        response = getattr(error, 'response', None)
        status = getattr(error, 'status_code', None) or getattr(response, 'status_code', None)
        if status in {400, 401, 403, 404, 422}:
            return True
        error = getattr(error, '__cause__', None)
    return False


def semantic_candidates(retrieval, project_id, text, rows, *, scope=None, limit=12):
    eligible = {row['id']: row for row in rows if row.get('kind') == 'entity'
                and not (row.get('metadata') or {}).get('_deleted')}
    if not eligible:
        return []
    if any(not row.get('version_id') for row in eligible.values()):
        raise EntityMatchingUnavailable()
    try:
        result = retrieval.search(project_id, text, retrieval_mode='semantic',
            scope={**(scope or {}), '_candidate_ids': list(eligible)},
            content_channels=['entity'], k=limit)
    except Exception as exc:
        raise EntityMatchingUnavailable(
            configuration=_configuration_failure(retrieval, scope, exc)) from exc
    matches = []
    seen = set()
    for hit in result['hits']:
        row = eligible.get(hit.get('id'))
        if (row is None or row['id'] in seen
                or hit.get('version_id') != row['version_id']):
            continue
        score = float(hit['score'])
        if not isfinite(score):
            raise EntityMatchingUnavailable(configuration=True)
        seen.add(row['id'])
        matches.append({**{key: value for key, value in row.items() if key != 'embedding'},
                        'score': score})
    if not matches:
        # Dense recall must return a scored candidate when qualified versions
        # exist. An empty or stale projection is never evidence of difference.
        raise EntityMatchingUnavailable()
    return sorted(matches, key=lambda row: (-row['score'], row['id']))[:limit]
