"""Fixed-version source application service; all reads require project ownership."""
import mimetypes
import os
import html
import hashlib
import logging
import re
from uuid import uuid4
from urllib.parse import quote
from ..integrations.markdown_parser import parse_markdown
from ..integrations.markdown_package import MarkdownPackageReader
from ..integrations.source_object_store import SourceObjectStore
from ..integrations.image_ocr import OCRError as SourceOCRError
from ..repository.source_store import SourceStore
from .image_ocr import ImageOCRService


LOG = logging.getLogger(__name__)


class SourceDocumentUnavailable(RuntimeError):
    """Frozen source bytes are missing and cannot be reconstructed exactly."""


class SourceDocumentService:
    @classmethod
    def for_service(cls, service):
        return cls(service.repository, SourceObjectStore(os.getenv('KG_SOURCE_OBJECT_ROOT', 'data/sources')))

    def __init__(self, repo, object_store, ocr_adapter=None):
        self.store=SourceStore(repo)
        self.object_store=object_store
        self.image_ocr=ImageOCRService(self.store,object_store,ocr_adapter)

    def persist(self,project_id,document_id,data,original_name,text=None,assets=None):
        # GC and source publication share a DB lock, including byte publication.
        with self.store.publication_guard():
            return self._persist(project_id,document_id,data,original_name,text,assets)

    def _persist(self,project_id,document_id,data,original_name,text=None,assets=None):
        version_id=str(uuid4())
        text=data.decode('utf-8-sig') if text is None else text
        parsed=parse_markdown(text,version_id)
        parsed['document_version_id']=version_id
        refs=assets if assets is not None else parsed['images']
        prepared=[]
        for ref in refs:
            asset=dict(ref,id=str(uuid4()))
            if 'data' in asset:
                mime=asset.get('mime_type') or mimetypes.guess_type(asset.get('original_path',asset.get('path','')))[0] or 'application/octet-stream'
                if mime not in {'image/png','image/jpeg','image/webp','image/gif','image/bmp','image/tiff','application/octet-stream'}:
                    raise ValueError('Unsupported source image MIME type')
                asset['object']=self.object_store.put(asset.pop('data'),mime)
            elif asset['kind']=='local': asset['status']='missing'
            prepared.append(asset)
        markdown_name=original_name.lower().endswith(('.md','.markdown'))
        mime='application/zip' if markdown_name and data.startswith(b'PK\x03\x04') else mimetypes.guess_type(original_name)[0] or 'application/octet-stream'
        obj=self.object_store.put(data,mime)
        return self.store.create_version(project_id,document_id,obj,original_name,parsed,prepared)

    def persist_package(self,project_id,data,original_name,document_id_factory=None):
        package=MarkdownPackageReader().read(data,original_name)
        # Each fixed Markdown has its own download name. Object bytes retain the full ZIP.
        return [self.persist(project_id,document_id_factory(entry) if document_id_factory else str(uuid4()),data,entry['path'],text=entry['text'],assets=entry['assets']) for entry in package['documents']]

    def get(self,project_id,version_id): return self.store.get_version(project_id,version_id)
    def rendered(self,project_id,version_id):
        version=self.get(project_id,version_id)
        assets=sorted(version['assets'],key=lambda a:(a.get('source_start_line') or 0,a['id']))
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
                return '<img data-asset-id="'+html.escape(asset['id'],quote=True)+'" src="'+html.escape(url,quote=True)+'" alt="'+html.escape(asset['alt_text'],quote=True)+'">'
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
