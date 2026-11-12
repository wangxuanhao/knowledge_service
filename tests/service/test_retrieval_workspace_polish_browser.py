"""Layout regressions reported in the real retrieval workspace screenshots."""
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page
from test_graph_workspace_layout_browser import _open, _graph


@pytest.mark.parametrize('width', [320, 390, 768, 1440])
def test_search_settings_and_scope_belong_to_search_card(redesign_page, width):
    page, errors = redesign_page
    _open(page, width, 950)
    assert page.locator('.retrieval-query-panel .searchbar > .search-options').count() == 1
    assert page.locator('.retrieval-page-heading .search-options').count() == 0
    search = page.locator('.retrieval-query-panel').bounding_box()
    scope = page.locator('#scope').bounding_box()
    assert abs(scope['y'] - search['y'] - search['height']) <= 1
    page.locator('.search-options > summary').click()
    assert page.locator('.search-options-panel').is_visible()
    panel = page.locator('.search-options-panel').bounding_box()
    assert panel['x'] >= 0 and panel['x'] + panel['width'] <= width + 1
    assert page.locator('#build-index').is_visible()
    page.locator('.search-options > summary').click()
    page.click('#scope-toggle')
    page.fill('#query-platforms', '布局修整平台')
    page.click('#apply-scope')
    assert '布局修整平台' in page.locator('#applied-scope-chips').inner_text()
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
    assert errors == []


@pytest.mark.parametrize('width', [390, 1440])
def test_graph_footer_does_not_squeeze_types_into_timeline(redesign_page, width):
    page, errors = redesign_page
    _open(page, width, 950)
    _graph(page)
    for _ in range(2):
        page.evaluate("() => drawGraph('layout-0', 1)")
        page.evaluate('() => GraphWorkspaceLayout.settled()')
        assert page.locator('#graph-type-buttons').locator('..').get_attribute('class') == 'retrieval-footer-tools'
        assert page.locator('#graph-timeline').locator('..').get_attribute('class') == 'retrieval-footer-tools'
        types = page.locator('#graph-type-buttons').bounding_box()
        timeline = page.locator('#graph-timeline').bounding_box()
        separated = (types['x'] + types['width'] + 9 <= timeline['x'] or
                     types['y'] + types['height'] + 7 <= timeline['y'])
        assert separated, (types, timeline)
        assert page.locator('#graph-timeline .tl-points').is_visible()
        assert page.locator('#graph-type-buttons [data-graph-type-toggle]').is_visible()
    page.click('[data-fg-mode="full"]')
    assert page.locator('.full-graph-types').locator('..').get_attribute('class') == 'retrieval-footer-tools'
    assert page.locator('.full-graph-footer').locator('..').get_attribute('class') == 'retrieval-footer-tools'
    for action in ['load', 'next', 'auto', 'pause', 'cancel', 'restart']:
        assert page.locator(f'.retrieval-footer-tools [data-fg="{action}"]').count() == 1
    assert errors == []


def test_neighbor_selects_and_focus_action_have_their_own_space(redesign_page):
    page, errors = redesign_page
    _open(page)
    nodes, _ = _graph(page)
    page.evaluate('(node) => GraphExplorer.select(node)', nodes[0])
    page.click('#graph-neighbors')
    direction = page.locator('#graph-explorer [data-explorer="direction"]')
    hops = page.locator('#graph-explorer [data-explorer="hops"]')
    focus = page.locator('#graph-explorer [data-explorer="focus"]')
    assert direction.is_visible() and hops.is_visible() and focus.is_visible()
    assert direction.evaluate('(el) => getComputedStyle(el).backgroundImage').startswith('url(')
    d, h, f = direction.bounding_box(), hops.bounding_box(), focus.bounding_box()
    assert f['y'] >= max(d['y'] + d['height'], h['y'] + h['height']) + 7
    assert abs(d['width'] - h['width']) <= 1
    panel_box = page.locator('#graph-explorer').bounding_box()
    canvas_box = page.locator('#graph-canvas').bounding_box()
    assert panel_box['y'] + panel_box['height'] <= canvas_box['y'] + canvas_box['height'] + 1
    direction.select_option('forward')
    assert page.evaluate('() => GraphExplorer.snapshot().direction') == 'forward'
    hops.select_option('2')
    assert page.evaluate('() => GraphExplorer.snapshot().hops') == 2
    output = Path('.benchmarks/retrieval-polish')
    output.mkdir(parents=True, exist_ok=True)
    notification = page.locator('#status .ks-notice__close')
    if notification.count():
        notification.click()
    page.screenshot(path=str(output / 'desktop-neighbors.png'), full_page=True)
    page.click('#graph-explorer [data-explorer="close"]')
    page.screenshot(path=str(output / 'desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 950})
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    page.screenshot(path=str(output / 'mobile.png'), full_page=True)
    assert errors == []


def test_fullscreen_footer_keeps_canvas_space_with_details(redesign_page):
    page, errors = redesign_page
    _open(page)
    _graph(page)
    page.evaluate("() => selectEntityDetail('layout-0')")
    page.click('#graph-fullscreen')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    canvas = page.locator('#graph-canvas').bounding_box()
    stage = page.locator('#graph-stage').bounding_box()
    geometry = page.evaluate('''() => {
      const stage=document.getElementById('graph-stage');
      return {rows:getComputedStyle(stage).gridTemplateRows, children:[...stage.children].map(el=>({
        id:el.id,cls:el.className,hidden:el.hidden,row:getComputedStyle(el).gridRow,
        width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height,
        children:el.classList.contains('retrieval-footer-tools')?[...el.children].map(n=>({id:n.id,width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height})):[]}))};
    }''')
    if canvas['height'] < stage['height'] - 230:
        print(geometry)
    assert canvas['height'] >= stage['height'] - 230, geometry
    assert errors == []
