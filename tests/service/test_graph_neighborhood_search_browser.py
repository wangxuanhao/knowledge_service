"""Exercise neighborhood filters, real path highlighting, and the mobile list."""
import pytest

from test_workspace_redesign_ui import redesign_page


@pytest.fixture
def postgres_backend():
    """These routes serve isolated API fixtures."""


def test_neighborhood_semantic_lookup_only_selects_versioned_entities(redesign_page):
    page, errors = redesign_page
    requests = []
    scope = {'valid_at': '2026-11-01T00:00:00Z', 'include_unknown': False,
             'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}}
    page.evaluate('''scope => {
        WorkspaceContext.store.edit('p', 'search', scope);
        WorkspaceContext.store.apply('p', 'search');
    }''', scope)
    seed = {'id': 'seed', 'version_id': 'seed-v1', 'kind': 'entity', 'text': '商户整改'}

    def find(route):
        requests.append(route.request.post_data_json)
        route.fulfill(json={'active_mode': 'semantic', 'hits': [{**seed, 'score': .83427},
            {'id': 'relation', 'version_id': 'r1', 'kind': 'relation', 'text': '误入的关系'},
            {'id': 'unversioned', 'kind': 'entity', 'text': '无版本对象'}]})

    page.route('**/api/projects/p/search', find)
    page.route('**/graph-neighborhood/search', lambda route: route.fulfill(json={
        'records': [seed], 'matches': [{**seed, 'label': seed['text'], 'hops': 0, 'score': .83427,
                                      'path': {'start': 'seed', 'end': 'seed', 'steps': []}}],
        'total': 1, 'partial': False,
    }))
    page.click('#neighborhood-search-toggle')
    pane = page.locator('#graph-panel-neighborhood')
    mode = page.locator('#graph-workspace-header').get_by_label('起点查找方式')
    assert mode.is_visible()
    assert mode.input_value() == 'keyword'
    mode.select_option('semantic')
    page.locator('#query').fill('店铺违规后如何纠正')
    page.click('#search')
    pane.get_by_text('语义相似度 0.83427', exact=True).wait_for()
    assert pane.get_by_text('匹配实体向量', exact=True).is_visible()
    pane.get_by_role('button', name='商户整改').click()
    assert not pane.get_by_text('误入的关系').count()
    assert not pane.get_by_text('无版本对象').count()
    assert requests[0]['query'] == '店铺违规后如何纠正'
    assert requests[0]['retrieval_mode'] == 'semantic'
    assert requests[0]['kinds'] == ['entity']
    assert requests[0]['valid_at'] == scope['valid_at']
    assert requests[0]['include_unknown'] is False
    assert requests[0]['applicability']['platforms'] == scope['applicability']['platforms']
    assert 'entity_type' not in requests[0] and 'predicate' not in requests[0]
    with page.expect_request('**/graph-neighborhood/search') as traversal:
        pane.get_by_role('button', name='检索邻域', exact=True).click()
    body = traversal.value.post_data_json
    assert body['seeds'] == ['seed'] and body['valid_at'] == scope['valid_at']
    assert 'retrieval_mode' not in body
    pane.locator('.neighborhood-match').get_by_text('商户整改', exact=True).wait_for()
    assert not pane.get_by_text('语义相似度 0.83427', exact=True).count()

    page.evaluate('''() => {
        const scope = WorkspaceContext.store.read('p', 'search');
        WorkspaceContext.store.edit('p', 'search', {...scope, known_at: '2026-10-01T00:00:00Z'});
        WorkspaceContext.store.apply('p', 'search');
        document.dispatchEvent(new CustomEvent('graph-workspace:invalidated'));
    }''')
    assert mode.input_value() == 'keyword'
    assert mode.locator('option[value="semantic"]').evaluate('(option) => option.disabled')
    assert pane.get_by_text('历史时点使用关键词查找起点。').is_visible()
    assert not errors


def test_canvas_pick_sets_neighborhood_seed_without_opening_detail(redesign_page):
    page, errors = redesign_page
    page.click('#neighborhood-search-toggle')
    pane = page.locator('#graph-panel-neighborhood')
    page.evaluate("""() => FullGraphView.renderer.onSelect(
        {id:'picked', version_id:'picked-v1', kind:'entity', text:'图上选择的起点'})""")
    pane.get_by_text('起点 · 图上选择的起点').wait_for()
    assert pane.is_visible()
    assert pane.get_by_role('button', name='检索邻域', exact=True).is_enabled()
    assert page.locator('#query').input_value() == '图上选择的起点'
    assert not page.locator('#graph-panel-detail').is_visible()
    assert not errors


@pytest.mark.parametrize('width', [1440, 390])
def test_neighborhood_search_uses_full_neighborhood_and_reuses_graph(redesign_page, width):
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 860})
    records = [
        {'id': 'a', 'version_id': 'a1', 'kind': 'entity', 'type': 'task', 'text': '提交材料',
         'metadata': {'aliases': ['提交申请材料', '材料登记']}},
        {'id': 'b', 'version_id': 'b1', 'kind': 'entity', 'type': 'task', 'text': '审批材料'},
        {'id': 'ab', 'version_id': 'ab1', 'kind': 'relation', 'type': 'approves', 'type_label': '审批',
         'subject_id': 'a', 'object_id': 'b', 'text': '审批'},
    ]
    source = {'title': '申请处理指南', 'platforms': ['统一平台'], 'published_at': '2026-11-20',
              'document_version_id': 'd1', 'assertion_id': 's1', 'quote': '申请应提交材料'}
    if width >= 768:
        source.update(source_version_id='original-v1', chunk_version_id='chunk-v1')
    page.evaluate("""() => {
        window.neighborhoodEvidenceCalls = [];
        window.SourceReader = {open: args => window.neighborhoodEvidenceCalls.push(['source', args])};
        window.openFrozenSourceEvidence = args => window.neighborhoodEvidenceCalls.push(['frozen', args]);
    }""")
    path = {'start': 'a', 'end': 'b', 'steps': [
        {'from': 'a', 'to': 'b', 'relation_id': 'ab', 'relation': 'approves', 'direction': 'forward',
         'relation_version_id': 'ab1', 'from_version_id': 'a1', 'to_version_id': 'b1'},
    ]}
    match = {'id': 'b', 'version_id': 'b1', 'label': '审批材料', 'type': 'task', 'hops': 1,
             'path': path, 'source': source}
    next_records = [records[1],
                    {'id': 'c', 'version_id': 'c1', 'kind': 'entity', 'type': 'task', 'text': '通知结果'},
                    {'id': 'bc', 'version_id': 'bc1', 'kind': 'relation', 'type': 'approves', 'type_label': '审批',
                     'subject_id': 'b', 'object_id': 'c', 'text': '审批'}]
    next_matches = [
        {**match, 'hops': 0, 'path': {'start': 'b', 'end': 'b', 'steps': []}},
        {**match, 'id': 'c', 'version_id': 'c1', 'label': '通知结果',
         'path': {'start': 'b', 'end': 'c', 'steps': [
             {'from': 'b', 'to': 'c', 'relation_id': 'bc', 'relation': 'approves', 'direction': 'forward'}]}},
    ]
    requests = []
    first_filter = {'field': 'source_platform', 'value': '统一平台'}
    revised_filter = {'field': 'source_platform', 'value': '备用平台'}
    second_filter = {'field': 'entity_name', 'value': '材料'}

    def search(route):
        body = route.request.post_data_json
        requests.append(body)
        filters = body['filter_steps']
        if filters == [first_filter] and sum(req['filter_steps'] == filters for req in requests) == 1:
            route.fulfill(status=503, json={'detail': '图谱读取暂时不可用'})
        elif filters == [revised_filter]:
            route.fulfill(json={'records': records[:1], 'matches': [], 'total': 0, 'partial': False})
        elif filters == [first_filter, second_filter]:
            route.fulfill(json={'records': records[:1], 'matches': [], 'total': 0, 'partial': True})
        elif body['seeds'] == ['b']:
            route.fulfill(json={'records': next_records, 'matches': next_matches, 'total': 2, 'partial': False})
        else:
            route.fulfill(json={'records': records, 'matches': [{**match, 'evidence': filters}], 'total': 1, 'partial': False})

    page.route('**/records/page', lambda route: route.fulfill(json={'records': records[:1], 'next_cursor': None}))
    page.route('**/graph-neighborhood/search', search)
    page.evaluate("() => { document.getElementById('predicate-scope').add(new Option('审批', 'approves')); }")
    page.click('#neighborhood-search-toggle')
    panel = page.locator('#graph-panel-neighborhood')
    assert panel.is_visible()
    assert panel.locator('[data-ns=hops]').input_value() == '2'
    page.locator('#query').fill('提交')
    page.click('#search')
    panel.get_by_text('关键词匹配 1', exact=True).wait_for()
    assert panel.get_by_text('命中实体名称', exact=True).is_visible()
    assert panel.get_by_text('命中实体别名', exact=True).is_visible()
    assert panel.locator('.neighborhood-choices mark').all_text_contents() == ['提交', '提交']
    panel.get_by_role('button', name='提交材料').click()
    panel.locator('[data-ns=direction]').select_option('forward')
    panel.locator('[data-ns=types-summary]').click()
    panel.get_by_role('checkbox', name='审批', exact=True).check()
    panel.get_by_role('button', name='检索邻域', exact=True).click()
    row = panel.locator('.neighborhood-match')
    row.wait_for()
    assert not panel.get_by_text('关键词匹配 1', exact=True).count()
    assert '1 跳' in row.inner_text()
    assert '申请处理指南' in row.inner_text()
    assert requests[-1]['seeds'] == ['a'] and requests[-1]['hops'] == 2
    assert requests[-1]['relationship_types'] == ['approves'] and requests[-1]['direction'] == 'forward'
    row.click()
    panel.get_by_role('button', name='查看原文').click()
    evidence_calls = page.evaluate('window.neighborhoodEvidenceCalls')
    (viewer, arguments), = evidence_calls
    if width >= 768:
        assert viewer == 'source'
        assert arguments['projectId'] == 'p'
        assert arguments['versionId'] == 'original-v1'
        assert arguments['chunkVersionId'] == 'chunk-v1'
        assert arguments['focusText'] == '申请应提交材料'
    else:
        assert viewer == 'frozen'
        assert arguments['highlight'] == '申请应提交材料'
    assert panel.get_by_role('button', name='审批', exact=True).is_visible()

    if width < 768:
        assert not page.locator('#graph-canvas').is_visible()
        panel.get_by_role('button', name='查看图谱').click()
        assert not panel.is_visible()
        assert page.locator('#graph-canvas').is_visible()
    page.wait_for_function("() => wb.chart.getOption().series[0].links.find(row => row.id === 'ab')?.lineStyle?.color === '#167d79'")
    if width < 768:
        page.click('#neighborhood-search-toggle')

    def add_filter(field, value):
        panel.locator('[data-ns=filter-summary]').click()
        panel.locator('[data-ns=filter-field]').select_option(field)
        panel.locator('[data-ns=filter-value]').fill(value)
        panel.get_by_role('button', name='应用筛选').click()

    add_filter(first_filter['field'], first_filter['value'])
    panel.get_by_role('button', name='重试').wait_for()
    assert row.is_visible()
    assert panel.locator('[data-ns=filter-value]').input_value() == first_filter['value']
    panel.get_by_role('button', name='重试').click()
    edit = panel.get_by_role('button', name='编辑 来源平台 · 统一平台')
    edit.wait_for()
    if panel.locator('[data-ns=refine]').get_attribute('open') is not None:
        panel.locator('[data-ns=filter-summary]').click()

    edit.click()
    panel.locator('[data-ns=filter-value]').fill(revised_filter['value'])
    panel.get_by_role('button', name='应用筛选').click()
    panel.get_by_text('0 个匹配实体', exact=True).wait_for()
    panel.get_by_role('button', name='返回上一步').click()
    edit.wait_for()
    assert row.is_visible()

    add_filter(second_filter['field'], second_filter['value'])
    panel.get_by_text('部分结果 · 已找到 0 个实体', exact=True).wait_for()
    assert requests[-1]['filter_steps'] == [first_filter, second_filter]
    panel.get_by_role('button', name='移除 实体名称 · 材料').click()
    row.wait_for()
    assert requests[-1]['filter_steps'] == [first_filter]

    row.click()
    panel.get_by_role('button', name='从选中节点继续展开').click()
    panel.get_by_text('已探索路径 · 提交材料 → 审批材料', exact=True).wait_for()
    assert requests[-1]['seeds'] == ['b'] and requests[-1]['filter_steps'] == [first_filter]
    assert panel.locator('[data-ns=seed]').get_attribute('title') == 'b'
    assert panel.get_by_role('button', name='通知结果').is_visible()
    if width < 768:
        panel.get_by_role('button', name='查看图谱').click()
    page.wait_for_function("() => wb.chart.getOption().series[0].links.some(row => row.id === 'bc' && (row.lineStyle?.opacity ?? 1) > .12)")
    if width < 768:
        page.click('#neighborhood-search-toggle')
    panel.get_by_role('button', name='审批材料').click()
    assert not panel.get_by_role('button', name='从选中节点继续展开').is_visible()
    panel.get_by_role('button', name='返回上一步').click()
    row.wait_for()
    assert panel.locator('[data-ns=seed]').get_attribute('title') == 'a'
    row.click()
    panel.get_by_role('button', name='从选中节点继续展开').click()
    panel.get_by_role('button', name='通知结果').wait_for()
    panel.get_by_text('设置检索起点与范围').click()
    with page.expect_request('**/graph-neighborhood/search') as rerun:
        panel.get_by_role('button', name='检索邻域', exact=True).click()
    assert rerun.value.post_data_json['seeds'] == ['b']
    panel.get_by_role('button', name='返回上一步').wait_for(state='hidden')
    assert errors == []


def test_neighborhood_seed_from_inspector_keeps_historical_scope_and_exact_version(redesign_page):
    page, errors = redesign_page
    known_at = '2026-03-01T09:00:00Z'
    historical = {'id': 'seed', 'kind': 'entity', 'type': 'task', 'text': '历史申请', 'version': 1, 'version_id': 'seed-v1'}
    current = {**historical, 'text': '当前申请', 'version': 2, 'version_id': 'seed-v2'}
    requests = []

    def search(route):
        body = route.request.post_data_json
        requests.append(body)
        row = historical if len(requests) == 1 else current
        match = {'id': row['id'], 'label': row['text'], 'version_id': row['version_id'], 'type': row['type'],
                 'hops': 0, 'path': {'start': row['id'], 'end': row['id'], 'steps': []}}
        route.fulfill(json={'matches': [match], 'records': [row], 'total': 1, 'partial': False})

    page.route('**/graph-neighborhood/search', search)
    page.route('**/records/page', lambda route: route.fulfill(json={'records': [current], 'next_cursor': None}))
    page.evaluate('''({row,knownAt}) => inspectKnowledgeRecord(row, {
        scope: {...GraphWorkspaceContext.read().scope, known_at: knownAt},
    })''', {'row': historical, 'knownAt': known_at})
    page.get_by_role('button', name='以此实体检索邻域').click()
    panel = page.locator('#graph-panel-neighborhood')
    panel.get_by_role('button', name='检索邻域').click()
    panel.locator('.neighborhood-match').get_by_text('历史申请', exact=True).wait_for()
    assert requests[-1]['known_at'] == known_at
    assert page.evaluate("() => wb.nodes.get('seed').version_id") == historical['version_id']
    assert page.evaluate('() => readGraphMaintenanceScope().known_at') == known_at

    panel.get_by_text('设置检索起点与范围').click()
    panel.get_by_role('button', name='检索邻域').click()
    locate = panel.get_by_role('button', name='在当前范围重新定位')
    locate.wait_for()
    assert requests[-1]['known_at'] == known_at
    assert panel.locator('.neighborhood-match').get_by_text('历史申请', exact=True).is_visible()
    assert page.evaluate("() => wb.nodes.get('seed').version_id") == historical['version_id']

    locate.click()
    panel.locator('[data-ns=choices]').get_by_role('button', name='当前申请').click()
    panel.get_by_role('button', name='检索邻域').click()
    panel.locator('.neighborhood-match').get_by_text('当前申请', exact=True).wait_for()
    assert requests[-1].get('known_at') is None
    assert page.evaluate("() => wb.nodes.get('seed').version_id") == current['version_id']
    assert errors == []
