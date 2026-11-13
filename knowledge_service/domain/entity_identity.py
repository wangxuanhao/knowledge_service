"""Published identity policy, separate from source time and applicability."""
from dataclasses import asdict, dataclass
import json

from rdflib import URIRef

from .applicability import scope_identity
from ..core.time import normalize_time


IDENTITY_NAMESPACE = 'https://knowledge.local/schema/'


class IdentityResolutionStale(ValueError):
    code = 'identity_resolution_stale'
    retryable = True


class OperationContextStale(ValueError):
    code = 'operation_context_stale'
    retryable = False


def exact_candidates(mention, rows, policy):
    eligible = [row for row in rows if row.get('id') != mention.get('id')
        and not (row.get('metadata') or {}).get('_deleted')
        and compatible_identity(row, mention, policy)]
    strong = [row for row in eligible if strong_match(mention, row, policy)]
    return strong or [row for row in eligible if exact_name(mention, row)]


@dataclass(frozen=True)
class IdentityPolicy:
    mode: str = 'scoped'
    declared: bool = False
    scope: str = ''
    keys: tuple[str, ...] = ()

    def summary(self):
        return {**asdict(self), 'keys': list(self.keys)}


def class_policy(ontology, class_iri):
    """Only the supplied published graph can authorize a reference policy."""
    node = ontology.resolve(class_iri, ontology.classes)
    def values(name):
        return sorted({str(value) for value in ontology.graph.objects(
            node, URIRef(IDENTITY_NAMESPACE + name))})
    modes, scopes = values('identityPolicy'), values('identityScope')
    if len(modes) > 1 or (modes and modes[0] not in {'reference', 'scoped'}):
        raise ValueError('实体身份策略必须唯一且为 reference/scoped')
    if len(scopes) > 1:
        raise ValueError('实体身份范围必须唯一')
    return IdentityPolicy(modes[0] if modes else 'scoped', bool(modes),
                          scopes[0] if scopes else '', tuple(values('identityKey')))


def source_boundary(record):
    return (normalize_time(record.get('valid_from')), normalize_time(record.get('valid_until')),
            scope_identity((record.get('metadata') or {}).get('applicability')))


def identity_scope(record, policy):
    return json.dumps([policy.scope, (record.get('metadata') or {}).get('identity_scope', '')],
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def key_values(record, policy):
    raw = (record.get('metadata') or {}).get('identity_keys') or {}
    if not isinstance(raw, dict):
        raise ValueError('实体身份键必须为对象')
    result = {}
    for key in policy.keys:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list, bool)) or not isinstance(value, (str, int)):
            raise ValueError('实体身份键必须为非空字符串或整数')
        if isinstance(value, str) and not value.strip():
            raise ValueError('实体身份键不能为空')
        result[key] = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return result


def key_conflict(left, right, policy):
    a, b = key_values(left, policy), key_values(right, policy)
    return any(a[key] != b[key] for key in a.keys() & b.keys())


def merge_identity_keys(left,right,policy):
    """Keep accepted registered identifiers; never let new evidence replace one."""
    if key_conflict(left,right,policy):raise ValueError('实体身份键与已有唯一键冲突')
    raw=(left.get('metadata') or {}).get('identity_keys') or {}
    result=dict(raw)
    incoming=(right.get('metadata') or {}).get('identity_keys') or {}
    result.update({key:incoming[key] for key in policy.keys if key in key_values(right,policy)})
    return result


def strong_match(left, right, policy):
    a, b = key_values(left, policy), key_values(right, policy)
    return bool(a.keys() & b.keys()) and not key_conflict(left, right, policy)


def policy_source_qualified(record, policy):
    """Derive authorization from a published class, including keyed contracts."""
    return record.get('kind') == 'entity' and policy.declared and (
        policy.mode == 'reference' or bool(key_values(record, policy)))


def source_qualified(record):
    """Read a planner-stamped canonical row's source qualification contract."""
    metadata = record.get('metadata') or {}
    return record.get('kind') == 'entity' and (
        metadata.get('identity_policy') == 'reference' or metadata.get('identity_source_qualified') is True)


def compatible_identity(left, right, policy):
    if left.get('type') != right.get('type') or identity_scope(left, policy) != identity_scope(right, policy):
        return False
    if key_conflict(left, right, policy):
        return False
    return policy.mode == 'reference' or strong_match(left, right, policy) or source_boundary(left) == source_boundary(right)


def exact_name(left, right):
    folded = left.get('text', '').strip().casefold()
    return bool(folded) and folded in {name.strip().casefold() for name in
        [right.get('text', ''), *(right.get('metadata') or {}).get('aliases', [])]
        if isinstance(name, str)}


def projection_readiness(repository, project):
    reader = getattr(repository, 'source_identity_projection_readiness', None)
    state = reader(project) if reader else {}
    return state if isinstance(state, dict) else {}


def require_projection_ready(repository, project):
    state = projection_readiness(repository, project)
    if state.get('ready') is not True:
        raise ValueError('跨来源身份写入需要来源上下文实体及关系索引完成构建、对账并追平：' +
                         str(state.get('reason') or 'source_identity_projection_not_ready'))
