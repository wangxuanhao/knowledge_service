"""正交的摄取就绪状态与持久化运行状态辅助函数。"""

READINESS_FIELDS = (
    'document_ready', 'keyword_ready', 'semantic_ready',
    'candidate_ready', 'graph_ready',
)


def readiness(**values):
    unknown = set(values) - set(READINESS_FIELDS)
    if unknown:
        raise ValueError('未知的就绪字段：' + ', '.join(sorted(unknown)))
    state = {field: False for field in READINESS_FIELDS}
    for field, value in values.items():
        if type(value) is not bool:
            raise ValueError(f'{field} 就绪状态必须是布尔值')
        state[field] = value
    state['search_ready'] = state['keyword_ready'] or state['semantic_ready']
    return state


def merge_readiness(current, patch):
    current = {field: bool((current or {}).get(field, False)) for field in READINESS_FIELDS}
    if not isinstance(patch, dict):
        raise ValueError('就绪状态补丁必须是对象')
    unknown = set(patch) - set(READINESS_FIELDS)
    if unknown:
        raise ValueError('未知的就绪字段：' + ', '.join(sorted(unknown)))
    for field, value in patch.items():
        if type(value) is not bool:
            raise ValueError(f'{field} 就绪状态必须是布尔值')
        current[field] = value
    return readiness(**current)
