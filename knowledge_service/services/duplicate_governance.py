"""Bounded duplicate groups share merge compatibility and one formal commit."""
import hashlib
import json
import base64
from contextlib import contextmanager,nullcontext

from .duplicate_partitions import commit_merges, survivor_identity
from ..repository import OntologyNotPublished
from ..repository.attribute_conflicts import ConflictReads
from ..repository.duplicate_entities import DuplicateReads, DuplicateReviewRequired


class DuplicateGovernance:
    def __init__(self,governance):
        self.governance=governance;self.service=governance.service;self.repo=governance.repo
        self.reads=DuplicateReads(self.repo)

    def inputs(self,project,key):
        from .ontology import Ontology
        from .formal_writes import _published_identity_policy
        members, document_scope = self.reads.group_context(project, key)
        if self.reads.current_review_receipt(project, key, members, document_scope):
            raise DuplicateReviewRequired('本组已确认保留多个独立实体。如需调整，请重新逐成员分配并预览。')
        keep = members[0]
        ontology=Ontology(self.repo.get_ontology(project)['turtle']);policy=_published_identity_policy(ontology,keep['type'])
        survivor_identity(self.repo, project, keep, members[1:], policy)
        rows=self.reads.merge_rows(project,[r['id'] for r in members]);ids={r['id'] for r in members}
        if any(r.get('metadata',{}).get('_quarantined') and r.get('subject_id') in ids for r in rows.values()):
            raise DuplicateReviewRequired('组内已有待审核属性冲突，可在「知识审核」处理现有冲突集合。')
        attributes=[r for r in rows.values() if r['kind']=='attribute' and r.get('subject_id') in ids and not r.get('metadata',{}).get('_deleted')]
        conflicts=self.governance._attribute_conflicts(project,keep,attributes)
        supports=ConflictReads(self.repo).support_versions(project,list(rows))
        fingerprint_inputs = dict(project=project,key=key,
            members=members,records=sorted(rows.values(),key=lambda r:r['id']),supports=supports,
            policy=policy.summary(),conflicts=conflicts)
        if document_scope:
            fingerprint_inputs['document_support'] = self.reads.document_support_fingerprint(project, list(ids), document_scope)
        fingerprint = hashlib.sha256(json.dumps(fingerprint_inputs, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()
        return members,rows,attributes,conflicts,fingerprint,policy,document_scope

    @staticmethod
    def _relation_context(rows, members):
        contexts = {member['id']: dict(total=0, examples=[]) for member in members}
        relations = sorted((row for row in rows.values() if row['kind'] == 'relation'), key=lambda row: row['id'])
        for row in relations:
            subject_id, object_id = row.get('subject_id'), row.get('object_id')
            for entity_id in {subject_id, object_id} & contexts.keys():
                context = contexts[entity_id]
                context['total'] += 1
                if len(context['examples']) < 5:
                    context['examples'].append(dict(id=row['id'], type=row['type'], subject_id=subject_id,
                        subject_label=rows.get(subject_id, {}).get('text') or subject_id, object_id=object_id,
                        object_label=rows.get(object_id, {}).get('text') or object_id,
                        valid_from=row.get('valid_from'), valid_until=row.get('valid_until')))
        return contexts

    def preview(self,project,key):
        from .service import public
        members,rows,attributes,conflicts,fingerprint,policy,document_scope=self.inputs(project,key)
        drops={r['id'] for r in members[1:]}
        relation_context = self._relation_context(rows, members)
        source_pages=self.reads.source_pages(project,[member['id'] for member in members],document_scope=document_scope)
        public_members=[]
        for member in members:
            page=source_pages[member['id']];after=page.pop('next_after')
            public_members.append({**public(member),**page,'source_contexts_truncated':bool(after),
                'source_next_cursor':self._source_cursor(project,key,fingerprint,member,after) if after else None,
                'relation_context': relation_context[member['id']]})
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

    def members(self,project,key,*,limit=10,after_id=None):
        from .service import public
        members,after,document_scope=self.reads.member_page(project,key,limit=limit,after_id=after_id)
        if not members:
            return dict(key=key,members=[],next_after_id=None)
        pages=self.reads.source_pages(project,[member['id'] for member in members],limit=3,document_scope=document_scope)
        relations=self.reads.relation_contexts(project,[member['id'] for member in members])
        result=[]
        for member in members:
            page=pages[member['id']]
            source_after=page.pop('next_after')
            result.append({**public(member),**page,'relation_context':relations[member['id']],'source_next_cursor':
                self._source_cursor(project,key,None,member,source_after) if source_after else None})
        return dict(key=key,members=result,next_after_id=after)

    def preview_batch(self, project, keys):
        if not 1 <= len(keys) <= 20:
            raise ValueError('每批预览1..20个重复组')
        results = []
        for key in keys:
            try:
                results.append({**self.preview(project, key), 'status': 'ready'})
            except DuplicateReviewRequired as exc:
                results.append(exc.result(key))
            except (ValueError, KeyError) as exc:
                results.append(dict(key=key, status='failed', error=str(exc)))
            except OntologyNotPublished:
                results.append(dict(key=key, status='failed', error='本项目尚未发布本体，无法预览重复组'))
        return dict(results=results)

    @staticmethod
    def _source_identity(project,key,fingerprint,member):
        return hashlib.sha256(json.dumps([project,key,fingerprint,member['id'],member['version_id']],ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def _source_cursor(cls,project,key,fingerprint,member,after):
        return base64.urlsafe_b64encode(json.dumps([cls._source_identity(project,key,fingerprint,member),after]).encode()).decode()

    def sources(self,project,key,fingerprint,entity_id,expected_version_id,*,limit=10,cursor=None):
        if fingerprint is None:
            member,document_scope=self.reads.review_member(project,key,entity_id)
        else:
            members,_,_,_,actual,_,document_scope=self.inputs(project,key)
            if actual!=fingerprint:raise ValueError('版本冲突：重复组来源已变化，请重新预览')
            member=next((m for m in members if m['id']==entity_id),None)
            if member is None:raise KeyError(entity_id)
        if member['version_id']!=expected_version_id:raise ValueError('版本冲突：实体修订已变化')
        after=''
        if cursor:
            try:
                identity,after=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if not isinstance(after, str) or identity!=self._source_identity(project,key,fingerprint,member):raise ValueError()
            except Exception as exc:raise ValueError('来源游标不属于当前预览') from exc
        page=self.reads.source_pages(project,[entity_id],limit=limit,after=after,document_scope=document_scope)[entity_id]
        after=page.pop('next_after')
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
        with self.service.lock:
            members,rows,_,conflicts,actual,policy,_=self.inputs(project,key)
            if fingerprint!=actual:raise ValueError('版本冲突：重复组预览已变化，请重新预览')
            keep, drops = members[0], members[1:]
            target = dict(keep_id=keep['id'], drop_ids=[r['id'] for r in drops], attribute_conflicts=conflicts)
            metadata = survivor_identity(self.repo, project, keep, drops, policy)
            with self.guard(project,key,fingerprint):
                result, actions = commit_merges(self.governance, project, key, fingerprint, [target], rows,
                    {r['id']: r for r in members}, {keep['id']: metadata}, actor)
            return {**result,'key':key,'name':keep['text'],'status':'merged',
                'conflict_sets':[ConflictReads(self.repo).read(project,a['id'],{}) for a in actions]}

    def _batch_group_errors(self, project, groups):
        errors = {}
        keys_by_entity = {}
        with self.repo.independent_read_view() as reader:
            reads = DuplicateReads(reader)
            for key in dict.fromkeys(group['key'] for group in groups):
                try:
                    members = reads.group(project, key)
                except (ValueError, KeyError) as exc:
                    errors[key] = str(exc)
                    continue
                for member in members:
                    keys_by_entity.setdefault(member['id'], set()).add(key)
        for keys in keys_by_entity.values():
            if len(keys) > 1:
                errors.update(dict.fromkeys(keys, '所选重复组的实体成员重叠，请分批复核后重新预览'))
        return errors

    def batch(self,project,groups,*,actor):
        if not 1<=len(groups)<=20:raise ValueError('每批确认1..20个重复组')
        errors = self._batch_group_errors(project, groups)
        results=[]
        for group in groups:
            if group['key'] in errors:
                results.append(dict(key=group['key'], status='failed', error=errors[group['key']], retryable=True))
                continue
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
