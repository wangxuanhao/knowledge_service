"""One boundary for uploaded document parsing and provenance normalization."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    assets: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


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
    def __init__(self, ocr_mode: str | None = None, parser_factory: Callable[..., Any] | None = None,
                 max_upload_bytes: int = MAX_UPLOAD_BYTES):
        self.ocr_mode = ocr_mode if ocr_mode is not None else os.environ.get('KG_DOCUMENT_OCR_MODE', 'auto')
        if self.ocr_mode not in {'auto', 'disabled'}:
            raise ValueError('KG_DOCUMENT_OCR_MODE 必须为 auto 或 disabled')
        self._parser_factory = parser_factory
        if max_upload_bytes<1:raise ValueError('max_upload_bytes must be positive')
        self.max_upload_bytes=max_upload_bytes

    def parse(self, path: str | Path, original_name: str, *, size: int | None = None,
              digest: str | None = None) -> ParsedDocument:
        source = Path(path)
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise DocumentParseError('unsupported_format', '仅支持 TXT、Markdown、PDF、DOCX、HTML 文件')
        actual_size = source.stat().st_size if size is None else size
        if actual_size > self.max_upload_bytes:
            raise DocumentParseError('file_too_large', f'单个文件超过服务端上限（{self.max_upload_bytes} 字节）')
        checksum = digest or sha256(source.read_bytes()).hexdigest()

        assets,provenance=[],{}
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
            text, structural, assets, provenance = self._parse_docling(source)

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
        return ParsedDocument(text=text, metadata=metadata,assets=assets,provenance=provenance)

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

    def _new_docling_parser(self, extension=None):
        if self._parser_factory is not None:
            return self._factory()(export_format='markdown', enable_ocr=self.ocr_mode == 'auto')
        # Semantica 0.7.0 forwards enable_ocr=True, but its False branch creates
        # Docling's default converter; Docling 2.130 defaults PDF OCR back to True.
        # Seed the lazy converter explicitly so the public disabled setting is real.
        if self._parser_factory is None:
            from types import SimpleNamespace
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            options = PdfPipelineOptions()
            options.do_ocr = self.ocr_mode=='auto'
            options.generate_picture_images = True
            options.generate_table_images = True
            options.generate_page_images = True
            options.generate_parsed_pages = True
            options.enable_remote_services = False
            if extension=='.pdf':
                root=document_artifacts_path(require_ocr=options.do_ocr)
                options.artifacts_path=root
                if options.do_ocr:
                    options.ocr_options=RapidOcrOptions(backend='onnxruntime',
                        det_model_path=str(root/'RapidOcr/PP-OCRv6_det_small.onnx'),
                        rec_model_path=str(root/'RapidOcr/PP-OCRv6_rec_small.onnx'),
                        cls_model_path=str(root/'RapidOcr/ch_ppocr_mobile_v2.0_cls_mobile.onnx'),
                        rec_keys_path=str(root/'RapidOcr/ppocrv6_dict.txt'))
            parser=SimpleNamespace(_converter=DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            }))
        return parser

    def _parse_docling(self, path: Path):
        try:
            parser = self._new_docling_parser(path.suffix.lower())
            if self._parser_factory is None:
                conversion=parser._converter.convert(str(path))
                document=conversion.document
                result={'document':document,'total_pages':len(document.pages),'pages':list(document.pages.values()),
                        'tables':document.tables,'warnings':[str(error) for error in conversion.errors],
                        'scanned_pages':[page.page_no for page in conversion.pages if not any(cell.text.strip() and not cell.from_ocr for cell in page.cells)] if path.suffix.lower()=='.pdf' else [],
                        'ocr_regions':[dict(page_no=page.page_no,text=cell.text,bbox=cell.to_bounding_box().model_dump(mode='json')) for page in conversion.pages for cell in page.cells if cell.from_ocr],
                        'digital_regions':[dict(page_no=page.page_no,text=cell.text,bbox=cell.to_bounding_box().model_dump(mode='json')) for page in conversion.pages for cell in page.cells if not cell.from_ocr]}
            else:
                result = parser.parse(str(path))
        except DocumentParseError:
            raise
        except (ImportError,ModuleNotFoundError) as exc:
            raise DocumentParseError('model_unavailable','未安装 Docling 解析能力，请安装项目的 semantica-runtime 依赖',503) from exc
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
        assets,provenance=[],{}
        document=_value(result,'document','docling_document')
        media_warnings=[]
        if document is not None and hasattr(document,'export_to_markdown'):
            from .document_media import normalize_docling_document
            try:text,assets,provenance,media_warnings=normalize_docling_document(document,scanned_pages=_value(result,'scanned_pages',default=[]),ocr_regions=_value(result,'ocr_regions',default=[]),digital_regions=_value(result,'digital_regions',default=[]))
            except (ImportError,ModuleNotFoundError) as exc:
                raise DocumentParseError('model_unavailable','未安装文档图片解析能力',503) from exc
            except (ValueError,OSError) as exc:
                raise DocumentParseError('parse_failed','文档结构或图片解析失败') from exc
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
                'row_count': _value(data,'num_rows',default=len(rows)),
                'column_count': _value(data,'num_cols',default=columns),
            })
        total_pages = _value(result, 'total_pages', 'page_count', default=None)
        if total_pages is None and pages:
            total_pages = len(pages)
        return text, {
            'document_parser': 'docling-native' if self._parser_factory is None else 'semantica-docling',
            'semantica_version': _package_version('semantica'),
            'docling_version': _package_version('docling'),
            'page_count': total_pages,
            'table_count': len(tables),
            'page_summaries': page_summaries,
            'page_summaries_truncated': len(pages) > 200,
            'table_summaries': table_summaries,
            'table_summaries_truncated': len(tables) > 100,
            'ocr_mode': self.ocr_mode,
            'image_count':len(assets),
            'warnings': [str(item)[:500] for item in [*warnings,*media_warnings][:100]],
        },assets,provenance


def document_artifacts_path(*,require_ocr=False):
    """Only validated local files reach PDF pipelines; requests never download weights."""
    default=Path(__file__).resolve().parents[2]/'data/model/document-parser/docling'
    configured=Path(os.getenv('KG_DOCUMENT_ARTIFACTS_PATH',str(default))).expanduser()
    root=(Path(__file__).resolve().parents[2]/configured).resolve() if not configured.is_absolute() else configured.resolve()
    required=['docling-project--docling-layout-heron/config.json',
              'docling-project--docling-layout-heron/preprocessor_config.json',
              'docling-project--docling-layout-heron/model.safetensors',
              'docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json',
              'docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors']
    if require_ocr: required.extend('RapidOcr/'+name for name in ['PP-OCRv6_det_small.onnx','PP-OCRv6_rec_small.onnx','ch_ppocr_mobile_v2.0_cls_mobile.onnx','ppocrv6_dict.txt'])
    if any(not (root/name).is_file() or (root/name).stat().st_size==0 for name in required):
        raise DocumentParseError('model_unavailable','Docling 所需的本地模型不可用，请运行 scripts/prepare_document_models.py',503)
    return root
