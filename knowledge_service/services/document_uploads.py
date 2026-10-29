"""Safe staging and conversion of multipart uploads into Ingest payloads."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4,uuid5,NAMESPACE_URL

from pydantic import ValidationError

from ..integrations.document_parser import (
    DocumentParseError,
    DocumentParser,
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
)
from ..models import UploadOptions


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    original_name: str
    size: int
    digest: str


class DocumentUploads:
    def __init__(self, root: str | Path | None = None, parser: DocumentParser | None = None):
        self.root = Path(root) if root is not None else Path(tempfile.gettempdir()) / 'knowledge-service-uploads'
        self.root.mkdir(parents=True, exist_ok=True)
        self.parser = parser or DocumentParser()

    def parse_options(self, raw: str) -> UploadOptions:
        try:
            return UploadOptions.model_validate_json(raw or '{}')
        except (ValidationError, ValueError) as exc:
            raise DocumentParseError('invalid_options', 'options 必须是符合上传契约的 JSON 对象') from exc

    def stage(self, upload) -> StagedUpload:
        original_name = Path(upload.filename or '').name
        extension = Path(original_name).suffix.lower()
        if not original_name or extension not in SUPPORTED_EXTENSIONS:
            raise DocumentParseError('unsupported_format', '仅支持 TXT、Markdown、PDF、DOCX、HTML 文件')
        target = self.root / f'{uuid4().hex}{extension}'
        digest = sha256()
        size = 0
        try:
            with target.open('xb') as handle:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise DocumentParseError('file_too_large', '单个文件不能超过 25 MB')
                    digest.update(chunk)
                    handle.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return StagedUpload(target, original_name, size, digest.hexdigest())

    def parse(self, staged: StagedUpload):
        return self.parser.parse(staged.path, staged.original_name, size=staged.size, digest=staged.digest)

    def payload(self, staged: StagedUpload, options: UploadOptions):
        parsed = self.parse(staged)
        title = (options.title or '').strip() or staged.original_name
        data = options.model_dump(exclude={'title'})
        data.update(title=title, text=parsed.text,
                    metadata={**options.metadata, **parsed.metadata})
        return data, parsed

    def ingest(self,service,project_id,staged,options,*,task_id=None,sources=None):
        """All committed file uploads publish originals, media and mapped spans."""
        from .source_documents import SourceDocumentService
        from .source_ingest import prepare_source_ingest
        sources=sources or SourceDocumentService.for_service(service)
        document_id=str(uuid5(NAMESPACE_URL,f'upload:{project_id}:{task_id}')) if task_id else str(uuid4())
        try:prior=service.repository.get_record(project_id,document_id)
        except KeyError:prior=None
        if prior and prior.get('metadata',{}).get('status')=='ready':
            return dict(document=prior,records=[],replayed=True)
        source_id=(prior or {}).get('metadata',{}).get('source_document_version_id')
        if source_id:
            source=sources.get(project_id,source_id)
            data=options.model_dump(exclude={'title'})
            data.update(title=(options.title or '').strip() or staged.original_name)
        else:
            data,parsed=self.payload(staged,options)
            source=sources.persist(project_id,document_id,staged.path.read_bytes(),staged.original_name,
                text=parsed.text,assets=parsed.assets,metadata=parsed.metadata,provenance=parsed.provenance)
        metadata={**data.get('metadata',{}),**{k:v for k,v in source.get('parser_metadata',{}).items() if k!='provenance'},
                  'source_document_version_id':source['id'],**({'durable_task_id':task_id} if task_id else {})}
        # Publish a reading receipt before OCR so a strict failure remains inspectable
        # and a durable retry reuses its immutable source instead of refetching media.
        service.repository.put_record(project_id,dict(id=document_id,kind='document',text=source['source_text'],
            metadata={**metadata,'title':data['title'],'status':'processing','active_stage':'ocr'}),expected_version=prior['version'] if prior else 0)
        chunks,text,warnings=prepare_source_ingest(sources,project_id,source,data)
        def map_chunks(saved):
            for row,chunk in zip(saved,chunks):
                if chunk.get('source_spans'):sources.store.add_spans(project_id,source['id'],row['version_id'],chunk['source_spans'])
        data.update(text=text,metadata={**metadata,'warnings':warnings},_document_id=document_id,
                    _prepared_chunks=chunks,_source_span_mapper=map_chunks)
        return service.ingest(project_id,data)

    def discard(self, staged: StagedUpload):
        staged.path.unlink(missing_ok=True)

    def cleanup_old(self, max_age_seconds: int = 24 * 60 * 60, *, protected_paths=()):
        cutoff = time.time() - max_age_seconds
        protected = {Path(path).resolve() for path in protected_paths}
        for path in self.root.iterdir():
            try:
                if path.resolve() not in protected and path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
