"""Real-browser contract for the linked search/graph workspace and the one-shot knowledge chat."""
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
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.ontology import Ontology

INITIAL_GRAPH_HINT = '从左侧检索结果选择实体或关系'
CHANNEL_LABELS = ['匹配实体', '原文片段', '关系链路']
SEARCH_BODY_FIELDS = {
    'query', 'retrieval_mode', 'filters', 'valid_at', 'known_at', 'include_unknown',
    'k_entities', 'k_chunks', 'k_relations',
}
SUBGRAPH_BODY_FIELDS = {'node_id', 'hops', 'filters', 'valid_at', 'known_at', 'include_unknown'}


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
        {'id': 'r', 'kind': 'relation', 'type': 'mentions', 'text': '退款关系',
         'subject_id': 'a', 'object_id': 'b', 'metadata': {}},
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
    page.wait_for_timeout(80)
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


# ── Task 1: the combined workspace keeps search, graph and details decoupled ──

def test_search_updates_only_the_result_rail_and_ignores_graph_keys(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
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
    graph_before = page.locator('#graph-canvas').evaluate('(el) => el.outerHTML')
    detail_before = page.locator('#graph-detail').evaluate('(el) => el.outerHTML')
    hint_before = page.locator('#graph-summary').inner_text()
    assert hint_before == INITIAL_GRAPH_HINT

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
    page.wait_for_timeout(120)

    assert '不应出现的节点' not in page.locator('#hits').inner_text()
    assert page.locator('#graph-canvas').evaluate('(el) => el.outerHTML') == graph_before
    assert page.locator('#graph-detail').evaluate('(el) => el.outerHTML') == detail_before
    assert page.locator('#graph-summary').inner_text() == INITIAL_GRAPH_HINT
    assert not any(path.endswith('/subgraph') for path in workbench.paths)


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


def test_entity_and_relation_hits_each_load_one_scoped_subgraph(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    _search(page)
    subgraph_path = f'/api/projects/{workbench.project}/subgraph'

    for index, expected_seed in ((0, 'a'), (2, 'a')):
        workbench.paths.clear()
        bodies = []
        page.once('request', lambda request: bodies.append(request.post_data_json)
                  if request.url.endswith('/subgraph') else None)
        with page.expect_response(lambda response: response.url.endswith('/subgraph')):
            page.locator('#hits > section').nth(index).locator('[data-graph-node]').first.click()
        assert workbench.paths.count(subgraph_path) == 1
        assert not any(path.endswith('/explore') or path.endswith('/search')
                       for path in workbench.paths)
        assert bodies and set(bodies[0]) == SUBGRAPH_BODY_FIELDS
        assert bodies[0]['node_id'] == expected_seed
        assert page.locator('[data-tab="search"]').get_attribute('class') == 'active'
        assert page.locator('#graph-summary').inner_text() != INITIAL_GRAPH_HINT


def test_project_and_scope_changes_clear_isolated_state(workbench):
    page = workbench.page
    page.click('[data-tab="search"]')
    _search(page)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
    hits_after_graph = page.locator('#hits').inner_html()

    # Scope change clears graph, selection and details but keeps the result rail.
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
        page.click('#apply-scope')
    page.wait_for_timeout(120)
    assert page.locator('#graph-summary').inner_text() == INITIAL_GRAPH_HINT
    assert page.locator('#graph-detail').inner_html() == ''
    assert page.locator('#graph-node').input_value() == ''
    assert page.locator('#hits').inner_html() == hits_after_graph
    assert not any(path.endswith('/subgraph') for path in workbench.paths)

    # The knowledge-time switch clears the graph without re-loading it.
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
    page.wait_for_function(
        "document.querySelectorAll('#graph-timeline .tl-points option').length > 1")
    hits_before_time = page.locator('#hits').inner_html()
    workbench.paths.clear()
    page.click('#graph-timeline .tl-prev')
    page.wait_for_timeout(120)
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
    workbench.paths.clear()
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
        page.select_option('#project', other)
    page.wait_for_timeout(120)
    assert page.locator('#hits').inner_html() == ''
    assert page.locator('#graph-summary').inner_text() == INITIAL_GRAPH_HINT
    assert page.locator('#graph-detail').inner_html() == ''
    assert page.locator('#graph-node').input_value() == ''
    assert not any(path.endswith('/subgraph') for path in workbench.paths)


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

    # 检索命中进图谱：workspace.js 的实体入口
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.locator('#hits > section').nth(0).locator('[data-graph-node]').first.click()
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
    assert page.locator('#status').inner_text() == '已展开 1 跳邻域'

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
    workbench.paths.clear()
    bodies = []
    page.once('request', lambda request: bodies.append(request.post_data_json)
              if request.url.endswith('/subgraph') else None)
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        graph_buttons.first.click()
    assert workbench.paths.count(f'/api/projects/{workbench.project}/subgraph') == 1
    assert not any(path.endswith('/search') or path.endswith('/explore')
                   for path in workbench.paths)
    assert bodies and set(bodies[0]) == SUBGRAPH_BODY_FIELDS
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
    page.wait_for_timeout(150)
    assert not any(path.endswith('/qa/stream') for path in workbench.paths)
    page.evaluate("""() => document.getElementById('qa-query')
      .dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}))""")

    page.click('#qa')
    page.wait_for_function(
        "document.querySelector('#qa-transcript .qa-turn-assistant')?.dataset.state === 'done'")
    assert page.locator('#qa-transcript .qa-turn').count() == 2

    # Changing scope clears the conversation and explains why.
    page.click('#apply-scope')
    page.wait_for_timeout(150)
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


def test_chunk_evidence_navigates_to_the_exact_source(workbench):
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
    with page.expect_response(lambda response: response.url.endswith('/sources')):
        source_button.click()
    page.wait_for_function("() => document.getElementById('source-title').textContent.length > 0")
    assert page.locator('[data-tab="sources"]').get_attribute('class') == 'active'
    assert page.locator('#source-title').inner_text() == '退款规则'
    assert page.locator('#source-list [aria-current="true"]').count() == 1
    assert workbench.errors == []
