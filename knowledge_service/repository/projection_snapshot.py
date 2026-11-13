"""Repeatable bounded reads of the immutable versions captured for one build."""
from __future__ import annotations

from .projection_inputs import ProjectionInputs
from ..domain.search_projection import ProjectionInput
from ..domain.search_filter_catalog import parse_filter_json


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
                captured = reader._db.execute('''SELECT version_id,projection_input
                    FROM projection_rebuild_snapshots WHERE project_id=? AND target=?
                    AND build_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?''',
                    (self.project_id, self.target, self.build_id, ordinal, self.batch_size)).fetchall()
                if not captured:
                    return
                values=[]
                for row in captured:
                    if row['projection_input']:
                        body=parse_filter_json(row['projection_input'])
                        # Context decision versions are native integers, not business decimals.
                        for context in body.get('source_contexts') or []:context['version']=int(context['version'])
                        values.append(ProjectionInput(**body))
                    else:
                        legacy=ProjectionInputs(reader).snapshot_versions(self.project_id,[row['version_id']])
                        if not legacy:raise RuntimeError('captured projection version disappeared')
                        values.extend(legacy)
            ordinal += len(captured)
            yield from values
