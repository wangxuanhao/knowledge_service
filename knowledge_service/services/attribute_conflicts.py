"""Exact-set conflict decisions use the existing formal fact transaction."""
from __future__ import annotations

from ..core.time import normalize_time
from ..repository.attribute_conflicts import ConflictReads


def allow_quarantine_deficits(report,before_report,context):
    """Only new minCount deficits on the quarantined owner/property may pass."""
    if not before_report.get('conforms') or not context.get('preview_fingerprint') or not context.get('member_ids'):
        return False
    violations=report.get('violations') or []
    if not violations or report.get('errors')!=violations:return False
    return all(v.get('constraint')=='MinCountConstraintComponent'
        and v.get('record_id')==context.get('entity_id')
        and v.get('path') in context.get('predicates',[]) for v in violations)


def member_versions(members):
    return {m['record_id']:(m['version'],m['version_id'],m.get('current_version',m['version']),
        m.get('current_version_id',m['version_id']),sorted(
        (a['id'],a['version']) for a in m.get('assertion_versions',[]))) for m in members}


class AttributeConflicts:
    def __init__(self,service):
        self.service=service;self.repo=service.repository;self.reads=ConflictReads(self.repo)

    def read(self,project,set_id,scope):return self.reads.read(project,set_id,scope)

    def resolve(self,project,set_id,request,*,actor):
        from .governance import writable
        reason=(request.get('reason') or '').strip()
        if not reason:raise ValueError('冲突审核理由不能为空')
        with self.service.lock:
            detail=self.read(project,set_id,{})
            expected=request['expected_members']
            if (detail['state']!='open' or request['expected_version']!=detail['version']
                or len(expected)!=len(detail['members']) or member_versions(expected)!=member_versions(detail['members'])):
                raise ValueError('版本冲突：冲突集合或成员已变化，请重新读取')
            decisions=request.get('decisions') or []
            by_id={d['attribute_id']:d for d in decisions}
            if len(by_id)!=len(decisions) or set(by_id)!={m['record_id'] for m in detail['members']}:
                raise ValueError('必须逐值明确接受或拒绝集合全部成员')
            if detail.get('owner_available') is False and any(d['decision']=='accept' for d in decisions):
                raise ValueError('实体来源已撤回，只能拒绝冲突值或保留待审核')
            updates=[];assertions=[];expected_versions={}
            support=self.reads.support_versions(project,list(by_id));support_by_id={}
            for item in support:support_by_id.setdefault(item['canonical_record_id'],[]).append(item)
            for member in detail['members']:
                frozen=self.repo.get_record_version(project,member['version_id'])
                record=self.repo.get_record_version(project,member.get('current_version_id') or member['version_id'])
                current=self.repo._db.execute('''SELECT version_id FROM record_versions
                    WHERE project_id=? AND id=? AND superseded_at IS NULL''',(project,record['id'])).fetchone()
                expected_support={a['id']:a for a in member['assertion_versions']}
                current_support=[a for a in support_by_id.get(record['id'],[]) if a['id'] in expected_support]
                unexpected=[a for a in support_by_id.get(record['id'],[]) if a['id'] not in expected_support and a['status'] in {'accepted','contradicting'}]
                if (current is None or current['version_id']!=member.get('current_version_id',member['version_id'])
                    or sorted((a['id'],a['decision_version']) for a in current_support)!=sorted((a['id'],a['version']) for a in member['assertion_versions'])
                    or unexpected or any(a['status']=='accepted' for a in current_support)):
                    raise ValueError('版本冲突：成员事实或来源支持已变化，请重新复核')
                decision=by_id[record['id']]
                if decision['decision'] not in {'accept','reject'}:raise ValueError('未知冲突审核决定')
                authorized=[a for a in current_support if a['status']=='contradicting' and expected_support[a['id']]['source_active']]
                if decision['decision']=='accept' and not authorized:
                    raise ValueError('该值的来源支持已撤回，只能拒绝该值或保留待审核')
                row=writable(frozen if decision['decision']=='accept' else record);metadata=dict(row.get('metadata') or {})
                metadata.pop('_quarantined',None);metadata.pop('conflict_set_ids',None)
                metadata.update(conflict_resolution=set_id,conflict_reason=reason,conflict_actor=actor)
                if decision['decision']=='reject':metadata['_deleted']=True
                for key in ('valid_from','valid_until'):
                    if key in decision:row[key]=normalize_time(decision[key])
                if row.get('valid_from') and row.get('valid_until') and row['valid_from']>=row['valid_until']:
                    raise ValueError('审核生效区间必须为正时长')
                row['metadata']=metadata;updates.append(row);expected_versions[row['id']]=record['version']
                for support in current_support:
                    if support['status']!='contradicting':continue
                    assertions.append(dict(id=support['id'],expected_version=support['decision_version'],
                        status='accepted' if decision['decision']=='accept' and support in authorized else 'rejected',reason=reason,actor=actor,
                        canonical_record_id=row['id']))
            saved=self.service.write(project,updates,expected_versions=expected_versions,operation='approve_review',
                suppress_auto_assertions=True,assertion_decisions=assertions,
                conflict_actions=[dict(kind='conflict_set_resolve',id=set_id,expected_version=detail['version'],
                    reason=reason,actor=actor,decisions=decisions)])
            from .service import public
            return dict(id=set_id,state='resolved',version=detail['version']+1,records=[public(r) for r in saved])
