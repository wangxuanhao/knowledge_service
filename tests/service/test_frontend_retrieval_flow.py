"""Real-browser contract for the linked search/graph workspace and the one-shot knowledge chat."""
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
    'node_id', 'hops', 'filters', 'valid_at', 'known_at', 'include_unknown',
    'attribute_mode', 'entity_type', 'predicate',
}


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
    repository.save_ontology(project_id, turtle, Ontology(turtle).summary())
    return project_id


def _seed(repository, project_id):
    repository.put_batch(project_id, [
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
        {'id': 'd', 'kind': 'document', 'text': '退款规则原文', 'metadata': {'title': '退款规则'}},
    ])


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
    page.on('request', lambda request: paths.append(urlparse(request.url).path)
            if '/api/' in request.url else None)
    page.goto(f'http://127.0.0.1:{port}/', wait_until='load')
    page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
        page.select_option('#project', project_id)
    page.wait_for_function(
        "document.querySelector('#graph-summary').textContent !== "
        f"{json.dumps(INITIAL_GRAPH_HINT)}")
    paths.clear()
    try:
        yield SimpleNamespace(page=page, project=project_id, paths=paths, errors=errors)
    finally:
        context.close()
        server.should_exit = True
        thread.join(timeout=10)


def _search(page, query='退款', mode='keyword'):
    page.select_option('#search-mode', mode)
    page.fill('#query', query)
    with page.expect_response(lambda response: response.url.endswith('/search')):
        page.click('#search')
    page.wait_for_function("document.querySelectorAll('#hits .hit-kind').length === 3")


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
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
        page.select_option('#project', other)
    assert page.evaluate('window.wb.versionContext') == empty

    page.evaluate("window.wb.setVersionContext({ontologyScope: 'unknown', source: 'test'})")
    page.reload(wait_until='load')
    page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
    assert page.evaluate('window.wb.versionContext') == empty


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
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return (series?.data || []).map(item => String(item.id)).sort();
    }""")


def _graph_edge_ids(page):
    return page.evaluate("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return (series?.links || series?.edges || []).map(item => String(item.id)).sort();
    }""")


def _assert_complete_entity_inspector(page, text):
    detail = page.locator('#graph-detail')
    detail_text = detail.inner_text()
    assert text in detail_text
    assert '版本历史 · v1' in detail_text
    assert '新增关系' in detail_text
    assert '展开脑图' in detail_text
    assert 'undefined' not in detail_text


def _render_a_neighborhood(page):
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series && !series.data.some(item => item.id === 'x');
    }""")


def _hold_record_request(page, record_id='x'):
    pattern = re.compile(rf'/records/{re.escape(record_id)}(?:\?.*)?$')
    held = []

    def hold(route):
        held.append(route)

    page.route(pattern, hold)
    return pattern, hold, held


def _is_record_exchange(exchange, record_id='x'):
    request = exchange.request if hasattr(exchange, 'request') else exchange
    return (request.method == 'GET'
            and urlparse(request.url).path.endswith(f'/records/{record_id}'))


def _release_record_request(page, held, record_id='x'):
    assert len(held) == 1
    with page.expect_response(lambda response: _is_record_exchange(response, record_id)) as info:
        held.pop().continue_()
    response = info.value
    response.body()
    page.evaluate("""() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
    return response


def _stable_graph_summary(page):
    summary = page.locator('#graph-summary').inner_text()
    assert ' · 服务' in summary
    return summary.split(' · 服务', 1)[0]


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
        if current and current == previous and ' · 服务' in current:
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
    sections = page.locator('#hits > section')
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
    assert not any(path.endswith('/subgraph') for path in workbench.paths)


def test_graph_node_click_updates_only_details(workbench):
    page = workbench.page
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()

    _trigger_graph_click(page, 'node', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    page.wait_for_timeout(120)

    assert not any(path.endswith('/subgraph') for path in workbench.paths)
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

    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == summary_before


def test_entity_selection_requires_explicit_expand_and_full_graph_ignores_selection(workbench):
    page = workbench.page
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series?.data.some(item => item.id === 'x');
    }""")
    full_node_ids = _graph_node_ids(page)
    assert 'x' in full_node_ids
    full_stable_summary = _stable_graph_summary(page)
    full_summary = page.locator('#graph-summary').inner_text()
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)
    workbench.paths.clear()

    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    page.wait_for_timeout(120)
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == full_summary
    _assert_complete_entity_inspector(page, '退款商户')

    detail_before = page.locator('#graph-detail').inner_html()
    workbench.paths.clear()
    # 选回空白项＝清空选择、详情归零、并重绘当前范围全图。
    # 这条契约以前断言的是"值被弹回上一个实体、不发任何请求"——那正是用户反馈的
    # "选择实体下拉框，点其他之后切不回空白了"，所以按用户要求反过来了。
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.select_option('#graph-entity-choice', '')
    page.wait_for_timeout(120)
    assert page.locator('#graph-entity-choice').input_value() == ''
    assert page.locator('#graph-node').input_value() == ''
    assert page.locator('#graph-detail').inner_html() != detail_before
    assert any(path.endswith('/subgraph') for path in workbench.paths)
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series?.data.some(item => item.id === 'x');
    }""")
    assert 'x' in _graph_node_ids(page)
    # 用去掉耗时的那一版比：这里真的重绘了全图，服务端耗时当然会变
    # （原断言能相等，恰恰是因为以前根本没重绘）。
    assert _stable_graph_summary(page) == full_stable_summary

    # 回到"选中实体 → 展开邻域"这步：重新选中 a 再展开。
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series && !series.data.some(item => item.id === 'x');
    }""")
    assert bodies[-1]['node_id'] == 'a'
    assert bodies[-1]['attribute_mode'] == 'expanded'
    expanded_node_ids = _graph_node_ids(page)
    assert 'x' not in expanded_node_ids
    assert len(expanded_node_ids) < len(full_node_ids)

    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    page.wait_for_function("""() => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return series?.data.some(item => item.id === 'x');
    }""")
    assert bodies[-1]['node_id'] is None
    assert bodies[-1]['attribute_mode'] == 'summary'
    assert _graph_node_ids(page) == full_node_ids
    assert _stable_graph_summary(page) == full_stable_summary
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
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

    assert f'/api/projects/{workbench.project}/records/x' in workbench.paths
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert _graph_node_ids(page) == neighborhood_node_ids
    assert page.locator('#graph-summary').inner_text() == summary_before
    assert page.locator('#graph-node').input_value() == 'x'
    assert page.locator('#graph-entity-choice').input_value() == 'x'
    _assert_complete_entity_inspector(page, '无关实体')


def test_expand_stays_disabled_until_pending_entity_detail_resolves(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    subgraph_pattern = re.compile(r'/subgraph$')
    held_subgraphs = []

    def hold_subgraph(route):
        held_subgraphs.append(route)

    page.route(subgraph_pattern, hold_subgraph)
    record_pattern, record_handler, held_records = _hold_record_request(page)
    try:
        with page.expect_request(lambda request: request.url.endswith('/subgraph')):
            page.click('#graph-expand')
        assert page.locator('#graph-expand').is_disabled()

        with page.expect_request(_is_record_exchange):
            page.select_option('#graph-entity-choice', 'x')
        assert page.locator('#graph-expand').is_disabled()

        assert len(held_subgraphs) == 1
        with page.expect_response(
                lambda response: response.url.endswith('/subgraph')) as info:
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

        page.fill('#graph-entity-filter', '退款')
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

        page.fill('#graph-entity-filter', '完全不匹配')
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


def test_invalid_detail_scope_rolls_back_pending_detail(workbench):
    page = workbench.page
    _render_a_neighborhood(page)
    page.fill('#filters', '{')

    page.select_option('#graph-entity-choice', 'x')
    page.evaluate("""() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)))""")

    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-expand').is_enabled()
    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert not any(path.endswith('/records/x') for path in workbench.paths)
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
            if request.url.endswith('/subgraph') else None)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    assert bodies[-1]['node_id'] == 'a'


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

    pattern = re.compile(r'/records/x(?:\?.*)?$')
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
            if request.url.endswith('/subgraph') else None)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    assert bodies[-1]['node_id'] == 'b'


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
        page.locator('#hits > section').nth(index).locator('[data-graph-node]').first.click()
        page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
        assert not any(path.endswith('/subgraph') for path in workbench.paths)
        assert not any(path.endswith('/explore') or path.endswith('/search')
                       for path in workbench.paths)
        assert page.locator('#graph-entity-choice').input_value() == expected_seed
        assert page.locator('#graph-node').input_value() == expected_seed
        assert '退款商户' in page.locator('#graph-detail').inner_text()
        assert _graph_node_ids(page) == graph_nodes
        assert page.locator('#graph-summary').inner_text() == graph_summary
        assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'


def test_project_and_scope_changes_clear_isolated_state(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    _search(page)
    page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    hits_after_graph = page.locator('#hits').inner_html()

    # Scope change clears graph, selection and details but keeps the result rail.
    workbench.paths.clear()
    _watch_graph_clear(page, '#apply-scope', '__scopeClearSnapshot')
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
        page.click('#apply-scope')
    scope_cleared = _graph_clear_snapshot(page, '__scopeClearSnapshot')
    assert scope_cleared == {
        'hits': hits_after_graph, 'summary': INITIAL_GRAPH_HINT, 'detail': '', 'node': ''}
    page.wait_for_function(
        "document.querySelector('#graph-summary').textContent === "
        f"{json.dumps(INITIAL_GRAPH_HINT)}")
    assert page.locator('#graph-summary').inner_text() == INITIAL_GRAPH_HINT
    assert page.locator('#graph-detail').inner_html() == ''
    assert page.locator('#graph-node').input_value() == ''
    assert page.locator('#hits').inner_html() == hits_after_graph
    assert not any(path.endswith('/subgraph') for path in workbench.paths)

    # The knowledge-time switch clears the graph without re-loading it.
    page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    page.wait_for_function(
        "document.querySelectorAll('#graph-timeline .tl-points option').length > 1")
    hits_before_time = page.locator('#hits').inner_html()
    workbench.paths.clear()
    _watch_graph_clear(page, '#graph-timeline .tl-prev', '__timeClearSnapshot')
    page.click('#graph-timeline .tl-prev')
    time_cleared = _graph_clear_snapshot(page, '__timeClearSnapshot')
    assert time_cleared == {
        'hits': hits_before_time, 'summary': INITIAL_GRAPH_HINT, 'detail': '', 'node': ''}
    page.wait_for_function(
        "document.querySelector('#graph-summary').textContent === "
        f"{json.dumps(INITIAL_GRAPH_HINT)}")
    assert page.locator('#graph-summary').inner_text() == INITIAL_GRAPH_HINT
    assert page.locator('#graph-detail').inner_html() == ''
    assert page.locator('#hits').inner_html() == hits_before_time
    assert not any(path.endswith('/subgraph') for path in workbench.paths)

    # Switching project clears results, graph, selection and details without loading a graph.
    other = page.evaluate(
        "async () => {const created = await (await fetch('/api/projects', {method: 'POST', "
        "headers: {'Content-Type': 'application/json'}, "
        "body: JSON.stringify({name: '第二个项目'})})).json(); "
        "await projects(); return created.id;}")
    _watch_graph_clear(page, '#project', '__projectClearSnapshot', event='change')
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
        page.select_option('#project', other)
    cleared = _graph_clear_snapshot(page, '__projectClearSnapshot')
    assert cleared == {'hits': '', 'summary': INITIAL_GRAPH_HINT, 'detail': '', 'node': ''}


def test_graph_scope_filters_survive_expand_and_explicit_full_redraw(workbench):
    page = workbench.page
    assert _graph_node_ids(page) == ['a', 'b', 's', 'x']
    assert _graph_edge_ids(page) == ['r', 'r-alt', 'r-cross']

    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)
    page.select_option('#type-scope', 'Thing')
    page.select_option('#predicate-scope', 'mentions')
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
        page.click('#apply-scope')
    assert page.locator('#type-scope').input_value() == 'Thing'
    assert page.locator('#predicate-scope').input_value() == 'mentions'

    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    assert _graph_node_ids(page) == ['a', 'b', 'x']
    assert _graph_edge_ids(page) == ['r']

    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    assert _graph_node_ids(page) == ['a', 'b']
    assert _graph_edge_ids(page) == ['r']

    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    assert _graph_node_ids(page) == ['a', 'b', 'x']
    assert _graph_edge_ids(page) == ['r']
    assert page.locator('#type-scope').input_value() == 'Thing'
    assert page.locator('#predicate-scope').input_value() == 'mentions'
    assert len(bodies) == 3
    assert [body['node_id'] for body in bodies] == [None, 'a', None]
    assert all(body['entity_type'] == 'Thing' and body['predicate'] == 'mentions'
               for body in bodies)


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
    page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    assert page.locator('#graph-type-buttons').count() == 1
    assert '服务' in page.locator('#graph-summary').inner_text()

    # 「展开邻域」这个按钮原先绑在 workbench.js 的旧副本上；收敛后必须和上面同源：
    # 请求体带 valid_at（旧副本不发这个字段），并且类型筛选按钮/时间轴仍在（旧副本不会创建）。
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    page.wait_for_timeout(120)

    assert workbench.paths.count(f'/api/projects/{workbench.project}/subgraph') == 1
    assert bodies and set(bodies[-1]) == SUBGRAPH_BODY_FIELDS, \
        f'展开邻域必须走收敛后的实现（请求体含 valid_at）: {bodies[-1] if bodies else None}'
    assert bodies[-1]['node_id'] == 'a' and bodies[-1]['hops'] == 1
    assert page.locator('#graph-type-buttons').count() == 1
    assert page.locator('#graph-timeline').count() == 1
    assert '服务' in page.locator('#graph-summary').inner_text()
    # 提示现在是实体卡片（图标 + 正文 + 关闭按钮）：断言正文那一段，
    # 不能拿整个容器跟一句话做全等比较。
    assert page.locator('#status .ks-notice__text').inner_text() == '已展开 1 跳邻域'

    # 没选实体时给出明确提示，而不是静默画一张空图
    page.evaluate("() => {document.getElementById('graph-node').value = '';}")
    workbench.paths.clear()
    page.click('#graph-expand')
    page.wait_for_timeout(120)
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
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
            if request.url.endswith('/subgraph') else None)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.evaluate("() => document.getElementById('load-graph').click()")
    page.wait_for_timeout(120)

    assert bodies and set(bodies[-1]) == SUBGRAPH_BODY_FIELDS
    assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'
    assert page.locator('#graph-type-buttons').count() == 1
    assert '服务' in page.locator('#graph-summary').inner_text()
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
    assert page.locator('#qa-hero').is_visible()
    assert page.locator('.qa-example').count() == 3

    page.fill('#qa-query', '退款')
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")

    assert workbench.paths.count(f'/api/projects/{workbench.project}/qa/stream') == 1
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
    page.fill('#qa-query', '退款')
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")

    toggle = page.locator('#qa-transcript .qa-evidence-toggle').first
    assert toggle.get_attribute('aria-expanded') == 'false'
    panel_id = toggle.get_attribute('aria-controls')
    assert page.locator(f'#{panel_id}').is_hidden()
    assert toggle.inner_text().startswith('查看 ')
    toggle.press('Enter')
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert page.locator(f'#{panel_id}').is_visible()

    graph_buttons = page.locator(f'#{panel_id} [data-evidence-node]')
    assert graph_buttons.count() >= 1
    graph_nodes = _graph_node_ids(page)
    graph_summary = page.locator('#graph-summary').inner_text()
    subgraph_bodies = []
    page.on('request', lambda request: subgraph_bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)
    workbench.paths.clear()
    graph_buttons.first.click()
    page.wait_for_function(
        "document.querySelector('#graph-detail').textContent.includes('退款商户')")
    offenders = [path for path in workbench.paths
                 if path.endswith(('/subgraph', '/search', '/explore'))]
    # 断言里带上"到底是哪个请求、带了什么载荷"：只报 True/False 时排查得靠猜。
    key_fields = {k: v for k, v in (subgraph_bodies[-1] if subgraph_bodies else {}).items()
                  if k in ('node_id', 'hops', 'attribute_mode', 'include_unknown', 'record_id')}
    assert not offenders, (offenders, key_fields)
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert page.locator('#graph-node').input_value() == 'a'
    assert '退款商户' in page.locator('#graph-detail').inner_text()
    assert _graph_node_ids(page) == graph_nodes
    assert page.locator('#graph-summary').inner_text() == graph_summary
    assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'
    assert page.evaluate("() => document.activeElement.id") == 'graph-heading'

    # A chunk citation without a source must not offer an unusable action.
    page.click('[data-tab="qa"]')
    assert page.locator(f'#{panel_id} [data-evidence-source]').count() >= 1


def test_knowledge_chat_keyboard_ime_and_scope_change(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')

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

    page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    assert page.locator('#qa-transcript .qa-turn').count() == 2

    # The project selector is the visible owner of QA scope. Changing it clears the
    # conversation without reaching into the hidden search-only scope controls.
    other = page.evaluate(
        "async () => {const created = await (await fetch('/api/projects', {method: 'POST', "
        "headers: {'Content-Type': 'application/json'}, "
        "body: JSON.stringify({name: '问答范围'})})).json(); "
        "await projects(); return created.id;}")
    assert page.locator('#project').is_visible()
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
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
    for question in ('退款', '退款商户'):
        page.fill('#qa-query', question)
        page.click('#qa')
        page.wait_for_function(
            "() => {const turns = document.querySelectorAll('#qa-transcript .qa-turn-assistant'); "
            "return turns.length && turns[turns.length - 1].dataset.state === 'done';}")
    page.locator('#qa-transcript .qa-evidence-toggle').first.press('Enter')
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


def test_chunk_evidence_declares_its_home_when_source_is_merged(workbench):
    """P0：原文数据源并入「知识写入」，问答证据的来源按钮不再跳来源页，而是明确提示去向。

    旧的 test_chunk_evidence_navigates_to_the_exact_source 断言"点来源 → 跳到 sources 页"，
    那测的是被合并掉的**独立原文数据源页**。合并后 sources 无侧栏入口，落点改到「知识写入」
    （P1 接入），P0 期间点来源必须给出明确提示，不能静默失效。
    """
    page = workbench.page
    page.click('[data-tab="qa"]')
    page.fill('#qa-query', '退款')
    with page.expect_response(lambda response: response.url.endswith('/qa/stream')):
        page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    page.locator('#qa-transcript .qa-evidence-toggle').first.press('Enter')

    source_button = page.locator('#qa-transcript [data-evidence-source]').first
    assert source_button.count() == 1
    source_button.click()
    # P0：不再跳 sources（侧栏无此入口），改为明确提示去向，且全程不报错。
    page.wait_for_function(
        "() => document.getElementById('status').textContent.includes('原文正文查看将并入')")
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
    toggle = page.locator('.qa-evidence-toggle')
    toggle.click()
    citation = page.locator('.qa-evidence-panel button.provenance-citation')
    assert citation.count() == 1
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
    page.locator('.qa-evidence-toggle').click()
    assert page.locator('.qa-evidence-panel button.provenance-citation').count() == 1
    page.locator('.qa-answer button').click()
    page.wait_for_function("document.querySelector('#provenance-drawer').dataset.state === 'ready'")
    assert len(seen) == 2
    page.keyboard.press('Escape')
    page.click('#qa-clear')
    assert page.locator('.qa-turn').count() == 0
    assert page.locator('#provenance-drawer').is_hidden()
    assert workbench.errors == []


def test_knowledge_chat_provenance_legacy_and_retry_replaces_history(workbench):
    page = workbench.page
    page.click('[data-tab="qa"]')
    key = 'kg_qa_v1_' + workbench.project
    page.evaluate('([key, rows]) => {localStorage.setItem(key, JSON.stringify(rows)); clearKnowledgeChat();}',
                  [key, [{'kind': 'user', 'text': 'old'}, {'kind': 'assistant', 'text': 'old [E1]'}]])
    assert page.locator('.qa-answer').inner_text() == 'old [E1]'
    assert page.locator('.qa-answer button').count() == 0
    assert page.locator('.provenance-legacy-note').inner_text() == '该历史回答生成于溯源记录启用前'
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
    page.locator('.qa-evidence-toggle').click()
    page.locator('.qa-evidence-panel button.provenance-citation').click()
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
    return {'class_label': label, 'class_parents': parents, 'class_ancestors': ancestors}


def _inject_family(page, record_id, family):
    """把 /records/<id> 与 /subgraph 里这个实体的类家族换成测试数据。"""
    record_pattern = re.compile(rf'/records/{re.escape(record_id)}(?:\?.*)?$')

    def inject_record(route):
        response = route.fetch()
        route.fulfill(response=response, json={**response.json(), **family})

    def inject_subgraph(route):
        response = route.fetch()
        payload = response.json()
        for node in payload.get('nodes', []):
            if node.get('id') == record_id and node.get('kind') == 'entity':
                node.update(family)
        route.fulfill(response=response, json=payload)

    page.route(record_pattern, inject_record)
    page.route(re.compile(r'/subgraph(?:\?.*)?$'), inject_subgraph)
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
    """注入类家族并**强制重新取数**（进页面时那份详情/图谱是缓存，不重取就看不到注入）。

    细节：先注册路由，再重画图谱（让 /subgraph 带上注入数据），最后选实体（让 /records/<id>
    重新走一遍）。断言等的是注入的类名，注入没生效就会超时失败 —— 不会静默读旧数据通过。
    """
    _inject_family(page, record_id, family)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    page.wait_for_function("""(id) => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      const series = chart.getOption().series.find(item => item.type === 'graph');
      return Boolean(series && series.data.some(item => item.id === id));
    }""", arg=record_id)
    page.select_option('#graph-entity-choice', record_id)
    page.wait_for_function(
        "(label) => document.querySelector('#graph-detail').textContent.includes(label)",
        arg=family['class_label'])


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
