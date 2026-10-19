"""Real-browser regression for unified metadata scope and graph path placement."""

from pathlib import Path
import json

from test_frontend_retrieval_flow import (
    _graph_edge_ids,
    _graph_node_ids,
    browser,
    workbench,
)


def _open_scope(page):
    page.locator('#cb-details').evaluate('(element) => {element.open = true;}')


def _capture(page, name):
    destination = Path(__file__).resolve().parents[3] / '.tmp_doc_review' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(destination), full_page=True)


def test_scope_contains_all_business_and_graph_constraints(workbench):
    page = workbench.page
    page.set_viewport_size({'width': 1280, 'height': 920})
    assert 'Metadata 限制' in page.locator('#cb-details > summary').inner_text()
    for control in ('query-platforms', 'query-businesses', 'query-audiences',
                    'type-scope', 'predicate-scope'):
        assert page.locator(f'#scope #cb-details #{control}').count() == 1
    assert page.locator('#scope > .query-platform-scope').count() == 0
    assert not page.locator('#cb-details').evaluate('(element) => element.open')
    _open_scope(page)
    for control in ('query-platforms', 'query-businesses', 'query-audiences'):
        assert page.locator('#' + control).is_visible()
    _capture(page, 'unified-scope-1280.png')
    assert not workbench.errors


def test_three_dimensional_scope_reaches_graph_and_excludes_other_business(workbench):
    page = workbench.page
    scope = {'platforms': ['美团'], 'businesses': ['外卖'], 'audiences': ['商家']}
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
    assert page.evaluate('scope().applicability') == scope
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
        page.click('#apply-scope')
    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    assert bodies[-1]['applicability'] == scope
    assert _graph_node_ids(page) == ['match-a', 'match-b']
    assert _graph_edge_ids(page) == ['match-r']
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
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
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
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
    assert page.evaluate('scope().filters') == filters
    with page.expect_response(lambda response: response.url.endswith('/metadata/facets')):
        page.click('#apply-scope')
    assert '高级 JSON' in page.locator('#cb-details > summary').inner_text()
    with page.expect_response(lambda response: response.url.endswith('/entity-options')):
        page.click('#reset-scope')
    assert page.locator('#filters').input_value() == ''
    assert page.locator('#cb-raw-json').input_value() == ''
    assert '未设置' in page.locator('#cb-details > summary').inner_text()
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
        screenshot = Path(__file__).resolve().parents[3] / '.tmp_doc_review' / 'unified-path-760-failure.png'
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert tools.is_visible(), diagnostics
    assert page.locator('#graph-path-from').is_visible()
    assert page.locator('#graph-path-to').is_visible()
    assert page.locator('.inspector-rail').evaluate(
        '(element) => getComputedStyle(element).display') != 'none'
    _capture(page, 'unified-path-760.png')
    assert not workbench.errors
