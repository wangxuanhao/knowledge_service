"""Inline filters and library focus/hover states in the actual browser UI."""
from pathlib import Path

import pytest
from test_workspace_redesign_ui import redesign_page  # noqa: F401


@pytest.mark.parametrize('width', [1440, 320])
def test_scope_choice_checkboxes_align_with_their_labels_and_apply(redesign_page, width):
    page, errors = redesign_page
    choices = {
        'platforms': ['京东', '小红书', '抖音电商'],
        'businesses': ['零售', '售后'],
        'audiences': ['商家', '消费者'],
    }
    page.route('**/applicability-values', lambda route: route.fulfill(json=choices))
    page.evaluate("KnowledgeTabs.open('search')")
    page.set_viewport_size({'width': width, 'height': 850})
    page.locator('#scope-toggle').click()
    selected = {}
    for dimension, values in choices.items():
        picker = page.locator(f'#query-{dimension}')
        picker.locator('summary').click()
        checkbox = picker.get_by_role('checkbox', name=values[1], exact=True)
        checkbox.wait_for()
        option = picker.locator('label').filter(has_text=values[1])
        layout = option.evaluate('''row => {
          const box = row.querySelector('input').getBoundingClientRect();
          const range = document.createRange();
          range.selectNode(row.lastChild);
          const text = range.getBoundingClientRect();
          return {width: box.width, gap: text.left - box.right,
            centerOffset: Math.abs((box.top + box.bottom - text.top - text.bottom) / 2)};
        }''')
        assert 12 <= layout['width'] <= 18 and layout['gap'] >= 4 and layout['centerOffset'] <= 3, layout
        option.click()
        assert checkbox.is_checked()
        selected[dimension] = values[1]
    page.locator('#apply-scope').click()
    applied = page.evaluate("WorkspaceContext.read('p','search').applicability")
    assert {dimension: value['values'][0] for dimension, value in applied.items()} == selected
    assert not errors


@pytest.mark.parametrize('width', [1440, 768, 390, 320])
def test_inline_scope_entries_and_qa_popover(redesign_page, width):
    page, errors = redesign_page
    page.route('**/applicability-values', lambda route: route.fulfill(json=dict(platforms=['筛选测试平台'])))
    page.set_viewport_size({'width': width, 'height': 950})
    assert page.locator('#scope-to-qa').count() == 0
    for tab, host in [('sources', '.source-actions-row'),
                      ('records', '.panel > .row'), ('qa', '.qa-page-heading')]:
        page.evaluate('(tab) => KnowledgeTabs.open(tab)', tab)
        panel = page.locator(f'#tab-{tab} {host} #scope')
        assert panel.count() == 1, f'{tab} owns its inline filter entry'
        page.locator('#scope-toggle').click()
        popup = page.locator('#cb-details').bounding_box()
        assert popup['x'] >= -1 and popup['x'] + popup['width'] <= width + 1, popup
        assert popup['y'] >= 0 and popup['y'] + popup['height'] <= 951, popup
        assert page.locator('#query-platforms').is_visible()
        page.locator('#query-platforms > summary').click()
        page.get_by_role('checkbox', name='筛选测试平台', exact=True).check()
        page.locator('#apply-scope').click()
        assert page.evaluate(f"WorkspaceContext.read('p','{tab}').applicability.platforms.values") == ['筛选测试平台']
        assert page.locator('#scope-toggle').inner_text() == ('问答范围 · 1' if tab == 'qa' else '筛选 · 1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    output = Path('.benchmarks/page-scope-layout')
    output.mkdir(parents=True, exist_ok=True)
    page.locator('#scope-toggle').click()
    page.screenshot(path=str(output / f'qa-{width}.png'), full_page=True)
    assert errors == []


@pytest.mark.parametrize('width', [1440, 390])
def test_library_focus_belongs_to_field_and_hover_highlights_document(redesign_page, width):
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 950})
    page.evaluate("KnowledgeTabs.open('sources')")
    field = page.locator('.source-search__field')
    search = page.get_by_role('searchbox', name='搜索资料')
    search.click()
    style = search.evaluate('(el) => ({outline:getComputedStyle(el).outlineStyle,shadow:getComputedStyle(el).boxShadow})')
    assert style == {'outline': 'none', 'shadow': 'none'}, style
    assert field.evaluate('(el) => getComputedStyle(el).boxShadow') != 'none'
    card = page.locator('#source-list .source-item').first
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length > 0")
    page.mouse.move(0, 0)
    normal = card.evaluate('(el) => ({border:getComputedStyle(el).borderColor,shadow:getComputedStyle(el).boxShadow})')
    card.hover()
    highlighted = card.evaluate('(el) => ({border:getComputedStyle(el).borderColor,shadow:getComputedStyle(el).boxShadow})')
    assert normal != highlighted
    assert highlighted['shadow'] != 'none'
    assert card.evaluate('(el) => parseFloat(getComputedStyle(el).borderTopWidth)') >= 1
    assert errors == []
