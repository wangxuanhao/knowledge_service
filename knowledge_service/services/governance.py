"""版本化实体治理：Semantica 建议、事务化服务写入。"""
import json
from uuid import uuid4

from ..models import RecordWrite
from .service import public
from ..core.time import utc_now


def writable(row):
    return {key:value for key,value in row.items() if key in RecordWrite.model_fields}


class Governance:
    def __init__(self, service):
        self.service = service
        self.repo = service.repository

    def _rows(self,p):
        return {r['id']:r for r in self.repo.current_records(p)}

    def alias(self,p,entity_id,alias,expected_version):
        alias = alias.strip()
        if not alias or len(alias)>500:
            raise ValueError('别名必须包含 1..500 个字符')
        with self.service.lock:
            rows=self._rows(p)
            row=rows.get(entity_id)
            if not row or row['kind']!='entity' or row.get('metadata',{}).get('_deleted'):
                raise KeyError(entity_id)
            for other in rows.values():
                if other['id']!=entity_id and other['kind']=='entity' and not other.get('metadata',{}).get('_deleted'):
                    names=[other['text'],*other.get('metadata',{}).get('aliases',[])]
                    if alias.casefold() in [x.casefold() for x in names if isinstance(x,str)]:
                        raise ValueError('该别名已属于另一个实体；请显式解析或合并')
            updated=writable(row)
            updated['metadata']={**row.get('metadata',{}),'aliases':list(dict.fromkeys([*row.get('metadata',{}).get('aliases',[]),alias]))}
            return public(self.service.write(p,[updated],expected_versions={entity_id:expected_version})[0])

    def resolve(self,p,text,scope=None,threshold=0.7):
        if not isinstance(text,str) or not text.strip() or not 0<=threshold<=1:
            raise ValueError('需要文本且阈值在 0..1 之间')
        from semantica.deduplication import DuplicateDetector
        # 解析按已存向量排序，因此本次读取必须携带向量；
        # KnowledgeService.scoped 默认已不再包含向量。
        rows=[r for r in self.service.scoped(p,{**(scope or {}),'_include_embeddings':True}) if r['kind']=='entity']
        exact=[]
        for row in rows:
            names=[row['text'],*row.get('metadata',{}).get('aliases',[])]
            if text.casefold() in [x.casefold() for x in names if isinstance(x,str)]:
                exact.append(row)
        if len(exact)==1:
            row=exact[0]
            return {'status':'exists' if text==row['text'] else 'alias','canonical':public(row),'candidates':[],'backend':'semantica+aliases'}
        if exact:
            return {'status':'exact_duplicates','canonical':None,
                'candidates':[{**public(row),'score':1.0,'reasons':['exact_name_or_alias']} for row in exact[:12]],
                'backend':'semantica+aliases'}
        # 检测器仅对词法候选运行，避免对每个已有配对与外部向量做平方级比较。
        probe={'id':'__probe__','name':text,'text':text}
        detector=DuplicateDetector(similarity_threshold=threshold,confidence_threshold=0,use_clustering=False)
        candidates=[]
        for row in rows:
            existing={'id':row['id'],'name':row['text'],'text':row['text'],'type':row['type']}
            matches=detector.detect_duplicates([probe,existing])
            if matches:
                candidates.append({**public(row),'score':matches[0].similarity_score,'reasons':matches[0].reasons})
        # 用项目实际向量补充语义候选。
        from .retrieval import rank_candidates
        hits=rank_candidates(rows,text,min(12,len(rows)),self.service.encoder) if rows else []
        seen={r['id'] for r in candidates}
        candidates += [{**r,'reasons':['local_embedding']} for r in hits if r['score']>=threshold and r['id'] not in seen]
        return {'status':'candidates','canonical':None,'candidates':sorted(candidates,key=lambda r:r['score'],reverse=True)[:12],'backend':'semantica+project_embedding'}

    def _commit(self,p,before,updates,operation,backend='service',redirects=None,reversal_of=None,
                resolution_decisions=None,assertion_decisions=None):
        op_id=str(uuid4())
        expected={r['id']:r['version'] for r in before}
        for row in updates:
            row['metadata']={**row.get('metadata',{}),'_operation_id':op_id}
        audit=dict(id='audit:'+op_id,kind='document',text=f'{operation}: '+', '.join(r['id'] for r in before),
                   metadata={'_audit':True,'operation':operation,'operation_id':op_id,'backend':backend,'created_at':utc_now(),
                             'before':[public(r) for r in before]})
        expected[audit['id']]=0
        formal_operation={'merge':'merge_rewrite','undo':'merge_reversal',
                          'delete':'retract_source'}.get(operation,'manual_write')
        ledger={'id':op_id,'operation':formal_operation,'redirects':redirects or {},
                'before_state':[public(r) for r in before],
                'expected_versions':expected,'reversal_of':reversal_of}
        saved=self.service.write(p,[*updates,audit],expected_versions=expected,
            operation=formal_operation,ledger=ledger,resolution_decisions=resolution_decisions,
            assertion_decisions=assertion_decisions)
        return {'operation_id':op_id,'backend':backend,'records':[public(r) for r in saved if r['id']!=audit['id']]}

    @staticmethod
    def _attribute_identity(row):
        return json.dumps([row.get('datatype'),row.get('value')],ensure_ascii=False,
                          allow_nan=False,sort_keys=True,separators=(',',':'))

    def _attribute_conflicts(self,p,keep,attributes):
        from .ontology import Ontology
        ontologies={}
        by_predicate={}
        for row in attributes:
            by_predicate.setdefault(row['type'],[]).append(row)
        conflicts=[]
        for predicate,candidates in by_predicate.items():
            constrained=False
            for row in candidates:
                ontology_id=row.get('ontology_id')
                if ontology_id not in ontologies:
                    ontologies[ontology_id]=Ontology(
                        self.repo.get_ontology(p,ontology_id)['turtle'])
                if ontologies[ontology_id].attribute_max_count_one(predicate,keep['type']):
                    constrained=True
                    break
            if not constrained:
                continue
            by_id={row['id']:row for row in candidates}
            instants=sorted({row.get('valid_from') for row in candidates
                             if row.get('valid_from')})
            if any(not row.get('valid_from') for row in candidates):
                instants.insert(0,None)
            active_sets=set()
            for instant in instants:
                active=frozenset(row['id'] for row in candidates if
                    (instant is None and not row.get('valid_from')) or
                    (instant is not None and
                     (not row.get('valid_from') or row['valid_from']<=instant) and
                     (not row.get('valid_until') or instant<row['valid_until'])))
                if len(active)>1 and len({self._attribute_identity(by_id[record_id])
                                          for record_id in active})>1:
                    active_sets.add(active)
            maximal=[group for group in active_sets
                     if not any(group<other for other in active_sets)]
            for group in sorted(maximal,key=lambda item:sorted(item)):
                rows=[by_id[record_id] for record_id in sorted(group)]
                conflicts.append({'predicate':predicate,'record_ids':[r['id'] for r in rows],
                    'values':[{key:r.get(key) for key in
                               ('id','value','datatype','valid_from','valid_until')} for r in rows]})
        return conflicts

    def merge(self,p,keep_id,drop_id,expected_versions,resolution_decision=None,
              attribute_winners=None):
        if keep_id==drop_id:
            raise ValueError('请选择两个不同的实体')
        with self.service.lock:
            rows=self._rows(p)
            keep,drop=rows.get(keep_id),rows.get(drop_id)
            if not keep or not drop:
                raise KeyError(keep_id if not keep else drop_id)
            if any(r['kind']!='entity' or r.get('metadata',{}).get('_deleted') for r in (keep,drop)):
                raise ValueError('合并需要两个有效实体')
            if expected_versions!={keep_id:keep['version'],drop_id:drop['version']}:
                raise ValueError('版本冲突：请刷新实体版本')
            if keep['type']!=drop['type']:
                raise ValueError('合并要求相同的稳定类型 IRI')
            if any(keep.get(k)!=drop.get(k) for k in ('valid_from','valid_until')):
                raise ValueError('合并要求相同的业务时间区间；请显式修正区间')
            attributes=[r for r in rows.values() if r['kind']=='attribute' and
                        not r.get('metadata',{}).get('_deleted') and
                        r.get('subject_id') in {keep_id,drop_id}]
            conflicts=self._attribute_conflicts(p,keep,attributes)
            winners=list(attribute_winners or [])
            conflict_ids={record_id for conflict in conflicts
                          for record_id in conflict['record_ids']}
            unrelated=sorted(set(winners)-conflict_ids)
            details=json.dumps(conflicts,ensure_ascii=False,sort_keys=True)
            if len(winners)!=len(set(winners)):
                raise ValueError(f'属性胜出记录不能重复；冲突详情：{details}')
            if unrelated:
                raise ValueError(f'属性胜出记录与冲突无关：{", ".join(unrelated)}；冲突详情：{details}')
            selected=[]
            for conflict in conflicts:
                choices=[record_id for record_id in winners
                         if record_id in conflict['record_ids']]
                if len(choices)!=1:
                    raise ValueError('每个 maxCount 1 属性冲突组必须选择恰好一个现有记录 ID；'
                                     f'候选 {", ".join(conflict["record_ids"])}；冲突详情：{details}')
                selected.append((choices[0],conflict))
            if winners and not conflicts:
                raise ValueError(f'属性胜出记录与任何 maxCount 1 冲突无关：{", ".join(winners)}')
            from semantica.deduplication import EntityMerger
            operation=EntityMerger(preserve_provenance=True).merge_entity_group([
                {'id':r['id'],'name':r['text'],'type':r['type'],'properties':r.get('properties',{})} for r in (keep,drop)],strategy='keep_first')
            updated=writable(keep)
            # 将冲突的元数据/属性保留在原始不可变版本和 merged_sources 中；
            # 选定的规范值优先。
            updated['properties']={**drop.get('properties',{}),**keep.get('properties',{})}
            aliases=[*keep.get('metadata',{}).get('aliases',[]),drop['text'],*drop.get('metadata',{}).get('aliases',[])]
            ontology_versions=list(dict.fromkeys(value for value in [
                *keep.get('metadata',{}).get('ontology_versions',[]),
                *drop.get('metadata',{}).get('ontology_versions',[]),
                keep.get('ontology_id'),drop.get('ontology_id')] if value))
            updated['metadata']={**keep.get('metadata',{}),'aliases':list(dict.fromkeys(aliases)),
                'merged_sources':[*keep.get('metadata',{}).get('merged_sources',[]),public(drop)],
                'ontology_versions':ontology_versions,
                'merge_backend':'semantica.EntityMerger','merge_advice':operation.metadata}
            removed=writable(drop)
            removed['metadata']={**drop.get('metadata',{}),'_deleted':True,'merged_into':keep_id}
            before=[keep,drop]; updates=[updated,removed]
            for row in rows.values():
                if row['kind']=='relation' and not row.get('metadata',{}).get('_deleted') and drop_id in (row.get('subject_id'),row.get('object_id')):
                    before.append(row); edge=writable(row)
                    for key in ('subject_id','object_id'):
                        if edge[key]==drop_id: edge[key]=keep_id
                    updates.append(edge)
            attribute_by_id={row['id']:row for row in attributes}
            losing={}
            for winner_id,conflict in selected:
                winner=attribute_by_id[winner_id]
                winner_value=self._attribute_identity(winner)
                for record_id in conflict['record_ids']:
                    if self._attribute_identity(attribute_by_id[record_id])!=winner_value:
                        losing[record_id]=winner_id
            assertion_decisions=[]
            for row in attributes:
                if row['id'] in losing:
                    before.append(row)
                    loser=writable(row)
                    loser['metadata']={**row.get('metadata',{}),'_deleted':True,
                        'superseded_by_attribute':losing[row['id']],
                        'merge_conflict':'shacl_max_count_1'}
                    updates.append(loser)
                    for assertion in self.repo.list_assertions(
                            p,status='accepted',canonical_record_id=row['id']):
                        reason=(f'实体合并后的 {row["type"]} 违反 maxCount 1；'
                                f'已选择属性记录 {losing[row["id"]]}')
                        assertion_decisions.extend([
                            {'id':assertion['id'],'expected_version':assertion['decision_version'],
                             'status':'contradicting','reason':reason,'actor':'merge-governance',
                             'canonical_record_id':row['id']},
                            {'id':assertion['id'],'expected_version':assertion['decision_version']+1,
                             'status':'superseded','reason':reason,'actor':'merge-governance',
                             'canonical_record_id':row['id']},
                        ])
                elif row.get('subject_id')==drop_id:
                    before.append(row)
                    attribute=writable(row)
                    attribute['subject_id']=keep_id
                    updates.append(attribute)
            decisions=[resolution_decision] if resolution_decision else None
            return self._commit(p,before,updates,'merge','semantica',redirects={drop_id:keep_id},
                resolution_decisions=decisions,assertion_decisions=assertion_decisions)

    def delete(self,p,record_id,expected_version):
        with self.service.lock:
            rows=self._rows(p)
            row=rows.get(record_id)
            if not row: raise KeyError(record_id)
            if row['version']!=expected_version: raise ValueError('版本冲突：请刷新记录')
            affected=[row]+[r for r in rows.values() if r['id']!=record_id and
                (r.get('source_id')==record_id or r['kind']=='relation' and record_id in (r.get('subject_id'),r.get('object_id')))]
            # 若删除文档同时删除其来源实体，则一并移除相关关系边。
            deleted_ids={r['id'] for r in affected}
            affected += [r for r in rows.values() if r['id'] not in deleted_ids and r['kind']=='relation' and
                         deleted_ids.intersection((r.get('subject_id'),r.get('object_id')))]
            updates=[{**writable(r),'metadata':{**r.get('metadata',{}),'_deleted':True}} for r in affected]
            result=self._commit(p,affected,updates,'delete')
            return {**result,'deleted':len(affected)}

    def operations(self,p):
        return [public(r) for r in self.repo.current_records(p) if r.get('metadata',{}).get('_audit')]

    def undo_merge(self,p,operation_id):
        with self.service.lock:
            rows=self._rows(p)
            audit=rows.get('audit:'+operation_id)
            if not audit or not audit.get('metadata',{}).get('_audit'): raise KeyError(operation_id)
            before=audit['metadata']['before']
            current=[rows[r['id']] for r in before]
            if any(r.get('metadata',{}).get('_operation_id')!=operation_id for r in current):
                raise ValueError('版本冲突：受影响记录已变更；撤销会覆盖更新的编辑')
            result=self._commit(p,current,[writable(r) for r in before],'undo',reversal_of=operation_id)
            return {**result,'restored':len(before)}

    def restore(self,p,record_id,version,expected_version):
        with self.service.lock:
            versions=self.repo.history(p,record_id)
            chosen=next((r for r in versions if r['version']==version),None)
            if not chosen: raise KeyError(record_id)
            return public(self.service.write(p,[writable(chosen)],expected_versions={record_id:expected_version})[0])
