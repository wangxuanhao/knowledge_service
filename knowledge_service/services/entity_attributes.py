"""Scoped, bounded property values and frozen support for an entity sidebar."""
from __future__ import annotations

import base64
import hashlib
import json

from ..repository.entity_attributes import EntityAttributeReads
from ..repository.scoped_reads import ScopedReads


class EntityAttributes:
    PREVIEW=400

    def __init__(self,service):
        self.service=service
        self.repo=service.repository
        self.reads=EntityAttributeReads(self.repo)

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

    def page(self,project,entity,scope,*,limit=20,cursor=None,predicate=None,focus_record_id=None):
        self._bound(limit)
        with self.service.independent_read_view() as view:
            return EntityAttributes(view)._page(project,entity,dict(scope or {}),limit,cursor,predicate,focus_record_id)

    def _page(self,project,entity,scope,limit,cursor,predicate,focus_record_id):
        owner=self._entity(project,entity,scope)
        watermark=self.repo.projections.project_watermark(project)
        identity=self._identity(project,entity,scope,watermark,['values',predicate,focus_record_id])
        after=self._position(cursor,identity)
        counts,rows=self.reads.value_page(project,entity,scope,limit=limit,preview_length=self.PREVIEW,
            predicate=predicate,focus_record_id=focus_record_id,after=after)
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
            record=reader.reads.record_for_sources(project,entity,record_id,scope)
            if record is None:raise KeyError(record_id)
            if expected_version_id is not None and record['version_id']!=expected_version_id:raise ValueError('属性已更新，请重新加载')
            identity=self._identity(project,entity,scope,view.repository.projections.project_watermark(project),['sources',record_id])
            after=self._position(cursor,identity)
            if after is not None and not isinstance(after,str):raise ValueError('无效的属性来源游标')
            found=reader.reads.source_page(project,record_id,record['version_id'],scope,limit=limit,after=after)
            more=len(found)>limit;found=found[:limit]
            return {'record_id':record_id,'version':record['version'],'version_id':record['version_id'],'sources':[dict(row) for row in found],
                'next_cursor':self._cursor(identity,found[-1]['id']) if more else None}
