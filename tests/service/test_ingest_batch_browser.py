"""Browser checks for the actual batch and fixed-source widgets, without an LLM."""
import base64
import json
import io
from pathlib import Path
from PIL import Image

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def test_source_reader_browser_contract(tmp_path):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        pixels = io.BytesIO()
        Image.new('RGB', (480, 160), '#e7f2ef').save(pixels, format='PNG')
        def respond(route):
            path = route.request.url.split('browser.test', 1)[-1]
            if path.startswith('/api/') and route.request.headers.get('authorization') != 'Bearer browser-test-token':
                route.fulfill(status=401, json={'detail': 'not_authenticated'})
                return
            if path.endswith('/rendered'):
                route.fulfill(content_type='text/html', body='<h1 data-block-id="block-1">规则正文</h1><table data-block-id="table-1"><thead><tr><th>金额</th></tr></thead><tbody><tr><td>100 元</td></tr><tr><td>200 元</td></tr><tr><td>300 元</td></tr></tbody></table>')
            elif path.endswith('/source'):
                route.fulfill(content_type='text/plain', body='<script>window.injected=true</script>\n原始正文')
            elif path.endswith('/ocr'):
                route.fulfill(json={'run': {'engine': 'fake-ocr', 'status': 'completed', 'average_confidence': .95,
                    'normalized_text': '中文规则'}, 'blocks': [{'text': '中文规则', 'x': .1, 'y': .2, 'width': .3, 'height': .4}]})
            elif '/assets/' in path:
                route.fulfill(content_type='image/png', body=pixels.getvalue())
            elif '/source-documents/' in path:
                route.fulfill(json={'original_name': '规则.md', 'version': 1, 'blocks': [
                    {'block_id': 'block-1', 'type': 'paragraph', 'normalized_text': '正文', 'source_start_line': 1, 'source_end_line': 2}],
                    'assets': [{'id': 'image-1', 'original_path': 'rule.png', 'status': 'completed', 'kind': 'local'}]})
            else:
                route.fulfill(content_type='text/html', body='<button id="open">打开来源</button>')
        page.route('http://browser.test/**', respond)
        page.goto('http://browser.test/')
        page.evaluate("""() => {const original=window.fetch.bind(window);window.fetch=(url,options={})=>original(url,{...options,headers:{...options.headers,Authorization:'Bearer browser-test-token'}});} """)
        page.add_style_tag(path=str(WEB / 'source-reader.css'))
        page.add_script_tag(path=str(WEB / 'source-reader.js'))
        page.locator('#open').focus()
        page.evaluate("SourceReader.open({projectId:'p',versionId:'v',location:{block_id:'block-1'}})")
        page.locator('[data-block-id="block-1"].source-context').wait_for()
        page.evaluate("SourceReader.open({projectId:'p',versionId:'v',location:{spans:[{block_id:'table-1',payload:{table_start_row:1,table_end_row:2}}]}})")
        page.locator('tbody tr.source-context').wait_for()
        assert page.locator('tbody tr.source-context').count() == 1
        assert page.locator('tbody tr.source-context').inner_text() == '200 元'
        page.get_by_role('button', name='原始视图').click()
        page.get_by_text('原始正文', exact=False).wait_for()
        assert page.evaluate('window.injected') is None
        assert page.locator('.source-reader-body script').count() == 0
        with page.expect_download() as downloaded:
            page.get_by_role('link', name='下载固定版本原文件').click()
        assert downloaded.value.suggested_filename == '规则.md'
        assert Path(downloaded.value.path()).read_text(encoding='utf-8').startswith('<script>')
        page.get_by_role('button', name='解析视图').click()
        page.locator('.source-ocr-box').wait_for()
        page.wait_for_function("document.querySelector('.source-image-canvas img').naturalWidth > 0")
        assert page.locator('.source-ocr-box').evaluate('(n)=>n.style.left') == '10%'
        assert 'fake-ocr' in page.locator('.source-reader-body').inner_text()
        page.screenshot(path=str(tmp_path / 'source-reader.png'))
        page.keyboard.press('Escape')
        assert not page.locator('#source-reader').is_visible()
        assert page.locator('#open').evaluate('(n)=>n===document.activeElement')
        browser.close()


def test_batch_card_paginates_and_uses_selected_retry_mode(tmp_path):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        calls = []
        def respond(route):
            path = route.request.url.split('browser.test', 1)[-1]
            calls.append((path, route.request.post_data))
            if path == '/api/ingest/capabilities':
                route.fulfill(json={'max_batch_items': 1000, 'max_file_bytes': 25000000, 'max_batch_bytes': 1000000000,
                    'concurrency_choices': ['auto', 1, 2, 4, 8]})
            elif '/items?' in path:
                route.fulfill(json={'items': [{'id': 'i', 'status': 'needs_attention', 'original_name': '<img onerror=alert(1)>.md',
                    'stage': 'extracting', 'error_code': 'llm_timeout', 'public_error': '模型超时', 'failed_chunk_ordinal': 28,
                    'auto_retry_count': 5}], 'next_cursor': None if 'cursor=' in path else 'next'})
            elif path.startswith('/api/'):
                route.fulfill(json={'id': 'b', 'status': 'completed_with_errors', 'requested_concurrency': 8,
                    'effective_concurrency': 4, 'counts': {'completed': 999, 'needs_attention': 1}})
            else:
                route.fulfill(content_type='text/html', body='<select id="project"><option value="p">p</option></select><select id="ingest-concurrency"></select><div id="ingest-batches"></div>')
        page.route('http://browser.test/**', respond)
        page.goto('http://browser.test/')
        page.evaluate('window.status=()=>{}')
        page.add_style_tag(path=str(WEB / 'ingest-batches.css'))
        page.add_script_tag(path=str(WEB / 'ingest-batches.js'))
        page.evaluate("IngestBatches.monitor('p',{id:'b'})")
        page.get_by_text('模型超时', exact=False).wait_for()
        concurrency = page.locator('.ingest-batch-card').inner_text()
        assert '并发设置：8' in concurrency
        assert '当前并发 4' in concurrency
        assert page.locator('.batch-item img').count() == 0
        page.get_by_role('button', name='下一页').click()
        page.get_by_role('button', name='回到首页').wait_for()
        assert any('cursor=next' in path for path, _ in calls)
        page.get_by_role('button', name='只重试失败切片', exact=True).click()
        page.wait_for_timeout(100)
        assert any('/items/i/retry' in path and json.loads(body)['mode'] == 'retry_failed_chunks' for path, body in calls if body)
        page.screenshot(path=str(tmp_path / 'ingest-batches.png'))
        browser.close()
