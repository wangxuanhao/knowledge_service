"""Real pointer clicks against the bundled ECharts and WebGL renderers."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from test_workspace_redesign_ui import redesign_page

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service/web'


def _local_point(page, kind):
    return page.evaluate('''kind=>{
      const chart=wb.chart,series=chart.getModel().getSeriesByIndex(0);
      if(kind==='node'){
        const symbol=series.getData().getItemGraphicEl(1).getSymbolPath(),parent=series.getData().getItemGraphicEl(0).getSymbolPath();
        const center=symbol.transformCoordToGlobal(0,0),other=parent.transformCoordToGlobal(0,0),distance=Math.hypot(center[0]-other[0],center[1]-other[1]);
        return {center,normal:[(center[0]-other[0])/distance,(center[1]-other[1])/distance]};
      }
      const line=series.getEdgeData().getItemGraphicEl(0).childAt(0),t=.3;
      const center=line.transformCoordToGlobal(...line.pointAt(t));
      const a=line.transformCoordToGlobal(...line.pointAt(t-.01)),b=line.transformCoordToGlobal(...line.pointAt(t+.01));
      const length=Math.hypot(b[0]-a[0],b[1]-a[1]);
      return {center,normal:[-(b[1]-a[1])/length,(b[0]-a[0])/length]};
    }''', kind)


@pytest.mark.parametrize('zoom', [.5, 1, 2])
def test_local_graph_nearby_click_opens_the_existing_detail_and_drag_still_pans(redesign_page, zoom):
    page, errors = redesign_page
    page.set_default_timeout(8000)
    nodes = [dict(id=f'click-node-{i}', kind='entity', type='Thing', text=f'实体{i}', version=1)
             for i in range(3)]
    edges = [dict(id=f'click-edge-{i}', kind='relation', subject_id=nodes[0]['id'],
                  object_id=nodes[i+1]['id'], type='关联', version=1) for i in range(2)]
    page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': nodes+edges, 'truncated': False}))
    page.route('**/records/page', lambda route: route.fulfill(json={'records': nodes, 'total': len(nodes), 'next_cursor': None}))
    page.click('[data-tab="search"]')
    page.evaluate("()=>drawGraph('click-node-0',1)")
    page.click('#graph-settings')
    page.locator('#graph-edge-labels').uncheck()
    page.locator('#graph-labels').uncheck()
    page.locator('#graph-settings-drawer .workspace-drawer-close').click()
    if zoom != 1:
        page.evaluate('(factor)=>GraphWorkbench.zoom(wb.chart,factor)', zoom)
    page.evaluate('''()=>{window.graphTargetEvents=[];
      wb.chart.on('click',event=>window.graphTargetEvents.push({id:event.data?.id,kind:event.dataType}));}''')
    point = _local_point(page, 'edge')
    box = page.locator('#graph-canvas').bounding_box()
    page.mouse.click(box['x']+point['center'][0]+point['normal'][0]*8,
                     box['y']+point['center'][1]+point['normal'][1]*8)
    page.wait_for_function("()=>window.graphTargetEvents.some(event=>event.id==='click-edge-0'&&event.kind==='edge')")
    assert '关系' in page.locator('#graph-detail').inner_text()
    page.evaluate("()=>GraphWorkbench.locate('click-node-1')")
    point = _local_point(page, 'node')
    box = page.locator('#graph-canvas').bounding_box()
    page.mouse.click(box['x']+point['center'][0]+point['normal'][0]*11,
                     box['y']+point['center'][1]+point['normal'][1]*11)
    page.wait_for_function("()=>window.graphTargetEvents.some(event=>event.id==='click-node-1'&&event.kind==='node')")
    assert '实体1' in page.locator('#graph-detail').inner_text()
    before = page.evaluate('()=>({center:wb.chart.getModel().getSeriesByIndex(0).coordinateSystem.transform.slice(),clicks:window.graphTargetEvents.length})')
    box = page.locator('#graph-canvas').bounding_box()
    page.mouse.move(box['x']+18, box['y']+18)
    page.mouse.down()
    page.mouse.move(box['x']+65, box['y']+45, steps=6)
    page.mouse.up()
    after = page.evaluate('()=>({center:wb.chart.getModel().getSeriesByIndex(0).coordinateSystem.transform.slice(),clicks:window.graphTargetEvents.length})')
    assert before['center'] != after['center'], 'empty canvas dragging still pans'
    assert before['clicks'] == after['clicks'], 'dragging must not open another record'
    assert not errors


@pytest.fixture
def touch_graph_page(monkeypatch):
    # Reuse the real application fixture with a real touch-capable context.
    from playwright.sync_api import Browser
    create_page = Browser.new_page
    def touch_page(browser, **options):
        return create_page(browser, **{**options, 'has_touch': True})
    monkeypatch.setattr(Browser, 'new_page', touch_page)
    session = redesign_page.__wrapped__()
    try:
        yield next(session)
    finally:
        session.close()


def test_local_touch_retains_a_larger_target_than_the_desktop_pointer(touch_graph_page):
    page, errors = touch_graph_page
    page.set_default_timeout(8000)
    assert page.evaluate("()=>matchMedia('(pointer:coarse)').matches")
    nodes = [dict(id=f'touch-node-{i}', kind='entity', type='Thing', text=f'触屏实体{i}', version=1) for i in range(2)]
    edge = dict(id='touch-edge', kind='relation', type='关联', subject_id=nodes[0]['id'], object_id=nodes[1]['id'], version=1)
    page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': nodes+[edge], 'truncated': False}))
    page.click('[data-tab="search"]')
    page.evaluate("()=>drawGraph('touch-node-0',1)")
    page.click('#graph-settings')
    page.locator('#graph-labels').uncheck()
    page.locator('#graph-edge-labels').uncheck()
    page.locator('#graph-settings-drawer .workspace-drawer-close').click()
    page.evaluate("()=>GraphWorkbench.locate('touch-node-1')")
    page.evaluate("()=>{window.touchSelections=[];wb.chart.on('click',event=>touchSelections.push(event.data?.id));}")
    point = _local_point(page, 'node')
    box = page.locator('#graph-canvas').bounding_box()
    page.touchscreen.tap(box['x']+point['center'][0]+point['normal'][0]*23,
                        box['y']+point['center'][1]+point['normal'][1]*23)
    page.wait_for_function("()=>window.touchSelections.includes('touch-node-1')")
    assert '触屏实体1' in page.locator('#graph-detail').inner_text()
    assert not errors


@pytest.mark.parametrize('dpr', [1, 2])
def test_full_graph_real_pointer_uses_css_tolerance_across_zoom_and_drag(dpr):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel='msedge', headless=True,
                                          args=['--enable-webgl', '--use-angle=swiftshader'])
        try:
            page = browser.new_page(viewport={'width': 900, 'height': 700}, device_scale_factor=dpr)
            page.set_content('<div id="host" style="width:800px;height:600px"></div>')
            page.add_script_tag(path=str(WEB / 'full-graph.js'))
            page.evaluate('''()=>{
              const dataset=new FullGraph.GraphDataset();
              dataset.addNodes([{id:'a',kind:'entity'},{id:'b',kind:'entity'}],new Float32Array([-.2,0,.2,0]));
              dataset.addEdges([{id:'ab',kind:'relation',subject_id:'a',object_id:'b'}]);
              window.targetSelections=[];window.renderer=new FullGraph.Renderer(document.getElementById('host'),{
                onSelect:row=>targetSelections.push(row.id)});renderer.setDataset(dataset);
            }''')
            for scale in [.25, 1, 4]:
                page.evaluate('(scale)=>{renderer.camera.scale=scale;renderer.draw();}', scale)
                box = page.locator('.fg-gl').bounding_box()
                point = page.evaluate('()=>renderer.camera.worldToScreen(0,0,renderer.width,renderer.height)')
                page.mouse.click(box['x']+point[0], box['y']+point[1]+9)
                assert page.evaluate('()=>targetSelections.at(-1)') == 'ab'
                point = page.evaluate('()=>renderer.camera.worldToScreen(-.2,0,renderer.width,renderer.height)')
                page.mouse.click(box['x']+point[0], box['y']+point[1]+11)
                assert page.evaluate('()=>targetSelections.at(-1)') == 'a'
                assert page.evaluate('()=>renderer.selectedEdge') == -1
            count = page.evaluate('()=>targetSelections.length')
            page.mouse.move(box['x']+20, box['y']+20)
            page.mouse.down()
            page.mouse.move(box['x']+70, box['y']+45, steps=5)
            page.mouse.up()
            assert page.evaluate('()=>targetSelections.length') == count
            assert page.evaluate('()=>renderer.camera.x') != 0
            assert page.evaluate('()=>renderer.gl.getError()') == 0
            page.evaluate('()=>renderer.destroy()')
        finally:
            browser.close()
