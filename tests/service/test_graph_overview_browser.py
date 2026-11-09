"""Real page, real SessionLoader, faithful PostgreSQL graph-session transport."""
import threading
from contextlib import ExitStack
from http.server import ThreadingHTTPServer

import pytest

from test_workspace_redesign_ui import EDGE, Handler


@pytest.fixture
def overview_page(request):
    if not EDGE.exists():
        pytest.skip('system Edge is required')
    from playwright.sync_api import sync_playwright
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with ExitStack() as cleanup:
        cleanup.callback(server.server_close)
        cleanup.callback(server.shutdown)
        runtime = cleanup.enter_context(sync_playwright())
        browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE), args=[
            '--disable-gpu', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        cleanup.callback(browser.close)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors, calls = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        settings = getattr(request, 'param', {})
        node_types = settings.get('node_types', ['Thing'] * 4)
        nodes = [dict(id=f'n{i}', kind='entity', type=node_types[i], text=f'实体{i}', version=1, version_id=f'v{i}') for i in range(4)]
        edge = dict(id='edge-exact', kind='relation', type='knows', text='认识', subject_id='n0', object_id='n1', version=2, version_id='edge-v2')
        state = dict(nodes=nodes, edges=[edge], entity_count=4, relation_count=1, fail_page=False, manifests=0,
                     detail_records={}, record_details={}, hold_page=settings.get('hold_initial_page', False), held_pages=[])

        def compact(row):
            item = {key: row.get(key) for key in ['id', 'version', 'version_id', 'kind', 'type', 'subject_id', 'object_id']}
            item['text'] = str(row.get('text') or '')[:160]
            return item

        def respond(route):
            path = route.request.url.split('/api/', 1)[1].split('?', 1)[0]
            body = route.request.post_data_json if route.request.method == 'POST' else {}
            calls.append((path, body))
            value = {}
            if path == 'auth/policy':
                value = {'auth_enabled': False}
            elif path == 'ingest/capabilities':
                value = dict(concurrency_choices=['auto', 1, 2], max_file_bytes=10000000,
                             max_batch_bytes=20000000, max_batch_items=20, supported_extensions=['.txt', '.md', '.pdf', '.docx', '.zip'])
            elif path == 'projects':
                value = {'projects': [dict(id=p, name=p, counts={'documents': 0, 'entities': 4, 'relations': 1, 'chunks': 0}, metadata={'ontology_mode': 'discovery'}) for p in ['p', 'b']]}
            elif path.endswith('/graph-sessions'):
                state['manifests'] += 1
                project = path.split('/')[1]
                value = dict(id=f'session-{project}-{state["manifests"]}', entity_count=state['entity_count'], relation_count=state['relation_count'], watermark=1, layout_version='stable-id-v1')
            elif '/graph-sessions/' in path and path.endswith('/page'):
                if state['hold_page']:
                    state['held_pages'].append(route)
                    return
                if state['fail_page']:
                    route.fulfill(status=503, json={'detail': 'PostgreSQL graph page unavailable'})
                    return
                kind, offset, limit = body['kind'], int(body.get('cursor') or 0), body['limit']
                rows = state['edges'] if kind == 'connected' else state['nodes']
                records = [compact(row) for row in rows[offset:offset + limit]]
                endpoint_ids = {row.get(key) for row in records for key in ['subject_id', 'object_id']}
                value = dict(session_id=path.split('/')[-2], kind=kind, records=records,
                             entities=[compact(row) for row in state['nodes'] if row['id'] in endpoint_ids],
                             next_cursor=str(offset + limit) if offset + limit < len(rows) else None,
                             watermark=1, layout_version='stable-id-v1')
            elif path.endswith('/records/page'):
                ids = body.get('record_ids', [])
                records = [row for row in state['nodes'] + state['edges'] if row['id'] in ids] if ids else [row for row in state['nodes'] + state['edges'] if row['kind'] in body.get('kinds', ['entity', 'relation'])]
                records = [{**row, **state['record_details'].get(row['id'], {})} for row in records]
                value = dict(records=records, total=len(records), next_cursor=None)
            elif path.endswith('/graph-neighborhood'):
                seeds = set(body.get('seeds', []))
                records = [row for row in state['nodes'] if row['id'] in seeds]
                if body.get('hops', 0):
                    edges = [row for row in state['edges'] if row['subject_id'] in seeds or row['object_id'] in seeds]
                    endpoints = {row[key] for row in edges for key in ['subject_id', 'object_id']}
                    records = [row for row in state['nodes'] if row['id'] in seeds | endpoints] + edges
                records = [{**row, **state['detail_records'].get(row['id'], {})} for row in records]
                value = dict(records=records, truncated=False)
            elif path.endswith('/ontology'):
                route.fulfill(status=404, json={'detail': '尚未发布本体'})
                return
            elif path.endswith('/ontologies'):
                value = {'versions': []}
            elif path.endswith('/metadata/facets'):
                value = {'fields': [], 'records': 4}
            elif path.endswith('/ontology-discovery'):
                value = {'unpublished_candidate_count': 7}
            elif path.endswith('/applicability-values'):
                value = dict(platforms=[], businesses=[], audiences=[])
            elif path.endswith('/timeline'):
                value = dict(events=[], truncated=False)
            elif path.endswith('/evidence'):
                value = dict(documents=[], message='没有来源')
            elif path.endswith('/index/status'):
                value = dict(ready=False, status='not_built')
            for suffix, key in [('sources', 'documents'), ('jobs', 'jobs'), ('snapshots', 'snapshots'), ('ingest-batches', 'batches'), ('ontology-drafts', 'drafts')]:
                if path.endswith('/' + suffix):
                    value = {key: []}
            route.fulfill(json=value)

        page.route('**/api/**', respond)
        page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_function("() => typeof current === 'string' && current === 'p'")
        yield page, errors, calls, state


def _loaded(page):
    page.wait_for_function("() => FullGraphView.loader?.state === 'complete'")


def _restart(page):
    page.evaluate('() => FullGraphView.restart()')


def test_default_entry_reads_real_bounded_overview_without_an_index(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert page.evaluate('() => [FullGraphView.renderer.dataset.nodeCount, FullGraphView.renderer.dataset.edgeCount]') == [4, 1]
    pages = [body for path, body in calls if '/graph-sessions/' in path and path.endswith('/page')]
    assert 1 <= len(pages) <= 3
    assert all(body['limit'] == 200 for body in pages)
    assert not any('vector' in path or 'milvus' in path for path, _ in calls)
    assert '4' in page.locator('.full-graph-progress').inner_text()
    assert errors == []


def test_reentry_preserves_camera_and_loaded_dataset(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    before = len([1 for path, _ in calls if path.endswith('/graph-sessions')])
    page.evaluate("() => {FullGraphView.renderer.camera.x=123;KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert len([1 for path, _ in calls if path.endswith('/graph-sessions')]) == before
    assert page.evaluate('() => FullGraphView.renderer.camera.x') == 123
    assert errors == []


def test_explicit_loaded_relation_focuses_exact_edge_and_both_endpoints_without_reload(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    before = len([1 for path, _ in calls if path.endswith('/graph-sessions')])
    page.evaluate("() => openKnowledgeRecord({id:'edge-exact',kind:'relation'})")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.evaluate('''() => {const r=FullGraphView.renderer,d=r.displayDataset;return {
      edge:d.edges[r.selectedEdge]?.id,endpoints:['n0','n1'].map(id=>d.nodeIndex.has(id))};}''') == {'edge': 'edge-exact', 'endpoints': [True, True]}
    assert len([1 for path, _ in calls if path.endswith('/graph-sessions')]) == before
    assert page.locator('#graph-detail').inner_text().find('认识') >= 0
    assert errors == []


def test_unloaded_record_navigation_draws_qualified_scope_and_focuses_it(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    state['nodes'].append(dict(id='unloaded', kind='entity', type='Thing', text='未加载实体', version=3, version_id='unloaded-v3'))
    frozen = dict(known_at='2025-06-01T00:00:00Z', ontology_scope='ids', ontology_ids=['historical'],
                  applicability={'platforms': {'mode': 'specific', 'values': ['限定平台']}})
    page.evaluate('(scope) => openKnowledgeRecord({id:"unloaded",kind:"entity",scope})', frozen)
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.evaluate("() => wb.nodes.has('unloaded') || FullGraphView.renderer.displayDataset.nodeIndex.has('unloaded')")
    exact = [body for path, body in calls if path.endswith('/records/page') and body.get('record_ids') == ['unloaded']]
    canvas = [body for path, body in calls if path.endswith('/graph-neighborhood') and 'unloaded' in body.get('seeds', [])]
    assert len(exact) == 1
    assert canvas
    assert all(body.get('known_at') == frozen['known_at'] and body.get('ontology_ids') == ['historical'] for body in exact + canvas)
    assert all(body.get('applicability') == frozen['applicability'] for body in exact + canvas)
    assert page.locator('#graph-detail [data-action="edit"]').is_disabled()
    assert page.locator('#graph-detail .evidence-danger button').is_disabled()
    evidence = [body for path, body in calls if path.endswith('/records/unloaded/evidence')]
    assert evidence and evidence[-1]['known_at'] == frozen['known_at']
    assert evidence[-1]['ontology_ids'] == ['historical']
    assert evidence[-1]['applicability'] == frozen['applicability']
    page.evaluate("() => expandGraphBranch('unloaded', 1)")
    continuation = [body for path, body in calls if path.endswith('/graph-neighborhood') and body.get('hops') == 1 and 'unloaded' in body.get('seeds', [])]
    assert continuation and continuation[-1]['known_at'] == frozen['known_at']
    assert continuation[-1]['ontology_ids'] == ['historical']
    assert errors == []


def test_large_graph_stops_after_first_page_and_manual_cancel_survives_reentry(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    large_nodes = [dict(id=f'n{i}', kind='entity', type='Thing', text=f'实体{i}', version=1, version_id=f'v{i}') for i in range(1000)]
    large_edges = [dict(id=f'large-edge-{i}', kind='relation', type='knows', subject_id=f'n{i}', object_id=f'n{i + 1}', version=1, version_id=f'large-edge-v{i}') for i in range(800)]
    state.update(nodes=large_nodes, edges=large_edges, entity_count=1000, relation_count=800)
    before = len(calls)
    _restart(page)
    page.wait_for_function("() => FullGraphView.loader?.state === 'paused'")
    pages = [body for path, body in calls[before:] if '/graph-sessions/' in path and path.endswith('/page')]
    assert len(pages) == 1 and pages[0]['limit'] == 200
    page.evaluate("() => {FullGraphView.cancel();KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.evaluate('() => FullGraphView.loader.state') == 'cancelled'
    assert '不完整' in page.locator('.full-graph-progress').inner_text()
    assert errors == []


def test_page_failure_is_not_empty_knowledge_and_can_retry(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state['fail_page'] = True
    _restart(page)
    assert page.evaluate('() => FullGraphView.loader.state') == 'error'
    text = page.locator('.full-graph-progress').inner_text()
    assert '不完整' in text and 'PostgreSQL' in text
    assert '没有实体' not in text
    state['fail_page'] = False
    _restart(page)
    _loaded(page)
    assert errors == []


def test_ledger_relation_navigation_keeps_exact_edge_and_ledger_scope(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    page.evaluate("() => KnowledgeTabs.open('records')")
    page.click('[data-ledger-view="relation"]')
    page.wait_for_selector('#records tr[data-record-id="edge-exact"]')
    page.click('#scope-toggle')
    page.fill('#query-platforms', '台账平台')
    page.click('#apply-scope')
    page.wait_for_selector('#records tr[data-record-id="edge-exact"]')
    before = len(calls)
    page.click('#records tr[data-record-id="edge-exact"] [data-locate-graph]')
    page.wait_for_function("() => document.querySelector('#graph-detail').dataset.recordId === 'edge-exact'")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert page.evaluate("() => wb.nodes.has('n0') && wb.nodes.has('n1') && wb.chart.getModel().getSeriesByIndex(0).option.links.some(edge=>edge.id==='edge-exact')")
    exact = [body for path, body in calls[before:] if path.endswith('/records/page') and body.get('record_ids') == ['edge-exact']]
    assert len(exact) == 1
    assert exact[0]['applicability']['platforms']['values'] == ['台账平台']
    neighborhood = [body for path, body in calls[before:] if path.endswith('/graph-neighborhood')]
    assert neighborhood and set(neighborhood[-1]['seeds']) == {'n0', 'n1'}
    assert neighborhood[-1]['applicability']['platforms']['values'] == ['台账平台']
    assert errors == []


def test_canvas_click_inspects_without_record_navigation_or_graph_rebuild(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    before = len(calls)
    point = page.evaluate('''() => {const r=FullGraphView.renderer,d=r.displayDataset,i=d.nodeIndex.get('n0'),p=r.renderPositions||d.positions;return r.camera.worldToScreen(p[i*2],p[i*2+1],r.width,r.height);}''')
    canvas = page.locator('.full-graph-host canvas').first.bounding_box()
    page.mouse.click(canvas['x'] + point[0], canvas['y'] + point[1])
    page.wait_for_function("() => document.querySelector('#graph-detail').dataset.recordId === 'n0'")
    assert not any('/graph-sessions' in path for path, _ in calls[before:])
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert errors == []


def test_local_frozen_navigation_replaces_prior_complete_overview_status(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    assert '完整加载' in page.locator('#graph-workspace-state').inner_text()
    state['nodes'].append(dict(id='local-status', kind='entity', type='Thing', text='局部历史实体', version=1, version_id='local-status-v1'))
    page.evaluate("() => openKnowledgeRecord({id:'local-status',kind:'entity',scope:{known_at:'2025-01-01T00:00:00Z'}})")
    text = page.locator('#graph-workspace-state').inner_text()
    assert '局部' in text and '2025' in text
    assert '完整加载' not in text
    assert errors == []


def test_compact_full_canvas_node_inspection_enriches_details_without_moving_camera(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    state['detail_records']['n0'] = dict(class_label='详细条款', class_ontology_id='bound-release',
        class_parents=[dict(id='Rule', label='上位规则')], class_ancestors=[dict(id='Rule', label='上位规则')],
        attributes=[dict(predicate='code', label='法律编号', values=[dict(value='QA-42', datatype='string', accepted_support_count=1)])])
    point = page.evaluate('''() => {const r=FullGraphView.renderer,d=r.displayDataset,i=d.nodeIndex.get('n0'),p=r.renderPositions||d.positions;
      window.__inspectionBefore={dataset:r.dataset,camera:[r.camera.x,r.camera.y,r.camera.scale]};
      return r.camera.worldToScreen(p[i*2],p[i*2+1],r.width,r.height);}''')
    before = len(calls)
    canvas = page.locator('.full-graph-host canvas').first.bounding_box()
    page.mouse.click(canvas['x'] + point[0], canvas['y'] + point[1])
    page.wait_for_function("() => document.querySelector('#graph-detail').textContent.includes('QA-42')")
    assert '上位规则' in page.locator('#graph-detail').inner_text()
    assert page.evaluate('''() => {const r=FullGraphView.renderer;return r.dataset===__inspectionBefore.dataset&&JSON.stringify([r.camera.x,r.camera.y,r.camera.scale])===JSON.stringify(__inspectionBefore.camera);}''')
    reads = [body for path, body in calls[before:] if path.endswith('/graph-neighborhood')]
    assert len(reads) == 1 and reads[0]['hops'] == 0 and reads[0]['max_nodes'] == 1
    assert not any('/graph-sessions' in path for path, _ in calls[before:])
    assert errors == []


def test_dropdown_selection_inspects_without_replacing_the_existing_local_canvas(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    page.evaluate("() => drawGraph('n0',0)")
    state['record_details']['n3'] = {'metadata': {'complete_marker': '完整下拉详情'}}
    page.click('#graph-settings')
    page.wait_for_selector('#graph-entity-choice option[value="n3"]', state='attached')
    summary = page.locator('#graph-summary').inner_text()
    ids = page.evaluate('() => [...wb.nodes.keys()]')
    page.evaluate('''() => {const s=wb.chart.getModel().getSeriesByIndex(0);window.__dropdownBefore={data:s.option.data,zoom:s.option.zoom,center:s.option.center};}''')
    before = len(calls)
    page.select_option('#graph-entity-choice', 'n3')
    page.wait_for_function("() => document.querySelector('#graph-detail').textContent.includes('完整下拉详情')")
    assert page.evaluate('() => [...wb.nodes.keys()]') == ids
    assert page.locator('#graph-summary').inner_text() == summary
    assert page.evaluate('() => FullGraphView.mode') == 'local'
    assert page.locator('#graph-canvas').is_visible()
    assert page.evaluate('''() => {const s=wb.chart.getModel().getSeriesByIndex(0);return s.option.data===__dropdownBefore.data&&s.option.zoom===__dropdownBefore.zoom&&JSON.stringify(s.option.center)===JSON.stringify(__dropdownBefore.center);}''')
    assert not any(path.endswith('/graph-neighborhood') or '/graph-sessions' in path for path, _ in calls[before:])
    assert page.locator('#graph-node').input_value() == 'n3'
    assert errors == []


def test_local_relation_reopen_focuses_without_another_neighborhood_read(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    fixed = dict(known_at='2025-01-01T00:00:00Z', ontology_scope='ids', ontology_ids=['historical'])
    page.evaluate('(scope) => openKnowledgeRecord({id:"edge-exact",kind:"relation",scope})', fixed)
    before = len(calls)
    page.evaluate('(scope) => openKnowledgeRecord({id:"edge-exact",kind:"relation",scope})', fixed)
    assert not any(path.endswith('/graph-neighborhood') for path, _ in calls[before:])
    assert page.evaluate("() => wb.chart.getModel().getSeriesByIndex(0).option.links.some(edge=>edge.id==='edge-exact')")
    assert errors == []


def test_source_canonical_navigation_keeps_originating_scope_and_frozen_evidence(overview_page):
    page, errors, calls, _ = overview_page
    _loaded(page)
    document = dict(id='source-document', kind='document', version=1, text='历史来源正文',
                    metadata={'title': '历史来源', 'status': 'completed'}, derived_count=1)
    page.route('**/sources?*', lambda route: route.fulfill(json={'documents': [document]}))
    page.route('**/records/source-document*', lambda route: route.fulfill(json=document))
    page.route('**/assertions?document_id=source-document*', lambda route: route.fulfill(json={'assertions': [
        dict(id='source-assertion', kind='relation', status='accepted', canonical_record_id='edge-exact', payload={'text': '来源关系'}, quote='历史来源正文')]}))
    page.evaluate("() => KnowledgeTabs.open('sources')")
    page.click('#scope-toggle')
    page.fill('#query-platforms', '来源平台')
    page.click('#apply-scope')
    page.evaluate('''() => {
      const value={...WorkspaceContext.read(current,'sources'),known_at:'2025-01-01T00:00:00Z'};
      WorkspaceContext.store.edit(current,'sources',value);WorkspaceContext.store.apply(current,'sources');WorkspaceContext.restore();
      document.dispatchEvent(new CustomEvent('workspace:scope-changed',{detail:{projectId:current,page:'sources',scope:value}}));
    }''')
    page.locator('#source-list .source-item').first.click()
    page.get_by_role('button', name='提取知识', exact=True).click()
    before = len(calls)
    page.get_by_role('button', name='查看正式知识', exact=True).click()
    page.wait_for_function("() => document.querySelector('#graph-detail').dataset.recordId === 'edge-exact'")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    reads = [body for path, body in calls[before:] if
             (path.endswith('/records/page') and body.get('record_ids') == ['edge-exact'])
             or path.endswith('/graph-neighborhood') or path.endswith('/records/edge-exact/evidence')]
    assert len(reads) >= 3
    assert all(body.get('known_at') == '2025-01-01T00:00:00Z' for body in reads)
    assert all(body.get('applicability', {}).get('platforms', {}).get('values') == ['来源平台'] for body in reads)
    assert page.locator('#graph-detail [data-action="edit"]').is_disabled()
    assert page.locator('#graph-detail .evidence-danger button').is_disabled()
    assert page.evaluate("() => wb.nodes.has('n0') && wb.nodes.has('n1')")
    assert errors == []


def test_stale_search_hit_revision_is_rejected_without_focusing_a_newer_record(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    hit = dict(state['nodes'][0])
    page.route('**/search', lambda route: route.fulfill(json={'active_mode': 'keyword', 'candidate_count': 1, 'hits': [hit], 'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '实体0')
    page.click('#search')
    page.wait_for_selector('#hits [data-graph-node="n0"]')
    page.evaluate('''() => {const r=FullGraphView.renderer;window.__staleHitBefore={dataset:r.dataset,camera:[r.camera.x,r.camera.y,r.camera.scale]};}''')
    state['nodes'][0] = {**state['nodes'][0], 'version': 2, 'version_id': 'n0-new-version', 'text': '较新实体'}
    page.click('#hits [data-graph-node="n0"]')
    page.wait_for_function("() => document.querySelector('#status').textContent.includes('版本已变化')")
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert page.evaluate('''() => {const r=FullGraphView.renderer;return r.dataset===__staleHitBefore.dataset&&JSON.stringify([r.camera.x,r.camera.y,r.camera.scale])===JSON.stringify(__staleHitBefore.camera);}''')
    assert page.locator('#graph-detail').get_attribute('data-record-id') != 'n0'
    assert errors == []


def test_search_hit_keeps_originating_scope_with_current_display_restrictions(overview_page):
    page, errors, calls, state = overview_page
    _loaded(page)
    fixed = {'known_at': '2025-01-01T00:00:00Z', 'filters': {'field': 'metadata.city', 'op': 'eq', 'value': '原始城市'}}
    page.evaluate('''(fixed) => {WorkspaceContext.store.edit(current,'search',{...WorkspaceContext.read(current,'search'),...fixed});WorkspaceContext.store.apply(current,'search');WorkspaceContext.restore();document.dispatchEvent(new CustomEvent('workspace:scope-changed',{detail:{projectId:current,page:'search'}}));}''', fixed)
    _loaded(page)
    page.route('**/search', lambda route: route.fulfill(json={'active_mode': 'keyword', 'candidate_count': 1, 'hits': [state['nodes'][0]], 'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '实体0')
    page.click('#search')
    page.wait_for_selector('#hits [data-graph-node="n0"]')
    # Another owner restoration can occur while an already-rendered result is
    # still present; its explicit navigation retains its request's authority scope.
    page.evaluate('''() => {
      WorkspaceContext.store.edit(current,'search',{...WorkspaceContext.read(current,'search'),known_at:null,filters:null});WorkspaceContext.store.apply(current,'search');WorkspaceContext.restore();
      for(const [id,value] of [['type-scope','Thing'],['predicate-scope','knows']]){const select=document.getElementById(id);if(![...select.options].some(option=>option.value===value))select.add(new Option(value,value));select.value=value;}
    }''')
    before = len(calls)
    page.click('#hits [data-graph-node="n0"]')
    page.wait_for_function("() => document.querySelector('#graph-detail').dataset.recordId === 'n0'")
    exact = [body for path, body in calls[before:] if path.endswith('/records/page') and body.get('record_ids') == ['n0']]
    assert len(exact) == 1 and exact[0]['known_at'] == fixed['known_at']
    assert exact[0]['filters'] == fixed['filters']
    assert page.locator('#type-scope').input_value() == 'Thing'
    assert page.locator('#predicate-scope').input_value() == 'knows'
    assert errors == []


def test_project_empty_overview_offers_existing_destinations_and_actual_candidate_status(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state.update(nodes=[], edges=[], entity_count=0, relation_count=0)
    _restart(page)
    _loaded(page)
    assert page.locator('.full-graph-empty').count() == 1
    guidance = page.locator('.full-graph-empty')
    assert guidance.is_visible()
    assert '正式' in guidance.inner_text()
    for action in ['sources', 'jobs', 'candidates']:
        assert guidance.locator(f'[data-graph-empty-action="{action}"]').is_visible()
    page.wait_for_function("() => document.querySelector('.full-graph-empty').textContent.includes('7 条候选')")
    guidance.locator('[data-graph-empty-action="sources"]').click()
    assert page.locator('#tab-sources').is_visible()
    assert errors == []


def test_filtered_empty_overview_explains_and_operates_existing_filter_actions(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state.update(nodes=[], edges=[], entity_count=0, relation_count=0)
    page.click('#scope-toggle')
    page.fill('#query-platforms', '未命中平台')
    page.click('#apply-scope')
    _loaded(page)
    assert page.locator('.full-graph-empty').count() == 1
    guidance = page.locator('.full-graph-empty')
    assert '筛选' in guidance.inner_text()
    assert not guidance.locator('[data-graph-empty-action="sources"]').count()
    guidance.locator('[data-graph-empty-action="adjust"]').click()
    assert page.locator('#cb-details').evaluate('(element) => element.open')
    page.click('#scope-close')
    guidance.locator('[data-graph-empty-action="reset"]').click()
    page.wait_for_function("() => !WorkspaceContext.read(current,'search').applicability")
    assert errors == []


@pytest.mark.parametrize('overview_page', [{'hold_initial_page': True}], indirect=True)
def test_abandoned_explicit_before_first_paint_restarts_default_on_reentry(overview_page):
    page, errors, calls, state = overview_page
    page.wait_for_function("() => FullGraphView.loader?.manifest && FullGraphView.loader.state === 'loading'")
    held_records = []

    def hold_exact(route):
        if route.request.post_data_json.get('record_ids') == ['n0']:
            held_records.append(route)
        else:
            route.fallback()

    page.route('**/records/page', hold_exact)
    with page.expect_request(lambda request: request.url.endswith('/records/page') and request.post_data_json.get('record_ids') == ['n0']):
        page.evaluate("() => {void openKnowledgeRecord({id:'n0',kind:'entity'});}")
    before = state['manifests']
    page.evaluate('() => GraphOverview.ensure()')
    assert state['manifests'] == before, 'active explicit navigation keeps its priority'
    state['hold_page'] = False
    page.evaluate("() => {KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    _loaded(page)
    assert state['manifests'] == before + 1
    assert page.evaluate('() => FullGraphView.renderer.dataset.nodeCount') == 4
    assert page.locator('#graph-detail').get_attribute('data-record-id') != 'n0'
    assert errors == []


def test_successful_local_navigation_survives_page_reentry_without_a_default_reload(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state['nodes'].append(dict(id='local-reentry', kind='entity', type='Thing', text='已探索局部实体', version=1, version_id='local-reentry-v1'))
    page.evaluate("() => openKnowledgeRecord({id:'local-reentry',kind:'entity',scope:{known_at:'2025-01-01T00:00:00Z'}})")
    page.evaluate('''() => {const s=wb.chart.getModel().getSeriesByIndex(0);window.__localReentryBefore={data:s.option.data,zoom:s.option.zoom,center:s.option.center};}''')
    before = state['manifests']
    page.evaluate("() => {KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert state['manifests'] == before
    assert page.evaluate('() => FullGraphView.mode') == 'local'
    assert page.evaluate('''() => {const s=wb.chart.getModel().getSeriesByIndex(0);return s.option.data===__localReentryBefore.data&&s.option.zoom===__localReentryBefore.zoom&&JSON.stringify(s.option.center)===JSON.stringify(__localReentryBefore.center);}''')
    assert errors == []


def test_failed_explicit_keeps_a_prior_authoritative_empty_guide_on_reentry(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    state.update(nodes=[], edges=[], entity_count=0, relation_count=0)
    _restart(page)
    _loaded(page)
    before = state['manifests']
    page.evaluate("() => openKnowledgeRecord({id:'missing',kind:'entity'}).catch(() => null)")
    page.evaluate("() => {KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert state['manifests'] == before
    assert page.locator('.full-graph-empty').is_visible()
    assert errors == []


@pytest.mark.parametrize('overview_page', [{'node_types': ['TypeA', 'TypeB', 'TypeA', 'TypeB']}], indirect=True)
def test_explicit_search_navigation_honors_active_full_type_chip_and_reuses_allowed_records(overview_page):
    page, errors, _, state = overview_page
    _loaded(page)
    page.locator('.full-graph-types [data-graph-type-toggle]').click()
    page.click('[data-fg-type="TypeA"]')
    assert '当前可见 2 实体' in page.locator('.full-graph-progress').inner_text()
    page.evaluate('''() => {window.__filteredBefore=FullGraphView.renderer.dataset;}''')
    page.route('**/search', lambda route: route.fulfill(json={'active_mode': 'keyword', 'candidate_count': 1, 'hits': [state['nodes'][1]], 'source_chunks': [], 'context_chunks': []}))
    page.select_option('#search-mode', 'keyword')
    page.fill('#query', '实体1')
    page.click('#search')
    page.wait_for_selector('#hits [data-graph-node="n1"]')
    page.click('#hits [data-graph-node="n1"]')
    page.wait_for_function("() => document.querySelector('#status').textContent.includes('筛选') || document.querySelector('#graph-detail').dataset.recordId === 'n1'")
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert page.evaluate("() => FullGraphView.renderer.typeFilter") == 'TypeA'
    assert page.evaluate('() => FullGraphView.renderer.dataset === __filteredBefore')
    assert page.locator('#graph-detail').get_attribute('data-record-id') != 'n1'
    assert '筛选' in page.locator('#status').inner_text()
    before = state['manifests']
    page.evaluate("() => openKnowledgeRecord({id:'n0',kind:'entity',version_id:'v0'})")
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert page.evaluate('() => FullGraphView.renderer.dataset === __filteredBefore')
    assert page.evaluate("() => FullGraphView.renderer.displayDataset.records[FullGraphView.renderer.selected]?.id") == 'n0'
    assert page.evaluate("() => FullGraphView.renderer.typeFilter") == 'TypeA'
    page.evaluate("() => {KnowledgeTabs.open('records');KnowledgeTabs.open('search');}")
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    assert state['manifests'] == before
    assert page.evaluate("() => FullGraphView.renderer.typeFilter") == 'TypeA'
    page.click('[data-fg="all-types"]')
    assert '当前可见 4 实体' in page.locator('.full-graph-progress').inner_text()
    assert state['manifests'] == before
    assert errors == []
