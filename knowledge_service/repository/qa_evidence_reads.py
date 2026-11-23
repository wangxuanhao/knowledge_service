"""Batch reads for exact source occurrences used in QA validity checks."""
from .scoped_reads import FIELDS


_BATCH_SIZE = 256


def _batches(values):
    values = sorted(set(values))
    for start in range(0, len(values), _BATCH_SIZE):
        yield values[start:start + _BATCH_SIZE]


class QaEvidenceReads:
    def __init__(self, repository):
        self.repo = repository

    def mappings(self, project, versions, known_at):
        for batch in _batches(versions):
            rows = self.repo._db.execute("""
                SELECT record_version_id, record_id, assertion_id, assertion_event_id, created_at
                FROM record_version_assertions
                WHERE project_id=? AND record_version_id=ANY(?::text[]) AND created_at<=?
                ORDER BY record_version_id, assertion_id, assertion_event_id
            """, (project, batch, known_at)).fetchall()
            yield from rows

    def assertions_at(self, project, assertion_ids, known_at):
        for batch in _batches(assertion_ids):
            rows = self.repo._db.execute("""
                SELECT a.id, a.created_at, a.document_id, a.document_version_id, a.chunk_id,
                       decision.id AS event_id, decision.to_status, decision.canonical_record_id
                FROM assertions a
                LEFT JOIN LATERAL (
                    SELECT e.id, e.to_status, e.canonical_record_id
                    FROM assertion_events e
                    WHERE e.project_id=a.project_id AND e.assertion_id=a.id AND e.created_at<=?
                    ORDER BY e.decision_version DESC, e.created_at, e.seq
                    LIMIT 1
                ) decision ON TRUE
                WHERE a.project_id=? AND a.id=ANY(?::text[])
            """, (known_at, project, batch)).fetchall()
            yield from rows

    def source_chunks(self, project, sources, known_at):
        fields = ', '.join('r.' + field for field in FIELDS.split(','))
        for batch in _batches(sources):
            chunk_ids, document_ids, document_versions = map(list, zip(*batch))
            rows = self.repo._db.execute(f"""
                SELECT {fields}, keys.document_id, keys.document_version_id
                FROM unnest(?::text[], ?::text[], ?::text[])
                    AS keys(chunk_id, document_id, document_version_id)
                JOIN record_versions r ON r.project_id=? AND r.id=keys.chunk_id
                    AND r.recorded_at<=? AND r.read_kind='chunk'
                    AND r.payload::jsonb->>'source_id'=keys.document_id
                    AND r.payload::jsonb#>>'{{metadata,source_version_id}}'=keys.document_version_id
                ORDER BY r.id, r.version
            """, (chunk_ids, document_ids, document_versions, project, known_at)).fetchall()
            for row in rows:
                yield (row['id'], row['document_id'], row['document_version_id']), self.repo._record(row)
