"""Scoped property rows and bounded source evidence for an entity."""
from __future__ import annotations

from .scoped_reads import ScopedReads, _support_matches_record


class EntityAttributeReads:
    def __init__(self, repository):
        self.repo = repository

    def value_page(self, project, entity, scope, *, limit, preview_length,
                   predicate=None, focus_record_id=None, after=None):
        sql, params = ScopedReads(self.repo)._qualified(project, scope, kinds=['attribute'], keyset=True)
        sql += ' AND r.read_subject_id=?'
        params.append(entity)
        if predicate:
            if not isinstance(predicate, str) or len(predicate) > 2000:
                raise ValueError('无效的属性类型')
            sql += ' AND r.read_type=?'
            params.append(predicate)
        counts = self.repo._db.execute(
            'SELECT r.read_type,count(*) count FROM record_versions r WHERE ' + sql + ' GROUP BY r.read_type',
            params).fetchall()
        if focus_record_id is not None:
            if not isinstance(focus_record_id, str) or not focus_record_id or len(focus_record_id) > 200:
                raise ValueError('无效的属性事实标识')
            sql += ' AND r.id=?'
            params.append(focus_record_id)
        if after is not None:
            if (not isinstance(after, list) or len(after) != 3 or not isinstance(after[0], str)
                    or not isinstance(after[1], str) or type(after[2]) is not int):
                raise ValueError('无效的属性游标')
            sql += ' AND (r.read_type,r.id,r.version)>(?,?,?)'
            params.extend(after)
        known, known_params = ScopedReads._support_known(scope, 's')
        # Limit the identifiers first so previews and source counts are evaluated
        # only on the page. Large literals stay in the database until requested.
        rows = self.repo._db.execute('''WITH page AS MATERIALIZED (
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
            [*params, limit+1, preview_length, preview_length, *known_params]).fetchall()
        return counts, rows

    def record_for_sources(self, project, entity, record_id, scope):
        qualified, params = ScopedReads(self.repo)._qualified(project, scope, kinds=['attribute'])
        return self.repo._db.execute('''SELECT r.id,r.version,r.version_id,r.read_subject_id subject_id
            FROM record_versions r WHERE ''' + qualified + ' AND r.id=? AND r.read_subject_id=? LIMIT 1',
            [*params, record_id, entity]).fetchone()

    def source_page(self, project, record_id, version_id, scope, *, limit, after=None):
        known, params = ScopedReads._support_known(scope, 's')
        return self.repo._db.execute('''SELECT s.id,s.assertion_id,s.document_id,s.document_version_id,
            a.chunk_id,a.start_char,a.end_char,left(a.quote,2000) quote,length(a.quote)>2000 quote_truncated,
            COALESCE(d.payload::jsonb#>>'{metadata,title}',s.document_id,'手工维护') document_title,
            s.valid_from,s.valid_until FROM source_context_support s
            JOIN assertions a ON a.project_id=s.project_id AND a.id=s.assertion_id
            JOIN record_versions fact ON fact.project_id=s.project_id AND fact.version_id=?
            LEFT JOIN record_versions d ON d.project_id=s.project_id AND d.version_id=s.document_version_id
            WHERE s.project_id=? AND s.record_id=? AND s.invalid_reason IS NULL AND '''+known+
            ' AND '+_support_matches_record('fact','s')+' AND s.id>? ORDER BY s.id LIMIT ?',
            [version_id, project, record_id, *params, after or '', limit+1]).fetchall()
