"""Functional preservation checks for the unified retrieval workspace."""
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page
from test_graph_workspace_layout_browser import _open, _graph


def test_unified_workspace_keeps_original_controls_and_handlers(redesign_page):
    page, errors = redesign_page
    _open(page)
    assert page.locator('#graph-workspace-header').get_attribute('data-presentation') == 'unified'
    assert page.locator('#graph-workspace-header > .retrieval-page-heading').is_visible()
    assert page.locator('#graph-workspace-header > .retrieval-query-panel #query').is_visible()
    assert page.locator('#graph-workspace-header > #scope #scope-toggle').is_visible()
    for selector in ['#search-mode', '#ontology-expansion-toggle', '.search-options > summary', '#scope-to-qa']:
        assert page.locator(selector).is_visible(), selector
    page.click('#ontology-expansion-toggle')
    assert page.locator('#ontology-expansion').is_checked()
    page.click('#scope-toggle')
    page.fill('#query-platforms', '统一平台')
    page.click('#apply-scope')
    assert '统一平台' in page.locator('#applied-scope-chips').inner_text()
    assert page.evaluate("() => WorkspaceContext.read(current, 'search').applicability.platforms.values") == ['统一平台']
    page.click('[data-fg-mode="local"]')
    page.click('#graph-settings')
    for selector in ['#graph-entity-filter', '#graph-entity-choice', '#type-scope', '#predicate-scope', '#graph-labels', '#graph-edge-labels', '#draw-graph']:
        assert page.locator(selector).is_visible(), selector
    page.keyboard.press('Escape')
    assert not page.locator('#graph-settings-drawer').is_visible()
    page.click('[data-fg-mode="full"]')
    page.locator('.full-graph-options > summary').click()
    assert page.locator('#full-graph-settings-drawer').is_visible()
    assert page.locator('#full-graph-settings-drawer .full-graph-settings-panel').evaluate('(el) => getComputedStyle(el).position') == 'static'
    page.locator('#full-graph-settings-drawer [data-fg="labels"]').uncheck()
    assert not page.locator('[data-fg="labels"]').is_checked()
    page.keyboard.press('Escape')
    assert not page.locator('#full-graph-settings-drawer').is_visible()
    page.evaluate('async () => { await FullGraphView.restart(); }')
    assert page.evaluate('() => FullGraphView.renderer.showLabels') is False
    for action in ['load', 'next', 'auto', 'pause', 'cancel', 'restart']:
        assert page.locator(f'[data-fg="{action}"]').count() == 1
    assert errors == []


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_shared_mode_tools_and_panels_remain_usable(redesign_page, width):
    page, errors = redesign_page
    _open(page, width, 950)
    _graph(page)
    for mode in ['local', 'full', 'local']:
        page.click(f'[data-fg-mode="{mode}"]')
        page.evaluate('() => GraphWorkspaceLayout.settled()')
        assert page.locator(f'[data-fg-mode="{mode}"]').get_attribute('aria-pressed') == 'true'
        tools = '#graph-zoom-in' if mode == 'local' else '[data-fg="zoom-in"]'
        assert page.locator(tools).is_visible()
        assert page.locator(tools).locator('..').get_attribute('class') == 'retrieval-camera-tools'
        assert page.locator('#graph-fullscreen').is_visible()
        assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
    if width <= 768:
        page.evaluate('''() => {
          const notice = document.getElementById('graph-canvas-discovery-hint');
          notice.hidden = false;
          notice.innerHTML = '<b>3</b> 项待核对候选 <button type="button">核对候选</button>';
        }''')
        summary = page.locator('.graph-discovery-notice > summary')
        assert summary.is_visible()
        assert summary.evaluate('''el => {
          const r=el.getBoundingClientRect();
          return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
        }''')
        summary.click()
        assert page.locator('.graph-discovery-notice button').is_visible()
        page.keyboard.press('Escape')
    page.click('#graph-settings')
    dialog = page.locator('#graph-settings-drawer')
    assert dialog.is_visible()
    box = dialog.bounding_box()
    assert box['x'] >= -1 and box['x'] + box['width'] <= width + 1
    page.keyboard.press('Escape')
    assert not dialog.is_visible()
    assert errors == []


def test_navigation_collapse_keeps_tabs_project_and_session_accessible(redesign_page):
    page, errors = redesign_page
    _open(page)
    toggle = page.locator('#workspace-nav-collapse')
    assert toggle.is_visible()
    expanded = page.locator('body > aside').bounding_box()['width']
    toggle.click()
    assert toggle.get_attribute('aria-expanded') == 'false'
    compact = page.locator('body > aside').bounding_box()['width']
    assert compact < expanded - 80
    assert page.locator('body > aside [data-tab="search"]').is_visible()
    assert page.locator('body > aside [data-tab="search"]').get_attribute('title') == '检索与图谱'
    page.locator('body > aside [data-tab="mindmap"]').click()
    assert page.locator('#tab-mindmap').is_visible()
    page.locator('body > aside [data-tab="search"]').click()
    assert page.locator('#tab-search').is_visible()
    toggle.click()
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert page.locator('#project').is_visible()
    toggle.click()
    page.reload(wait_until='domcontentloaded')
    page.wait_for_selector('body.workspace-nav-compact')
    assert page.locator('#workspace-nav-collapse').get_attribute('aria-expanded') == 'false'
    assert errors == []


def test_real_workspace_screenshots_and_compact_project_access(redesign_page):
    page, errors = redesign_page
    _open(page, 1600, 1000)
    notification = page.locator('#status .ks-notice__close')
    if notification.count():
        notification.click()
    names = ['退款申请', '商户', '订单', '退款时限', '消费者', '退款条件', '平台', '售后规则', '支付渠道']
    nodes = [dict(id=f'preview-{i}', kind='entity', type=['Process', 'Actor', 'Rule'][i % 3], text=name, version=1)
             for i, name in enumerate(names)]
    links = [dict(id=f'preview-edge-{i}', kind='relation', type='关联', text='关联', subject_id='preview-0', object_id=node['id'], version=1)
             for i, node in enumerate(nodes[1:])]
    page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': nodes + links, 'truncated': False}))
    page.route('**/records/page', lambda route: route.fulfill(json={'records': nodes, 'total': len(nodes), 'next_cursor': None}))
    page.route('**/timeline?*', lambda route: route.fulfill(json={'events': [], 'truncated': False}))
    page.route('**/records/*/evidence', lambda route: route.fulfill(json={'documents': [], 'message': '没有来源'}))
    page.click('[data-fg-mode="local"]')
    page.evaluate("() => drawGraph('preview-0', 1)")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    page.wait_for_function('() => wb.chart.getZr().animation.isFinished()')
    output = Path('.benchmarks/retrieval-workspace')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / 'local-desktop.png'), full_page=True)
    page.click('#workspace-nav-collapse')
    page.click('#workspace-project-toggle')
    assert page.locator('#project').is_visible()
    page.keyboard.press('Escape')
    assert page.locator('#workspace-project-toggle').evaluate('(el) => el === document.activeElement')
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(output / 'local-compact.png'), full_page=True)
    page.click('[data-fg-mode="full"]')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    page.screenshot(path=str(output / 'full-compact.png'), full_page=True)
    page.locator('.full-graph-options > summary').click()
    page.screenshot(path=str(output / 'full-settings.png'), full_page=True)
    page.keyboard.press('Escape')
    page.set_viewport_size({'width': 390, 'height': 950})
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#project').is_visible()
    assert page.locator('#qa-app-menu-toggle').is_visible()
    locate = page.locator('.full-graph-locate').bounding_box()
    assert locate['x'] >= 0 and locate['x'] + locate['width'] <= 391
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(output / 'mobile.png'), full_page=True)
    assert errors == []
