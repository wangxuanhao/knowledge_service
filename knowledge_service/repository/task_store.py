"""PostgreSQL-backed artifact task leases with fenced completion."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec='microseconds').replace('+00:00', 'Z')


class TaskStore:
    def __init__(self, repository):
        self.repo = repository

    def claim(self, kind, owner, *, lease_seconds=120, task_id=None):
        stamp = now()
        with self.repo.unit_of_work():
            # Claims are brief; serialize their eligibility check so two
            # consumers cannot lease different events for one project at once.
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',
                                  ('task-claim:shared-index',))
            row = self.repo._db.execute('''SELECT a.id,a.payload FROM artifacts a
                WHERE a.kind=? AND (?::text IS NULL OR a.id=?) AND (
                  (payload::jsonb->>'status' IN ('pending','queued','retry_wait')
                   AND COALESCE(payload::jsonb->>'next_attempt_at','')<=?) OR
                  (payload::jsonb->>'status'='running'
                   AND COALESCE(payload::jsonb->>'lease_until','')<=?))
                AND (a.kind<>'ontology_sync_job' OR NOT EXISTS (
                  SELECT 1 FROM artifacts b WHERE b.kind=a.kind AND b.id<>a.id
                  AND b.project_id=a.project_id
                  AND b.payload::jsonb->>'status'='running'
                  AND COALESCE(b.payload::jsonb->>'lease_until','')>?))
                AND NOT EXISTS (
                  SELECT 1 FROM artifacts b
                  WHERE (b.kind<>a.kind OR b.id<>a.id)
                  AND b.payload::jsonb->>'status'='running'
                  AND COALESCE(b.payload::jsonb->>'lease_until','')>?
                  AND (
                    (a.kind='job' AND a.payload::jsonb->>'kind'='semantic_index'
                     AND (b.kind='ontology_sync_job' OR
                          (b.kind='job' AND b.payload::jsonb->>'kind'='semantic_index')))
                    OR (a.kind='ontology_sync_job' AND b.kind='job'
                        AND b.payload::jsonb->>'kind'='semantic_index')))
                ORDER BY a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1''',
                (kind, task_id, task_id, stamp, stamp, stamp, stamp)).fetchone()
            if row is None:
                return None
            task = json.loads(row['payload'])
            task.update(status='running', owner=owner, lease_token=str(uuid4()),
                        lease_until=after(lease_seconds), updated_at=stamp,
                        attempts=int(task.get('attempts', 0)) + 1)
            self.repo.save_artifact(kind, task)
            return task

    def update(self, kind, task, updates):
        """Fence old/expired workers; cancellation also invalidates their lease."""
        with self.repo.unit_of_work():
            row = self.repo._db.execute(
                'SELECT payload FROM artifacts WHERE kind=? AND id=? FOR UPDATE',
                (kind, task['id'])).fetchone()
            if row is None:
                return None
            current = json.loads(row['payload'])
            if (current.get('status') != 'running'
                    or current.get('lease_token') != task.get('lease_token')
                    or current.get('lease_until', '') <= now()):
                return None
            current.update(updates, updated_at=now())
            self.repo.save_artifact(kind, current)
            return current

    def control(self, kind, task_id, action):
        with self.repo.unit_of_work():
            row = self.repo._db.execute(
                'SELECT payload FROM artifacts WHERE kind=? AND id=? FOR UPDATE',
                (kind, task_id)).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = json.loads(row['payload'])
            if action == 'cancel':
                if task['status'] == 'completed':
                    raise ValueError('已完成任务不能取消')
                task.update(status='cancelled', lease_token=None, lease_until=None)
            elif action in {'retry', 'resume'}:
                if task['status'] not in {'failed','interrupted','cancelled','disabled'}:
                    raise ValueError('只有已停止的任务可以重新排队')
                task.update(status='queued', lease_token=None, lease_until=None,
                            next_attempt_at=now(), error=None)
            else:
                raise ValueError('未知任务操作')
            task['updated_at'] = now()
            self.repo.save_artifact(kind, task)
            return task
