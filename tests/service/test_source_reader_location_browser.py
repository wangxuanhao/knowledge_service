"""Real DOM source navigation with immutable, character-level evidence mappings."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_chunk_preview_ui import browser

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def test_reader_locates_entity_without_painting_whole_blocks(tmp_path):
    prefix = '开头😀同样提到《电子商务法》，但不是本条证据。'
    evidence = '根据《电子商务法》处理投诉。'
    paragraph = prefix + ' ' + evidence + ' 后续无关说明。'
    start = paragraph.index(evidence)
    end = start + len(evidence)
    chunk_text = '# 平台规则\n\n' + paragraph
    chunk_start = chunk_text.index(paragraph)
    mapped = dict(block_id='evidence', chunk_version_id='chunk-v',
                  block_start_char=start, block_end_char=end,
                  chunk_start_char=chunk_start + start, chunk_end_char=chunk_start + end)
    doc = dict(original_name='投诉须知.md', version=1, sha256='fixed-hash',
               blocks=[dict(block_id='evidence', type='paragraph', normalized_text=paragraph,
                            source_start_line=3, source_end_line=3)], assets=[],
               chunks=[dict(id='model-chunk', version_id='chunk-v', text=chunk_text,
                            metadata={}, spans=[mapped])])
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            args=['--disable-gpu', '--disable-gpu-compositing'])
        page = browser.new_page(viewport={'width': 1280, 'height': 800})

        def respond(route):
            path = route.request.url.split('reader.test', 1)[-1]
            if path.endswith('/rendered'):
                route.fulfill(content_type='text/html', body=
                    '<p>无关开头</p>' * 30 + '<p data-block-id="evidence">' + prefix +
                    ' 根据<strong>《电子商务法》</strong>处理投诉。 后续无关说明。</p>' + '<p>无关末尾</p>' * 30)
            elif path.endswith('/source'):
                route.fulfill(content_type='text/plain; charset=utf-8', body='# 投诉须知\n\n' + paragraph)
            elif '/source-documents/' in path:
                route.fulfill(json=doc)
            else:
                route.fulfill(content_type='text/html', body='<button>打开</button>')

        page.route('http://reader.test/**', respond)
        page.goto('http://reader.test/')
        page.add_style_tag(path=str(WEB / 'source-reader.css'))
        page.add_script_tag(path=str(WEB / 'source-content.js'))
        page.add_script_tag(path=str(WEB / 'source-reader.js'))
        page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='v',
                      focusText='《电子商务法》', location=dict(document_version_id='v', status='available',
                      precision='evidence', spans=[mapped])))
        marks = page.locator('.source-reader-body mark')
        assert marks.count() == 1, 'only the selected entity occurrence should be marked'
        assert marks.inner_text() == '《电子商务法》'
        assert page.locator('p.source-highlight,section.source-highlight').count() == 0, 'no whole paragraph yellow'
        assert marks.evaluate('(n)=>{const r=n.getBoundingClientRect(),b=n.closest("article").getBoundingClientRect();return r.top>=b.top&&r.bottom<=b.bottom;}')
        page.get_by_role('button', name='解析视图').click()
        page.locator('.source-parsed-block[open]').wait_for()
        chunk = page.locator('details[data-chunk-version-id="chunk-v"]')
        assert chunk.get_attribute('open') is not None
        assert chunk.locator('mark').inner_text() == '《电子商务法》'
        assert page.locator('section[data-block-id="evidence"] mark').inner_text() == '《电子商务法》'
        page.screenshot(path=str(tmp_path / 'source-reader-exact-parsed.png'))
        page.get_by_role('button', name='原始视图').click()
        page.get_by_role('link', name='下载固定版本原文件').wait_for()
        assert '投诉须知' in page.locator('.source-reader-body').inner_text()
        browser.close()


def test_reader_tabs_wait_until_initial_source_metadata_is_ready():
    doc = dict(original_name='规则.md', version=1, sha256='fixed', assets=[], chunks=[],
               blocks=[dict(block_id='body', type='paragraph', normalized_text='已加载正文',
                            source_start_line=1, source_end_line=1)])
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        page = browser.new_page()

        def respond(route):
            if route.request.url.endswith('/rendered'):
                route.fulfill(content_type='text/html', body='<p>已加载正文</p>')
            elif '/source-documents/' in route.request.url:
                route.fulfill(json=doc)
            else:
                route.fulfill(content_type='text/html', body='<button>打开</button>')

        page.route('http://reader.test/**', respond)
        page.goto('http://reader.test/')
        page.add_script_tag(path=str(WEB / 'source-content.js'))
        page.add_script_tag(path=str(WEB / 'source-reader.js'))
        page.evaluate('''doc => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (url, ...args) => {
                if (String(url).endsWith('/source-documents/v') && !window.metadataReleased) {
                    return new Promise(resolve => {
                        window.releaseMetadata = () => {
                            window.metadataReleased = true;
                            resolve(new Response(JSON.stringify(doc), {
                                headers: {'Content-Type': 'application/json'}
                            }));
                        };
                    });
                }
                return originalFetch(url, ...args);
            };
            void SourceReader.open({projectId: 'p', versionId: 'v'});
        }''', doc)
        parsed = page.get_by_role('button', name='解析视图', exact=True)
        assert parsed.is_disabled(), 'view clicks must wait for their source-specific handlers'
        page.evaluate('window.releaseMetadata()')
        parsed.click()
        page.locator('.source-parsed-block').wait_for()
        assert parsed.get_attribute('aria-pressed') == 'true'
        assert '已加载正文' in page.locator('.source-reader-body').inner_text()
        browser.close()


def test_reader_uses_each_assertion_verified_range_with_the_same_quote(browser):
    page = browser.new_page()
    page.set_default_timeout(5000)
    quote = '处置措施'
    sections = ['规则标题', '前一份说明涉及处置措施。', '后一份说明也涉及处置措施。']
    spans = []
    offset = 0
    for index, text in enumerate(sections):
        spans.append(dict(block_id=f'block-{index}', chunk_version_id='chunk-v',
                          chunk_start_char=offset, chunk_end_char=offset + len(text),
                          block_start_char=0, block_end_char=len(text)))
        offset += len(text) + 1
    doc = dict(original_name='规则.md', assets=[], blocks=[
        dict(block_id=f'block-{index}', normalized_text=text)
        for index, text in enumerate(sections)])

    def location(index):
        span = spans[index]
        start = sections[index].index(quote)
        return dict(document_version_id='v', status='available', precision='evidence', spans=[{
            **span, 'chunk_start_char': span['chunk_start_char'] + start,
            'chunk_end_char': span['chunk_start_char'] + start + len(quote),
            'block_start_char': start, 'block_end_char': start + len(quote),
        }])

    def respond(route):
        url = route.request.url
        if url.endswith('/rendered'):
            return route.fulfill(content_type='text/html', body=''.join(
                f'<p data-block-id="block-{index}">{text}</p>' for index, text in enumerate(sections)))
        if '/locate?' in url:
            return route.fulfill(json={'status':'available', 'spans':spans})
        if url.endswith('/source-documents/v'):
            return route.fulfill(json=doc)
        if url.endswith('/assertions/first/evidence'):
            return route.fulfill(json={'source_location':location(1), 'location':{'mode':'exact'}})
        if url.endswith('/assertions/second/evidence'):
            return route.fulfill(json={'source_location':location(2), 'location':{'mode':'exact'}})
        if url.endswith('/assertions/unverified/evidence'):
            source = dict(document_version_id='v', status='available', precision='chunk', spans=spans)
            return route.fulfill(json={'source_location':source, 'location':{'mode':'chunk'}})
        if url.endswith('/assertions/mismatched/evidence'):
            return route.fulfill(json={'source_location':{**location(2), 'document_version_id':'other-v'},
                                       'location':{'mode':'exact'}})
        return route.fulfill(content_type='text/html', body='<button>打开</button>')

    page.route('http://reader.test/**', respond)
    page.goto('http://reader.test/')
    page.add_script_tag(path=str(WEB / 'source-content.js'))
    page.add_script_tag(path=str(WEB / 'source-reader.js'))
    marks = page.locator('.source-reader-body mark')
    for assertion_id, block_id in [('first', 'block-1'), ('second', 'block-2')]:
        page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='v',
                      assertionId=assertion_id, focusText=quote))
        assert marks.all_inner_texts() == [quote]
        assert marks.evaluate('(mark)=>mark.parentElement.dataset.blockId') == block_id
    page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='v',
                  assertionId='second', chunkVersionId='chunk-v', focusText=quote))
    assert marks.evaluate('(mark)=>mark.parentElement.dataset.blockId') == 'block-2'
    for assertion_id, message in [('unverified', '未核验精确引文位置'), ('mismatched', '未找到证据位置')]:
        page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='v',
                      assertionId=assertion_id, focusText=quote))
        assert marks.count() == 0
        assert message in page.locator('.source-reader-note').first.inner_text()
        assert sections[0] in page.locator('.source-reader-body').inner_text()
    page.close()


@pytest.mark.parametrize('missing_step', ['metadata', 'locate', 'rendered'])
def test_reader_preserves_frozen_evidence_when_source_read_returns_404(browser, missing_step):
    page = browser.new_page()
    page.set_default_timeout(5000)
    base = '/api/projects/p/source-documents/frozen-v1'
    quote = '保存的引文 <img src=x>。'

    def respond(route):
        url = route.request.url
        if url.endswith(base):
            if missing_step == 'metadata':
                return route.fulfill(status=404, json={'detail':'源版本不存在'})
            return route.fulfill(json={'original_name':'平台规则.md', 'assets':[], 'blocks':[]})
        if url.endswith('/assertions/a1/evidence'):
            if missing_step == 'locate':
                return route.fulfill(status=404, json={'detail':'定位不存在'})
            return route.fulfill(json={'source_location':{'document_version_id':'frozen-v1',
                'status':'available', 'precision':'chunk', 'spans':[]}, 'location':{'mode':'chunk'}})
        if url.endswith(base + '/rendered'):
            if missing_step == 'rendered':
                return route.fulfill(status=404, json={'detail':'原文内容不存在'})
            return route.fulfill(content_type='text/html', body='<p>已打开固定版本正文</p>')
        return route.fulfill(content_type='text/html', body='<button>打开</button>')

    page.route('http://reader.test/**', respond)
    page.goto('http://reader.test/')
    page.add_script_tag(path=str(WEB / 'source-content.js'))
    page.add_script_tag(path=str(WEB / 'source-reader.js'))
    page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='frozen-v1',
                  assertionId='a1', fallbackEvidence=dict(documentTitle='平台规则.md',
                  documentVersionId='receipt-v1', quote=quote)))
    if missing_step == 'locate':
        assert '已打开固定版本正文' in page.locator('.source-reader-body').inner_text()
        assert '未找到证据位置' in page.locator('.source-reader-note').first.inner_text()
    else:
        assert page.locator('.source-reader-body pre').inner_text() == quote
        assert '已保存引文' in page.locator('.source-reader-body').inner_text()
        assert page.locator('.source-reader-body img').count() == 0
    page.close()


if __name__ == '__main__':
    import sys
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.benchmarks/source-reader-location')
    output.mkdir(parents=True, exist_ok=True)
    test_reader_locates_entity_without_painting_whole_blocks(output)
    print(json.dumps({'source_reader_entity_location': 'passed'}))



def test_reader_can_switch_independent_video_locations_in_one_chunk(browser):
    page=browser.new_page()
    page.set_default_timeout(5000)
    assets=[dict(id='v'+str(i),kind='local',status='available',original_path='clip'+str(i)+'.mp4',
        provenance={'media_type':'video'}) for i in range(2)]
    doc=dict(original_name='mixed.md',version=1,sha256='hash',assets=assets,blocks=[],chunks=[])
    def route(request):
        path=request.request.url
        if path.endswith('/rendered'):
            request.fulfill(content_type='text/html',body=''.join('<video controls data-asset-id="'+a['id']+'" data-asset-url="/api/projects/p/source-documents/v/assets/'+a['id']+'"></video>' for a in assets))
        elif '/assets/' in path:request.fulfill(content_type='video/mp4',body=b'')
        elif '/source-documents/' in path:request.fulfill(json=doc)
        else:request.fulfill(content_type='text/html',body='<body></body>')
    page.route('http://test/**',route);page.goto('http://test/')
    page.add_script_tag(path=str(WEB/'source-content.js'))
    page.add_script_tag(path=str(WEB/'source-reader.js'))
    page.evaluate("""() => {
      window.seeks=[];const create=SourceContent.createSession;
      SourceContent.createSession=options=>{const session=create(options),seek=session.seekVideo;
        session.seekVideo=(...args)=>{window.seeks.push(args);return seek(...args);};return session;};
    }""")
    groups=[dict(source_kind='video_asr',asset_id='v'+str(i),asr_run_id='r'+str(i),
        start_ms=100+i*40000,end_ms=900+i*40000,boxes=[],segments=[]) for i in range(2)]
    page.evaluate('location=>SourceReader.open({projectId:"p",versionId:"v",location})',
        dict(status='available',spans=[],asset_id=None,media_locations=groups))
    page.get_by_role('button',name='定位视频 2',exact=False).click()
    page.wait_for_function('window.seeks.some(s=>s[0]==="v1"&&s[1]===40100)')
    assert page.get_by_role('button',name='定位视频 1',exact=False).is_visible()
    page.close()



def test_reader_shows_filtered_ocr_as_completed_with_notice(browser):
    from source_test_images import png
    page=browser.new_page();page.set_default_timeout(5000)
    asset=dict(id='image',kind='local',status='completed',original_path='photo.png',width=8,height=8,provenance={})
    blocks=[dict(id='b'+str(i),text=text,normalized_text=text,confidence=.95,x=.1,y=.1,width=.5,height=.5)
        for i,text in enumerate(['可靠文字','有效规则'])]
    run=dict(id='run',status='completed',average_confidence=.95,blocks=blocks,
        processing_details={'quality':{'accepted_blocks':2,'discarded_blocks':1,'review_recommended':True}})
    doc=dict(original_name='rule.md',version=1,sha256='hash',assets=[asset],blocks=[],chunks=[])
    def route(request):
        path=request.request.url
        if path.endswith('/rendered'):request.fulfill(content_type='text/html',body='<p>正文</p>')
        elif path.endswith('/ocr'):request.fulfill(json={'run':run,'runs':[run],'blocks':blocks})
        elif path.endswith('/assets/image'):request.fulfill(content_type='image/png',body=png())
        elif '/source-documents/' in path:request.fulfill(json=doc)
        else:request.fulfill(content_type='text/html',body='<body></body>')
    page.route('http://test/**',route);page.goto('http://test/')
    page.add_script_tag(path=str(WEB/'source-content.js'));page.add_script_tag(path=str(WEB/'source-reader.js'))
    page.evaluate('SourceReader.open({projectId:"p",versionId:"v"})')
    page.get_by_role('button',name='解析视图',exact=True).click()
    page.get_by_text('OCR 状态：识别完成（部分模糊文字未采用）',exact=True).wait_for()
    assert page.get_by_text('已采用 2 块可靠文字，1 块模糊文字未采用，请核对原图。',exact=True).is_visible()
    assert page.get_by_role('button',name='重试 OCR',exact=True).count()==0
    page.close()



def test_pdf_ocr_focus_uses_original_page_without_duplicate_scan(browser):
    import io,base64
    from PIL import Image
    page=browser.new_page();page.set_default_timeout(5000)
    data=io.BytesIO();Image.new('RGB',(300,400),'white').save(data,format='PNG')
    asset=dict(id='scan',kind='local',status='completed',width=1800,height=2400,original_path='assets/page-1.png',
        provenance={'kind':'scanned_page','page_no':1,'locations':[{'page_no':1,'bbox':{'l':0,'t':0,'r':600,'b':800,'coord_origin':'TOPLEFT'}}]})
    doc=dict(original_name='agreement.pdf',format='pdf',mime_type='application/pdf',version=1,sha256='hash',assets=[asset],blocks=[],chunks=[])
    box=dict(id='b',asset_id='scan',x=.1,y=.2,width=.3,height=.1,text='协议正文')
    def route(request):
        path=request.request.url
        if '/pdf-preview?' in path:request.fulfill(json={'page_number':1,'page_count':2,'page_width':600,'page_height':800,'width':300,'height':400,'mime_type':'image/png','image_base64':base64.b64encode(data.getvalue()).decode()})
        elif path.endswith('/ocr'):request.fulfill(json={'run':{'status':'completed','blocks':[box]},'blocks':[box]})
        elif '/assets/' in path:request.fulfill(content_type='image/png',body=data.getvalue())
        elif '/source-documents/' in path:request.fulfill(json=doc)
        else:request.fulfill(content_type='text/html',body='<body></body>')
    page.route('http://test/**',route);page.goto('http://test/')
    page.add_style_tag(path=str(WEB/'source-reader.css'))
    page.add_script_tag(path=str(WEB/'source-content.js'));page.add_script_tag(path=str(WEB/'source-reader.js'))
    page.evaluate('location=>SourceReader.open({projectId:"p",versionId:"v",location})',{'status':'available','asset_id':'scan','boxes':[box]})
    marker=page.locator('.source-pdf-page .source-region-focus');marker.wait_for()
    assert marker.evaluate('e=>e.style.width')=='30%'
    assert marker.evaluate('e=>e.style.height')=='10%'
    assert page.locator('.source-reader-body figure.source-reader-image').count()==0
    page.close()
