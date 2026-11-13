"""Hydrate ranked windows only after exact authority/support qualification."""
from math import isfinite

from ..domain.applicability import build_scope_predicate, dimension_mode
from ..domain.entity_identity import source_qualified


def qualified_hits(repository, project, scope, rows, hits):
    by_version={row['version_id']:row for row in rows}
    ids=list(dict.fromkeys(row['id'] for row in rows))
    if hasattr(repository,'_db') and ids:
        from ..repository.projection_contexts import ProjectionContexts
        supports=ProjectionContexts(repository).collect(project,rows,known_at=scope.get('known_at'))
    else:
        supports=(repository.current_source_contexts(project,ids,known_at=scope.get('known_at'))
                  if hasattr(repository,'current_source_contexts') and ids else {})
    predicate=build_scope_predicate(scope.get('applicability'),scope.get('valid_at'),scope.get('include_unknown',True))
    platform_modes={'all_only':'all','unknown_only':'unknown','specific_only':'specific'}
    result=[];seen=set()
    for hit in hits:
        row=by_version.get(hit.get('version_id'))
        if row is None or row['id']!=hit.get('record_id') or row['id'] in seen:continue
        context_id=hit.get('support_context_id')
        support=None
        if context_id:
            support=next((value for value in supports.get(row['id'],[]) if value['id']==context_id
                and value['version']==hit.get('support_context_version')),None)
            if support is None:continue
            if source_qualified(row) and not predicate.matches(support):continue
            expected_mode=platform_modes.get(scope.get('_platform_match'))
            if source_qualified(row) and expected_mode and dimension_mode((support.get('metadata') or {}).get('applicability'),'platforms')!=expected_mode:continue
        elif source_qualified(row):continue
        score=float(hit.get('score') or 0)
        if not isfinite(score):raise RuntimeError('Milvus 返回无效的排名分数')
        value={key:val for key,val in row.items() if key!='embedding'}
        value.update({key:hit[key] for key in ('rrf_score','backend_ranks') if key in hit},score=score)
        if support is not None:
            value['matched_source_context']={key:support.get(key) for key in
                ('id','version','assertion_id','document_id','document_version_id','record_version_id',
                 'support_fact_id','support_fact_version_id','valid_from','valid_until','metadata')}
        if row.get('kind')=='attribute':
            actual=row.get('value');start=hit.get('window_start',0);end=hit.get('window_end')
            if isinstance(actual,str):
                if type(start) is not int or start<0 or start>len(actual):continue
                end=min(len(actual),start+6000) if type(end) is not int else min(end,start+6000,len(actual))
                if end<start:continue
                matched=actual[start:end]
                value.update(value=matched,matched_value=matched,value_truncated=start>0 or end<len(actual),
                    value_length=len(actual),window_start=start,window_end=end)
            else:value.update(matched_value=actual,value_truncated=False)
            value.update(owner_id=row.get('subject_id'),property_id=row.get('type'))
            value['text']=str(value['matched_value'])[:6000]
        seen.add(row['id']);result.append(value)
    return result
