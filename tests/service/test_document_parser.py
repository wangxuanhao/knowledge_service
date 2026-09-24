from pathlib import Path

import pytest

from knowledge_service.integrations.document_parser import (
    DocumentParseError,
    DocumentParser,
)
from knowledge_service.models import UploadOptions


def test_upload_options_are_strict_and_title_is_optional():
    options = UploadOptions.model_validate({'title': ' ', 'extract': False})
    assert options.title == ' '
    with pytest.raises(ValueError):
        UploadOptions.model_validate({'unknown': True})


def test_text_files_preserve_utf8_and_publish_bounded_provenance(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_bytes(b'\xef\xbb\xbfhello\nworld')
    parsed = DocumentParser(ocr_mode='auto').parse(path, 'source.txt')

    assert parsed.text == 'hello\nworld'
    assert parsed.metadata['source_file'] == 'source.txt'
    assert parsed.metadata['source_format'] == 'txt'
    assert parsed.metadata['source_size_bytes'] == path.stat().st_size
    assert len(parsed.metadata['source_sha256']) == 64
    assert parsed.metadata['document_parser'] == 'builtin-text'
    assert parsed.metadata['ocr_mode'] == 'not_applicable'


def test_docling_formats_use_semantica_and_normalize_structure(tmp_path):
    calls = []

    class FakeDoclingParser:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def parse(self, path):
            calls.append(path)
            return {
                'full_text': '# Parsed\ncontent',
                'total_pages': 2,
                'pages': [{'page_no': 1, 'text': 'A' * 250}, {'page_no': 2, 'text': 'B'}],
                'tables': [{'page_no': 2, 'data': [['a', 'b'], ['1', '2']]}],
                'metadata': {'warnings': ['layout fallback']},
            }

    path = tmp_path / 'report.pdf'
    path.write_bytes(b'%PDF fixture')
    parsed = DocumentParser(ocr_mode='auto', parser_factory=FakeDoclingParser).parse(path, 'report.pdf')

    assert calls[0] == {'export_format': 'markdown', 'enable_ocr': True}
    assert calls[1] == str(path)
    assert parsed.text == '# Parsed\ncontent'
    assert parsed.metadata['page_count'] == 2
    assert parsed.metadata['table_count'] == 1
    assert len(parsed.metadata['page_summaries'][0]['preview']) == 200
    assert parsed.metadata['table_summaries'][0]['row_count'] == 2
    assert parsed.metadata['table_summaries'][0]['column_count'] == 2
    assert parsed.metadata['warnings'] == ['layout fallback']
    assert parsed.metadata['ocr_mode'] == 'auto'


def test_ocr_disabled_is_forwarded_to_semantica(tmp_path):
    seen = []

    class Fake:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def parse(self, path):
            return {'full_text': 'text'}

    path = tmp_path / 'report.docx'
    path.write_bytes(b'docx')
    DocumentParser(ocr_mode='disabled', parser_factory=Fake).parse(path, 'report.docx')
    assert seen == [{'export_format': 'markdown', 'enable_ocr': False}]


@pytest.mark.parametrize('mode', ['always', '', 'AUTO'])
def test_invalid_ocr_mode_is_rejected(mode):
    with pytest.raises(ValueError, match='KG_DOCUMENT_OCR_MODE'):
        DocumentParser(ocr_mode=mode)


def test_parser_rejects_unsupported_empty_and_oversized_inputs(tmp_path):
    parser = DocumentParser()
    unsupported = tmp_path / 'deck.pptx'
    unsupported.write_bytes(b'x')
    with pytest.raises(DocumentParseError) as error:
        parser.parse(unsupported, unsupported.name)
    assert error.value.code == 'unsupported_format'

    empty = tmp_path / 'empty.md'
    empty.write_bytes(b'  ')
    with pytest.raises(DocumentParseError) as error:
        parser.parse(empty, empty.name)
    assert error.value.code == 'empty_document'

    large = tmp_path / 'large.txt'
    large.write_bytes(b'x')
    with pytest.raises(DocumentParseError) as error:
        parser.parse(large, large.name, size=25_000_001)
    assert error.value.code == 'file_too_large'
