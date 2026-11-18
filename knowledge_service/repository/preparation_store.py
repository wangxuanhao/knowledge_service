"""PostgreSQL preparation drafts, immutable bytes and fenced parse leases."""
from contextlib import contextmanager
import json
from time import monotonic
from uuid import uuid4


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,default=str)


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


class PreparationStore:
    # 过期租约回收是幂等的全局扫描；分发线程每 0.25s 认领一次，没必要每轮
    # 都扫两遍任务表。1 秒一次即可把恢复延迟控制在无感范围内。
    RECOVERY_INTERVAL_SECONDS = 1.0

    def __init__(self,repo):
        self.repo=repo
        self.db=repo._db
        self._last_recovery=0.0

    @contextmanager
    def guard(self,project):
        with self.repo._transaction():
            self.db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('preparation:'+project,))
            yield

    def create(self,project,owner,defaults):
        identifier=uuid4().hex
        with self.guard(project):
            self.db.execute('INSERT INTO preparation_sessions(id,project_id,owner_id,payload) VALUES (?,?,?,?::jsonb)',
                (identifier,project,owner,encoded(dict(id=identifier,project_id=project,defaults=defaults,items=[],settings_version=0,operations={}))))
        return self.get(project,identifier,owner)

    def get(self,project,identifier,owner):
        with self.repo._transaction():
            row=self.db.execute('SELECT payload,expires_at FROM preparation_sessions WHERE id=? AND project_id=? AND owner_id=? AND expires_at>now()',
                (identifier,project,owner)).fetchone()
            if row is None:raise KeyError('preparation not found or expired')
            value=decoded(row['payload'])
            value['expires_at']=str(row['expires_at'])
            return value

    def list(self,project,owner):
        with self.repo._transaction():
            rows=self.db.execute('SELECT id FROM preparation_sessions WHERE project_id=? AND owner_id=? AND expires_at>now() ORDER BY created_at DESC LIMIT 100',(project,owner)).fetchall()
            return [self.get(project,row['id'],owner) for row in rows]

    def save(self,value):
        self.db.execute('UPDATE preparation_sessions SET payload=?::jsonb WHERE id=?',(encoded(value),value['id']))

    def put_file(self,identifier,session,data):
        self.db.execute('INSERT INTO preparation_files(id,session_id,content) VALUES (?,?,?)',(identifier,session,data))

    def file(self,identifier):
        with self.repo._transaction():
            row=self.db.execute('SELECT content FROM preparation_files WHERE id=?',(identifier,)).fetchone()
            if row is None:raise KeyError('staged bytes expired')
            return bytes(row['content'])

    def task(self,identifier):
        with self.repo._transaction():
            row=self.db.execute('SELECT * FROM preparation_tasks WHERE id=?',(identifier,)).fetchone()
            if row is None:return None
            value=dict(row)
            for name in ('options','result'):
                value[name]=decoded(value.get(name))
            return value

    def find_task(self,project,fingerprint):
        with self.repo._transaction():
            row=self.db.execute('SELECT id FROM preparation_tasks WHERE project_id=? AND fingerprint=?',(project,fingerprint)).fetchone()
            return self.task(row['id']) if row else None

    def existing_documents(self,project,digest):
        with self.repo._transaction():
            rows=self.db.execute("""SELECT v.document_id,v.id AS source_version_id,v.original_name,
                    r.payload,i.options_json
                FROM source_document_versions v JOIN source_objects o ON o.id=v.source_object_id
                LEFT JOIN record_versions r ON r.project_id=v.project_id AND r.id=v.document_id AND r.superseded_at IS NULL
                LEFT JOIN ingest_batch_items i ON i.project_id=v.project_id AND i.source_version_id=v.id
                WHERE v.project_id=? AND o.sha256=? AND (
                    (r.id IS NOT NULL AND r.payload::jsonb->>'kind'='document'
                        AND COALESCE((r.payload::jsonb->'metadata'->>'_deleted')::boolean,false)=false
                        AND r.payload::jsonb->'metadata'->>'source_document_version_id'=v.id)
                    OR (r.id IS NULL AND i.id IS NOT NULL AND i.status<>'cancelled'))
                ORDER BY v.created_at DESC,v.id""",(project,digest)).fetchall()
            result={}
            for row in rows:
                payload=decoded(row['payload']) or {}
                config=decoded(row['options_json']) or {}
                result.setdefault(row['document_id'],dict(id=row['document_id'],source_version_id=row['source_version_id'],
                    title=payload.get('metadata',{}).get('title') or config.get('title') or row['original_name']))
            # Legacy/API uploads have formal document identities immediately,
            # while their frozen source version is created only by the worker.
            pending=self.db.execute("""SELECT i.document_id,i.source_version_id,i.original_name,i.options_json,r.payload
                FROM ingest_batch_items i JOIN ingest_batches b ON b.id=i.batch_id AND b.project_id=i.project_id
                LEFT JOIN record_versions r ON r.project_id=i.project_id AND r.id=i.document_id AND r.superseded_at IS NULL
                WHERE i.project_id=? AND i.file_sha256=? AND i.source_version_id IS NULL
                    AND i.status<>'cancelled' AND b.status<>'cancelled'
                    AND (r.id IS NULL OR r.payload::jsonb->'metadata'->'_deleted' IS DISTINCT FROM 'true'::jsonb)
                ORDER BY i.created_at DESC,i.id""",(project,digest)).fetchall()
            for row in pending:
                payload=decoded(row['payload']) or {}
                config=decoded(row['options_json']) or {}
                result.setdefault(row['document_id'],dict(id=row['document_id'],source_version_id=row['source_version_id'],
                    title=payload.get('metadata',{}).get('title') or config.get('title') or row['original_name']))
            return list(result.values())

    def attach(self,project,item,retry=False):
        row=self.db.execute('SELECT id FROM preparation_tasks WHERE project_id=? AND fingerprint=?',(project,item['parser_fingerprint'])).fetchone()
        if row:
            task=self.task(row['id'])
            if task['status'] in ('failed','cancelled') or (retry and task['status']=='completed'):
                self.db.execute("UPDATE preparation_tasks SET status='queued',stage='queued',error=NULL,result=NULL,lease_token=NULL,lease_until=NULL,started_at=NULL,heartbeat_at=NULL,file_id=?,original_name=?,options=?::jsonb WHERE id=?",(item['id'],item['original_name'],encoded(item['options']),task['id']))
                task=self.task(task['id'])
            elif task['status']=='cancelling':
                raise ValueError('已有解析任务正在安全取消，请稍后重试')
        else:
            identifier=uuid4().hex
            self.db.execute('INSERT INTO preparation_tasks(id,project_id,fingerprint,file_id,original_name,options) VALUES (?,?,?,?,?,?::jsonb)',
                (identifier,project,item['parser_fingerprint'],item['id'],item['original_name'],encoded(item['options'])))
            task=self.task(identifier)
        item['task_id']=task['id']
        item['status']='reused' if task['status']=='completed' else task['status']
        return task

    def detach(self,project,item):
        task_id=item.pop('task_id',None)
        if not task_id:return
        item['cancelled_task_id']=task_id
        # Caller has saved its updated draft first. Shared task runs while any live draft references it.
        rows=self.db.execute('SELECT payload FROM preparation_sessions WHERE project_id=? AND expires_at>now()',(project,)).fetchall()
        referenced=any(i.get('task_id')==task_id and i.get('status') not in ('cancelled','stale')
            for row in rows for i in decoded(row['payload'])['items'])
        if not referenced:
            self.db.execute("UPDATE preparation_tasks SET status=CASE WHEN status='running' THEN 'cancelling' ELSE 'cancelled' END,stage=CASE WHEN status='running' THEN 'cancelling' ELSE 'cancelled' END WHERE id=? AND status IN ('queued','running')",(task_id,))

    def acknowledge_cancel(self,task):
        with self.repo._transaction():
            self.db.execute("UPDATE preparation_tasks SET status='cancelled',stage='cancelled',lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND status='cancelling'",(task['id'],task['lease_token']))

    def claim(self,lease_seconds=120):
        with self.repo._transaction():
            # Expired workers cannot publish late results after this token changes.
            now=monotonic()
            if now-self._last_recovery>=self.RECOVERY_INTERVAL_SECONDS:
                self.db.execute("UPDATE preparation_tasks SET status='queued',stage='recovered',lease_token=NULL WHERE status='running' AND lease_until<now()")
                self.db.execute("UPDATE preparation_tasks SET status='cancelled',stage='cancelled',lease_token=NULL WHERE status='cancelling' AND lease_until<now()")
                self._last_recovery=now
            row=self.db.execute("SELECT id FROM preparation_tasks WHERE status='queued' AND file_id IS NOT NULL ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row:return None
            token=uuid4().hex
            self.db.execute("UPDATE preparation_tasks SET status='running',stage='parsing',lease_token=?,lease_until=now()+(? * interval '1 second'),heartbeat_at=now(),started_at=COALESCE(started_at,now()) WHERE id=?",(token,lease_seconds,row['id']))
            return self.task(row['id'])

    def update(self,task,status,stage,result=None,error=None):
        with self.repo._transaction():
            return self.db.execute('UPDATE preparation_tasks SET status=?,stage=?,result=?::jsonb,error=?,lease_until=NULL WHERE id=? AND lease_token=? AND status=\'running\' AND lease_until>now()',
                (status,stage,encoded(result) if result is not None else None,error,task['id'],task['lease_token'])).rowcount>0

    def heartbeat(self,task,stage=None):
        with self.repo._transaction():
            return self.db.execute("UPDATE preparation_tasks SET heartbeat_at=now(),lease_until=now()+interval '120 seconds',stage=COALESCE(?,stage) WHERE id=? AND lease_token=? AND status='running' AND lease_until>now()",(stage,task['id'],task['lease_token'])).rowcount>0

    def interrupt(self,task):
        with self.repo._transaction():
            self.db.execute("UPDATE preparation_tasks SET status='queued',stage='interrupted',lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND status='running'",(task['id'],task['lease_token']))

    def cleanup(self):
        with self.repo._transaction():
            self.db.execute("UPDATE preparation_tasks SET status=CASE WHEN status='running' THEN 'cancelling' ELSE 'cancelled' END WHERE status IN ('queued','running') AND NOT EXISTS (SELECT 1 FROM preparation_sessions s,jsonb_array_elements(s.payload->'items') i WHERE s.expires_at>now() AND i->>'task_id'=preparation_tasks.id AND i->>'status' NOT IN ('cancelled','stale'))")
            self.db.execute("""UPDATE preparation_tasks t SET file_id=(
                SELECT f.id FROM preparation_sessions s,jsonb_array_elements(s.payload->'items') i
                JOIN preparation_files f ON f.id=i->>'id'
                WHERE s.expires_at>now() AND i->>'task_id'=t.id
                    AND i->>'status' NOT IN ('cancelled','stale') LIMIT 1)
                WHERE t.status IN ('queued','running') AND NOT EXISTS (
                    SELECT 1 FROM preparation_files f JOIN preparation_sessions s ON s.id=f.session_id
                    WHERE f.id=t.file_id AND s.expires_at>now())""")
            self.db.execute('DELETE FROM preparation_sessions WHERE expires_at<now()')
            self.db.execute("DELETE FROM preparation_tasks WHERE status NOT IN ('queued','running','cancelling') AND NOT EXISTS (SELECT 1 FROM preparation_sessions s,jsonb_array_elements(s.payload->'items') i WHERE i->>'task_id'=preparation_tasks.id)")
            self.db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-object-gc',))
            removed=self.db.execute("""DELETE FROM source_document_versions v
                WHERE v.document_id LIKE 'preparation-%'
                AND v.parser_metadata::jsonb->>'preparation_task_id' IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM preparation_tasks t WHERE v.document_id='preparation-'||t.id
                    AND (t.status IN ('queued','running','cancelling') OR t.result->>'source_version_id'=v.id))
                AND NOT EXISTS (SELECT 1 FROM ingest_batch_items i WHERE i.project_id=v.project_id AND i.source_version_id=v.id)
                AND NOT EXISTS (SELECT 1 FROM chunk_source_spans s WHERE s.project_id=v.project_id AND s.document_version_id=v.id)
                AND NOT EXISTS (SELECT 1 FROM record_versions r WHERE r.project_id=v.project_id AND r.payload::jsonb->'metadata'->>'source_document_version_id'=v.id)
                """).rowcount
            return removed
