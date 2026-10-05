"""「项目与运行」页的真实浏览器契约（D2：项目管理 / 项目总览 / 后台任务 / 快照·评测 四页合并）。

为什么要单独有这一份：

  · 合并的收益全在"用户能不能一眼找到、能不能少点一次"——只有真页面能量：
    一个入口（不是四个平铺菜单）、一屏一问（同一时刻只有一个分区可见）、
    进哪一屏就把那一屏的数据给出来（不用先点一次「刷新」）；
  · 引导式界面规则是硬线，而它们是**像素读数**，文案契约测不出来：
    字号 ≥11px、首屏内容起点 ≤140px、一屏最多一个实心按钮。

这里打的是真实服务（真实 PostgreSQL 槽位 + 真实 index.html），只用真实项目做只读操作。
"""
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'knowledge_service' / 'web'


def _browser_path():
    candidates = [
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
    ]
    return next((str(path) for path in candidates if path.exists()), None)


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
def runtime(browser, tmp_path):
    app = create_app(tmp_path / 'runtime.sqlite', HashingEncoder())
    repository = app.state.service.repository
    project_id = repository.create_project('运行层合并', {'ontology_mode': 'ontology'})['id']
    turtle = (ROOT / 'knowledge_service' / 'resources' / 'default_ontology.ttl').read_text(encoding='utf-8')
    repository.save_ontology(project_id, turtle, Ontology(turtle).summary())
    repository.put_batch(project_id, [
        {'id': 'e1', 'kind': 'entity', 'type': 'Thing', 'text': '商户甲', 'metadata': {}},
        {'id': 'e2', 'kind': 'entity', 'type': 'Thing', 'text': '商户乙', 'metadata': {}},
    ])
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

    context = browser.new_context(viewport={'width': 1680, 'height': 950})
    instance = context.new_page()
    errors, paths = [], []
    instance.on('pageerror', lambda error: errors.append(str(error)))
    # 只记 /api/ 之后的路径（去掉 api 前缀），断言里就能直接写 projects/<id>/dashboard
    instance.on('request', lambda request: paths.append(request.url.split('/api/', 1)[1])
                if '/api/' in request.url else None)
    instance.goto(f'http://127.0.0.1:{port}/', wait_until='load')
    instance.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
    instance.select_option('#project', project_id)
    # 项目切换链跑完的标志：类型下拉被 options() 填上了。
    instance.wait_for_function("() => document.querySelectorAll('#type-scope option').length > 1")
    paths.clear()
    try:
        yield SimpleNamespace(page=instance, project=project_id, paths=paths, errors=errors)
    finally:
        context.close()
        server.should_exit = True
        thread.join(timeout=10)


def _click_runtime(page, view=None):
    """进「项目与运行」并切到某个分区（view=None 表示只进不切）。"""
    page.evaluate("""() => document.querySelector('[data-tab="runtime"]').click()""")
    if view:
        page.evaluate("""(view) => document.querySelector(`[data-runtime-view="${view}"]`).click()""", view)
    page.wait_for_timeout(120)


def _visible_panels(page):
    return page.evaluate("""() => [...document.querySelectorAll('.runtime-panel')]
        .filter(el => getComputedStyle(el).display !== 'none')
        .map(el => el.id)""")


def test_sidebar_has_exactly_one_runtime_entry_and_no_old_page_tabs(runtime):
    """13 项平铺菜单 → 10 项：四个运行页各占一个菜单的日子结束。"""
    page = runtime.page
    tabs = page.evaluate("""() => [...document.querySelectorAll('aside nav button[data-tab]')]
        .map(b => b.dataset.tab)""")
    for retired in ('projects', 'dashboard', 'jobs', 'evaluation'):
        assert retired not in tabs, f'旧页签 {retired} 必须已被「项目与运行」取代'
    assert tabs.count('runtime') == 1
    label = page.evaluate("""() => document.querySelector('[data-tab="runtime"]').textContent.trim()""")
    assert label == '项目与运行'
    # 「项目与运行」这一组只有一个入口，不该默认折叠（折叠了用户就找不到它在哪）
    opened = page.evaluate("""() => {
        const group=[...document.querySelectorAll('details.nav-group')]
            .find(d => d.querySelector('[data-tab="runtime"]'));
        return group ? group.open : null;
    }""")
    assert opened is True


def test_entering_the_page_shows_one_screen_at_a_time_and_a_question_per_screen(runtime):
    """一屏一问：进来只看得到一屏，且每一格都写清"这里回答什么"。"""
    page = runtime.page
    _click_runtime(page)
    page.wait_for_function("() => !document.getElementById('dashboard').textContent.includes('正在读取')")
    assert page.evaluate("() => document.getElementById('tab-runtime').classList.contains('hidden')") is False
    assert page.evaluate("() => document.querySelector('.runtime-hero h2').textContent.trim()") == '项目与运行'
    # 四格分区，且每格都带一句说明（只有一个名词的分区等于没写）
    views = page.evaluate("""() => [...document.querySelectorAll('[data-runtime-view]')]
        .map(b => ({view: b.dataset.runtimeView, pressed: b.getAttribute('aria-pressed'),
                    name: b.querySelector('b').textContent.trim(),
                    note: (b.querySelector('small')||{}).textContent?.trim() || ''}))""")
    assert [item['view'] for item in views] == ['project', 'overview', 'jobs', 'snapshots']
    for item in views:
        assert item['name'], item
        assert len(item['note']) >= 8, f"分区 {item['view']} 没写清它回答什么：{item['note']!r}"
    assert [item['view'] for item in views if item['pressed'] == 'true'] == ['overview']
    # 一次只显示一屏
    assert _visible_panels(page) == ['tab-dashboard']
    assert page.evaluate("() => document.getElementById('title').textContent") == '项目与运行 · 运行总览'
    # 进这一屏就把这一屏的数据给出来（不允许"空着等你点刷新"）
    assert any(path.startswith('projects/') and path.endswith('/dashboard') for path in runtime.paths), runtime.paths


def test_switching_partitions_loads_that_partition_and_hides_the_others(runtime):
    """切分区＝换一问：任务/快照/项目三屏都自己拉数据，且同时只有一屏可见。"""
    page = runtime.page
    _click_runtime(page)
    runtime.paths.clear()

    _click_runtime(page, 'jobs')
    page.wait_for_function("() => document.getElementById('tasks').textContent.trim() !== ''")
    assert _visible_panels(page) == ['tab-jobs']
    assert page.evaluate("() => document.getElementById('title').textContent") == '项目与运行 · 后台任务'
    assert any(path.endswith('/jobs') for path in runtime.paths), runtime.paths
    assert page.evaluate("() => document.getElementById('tasks').textContent.trim()") != ''

    runtime.paths.clear()
    _click_runtime(page, 'snapshots')
    assert _visible_panels(page) == ['tab-evaluation']
    assert page.evaluate("() => document.getElementById('title').textContent") == '项目与运行 · 快照与评测'
    # 快照列表必须进来就自动读（以前要先点一次「刷新」才看得到）
    assert any(path.startswith('projects/') and path.endswith('/snapshots') for path in runtime.paths), runtime.paths
    page.wait_for_function("() => document.getElementById('snapshot-cards').children.length > 0")
    assert '还没有快照' in page.evaluate("() => document.getElementById('snapshot-cards').textContent")

    runtime.paths.clear()
    _click_runtime(page, 'project')
    page.wait_for_function("() => document.querySelector('#projects-root .projects-header h2')")
    assert _visible_panels(page) == ['tab-projects']
    assert page.evaluate("() => document.getElementById('title').textContent") == '项目与运行 · 项目设置'
    assert page.evaluate("() => document.querySelector('#projects-root .projects-header h2').textContent.trim()") == '项目管理'
    assert page.evaluate("() => document.getElementById('projects-root').textContent").count('运行层合并') >= 1
    assert not runtime.errors, runtime.errors


def test_runtime_page_keeps_the_guided_ui_rules(runtime):
    """引导式界面的三条硬线：字号 ≥11px、首屏内容起点 ≤140px、一屏最多一个实心按钮。

    量的是像素，不是印象——每条断言都能用读数证伪。
    """
    page = runtime.page
    _click_runtime(page)
    # ① 首屏内容起点：页头（标题栏）下方第一块内容不能压到半屏以下
    top = page.evaluate("() => document.querySelector('#tab-runtime .runtime-hero').getBoundingClientRect().top")
    assert top <= 140, f'首屏内容起点 {top}px 超出 140px'

    # ② 字号下限 11px：逐屏检查（只看直接承载文字的节点）
    for view in ('overview', 'jobs', 'snapshots', 'project'):
        _click_runtime(page, view)
        offenders = page.evaluate("""() => {
            const scopes=[document.getElementById('tab-runtime'),
                          ...document.querySelectorAll('.runtime-panel')].filter(el => el && getComputedStyle(el).display !== 'none');
            const bad=[];
            for (const scope of scopes){
                for (const el of scope.querySelectorAll('*')){
                    const own=[...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim());
                    if (!own) continue;
                    const size=parseFloat(getComputedStyle(el).fontSize);
                    if (size < 11) bad.push(size + 'px ' + el.tagName + '.' + (el.getAttribute('class')||'') + ' :: ' + el.textContent.trim().slice(0, 24));
                }
            }
            return [...new Set(bad)];
        }""")
        assert offenders == [], f'{view} 分区里有小于 11px 的文字：{offenders}'

    # ③ 一屏最多一个实心按钮（实心 = 填了底色；白底/透明就是次级按钮）
    #    不写死某一个绿色：站点里主色有 #19654e 与 #216952 两种，写死色值会把"换了主色"变成假红灯。
    for view, expected in (('overview', 1), ('jobs', 0), ('snapshots', 1), ('project', 1)):
        _click_runtime(page, view)
        visible = page.evaluate("""() => {
            const panel=[...document.querySelectorAll('.runtime-panel')]
                .find(el => getComputedStyle(el).display !== 'none' && el.offsetParent);
            if (!panel) return [];
            const isSolid=bg => !(bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent' || bg === 'rgb(255, 255, 255)');
            return [...panel.querySelectorAll('button')]
                // 真机上根本看不见的东西（1px 裁剪、隐藏）不算"屏幕上的按钮"
                .filter(button => {
                    if (getComputedStyle(button).display === 'none' || !button.offsetParent) return false;
                    const rect=button.getBoundingClientRect();
                    return rect.width >= 2 && rect.height >= 2;
                })
                .map(button => ({text: button.textContent.trim(),
                                 bg: getComputedStyle(button).backgroundColor,
                                 solid: isSolid(getComputedStyle(button).backgroundColor)}));
        }""")
        solid = [button['text'] for button in visible if button['solid']]
        assert len(solid) <= 1, f'{view} 分区里有 {len(solid)} 个实心按钮：{visible}'
        assert len(solid) == expected, f'{view} 分区实心按钮应为 {expected} 个，实际 {visible}'
    assert not runtime.errors, runtime.errors
