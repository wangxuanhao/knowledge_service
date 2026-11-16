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



def mixed_ingest(markdown, **settings):
    from knowledge_service.services.image_ocr import ImageOCRService
    from knowledge_service.services.video_asr import VideoASRService
    parsed = parse_markdown(markdown, 'source-version')
    assets = []
    receipts = {}
    for index, ref in enumerate(parsed['images']):
        asset_id = str(index)
        video = ref.get('provenance', {}).get('media_type') == 'video'
        assets.append(dict(id=asset_id, kind='local', status='available', original_path=ref['path'],
            source_start_line=ref['source_start_line'], source_end_line=ref['source_end_line'],
            provenance=dict(source_image_ordinal=index, **({'media_type':'video'} if video else {}))))
        text = ('spoken-' if video else 'recognized-')+asset_id+' '+('v' if video else 'x')*60
        row = dict(id=('asr-' if video else 'ocr-')+asset_id, normalized_text=text, text=text,
                   **({'start_ms':100,'end_ms':900} if video else {}))
        receipts[asset_id] = dict(id='run-'+asset_id, asset_id=asset_id, document_version_id='source-version',
            status='completed', configuration_fingerprint='runtime', average_confidence=.99,
            **({'segments':[row]} if video else {'blocks':[row]}))
    class Processor:
        def __init__(self, video=False):self.video=video
        def process_asset(self, project, source, asset):return receipts[asset]
        def chunks(self, run, max_chars):
            return (VideoASRService if self.video else ImageOCRService).chunks(run,max_chars)
    source = dict(id='source-version', source_text=markdown, blocks=parsed['blocks'], assets=assets)
    options={**UploadOptions(title='Rule',extraction_mode='documents',chunk_size=1000,chunk_overlap=0).model_dump(),**settings}
    chunks,text,warnings = prepare_source_ingest(SimpleNamespace(image_ocr=Processor(),video_asr=Processor(True)),
        'project',source,options)
    blocks = {b['block_id']:b['normalized_text'] for b in source['blocks']}
    for run in receipts.values():
        for row in run.get('blocks',run.get('segments',[])):blocks[row['id']]=row['normalized_text']
    for chunk in chunks:
        assert text[chunk['start_char']:chunk['end_char']]==chunk['text']
        for span in chunk['source_spans']:
            identity = span.get('block_id') or span.get('ocr_block_id') or span.get('asr_segment_id')
            assert chunk['text'][span['chunk_start_char']:span['chunk_end_char']]==blocks[identity][span['block_start_char']:span['block_end_char']]
    assert not warnings
    return chunks,text


@pytest.mark.parametrize('strategy',['fixed','paragraph','structural'])
def test_prose_ocr_and_asr_share_one_normal_chunk(strategy):
    chunks,text=mixed_ingest('before ![](p.png) middle <video src="v.mp4"></video> after',chunk_strategy=strategy)
    assert len(chunks)==1
    assert text.index('before')<text.index('recognized-0')<text.index('middle')<text.index('spoken-1')<text.index('after')
    assert chunks[0]['metadata']['source_kind']=='mixed'
    assert set(chunks[0]['metadata']['source_kinds'])=={'markdown','image_ocr','video_asr'}
    assert chunks[0]['metadata']['ocr_block_ids']==['ocr-0']
    assert chunks[0]['metadata']['asr_segment_ids']==['asr-1']


def test_global_windows_overlap_across_media_without_duplicate_canonical_text():
    chunks,text=mixed_ingest('a'*70+' ![](p.png) '+'b'*70+' <video src="v.mp4"></video> '+'c'*70,
        chunk_size=100,chunk_overlap=20,chunk_strategy='fixed')
    assert [c['start_char'] for c in chunks]==list(range(0,len(text)-20,80))
    assert all(len(c['text'])<=100 for c in chunks)
    assert all(left['text'][-20:]==right['text'][:20] for left,right in zip(chunks,chunks[1:]))
    assert text.count('recognized-0')==text.count('spoken-1')==1
    assert any(c['metadata']['source_kind']=='mixed' for c in chunks)


def test_structural_boundaries_follow_article_headings_not_media_edges():
    chunks,text=mixed_ingest('# A\n\nbefore ![](p.png) after\n\n# B\n\nnext <video src="v.mp4"></video> end',chunk_strategy='structural')
    assert len(chunks)==2
    assert chunks[0]['text'].startswith('# A') and 'recognized-0' in chunks[0]['text'] and 'after' in chunks[0]['text']
    assert chunks[1]['text'].startswith('# B') and 'spoken-1' in chunks[1]['text'] and 'end' in chunks[1]['text']



def test_two_images_share_one_window_without_single_asset_metadata():
    chunks,text=mixed_ingest('before ![](a.png) between ![](b.png) after')
    assert len(chunks)==1
    metadata=chunks[0]['metadata']
    assert metadata['source_asset_ids']==['0','1']
    assert metadata['ocr_block_ids']==['ocr-0','ocr-1']
    assert metadata['ocr_run_ids']==['run-0','run-1']
    assert 'source_asset_id' not in metadata and 'ocr_run_id' not in metadata
