"""Owned staging and explicit parse/publication application workflow."""
from copy import deepcopy
from contextlib import nullcontext
import base64
import hashlib
import html
import io
import mimetypes
import os
import posixpath
from pathlib import Path
import re
import zipfile
from uuid import uuid4
from importlib.metadata import version,PackageNotFoundError

from ..models import UploadOptions,IngestBatchSettings
from ..repository.preparation_store import PreparationStore,encoded
from ..integrations.document_package import SingleDocumentPackageReader
from ..integrations.markdown_parser import parse_markdown


class PreparationConflict(ValueError):
    def __init__(self,message,items=None):
        super().__init__(message)
        self.items=items or []


def recognition_error(result):
    """Technical recognition failures block publication, including old caches."""
    informational = {'ocr_disabled', 'asr_no_audio', 'asr_no_speech'}
    return next((w for w in (result or {}).get('warnings', [])
        if isinstance(w, dict) and (str(w.get('code', '')).startswith(('ocr_', 'asr_'))
            or w.get('code') == 'source_asset_missing')
        and w['code'] not in informational), None)


def validate_file(data,name):
    """Container checks only; never extract text, render pages or run models."""
    suffix=Path(name).suffix.lower()
    if suffix=='.pdf':
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            if not data.lstrip().startswith(b'%PDF-'):raise ValueError('PDF 文件头无效')
            with io.BytesIO(data) as stream:
                document=PdfReader(stream,strict=False)
                if document.is_encrypted and not document.decrypt(''):raise ValueError('PDF 已加密，请上传可读取的原文件')
                if len(document.pages)<1:raise ValueError('PDF 没有页面')
        except PdfReadError as exc:raise ValueError('PDF 结构损坏，请检查原件') from exc
    elif suffix=='.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                reader=SingleDocumentPackageReader()
                files,_=reader._members(archive)
                if '[Content_Types].xml' not in files or 'word/document.xml' not in files:
                    raise ValueError('DOCX 缺少 Word 文档结构')
        except zipfile.BadZipFile as exc:raise ValueError('DOCX 容器损坏，请检查原件') from exc
    elif suffix in ('.png','.jpg','.jpeg','.webp'):
        from ..integrations.document_media import inspect_image
        try:inspect_image(data,mimetypes.guess_type(name)[0])
        except (OSError,SyntaxError) as exc:raise ValueError('图片结构损坏，请检查原件') from exc
    elif suffix in ('.mp4','.webm'):
        from ..integrations.video_asr import inspect_video
        inspect_video(data,mimetypes.guess_type(name)[0])


def options(raw):
    raw=dict(raw)
    parse_config=raw.pop('parse_config',{})
    if not isinstance(parse_config,dict):raise ValueError('parse_config 必须为对象')
    if set(parse_config)-{'ocr_mode'} or parse_config.get('ocr_mode','auto') not in ('auto','disabled'):
        raise ValueError('解析配置仅支持 ocr_mode=auto/disabled')
    value=UploadOptions.model_validate(raw).model_dump()
    value['parse_config']=parse_config
    return value


def fingerprint(digest,name,config):
    """Business metadata and chunk settings never enter the parse identity."""
    root=Path(__file__).resolve().parents[1]
    code=['integrations/document_parser.py','integrations/document_package.py','integrations/image_ocr.py',
          'integrations/video_asr.py','services/source_ingest.py','services/image_ocr.py','services/video_asr.py',
          'services/source_documents.py','integrations/markdown_parser.py','integrations/document_media.py']
    versions={path:hashlib.sha256((root/path).read_bytes()).hexdigest() for path in code}
    model_config={key:value for key,value in os.environ.items() if key.startswith(('KG_DOCUMENT_','KG_OCR_','KG_IMAGE_OCR_','KG_VIDEO_ASR_','KG_SOURCE_REMOTE_'))}
    model_config.setdefault('KG_DOCUMENT_ARTIFACTS_PATH',str(root.parent/'data/model/document-parser/docling'))
    model_config.setdefault('KG_IMAGE_OCR_MODEL_ROOT','data/model/ocr/pp-ocr-v6-small')
    model_config.setdefault('KG_VIDEO_ASR_MODEL_ROOT','data/model/asr/SenseVoiceSmall')
    model_config.setdefault('KG_VIDEO_ASR_VAD_ROOT','data/model/asr/speech_fsmn_vad_zh-cn-16k-common-pytorch')
    for key,value in list(model_config.items()):
        if key.endswith(('_PATH','_DIR','_ROOT','_CONFIG')):
            path=Path(value).expanduser()
            if key=='KG_DOCUMENT_ARTIFACTS_PATH' and not path.is_absolute():path=root.parent/path
            model_config[key]=str(path.resolve())
    runtime={}
    for package in ('docling','docling-core','rapidocr','onnxruntime','funasr','torch','semantica'):
        try:runtime[package]=version(package)
        except PackageNotFoundError:runtime[package]=None
    # An unchanged model directory string must not hide replaced model artifacts.
    artifacts={}
    for key,value in model_config.items():
        if key.endswith(('_PATH','_DIR','_ROOT','_CONFIG')):
            path=Path(value)
            if path.is_file():artifacts[key]=(path.stat().st_size,path.stat().st_mtime_ns)
            elif path.is_dir():
                artifacts[key]=[(str(p.relative_to(path)),p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(path.rglob('*')) if p.is_file() and p.suffix.lower() in ('.onnx','.json','.yaml','.yml','.pt','.pth','.bin','.txt','.safetensors')]
    effective_config={'ocr_mode':config.get('ocr_mode',os.getenv('KG_DOCUMENT_OCR_MODE','auto'))}
    model_config.pop('KG_DOCUMENT_OCR_MODE',None)
    return hashlib.sha256(encoded([digest,Path(name).suffix.lower(),effective_config,versions,model_config,runtime,artifacts]).encode()).hexdigest()


def chunk_version(item):
    code=hashlib.sha256((Path(__file__).parent/'chunking.py').read_bytes()).hexdigest()
    return hashlib.sha256(encoded([item['parser_fingerprint'],{key:item['options'][key] for key in ('chunk_size','chunk_overlap','chunk_strategy')},code]).encode()).hexdigest()


def apply_fields(original,fields,mode='overwrite'):
    result=deepcopy(original)
    for key,value in fields.items():
        if key=='applicability':
            if value is None:
                if mode=='overwrite':
                    result[key]=None
                    result.setdefault('metadata',{}).pop('applicability',None)
                continue
            if not isinstance(value,dict):raise ValueError('applicability 必须为对象')
            scope=deepcopy(result.get(key) or {})
            for dimension,setting in value.items():
                current=scope.get(dimension)
                if mode=='overwrite' or not current or current.get('mode')=='unknown':
                    scope[dimension]=deepcopy(setting)
            result[key]=scope
        elif key=='metadata':
            if not isinstance(value,dict):raise ValueError('metadata 必须为对象')
            metadata=deepcopy(result.get(key) or {})
            for name,setting in value.items():
                if mode=='overwrite' or metadata.get(name) in (None,'',{},[]):metadata[name]=deepcopy(setting)
            result[key]=metadata
        elif mode=='overwrite' or result.get(key) in (None,'',{},[]):result[key]=deepcopy(value)
    return result


class Preparations:
    def __init__(self,service,uploads,sources,batches):
        self.service,self.uploads,self.sources,self.batches=service,uploads,sources,batches
        self.store=PreparationStore(service.repository)

    def create(self,project,owner,defaults):
        self.service.data.get_project(project)
        return self.public(self.store.create(project,owner,options(defaults)))

    def list(self,project,owner):
        self.service.data.get_project(project)
        return {'items':[self.public(s) for s in self.store.list(project,owner)]}

    def _get(self,project,identifier,owner):
        self.service.data.get_project(project)
        return self.store.get(project,identifier,owner)

    @staticmethod
    def item(session,identifier):
        for item in session['items']:
            if item['id']==identifier:return item
        raise KeyError('preparation item not found')

    def get(self,project,identifier,owner):
        return self.public(self._get(project,identifier,owner))

    def settings(self,project,identifier,owner,settings,expected_version):
        validated=IngestBatchSettings.model_validate(settings).model_dump(exclude_unset=True)
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            if expected_version!=session.get('settings_version',0):
                raise PreparationConflict('入库设置版本冲突，请刷新后修改')
            session['publication_settings']=validated
            session['settings_version']=session.get('settings_version',0)+1
            self.store.save(session)
            return self.public(session)

    def public_item(self,item):
        value=deepcopy(item)
        value.pop('upload_key',None)
        value.pop('upload_request',None)
        value.pop('published_snapshot',None)
        value.pop('task_id',None)
        value.pop('source_result',None)
        value.pop('cancelled_task_id',None)
        value.update(progress=None,started_at=None,heartbeat_at=None,error=item.get('error'),stage=item.get('status','staged'),result_ready=False)
        task=self.store.task(item['task_id']) if item.get('task_id') else None
        value['parse_task_id']=task['id'] if task else None
        if task and task['fingerprint']==item['parser_fingerprint'] and item['status'] not in ('cancelled','stale','published'):
            value.update(status='reused' if item['status']=='reused' and task['status']=='completed' else task['status'],
                stage=task['stage'],started_at=str(task['started_at']) if task.get('started_at') else None,
                heartbeat_at=str(task['heartbeat_at']) if task.get('heartbeat_at') else None,error=task.get('error'))
        if task and task.get('result') and task['fingerprint']==item['parser_fingerprint']:
            value['result_ready']=value['status'] in ('completed','reused','published')
        value['reusable']=bool(task and task['status']=='completed')
        value['existing_documents']=self.store.existing_documents(item['project_id'],item['file_sha256']) if item.get('project_id') else []
        if item['status']=='cancelled' and item.get('cancelled_task_id'):
            cancelled=self.store.task(item['cancelled_task_id'])
            if cancelled and cancelled['status']=='cancelling':value.update(status='cancelling',stage='cancelling')
        if not task and item.get('project_id'):
            cached=self.store.find_task(item['project_id'],item['parser_fingerprint'])
            value['reusable']=bool(cached and cached['status']=='completed' and not recognition_error(cached.get('result')))
            value['existing_task_status']=cached['status'] if cached else None
        error=recognition_error(task.get('result')) if task else None
        if error:
            value.update(result_ready=False,reusable=False)
            if item['status'] not in ('cancelled','stale','published'):
                value.update(status='failed',stage='failed',error=error.get('message') or '附件识别未完成，请重试')
        return value

    def public(self,session):
        value={key:deepcopy(val) for key,val in session.items() if key!='operations'}
        value['items']=[self.public_item(item) for item in session['items']]
        return value

    def upload(self,project,identifier,owner,upload,raw,key):
        # Staging validates size/type and owns temporary-file cleanup on all outcomes.
        self._get(project,identifier,owner)
        staged=self.uploads.stage(upload,allow_package=True,max_bytes=self.batches.limits.max_file_bytes)
        try:
            data=staged.path.read_bytes()
            if not data:raise ValueError('文件为空')
            validate_file(data,staged.original_name)
            if staged.original_name.lower().endswith('.zip'):
                from ..integrations.document_parser import DocumentParseError
                try:SingleDocumentPackageReader().validate_upload(data)
                except (ValueError,zipfile.BadZipFile,RuntimeError) as exc:
                    raise DocumentParseError('invalid_package',str(exc)) from exc
            elif Path(staged.original_name).suffix.lower() in ('.md','.txt','.html','.htm'):
                data.decode('utf-8-sig')
            with self.store.guard(project):
                session=self._get(project,identifier,owner)
                merged=options({**session['defaults'],**raw})
                request=hashlib.sha256(encoded([staged.digest,staged.original_name,merged]).encode()).hexdigest()
                if key:
                    self._operation(session,'upload',key,[staged.digest,staged.original_name,merged])
                    prior=next((i for i in session['items'] if i.get('upload_key')==key),None)
                    if prior:
                        if prior['upload_request']!=request:raise PreparationConflict('上传幂等键与已有请求冲突')
                        return self.public_item(prior)
                if len(session['items'])>=self.batches.limits.max_batch_items or sum(i['size_bytes'] for i in session['items'])+staged.size>self.batches.limits.max_batch_bytes:
                    raise ValueError('超过会话文件数量或总大小上限')
                item=dict(id=uuid4().hex,project_id=project,original_name=staged.original_name,size_bytes=staged.size,file_sha256=staged.digest,
                    status='staged',options=merged,metadata_version=1,parser_fingerprint=fingerprint(staged.digest,staged.original_name,merged['parse_config']),
                    batch_id=None,duplicate_of=next((i['id'] for i in session['items'] if i['file_sha256']==staged.digest),None),
                    upload_key=key,upload_request=request)
                item['chunk_version']=chunk_version(item)
                self.store.put_file(item['id'],identifier,data)
                session['items'].append(item)
                self.store.save(session)
                return self.public_item(item)
        finally:
            self.uploads.discard(staged)

    def _version(self,item,expected):
        if expected!=item['metadata_version']:
            raise PreparationConflict('配置版本冲突：'+item['id'])

    def _change(self,item,fields):
        merged=apply_fields(item['options'],fields)
        updated=options(merged)
        current=fingerprint(item['file_sha256'],item['original_name'],updated['parse_config'])
        old=item['parser_fingerprint']
        item['options']=updated
        item['metadata_version']+=1
        item['parser_fingerprint']=current
        item['chunk_version']=chunk_version(item)
        if old!=current:
            item['status']='stale'
        # A published snapshot is immutable. A new publication needs an explicit identity decision.

    def patch(self,project,identifier,owner,item_id,fields,expected):
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            item=self.item(session,item_id)
            self._version(item,expected)
            old_task=item.get('task_id')
            self._change(item,fields)
            self.store.save(session)
            if item['status']=='stale' and old_task:
                self.store.detach(project,item)
                self.store.save(session)
            return self.public_item(item)

    def bulk(self,project,identifier,owner,ids,fields,versions,mode):
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            selected=[self.item(session,i) for i in ids]
            conflicts=[{'item_id':item['id'],'expected_version':versions.get(item['id']),'actual_version':item['metadata_version']}
                for item in selected if versions.get(item['id'])!=item['metadata_version']]
            if conflicts:raise PreparationConflict('配置版本冲突，请刷新所选文件',conflicts)
            detached=[]
            for item in selected:
                updated=apply_fields(item['options'],fields,mode)
                if updated!=item['options']:
                    self._change(item,updated)
                    if item['status']=='stale' and item.get('task_id'):detached.append(item)
            self.store.save(session)
            for item in detached:self.store.detach(project,item)
            self.store.save(session)
            return self.public(session)

    def _operation(self,session,action,key,body):
        if not key:raise ValueError('需要 idempotency_key')
        token=action+':'+key
        digest=hashlib.sha256(encoded([session['id'],body]).encode()).hexdigest()
        operation=self.store.db.execute('SELECT request_hash FROM preparation_operations WHERE project_id=? AND operation=? AND idempotency_key=?',
            (session['project_id'],action,key)).fetchone()
        if operation and operation['request_hash']!=digest:raise PreparationConflict('项目范围内该操作幂等键与已有请求冲突')
        if not operation:
            self.store.db.execute('INSERT INTO preparation_operations(project_id,operation,idempotency_key,session_id,request_hash) VALUES (?,?,?,?,?)',
                (session['project_id'],action,key,session['id'],digest))
        prior=session['operations'].get(token)
        if prior and prior['digest']!=digest:raise PreparationConflict('幂等键与已有操作冲突')
        return token,digest,prior

    def parse(self,project,identifier,owner,ids,versions,key,retry=False):
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            token,digest,prior=self._operation(session,'retry' if retry else 'parse',key,[ids,versions])
            if prior:return self.public(session)
            selected=[self.item(session,i) for i in ids]
            conflicts=[]
            for item in selected:
                if versions.get(item['id'])!=item['metadata_version']:
                    conflicts.append({'item_id':item['id'],'code':'configuration_conflict','actual_version':item['metadata_version']})
                if retry and self.public_item(item)['status'] not in ('failed','cancelled'):
                    conflicts.append({'item_id':item['id'],'code':'not_retryable','message':'仅可重试失败或取消的文件'})
            if conflicts:raise PreparationConflict('所选文件需要刷新或修正，本次未启动解析',conflicts)
            for item in selected:
                # A changed parser/model runtime is a new explicit parse request.
                item['parser_fingerprint']=fingerprint(item['file_sha256'],item['original_name'],item['options']['parse_config'])
                item['chunk_version']=chunk_version(item)
                # A cancelled item can share a healthy completed task. Reset
                # only invalid cached results, never its neighbours' good work.
                cached=self.store.find_task(project,item['parser_fingerprint'])
                self.store.attach(project,item,retry=bool(cached and recognition_error(cached.get('result'))))
                item.pop('cancelled_task_id',None)
            session['operations'][token]={'digest':digest}
            self.store.save(session)
            return self.public(session)

    def cancel(self,project,identifier,owner,ids,remove=False):
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            selected=[self.item(session,i) for i in ids]
            for item in selected:item['status']='cancelled'
            self.store.save(session)
            for item in selected:self.store.detach(project,item)
            if remove:session['items']=[i for i in session['items'] if i['id'] not in ids]
            self.store.save(session)
            # Keep bytes while another shared task may depend on them; expiry reclaims them.
            return self.public(session)

    def result(self,project,identifier,owner,item_id):
        session=self._get(project,identifier,owner)
        item=self.item(session,item_id)
        task=self.store.task(item.get('task_id')) if item.get('task_id') else None
        if not task or task['status']!='completed' or task['fingerprint']!=item['parser_fingerprint'] or item['status'] in ('stale','cancelled'):
            raise PreparationConflict('解析结果尚未就绪或已过期')
        if fingerprint(item['file_sha256'],item['original_name'],item['options']['parse_config'])!=item['parser_fingerprint']:
            raise PreparationConflict('解析器或模型已变化，请明确重新开始解析')
        error=recognition_error(task.get('result'))
        if error:raise PreparationConflict('附件识别未完成，不能入库：'+(error.get('message') or error['code']))
        result=deepcopy(task['result'])
        source=self.sources.get(project,result['source_version_id'])
        # Rechunk from immutable native blocks and saved media receipts only.
        # This path cannot invoke process_asset, OCR, ASR or the document parser.
        if result.get('chunk_settings')!={k:item['options'][k] for k in ('chunk_size','chunk_overlap','chunk_strategy')}:
            # Source processing fetches cached media receipts; it never repeats model work.
            from .source_ingest import prepare_source_ingest
            chunk_options={**item['options'],'title':item['options'].get('title') or item['original_name']}
            chunks,text,warnings=prepare_source_ingest(self.sources,project,source,chunk_options,cached_only=True)
            combined=list(result.get('warnings',[]))
            combined.extend(w for w in warnings if w not in combined)
            result.update(chunks=chunks,text=text,warnings=combined[:100])
        result.update(parser_fingerprint=item['parser_fingerprint'],chunk_version=item['chunk_version'])
        result['parse_task_id']=task['id']
        return result

    def original(self,project,identifier,owner,item_id):
        session=self._get(project,identifier,owner)
        item=self.item(session,item_id)
        return dict(data=self.store.file(item_id),original_name=item['original_name'],mime_type=mimetypes.guess_type(item['original_name'])[0] or 'application/octet-stream')

    def preview(self,project,identifier,owner,item_id):
        original=self.original(project,identifier,owner,item_id)
        suffix=Path(original['original_name']).suffix.lower()
        data=original['data']
        package_files=None
        warnings=[]
        if suffix=='.zip':
            reader=SingleDocumentPackageReader()
            profile=reader.validate_upload(data)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                package_files,_=reader._members(archive)
                text=archive.read(package_files[profile['entry']]).decode('utf-8-sig')
        elif suffix in ('.md','.txt','.html','.htm'):text=data.decode('utf-8-sig')
        else:raise ValueError('该格式请打开原件预览；DOCX 可下载原件，原版式转换尚未就绪')
        parsed=parse_markdown(text,uuid4().hex)
        rendered=parsed['rendered_html']
        images=parsed.get('images',[])
        from ..integrations.document_media import inspect_image,MAX_DOCUMENT_IMAGES,MAX_DOCUMENT_IMAGE_BYTES
        from ..integrations.markdown_package import safe_path
        uris={};cache={};resource_bytes=0
        if len(images)>MAX_DOCUMENT_IMAGES:warnings.append('引用资源超过预览上限，最多展示 '+str(MAX_DOCUMENT_IMAGES)+' 项')
        with zipfile.ZipFile(io.BytesIO(data)) if package_files is not None else nullcontext(None) as archive:
            for index,ref in enumerate(images[:MAX_DOCUMENT_IMAGES]):
                label=ref.get('path') or ref.get('alt') or ref.get('url','')[:200]
                key=None
                try:
                    if ref['kind']=='base64':
                        key='inline:'+hashlib.sha256(ref['data']).hexdigest()
                        payload=ref['data'];mime=ref.get('mime_type')
                    elif ref['kind']=='local' and package_files is not None:
                        key=safe_path(posixpath.normpath(ref['path'].replace('\\','/')))
                        if key in cache:
                            uris[index]=cache[key]
                            continue
                        info=package_files.get(key)
                        if info is None:raise ValueError('资源缺失')
                        if info.file_size>self.batches.limits.max_file_bytes or resource_bytes+info.file_size>MAX_DOCUMENT_IMAGE_BYTES:
                            raise ValueError('资源超过预览容量上限')
                        payload=archive.read(info)
                        mime=mimetypes.guess_type(key)[0]
                    else:raise ValueError('资源缺失、未固化或被安全规则阻止')
                    if key in cache:
                        uris[index]=cache[key]
                        continue
                    resource_bytes+=len(payload)
                    if resource_bytes>MAX_DOCUMENT_IMAGE_BYTES:raise ValueError('资源超过预览容量上限')
                    if not mime or not mime.startswith('video/'):
                        mime=inspect_image(payload,mime if mime and mime.startswith('image/') else None)['mime_type']
                    uri='data:'+mime+';base64,'+base64.b64encode(payload).decode()
                    cache[key]=uri;uris[index]=uri
                except (ValueError,OSError,SyntaxError,zipfile.BadZipFile) as exc:
                    message=str(exc) if str(exc).startswith('资源') else '资源损坏或格式不受支持'
                    warnings.append(message+'：'+label[:200])
                    if key is not None:cache[key]=None
        def replace(match):
            index=int(match.group(1))
            uri=uris.get(index)
            if not uri:return '<span>'+html.escape('资源不可用，请查看具体警告')+'</span>'
            tag='video controls' if uri.startswith('data:video/') else 'img'
            return '<'+tag+' src="'+html.escape(uri,quote=True)+'">'+('</video>' if tag.startswith('video') else '')
        rendered=re.sub(r'<span class="source-image" data-image-index="(\d+)">.*?</span>',replace,rendered,flags=re.S)
        return dict(html=rendered,warnings=warnings[:100])

    def publish(self,project,identifier,owner,ids,versions,result_versions,settings,key,settings_version=None,independent_item_ids=None):
        independent_item_ids=independent_item_ids or []
        with self.store.guard(project):
            existing=self._get(project,identifier,owner)
            if settings_version!=existing.get('settings_version',0) and not (settings_version is None and existing.get('settings_version',0)==0):
                raise PreparationConflict('入库设置版本冲突，请刷新后确认')
            settings={**existing.get('publication_settings',{}),**settings}
            prior=existing['operations'].get('publish:'+key)
            digest=hashlib.sha256(encoded([identifier,[ids,versions,result_versions,settings,independent_item_ids]]).encode()).hexdigest()
            if prior:
                if prior['digest']!=digest:raise PreparationConflict('幂等键与已有操作冲突')
                return {**prior['response'],'session':self.public(existing)}
        # Parsing/rechunking happens before the publication transaction.
        results={i:self.result(project,identifier,owner,i) for i in ids}
        with self.store.guard(project):
            session=self._get(project,identifier,owner)
            if session.get('publication_settings')!=existing.get('publication_settings'):
                raise PreparationConflict('入库设置已变化，请刷新后确认')
            if session.get('settings_version',0)!=existing.get('settings_version',0):
                raise PreparationConflict('入库设置版本冲突，请刷新后确认')
            token,digest,prior=self._operation(session,'publish',key,[ids,versions,result_versions,settings,independent_item_ids])
            if prior:return {**prior['response'],'session':self.public(session)}
            selected=[self.item(session,i) for i in ids]
            for item in selected:
                self._version(item,versions.get(item['id']))
                live=self.public_item(item)
                task=self.store.task(item.get('task_id')) if item.get('task_id') else None
                if (not live['result_ready'] or live['status'] not in ('completed','reused','published') or not task
                        or task['id']!=results[item['id']]['parse_task_id'] or not task.get('result')
                        or task['result']['source_version_id']!=results[item['id']]['source_version_id']):
                    raise PreparationConflict('所选文件已取消、正在更新或结果已变化：'+item['id'])
                if result_versions.get(item['id'])!=item['chunk_version']:raise PreparationConflict('切片版本冲突：'+item['id'])
                if results[item['id']]['chunk_version']!=item['chunk_version']:raise PreparationConflict('结果生成期间配置已变化，请刷新')
                snapshot=hashlib.sha256(encoded([item['metadata_version'],item['chunk_version'],settings]).encode()).hexdigest()
                if item.get('batch_id') and item.get('published_snapshot')!=snapshot:
                    raise PreparationConflict('该文件已入库；请从资料详情明确修订，或作为新的独立文件上传')
            pending=[i for i in selected if not i.get('batch_id')]
            seen=set()
            duplicates=[]
            for item in pending:
                existing_docs=self.store.existing_documents(project,item['file_sha256'])
                if (existing_docs or item['file_sha256'] in seen) and item['id'] not in independent_item_ids:
                    duplicates.append({'item_id':item['id'],'code':'explicit_independent_required','existing_documents':existing_docs})
                seen.add(item['file_sha256'])
            if duplicates:raise PreparationConflict('存在相同内容的已提交资料或本次重复文件，请复用已有资料，或明确选择作为独立资料入库',duplicates)
            if pending:
                batch=self.batches.create(project,IngestBatchSettings.model_validate(settings),owner)
                for item in pending:
                    config={k:v for k,v in item['options'].items() if k!='parse_config'}
                    publication_fields={'extraction_mode','ontology_id','extract_attributes','attribute_max_per_entity',
                        'attribute_max_total','relation_constraint_mode','resolve_entities','auto_merge','review_threshold','merge_threshold'}
                    config.update({k:v for k,v in settings.items() if k in publication_fields})
                    UploadOptions.model_validate(config)
                    row=self.batches.store.add_item(project,batch['id'],item['original_name'],self.store.file(item['id']),item['file_sha256'],
                        'preparation:'+item['id'],self.batches.limits.max_batch_items,self.batches.limits.max_batch_bytes,options=config)
                    parsed=results[item['id']]
                    source,mapping=self.sources.store.clone_version(project,parsed['source_version_id'],row['document_id'],original_name=item['original_name'])
                    def remap(value):
                        if isinstance(value,str):return mapping.get(value,value)
                        if isinstance(value,list):return [remap(part) for part in value]
                        if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
                        return value
                    result=remap(parsed)
                    result['source_version_id']=source['id']
                    self.batches.store.set_source(project,batch['id'],row['id'],result['source_version_id'])
                    self.store.db.execute('UPDATE ingest_batch_items SET prepared_result=?::jsonb WHERE id=?',(encoded(result),row['id']))
                    item.update(batch_id=batch['id'],status='published',published_snapshot=hashlib.sha256(encoded([item['metadata_version'],item['chunk_version'],settings]).encode()).hexdigest())
                self.batches.control(project,batch['id'],'start')
            batch_ids=list(dict.fromkeys(i['batch_id'] for i in selected))
            response=dict(batch_id=batch_ids[0],batch_ids=batch_ids,items=[self.public_item(i) for i in selected])
            session['operations'][token]={'digest':digest,'response':response}
            self.store.save(session)
            return {**response,'session':self.public(session)}
