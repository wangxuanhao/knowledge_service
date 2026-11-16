"""Read-only fixture inspection; never upload, OCR, transcribe or write knowledge.

Run from the repository root with the existing llm_model Python environment.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from knowledge_service.integrations.document_package import SingleDocumentPackageReader
from knowledge_service.integrations.document_parser import DocumentParser
from knowledge_service.integrations.video_asr import inspect_video


def inspect_pdf(path):
    import fitz
    with fitz.open(path) as document:
        if document.needs_pass:
            return {'status': 'blocked', 'reason': 'encrypted_pdf'}
        pages, repeated = [], collections.Counter()
        for number, page in enumerate(document, 1):
            text = page.get_text().strip()
            repeated.update(set(line.strip() for line in text.splitlines() if line.strip()))
            coverage = [fitz.Rect(image['bbox']).intersect(page.rect).get_area() /
                        page.rect.get_area() for image in page.get_image_info()]
            pages.append({'page': number, 'text_chars': len(text),
                          'largest_image_coverage': round(max(coverage, default=0), 3),
                          'images': len(coverage)})
        common = {line for line, count in repeated.items()
                  if count >= max(2, int(len(document) * .8))}
        for row, page in zip(pages, document):
            meaningful = '\n'.join(line for line in page.get_text().splitlines()
                                   if line.strip() not in common).strip()
            row['nonrepeated_text_chars'] = len(meaningful)
            # A diagnostic heuristic, not a substitute for region OCR/visual QA.
            row['ocr_candidate'] = (row['images'] > 0 and
                                    row['largest_image_coverage'] > .1 and len(meaningful) < 100)
        return {'status': 'structurally_readable', 'pages': len(document),
                'text_chars': sum(row['text_chars'] for row in pages),
                'ocr_candidate_pages': [row['page'] for row in pages if row['ocr_candidate']],
                'page_checks': pages}


def inspect_file(path):
    raw = path.read_bytes()
    result = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(raw),
              'sha256': hashlib.sha256(raw).hexdigest()}
    negative = 'invalid' in path.relative_to(Path(__file__).parent).parts
    is_legacy_word = False
    if raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')) and path.suffix.lower() == '.doc':
        import olefile
        with olefile.OleFileIO(path) as container:
            is_legacy_word = container.exists('WordDocument')
        result['has_word_stream'] = is_legacy_word
    try:
        if path.suffix.lower() == '.pdf':
            result.update(inspect_pdf(path))
        elif path.suffix.lower() == '.zip':
            import zipfile
            with zipfile.ZipFile(path) as archive:
                has_manifest = any(name == 'manifest.json' or name.endswith('/manifest.json')
                                   for name in archive.namelist())
            package = SingleDocumentPackageReader().read(raw, path.name)
            main = package['documents'][0]
            result.update(status='package_parser_passed', has_manifest=has_manifest,
                          document_count=len(package['documents']), main=main['path'],
                          asset_count=len(main['assets']), warning_count=len(package['warnings']))
        elif path.suffix.lower() in {'.mp4', '.webm'}:
            result.update(status='media_probe_passed', **inspect_video(raw))
        elif path.suffix.lower() in {'.md', '.txt', '.png', '.jpg', '.jpeg', '.webp', '.doc', '.docx'}:
            parsed = DocumentParser().parse(path, path.name)
            result.update(status='document_parser_passed', text_chars=len(parsed.text),
                          asset_count=len(parsed.assets))
        elif path.suffix.lower() in {'.html', '.htm'}:
            text = raw.decode('utf-8-sig')
            result.update(status='utf8_html_readable', text_chars=len(text),
                          note='Full HTML/media parsing is validated through the matching resource ZIP.')
        else:
            return None
        if negative:
            result.update(status='unexpected_acceptance', unexpected=True)
    except Exception as error:
        expected = is_legacy_word and getattr(error, 'code', None) == 'unsupported_format'
        if negative and path.name == 'two-main-documents.zip':
            expected = isinstance(error, ValueError) and 'one root main file' in str(error)
        if negative and path.name == 'fake.pdf':
            import fitz
            expected = isinstance(error, fitz.FileDataError)
        result.update(status='expected_rejection' if expected else 'validation_failed',
                      error_code=getattr(error, 'code', type(error).__name__), reason=str(error),
                      unexpected=not expected)
        if is_legacy_word:
            result['note'] = 'Valid legacy Word container; current product supports DOCX, not DOC.'
    return result


def main():
    files = [inspect_file(path) for path in sorted(Path(__file__).parent.rglob('*'))
             if path.is_file() and path.name not in {'README.md', 'validation-report.json'}]
    files = [row for row in files if row is not None]
    report = {'scope': 'Structural and current parser checks only; no full-PDF OCR, ASR, upload or indexing.',
              'files': files, 'unexpected_failures': sum(bool(row.get('unexpected')) for row in files)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report['unexpected_failures']))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
