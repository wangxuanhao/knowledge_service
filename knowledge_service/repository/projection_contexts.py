"""Bounded entity mention and incident-fact contexts for native eligibility."""
import json
import re

from .scoped_reads import ScopedReads, _support_matches_record
from ..domain.entity_identity import source_qualified


class ProjectionContexts:
    def __init__(self,repository):self.repo=repository

    def collect(self,project,records,*,known_at=None,direct=None):
        if len(records)>1000:raise ValueError('来源投影上下文批次最多 1000 条记录')
        ids=[row['id'] for row in records]
        result=(self.repo.current_source_contexts(project,ids,known_at=known_at) if direct is None else direct)
        entities=[row['id'] for row in records if row.get('kind')=='entity' and source_qualified(row) and result.get(row['id'])]
        if not entities:return result
        reads=ScopedReads(self.repo);scope={'known_at':known_at,'_qualifying_fact':True}
        sql,params=reads._qualified(project,scope,kinds=['relation','attribute'])
        sql=re.sub(r'\br\.', 'f.',sql)
        known,known_params=reads._support_known(scope,'s')
        rows=self.repo._db.execute('''SELECT e.id entity_id,s.*,f.version_id support_fact_version_id,f.payload fact_payload FROM
            record_versions e JOIN record_versions f ON f.project_id=e.project_id AND
             (f.read_subject_id=e.id OR (f.read_kind='relation' AND f.read_object_id=e.id))
            JOIN source_context_support s ON s.project_id=f.project_id AND s.record_id=f.id
            WHERE e.project_id=? AND e.id=ANY(?::text[]) AND e.superseded_at IS NULL
              AND '''+sql+' AND s.invalid_reason IS NULL AND '+_support_matches_record('f','s')+' AND '+known+
            ' ORDER BY e.id,s.id LIMIT 10001',[project,entities,*params,*known_params]).fetchall()
        if len(rows)>10000:raise ValueError('实体关联事实来源超过 10000 条预算，请缩小批次')
        for row in rows:
            fact=json.loads(row['fact_payload'])
            context={'id':row['id'],'version':row['decision_version'],'assertion_id':row['assertion_id'],
                'document_id':row['document_id'],'document_version_id':row['document_version_id'],
                'record_version_id':row['record_version_id'],'known_from':row['known_from'],'known_until':row['known_until'],
                'valid_from':fact.get('valid_from'),'valid_until':fact.get('valid_until'),
                'metadata':{'applicability':(fact.get('metadata') or {}).get('applicability')},
                'support_fact_id':row['record_id'],'support_fact_version_id':row['support_fact_version_id']}
            result.setdefault(row['entity_id'],[]).append(context)
        if sum(map(len,result.values()))>10000:raise ValueError('来源投影上下文超过 10000 条预算，请缩小批次')
        return result
