"""Project/version constrained source metadata persistence on Repository's connection."""
import json
from contextlib import contextmanager
from uuid import uuid4
from pathlib import PurePosixPath
from ..core.time import utc_now
from ..utils.source_locations import focus_source_location


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SourceStore:
    def __init__(self, repo):
        self.repo = repo

    @contextmanager
    def publication_guard(self):
        with self.repo.unit_of_work():
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('source-object-gc',))
            yield

    def _insert(self, table, value, conflict=''):
        fields = ','.join(value)
        marks = ','.join('?' for _ in value)
        self.repo._db.execute(f'INSERT INTO {table} ({fields}) VALUES ({marks}) {conflict}', tuple(value.values()))

    def register_object(self, obj):
        """Register already durable bytes using application DML under the GC lock."""
        with self.publication_guard():
            self._insert('source_objects', dict(obj, created_at=utc_now()), 'ON CONFLICT(sha256) DO NOTHING')
            row = self.repo._db.execute('SELECT * FROM source_objects WHERE sha256=?', (obj['sha256'],)).fetchone()
            if row['storage_key'] != obj['storage_key'] or row['size_bytes'] != obj['size_bytes']:
                raise ValueError('Source object metadata conflict')
            return dict(row)

    def get_upload_object(self, project_id, item_id):
        with self.repo._lock:
            row = self.repo._db.execute('''SELECT o.* FROM source_objects o JOIN ingest_batch_items i
                ON i.upload_object_id=o.id WHERE i.project_id=? AND i.id=?''', (project_id,item_id)).fetchone()
            if row is None:
                raise KeyError('Upload object not found')
            return dict(row)

    def create_version(self, project_id, document_id, obj, original_name, parsed, assets=()):
        with self.repo.unit_of_work():
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-object-gc',))
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('source:'+project_id+':'+document_id,))
            self._insert('source_objects', dict(obj, created_at=utc_now()), 'ON CONFLICT(sha256) DO NOTHING')
            n = self.repo._db.execute('SELECT COALESCE(MAX(version),0)+1 AS n FROM source_document_versions WHERE project_id=? AND document_id=?', (project_id, document_id)).fetchone()['n']
            version_id = parsed['document_version_id']
            extension=PurePosixPath(original_name.replace('\\','/')).suffix.lower().lstrip('.')
            source_format='markdown' if extension in {'md','markdown'} else extension or 'text'
            self._insert('source_document_versions', dict(id=version_id,project_id=project_id,document_id=document_id,version=n,source_object_id=obj['id'],original_name=original_name,format=source_format,parser_name=parsed['parser_name'],parser_version=parsed['parser_version'],parse_status='incomplete' if any(a['status'] in {'missing','blocked','external_unfrozen'} for a in assets) else 'completed',source_text=parsed['text'],rendered_html=parsed['rendered_html'],parser_metadata=encode(parsed.get('metadata',{})),created_at=utc_now()))
            for block in parsed['blocks']:
                self._insert('parsed_blocks',dict(block,project_id=project_id,heading_path=encode(block['heading_path']),payload=encode(block['payload'])))
            for asset in assets:
                value=dict(asset)
                asset_obj=value.pop('object',None)
                if asset_obj: self._insert('source_objects',dict(asset_obj,created_at=utc_now()),'ON CONFLICT(sha256) DO NOTHING')
                self._insert('source_assets',dict(id=value['id'],project_id=project_id,document_version_id=version_id,source_object_id=asset_obj['id'] if asset_obj else None,original_path=value.get('original_path',value.get('url','')),resolved_path=value.get('resolved_path',''),alt_text=value.get('alt',''),width=value.get('width'),height=value.get('height'),status=value['status'],kind=value['kind'],source_start_line=value.get('source_start_line'),source_end_line=value.get('source_end_line'),provenance=encode(value.get('provenance',{}))))
        return self.get_version(project_id, version_id)

    def get_version(self, project_id, version_id, *, chunk_version_id=None):
        with self.repo._lock:
            row=self.repo._db.execute('SELECT v.*,o.storage_key,o.mime_type,o.sha256 FROM source_document_versions v JOIN source_objects o ON o.id=v.source_object_id WHERE v.project_id=? AND v.id=?',(project_id,version_id)).fetchone()
            if row is None: raise KeyError('Source version not found')
            result=dict(row)
            result['parser_metadata']=json.loads(result['parser_metadata'])
            result['blocks']=self._list('parsed_blocks',project_id,version_id,'ordinal')
            result['assets']=self._list('source_assets',project_id,version_id,'id')
            result['assets'].sort(key=lambda a:(a.get('provenance',{}).get('source_image_ordinal',1_000_000),a.get('source_start_line') or 0,a['id']))
            result['chunks']=self._version_chunks(project_id,version_id,chunk_version_id)
            return result

    def clone_version(self,project_id,version_id,document_id,*,original_name=None):
        """Copy immutable reading/OCR/ASR receipts for one formal document.

        Object bytes remain shared; every source, block, asset and recognition ID
        is remapped so document ownership and evidence foreign keys stay exact.
        No parser, decoder or recognition adapter participates in this operation.
        """
        tables=[('parsed_blocks','block_id'),('source_assets','id'),
                ('source_asset_ocr_runs','id'),('source_asset_ocr_blocks','id'),
                ('source_asset_asr_runs','id'),('source_asset_asr_segments','id')]
        json_fields={'payload','provenance','parser_metadata','processing_details','heading_path'}
        with self.publication_guard():
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source:'+project_id+':'+document_id,))
            original=self.repo._db.execute('SELECT * FROM source_document_versions WHERE project_id=? AND id=?',(project_id,version_id)).fetchone()
            if original is None:raise KeyError('Source version not found')
            original=dict(original)
            identifier=str(uuid4())
            mapping={version_id:identifier,original['document_id']:document_id}
            copies={}
            for table,key in tables:
                copies[table]=[dict(row) for row in self.repo._db.execute(
                    f'SELECT * FROM {table} WHERE project_id=? AND document_version_id=?',(project_id,version_id))]
                mapping.update({row[key]:str(uuid4()) for row in copies[table]})
            def remap(value):
                if isinstance(value,str):return mapping.get(value,value)
                if isinstance(value,list):return [remap(item) for item in value]
                if isinstance(value,dict):return {key:remap(item) for key,item in value.items()}
                return value
            def copy_row(row):
                result={}
                for key,value in row.items():
                    if key in json_fields and isinstance(value,str):result[key]=encode(remap(json.loads(value)))
                    elif key in {'id','block_id','document_id','document_version_id','asset_id','ocr_run_id','asr_run_id'}:result[key]=remap(value)
                    else:result[key]=value
                return result
            version=copy_row(original)
            metadata=json.loads(version.get('parser_metadata') or '{}')
            metadata.pop('preparation_task_id',None)
            if original_name is not None:
                version['original_name']=original_name
                metadata['source_file']=original_name
            version['parser_metadata']=encode(metadata)
            version.update(id=identifier,document_id=document_id,created_at=utc_now(),
                version=self.repo._db.execute('SELECT COALESCE(MAX(version),0)+1 AS n FROM source_document_versions WHERE project_id=? AND document_id=?',(project_id,document_id)).fetchone()['n'])
            for before,after in mapping.items():version['rendered_html']=version['rendered_html'].replace(before,after)
            self._insert('source_document_versions',version)
            for table,key in tables:
                for row in copies[table]:self._insert(table,copy_row(row))
            return self.get_version(project_id,identifier),mapping

    def _version_chunks(self,project_id,version_id,chunk_version_id=None):
        """Read one successful result for this source, with an optional pinned fragment."""
        rows=self.repo._db.execute('''WITH mapped AS (
                SELECT s.*,r.id AS record_id,r.payload AS record_payload,r.version,
                    r.payload::jsonb#>>'{metadata,run_id}' AS run_id
                FROM chunk_source_spans s JOIN record_versions r
                  ON r.project_id=s.project_id AND r.version_id=s.chunk_version_id
                WHERE s.project_id=? AND s.document_version_id=?
                  AND r.payload::jsonb#>>'{metadata,_deleted}' IS DISTINCT FROM 'true'
            ), latest_run AS (
                SELECT run.id FROM ingest_runs run JOIN record_versions receipt
                  ON receipt.project_id=run.project_id AND receipt.version_id=run.document_version_id
                JOIN source_document_versions source ON source.project_id=run.project_id
                  AND source.document_id=run.document_id AND source.id=?
                WHERE run.project_id=? AND run.status='completed'
                  AND (receipt.payload::jsonb#>>'{metadata,source_document_version_id}'=?
                       OR EXISTS (SELECT 1 FROM mapped WHERE mapped.run_id=run.id))
                ORDER BY run.created_at DESC,run.id DESC LIMIT 1
            ), ranked AS (
                SELECT mapped.*,dense_rank() OVER (
                    PARTITION BY record_id ORDER BY
                    CASE WHEN chunk_version_id=? THEN 0 ELSE 1 END,mapped.version DESC
                ) AS position
                FROM mapped LEFT JOIN ingest_runs run
                  ON run.project_id=mapped.project_id AND run.id=mapped.run_id
                WHERE mapped.run_id IS NOT DISTINCT FROM (SELECT id FROM latest_run)
                   OR (chunk_version_id=? AND (mapped.run_id IS NULL OR run.status='completed'))
            )
            SELECT * FROM ranked WHERE position=1 ORDER BY record_id,ordinal''',
            (project_id,version_id,version_id,project_id,version_id,chunk_version_id,chunk_version_id)).fetchall()
        chunks={}
        for row in rows:
            item=dict(row)
            payload=json.loads(item.pop('record_payload'))
            record_id=item.pop('record_id')
            for key in ('version','run_id','position'):
                item.pop(key)
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
            for key in ('payload','heading_path','provenance'):
                if key in item: item[key]=json.loads(item[key])
            result.append(item)
        return result

    def get_asset(self, project_id, version_id, asset_id):
        with self.repo._lock:
            row=self.repo._db.execute('SELECT a.*,o.storage_key,o.mime_type,o.sha256 FROM source_assets a LEFT JOIN source_objects o ON o.id=a.source_object_id WHERE a.project_id=? AND a.document_version_id=? AND a.id=?',(project_id,version_id,asset_id)).fetchone()
            if row is None: raise KeyError('Source asset not found')
            result=dict(row)
            result['provenance']=json.loads(result['provenance'])
            return result

    def add_spans(self, project_id, version_id, chunk_version_id, spans):
        # 只校验版本存在：切片写入不需要 blocks/assets/chunks 这些大对象。
        # 摄取时每个切片都会调用一次，全量读取会变成 O(切片数 × 全文)。
        with self.repo._lock:
            exists = self.repo._db.execute(
                'SELECT 1 FROM source_document_versions WHERE project_id=? AND id=?',
                (project_id, version_id)).fetchone()
        if exists is None:
            raise KeyError('Source version not found')
        with self.repo.unit_of_work():
            # 同一 chunk 的片段共用一把锁，提到循环外即可，不必每片段重复加锁。
            self.repo._db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('source-span:'+project_id+':'+version_id+':'+chunk_version_id,))
            for ordinal, span in enumerate(spans):
                s=dict(span)
                value=dict(id=str(uuid4()),project_id=project_id,document_version_id=version_id,chunk_version_id=chunk_version_id,block_id=s.get('block_id'),ocr_block_id=s.get('ocr_block_id'),asr_segment_id=s.get('asr_segment_id'),ordinal=ordinal,chunk_start_char=s.get('chunk_start_char',0),chunk_end_char=s.get('chunk_end_char',0),block_start_char=s.get('block_start_char',0),block_end_char=s.get('block_end_char',0),payload=encode(s.get('payload',{})))
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
            # 证据定位在一次点击里发生，按 span 逐条取块会在几十个片段上
            # 变成几十次往返；整批 ID 一次取回再按 span 顺序还原。
            boxes_by_id={};segments_by_id={}
            ocr_ids=list(dict.fromkeys(s['ocr_block_id'] for s in spans if s['ocr_block_id']))
            if ocr_ids:
                placeholders=','.join('?'*len(ocr_ids))
                for row in self.repo._db.execute(
                        'SELECT b.*,r.asset_id FROM source_asset_ocr_blocks b JOIN source_asset_ocr_runs r ON r.id=b.ocr_run_id WHERE b.project_id=? AND b.document_version_id=? AND b.id IN ('+placeholders+')',
                        (project_id,version_id,*ocr_ids)).fetchall():
                    boxes_by_id[row['id']]=dict(row)
            asr_ids=list(dict.fromkeys(s['asr_segment_id'] for s in spans if s.get('asr_segment_id')))
            if asr_ids:
                placeholders=','.join('?'*len(asr_ids))
                for row in self.repo._db.execute(
                        'SELECT s.*,r.asset_id FROM source_asset_asr_segments s JOIN source_asset_asr_runs r ON r.id=s.asr_run_id AND r.project_id=s.project_id AND r.document_version_id=s.document_version_id WHERE s.project_id=? AND s.document_version_id=? AND s.id IN ('+placeholders+')',
                        (project_id,version_id,*asr_ids)).fetchall():
                    segments_by_id[row['id']]=dict(row)
            boxes=[boxes_by_id[s['ocr_block_id']] for s in spans if s['ocr_block_id'] in boxes_by_id]
            segments=[segments_by_id[s['asr_segment_id']] for s in spans if s.get('asr_segment_id') in segments_by_id]
            return focus_source_location(dict(status='available' if spans else 'mapping_unavailable',code=None if spans else 'source_mapping_unavailable',document_version_id=version_id,spans=spans,blocks=version['blocks'],tab='reading'),boxes=boxes,segments=segments)

    def asr_runs(self, project_id, version_id, asset_id):
        self.get_asset(project_id,version_id,asset_id)
        with self.repo._lock:
            runs=[dict(row) for row in self.repo._db.execute('SELECT * FROM source_asset_asr_runs WHERE project_id=? AND document_version_id=? AND asset_id=? ORDER BY started_at,id',(project_id,version_id,asset_id)).fetchall()]
            for run in runs:
                run['segments']=[dict(row) for row in self.repo._db.execute('SELECT * FROM source_asset_asr_segments WHERE project_id=? AND document_version_id=? AND asr_run_id=? ORDER BY ordinal',(project_id,version_id,run['id'])).fetchall()]
            return runs

    def save_asr(self, run, segments):
        with self.repo.unit_of_work():
            self._insert('source_asset_asr_runs',run)
            for segment in segments:self._insert('source_asset_asr_segments',segment)
        return dict(run,segments=segments)

    def reusable_asr(self, project_id, sha256, fingerprint):
        with self.repo._lock:
            row=self.repo._db.execute("SELECT * FROM source_asset_asr_runs WHERE project_id=? AND asset_sha256=? AND configuration_fingerprint=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",(project_id,sha256,fingerprint)).fetchone()
            if not row:return None
            run=dict(row)
            run['segments']=[dict(row) for row in self.repo._db.execute('SELECT * FROM source_asset_asr_segments WHERE project_id=? AND document_version_id=? AND asr_run_id=? ORDER BY ordinal',(project_id,run['document_version_id'],run['id'])).fetchall()]
            return run

    def ocr_runs(self, project_id, version_id, asset_id):
        self.get_asset(project_id,version_id,asset_id)
        with self.repo._lock:
            runs=[dict(r) for r in self.repo._db.execute('SELECT * FROM source_asset_ocr_runs WHERE project_id=? AND document_version_id=? AND asset_id=? ORDER BY started_at',(project_id,version_id,asset_id)).fetchall()]
            blocks=[dict(b) for b in self.repo._db.execute('SELECT b.* FROM source_asset_ocr_blocks b JOIN source_asset_ocr_runs r ON r.id=b.ocr_run_id AND r.project_id=b.project_id AND r.document_version_id=b.document_version_id WHERE b.project_id=? AND b.document_version_id=? AND r.asset_id=? ORDER BY b.ordinal',(project_id,version_id,asset_id)).fetchall()]
            for run in runs:
                run['processing_details']=json.loads(run.get('processing_details') or '{}')
                run['blocks']=[b for b in blocks if b['ocr_run_id']==run['id']]
            return runs

    def save_ocr(self, run, blocks):
        with self.repo.unit_of_work():
            value={**run,'processing_details':encode(run.get('processing_details') or {})}
            update=','.join(f'{field}=excluded.{field}' for field in value if field not in {'id','project_id','document_version_id','asset_id'})
            self._insert('source_asset_ocr_runs',value,'ON CONFLICT(id) DO UPDATE SET '+update+
                         ' WHERE source_asset_ocr_runs.project_id=excluded.project_id AND source_asset_ocr_runs.document_version_id=excluded.document_version_id AND source_asset_ocr_runs.asset_id=excluded.asset_id')
            for block in blocks: self._insert('source_asset_ocr_blocks',block)
        return dict(run,blocks=blocks)

    def reusable_ocr(self, project_id, sha256, fingerprint):
        with self.repo._lock:
            row=self.repo._db.execute("SELECT * FROM source_asset_ocr_runs WHERE project_id=? AND asset_sha256=? AND configuration_fingerprint=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",(project_id,sha256,fingerprint)).fetchone()
            if not row: return None
            run=dict(row)
            run['processing_details']=json.loads(run.get('processing_details') or '{}')
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
            rows=self.repo._db.execute('SELECT o.id,o.storage_key FROM source_objects o WHERE NOT EXISTS(SELECT 1 FROM source_document_versions v WHERE v.source_object_id=o.id) AND NOT EXISTS(SELECT 1 FROM source_assets a WHERE a.source_object_id=o.id) AND NOT EXISTS(SELECT 1 FROM ingest_batch_items i WHERE i.upload_object_id=o.id) FOR UPDATE').fetchall()
            for row in rows:
                object_store.delete_unreferenced(row['storage_key'],lambda key:False)
                self.repo._db.execute('DELETE FROM source_objects WHERE id=?',(row['id'],))
                removed.append(row['id'])
        return removed
