"""Explicit relevance controls, separate from authorization and business scope."""
from math import isfinite


def retrieval_policy(scope):
    value = (scope or {}).get('min_semantic_score', .35)
    if type(value) not in (int, float) or not isfinite(value) or not -1 <= value <= 1:
        raise ValueError('最低语义相似度必须为 -1 到 1 之间的有限数字')
    strategy = (scope or {}).get('hybrid_strategy', 'fusion')
    if strategy not in {'fusion', 'keyword_rerank'}:
        raise ValueError('混合策略必须为 fusion 或 keyword_rerank')
    return {'min_semantic_score': float(value), 'hybrid_strategy': strategy}
