"""Real-browser regression for unified metadata scope and graph path placement."""

from pathlib import Path
import json

from test_frontend_retrieval_flow import (
    _graph_edge_ids,
    _graph_node_ids,
    _is_graph_exchange,
    browser,
    workbench,
)


SCREENSHOT_ROOT = Path(__file__).resolve().parents[2] / '.benchmarks' / 'unified-scope-path-browser'


def _open_scope(page):
    if not page.locator('#cb-details').evaluate('(element) => element.open'):
        with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
            page.click('#scope-toggle')
    page.wait_for_function("document.getElementById('metadata-facet-summary').textContent.includes('系统字段')")


def _capture(page, name):
    destination = SCREENSHOT_ROOT / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(destination), full_page=True)


def _open_graph_settings(page):
    # Candidate pages load only when their drawer is visible.
    with page.expect_response(lambda response: response.url.endswith('/records/page')) as response:
        page.click('#graph-settings')
    return response.value.request.post_data_json


def test_scope_contains_all_business_and_graph_constraints(workbench):
    page = workbench.page
    page.set_viewport_size({'width': 1280, 'height': 920})
    assert page.locator('#scope-toggle').inner_text() == '筛选'
    for control in ('query-platforms', 'query-businesses', 'query-audiences'):
        assert page.locator(f'#scope #cb-details #{control}').count() == 1
    assert page.locator('#graph-stage #type-scope').count() == 1
    assert page.locator('#graph-stage #predicate-scope').count() == 1
    assert page.locator('#scope #type-scope').count() == 0
    assert not page.locator('#cb-details').evaluate('(element) => element.open')
    _open_scope(page)
    for control in ('query-platforms', 'query-businesses', 'query-audiences'):
        assert page.locator('#' + control).is_visible()
    _capture(page, 'unified-scope-1280.png')
    assert not workbench.errors


def test_three_dimensional_scope_reaches_graph_and_excludes_other_business(workbench):
    page = workbench.page
    scope = {'platforms': ['美团'], 'businesses': ['外卖'], 'audiences': ['商家']}
    expected_scope = {dimension: {'mode': 'specific', 'values': values}
                      for dimension, values in scope.items()}
    records = []
    for suffix, business in [('match', '外卖'), ('other', '到店')]:
        metadata = {'applicability': {
            dimension: {'mode': 'specific', 'values': [business] if dimension == 'businesses' else values}
            for dimension, values in scope.items()
        }}
        records.extend([
            {'id': suffix + '-a', 'kind': 'entity', 'type': 'Thing',
             'text': suffix + '商户', 'metadata': metadata},
            {'id': suffix + '-b', 'kind': 'entity', 'type': 'Thing',
             'text': suffix + '平台', 'metadata': metadata},
            {'id': suffix + '-r', 'kind': 'relation', 'type': 'mentions',
             'text': suffix + '关系', 'subject_id': suffix + '-a',
             'object_id': suffix + '-b', 'metadata': metadata},
        ])
    workbench.service.repository.put_batch(workbench.project, records)
    _open_scope(page)
    for dimension, values in scope.items():
        assert page.locator('#query-' + dimension).count() == 1
        page.fill('#query-' + dimension, '，'.join(values))
    assert page.evaluate('scope().applicability || null') is None
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if _is_graph_exchange(request) else None)
    page.click('#apply-scope')
    assert page.evaluate('scope().applicability') == expected_scope
    choices_request = _open_graph_settings(page)
    assert choices_request['applicability'] == expected_scope
    page.wait_for_function("[...document.querySelector('#graph-entity-choice').options].some(row=>row.value==='match-a')")
    page.select_option('#graph-entity-choice', 'match-a')
    page.wait_for_function("document.querySelector('#graph-node').value === 'match-a'")
    with page.expect_response(lambda response: _is_graph_exchange(response)):
        page.click('#draw-graph')
    assert bodies[-1]['applicability'] == expected_scope
    assert _graph_node_ids(page) == ['match-a', 'match-b']
    assert _graph_edge_ids(page) == ['match-r']
    page.click('#graph-settings-drawer .workspace-drawer-close')
    _open_scope(page)
    page.click('#reset-scope')
    assert not page.evaluate('scope().applicability || null')
    assert all(page.locator('#query-' + dimension).input_value() == '' for dimension in scope)
    assert not workbench.errors


def test_metadata_picker_hides_system_fields_and_keeps_business_fields(workbench):
    page = workbench.page
    workbench.service.repository.put_batch(workbench.project, [{
        'id': 'metadata-discovery', 'kind': 'document', 'text': '业务字段发现',
        'metadata': {'business_segment': '餐饮', 'batch_id': 'batch-1',
                     'readiness': {'search_ready': True}, 'source_version_id': 'source-1',
                     'applicability': {'platforms': {'mode': 'all'}}},
    }])
    _open_scope(page)
    page.click('#apply-scope')
    choices = page.locator('#metadata-field-choice option').evaluate_all(
        '(options) => options.map(option => option.value)')
    assert 'metadata.business_segment' in choices
    hidden = ['metadata.batch_id', 'metadata.readiness.search_ready',
              'metadata.source_version_id', 'metadata.applicability.platforms.mode']
    assert not any(field in choices for field in hidden)
    assert page.locator('#cb-show-system').count() == 1
    _open_scope(page)
    page.locator('#cb-details .cb-advanced').evaluate('(element) => {element.open = true;}')
    page.check('#cb-show-system')
    choices = page.locator('#metadata-field-choice option').evaluate_all(
        '(options) => options.map(option => option.value)')
    assert all(field in choices for field in hidden[:3])
    # Structured applicability controls remain the only editor for these dimensions.
    assert hidden[3] not in choices
    page.select_option('#metadata-field-choice', 'metadata.business_segment')
    page.select_option('#metadata-value-choice', '0')
    assert page.evaluate('scope().filters') is None
    page.click('#apply-scope')
    assert page.evaluate('scope().filters') == {
        'and': [{'field': 'metadata.business_segment', 'op': 'eq', 'value': '餐饮'}]}
    assert not workbench.errors


def test_graph_path_tool_opens_in_inspector_and_inspects_actual_relation(workbench):
    page = workbench.page
    assert page.locator('.graph-toolbar #graph-path-toggle').count() == 1
    page.click('#graph-path-toggle')
    tools = page.locator('.inspector-rail #graph-path-tools')
    assert tools.count() == 1
    assert tools.is_visible()
    page.select_option('#graph-path-from', 'a')
    page.select_option('#graph-path-to', 'b')
    tools.get_by_role('button', name='寻找路径', exact=True).click()
    edge = page.locator('#graph-path-result [data-path-edge="r"]')
    assert edge.count() == 1
    edge.click()
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款关系')")
    assert '退款关系' in page.locator('#graph-detail').inner_text()
    assert not workbench.errors


def test_advanced_json_scope_summary_and_reset_remain_consistent(workbench):
    page = workbench.page
    _open_scope(page)
    page.locator('#cb-details .cb-advanced').evaluate('(element) => {element.open = true;}')
    filters = {'and': [{'field': 'metadata.title', 'op': 'eq', 'value': '退款规则'}]}
    page.fill('#cb-raw-json', json.dumps(filters, ensure_ascii=False))
    page.locator('#cb-raw-json').dispatch_event('change')
    assert page.evaluate('scope().filters') is None
    page.click('#apply-scope')
    assert page.evaluate('scope().filters') == filters
    assert '更多字段' in page.locator('#applied-scope-chips').inner_text()
    _open_scope(page)
    page.click('#reset-scope')
    assert page.locator('#filters').input_value() == ''
    assert page.locator('#cb-raw-json').input_value() == ''
    assert page.locator('#applied-scope-chips').inner_text() == ''
    assert page.locator('#cb-add').is_enabled()
    assert not workbench.errors


def test_graph_path_remains_visible_on_narrow_screen_without_selected_detail(workbench):
    page = workbench.page
    page.set_viewport_size({'width': 760, 'height': 920})
    assert page.locator('#graph-detail').inner_text() == ''
    assert page.locator('.graph-toolbar #graph-path-toggle').count() == 1
    page.click('#graph-path-toggle')
    tools = page.locator('.inspector-rail #graph-path-tools')
    if not tools.is_visible():
        diagnostics = tools.evaluate('''element => {
          const parents = [];
          for (let node = element; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            parents.push({tag: node.tagName, id: node.id, className: node.className,
              display: style.display, visibility: style.visibility, position: style.position});
          }
          return {open: element.open, parents,
            expanded: document.querySelector('#graph-path-toggle').getAttribute('aria-expanded')};
        }''')
        screenshot = SCREENSHOT_ROOT / 'unified-path-760-failure.png'
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert tools.is_visible(), diagnostics
    assert page.locator('#graph-path-from').is_visible()
    assert page.locator('#graph-path-to').is_visible()
    assert page.locator('.inspector-rail').evaluate(
        '(element) => getComputedStyle(element).display') != 'none'
    _capture(page, 'unified-path-760.png')
    assert not workbench.errors


def test_deep_page_entity_keeps_edit_local_graph_and_mindmap_actions(workbench):
    page = workbench.page
    metadata = {'applicability': {'platforms': {'mode': 'all'}}}
    records = [{'id': f'page-{i:03}', 'kind': 'entity', 'type': 'Thing',
                'text': f'分页实体{i}', 'metadata': metadata} for i in range(75)]
    records += [
        {'id': 'zz-deep-target', 'kind': 'entity', 'type': 'Thing', 'text': '深页目标', 'metadata': metadata},
        {'id': 'zz-deep-neighbor', 'kind': 'entity', 'type': 'Thing', 'text': '深页邻居', 'metadata': metadata},
        {'id': 'zz-deep-edge', 'kind': 'relation', 'type': 'mentions', 'text': '深页关系',
         'subject_id': 'zz-deep-target', 'object_id': 'zz-deep-neighbor', 'metadata': metadata},
    ]
    workbench.service.repository.put_batch(workbench.project, records)
    _open_graph_settings(page)
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-node').value === 'a'")
    with page.expect_response(lambda response: response.url.endswith('/records/page')):
        page.click('#graph-choice-next')
    page.wait_for_function("[...document.querySelector('#graph-entity-choice').options].some(row => row.value === 'zz-deep-target')")
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    page.select_option('#graph-entity-choice', 'zz-deep-target')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('深页目标')")
    page.click('#graph-settings-drawer .workspace-drawer-close')
    page.locator('#graph-detail [data-action="edit"]').click()
    assert page.locator('#record-dialog').is_visible()
    assert page.locator('#revision-id').input_value() == 'zz-deep-target'
    page.click('#close-record-dialog')
    with page.expect_response(lambda response: _is_graph_exchange(response)
                              and response.request.post_data_json.get('hops') == 1) as neighborhood:
        page.click('#graph-expand')
    assert neighborhood.value.request.post_data_json['seeds'] == ['zz-deep-target']
    page.wait_for_function("window.wb.nodes.has('zz-deep-neighbor')")
    assert _graph_edge_ids(page) == ['zz-deep-edge']
    assert '局部邻域' in page.locator('#graph-summary').inner_text()
    with page.expect_response(lambda response: response.url.endswith('/mindmap')) as mindmap:
        page.locator('#graph-detail [data-action="mindmap"]').click()
    assert mindmap.value.request.post_data_json['root_id'] == 'zz-deep-target'
    assert page.locator('#mindmap-root').input_value() == 'zz-deep-target'
    assert not workbench.errors
