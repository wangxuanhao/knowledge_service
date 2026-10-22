"""Compact authority snapshots and bounded payload reads for projection audits."""


INDEXED_KINDS = frozenset({'entity', 'relation', 'chunk'})


class ProjectionAuthorityReads:
    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def _project(reader, project):
        if reader.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone() is None:
            raise KeyError(project)

    def manifest(self, project, kinds):
        if not isinstance(kinds, (set, frozenset, list, tuple)) or not set(kinds) <= INDEXED_KINDS:
            raise ValueError('Invalid projection authority kinds')
        with self.repo._db.independent_read_snapshot() as reader:
            self._project(reader, project)
            row = reader.execute(
                'SELECT projection_seq FROM project_projection_sequences WHERE project_id=?',
                (project,)).fetchone()
            sequence = 0 if row is None else int(row['projection_seq'])
            # read_visible proves _deleted is falsy on the usual path without
            # decoding payload. Audit-marked records still belong to projection
            # authority when _deleted is falsy, preserving the previous contract.
            rows = reader.execute('''
                SELECT id,version_id FROM record_versions
                WHERE project_id=? AND superseded_at IS NULL
                  AND read_kind=ANY(?::text[])
                  AND (read_visible OR
                    COALESCE(payload::jsonb#>'{metadata,_deleted}', 'null'::jsonb)
                    IN ('null'::jsonb,'false'::jsonb,'0'::jsonb,
                        '""'::jsonb,'[]'::jsonb,'{}'::jsonb))''',
                (project, sorted(set(kinds)))).fetchall()
        return sequence, {str(row['id']): str(row['version_id']) for row in rows}

    def records(self, project, version_ids):
        if (not isinstance(version_ids, (list, tuple)) or len(version_ids) > 256
                or any(not isinstance(value, str) or not value for value in version_ids)):
            raise ValueError('Authority repair batches require at most 256 version IDs')
        if not version_ids:
            return []
        with self.repo._db.independent_read_snapshot() as reader:
            self._project(reader, project)
            rows = reader.execute('''SELECT * FROM record_versions
                WHERE project_id=? AND superseded_at IS NULL
                  AND version_id=ANY(?::text[])''',
                (project, list(dict.fromkeys(version_ids)))).fetchall()
        by_id = {row['version_id']: self.repo._record(row) for row in rows}
        return [by_id[value] for value in version_ids if value in by_id]
