"""Bounded offline PDF page rasterization in a fresh, short-lived process.

PDFium has process-global native state. Never share it with Docling's worker
threads: only the isolated child imports pypdfium2 or opens a PDF document.
"""
import base64
from contextlib import closing
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGE_NUMBER = 10_000
MAX_EDGE = 1800
MAX_PIXELS = 3_000_000
TIMEOUT_SECONDS = 12
_RENDER_SLOTS = threading.BoundedSemaphore(2)


class PDFPreviewError(ValueError):
    def __init__(self, code, message, status_code=422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def render_pdf_page(data: bytes, page_number: int = 1) -> dict:
    """Return one PNG and native page geometry without modifying source bytes."""
    if type(page_number) is not int or not 1 <= page_number <= MAX_PAGE_NUMBER:
        raise PDFPreviewError('pdf_page_out_of_range', 'PDF 页码超出允许范围')
    if len(data) > MAX_PDF_BYTES:
        raise PDFPreviewError('pdf_too_large', 'PDF 超过 25 MB 页面预览上限，请下载原件查看', 413)
    if not data.lstrip().startswith(b'%PDF-'):
        raise PDFPreviewError('invalid_pdf', 'PDF 原件无效或无法读取')
    if not _RENDER_SLOTS.acquire(timeout=.1):
        raise PDFPreviewError('pdf_preview_busy', 'PDF 页面预览繁忙，请稍后重试', 503)
    try:
        try:
            result = subprocess.run(
                [sys.executable, '-I', str(Path(__file__).resolve()), str(page_number)],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=TIMEOUT_SECONDS, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills and reaps the native child before raising.
            raise PDFPreviewError('pdf_preview_timeout', 'PDF 页面预览超时，请下载原件查看', 504) from exc
        except OSError as exc:
            raise PDFPreviewError('pdf_preview_unavailable', 'PDF 页面预览暂不可用', 503) from exc
        if result.returncode or len(result.stdout) > 20 * 1024 * 1024:
            raise PDFPreviewError('invalid_pdf', 'PDF 原件无法生成页面预览')
        try:
            page = json.loads(result.stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PDFPreviewError('invalid_pdf', 'PDF 原件无法生成页面预览') from exc
        if 'error' in page:
            raise PDFPreviewError(page['error'], 'PDF 页码超出原件页数' if page['error'] == 'pdf_page_out_of_range'
                                  else 'PDF 原件无法生成页面预览')
        return page
    finally:
        _RENDER_SLOTS.release()


def _render_child(data, page_number):
    import pypdfium2 as pdfium

    if len(data) > MAX_PDF_BYTES:
        raise ValueError('PDF exceeds byte limit')
    with closing(pdfium.PdfDocument(data)) as document:
        count = len(document)
        if count > MAX_PAGE_NUMBER or not 1 <= page_number <= count:
            return {'error': 'pdf_page_out_of_range'}
        with closing(document[page_number - 1]) as page:
            width, height = page.get_size()
            if not all(math.isfinite(value) and 0 < value <= 1_000_000 for value in (width, height)):
                raise ValueError('Invalid PDF page geometry')
            # Ceil-rounded bitmap dimensions must stay within both limits.
            scale = min(2, (MAX_EDGE - 1) / max(width, height),
                        math.sqrt((MAX_PIXELS - 2 * MAX_EDGE) / (width * height)))
            with closing(page.render(scale=scale)) as bitmap:
                image = bitmap.to_pil().convert('RGB')
                if max(image.size) > MAX_EDGE or image.width * image.height > MAX_PIXELS:
                    raise ValueError('PDF bitmap exceeds preview limit')
                output = io.BytesIO()
                image.save(output, 'PNG')
                return {'page_number': page_number, 'page_count': count,
                        'page_width': width, 'page_height': height,
                        'width': image.width, 'height': image.height, 'mime_type': 'image/png',
                        'image_base64': base64.b64encode(output.getvalue()).decode('ascii')}


if __name__ == '__main__':
    try:
        response = _render_child(sys.stdin.buffer.read(MAX_PDF_BYTES + 1), int(sys.argv[1]))
    except Exception:
        # Never return parser exceptions or source text through the public API.
        response = {'error': 'invalid_pdf'}
    sys.stdout.buffer.write(json.dumps(response, separators=(',', ':')).encode('ascii'))
