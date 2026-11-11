"""Inline media keeps article order and original normalized-block coordinates."""
from types import SimpleNamespace

import pytest

from knowledge_service.integrations.markdown_parser import parse_markdown
from knowledge_service.models import UploadOptions
from knowledge_service.services.document_temporal import source_temporal_exclusions
from knowledge_service.services.source_ingest import prepare_source_ingest


class OCR:
    def __init__(self):
        self.calls = []

    def process_asset(self, project_id, source_id, asset_id):
        self.calls.append(asset_id)
        return {'status': 'completed', 'blocks': [
            {'id': 'ocr-' + asset_id, 'normalized_text': 'recognized-' + asset_id}]}

    def chunks(self, run, max_chars):
        block = run['blocks'][0]
        text = block['normalized_text']
        return [dict(text=text, source_spans=[dict(ocr_block_id=block['id'],
            chunk_start_char=0, chunk_end_char=len(text), block_start_char=0,
            block_end_char=len(text), payload={})], metadata=dict(source_kind='image_ocr'))]


def ingest(markdown):
    parsed = parse_markdown(markdown, 'source-version')
    assets = [dict(id=str(index), kind='local', status='available',
        original_path=ref.get('path', ref['url']), resolved_path='',
        source_start_line=ref['source_start_line'], source_end_line=ref['source_end_line'],
        provenance=dict(source_image_ordinal=index)) for index, ref in enumerate(parsed['images'])]
    source = dict(id='source-version', source_text=markdown, blocks=parsed['blocks'], assets=assets)
    ocr = OCR()
    options = UploadOptions(title='Rule', extraction_mode='documents',
                            chunk_size=100, chunk_overlap=0).model_dump()
    chunks, text, warnings = prepare_source_ingest(SimpleNamespace(image_ocr=ocr),
                                                   'project', source, options)
    return source, ocr, chunks, text, warnings


def assert_mapped_text(source, chunks, text):
    blocks = {block['block_id']: block['normalized_text'] for block in source['blocks']}
    for chunk in chunks:
        assert text[chunk['start_char']:chunk['end_char']] == chunk['text']
        for span in chunk['source_spans']:
            if span.get('block_id'):
                assert chunk['text'][span['chunk_start_char']:span['chunk_end_char']] == (
                    blocks[span['block_id']][span['block_start_char']:span['block_end_char']])


@pytest.mark.parametrize('markdown', [
    'before text\n![not body](a.png)\nafter text',
    'before text ![not body](a.png) after text',
    'before text <img src="a.png" alt="not body"> after text',
    '<p>before text<img src="a.png" alt="not body">after text</p>',
])
def test_media_between_text_in_one_block_preserves_reading_order(markdown):
    source, ocr, chunks, text, warnings = ingest(markdown)
    assert len(source['blocks']) == 1
    assert text.index('before text') < text.index('recognized-0') < text.index('after text')
    assert 'not body' not in text
    assert ocr.calls == ['0']
    assert not warnings
    assert_mapped_text(source, chunks, text)


def test_same_line_repeated_media_processes_each_occurrence_at_its_own_position():
    source, ocr, chunks, text, _ = ingest('first ![](a.png) middle ![](a.png) last')
    assert text.index('first') < text.index('recognized-0') < text.index('middle')
    assert text.index('middle') < text.index('recognized-1') < text.index('last')
    assert ocr.calls == ['0', '1']
    assert_mapped_text(source, chunks, text)


def test_media_offsets_follow_final_heading_and_table_normalization():
    parsed = parse_markdown('# title ![not body](a.png) suffix\n\n'
                            '| left | right |\n| --- | --- |\n'
                            '| before ![not body](b.png) after | end |', 'version')
    heading, table = parsed['blocks']
    assert heading['type'] == 'heading' and heading['payload']['level'] == 1
    assert heading['normalized_text'] == 'title  suffix'
    assert heading['payload']['images'][0]['block_text_offset'] == len('title ')
    assert table['type'] == 'table'
    assert table['payload']['headers'] == ['left', 'right']
    assert table['payload']['rows'] == [['before  after', 'end']]
    assert table['payload']['images'][0]['block_text_offset'] == len('left | right\nbefore ')
    assert all('not body' not in block['normalized_text'] for block in parsed['blocks'])


def test_temporal_exclusions_keep_exact_original_coordinates_after_inline_media():
    source, _, chunks, text, _ = ingest(
        'before ![](a.png) `本规则自2030年1月1日起生效。` after\n\n'
        '> quoted ![](b.png) 本规则自2031年1月1日起生效。')
    exclusions = source_temporal_exclusions(source, chunks=chunks)
    for phrase in ('本规则自2030年1月1日起生效。', '本规则自2031年1月1日起生效。'):
        start = text.index(phrase)
        assert any(begin <= start and end >= start + len(phrase) for begin, end in exclusions)
    assert_mapped_text(source, chunks, text)


def test_inline_media_preserves_heading_table_and_table_code_source_ranges():
    source, _, chunks, text, _ = ingest(
        '# before ![](a.png) after\n\n'
        '| left | right |\n| --- | --- |\n'
        '| first ![](b.png) `本规则自2030年1月1日起生效。` last | end |')
    heading, table = source['blocks']
    assert text.count('# ') == 1
    assert text.index('before') < text.index('recognized-0') < text.index('after')
    assert text.index('first') < text.index('recognized-1') < text.index('last')
    assert table['payload']['rows'] == [['first  本规则自2030年1月1日起生效。 last', 'end']]
    assert_mapped_text(source, chunks, text)
    table_spans = [span for chunk in chunks for span in chunk['source_spans']
                   if span.get('block_id') == table['block_id']]
    assert table_spans and all(span['payload']['table_start_row'] == 0 for span in table_spans)
    phrase = '本规则自2030年1月1日起生效。'
    start = text.index(phrase)
    exclusions = source_temporal_exclusions(source, chunks=chunks)
    assert any(begin <= start and end >= start + len(phrase) for begin, end in exclusions)
    assert all(text[begin:end] == phrase for begin, end in exclusions)
