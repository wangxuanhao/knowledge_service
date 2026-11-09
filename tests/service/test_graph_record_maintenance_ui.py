"""Real workspace UI with isolated record mutation fixtures."""
import pytest

from test_workspace_redesign_ui import redesign_page


@pytest.fixture
def record_page(redesign_page):
    page, errors = redesign_page
    rows = [dict(id=key, kind='entity', type='Person', text=name, ontology_id='o1',
                 version=1, version_id=key + '-v1', metadata={}, properties={})
            for key, name in [('a', '原名称'), ('b', '另一个实体')]]
    release = dict(id='o1', version=1, summary=dict(
        classes=[dict(id='Person', label='人')], relations=[], attributes=[]))
    calls = dict(sessions=0, neighborhoods=[], creates=0, edits=0)

    def records(route):
        body = route.request.post_data_json
        selected = [row for row in rows if not body.get('record_ids') or row['id'] in body['record_ids']]
        selected = [row for row in selected if not body.get('kinds') or row['kind'] in body['kinds']]
        if body.get('query'):
            selected = [row for row in selected if body['query'] in row['text'] or body['query'] == row['id']]
        route.fulfill(json=dict(records=selected, total=len(selected), next_cursor=None))

    def create(route):
        calls['creates'] += 1
        row = dict(route.request.post_data_json['records'][0], id='c', version=1, version_id='c-v1')
        rows.append(row)
        route.fulfill(status=201, json=dict(records=[row]))

    def revise(route):
        if route.request.method != 'PUT':
            route.fulfill(json=rows[0])
            return
        calls['edits'] += 1
        rows[0] = dict(route.request.post_data_json['record'], version=2, version_id='a-v2')
        route.fulfill(json=rows[0])

    def manifest(route):
        calls['sessions'] += 1
        route.fulfill(json=dict(id='maintenance', entity_count=len(rows), relation_count=0,
                                watermark=calls['sessions'], layout_version='stable-id-v1'))

    def graph_page(route):
        route.fulfill(json=dict(session_id='maintenance', kind=route.request.post_data_json['kind'],
                                records=rows, entities=[], next_cursor=None,
                                watermark=calls['sessions'], layout_version='stable-id-v1'))

    def neighborhood(route):
        calls['neighborhoods'].append(route.request.post_data_json)
        route.fulfill(json=dict(records=rows, truncated=False))

    page.route('**/records/page', records)
    page.route('**/records', create)
    page.route('**/records/a', revise)
    page.route('**/ontology**', lambda route: route.fulfill(json=release))
    page.route('**/ontologies', lambda route: route.fulfill(json=dict(versions=[release])))
    page.route('**/graph-sessions', manifest)
    page.route('**/graph-sessions/maintenance/page', graph_page)
    page.route('**/graph-neighborhood', neighborhood)
    page.click('[data-tab="search"]')
    page.evaluate('() => FullGraphView.restart({projectId:"p",scope:{}})')
    page.wait_for_function('() => FullGraphView.loader.state === "complete"')
    yield page, rows, calls
    assert errors == []


def test_created_ledger_entity_appears_after_returning_to_full_graph(record_page):
    page, rows, calls = record_page
    page.click('[data-tab="records"]')
    page.get_by_role('button', name='新增实体', exact=True).click()
    page.fill('#create-record-text', '新增实体')
    page.click('#save-created-record')
    page.wait_for_function('() => document.querySelector(".record-create-message").textContent.includes("已创建")')
    page.evaluate('() => RecordCreate.close()')
    page.wait_for_function('() => document.querySelector("#records").textContent.includes("新增实体")')
    page.click('[data-tab="search"]')
    page.wait_for_function('() => FullGraphView.mode === "full" && FullGraphView.renderer.dataset.nodeIndex.has("c")')
    assert calls['creates'] == 1
    assert calls['sessions'] <= 3


def test_editing_in_full_graph_keeps_mode_and_updates_name(record_page):
    page, rows, calls = record_page
    page.evaluate('() => FullGraphView.renderer.locate("a")')
    page.wait_for_selector('#graph-detail [data-action="edit"]')
    page.click('#graph-detail [data-action="edit"]')
    page.fill('#structured-record-text', '修改后名称')
    page.click('#save-structured-record')
    page.wait_for_function('() => document.querySelector("#record-dialog-message").textContent.includes("已保存")')
    page.wait_for_function('() => FullGraphView.mode === "full" && FullGraphView.renderer.dataset.records.find(row=>row.id==="a")?.text === "修改后名称"')
    assert calls['edits'] == 1


def test_local_save_keeps_current_hops_and_root(record_page):
    page, rows, calls = record_page
    page.click('[data-fg-mode="local"]')
    page.fill('#graph-hops', '3')
    page.evaluate('() => window.drawGraph("a",3)')
    page.evaluate('(row) => window.editRecord(row)', rows[0])
    page.fill('#structured-record-text', '局部修改')
    page.click('#save-structured-record')
    page.wait_for_function('() => document.querySelector("#record-dialog-message").textContent.includes("已保存")')
    page.wait_for_function('() => wb.nodes.get("a")?.text === "局部修改"')
    assert calls['neighborhoods'][-1]['hops'] == 3
    assert calls['neighborhoods'][-1]['seeds'] == ['a']
    assert page.evaluate('() => FullGraphView.mode') == 'local'


def test_graph_has_shared_entity_creation_entry(record_page):
    page, rows, calls = record_page
    page.click('#graph-create-entity')
    page.wait_for_selector('#create-record-text')
    page.fill('#create-record-text', '画布新增')
    page.click('#save-created-record')
    page.wait_for_function('() => FullGraphView.renderer.dataset.nodeIndex.has("c")')
    assert calls['creates'] == 1


def _route_connected_neighborhood(page, rows, edges, requests):
    def neighborhood(route):
        body = route.request.post_data_json
        requests.append(body)
        present = {row['id'] for row in rows}
        seen = set(body['seeds']) & present
        frontier = set(seen)
        for _ in range(body['hops']):
            following = set()
            for edge in edges:
                subject, target = edge['subject_id'], edge['object_id']
                if subject not in present or target not in present:
                    continue
                if body['direction'] != 'backward' and subject in frontier:
                    following.add(target)
                if body['direction'] != 'forward' and target in frontier:
                    following.add(subject)
            frontier = following - seen
            seen.update(following)
        selected = [row for row in rows if row['id'] in seen]
        selected += [edge for edge in edges if edge['subject_id'] in seen and edge['object_id'] in seen]
        route.fulfill(json=dict(records=selected, truncated=False))
    page.route('**/graph-neighborhood', neighborhood)


def test_local_refresh_reloads_independent_branches_with_their_own_hops(record_page):
    page, rows, calls = record_page
    rows.extend(dict(rows[0], id=key, text='分支实体' + key, version_id=key + '-v1')
                for key in ['c', 'd', 'e', 'f'])
    edges = [dict(id=subject + target, kind='relation', type='related', text='关联',
                  subject_id=subject, object_id=target, ontology_id='o1', version=1,
                  version_id=subject + target + '-v1')
             for subject, target in [('a', 'b'), ('a', 'c'), ('b', 'd'), ('d', 'e'), ('c', 'f')]]
    _route_connected_neighborhood(page, rows, edges, calls['neighborhoods'])
    page.click('[data-fg-mode="local"]')
    page.fill('#graph-hops', '1')
    page.evaluate('async () => { await drawGraph("a", 1); await expandGraphBranch("b", 2); await expandGraphBranch("c", 1); }')
    assert page.evaluate('() => [...wb.nodes.keys()].sort()') == ['a', 'b', 'c', 'd', 'e', 'f']
    before = len(calls['neighborhoods'])
    page.evaluate('(row) => editRecord(row)', rows[0])
    page.fill('#structured-record-text', '分支共享实体的新名称')
    page.click('#save-structured-record')
    page.wait_for_function('() => wb.nodes.get("a")?.version === 2')
    assert [(body['seeds'], body['hops']) for body in calls['neighborhoods'][before:]] == [
        (['a'], 1), (['b'], 2), (['c'], 1)]
    assert page.evaluate('() => FullGraphView.mode') == 'local'
    assert page.locator('#graph-hops').input_value() == '1'
    assert page.evaluate('() => [...wb.nodes.keys()].sort()') == ['a', 'b', 'c', 'd', 'e', 'f']
    assert page.evaluate('() => ({text:wb.nodes.get("a").text, version:wb.nodes.get("a").version, version_id:wb.nodes.get("a").version_id})') == dict(
        text='分支共享实体的新名称', version=2, version_id='a-v2')
    rendered = page.evaluate('() => wb.chart.getModel().getSeriesByIndex(0).option')
    assert {edge['id'] for edge in rendered['links']} == {edge['id'] for edge in edges}
    assert next(node['name'] for node in rendered['data'] if node['id'] == 'a') == '分支共享实体的新名称'


def test_local_deleted_root_is_restored_by_ledger_undo_with_the_same_hops(record_page):
    page, rows, calls = record_page
    original = dict(rows[0])
    edge = dict(id='ab', kind='relation', type='related', text='关联', subject_id='a', object_id='b',
                ontology_id='o1', version=1, version_id='ab-v1')
    edges = [edge]
    _route_connected_neighborhood(page, rows, edges, calls['neighborhoods'])
    operation = dict(id='audit-delete-a', recorded_at='2026-11-09T01:00:00Z', metadata=dict(
        operation='delete', operation_id='delete-a', before=[original, edge]))
    mutations = []

    def delete(route):
        mutations.append(('delete', route.request.post_data_json))
        rows[:] = [row for row in rows if row['id'] != 'a']
        edges.clear()
        route.fulfill(json=dict(deleted=2, operation_id='delete-a', records=[
            dict(original, version=2, version_id='a-v2', metadata={'_deleted': True}),
            dict(edge, version=2, version_id='ab-v2', metadata={'_deleted': True})]))

    def undo(route):
        mutations.append(('undo', route.request.post_data_json))
        restored = dict(original, version=3, version_id='a-v3')
        restored_edge = dict(edge, version=3, version_id='ab-v3')
        rows.insert(0, restored)
        edges.append(restored_edge)
        route.fulfill(json=dict(restored=2, records=[restored, restored_edge]))

    page.route('**/delete', delete)
    page.route('**/operations', lambda route: route.fulfill(json={'operations': [operation] if mutations else []}))
    page.route('**/operations/delete-a/undo', undo)
    page.click('[data-fg-mode="local"]')
    page.fill('#graph-hops', '3')
    page.evaluate('() => drawGraph("a", 3)')
    page.evaluate('(row) => editRecord(row)', original)
    page.locator('#record-advanced-edit > summary').click()
    page.check('#delete-confirm')
    page.click('#soft-delete')
    page.wait_for_function('() => !wb.nodes.has("a") && wb.nodes.size === 0')
    assert page.evaluate('() => FullGraphView.mode') == 'local'
    page.click('#close-record-dialog')
    page.click('[data-tab="records"]')
    page.get_by_role('button', name='读取操作历史', exact=True).click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('[data-undo-operation="delete-a"]').click()
    page.wait_for_function('() => document.getElementById("status").textContent.includes("已撤销")')
    page.click('[data-tab="search"]')
    page.wait_for_function('() => FullGraphView.mode === "local" && wb.nodes.get("a")?.version === 3')
    assert mutations == [('delete', {'record_id': 'a', 'expected_version': 1}), ('undo', {})]
    assert page.locator('#graph-hops').input_value() == '3', page.locator('#graph-hops').input_value()
    assert calls['neighborhoods'][-1]['seeds'] == ['a']
    assert calls['neighborhoods'][-1]['hops'] == 3
    assert page.evaluate('() => wb.nodes.get("a").text') == original['text']
    assert page.evaluate('() => wb.chart.getModel().getSeriesByIndex(0).option.links.map(edge => edge.id)') == ['ab']


def test_large_full_graph_refresh_reads_one_bounded_batch_and_preserves_camera(record_page):
    page, rows, calls = record_page
    rows.extend(dict(rows[0], id=f'large-{i}', text=f'大图实体{i}', version_id=f'large-{i}-v1')
                for i in range(2, 600))
    edges = [dict(id=f'large-edge-{i}', kind='relation', type='related', text='关联',
                  subject_id=rows[i]['id'], object_id=rows[i + 1]['id'], version=1, version_id=f'edge-{i}-v1')
             for i in range(len(rows) - 1)]
    sessions, pages = {}, []

    def manifest(route):
        serial = len(sessions) + 1
        value = dict(id=f'large-maintenance-{serial}', entity_count=len(rows), relation_count=len(edges),
                     watermark=serial, layout_version='stable-id-v1')
        sessions[value['id']] = value
        route.fulfill(json=value)

    def graph_page(route):
        body = route.request.post_data_json
        session_id = route.request.url.split('/')[-2]
        pages.append((session_id, body))
        candidates = edges if body['kind'] == 'connected' else rows
        offset = int(body.get('cursor') or 0)
        selected = candidates[offset:offset + body['limit']]
        endpoints = {edge[key] for edge in selected for key in ['subject_id', 'object_id']} if body['kind'] == 'connected' else set()
        route.fulfill(json=dict(session_id=session_id, kind=body['kind'], records=selected,
            entities=[row for row in rows if row['id'] in endpoints],
            next_cursor=str(offset + len(selected)) if offset + len(selected) < len(candidates) else None,
            watermark=sessions[session_id]['watermark'], layout_version='stable-id-v1'))

    page.route('**/graph-sessions', manifest)
    page.route('**/graph-sessions/large-maintenance-*/page', graph_page)
    page.evaluate('async () => { await GraphOverview.open({projectId:"p", scope:{}}); await FullGraphView.next(); }')
    assert page.evaluate('() => FullGraphView.renderer.dataset.edgeCount') == 400
    page.evaluate('() => FullGraphView.renderer.locate("a")')
    page.wait_for_selector('#graph-detail [data-action="edit"]')
    camera = page.evaluate('() => { const camera = FullGraphView.renderer.camera; Object.assign(camera,{x:.25,y:-.125,scale:1.8}); FullGraphView.renderer.requestDraw(); return {...camera}; }')
    page.click('#graph-detail [data-action="edit"]')
    page.fill('#structured-record-text', '大图修改后名称')
    page.click('#save-structured-record')
    page.wait_for_function('() => FullGraphView.renderer.dataset.records.find(row => row.id === "a")?.text === "大图修改后名称" && FullGraphView.loader.state === "paused"')
    assert len(sessions) == 2
    refreshed_pages = [body for session, body in pages if session == 'large-maintenance-2']
    assert refreshed_pages == [{'kind': 'connected', 'cursor': None, 'limit': 200}]
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    assert page.evaluate('() => ({...FullGraphView.renderer.camera})') == camera
    assert page.evaluate('() => ({nodes:FullGraphView.renderer.dataset.nodeCount, edges:FullGraphView.renderer.dataset.edgeCount})') == dict(nodes=201, edges=200)
    assert page.evaluate('() => FullGraphView.loader.manifest.entity_count') == 600
    assert page.evaluate('() => FullGraphView.loader.manifest.relation_count') == 599
    assert calls['edits'] == 1


def test_full_frozen_scope_survives_a_current_ledger_edit(record_page):
    page, rows, calls = record_page
    for row in rows:
        row['metadata'] = {'city': '杭州'}
    historical = [dict(row) for row in rows]
    frozen = dict(known_at='2025-01-01T00:00:00Z', valid_at='2024-12-01T00:00:00Z', include_unknown=False,
                  filters={'field': 'metadata.city', 'op': 'eq', 'value': '杭州'},
                  ontology_scope='ids', ontology_ids=['o1'], entity_type='Person')
    requests = []
    sessions = {}

    def manifest(route):
        requests.append(route.request.post_data_json)
        serial = len(requests)
        value = dict(id=f'frozen-maintenance-{serial}', entity_count=len(historical), relation_count=0,
                     watermark=serial, layout_version='stable-id-v1')
        sessions[value['id']] = value
        route.fulfill(json=value)

    def graph_page(route):
        session_id = route.request.url.split('/')[-2]
        route.fulfill(json=dict(session_id=session_id, kind=route.request.post_data_json['kind'],
            records=historical, entities=[], next_cursor=None,
            watermark=sessions[session_id]['watermark'], layout_version='stable-id-v1'))

    page.route('**/graph-sessions', manifest)
    page.route('**/graph-sessions/frozen-maintenance-*/page', graph_page)
    page.evaluate('async scope => { await GraphOverview.open({projectId:"p", scope}); }', frozen)
    assert page.evaluate('() => WorkspaceContext.read("p", "search").known_at') is None
    page.click('[data-tab="records"]')
    page.locator('#records [data-record-id="a"] [data-edit-row]').click()
    page.fill('#structured-record-text', '台账当前版本已修改')
    page.click('#save-structured-record')
    page.wait_for_function('() => document.getElementById("record-dialog-message").textContent.includes("已保存")')
    page.click('#close-record-dialog')
    page.click('[data-tab="search"]')
    page.wait_for_function('() => FullGraphView.loader.manifest?.id === "frozen-maintenance-2" && FullGraphView.loader.state === "complete"')
    assert calls['edits'] == 1 and rows[0]['version'] == 2
    assert page.evaluate('() => FullGraphView.mode') == 'full'
    authority = {key: value for key, value in frozen.items() if key != 'entity_type'}
    assert all(request.get(key) == value for request in requests for key, value in authority.items()), requests
    assert all(page.evaluate('(key) => FullGraphView.scope[key]', key) == value for key, value in frozen.items())
    assert page.evaluate('() => FullGraphView.renderer.dataset.records.find(row => row.id === "a").text') == '原名称'
    assert page.locator('#graph-create-entity').is_disabled()


def test_graph_creation_entry_is_disabled_in_history_on_a_narrow_screen(record_page):
    page, rows, calls = record_page
    page.set_viewport_size({'width': 320, 'height': 900})
    page.click('#scope-toggle')
    page.locator('#cb-details .cb-advanced > summary').click()
    page.fill('#scope-known-at', '2025-01-01T00:00')
    page.click('#apply-scope')
    page.wait_for_function('() => document.getElementById("graph-create-entity").disabled')
    for mode in ['full', 'local']:
        page.click(f'[data-fg-mode="{mode}"]')
        if mode == 'local':
            page.evaluate('() => drawGraph("a", 1)')
        page.evaluate('() => GraphWorkspaceLayout.settled()')
        button = page.locator('#graph-create-entity')
        assert button.is_visible() and button.is_disabled()
        assert '历史' in button.get_attribute('title')
        box = button.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 321, box
        stage = page.locator('#graph-stage').bounding_box()
        for control in page.locator('#graph-stage .graph-stage-heading button:visible').all():
            bounds = control.bounding_box()
            measurement = dict(control=control.get_attribute('id') or control.get_attribute('data-fg-mode'),
                               bounds=bounds, stage=stage, viewport=320)
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 321, measurement
            assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 901, measurement
            assert bounds['x'] >= stage['x'] - 1 and bounds['x'] + bounds['width'] <= stage['x'] + stage['width'] + 1, measurement
            assert bounds['y'] >= stage['y'] - 1 and bounds['y'] + bounds['height'] <= stage['y'] + stage['height'] + 1, measurement
        button.evaluate('node => node.click()')
        assert page.locator('#record-create-dialog').count() == 0
    assert calls['creates'] == 0


def test_local_edit_refreshes_the_full_cache_and_retains_local_root_and_hops(record_page):
    page, rows, calls = record_page
    page.click('[data-fg-mode="local"]')
    page.fill('#graph-hops', '3')
    page.evaluate('() => drawGraph("a", 3)')
    page.evaluate('() => selectEntityDetail("a")')
    assert page.locator('#graph-node').input_value() == 'a'
    page.evaluate('(row) => editRecord(row)', rows[0])
    page.fill('#structured-record-text', '跨模式同步后的名称')
    page.click('#save-structured-record')
    page.wait_for_function('() => wb.nodes.get("a")?.text === "跨模式同步后的名称"')
    page.click('#close-record-dialog')
    sessions = calls['sessions']
    page.click('[data-fg-mode="full"]')
    page.wait_for_function('() => FullGraphView.mode === "full" && FullGraphView.renderer.dataset.records.find(row => row.id === "a")?.text === "跨模式同步后的名称"')
    assert calls['sessions'] == sessions + 1
    page.click('[data-fg-mode="local"]')
    page.wait_for_function('() => FullGraphView.mode === "local" && wb.nodes.get("a")?.text === "跨模式同步后的名称"')
    assert page.locator('#graph-node').input_value() == 'a'
    assert page.locator('#graph-hops').input_value() == '3'
    expanded = [body for body in calls['neighborhoods'] if body['hops'] > 0]
    assert expanded[-1]['seeds'] == ['a']
    assert expanded[-1]['hops'] == 3


def test_full_presentation_type_remains_switchable_after_an_edit(record_page):
    page, rows, calls = record_page
    rows[1]['type'] = 'Company'
    page.evaluate('() => GraphOverview.open({projectId:"p",scope:{}})')
    root = page.locator('.full-graph-types')
    root.locator('[data-graph-type-toggle]').click()
    root.locator('[data-fg-type="Person"]').click()
    page.evaluate('(row) => editRecord(row)', rows[0])
    page.fill('#structured-record-text', '筛选内修改名称')
    page.click('#save-structured-record')
    page.wait_for_function('() => FullGraphView.renderer.dataset.records.find(row=>row.id==="a")?.version === 2')
    page.click('#close-record-dialog')
    assert page.evaluate('() => FullGraphView.filters.entity_type') == 'Person'
    assert page.evaluate('() => FullGraphView.scope.entity_type || null') is None
    root.locator('[data-graph-type-toggle]').click()
    root.locator('[data-fg-type="Company"]').click()
    assert page.evaluate('() => FullGraphView.filters.entity_type') == 'Company'
    assert page.evaluate('() => FullGraphView.renderer.displayDataset.records.map(row=>row.id)') == ['b']
    root.locator('[data-fg-types-all]').click()
    assert page.evaluate('() => FullGraphView.filters.entity_type') is None
    assert page.evaluate('() => FullGraphView.renderer.displayDataset.nodeCount') == 2
