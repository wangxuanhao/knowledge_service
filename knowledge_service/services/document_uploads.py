"""Safe staging and conversion of multipart uploads into Ingest payloads."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

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

    def discard(self, staged: StagedUpload):
        staged.path.unlink(missing_ok=True)

    def cleanup_old(self, max_age_seconds: int = 24 * 60 * 60):
        cutoff = time.time() - max_age_seconds
        for path in self.root.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
