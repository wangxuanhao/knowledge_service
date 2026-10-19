"""Measure the production graph interaction module in a real Chromium canvas.

No database or user session is used. Replays mouse bursts through ZRender and
reports main-thread work, graph updates and viewport dispatches separately.
"""
import argparse
import json
import statistics
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / 'knowledge_service' / 'web'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
HTML = '''<!doctype html><meta charset="utf-8"><style>
body{margin:0;font:13px sans-serif}.search-graph-workspace{display:flex}
#graph-canvas{width:1000px;height:700px}.inspector-rail{width:260px}
</style><div class="graph-toolbar"><button id="graph-reset-view">Fit</button></div>
<div class="search-graph-workspace"><div id="graph-canvas"></div>
<section class="inspector-rail"><button id="detail-drawer-close">Close</button><div id="graph-detail"></div></section></div>
<script src="/vendor/echarts.min.js"></script>
<script src="/assets/graph-interaction.js"></script>
<script src="/assets/graph-paths.js"></script>
<script src="/assets/graph-workbench.js"></script>'''
SETUP = '''count => {
  const graph={nodes:Array.from({length:count},(_,i)=>({id:'n'+i,text:'知识实体 '+i,kind:'entity'})),edges:[]};
  for(let i=0;i<count;i++)for(let d=1;d<=3;d++)graph.edges.push({id:'e'+i+'-'+d,kind:'relation',subject_id:'n'+i,object_id:'n'+((i+d)%count),type:'关联'});
  const chart=echarts.init(document.getElementById('graph-canvas'));
  const nodes=GraphWorkbench.positions(graph,'benchmark');
  chart.setOption({animation:false,tooltip:{},series:[{type:'graph',layout:'none',roam:'scale',draggable:true,
    ...GraphWorkbench.bounds(nodes,chart.getWidth(),chart.getHeight()),zoom:1,center:null,
    label:{show:true,fontSize:11},edgeSymbol:['none','arrow'],edgeLabel:{show:false},
    emphasis:{focus:'adjacency'},lineStyle:{opacity:.35,curveness:.12},
    data:nodes.map(n=>({...n,name:n.text,symbolSize:22})),
    links:graph.edges.map(e=>({...e,source:e.subject_id,target:e.object_id}))}]});
  const counters={dispatches:0,updates:0};
  const update=chart.setOption.bind(chart);
  // Native controllers dispatch via ExtensionAPI, bypassing chart.dispatchAction.
  chart.on('graphroam',()=>{counters.dispatches++});
  chart.setOption=(...args)=>{counters.updates++;return update(...args)};
  window.graphPerf={chart,graph,counters};
  GraphWorkbench.mount(chart,graph,'benchmark',()=>{},{});
}'''
MEASURE = '''async mode => {
  const {chart,graph,counters}=graphPerf;
  await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
  counters.dispatches=0;counters.updates=0;
  const begin=performance.now();
  if(mode==='pan'){
    const zr=chart.getZr(),event=(x,y)=>({offsetX:x,offsetY:y,target:null,event:{zrX:x,zrY:y,preventDefault(){},stopPropagation(){}}});
    zr.trigger('mousedown',event(200,200));
    for(let i=1;i<=120;i++)zr.trigger('mousemove',event(200+i/2,200+i/4));
    zr.trigger('mouseup',event(260,230));
  }else if(mode==='mount')GraphWorkbench.mount(chart,graph,'benchmark',()=>{},{});
  else if(mode==='zoom')for(let i=0;i<12;i++){
    const factor=i%2?1/1.02:1.02;
    if(GraphWorkbench.zoom)GraphWorkbench.zoom(chart,factor);
    else {const current=Number(chart.getOption().series[0].zoom)||1;chart.setOption({series:[{zoom:Math.min(4,Math.max(.2,current*factor))}]});}
  }
  else if(mode==='relayout')document.getElementById('graph-relayout').click();
  const workMs=performance.now()-begin;
  await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
  return {work_ms:workMs,settled_ms:performance.now()-begin,...counters};
}'''


@contextmanager
def graph_page(nodes):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=EDGE)
        page = browser.new_page(viewport={'width': 1300, 'height': 850})

        def respond(route):
            path = route.request.url.split('graph-perf.test', 1)[1]
            if path == '/':
                route.fulfill(body=HTML, content_type='text/html; charset=utf-8')
            else:
                file = WEB / path.removeprefix('/assets/').removeprefix('/vendor/')
                if path.startswith('/vendor/'):
                    file = WEB / 'vendor' / path.rsplit('/', 1)[-1]
                route.fulfill(body=file.read_bytes(), content_type='text/javascript')

        page.route('http://graph-perf.test/**', respond)
        page.goto('http://graph-perf.test/')
        page.evaluate(SETUP, nodes)
        try:
            yield page
        finally:
            browser.close()


def run(nodes=500, repeats=5):
    with graph_page(nodes) as page:
        results = {}
        for mode in ['pan', 'mount', 'zoom', 'relayout']:
            samples = [page.evaluate(MEASURE, mode) for _ in range(repeats)]
            results[mode] = {key: round(statistics.median(x[key] for x in samples), 2)
                             for key in samples[0]}
        return {'nodes': nodes, 'edges': nodes * 3, 'repeats': repeats, 'metrics': results}


def verify_interactions():
    with graph_page(20) as page:
        def settle():
            page.evaluate('async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}')

        def points():
            return page.evaluate('''()=>{const s=graphPerf.chart.getModel().getSeriesByIndex(0),d=s.getData();
              return Array.from({length:d.count()},(_,i)=>({id:d.getRawDataItem(i).id,p:d.getItemGraphicEl(i).transformCoordToGlobal(0,0)}));}''')

        settle()
        original = page.evaluate('JSON.stringify(graphPerf.graph)')
        before = points()
        outer = min(before, key=lambda item: item['p'][0])
        box = page.locator('#graph-canvas').bounding_box()
        x, y = box['x'] + outer['p'][0], box['y'] + outer['p'][1]
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x - 30, y + 10, steps=8)
        page.mouse.up()
        settle()
        dragged = points()
        by_id = {item['id']: item['p'] for item in dragged}
        assert abs(by_id[outer['id']][0] - outer['p'][0] + 30) < 2, 'node did not retain its drag position'
        for item in before:
            if item['id'] != outer['id']:
                assert max(abs(a - b) for a, b in zip(item['p'], by_id[item['id']])) < 2, 'dragging an outer node shifted its neighbors'
        assert page.evaluate('!GraphWorkbench.canClick()'), 'a drag must not open a node detail'

        page.evaluate('graphPerf.counters.updates=0')
        page.mouse.move(box['x'] + 12, box['y'] + 12)
        page.mouse.down()
        page.mouse.move(box['x'] + 62, box['y'] + 37, steps=8)
        page.mouse.up()
        settle()
        panned = points()
        for old, new in zip(dragged, panned):
            assert abs(new['p'][0] - old['p'][0] - 50) < 2
            assert abs(new['p'][1] - old['p'][1] - 25) < 2
        assert page.evaluate('graphPerf.counters.updates') == 0, 'pan rebuilt graph data'

        page.evaluate('GraphWorkbench.zoom(graphPerf.chart,1.2)')
        settle()
        zoomed = points()
        distance = lambda values: sum((a - b) ** 2 for a, b in zip(values[0]['p'], values[1]['p'])) ** .5
        assert abs(distance(zoomed) / distance(panned) - 1.2) < .01, 'zoom did not change the actual viewport'
        assert page.evaluate('graphPerf.counters.updates') == 0
        page.locator('#graph-path-toggle').click()
        page.select_option('#graph-path-from', 'n0')
        page.select_option('#graph-path-to', 'n3')
        page.get_by_role('button', name='寻找路径', exact=True).click()
        assert page.locator('[data-path-edge="e0-3"]').count() == 1
        page.get_by_role('button', name='清除路径', exact=True).click()
        assert page.evaluate('graphPerf.chart.getOption().series[0].data.every(n=>(n.itemStyle?.opacity??1)===1)'), 'path clearing left dimmed nodes'
        # The application window listener resizes ECharts before ResizeObserver
        # runs. Bounds must still be refitted when that has already happened.
        page.evaluate("document.getElementById('graph-canvas').style.cssText='width:400px;height:400px';graphPerf.chart.resize()")
        settle()
        assert all(0 <= axis <= 400 for item in points() for axis in item['p']), 'window resize kept stale pixel bounds and clipped nodes'
        assert page.evaluate('JSON.stringify(graphPerf.graph)') == original, 'view operations modified facts'
        return {'outer_node_drag_stable': True, 'pan_moves_viewport_only': True,
                'zoom_changes_viewport': True, 'path_clears_highlight': True,
                'resize_refits_after_external_resize': True, 'facts_unchanged': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodes', type=int, default=500)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='Assert pan is frame-batched and clean mounts do not redraw')
    parser.add_argument('--verify', action='store_true', help='Also use a real mouse to verify drag, pan, zoom and path clearing')
    args = parser.parse_args()
    result = run(args.nodes, args.repeats)
    if args.verify:
        result['interaction_checks'] = verify_interactions()
    report = json.dumps(result, indent=2, ensure_ascii=False)
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding='utf-8')
    if args.check:
        assert result['metrics']['pan']['dispatches'] <= 2, 'pan re-renders for every mouse event'
        assert result['metrics']['pan']['updates'] == 0, 'panning commits an unnecessary full graph update'
        assert result['metrics']['mount']['updates'] == 0, 'mount redraws a graph without a path highlight'
        assert result['metrics']['zoom']['updates'] == 0, 'zoom performs a full graph update'
        assert result['metrics']['relayout']['updates'] <= 1, 'relayout redraws the same graph more than once'
