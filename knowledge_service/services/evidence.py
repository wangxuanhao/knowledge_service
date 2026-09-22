"""将范围内的记录解析到其原始来源版本与可信定位信息（业务逻辑）。

路由层在 api.evidence（POST /records/{record_id}/evidence）。
"""
from ..models import Scope


def evidence(service, project_id, record_id, scope):
    rows=service.scoped(project_id,scope)
    row=next((r for r in rows if r['id']==record_id),None)
    if row is None:raise KeyError(record_id)
    repo=service.repository
    assertions=repo.list_assertions(project_id,canonical_record_id=record_id)
    origins=[]
    for assertion in assertions:
        origin={**assertion['payload'],
            'source_id':assertion.get('document_id'),
            'metadata':{**assertion['payload'].get('metadata',{}),
                'source_version_id':assertion.get('document_version_id'),
                'chunk_id':assertion.get('chunk_id'),
                'start_char':assertion.get('start_char'),
                'end_char':assertion.get('end_char'),
                'passage':assertion.get('quote')},
            '_assertion_id':assertion['id'],'_assertion_status':assertion['status']}
        origins.append(origin)
    if not origins:
        origins=[row]+[r for r in row.get('metadata',{}).get('merged_sources',[]) if isinstance(r,dict)]
    documents=[];seen=set()
    for origin in origins[:20]:
        meta=origin.get('metadata',{})
        source_id=origin.get('source_id')
        if origin.get('kind')=='document':source_id=origin['id']
        if not source_id:continue
        versions=repo.history(project_id,source_id)
        pin=meta.get('source_version_id')
        if origin.get('kind')=='document':pin=origin.get('version_id')
        if pin:
            doc=next((v for v in versions if v['version_id']==pin),None)
        else:
            cutoff=origin.get('recorded_at',row['recorded_at'])
            doc=next((v for v in reversed(versions) if v['recorded_at']<=cutoff),None)
        if not doc or doc['kind']!='document' or doc['version_id'] in seen:continue
        seen.add(doc['version_id'])
        text=doc['text'];start=meta.get('start_char');end=meta.get('end_char')
        mode='unlocated';reason='来源已记录，但没有可核验的原文定位信息。'
        if type(start) is int and type(end) is int and 0<=start<end<=len(text):
            mode='offset';reason='按记录保存的原文字符范围定位（可能是抽取片段范围）。'
        else:
            start=end=None
            passage=meta.get('passage')
            if not isinstance(passage,str):passage=''
            needles=[(passage,'passage','证据文本首次匹配'),(origin.get('text',''),'text','名称 / 内容首次匹配，不代表原始抽取位置')]
            for needle,kind,label in needles:
                if needle and needle in text:
                    start=text.index(needle);end=start+len(needle);mode=kind;reason=label;break
        before=text[:start] if start is not None else text
        highlight=text[start:end] if start is not None else ''
        after=text[end:] if end is not None else ''
        documents.append({'id':doc['id'],'version':doc['version'],'version_id':doc['version_id'],
                          'assertion_id':origin.get('_assertion_id'),
                          'assertion_status':origin.get('_assertion_status'),
                          'title':doc.get('metadata',{}).get('title') or doc.get('metadata',{}).get('source_file') or doc['id'],
                          'source_content':doc.get('metadata',{}).get('legacy',{}).get('source_content','original'),
                          'mode':mode,'reason':reason,'start_char':start,'end_char':end,
                          'before':before,'highlight':highlight,'after':after,
                          'preview':text[max(0,(start or 0)-100):min(len(text),(start or 0)+500)]})
    counts={status:sum(item['status']==status for item in assertions)
            for status in ('accepted','pending','contradicting','rejected','superseded')}
    return {'record_id':record_id,'record_version':row['version'],'documents':documents,
            'assertions':[{key:value for key,value in item.items() if key!='payload'} for item in assertions],
            'support_counts':counts,
            'message':'' if documents else '这条知识没有可解析的来源文档。可能是手工录入，或旧数据未保存来源；不能精确回到原文。'}