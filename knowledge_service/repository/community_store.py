"""Current approved graph inputs and durable community snapshots in PostgreSQL."""
import json

from .scoped_reads import ScopedReads


ACTIVE_JOB_STATES = frozenset({'queued', 'running', 'retry_wait'})
_ORDER = 'level, member_count DESC, id'
_SUMMARY = """id, title, level, parent_id, member_count, relationship_count, evidence_count,
    report->>'status' AS report_status, report->>'title' AS report_title,
    report->>'summary' AS summary"""

# Verify the coarse index hit against fields that can explain the result to a reader.
_KEYWORD_MATCH = """
    SELECT jsonb_build_object(
        'field', field, 'source_title', source_title,
        'before', (CASE WHEN match_start > 25 THEN '…' ELSE '' END)
                  || substring(value FROM greatest(1, match_start - 24) FOR least(24, match_start - 1)),
        'term', substring(value FROM match_start FOR char_length(needle)),
        'after', substring(value FROM match_start + char_length(needle) FOR 42)
                 || (CASE WHEN char_length(value) >= match_start + char_length(needle) + 42
                          THEN '…' ELSE '' END)
    ) AS keyword_match
    FROM (
        SELECT fields.*, matched.*
        FROM (
            SELECT * FROM (VALUES
                (1, 0::bigint, 0, 'report_title',
                    CASE WHEN NOT candidate.answer_search THEN candidate.report->>'title' END, NULL::text),
                (2, 0::bigint, 0, 'report_summary', candidate.report->>'summary', NULL::text),
                (3, 0::bigint, 0, 'community_title', candidate.title, NULL::text)
            ) AS headline(priority, ordinal, part, field, value, source_title)
            UNION ALL
            SELECT 4, ordinal, part, field, field_value, NULL::text
            FROM jsonb_array_elements(candidate.content->'members') WITH ORDINALITY AS items(item, ordinal)
            CROSS JOIN LATERAL (VALUES
                (1, 'entity', item->>'text'), (2, 'entity_alias', item->>'aliases'),
                (3, 'entity_id', CASE WHEN candidate.answer_search THEN item->>'id' END),
                (4, 'entity_type', CASE WHEN candidate.answer_search THEN item->>'type' END)
            ) AS member_fields(part, field, field_value)
            UNION ALL
            SELECT 5, ordinal, part, field, field_value, NULL::text
            FROM jsonb_array_elements(candidate.content->'relationships') WITH ORDINALITY AS items(item, ordinal)
            CROSS JOIN LATERAL (VALUES
                (1, 'relationship', item->>'text'),
                (2, 'relationship_id', CASE WHEN candidate.answer_search THEN item->>'id' END),
                (3, 'relationship_type', CASE WHEN candidate.answer_search THEN item->>'type' END)
            ) AS relation_fields(part, field, field_value)
            UNION ALL
            SELECT 6, ordinal, 0, CASE WHEN item->>'kind' = 'document' THEN 'evidence' ELSE 'graph_evidence' END,
                item->>'quote', item->>'title'
            FROM jsonb_array_elements(candidate.content->'evidence') WITH ORDINALITY AS sources(item, ordinal)
            WHERE item->>'kind' = 'document' OR candidate.answer_search
        ) AS fields
        CROSS JOIN LATERAL (
            SELECT needle, term_order, strpos(lower(value), needle) AS match_start
            FROM unnest(candidate.needles) WITH ORDINALITY AS terms(needle, term_order)
            WHERE needle <> '' AND strpos(lower(value), needle) > 0
            ORDER BY term_order
            LIMIT 1
        ) AS matched
    ) AS located
    ORDER BY term_order, priority, ordinal, part
    LIMIT 1
"""


def _json(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


class CommunityStore:
    def __init__(self, repository):
        self.repo = repository

    def scope(self, project, scope_key, *, lock=False):
        self.repo.get_project(project)
        sql = """SELECT s.*, COALESCE(g.generation, 0) AS generation
                 FROM community_scopes s
                 LEFT JOIN community_graph_changes g USING (project_id)
                 WHERE s.project_id=? AND s.scope_key=?"""
        return self.repo._db.execute(
            sql + (' FOR UPDATE OF s' if lock else ''), (project, scope_key)).fetchone()

    def enable(self, project, scope_key, scope):
        self.repo.get_project(project)
        self.repo._db.execute("""
            INSERT INTO community_scopes(project_id, scope_key, scope)
            VALUES (?, ?, ?::jsonb) ON CONFLICT DO NOTHING""",
            (project, scope_key, _json(scope)))

    def enable_vectors(self, project, scope_key):
        self.repo._db.execute("""UPDATE community_scopes SET vector_enabled=true
            WHERE project_id=? AND scope_key=?""", (project, scope_key))

    def write_vector_snapshot(self, project, scope_key, snapshot):
        self.repo._db.execute("""UPDATE community_scopes SET vector_snapshot=?::jsonb
            WHERE project_id=? AND scope_key=?""", (_json(snapshot), project, scope_key))

    def levels(self, project, scope_key):
        return [dict(row) for row in self.repo._db.execute("""
            SELECT level, count(*) AS communities,
                   count(*) FILTER (WHERE report->>'status'='ready') AS reports_ready
            FROM knowledge_communities WHERE project_id=? AND scope_key=?
            GROUP BY level ORDER BY level""", (project, scope_key)).fetchall()]

    def ready_reports(self, project, scope_key, *, level=None):
        rows = self.repo._db.execute("""
            SELECT id, report::text AS report FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND report->>'status'='ready'
              AND (?::int IS NULL OR level=?)
            ORDER BY level, member_count DESC, id""", (project, scope_key, level, level)).fetchall()
        return [{'id': row['id'], 'report': json.loads(row['report'])} for row in rows]

    def member_communities(self, project, scope_key, member_id):
        rows = self.repo._db.execute("""
            SELECT id FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND member_ids @> ARRAY[?::text]
            """, (project, scope_key, member_id)).fetchall()
        return {row['id'] for row in rows}

    def job(self, scope):
        if not scope or not scope['job_id']:
            return None
        try:
            return self.repo.get_artifact('job', scope['job_id'])
        except KeyError:
            return None

    def set_job(self, project, scope_key, job):
        self.repo.save_artifact('job', job)
        self.repo._db.execute("""
            UPDATE community_scopes SET job_id=? WHERE project_id=? AND scope_key=?""",
            (job['id'], project, scope_key))

    def due(self):
        return self.repo._db.execute("""
            SELECT s.project_id, s.scope_key, s.scope::text AS scope
            FROM community_scopes s
            JOIN community_graph_changes g USING (project_id)
            LEFT JOIN artifacts j ON j.id=s.job_id AND j.kind='job'
            WHERE g.generation>s.observed_generation
              AND g.changed_at<clock_timestamp()-interval '1 second'
              AND COALESCE(j.payload::jsonb->>'status', '') NOT IN ('queued','running','retry_wait')
            ORDER BY g.changed_at, s.project_id, s.scope_key LIMIT 32""").fetchall()

    def mark_observed(self, project, scope_key, generation, revision):
        self.repo._db.execute("""
            UPDATE community_scopes SET observed_generation=?, live_revision=?
            WHERE project_id=? AND scope_key=? AND observed_generation<=?""",
            (generation, revision, project, scope_key, generation))

    def approved_graph(self, project, read_scope, *, include_records=False):
        reads = ScopedReads(self.repo)
        entity_sql, entity_params = reads._qualified(project, read_scope, kinds=['entity'])
        relation_sql, relation_params = reads._qualified(
            project, read_scope, kinds=['relation'], entity_source='entities')
        support_sql, support_params = reads._scope_predicates(read_scope, 's')
        graph = f"""
            WITH entities AS MATERIALIZED (
                SELECT r.* FROM record_versions r WHERE {entity_sql}
            ), graph AS MATERIALIZED (
                SELECT * FROM entities
                UNION ALL
                SELECT r.* FROM record_versions r WHERE {relation_sql}
            ), connected AS MATERIALIZED (
                SELECT id FROM graph WHERE read_kind='relation'
                UNION SELECT read_subject_id FROM graph WHERE read_kind='relation'
                UNION SELECT read_object_id FROM graph WHERE read_kind='relation'
            ), supports AS MATERIALIZED (
                SELECT s.id, s.assertion_id, s.record_id, s.record_version_id,
                       s.document_id, s.document_version_id
                FROM graph r JOIN connected c ON c.id=r.id
                JOIN source_context_support s
                  ON s.project_id=r.project_id AND s.record_id=r.id
                JOIN assertions a ON a.project_id=s.project_id AND a.id=s.assertion_id
                  AND a.status='accepted' AND a.decision_version=s.decision_version
                  AND a.canonical_record_id=s.record_id
                WHERE s.invalid_reason IS NULL AND s.known_until IS NULL AND {support_sql}
            )"""
        params = [*entity_params, *relation_params, *support_params]
        digest = self.repo._db.execute(graph + """
            SELECT (SELECT COALESCE(generation, 0) FROM community_graph_changes
                    WHERE project_id=?) AS generation,
                   (SELECT md5(COALESCE(string_agg(json_build_array(
                        version_id, read_text, read_type, read_subject_id, read_object_id,
                        payload::jsonb#>'{metadata,aliases}')::text, ',' ORDER BY id), ''))
                    FROM graph) AS records_digest,
                   (SELECT md5(COALESCE(string_agg(json_build_array(
                        id, assertion_id, record_id, record_version_id, document_id, document_version_id)::text,
                        ',' ORDER BY record_id,id), '')) FROM supports) AS supports_digest""",
            [*params, project]).fetchone()
        result = dict(digest)
        result['generation'] = result['generation'] or 0
        if include_records:
            result['records'] = [dict(row) for row in self.repo._db.execute(graph + """
                SELECT id, version, version_id, read_kind AS kind, read_type AS type, read_text AS text,
                       read_subject_id AS subject_id, read_object_id AS object_id,
                       (payload::jsonb#>'{metadata,aliases}')::text AS aliases
                FROM graph ORDER BY id""", params).fetchall()]
            result['supports'] = [dict(row) for row in self.repo._db.execute(graph + """
                SELECT * FROM supports WHERE document_version_id IS NOT NULL ORDER BY record_id, id
                """, params).fetchall()]
        return result

    def replace(self, project, scope_key, revision, algorithm, counts, communities):
        self.repo._db.execute('DELETE FROM knowledge_communities WHERE project_id=? AND scope_key=?',
                              (project, scope_key))
        if communities:
            self.repo._db.executemany("""
                INSERT INTO knowledge_communities(
                    project_id, scope_key, id, parent_id, level, title, member_ids, member_count,
                    relationship_count, evidence_count, search_text, content, report)
                VALUES (?, ?, ?, ?, ?, ?, ?::text[], ?, ?, ?, ?, ?::jsonb, ?::jsonb)""",
                [(project, scope_key, row['id'], row['parent_id'], row['level'], row['title'],
                  row['member_ids'], len(row['member_ids']), len(row['content']['relationships']),
                  len(row['content']['evidence']), row['search_text'], _json(row['content']),
                  _json(row['report'])) for row in communities])
        self.repo._db.execute("""
            UPDATE community_scopes SET revision=?, algorithm=?, counts=?::jsonb, built_at=clock_timestamp()
            WHERE project_id=? AND scope_key=?""",
            (revision, algorithm, _json(counts), project, scope_key))

    def counts(self, project, scope_key):
        rows = self.repo._db.execute("""
            SELECT report->>'status' AS status, count(*) AS total FROM knowledge_communities
            WHERE project_id=? AND scope_key=? GROUP BY status""", (project, scope_key)).fetchall()
        return {row['status']: row['total'] for row in rows}

    def list(self, project, scope_key, *, keyword='', anchor='', limit=30, offset=0):
        filters, params = ['project_id=?', 'scope_key=?'], [project, scope_key]
        if anchor:
            filters.append('member_ids @> ARRAY[?::text]')
            params.append(anchor)
        where = ' AND '.join(filters)
        if keyword:
            row = self.repo._db.execute(f"""
                WITH keyword_query AS (SELECT ?::text AS needle),
                candidates AS (
                    SELECT knowledge_communities.*, ARRAY[needle] AS needles, FALSE AS answer_search
                    FROM knowledge_communities CROSS JOIN keyword_query
                    WHERE {where} AND greatest(strpos(search_text, needle),
                        strpos(lower(report->>'summary'), needle), strpos(lower(report->>'title'), needle)) > 0
                ), matched AS MATERIALIZED (
                    SELECT {_SUMMARY}, match.keyword_match FROM candidates AS candidate
                    CROSS JOIN LATERAL ({_KEYWORD_MATCH}) AS match
                ), page AS (
                    SELECT * FROM matched ORDER BY {_ORDER} LIMIT ? OFFSET ?
                )
                SELECT (SELECT count(*) FROM matched) AS total,
                       coalesce(jsonb_agg(to_jsonb(page) ORDER BY {_ORDER}), '[]'::jsonb)::text AS communities
                FROM page""", [keyword.casefold(), *params, limit, offset]).fetchone()
            return json.loads(row['communities']), row['total']
        total = self.repo._db.execute(
            f'SELECT count(*) FROM knowledge_communities WHERE {where}', params).fetchone()[0]
        rows = self.repo._db.execute(f"""
            SELECT {_SUMMARY} FROM knowledge_communities WHERE {where}
            ORDER BY {_ORDER} LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
        return [dict(row) for row in rows], total

    def detail(self, project, scope_key, community_id):
        item = self.repo._db.execute(f"""
            SELECT {_SUMMARY}, content::text, report::text FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND id=?""", (project, scope_key, community_id)).fetchone()
        if item is None:
            raise KeyError(community_id)
        children = self.repo._db.execute(f"""
            SELECT {_SUMMARY} FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND parent_id=? ORDER BY {_ORDER}""",
            (project, scope_key, community_id)).fetchall()
        data = dict(item)
        return {**data, 'content': json.loads(data['content']), 'report': json.loads(data['report']),
                'children': [dict(row) for row in children]}

    def summaries(self, project, scope_key, ids):
        if not ids:
            return {}
        rows = self.repo._db.execute(f"""
            SELECT {_SUMMARY} FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND id=ANY(?::text[])
            """, (project, scope_key, ids)).fetchall()
        return {row['id']: dict(row) for row in rows}

    def candidates(self, project, scope_key, terms, *, limit):
        if not terms:
            return [], 0
        rows = self.repo._db.execute(f"""
            WITH ranked AS (
                SELECT c.id, c.level, c.member_count,
                       (SELECT count(*) FROM unnest(?::text[]) AS query(term)
                        WHERE strpos(c.search_text || ' ' || lower(coalesce(c.report->>'summary', '')),
                                     query.term)>0) AS matched
                FROM knowledge_communities c WHERE c.project_id=? AND c.scope_key=?
            )
            SELECT id, count(*) OVER () AS matching_count FROM ranked WHERE matched>0
            ORDER BY matched DESC, level DESC, member_count, id LIMIT ?""",
            (terms, project, scope_key, limit)).fetchall()
        return [row['id'] for row in rows], rows[0]['matching_count'] if rows else 0

    def answer_details(self, project, scope_key, ids, *, cited_only=False, keyword_terms=()):
        if not ids:
            return []
        evidence = "content->'evidence'"
        if cited_only:
            evidence = """(SELECT coalesce(jsonb_agg(source), '[]'::jsonb)
                           FROM jsonb_array_elements(content->'evidence') AS refs(source)
                           WHERE source->>'id' IN (
                               SELECT jsonb_array_elements_text(finding->'citations')
                               FROM jsonb_array_elements(report->'findings') AS findings(finding)))"""
        params = [project, scope_key, ids]
        keyword_field = keyword_join = ''
        if keyword_terms:
            keyword_field = ', match.keyword_match::text AS keyword_match'
            keyword_join = f"""
                LEFT JOIN LATERAL (
                    SELECT located.keyword_match
                    FROM (SELECT c.content, c.report, c.title, ?::text[] AS needles, TRUE AS answer_search) AS candidate
                    CROSS JOIN LATERAL ({_KEYWORD_MATCH}) AS located
                ) AS match ON TRUE
            """
            params.insert(0, list(keyword_terms))
        rows = self.repo._db.execute(f"""
            SELECT {_SUMMARY}, ({evidence})::text AS evidence, report::text{keyword_field}
            FROM knowledge_communities c {keyword_join}
            WHERE project_id=? AND scope_key=? AND id=ANY(?::text[])
            """, params).fetchall()
        by_id = {row['id']: {**dict(row), 'evidence': json.loads(row['evidence']),
                             'report': json.loads(row['report'])} for row in rows}
        if keyword_terms:
            for row in by_id.values():
                match = row.pop('keyword_match')
                if match is not None:
                    row['keyword_match'] = json.loads(match)
        return [by_id[ident] for ident in ids]

    def representative_communities(self, project, scope_key, *, limit):
        rows = self.repo._db.execute(f"""
            SELECT id FROM knowledge_communities WHERE project_id=? AND scope_key=?
            ORDER BY (report->>'status'='ready') DESC, {_ORDER} LIMIT ?
            """, (project, scope_key, limit)).fetchall()
        return [row['id'] for row in rows]

    def reports_to_build(self, project, scope_key, retry_failed):
        statuses = ['pending', *(['failed'] if retry_failed else [])]
        rows = self.repo._db.execute(f"""
            SELECT id, title, content::text FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND report->>'status'=ANY(?::text[]) ORDER BY {_ORDER}
            """, (project, scope_key, statuses)).fetchall()
        return [(row['id'], row['title'], json.loads(row['content'])) for row in rows]

    def reusable_reports(self, project, scope_key):
        rows = self.repo._db.execute("""
            SELECT id, content->>'digest' AS digest, report::text FROM knowledge_communities
            WHERE project_id=? AND scope_key=? AND report->>'status' IN ('ready','failed')
            """, (project, scope_key)).fetchall()
        return {(row['id'], row['digest']): json.loads(row['report']) for row in rows}

    def write_report(self, project, scope_key, community_id, report):
        self.repo._db.execute("""
            UPDATE knowledge_communities SET report=?::jsonb WHERE project_id=? AND scope_key=? AND id=?
            """, (_json(report), project, scope_key, community_id))
