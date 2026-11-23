"""Community build layout and state transitions against isolated API fixtures."""

from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page  # noqa: F401


@pytest.fixture
def postgres_backend():
    """The browser fixture serves mock responses without a database."""


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_build_controls_align_and_reflect_selected_scope(redesign_page, width):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 950})
    requests = []
    current = {'state': 'partial', 'enabled': True, 'revision': 'r1',
               'built_at': '2026-11-23T05:00:00Z',
               'counts': {'communities': 8, 'entities': 64, 'covered_entities': 60,
                          'isolated_entities': 4, 'reports_ready': 6, 'reports_failed': 2}}

    def community_api(route):
        path = route.request.url.rsplit('/', 1)[-1]
        body = route.request.post_data_json
        requests.append((path, body))
        if path == 'build':
            current.update(state='building', job={'status': 'running', 'progress': 42,
                                                  'stage': '正在生成社区报告 2/8'})
            return route.fulfill(json={'status': current})
        if body['scope']['kind'] == 'project':
            return route.fulfill(json={'state': 'disabled', 'enabled': False})
        route.fulfill(json=current)

    page.route('**/api/projects/p/communities/*', community_api)
    page.route('**/api/projects/p/dashboard', lambda route: route.fulfill(json={'counts': {'entity': 64, 'relation': 39}}))
    page.evaluate("() => { KnowledgeTabs.open('runtime'); RuntimeView.show('communities'); }")
    build = page.locator('#community-build')
    refresh = page.locator('#community-build-refresh')
    retry = page.locator('#community-build-retry')
    status = page.locator('#community-build-status')
    metrics = page.locator('#community-build-metrics')
    expect(page.locator('#community-build-preview')).to_contain_text('64 个实体')
    expect(status).to_contain_text('这个范围还没有构建社区索引。')
    expect(metrics).to_be_hidden()
    expect(retry).to_be_hidden()
    assert {control.bounding_box()['height'] for control in [build, refresh]} == {44}

    scope = page.get_by_label('构建范围', exact=True)
    scope.select_option('platform')
    expect(status).to_contain_text('请输入平台名称。')
    expect(build).to_be_disabled()
    expect(refresh).to_be_disabled()
    platform = page.get_by_label('平台名称', exact=True)
    platform.fill('美团')
    platform.press('Tab')
    expect(status).to_contain_text('部分报告尚不可用')
    expect(metrics).to_contain_text('60 / 64')
    expect(metrics).to_contain_text('6 / 8')
    expect(metrics).to_contain_text('2 份失败')
    expect(build).to_have_text('检查并构建')
    expect(retry).to_have_text('重试失败报告')
    controls = [scope, platform, build, refresh, retry]
    boxes = [control.bounding_box() for control in controls]
    assert {box['height'] for box in boxes} == {44}
    assert all(box['x'] >= 0 and box['x'] + box['width'] <= width + 1 for box in boxes)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    output = Path('.benchmarks/community-build')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f'{width}.png'), full_page=True,
                    style='.ks-notice { visibility: hidden; }')

    retry.click()
    expect(page.get_by_role('progressbar', name='社区构建进度')).to_have_attribute('value', '42')
    expect(status).to_contain_text('正在生成社区报告 2/8')
    expect(build).to_be_disabled()
    expect(retry).to_be_hidden()
    assert [body for path, body in requests if path == 'build'] == [
        {'scope': {'kind': 'platform', 'platform': '美团'}, 'retry_failed': True},
    ]

    current.update(state='ready', job=None, counts={**current['counts'], 'reports_ready': 8, 'reports_failed': 0})
    refresh.click()
    expect(status).to_contain_text('社区索引可用。')
    expect(metrics).to_contain_text('8 / 8')
    expect(page.get_by_role('progressbar')).to_be_hidden()
    expect(build).to_be_enabled()

    current.update(state='failed', job={'status': 'failed', 'error': '无法读取图谱快照'})
    refresh.click()
    expect(status).to_contain_text('无法读取图谱快照')
    expect(retry).to_have_text('重试构建')
    scope.select_option('project')
    expect(status).to_contain_text('这个范围还没有构建社区索引。')
    expect(metrics).to_be_hidden()
    expect(retry).to_be_hidden()
    assert not errors
