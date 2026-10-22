"""Repeatable bounded reads of the immutable versions captured for one build."""
from __future__ import annotations

from .projection_inputs import ProjectionInputs


class CapturedProjectionInputs:
    def __init__(self, repository, claimed, *, batch_size=64):
        self.repository = repository
        self.project_id, self.target, self.build_id = claimed.project_id, claimed.target, claimed.build_id
        self.batch_size = batch_size

    def __iter__(self):
        ordinal = 0
        while True:
            # Captured version IDs and their bodies are immutable. Close each
            # read transaction before the caller encodes a potentially slow batch.
            with self.repository.independent_read_view() as reader:
                ids = [row['version_id'] for row in reader._db.execute('''SELECT version_id
                    FROM projection_rebuild_snapshots WHERE project_id=? AND target=?
                    AND build_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?''',
                    (self.project_id, self.target, self.build_id, ordinal, self.batch_size)).fetchall()]
                if not ids:
                    return
                values = ProjectionInputs(reader).snapshot_versions(self.project_id, ids)
                if len(values) != len(ids):
                    raise RuntimeError('captured projection version disappeared')
            ordinal += len(ids)
            yield from values
