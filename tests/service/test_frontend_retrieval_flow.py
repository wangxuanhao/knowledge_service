"""Real-browser contract for the linked search/graph workspace and the one-shot knowledge chat."""
from ontology_seed import seed_ontology
import json
import re
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import uvicorn

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology

INITIAL_GRAPH_HINT = '从左侧检索结果选择实体或关系'
CHANNEL_LABELS = ['匹配实体', '原文片段', '关系链路']
SEARCH_BODY_FIELDS = {
    'query', 'retrieval_mode', 'filters', 'valid_at', 'known_at', 'include_unknown',
    'k_entities', 'k_chunks', 'k_relations',
}
SUBGRAPH_BODY_FIELDS = {
    'seeds', 'hops', 'direction', 'max_nodes', 'max_edges',
    'filters', 'valid_at', 'known_at', 'include_unknown', 'ontology_scope', 'ontology_ids',
}


def _is_graph_exchange(exchange):
    """A canvas neighborhood; hops=0 only enriches a record inspector."""
    request = exchange.request if hasattr(exchange, 'request') else exchange
    return (request.method == 'POST'
            and urlparse(request.url).path.endswith('/graph-neighborhood')
            and (request.post_data_json or {}).get('hops', 1) > 0)


def _browser_path():
    candidates = [
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path('/usr/bin/google-chrome'),
        Path('/usr/bin/chromium'),
    ]
    return next((str(path) for path in candidates if path.exists()), None)


def _project(repository, name):
    """Mirror POST /api/projects so the fixture matches the real ontology-backed boot path."""
    project_id = repository.create_project(name, {'ontology_mode': 'ontology'})['id']
    turtle = (Path(__file__).resolve().parents[2] / 'knowledge_service' /
              'resources' / 'default_ontology.ttl').read_text(encoding='utf-8')
    seed_ontology(repository, project_id, turtle, Ontology(turtle).summary())
    return project_id


def _seed(repository, project_id):
    rows = [
        {'id': 'a', 'kind': 'entity', 'type': 'Thing', 'text': '退款商户', 'metadata': {}},
        {'id': 'b', 'kind': 'entity', 'type': 'Thing', 'text': '退款平台', 'metadata': {}},
        {'id': 'x', 'kind': 'entity', 'type': 'Thing', 'text': '无关实体', 'metadata': {}},
        {'id': 's', 'kind': 'entity', 'type': 'Service', 'text': '结算服务', 'metadata': {}},
        {'id': 'r', 'kind': 'relation', 'type': 'mentions', 'text': '退款关系',
         'subject_id': 'a', 'object_id': 'b', 'metadata': {}},
        {'id': 'r-alt', 'kind': 'relation', 'type': 'ignores', 'text': '忽略关系',
         'subject_id': 'b', 'object_id': 'x', 'metadata': {}},
        {'id': 'r-cross', 'kind': 'relation', 'type': 'supports', 'text': '服务支持',
         'subject_id': 'a', 'object_id': 's', 'metadata': {}},
        {'id': 'c', 'kind': 'chunk', 'text': '退款需要原始凭证', 'source_id': 'd', 'metadata': {}},
        {'id': 'd', 'kind': 'document', 'text': '退款规则原文：退款需要原始凭证',
         'metadata': {'title': '退款规则'}},
    ]
    # Real QA uses fixed, effective sources and accepted assertion mappings.
    # Keep the document and its pinned chunk in the same ingest operation so
    # evidence resolution can identify the exact source occurrence.
    for row in rows:
        row['valid_from'] = '2020-01-01'
        row['metadata']['effective_time_source'] = 'explicit'
        row['metadata']['applicability'] = {'platforms': {'mode': 'all'}}
    operation = repository._reserve_record_operation(project_id)
    document = repository._put_record_for_operation(project_id, rows[-1], 0, operation)
    saved = {}
    for row in rows[:-1]:
        if row['kind'] == 'chunk':
            row['metadata']['source_version_id'] = document['version_id']
            row['metadata']['start_char'] = document['text'].index(row['text'])
            row['metadata']['end_char'] = row['metadata']['start_char'] + len(row['text'])
        saved[row['id']] = repository._put_record_for_operation(project_id, row, 0, operation)
    chunk = saved['c']
    start = document['text'].index(chunk['text'])
    for row in saved.values():
        if row['kind'] not in {'entity', 'relation'}:
            continue
        assertion = repository.create_assertion(project_id, {
            'id': f"{project_id}:fixture:{row['id']}", 'kind': row['kind'],
            'payload': {'text': row['text']}, 'document_id': document['id'],
            'document_version_id': document['version_id'], 'chunk_id': chunk['id'],
            'quote': chunk['text'], 'start_char': start, 'end_char': start + len(chunk['text']),
        })
        repository.transition_assertion(project_id, assertion['id'], 1, 'accepted',
                                        'browser fixture verified', 'fixture',
                                        canonical_record_id=row['id'])
        event = repository.list_assertion_events(project_id, assertion['id'])[-1]
        repository.add_record_version_assertion(project_id, row['id'], row['version_id'],
                                                assertion['id'], event['id'])


@pytest.fixture(scope='module')
def browser():
    playwright = pytest.importorskip('playwright.sync_api')
    executable = _browser_path()
    if not executable:
        pytest.skip('A Chromium browser is required for the real interaction test')
    with playwright.sync_playwright() as runtime:
        instance = runtime.chromium.launch(headless=True, executable_path=executable)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def workbench(browser, tmp_path):
    app = create_app(tmp_path / 'browser.sqlite', HashingEncoder())
    repository = app.state.service.repository
    project_id = _project(repository, '浏览器交互')
    _seed(repository, project_id)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started

    context = browser.new_context()
    page = context.new_page()
    errors, paths = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    # Inspector enrichment uses the same endpoint with hops=0 and never redraws
    # the canvas. Keep it separate so no-redraw assertions remain about rendering.
    detail_paths = []
    def log_request(request):
        if '/api/' not in request.url:
            return
        path = urlparse(request.url).path
        if path.endswith('/graph-neighborhood') and not _is_graph_exchange(request):
            detail_paths.append(path)
        else:
            paths.append(path)
    page.on('request', log_request)
    page.goto(f'http://127.0.0.1:{port}/', wait_until='load')
    page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
    # Project selection initializes only the visible graph workspace. The ledger
    # owns /records/page and does not load until its page is opened.
    page.select_option('#project', project_id)
    page.wait_for_function('project => current === project', arg=project_id)
    page.wait_for_function(
        "document.querySelector('#graph-summary').textContent !== "
        f"{json.dumps(INITIAL_GRAPH_HINT)}")
    # Interaction tests start from an explicitly seeded bounded fixture graph.
    # Product boot itself remains unseeded and does not scan the whole project.
    page.evaluate("""async () => {
      document.getElementById('graph-node').value='a';
      await window.drawGraph('a',3);
    }""")
    paths.clear()
    try:
        yield SimpleNamespace(page=page, project=project_id, paths=paths, detail_paths=detail_paths, errors=errors,
                              service=app.state.service)
    finally:
        context.close()
        server.should_exit = True
        thread.join(timeout=10)


def _channel_sections(page):
    # Applicability summaries precede the three ranked retrieval channels.
    return page.locator('#hits > section').filter(
        has=page.locator('.hit-kind').filter(
            has_text=re.compile(r'^(匹配实体|原文片段|关系链路)')))


def _search(page, query='退款', mode='keyword'):
    page.select_option('#search-mode', mode)
    page.fill('#query', query)
    with page.expect_response(lambda response: response.url.endswith('/search')):
        page.click('#search')
    page.wait_for_function("""() => [...document.querySelectorAll('#hits > section > .hit-kind')]
        .filter(el => /^(匹配实体|原文片段|关系链路)/.test(el.textContent)).length === 3""")


def _select_keyword_qa(page):
    """The browser fixture has no vector service; select its real supported mode."""
    page.locator('#qa-mode + .ks-select .ks-select-trigger').click()
    page.locator('#qa-mode + .ks-select [data-value="keyword"]').click()
    assert page.locator('#qa-mode').input_value() == 'keyword'
    assert not page.locator('#generate').is_checked()


def _expand_qa_evidence(page, index=0):
    toggle = page.locator('#qa-transcript .qa-evidence-toggle').nth(index)
    assert toggle.get_attribute('aria-expanded') == 'false'
    panel = page.locator('#' + toggle.get_attribute('aria-controls'))
    assert panel.is_hidden()
    toggle.click()
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert panel.is_visible()
    return panel


def test_ontology_preview_sets_ephemeral_version_context_and_project_change_resets_it(
        workbench):
    page = workbench.page
    empty = {
        'ontologyScope': 'all', 'ontologyIds': None,
        'ontologyVersion': None, 'source': None,
    }
    assert page.evaluate('window.wb.versionContext') == empty

    release = page.evaluate(
        "async (projectId) => {const response = await fetch("
        "'/api/projects/' + encodeURIComponent(projectId) + '/ontologies'); "
        "const body = await response.json(); return body.versions[0];}",
        workbench.project,
    )
    page.evaluate(
        "async (release) => {await window.OntologyModel.previewVersion("
        "release.id, release.version);}",
        release,
    )
    assert page.evaluate('window.wb.versionContext') == {
        'ontologyScope': 'ids', 'ontologyIds': [release['id']],
        'ontologyVersion': release['version'], 'source': 'ontology-history',
    }
    page.evaluate("async () => {await window.OntologyModel.clearPreview();}")
    assert page.evaluate('window.wb.versionContext') == empty
    page.evaluate(
        "async (release) => {await window.OntologyModel.previewVersion("
        "release.id, release.version);}",
        release,
    )

    other = page.evaluate(
        "async () => {const created = await (await fetch('/api/projects', {method: 'POST', "
        "headers: {'Content-Type': 'application/json'}, "
        "body: JSON.stringify({name: '版本上下文切换'})})).json(); "
        "await projects(); return created.id;}")
    with page.expect_response(lambda response: response.url.endswith('/records/page')):
        page.select_option('#project', other)
    assert page.evaluate('window.wb.versionContext') == empty

    page.evaluate("window.wb.setVersionContext({ontologyScope: 'unknown', source: 'test'})")
    page.reload(wait_until='load')
    page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
    assert page.evaluate('window.wb.versionContext') == empty


def test_ontology_context_strip_knowledge_context_and_ledger_navigation(workbench):
    page = workbench.page
    summary = {
        'classes': [{'id': 'urn:test:Thing', 'label_zh': '事项'}],
        'relations': [], 'attributes': [], 'triples': 1,
    }
    old_id = 'ontology-old-12345678'
    current_id = 'ontology-current-87654321'
    annotation_id = 'ontology-annotation-abcdef12'
    releases = [
        {'id': old_id, 'version': 1, 'version_reused': False,
         'created_at': '2026-10-01T08:00:00Z', 'summary': summary},
        {'id': current_id, 'version': 2, 'version_reused': False,
         'created_at': '2026-10-02T08:00:00Z', 'summary': summary},
        {'id': annotation_id, 'version': 2, 'version_reused': True,
         'created_at': '2026-10-03T08:00:00Z', 'summary': summary},
    ]
    context_state = {'error': False}

    def ontologies(route):
        route.fulfill(status=200, json={'versions': releases})

    def ontology(route):
        ontology_id = urlparse(route.request.url).query.replace('ontology_id=', '')
        selected = next((item for item in releases if item['id'] == ontology_id), releases[-1])
        route.fulfill(status=200, json={**selected, 'summary': summary})

    def knowledge_context(route):
        if context_state['error']:
            route.fulfill(status=500, json={'detail': 'temporary failure'})
            return
        route.fulfill(status=200, json={
            'ontology': {'id': old_id, 'version': 1, 'is_current': False,
                         'created_at': '2026-10-01T08:00:00Z'},
            'current_bound': {'total': 6, 'by_kind': {
                'entity': 3, 'relation': 2, 'attribute': 1}},
            'migration': {'pending': 4, 'blocked': 2},
            'history': {'revision_count': 9, 'record_count': 7, 'migrated_away': 1},
        })

    page.route(re.compile(r'/api/projects/[^/]+/ontologies(?:\?.*)?$'), ontologies)
    page.route(re.compile(r'/api/projects/[^/]+/ontology(?:\?.*)?$'), ontology)
    page.route(re.compile(r'/api/projects/[^/]+/ontologies/[^/]+/knowledge-context$'),
               knowledge_context)

    page.click('[data-tab="ontology-model"]')
    page.wait_for_function(
        "document.querySelector('#om-version-context')?.textContent.includes('当前生效本体：v2')")
    assert '正在查看：v2（当前）' in page.locator('#om-version-context').inner_text()

    page.click('#om-history')
    page.wait_for_selector('#om-history-overlay:not([hidden])')
    page.wait_for_function("document.querySelectorAll('.om-history-row').length === 3")
    history_text = page.locator('#om-history-body').inner_text()
    assert history_text.count('v2') >= 2
    assert '标注修订' in history_text
    assert 'ontology-' in history_text

    page.locator('.om-history-row').filter(has_text='v1').click()
    page.wait_for_function(
        "document.querySelector('.om-history-item.sel .om-knowledge-context')?.dataset.state === 'ready'")
    detail = page.locator(
        '.om-history-item.sel .om-knowledge-context').inner_text()
    for expected in ('当前关联', '6 条', '实体 3', '关系 2', '属性 1',
                     '待迁移', '4', '迁移阻塞', '2', '历史修订', '9', '已迁出 1'):
        assert expected in detail

    page.locator('.om-history-item.sel').get_by_role(
        'button', name='查看这一版（只读）').click()
    page.wait_for_function(
        "document.querySelector('#om-version-context')?.textContent.includes('正在查看：v1（历史·只读）')")
    assert '当前生效本体：v2' in page.locator('#om-version-context').inner_text()

    page.click('#om-history')
    page.wait_for_selector('#om-history-overlay:not([hidden])')
    page.wait_for_function(
        "document.querySelector('.om-history-item.sel .om-knowledge-context')?.dataset.state === 'ready'")
    page.locator('.om-history-item.sel').get_by_role(
        'button', name='在知识台账查看').click()
    assert page.locator('#tab-records').is_visible()
    assert page.evaluate('window.wb.versionContext') == {
        'ontologyScope': 'ids', 'ontologyIds': [old_id],
        'ontologyVersion': 1, 'source': 'ontology-history',
    }

    page.evaluate("showTab('ontology-model')")
    page.click('#om-history')
    context_state['error'] = True
    page.locator('.om-history-row').filter(has_text='v2').first.evaluate(
        'button => button.click()')
    page.wait_for_function(
        "document.querySelector('.om-history-item.sel .om-knowledge-context')?.dataset.state === 'error'")
    error_text = page.locator(
        '.om-history-item.sel .om-knowledge-context').inner_text()
    assert '读不到关联知识' in error_text
    assert '—' in error_text

def _watch_graph_clear(page, selector, snapshot_name, event='click'):
    page.evaluate("""([selector, snapshotName, event]) => {
      document.querySelector(selector).addEventListener(event, () => {
        window[snapshotName] = {
          hits: document.getElementById('hits').innerHTML,
          summary: document.getElementById('graph-summary').textContent,
          detail: document.getElementById('graph-detail').innerHTML,
          node: document.getElementById('graph-node').value,
        };
      }, {once: true});
    }""", [selector, snapshot_name, event])


def _graph_clear_snapshot(page, snapshot_name):
    return page.evaluate("snapshotName => window[snapshotName]", snapshot_name)


def _trigger_graph_click(page, data_type, row_id):
    page.evaluate("""([dataType, rowId]) => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      chart.trigger('click', {dataType, data: {id: rowId}});
    }""", [data_type, row_id])


def _graph_node_ids(page):
    return page.evaluate("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = (chart?.getOption().series || []).find(item => item.type === 'graph');
      return (series?.data || []).map(item => String(item.id)).sort();
    }""")


def _graph_edge_ids(page):
    return page.evaluate("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = (chart?.getOption().series || []).find(item => item.type === 'graph');
      return (series?.links || series?.edges || []).map(item => String(item.id)).sort();
    }""")


def _assert_complete_entity_inspector(page, text):
    detail = page.locator('#graph-detail')
    detail_text = detail.inner_text()
    assert text in detail_text
    assert '修订历史 · 修订 r1' in detail_text
    assert '新增关系' in detail_text
    assert '展开脑图' in detail_text
    assert 'undefined' not in detail_text


def _render_a_neighborhood(page):
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.click('#graph-expand')
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series && !series.data.some(item => item.id === 'x');
    }""")


def _hold_record_request(page, record_id='x'):
    pattern = re.compile(r'/records/page$')
    held = []

    def hold(route):
        if _is_record_exchange(route.request, record_id):
            held.append(route)
        else:
            route.continue_()

    page.route(pattern, hold)
    return pattern, hold, held


def _is_record_exchange(exchange, record_id='x'):
    request = exchange.request if hasattr(exchange, 'request') else exchange
    return (request.method == 'POST'
            and urlparse(request.url).path.endswith('/records/page')
            and (request.post_data_json or {}).get('record_ids') == [record_id])


def _release_record_request(page, held, record_id='x'):
    assert len(held) == 1
    with page.expect_response(lambda response: _is_record_exchange(response, record_id)) as info:
        held.pop().continue_()
    response = info.value
    response.body()
    page.evaluate("""() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
    page.wait_for_function("!document.querySelector('#graph-expand').disabled")
    return response


def _stable_graph_summary(page):
    summary = page.locator('#graph-summary').inner_text()
    assert '局部邻域' in summary
    return summary


def _settle_graph(page, timeout_ms=6000):
    """等图谱首屏渲染真的落地，再对 #graph-summary 做快照。

    摘要由"最后一次图谱渲染"写入，而渲染是异步的（还要跑力导向布局）。
    以前这些用例是"点完立刻读"，靠够快侥幸赢；布局一慢就会读到渲染前的值，
    表现为 `服务 64.7 ms` vs `服务 15.4 ms` 这种只有耗时不同的假失败。
    这里要求连续两次读到同一个值，才认为落定。
    """
    previous = None
    waited = 0
    while waited < timeout_ms:
        current = page.locator('#graph-summary').inner_text()
        if current and current == previous and ('局部邻域' in current or '请选择实体' in current):
            return current
        previous = current
        page.wait_for_timeout(100)
        waited += 100
    return previous or ''


# ── Task 1: the combined workspace keeps search, graph and details decoupled ──

def test_search_updates_only_the_result_rail_and_ignores_graph_keys(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')

    # The project boot may intentionally populate the graph. Search owns only the
    # result rail, so compare against the settled graph state rather than racing it.
    summary_before = _settle_graph(page)   # 先等首屏渲染落定，再快照（否则会和异步渲染赛跑）
    graph_before = page.locator('#graph-canvas').evaluate('(el) => el.outerHTML')
    detail_before = page.locator('#graph-detail').evaluate('(el) => el.outerHTML')
    # 只统计"检索动作"发出的请求：进入检索页本身会按最新本体补画一次图谱
    # （"切回页签自动跟上新版本"），那是页面进入行为，不属于这条断言的射程。
    workbench.paths.clear()
    _search(page)

    # Every channel owns a heading, a quota count and its own empty state.
    sections = _channel_sections(page)
    assert sections.count() == 3
    for index, label in enumerate(CHANNEL_LABELS):
        heading = sections.nth(index).locator('.hit-kind').inner_text()
        assert heading.startswith(label)
        hits = sections.nth(index).locator('.hit-entry').count()
        assert f'{hits} /' in heading
        assert sections.nth(index).locator('p.subtle').count() == (1 if hits == 0 else 0)

    # The renderer must not turn stray graph keys in the response into a graph.
    def inject(route):
        response = route.fetch()
        payload = response.json()
        payload['nodes'] = [{'id': 'zzz', 'text': '不应出现的节点', 'type': 'Thing'}]
        payload['edges'] = [{'id': 'zzz-edge', 'subject_id': 'zzz', 'object_id': 'zzz', 'type': 'x'}]
        route.fulfill(response=response, json=payload)

    page.route('**/search', inject)
    try:
        with page.expect_response(lambda response: response.url.endswith('/search')):
            page.click('#search')
    finally:
        page.unroute('**/search', inject)
    page.wait_for_function("!document.querySelector('#search').disabled")

    assert '不应出现的节点' not in page.locator('#hits').inner_text()
    assert page.locator('#graph-canvas').evaluate('(el) => el.outerHTML') == graph_before
    assert page.locator('#graph-detail').evaluate('(el) => el.outerHTML') == detail_before
    assert page.locator('#graph-summary').inner_text() == summary_before
    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)


def test_graph_node_click_updates_only_details(workbench):
    page = workbench.page
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()

    _trigger_graph_click(page, 'node', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    page.wait_for_timeout(120)

    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == summary_before
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-entity-choice').input_value() == 'a'


def test_graph_edge_click_updates_only_details(workbench):
    page = workbench.page
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()

    _trigger_graph_click(page, 'edge', 'r')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款关系')")
    page.wait_for_timeout(120)

    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == summary_before


def test_entity_selection_requires_explicit_expand_and_full_graph_ignores_selection(workbench):
    """Selection changes details; local redraw stays seeded and full mode is explicit."""
    page = workbench.page
    original_nodes = _graph_node_ids(page)
    assert 'x' in original_nodes
    summary_before = page.locator('#graph-summary').inner_text()
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款商户')")
    _assert_complete_entity_inspector(page, '退款商户')
    assert _graph_node_ids(page) == original_nodes
    assert page.locator('#graph-summary').inner_text() == summary_before
    workbench.paths.clear()
    page.select_option('#graph-entity-choice', '')
    page.wait_for_function("document.querySelector('#graph-summary').textContent.includes('请选择实体')")
    assert page.locator('#graph-node').input_value() == ''
    assert page.locator('#graph-detail').inner_html() == ''
    assert _graph_node_ids(page) == []
    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款商户')")
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json) if _is_graph_exchange(request) else None)
    with page.expect_response(_is_graph_exchange):
        page.click('#graph-expand')
    page.wait_for_function("window.wb.nodes.has('b') && !window.wb.nodes.has('x')")
    expanded = _graph_node_ids(page)
    assert len(expanded) < len(original_nodes)
    with page.expect_response(_is_graph_exchange):
        page.click('#draw-graph')
    assert bodies[-1]['seeds'] == ['a']
    assert bodies[-1]['hops'] == 1
    assert _graph_node_ids(page) == expanded
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    workbench.paths.clear()
    page.click('[data-fg-mode="full"]')
    assert page.locator('.full-graph-section').is_visible()
    assert not page.locator('#graph-canvas').is_visible()
    assert page.locator('#graph-node').input_value() == 'a'
    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    page.click('[data-fg-mode="local"]')
    assert _graph_node_ids(page) == expanded
    assert '退款商户' in page.locator('#graph-detail').inner_text()


def test_entity_selection_fetches_complete_detail_outside_rendered_neighborhood(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    neighborhood_node_ids = _graph_node_ids(page)
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()

    pattern, handler, held = _hold_record_request(page)
    try:
        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()
        assert page.locator('#graph-node').input_value() == 'a'
        assert page.locator('#graph-entity-choice').input_value() == 'x'
        assert '退款商户' in page.locator('#graph-detail').inner_text()

        response = _release_record_request(page, held)
        assert response.status == 200
        page.wait_for_function("""() =>
          document.querySelector('#graph-detail').textContent.includes('无关实体')
          && !document.querySelector('#graph-expand').disabled""")
    finally:
        for route in held:
            route.abort()
        page.unroute(pattern, handler)

    assert f'/api/projects/{workbench.project}/records/page' in workbench.paths
    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    assert _graph_node_ids(page) == neighborhood_node_ids
    assert page.locator('#graph-summary').inner_text() == summary_before
    assert page.locator('#graph-node').input_value() == 'x'
    assert page.locator('#graph-entity-choice').input_value() == 'x'
    _assert_complete_entity_inspector(page, '无关实体')


def test_expand_stays_disabled_until_pending_entity_detail_resolves(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    subgraph_pattern = re.compile(r'/graph-neighborhood$')
    held_subgraphs = []

    def hold_subgraph(route):
        if _is_graph_exchange(route.request):
            held_subgraphs.append(route)
        else:
            route.continue_()

    page.route(subgraph_pattern, hold_subgraph)
    record_pattern, record_handler, held_records = _hold_record_request(page)
    try:
        with page.expect_request(lambda request: _is_graph_exchange(request)):
            page.click('#graph-expand')
        assert page.locator('#graph-expand').is_disabled()

        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()

        assert len(held_subgraphs) == 1
        with page.expect_response(
                lambda response: _is_graph_exchange(response)) as info:
            held_subgraphs.pop().continue_()
        assert info.value.status == 200
        info.value.body()
        page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)))""")

        assert page.locator('#graph-expand').is_disabled()

        response = _release_record_request(page, held_records)
        assert response.status == 200
        assert page.locator('#graph-expand').is_enabled()
    finally:
        for route in held_subgraphs + held_records:
            route.abort()
        page.unroute(subgraph_pattern, hold_subgraph)
        page.unroute(record_pattern, record_handler)


def test_entity_filter_removal_rolls_back_pending_detail(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    pattern, handler, held = _hold_record_request(page)
    try:
        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()

        with page.expect_response(lambda response: response.url.endswith('/records/page')
                                  and response.request.post_data_json.get('query') == '退款'):
            page.fill('#graph-entity-filter', '退款')
        page.wait_for_function("document.querySelector('#graph-entity-choice').value === 'a'")
        assert page.locator('#graph-entity-choice').input_value() == 'a'
        assert page.locator('#graph-node').input_value() == 'a'
        assert page.locator('#graph-expand').is_enabled()

        response = _release_record_request(page, held)
        assert response.status == 200
    finally:
        for route in held:
            route.abort()
        page.unroute(pattern, handler)

    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert '无关实体' not in page.locator('#graph-detail').inner_text()


def test_entity_filter_keeps_committed_fallback_when_no_entities_match(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    pattern, handler, held = _hold_record_request(page)
    try:
        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()

        with page.expect_response(lambda response: response.url.endswith('/records/page')
                                  and response.request.post_data_json.get('query') == '完全不匹配'):
            page.fill('#graph-entity-filter', '完全不匹配')
        page.wait_for_function("document.querySelector('#graph-entity-choice').value === 'a'")
        assert page.locator('#graph-entity-filter').input_value() == '完全不匹配'
        assert page.locator('#graph-entity-choice option').evaluate_all(
            '(options) => options.map(option => option.value)') == ['', 'a']
        assert page.locator('#graph-entity-choice').input_value() == 'a'
        assert page.locator('#graph-node').input_value() == 'a'
        assert page.locator('#graph-expand').is_enabled()

        response = _release_record_request(page, held)
        assert response.status == 200
    finally:
        for route in held:
            route.abort()
        page.unroute(pattern, handler)

    assert page.locator('#graph-entity-filter').input_value() == '完全不匹配'
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert '无关实体' not in page.locator('#graph-detail').inner_text()


def test_invalid_scope_draft_keeps_applied_scope_and_current_detail(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    page.click('#scope-toggle')
    page.locator('.cb-advanced').evaluate('(node)=>{node.open=true}')
    page.fill('#cb-raw-json','{')
    page.click('#apply-scope')
    assert 'JSON' in page.locator('#status').inner_text()
    assert page.evaluate('scope().filters') is None
    assert page.locator('#graph-node').input_value() == 'a'
    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert workbench.errors == []


def test_late_entity_detail_does_not_replace_relation_detail(workbench):
    page = workbench.page
    _render_a_neighborhood(page)

    pattern, handler, held = _hold_record_request(page)
    try:
        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()
        _trigger_graph_click(page, 'edge', 'r')
        page.wait_for_function(
            "document.querySelector('#graph-detail').textContent.includes('退款关系')")
        assert page.locator('#graph-entity-choice').input_value() == 'a'
        assert page.locator('#graph-node').input_value() == 'a'
        assert page.locator('#graph-expand').is_enabled()

        response = _release_record_request(page, held)
        assert response.status == 200
    finally:
        for route in held:
            route.abort()
        page.unroute(pattern, handler)

    detail = page.locator('#graph-detail').inner_text()
    assert '退款关系' in detail
    assert '无关实体' not in detail
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-expand').is_enabled()

    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.click('#graph-expand')
    assert bodies[-1]['seeds'][0] == 'a'


def test_late_entity_detail_does_not_reopen_closed_drawer(workbench):
    page = workbench.page
    _render_a_neighborhood(page)

    pattern, handler, held = _hold_record_request(page)
    try:
        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()
        page.evaluate("() => document.getElementById('detail-drawer-close').click()")
        assert page.locator('#graph-detail').inner_html() == ''
        assert page.locator('#graph-entity-choice').input_value() == 'a'
        assert page.locator('#graph-node').input_value() == 'a'
        assert page.locator('#graph-expand').is_enabled()

        response = _release_record_request(page, held)
        assert response.status == 200
    finally:
        for route in held:
            route.abort()
        page.unroute(pattern, handler)

    assert page.locator('#graph-detail').inner_html() == ''
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-expand').is_enabled()


def test_failed_entity_read_restores_committed_selection(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    detail_before = page.locator('#graph-detail').inner_html()

    def fail_record(route):
        route.fulfill(status=500, json={'detail': 'record failed'})

    pattern = re.compile(r'/records/page$')
    page.route(pattern, fail_record)
    try:
        with page.expect_response(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        page.wait_for_function(
            "document.querySelector('#status').textContent.includes('record failed')")
    finally:
        page.unroute(pattern, fail_record)

    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-detail').inner_html() == detail_before
    assert page.locator('#graph-expand').is_enabled()


def test_relation_detail_preserves_entity_selection_for_expand(workbench):
    page = workbench.page
    page.select_option('#graph-entity-choice', 'b')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款平台')")

    _trigger_graph_click(page, 'edge', 'r')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款关系')")
    assert page.locator('#graph-entity-choice').input_value() == 'b'
    assert page.locator('#graph-node').input_value() == 'b'

    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.click('#graph-expand')
    assert bodies[-1]['seeds'][0] == 'b'


def test_search_button_and_enter_each_issue_exactly_one_search(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    search_path = f'/api/projects/{workbench.project}/search'
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/search') else None)

    _search(page)
    assert workbench.paths.count(search_path) == 1

    workbench.paths.clear()
    page.fill('#query', '退款商户')
    page.wait_for_timeout(450)
    assert workbench.paths.count(search_path) == 0, 'typing must not search'

    with page.expect_response(lambda response: response.url.endswith('/search')):
        page.press('#query', 'Enter')
    assert workbench.paths.count(search_path) == 1

    assert len(bodies) == 2
    assert SEARCH_BODY_FIELDS <= set(bodies[1])
    assert bodies[1]['query'] == '退款商户'
    assert bodies[1]['retrieval_mode'] == 'keyword'
    assert bodies[1]['k_entities'] == 5 and bodies[1]['k_chunks'] == 5
    assert bodies[1]['k_relations'] == 5
    assert bodies[1]['known_at'] is None and bodies[1]['filters'] is None
    assert bodies[1]['include_unknown'] is True


def test_ontology_expansion_toggle_stays_off_by_default_and_explains_itself(workbench):
    """本体扩展开关（P0-1 的入口）：默认关、自带中文说明、开启后请求与会话栏都要说清楚。

    这个开关最容易"功能在、但没人知道它做什么"，所以锁三件事：
    ① 默认必须是关（关时检索路径与旧行为逐字节一致）；
    ② 悬停说明要说清"查父类也会命中子类、只补召回不放宽范围"；
    ③ 开启后的结果栏要写出命中的类与带出的子类数。
    """
    page = workbench.page
    page.click('[data-tab="search"]')
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/search') else None)

    _search(page)
    assert bodies[-1]['ontology_expansion'] is False, '默认必须是关闭'

    toggle = page.locator('#ontology-expansion')
    assert toggle.is_visible()
    # 说明挂在 label 上（鼠标悬停在文字上就能看到），不是挂在 input 上
    hint = page.locator('label.search-ontology-expansion').get_attribute('title') or ''
    assert '子类' in hint and '只补召回' in hint, '开关必须自带中文说明'

    # 勾上后检索一个本体里存在的父类：结果栏要解释"是靠本体层级补到的"
    page.check('#ontology-expansion')
    _search(page, query='Actor')
    assert bodies[-1]['ontology_expansion'] is True
    summary = page.locator('#search-summary').inner_text()
    assert '本体扩展' in summary and 'Actor' in summary
    assert '子类' in summary


def test_entity_and_relation_hits_select_consistent_detail_without_redraw(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    _search(page)
    graph_nodes = _graph_node_ids(page)
    graph_summary = page.locator('#graph-summary').inner_text()

    for index, expected_seed in ((0, 'a'), (2, 'a')):
        workbench.paths.clear()
        _channel_sections(page).nth(index).locator('[data-graph-node]').first.click()
        page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
        assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
        assert not any(path.endswith('/explore') or path.endswith('/search')
                       for path in workbench.paths)
        assert page.locator('#graph-entity-choice').input_value() == expected_seed
        assert page.locator('#graph-node').input_value() == expected_seed
        assert '退款商户' in page.locator('#graph-detail').inner_text()
        assert _graph_node_ids(page) == graph_nodes
        assert page.locator('#graph-summary').inner_text() == graph_summary
        assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'


def test_scope_application_preserves_qa_and_isolates_project_pages(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    page.click('#scope-toggle')
    page.fill('#query-platforms','美团')
    assert page.evaluate('scope().applicability || null') is None
    page.click('#apply-scope')
    assert page.evaluate('scope().applicability.platforms.values') == ['美团']
    assert '平台：美团' in page.locator('#applied-scope-chips').inner_text()
    page.click('[data-tab="qa"]')
    assert page.evaluate('scope().applicability || null') is None
    page.fill('#qa-query','退款')
    _select_keyword_qa(page)
    page.click('#qa')
    page.wait_for_function("() => document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    count=page.locator('#qa-transcript .qa-turn').count()
    page.click('#scope-toggle');page.fill('#query-platforms','淘宝');page.click('#apply-scope')
    assert page.locator('#qa-transcript .qa-turn').count() == count
    assert '仅影响下一次提问' in page.locator('#qa-summary').inner_text()
    page.click('[data-tab="search"]')
    assert page.evaluate('scope().applicability.platforms.values') == ['美团']
    assert workbench.errors == []


def test_graph_scope_filters_survive_expand_and_explicit_full_redraw(workbench):
    """Canvas type choices survive seed expansion and explicit bounded redraw."""
    page = workbench.page
    assert _graph_node_ids(page) == ['a', 'b', 's', 'x']
    page.select_option('#graph-entity-choice', 'a')
    with page.expect_response(_is_graph_exchange):
        page.select_option('#type-scope', 'Thing')
    with page.expect_response(_is_graph_exchange):
        page.select_option('#predicate-scope', 'mentions')
    page.wait_for_function("window.wb.nodes.size === 2")
    assert _graph_node_ids(page) == ['a', 'b']
    assert _graph_edge_ids(page) == ['r']
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json) if _is_graph_exchange(request) else None)
    with page.expect_response(_is_graph_exchange):
        page.click('#draw-graph')
    page.select_option('#graph-entity-choice', 'b')
    page.wait_for_function("document.querySelector('#graph-node').value === 'b'")
    with page.expect_response(_is_graph_exchange):
        page.click('#graph-expand')
    page.wait_for_function("window.wb.nodes.has('x')")
    assert _graph_node_ids(page) == ['a', 'b', 'x']
    assert _graph_edge_ids(page) == ['r']
    with page.expect_response(_is_graph_exchange):
        page.click('#draw-graph')
    assert _graph_node_ids(page) == ['a', 'b', 'x']
    assert page.locator('#type-scope').input_value() == 'Thing'
    assert page.locator('#predicate-scope').input_value() == 'mentions'
    assert [body['seeds'] for body in bodies] == [['a'], ['b'], ['b']]
    assert all('entity_type' not in body and 'predicate' not in body for body in bodies)
    assert all(body['max_nodes'] == 500 and body['max_edges'] == 1000 for body in bodies)


def test_ontology_scope_reaches_graph_redraw_expand_and_mindmap(workbench):
    page = workbench.page
    repo = workbench.service.repository
    release = repo.list_ontologies(workbench.project)[-1]
    rows = repo.current_records(workbench.project)
    repo.put_batch(workbench.project, [
        {**{key: value for key, value in row.items() if key not in {
            'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}},
         'ontology_id': release['id']} for row in rows])
    graph_bodies, mindmap_bodies = [], []
    page.on('request', lambda request: graph_bodies.append(request.post_data_json) if _is_graph_exchange(request) else None)
    page.on('request', lambda request: mindmap_bodies.append(request.post_data_json) if request.url.endswith('/mindmap') else None)
    page.evaluate("id => window.wb.setVersionContext({ontologyScope:'ids',ontologyIds:[id],ontologyVersion:1,source:'test'})", release['id'])
    # Version changes invalidate the old seed. Explicitly select a new seed in
    # this bound release before drawing or expanding.
    with page.expect_response(_is_graph_exchange):
        page.evaluate("async () => {await window.drawGraph('a',1);}")
    page.select_option('#graph-entity-choice', 'a')
    with page.expect_response(_is_graph_exchange):
        page.click('#graph-expand')
    with page.expect_response(_is_graph_exchange):
        page.click('#draw-graph')
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: response.url.endswith('/mindmap')):
        page.click('#graph-detail [data-action="mindmap"]')
    assert page.locator('#tab-mindmap').is_visible()
    page.wait_for_function("[...document.querySelector('#mindmap-root').options].some(row=>row.value==='a')")
    page.select_option('#mindmap-root', 'a')
    with page.expect_response(lambda response: response.url.endswith('/mindmap')):
        page.click('#draw-mindmap')
    assert len(graph_bodies) == 3
    assert all(body['ontology_scope'] == 'ids' and body['ontology_ids'] == [release['id']] for body in graph_bodies)
    assert mindmap_bodies[-1]['ontology_scope'] == 'ids'
    assert mindmap_bodies[-1]['ontology_ids'] == [release['id']]
    page.evaluate("() => window.wb.setVersionContext({ontologyScope:'unknown',source:'test'})")
    with page.expect_response(lambda response: response.url.endswith('/mindmap')):
        page.click('#draw-mindmap')
    assert mindmap_bodies[-1]['ontology_scope'] == 'unknown'
    assert mindmap_bodies[-1]['ontology_ids'] is None
    page.evaluate('() => window.wb.clearVersionContext()')
    with page.expect_response(lambda response: response.url.endswith('/mindmap')):
        page.click('#draw-mindmap')
    assert mindmap_bodies[-1]['ontology_scope'] == 'all'
    assert mindmap_bodies[-1]['ontology_ids'] is None


def test_ontology_scope_banner_reset_redraws_graph_with_all_scope(workbench):
    page = workbench.page
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json) if _is_graph_exchange(request) else None)
    page.evaluate("() => window.wb.setVersionContext({ontologyScope:'ids',ontologyIds:['ontology-v1'],ontologyVersion:1,source:'test'})")
    assert '知识范围：本体 v1' in page.locator('#graph-version-scope').inner_text()
    assert '知识范围：本体 v1' in page.locator('#mindmap-version-scope').inner_text()
    page.click('#graph-version-scope [data-reset-ontology-scope]')
    page.wait_for_function("document.querySelector('#graph-summary').textContent.includes('请选择实体')")
    assert bodies == []
    assert page.evaluate('window.wb.versionContext') == {'ontologyScope':'all','ontologyIds':None,'ontologyVersion':None,'source':None}
    assert '知识范围：全部本体' in page.locator('#graph-version-scope').inner_text()
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-node').value === 'a'")
    with page.expect_response(_is_graph_exchange):
        page.click('#graph-expand')
    assert bodies[-1]['ontology_scope'] == 'all'
    assert bodies[-1]['ontology_ids'] is None


# ── 图谱渲染器只能有一份实现（双 UI 收敛的防回归闸门） ──

GRAPH_OWNER = 'workspace.js'


def test_graph_renderer_is_defined_once_and_owned_by_workspace():
    """drawGraph 只能存在一份实现：两份同名函数靠"后加载覆盖"互相打架会静默失效。"""
    web = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
    sources = {path.name: path.read_text(encoding='utf-8') for path in sorted(web.glob('*.js'))}

    defining = [name for name, text in sources.items()
                if re.search(r'(?:window\.)?drawGraph\s*=[^=]', text)
                or re.search(r'function\s+drawGraph\b', text)]
    assert defining == [GRAPH_OWNER], f'drawGraph 必须只有一份实现，实际定义在: {defining}'

    owner = sources[GRAPH_OWNER]
    assert re.search(r'window\.drawGraph\s*=', owner), \
        '唯一实现必须显式挂到 window，不能依赖非严格模式的隐式全局赋值'
    assert "act('graph-expand'" in owner, '展开邻域按钮必须由 workspace.js 绑定到同一份渲染器'

    legacy = sources['workbench.js']
    assert 'function drawGraph' not in legacy, 'workbench.js 不得再保留旧 drawGraph 副本'
    assert '_palette' not in legacy, 'workbench.js 不得再保留旧调色板副本'
    assert "bind('draw-graph'" not in legacy and "bind('graph-expand'" not in legacy, \
        'workbench.js 不得再绑定图谱按钮，否则同一个按钮会被两个文件各绑一次'
    assert re.search(r'window\.drawGraph\s*\(', legacy), \
        'workbench.js 的图谱入口必须显式引用 window.drawGraph'


def test_draw_and_expand_buttons_share_the_single_graph_renderer(workbench):
    """两个入口（绘制图谱 / 展开邻域）必须落到同一个渲染器：请求体与渲染产物都要一致。"""
    page = workbench.page
    page.click('[data-tab="search"]')
    _search(page)

    # 检索命中先走统一详情选择；只有下方显式「展开邻域」才请求子图。
    _channel_sections(page).nth(0).locator('[data-graph-node]').first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    assert page.locator('#graph-type-buttons').count() == 1
    assert '局部邻域' in page.locator('#graph-summary').inner_text()

    # 「展开邻域」这个按钮原先绑在 workbench.js 的旧副本上；收敛后必须和上面同源：
    # 请求体带 valid_at（旧副本不发这个字段），并且类型筛选按钮/时间轴仍在（旧副本不会创建）。
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    workbench.paths.clear()
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.click('#graph-expand')
    page.wait_for_timeout(120)

    assert workbench.paths.count(f'/api/projects/{workbench.project}/graph-neighborhood') == 1
    assert bodies and set(bodies[-1]) == SUBGRAPH_BODY_FIELDS, \
        f'展开邻域必须走收敛后的实现（请求体含 valid_at）: {bodies[-1] if bodies else None}'
    assert bodies[-1]['seeds'][0] == 'a' and bodies[-1]['hops'] == 1
    assert page.locator('#graph-type-buttons').count() == 1
    assert page.locator('#graph-timeline').count() == 1
    assert '局部邻域' in page.locator('#graph-summary').inner_text()
    # 提示现在是实体卡片（图标 + 正文 + 关闭按钮）：断言正文那一段，
    # 不能拿整个容器跟一句话做全等比较。
    assert page.locator('#status .ks-notice__text').inner_text() == '已展开 1 跳邻域'

    # 没选实体时给出明确提示，而不是静默画一张空图
    page.evaluate("() => {document.getElementById('graph-node').value = '';}")
    workbench.paths.clear()
    page.click('#graph-expand')
    page.wait_for_timeout(120)
    assert not any(path.endswith('/graph-neighborhood') for path in workbench.paths)
    assert '请先选择实体' in page.locator('#status').inner_text()
    assert workbench.errors == []


def test_load_graph_button_uses_the_shipped_renderer(workbench):
    """「知识与历史」里的「查看关系」也要走同一份实现，并切回检索页。

    这里用 DOM click 而不是 page.click：该按钮所在的「知识与历史」页默认是 hidden，
    headless 视口下导航组也是折叠的（同文件里 #apply-scope 的既有失败是同一原因），
    而本项目本来就把这类隐藏触发器当内部按钮用 .click() 驱动。
    """
    page = workbench.page
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.evaluate("() => document.getElementById('load-graph').click()")
    page.wait_for_timeout(120)

    assert bodies and set(bodies[-1]) == SUBGRAPH_BODY_FIELDS
    assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'
    assert page.locator('#graph-type-buttons').count() == 1
    assert '局部邻域' in page.locator('#graph-summary').inner_text()
    assert workbench.errors == []


# ── Task 3: the knowledge chat is one independent request per turn ──

def test_sse_parser_handles_split_boundaries_crlf_multiline_and_malformed(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    payloads = {
        'lf': 'event: evidence\ndata: {"channels":{"entity":1}}\n\n'
              'event: delta\ndata: {"text":"退款"}\n\nevent: done\ndata: {"mode":"llm"}\n\n',
        'crlf': 'event: delta\r\ndata: {"text":"a"}\r\n\r\n'
                'event: done\r\ndata: {}\r\n\r\n',
        'multiline': 'event: evidence\ndata: {"channels":\ndata: {"entity":2}}\n\n',
        'malformed': 'event: delta\ndata: {oops}\n\n',
        'comment': ': keep-alive\n\nevent: delta\ndata: {"text":"b"}\n\n',
    }
    result = page.evaluate(
        """(payloads) => {
             const chat = window.KnowledgeChat;
             const run = (text) => {
               let buffer = '', events = [];
               for (const character of text) {
                 buffer += character;
                 const step = chat.splitSseBuffer(buffer);
                 buffer = step.rest;
                 events.push(...step.events);
               }
               const tail = chat.splitSseBuffer(buffer + '\\n\\n');
               return {events: [...events, ...tail.events], rest: tail.rest};
             };
             return {
               lf: run(payloads.lf),
               crlf: run(payloads.crlf),
               multiline: run(payloads.multiline),
               malformed: run(payloads.malformed),
               comment: run(payloads.comment),
               sentences: [chat.completeSentences('退款需要'),
                           chat.completeSentences('退款需要。原始')],
             };
           }""", payloads)

    assert [item['event'] for item in result['lf']['events']] == ['evidence', 'delta', 'done']
    assert result['lf']['events'][1]['data'] == {'text': '退款'}
    assert result['lf']['rest'] == ''
    assert [item['event'] for item in result['crlf']['events']] == ['delta', 'done']
    assert result['crlf']['events'][0]['data'] == {'text': 'a'}
    assert result['multiline']['events'][0]['data'] == {'channels': {'entity': 2}}
    assert result['malformed']['events'][0]['event'] == 'malformed'
    assert [item['event'] for item in result['comment']['events']] == ['delta']
    assert result['sentences'][0] == {'announce': '', 'rest': '退款需要'}
    assert result['sentences'][1] == {'announce': '退款需要。', 'rest': '原始'}


def test_knowledge_chat_streams_evidence_deltas_and_done(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    _select_keyword_qa(page)
    assert page.locator('#qa-hero').is_visible()
    assert page.locator('.qa-example').count() == 3

    page.fill('#qa-query', '退款')
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')) as streamed:
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")

    assert workbench.paths.count(f'/api/projects/{workbench.project}/qa/stream') == 1
    assert streamed.value.request.post_data_json['retrieval_mode'] == 'keyword'
    assert streamed.value.request.post_data_json['generate'] is False
    assert not any(path.endswith('/explore') or path.endswith('/search')
                   for path in workbench.paths)
    turns = page.locator('#qa-transcript .qa-turn')
    assert turns.count() == 2
    assert turns.nth(0).get_attribute('data-role') == 'user'
    assert '退款' in turns.nth(0).inner_text()
    answer = turns.nth(1).locator('.qa-answer').inner_text()
    assert '退款需要原始凭证' in answer
    assert '退款需要原始凭证' in (page.locator('#qa-live').text_content() or '')
    assert not page.locator('#qa-hero').is_visible()
    assert page.locator('#qa').is_enabled()
    assert page.evaluate("() => document.activeElement.id") == 'qa-query'


EVIDENCE_EVENT = (
    'event: evidence\ndata: {"channels":{"entity":1,"chunk":1,"graph_evidence":0},'
    '"evidence":[{"citation":"E1","id":"c","kind":"chunk","text":"退款需要原始凭证",'
    '"source_id":"d","type":"","version":1}]}\n\n')


def test_knowledge_chat_surfaces_every_stream_failure_mode(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    seen = []

    def handler(route):
        seen.append(route.request.post_data_json)
        index = len(seen)
        if index == 1:
            route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                          body='event: error\ndata: {"detail":"模型暂时不可用"}\n\n')
        elif index == 2:
            route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                          body='event: delta\ndata: {oops}\n\n')
        elif index == 3:
            route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                          body=EVIDENCE_EVENT)
        else:
            route.fulfill(status=500, headers={'Content-Type': 'application/json'},
                          body='{"detail":"服务内部错误"}')

    page.route('**/qa/stream', handler)
    try:
        for index, expected in enumerate(
                ('模型暂时不可用', '无法解析', '回答流提前结束', '服务内部错误')):
            page.fill('#qa-query', '退款')
            page.click('#qa')
            page.wait_for_function(
                "() => {const turns = document.querySelectorAll("
                "'#qa-transcript .qa-turn-assistant'); "
                "return turns.length && turns[turns.length - 1].dataset.state === 'error';}")
            assert expected in page.locator('#qa-transcript .qa-error').nth(index).inner_text()
            assert page.locator('#qa-transcript .qa-turn-user').count() == index + 1, \
                'every failed turn must keep its user question'
            assert page.evaluate("() => document.activeElement.className") == 'qa-retry'
            assert page.locator('#qa').is_enabled()
    finally:
        page.unroute('**/qa/stream', handler)
    assert workbench.errors == []


def test_knowledge_chat_retry_replays_the_exact_request(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    bodies = []

    def handler(route):
        bodies.append(route.request.post_data_json)
        if len(bodies) == 1:
            route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                          body='event: error\ndata: {"detail":"模型暂时不可用"}\n\n')
        else:
            route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                          body=EVIDENCE_EVENT + 'event: done\ndata: {"mode":"evidence_only"}\n\n')

    page.route('**/qa/stream', handler)
    try:
        page.fill('#qa-query', '退款')
        page.click('#qa')
        page.wait_for_function(
            "() => document.querySelector('#qa-transcript .qa-turn-assistant')"
            "?.dataset.state === 'error'")
        page.click('#qa-transcript .qa-retry')
        page.wait_for_function(
            "() => document.querySelector('#qa-transcript .qa-turn-assistant')"
            "?.dataset.state === 'done'")
    finally:
        page.unroute('**/qa/stream', handler)

    assert len(bodies) == 2
    assert bodies[0] == bodies[1], 'retry must replay the exact request'
    assert bodies[0]['query'] == '退款'
    assert 'generate' in bodies[0] and 'retrieval_mode' in bodies[0]
    assert 'hops' not in bodies[0], 'the chat must not borrow the graph hop control'
    assert page.locator('#qa-transcript .qa-turn-user').count() == 1
    assert page.locator('#qa-transcript .qa-turn-assistant').count() == 1
    assert workbench.errors == []


def test_knowledge_chat_evidence_disclosure_and_cross_menu_navigation(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    _select_keyword_qa(page)
    page.fill('#qa-query', '退款')
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")

    panel = _expand_qa_evidence(page)
    graph_buttons = panel.locator('[data-evidence-node]')
    assert graph_buttons.count() >= 1
    graph_nodes = _graph_node_ids(page)
    graph_summary = page.locator('#graph-summary').inner_text()
    subgraph_bodies = []
    page.on('request', lambda request: subgraph_bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    workbench.paths.clear()
    graph_buttons.first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    offenders = [path for path in workbench.paths
                 if path.endswith(('/graph-neighborhood', '/search', '/explore'))]
    # 断言里带上"到底是哪个请求、带了什么载荷"：只报 True/False 时排查得靠猜。
    key_fields = {k: v for k, v in (subgraph_bodies[-1] if subgraph_bodies else {}).items()
                  if k in ('seeds', 'hops', 'direction', 'max_nodes', 'max_edges', 'include_unknown', 'record_ids')}
    assert not offenders, (offenders, key_fields)
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert _graph_node_ids(page) == graph_nodes
    assert page.locator('#graph-summary').inner_text() == graph_summary
    assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'
    assert page.evaluate("() => document.activeElement.id") == 'graph-heading'

    # The fixed citation also exposes its historical source action.
    page.click('[data-tab="qa"]')
    assert panel.get_by_text('查看冻结原文', exact=True).count() >= 1


def test_knowledge_chat_keyboard_ime_and_scope_change(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    _select_keyword_qa(page)

    # Shift+Enter inserts a newline, Enter sends.
    page.click('#qa-query')
    page.keyboard.type('退款')
    page.keyboard.press('Shift+Enter')
    assert '\n' in page.locator('#qa-query').input_value()
    page.fill('#qa-query', '退款')

    # IME composition must never send.
    page.evaluate("""() => {
      const input = document.getElementById('qa-query');
      input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      input.dispatchEvent(new KeyboardEvent('keydown',
        {key: 'Enter', bubbles: true, cancelable: true, isComposing: true}));
    }""")
    assert not any(path.endswith('/qa/stream') for path in workbench.paths)
    page.evaluate("""() => document.getElementById('qa-query')
      .dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}))""")

    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.locator('#qa-query').press('Enter')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    assert page.locator('#qa-transcript .qa-turn').count() == 2
    assert workbench.paths.count(f'/api/projects/{workbench.project}/qa/stream') == 1

    # The project selector is the visible owner of QA scope. Changing it clears the
    # conversation without reaching into the hidden search-only scope controls.
    other = page.evaluate(
        "async () => {const created = await (await fetch('/api/projects', {method: 'POST', "
        "headers: {'Content-Type': 'application/json'}, "
        "body: JSON.stringify({name: '问答范围'})})).json(); "
        "await projects(); return created.id;}")
    assert page.locator('#project').is_visible()
    with page.expect_response(lambda response: response.url.endswith('/records/page')):
        page.select_option('#project', other)
    page.wait_for_function(
        "document.querySelectorAll('#qa-transcript .qa-turn').length === 0 && "
        "document.querySelector('#qa-summary').textContent.includes('范围已变化')")
    assert page.locator('#qa-transcript .qa-turn').count() == 0
    assert '范围已变化' in (page.locator('#qa-summary').text_content() or '')
    assert page.locator('#qa-hero').is_visible()
    assert (page.locator('#qa-live').text_content() or '') == ''
    assert workbench.errors == []


def test_mobile_composer_stays_reachable_with_expanded_evidence(workbench):
    page = workbench.page
    page.set_viewport_size({'width': 390, 'height': 844})
    page.click('[data-tab="qa"]')
    _select_keyword_qa(page)
    for index, question in enumerate(('退款', '退款商户')):
        page.fill('#qa-query', question)
        page.click('#qa')
        page.wait_for_function(
            "() => {const turns = document.querySelectorAll('#qa-transcript .qa-turn-assistant'); "
            "return turns.length && turns[turns.length - 1].dataset.state === 'done';}")
        _expand_qa_evidence(page, index)
    assert page.locator('#qa-transcript .qa-evidence-panel').first.is_visible()
    page.wait_for_timeout(250)

    box = page.evaluate("""() => {
      const composer = document.querySelector('#qa-composer').getBoundingClientRect();
      const scroll = document.querySelector('#qa-scroll');
      return {
        top: Math.round(composer.top), bottom: Math.round(composer.bottom),
        innerHeight: window.innerHeight,
        scrollsInternally: scroll.scrollHeight > scroll.clientHeight,
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      };
    }""")
    assert box['overflow'] == 0, 'narrow screens must not scroll horizontally'
    assert 0 <= box['top'] and box['bottom'] <= box['innerHeight'] + 1, \
        f"the composer must stay inside the viewport: {box}"
    assert box['scrollsInternally'], 'the transcript must scroll instead of pushing the composer away'
    assert page.locator('#qa').is_enabled()
    assert workbench.errors == []


def test_chunk_evidence_opens_the_exact_frozen_source(workbench):
    """A citation keeps its exact source version after the current document changes."""
    page = workbench.page
    repo = workbench.service.repository
    document = next(row for row in repo.current_records(workbench.project) if row['id'] == 'd')
    page.click('[data-tab="qa"]')
    _select_keyword_qa(page)
    page.fill('#qa-query', '退款')
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    panel = _expand_qa_evidence(page)
    chunk = panel.locator('.qa-evidence-item').filter(
        has=page.locator('.qa-evidence-meta').filter(has_text=re.compile(r'^chunk ·')))
    assert chunk.count() == 1
    if not chunk.is_visible():
        panel.locator('.qa-evidence-more').click()
    assert chunk.is_visible()
    source_button = chunk.get_by_text('查看冻结原文', exact=True)
    assert source_button.count() == 1
    updated = repo.put_record(workbench.project, {
        'id': 'd', 'kind': 'document', 'text': '更新后的原文不得替换已冻结内容',
        'valid_from': '2020-01-01', 'metadata': {
            'title': '后来的修订', 'effective_time_source': 'explicit',
            'applicability': {'platforms': {'mode': 'all'}},
        },
    }, expected_version=document['version'])
    assert updated['version'] == document['version'] + 1
    assert updated['version_id'] != document['version_id']
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/provenance')) as traced:
        source_button.click()
    page.wait_for_function("document.querySelector('#provenance-drawer').dataset.state === 'ready'")
    trace = traced.value.json()
    documents = [node['details'] for node in trace['nodes'] if node['type'] == 'document_version']
    assert len(documents) == 1
    assert documents[0]['document_id'] == 'd'
    assert documents[0]['version_id'] == document['version_id']
    assert documents[0]['version'] == 1
    page.locator('#provenance-chain').get_by_role('button', name='查看历史原文', exact=True).click()
    assert page.locator('#source-evidence-dialog').is_visible()
    assert page.locator('#source-evidence-title').inner_text() == '退款规则'
    assert '来源版本 1' in page.locator('#source-evidence-note').inner_text()
    assert page.locator('#source-evidence-text').inner_text() == document['text']
    assert page.locator('#source-evidence-anchor').inner_text() == '退款需要原始凭证'
    assert page.locator('[data-tab="qa"]').get_attribute('class') == 'active'
    assert not any(path.endswith('/sources') or '/records/' in path for path in workbench.paths)
    assert workbench.errors == []


def _provenance_stream(page):
    """Hold the real chat reader after evidence so running answers are interactive."""
    page.evaluate("""() => {
      const original = window.fetch;
      window.__qaRequests = [];
      window.fetch = (url, options) => {
        if (!String(url).endsWith('/qa/stream')) return original(url, options);
        window.__qaRequests.push(JSON.parse(options.body));
        return Promise.resolve(new Response(new ReadableStream({start(controller) {
          window.__qaEvent = (event, data) => controller.enqueue(new TextEncoder().encode(
            'event: ' + event + '\\ndata: ' + JSON.stringify(data) + '\\n\\n'));
          window.__qaEnd = () => controller.close();
        }}), {headers: {'Content-Type': 'text/event-stream'}}));
      };
    }""")


def _emit(page, event, payload):
    page.evaluate('([event, data]) => window.__qaEvent(event, data)', [event, payload])


def _provenance_evidence(answer='answer-running'):
    return {'answer_id': answer, 'retrieval_run_id': 'run-' + answer,
            'channels': {'chunk': 1}, 'evidence': [{
                'citation': 'E1', 'kind': 'chunk', 'version': 2,
                'text': 'preview' * 60, 'text_preview': 'preview' * 60,
                'provenance_ref': 'answer:' + answer + '#E1',
                'metadata': {'secret': 'discard-me'}, 'properties': {'private': 1},
                'embedding': [1, 2], 'document': 'full-document'}]}


def _mock_provenance(page, seen):
    def handler(route):
        seen.append(route.request.url)
        route.fulfill(json={'subject': {'answer_id': 'answer-running', 'citation': 'E1'},
                            'nodes': [{'type': 'answer', 'ref': 'a', 'status': 'running',
                                       'details': {'answer_id': 'answer-running'}}],
                            'edges': [], 'integrity': {'complete': True, 'warnings': []}})
    page.route('**/provenance', handler)


def test_knowledge_chat_provenance_running_safe_done_and_history(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    _provenance_stream(page)
    seen = []
    _mock_provenance(page, seen)
    page.fill('#qa-query', 'provenance')
    page.click('#qa')
    _emit(page, 'evidence', _provenance_evidence())
    _expand_qa_evidence(page)
    citation = page.locator('.qa-evidence-item > button.provenance-citation')
    assert citation.count() == 1
    assert page.get_by_text('查看冻结原文', exact=True).count() == 1
    assert page.locator('.qa-turn-assistant').get_attribute('data-state') == 'streaming'
    citation.click()
    page.wait_for_function("document.querySelector('#provenance-drawer').dataset.state === 'ready'")
    assert 'running' in page.locator('#provenance-chain').inner_text()
    assert len(seen) == 1 and '/answers/answer-running/evidence/E1/provenance' in seen[0]
    assert page.locator('[data-tab="qa"]').get_attribute('class') == 'active'
    page.keyboard.press('Escape')
    assert page.locator('#provenance-drawer').is_hidden()
    assert citation.evaluate('(el) => el === document.activeElement')

    literal = '<img src=x onerror="window.__xss=1"> [E1] [E99]'
    _emit(page, 'delta', {'text': literal})
    assert page.locator('.qa-answer').inner_text() == literal
    assert page.locator('.qa-answer button').count() == 0
    _emit(page, 'done', {'answer_id': 'answer-running', 'retrieval_run_id': 'run-answer-running'})
    page.evaluate('window.__qaEnd()')
    page.wait_for_function("document.querySelector('.qa-turn-assistant').dataset.state === 'done'")
    assert page.locator('.qa-answer button').count() == 1
    assert page.locator('.qa-answer').inner_text() == literal
    assert page.locator('.qa-answer img').count() == 0
    assert page.evaluate('window.__xss || null') is None
    key = 'kg_qa_v1_' + workbench.project
    history = page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', key)
    assert len(history) == 2
    assistant = history[1]
    assert assistant['answer_id'] == 'answer-running'
    assert assistant['retrieval_run_id'] == 'run-answer-running'
    assert set(assistant['evidence'][0]) == {'citation', 'text_preview', 'kind', 'version', 'provenance_ref'}
    assert len(assistant['evidence'][0]['text_preview']) == 240
    assert 'discard-me' not in json.dumps(history) and 'full-document' not in json.dumps(history)

    page.reload(wait_until='load')
    page.wait_for_function("document.querySelectorAll('#project option').length > 1")
    page.select_option('#project', workbench.project)
    page.click('[data-tab="qa"]')
    page.wait_for_function("document.querySelectorAll('.qa-answer button').length === 1")
    assert page.locator('.qa-answer').inner_text() == literal
    _expand_qa_evidence(page)
    assert page.locator('.qa-evidence-item > button.provenance-citation').count() == 1
    page.locator('.qa-answer button').click()
    page.wait_for_function("document.querySelector('#provenance-drawer').dataset.state === 'ready'")
    assert len(seen) == 2
    page.keyboard.press('Escape')
    page.click('#qa-clear')
    assert page.locator('.qa-turn').count() == 0
    assert page.locator('#provenance-drawer').is_hidden()
    assert workbench.errors == []


def test_knowledge_chat_missing_answer_identity_and_retry_replaces_history(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    key = 'kg_qa_v1_' + workbench.project
    page.evaluate('([key, rows]) => {localStorage.setItem(key, JSON.stringify(rows)); clearKnowledgeChat();}',
                  [key, [{'kind': 'user', 'text': 'old'}, {'kind': 'assistant', 'text': 'old [E1]'}]])
    assert page.locator('.qa-answer').inner_text() == 'old [E1]'
    assert page.locator('.qa-answer button').count() == 0
    assert page.locator('.qa-provenance-unresolved').inner_text() == '缺少答案标识，无法定位已冻结证据链'
    page.click('#qa-clear')
    _provenance_stream(page)
    page.fill('#qa-query', 'retry provenance')
    page.click('#qa')
    _emit(page, 'evidence', _provenance_evidence('failed-answer'))
    _emit(page, 'error', {'detail': 'retry-me'})
    page.wait_for_function("document.querySelector('.qa-turn-assistant').dataset.state === 'error'")
    page.locator('.qa-retry').click()
    _emit(page, 'evidence', _provenance_evidence('new-answer'))
    _emit(page, 'delta', {'text': 'new [E1]'})
    _emit(page, 'done', {})
    page.evaluate('window.__qaEnd()')
    page.wait_for_function("document.querySelector('.qa-turn-assistant').dataset.state === 'done'")
    requests = page.evaluate('window.__qaRequests')
    assert len(requests) == 2 and requests[0] == requests[1]
    history = page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', key)
    assert len(history) == 2
    assert history[1]['answer_id'] == 'new-answer'
    assert history[1]['retrieval_run_id'] == 'run-new-answer'
    assert 'error' not in history[1]
    assert page.locator('.qa-turn-user').count() == page.locator('.qa-turn-assistant').count() == 1


def test_knowledge_chat_provenance_clear_aborts_and_ignores_stale_response(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    _provenance_stream(page)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (url, options) => {
        if (!String(url).endsWith('/provenance')) return original(url, options);
        window.__provenanceSignal = options.signal;
        return new Promise(resolve => window.__resolveProvenance = resolve);
      };
    }""")
    page.fill('#qa-query', 'clear pending')
    page.click('#qa')
    _emit(page, 'evidence', _provenance_evidence())
    _expand_qa_evidence(page)
    page.locator('.qa-evidence-item > button.provenance-citation').click()
    page.evaluate('clearKnowledgeChat({clearPersist:true})')
    assert page.evaluate('window.__provenanceSignal.aborted')
    page.evaluate("""() => {
      window.__resolveProvenance(new Response(JSON.stringify({
        subject: {answer_id:'answer-running', citation:'E1'}, nodes:[], integrity:{complete:true}
      }), {headers:{'Content-Type':'application/json'}}));
      window.__qaEnd();
    }""")
    page.wait_for_timeout(100)
    assert page.locator('#provenance-drawer').is_hidden()
    assert page.locator('.qa-turn').count() == 0
    assert page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', 'kg_qa_v1_' + workbench.project) is None

# --------------------------------------------------------------------------- 父类可见性
def _family(label, parent=None, ancestor=None):
    """按后端 ontology_family 的载荷形状造一份"类家族"（父类可为空＝顶层类）。"""
    parents = [{'id': 'urn:knowledge:ontology:' + parent, 'label': parent}] if parent else []
    ancestors = [{'id': 'urn:knowledge:ontology:' + (ancestor or parent),
                  'label': ancestor or parent}] if (parent or ancestor) else []
    return {'class_label': label, 'class_parents': parents, 'class_ancestors': ancestors,
            'class_ontology_id': 'fixture-bound-release'}


def _inject_family(page, record_id, family):
    """Inject the same bound-release family into exact details and canvas rows."""
    record_pattern = re.compile(r'/records/page$')
    def inject_record(route):
        response = route.fetch()
        payload = response.json()
        if _is_record_exchange(route.request, record_id):
            for row in payload.get('records', []):
                if row.get('id') == record_id:
                    row.update(family)
        route.fulfill(response=response, json=payload)
    def inject_neighborhood(route):
        response = route.fetch()
        payload = response.json()
        for row in payload.get('records', []):
            if row.get('id') == record_id and row.get('kind') == 'entity':
                row.update(family)
        route.fulfill(response=response, json=payload)
    page.route(record_pattern, inject_record)
    page.route(re.compile(r'/graph-neighborhood$'), inject_neighborhood)
    return record_pattern


def _tooltip_text(page, node_id):
    """把某个图谱节点的悬浮提示逼出来并读回文本（hover 到像素不稳，用 ECharts 自己的 showTip）。"""
    return page.evaluate("""async (id) => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      const index = series.data.findIndex(item => item.id === id);
      if (index < 0) return null;
      chart.dispatchAction({type: 'showTip', seriesIndex: 0, dataIndex: index});
      await new Promise(resolve => setTimeout(resolve, 300));
      const texts = [...document.querySelectorAll('#graph-canvas div')]
        .map(node => node.innerText).filter(Boolean);
      return texts[texts.length - 1] || '';
    }""", node_id)


def _open_entity_with_family(page, record_id, family):
    """Read a fresh, explicitly seeded neighborhood before inspecting the row."""
    _inject_family(page, record_id, family)
    with page.expect_response(_is_graph_exchange):
        page.evaluate("async id => {await window.drawGraph(id,1);}", record_id)
    page.wait_for_function("id => window.wb.nodes.has(id)", arg=record_id)
    page.select_option('#graph-entity-choice', record_id)
    page.wait_for_function("label => document.querySelector('#graph-detail').textContent.includes(label)", arg=family['class_label'])


def test_entity_detail_and_graph_tooltip_name_the_parent_class(workbench):
    """检索详情与图谱悬浮提示都要写出"父类"。

    用户的反馈：检索里看不到父类信息、知识图谱也没有。层级长在类之间，实体节点上没有，
    所以这条契约同时盯住两条前端通路：右侧实体详情卡（evidence-family）与图谱 tooltip。
    """
    page = workbench.page
    _open_entity_with_family(page, 'a', _family('价格类型', parent='电商平台'))
    detail = page.locator('#graph-detail').inner_text()
    assert '当前类' in detail and '价格类型' in detail
    assert '父类' in detail and '电商平台' in detail
    # 完整继承链只在比直接父类更深时才出现，别把简单情况也塞一行。
    assert '完整继承链' not in detail

    tooltip = _tooltip_text(page, 'a')
    assert tooltip and '类型：价格类型' in tooltip
    assert '父类：电商平台' in tooltip


def test_root_class_says_it_has_no_parent_on_purpose(workbench):
    """顶层类不能显示成空白或"读不到"，要明确说"本体里没有父类"。

    否则用户看到空字段只会以为是坏了 —— 与阶段门禁同样的原则：不锁定/没有父类，
    都要把原因写在界面上。
    """
    page = workbench.page
    _open_entity_with_family(page, 'b', _family('顶层类'))
    detail = page.locator('#graph-detail').inner_text()
    assert '父类' in detail and '本体里没有父类' in detail

    tooltip = _tooltip_text(page, 'b')
    assert tooltip and '父类：顶层类（本体里没有父类）' in tooltip


def test_grandparent_chain_is_shown_in_order(workbench):
    """多级继承要按"父类 → 祖父类"的顺序读得通，而不是一串无序的类名。"""
    page = workbench.page
    family = _family('价格类型', parent='电商平台', ancestor='顶层类')
    family['class_ancestors'] = [
        {'id': 'urn:knowledge:ontology:电商平台', 'label': '电商平台'},
        {'id': 'urn:knowledge:ontology:顶层类', 'label': '顶层类'},
    ]
    _open_entity_with_family(page, 's', family)
    detail = page.locator('#graph-detail').inner_text()
    assert '完整继承链' in detail
    assert '价格类型 → 电商平台 → 顶层类' in detail
