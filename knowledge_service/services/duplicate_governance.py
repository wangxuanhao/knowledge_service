"""Bounded duplicate groups share merge compatibility and one formal commit."""
import hashlib
import json
import base64
from contextlib import contextmanager,nullcontext
from uuid import uuid4

from ..domain.entity_identity import compatible_identity,source_boundary,require_projection_ready
from ..repository.attribute_conflicts import ConflictReads
from ..repository.duplicate_entities import DuplicateReads


class DuplicateGovernance:
    def __init__(self,governance):
        self.governance=governance;self.service=governance.service;self.repo=governance.repo
        self.reads=DuplicateReads(self.repo)

    def inputs(self,project,key):
        from .ontology import Ontology
        from .formal_writes import _published_identity_policy
        members=self.reads.group(project,key);keep=members[0]
        ontology=Ontology(self.repo.get_ontology(project)['turtle']);policy=_published_identity_policy(ontology,keep['type'])
        from ..domain.entity_identity import merge_identity_keys
        accumulated=keep
        for row in members[1:]:
            if not compatible_identity(accumulated,row,policy):raise ValueError('存在身份范围、唯一键或业务区间冲突，请先复核身份规则')
            accumulated={**accumulated,'metadata':{**accumulated.get('metadata',{}),
                'identity_keys':merge_identity_keys(accumulated,row,policy)}}
            if source_boundary(keep)!=source_boundary(row):require_projection_ready(self.repo,project)
        rows=self.reads.merge_rows(project,[r['id'] for r in members]);ids={r['id'] for r in members}
        if any(r.get('metadata',{}).get('_quarantined') and r.get('subject_id') in ids for r in rows.values()):
            raise ValueError('组内已有待审核属性冲突，请先处理现有冲突集合')
        attributes=[r for r in rows.values() if r['kind']=='attribute' and r.get('subject_id') in ids and not r.get('metadata',{}).get('_deleted')]
        conflicts=self.governance._attribute_conflicts(project,keep,attributes)
        supports=ConflictReads(self.repo).support_versions(project,list(rows))
        fingerprint=hashlib.sha256(json.dumps(dict(project=project,key=key,
            members=members,records=sorted(rows.values(),key=lambda r:r['id']),supports=supports,
            policy=policy.summary(),conflicts=conflicts),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return members,rows,attributes,conflicts,fingerprint,policy

    def preview(self,project,key):
        from .service import public
        members,rows,attributes,conflicts,fingerprint,policy=self.inputs(project,key)
        drops={r['id'] for r in members[1:]}
        relations=[r for r in rows.values() if r['kind']=='relation' and drops.intersection((r.get('subject_id'),r.get('object_id')))]
        source_pages=self.reads.source_pages(project,[member['id'] for member in members])
        public_members=[]
        for member in members:
            page=source_pages[member['id']];after=page.pop('next_after')
            public_members.append({**public(member),**page,'source_contexts_truncated':bool(after),
                'source_next_cursor':self._source_cursor(project,key,fingerprint,member,after) if after else None})
        from ..domain.formal_facts import canonical_attribute_key,canonical_relation_key
        fact_groups={}
        for row in rows.values():
            if row['kind'] not in {'attribute','relation'}:continue
            changed={**row,'subject_id':members[0]['id'] if row.get('subject_id') in drops else row.get('subject_id')}
            if row['kind']=='relation':changed['object_id']=members[0]['id'] if row.get('object_id') in drops else row.get('object_id')
            fact_key=(canonical_attribute_key(changed) if row['kind']=='attribute' else canonical_relation_key(changed))
            fact_groups.setdefault(fact_key,[]).append(row)
        colliding={key:sorted(group,key=lambda row:row['id']) for key,group in fact_groups.items() if len(group)>1}
        supports=self.reads.fact_support_counts(project,{key:[r['id'] for r in group] for key,group in list(sorted(colliding.items()))[:20]})
        dedup=[dict(kind=group[0]['kind'],predicate=group[0]['type'],survivor_id=group[0]['id'],
            record_ids=[r['id'] for r in group[:50]],record_count=len(group),record_ids_truncated=len(group)>50,
            **supports[key]) for key,group in list(sorted(colliding.items()))[:20]]
        collision_ids={row['id'] for group in colliding.values() for row in group}
        conflict_ids={record_id for conflict in conflicts for record_id in conflict['record_ids']}
        affected_attributes=[row for row in attributes if row.get('subject_id') in drops or row['id'] in collision_ids|conflict_ids]
        affected_relations=[row for row in rows.values() if row['kind']=='relation' and
            (drops.intersection((row.get('subject_id'),row.get('object_id'))) or row['id'] in collision_ids)]
        display_conflicts=[{**conflict,'values':[{**value,
            **({'value':value['value'][:240],'value_truncated':True,'value_length':len(value['value'])}
               if isinstance(value.get('value'),str) and len(value['value'])>240 else {})}
            for value in conflict['values']]} for conflict in conflicts]
        return dict(key=key,name=members[0]['text'],type=members[0]['type'],keep_id=members[0]['id'],
            members=public_members,expected_versions={r['id']:r['version'] for r in members},
            preview_fingerprint=fingerprint,attribute_conflicts=display_conflicts,
            affected_records=dict(relations=len(affected_relations),attributes=len(affected_attributes)),checked_attributes=len(attributes),
            affected_record_ids=dict(relations=[r['id'] for r in affected_relations[:100]],attributes=[r['id'] for r in affected_attributes[:100]]),
            affected_records_truncated=dict(relations=len(affected_relations)>100,attributes=len(affected_attributes)>100),
            fact_deduplication=dedup,fact_deduplication_total=len(colliding),fact_deduplication_truncated=len(colliding)>20,
            reasons=['已发布跨来源身份规则' if policy.declared else '保守身份规则：业务区间和适用范围一致'],
            requires_review=bool(conflicts) or not policy.declared,blocked=False)

    @staticmethod
    def _source_identity(project,key,fingerprint,member):
        return hashlib.sha256(json.dumps([project,key,fingerprint,member['id'],member['version_id']],ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def _source_cursor(cls,project,key,fingerprint,member,after):
        return base64.urlsafe_b64encode(json.dumps([cls._source_identity(project,key,fingerprint,member),after]).encode()).decode()

    def sources(self,project,key,fingerprint,entity_id,expected_version_id,*,limit=10,cursor=None):
        members,_,_,_,actual,_=self.inputs(project,key)
        if actual!=fingerprint:raise ValueError('版本冲突：重复组来源已变化，请重新预览')
        member=next((m for m in members if m['id']==entity_id),None)
        if member is None:raise KeyError(entity_id)
        if member['version_id']!=expected_version_id:raise ValueError('版本冲突：实体修订已变化')
        after=''
        if cursor:
            try:
                identity,after=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if identity!=self._source_identity(project,key,fingerprint,member):raise ValueError()
            except Exception as exc:raise ValueError('来源游标不属于当前预览') from exc
        page=self.reads.source_pages(project,[entity_id],limit=limit,after=after)[entity_id];after=page.pop('next_after')
        return dict(entity_id=entity_id,version_id=member['version_id'],**page,
            next_cursor=self._source_cursor(project,key,fingerprint,member,after) if after else None)

    @contextmanager
    def guard(self,project,key,fingerprint):
        from ..core.ingest_context import ingest_commit_guard
        previous=ingest_commit_guard.get()
        @contextmanager
        def verify():
            with previous() if previous else nullcontext():
                if self.inputs(project,key)[4]!=fingerprint:raise ValueError('版本冲突：重复组预览已变化，请重新预览')
                yield
        token=ingest_commit_guard.set(verify)
        try:yield
        finally:ingest_commit_guard.reset(token)

    def confirm(self,project,key,fingerprint,*,actor):
        from .governance import writable
        from ..domain.entity_identity import merge_identity_keys
        with self.service.lock:
            members,rows,attributes,conflicts,actual,policy=self.inputs(project,key)
            if fingerprint!=actual:raise ValueError('版本冲突：重复组预览已变化，请重新预览')
            keep=members[0];drops={r['id'] for r in members[1:]};before=list(members)
            survivor=writable(keep);metadata=dict(survivor.get('metadata') or {})
            aliases=list(metadata.get('aliases',[]));keys=dict(metadata.get('identity_keys') or {})
            for member in members[1:]:
                aliases.extend([member['text'],*(member.get('metadata') or {}).get('aliases',[])])
                probe={**survivor,'metadata':{**metadata,'identity_keys':keys}}
                keys=merge_identity_keys(probe,member,policy)
            survivor['metadata']={**metadata,'aliases':list(dict.fromkeys(aliases)),'identity_keys':keys,
                'merge_backend':'confirmed_identity_group'}
            updates=[survivor,*[{**writable(row),'metadata':{**row.get('metadata',{}),'_deleted':True,'merged_into':keep['id']}} for row in members[1:]]]
            for row in rows.values():
                if row['kind']=='relation' and drops.intersection((row.get('subject_id'),row.get('object_id'))):
                    changed=writable(row)
                    for field in ('subject_id','object_id'):
                        if changed[field] in drops:changed[field]=keep['id']
                    before.append(row);updates.append(changed)
            by_predicate={}
            for conflict in conflicts:by_predicate.setdefault(conflict['predicate'],set()).update(conflict['record_ids'])
            actions=[dict(kind='conflict_set_create',id='conflict:'+str(uuid4()),entity_id=keep['id'],predicate=predicate,member_ids=sorted(ids)) for predicate,ids in sorted(by_predicate.items())]
            conflict_ids=set().union(*by_predicate.values()) if by_predicate else set()
            assertions=[]
            for row in attributes:
                if row['subject_id'] not in drops and row['id'] not in conflict_ids:continue
                changed=writable(row);changed['subject_id']=keep['id']
                if row['id'] in conflict_ids:
                    changed['metadata']={**row.get('metadata',{}),'_quarantined':True,
                        'conflict_set_ids':[a['id'] for a in actions if row['id'] in a['member_ids']]}
                    for assertion in self.repo.list_assertions(project,status='accepted',canonical_record_id=row['id']):
                        assertions.append(dict(id=assertion['id'],expected_version=assertion['decision_version'],status='contradicting',
                            canonical_record_id=row['id'],reason='确认身份融合；单值属性隔离，等待集合审核',actor=actor))
                before.append(row);updates.append(changed)
            with self.guard(project,key,fingerprint):
                result=self.governance._commit(project,before,updates,'merge','confirmed_identity_group',
                    redirects={drop:keep['id'] for drop in drops},assertion_decisions=assertions,
                    expected_dependencies={r['id']:r['version'] for r in attributes},
                    audit_extra=dict(actor=actor,preview_fingerprint=fingerprint,duplicate_group=key),conflict_actions=actions,
                    quarantine_conflicts=dict(entity_id=keep['id'],predicates=list(by_predicate),member_ids=sorted(conflict_ids),preview_fingerprint=fingerprint) if actions else None)
            return {**result,'key':key,'name':keep['text'],'status':'merged',
                'conflict_sets':[ConflictReads(self.repo).read(project,a['id'],{}) for a in actions]}

    def batch(self,project,groups,*,actor):
        if not 1<=len(groups)<=20:raise ValueError('每批确认1..20个重复组')
        results=[]
        for group in groups:
            try:results.append(self.confirm(project,group['key'],group['preview_fingerprint'],actor=actor))
            except (ValueError,KeyError) as exc:
                results.append(dict(key=group['key'],status='failed',error=str(exc),retryable=True))
            except Exception as exc:
                # A lost response can follow a successful commit. Never claim
                # rollback or retry the old fingerprint as if nothing happened.
                results.append(dict(key=group['key'],status='failed',
                    error='本组提交状态未确认，请刷新本组状态后重新预览：'+type(exc).__name__,
                    retryable=True,refresh_required=True))
        return dict(results=results)
