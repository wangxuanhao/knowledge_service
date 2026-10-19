"""Replay ontology canvas gestures in Edge using production JS/CSS (no database)."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / 'knowledge_service' / 'web'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
HTML = '''<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="/ontology-model-canvas.css">
<style>body{margin:0}.ontology-model{position:relative;width:1000px;height:740px}</style>
<div class="ontology-model" id="host"></div>
<script src="/vendor/cytoscape.min.js"></script>
<script src="/ontology-model-canvas.js"></script>'''
SETUP = '''({count,view}) => {
  const graph={nodes:Array.from({length:count},(_,i)=>({id:'n'+i,label:'本体类 '+i})),edges:[]};
  for(let i=1;i<count;i++)graph.edges.push({id:'e'+i,source:'n'+Math.floor((i-1)/4),target:'n'+i,kind:'inherit'});
  OntologyCanvas.mount(document.getElementById('host'),{select(){},status(){},sendCommand(){throw Error('visual gesture wrote data')}});
  OntologyCanvas.render({graph,view});
  window.cy=OntologyCanvas.__cy();
}'''
MEASURE = '''async () => {
  await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
  let writes=0,lookups=0;
  const attr=SVGElement.prototype.setAttribute, get=cy.getElementById;
  SVGElement.prototype.setAttribute=function(...args){writes++;return attr.apply(this,args)};
  cy.getElementById=function(...args){lookups++;return get.apply(this,args)};
  const node=cy.nodes()[0], start={...node.position()}, pan={...cy.pan()}, frames=[];
  const begin=performance.now();
  for(let i=1;i<=120;i++)node.position({x:start.x+i,y:start.y+i/2});
  const burst=performance.now()-begin;
  await new Promise(requestAnimationFrame);
  const drag={work_ms:burst,svg_writes:writes,node_lookups:lookups};
  writes=0;lookups=0;
  const pbegin=performance.now();
  for(let i=1;i<=120;i++)cy.pan({x:pan.x+i,y:pan.y+i/2});
  const panWork=performance.now()-pbegin;
  await new Promise(requestAnimationFrame);
  const panning={work_ms:panWork,svg_writes:writes,node_lookups:lookups};
  for(let i=0;i<30;i++){
    const t=performance.now();
    node.position({x:start.x+i,y:start.y+i/2});
    await new Promise(requestAnimationFrame);
    frames.push(performance.now()-t);
  }
  SVGElement.prototype.setAttribute=attr;cy.getElementById=get;
  frames.sort((a,b)=>a-b);
  return {drag,pan:panning,programmatic_frames:{p50_ms:frames[15],p95_ms:frames[28]}};
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodes', type=int, default=160)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--probe', action='store_true', help='Compare individual rendering costs on the same graph')
    args = parser.parse_args()
    results = {}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=EDGE, headless=True)
        for view in ('graph', 'hierarchy'):
            page = browser.new_page(viewport={'width': 1100, 'height': 850})

            def respond(route):
                path = route.request.url.split('canvas.test/', 1)[1].split('?')[0]
                if not path:
                    route.fulfill(body=HTML, content_type='text/html')
                else:
                    file = WEB / path
                    route.fulfill(body=file.read_bytes(), content_type=(
                        'text/css' if file.suffix == '.css' else 'text/javascript'))

            page.route('http://canvas.test/**', respond)
            page.goto('http://canvas.test/')
            page.evaluate(SETUP, {'count': args.nodes, 'view': view})
            results[view] = page.evaluate(MEASURE)
            # Keep the renderer's real grabbed-node state: programmatic position
            # updates alone redraw the entire graph and are not drag-frame FPS.
            page.evaluate('''() => {
              const nodes=cy.nodes(), z=cy.zoom();
              const p={x:Math.min(...nodes.map(n=>n.position('x')))-400,y:Math.min(...nodes.map(n=>n.position('y')))-200};
              nodes[0].position(p);cy.pan({x:450-p.x*z,y:300-p.y*z});
            }''')
            page.mouse.move(450, 300)
            page.mouse.down()
            native = page.evaluate('''async () => {
              const times=[]; let drags=0; const onDrag=()=>drags++;
              cy.on('drag',onDrag);
              for(let i=1;i<=45;i++){
                const start=performance.now();
                document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,buttons:1,clientX:450+i,clientY:300+i/2}));
                await new Promise(requestAnimationFrame);
                if(i>10)times.push(performance.now()-start);
              }
              cy.off('drag',onDrag);times.sort((a,b)=>a-b);
              return {events:drags,p50_ms:times[17],p95_ms:times[32]};
            }''')
            page.mouse.move(495, 322.5)
            page.mouse.up()
            assert native['events'] > 30, 'frame replay must use real renderer drag state'
            results[view]['grabbed_drag_frames'] = native
            if args.probe and view == 'graph':
                base_style = page.evaluate('() => cy.style().json()')
                probe_position = page.evaluate('() => ({...cy.nodes()[0].position()})')
                probes = {}
                for name, override in (
                    ('baseline', {}),
                    ('solid_nodes', {'background-fill': 'solid'}),
                    ('readable_labels_only', {'min-zoomed-font-size': 8}),
                ):
                    page.evaluate('''({base,override,position}) => {
                      cy.style().fromJson(base).selector('node').style(override).update();
                      const nodes=cy.nodes(),z=cy.zoom();
                      const p=position;
                      nodes[0].position(p);cy.pan({x:450-p.x*z,y:300-p.y*z});
                    }''', {'base': base_style, 'override': override, 'position': probe_position})
                    page.mouse.move(450, 300)
                    page.mouse.down()
                    probes[name] = page.evaluate('''async () => {
                      const times=[];
                      for(let i=1;i<=60;i++){
                        const t=performance.now();
                        document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,buttons:1,clientX:450+i,clientY:300+i/2}));
                        await new Promise(requestAnimationFrame);
                        if(i>20)times.push(performance.now()-t);
                      }
                      times.sort((a,b)=>a-b);return {p50_ms:times[20],p95_ms:times[38]};
                    }''')
                    page.mouse.move(510, 330)
                    page.mouse.up()
                results[view]['render_probes'] = probes
                page.evaluate('(base) => cy.style().fromJson(base).update()', base_style)
                pan_probes = {}
                probe_pan = page.evaluate('() => ({...cy.pan()})')
                for texture in (False, True):
                    page.evaluate('''({texture,pan}) => {cy.renderer().textureOnViewport=texture;cy.pan(pan)}''',
                                  {'texture': texture, 'pan': probe_pan})
                    page.mouse.move(5, 5)
                    page.mouse.down()
                    pan_probes[str(texture)] = page.evaluate('''async () => {
                      const times=[];let pans=0;const onPan=()=>pans++;cy.on('pan',onPan);
                      for(let i=1;i<=60;i++){
                        const t=performance.now();
                        document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,buttons:1,clientX:5+i,clientY:5+i/2}));
                        await new Promise(requestAnimationFrame);
                        if(i>20)times.push(performance.now()-t);
                      }
                      cy.off('pan',onPan);times.sort((a,b)=>a-b);return {events:pans,p50_ms:times[20],p95_ms:times[38]};
                    }''')
                    page.mouse.move(65, 35)
                    page.mouse.up()
                results[view]['pan_texture_probes'] = pan_probes
                page.evaluate('() => {cy.renderer().textureOnViewport=false}')
            # Drive a real pointer through the renderer as well as model events.
            page.evaluate('''() => {
              cy.zoom(1);const nodes=cy.nodes();
              const p={x:Math.min(...nodes.map(n=>n.position('x')))-400,y:Math.min(...nodes.map(n=>n.position('y')))-200};
              nodes[0].position(p);cy.pan({x:450-p.x,y:300-p.y});
            }''')
            before = page.evaluate('() => ({...cy.nodes()[0].position()})')
            page.mouse.move(450, 300)
            page.mouse.down()
            page.mouse.move(530, 345, steps=20)
            page.mouse.up()
            after = page.evaluate('() => ({...cy.nodes()[0].position()})')
            assert after['x'] - before['x'] > 60, 'real pointer must drag the node'
            results[view]['pointer_drag_verified'] = True
            pan_before = page.evaluate('() => ({...cy.pan()})')
            page.mouse.move(5, 5)
            page.mouse.down()
            page.mouse.move(85, 50, steps=20)
            page.mouse.up()
            pan_after = page.evaluate('() => ({...cy.pan()})')
            assert pan_after['x'] - pan_before['x'] > 60, 'real pointer must pan the canvas'
            zoom_before = page.evaluate('() => cy.zoom()')
            page.mouse.move(450, 350)
            page.mouse.wheel(0, -120)
            page.wait_for_function('(z) => cy.zoom() > z', arg=zoom_before)
            page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
            if view == 'hierarchy':
                aligned = page.evaluate('''() => {
                  const rect=document.querySelector('.om-canvas-band').getBoundingClientRect();
                  const p=cy.nodes()[0].renderedPosition();
                  return p.x>rect.left && p.x<rect.right && p.y>rect.top && p.y<rect.bottom;
                }''')
                assert aligned, 'band must track dragged node after real pan and wheel zoom'
                page.evaluate('() => cy.zoom(0.25)')
                page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
                label_size = page.evaluate('''() => parseFloat(getComputedStyle(document.querySelector('.om-canvas-band-label')).fontSize)*cy.zoom()''')
                assert abs(label_size - 8) < 0.1, 'group zoom must preserve readable label size despite CSS'
            results[view]['pointer_pan_zoom_verified'] = True
            if args.screenshot and view == 'hierarchy':
                page.screenshot(path=str(args.screenshot))
            page.close()
        browser.close()
    print(json.dumps(results, indent=2))
    if args.output:
        args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    if args.check:
        hierarchy = results['hierarchy']
        assert hierarchy['drag']['svg_writes'] <= args.nodes * 2, 'drag rewrites every hierarchy band per event'
        assert hierarchy['pan']['node_lookups'] == 0, 'panning must not rescan node geometry'


if __name__ == '__main__':
    main()
