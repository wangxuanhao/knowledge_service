"""Search controls work in the actual library at desktop and mobile widths."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from test_workspace_redesign_ui import redesign_page


@pytest.mark.parametrize('width,height', [(1440, 950), (390, 844)])
def test_library_search_enter_clear_and_mobile_layout(redesign_page, width, height):
    page, errors = redesign_page
    requests = []
    docs = [
        {'id': 'refund', 'metadata': {'title': '退款申请处理规则', 'status': 'ready'}},
        {'id': 'other', 'metadata': {'title': '其他资料', 'status': 'failed'}},
    ]

    def directory(route):
        query = parse_qs(urlparse(route.request.url).query).get('query', [''])[0]
        requests.append(query)
        found = [doc for doc in docs if query in doc['metadata']['title']]
        route.fulfill(json={'documents': found, 'total': len(found),
            'counts': {'all': len(found), 'ready': sum(d['id'] == 'refund' for d in found),
                       'failed': sum(d['id'] == 'other' for d in found), 'processing': 0}})

    page.route('**/api/projects/p/sources**', directory)
    page.click('[data-tab="sources"]')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===2")
    page.set_viewport_size({'width': width, 'height': height})
    search = page.get_by_role('searchbox', name='搜索资料')
    assert not page.locator('#source-search-steps').is_visible()
    assert not page.locator('.source-search__actions').is_visible()
    if width >= 1000:
        assert page.locator('#source-search-form').bounding_box()['height'] <= 45
        toolbar = page.locator('.source-results-toolbar').bounding_box()
        assert toolbar['height'] <= 65
    search.fill('退款')
    search.press('Enter')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===1")
    assert requests[-1] == '退款'
    assert '退款申请处理规则' in page.locator('#source-list').inner_text()
    assert '搜索“退款”' in page.locator('.source-summary').inner_text()
    assert page.locator('#all-count').inner_text() == '1'
    form = page.locator('#source-search-form')
    assert form.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    folder = Path(__file__).resolve().parents[2] / '.benchmarks' / 'source-library-search'
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f'search-{width}.png'), full_page=True)
    page.locator('#source-search-clear').click()
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===2")
    assert search.input_value() == '' and requests[-1] == ''
    assert errors == []


@pytest.mark.parametrize('width,height', [(1440, 950), (390, 844)])
def test_library_vector_mode_snippet_and_keyword_switch(redesign_page, width, height):
    page, errors = redesign_page
    requests = []
    term = '为什么过了几天钱还没回来'
    snippet = '退款申请审核通过后，款项通常在三个工作日内退回原支付账户。'
    doc = {'id': 'refund', 'metadata': {'title': '售后办理说明', 'status': 'ready'}}

    def directory(route):
        params = parse_qs(urlparse(route.request.url).query)
        mode = params.get('retrieval_mode', ['keyword'])[0]
        query = params.get('query', [''])[0]
        requests.append((mode, query))
        semantic = mode == 'semantic' and bool(query)
        found = [{**doc, 'match': {'score': .87, 'chunk_id': 'chunk-refund',
            'chunk_version_id': 'chunk-version', 'text': snippet}}] if semantic else ([] if query else [doc])
        route.fulfill(json={'documents': found, 'total': len(found), 'next_cursor': None,
            'counts': {'all': len(found), 'ready': len(found), 'failed': 0, 'processing': 0},
            **({'search_mode': 'semantic', 'result_limit': 100, 'matched_chunk_count': 2,
                'total_is_exact': False, 'complete': True} if semantic else {})})

    page.route('**/api/projects/p/sources**', directory)
    page.click('[data-tab="sources"]')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===1")
    page.set_viewport_size({'width': width, 'height': height})
    mode = page.get_by_label('搜索方式', exact=True)
    mode.select_option('semantic')
    search = page.get_by_role('searchbox', name='搜索资料')
    search.fill(term)
    search.press('Enter')
    page.wait_for_function("document.querySelector('#source-list')?.textContent.includes('三个工作日')")
    assert requests[-1] == ('semantic', term)
    assert snippet in page.locator('#source-list').inner_text()
    summary = page.locator('.source-summary').inner_text()
    assert '召回' in summary and '100' in summary
    assert '当前范围共' not in summary
    assert page.locator('#source-search-form').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    folder = Path(__file__).resolve().parents[2] / '.benchmarks' / 'source-library-search'
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f'semantic-{width}.png'), full_page=True)
    mode.select_option('keyword')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===0")
    assert requests[-1] == ('keyword', term)
    assert search.input_value() == term
    assert errors == []


@pytest.mark.parametrize('width,height', [(1440, 950), (390, 844)])
def test_library_progressive_search_back_remove_and_mobile_layout(redesign_page, width, height):
    page, errors = redesign_page
    requests = []
    docs = [{'id': name, 'metadata': {'title': title, 'status': 'ready'}} for name, title in
            [('a', '退款到账规则'), ('b', '退款申请流程'), ('c', '配送规则')]]

    def directory(route):
        params = parse_qs(urlparse(route.request.url).query)
        query = params.get('query', [''])[0]
        mode = params.get('retrieval_mode', ['keyword'])[0]
        steps = route.request.post_data_json.get('search_steps', [])
        requests.append((steps, query, mode))
        found = docs[:1] if steps and query else (docs[:2] if query == '退款' else docs)
        bounded = bool(steps and mode == 'semantic' and query)
        result = {'documents': found, 'total': len(found), 'next_cursor': None,
            'counts': {'all': len(found), 'ready': len(found), 'processing': 0, 'failed': 0},
            'bounded_by_semantic': bounded, 'complete': True}
        if bounded:
            result['documents'] = [{**found[0], 'match': {'text': '审核通过后三个工作日内到账。'}}]
            result.update(search_mode='semantic', result_limit=100, total_is_exact=False)
        route.fulfill(json=result)

    page.route('**/api/projects/p/sources**', directory)
    page.click('[data-tab="sources"]')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===3")
    page.set_viewport_size({'width': width, 'height': height})
    search = page.get_by_role('searchbox', name='搜索资料')
    search.fill('退款')
    search.press('Enter')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===2")
    page.locator('#source-search-refine').click()
    assert search.input_value() == ''
    assert page.locator('#source-list .source-item').count() == 2
    page.get_by_label('搜索方式', exact=True).select_option('semantic')
    assert page.locator('#source-list .source-item').count() == 2
    search.fill('多久到账')
    search.press('Enter')
    page.wait_for_function("document.querySelector('#source-list')?.textContent.includes('三个工作日')")
    assert requests[-1] == ([{'query': '退款', 'retrieval_mode': 'keyword'}], '多久到账', 'semantic')
    chain = page.locator('#source-search-steps')
    assert '退款' in chain.inner_text() and '多久到账' in chain.inner_text()
    assert '召回' in page.locator('.source-summary').inner_text()
    assert page.locator('#source-search-form').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    page.locator('#source-list .source-item').first.scroll_into_view_if_needed()
    folder = Path(__file__).resolve().parents[2] / '.benchmarks' / 'source-library-search'
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f'progressive-{width}.png'), full_page=True)
    page.locator('#source-search-back').click()
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===2")
    assert requests[-1] == ([], '退款', 'keyword')
    assert search.input_value() == '退款'
    chain.get_by_role('button').first.click()
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length===3")
    assert requests[-1][0] == [] and requests[-1][1] == ''
    assert errors == []
