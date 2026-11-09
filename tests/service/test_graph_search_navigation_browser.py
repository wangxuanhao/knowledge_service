"""Explicit search intent against the real page, renderer and session loader."""
import pytest

from test_graph_overview_browser import overview_page, _loaded


def _search(page, rows):
    page.route('**/search', lambda route: route.fulfill(json={
        'active_mode': 'keyword', 'candidate_count': len(rows), 'hits': rows,
        'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '实体')
    page.click('#search')
    page.wait_for_selector('#hits .hit-entry')


def _hops(page, value):
    page.evaluate('''value => {const input=document.getElementById('graph-hops');
      input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));}''', value)


def _local(page, record_id):
    page.locator(f'#hits [data-graph-node="{record_id}"]').click()
    page.wait_for_function("() => FullGraphView.mode==='local' && !!wb.chart")


def _neighborhoods(calls):
    return [body for path, body in calls if path.endswith('/graph-neighborhood')]


@pytest.mark.parametrize('hops', [0, 2, 3, 10])
def test_search_local_action_uses_current_hops_even_with_cached_full_target(overview_page, hops):
    page, errors, calls, state = overview_page
    _loaded(page)
    _hops(page, hops)
    _search(page, [state['nodes'][0], state['edges'][0]])
    assert page.locator('#graph-hops').count() == 1
    assert page.locator('#hits [data-graph-node="n0"]').inner_text() == f'展开{hops}跳邻域'
    assert page.locator('#hits [data-graph-view="full"]').count() == 2
    assert page.locator('#hits [data-graph-view="full"]').first.inner_text() == '全局查看'
    before = len(calls)
    _local(page, 'n0')
    requests = _neighborhoods(calls[before:])
    assert len(requests) == 1
    assert requests[0]['seeds'] == ['n0'] and requests[0]['hops'] == hops
    assert requests[0]['max_nodes'] == 500 and requests[0]['max_edges'] == 1000
    assert not any('/graph-sessions' in path for path, _ in calls[before:])
    assert page.locator('#graph-hops').input_value() == str(hops)
    assert errors == []


def test_rendered_actions_follow_hop_edits_without_fetching_or_relabelling_applied_scene(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    _search(page, [state['nodes'][0], state['nodes'][2]])
    _hops(page, 2)
    _local(page, 'n0')
    before, summary = len(calls), page.locator('#graph-summary').inner_text()
    _hops(page, 3)
    assert len(calls) == before
    assert page.locator('#graph-summary').inner_text() == summary
    assert '2 跳' in summary or '2跳' in summary
    page.locator('[data-graph-panel="results"]').click()
    assert page.locator('#hits [data-graph-node="n2"]').inner_text() == '展开3跳邻域'
    _local(page, 'n2')
    assert _neighborhoods(calls[before:])[-1]['hops'] == 3
    assert page.evaluate("() => wb.nodes.has('n2') && !wb.nodes.has('n0')")
    assert errors == []


def test_hop_edit_rejects_late_local_response_and_keeps_previous_valid_scene(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('''() => {window.__beforeLocalNodes=wb.nodes;window.__realNavigationApi=api;
      api=(url,body,...rest)=>url.endsWith('/graph-neighborhood')?
        new Promise(resolve=>{window.__heldNeighborhood={body,resolve};}):__realNavigationApi(url,body,...rest);
      window.__pendingLocal=openKnowledgeRecord({id:'n0',kind:'entity',view:'local',hops:2});}''')
    page.wait_for_function('() => !!window.__heldNeighborhood')
    _hops(page, 3)
    page.evaluate('(records) => __heldNeighborhood.resolve({records,truncated:false})', [state['nodes'][0], state['nodes'][1], state['edges'][0]])
    page.evaluate('() => __pendingLocal')
    assert page.evaluate("() => wb.nodes===__beforeLocalNodes && wb.nodes.has('n2') && !wb.nodes.has('n0')")
    assert page.evaluate('() => __heldNeighborhood.body.hops') == 2
    assert errors == []


def test_loading_disables_duplicate_action_and_truncation_stays_explicit(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    _hops(page, 3)
    _search(page, [state['nodes'][0]])
    held = []
    page.route('**/graph-neighborhood', lambda route: held.append(route))
    page.locator('#hits [data-graph-node="n0"]').click()
    page.wait_for_function("() => document.querySelector('#graph-workspace-state').textContent.includes('3')")
    assert page.locator('#hits [data-graph-node="n0"]').is_disabled()
    assert len(held) == 1
    held[0].fulfill(json={'records': [state['nodes'][0]], 'truncated': True})
    page.wait_for_function("() => FullGraphView.mode==='local' && wb.nodes.has('n0')")
    summary = page.locator('#graph-summary').inner_text()
    assert '截断' in summary and '提高上限' in summary
    assert '没有关系' not in summary and '无关联关系' not in summary
    assert errors == []


@pytest.mark.parametrize('overview_page', [{'hold_initial_page': True}], indirect=True)
def test_late_default_page_cannot_overwrite_explicit_local(overview_page):
    page, errors, calls, state = overview_page
    _search(page, [state['nodes'][0]])
    _local(page, 'n0')
    before = state['manifests']
    state['hold_page'] = False
    for route in state['held_pages']:
        try:
            body = route.request.post_data_json
            route.fulfill(json={'session_id': route.request.url.split('/')[-2], 'kind': body['kind'],
                                'records': [], 'entities': [], 'next_cursor': None,
                                'watermark': 1, 'layout_version': 'stable-id-v1'})
        except Exception:
            pass  # The browser may have already cancelled the request.
    page.evaluate('() => GraphOverview.ensure()')
    assert page.evaluate("() => FullGraphView.mode==='local' && wb.nodes.has('n0')")
    assert state['manifests'] == before
    assert errors == []


@pytest.mark.parametrize('overview_page', [{'hold_initial_page': True}], indirect=True)
def test_hop_edit_does_not_cancel_an_unrelated_global_read(overview_page):
    page, errors, calls, state = overview_page
    page.wait_for_function("() => FullGraphView.loader?.state==='loading'")
    before = len(calls)
    _hops(page, 3)
    assert len(calls) == before
    assert page.evaluate("() => FullGraphView.loader.state==='loading'")
    state['hold_page'] = False
    for route in state['held_pages']:
        route.fulfill(json={'session_id': route.request.url.split('/')[-2], 'kind': 'connected',
                            'records': state['edges'], 'entities': state['nodes'][:2],
                            'next_cursor': None, 'watermark': 1, 'layout_version': 'stable-id-v1'})
    _loaded(page)
    assert page.evaluate("() => FullGraphView.mode==='full'")
    assert errors == []


def test_project_round_trip_discards_old_local_work(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    page.evaluate('''() => {window.__roundTripApi=api;
      api=(url,body,...rest)=>url.endsWith('/graph-neighborhood')?
        new Promise(resolve=>window.__roundTripResolve=resolve):__roundTripApi(url,body,...rest);
      window.__roundTripPending=openKnowledgeRecord({id:'n0',kind:'entity',view:'local',hops:2});}''')
    page.wait_for_function('() => !!window.__roundTripResolve')
    page.select_option('#project', 'b')
    page.wait_for_function("() => current==='b' && GraphWorkspaceContext.ready")
    page.select_option('#project', 'p')
    page.wait_for_function("() => current==='p' && GraphWorkspaceContext.ready")
    page.evaluate('(records) => __roundTripResolve({records,truncated:false})', [state['nodes'][0]])
    page.evaluate('() => __roundTripPending')
    assert page.locator('#graph-detail').get_attribute('data-record-id') != 'n0'
    assert errors == []


def test_relation_local_action_uses_both_endpoints_exact_revision_and_configured_budgets(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    _hops(page, 3)
    _search(page, [state['edges'][0]])
    before = len(calls)
    _local(page, 'edge-exact')
    read = _neighborhoods(calls[before:])[-1]
    assert set(read['seeds']) == {'n0', 'n1'} and read['hops'] == 3
    assert read['max_nodes'] == 500 and read['max_edges'] == 1000
    assert page.evaluate("() => wb.nodes.has('n0') && wb.nodes.has('n1') && wb.chart.getModel().getSeriesByIndex(0).option.links.some(e=>e.id==='edge-exact')")
    assert page.locator('#graph-detail').get_attribute('data-record-id') == 'edge-exact'
    # A relation selection must not expand an earlier hidden entity selection.
    before = len(calls)
    page.click('#graph-expand')
    assert not _neighborhoods(calls[before:])
    assert errors == []


def test_explicit_global_reuses_cached_view_and_local_return_preserves_camera(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    _search(page, [state['nodes'][0]])
    _local(page, 'n0')
    page.evaluate("() => {window.__localBefore=wb.nodes;window.__localOption=wb.chart.getOption();}")
    page.locator('[data-graph-panel="results"]').click()
    before = len(calls)
    page.locator('#hits [data-graph-view="full"]').click()
    page.wait_for_function("() => FullGraphView.mode==='full' && FullGraphView.renderer.displayDataset.records[FullGraphView.renderer.selected]?.id==='n0'")
    assert not any('/graph-sessions' in path for path, _ in calls[before:])
    assert page.evaluate("() => FullGraphView.renderer.displayDataset.records[FullGraphView.renderer.selected].id==='n0'")
    page.locator('[data-fg-mode="local"]').click()
    assert page.evaluate("() => wb.nodes===__localBefore && wb.chart.getOption().series[0].zoom===__localOption.series[0].zoom")
    assert errors == []


def test_uncached_global_is_bounded_same_scope_with_honest_unloaded_guidance(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    state['nodes'].extend(dict(id=f'n{i}', kind='entity', type='Thing', text=f'实体{i}', version=1, version_id=f'v{i}') for i in range(4, 1000))
    state.update(entity_count=1000, relation_count=800)
    fixed = {'known_at': '2025-01-01T00:00:00Z', 'ontology_scope': 'ids', 'ontology_ids': ['historical'],
             'applicability': {'platforms': {'mode': 'specific', 'values': ['历史平台']}}}
    before = len(calls)
    page.evaluate('(scope) => openKnowledgeRecord({id:"n999",kind:"entity",view:"full",scope})', fixed)
    page.wait_for_function("() => FullGraphView.mode==='full' && FullGraphView.loader?.state==='paused'")
    manifests = [body for path, body in calls[before:] if path.endswith('/graph-sessions')]
    pages = [body for path, body in calls[before:] if '/graph-sessions/' in path and path.endswith('/page')]
    assert len(manifests) == 1 and len(pages) == 1 and pages[0]['limit'] == 200
    assert all(manifests[0].get(key) == value for key, value in fixed.items())
    assert not _neighborhoods(calls[before:])
    assert '目标尚未载入当前概览' in page.locator('.full-graph-progress').inner_text()
    assert page.locator('[data-fg-target-local]').is_visible()
    assert not page.evaluate("() => FullGraphView.renderer.dataset.nodeIndex.has('n999')")
    assert page.evaluate("() => !WorkspaceContext.read(current,'search').known_at")
    page.locator('[data-fg-target-local]').click()
    page.wait_for_function("() => FullGraphView.mode==='local' && wb.nodes.has('n999')")
    assert _neighborhoods(calls[before:])[-1]['known_at'] == fixed['known_at']
    assert page.locator('#graph-detail [data-action="edit"]').is_disabled()
    assert errors == []


@pytest.mark.parametrize('failure', ['version', 'network', 'endpoint', 'type'])
def test_rejected_local_navigation_keeps_existing_scene(overview_page, failure):
    page, errors, _, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('() => {window.__previousNodes=wb.nodes;window.__previousMode=FullGraphView.mode;}')
    record = dict(id='n0', kind='entity', view='local', hops=2)
    if failure == 'version':
        record['version_id'] = 'obsolete'
    elif failure == 'network':
        page.route('**/graph-neighborhood', lambda route: route.fulfill(status=503, json={'detail': '邻域读取失败'}))
    elif failure == 'endpoint':
        record.update(id='edge-exact', kind='relation')
        page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': [state['nodes'][0]], 'truncated': False}))
    else:
        record['scope'] = {'entity_type': 'Excluded'}
    result = page.evaluate('record => openKnowledgeRecord(record).then(()=>null,error=>error.message)', record)
    assert result
    assert page.evaluate('() => wb.nodes===__previousNodes && FullGraphView.mode===__previousMode')
    assert errors == []


def test_uncached_global_failure_retains_valid_local_scene(overview_page):
    page, errors, _, _ = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('() => {window.__localBeforeFailure=wb.nodes;}')
    page.route('**/graph-sessions', lambda route: route.fulfill(status=503, json={'detail': '清单读取失败'}))
    page.evaluate("() => openKnowledgeRecord({id:'n0',kind:'entity',view:'full',scope:{known_at:'2025-01-01T00:00:00Z'}}).catch(()=>null)")
    assert page.evaluate("() => FullGraphView.mode==='local' && wb.nodes===__localBeforeFailure")
    assert errors == []


def test_exact_relation_reserves_its_place_within_the_configured_edge_budget(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    page.evaluate("() => {const s=document.getElementById('graph-max-edges');s.add(new Option('1','1'));s.value='1';}")
    observed = []
    other = {**state['edges'][0], 'id': 'parallel-earlier', 'version_id': 'parallel-v1'}

    def neighborhood(route):
        observed.append(route.request.post_data_json)
        route.fulfill(json={'records': state['nodes'][:2] + [other], 'truncated': False,
                            'attribute_budget': 200, 'attribute_count': 0})

    page.route('**/graph-neighborhood', neighborhood)
    page.evaluate("() => openKnowledgeRecord({id:'edge-exact',kind:'relation',view:'local',hops:3,version_id:'edge-v2'})")
    assert observed[0]['hops'] == 3 and observed[0]['max_edges'] == 1
    assert set(observed[0]['seeds']) == {'n0', 'n1'}
    assert page.evaluate("() => wb.chart.getModel().getSeriesByIndex(0).option.links.map(edge=>edge.id)") == ['edge-exact']
    assert '截断' in page.locator('#graph-summary').inner_text()
    assert '属性另计 0 / 200' in page.locator('#graph-summary').inner_text()
    assert page.locator('#graph-detail').get_attribute('data-record-id') == 'edge-exact'
    assert errors == []


@pytest.mark.parametrize('kind,record_id', [('entity', 'n0'), ('relation', 'edge-exact')])
def test_observed_global_target_revision_change_rejects_before_scene_commit(overview_page, kind, record_id):
    page, errors, _, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('''() => {const r=FullGraphView.renderer;window.__beforeRevisionMismatch={
      nodes:wb.nodes,dataset:r.dataset,camera:[r.camera.x,r.camera.y,r.camera.scale]};}''')
    state['hold_page'] = True
    with page.expect_request(lambda request: '/graph-sessions/' in request.url and request.url.endswith('/page')):
        page.evaluate('''record => {window.__revisionMismatchPending=openKnowledgeRecord({...record,view:'full',
          scope:{known_at:'2025-01-01T00:00:00Z'}}).then(()=>null,error=>error.message);}''', {'id': record_id, 'kind': kind})
    # request is emitted before the route callback necessarily enters the held
    # list. Wait for that actual interception before releasing a changed page.
    for _ in range(500):
        if state['held_pages']:
            break
        page.wait_for_timeout(10)
    assert len(state['held_pages']) == 1, 'the mismatched session page must actually be intercepted'
    nodes, edges = [dict(row) for row in state['nodes'][:2]], [dict(state['edges'][0])]
    changed = nodes[0] if kind == 'entity' else edges[0]
    changed.update(version=changed['version'] + 1, version_id='changed-in-frozen-session')
    state['hold_page'] = False
    for route in state['held_pages']:
        route.fulfill(json={'session_id': route.request.url.split('/')[-2], 'kind': 'connected',
                            'records': edges, 'entities': nodes, 'next_cursor': None,
                            'watermark': 1, 'layout_version': 'stable-id-v1'})
    message = page.evaluate('() => __revisionMismatchPending')
    assert message and '版本' in message
    assert page.evaluate('''() => {const old=__beforeRevisionMismatch,r=FullGraphView.renderer;return (
      FullGraphView.mode==='local'&&wb.nodes===old.nodes&&r.dataset===old.dataset&&
      JSON.stringify([r.camera.x,r.camera.y,r.camera.scale])===JSON.stringify(old.camera));}''')
    assert not page.locator('[data-fg-target-local]').is_visible()
    assert errors == []


def test_small_global_checks_later_bounded_page_target_version_before_commit(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n3',kind:'entity',view:'local',hops:1})")
    page.evaluate('''() => {window.__beforeLaterMismatch={nodes:wb.nodes,dataset:FullGraphView.renderer.dataset};}''')
    state['record_details']['n2'] = {'version': 1, 'version_id': 'v2'}
    state['nodes'][2] = {**state['nodes'][2], 'version': 2, 'version_id': 'later-page-version'}
    before = len(calls)
    message = page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'full',scope:{known_at:'2025-01-01T00:00:00Z'}}).then(()=>null,error=>error.message)")
    assert message and '版本' in message
    assert page.evaluate("() => FullGraphView.mode==='local' && wb.nodes===__beforeLaterMismatch.nodes && FullGraphView.renderer.dataset===__beforeLaterMismatch.dataset")
    pages = [body for path, body in calls[before:] if '/graph-sessions/' in path and path.endswith('/page')]
    assert len(pages) == 2 and all(body['limit'] == 200 for body in pages)
    assert errors == []


def test_failed_staged_full_attempt_restores_paused_session_progress_and_type_counts(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state.update(nodes=[dict(id=f'n{i}', kind='entity', type='Thing' if i < 200 else 'LaterType',
                             text=f'实体{i}', version=1, version_id=f'v{i}') for i in range(401)],
                 edges=[], entity_count=401, relation_count=0)
    page.evaluate('() => FullGraphView.restart()')
    page.wait_for_function("() => FullGraphView.loader?.state==='paused' && FullGraphView.renderer.dataset.nodeCount===200")
    page.evaluate('''() => {window.__pausedBeforeFailure={loader:FullGraphView.loader,dataset:FullGraphView.renderer.dataset};}''')
    page.route('**/graph-sessions', lambda route: route.fulfill(status=503, json={'detail': '清单读取失败'}))
    message = page.evaluate("() => openKnowledgeRecord({id:'n0',kind:'entity',view:'full',scope:{known_at:'2025-01-01T00:00:00Z'}}).then(()=>null,error=>error.message)")
    assert message and '失败' in message
    assert page.evaluate("() => FullGraphView.loader===__pausedBeforeFailure.loader && FullGraphView.renderer.dataset===__pausedBeforeFailure.dataset")
    page.locator('[data-fg="next"]').click()
    page.wait_for_function("() => FullGraphView.loader.state==='paused' && FullGraphView.renderer.dataset.nodeCount===400")
    assert '400 / 401' in page.locator('.full-graph-progress').inner_text()
    assert page.locator('[data-fg-type="LaterType"]').inner_text().endswith('200')
    page.locator('[data-fg="auto"]').click()
    _loaded(page)
    assert '401 / 401' in page.locator('.full-graph-progress').inner_text()
    assert page.locator('[data-fg-type="LaterType"]').inner_text().endswith('201')
    assert page.locator('[data-fg="next"]').is_disabled()
    assert errors == []


@pytest.mark.parametrize('width,height', [(1440, 900), (390, 844)])
def test_local_controls_and_single_inspector_stay_reachable(overview_page, width, height):
    page, errors, _, state = overview_page
    _loaded(page)
    page.set_viewport_size({'width': width, 'height': height})
    _search(page, [state['nodes'][0]])
    _local(page, 'n0')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.locator('#graph-hops').count() == 1
    assert page.locator('#graph-hops').is_visible() and page.locator('#graph-expand').is_visible()
    assert page.locator('.search-graph-workspace .inspector-rail').count() == 1
    page.wait_for_function("() => document.querySelector('.graph-discovery-notice summary')?.textContent.includes('7')")
    bounds = page.locator('#graph-canvas').bounding_box()
    assert bounds['height'] >= 180 and bounds['width'] >= 150
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth+1')
    notice = page.locator('.graph-discovery-notice')
    assert '7' in notice.locator('summary').inner_text()
    notice.locator('summary').click()
    assert '7 条候选' in notice.locator('#graph-canvas-discovery-hint').inner_text()
    assert notice.locator('[data-goto-discovery]').is_visible()
    assert abs(page.locator('#graph-canvas').bounding_box()['height'] - bounds['height']) <= 1
    notice.locator('summary').click()
    assert abs(page.locator('#graph-canvas').bounding_box()['height'] - bounds['height']) <= 1
    notice.locator('summary').click()
    notice.locator('[data-goto-discovery]').click()
    assert page.locator('#tab-ontology-model').is_visible()
    assert errors == []


@pytest.mark.parametrize('failure', ['network', 'version'])
def test_staged_global_entity_enrichment_failure_keeps_prior_scene(overview_page, failure):
    page, errors, _, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('''() => {const r=FullGraphView.renderer;window.__beforeEnrichmentFailure={
      nodes:wb.nodes,dataset:r.dataset,camera:[r.camera.x,r.camera.y,r.camera.scale]};}''')
    enrichments = []

    def enrich(route):
        body = route.request.post_data_json
        if body.get('seeds') != ['n0'] or body.get('hops') != 0:
            route.fallback()
            return
        enrichments.append(body)
        if failure == 'network':
            route.fulfill(status=503, json={'detail': '详情读取失败'})
        else:
            route.fulfill(json={'records': [{**state['nodes'][0], 'version': 2,
                                             'version_id': 'changed-during-enrichment'}], 'truncated': False})

    page.route('**/graph-neighborhood', enrich)
    message = page.evaluate("() => openKnowledgeRecord({id:'n0',kind:'entity',view:'full',scope:{known_at:'2025-01-01T00:00:00Z'}}).then(()=>null,error=>error.message)")
    assert len(enrichments) == 1
    assert message and ('失败' in message if failure == 'network' else '版本' in message)
    assert page.evaluate('''() => {const old=__beforeEnrichmentFailure,r=FullGraphView.renderer;return (
      FullGraphView.mode==='local'&&wb.nodes===old.nodes&&r.dataset===old.dataset&&
      JSON.stringify([r.camera.x,r.camera.y,r.camera.scale])===JSON.stringify(old.camera));}''')
    assert errors == []


@pytest.mark.parametrize('kind,record_id', [('entity', 'n0'), ('relation', 'edge-exact')])
def test_complete_staged_global_requires_qualified_target_before_commit(overview_page, kind, record_id):
    page, errors, calls, state = overview_page
    _loaded(page)
    page.evaluate("() => openKnowledgeRecord({id:'n2',kind:'entity',view:'local',hops:1})")
    page.evaluate('''() => {const r=FullGraphView.renderer;window.__beforeCompleteAbsence={
      nodes:wb.nodes,dataset:r.dataset,camera:[r.camera.x,r.camera.y,r.camera.scale]};}''')
    # Exact record qualification still succeeds. The new frozen session is a
    # valid complete dataset in which that entity/relation has been withdrawn.
    records = [] if kind == 'entity' else state['nodes'][:2]
    page.route('**/graph-sessions', lambda route: route.fulfill(json={
        'id': 'complete-withdrawn-target', 'entity_count': len(records), 'relation_count': 0,
        'watermark': 1, 'layout_version': 'stable-id-v1'}))

    def completed_page(route):
        route.fulfill(json={'session_id': 'complete-withdrawn-target',
                            'kind': route.request.post_data_json['kind'], 'records': records,
                            'entities': [], 'next_cursor': None, 'watermark': 1,
                            'layout_version': 'stable-id-v1'})

    page.route('**/graph-sessions/*/page', completed_page)
    message = page.evaluate('''record => openKnowledgeRecord({...record,view:'full',
      scope:{known_at:'2025-01-01T00:00:00Z'}}).then(()=>null,error=>error.message)''', {'id': record_id, 'kind': kind})
    assert message and ('资格' in message or '范围' in message or '记录' in message)
    assert page.evaluate('''() => {const old=__beforeCompleteAbsence,r=FullGraphView.renderer;return (
      FullGraphView.mode==='local'&&wb.nodes===old.nodes&&r.dataset===old.dataset&&
      JSON.stringify([r.camera.x,r.camera.y,r.camera.scale])===JSON.stringify(old.camera));}''')
    assert not page.locator('[data-fg-target-local]').is_visible()
    assert errors == []
