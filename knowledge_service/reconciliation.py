"""可选的增量实体融合，不丢弃来源出现记录。"""
from .governance import writable
from .service import public
from .diagnostics import event


def reconcile(service,p,derived,request,receipt):
    if not request.get('resolve_entities',True):return derived,{}
    existing={r['id']:r for r in service.repository.current_records(p)
              if r['kind']=='entity' and not r.get('metadata',{}).get('_deleted')}
    existing_relations={r['id']:r for r in service.repository.current_records(p)
              if r['kind']=='relation' and not r.get('metadata',{}).get('_deleted')}
    pool=dict(existing);updates={};redirects={};expected={}
    from semantica.deduplication import EntityMerger
    merger=EntityMerger(preserve_provenance=True)
    for row in derived:
        if row['kind']!='entity':continue
        # 稳定的类型 IRI 是跨版本契约。本体版本 ID 描述抽取来源，
        # 当类型本身在各本体版本间保持不变时，不得据此拆分同一实体。
        eligible=[r for r in pool.values() if all(r.get(k)==row.get(k) for k in ('type','valid_from','valid_until'))]
        target=next((r for r in eligible if row['text'].casefold() in
                    [n.casefold() for n in [r['text'],*r.get('metadata',{}).get('aliases',[])] if isinstance(n,str)]),None)
        if target is None and eligible:
            from .entity_resolution import EntityResolver
            probe={**row,'_auto_merge':bool(request.get('auto_merge'))}
            outcome=EntityResolver(service.repository,service.encoder).resolve(
                p,probe,review_threshold=request.get('review_threshold',.72),
                merge_threshold=request.get('merge_threshold',.88))
            if outcome.status=='aligned' and outcome.canonical_id in pool:
                target=pool[outcome.canonical_id]
            elif outcome.status=='review' and outcome.candidate_id in pool:
                import hashlib,json
                identity=json.dumps([receipt['version_id'],row['id'],outcome.candidate_id],
                    ensure_ascii=False,separators=(',',':')).encode('utf-8')
                request.setdefault('_resolution_reviews',[]).append({
                    'id':'resolution_'+hashlib.sha256(identity).hexdigest(),
                    'source_entity_id':row['id'],'candidate_entity_id':outcome.candidate_id,
                    'score':outcome.score,'payload':{'reason':outcome.reason,
                        'document_id':receipt['id'],'document_version_id':receipt['version_id'],
                        'mention':row['text'],'candidate':pool[outcome.candidate_id]['text']}})
        if target is None:
            pool[row['id']]=row
            continue
        advice_metadata={}
        try:
            advisory=merger.merge_entity_group([{'id':r['id'],'name':r['text'],'type':r['type'],'properties':r.get('properties',{})} for r in (target,row)],strategy='keep_first')
            advice_metadata=getattr(advisory,'metadata',{}) or {}
        except Exception as exc:
            # 上面的精确别名匹配是确定性的；Semantica 建议只是补充，
            # 不得使本来安全的合并失效。
            event(f'实体融合建议不可用 · 已按精确同名规则继续 · {type(exc).__name__}')
        target_id=target['id'];redirects[row['id']]=target_id
        occurrence={**public(row),'source_id':receipt['id'],'source_version_id':receipt['version_id']}
        merged=writable(target)
        ontology_versions=list(dict.fromkeys(value for value in [
            *target.get('metadata',{}).get('ontology_versions',[]),
            target.get('ontology_id'),row.get('ontology_id')] if value))
        if row.get('ontology_id'):
            merged['ontology_id']=row['ontology_id']
        merged['metadata']={**target.get('metadata',{}),
            'aliases':list(dict.fromkeys([*target.get('metadata',{}).get('aliases',[]),row['text']])),
            'merged_sources':[*target.get('metadata',{}).get('merged_sources',[]),occurrence],
            'ontology_versions':ontology_versions,
            'merge_backend':'semantica.EntityMerger','merge_advice':advice_metadata}
        if target_id in existing:
            updates[target_id]=merged;expected[target_id]=existing[target_id]['version'];pool[target_id]={**target,**merged}
        else:
            target.update(merged)
        row['metadata']={**row.get('metadata',{}),'_deleted':True,'merged_into':target_id,'merge_backend':'semantica.EntityMerger'}
    for row in derived:
        if row['kind']=='relation':
            for k in ('subject_id','object_id'):
                row[k]=redirects.get(row[k],row[k])
    relation_pool=dict(existing_relations)
    for row in derived:
        if row['kind']!='relation' or row.get('metadata',{}).get('_deleted'):continue
        target=next((candidate for candidate in relation_pool.values() if all(
            candidate.get(key)==row.get(key) for key in
            ('type','subject_id','object_id','valid_from','valid_until'))),None)
        if target is None:
            relation_pool[row['id']]=row
            continue
        target_id=target['id']
        occurrence={**public(row),'source_id':receipt['id'],'source_version_id':receipt['version_id']}
        merged=writable(target)
        ontology_versions=list(dict.fromkeys(value for value in [
            *target.get('metadata',{}).get('ontology_versions',[]),
            target.get('ontology_id'),row.get('ontology_id')] if value))
        if row.get('ontology_id'):
            merged['ontology_id']=row['ontology_id']
        merged['metadata']={**target.get('metadata',{}),
            'merged_sources':[*target.get('metadata',{}).get('merged_sources',[]),occurrence],
            'ontology_versions':ontology_versions,'merge_backend':'deterministic.relation_key'}
        if target_id in existing_relations:
            updates[target_id]=merged
            expected[target_id]=existing_relations[target_id]['version']
            relation_pool[target_id]={**target,**merged}
        else:
            target.update(merged)
        row['metadata']={**row.get('metadata',{}),'_deleted':True,'merged_into':target_id,
            'merge_backend':'deterministic.relation_key'}
    return [*derived,*updates.values()],expected
