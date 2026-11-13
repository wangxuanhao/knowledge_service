"""可选的增量实体融合，不丢弃来源出现记录。"""
from .governance import writable
from ..domain.applicability import scope_identity
from ..domain.entity_identity import (IdentityPolicy, compatible_identity, exact_name, key_conflict,
    merge_identity_keys, projection_readiness, source_boundary, strong_match)
from ..utils.diagnostics import event


def reconcile(service,p,derived,request,receipt):
    if not request.get('resolve_entities',True):return derived,{}
    existing={r['id']:r for r in service.repository.current_records(p)
              if r['kind']=='entity' and not r.get('metadata',{}).get('_deleted')}
    existing_relations={r['id']:r for r in service.repository.current_records(p)
              if r['kind']=='relation' and not r.get('metadata',{}).get('_deleted')}
    pool=dict(existing);updates={};redirects={};expected={}
    from semantica.deduplication import EntityMerger
    merger=EntityMerger(preserve_provenance=True)
    from .ontology import Ontology
    from ..repository import OntologyNotPublished
    reader=getattr(service.repository,'get_ontology',None)
    try:ontology=Ontology(reader(p)['turtle'] if reader else '')
    except OntologyNotPublished:ontology=Ontology('')
    from .ontology import UnknownOntologyTerm
    for record_id,record in list(pool.items()):
        try:
            if ontology.identity_policy(record['type']).declared:
                pool[record_id]={**record,'type':str(ontology.resolve(record['type'],ontology.classes))}
        except UnknownOntologyTerm:
            pass
    for row in derived:
        if row['kind']!='entity':continue
        # 稳定的类型 IRI 是跨版本契约。本体版本 ID 描述抽取来源，
        # 当类型本身在各本体版本间保持不变时，不得据此拆分同一实体。
        try:policy=ontology.identity_policy(row['type'])
        except UnknownOntologyTerm:policy=IdentityPolicy()
        if policy.declared:
            row['type']=str(ontology.resolve(row['type'],ontology.classes))
        eligible=[r for r in pool.values() if compatible_identity(r,row,policy)]
        strong=[r for r in eligible if strong_match(row,r,policy)]
        exact=strong or [r for r in eligible if exact_name(row,r)]
        row.setdefault('metadata',{})['_identity_resolution_checked']={
            'candidates':sorted(candidate['id'] for candidate in exact)}
        target=exact[0] if len(exact)==1 else None
        ambiguous=len(exact)>1
        conflicts=[r for r in pool.values() if r.get('type')==row.get('type') and exact_name(row,r)
                        and key_conflict(row,r,policy)]
        conflicting=bool(conflicts)
        review_reason='ambiguous_exact_identity' if ambiguous else 'conflicting_identity_key'
        review_candidates=exact if ambiguous else conflicts
        if target is not None and source_boundary(target)!=source_boundary(row) and projection_readiness(
                service.repository,p).get('ready') is not True:
            review_candidates=[target]
            review_reason='source_identity_projection_not_ready'
            target=None
            ambiguous=True
        if target is None and (ambiguous or conflicting):
            import hashlib,json
            for candidate in sorted(review_candidates,key=lambda item:item['id']):
                identity=json.dumps([receipt['version_id'],row['id'],candidate['id']],
                    ensure_ascii=False,separators=(',',':')).encode('utf-8')
                request.setdefault('_resolution_reviews',[]).append({
                    'id':'resolution_'+hashlib.sha256(identity).hexdigest(),
                    'source_entity_id':row['id'],'candidate_entity_id':candidate['id'],
                    'score':1.0,'payload':{'reason':review_reason,
                        'document_id':receipt['id'],'document_version_id':receipt['version_id'],
                        'mention':row['text'],'candidate':candidate['text']}})
        if target is None and eligible and not ambiguous and not conflicting:
            probe={**row,'_auto_merge':bool(request.get('auto_merge'))}
            outcome=service.entity_resolver().resolve(
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
        merged=writable(target)
        ontology_versions=list(dict.fromkeys(value for value in [
            *target.get('metadata',{}).get('ontology_versions',[]),
            target.get('ontology_id'),row.get('ontology_id')] if value))
        if row.get('ontology_id'):
            merged['ontology_id']=row['ontology_id']
        merged['metadata']={**target.get('metadata',{}),
            'identity_keys':merge_identity_keys(target,row,policy),
            'aliases':list(dict.fromkeys([*target.get('metadata',{}).get('aliases',[]),row['text']])),
            'ontology_versions':ontology_versions,
            'merge_backend':'semantica.EntityMerger','merge_advice':advice_metadata}
        if target_id in existing:
            updates[target_id]=merged;expected[target_id]=existing[target_id]['version'];pool[target_id]={**target,**merged}
        else:
            target.update(merged)
        row['metadata']={**row.get('metadata',{}),'_deleted':True,'merged_into':target_id,'merge_backend':'semantica.EntityMerger'}
    for row in derived:
        if row['kind'] in {'relation','attribute'}:
            for k in (('subject_id','object_id') if row['kind']=='relation' else ('subject_id',)):
                row[k]=redirects.get(row[k],row[k])
    relation_pool=dict(existing_relations)
    for row in derived:
        if row['kind']!='relation' or row.get('metadata',{}).get('_deleted'):continue
        target=next((candidate for candidate in relation_pool.values() if all(
            candidate.get(key)==row.get(key) for key in
            ('type','subject_id','object_id','valid_from','valid_until')) and
            scope_identity((candidate.get('metadata') or {}).get('applicability')) ==
            scope_identity((row.get('metadata') or {}).get('applicability'))),None)
        if target is None:
            relation_pool[row['id']]=row
            continue
        target_id=target['id']
        merged=writable(target)
        ontology_versions=list(dict.fromkeys(value for value in [
            *target.get('metadata',{}).get('ontology_versions',[]),
            target.get('ontology_id'),row.get('ontology_id')] if value))
        if row.get('ontology_id'):
            merged['ontology_id']=row['ontology_id']
        merged['metadata']={**target.get('metadata',{}),
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
