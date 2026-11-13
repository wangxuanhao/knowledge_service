"""Finite applicability contexts for ontology checks; unknown is conservative."""
from itertools import product

from .applicability import DIMENSIONS,normalize_applicability


MAX_SCOPE_CONTEXTS=256


def validation_contexts(records):
    """Yield actual compatible fact sets, never a union of disjoint platforms.

    An unknown applicability cannot prove disjointness, so it participates in
    every explicit bucket. Entities with scoped incident facts follow the facts
    in that bucket; an entity without any facts still receives required checks.
    """
    normalized={row['id']:normalize_applicability((row.get('metadata') or {}).get('applicability')) for row in records}
    choices=[];count=1
    for dimension in DIMENSIONS:
        values=sorted({value for scope in normalized.values() for value in scope[dimension]['values']})
        choices.append(values or [None]);count*=len(values) or 1
    if count>MAX_SCOPE_CONTEXTS:
        raise ValueError(f'适用范围校验需要{count}个上下文，超过有界上限{MAX_SCOPE_CONTEXTS}；请缩小本体/实体融合批次')
    incident=set()
    for row in records:
        if row['kind'] in {'attribute','relation'}:
            incident.add(row.get('subject_id'))
            if row['kind']=='relation':incident.add(row.get('object_id'))
    seen=set()
    for values in product(*choices):
        def matches(row):
            scope=normalized[row['id']]
            return all(scope[d]['mode']!='specific' or value in scope[d]['values'] for d,value in zip(DIMENSIONS,values))
        facts=[row for row in records if row['kind']!='entity' and matches(row)]
        endpoints={row.get(field) for row in facts if row['kind'] in {'attribute','relation'}
                   for field in (('subject_id','object_id') if row['kind']=='relation' else ('subject_id',))}
        selected=facts+[row for row in records if row['kind']=='entity' and
            (row['id'] in endpoints or row['id'] not in incident and matches(row))]
        identity=frozenset(row['id'] for row in selected)
        if identity not in seen:
            seen.add(identity)
            yield selected,dict(zip(DIMENSIONS,values))
