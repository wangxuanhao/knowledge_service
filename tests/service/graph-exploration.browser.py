"""Standalone software browser checks; no PostgreSQL fixture or business store."""
import json
import runpy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
fixture=runpy.run_path(str(ROOT/'tests/service/test_workspace_redesign_ui.py'))['redesign_page'].__wrapped__
OUT=ROOT/'.benchmarks/graph-exploration'
OUT.mkdir(parents=True,exist_ok=True)


def run():
    session=fixture()
    page=None
    try:
        page,errors=next(session)
        page.set_default_timeout(10000)
        print('phase: fixture-ready',flush=True)
        entities={key:{'id':key,'kind':'entity','type':'Type'+str(i%2),'text':name,'version':1} for i,(key,name) in enumerate([('a','平台'),('b','规则'),('c','孤立实体'),('x','共享实体'),('y','后代实体'),('z','独有实体')])}
        def edge(key,a,b):return {'id':key,'kind':'relation','subject_id':a,'object_id':b,'type':'关联','version':1}
        rows={
            'root':[entities[k] for k in 'abc']+[edge('ab','a','b')],
            'a':[entities[k] for k in 'ax']+[edge('ax','a','x')],
            'b':[entities[k] for k in 'bxz']+[edge('bx','b','x'),edge('bz','b','z')],
            'x':[entities[k] for k in 'xy']+[edge('xy','x','y')],
            'y':[entities[k] for k in 'yz']+[edge('yz','y','z')],
        }
        requests=[]
        def neighborhood(route):
            seed=route.request.post_data_json['seeds'][0];requests.append(seed)
            route.fulfill(json={'records':rows['root'] if len(requests)==1 else rows.get(seed,rows['root']),'truncated':False})
        page.route('**/graph-neighborhood',neighborhood)
        page.route('**/records/page',lambda route:route.fulfill(json={'records':[entities[k] for k in route.request.post_data_json.get('record_ids',[]) if k in entities],'total':len(entities)}))
        page.click('[data-tab="search"]')
        page.evaluate("()=>window.drawGraph('a',1)")
        assert page.locator('#graph-neighbors').count()==1,'one shared neighbor menu must exist'
        page.evaluate("()=>window.selectEntityDetail('a')")
        page.click('#graph-neighbors')
        page.wait_for_selector('#graph-explorer:not([hidden])')
        page.wait_for_function("()=>document.querySelector('.graph-neighbor-row')?.textContent.includes('规则')")
        clipped=page.locator('.graph-toolbar button').evaluate_all('(buttons)=>buttons.filter(b=>b.offsetParent&&b.scrollWidth>b.clientWidth+2).map(b=>b.id)')
        assert not clipped,('toolbar labels must remain fully visible',clipped)
        before=page.locator('#graph-canvas').bounding_box()
        page.click('#graph-path-toggle') if page.locator('#graph-path-toggle').count() else page.get_by_role('button',name='路径循迹',exact=True).click()
        page.select_option('#graph-path-from','a');page.select_option('#graph-path-to','b');page.get_by_role('button',name='寻找路径',exact=True).click()
        page.wait_for_function("()=>document.getElementById('graph-path-result').textContent.includes('最短路径')")
        path_opacity=page.evaluate("()=>wb.chart.getModel().getSeriesByIndex(0).option.data.find(n=>n.id==='c').itemStyle.opacity??1")
        page.click('[data-explorer="focus"]')
        page.wait_for_function("()=>wb.chart.getModel().getSeriesByIndex(0).option.data.find(n=>n.id==='c').itemStyle.opacity<.3")
        page.click('[data-explorer="focus"]')
        page.wait_for_function("expected=>(wb.chart.getModel().getSeriesByIndex(0).option.data.find(n=>n.id==='c').itemStyle.opacity??1)===expected",arg=path_opacity)
        print('phase: local-focus-and-path',flush=True)
        page.click('[data-explorer="expand"]')
        page.wait_for_function("()=>wb.nodes.has('x')")
        page.evaluate("()=>window.selectEntityDetail('b')")
        page.click('[data-explorer="expand"]')
        page.wait_for_function("()=>wb.nodes.has('z')")
        page.evaluate("()=>window.selectEntityDetail('x')")
        page.click('[data-explorer="expand"]')
        page.wait_for_function("()=>wb.nodes.has('y')")
        assert '最短路径' in page.locator('#graph-path-result').inner_text(),'valid path must survive branch redraw'
        page.evaluate('()=>GraphWorkbench.fit(wb.chart)')
        page.screenshot(path=str(OUT/'local-exploration-expanded.png'))
        page.evaluate("()=>window.selectEntityDetail('a')")
        page.click('[data-explorer="collapse"]')
        assert page.evaluate("()=>wb.nodes.has('x')&&wb.nodes.has('y')"),'shared descendant must survive another owner'
        page.evaluate("()=>window.selectEntityDetail('b')")
        page.click('[data-explorer="collapse"]')
        page.wait_for_function("()=>!wb.nodes.has('x')&&!wb.nodes.has('y')&&!wb.nodes.has('z')")
        assert page.evaluate("()=>['a','b','c'].every(id=>wb.nodes.has(id))")
        assert '最短路径' in page.locator('#graph-path-result').inner_text(),'base path must survive collapsing unrelated branches'
        page.evaluate("()=>window.selectEntityDetail('a')")
        pending=[]
        def hold_neighborhood(route):pending.append(route)
        page.route('**/graph-neighborhood',hold_neighborhood)
        page.click('[data-explorer="expand"]');page.wait_for_function('()=>GraphExplorer.snapshot().busy')
        page.click('[data-explorer="collapse"]')
        assert pending,'expansion should have reached the bounded endpoint'
        pending.pop().fulfill(json={'records':rows['a'],'truncated':False})
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert not page.evaluate("()=>wb.nodes.has('x')"),'late response must not revive a collapsed branch'
        page.unroute('**/graph-neighborhood',hold_neighborhood)
        page.evaluate("()=>window.openKnowledgeRecord({id:'y',kind:'entity'})")
        assert page.evaluate('()=>GraphExplorer.snapshot().selected===null'),'outside detail must not become a displayed anchor'
        page.click('#graph-expand');page.wait_for_function("()=>wb.nodes.has('y')&&!wb.nodes.has('a')")
        assert requests[-1]=='y','existing expand must fetch a fresh outside anchor'
        page.click('[data-explorer="close"]')
        after=page.locator('#graph-canvas').bounding_box()
        assert abs(before['height']-after['height'])<1 and abs(before['y']-after['y'])<1,'opening or collapsing must not move canvas'
        page.screenshot(path=str(OUT/'local-exploration.png'))
        print('phase: local-branches-and-outside-picker',flush=True)
        all_edges=[edge('ab','a','b'),edge('ax','a','x'),edge('bx','b','x'),edge('bz','b','z'),edge('xy','x','y')]
        manifest={'id':'exploration','entity_count':6,'relation_count':5,'watermark':1,'layout_version':'stable-id-v1'}
        page.route('**/graph-sessions',lambda route:route.fulfill(json=manifest))
        page.route('**/graph-sessions/exploration/page',lambda route:route.fulfill(json={
            'session_id':'exploration','watermark':1,'layout_version':'stable-id-v1','kind':route.request.post_data_json['kind'],
            'next_cursor':None,'entities':list(entities.values()),'records':all_edges if route.request.post_data_json['kind']=='connected' else list(entities.values())}))
        page.click('#graph-neighbors');page.route('**/graph-neighborhood',hold_neighborhood)
        page.click('[data-explorer="expand"]');page.wait_for_function('()=>GraphExplorer.snapshot().busy')
        page.click('[data-fg-mode="full"]')
        assert pending;pending.pop().fulfill(json={'records':rows['y'],'truncated':False})
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert page.evaluate('()=>GraphExplorer.snapshot().mode==="full"')
        assert page.locator('#graph-neighbors').is_visible(),'late local response must not steal the full-mode menu'
        page.unroute('**/graph-neighborhood',hold_neighborhood)
        page.click('[data-fg="load"]')
        page.wait_for_function('()=>FullGraphView.loader?.state==="paused"');page.click('[data-fg="auto"]')
        page.wait_for_function('()=>FullGraphView.loader.state==="complete"')
        assert page.locator('#graph-neighbors').is_visible(),'shared neighbor menu must be reachable in full mode'
        page.evaluate("()=>FullGraphView.renderer.locate('a')")
        if not page.locator('#graph-explorer').is_visible():page.click('#graph-neighbors')
        page.wait_for_selector('#graph-explorer:not([hidden])')
        page.click('[data-explorer="focus"]')
        page.wait_for_function('()=>GraphExplorer.snapshot().focusInfo?.nodes===3')
        assert page.evaluate('()=>Number(FullGraphView.renderer.canvas.style.opacity)<.5')
        page.evaluate('()=>FullGraphView.renderer.reset()')
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        page.screenshot(path=str(OUT/'full-exploration.png'))
        page.locator('.full-graph-options > summary').click();page.uncheck('[data-fg="labels"]')
        page.locator('.full-graph-options > summary').click()
        page.wait_for_function('''()=>{const r=FullGraphView.renderer,i=r.displayDataset.nodeIndex.get('a'),p=r.renderPositions||r.displayDataset.positions;
          const [x,y]=r.camera.worldToScreen(p[i*2],p[i*2+1],r.width,r.height);
          return r.labelContext.getImageData(Math.floor(x*r.dpr),Math.floor(y*r.dpr),1,1).data[3]>0;}''')
        page.click('[data-explorer="focus"]')
        assert page.evaluate('()=>!FullGraphView.renderer.canvas.style.opacity||Number(FullGraphView.renderer.canvas.style.opacity)===1')
        assert page.evaluate('()=>FullGraphView.loader.dataset.nodeCount===6&&FullGraphView.loader.dataset.edgeCount===5')
        print('phase: full-focus-and-names-off',flush=True)
        manifests=[]
        page.route('**/graph-sessions',lambda route:manifests.append(route))
        page.click('[data-fg="restart"]');page.wait_for_function('()=>FullGraphView.loader.state==="loading"&&GraphExplorer.snapshot().selected===null')
        assert not page.evaluate('()=>GraphExplorer.snapshot().page.items.length'),'restart must discard old neighbor rows before manifest returns'
        assert manifests;manifests.pop().fulfill(json=manifest)
        page.wait_for_function('()=>FullGraphView.loader.state==="paused"')
        dimensions=[]
        for width,height in [(1440,950),(1280,800),(1024,768),(768,950),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            modes={}
            for mode in ['local','full']:
                page.click(f'[data-fg-mode="{mode}"]');page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                box=page.locator('#graph-canvas' if mode=='local' else '.full-graph-host').bounding_box();modes[mode]=box
                assert page.locator('#graph-neighbors').is_visible()
                clipped=page.locator('.graph-toolbar button' if mode=='local' else '.full-graph-toolbar button').evaluate_all('(buttons)=>buttons.filter(b=>b.offsetParent&&b.scrollWidth>b.clientWidth+2).map(b=>b.id||b.dataset.fg)')
                assert not clipped,(width,mode,clipped)
            assert abs(modes['local']['y']-modes['full']['y'])<1 and abs(modes['local']['height']-modes['full']['height'])<1,(width,modes)
            dimensions.append({'width':width,'height':modes['local']['height']})
        print('phase: responsive-geometry',flush=True)
        page.select_option('#project','')
        assert page.locator('#graph-neighbors').is_visible(),'project clearing must preserve full-mode menu placement'
        assert page.evaluate('()=>GraphExplorer.snapshot().mode==="full"')
        assert not errors,errors
        print(json.dumps({'local_branch_shared_descendants':True,'late_response_rejected':True,'outside_picker':True,'path_preserved':True,'focus_restore':True,'canvas_geometry_stable':True,'full_focus_without_labels':True,'manifest_unchanged':True,'restart_and_mode_isolation':True,'dimensions':dimensions,'requests':requests,'errors':errors}),flush=True)
    except Exception:
        if page:page.screenshot(path=str(OUT/'browser-failure.png'))
        raise
    finally:
        try:next(session)
        except StopIteration:pass


if __name__=='__main__':run()
