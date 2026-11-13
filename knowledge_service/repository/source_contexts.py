"""Bounded reads and resumable rebuild of correlated assertion support."""
import json

from ..core.time import normalize_time


class SourceContexts:
    def __init__(self, repository):
        self.repo = repository

    def current_source_contexts(self, project, record_ids, *, known_at=None):
        if not isinstance(record_ids, (list, tuple, set)) or len(record_ids) > 1000 or any(
                not isinstance(value, str) or not value for value in record_ids):
            raise ValueError('来源上下文读取需要最多 1000 个有效记录 ID')
        self.repo.get_project(project)
        ids = list(dict.fromkeys(record_ids))
        result = {id: [] for id in ids}
        if not ids:
            return result
        known = normalize_time(known_at)
        predicate = ('known_until IS NULL' if known is None else
                     'known_from<=? AND (known_until IS NULL OR known_until>?)')
        params = [project, ids, *([known, known] if known else [])]
        target_known=('target.superseded_at IS NULL' if known is None else
            'target.recorded_at<=? AND (target.superseded_at IS NULL OR target.superseded_at>?)')
        if known:params.extend([known,known])
        from .scoped_reads import _support_matches_record
        with self.repo._lock:
            rows = self.repo._db.execute('''SELECT support.* FROM source_context_support support
                JOIN record_versions target ON target.project_id=support.project_id AND target.id=support.record_id
                WHERE support.project_id=? AND support.record_id=ANY(?::text[]) AND support.invalid_reason IS NULL AND '''
                + predicate+' AND '+target_known+' AND '+_support_matches_record('target')+
                ' ORDER BY support.record_id,support.id LIMIT 10001', params).fetchall()
        if len(rows) > 10000:
            raise ValueError('来源上下文超过单批 10000 条预算，请缩小记录批次并检查高支撑实体')
        for row in rows:
            result[row['record_id']].append({
                'id': row['id'], 'version': row['decision_version'],
                'assertion_id': row['assertion_id'],
                'document_id': row['document_id'], 'document_version_id': row['document_version_id'],
                'record_version_id': row['record_version_id'],
                'known_from': row['known_from'], 'known_until': row['known_until'],
                'valid_from': row['valid_from'], 'valid_until': row['valid_until'],
                'metadata': {'applicability': json.loads(row['applicability'])},
            })
        return result

    def diagnostics(self, project):
        """Separate historical repair counts from current accepted-state blockers."""
        with self.repo._lock:
            counts = self.repo._db.execute('''SELECT count(*) support_count,
                count(*) FILTER(WHERE invalid_reason IS NOT NULL) invalid,
                count(*) FILTER(WHERE known_until IS NULL AND invalid_reason IS NOT NULL) invalid_active,
                count(*) FILTER(WHERE known_until IS NULL AND invalid_reason IS NULL) active
                FROM source_context_support WHERE project_id=?''', (project,)).fetchone()
            missing = self.repo._db.execute('''SELECT count(*) FROM assertion_events e
                JOIN assertions a ON a.project_id=e.project_id AND a.id=e.assertion_id
                WHERE e.project_id=? AND e.to_status='accepted'
                  AND a.kind IN ('entity','relation','attribute')
                  AND NOT EXISTS(SELECT 1 FROM source_context_support s
                      WHERE s.project_id=e.project_id AND s.id=e.id)''', (project,)).fetchone()[0]
            missing_active = self.repo._db.execute('''SELECT count(*) FROM assertions a
                WHERE a.project_id=? AND a.status='accepted'
                  AND a.kind IN ('entity','relation','attribute')
                  AND NOT EXISTS(SELECT 1 FROM source_context_support s
                    WHERE s.project_id=a.project_id AND s.assertion_id=a.id
                      AND s.record_id=a.canonical_record_id AND s.decision_version=a.decision_version
                      AND s.known_until IS NULL)''', (project,)).fetchone()[0]
        return {**dict(counts), 'missing': missing, 'missing_active': missing_active}

    def backfill(self, project, *, batch_size=500, after=None, max_batches=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('来源上下文回填批次必须为 1 到 1000')
        if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
            raise ValueError('来源上下文回填批次数必须为正整数')
        self.repo.get_project(project)
        cursor, processed, batches, complete = after or '', 0, 0, False
        while max_batches is None or batches < max_batches:
            with self.repo._transaction():
                rows = self.repo._db.execute('''SELECT id FROM assertions
                    WHERE project_id=? AND id>? AND kind IN ('entity','relation','attribute')
                    ORDER BY id LIMIT ?''', (project, cursor, batch_size)).fetchall()
                for row in rows:
                    self.repo._db.execute('SELECT refresh_identity_support(?,?)', (project, row['id']))
            batches += 1
            processed += len(rows)
            if len(rows) < batch_size:
                complete = True
                break
            cursor = rows[-1]['id']
        return {**self.diagnostics(project), 'processed': processed, 'complete': complete,
                'after': None if complete else cursor}
