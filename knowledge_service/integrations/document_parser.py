"""One boundary for uploaded document parsing and provenance normalization."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from typing import Any, Callable


MAX_UPLOAD_BYTES = 25_000_000
MAX_TEXT_CHARS = 1_000_000
TEXT_EXTENSIONS = {'.txt', '.md'}
DOCLING_EXTENSIONS = {'.pdf', '.docx', '.html', '.htm'}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCLING_EXTENSIONS


class DocumentParseError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    metadata: dict[str, Any]


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _value(item: Any, *names: str, default=None):
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return default
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return default


class DocumentParser:
    def __init__(self, ocr_mode: str | None = None, parser_factory: Callable[..., Any] | None = None):
        self.ocr_mode = ocr_mode if ocr_mode is not None else os.environ.get('KG_DOCUMENT_OCR_MODE', 'auto')
        if self.ocr_mode not in {'auto', 'disabled'}:
            raise ValueError('KG_DOCUMENT_OCR_MODE 必须为 auto 或 disabled')
        self._parser_factory = parser_factory

    def parse(self, path: str | Path, original_name: str, *, size: int | None = None,
              digest: str | None = None) -> ParsedDocument:
        source = Path(path)
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise DocumentParseError('unsupported_format', '仅支持 TXT、Markdown、PDF、DOCX、HTML 文件')
        actual_size = source.stat().st_size if size is None else size
        if actual_size > MAX_UPLOAD_BYTES:
            raise DocumentParseError('file_too_large', '单个文件不能超过 25 MB')
        checksum = digest or sha256(source.read_bytes()).hexdigest()

        if extension in TEXT_EXTENSIONS:
            try:
                text = source.read_bytes().decode('utf-8-sig')
            except UnicodeError as exc:
                raise DocumentParseError('parse_failed', 'TXT/Markdown 文件必须使用 UTF-8 编码') from exc
            structural = {
                'document_parser': 'builtin-text',
                'parser_version': '1',
                'page_count': None,
                'table_count': 0,
                'page_summaries': [],
                'table_summaries': [],
                'ocr_mode': 'not_applicable',
                'warnings': [],
            }
        else:
            text, structural = self._parse_docling(source)

        if not text or not text.strip():
            raise DocumentParseError('empty_document', '文档解析后没有可处理的正文')
        if len(text) > MAX_TEXT_CHARS:
            raise DocumentParseError('text_too_large', '解析后的正文超过 100 万字符限制')
        metadata = {
            'source_file': Path(original_name).name,
            'source_format': extension.lstrip('.'),
            'source_size_bytes': actual_size,
            'source_sha256': checksum,
            **structural,
        }
        return ParsedDocument(text=text, metadata=metadata)

    def _factory(self):
        if self._parser_factory is not None:
            return self._parser_factory
        try:
            from semantica.parse import DoclingParser
        except (ImportError, ModuleNotFoundError) as exc:
            raise DocumentParseError(
                'model_unavailable',
                '未安装 Semantica Docling 解析能力，请安装项目的 semantica-runtime 依赖',
                503,
            ) from exc
        return DoclingParser

    def _new_docling_parser(self):
        parser = self._factory()(export_format='markdown', enable_ocr=self.ocr_mode == 'auto')
        # Semantica 0.7.0 forwards enable_ocr=True, but its False branch creates
        # Docling's default converter; Docling 2.130 defaults PDF OCR back to True.
        # Seed the lazy converter explicitly so the public disabled setting is real.
        if self.ocr_mode == 'disabled' and self._parser_factory is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            options = PdfPipelineOptions()
            options.do_ocr = False
            parser._converter = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            })
        return parser

    def _parse_docling(self, path: Path):
        try:
            parser = self._new_docling_parser()
            result = parser.parse(str(path))
        except DocumentParseError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if 'encrypt' in message or 'password' in message:
                code, public = 'encrypted_document', '加密或受密码保护的文档无法解析'
            elif 'model' in message and any(word in message for word in ('missing', 'download', 'not found', 'unavailable')):
                code, public = 'model_unavailable', 'Docling 所需的本地模型不可用'
            elif 'ocr' in message and self.ocr_mode == 'disabled':
                code, public = 'ocr_required', '该文档需要 OCR，请将 KG_DOCUMENT_OCR_MODE 设为 auto'
            else:
                code, public = 'parse_failed', f'文档解析失败：{type(exc).__name__}'
            raise DocumentParseError(code, public, 503 if code == 'model_unavailable' else 422) from exc

        text = _value(result, 'full_text', 'text', default='') or ''
        pages = list(_value(result, 'pages', default=[]) or [])
        tables = list(_value(result, 'tables', default=[]) or [])
        raw_metadata = _value(result, 'metadata', default={}) or {}
        warnings = _value(raw_metadata, 'warnings', default=[]) or _value(result, 'warnings', default=[]) or []
        if isinstance(warnings, str):
            warnings = [warnings]
        page_summaries = []
        for index, page in enumerate(pages[:200]):
            preview = str(_value(page, 'text', 'content', default='') or '')[:200]
            page_summaries.append({'number': _value(page, 'page_no', 'page_number', 'number', default=index + 1),
                                   'preview': preview})
        table_summaries = []
        for table in tables[:100]:
            data = _value(table, 'data', 'rows', default=[]) or []
            rows = list(data) if not isinstance(data, str) else []
            columns = max((len(row) if isinstance(row, (list, tuple)) else 0 for row in rows), default=0)
            table_summaries.append({
                'page': _value(table, 'page_no', 'page_number', 'page'),
                'row_count': len(rows),
                'column_count': columns,
            })
        total_pages = _value(result, 'total_pages', 'page_count', default=None)
        if total_pages is None and pages:
            total_pages = len(pages)
        return text, {
            'document_parser': 'semantica-docling',
            'semantica_version': _package_version('semantica'),
            'docling_version': _package_version('docling'),
            'page_count': total_pages,
            'table_count': len(tables),
            'page_summaries': page_summaries,
            'page_summaries_truncated': len(pages) > 200,
            'table_summaries': table_summaries,
            'table_summaries_truncated': len(tables) > 100,
            'ocr_mode': self.ocr_mode,
            'warnings': [str(item)[:500] for item in warnings[:100]],
        }
