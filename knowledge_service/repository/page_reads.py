"""Scope-preserving page directories, discovery inputs and batched lineage."""
import json
from uuid import uuid4

from ..core.time import normalize_time
from .assertions_store import AssertionStore
from .pg_engine import translate
from .scoped_reads import ScopedReads, _snapshot_read


class PageReads:
    def __init__(self, repository):
        self.repo = repository

    @_snapshot_read
    def reclassify_inputs(self, project, scope, current_ontology_id, *, sample_limit=20):
        """Count every qualified knowledge group and transfer bounded samples.

        Rank only compact identities in PostgreSQL. Sample text scalars are
        fetched after the per-group bound, never with full record payloads.
        """
        if type(sample_limit) is not int or not 1 <= sample_limit <= 100:
            raise ValueError('重分类分组样本上限必须在 1 到 100 之间')
        self.repo.get_project(project)
        scope = scope or {}
        reads = ScopedReads(self.repo)
        kinds = ['entity', 'relation', 'attribute']
        if scope.get('kinds') is not None:
            kinds = [kind for kind in kinds if kind in scope['kinds']]
        if not kinds:
            return {'total_records': 0, 'groups': []}
        branches, params = [], []
        for kind in kinds:
            # A complete grouping benefits from ordinary endpoint semi-joins;
            # unlike keyset pages it must not fence each endpoint with OFFSET.
            qualified, values = reads._qualified(project, scope, kinds=[kind])
            branches.append('''SELECT r.id,r.version,r.version_id,r.read_kind AS kind,
                    r.read_type AS type,NULLIF(r.read_type,'') AS group_type,
                    NULLIF(r.payload::jsonb->>'ontology_id','') AS ontology_id
                FROM record_versions r WHERE ''' + qualified)
            params.extend(values)
        rows = self.repo._db.execute('''WITH qualified AS (
            ''' + ' UNION ALL '.join(branches) + '''
        ), ranked AS (
            SELECT q.*,row_number() OVER (
                PARTITION BY ontology_id,kind,group_type ORDER BY id,version) AS position
            FROM qualified q
        ) SELECT ontology_id,kind,
            max(type) FILTER (WHERE position=1) AS type,count(*) AS record_count,
            array_agg(version_id ORDER BY id,version) FILTER (
                WHERE ontology_id IS DISTINCT FROM ? AND position<=?) AS sample_version_ids
          FROM ranked GROUP BY ontology_id,kind,group_type
          ORDER BY ontology_id NULLS FIRST,kind,group_type NULLS FIRST''',
            [*params, current_ontology_id, sample_limit]).fetchall()
        groups = [dict(row) for row in rows]
        version_ids = [version for group in groups for version in (group['sample_version_ids'] or [])]
        samples = {}
        if version_ids:
            samples = {row['version_id']: {'id': row['id'], 'version': row['version'], 'text': row['text']}
                for row in self.repo._db.execute('''SELECT version_id,id,version,payload::jsonb->>'text' AS text
                    FROM record_versions WHERE project_id=? AND version_id=ANY(?::text[])''',
                    (project, version_ids)).fetchall()}
        for group in groups:
            group['records'] = [samples[version] for version in group.pop('sample_version_ids') or []]
        return {'total_records': sum(group['record_count'] for group in groups), 'groups': groups}

    @_snapshot_read
    def metadata_groups(self, project, scope):
        """Stream metadata alone, weighting equal objects by their record count."""
        self.repo.get_project(project)
        sql, params = ScopedReads(self.repo)._qualified(
            project, {**scope, 'filters': None})
        query = '''SELECT (r.payload::jsonb->'metadata')::text AS metadata,
                count(*) AS records FROM record_versions r WHERE ''' + sql + '''
            GROUP BY r.payload::jsonb->'metadata' ORDER BY min(r.id),min(r.version)'''
        with self.repo._db.raw.cursor(name='metadata_facets_' + uuid4().hex) as cursor:
            cursor.itersize = 200
            cursor.execute(translate(query), params)
            for row in cursor:
                yield json.loads(row['metadata']) if row['metadata'] else {}, row['records']

    @_snapshot_read
    def discovery_inputs(self, project):
        """Read candidate-bearing documents and only their materialized facts."""
        self.repo.get_project(project)
        rows = self.repo._db.execute('''WITH documents AS MATERIALIZED (
            SELECT project_id,id,version,version_id,recorded_at,superseded_at,payload::jsonb AS body
            FROM record_versions WHERE project_id=? AND superseded_at IS NULL
              AND read_kind='document' AND read_visible
        ) SELECT project_id,id,version,version_id,
            recorded_at,superseded_at,
            jsonb_build_object('id',id,'kind','document',
                'valid_from',body->'valid_from',
                'valid_until',body->'valid_until',
                'metadata',jsonb_build_object('title',COALESCE(body#>>'{metadata,title}',id),
                    'discovery_candidates',body#>'{metadata,discovery_candidates}'))::text AS payload
            FROM documents WHERE jsonb_typeof(body#>'{metadata,discovery_candidates}')='array'
            ORDER BY id''', (project,)).fetchall()
        documents = [self.repo._record(row) for row in rows]
        ids = list(dict.fromkeys(item['id'] for doc in documents
            for item in doc['metadata']['discovery_candidates']
            if item.get('kind') in {'entity','relation','attribute'} and item.get('id')))
        if not ids:
            return {'documents': documents, 'assertions': [], 'materialized': []}
        assertions = [AssertionStore._assertion(row) for row in self.repo._db.execute(
            'SELECT * FROM assertions WHERE project_id=? AND id=ANY(?::text[])',
            (project,ids)).fetchall()]
        visible = {row['id'] for row in self.repo._db.execute('''SELECT id FROM record_versions
            WHERE project_id=? AND superseded_at IS NULL AND read_visible
              AND id=ANY(?::text[])''', (project, list({x['canonical_record_id'] for x in assertions
                  if x['status']=='accepted' and x.get('canonical_record_id')}))).fetchall()}
        materialized = [{'kind':x['kind'],'metadata':{'discovery_candidate_ids':[x['id']]}}
            for x in assertions if x['status']=='accepted' and x.get('canonical_record_id') in visible]
        return {'documents':documents,'assertions':assertions,'materialized':materialized}

    @staticmethod
    def _state_scope(scope, state):
        if state not in {'all','ready','processing','failed'}:
            raise ValueError('无效的资料处理状态')
        result = dict(scope or {})
        if state == 'all':
            return result
        field = 'metadata.status'
        predicate = ({'field':field,'op':'eq','value':state} if state != 'ready' else
            {'or':[{'field':field,'op':'exists','value':False},
                   {'and':[{'field':field,'op':'ne','value':'failed'},
                           {'field':field,'op':'ne','value':'processing'}]}]})
        result['filters'] = {'and':[result['filters'],predicate]} if result.get('filters') else predicate
        return result

    @_snapshot_read
    def sources(self, project, scope, *, limit=50, cursor=None, summary_only=False,
                state='all', document_id=None):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('资料目录分页上限必须在 1 到 200 之间')
        if document_id is not None and (not isinstance(document_id,str) or not document_id or len(document_id)>1000):
            raise ValueError('无效的资料 ID')
        reads=ScopedReads(self.repo)
        self.repo.get_project(project)
        if scope.get('kinds') is not None and 'document' not in scope['kinds']:
            return {'documents':[],'total':0,'next_cursor':None,
                    'counts':{'all':0,'ready':0,'processing':0,'failed':0}}
        base,params=reads._qualified(project,scope or {},kinds=['document'])
        states=self.repo._db.execute('''SELECT CASE r.payload::jsonb#>>'{metadata,status}'
                WHEN 'failed' THEN 'failed' WHEN 'processing' THEN 'processing' ELSE 'ready' END AS state,
                count(*) AS count FROM record_versions r WHERE '''+base+' GROUP BY 1',params).fetchall()
        counts={'all':0,'ready':0,'processing':0,'failed':0}
        for row in states:
            counts[row['state']]=row['count'];counts['all']+=row['count']
        selected=self._state_scope(scope,state)
        if document_id is not None:
            if cursor is not None:
                raise ValueError('单份资料定位不能携带分页游标')
            previous=selected.get('filters');point={'field':'id','op':'eq','value':document_id}
            selected['filters']={'and':[previous,point]} if previous else point
        # Let the shared compiler validate and bind cursor identity/visibility.
        # This reads at most one bounded page; summary payload projection below
        # avoids transferring source bodies and extraction metadata.
        if not summary_only:
            page=reads.page(project,selected,kinds=['document'],after=cursor,limit=limit)
        else:
            page=self._source_metadata_page(reads,project,selected,cursor,limit)
        docs=page['records']
        self._source_derivatives(project,scope or {},docs)
        return {'documents':docs,'total':page['total'],'next_cursor':page['next_cursor'],'counts':counts}

    def _source_metadata_page(self, reads, project, scope, cursor, limit):
        # Reuse the keyset validation without reading the authoritative bodies.
        page=reads.page(project,scope,kinds=['document'],after=cursor,limit=limit,compact_graph=True)
        ids=[row['version_id'] for row in page['records']]
        if not ids:
            page['records']=[]
            return page
        fields=('title','source_file','source_format','source_size_bytes','document_parser',
                'page_count','source_document_version_id','status')
        metadata=','.join("'"+key+"',r.body#>'{metadata,"+key+"}'" for key in fields)
        # MATERIALIZED fences expression inlining: parse/decompress each large
        # document once, rather than once per projected metadata field.
        rows=self.repo._db.execute('''WITH documents AS MATERIALIZED (
            SELECT project_id,id,version,version_id,recorded_at,superseded_at,payload::jsonb AS body
            FROM record_versions WHERE project_id=? AND version_id=ANY(?::text[])
        ) SELECT r.project_id,r.id,r.version,r.version_id,
            r.recorded_at,r.superseded_at,jsonb_build_object('id',r.id,'kind','document',
                'valid_from',r.body->'valid_from','valid_until',r.body->'valid_until',
                'metadata',jsonb_strip_nulls(jsonb_build_object('''+metadata+''')))::text AS payload
            FROM documents r''',
            (project,ids)).fetchall()
        by_id={row['version_id']:self.repo._record(row) for row in rows}
        page['records']=[by_id[id] for id in ids]
        return page

    def _source_derivatives(self, project, scope, docs):
        for doc in docs:
            doc.update(derived_count=0,derived_breakdown={kind:0 for kind in ('entity','relation','attribute','chunk')},
                       derived_entities=[],derived_relations=[],derived_chunks=[])
        kinds=['entity','relation','attribute','chunk']
        if scope.get('kinds') is not None:
            kinds=[kind for kind in kinds if kind in scope['kinds']]
        if not docs or not kinds:
            return
        sql,params=ScopedReads(self.repo)._qualified(project,scope,kinds=kinds,keyset=True)
        ids=[doc['id'] for doc in docs]
        known=normalize_time(scope.get('known_at'))
        if known is None:
            associations='''SELECT a.document_id,a.canonical_record_id FROM assertions a
                WHERE a.project_id=? AND a.document_id=ANY(?::text[])
                  AND a.status='accepted' AND a.canonical_record_id IS NOT NULL'''
            association_params=[project,ids]
        else:
            associations='''SELECT a.document_id,event.canonical_record_id FROM assertions a
                CROSS JOIN LATERAL (
                    SELECT e.to_status,e.canonical_record_id FROM assertion_events e
                    WHERE e.project_id=a.project_id AND e.assertion_id=a.id AND e.created_at<=?
                    ORDER BY e.decision_version DESC LIMIT 1
                ) event WHERE a.project_id=? AND a.document_id=ANY(?::text[])
                    AND event.to_status='accepted' AND event.canonical_record_id IS NOT NULL'''
            association_params=[known,project,ids]
        # Build the source-linked key set first. An OR/subquery predicate on
        # the outer fact scan prevents PostgreSQL from using source indexes.
        # Window counts remain exact while samples/transport stay bounded.
        rows=self.repo._db.execute('''WITH associations AS ('''+associations+'''), linked AS MATERIALIZED (
            SELECT a.document_id,r.id,r.version FROM associations a
              JOIN record_versions r ON r.project_id=? AND r.id=a.canonical_record_id
            UNION
            SELECT payload::jsonb->>'source_id',id,version FROM record_versions
            WHERE project_id=? AND read_kind='chunk' AND payload::jsonb->>'source_id'=ANY(?::text[])
        ), matches AS (
            SELECT l.document_id,r.read_kind AS kind,r.id,r.read_type AS type,r.read_text AS text,
                count(*) OVER (PARTITION BY l.document_id,r.read_kind) AS total,
                row_number() OVER (PARTITION BY l.document_id,r.read_kind ORDER BY r.id,r.version) AS position
            FROM linked l JOIN record_versions r ON r.id=l.id AND r.version=l.version
            WHERE '''+sql+'''
        ) SELECT document_id,kind,id,type,text,total FROM matches
          WHERE position<=CASE kind WHEN 'chunk' THEN 10 WHEN 'attribute' THEN 1 ELSE 20 END
          ORDER BY document_id,kind,position''', [*association_params,project,project,ids,*params]).fetchall()
        by_id={doc['id']:doc for doc in docs}
        for row in rows:
            doc=by_id[row['document_id']];kind=row['kind']
            doc['derived_breakdown'][kind]=row['total']
            if kind in ('entity','relation'):
                doc['derived_entities' if kind=='entity' else 'derived_relations'].append({'text':row['text'],'type':row['type']})
            elif kind=='chunk':
                doc['derived_chunks'].append({'id':row['id'],'text':row['text']})
        for doc in docs:
            doc['derived_count']=sum(doc['derived_breakdown'].values())

    @_snapshot_read
    def assertions_batch(self, project, record_ids, *, per_record_limit=200):
        if (not isinstance(record_ids,(list,tuple)) or not 1<=len(record_ids)<=100
                or any(not isinstance(id,str) or not id or len(id)>1000 for id in record_ids)):
            raise ValueError('批量来源查询需要 1 到 100 个有效记录 ID')
        if type(per_record_limit) is not int or not 1<=per_record_limit<=200:
            raise ValueError('无效的单条来源上限')
        ids=list(dict.fromkeys(record_ids));self.repo.get_project(project)
        rows=self.repo._db.execute('''SELECT requested.id AS requested_record,a.*
            FROM unnest(?::text[]) requested(id) CROSS JOIN LATERAL (
                SELECT id,status,canonical_record_id,created_at,count(*) OVER () AS matching_total
                FROM assertions WHERE project_id=? AND canonical_record_id=requested.id
                ORDER BY created_at,id LIMIT ?
            ) a ORDER BY requested.id,a.created_at,a.id''',
            (ids,project,per_record_limit)).fetchall()
        result={id:[] for id in ids};totals={id:0 for id in ids}
        for row in rows:
            id=row['requested_record']
            result[id].append({key:row[key] for key in ('id','status','canonical_record_id')})
            totals[id]=row['matching_total']
        return {'assertions_by_record':result,'total':sum(totals.values()),
                'truncated_record_ids':[id for id in ids if totals[id]>per_record_limit],
                'per_record_limit':per_record_limit}
