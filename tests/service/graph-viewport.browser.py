"""CPU browser regression for graph mode geometry and click/resize flicker.

Standalone: D:/anaconda/envs/llm_model/python.exe -X utf8 tests/service/graph-viewport.browser.py
Uses the workspace HTTP fixture, without the global PostgreSQL pytest fixture.
"""
import argparse
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ui = runpy.run_path(str(ROOT / 'tests/service/test_workspace_redesign_ui.py'))


def run(record_only=False):
    fixture = ui['redesign_page'].__wrapped__()
    try:
        page, errors = next(fixture)
        nodes = [{'id':f'viewport-{i}', 'kind':'entity', 'type':f'Type{i % 12}',
                  'text':f'知识实体 {i}', 'version':1} for i in range(48)]
        edges = [{'id':f've-{i}', 'kind':'relation', 'subject_id':nodes[i // 4]['id'],
                  'object_id':nodes[i + 1]['id'], 'type':'包含', 'version':1} for i in range(40)]
        manifest = {'id':'viewport', 'entity_count':48, 'relation_count':40,
                    'watermark':1, 'layout_version':'stable-id-v1'}
        page.route('**/records/page', lambda route:route.fulfill(json={'records':nodes, 'total':48}))
        page.route('**/graph-neighborhood', lambda route:route.fulfill(json={'records':nodes + edges}))
        page.route('**/graph-sessions', lambda route:route.fulfill(json=manifest))
        page.route('**/graph-sessions/viewport/page', lambda route:route.fulfill(json={
            'session_id':'viewport', 'watermark':1, 'layout_version':'stable-id-v1',
            'kind':route.request.post_data_json['kind'], 'next_cursor':None, 'entities':nodes,
            'records':edges if route.request.post_data_json['kind']=='connected' else nodes}))
        page.click('[data-tab="search"]')
        page.evaluate('(id)=>window.drawGraph(id,1)', nodes[0]['id'])
        page.click('[data-fg-mode="full"]'); page.click('[data-fg="load"]')
        page.wait_for_function('()=>FullGraphView.loader?.state==="paused"')
        page.click('[data-fg="auto"]')
        page.wait_for_function('()=>FullGraphView.loader.state==="complete"')
        settle = '()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))'
        point=page.evaluate('''()=>{const r=FullGraphView.renderer,box=r.canvas.getBoundingClientRect(),p=r.renderPositions;
          const [x,y]=r.camera.worldToScreen(p[0],p[1],r.width,r.height);return {x:box.x+x,y:box.y+y};}''')
        before_detail=page.locator('#graph-detail').inner_text()
        page.mouse.move(point['x'],point['y'])
        page.wait_for_selector('.fg-hover-card:not([hidden])')
        assert page.locator('.fg-hover-card .fg-hover-id').inner_text()==nodes[0]['id']
        assert page.locator('#graph-detail').inner_text()==before_detail,'hover must not open the detail editor or issue a record lookup'
        page.mouse.move(1,1);page.evaluate(settle)
        geometry = '''()=>{const host=document.querySelector('#graph-stage[data-graph-mode="full"] .full-graph-host')||document.getElementById('graph-canvas');
          const box=host.getBoundingClientRect(),stage=document.getElementById('graph-stage').getBoundingClientRect();
          return {top:box.top,height:box.height,width:box.width,stageHeight:stage.height};}'''
        reports = []
        for width, height in [(1440,950),(1440,728),(1024,768),(768,950),(390,844)]:
            page.set_viewport_size({'width':width, 'height':height})
            modes = {}
            for mode in ['local','full']:
                page.click(f'[data-fg-mode="{mode}"]'); page.evaluate(settle)
                modes[mode] = page.evaluate(geometry)
            if width<701:
                assert page.locator('.search-results-pane').bounding_box()['height']>=130,'mobile evidence must remain readable'
            reports.append({'viewport':[width,height], **modes})
        page.set_viewport_size({'width':1440,'height':950}); page.evaluate(settle)
        resize = page.evaluate('''()=>{
          const r=FullGraphView.renderer; r.draw();r.gl.finish();
          const native=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'width');let clears=0;
          Object.defineProperty(r.canvas,'width',{get(){return native.get.call(this);},set(value){clears++;native.set.call(this,value);},configurable:true});
          r.resize();r.resize();const redundant=clears;delete r.canvas.width;
          r.host.style.width=(r.host.clientWidth-17)+'px';r.resize();
          const pixels=new Uint8Array(r.canvas.width*r.canvas.height*4);r.gl.readPixels(0,0,r.canvas.width,r.canvas.height,r.gl.RGBA,r.gl.UNSIGNED_BYTE,pixels);
          let ink=0;for(let i=0;i<pixels.length;i+=4){const rgb=pixels.subarray(i,i+3);if(pixels[i+3]&&Math.max(...rgb)-Math.min(...rgb)>25&&Math.max(...rgb)>50)ink++;}
          r.host.style.width='';return {redundantClears:redundant,inkAfterResize:ink};}''')
        page.evaluate(settle)
        # Drive real pointer selection, including asynchronous inspector rendering.
        for i in range(3):
            position=page.evaluate('''()=>{const r=FullGraphView.renderer,p=r.renderPositions,box=r.canvas.getBoundingClientRect();
              const [x,y]=r.camera.worldToScreen(p[0],p[1],r.width,r.height);return {x:box.x+x,y:box.y+y};}''')
            page.mouse.click(position['x'],position['y'])
            page.wait_for_selector('#graph-detail .evidence-inspector') if page.locator('#graph-detail .evidence-inspector').count() else page.wait_for_function('()=>!!document.getElementById("graph-detail").textContent.trim()')
            page.evaluate(settle)
        double=page.evaluate('''()=>{const r=FullGraphView.renderer;r.camera.x=r.renderPositions[0];r.camera.y=r.renderPositions[1];r.camera.scale=2;r.requestDraw();return {scale:r.camera.scale};}''')
        page.evaluate(settle)
        position=page.evaluate('''()=>{const r=FullGraphView.renderer,p=r.renderPositions,box=r.canvas.getBoundingClientRect();
          const [x,y]=r.camera.worldToScreen(p[0],p[1],r.width,r.height);return {x:box.x+x,y:box.y+y};}''')
        page.mouse.dblclick(position['x'],position['y']);page.evaluate(settle)
        double['after']=page.evaluate('FullGraphView.renderer.camera.scale')
        page.set_viewport_size({'width':1200,'height':950});page.click('[data-fg-mode="local"]');page.evaluate(settle)
        tools=page.locator('.graph-toolbar').evaluate('(el)=>({width:el.clientWidth,content:el.scrollWidth})')
        assert tools['content']<=tools['width']+1,tools
        page.set_viewport_size({'width':1440,'height':950});page.click('[data-fg-mode="full"]');page.evaluate(settle)
        page.click('#detail-drawer-close');page.evaluate(settle)
        page.evaluate('()=>FullGraphView.renderer.reset()');page.evaluate(settle)
        print(json.dumps({'geometry':reports,'resize':resize,'doubleClick':double,'errors':errors},ensure_ascii=False),flush=True)
        out = ROOT / '.benchmarks/graph-viewport'
        out.mkdir(exist_ok=True)
        page.screenshot(path=str(out / ('before.png' if record_only else 'after.png')),full_page=True)
        if not record_only:
            for row in reports:
                assert abs(row['local']['height']-row['full']['height'])<=1,row
                assert abs(row['local']['top']-row['full']['top'])<=1,row
            assert resize['redundantClears']==0,resize
            assert resize['inkAfterResize']>100,resize
            assert double['after']==double['scale'],'double-clicking an entity must not reset the camera'
            assert not errors,errors
    finally:
        fixture.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--record-only',action='store_true')
    run(parser.parse_args().record_only)
