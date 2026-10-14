"""Project/version constrained source metadata persistence on Repository's connection."""
import json
from uuid import uuid4
from pathlib import PurePosixPath
from ..core.time import utc_now


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SourceStore:
    def __init__(self, repo):
        self.repo = repo

    def _insert(self, table, value, conflict=''):
        fields = ','.join(value)
        marks = ','.join('?' for _ in value)
        self.repo._db.execute(f'INSERT INTO {table} ({fields}) VALUES ({marks}) {conflict}', tuple(value.values()))

    def create_version(self, project_id, document_id, obj, original_name, parsed, assets=()):
        with self.repo.unit_of_work():
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-object-gc',))
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('source:'+project_id+':'+document_id,))
            self._insert('source_objects', dict(obj, created_at=utc_now()), 'ON CONFLICT(sha256) DO NOTHING')
            n = self.repo._db.execute('SELECT COALESCE(MAX(version),0)+1 AS n FROM source_document_versions WHERE project_id=? AND document_id=?', (project_id, document_id)).fetchone()['n']
            version_id = parsed['document_version_id']
            extension=PurePosixPath(original_name.replace('\\','/')).suffix.lower().lstrip('.')
            source_format='markdown' if extension in {'md','markdown'} else extension or 'text'
            self._insert('source_document_versions', dict(id=version_id,project_id=project_id,document_id=document_id,version=n,source_object_id=obj['id'],original_name=original_name,format=source_format,parser_name=parsed['parser_name'],parser_version=parsed['parser_version'],parse_status='incomplete' if any(a['status']=='missing' for a in assets) else 'completed',source_text=parsed['text'],rendered_html=parsed['rendered_html'],created_at=utc_now()))
            for block in parsed['blocks']:
                self._insert('parsed_blocks',dict(block,project_id=project_id,heading_path=encode(block['heading_path']),payload=encode(block['payload'])))
            for asset in assets:
                value=dict(asset)
                asset_obj=value.pop('object',None)
                if asset_obj: self._insert('source_objects',dict(asset_obj,created_at=utc_now()),'ON CONFLICT(sha256) DO NOTHING')
                self._insert('source_assets',dict(id=value['id'],project_id=project_id,document_version_id=version_id,source_object_id=asset_obj['id'] if asset_obj else None,original_path=value.get('original_path',value.get('url','')),resolved_path=value.get('resolved_path',''),alt_text=value.get('alt',''),width=value.get('width'),height=value.get('height'),status=value['status'],kind=value['kind'],source_start_line=value.get('source_start_line'),source_end_line=value.get('source_end_line')))
        return self.get_version(project_id, version_id)

    def get_version(self, project_id, version_id):
        with self.repo._lock:
            row=self.repo._db.execute('SELECT v.*,o.storage_key,o.mime_type,o.sha256 FROM source_document_versions v JOIN source_objects o ON o.id=v.source_object_id WHERE v.project_id=? AND v.id=?',(project_id,version_id)).fetchone()
            if row is None: raise KeyError('Source version not found')
            result=dict(row)
            result['blocks']=self._list('parsed_blocks',project_id,version_id,'ordinal')
            result['assets']=self._list('source_assets',project_id,version_id,'id')
            result['chunks']=self._version_chunks(project_id,version_id)
            return result

    def _version_chunks(self,project_id,version_id):
        """Read the immutable mapped record version; never substitute a current revision."""
        rows=self.repo._db.execute('''SELECT s.*,r.id AS record_id,r.payload AS record_payload
            FROM chunk_source_spans s JOIN record_versions r
              ON r.project_id=s.project_id AND r.version_id=s.chunk_version_id
            WHERE s.project_id=? AND s.document_version_id=?
            ORDER BY r.recorded_at,r.id,s.ordinal''',(project_id,version_id)).fetchall()
        chunks={}
        for row in rows:
            item=dict(row)
            payload=json.loads(item.pop('record_payload'))
            record_id=item.pop('record_id')
            identity=item['chunk_version_id']
            item['payload']=json.loads(item['payload'])
            if identity not in chunks:
                chunks[identity]=dict(id=record_id,version_id=identity,text=payload.get('text',''),metadata=payload.get('metadata',{}),spans=[])
            chunks[identity]['spans'].append(item)
        return list(chunks.values())

    def _list(self, table, project_id, version_id, order):
        rows=self.repo._db.execute(f'SELECT * FROM {table} WHERE project_id=? AND document_version_id=? ORDER BY {order}',(project_id,version_id)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            for key in ('payload','heading_path'):
                if key in item: item[key]=json.loads(item[key])
            result.append(item)
        return result

    def get_asset(self, project_id, version_id, asset_id):
        with self.repo._lock:
            row=self.repo._db.execute('SELECT a.*,o.storage_key,o.mime_type,o.sha256 FROM source_assets a LEFT JOIN source_objects o ON o.id=a.source_object_id WHERE a.project_id=? AND a.document_version_id=? AND a.id=?',(project_id,version_id,asset_id)).fetchone()
            if row is None: raise KeyError('Source asset not found')
            return dict(row)

    def add_spans(self, project_id, version_id, chunk_version_id, spans):
        self.get_version(project_id,version_id)
        with self.repo.unit_of_work():
            for ordinal, span in enumerate(spans):
                s=dict(span)
                value=dict(id=str(uuid4()),project_id=project_id,document_version_id=version_id,chunk_version_id=chunk_version_id,block_id=s.get('block_id'),ocr_block_id=s.get('ocr_block_id'),ordinal=ordinal,chunk_start_char=s.get('chunk_start_char',0),chunk_end_char=s.get('chunk_end_char',0),block_start_char=s.get('block_start_char',0),block_end_char=s.get('block_end_char',0),payload=encode(s.get('payload',{})))
                self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-span:'+project_id+':'+version_id+':'+chunk_version_id,))
                existing=self.repo._db.execute('SELECT * FROM chunk_source_spans WHERE project_id=? AND document_version_id=? AND chunk_version_id=? AND ordinal=?',(project_id,version_id,chunk_version_id,ordinal)).fetchone()
                if existing:
                    for key in value:
                        if key=='id': continue
                        before=json.loads(existing[key]) if key=='payload' else existing[key]
                        after=json.loads(value[key]) if key=='payload' else value[key]
                        if before!=after: raise ValueError('Immutable source span conflict')
                else: self._insert('chunk_source_spans',value)

    def locate(self, project_id, version_id, chunk_version_id=None, assertion_id=None):
        version=self.get_version(project_id,version_id)
        with self.repo._lock:
            if assertion_id and not chunk_version_id:
                assertion=self.repo._db.execute('SELECT chunk_id,document_version_id FROM assertions WHERE project_id=? AND id=?',(project_id,assertion_id)).fetchone()
                if assertion:
                    candidates=self.repo._db.execute('SELECT DISTINCT s.chunk_version_id FROM chunk_source_spans s JOIN record_versions r ON r.project_id=s.project_id AND r.version_id=s.chunk_version_id WHERE s.project_id=? AND s.document_version_id=? AND r.id=?',(project_id,version_id,assertion['chunk_id'])).fetchall()
                    if len(candidates)==1: chunk_version_id=candidates[0]['chunk_version_id']
            if not chunk_version_id: return dict(status='mapping_unavailable',code='source_mapping_unavailable',document_version_id=version_id,spans=[])
            spans=[dict(s) for s in self.repo._db.execute('SELECT * FROM chunk_source_spans WHERE project_id=? AND document_version_id=? AND chunk_version_id=? ORDER BY ordinal',(project_id,version_id,chunk_version_id)).fetchall()]
            for s in spans: s['payload']=json.loads(s['payload'])
            boxes=[]; asset_id=None
            for s in spans:
                if s['ocr_block_id']:
                    row=self.repo._db.execute('SELECT b.*,r.asset_id FROM source_asset_ocr_blocks b JOIN source_asset_ocr_runs r ON r.id=b.ocr_run_id WHERE b.project_id=? AND b.document_version_id=? AND b.id=?',(project_id,version_id,s['ocr_block_id'])).fetchone()
                    if row: boxes.append(dict(row)); asset_id=row['asset_id']
            return dict(status='available' if spans else 'mapping_unavailable',code=None if spans else 'source_mapping_unavailable',document_version_id=version_id,spans=spans,blocks=version['blocks'],block_id=next((s['block_id'] for s in spans if s['block_id']),None),asset_id=asset_id,boxes=boxes,tab='reading')

    def ocr_runs(self, project_id, version_id, asset_id):
        self.get_asset(project_id,version_id,asset_id)
        with self.repo._lock:
            runs=[dict(r) for r in self.repo._db.execute('SELECT * FROM source_asset_ocr_runs WHERE project_id=? AND document_version_id=? AND asset_id=? ORDER BY started_at',(project_id,version_id,asset_id)).fetchall()]
            blocks=[dict(b) for b in self.repo._db.execute('SELECT b.* FROM source_asset_ocr_blocks b JOIN source_asset_ocr_runs r ON r.id=b.ocr_run_id AND r.project_id=b.project_id AND r.document_version_id=b.document_version_id WHERE b.project_id=? AND b.document_version_id=? AND r.asset_id=? ORDER BY b.ordinal',(project_id,version_id,asset_id)).fetchall()]
            for run in runs: run['blocks']=[b for b in blocks if b['ocr_run_id']==run['id']]
            return runs

    def save_ocr(self, run, blocks):
        with self.repo.unit_of_work():
            self._insert('source_asset_ocr_runs',run)
            for block in blocks: self._insert('source_asset_ocr_blocks',block)
        return dict(run,blocks=blocks)

    def reusable_ocr(self, project_id, sha256, fingerprint):
        with self.repo._lock:
            row=self.repo._db.execute("SELECT * FROM source_asset_ocr_runs WHERE project_id=? AND asset_sha256=? AND configuration_fingerprint=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",(project_id,sha256,fingerprint)).fetchone()
            if not row: return None
            run=dict(row)
            run['blocks']=[b for b in self._list('source_asset_ocr_blocks',project_id,run['document_version_id'],'ordinal') if b['ocr_run_id']==run['id']]
            return run

    def ignore_asset(self, project_id, version_id, asset_id, actor, reason):
        self.get_asset(project_id,version_id,asset_id)
        if not actor or not reason.strip(): raise ValueError('Actor and reason are required')
        with self.repo.unit_of_work():
            current=self.repo._db.execute('SELECT ignored_by,ignore_reason FROM source_assets WHERE project_id=? AND document_version_id=? AND id=? FOR UPDATE',(project_id,version_id,asset_id)).fetchone()
            if current['ignored_by']:
                if current['ignored_by']!=actor or current['ignore_reason']!=reason:
                    raise ValueError('Image ignore decision has already been audited')
                return self.get_asset(project_id,version_id,asset_id)
            self.repo._db.execute("UPDATE source_assets SET status='ignored',ignored_by=?,ignored_at=?,ignore_reason=? WHERE project_id=? AND document_version_id=? AND id=?",(actor,utc_now(),reason,project_id,version_id,asset_id))
        return self.get_asset(project_id,version_id,asset_id)

    def update_asset_ocr(self,project_id,version_id,asset_id,status,width=None,height=None):
        self.get_asset(project_id,version_id,asset_id)
        with self.repo.unit_of_work():
            self.repo._db.execute('UPDATE source_assets SET status=?,width=COALESCE(?,width),height=COALESCE(?,height) WHERE project_id=? AND document_version_id=? AND id=? AND status<>?',(status,width,height,project_id,version_id,asset_id,'ignored'))

    def collect_unreferenced_objects(self,object_store):
        """Remove only objects with no document/asset references under publication lock."""
        removed=[]
        with self.repo.unit_of_work():
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-object-gc',))
            rows=self.repo._db.execute('SELECT o.id,o.storage_key FROM source_objects o WHERE NOT EXISTS(SELECT 1 FROM source_document_versions v WHERE v.source_object_id=o.id) AND NOT EXISTS(SELECT 1 FROM source_assets a WHERE a.source_object_id=o.id) FOR UPDATE').fetchall()
            for row in rows:
                object_store.delete_unreferenced(row['storage_key'],lambda key:False)
                self.repo._db.execute('DELETE FROM source_objects WHERE id=?',(row['id'],))
                removed.append(row['id'])
        return removed
