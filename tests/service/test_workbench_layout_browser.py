"""Render production CSS and batch controller against deterministic API responses."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'


@pytest.fixture
def layout_page():
    playwright = pytest.importorskip('playwright.sync_api')
    if not Path(EDGE).exists():
        pytest.skip('Edge is required')
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=EDGE)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        state = {'fail': False, 'requests': [], 'unexpected': [], 'errors': []}
        page.on('pageerror', lambda error: state['errors'].append(str(error)))
        batch = {'id': 'batch-a', 'status': 'completed', 'counts': {'completed': 1},
                 'requested_concurrency': 'auto', 'effective_concurrency': 4}
        state['batch'] = batch
        state['item'] = {'id': 'file-a', 'original_name': '平台基础规则_价格说明.md',
                         'status': 'completed', 'source_version_id': 'version-a', 'document_id': 'document-a'}
        styles = ''.join(re.findall(r'<link[^>]+rel="stylesheet"[^>]*>',
                                    (WEB / 'index.html').read_text(encoding='utf-8')))
        html = f'''<html><head><meta charset="utf-8">{styles}</head><body class="is-admin"><main>
          <select id="project"><option value="a">A</option><option value="b">B</option></select>
          <div id="ingest-batches"></div><div id="ingest-batch-tasks"></div>
          <section id="tab-dashboard"><div class="metric-card"><span class="metric-icon">文</span></div></section>
          <section class="ontology-model"><div class="om-page">
            <div class="om-version-context"><span class="om-version-context-current">当前生效本体：v1</span></div>
            <div class="om-body"><div class="om-side"><div class="om-discovery-inline">
              <button class="om-btn primary">把这 58 个新候选生成进本体</button>
            </div></div><div class="om-canvas-host"></div><div class="om-detail">术语详情</div></div>
          </div></section>
          </main><script>window.Auth={{isAdmin:()=>true}};
            window.status=(message,isError)=>{{window.__layoutStatus={{message,isError:Boolean(isError)}};}};</script>
          <script src="/assets/ingest-batches.js"></script></body></html>'''

        def respond(route):
            request = route.request
            parsed = urlsplit(request.url)
            path, query = parsed.path, parse_qs(parsed.query, keep_blank_values=True)
            if path.startswith('/assets/'):
                file = WEB / path.removeprefix('/assets/')
                route.fulfill(body=file.read_bytes(), content_type=(
                    'text/css' if file.suffix == '.css' else 'text/javascript'))
            elif path == '/':
                route.fulfill(body=html, content_type='text/html')
            else:
                body = request.post_data_json if request.method == 'POST' else None
                call = {'method': request.method, 'path': path, 'query': query, 'body': body}
                state['requests'].append(call)
                if path == '/api/ingest/capabilities' and request.method == 'GET' and not query:
                    route.fulfill(json={'concurrency_choices': ['auto', 1, 4]})
                elif path in ('/api/projects/a/ingest-batches', '/api/projects/b/ingest-batches'):
                    assert request.method == 'GET' and query == {'limit': ['20']}
                    route.fulfill(status=503, json={'detail': '服务暂不可用'}) if state['fail'] else route.fulfill(
                        json={'items': [batch] if '/projects/a/' in path else []})
                elif path == '/api/projects/a/ingest-batches/batch-a' and request.method == 'GET' and not query:
                    route.fulfill(json=batch)
                elif path == '/api/projects/a/ingest-batches/batch-a/items':
                    assert request.method == 'GET' and query == {'limit': ['50'], 'status': ['']}
                    route.fulfill(json={'items': [state['item']], 'next_cursor': None})
                elif path == '/api/projects/a/ingest-batches/batch-a/cancel':
                    assert request.method == 'POST' and not query and body == {}
                    assert batch['counts'].get('retry_wait') == 1
                    batch.update(status='cancelled', counts={'cancelled': 1})
                    state['item']['status'] = 'cancelled'
                    route.fulfill(json=batch)
                else:
                    state['unexpected'].append(call)
                    route.abort()

        page.route('http://workbench.test/**', respond)
        page.goto('http://workbench.test/')
        assert state['requests'] == [], 'Controller readiness must not start hidden batch reads'
        page.evaluate('()=>IngestBatches.loadRecent()')
        assert [call['path'] for call in state['requests']] == ['/api/projects/a/ingest-batches']
        assert page.locator('.batch-item').count() == 0
        page.get_by_role('button', name='查看批次详情', exact=True).click()
        page.wait_for_selector('.batch-item')
        assert [call['path'] for call in state['requests']] == [
            '/api/projects/a/ingest-batches', '/api/projects/a/ingest-batches/batch-a',
            '/api/projects/a/ingest-batches/batch-a/items']
        try:
            yield page, state
            assert state['unexpected'] == []
            assert state['errors'] == []
        finally:
            browser.close()


def test_batch_file_actions_are_grouped_and_do_not_stretch(layout_page):
    page, _ = layout_page
    widths = page.locator('.batch-item').evaluate('''row => ({row:row.clientWidth,
        buttons:[...row.querySelectorAll('button')].map(b=>b.getBoundingClientRect().width)})''')
    assert all(width < widths['row'] / 2 for width in widths['buttons'])


def test_completed_batch_disables_inapplicable_actions(layout_page):
    page, _ = layout_page
    for label in ['重试全部失败', '取消未开始文件', '下载失败明细']:
        assert page.get_by_role('button', name=label, exact=True).is_disabled()


def test_waiting_for_automatic_retry_can_be_cancelled(layout_page):
    page, state = layout_page
    state['batch'].update(status='running', counts={'retry_wait': 1})
    state['item']['status'] = 'retry_wait'
    page.get_by_role('button', name='收起文件', exact=True).click()
    page.get_by_role('button', name='查看批次详情', exact=True).click()
    page.get_by_role('button', name='取消未开始文件', exact=True).wait_for()
    page.wait_for_function("[...document.querySelectorAll('.batch-actions button')].some(b=>b.textContent==='取消未开始文件'&&!b.disabled)")
    page.get_by_role('button', name='取消未开始文件', exact=True).click()
    page.wait_for_function("document.querySelector('[data-batch-id=\"batch-a\"]').dataset.status==='cancelled'")
    assert page.get_by_role('button', name='取消未开始文件', exact=True).is_disabled()
    assert [call for call in state['requests'] if call['method'] == 'POST'] == [{
        'method': 'POST', 'path': '/api/projects/a/ingest-batches/batch-a/cancel', 'query': {}, 'body': {}}]
    assert not page.evaluate('Boolean(window.__layoutStatus?.isError)')


def test_list_errors_are_deduplicated_and_clear_after_recovery(layout_page):
    page, state = layout_page
    state['fail'] = True
    page.evaluate('async()=>{await IngestBatches.loadRecent();await IngestBatches.loadRecent();}')
    assert page.get_by_text(re.compile('批次列表读取失败')).count() == 1
    state['fail'] = False
    page.evaluate('()=>IngestBatches.loadRecent()')
    assert page.get_by_text(re.compile('批次列表读取失败')).count() == 0


def test_switch_project_hides_completed_batches(layout_page):
    page, _ = layout_page
    page.select_option('#project', 'b')
    page.wait_for_function("!document.querySelector('#ingest-batches .ingest-batch-card:not([hidden])')")


def test_theme_and_narrow_ontology_controls_stay_inside_containers(layout_page):
    page, _ = layout_page
    colors = page.locator('.om-version-context').evaluate('''e=>({
        actual:getComputedStyle(e).backgroundColor,
        expected:getComputedStyle(document.documentElement).getPropertyValue('--ui-surface-2').trim()})''')
    assert colors['actual'] == 'rgb(238, 245, 245)'
    icon = page.locator('.metric-icon').bounding_box()
    assert icon['width'] <= 48
    for width in [1280, 1024, 768]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.locator('.om-body').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        assert page.locator('.om-discovery-inline').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
