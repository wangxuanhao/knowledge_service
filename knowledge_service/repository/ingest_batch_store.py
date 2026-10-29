"""Transactional batch control and fenced document leases."""
from contextlib import contextmanager
import hashlib
import json
from uuid import uuid4


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),allow_nan=False)


def _row(row):
    if row is None:
        return None
    value = dict(row)
    for name in ('settings_json', 'options_json', 'result'):
        if isinstance(value.get(name), str):
            value[name] = json.loads(value[name])
    if 'settings_json' in value:
        value['settings'] = value['settings_json']
    if 'options_json' in value:
        value['options'] = value['options_json']
    if value.get('content') is not None:
        value['content'] = bytes(value['content'])
    return value


class IngestBatchStore:
    def __init__(self, repository):
        self.repo = repository
        self.db = repository._db

    def create_batch(self, project_id, settings, created_by=None):
        settings = settings.model_dump() if hasattr(settings, 'model_dump') else dict(settings)
        encoded = _json(settings)
        batch_id = uuid4().hex
        with self.repo._transaction():
            self.db.execute('''INSERT INTO ingest_batches
                (id,project_id,settings_json,settings_fingerprint,requested_concurrency,created_by)
                VALUES (?,?,?::jsonb,?,?,?)''', (batch_id, project_id, encoded,
                hashlib.sha256(encoded.encode()).hexdigest(), str(settings.get('requested_concurrency', 'auto')), created_by))
        return self.get_batch(project_id, batch_id)

    def get_batch(self, project_id, batch_id):
        with self.repo._transaction():
            batch = _row(self.db.execute('SELECT * FROM ingest_batches WHERE project_id=? AND id=?', (project_id, batch_id)).fetchone())
            if batch is None:
                return None
            counts = {status: 0 for status in ('pending','running','retry_wait','succeeded','failed','cancelled','interrupted')}
            for row in self.db.execute('SELECT status,COUNT(*) AS count FROM ingest_batch_items WHERE batch_id=? GROUP BY status', (batch_id,)):
                counts[row['status']] = row['count']
            totals = self.db.execute('SELECT COUNT(*) AS total,COALESCE(SUM(size_bytes),0) AS total_bytes FROM ingest_batch_items WHERE batch_id=?', (batch_id,)).fetchone()
            batch.update(counts=counts, total=totals['total'], total_bytes=totals['total_bytes'])
            return batch

    def list_batches(self, project_id, limit=50, offset=0):
        limit, offset = self._page(limit, offset)
        with self.repo._transaction():
            total = self.db.execute('SELECT COUNT(*) FROM ingest_batches WHERE project_id=?', (project_id,)).fetchone()[0]
            ids = [row[0] for row in self.db.execute('SELECT id FROM ingest_batches WHERE project_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (project_id,limit,offset))]
            return {'items': [self.get_batch(project_id, value) for value in ids], 'total': total, 'limit': limit, 'offset': offset}

    @staticmethod
    def _page(limit, offset):
        if int(limit) < 1 or int(offset) < 0:
            raise ValueError('invalid pagination')
        return min(int(limit),1000), int(offset)

    def add_item(self, project_id, batch_id, filename, content, content_hash, idempotency_key, max_items, max_bytes, options=None, accounted_size=None, upload_object_id=None):
        content = bytes(content) if content is not None else None
        if content is None and upload_object_id is None:
            raise ValueError('Upload content or object reference required')
        size=(len(content) if content is not None else 0) if accounted_size is None else int(accounted_size)
        if size<0:raise ValueError('invalid accounted size')
        options = dict(options or {})
        with self.repo._transaction():
            if upload_object_id is not None:
                self.db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('source-object-gc',))
                obj = self.db.execute('SELECT * FROM source_objects WHERE id=?', (upload_object_id,)).fetchone()
                if obj is None or obj['sha256'] != content_hash:
                    raise ValueError('Upload object does not match content checksum')
                if accounted_size is None:
                    size = obj['size_bytes']
            batch = self.db.execute('SELECT * FROM ingest_batches WHERE project_id=? AND id=? FOR UPDATE', (project_id,batch_id)).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            old = self.db.execute('SELECT * FROM ingest_batch_items WHERE batch_id=? AND idempotency_key=?', (batch_id,idempotency_key)).fetchone()
            if old:
                same_content = (bytes(old['content']) == content if old['content'] is not None and content is not None
                    else old['upload_object_id'] == upload_object_id if old['upload_object_id'] and upload_object_id
                    else hashlib.sha256(content).hexdigest() == old['file_sha256'] if content is not None
                    else hashlib.sha256(bytes(old['content'])).hexdigest() == content_hash
                        or options.get('metadata',{}).get('package_sha256') == content_hash)
                if old['file_sha256'] != content_hash or old['original_name'] != filename or not same_content or _json(_row(old)['options']) != _json(options):
                    raise ValueError('idempotency key conflicts with existing file')
                return _row(old)
            if batch['status'] != 'draft':
                raise ValueError('batch accepts files only while draft')
            totals = self.db.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM ingest_batch_items WHERE batch_id=?', (batch_id,)).fetchone()
            if totals[0] >= max_items or totals[1]+size > max_bytes:
                raise ValueError('batch item or byte limit exceeded')
            return _row(self.db.execute('''INSERT INTO ingest_batch_items
                (id,batch_id,project_id,document_id,original_name,content,size_bytes,file_sha256,idempotency_key,options_json,upload_object_id)
                VALUES (?,?,?,?,?,?,?,?,?,?::jsonb,?) RETURNING *''', (uuid4().hex,batch_id,project_id,uuid4().hex,filename,content,size,content_hash,idempotency_key,_json(options),upload_object_id)).fetchone())

    def set_source(self, project_id, batch_id, item_id, version_id):
        with self.repo._transaction():
            self.db.execute('UPDATE ingest_batch_items SET source_version_id=? WHERE id=? AND batch_id=? AND project_id=?',(version_id,item_id,batch_id,project_id))

    def list_items(self, project_id, batch_id, limit=50, offset=0, status=None):
        limit,offset = self._page(limit,offset)
        with self.repo._transaction():
            if self.get_batch(project_id,batch_id) is None:
                raise KeyError(batch_id)
            where='batch_id=?'; values=[batch_id]
            if status:
                where+=' AND status=?';values.append(status)
            total = self.db.execute('SELECT COUNT(*) FROM ingest_batch_items WHERE '+where, values).fetchone()[0]
            rows = self.db.execute('''SELECT id,batch_id,project_id,document_id,source_document_id,source_version_id,source_restart_of,document_generation,original_name,size_bytes,file_sha256,
                status,stage,progress,attempt,auto_retry_count,next_retry_at,error_code,error_message,public_error,
                failed_chunk_ordinal,result,options_json,created_at,updated_at,completed_at FROM ingest_batch_items
                WHERE '''+where+' ORDER BY created_at,id LIMIT ? OFFSET ?', (*values,limit,offset))
            return {'items': [_row(row) for row in rows], 'total':total,'limit':limit,'offset':offset}

    def transition(self, project_id, batch_id, action):
        targets = {'start':'running','resume':'running','pause':'paused','cancel':'cancelled'}
        if action not in targets:
            raise ValueError('unknown batch action')
        with self.repo._transaction():
            batch = self.db.execute('SELECT * FROM ingest_batches WHERE project_id=? AND id=? FOR UPDATE', (project_id,batch_id)).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            allowed = {'start':('draft','running'), 'resume':('paused','running'), 'pause':('running','paused'), 'cancel':('draft','running','paused','cancelled')}
            if batch['status'] not in allowed[action]:
                raise ValueError('invalid batch transition')
            self.db.execute('UPDATE ingest_batches SET status=?,updated_at=clock_timestamp(),started_at=CASE WHEN ?=\'running\' THEN COALESCE(started_at,clock_timestamp()) ELSE started_at END WHERE id=?', (targets[action],targets[action],batch_id))
            if action in ('pause','cancel'):
                self.db.execute('''UPDATE ingest_item_attempts SET status=?,completed_at=clock_timestamp()
                    WHERE status='running' AND item_id IN (SELECT id FROM ingest_batch_items WHERE batch_id=?)''', ('paused' if action=='pause' else 'cancelled',batch_id))
                self.db.execute('''UPDATE ingest_batch_items SET status=?,lease_owner=CASE WHEN ?='pause' THEN lease_owner ELSE NULL END,
                    lease_token=CASE WHEN ?='pause' THEN lease_token ELSE NULL END,
                    lease_until=CASE WHEN ?='pause' THEN lease_until ELSE NULL END,
                    updated_at=clock_timestamp() WHERE batch_id=? AND
                    (status='running' OR (?='cancel' AND status IN ('pending','retry_wait')))''', ('cancelled' if action=='cancel' else 'pending',action,action,action,batch_id,action))
        return self.get_batch(project_id,batch_id)

    def claim(self, batch_id, owner, lease_seconds):
        if not owner or lease_seconds <= 0:
            raise ValueError('lease owner and positive duration required')
        with self.repo._transaction():
            batch = self.db.execute('SELECT * FROM ingest_batches WHERE id=? FOR UPDATE', (batch_id,)).fetchone()
            if batch is None or batch['status'] != 'running':
                return None
            running = self.db.execute("SELECT COUNT(*) FROM ingest_batch_items WHERE batch_id=? AND (status='running' OR (status='pending' AND lease_until>clock_timestamp() AND lease_owner IS NOT NULL))", (batch_id,)).fetchone()[0]
            if running >= batch['effective_concurrency']:
                return None
            item = self.db.execute('''SELECT * FROM ingest_batch_items WHERE batch_id=?
                AND ((status IN ('pending','interrupted') AND (lease_until IS NULL OR lease_until<=clock_timestamp()))
                  OR (status='retry_wait' AND next_retry_at<=clock_timestamp()))
                ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED''', (batch_id,)).fetchone()
            if item is None:
                return None
            token = uuid4().hex
            row = self.db.execute('''UPDATE ingest_batch_items SET status='running',attempt=attempt+1,
                lease_owner=?,lease_token=?,lease_until=clock_timestamp()+(? * interval '1 second'),
                updated_at=clock_timestamp() WHERE id=? RETURNING *''', (owner,token,lease_seconds,item['id'])).fetchone()
            self.db.execute('INSERT INTO ingest_item_attempts(id,item_id,attempt,lease_owner,lease_token) VALUES (?,?,?,?,?)', (uuid4().hex,row['id'],row['attempt'],owner,token))
            return _row(row)

    def running_batches(self, limit=1000):
        """Return dispatcher control rows without loading document content."""
        limit, _ = self._page(limit, 0)
        with self.repo._transaction():
            return [_row(row) for row in self.db.execute("SELECT * FROM ingest_batches WHERE status='running' ORDER BY created_at,id LIMIT ?", (limit,))]

    def update_progress(self, item_id, owner, token, **fields):
        allowed={'stage','progress','source_document_id','source_version_id'}
        if set(fields)-allowed:
            raise ValueError('unknown progress fields')
        with self.commit_guard(item_id,owner,token):
            sets=','.join(key+'=?' for key in fields)
            self.db.execute('UPDATE ingest_batch_items SET '+sets+',updated_at=clock_timestamp() WHERE id=?',(*fields.values(),item_id))

    def recent_outcomes(self, batch_id, limit=20):
        with self.repo._lock:
            rows=self.db.execute('''SELECT error_code FROM ingest_item_attempts WHERE item_id IN
                (SELECT id FROM ingest_batch_items WHERE batch_id=?) AND completed_at IS NOT NULL
                ORDER BY completed_at DESC LIMIT ?''',(batch_id,limit)).fetchall()
            return [row['error_code'] for row in rows]

    def pause_for_error(self, project_id, batch_id, code):
        with self.repo._transaction():
            batch=self.get_batch(project_id,batch_id)
            if batch and batch['status']=='running':
                self.transition(project_id,batch_id,'pause')
                self.db.execute('UPDATE ingest_batches SET paused_reason=? WHERE id=?',(code,batch_id))

    def set_concurrency(self, batch_id, effective):
        effective = int(effective)
        if effective < 1:
            raise ValueError('positive concurrency required')
        with self.repo._transaction():
            row = self.db.execute('UPDATE ingest_batches SET effective_concurrency=?,updated_at=clock_timestamp() WHERE id=? RETURNING *', (effective,batch_id)).fetchone()
            if row is None:
                raise KeyError(batch_id)
            return _row(row)

    @contextmanager
    def checkpoint_guard(self, item_id, owner, token):
        """Paused in-flight calls may publish raw receipts, never formal knowledge."""
        with self.repo._transaction():
            row=self.db.execute('''SELECT i.id FROM ingest_batch_items i JOIN ingest_batches b ON b.id=i.batch_id
                WHERE i.id=? AND i.lease_owner=? AND i.lease_token=? AND i.lease_until>clock_timestamp()
                AND ((b.status='running' AND i.status='running') OR (b.status IN ('running','paused') AND i.status='pending'))
                FOR UPDATE OF b,i''',(item_id,owner,token)).fetchone()
            if row is None:raise ValueError('stale checkpoint lease')
            yield

    def release_paused(self, item_id, owner, token):
        with self.repo._transaction():
            self.db.execute('''UPDATE ingest_batch_items SET lease_owner=NULL,lease_token=NULL,lease_until=NULL
                WHERE id=? AND status='pending' AND lease_owner=? AND lease_token=?''',(item_id,owner,token))

    def interrupt_owner(self, owner):
        with self.repo._transaction():
            self.db.execute('''UPDATE ingest_item_attempts SET status='interrupted',completed_at=clock_timestamp()
                WHERE status='running' AND lease_owner=?''',(owner,))
            self.db.execute('''UPDATE ingest_batch_items SET status='interrupted',stage='interrupted',lease_owner=NULL,
                lease_token=NULL,lease_until=NULL WHERE status='running' AND lease_owner=?''',(owner,))

    @contextmanager
    def commit_guard(self, item_id, owner, token):
        with self.repo._transaction():
            batch = self.db.execute('''SELECT b.* FROM ingest_batches b JOIN ingest_batch_items i ON i.batch_id=b.id WHERE i.id=? FOR UPDATE OF b''', (item_id,)).fetchone()
            row = self.db.execute('SELECT * FROM ingest_batch_items WHERE id=? FOR UPDATE', (item_id,)).fetchone()
            valid = batch is not None and batch['status']=='running' and row is not None and self.lease_valid(item_id,owner,token)
            if not valid:
                raise ValueError('stale ingest lease')
            yield _row(row)
            # A long extraction/write may outlive its lease even while row locks
            # block another claimant. Roll the whole write back in that case.
            current = self.db.execute('SELECT status,lease_token FROM ingest_batch_items WHERE id=?', (item_id,)).fetchone()
            if current['status'] == 'running' and not self.lease_valid(item_id,owner,token):
                raise ValueError('ingest lease expired before commit')

    def lease_valid(self, item_id, owner, token):
        with self.repo._lock:
            return bool(self.db.execute('''SELECT 1 FROM ingest_batch_items i JOIN ingest_batches b ON b.id=i.batch_id
                WHERE i.id=? AND i.status='running' AND b.status='running' AND i.lease_owner=? AND i.lease_token=?
                AND i.lease_until>clock_timestamp()''', (item_id,owner,token)).fetchone())

    def heartbeat(self, item_id, owner, token, lease_seconds):
        if lease_seconds <= 0:
            raise ValueError('positive lease duration required')
        try:
            with self.checkpoint_guard(item_id,owner,token):
                self.db.execute("UPDATE ingest_batch_items SET lease_until=clock_timestamp()+(? * interval '1 second'),updated_at=clock_timestamp() WHERE id=?", (lease_seconds,item_id))
            return True
        except ValueError:
            return False

    def finish(self, item_id, owner, token, status, **fields):
        if status not in ('succeeded','failed','retry_wait','cancelled'):
            raise ValueError('invalid item finish status')
        allowed = {'stage','progress','error_code','error_message','public_error','failed_chunk_ordinal','auto_retry_count','next_retry_at','result','source_document_id','source_version_id'}
        if set(fields)-allowed:
            raise ValueError('unknown item fields')
        try:
            with self.commit_guard(item_id,owner,token) as item:
                sets = ["status=?","lease_owner=NULL","lease_token=NULL","lease_until=NULL","updated_at=clock_timestamp()","completed_at=CASE WHEN ?='retry_wait' THEN NULL ELSE clock_timestamp() END"]
                args = [status,status]
                for key,value in fields.items():
                    sets.append(key+'=?'+('::jsonb' if key=='result' else ''))
                    args.append(_json(value) if key=='result' else value)
                self.db.execute('UPDATE ingest_batch_items SET '+','.join(sets)+' WHERE id=?', (*args,item_id))
                attempt_result=fields.get('result') if status=='succeeded' else {
                    'document_generation':item['document_generation'],'source_version_id':item['source_version_id'],
                    'failure':{key:fields.get(key) for key in ('stage','error_code','error_message',
                        'failed_chunk_ordinal','auto_retry_count','next_retry_at')}}
                self.db.execute('''UPDATE ingest_item_attempts SET status=?,error_code=?,error_message=?,result=?::jsonb,
                    completed_at=clock_timestamp() WHERE item_id=? AND lease_token=?''', (status,fields.get('error_code'),fields.get('error_message'),_json(attempt_result),item_id,token))
                self.db.execute('''UPDATE ingest_batches SET status='completed',completed_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE id=? AND status='running' AND NOT EXISTS (SELECT 1 FROM ingest_batch_items WHERE batch_id=? AND status IN ('pending','running','retry_wait','interrupted'))''', (item['batch_id'],item['batch_id']))
            return True
        except ValueError:
            return False

    def recover_expired(self):
        recovered = 0
        with self.repo._transaction():
            batches = [row[0] for row in self.db.execute("SELECT DISTINCT batch_id FROM ingest_batch_items WHERE status='running' AND lease_until<=clock_timestamp()")]
            for batch_id in batches:
                self.db.execute('SELECT id FROM ingest_batches WHERE id=? FOR UPDATE', (batch_id,)).fetchone()
                rows = self.db.execute("SELECT id,lease_token FROM ingest_batch_items WHERE batch_id=? AND status='running' AND lease_until<=clock_timestamp() FOR UPDATE SKIP LOCKED", (batch_id,)).fetchall()
                for row in rows:
                    self.db.execute("UPDATE ingest_item_attempts SET status='lease_expired',completed_at=clock_timestamp() WHERE item_id=? AND lease_token=?", (row['id'],row['lease_token']))
                    self.db.execute("UPDATE ingest_batch_items SET status='interrupted',stage='interrupted',lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE id=?", (row['id'],))
                    recovered += 1
        return recovered

    def retry(self, project_id, batch_id, item_id=None, mode='resume_checkpoint'):
        if mode not in ('resume_checkpoint','retry_failed_chunks','restart_document'):
            raise ValueError('invalid retry mode')
        with self.repo._transaction():
            batch = self.db.execute('SELECT * FROM ingest_batches WHERE project_id=? AND id=? FOR UPDATE', (project_id,batch_id)).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            query = "SELECT * FROM ingest_batch_items WHERE batch_id=? AND status IN ('failed','cancelled','interrupted','retry_wait')"
            args = [batch_id]
            if item_id is not None:
                query += ' AND id=?'
                args.append(item_id)
            rows = self.db.execute(query+' FOR UPDATE',args).fetchall()
            if item_id is not None and not rows:
                existing=self.db.execute('SELECT status,attempt,retry_mode FROM ingest_batch_items WHERE id=? AND batch_id=? FOR UPDATE',(item_id,batch_id)).fetchone()
                if (existing and existing['retry_mode']==mode and
                        ((existing['status']=='pending' and existing['attempt']>0) or
                         (existing['status']=='running' and existing['attempt']>1))):
                    return self.get_batch(project_id,batch_id)
                raise ValueError('item is not retryable')
            for row in rows:
                if mode=='restart_document':
                    self.db.execute('UPDATE ingest_batch_items SET document_generation=document_generation+1,source_restart_of=source_version_id,source_version_id=NULL WHERE id=?',(row['id'],))
                self.db.execute("UPDATE ingest_batch_items SET status='pending',retry_mode=?,stage='queued',progress=0,error_code=NULL,error_message=NULL,public_error=NULL,auto_retry_count=0,next_retry_at=NULL,completed_at=NULL,updated_at=clock_timestamp() WHERE id=?",(mode,row['id']))
            if rows:
                self.db.execute("UPDATE ingest_batches SET status='running',completed_at=NULL,updated_at=clock_timestamp() WHERE id=?",(batch_id,))
        return self.get_batch(project_id,batch_id)
