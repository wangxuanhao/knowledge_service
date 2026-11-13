"""Keyset duplicate candidate scan and bounded selected-group dependency reads."""
import base64
import json


def encode_key(parts):
    return base64.urlsafe_b64encode(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).decode()


def decode_key(value):
    try:
        parts=json.loads(base64.urlsafe_b64decode(value.encode()))
        if not isinstance(parts,list) or len(parts)!=5 or any(not isinstance(v,str) for v in parts):raise ValueError()
        return parts
    except Exception as exc:raise ValueError('重复组游标无效') from exc


class DuplicateReads:
    MAX_MEMBERS=20
    MAX_DEPENDENCIES=2000

    def __init__(self,repository):self.repo=repository

    def group(self,project,key):
        parts=decode_key(key)
        if parts[0]!=project:raise ValueError('重复组不属于当前项目')
        rows=self.repo._db.execute('''SELECT r.* FROM entity_duplicate_keys k JOIN record_versions r
            ON r.project_id=k.project_id AND r.id=k.record_id AND r.superseded_at IS NULL
            WHERE k.project_id=? AND k.class_iri=? AND k.identity_scope=? AND k.match_kind=? AND k.match_key=?
            ORDER BY r.id LIMIT ?''',[*parts,self.MAX_MEMBERS+1]).fetchall()
        if len(rows)<2:raise ValueError('版本冲突：重复组成员已变化，请重新扫描')
        if len(rows)>self.MAX_MEMBERS:raise ValueError('重复组超过20个实体，请先按身份范围复核拆分')
        return [self.repo._record(row) for row in rows]

    def scan(self,project,*,limit=20,cursor=None):
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('重复扫描分页上限为50组')
        self.repo.get_project(project);clauses=['project_id=?'];params=[project]
        if cursor:
            parts=decode_key(cursor)
            if parts[0]!=project:raise ValueError('扫描游标不属于当前项目')
            clauses.append('(class_iri,identity_scope,match_kind,match_key)>(?,?,?,?)');params.extend(parts[1:])
        keys=self.repo._db.execute('''SELECT class_iri,identity_scope,match_kind,match_key,count(*) AS member_count
            FROM entity_duplicate_keys WHERE '''+' AND '.join(clauses)+
            ' GROUP BY class_iri,identity_scope,match_kind,match_key HAVING count(*)>1 ORDER BY class_iri,identity_scope,match_kind,match_key LIMIT ?',
            [*params,limit+1]).fetchall()
        groups=[]
        for row in keys[:limit]:
            key=encode_key([project,*[row[x] for x in ('class_iri','identity_scope','match_kind','match_key')]])
            blocked=row['member_count']>self.MAX_MEMBERS
            members=[] if blocked else self.group(project,key)
            groups.append(dict(key=key,name=members[0]['text'] if members else row['match_key'],type=row['class_iri'],
                match_kind=row['match_kind'],member_count=row['member_count'],members=members,
                blocked=blocked,blocked_reason='组成员超过20，请先核对身份范围' if blocked else None))
        return dict(groups=groups,next_cursor=groups[-1]['key'] if len(keys)>limit else None)

    def merge_rows(self,project,entity_ids):
        if not 2<=len(entity_ids)<=self.MAX_MEMBERS:raise ValueError('每个融合组须包含2..20个实体')
        # Select only the chosen identities, their facts, and those facts' entity
        # endpoints. Query/result budget rejects dense groups before decoding.
        rows=self.repo._db.execute('''WITH facts AS (
            SELECT * FROM record_versions WHERE project_id=? AND superseded_at IS NULL
              AND read_visible AND (id=ANY(?::text[]) OR read_subject_id=ANY(?::text[])
               OR (read_kind='relation' AND read_object_id=ANY(?::text[]))) LIMIT ?),
          endpoints AS (SELECT read_subject_id AS id FROM facts UNION SELECT read_object_id FROM facts)
          SELECT * FROM facts UNION SELECT r.* FROM record_versions r WHERE r.project_id=?
           AND r.superseded_at IS NULL AND r.read_kind='entity' AND r.read_visible AND r.id IN(SELECT id FROM endpoints)
          LIMIT ?''',(project,entity_ids,entity_ids,entity_ids,self.MAX_DEPENDENCIES+1,project,self.MAX_DEPENDENCIES+1)).fetchall()
        if len(rows)>self.MAX_DEPENDENCIES:raise ValueError('本组影响记录超过2000，请缩小融合组后预览')
        return {row['id']:self.repo._record(row) for row in rows}

    def source_pages(self,project,entity_ids,*,limit=10,after=''):
        if not 1<=len(entity_ids)<=self.MAX_MEMBERS or not 1<=limit<=20:raise ValueError('来源读取范围超过上限')
        rows=self.repo._db.execute('''WITH members AS (SELECT unnest(?::text[]) AS member_id),
            counts AS (SELECT record_id,count(*) AS support_count,count(DISTINCT document_id) AS document_count
              FROM source_context_support WHERE project_id=? AND record_id=ANY(?::text[])
               AND known_until IS NULL AND invalid_reason IS NULL GROUP BY record_id)
            SELECT members.member_id,coalesce(counts.support_count,0) AS support_count,
             coalesce(counts.document_count,0) AS document_count,s.id AS context_id,s.assertion_id,
             s.document_id,s.document_version_id,s.valid_from,s.valid_until,s.applicability,
             a.chunk_id,a.start_char,a.end_char,left(a.quote,2000) AS quote,length(a.quote)>2000 AS quote_truncated,
             d.payload::jsonb#>>'{metadata,title}' AS document_title
            FROM members LEFT JOIN counts ON counts.record_id=members.member_id
            LEFT JOIN LATERAL (SELECT * FROM source_context_support context
             WHERE context.project_id=? AND context.record_id=members.member_id
              AND context.known_until IS NULL AND context.invalid_reason IS NULL AND context.id>?
             ORDER BY context.id LIMIT ?) s ON TRUE
            LEFT JOIN assertions a ON a.project_id=? AND a.id=s.assertion_id
            LEFT JOIN record_versions d ON d.project_id=? AND d.version_id=s.document_version_id
            ORDER BY members.member_id,s.id''',(entity_ids,project,entity_ids,project,after,limit+1,project,project)).fetchall()
        pages={entity_id:dict(document_count=0,support_count=0,source_contexts=[],next_after=None) for entity_id in entity_ids}
        for raw in rows:
            row=dict(raw);page=pages[row.pop('member_id')]
            page['document_count']=row.pop('document_count');page['support_count']=row.pop('support_count')
            if row['context_id'] is not None:
                if row['applicability'] is not None:row['applicability']=json.loads(row['applicability'])
                page['source_contexts'].append(row)
        for page in pages.values():
            if len(page['source_contexts'])>limit:page['next_after']=page['source_contexts'][limit-1]['context_id']
            page['source_contexts']=page['source_contexts'][:limit]
        return pages

    def fact_support_counts(self,project,groups):
        if not groups:return {}
        membership=[dict(group_id=key,record_id=record_id) for key,ids in groups.items() for record_id in ids]
        rows=self.repo._db.execute('''WITH members AS (
            SELECT value->>'group_id' AS group_id,value->>'record_id' AS record_id
            FROM jsonb_array_elements(?::jsonb))
            SELECT members.group_id,count(DISTINCT s.assertion_id) AS support_count,
             count(DISTINCT s.document_id) AS document_count FROM members
            LEFT JOIN record_versions r ON r.project_id=? AND r.id=members.record_id AND r.superseded_at IS NULL
            LEFT JOIN source_context_support s ON s.project_id=r.project_id AND s.record_id=r.id
             AND s.record_version_id=r.version_id AND s.known_until IS NULL AND s.invalid_reason IS NULL
            GROUP BY members.group_id''',(json.dumps(membership),project)).fetchall()
        return {row['group_id']:dict(support_count=row['support_count'],document_count=row['document_count']) for row in rows}
