"""Authoritative attribute and source predicates for graph neighborhood search."""
from ..core.time import normalize_time


class GraphSearchEvidence:
    def __init__(self, reads):
        self.reads = reads
        self.repo = reads.repo

    def attributes(self, project, scope, entity_ids, values):
        if not entity_ids or not values:
            return {}
        qualified, params = self.reads._qualified(project, scope, kinds=['attribute'])
        literal = "r.payload::jsonb->>'value'"
        # Choose an evidence record per exact literal and owner without paging
        # through unrelated attributes or transferring their full payloads.
        with self.repo._lock:
            rows = self.repo._db.execute(f"""
                SELECT DISTINCT ON (r.read_subject_id, {literal})
                    r.read_subject_id AS owner_id, {literal} AS value, r.id, r.version_id
                FROM record_versions r
                WHERE {qualified}
                    AND r.read_subject_id=ANY(?::text[]) AND {literal}=ANY(?::text[])
                ORDER BY r.read_subject_id, {literal}, r.id, r.version
            """, [*params, list(entity_ids), list(values)]).fetchall()
        return {(row['owner_id'], row['value']): row for row in rows}

    def sources(self, project, scope, entities, steps):
        if not entities:
            return {}
        authority, authority_params = self.reads._base(project, {**scope, '_read_kinds': ['document']}, 'authority')
        accepted, accepted_params = self.reads._support_known(scope)
        applicable, applicability_params = self.reads._scope_predicates(scope, 'support')
        known_at = normalize_time(scope.get('known_at'))
        legacy_cutoff = ' AND origin.created_at<=?' if known_at else ''
        frozen_cutoff = ' AND source.recorded_at<=?' if known_at else ''
        published = "COALESCE(source.payload::jsonb#>>'{metadata,published_at}', source.payload::jsonb->>'published_at')"
        # Date-only values represent the stated date. A timestamp is evaluated
        # in Shanghai regardless of the database connection's timezone.
        local_date = f"""CASE WHEN {published} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                AND pg_input_is_valid({published}, 'timestamp with time zone')
            THEN CASE WHEN length({published})=10 THEN {published}::date
                ELSE ({published}::timestamptz AT TIME ZONE 'Asia/Shanghai')::date END END"""
        predicates, params = [], []
        for step in steps:
            value = step['value']
            if step['field'] == 'source_platform':
                predicates.append("?=ANY(source.platform_values)")
                params.append(value)
            elif step['field'] == 'source_published_date':
                predicates.append(f'({local_date})=?::date')
                params.append(value)
            else:
                predicate, values = self.reads._keyword_sql(value, 'source')
                predicates.append(predicate)
                params.extend(values)
        source_filters = ' AND ' + ' AND '.join(predicates) if predicates else ''
        ids = [row['id'] for row in entities]
        direct_ids = [row.get('source_id') for row in entities]
        # Every source condition must hold on a single accepted occurrence.
        # Use its frozen version, and independently require the document to
        # remain visible in the selected scope. Legacy-only source bindings
        # are allowed when no accepted occurrence has superseded that link.
        statement = f"""
            SELECT candidate.id AS entity_id, evidence.document_id, evidence.document_version_id,
                evidence.version, evidence.assertion_id, evidence.published_at, evidence.platforms,
                evidence.title, original.id AS source_version_id, anchor.chunk_version_id,
                CASE WHEN strpos(evidence.body, NULLIF(assertion.quote, ''))>0
                    THEN left(assertion.quote, 800) END AS quote,
                CASE WHEN original.id IS NULL THEN left(evidence.body, 800) END AS excerpt
            FROM unnest(?::text[], ?::text[]) AS candidate(id, direct_source_id)
            CROSS JOIN LATERAL (
                SELECT source.project_id, source.id AS document_id, source.version_id AS document_version_id,
                    source.version, source.payload::jsonb->>'text' AS body,
                    source.payload::jsonb#>>'{{metadata,source_document_version_id}}' AS artifact_id,
                    provenance.assertion_id, {published} AS published_at, source.platform_values AS platforms,
                    COALESCE(NULLIF(source.payload::jsonb#>>'{{metadata,title}}',''),
                             NULLIF(source.payload::jsonb#>>'{{metadata,source_file}}',''), source.id) AS title
                FROM (
                    SELECT support.document_id, support.document_version_id, support.assertion_id
                    FROM source_context_support support
                    WHERE support.project_id=? AND support.record_id=candidate.id
                        AND support.kind='entity' AND support.invalid_reason IS NULL
                        AND support.document_version_id IS NOT NULL
                        AND {accepted} AND {applicable}
                    UNION ALL
                    SELECT candidate.direct_source_id, NULL::text, NULL::text
                    WHERE candidate.direct_source_id IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM record_version_assertions origin
                            WHERE origin.project_id=? AND origin.record_id=candidate.id{legacy_cutoff})
                        AND NOT EXISTS (
                            SELECT 1 FROM assertion_events origin
                            WHERE origin.project_id=? AND origin.canonical_record_id=candidate.id
                                AND origin.to_status='accepted'{legacy_cutoff})
                ) provenance
                JOIN record_versions authority ON authority.id=provenance.document_id
                    AND authority.read_kind='document' AND {authority}
                JOIN record_versions source ON source.project_id=authority.project_id
                    AND source.id=authority.id
                    AND source.version_id=COALESCE(provenance.document_version_id, authority.version_id)
                    AND source.read_kind='document' AND source.read_visible AND NOT source.read_quarantined
                    {frozen_cutoff}
                WHERE TRUE{source_filters}
                ORDER BY source.recorded_at DESC, source.id, provenance.assertion_id
                LIMIT 1
            ) evidence
            LEFT JOIN source_document_versions original ON original.project_id=evidence.project_id
                AND original.document_id=evidence.document_id AND original.id=evidence.artifact_id
            LEFT JOIN assertions assertion ON assertion.project_id=evidence.project_id
                AND assertion.id=evidence.assertion_id AND assertion.document_id=evidence.document_id
                AND assertion.document_version_id=evidence.document_version_id
            LEFT JOIN LATERAL (
                SELECT min(chunk.version_id) AS chunk_version_id
                FROM record_versions chunk
                WHERE original.id IS NOT NULL AND chunk.project_id=evidence.project_id
                    AND chunk.id=assertion.chunk_id AND chunk.read_kind='chunk' AND NOT chunk.read_quarantined
                    AND chunk.payload::jsonb->>'source_id'=evidence.document_id
                    AND chunk.payload::jsonb#>>'{{metadata,source_version_id}}'=evidence.document_version_id
                    AND EXISTS (
                        SELECT 1 FROM chunk_source_spans span WHERE span.project_id=chunk.project_id
                            AND span.document_version_id=original.id AND span.chunk_version_id=chunk.version_id)
                HAVING count(*)=1
            ) anchor ON TRUE
        """
        legacy_params = [project, known_at, project, known_at] if known_at else [project, project]
        with self.repo._lock:
            rows = self.repo._db.execute(statement, [
                ids, direct_ids, project, *accepted_params, *applicability_params, *legacy_params,
                *authority_params, *([known_at] if known_at else []), *params,
            ]).fetchall()
        fields = ('document_id', 'document_version_id', 'version', 'assertion_id', 'published_at', 'platforms',
                  'title', 'source_version_id', 'chunk_version_id', 'quote', 'excerpt')
        return {row['entity_id']: {field: row[field] for field in fields} for row in rows}
