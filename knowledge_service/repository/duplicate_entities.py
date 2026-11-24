"""Keyset duplicate candidate scan and bounded selected-group dependency reads."""
import base64
import hashlib
import json
from dataclasses import dataclass

from ..domain.entity_identity import DuplicateReviewRequired


KEY_COLUMNS = ('class_iri', 'identity_scope', 'match_kind', 'match_key')
QUALIFIED_KEYS = ','.join(f'k.{column}' for column in KEY_COLUMNS)


@dataclass(frozen=True)
class DocumentScope:
    document_id: str
    version_id: str
    mode: str
    artifact_id: str | None
    ingest_run_id: str | None

    @property
    def key_parts(self):
        return self.document_id, self.version_id, self.mode


def encode_key(parts):
    return base64.urlsafe_b64encode(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).decode()


def decode_key(value):
    try:
        parts = json.loads(base64.urlsafe_b64decode(value.encode()))
        if not isinstance(parts, list) or len(parts) not in (5, 8) or any(not isinstance(v, str) for v in parts):
            raise ValueError()
        return parts
    except (AttributeError, ValueError) as exc:
        raise ValueError('重复组游标无效') from exc


class DuplicateReads:
    MAX_MEMBERS=20
    MAX_DEPENDENCIES=2000

    def __init__(self,repository):self.repo=repository

    def _document_scope(self, project, document_id=None, document_version_id=None, document_mode=None):
        if document_id is document_version_id is document_mode is None:
            return None
        if not document_id or not document_version_id or document_mode not in ('within', 'related'):
            raise ValueError('按资料扫描须同时提供资料 ID、资料版本 ID 和扫描方式')
        document = self.repo._db.execute('''
            SELECT payload::jsonb#>>'{metadata,source_document_version_id}' AS artifact_id,
                payload::jsonb#>>'{metadata,run_id}' AS ingest_run_id
            FROM record_versions WHERE project_id=? AND id=? AND version_id=? AND read_kind='document'
        ''', (project, document_id, document_version_id)).fetchone()
        if document is None:
            raise ValueError('资料版本不属于当前项目或资料')
        return DocumentScope(document_id, document_version_id, document_mode,
                             document['artifact_id'], document['ingest_run_id'])

    @staticmethod
    def _document_versions(project, scope):
        # Accepted occurrences can refer to the processing receipt. Rich receipts
        # share a source artifact, and text completions link their receipt through the ingest run.
        if scope.artifact_id:
            return '''document_versions AS (
                SELECT version_id FROM record_versions WHERE project_id=? AND id=? AND read_kind='document'
                    AND payload::jsonb#>>'{metadata,source_document_version_id}'=?
            )''', [project, scope.document_id, scope.artifact_id]
        if scope.ingest_run_id:
            return '''document_versions AS (
                SELECT ?::text AS version_id UNION
                SELECT doc.version_id FROM ingest_runs run JOIN record_versions doc
                    ON doc.project_id=run.project_id AND doc.id=run.document_id
                    AND doc.version_id=run.document_version_id AND doc.read_kind='document'
                WHERE run.project_id=? AND run.document_id=? AND run.id=?
            )''', [scope.version_id, project, scope.document_id, scope.ingest_run_id]
        return 'document_versions AS (SELECT ?::text AS version_id)', [scope.version_id]

    def _document_support(self, project, scope):
        versions, params = self._document_versions(project, scope)
        sql = f'''WITH {versions}, document_support AS (
            SELECT s.project_id,s.record_id,s.id,s.record_version_id,s.document_version_id
            FROM source_context_support s JOIN document_versions d ON d.version_id=s.document_version_id
            WHERE s.project_id=? AND s.document_id=?
                AND s.kind='entity' AND s.known_until IS NULL AND s.invalid_reason IS NULL
        )'''
        return sql, [*params, project, scope.document_id]

    def _key_source(self, project, scope):
        if scope is None:
            return '', '', []
        prefix, params = self._document_support(project, scope)
        prefix += ', document_members AS (SELECT DISTINCT project_id,record_id FROM document_support)'
        if scope.mode == 'within':
            return prefix, 'JOIN document_members USING (project_id,record_id)', params
        prefix += f''', matched_groups AS (
            SELECT DISTINCT k.project_id,{QUALIFIED_KEYS} FROM document_members
            JOIN entity_duplicate_keys k USING (project_id,record_id)
        )'''
        return prefix, f'JOIN matched_groups USING (project_id,{",".join(KEY_COLUMNS)})', params

    def group(self, project, key):
        members, _ = self.group_context(project, key)
        return members

    def _group_key(self, project, key):
        parts = decode_key(key)
        if parts[0] != project:
            raise ValueError('重复组不属于当前项目')
        return parts[:5], self._document_scope(project, *parts[5:])

    def group_context(self, project, key):
        parts, scope = self._group_key(project, key)
        return self._members(parts, scope), scope

    def _member_rows(self, key_parts, scope, limit, *, after_id='', entity_id=None):
        prefix, join, params = self._key_source(key_parts[0], scope)
        member_filter = 'k.record_id=?' if entity_id is not None else 'k.record_id>?'
        return self.repo._db.execute(f'''{prefix}
            SELECT r.* FROM entity_duplicate_keys k {join} JOIN record_versions r
                ON r.project_id=k.project_id AND r.id=k.record_id AND r.superseded_at IS NULL
            WHERE k.project_id=? AND (k.class_iri,k.identity_scope,k.match_kind,k.match_key)=(?,?,?,?)
                AND {member_filter}
            ORDER BY k.record_id LIMIT ?''', [*params, *key_parts, entity_id if entity_id is not None else after_id, limit]).fetchall()

    def _members(self, key_parts, scope):
        rows = self._member_rows(key_parts, scope, self.MAX_MEMBERS + 1)
        if len(rows) < 2:
            raise ValueError('版本冲突：重复组成员已变化，请重新扫描')
        if len(rows) > self.MAX_MEMBERS:
            raise DuplicateReviewRequired('整组预览最多支持 20 个实体，请分批查看成员并逐对复核。')
        return [self.repo._record(row) for row in rows]

    def member_page(self, project, key, *, limit, after_id=None):
        if not 1 <= limit <= self.MAX_MEMBERS:
            raise ValueError('每页可读取 1 至 20 个重复组成员')
        parts, scope = self._group_key(project, key)
        rows = self._member_rows(parts, scope, limit + 1, after_id=after_id or '')
        members = [self.repo._record(row) for row in rows[:limit]]
        return members, members[-1]['id'] if len(rows) > limit else None, scope

    def review_member(self, project, key, entity_id):
        parts, scope = self._group_key(project, key)
        rows = self._member_rows(parts, scope, 1, entity_id=entity_id)
        if not rows:
            raise KeyError(entity_id)
        return self.repo._record(rows[0]), scope

    def scan(self, project, *, limit=20, cursor=None, document_id=None, document_version_id=None, document_mode=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('重复扫描分页上限为50组')
        self.repo.get_project(project)
        scope = self._document_scope(project, document_id, document_version_id, document_mode)
        scope_parts = scope.key_parts if scope else ()
        prefix, join, params = self._key_source(project, scope)
        clauses = 'k.project_id=?'
        params.append(project)
        if cursor:
            parts = decode_key(cursor)
            if parts[0] != project or tuple(parts[5:]) != scope_parts:
                raise ValueError('扫描游标不属于当前项目或扫描范围')
            clauses += f' AND ({QUALIFIED_KEYS})>(?,?,?,?)'
            params.extend(parts[1:5])
        keys = self.repo._db.execute(f'''{prefix}
            SELECT {QUALIFIED_KEYS},count(*) AS member_count FROM entity_duplicate_keys k {join}
            WHERE {clauses} GROUP BY {QUALIFIED_KEYS} HAVING count(*)>1
            ORDER BY {QUALIFIED_KEYS} LIMIT ?''', [*params, limit + 1]).fetchall()
        groups = []
        for row in keys[:limit]:
            parts = [project, *(row[column] for column in KEY_COLUMNS)]
            key = encode_key([*parts, *scope_parts])
            blocked = row['member_count'] > self.MAX_MEMBERS
            members = [] if blocked else self._members(parts, scope)
            groups.append(dict(key=key, name=members[0]['text'] if members else row['match_key'], type=row['class_iri'],
                match_kind=row['match_kind'], member_count=row['member_count'], members=members,
                review_receipt=None,
                blocked=blocked, blocked_reason='整组预览最多支持 20 个实体，请分批查看成员并逐对复核。' if blocked else None))
        pointers = {group['key']: self._review_pointer(project, [m['id'] for m in group['members']])
                    for group in groups if group['members']}
        if pointers:
            stored = self.repo._db.execute('''SELECT id,payload FROM artifacts WHERE project_id=?
                AND kind='duplicate_review_group' AND id=ANY(?::text[])''', (project, list(pointers.values()))).fetchall()
            by_id = {r['id']: json.loads(r['payload']) for r in stored}
            for group in groups:
                pointer = by_id.get(pointers.get(group['key']))
                if pointer:
                    group['review_receipt'] = self._validated_receipt(project, group['key'],
                        [m['id'] for m in group['members']], scope, pointer)
        return dict(groups=groups, next_cursor=groups[-1]['key'] if len(keys) > limit else None)

    def review_signature(self, project, ids, scope):
        """Bind a review to the currently visible identities, their facts, evidence and published ontology."""
        signatures = self.repo._db.execute('''WITH adjacent AS MATERIALIZED (
            SELECT id,version_id,read_subject_id,read_object_id FROM record_versions WHERE project_id=? AND superseded_at IS NULL
                AND read_visible AND (id=ANY(?::text[]) OR read_subject_id=ANY(?::text[])
                    OR (read_kind='relation' AND read_object_id=ANY(?::text[])))), relevant AS MATERIALIZED (
            SELECT id,version_id FROM adjacent UNION
            SELECT id,version_id FROM record_versions WHERE project_id=? AND superseded_at IS NULL AND read_visible
                AND read_kind='entity' AND id IN (SELECT read_subject_id FROM adjacent UNION SELECT read_object_id FROM adjacent))
            SELECT (SELECT id FROM ontologies WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 1) AS ontology,
                (SELECT encode(sha256(convert_to(coalesce(string_agg(jsonb_build_array(id,version_id)::text,
                    ',' ORDER BY id),''),'UTF8')),'hex') FROM relevant) AS revisions,
                (SELECT encode(sha256(convert_to(coalesce(string_agg(
                    jsonb_build_array(a.id,a.canonical_record_id,a.decision_version,a.status)::text,
                    ',' ORDER BY a.id),''),'UTF8')),'hex') FROM relevant r JOIN assertions a
                    ON a.project_id=? AND a.canonical_record_id=r.id) AS assertions,
                (SELECT encode(sha256(convert_to(coalesce(string_agg(
                    jsonb_build_array(s.id,s.record_version_id,s.document_version_id,s.valid_from,s.valid_until,
                        s.applicability)::text, ',' ORDER BY s.id),''),'UTF8')),'hex')
                    FROM relevant r JOIN source_context_support s ON s.project_id=? AND s.record_id=r.id
                        AND s.known_until IS NULL AND s.invalid_reason IS NULL) AS sources''',
            (project, ids, ids, ids, project, project, project, project)).fetchone()
        signature = dict(signatures)
        if scope:
            signature['document_support'] = self.document_support_fingerprint(project, ids, scope)
        return signature

    @staticmethod
    def _review_pointer(project, ids):
        identity = json.dumps([project, sorted(ids)], ensure_ascii=False, separators=(',', ':'))
        return 'duplicate-review-group:' + hashlib.sha256(identity.encode()).hexdigest()

    def current_review_receipt(self, project, key, members, scope, *, signature=None):
        ids = sorted(member['id'] for member in members)
        try:
            pointer = self.repo.get_artifact('duplicate_review_group', self._review_pointer(project, ids))
        except KeyError:
            return None
        return self._validated_receipt(project, key, ids, scope, pointer, signature)

    def _validated_receipt(self, project, key, ids, scope, pointer, signature=None):
        # A second alias or a document-specific scan can find the same set of
        # survivors. Validate the evidence from the scope actually reviewed.
        if pointer['receipt']['key'] != key:
            _, scope = self._group_key(project, pointer['receipt']['key'])
            signature = None
        if (signature or self.review_signature(project, ids, scope)) != pointer['signature']:
            return None
        return pointer['receipt']

    def confirmed_receipt(self, project, fingerprint):
        try:
            stored = self.repo.get_artifact('duplicate_review', 'duplicate-review:' + fingerprint)
        except KeyError:
            return None
        return stored if stored['project_id'] == project else None

    def save_review(self, project, key, receipt, request_hash, scope):
        fingerprint = receipt['preview_fingerprint']
        self.repo.save_artifact('duplicate_review', dict(id='duplicate-review:' + fingerprint,
            project_id=project, request_hash=request_hash, receipt=receipt))
        ids = sorted(target['keep_id'] for target in receipt['targets'])
        if len(ids) > 1:
            self.repo.save_artifact('duplicate_review_group', dict(id=self._review_pointer(project, ids),
                project_id=project, signature=self.review_signature(project, ids, scope), receipt=receipt))

    def review_transaction(self):
        # Receipts and any rewritten facts share the formal writer's lock order and commit.
        return self.repo._formal_write_transaction()

    def relation_contexts(self, project, entity_ids):
        contexts = {entity_id: dict(total=0, examples=[]) for entity_id in entity_ids}
        rows = self.repo._db.execute('''SELECT m.id AS entity_id,rel.*,
                s.payload::jsonb->>'text' AS subject_label, o.payload::jsonb->>'text' AS object_label
            FROM unnest(?::text[]) AS m(id) CROSS JOIN LATERAL (
                SELECT r.id,r.read_subject_id AS subject_id,r.read_object_id AS object_id,r.read_type AS type,
                    r.payload::jsonb->>'valid_from' AS valid_from,r.payload::jsonb->>'valid_until' AS valid_until,
                    count(*) OVER () AS total
                FROM record_versions r WHERE r.project_id=? AND r.superseded_at IS NULL AND r.read_visible
                    AND r.read_kind='relation' AND (r.read_subject_id=m.id OR r.read_object_id=m.id)
                ORDER BY r.id LIMIT 5) rel
            LEFT JOIN record_versions s ON s.project_id=? AND s.id=rel.subject_id AND s.superseded_at IS NULL
            LEFT JOIN record_versions o ON o.project_id=? AND o.id=rel.object_id AND o.superseded_at IS NULL
            ORDER BY m.id,rel.id''', (entity_ids, project, project, project)).fetchall()
        for raw in rows:
            row = dict(raw)
            context = contexts[row.pop('entity_id')]
            context['total'] = row.pop('total')
            context['examples'].append(row)
        return contexts

    def document_support_fingerprint(self, project, entity_ids, scope):
        prefix, params = self._document_support(project, scope)
        return self.repo._db.execute(f'''{prefix}
            SELECT encode(sha256(convert_to(coalesce(string_agg(
                jsonb_build_array(id,record_id,record_version_id,document_version_id)::text,
                ',' ORDER BY id),''),'UTF8')),'hex') AS fingerprint
            FROM document_support WHERE record_id=ANY(?::text[])''', [*params, entity_ids]).fetchone()['fingerprint']

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
        if len(rows)>self.MAX_DEPENDENCIES:raise DuplicateReviewRequired('本组影响记录超过 2000 条，请逐对复核。')
        return {row['id']:self.repo._record(row) for row in rows}

    def source_pages(self,project,entity_ids,*,limit=10,after='',document_scope=None):
        if not 1<=len(entity_ids)<=self.MAX_MEMBERS or not 1<=limit<=20:raise ValueError('来源读取范围超过上限')
        prefix, params = '', []
        sort_key, sort_params = 'context.id', []
        if document_scope:
            versions, params = self._document_versions(project, document_scope)
            prefix = versions + ','
            sort_key = '''(CASE WHEN context.document_id=? AND context.document_version_id IN
                (SELECT version_id FROM document_versions) THEN '0' ELSE '1' END)||context.id'''
            sort_params = [document_scope.document_id]
        rows=self.repo._db.execute(f'''WITH {prefix} members AS (SELECT unnest(?::text[]) AS member_id),
            counts AS (SELECT record_id,count(*) AS support_count,count(DISTINCT document_id) AS document_count
              FROM source_context_support WHERE project_id=? AND record_id=ANY(?::text[])
               AND known_until IS NULL AND invalid_reason IS NULL GROUP BY record_id)
            SELECT members.member_id,coalesce(counts.support_count,0) AS support_count,
             coalesce(counts.document_count,0) AS document_count,s.id AS context_id,s.assertion_id,
             s.document_id,s.document_version_id,s.valid_from,s.valid_until,s.applicability,
             a.chunk_id,a.start_char,a.end_char,left(a.quote,2000) AS quote,length(a.quote)>2000 AS quote_truncated,
             d.payload::jsonb#>>'{{metadata,title}}' AS document_title,
             d.payload::jsonb#>>'{{metadata,source_document_version_id}}' AS source_document_version_id,s.cursor_key
            FROM members LEFT JOIN counts ON counts.record_id=members.member_id
            LEFT JOIN LATERAL (SELECT context.*,ordering.cursor_key FROM source_context_support context
             CROSS JOIN LATERAL (SELECT {sort_key} AS cursor_key) ordering
             WHERE context.project_id=? AND context.record_id=members.member_id
              AND context.known_until IS NULL AND context.invalid_reason IS NULL AND ordering.cursor_key>?
             ORDER BY ordering.cursor_key LIMIT ?) s ON TRUE
            LEFT JOIN assertions a ON a.project_id=? AND a.id=s.assertion_id
            LEFT JOIN record_versions d ON d.project_id=? AND d.id=s.document_id AND d.version_id=s.document_version_id
             AND d.read_kind='document'
            ORDER BY members.member_id,s.cursor_key''',
            [*params,entity_ids,project,entity_ids,*sort_params,project,after,limit+1,project,project]).fetchall()
        pages={entity_id:dict(document_count=0,support_count=0,source_contexts=[],next_after=None) for entity_id in entity_ids}
        for raw in rows:
            row=dict(raw);page=pages[row.pop('member_id')]
            page['document_count']=row.pop('document_count');page['support_count']=row.pop('support_count')
            if row['context_id'] is not None:
                if row['applicability'] is not None:row['applicability']=json.loads(row['applicability'])
                page['source_contexts'].append(row)
        for page in pages.values():
            if len(page['source_contexts'])>limit:page['next_after']=page['source_contexts'][limit-1]['cursor_key']
            page['source_contexts']=page['source_contexts'][:limit]
            for context in page['source_contexts']:
                context.pop('cursor_key')
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
