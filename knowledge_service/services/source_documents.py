"""Fixed-version source application service; all reads require project ownership."""
import mimetypes
import os
import html
import hashlib
import logging
import re
import time
from uuid import uuid4
from collections import defaultdict,deque
from urllib.parse import quote
from ..integrations.markdown_parser import parse_markdown
from ..integrations.markdown_package import MarkdownPackageReader
from ..integrations.source_object_store import source_object_store_from_environment
from ..integrations.image_ocr import OCRError as SourceOCRError
from ..repository.source_store import SourceStore
from .image_ocr import ImageOCRService


LOG = logging.getLogger(__name__)


class SourceDocumentUnavailable(RuntimeError):
    """Frozen source bytes are missing and cannot be reconstructed exactly."""


class SourceDocumentService:
    @classmethod
    def for_service(cls, service):
        return cls(service.repository, source_object_store_from_environment())

    def __init__(self, repo, object_store, ocr_adapter=None, remote_image_importer=None):
        self.store=SourceStore(repo)
        self.object_store=object_store
        self.image_ocr=ImageOCRService(self.store,object_store,ocr_adapter)
        self.remote_image_importer=remote_image_importer

    def persist(self,project_id,document_id,data,original_name,text=None,assets=None,metadata=None,provenance=None):
        prepared=self.prepare(data,original_name,text,assets,metadata,provenance)
        return self.publish(project_id,document_id,prepared)

    def prepare(self,data,original_name,text=None,assets=None,metadata=None,provenance=None):
        """Parse and fetch before opening any publication transaction."""
        version_id=str(uuid4())
        text=data.decode('utf-8-sig') if text is None else text
        parsed=parse_markdown(text,version_id)
        parsed['document_version_id']=version_id
        parsed['metadata']={**(metadata or {}),'provenance':provenance or {}}
        parsed['metadata']['warnings']=list(parsed['metadata'].get('warnings',[]))
        supplied=list(assets or [])
        refs=[];used=set()
        by_path=defaultdict(deque);first_by_path={}
        for index,asset in enumerate(supplied):
            for key in {asset.get('original_path'),asset.get('path'),asset.get('url')} - {None}:
                by_path[key].append(index);first_by_path.setdefault(key,index)
        # Merge payload bytes into parsed occurrences so line mapping and duplicate
        # image references retain article order, including two images on one line.
        for ref in parsed['images']:
            key=ref.get('path',ref['url']);queue=by_path[key]
            while queue and queue[0] in used:queue.popleft()
            index=queue.popleft() if queue else first_by_path.get(key)
            if index is not None:
                asset=supplied[index];used.add(index);refs.append({**ref,**asset,'alt':ref.get('alt') or asset.get('alt',''),'source_start_line':ref['source_start_line'],'source_end_line':ref['source_end_line']})
            else:refs.append(ref)
        refs.extend(a for i,a in enumerate(supplied) if i not in used)
        prepared=[]
        remote_cache={};total_image_bytes=0;remote_deadline=time.monotonic()+15
        for ordinal,ref in enumerate(refs):
            asset=dict(ref,id=str(uuid4()))
            asset['provenance']={**asset.get('provenance',{}),'source_image_ordinal':ordinal}
            if ordinal>=200:
                asset.pop('data',None)
                if asset['kind']!='unsafe':asset['status']='missing'
                parsed['metadata']['warnings'].append('图片数量超过文档上限：'+asset.get('original_path',asset.get('url',''))[:300])
                prepared.append(asset)
                continue
            if asset['kind']=='external':
                url=asset.get('url',asset.get('original_path',''))
                asset['provenance']={**asset.get('provenance',{}),'original_url':url}
                enabled=os.getenv('KG_SOURCE_REMOTE_IMAGES_ENABLED','true').lower() in {'1','true','yes'}
                if enabled or self.remote_image_importer is not None:
                    from ..integrations.remote_images import RemoteImageImporter, RemoteImageError
                    if url not in remote_cache:
                        try:
                            remaining=remote_deadline-time.monotonic()
                            if len(remote_cache)>=20 or remaining<=0:raise RemoteImageError('Document remote image import limit exceeded')
                            remote_cache[url]=(self.remote_image_importer or RemoteImageImporter(timeout=min(5,remaining))).fetch(url)
                        except RemoteImageError:remote_cache[url]=None
                    imported=remote_cache[url]
                    if imported:
                        asset.update(imported,kind='local',status='available',original_path=url,
                            provenance={**asset['provenance'],**imported.get('provenance',{})})
                    else:
                        asset['status']='missing'
                        parsed['metadata']['warnings'].append('远程图片导入失败：'+url[:300])
                else:parsed['metadata']['warnings'].append('远程图片导入已关闭：'+url[:300])
            if 'data' in asset:
                mime=asset.get('mime_type') or mimetypes.guess_type(asset.get('original_path',asset.get('path','')))[0] or 'application/octet-stream'
                from ..integrations.document_media import inspect_image
                try:
                    details=inspect_image(asset['data'],None if mime=='application/octet-stream' else mime)
                    total_image_bytes+=len(asset['data'])
                    if total_image_bytes>100*1024*1024:raise ValueError('Document image limit exceeded')
                    asset.update(details)
                except ImportError as exc:
                    from ..integrations.document_parser import DocumentParseError
                    raise DocumentParseError('model_unavailable','未安装文档图片解析能力，请安装文档解析依赖',503) from exc
                except (ValueError,OSError):
                    asset.pop('data',None);asset['status']='missing'
                    parsed['metadata']['warnings'].append('图片不可用或超过限制：'+asset.get('original_path',asset.get('url',''))[:300])
            elif asset['kind']=='local': asset['status']='missing'
            prepared.append(asset)
        parsed['metadata']['warnings']=parsed['metadata']['warnings'][:100]
        markdown_name=original_name.lower().endswith(('.md','.markdown'))
        mime='application/zip' if markdown_name and data.startswith(b'PK\x03\x04') else mimetypes.guess_type(original_name)[0] or 'application/octet-stream'
        return dict(data=data,original_name=original_name,mime_type=mime,parsed=parsed,assets=prepared)

    def publish(self,project_id,document_id,prepared):
        # GC and byte publication share this lock; network/image decoding never do.
        with self.store.publication_guard():
            assets=[]
            for ref in prepared['assets']:
                asset=dict(ref)
                if 'data' in asset:asset['object']=self.object_store.put(asset.pop('data'),asset['mime_type'])
                assets.append(asset)
            obj=self.object_store.put(prepared['data'],prepared['mime_type'])
            return self.store.create_version(project_id,document_id,obj,prepared['original_name'],prepared['parsed'],assets)

    def persist_package(self,project_id,data,original_name,document_id_factory=None):
        package=MarkdownPackageReader().read(data,original_name)
        # Each fixed Markdown has its own download name. Object bytes retain the full ZIP.
        return [self.persist(project_id,document_id_factory(entry) if document_id_factory else str(uuid4()),data,entry['path'],text=entry['text'],assets=entry['assets']) for entry in package['documents']]

    def get(self,project_id,version_id): return self.store.get_version(project_id,version_id)
    def rendered(self,project_id,version_id):
        version=self.get(project_id,version_id)
        assets=sorted(version['assets'],key=lambda a:(a.get('provenance',{}).get('source_image_ordinal',1_000_000),a.get('source_start_line') or 0,a['id']))
        # Keep same source order for multiple images on a line via stored parsed image URL.
        ordered=[]
        for block in version['blocks']:
            for ref in block['payload'].get('images',[]):
                match=next((a for a in assets if a not in ordered and a['original_path'] in {ref.get('path'),ref.get('url')}),None)
                if match: ordered.append(match)
        def replace(match):
            index=int(match.group(1))
            if index>=len(ordered): return match.group(0)
            asset=ordered[index]
            if asset.get('source_object_id'):
                url=f'/api/projects/{quote(project_id,safe="")}/source-documents/{quote(version_id,safe="")}/assets/{quote(asset["id"],safe="")}'
                width,height=asset.get('width'),asset.get('height')
                dimensions=f' width="{width}" height="{height}"' if all(type(value) is int and 0<value<=1_000_000 for value in (width,height)) else ''
                return '<img data-asset-id="'+html.escape(asset['id'],quote=True)+'" src="'+html.escape(url,quote=True)+'" alt="'+html.escape(asset['alt_text'],quote=True)+'"'+dimensions+'>'
            return '<span data-asset-id="'+html.escape(asset['id'],quote=True)+'">'+('外部图片，未固化' if asset['kind']=='external' else '图片缺失')+'</span>'
        rendered=re.sub(r'<span class="source-image" data-image-index="(\d+)">.*?</span>',replace,version['rendered_html'],flags=re.S)
        return dict(document_version_id=version_id,html=rendered,blocks=version['blocks'],assets=version['assets'])
    def source(self,project_id,version_id):
        version=self.get(project_id,version_id)
        try:
            original=self.object_store.read(version['storage_key'])
        except FileNotFoundError as exc:
            # The database retains frozen Markdown text. Serve it only when its
            # bytes reproduce the original object's checksum, including a BOM.
            original=None
            if version['format']=='markdown':
                for encoding in ('utf-8','utf-8-sig'):
                    candidate=version['source_text'].encode(encoding)
                    if hashlib.sha256(candidate).hexdigest()==version['sha256']:
                        original=candidate
                        break
            if original is None:
                LOG.warning('Frozen source object is missing and cannot be reconstructed: version=%s sha256=%s',version_id,version['sha256'])
                raise SourceDocumentUnavailable('来源原始文件缺失，请恢复来源存储后重试') from exc
            LOG.warning('Serving checksum-verified frozen Markdown because source object is missing: version=%s sha256=%s',version_id,version['sha256'])
        if version['format']=='markdown' and original.startswith(b'PK\x03\x04'):
            # Raw reading and source download represent the selected Markdown, not binary ZIP.
            return dict(text=version['source_text'],original_name=version['original_name'],mime_type='text/markdown; charset=utf-8',data=version['source_text'].encode('utf-8'),package_data=original,package_mime_type='application/zip')
        return dict(text=version['source_text'],original_name=version['original_name'],mime_type=version['mime_type'],data=original)
    def asset(self,project_id,version_id,asset_id):
        asset=self.store.get_asset(project_id,version_id,asset_id)
        if not asset.get('storage_key'): raise KeyError('Image is not stored locally')
        return dict(asset,data=self.object_store.read(asset['storage_key']))
    def ocr(self,project_id,version_id,asset_id):
        runs=self.store.ocr_runs(project_id,version_id,asset_id)
        run=runs[-1] if runs else None
        return dict(runs=runs,run=run,blocks=run['blocks'] if run else [])
    def locate(self,project_id,version_id,chunk_version_id=None,assertion_id=None): return self.store.locate(project_id,version_id,chunk_version_id,assertion_id)

    def collect_unreferenced_objects(self):
        return self.store.collect_unreferenced_objects(self.object_store)
