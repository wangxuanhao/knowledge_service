"""Bounded frozen conflict reads and mechanical formal-transaction actions."""
from __future__ import annotations

import base64
import json

from ..core.time import normalize_time
from .scoped_reads import ScopedReads, _snapshot_read, _filter_sql


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class ConflictReads:
    def __init__(self, repository):
        self.repo=repository

    @staticmethod
    def _state(row, known):
        return 'open' if known and (not row['resolved_at'] or known<row['resolved_at']) else row['state']

    def _sets(self,project,scope,*,entity_ids=None,set_id=None,limit=50,cursor=None,open_only=True):
        known=normalize_time((scope or {}).get('known_at'))
        clauses=['c.project_id=?'];params=[project]
        if known:
            clauses.append('c.created_at<=?');params.append(known)
            if open_only:
                clauses.append('(c.resolved_at IS NULL OR c.resolved_at>?)');params.append(known)
        elif open_only:clauses.append("c.state='open'")
        if entity_ids is not None:
            clauses.append('c.entity_id=ANY(?::text[])');params.append(list(entity_ids))
        if set_id:clauses.append('c.id=?');params.append(set_id)
        if cursor:clauses.append('c.id>?');params.append(cursor)
        member_scope,scope_values=self._member_scope(project,scope)
        clauses.append('''EXISTS(SELECT 1 FROM attribute_conflict_members m JOIN record_versions r
            ON r.project_id=m.project_id AND r.version_id=m.version_id
            WHERE m.project_id=c.project_id AND m.set_id=c.id AND '''+member_scope+')')
        params.extend(scope_values)
        params.append(limit)
        return [dict(row) for row in self.repo._db.execute(
            'SELECT c.* FROM attribute_conflict_sets c WHERE '+' AND '.join(clauses)+' ORDER BY c.id LIMIT ?',params).fetchall()]

    def _member_scope(self,project,scope):
        scope=scope or {}
        own,values=ScopedReads._scope_predicates(scope,'r')
        filtered,filter_values=_filter_sql(scope.get('filters'),'r')
        clauses=[own,filtered];values.extend(filter_values)
        ontology=scope.get('ontology_scope') or 'all'
        if ontology=='ids':
            clauses.append("r.payload::jsonb->>'ontology_id'=ANY(?::text[])");values.append(list(scope.get('ontology_ids') or []))
        elif ontology=='unknown':
            clauses.append("NOT EXISTS(SELECT 1 FROM ontologies o WHERE o.project_id=r.project_id AND o.id=r.payload::jsonb->>'ontology_id')")
        elif ontology!='all':raise ValueError('无效的本体范围')
        # This is frozen governance evidence, not an accepted-fact read. Verify
        # the owner that existed when the member was quarantined; withdrawal
        # must not erase the review queue or prevent explicit rejection.
        clauses.append('''EXISTS(SELECT 1 FROM record_versions owner
            WHERE owner.project_id=r.project_id AND owner.id=r.read_subject_id
             AND owner.read_kind='entity' AND owner.read_visible
             AND owner.recorded_at<=r.recorded_at
             AND (owner.superseded_at IS NULL OR owner.superseded_at>r.recorded_at))''')
        return ' AND '.join(clauses),values

    def _members(self,project,sets,scope,*,include_versions=True):
        if not sets:return {}
        known=normalize_time((scope or {}).get('known_at'))
        member_scope,values=self._member_scope(project,scope)
        decisions_sql='''coalesce((SELECT jsonb_agg(jsonb_build_object('id',a.id,'version',event.decision_version,
              'status',event.to_status,'source_active',a.document_id IS NULL OR EXISTS(
                SELECT 1 FROM record_versions source WHERE source.project_id=a.project_id AND source.id=a.document_id
                 AND source.read_visible AND (CASE WHEN ?::timestamptz IS NULL THEN source.superseded_at IS NULL
                  ELSE source.recorded_at<=?::timestamptz AND (source.superseded_at IS NULL OR source.superseded_at>?::timestamptz) END))) ORDER BY a.id)
             FROM assertions a JOIN LATERAL (SELECT e.decision_version,e.to_status FROM assertion_events e
               WHERE e.project_id=a.project_id AND e.assertion_id=a.id AND (?::timestamptz IS NULL OR e.created_at<=?::timestamptz)
               ORDER BY e.decision_version DESC LIMIT 1) event ON TRUE
             WHERE a.project_id=m.project_id AND a.id IN (SELECT value->>'id' FROM jsonb_array_elements(m.assertion_versions))), '[]'::jsonb)'''
        decision_params=[known]*5 if include_versions else []
        if not include_versions:decisions_sql="'[]'::jsonb"
        rows=self.repo._db.execute('''SELECT m.set_id,m.record_id,m.version,m.version_id,
            jsonb_array_length(m.assertion_versions) AS support_count,
            (r.payload::jsonb-'value'-'text')::text AS payload,r.recorded_at,r.superseded_at,
            current_record.version AS current_version,current_record.version_id AS current_version_id,'''+decisions_sql+''' AS current_assertions,
            (SELECT count(DISTINCT a.document_id) FROM assertions a
             WHERE a.project_id=m.project_id AND a.id IN
              (SELECT value->>'id' FROM jsonb_array_elements(m.assertion_versions))) AS document_count,
            jsonb_typeof(r.payload::jsonb->'value') AS value_kind,
            CASE WHEN jsonb_typeof(r.payload::jsonb->'value')='string'
             THEN to_jsonb(left(r.payload::jsonb->>'value',240)) ELSE r.payload::jsonb->'value' END AS value_preview,
            length(r.payload::jsonb->>'value')>240 AS value_truncated
            FROM attribute_conflict_members m JOIN record_versions r
             ON r.project_id=m.project_id AND r.version_id=m.version_id
            LEFT JOIN record_versions current_record ON current_record.project_id=m.project_id AND current_record.id=m.record_id
             AND (CASE WHEN ?::timestamptz IS NULL THEN current_record.superseded_at IS NULL
              ELSE current_record.recorded_at<=?::timestamptz AND (current_record.superseded_at IS NULL OR current_record.superseded_at>?::timestamptz) END)
            WHERE m.project_id=? AND m.set_id=ANY(?::text[]) AND '''+member_scope+
            ' ORDER BY m.set_id,m.record_id',[*decision_params,known,known,known,project,[s['id'] for s in sets],*values]).fetchall()
        result={}
        for row in rows:
            payload=json.loads(row['payload'])
            preview=json.loads(row['value_preview'])
            assertions=json.loads(row['current_assertions'])
            member=dict(record_id=row['record_id'],version=row['version'],version_id=row['version_id'],
                current_version=row['current_version'],current_version_id=row['current_version_id'],
                value_preview=preview,value_truncated=bool(row['value_truncated']),
                datatype=payload.get('datatype'),valid_from=payload.get('valid_from'),valid_until=payload.get('valid_until'),
                applicability=(payload.get('metadata') or {}).get('applicability'),
                document_count=row['document_count'],support_count=row['support_count'])
            if include_versions:member.update(assertion_versions=assertions,
                active_support_count=sum(a['status']=='contradicting' and a['source_active'] for a in assertions))
            result.setdefault(row['set_id'],[]).append(member)
        return result

    def _summaries(self,project,sets,scope,*,include_versions=True):
        members=self._members(project,sets,scope,include_versions=include_versions)
        return [dict(id=row['id'],entity_id=row['entity_id'],predicate=row['predicate'],
            version=row['version'] if self._state(row,normalize_time(scope.get('known_at')))!='open' or row['state']=='open' else 1,
            state=self._state(row,normalize_time(scope.get('known_at'))),members=members[row['id']],
            created_at=row['created_at'],resolved_at=row['resolved_at']) for row in sets if members.get(row['id'])]

    @_snapshot_read
    def page(self,project,scope=None,*,limit=20,cursor=None,entity_id=None):
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('冲突集合分页上限为50')
        self.repo.get_project(project);scope=scope or {}
        sets=self._sets(project,scope,limit=limit+1,cursor=cursor,entity_ids=[entity_id] if entity_id else None)
        return dict(conflict_sets=self._summaries(project,sets[:limit],scope,include_versions=False),
                    next_cursor=sets[limit-1]['id'] if len(sets)>limit else None,entity_id=entity_id)

    @_snapshot_read
    def list_for_entity(self,project,entity_id,scope=None,*,limit=50,cursor=None):
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('冲突集合分页上限为50')
        self.repo.get_project(project);scope=scope or {}
        sets=self._sets(project,scope,entity_ids=[entity_id],limit=limit+1,cursor=cursor)
        return dict(conflict_sets=self._summaries(project,sets[:limit],scope,include_versions=False),
                    next_cursor=sets[limit-1]['id'] if len(sets)>limit else None)

    @_snapshot_read
    def scoped_warnings(self,project,scope,entity_ids,*,limit=50):
        if entity_ids is not None and len(entity_ids)>1000:raise ValueError('冲突提示最多检查1000个实体')
        self.repo.get_project(project);scope=scope or {}
        sets=self._sets(project,scope,entity_ids=entity_ids,limit=min(50,limit)+1)
        return dict(conflict_sets=self._summaries(project,sets[:limit],scope,include_versions=False),truncated=len(sets)>limit)

    @_snapshot_read
    def read(self,project,set_id,scope=None):
        self.repo.get_project(project);scope=scope or {}
        sets=self._sets(project,scope,set_id=set_id,limit=1,open_only=False)
        items=self._summaries(project,sets,scope)
        if not items:raise KeyError(set_id)
        items[0]['owner_available']=bool(ScopedReads(self.repo).by_ids(project,
            {'known_at':scope.get('known_at'),'include_unknown':True},[items[0]['entity_id']],kinds=['entity']))
        return items[0]

    @_snapshot_read
    def member(self,project,set_id,record_id,scope=None,*,expected_version_id=None):
        detail=self.read(project,set_id,scope)
        member=next((m for m in detail['members'] if m['record_id']==record_id),None)
        if not member:raise KeyError(record_id)
        if expected_version_id and expected_version_id!=member['version_id']:raise ValueError('版本冲突：冲突成员修订已变化')
        record=self.repo.get_record_version(project,member['version_id'])
        return {**member,'id':record_id,'value':record['value']}

    @_snapshot_read
    def sources(self,project,set_id,record_id,scope=None,*,expected_version_id=None,limit=10,cursor=None):
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('来源分页上限为50')
        member=self.member(project,set_id,record_id,scope,expected_version_id=expected_version_id)
        after=''
        if cursor:
            try:
                decoded=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if decoded[:3]!=[project,set_id,member['version_id']]:raise ValueError()
                after=decoded[3]
            except Exception as exc:raise ValueError('来源游标不属于此冲突成员') from exc
        ids=[a['id'] for a in member['assertion_versions']]
        known=normalize_time((scope or {}).get('known_at'))
        rows=self.repo._db.execute('''SELECT a.id AS assertion_id,a.document_id,a.document_version_id,
            a.chunk_id,a.start_char,a.end_char,left(a.quote,2000) AS quote,length(a.quote)>2000 AS quote_truncated,
            a.payload::jsonb->>'valid_from' AS valid_from,a.payload::jsonb->>'valid_until' AS valid_until,
            a.payload::jsonb#>'{metadata,applicability}' AS applicability,
            a.document_id IS NULL OR EXISTS(SELECT 1 FROM record_versions source
             WHERE source.project_id=a.project_id AND source.id=a.document_id AND source.read_visible
              AND (CASE WHEN ?::timestamptz IS NULL THEN source.superseded_at IS NULL
               ELSE source.recorded_at<=?::timestamptz AND (source.superseded_at IS NULL OR source.superseded_at>?::timestamptz) END)) AS source_active,
            a.source_hash,event.decision_version,event.to_status AS status,
            d.payload::jsonb#>>'{metadata,title}' AS document_title
            FROM assertions a LEFT JOIN record_versions d
             ON d.project_id=a.project_id AND d.version_id=a.document_version_id
            LEFT JOIN LATERAL (SELECT e.decision_version,e.to_status FROM assertion_events e
             WHERE e.project_id=a.project_id AND e.assertion_id=a.id AND (?::timestamptz IS NULL OR e.created_at<=?::timestamptz)
             ORDER BY e.decision_version DESC LIMIT 1) event ON TRUE
            WHERE a.project_id=? AND a.id=ANY(?::text[]) AND a.id>? ORDER BY a.id LIMIT ?''',
            (known,known,known,known,known,project,ids,after,limit+1)).fetchall()
        page=[dict(r) for r in rows[:limit]]
        for item in page:
            if item['applicability'] is not None:item['applicability']=json.loads(item['applicability'])
        return dict(record_id=record_id,version=member['version'],version_id=member['version_id'],sources=page,
            next_cursor=base64.urlsafe_b64encode(_json([project,set_id,member['version_id'],page[-1]['assertion_id']]).encode()).decode() if len(rows)>limit else None)

    def support_versions(self,project,record_ids):
        if not record_ids:return []
        rows=self.repo._db.execute('''SELECT id,canonical_record_id,decision_version,status
            FROM assertions WHERE project_id=? AND canonical_record_id=ANY(?::text[])
            ORDER BY canonical_record_id,id LIMIT 100001''',(project,list(record_ids))).fetchall()
        if len(rows)>100000:raise ValueError('本组来源支持超过100000条，请缩小融合/审核范围')
        return [dict(r) for r in rows]


def execute_conflict_action(executor,project,action):
    """Called only inside the formal executor's locked transaction."""
    db=executor.repository._db;now=executor.operation_time;kind=action['kind']
    if kind=='conflict_set_create':
        db.execute('''INSERT INTO attribute_conflict_sets
            (project_id,id,entity_id,predicate,state,operation_id,created_at) VALUES(?,?,?,?,'open',?,?)''',
            (project,action['id'],action['entity_id'],action['predicate'],action['operation_id'],now))
        for member in action['members']:
            record=executor._record_for_ref(project,member['record_ref'])
            db.execute('''INSERT INTO attribute_conflict_members
                (project_id,set_id,record_id,version,version_id,assertion_versions) VALUES(?,?,?,?,?,?)''',
                (project,action['id'],record['id'],record['version'],record['version_id'],_json(member['assertion_versions'])))
    elif kind=='conflict_set_resolve':
        changed=db.execute('''UPDATE attribute_conflict_sets SET state='resolved',version=version+1,
            resolved_at=?,reason=?,actor=?,decisions=?::jsonb
            WHERE project_id=? AND id=? AND state='open' AND version=?''',
            (now,action['reason'],action['actor'],_json(action['decisions']),project,action['id'],action['expected_version']))
        if changed.rowcount!=1:raise ValueError('版本冲突：冲突集合已被处理，请重新读取')
    elif kind=='conflict_sets_reverse':
        rows=db.execute('SELECT state FROM attribute_conflict_sets WHERE project_id=? AND operation_id=?',
                        (project,action['operation_id'])).fetchall()
        if any(row['state']!='open' for row in rows):raise ValueError('版本冲突：冲突集合已经审核，无法撤销融合')
        db.execute("UPDATE attribute_conflict_sets SET state='reversed',version=version+1,resolved_at=? WHERE project_id=? AND operation_id=? AND state='open'",
                   (now,project,action['operation_id']))
    else:raise ValueError('未知冲突账本动作')
