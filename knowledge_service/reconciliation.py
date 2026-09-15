"""Opt-in incremental entity fusion without discarding source occurrences."""
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
        # A stable type IRI is the cross-version contract. Ontology version IDs
        # describe extraction provenance and must not split the same entity when
        # the type itself survived unchanged across ontology releases.
        eligible=[r for r in pool.values() if all(r.get(k)==row.get(k) for k in ('type','valid_from','valid_until'))]
        target=next((r for r in eligible if row['text'].casefold() in
                    [n.casefold() for n in [r['text'],*r.get('metadata',{}).get('aliases',[])] if isinstance(n,str)]),None)
        if target is None and request.get('auto_merge') and eligible:
            from .retrieval import rank_candidates
            persisted=[r for r in eligible if r.get('embedding')]
            hits=rank_candidates(persisted,row['text'],1,service.encoder)
            if hits and hits[0]['score']>=request.get('merge_threshold',.88):target=pool[hits[0]['id']]
        if target is None:
            pool[row['id']]=row
            continue
        advice_metadata={}
        try:
            advisory=merger.merge_entity_group([{'id':r['id'],'name':r['text'],'type':r['type'],'properties':r.get('properties',{})} for r in (target,row)],strategy='keep_first')
            advice_metadata=getattr(advisory,'metadata',{}) or {}
        except Exception as exc:
            # Exact alias matching above is deterministic; Semantica advice is
            # supplementary and must not invalidate an otherwise safe merge.
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
