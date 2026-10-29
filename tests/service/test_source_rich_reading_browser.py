"""Real Edge UI fixtures for the shared rich reader (no parser/object-store claim)."""
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
OUTPUT = WEB.parents[1] / '.benchmarks' / 'source-rich-reading'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
HOST = 'http://rich-reader.test'


def reader_page(browser, width=1440):
    page = browser.new_page(viewport={'width': width, 'height': 960})
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    data = io.BytesIO()
    Image.new('RGB', (960, 320), '#b6d4c9').save(data, format='PNG')
    rich = ('<h1 data-block-id="title">平台规则 · 图文原文</h1>'
            '<p data-block-id="before">BEFORE：图片前的说明，保持文档的阅读顺序。</p>'
            '<p data-block-id="picture"><img data-asset-id="good" width="960" height="320" '
            'src="/api/projects/p/source-documents/v/assets/good" alt="业务处理流程图"></p>'
            '<p data-block-id="after">AFTER：图片后的补充说明。</p>'
            '<table data-block-id="table"><thead><tr>' + '<th>很长的表格列名</th>' * 12 +
            '</tr></thead><tbody><tr>' + '<td>一项需要保留的条款说明</td>' * 12 + '</tr></tbody></table>'
            '<p><img data-asset-id="failed" src="/api/projects/p/source-documents/v/assets/failed" alt="附录图片"></p>'
            '<p><img data-asset-id="outside" src="https://outside.invalid/leak.png" alt="外部未固化图片"></p>'
            '<p>' + '后续正文。' * 800 + '</p>'
            '<p><img data-asset-id="lazy" width="960" height="320" src="/api/projects/p/source-documents/v/assets/lazy" alt="后文图片"></p>')
    record = {'id': 'one', 'text': '已保存纯文本 BEFORE AFTER', 'metadata': {
        'title': '平台图文规则', 'source_file': 'guide.md', 'source_format': 'md',
        'status': 'completed', 'source_document_version_id': 'v'}}

    def respond(route):
        url = route.request.url
        calls.append(url)
        path = url.split(HOST, 1)[-1]
        if path.startswith('/api/') and route.request.headers.get('authorization') != 'Bearer fixture':
            return route.fulfill(status=401, json={'detail': 'unauthorized'})
        if path.endswith('/rendered'):
            return route.fulfill(content_type='text/html; charset=utf-8', body=rich)
        if '/assets/' in path:
            if path.endswith('/failed'):
                return route.fulfill(status=404, body='missing')
            return route.fulfill(content_type='image/png', body=data.getvalue())
        if path.endswith('/source'):
            return route.fulfill(content_type='text/markdown', body='# 原始 Markdown\nBEFORE AFTER')
        if path.endswith('/package'):
            return route.fulfill(content_type='application/zip', body=b'PK\x03\x04fixture-package')
        if '/source-documents/' in path:
            return route.fulfill(json={'original_name': 'guide.md', 'format': 'markdown',
                                       'mime_type': 'application/zip', 'version': 1,
                                       'source_text': '# 解析正文', 'blocks': [], 'assets': []})
        if '/records/one' in path:
            return route.fulfill(json=record)
        if '/sources' in path:
            return route.fulfill(json={'documents': [record], 'total': 1})
        if path.startswith('/api/'):
            return route.fulfill(json={'versions': [], 'assertions': [], 'runs': []})
        return route.fulfill(content_type='text/html', body='''<!doctype html><html lang="zh-CN"><head><style>
          :root{--ks-line:#d9e1e1;--ks-sunken:#f4f7f5;--ks-muted:#647875;--text:#253d3b;--panel:#fff}
          *{box-sizing:border-box}body{margin:0;font:14px/1.8 "Microsoft YaHei",sans-serif;color:#253d3b}
          button,a{font:inherit}button{cursor:pointer}.hidden{display:none}#tab-sources{padding:24px}
          .workspace-drawer{width:min(900px,100vw);height:100vh;max-height:100vh;margin:0 0 0 auto;border:1px solid #d9e1e1;padding:0}
          .workspace-drawer-heading{display:flex;justify-content:space-between;gap:12px;padding:20px;border-bottom:1px solid #d9e1e1}
          .workspace-drawer-body{padding:24px;overflow:auto;height:calc(100vh - 130px)}
          </style></head><body><select id="project"><option>p</option><option>other</option></select>
          <section id="tab-sources"><button id="load-sources">刷新</button><button id="upload-source">上传</button>
          <div class="source-layout"><div id="source-list"></div><article><h3 id="source-title"></h3><pre id="source-body"></pre></article></div></section></body></html>''')

    page.route('**/*', respond)
    page.goto(HOST)
    page.evaluate("""() => {window.$=id=>document.getElementById(id);window.current='p';window.scope=()=>({});
      window.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      window.Auth={isAdmin:()=>false};window.status=()=>{};
      const original=fetch.bind(window);window.fetch=(url,options={})=>original(url,{...options,headers:{...options.headers,Authorization:'Bearer fixture'}});
      window.api=async(url,body,method='GET')=>{const r=await fetch(url,{method});if(!r.ok)throw Error('HTTP '+r.status);return r.json()};
      window.KnowledgeTabs={open:()=>{}};
      const originalCreate=URL.createObjectURL.bind(URL),originalRevoke=URL.revokeObjectURL.bind(URL);window.liveUrls=new Set();
      URL.createObjectURL=blob=>{const url=originalCreate(blob);liveUrls.add(url);return url};URL.revokeObjectURL=url=>{liveUrls.delete(url);originalRevoke(url)};
    }""")
    for name in ('library-workspace.css', 'source-reader.css', 'sources-library.css'):
        page.add_style_tag(path=str(WEB / name))
    for name in ('workspace-drawers.js', 'source-content.js', 'source-reader.js', 'sources-view.js'):
        if (WEB / name).exists():
            page.add_script_tag(path=str(WEB / name))
    return page, calls, errors


def test_library_rich_reading_widths_zoom_and_fallback():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=EDGE,
                                         args=['--disable-gpu', '--disable-gpu-compositing'])
        for width in (1440, 768, 390):
            page, calls, errors = reader_page(browser, width)
            page.locator('.source-item').click()
            body = page.locator('#source-body')
            body.locator('h1').wait_for()
            image = body.locator('img[data-asset-id="good"]')
            page.wait_for_function("document.querySelector('img[data-asset-id=good]').naturalWidth===960")
            assert body.locator('[data-block-id="before"]').evaluate('(n)=>n.getBoundingClientRect().top') < image.evaluate('(n)=>n.getBoundingClientRect().top')
            assert image.evaluate('(n)=>n.getBoundingClientRect().bottom') < body.locator('[data-block-id="after"]').evaluate('(n)=>n.getBoundingClientRect().top')
            assert image.evaluate('(n)=>n.naturalHeight') == 320
            assert not any('outside.invalid' in url for url in calls), 'external image must not issue a browser request'
            assert not any('/assets/lazy' in url for url in calls), 'offscreen image stays lazy'
            opener = image.locator('..')
            opener.click()
            zoom = page.get_by_role('dialog', name='图片全屏查看')
            assert zoom.is_visible()
            assert zoom.locator('img').evaluate('(n)=>n.naturalWidth') == 960
            assert page.evaluate('liveUrls.size') == 2
            page.keyboard.press('Escape')
            assert not zoom.count()
            assert opener.evaluate('(n)=>n===document.activeElement')
            assert page.evaluate('liveUrls.size') == 1
            page.screenshot(path=str(OUTPUT / f'library-rich-{width}.png'), full_page=True)
            failed = body.locator('img[data-asset-id="failed"]')
            # A fast 404 already hides the image; its visible wrapper retains the
            # failure message and is the stable scroll target in either state.
            failed.locator('../..').scroll_into_view_if_needed()
            body.get_by_text('图片读取失败', exact=False).wait_for()
            assert '附录图片' in body.inner_text()
            assert page.locator('#source-detail-drawer').evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.get_by_role('button', name='已保存正文', exact=True).click()
            assert body.inner_text() == '已保存纯文本 BEFORE AFTER'
            assert page.evaluate('liveUrls.size') == 0
            page.get_by_role('button', name='图文阅读', exact=True).click()
            body.locator('h1').wait_for()
            page.get_by_role('button', name='关闭 ×', exact=True).first.click()
            assert page.evaluate('liveUrls.size') == 0
            assert errors == []
            page.close()
        browser.close()


def test_fixed_reader_downloads_and_late_read_cancellation():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=EDGE)
        page, calls, errors = reader_page(browser)
        page.evaluate("SourceReader.open({projectId:'p',versionId:'v'})")
        page.locator('.source-reader-body h1').wait_for()
        page.get_by_role('button', name='原始视图', exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('link', name='下载当前 Markdown', exact=True).click()
        assert download.value.suggested_filename == 'guide.md'
        with page.expect_download() as package:
            page.get_by_role('link', name='下载原始 ZIP 知识包', exact=True).click()
        assert package.value.suggested_filename == 'guide.zip'
        assert any(url.endswith('/package') for url in calls)
        page.keyboard.press('Escape')
        page.wait_for_function("!document.getElementById('source-reader').open && liveUrls.size===0")
        assert page.evaluate('liveUrls.size') == 0
        # Deliberately ignore abort to prove stale responses cannot repaint a closed drawer.
        page.evaluate("""() => {const previous=fetch;window.fetch=(url,options)=>String(url).endsWith('/rendered')?new Promise(resolve=>{window.lateRead={resolve,signal:options.signal}}):previous(url,options)}""")
        page.locator('.source-item').click()
        page.wait_for_function('Boolean(window.lateRead)')
        page.get_by_role('button', name='关闭 ×', exact=True).first.click()
        assert page.evaluate('lateRead.signal.aborted')
        page.evaluate("lateRead.resolve(new Response('<p>STALE CONTENT</p>',{status:200}))")
        page.wait_for_timeout(100)
        assert page.locator('#source-body').inner_text() == ''
        assert page.evaluate('liveUrls.size') == 0
        assert errors == []
        browser.close()


def test_late_image_blob_is_discarded_on_project_change_and_binary_view_is_labeled():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=EDGE)
        page, calls, errors = reader_page(browser)
        page.evaluate("""() => {const previous=fetch;window.fetch=(url,options)=>String(url).endsWith('/assets/good')?
          new Promise(resolve=>{window.lateImage={resolve,signal:options.signal}}):previous(url,options)}""")
        page.locator('.source-item').click()
        page.wait_for_function('Boolean(window.lateImage)')
        page.evaluate("current='other';document.getElementById('project').dispatchEvent(new Event('change'))")
        assert page.evaluate('lateImage.signal.aborted')
        page.evaluate("lateImage.resolve(new Response(new Blob(['late-image'],{type:'image/png'}),{status:200}))")
        page.wait_for_timeout(100)
        assert page.locator('#source-body').inner_text() == ''
        assert page.evaluate('liveUrls.size') == 0
        page.route('**/source-documents/binary', lambda route: route.fulfill(json={
            'original_name': 'guide.pdf', 'format': 'pdf', 'mime_type': 'application/pdf',
            'source_text': '来自 PDF 的解析正文', 'blocks': [], 'assets': []}))
        page.evaluate("SourceReader.open({projectId:'other',versionId:'binary'})")
        page.locator('#source-reader').wait_for()
        page.get_by_role('button', name='原始视图', exact=True).click()
        page.get_by_text('下方是解析后的正文；原始二进制文件请通过下载入口查看。', exact=True).wait_for()
        assert page.locator('.source-reader-body pre').inner_text() == '来自 PDF 的解析正文'
        assert page.locator('.source-reader-body iframe,.source-reader-body embed').count() == 0
        assert not any(url.endswith('/binary/source') for url in calls), 'binary bytes are fetched only for explicit downloads'
        page.keyboard.press('Escape')
        assert errors == []
        browser.close()


if __name__ == '__main__':
    test_library_rich_reading_widths_zoom_and_fallback()
    test_fixed_reader_downloads_and_late_read_cancellation()
    test_late_image_blob_is_discarded_on_project_change_and_binary_view_is_labeled()
    print('source rich reading browser fixtures passed')
