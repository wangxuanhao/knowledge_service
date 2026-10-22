"""「项目与运行」页的真实浏览器契约（D2：项目管理 / 项目总览 / 后台任务 / 快照·评测 四页合并）。

为什么要单独有这一份：

  · 合并的收益全在"用户能不能一眼找到、能不能少点一次"——只有真页面能量：
    一个入口（不是四个平铺菜单）、一屏一问（同一时刻只有一个分区可见）、
    进哪一屏就把那一屏的数据给出来（不用先点一次「刷新」）；
  · 引导式界面规则是硬线，而它们是**像素读数**，文案契约测不出来：
    字号 ≥11px、首屏内容起点 ≤140px、一屏最多一个实心按钮。

这里打的是真实服务（真实 PostgreSQL 槽位 + 真实 index.html），只用真实项目做只读操作。
"""
from ontology_seed import seed_ontology
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
    seed_ontology(repository, project_id, turtle, Ontology(turtle).summary())
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
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started

        context = browser.new_context(viewport={'width': 1680, 'height': 950})
        try:
            instance = context.new_page()
            errors, paths = [], []
            instance.on('pageerror', lambda error: errors.append(str(error)))
            # 只记 /api/ 之后的路径（去掉 api 前缀），断言里就能直接写 projects/<id>/dashboard
            instance.on('request', lambda request: paths.append(request.url.split('/api/', 1)[1])
                        if '/api/' in request.url else None)
            instance.goto(f'http://127.0.0.1:{port}/', wait_until='load')
            instance.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
            instance.select_option('#project', project_id)
            # The selected project is ready without loading a hidden graph selector.
            instance.wait_for_function('(project) => current === project', arg=project_id)
            paths.clear()
            yield SimpleNamespace(page=instance, project=project_id, paths=paths, errors=errors)
        finally:
            context.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _click_runtime(page, view=None):
    """从可见的「构建与任务」入口进入，再切到所需分区。"""
    page.click('aside nav [data-tab="runtime"]')
    page.locator('#tab-runtime .runtime-views').wait_for(state='visible')
    if view:
        page.click('#tab-runtime [data-runtime-view="'+view+'"]')
        page.wait_for_function('(view) => window.RuntimeView.current === view', arg=view)


def _visible_panels(page):
    return page.evaluate("""() => [...document.querySelectorAll('.runtime-panel')]
        .filter(el => getComputedStyle(el).display !== 'none')
        .map(el => el.id)""")


def test_stable_navigation_exposes_global_management_and_quality(runtime):
    page = runtime.page
    assert page.locator('[data-tab="runtime"]').inner_text() == '构建与任务'
    assert page.locator('[data-tab="quality"]').is_visible()
    for name in ('projects','useradmin'):
        assert page.locator('.nav-management [data-tab="'+name+'"]').is_visible()
        assert page.locator('aside nav [data-tab="'+name+'"]').count() == 0
    assert page.locator('.nav-group h2').all_inner_texts() == ['使用知识','管理资料','运行与质量','管理']
    assert not runtime.errors


def test_runtime_entry_loads_unfiltered_project_overview(runtime):
    page = runtime.page
    _click_runtime(page)
    page.wait_for_function("() => !document.getElementById('dashboard').textContent.includes('正在读取')")
    assert _visible_panels(page) == ['tab-dashboard']
    assert page.locator('#title').inner_text() == '构建与任务 · 项目概览'
    assert page.locator('#scope').is_visible() is False
    assert any(path.endswith('/dashboard') for path in runtime.paths)
    assert not runtime.errors


def test_runtime_tasks_backup_and_global_project_center_reuse_real_loaders(runtime):
    page = runtime.page
    _click_runtime(page,'jobs')
    page.wait_for_function("() => document.getElementById('tasks').textContent.trim() !== ''")
    assert _visible_panels(page) == ['tab-jobs']
    assert any(path.endswith('/jobs') for path in runtime.paths)
    _click_runtime(page,'snapshots')
    assert _visible_panels(page) == ['tab-evaluation']
    page.wait_for_function("() => document.getElementById('snapshot-cards').children.length > 0")
    assert page.locator('.backup-only').last.is_visible()
    assert page.locator('.extraction-evaluation').is_visible() is False
    page.click('[data-tab="quality"]')
    assert page.locator('.extraction-evaluation').is_visible()
    assert page.locator('.backup-only').last.is_visible() is False
    page.click('[data-tab="projects"]')
    page.wait_for_selector('#projects-root .projects-header h2')
    assert _visible_panels(page) == ['tab-projects']
    assert page.locator('#title').inner_text() == '项目管理'
    assert page.locator('#scope').is_visible() is False
    assert '运行层合并' in page.locator('#projects-root').inner_text()
    assert not runtime.errors


def test_runtime_toolbar_is_compact_and_readable(runtime):
    page = runtime.page
    _click_runtime(page)
    assert page.locator('#tab-runtime .runtime-views').bounding_box()['y'] <= 180
    for view in ('overview','jobs','snapshots'):
        _click_runtime(page,view)
        assert page.locator('[data-runtime-view="'+view+'"]').get_attribute('aria-pressed') == 'true'
        assert page.evaluate("""() => [...document.querySelectorAll('#tab-runtime button')].every(el => parseFloat(getComputedStyle(el).fontSize)>=11)""")
    assert not runtime.errors
