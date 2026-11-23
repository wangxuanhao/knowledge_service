"""Real wheel and pointer input against both production graph renderers."""
import pytest

from test_workspace_redesign_ui import redesign_page
from test_graph_workspace_layout_browser import _open


def _load(page, mode):
    _open(page, 1440, 950)
    nodes = [dict(id=f'gesture-{i}', kind='entity', type='Thing', text=f'交互实体{i}', version=1)
             for i in range(9)]
    edges = [dict(id=f'gesture-edge-{i}', kind='relation', type='关联', text='关联',
                  subject_id='gesture-0', object_id=nodes[i]['id'], version=1)
             for i in range(1, len(nodes))]
    page.route('**/records/page', lambda route: route.fulfill(json={'records': nodes, 'total': len(nodes), 'next_cursor': None}))
    page.route('**/graph-neighborhood', lambda route: route.fulfill(json={'records': nodes + edges, 'truncated': False}))
    page.route('**/timeline?*', lambda route: route.fulfill(json={'events': [], 'truncated': False}))
    page.route('**/records/*/evidence', lambda route: route.fulfill(json={'documents': []}))
    manifest = dict(id='gestures', entity_count=len(nodes), relation_count=len(edges), watermark=1, layout_version='stable-id-v1')
    page.route('**/graph-sessions', lambda route: route.fulfill(json=manifest))
    page.route('**/graph-sessions/gestures/page', lambda route: route.fulfill(json={
        'session_id':'gestures', 'kind': route.request.post_data_json['kind'], 'watermark':1,
        'layout_version':'stable-id-v1', 'next_cursor':None, 'entities':nodes,
        'records':edges if route.request.post_data_json['kind']=='connected' else nodes}))
    page.evaluate("() => drawGraph('gesture-0', 1)")
    if mode == 'full':
        page.click('[data-fg-mode="full"]')
        page.evaluate('async () => { await FullGraphView.restart(); }')
        page.wait_for_function('() => FullGraphView.loader.state === "complete"')
    page.evaluate('() => GraphWorkspaceLayout.settled()')
    return '#graph-canvas' if mode == 'local' else '.full-graph-host'


def _camera(page, mode):
    return page.evaluate('''mode => {
      if(mode==='full') {const c=FullGraphView.renderer.camera;return {scale:c.scale,x:c.x,y:c.y};}
      const s=wb.chart.getModel().getSeriesByIndex(0),t=s.coordinateSystem.transform;
      return {scale:s.get('zoom'),x:t[4],y:t[5]};
    }''', mode)


@pytest.mark.parametrize('mode', ['local', 'full'])
@pytest.mark.parametrize('gesture', ['wheel', 'pan'])
def test_wheel_and_blank_drag_work_in_both_modes(redesign_page, mode, gesture):
    page, errors = redesign_page
    host = _load(page, mode)
    box = page.locator(host).bounding_box()
    x, y = box['x'] + 45, box['y'] + 45
    before = _camera(page, mode)
    page.mouse.move(x, y)
    if gesture == 'wheel':
        page.mouse.wheel(0, -240)
        page.wait_for_function('''({mode,before}) => {
          const scale=mode==='full'?FullGraphView.renderer.camera.scale:wb.chart.getModel().getSeriesByIndex(0).get('zoom');
          return Math.abs(scale-before.scale)>.001;
        }''', arg={'mode':mode,'before':before}, timeout=3000)
        assert _camera(page, mode)['scale'] > before['scale']
    else:
        page.mouse.down()
        page.mouse.move(x + 85, y + 45, steps=10)
        page.mouse.up()
        after = _camera(page, mode)
        assert abs(after['x'] - before['x']) + abs(after['y'] - before['y']) > .001, (before, after)
        assert abs(after['scale'] - before['scale']) < .001
    assert errors == []


def test_local_wheel_reaches_graph_inside_plot(redesign_page):
    page, errors = redesign_page
    _load(page, 'local')
    point = page.evaluate('''() => {
      const s=wb.chart.getModel().getSeriesByIndex(0),view=wb.chart.getViewOfSeriesModel(s),data=s.getData();
      const p=s.coordinateSystem.dataToPoint(data.getItemLayout(0)),r=wb.chart.getDom().getBoundingClientRect();
      return {x:r.x+p[0],y:r.y+p[1],options:view._controller._opt};
    }''')
    before = _camera(page, 'local')
    page.mouse.move(point['x'], point['y'])
    page.mouse.wheel(0, -240)
    page.wait_for_function('''scale => wb.chart.getModel().getSeriesByIndex(0).get('zoom') !== scale''', arg=before['scale'], timeout=3000)
    assert _camera(page, 'local')['scale'] > before['scale'], point
    assert abs(_camera(page, 'local')['scale'] - before['scale'] * 1.43332941456) < .001, 'one wheel gesture must not be applied twice'
    assert errors == []


def test_local_wheel_bursts_share_one_render_and_preserve_camera(redesign_page):
    page, errors = redesign_page
    _load(page, 'local')
    bursts = page.evaluate('''async () => {
      const chart=wb.chart, host=chart.getDom(), surface=host.querySelector('canvas'), box=host.getBoundingClientRect();
      const read=()=>{
        const series=chart.getModel().getSeriesByIndex(0), t=series.coordinateSystem.transform;
        return {scale:series.get('zoom'),x:t[4],y:t[5]};
      };
      let updates=0;
      chart.on('graphroam',()=>updates++);
      const results=[];
      for(const events of [
        [[-240,120,80,0],[-8,230,110,1],[60,300,150,0]],
        [[-4000,45,50,0],[-2,80,50,2],[20,180,100,0],[4000,200,250,0],[4000,400,120,0]],
        [[-240,200,100,0],[240,335,170,0]],
      ]){
        const before=read(),expected={...before};
        let captured=0;
        updates=0;
        for(const [delta,x,y,mode] of events){
          const unit=mode===1?16:mode===2?box.height:1;
          const factor=Math.exp(-Math.max(-2000,Math.min(2000,delta*unit))*.0015);
          const scale=Math.min(4,Math.max(.2,expected.scale*factor)),ratio=scale/expected.scale;
          expected.x=x+(expected.x-x)*ratio;
          expected.y=y+(expected.y-y)*ratio;
          expected.scale=scale;
          const event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:delta,deltaMode:mode,
            clientX:box.left+x,clientY:box.top+y});
          surface.dispatchEvent(event);
          captured+=Number(event.defaultPrevented);
        }
        const immediate=updates;
        await new Promise(requestAnimationFrame);
        results.push({before,expected,after:read(),immediate,updates,captured,events:events.length});
      }
      return results;
    }''')
    for burst in bursts:
        assert burst['after'] == pytest.approx(burst['expected'], abs=.001)
        assert burst['captured'] == burst['events']
        assert burst['immediate'] == 0, burst
        assert burst['updates'] == 1, burst
    assert bursts[-1]['after']['scale'] == pytest.approx(bursts[-1]['before']['scale'])
    assert bursts[-1]['after']['x'] != pytest.approx(bursts[-1]['before']['x'])
    assert errors == []


def test_revealing_unchanged_local_graph_skips_resize_and_label_rebuild(redesign_page):
    page, errors = redesign_page
    _load(page, 'local')
    calls = page.evaluate('''async () => {
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const chart=wb.chart,calls={resize:0,setOption:0};
      for(const method of Object.keys(calls)){
        const original=chart[method];
        chart[method]=function(...args){calls[method]++;return original.apply(this,args);};
      }
      GraphWorkbench.reveal();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return calls;
    }''')
    assert calls == {'resize': 0, 'setOption': 0}
    assert errors == []


def test_full_graph_node_drag_moves_only_that_node(redesign_page):
    page, errors = redesign_page
    _load(page, 'full')
    before = page.evaluate('''() => {
      const r=FullGraphView.renderer,p=r.renderPositions,c=r.camera,b=r.canvas.getBoundingClientRect();
      const at=c.worldToScreen(p[0],p[1],r.width,r.height);
      return {positions:Array.from(p),x:b.x+at[0],y:b.y+at[1],camera:{x:c.x,y:c.y,scale:c.scale}};
    }''')
    page.mouse.move(before['x'], before['y'])
    page.mouse.down()
    page.mouse.move(before['x'] + 65, before['y'] + 35, steps=10)
    page.mouse.up()
    after = page.evaluate('''() => {
      const r=FullGraphView.renderer,c=r.camera;
      return {positions:Array.from(r.renderPositions),camera:{x:c.x,y:c.y,scale:c.scale}};
    }''')
    assert after['positions'][:2] != before['positions'][:2]
    assert after['positions'][2:] == before['positions'][2:]
    assert after['camera'] == before['camera']
    assert page.evaluate('''() => {
      const r=FullGraphView.renderer,p=r.renderPositions;
      const at=r.camera.worldToScreen(p[0],p[1],r.width,r.height);
      return r.nearestNode(at[0],at[1]);
    }''') == 0
    assert errors == []


def test_dense_manual_relations_render_beyond_original_world_bounds(redesign_page):
    page, errors = redesign_page
    _load(page, 'full')
    rendered = page.evaluate('''() => {
      const r=FullGraphView.renderer,d=r.dataset;
      d.addEdges(Array.from({length:4001},(_,i)=>({id:'dense-gesture-'+i,subject_id:'gesture-0',object_id:'gesture-1',kind:'relation'})));
      r.setSessionManifest({id:'dense-gestures',entity_count:d.nodeCount,relation_count:d.edgeCount});r.sync();
      r.moveNode(r.beginNodeDrag(0),2,1.5);
      const p=r.renderPositions,at=[p[0]*.9+p[2]*.1,p[1]*.9+p[3]*.1];
      r.camera.x=at[0];r.camera.y=at[1];r.camera.scale=1;r.draw();r.gl.finish();
      const gl=r.gl,pixels=new Uint8Array(9*9*4);
      gl.readPixels(Math.floor(r.canvas.width/2)-4,Math.floor(r.canvas.height/2)-4,9,9,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
      let ink=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i+2]-pixels[i]>4&&pixels[i]<245)ink++;
      return {ink,error:gl.getError(),extent:r.cacheExtent};
    }''')
    assert rendered['extent'][0] > 2 and rendered['extent'][1] > 1.5
    assert rendered['ink'] > 0, rendered
    assert rendered['error'] == 0, rendered
    assert errors == []
