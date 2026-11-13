"""Scoped, bounded property values and frozen support for an entity sidebar."""
from __future__ import annotations

import base64
import hashlib
import json

from ..core.time import normalize_time
from ..repository.scoped_reads import ScopedReads,_support_matches_record


class EntityAttributes:
    PREVIEW=400

    def __init__(self,service):
        self.service=service
        self.repo=service.repository

    @staticmethod
    def _bound(limit):
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('属性分页上限必须为 1 到 50')

    @staticmethod
    def _identity(project,entity,scope,watermark,channel):
        value=json.dumps([project,entity,scope,watermark,channel],ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _cursor(identity,position):
        return base64.urlsafe_b64encode(json.dumps([identity,position],ensure_ascii=False,separators=(',',':')).encode()).decode()

    @staticmethod
    def _position(cursor,identity):
        if cursor is None:return None
        try:
            if not isinstance(cursor,str) or len(cursor)>5000:raise ValueError()
            body=json.loads(base64.urlsafe_b64decode(cursor))
            if not isinstance(body,list) or len(body)!=2 or body[0]!=identity:raise ValueError()
            return body[1]
        except (ValueError,TypeError,UnicodeError):raise ValueError('属性游标已失效或不属于当前范围，请重新加载') from None

    def _entity(self,project,entity,scope):
        rows=ScopedReads(self.repo).by_ids(project,scope,[entity],kinds=['entity'])
        if not rows:raise KeyError(entity)
        return rows[0]

    @staticmethod
    def _known(scope,alias='s'):
        known=normalize_time(scope.get('known_at'))
        return ((f'{alias}.known_until IS NULL',[]) if known is None else
                (f'{alias}.known_from<=? AND ({alias}.known_until IS NULL OR {alias}.known_until>?)',[known,known]))

    def page(self,project,entity,scope,*,limit=20,cursor=None,predicate=None,focus_record_id=None):
        self._bound(limit)
        with self.service.independent_read_view() as view:
            return EntityAttributes(view)._page(project,entity,dict(scope or {}),limit,cursor,predicate,focus_record_id)

    def _page(self,project,entity,scope,limit,cursor,predicate,focus_record_id):
        owner=self._entity(project,entity,scope)
        watermark=self.repo.projections.project_watermark(project)
        identity=self._identity(project,entity,scope,watermark,['values',predicate,focus_record_id])
        after=self._position(cursor,identity)
        sql,params=ScopedReads(self.repo)._qualified(project,scope,kinds=['attribute'],keyset=True)
        sql+=' AND r.read_subject_id=?';params.append(entity)
        if predicate:
            if not isinstance(predicate,str) or len(predicate)>2000:raise ValueError('无效的属性类型')
            sql+=' AND r.read_type=?';params.append(predicate)
        counts=self.repo._db.execute('SELECT r.read_type,count(*) count FROM record_versions r WHERE '+sql+' GROUP BY r.read_type',params).fetchall()
        if focus_record_id is not None:
            if not isinstance(focus_record_id,str) or not focus_record_id or len(focus_record_id)>200:raise ValueError('无效的属性事实标识')
            sql+=' AND r.id=?';params.append(focus_record_id)
        if after is not None:
            if not isinstance(after,list) or len(after)!=3 or not isinstance(after[0],str) or not isinstance(after[1],str) or type(after[2]) is not int:
                raise ValueError('无效的属性游标')
            sql+=' AND (r.read_type,r.id,r.version)>(?,?,?)';params.extend(after)
        known,known_params=self._known(scope)
        # The database extracts a bounded preview; the large literal is not
        # transferred to Python until its explicit full-value request.
        # Choose the ID page before decoding any paragraph or counting sources.
        # Otherwise a sort may evaluate these expressions for every owner fact.
        rows=self.repo._db.execute('''WITH page AS MATERIALIZED (
            SELECT r.project_id,r.version_id FROM record_versions r WHERE '''+sql+'''
            ORDER BY r.read_type,r.id,r.version LIMIT ?)
            SELECT r.id,r.version,r.version_id,r.read_type,
            r.payload::jsonb->>'datatype' datatype,r.payload::jsonb->>'valid_from' valid_from,
            r.payload::jsonb->>'valid_until' valid_until,
            r.payload::jsonb#>'{metadata,applicability}' applicability,
            jsonb_typeof(r.payload::jsonb->'value') value_kind,
            left(r.payload::jsonb->>'value',?) value_preview,
            length(r.payload::jsonb->>'value') value_length,
            CASE WHEN jsonb_typeof(r.payload::jsonb->'value')='string'
                AND length(r.payload::jsonb->>'value')>? THEN NULL ELSE r.payload::jsonb->'value' END AS value,
            support.documents,support.assertions FROM page
            JOIN record_versions r ON r.project_id=page.project_id AND r.version_id=page.version_id
            LEFT JOIN LATERAL (SELECT count(DISTINCT s.document_id) documents,count(*) assertions
                FROM source_context_support s WHERE s.project_id=r.project_id AND s.record_id=r.id
                AND s.invalid_reason IS NULL AND '''+known+' AND '+_support_matches_record('r','s')+''') support ON TRUE
            ORDER BY r.read_type,r.id,r.version''',
            [*params,limit+1,self.PREVIEW,self.PREVIEW,*known_params]).fetchall()
        more=len(rows)>limit;rows=rows[:limit]
        labels=self.service.ontology_labels(project)
        totals={row['read_type']:int(row['count']) for row in counts}
        groups={}
        for row in rows:
            key=row['read_type'];group=groups.setdefault(key,{'predicate':key,'label':labels.get(key,key),'total_values':totals[key],'values':[]})
            truncated=row['value_kind']=='string' and row['value_length']>self.PREVIEW
            item={'record_id':row['id'],'version':row['version'],'version_id':row['version_id'],
                'value_preview':row['value_preview'],'value_truncated':truncated,'value_length':row['value_length'],
                'datatype':row['datatype'],'valid_from':row['valid_from'],'valid_until':row['valid_until'],
                'applicability':row['applicability'],
                'document_count':int(row['documents']),'accepted_support_count':int(row['assertions']),
                'status':'accepted','conflict_state':'clear'}
            if not truncated:item['value']=row['value']
            group['values'].append(item)
        next_cursor=self._cursor(identity,[rows[-1]['read_type'],rows[-1]['id'],rows[-1]['version']]) if more else None
        from ..repository.attribute_conflicts import ConflictReads
        conflicts=ConflictReads(self.repo).list_for_entity(project,entity,scope,limit=50)
        return {'entity':{key:owner[key] for key in ('id','version','version_id','text','type')},
            'groups':list(groups.values()),'property_count':len(totals),'value_count':sum(totals.values()),
            'next_cursor':next_cursor,'watermark':watermark,
            'conflict_sets':conflicts['conflict_sets'],'conflict_next_cursor':conflicts.get('next_cursor')}

    def value(self,project,entity,record_id,scope,*,expected_version_id=None):
        with self.service.independent_read_view() as view:
            reader=EntityAttributes(view);reader._entity(project,entity,scope)
            rows=ScopedReads(view.repository).by_ids(project,scope,[record_id],kinds=['attribute'])
            if not rows or rows[0].get('subject_id')!=entity:raise KeyError(record_id)
            row=rows[0]
            if expected_version_id is not None and row['version_id']!=expected_version_id:raise ValueError('属性已更新，请重新加载')
            return {key:row.get(key) for key in ('id','version','version_id','type','subject_id','value','datatype','valid_from','valid_until')}

    def sources(self,project,entity,record_id,scope,*,limit=10,cursor=None,expected_version_id=None):
        self._bound(limit)
        with self.service.independent_read_view() as view:
            reader=EntityAttributes(view);reader._entity(project,entity,scope)
            qualified,params=ScopedReads(view.repository)._qualified(project,scope,kinds=['attribute'])
            rows=view.repository._db.execute('SELECT r.id,r.version,r.version_id,r.read_subject_id subject_id FROM record_versions r WHERE '+
                qualified+' AND r.id=? AND r.read_subject_id=? LIMIT 1',[*params,record_id,entity]).fetchall()
            if not rows:raise KeyError(record_id)
            if expected_version_id is not None and rows[0]['version_id']!=expected_version_id:raise ValueError('属性已更新，请重新加载')
            identity=self._identity(project,entity,scope,view.repository.projections.project_watermark(project),['sources',record_id])
            after=self._position(cursor,identity)
            if after is not None and not isinstance(after,str):raise ValueError('无效的属性来源游标')
            known,params=self._known(scope)
            found=view.repository._db.execute('''SELECT s.id,s.assertion_id,s.document_id,s.document_version_id,
                a.chunk_id,a.start_char,a.end_char,left(a.quote,2000) quote,length(a.quote)>2000 quote_truncated,
                COALESCE(d.payload::jsonb#>>'{metadata,title}',s.document_id,'手工维护') document_title,
                s.valid_from,s.valid_until FROM source_context_support s
                JOIN assertions a ON a.project_id=s.project_id AND a.id=s.assertion_id
                JOIN record_versions fact ON fact.project_id=s.project_id AND fact.version_id=?
                LEFT JOIN record_versions d ON d.project_id=s.project_id AND d.version_id=s.document_version_id
                WHERE s.project_id=? AND s.record_id=? AND s.invalid_reason IS NULL AND '''+known+
                ' AND '+_support_matches_record('fact','s')+' AND s.id>? ORDER BY s.id LIMIT ?', [rows[0]['version_id'],project,record_id,*params,after or '',limit+1]).fetchall()
            more=len(found)>limit;found=found[:limit]
            return {'record_id':record_id,'version':rows[0]['version'],'version_id':rows[0]['version_id'],'sources':[dict(row) for row in found],
                'next_cursor':self._cursor(identity,found[-1]['id']) if more else None}
