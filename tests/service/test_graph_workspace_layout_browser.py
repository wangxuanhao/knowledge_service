"""Graph-first layout contracts against the real isolated search page."""

import pytest

from test_workspace_redesign_ui import redesign_page


def _open(page, width=1440, height=900):
    page.set_viewport_size({'width': width, 'height': height})
    page.evaluate("() => KnowledgeTabs.open('search')")
    assert page.locator('#graph-workspace-header').count() == 1, 'search needs its compact graph workspace header'
    page.evaluate('() => GraphWorkspaceLayout.settled()')


def _graph(page):
    nodes = [dict(id=f'layout-{i}', kind='entity', type='Thing', text=f'布局实体{i}', version=1)
             for i in range(2)]
    edge = dict(id='layout-edge', kind='relation', type='关联', subject_id='layout-0',
                object_id='layout-1', text='布局关联', version=1)
    page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': nodes + [edge], 'truncated': False}))
    page.route('**/records/page', lambda route: route.fulfill(json={'records': nodes, 'total': 2, 'next_cursor': None}))
    page.route('**/timeline?*', lambda route: route.fulfill(json={'events': [], 'truncated': False}))
    page.route('**/records/*/evidence', lambda route: route.fulfill(json={'documents': [], 'message': '没有来源'}))
    page.evaluate("() => drawGraph('layout-0', 1)")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    return nodes, edge


def _bounds(page, selector):
    return page.locator(selector).bounding_box()


def _within(inner, outer):
    return (inner['x'] >= outer['x'] - 1 and inner['y'] >= outer['y'] - 1
            and inner['x'] + inner['width'] <= outer['x'] + outer['width'] + 1
            and inner['y'] + inner['height'] <= outer['y'] + outer['height'] + 1)


def test_compact_header_owns_existing_query_and_unique_page_scope(redesign_page):
    page, errors = redesign_page
    _open(page)
    header = _bounds(page, '#graph-workspace-header')
    assert 56 <= header['height'] <= 64
    for selector in ['#query', '#search', '#scope-toggle', '#scope-to-qa']:
        assert _within(_bounds(page, selector), header), selector
    assert not page.locator('#graph-workspace-panel').is_visible()
    page.click('#scope-toggle')
    page.fill('#query-platforms', '布局平台')
    page.click('#apply-scope')
    assert page.evaluate("() => WorkspaceContext.read(current, 'search').applicability.platforms.values") == ['布局平台']
    assert '布局平台' in page.locator('#applied-scope-chips').inner_text()
    for target in ['qa', 'sources', 'records', 'search']:
        page.evaluate('(target) => KnowledgeTabs.open(target)', target)
        assert page.locator(f'#tab-{target} #scope').count() == 1
        assert page.locator('#scope').count() == 1
        if target == 'search':
            assert page.locator('#graph-workspace-header #scope').count() == 1
    duplicates = page.evaluate('''() => {
      const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
      return ids.filter((id, index) => ids.indexOf(id) !== index);
    }''')
    assert duplicates == []
    assert errors == []


@pytest.mark.parametrize('viewport', [(1440, 900), (1366, 768)])
def test_desktop_detail_uses_one_fixed_pane_without_losing_canvas_height(redesign_page, viewport):
    page, errors = redesign_page
    _open(page, *viewport)
    _graph(page)
    before = _bounds(page, '#graph-canvas')
    stage = _bounds(page, '#graph-stage')
    assert before['height'] >= viewport[1] - stage['y'] - 230
    assert stage['y'] + stage['height'] >= viewport[1] - 20
    page.evaluate("() => selectEntityDetail('layout-0')")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    pane = _bounds(page, '#graph-workspace-panel')
    after = _bounds(page, '#graph-canvas')
    assert 300 <= pane['width'] <= 320
    assert abs(before['height'] - after['height']) <= 1
    assert page.locator('#graph-workspace-panel #graph-detail').is_visible()
    assert not page.locator('#graph-workspace-panel #hits').is_visible()
    assert after['x'] + after['width'] <= pane['x'] + 1
    page.click('#detail-drawer-close')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert not page.locator('#graph-workspace-panel').is_visible()
    closed = _bounds(page, '#graph-canvas')
    assert closed['width'] > after['width'] + 290
    assert abs(closed['height'] - before['height']) <= 1
    assert errors == []


@pytest.mark.parametrize('width', [320, 390, 768, 1024, 1440])
def test_responsive_panel_and_filter_controls_stay_inside_viewport(redesign_page, width):
    page, errors = redesign_page
    _open(page, width, 900)
    if width <= 580:
        menu = page.locator('#qa-app-menu-toggle')
        assert menu.is_visible()
        assert page.locator('#project').is_visible()
        assert _bounds(page, 'body>aside')['height'] <= 88
        menu.click()
        assert page.locator('body>aside [data-tab="search"]').is_visible()
        assert menu.get_attribute('aria-expanded') == 'true'
        menu.click()
        page.evaluate('() => GraphWorkspaceLayout.settled()')
        assert menu.get_attribute('aria-expanded') == 'false'
        assert not page.locator('body>aside [data-tab="search"]').is_visible()
    _graph(page)
    page.evaluate("() => selectEntityDetail('layout-0')")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    pane = _bounds(page, '#graph-workspace-panel')
    canvas = _bounds(page, '#graph-canvas')
    assert pane['x'] >= 0 and pane['x'] + pane['width'] <= width + 1
    assert pane['y'] + pane['height'] <= 901
    assert canvas['height'] >= 160
    if width <= 768:
        assert pane['height'] <= 900 * .42 + 1
        assert pane['y'] >= canvas['y'] + canvas['height'] - 1
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
    for selector in ['#query', '#search', '#scope-toggle', '#scope-to-qa', '#detail-drawer-close']:
        assert page.locator(selector).is_visible(), selector
        box = _bounds(page, selector)
        assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1, selector
    page.click('#scope-toggle')
    assert page.locator('#query-platforms').is_visible()
    page.click('#scope-close')
    page.click('#detail-drawer-close')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert not page.locator('#graph-workspace-panel').is_visible()
    assert errors == []


def test_legacy_search_detail_and_path_tools_share_scroll_preserving_panel(redesign_page):
    page, errors = redesign_page
    _open(page)
    nodes, _ = _graph(page)
    hits = [dict(id=f'result-{i}', kind='entity', type='Thing', text=f'检索结果{i}', version=1)
            for i in range(18)]
    hits[0] = nodes[0]
    page.route('**/search', lambda route: route.fulfill(json={
        'active_mode': 'keyword', 'candidate_count': 18, 'hits': hits,
        'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '布局')
    page.click('#search')
    page.wait_for_selector('#hits .hit-entry')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-workspace-panel #hits').is_visible()
    results = page.locator('.search-results-pane')
    results.evaluate('(node) => { node.scrollTop = 120; }')
    scroll = results.evaluate('(node) => node.scrollTop')
    assert scroll > 0
    page.evaluate("() => selectEntityDetail('layout-0')")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-detail [data-action="history"]').is_visible()
    page.click('[role="tab"][data-graph-panel="results"]')
    assert results.evaluate('(node) => node.scrollTop') == scroll
    page.click('#graph-path-toggle')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-workspace-panel #graph-path-from').is_visible()
    page.select_option('#graph-path-from', 'layout-0')
    page.select_option('#graph-path-to', 'layout-1')
    page.get_by_role('button', name='寻找路径', exact=True).click()
    assert '1 跳' in page.locator('#graph-path-result').inner_text()
    page.click('#detail-drawer-close')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert not page.locator('#graph-workspace-panel').is_visible()
    # Visibility/class changes made by the panel itself must not count as new content.
    page.evaluate('() => GraphWorkspaceLayout.resize()')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert not page.locator('#graph-workspace-panel').is_visible()
    assert errors == []


def test_fullscreen_retains_renderer_modes_inspector_and_source_reader(redesign_page):
    page, errors = redesign_page
    _open(page)
    _graph(page)
    page.route('**/records/*/evidence', lambda route: route.fulfill(json={'documents': [{
        'title': '布局来源', 'version': 1, 'source_content': 'segments_only', 'mode': 'text',
        'before': '前文', 'highlight': '布局证据', 'after': '后文', 'preview': '布局证据'}]}))
    page.evaluate("() => selectEntityDetail('layout-0')")
    page.wait_for_selector('#graph-detail [data-source-evidence]')
    page.click('#graph-fullscreen')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-detail').is_visible()
    assert page.locator('#graph-canvas').is_visible()
    local_canvas = _bounds(page, '#graph-canvas')
    local_stage = _bounds(page, '#graph-stage')
    assert local_canvas['height'] >= local_stage['height'] - 230
    page.click('[data-fg-mode="full"]')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('.full-graph-host').is_visible()
    assert page.locator('#graph-detail').is_visible()
    full_canvas = _bounds(page, '.full-graph-host')
    stage = _bounds(page, '#graph-stage')
    pane = _bounds(page, '#graph-workspace-panel')
    assert _within(full_canvas, stage)
    assert full_canvas['height'] >= stage['height'] - 230
    assert full_canvas['x'] + full_canvas['width'] <= pane['x'] + 1
    page.evaluate('() => GraphWorkspaceLayout.closePanel()')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    closed = _bounds(page, '.full-graph-host')
    assert abs(closed['height'] - full_canvas['height']) <= 1
    assert closed['width'] > full_canvas['width'] + 290
    page.evaluate('() => GraphWorkspaceLayout.openDetail()')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    page.click('#graph-detail [data-source-evidence]')
    assert page.locator('#source-evidence-dialog').is_visible()
    assert '布局证据' in page.locator('#source-evidence-text').inner_text()
    page.click('#close-source-evidence')
    page.click('[data-fg-mode="local"]')
    page.click('#graph-fullscreen')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-canvas').is_visible()
    assert page.locator('#graph-detail').is_visible()
    assert errors == []


@pytest.mark.parametrize('fullscreen', [False, True])
def test_full_graph_settings_receive_pointer_above_scrolling_toolbar(redesign_page, fullscreen):
    page, errors = redesign_page
    _open(page)
    page.click('[data-fg-mode="full"]')
    if fullscreen:
        page.click('#graph-fullscreen')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    page.locator('.full-graph-options > summary').click()
    settings = _bounds(page, '.full-graph-settings-panel')
    assert _within(settings, {'x': 0, 'y': 0, 'width': 1440, 'height': 900})
    for action in ['labels', 'edges']:
        checkbox = page.locator(f'.full-graph-settings-panel [data-fg="{action}"]')
        assert checkbox.evaluate('''node => {
          const box = node.getBoundingClientRect();
          return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === node;
        }'''), 'full-graph settings must receive pointer input outside the toolbar clip'
        checkbox.uncheck()
        assert not checkbox.is_checked()
    assert errors == []


def test_keyboard_result_activation_moves_focus_into_visible_detail_panel(redesign_page):
    page, errors = redesign_page
    _open(page)
    nodes, _ = _graph(page)
    page.route('**/search', lambda route: route.fulfill(json={
        'active_mode': 'keyword', 'candidate_count': 1, 'hits': [nodes[0]],
        'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '键盘布局')
    page.locator('#query').press('Enter')
    result = page.locator('#hits [data-graph-node="layout-0"]')
    result.wait_for(state='visible')
    assert page.locator('#query').evaluate('(node) => node === document.activeElement'), 'async results must preserve query focus'
    result.focus()
    page.keyboard.press('Enter')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-detail').is_visible()
    assert page.evaluate('''() => {
      const focused = document.activeElement, panel = document.getElementById('graph-workspace-panel');
      return panel.contains(focused) && !!focused.getClientRects().length && !focused.closest('[hidden]');
    }'''), 'activating a result must not leave keyboard focus in its hidden result tab'
    assert errors == []
