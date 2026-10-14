from pathlib import Path
import tempfile, io, zipfile
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.integrations.markdown_parser import parse_markdown
from knowledge_service.integrations.markdown_package import MarkdownPackageReader

def test_object_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        store=SourceObjectStore(d); a=store.put(b'abc'); b=store.put(b'abc')
        assert a['storage_key']==b['storage_key']; assert store.read(a['storage_key'])==b'abc'
        try: store.read('../bad')
        except ValueError: pass
        else: raise AssertionError('unsafe key')
def test_markdown_table_image_safety():
    text='# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n![alt](images/a.png)\n<script>alert(1)</script>'
    result=parse_markdown(text,'v1'); table=next(b for b in result['blocks'] if b['type']=='table')
    assert table['payload']['headers']==['A','B']; assert table['payload']['rows']==[['1','2']]
    assert table['heading_path']==['Title']; assert result['images'][0]['kind']=='local'
    assert '<script>' not in result['rendered_html']; assert 'src="images' not in result['rendered_html']
def test_package_reject_escape():
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z: z.writestr('../evil.md','# evil')
    try: MarkdownPackageReader().read(buf.getvalue())
    except ValueError: pass
    else: raise AssertionError('unsafe ZIP')

def test_package_multiple_markdown_shared_missing_and_external():
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('a.md','![one](shared/x.png)\n![missing](gone.png)\n![net](https://example.com/a.png)')
        z.writestr('sub/b.md','![two](../shared/x.png)')
        z.writestr('shared/x.png',b'PNG')
    result=MarkdownPackageReader().read(buf.getvalue())
    assert len(result['documents'])==2
    assert result['documents'][0]['assets'][0]['data']==b'PNG'
    assert result['documents'][0]['warnings'][0]['code']=='source_asset_missing'
    assert result['documents'][0]['assets'][2]['status']=='external_unfrozen'
    assert result['documents'][1]['assets'][0]['data']==b'PNG'

def test_parser_stable_source_anchors():
    text='# 标题\n\n正文\n\n```py\nprint(1)\n```\n'
    result=parse_markdown(text,'version')
    for block in result['blocks']:
        assert text[block['source_start_char']:block['source_end_char']]
        if block['type']!='code': assert block['block_id'] in result['rendered_html']

def test_ocr_models_unavailable_is_explicit(tmp_path):
    from knowledge_service.integrations.image_ocr import RapidOCRAdapter, OCRError, verify_models
    assert not RapidOCRAdapter(tmp_path).available()
    try: verify_models(tmp_path)
    except OCRError as exc: assert exc.code=='ocr_engine_unavailable'
    else: raise AssertionError('Missing models accepted')

def test_ocr_chunk_provenance():
    from knowledge_service.services.image_ocr import ImageOCRService
    run={'id':'r','asset_id':'a','document_version_id':'v','average_confidence':0.9,
         'blocks':[{'id':'b1','normalized_text':'rule one'},{'id':'b2','normalized_text':'rule two'}]}
    chunks=ImageOCRService.chunks(run,max_chars=10)
    assert len(chunks)==2
    assert chunks[0]['metadata']['source_kind']=='image_ocr'
    assert chunks[0]['metadata']['ocr_block_ids']==['b1']

def test_table_chunks_repeat_header_and_keep_rows():
    from knowledge_service.integrations.markdown_parser import chunk_markdown_blocks
    parsed=parse_markdown('# H\n\n| A | B |\n|---|---|\n| one | x |\n| two | y |\n| three | z |\n','v')
    chunks=[c for c in chunk_markdown_blocks(parsed,18) if 'table_start_row' in c['metadata']]
    assert len(chunks)>1
    assert all(c['text'].startswith('A | B\n') for c in chunks)
    assert [line for c in chunks for line in c['metadata']['table_source_lines']]==[5,6,7]
    assert all(c['source_spans'][0]['block_id']==parsed['blocks'][1]['block_id'] for c in chunks)
