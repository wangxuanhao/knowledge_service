"""Real browser checks using static assets and intercepted API fixtures, no database."""
import json
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'knowledge_service' / 'web'
ARTIFACTS = ROOT / '.benchmarks' / 'graph-maintenance'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
XSD = 'http://www.w3.org/2001/XMLSchema#'
nodes = [dict(id=i, text=name, type='Thing', kind='entity', ontology_id='o1',
              version=1, version_id=i+':1', metadata={})
         for i, name in [('a', '平台 A'), ('b', '规则 R'), ('c', '材料 M'), ('d', '其他规则')]]
edges = [dict(id=i, text=label, type=label, type_label=label, kind='relation',
              subject_id=a, object_id=b, version=1, ontology_id='o1', metadata={})
         for i, a, b, label in [('ab', 'a', 'b', '适用规则'), ('bc', 'b', 'c', '要求材料')]]
attribute = dict(id='value', kind='attribute', text='审核天数', subject_id='b', type='days',
                 value=3, datatype=XSD+'integer', ontology_id='o1', version=1,
                 version_id='value:1', metadata={})
summary = dict(classes=[dict(id='Thing', name='对象', label_zh='对象')],
               relations=[dict(id=e['type'], name=e['type']) for e in edges],
               attributes=[dict(id='days', name='审核天数', label_zh='审核天数', domain=['Thing'], range=[XSD+'integer'])])
ontology = dict(id='o1', version=1, turtle='', summary=summary, metadata={})


class Static(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = urlparse(path).path
        if path.startswith('/assets/'):
            path = path[len('/assets/'):]
        else:
            path = path.lstrip('/') or 'index.html'
        return str(WEB / path)

    def log_message(self, *_):
        pass


def main():
    html = (WEB/'index.html').read_text(encoding='utf-8')
    for resource in re.findall(r'(?:src|href)="(/(?:assets|vendor)/[^"?]+)', html):
        relative = resource.removeprefix('/assets/').lstrip('/')
        assert (WEB/relative).is_file(), ('missing static asset', resource)
    assert html.index('/assets/record-attribute-editor.js') < html.index('/assets/record-dialog.js')
    assert html.index('/assets/graph-workbench.js') < html.index('/assets/workspace.js')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    writes, errors = [], []

    def fixture(route):
        path = urlparse(route.request.url).path
        payload = {}
        if path == '/api/health':
            payload = dict(auth_enabled=False, status='ok')
        elif path == '/api/auth/me':
            payload = dict(user=dict(username='fixture', role='admin', display_name='交互验收'))
        elif path == '/api/projects':
            payload = dict(projects=[dict(id='p', name='交互验收', metadata={'ontology_mode': 'ontology'})])
        elif path.endswith('/subgraph'):
            payload = dict(nodes=nodes, edges=edges)
        elif path.endswith('/records/query'):
            payload = dict(records=nodes+[attribute], total=len(nodes)+1)
        elif path.endswith('/ontologies'):
            payload = dict(versions=[ontology])
        elif path.endswith('/ontology'):
            payload = ontology
        elif path.endswith('/records/value') and route.request.method == 'PUT':
            body = route.request.post_data_json
            assert body['expected_version'] == attribute['version']
            attribute.update(body['record'])
            attribute.update(version=attribute['version']+1, version_id='value:2')
            writes.append(body)
            payload = attribute
        elif path.endswith('/entities'):
            payload = dict(entities=nodes)
        elif path.endswith('/metadata/facets'):
            payload = dict(facets=[])
        else:
            payload = dict(records=[], entities=nodes, events=[], versions=[], jobs=[], operations=[],
                           groups=[], assertions=[], terms=[], drafts=[], proposals=[], items=[],
                           documents=[], total=0, pending_structure=0, unpublished_candidate_count=0)
        route.fulfill(json=payload)

    executable = next(str(p) for p in [Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                                      Path(r'C:/Program Files/Google/Chrome/Application/chrome.exe')] if p.exists())
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
            page.route('**/api/**', fixture)
            page.add_init_script("localStorage.setItem('kg_token','fixture');")
            page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
            page.select_option('#project', 'p')
            page.evaluate("showTab('search')")
            page.evaluate('window.drawGraph()')
            page.wait_for_function('wb.chart?.getOption()?.series?.[0]?.data?.length===4')
            assert page.evaluate("wb.chart.getOption().series[0].draggable")
            assert page.evaluate("wb.chart.getOption().series[0].layout") == 'none'
            point = page.evaluate("""() => {
              const s=wb.chart.getModel().getSeriesByIndex(0),d=s.getData();
              const p=s.coordinateSystem.dataToPoint(d.getItemLayout(0));
              const r=document.getElementById('graph-canvas').getBoundingClientRect();
              return [r.left+p[0],r.top+p[1]];
            }""")
            before = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).getData().getItemLayout(0).slice()')
            page.mouse.move(*point)
            page.mouse.down()
            page.mouse.move(point[0]+60, point[1]+30, steps=12)
            before_release = page.evaluate("""() => {const s=wb.chart.getModel().getSeriesByIndex(0);return [0,1].map(i=>s.coordinateSystem.dataToPoint(s.getData().getItemLayout(i)))}""")
            page.mouse.up()
            after_release = page.evaluate("""() => {const s=wb.chart.getModel().getSeriesByIndex(0);return [0,1].map(i=>s.coordinateSystem.dataToPoint(s.getData().getItemLayout(i)))}""")
            assert all(abs(a-b)<.1 for left,right in zip(before_release,after_release) for a,b in zip(left,right)), ('drag release snapped nodes',before_release,after_release)
            page.wait_for_timeout(350)
            after = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).getData().getItemLayout(0).slice()')
            assert before != after, ('node did not drag', before, after, errors)
            assert page.locator('#graph-detail').inner_text() == '', 'drag opened inspector'
            before_refresh = page.evaluate("""() => {const s=wb.chart.getModel().getSeriesByIndex(0);return [0,1].map(i=>s.coordinateSystem.dataToPoint(s.getData().getItemLayout(i)))}""")
            page.evaluate('window.drawGraph()')
            after_refresh = page.evaluate("""() => {const s=wb.chart.getModel().getSeriesByIndex(0);return [0,1].map(i=>s.coordinateSystem.dataToPoint(s.getData().getItemLayout(i)))}""")
            assert all(abs(a-b)<.1 for left,right in zip(before_refresh,after_refresh) for a,b in zip(left,right)), ('refresh moved nodes',before_refresh,after_refresh)
            # Fit must preserve the dragged raw coordinate, relayout must change it.
            page.click('#graph-reset-view')
            fitted = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).getData().getItemLayout(0).slice()')
            assert all(abs(a-b)<.01 for a,b in zip(after,fitted)), (after,fitted)
            page.click('#graph-relayout')
            reset = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).getData().getItemLayout(0).slice()')
            assert reset != fitted
            canvas = page.locator('#graph-canvas').bounding_box()
            center_before = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).coordinateSystem.getCenter().slice()')
            page.mouse.move(canvas['x']+8, canvas['y']+8)
            page.mouse.down()
            page.mouse.move(canvas['x']+48, canvas['y']+28, steps=8)
            page.mouse.up()
            center_after = page.evaluate('wb.chart.getModel().getSeriesByIndex(0).coordinateSystem.getCenter().slice()')
            assert center_before != center_after, 'blank canvas did not pan'
            zoom = page.evaluate('wb.chart.getOption().series[0].zoom')
            page.click('#graph-zoom-in')
            assert page.evaluate('wb.chart.getOption().series[0].zoom') > zoom
            zoomed = page.evaluate('wb.chart.getOption().series[0].zoom')
            page.evaluate('window.drawGraph()')
            assert page.evaluate('wb.chart.getOption().series[0].zoom') == zoomed, 'refresh lost zoom'
            page.mouse.move(canvas['x']+canvas['width']/2, canvas['y']+canvas['height']/2)
            page.mouse.wheel(0, -200)
            page.wait_for_timeout(150)
            assert page.evaluate('wb.chart.getOption().series[0].zoom') != zoomed, 'wheel did not zoom'
            page.click('#graph-reset-view')
            page.locator('#graph-path-tools summary').click()
            page.wait_for_timeout(100)
            assert page.evaluate("wb.chart.getHeight()===document.getElementById('graph-canvas').clientHeight"), 'canvas did not resize for path panel'
            page.select_option('#graph-path-from', 'a')
            page.select_option('#graph-path-to', 'c')
            page.get_by_role('button', name='寻找路径', exact=True).click()
            assert '2 跳' in page.locator('#graph-path-result').inner_text()
            assert page.evaluate("wb.chart.getOption().series[0].links.every(e=>e.lineStyle.width===3)")
            page.locator('[data-path-edge="ab"]').click()
            assert '适用规则' in page.locator('#graph-detail').inner_text()
            page.screenshot(path=str(ARTIFACTS/'main-graph.png'), full_page=True)
            scale = page.evaluate("""() => {const c=wb.chart.getModel().getSeriesByIndex(0).coordinateSystem;return [c.scaleX,c.scaleY]}""")
            assert abs(scale[0]-scale[1])<.001, ('circle nodes distorted', scale)
            # A same-ID endpoint revision invalidates old path results.
            edges[0]['object_id'] = 'd'
            page.evaluate('window.drawGraph()')
            assert '重新选择路径' in page.locator('#graph-path-result').inner_text()
            # Actual attribute modal and actual PUT orchestration, intercepted server only.
            page.evaluate('(record)=>editRecord(record)', attribute)
            page.wait_for_selector('#save-structured-record')
            page.screenshot(path=str(ARTIFACTS/'attribute-form.png'), full_page=True)
            value_input = page.locator('#structured-value')
            value_input.fill('7')
            page.click('#save-structured-record')
            page.wait_for_function("document.getElementById('record-dialog-message').textContent.includes('已保存')")
            assert writes[-1]['record']['value'] == 7
            assert writes[-1]['record']['ontology_id'] == 'o1'
            page.click('#close-record-dialog')
            # Isolate the actual ontology canvas in a visible host: no backend commands.
            page.evaluate("""() => {
              OntologyCanvas.destroy();
              const host=document.createElement('div');host.id='ontology-fixture';host.className='ontology-model';
              host.style.cssText='position:fixed;inset:40px;z-index:9999;background:white';
              document.body.append(host);window.__ontologySelected=null;
              OntologyCanvas.mount(host,{select:id=>window.__ontologySelected=id,status:()=>{}});
              window.__ontologyGraph={nodes:[{id:'A',label:'规则'},{id:'B',label:'材料'}],edges:[{id:'link',source:'A',target:'B',label:'要求',kind:'relation'}]};
              OntologyCanvas.render({graph:window.__ontologyGraph,view:'graph'});
            }""")
            page.wait_for_timeout(300)
            pos = page.evaluate("""() => {const c=OntologyCanvas.__cy(),p=c.$id('A').renderedPosition(),r=document.querySelector('#ontology-fixture .om-canvas-stage').getBoundingClientRect();return [r.left+p.x,r.top+p.y]}""")
            old = page.evaluate("OntologyCanvas.__cy().$id('A').position()")
            page.mouse.move(*pos)
            page.mouse.down()
            page.mouse.move(pos[0]+70,pos[1]+40,steps=10)
            page.mouse.up()
            moved = page.evaluate("OntologyCanvas.__cy().$id('A').position()")
            assert old != moved, 'ontology node did not drag'
            assert page.evaluate('window.__ontologySelected') is None
            page.evaluate("""() => {window.__ontologyGraph.nodes.push({id:'C',label:'平台'});OntologyCanvas.render({graph:window.__ontologyGraph,view:'graph'})}""")
            assert page.evaluate("OntologyCanvas.__cy().$id('A').position()") == moved
            page.screenshot(path=str(ARTIFACTS/'ontology-canvas.png'), full_page=True)
            page.evaluate("OntologyCanvas.destroy();document.getElementById('ontology-fixture').remove();window.clearGraphWorkspace()")
            assert page.locator('#graph-path-tools').count() == 0
            page.wait_for_timeout(150)
            assert not errors, errors
            result=dict(checks=['actual node drag', 'drag release preserves screen coordinates', 'drag click suppression', 'fit preserves coordinates',
                                'explicit relayout', 'blank pan', 'zoom button', '2-hop path and edge inspector',
                                'same graph refresh preserves zoom', 'refresh preserves pixel transform', 'wheel zoom', 'round node aspect', 'path panel resizes canvas',
                                'same-ID endpoint invalidation', 'typed attribute save', 'ontology actual drag',
                                'ontology drag suppression and incremental layout', 'workspace clear removes paths'], errors=errors,
                        fixture_writes=len(writes))
            (ARTIFACTS/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
