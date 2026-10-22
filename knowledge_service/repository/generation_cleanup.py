"""Leased retirement tombstones for project/generation-scoped Milvus deletion."""
from __future__ import annotations

import json
from uuid import uuid4

from .projection_receipts import ProjectionReceipts
from ..utils.diagnostics import redact


class GenerationCleanupStore:
    RESCAN_SECONDS = 3600

    def __init__(self, projections):
        self.projections = projections
        self.repository, self._db = projections.repository, projections._db

    def enqueue(self, project_id, index_version):
        ProjectionReceipts._scope(project_id, index_version)
        if not self._db.in_transaction:
            raise RuntimeError('generation retirement requires the generation transaction')
        self._db.execute('''INSERT INTO milvus_generation_cleanup_outbox(project_id,index_version)
            VALUES (?,?) ON CONFLICT(project_id,index_version) DO NOTHING''',
            (project_id, index_version))

    def _retired(self, project_id, index_version):
        row = self._db.execute("SELECT rebuild_state FROM projection_cursors "
            "WHERE project_id=? AND target='milvus'", (project_id,)).fetchone()
        if row is None:
            # A deleted project's existing retirement marker remains actionable.
            return not self._db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone()
        state = row['rebuild_state']
        state = json.loads(state) if isinstance(state, str) else state
        current = (state or {}).get('index_version')
        # Generations are monotone and never reactivate. Protect current,
        # resumable builds and unrecognized future generations alike.
        return type(current) is int and index_version < current

    def claim(self, owner, *, limit=10, lease_seconds=60):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('cleanup owner is required')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('cleanup limit must be 1..100')
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('cleanup lease must be 1..3600 seconds')
        claimed = []
        with self.repository._transaction():
            rows = self._db.execute('''SELECT * FROM milvus_generation_cleanup_outbox
                WHERE not_before<=clock_timestamp()
                  AND (claim_until IS NULL OR claim_until<=clock_timestamp())
                ORDER BY not_before,project_id,index_version LIMIT ? FOR UPDATE SKIP LOCKED''',
                (limit,)).fetchall()
            for row in rows:
                if not self._retired(row['project_id'], row['index_version']):
                    continue
                token = str(uuid4())
                updated = self._db.execute('''UPDATE milvus_generation_cleanup_outbox
                    SET claim_owner=?,claim_token=?,claim_until=clock_timestamp()+(?*interval '1 second'),
                        attempts=attempts+1 WHERE project_id=? AND index_version=?
                    RETURNING *''', (owner, token, lease_seconds, row['project_id'], row['index_version'])).fetchone()
                claimed.append(dict(updated))
        return claimed

    def ack(self, event):
        with self.repository._transaction():
            if not self._retired(event['project_id'], event['index_version']):
                return False
            updated = self._db.execute('''UPDATE milvus_generation_cleanup_outbox
                SET claim_owner=NULL,claim_token=NULL,claim_until=NULL,cleaned_at=clock_timestamp(),
                    last_error=NULL,not_before=clock_timestamp()+(?*interval '1 second')
                WHERE project_id=? AND index_version=? AND claim_owner=? AND claim_token=?
                  AND claim_until>clock_timestamp()''',
                (self.RESCAN_SECONDS, event['project_id'], event['index_version'],
                 event['claim_owner'], event['claim_token']))
            if updated.rowcount != 1:
                return False
            self._db.execute('DELETE FROM milvus_projection_receipts '
                'WHERE project_id=? AND index_version=?', (event['project_id'], event['index_version']))
            return True

    def release(self, event, error):
        retry_seconds = min(300, 2 ** min(int(event['attempts']), 8))
        with self.repository._transaction():
            result = self._db.execute('''UPDATE milvus_generation_cleanup_outbox
                SET claim_owner=NULL,claim_token=NULL,claim_until=NULL,last_error=?,
                    not_before=clock_timestamp()+(?*interval '1 second')
                WHERE project_id=? AND index_version=? AND claim_owner=? AND claim_token=?
                  AND claim_until>clock_timestamp()''',
                (redact(str(error)), retry_seconds, event['project_id'], event['index_version'],
                 event['claim_owner'], event['claim_token']))
            return result.rowcount == 1

    def renew(self, event, *, lease_seconds=60):
        with self.repository._transaction():
            result = self._db.execute('''UPDATE milvus_generation_cleanup_outbox
                SET claim_until=clock_timestamp()+(?*interval '1 second')
                WHERE project_id=? AND index_version=? AND claim_owner=? AND claim_token=?
                  AND claim_until>clock_timestamp()''',
                (lease_seconds, event['project_id'], event['index_version'],
                 event['claim_owner'], event['claim_token']))
            return result.rowcount == 1

    def drain(self, owner, delete_versions, *, limit=10):
        events = self.claim(owner, limit=limit)
        cleaned = failed = 0
        for event in events:
            try:
                if not self.renew(event):
                    raise RuntimeError('generation cleanup lease was lost before deletion')
                with self.repository._lock:
                    retired = self._retired(event['project_id'], event['index_version'])
                if not retired:
                    raise RuntimeError('generation is still protected')
                delete_versions(event['project_id'], [event['index_version']])
                if self.ack(event):
                    cleaned += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                self.release(event, exc)
        return {'claimed': len(events), 'cleaned': cleaned, 'failed': failed}
