"""Isolated browser UI contract. API fixtures test interface state, not Milvus integration."""
import json
import threading
from contextlib import ExitStack
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
EDGE = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

LOCAL_VISIBLE_LABEL_COUNT = '''()=>{const chart=wb.chart,data=chart.getModel().getSeriesByIndex(0).getData();let count=0;
  function visit(el,hidden=false){if(!el)return;hidden=hidden||!!el.ignore||!!el.invisible||el.style?.opacity===0;
    const text=el.getTextContent?.();
    if(text&&!hidden&&!text.ignore&&!text.invisible&&text.style?.opacity!==0&&text.style?.text){
      let ancestorHidden=false;for(let parent=el.parent;parent;parent=parent.parent)if(parent.ignore||parent.invisible||parent.style?.opacity===0)ancestorHidden=true;
      const box=text.getBoundingRect().clone();box.applyTransform(text.getComputedTransform());
      if(!ancestorHidden&&box.x<chart.getWidth()&&box.x+box.width>0&&box.y<chart.getHeight()&&box.y+box.height>0)count++;
    }
    for(const child of el.childrenRef?.()||[])visit(child,hidden);
  }
  for(let i=0;i<data.count();i++)visit(data.getItemGraphicEl(i));return count;}'''

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)
    def translate_path(self, path):
        if path.startswith('/assets/'):
            path = '/' + path[len('/assets/'):]
        if path.startswith('/vendor/'):
            path = '/' + path.lstrip('/')
        return super().translate_path(path)
    def log_message(self, *args):
        pass

@pytest.fixture
def redesign_page():
    if not EDGE.exists():
        pytest.skip('system Edge is required')
    from playwright.sync_api import sync_playwright
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with ExitStack() as cleanup:
        cleanup.callback(server.server_close)
        cleanup.callback(server.shutdown)
        runtime = cleanup.enter_context(sync_playwright())
        browser = runtime.chromium.launch(headless=True,executable_path=str(EDGE),args=[
            '--disable-gpu','--disable-accelerated-2d-canvas','--use-gl=angle',
            '--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        cleanup.callback(browser.close)
        page = browser.new_page(viewport={'width':1440,'height':950})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def respond(route):
            path=route.request.url.split('/api/',1)[1].split('?',1)[0]
            value={}
            if path=='auth/policy': value={'auth_enabled':False}
            elif path=='projects': value={'projects':[{'id':'p','name':'测试项目','counts':{'documents':1,'entities':0,'relations':0,'chunks':1},'metadata':{'ontology_mode':'discovery'}}]}
            elif path=='ingest/capabilities': value={'concurrency_choices':['auto',1,2],'max_file_bytes':10000000,'max_batch_bytes':20000000,'max_batch_items':20,'supported_extensions':['.txt','.md','.pdf','.docx','.zip']}
            elif path.endswith('/records/page'): value={'records':[],'total':0,'next_cursor':None}
            elif path.endswith('/metadata/facets'): value={'fields':[],'records':1}
            elif path.endswith('/graph-neighborhood'): value={'records':[],'truncated':False}
            elif path.endswith('/sources'): value={'documents':[{'id':'d','kind':'document','version':1,'text':'退款原文','metadata':{'title':'退款规则','source_format':'txt','status':'completed'},'derived_count':1}]}
            elif path.endswith('/ontologies'): value={'versions':[]}
            elif path.endswith('/ontology'): route.fulfill(status=404,json={'detail':'尚未发布本体'});return
            elif path.endswith('/ingest-batches'): value={'batches':[]}
            elif path.endswith('/jobs'): value={'jobs':[]}
            elif path.endswith('/snapshots'): value={'snapshots':[]}
            elif path.endswith('/applicability-values'): value={'platforms':[],'businesses':[],'audiences':[]}
            elif path.endswith('/ontology-drafts'): value={'drafts':[]}
            elif path.endswith('/ontology-discovery'): value={'unpublished_candidate_count':0}
            route.fulfill(status=200,json=value)
        page.route('**/api/**',respond)
        page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='domcontentloaded',timeout=60000)
        page.wait_for_function("() => document.querySelectorAll('#project option').length>1")
        page.select_option('#project','p')
        page.wait_for_function("() => typeof current === 'string' && current === 'p'")
        yield page,errors


@pytest.mark.parametrize('viewer', [False, True])
def test_mindmap_standalone_navigation_opens_and_generates(redesign_page, viewer):
    page,errors=redesign_page
    root={'id':'mindmap-entry-root','kind':'entity','type':'Thing','text':'脑图入口实体'}
    page.route('**/records/page',lambda route:route.fulfill(json={'records':[root],'total':1,'next_cursor':None}))
    requests=[]
    def mindmap(route):
        requests.append(route.request.post_data_json)
        route.fulfill(json={'tree':{**root,'children':[]},'bounded':True,'truncated':False})
    page.route('**/mindmap',mindmap)
    page.evaluate('(viewer)=>document.body.classList.toggle("is-viewer",viewer)',viewer)
    entry=page.locator('aside nav .nav-group [data-tab="mindmap"]')
    assert entry.is_visible(), '实体脑图必须保留独立可见的导航入口'
    assert entry.locator('..').locator('h2').inner_text()=='使用知识'
    entry.click()
    assert page.locator('#tab-mindmap').is_visible()
    page.wait_for_selector('#mindmap-root option[value="mindmap-entry-root"]',state='attached')
    page.select_option('#mindmap-root',root['id'])
    page.click('#draw-mindmap')
    page.wait_for_function('() => !!window.echarts.getInstanceByDom(document.getElementById("mindmap-canvas"))?.getOption()?.series?.length')
    assert requests[-1]['root_id']==root['id']
    assert requests[-1]['depth']==3
    assert errors==[]


def test_mindmap_cards_keep_text_inside_and_support_pointer_folding(redesign_page):
    page,errors=redesign_page
    tree={'id':'law','text':'《中华人民共和国民法典》','type':'法律法规','children':[
        {'id':'chapter','text':'侵权责任编','type':'法律条款','predicate':'包含条款','children':[
            {'id':'joint','text':'共同侵权的责任认定与承担','type':'法律条款','predicate':'规定','children':[]}]},
        {'id':'platform','text':'美团侵权投诉处理规则','type':'平台规则','predicate':'依据','children':[]}]}
    page.route('**/records/page',lambda route:route.fulfill(json={'records':[{**tree,'kind':'entity'}],'total':1,'next_cursor':None}))
    page.route('**/mindmap',lambda route:route.fulfill(json={'tree':tree,'bounded':True,'truncated':False}))
    page.click('[data-tab="mindmap"]')
    page.wait_for_selector('#mindmap-root option[value="law"]',state='attached')
    page.select_option('#mindmap-root','law');page.click('#draw-mindmap')
    page.wait_for_function('() => document.getElementById("mindmap-summary").textContent.includes("4 / 4")')
    page.wait_for_function('() => wb.tree.getZr().animation.isFinished()')
    cards=page.evaluate('''()=>{
      const data=wb.tree.getModel().getSeriesByIndex(0).getData(),cards=[];
      const bounds=el=>{const box=el.getBoundingRect().clone();box.applyTransform(el.getComputedTransform());return {x:box.x,y:box.y,width:box.width,height:box.height};};
      data.each(i=>{const symbol=data.getItemGraphicEl(i)?.childAt(0),text=symbol?.getTextContent();
        if(symbol&&text)cards.push({id:data.getId(i),symbol:bounds(symbol),text:bounds(text)});
      });return cards;
    }''')
    assert len(cards)==4
    for card in cards:
        box,text=card['symbol'],card['text']
        assert text['x']>=box['x']-1 and text['y']>=box['y']-1,card
        assert text['x']+text['width']<=box['x']+box['width']+1,card
        assert text['y']+text['height']<=box['y']+box['height']+1,card
    def click_branch():
        center=page.evaluate('''()=>{
          const data=wb.tree.getModel().getSeriesByIndex(0).getData();let index;
          data.each(i=>{if(data.getId(i)==='mindmap-root.0')index=i;});
          const symbol=data.getItemGraphicEl(index).childAt(0),box=symbol.getBoundingRect().clone();
          box.applyTransform(symbol.getComputedTransform());return {x:box.x+box.width/2,y:box.y+box.height/2};
        }''')
        canvas=page.locator('#mindmap-canvas').bounding_box()
        page.mouse.click(canvas['x']+center['x'],canvas['y']+center['y'])
    click_branch()
    page.wait_for_function('() => document.getElementById("mindmap-summary").textContent.includes("3 / 4")')
    page.wait_for_function('() => wb.tree.getZr().animation.isFinished()')
    click_branch()
    page.wait_for_function('() => document.getElementById("mindmap-summary").textContent.includes("4 / 4")')
    assert page.evaluate('''()=>{
      const data=wb.tree.getModel().getSeriesByIndex(0).getData();let root;
      data.each(i=>{if(data.getId(i)==='mindmap-root')root=data.getItemGraphicEl(i).childAt(0);});
      return !root.currentStates.includes('blur');
    }'''), '悬停分支时根实体仍须清晰可读'
    page.click('#mindmap-zoom-in')
    assert page.locator('#mindmap-zoom-level').inner_text()=='120%'
    page.click('#mindmap-reset-view')
    assert page.locator('#mindmap-zoom-level').inner_text()=='100%'
    assert errors==[]


def test_page_scope_drafts_tags_and_management_exclusion(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="sources"]')
    page.wait_for_selector('#source-list .source-item')
    assert page.locator('#cb-details').evaluate('(node)=>node.open') is False
    page.click('#scope-toggle');page.fill('#query-platforms','美团')
    assert page.evaluate("scope().applicability||null") is None
    page.click('#apply-scope')
    assert '平台：美团' in page.locator('#applied-scope-chips').inner_text()
    assert page.evaluate("scope().applicability.platforms.values") == ['美团']
    page.click('[data-tab="qa"]')
    assert page.evaluate("scope().applicability||null") is None
    page.click('#scope-toggle');page.fill('#query-platforms','淘宝');page.click('#scope-close')
    assert page.evaluate("scope().applicability||null") is None
    page.click('[data-tab="sources"]')
    assert page.evaluate("scope().applicability.platforms.values") == ['美团']
    page.click('[data-tab="projects"]')
    page.wait_for_selector('#projects-root .projects-header')
    assert not page.locator('#scope').is_visible()
    assert page.evaluate("scope().filters") is None
    assert page.locator('.nav-management [data-tab="projects"]').is_visible()
    assert errors == []


def test_graph_settings_dock_has_close_escape_backdrop_and_numeric_hops(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]')
    page.click('#graph-settings')
    drawer=page.locator('#graph-settings-drawer')
    assert drawer.evaluate('(e)=>e.open')
    assert abs(drawer.bounding_box()['x']+drawer.bounding_box()['width']-1440)<2
    assert page.locator('#graph-choice-prev').is_visible()
    page.keyboard.press('Escape')
    assert not drawer.evaluate('(e)=>e.open')
    assert page.locator('#graph-settings').evaluate('(e)=>e===document.activeElement')
    page.click('#graph-settings');page.mouse.click(20,400)
    assert not drawer.evaluate('(e)=>e.open')
    page.click('#graph-settings');drawer.locator('.workspace-drawer-close').click()
    assert not drawer.evaluate('(e)=>e.open')
    assert page.locator('#graph-hops').get_attribute('type')=='number'
    assert page.locator('#graph-hops').get_attribute('max')=='10'
    assert errors==[]


def test_ontology_expansion_button_uses_existing_checkbox_state(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]')
    toggle=page.locator('#ontology-expansion-toggle')
    assert toggle.count()==1, 'ontology expansion needs an independent state button'
    checkbox=page.locator('#ontology-expansion')
    initial=checkbox.is_checked()
    assert toggle.get_attribute('aria-pressed')==str(initial).lower()
    toggle.click()
    assert checkbox.is_checked() is not initial
    assert toggle.get_attribute('aria-pressed')==str(not initial).lower()
    toggle.focus();page.keyboard.press('Space')
    assert checkbox.is_checked() is initial
    checkbox.evaluate('(el)=>{el.checked=true;el.dispatchEvent(new Event("change",{bubbles:true}));}')
    assert toggle.get_attribute('aria-pressed')=='true'
    assert errors==[]


def test_local_graph_uses_canonical_type_colors_and_keeps_names(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'a','kind':'entity','text':'甲商户','type':'urn:Merchant','type_label':'商户','version':1},
           {'id':'b','kind':'entity','text':'退款规则','type':'urn:Rule','type_label':'规则','version':1}]
    edge={'id':'ab','kind':'relation','subject_id':'a','object_id':'b','type':'urn:follows','text':'遵循','version':1}
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':[*nodes,edge],'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':2,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('a',1)")
    result=page.evaluate('''()=>{
      const series=wb.chart.getModel().getSeriesByIndex(0).option;
      return series.data.map(n=>({id:n.id,name:n.name,color:n.itemStyle.color,
        expected:GraphPalette.colorFor(n.id==='a'?'urn:Merchant':'urn:Rule')}));
    }''')
    assert len(result)==2
    assert all(row['color']==row['expected'] for row in result), 'local and compact full records must share raw type colors'
    assert {row['name'] for row in result}=={'甲商户','退款规则'}
    assert errors==[]


def test_legend_distinguishes_canonical_types_with_duplicate_display_names(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'a','kind':'entity','text':'甲','type':'urn:A','type_label':'共同类型','version':1},
           {'id':'b','kind':'entity','text':'乙','type':'urn:B','type_label':'共同类型','version':1},
           {'id':'c','kind':'entity','text':'丙','type':'urn:C','type_label':'属性值','version':1},
           {'id':'v','kind':'attribute_value','virtual':True,'subject_id':'a','text':'值','type':'KS_ATTRIBUTE_VALUE','version':1}]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':nodes,'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes[:3],'total':3,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('a',1)")
    data=page.evaluate('''()=>{
      const series=wb.chart.getModel().getSeriesByIndex(0).option;
      return {keys:series.categories.map(c=>c.name),nodes:series.data.map(n=>({id:n.id,color:n.itemStyle.color,
        legendColor:series.categories[n.category].itemStyle.color}))};
    }''')
    assert len(set(data['keys']))==4
    assert all(n['color']==n['legendColor'] for n in data['nodes'])
    assert len(data['nodes'])==4
    assert errors==[]


def test_local_name_switch_controls_actual_budgeted_text(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'a','kind':'entity','text':'商户','type':'Merchant','version':1},
           {'id':'b','kind':'entity','text':'规则','type':'Rule','version':1}]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':nodes,'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':2,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('a',1)")
    count='''()=>{const data=wb.chart.getModel().getSeriesByIndex(0).getData(),texts=new Set();
      function visit(el,hidden=false){if(!el)return;hidden=hidden||!!el.ignore||!!el.invisible;
        const text=el.getTextContent?.();if(text&&!hidden&&!text.ignore&&!text.invisible&&String(text.style?.text||'').length)texts.add(text);
        for(const child of el.childrenRef?.()||[])visit(child,hidden);}
      for(let i=0;i<data.count();i++)visit(data.getItemGraphicEl(i));const count=texts.size;return count;}'''
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert page.evaluate(count)>0
    page.click('#graph-settings');page.uncheck('#graph-labels')
    page.wait_for_function(count.replace('return count;','return count===0;'),timeout=5000)
    page.keyboard.press('Escape')
    page.locator('[data-fg-mode="full"]').click()
    page.locator('[data-fg-mode="local"]').click()
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert page.evaluate(count)==0, 'restoring local view must preserve the user-disabled name switch'
    page.click('#graph-settings')
    page.check('#graph-labels')
    page.wait_for_function(count.replace('return count;','return count>0;'),timeout=5000)
    page.keyboard.press('Escape')
    assert errors==[]


def test_local_mode_first_click_restores_visible_names_after_full_view(redesign_page):
    page,errors=redesign_page
    local_requests=[]
    page.on('request',lambda request:local_requests.append(request.url) if request.url.split('?',1)[0].endswith('/graph-neighborhood') else None)
    nodes=[{'id':f'local{i}','kind':'entity','text':f'局部节点{i} · 中文长名称切换可见性验证','type':f'Thing{i%34}','version':1} for i in range(73)]
    edges=[{'id':f'edge{i}','kind':'relation','subject_id':'local0','object_id':f'local{i}',
            'type':'connects','version':1} for i in range(1,73)]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':[*nodes,*edges],'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':73,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    manifest={'id':'mode-names','entity_count':73,'relation_count':72,'watermark':1,'layout_version':'stable-id-v1'}
    page.route('**/projects/p/graph-sessions',lambda route:route.fulfill(json=manifest))
    def full_page(route):
        kind=route.request.post_data_json['kind']
        route.fulfill(json={'session_id':'mode-names','kind':kind,'watermark':1,'layout_version':'stable-id-v1',
            'records':edges if kind=='connected' else nodes,'entities':nodes if kind=='connected' else [],'next_cursor':None})
    page.route('**/projects/p/graph-sessions/mode-names/page',full_page)
    page.click('[data-tab="search"]')
    page.evaluate("()=>drawGraph('local0',1)")
    labels=LOCAL_VISIBLE_LABEL_COUNT
    page.wait_for_function(labels.replace('return count;','return count>0;'))
    page.evaluate('()=>GraphWorkbench.zoom(wb.chart,1.25)')
    zoom=page.evaluate('wb.chart.getModel().getSeriesByIndex(0).get("zoom")')
    for cycle in range(2):
        page.locator('[data-fg-mode="full"]').click()
        assert not page.locator('#graph-canvas').is_visible()
        if cycle==0:
            page.locator('[data-fg="load"]').click()
            page.wait_for_function('FullGraphView.renderer?.dataset.nodeCount===73')
            page.evaluate('()=>FullGraphView.resume()')
            page.wait_for_function('FullGraphView.loader.state==="complete"')
            page.evaluate('()=>FullGraphView.renderer.onSelect(FullGraphView.renderer.dataset.records[0])')
            page.wait_for_function('document.querySelector("#graph-detail").textContent.includes("局部节点0")')
        requests_before=len(local_requests)
        page.locator('[data-fg-mode="local"]').click()
        page.wait_for_function(labels.replace('return count;','return count>0;'),timeout=3000)
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        first=page.evaluate(labels)
        page.locator('[data-fg-mode="local"]').click()
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        second=page.evaluate(labels)
        assert first==second, f'first local click showed {first} names, second click changed this to {second}'
        assert len(local_requests)==requests_before, 'mode restore must not reload graph records'
        assert abs(page.evaluate('wb.chart.getModel().getSeriesByIndex(0).get("zoom")')-zoom)<1e-6
    assert errors==[]


def test_local_graph_loaded_while_hidden_has_names_on_first_mode_click(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'before0','kind':'entity','type':'Thing','text':'原局部节点','version':1},
           *[{'id':f'late{i}','kind':'entity','text':f'延迟节点{i}','type':'Thing','version':1} for i in range(19)]]
    pending=[]
    def neighborhood(route):
        if route.request.post_data_json.get('seeds')==['late0']:pending.append(route)
        else:route.fulfill(json={'records':[{'id':'before0','kind':'entity','type':'Thing','text':'原局部节点','version':1}], 'truncated':False})
    page.route('**/projects/p/graph-neighborhood',neighborhood)
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]')
    page.evaluate("()=>drawGraph('before0',1)")
    page.evaluate('()=>GraphWorkbench.zoom(wb.chart,1.25)')
    page.evaluate('()=>{const chart=wb.chart;chart.getViewOfSeriesModel(chart.getModel().getSeriesByIndex(0))._controller.trigger("pan",{dx:30,dy:-15});}')
    zoom=page.evaluate('wb.chart.getModel().getSeriesByIndex(0).get("zoom")')
    anchor=page.evaluate('Array.from(wb.chart.getModel().getSeriesByIndex(0).getData().getItemGraphicEl(0).getComputedTransform()).slice(4,6)')
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    page.evaluate('''()=>{window.__hiddenLocalReflows=[];const chart=wb.chart,original=chart.setOption.bind(chart);
      chart.setOption=(option,...args)=>{const el=chart.getDom();if(!el.clientWidth||!el.clientHeight)__hiddenLocalReflows.push([el.clientWidth,el.clientHeight]);return original(option,...args);};}''')
    page.locator('[data-fg-mode="full"]').click()
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert page.evaluate('__hiddenLocalReflows')==[], 'hiding local canvas must not recompute label bounds against zero dimensions'
    page.evaluate("()=>{window.__lateLocalDraw=drawGraph('late0',1)}")
    for _ in range(40):
        if pending:break
        page.wait_for_timeout(25)
    assert pending
    pending.pop().fulfill(json={'records':nodes,'truncated':False})
    page.wait_for_function("wb.chart?.getModel()?.getSeriesByIndex(0)?.getData().count()===20")
    # Hidden responsive containers may deliver a near-zero intermediate layout.
    page.evaluate('()=>wb.chart.resize({width:1,height:1})')
    page.locator('[data-fg-mode="local"]').click()
    page.wait_for_function(LOCAL_VISIBLE_LABEL_COUNT.replace('return count;','return count>0;'),timeout=3000)
    assert abs(page.evaluate('wb.chart.getModel().getSeriesByIndex(0).get("zoom")')-zoom)<1e-6, 'a hidden refresh must preserve the existing local camera'
    restored=page.evaluate('Array.from(wb.chart.getModel().getSeriesByIndex(0).getData().getItemGraphicEl(0).getComputedTransform()).slice(4,6)')
    assert sum((a-b)**2 for a,b in zip(anchor,restored))<1, 'the retained node must stay at its panned screen position'
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    first=page.evaluate(LOCAL_VISIBLE_LABEL_COUNT)
    page.locator('[data-fg-mode="local"]').click()
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert page.evaluate(LOCAL_VISIBLE_LABEL_COUNT)==first, 'second local click must not be needed to recover additional names'
    page.evaluate('()=>{clearGraphWorkspace({preserveSearchResults:true});FullGraphView.switchMode("full");window.__freshHiddenDraw=drawGraph("late0",1);}')
    for _ in range(40):
        if pending:break
        page.wait_for_timeout(25)
    assert pending
    pending.pop().fulfill(json={'records':nodes,'truncated':False})
    page.wait_for_function('wb.chart?.getModel()?.getSeriesByIndex(0)?.getData().count()===20')
    page.evaluate('()=>wb.chart.resize({width:1,height:1})')
    page.locator('[data-fg-mode="local"]').click()
    page.wait_for_function(LOCAL_VISIBLE_LABEL_COUNT.replace('return count;','return count>0;'),timeout=3000)
    assert errors==[]


def test_path_labels_outside_the_normal_budget_are_promoted_and_restored(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':f'n{i}','kind':'entity','text':f'节点{i}','type':'Thing','version':1} for i in range(60)]
    edge={'id':'late-path','kind':'relation','subject_id':'n58','object_id':'n59','type':'connects','version':1}
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':[*nodes,edge],'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':60,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('n0',1)")
    labels='''()=>Object.fromEntries(wb.chart.getModel().getSeriesByIndex(0).option.data.map(n=>[n.id,n.label?.show]))'''
    assert page.evaluate(labels)['n58'] is False
    page.click('#graph-path-toggle')
    page.select_option('#graph-path-from','n58');page.select_option('#graph-path-to','n59')
    page.locator('#graph-path-tools').get_by_role('button',name='寻找路径').click()
    current=page.evaluate(labels)
    assert current['n58'] and current['n59']
    assert sum(bool(v) for v in current.values())<=50
    page.locator('#graph-labels').evaluate('(el)=>{el.checked=false;el.dispatchEvent(new Event("change",{bubbles:true}));}')
    assert not any(page.evaluate(labels).values())
    page.locator('#graph-labels').evaluate('(el)=>{el.checked=true;el.dispatchEvent(new Event("change",{bubbles:true}));}')
    assert page.evaluate(labels)['n58'] is True
    page.locator('#graph-path-tools').get_by_role('button',name='清除路径').click()
    assert page.evaluate(labels)['n58'] is False
    assert errors==[]


def test_local_relationship_text_keeps_native_scale_and_names_have_no_backplates(redesign_page):
    page,errors=redesign_page
    nodes=[dict(id=f'edge-node-{i}',kind='entity',type=f'Type{i%9}',text=f'实体{i}',version=1) for i in range(13)]
    links=[dict(id=f'relation-{i}',kind='relation',subject_id=f'edge-node-{i%13}',
                object_id=f'edge-node-{(i+1+i//13)%13}',type='stored',type_label='存储于',text='存储于',version=1) for i in range(23)]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':nodes+links,'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':13,'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.route('**/records/*/evidence',lambda route:route.fulfill(json={'documents':[],'message':'无来源'}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('edge-node-0',3)")
    def check():
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        value=page.evaluate('''()=>{const chart=wb.chart,s=chart.getModel().getSeriesByIndex(0),e=s.getEdgeData(),labels=[];
          for(let i=0;i<e.count();i++){const text=e.getItemGraphicEl(i)?.getTextContent?.();if(!text||text.ignore||text.invisible)continue;
            const matrix=text.getComputedTransform(),rect=text.getBoundingRect().clone();rect.applyTransform(matrix);
            labels.push({scale:Math.hypot(matrix[0],matrix[1]),font:text.style.fontSize,inView:rect.x<chart.getWidth()&&rect.x+rect.width>0&&rect.y<chart.getHeight()&&rect.y+rect.height>0});}
          return {edges:e.count(),nodes:s.getData().count(),labels,background:s.option.label.backgroundColor,padding:s.option.label.padding};}''')
        assert value['nodes']==13 and value['edges']==23
        assert len(value['labels'])==23, 'all small-graph relationship names must remain attached to their links'
        assert all(abs(label['scale']-1)<.01 and label['font']==11 and label['inView'] for label in value['labels']), value
        assert value['background'] in (None,'transparent') and value['padding']==0
    check()
    page.evaluate("()=>{wb.chart.dispatchAction({type:'select',dataType:'edge',dataIndex:0});wb.chart.dispatchAction({type:'highlight',dataType:'edge',dataIndex:0});wb.chart.trigger('click',{dataType:'edge',data:{id:'relation-0'}})}")
    check()
    for _ in range(2):
        page.evaluate('()=>GraphWorkbench.zoom(wb.chart,1.25)');check()
        page.locator('[data-fg-mode="full"]').click()
        page.evaluate("()=>drawGraph('edge-node-0',3)")
        page.locator('[data-fg-mode="local"]').click();check()
    page.click('#graph-reset-view');check()
    assert errors==[]


def test_local_relationship_name_switch_keeps_lines_through_selection_redraw_and_mode_changes(redesign_page):
    page,errors=redesign_page
    nodes=[dict(id=f'label-node-{i}',kind='entity',type='Thing',text=f'实体{i}',version=1) for i in range(3)]
    links=[dict(id=f'label-edge-{i}',kind='relation',subject_id=f'label-node-{i}',
                object_id=f'label-node-{i+1}',type='connects',type_label='关联',version=1) for i in range(2)]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':nodes+links,'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':len(nodes),'next_cursor':None}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('label-node-0',2)")
    checkbox=page.locator('#graph-edge-labels')
    assert checkbox.count()==1, 'graph settings need an independent relationship-name switch'
    snapshot='''()=>{const s=wb.chart.getModel().getSeriesByIndex(0),data=s.getEdgeData();let lines=0,labels=0;
      for(let i=0;i<data.count();i++){const el=data.getItemGraphicEl(i),text=el?.getTextContent?.();
        if(el&&!el.ignore&&!el.invisible&&el.style?.opacity!==0)lines++;
        if(text&&!text.ignore&&!text.invisible&&text.style?.opacity!==0&&text.style?.text)labels++;}
      return {edges:data.count(),lines,labels};}'''
    def check(labels):
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        actual=page.evaluate(snapshot)
        assert actual=={'edges':len(links),'lines':len(links),'labels':labels},actual
        assert page.evaluate(LOCAL_VISIBLE_LABEL_COUNT)>0, 'entity names must remain independently visible'
    check(2)
    page.click('#graph-settings');assert checkbox.is_visible();checkbox.uncheck()
    page.locator('#graph-settings-drawer .workspace-drawer-close').click();check(0)
    page.evaluate("()=>{wb.chart.dispatchAction({type:'select',dataType:'edge',dataIndex:0});wb.chart.dispatchAction({type:'highlight',dataType:'edge',dataIndex:0});}")
    check(0)
    page.evaluate('()=>GraphWorkbench.zoom(wb.chart,1.2)');check(0)
    page.evaluate("()=>drawGraph('label-node-0',2)");check(0)
    page.click('#graph-path-toggle')
    page.select_option('#graph-path-from','label-node-0');page.select_option('#graph-path-to','label-node-2')
    page.locator('#graph-path-tools').get_by_role('button',name='寻找路径').click();check(0)
    page.locator('[data-fg-mode="full"]').click();page.locator('[data-fg-mode="local"]').click();check(0)
    assert not checkbox.is_checked()
    page.click('#graph-settings');checkbox.check()
    page.locator('#graph-settings-drawer .workspace-drawer-close').click();check(2)
    # Enabling names must retain the existing automatic budget for larger graphs.
    page.evaluate("()=>{wb.chart.dispatchAction({type:'unselect',dataType:'edge',dataIndex:0});wb.chart.dispatchAction({type:'downplay',dataType:'edge',dataIndex:0});}")
    nodes.extend(dict(id=f'label-node-{i}',kind='entity',type='Thing',text=f'实体{i}',version=1) for i in range(3,21))
    page.evaluate("()=>drawGraph('label-node-0',2)");check(0)
    page.evaluate("()=>wb.chart.dispatchAction({type:'highlight',dataType:'edge',dataIndex:0})");check(1)
    page.click('#graph-settings');checkbox.uncheck()
    page.locator('#graph-settings-drawer .workspace-drawer-close').click();check(0)
    assert errors==[]


def test_local_name_culling_follows_fit_resize_and_finished_zoom(redesign_page):
    page,errors=redesign_page
    nodes=[dict(id=name,kind='entity',type='Thing',text='中文节点名称用于尺寸避让验证'+name,version=1) for name in ['a','b','c']]
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':nodes,'truncated':False}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]');page.evaluate("()=>drawGraph('a',1)")
    page.evaluate('()=>GraphWorkbench.reveal()')
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    page.evaluate('''()=>{const el=wb.chart.getDom();el.style.setProperty('width','1000px','important');el.style.setProperty('height','600px','important');el.style.setProperty('min-height','0','important');el.style.setProperty('flex','none','important');
      const s=wb.chart.getModel().getSeriesByIndex(0),data=s.option.data.map((n,i)=>({...n,x:(i-1)*100,y:0}));wb.chart.setOption({series:[{data}]});wb.chart.resize();GraphWorkbench.fit(wb.chart);}''')
    page.wait_for_function('()=>wb.chart.getWidth()===wb.chart.getDom().clientWidth&&wb.chart.getHeight()===wb.chart.getDom().clientHeight',timeout=3000)
    names="()=>wb.chart.getModel().getSeriesByIndex(0).option.data.filter(n=>n.label?.show!==false).map(n=>n.id)"
    page.wait_for_function(names.replace('.map(n=>n.id)','.map(n=>n.id).length===2'),timeout=3000)
    page.evaluate('''()=>{const el=wb.chart.getDom();el.style.setProperty('width','300px','important');el.style.setProperty('height','200px','important');}''')
    page.wait_for_function(names.replace('.map(n=>n.id)','.map(n=>n.id).length===1'),timeout=3000)
    page.evaluate('()=>GraphWorkbench.zoom(wb.chart,2.5)')
    page.wait_for_function(names.replace('.map(n=>n.id)',".map(n=>n.id).join(',')==='b'"),timeout=3000)
    page.evaluate('()=>GraphWorkbench.fit(wb.chart)')
    page.wait_for_function(names.replace('.map(n=>n.id)',".map(n=>n.id).join(',')==='a'"),timeout=3000)
    assert errors==[]


def test_graph_mode_buttons_keep_a_clear_active_state_at_each_width(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]')
    colors='''()=>{const background=el=>{for(let node=el;node;node=node.parentElement){const color=getComputedStyle(node).backgroundColor;if(color!=='rgba(0, 0, 0, 0)'&&color!=='transparent')return color;}return 'rgb(255, 255, 255)';};return Array.from(document.querySelectorAll('[data-fg-mode]'),el=>({active:el.getAttribute('aria-pressed')==='true',bg:background(el),text:getComputedStyle(el).color}));}'''
    def luminance(color):
        import re
        rgb=[int(value)/255 for value in re.findall(r'\d+',color)[:3]]
        return sum(weight*(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4) for c,weight in zip(rgb,[.2126,.7152,.0722]))
    def contrast(a,b):
        values=sorted([luminance(a),luminance(b)])
        return (values[1]+.05)/(values[0]+.05)
    for width in [1440,900,390]:
        page.set_viewport_size({'width':width,'height':950})
        for mode in ['local','full','local']:
            page.locator(f'[data-fg-mode="{mode}"]').click()
            states=page.evaluate(colors);active=next(s for s in states if s['active']);inactive=next(s for s in states if not s['active'])
            assert contrast(active['bg'],inactive['bg'])>=3, states
            assert contrast(active['bg'],active['text'])>=4.5, states
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert errors==[]


def test_graph_settings_name_controls_center_the_boxes_text_and_group(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]');page.click('#graph-settings')
    for width in [1440,390]:
        page.set_viewport_size({'width':width,'height':950})
        page.locator('#graph-edge-labels').scroll_into_view_if_needed()
        geometry=page.evaluate('''()=>{const group=document.querySelector('#graph-settings-drawer .graph-display-switch'),box=group.getBoundingClientRect(),rows=Array.from(group.querySelectorAll('label'),el=>el.getBoundingClientRect());
          return {top:rows[0].top-box.top,bottom:box.bottom-rows.at(-1).bottom,controls:Array.from(group.querySelectorAll('input'),input=>{const label=input.closest('label'),r=input.getBoundingClientRect(),l=label.getBoundingClientRect(),range=document.createRange(),text=label.querySelector('span')||Array.from(label.childNodes).find(n=>n.nodeType===3&&n.textContent.trim());range.selectNodeContents(text);const t=range.getBoundingClientRect();return {box:r.height,row:l.height,boxDelta:r.y+r.height/2-l.y-l.height/2,textDelta:t.y+t.height/2-l.y-l.height/2};})};}''')
        assert abs(geometry['top']-geometry['bottom'])<=1, geometry
        assert all(abs(c['boxDelta'])<=1 and abs(c['textDelta'])<=1.5 for c in geometry['controls']),geometry
        for control in ['graph-labels','graph-edge-labels']:
            item=page.locator('#'+control);item.uncheck();assert not item.is_checked();item.check();assert item.is_checked()
    assert errors==[]


def test_full_graph_entity_types_filter_incremental_pages_without_changing_the_loading_scope(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':f'{t}{i}','kind':'entity','type':f'urn:{t}','text':f'{t}实体{i}','version':1} for t,count in [('A',3),('B',2)] for i in range(count)]
    nodes.append({'id':'unknown','kind':'entity','text':'未知类型实体','version':1})
    by_id={row['id']:row for row in nodes}
    edges=[{'id':name,'kind':'relation','subject_id':a,'object_id':b,'type':'related','version':1} for name,a,b in [('aa','A0','A1'),('bb','B0','B1'),('aa2','A1','A2'),('ab','A1','B1')]]
    manifest={'id':'type-session','entity_count':6,'relation_count':4,'watermark':1,'layout_version':'stable-id-v1'}
    page.route('**/projects/p/graph-sessions',lambda route:route.fulfill(json=manifest))
    requests=[]
    def graph_page(route):
        body=route.request.post_data_json;requests.append(body)
        if body['kind']=='entities':records=nodes;entities=[];cursor=None
        else:
            index={None:0,'second':1,'third':2}[body.get('cursor')]
            records=[edges[0]] if index==0 else [edges[1]] if index==1 else edges[2:]
            entities=[by_id[id] for id in dict.fromkeys([e[k] for e in records for k in ['subject_id','object_id']])]
            cursor=['second','third',None][index]
        route.fulfill(json={'session_id':manifest['id'],'kind':body['kind'],'watermark':1,'layout_version':'stable-id-v1',
            'records':records,'entities':entities,'next_cursor':cursor})
    page.route('**/projects/p/graph-sessions/type-session/page',graph_page)
    page.evaluate("()=>{window.__typeOriginalLabel=labelOf;window.labelOf=type=>type==='urn:A'||type==='urn:B'?'同名类型':window.__typeOriginalLabel(type);}")
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]');page.click('[data-fg="load"]')
    page.wait_for_function('()=>FullGraphView.renderer?.dataset.nodeCount===2')
    types=page.locator('.full-graph-types');a=types.locator('[data-fg-type="urn:A"]')
    assert a.is_visible() and a.get_attribute('data-type-count')=='2'
    assert '当前已加载' in types.inner_text()
    a.click();assert len(requests)==1
    assert page.evaluate('FullGraphView.renderer.typeFilter')=='urn:A'
    page.click('[data-fg="next"]');page.wait_for_function('()=>FullGraphView.renderer.dataset.nodeCount===4')
    assert types.locator('[data-fg-type="urn:B"]').get_attribute('data-type-count')=='2'
    assert types.locator('[data-fg-type="urn:A"]').get_attribute('aria-pressed')=='true'
    assert page.evaluate('FullGraphView.renderer.displayDataset.nodeCount')==2
    assert page.evaluate('''()=>{const r=FullGraphView.renderer;r.draw();return r.stats().drawn_relations;}''')==1
    assert page.evaluate('FullGraphView.renderer.locate("B0")') is False
    page.locator('[data-fg="query"]').fill('B0');page.click('[data-fg="locate"]')
    assert '已定位实体' not in page.locator('.full-graph-progress').inner_text()
    # Hidden nodes cannot be picked, even though they remain in the full loading dataset.
    assert page.evaluate('''()=>{const r=FullGraphView.renderer;r.setSessionManifest({id:'type-session',entity_count:300000,relation_count:4});r.sync();const i=r.dataset.nodeIndex.get('B0'),p=r.dataset.positions;return r.pick(...r.camera.worldToScreen(p[i*2],p[i*2+1],r.width,r.height))===null;}''')
    page.click('[data-fg="next"]');page.wait_for_function('()=>FullGraphView.renderer.dataset.nodeCount===5')
    assert a.get_attribute('data-type-count')=='3'
    assert page.evaluate('FullGraphView.renderer.displayDataset.edgeCount')==2
    page.click('[data-fg="next"]');page.wait_for_function('()=>FullGraphView.loader.state==="complete"')
    assert types.locator('[data-fg-type=""]').get_attribute('data-type-count')=='1'
    assert page.evaluate('FullGraphView.renderer.dataset.nodeCount')==6
    assert page.evaluate('FullGraphView.renderer.displayDataset.nodeCount')==3
    assert '完整清单' in types.inner_text()
    assert types.locator('[data-fg-type="urn:A"]').get_attribute('title')!=types.locator('[data-fg-type="urn:B"]').get_attribute('title')
    before=len(requests);types.locator('[data-fg-types-all]').click()
    assert len(requests)==before
    assert page.evaluate('FullGraphView.renderer.displayDataset===FullGraphView.renderer.dataset')
    for width in [1440,390]:
        page.set_viewport_size({'width':width,'height':728 if width>700 else 844});assert a.is_visible()
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        clipped=page.evaluate('''()=>{const stage=document.getElementById('graph-stage').getBoundingClientRect();return Array.from(document.querySelectorAll('.full-graph-section [data-fg]')).filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.height&&(r.bottom>stage.bottom+1||r.top<stage.top);}).map(el=>el.dataset.fg);}''')
        assert not clipped,clipped
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    a.click();page.click('[data-fg="restart"]');page.wait_for_function('()=>FullGraphView.loader.state==="paused"&&FullGraphView.renderer.dataset.nodeCount===2')
    assert page.evaluate('FullGraphView.renderer.typeFilter') is None
    assert not types.locator('[data-fg-type="urn:B"]').count()
    page.evaluate("()=>document.dispatchEvent(new CustomEvent('workspace:scope-changed',{detail:{page:'search'}}))")
    assert page.evaluate('FullGraphView.renderer.dataset.nodeCount')==0
    assert not types.locator('[data-fg-type]').count()
    page.evaluate('()=>window.labelOf=window.__typeOriginalLabel')
    assert errors==[]


def test_full_graph_type_filter_clears_cached_hidden_pixels_and_restores_all_entities(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]')
    result=page.evaluate('''()=>{const data=new FullGraph.GraphDataset(),N=10000,positions=new Float32Array(N*2);
      const rows=Array.from({length:N},(_,i)=>({id:'pixel-type-'+i,type:i<5000?'A':'B',text:'实体'+i}));
      for(let i=0;i<N;i++){positions[i*2]=(i%50)/100-.25;positions[i*2+1]=-.6-(i%10)/100;}
      positions.set([-.65,.65]);positions.set([.65,.65],5000*2);data.addNodes(rows,positions);
      data.addEdges(Array.from({length:6000},(_,i)=>{const start=i<3000?0:5000;return {id:'pixel-type-edge-'+i,subject_id:'pixel-type-'+(start+1+i%100),object_id:'pixel-type-'+(start+102+i%100)};}));
      const r=new FullGraph.Renderer(document.querySelector('.full-graph-host'));r.setDataset(data);
      const paint=()=>{r.draw();r.gl.finish();},settle=()=>{paint();paint();paint();};settle();
      const ink=(x,y)=>{const screen=r.camera.worldToScreen(x,y,r.width,r.height),bytes=new Uint8Array(5*5*4);r.gl.readPixels(Math.floor(screen[0]*r.dpr)-2,Math.floor((r.height-screen[1])*r.dpr)-2,5,5,r.gl.RGBA,r.gl.UNSIGNED_BYTE,bytes);let max=0;for(let i=0;i<25;i++)max=Math.max(max,...[248,250,251].map((c,k)=>Math.abs(c-bytes[i*4+k])));return max;};
      const all={b:ink(.65,.65),stats:r.stats()};r.setTypeFilter('A');settle();const a={a:ink(-.65,.65),b:ink(.65,.65),stats:r.stats()};
      r.setTypeFilter('B');settle();const b={a:ink(-.65,.65),b:ink(.65,.65),stats:r.stats()};r.setTypeFilter(null);settle();
      const restored={a:ink(-.65,.65),b:ink(.65,.65),stats:r.stats()},error=r.gl.getError();r.destroy();return {all,a,b,restored,error};}''')
    assert result['error']==0
    assert result['all']['stats']['node_layer']=='complete-view-cache'
    assert result['all']['b']>20 and result['a']['a']>20 and result['a']['b']<=2,result
    assert result['b']['b']>20 and result['b']['a']<=2,result
    assert result['restored']['a']>20 and result['restored']['b']>20,result
    assert result['a']['stats']['drawn_entities']==5000 and result['b']['stats']['drawn_entities']==5000
    assert result['restored']['stats']['drawn_entities']==10000
    assert errors==[]


def test_full_graph_many_types_keep_the_evidence_and_canvas_panels_level(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':f'level-{i}','kind':'entity','type':f'Type{i%34}','text':f'实体{i}','version':1} for i in range(73)]
    edges=[{'id':f'level-edge-{i}','kind':'relation','subject_id':nodes[i]['id'],'object_id':nodes[i+1]['id'],'type':'related','version':1} for i in range(45)]
    manifest={'id':'level-session','entity_count':73,'relation_count':45,'watermark':1,'layout_version':'stable-id-v1'}
    page.route('**/projects/p/graph-sessions',lambda route:route.fulfill(json=manifest))
    page.route('**/projects/p/graph-sessions/level-session/page',lambda route:route.fulfill(json={
        'session_id':manifest['id'],'kind':route.request.post_data_json['kind'],'watermark':1,'layout_version':'stable-id-v1',
        'records':edges if route.request.post_data_json['kind']=='connected' else nodes,'entities':nodes,'next_cursor':None}))
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]');page.click('[data-fg="load"]');page.click('[data-fg="auto"]')
    page.wait_for_function('()=>FullGraphView.loader.state==="complete"')
    for width,height in [(1920,1080),(1440,950),(1440,728),(1024,768)]:
        page.set_viewport_size({'width':width,'height':height})
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        layout=page.evaluate('''()=>{const area=document.querySelector('.search-graph-workspace'),left=area.querySelector('.search-results-pane').getBoundingClientRect(),right=document.getElementById('graph-stage').getBoundingClientRect(),canvas=area.querySelector('.full-graph-host').getBoundingClientRect(),types=area.querySelector('.full-graph-type-controls').getBoundingClientRect();return {left:{top:left.top,bottom:left.bottom},right:{top:right.top,bottom:right.bottom,height:right.height},canvas:canvas.height,types:types.height};}''')
        assert abs(layout['left']['top']-layout['right']['top'])<=1 and abs(layout['left']['bottom']-layout['right']['bottom'])<=1,layout
        assert layout['canvas']>=279 and layout['types']<=65,layout
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    original=page.evaluate("document.querySelector('.search-results-pane').getBoundingClientRect().height")
    page.evaluate('''()=>{const list=document.getElementById('hits');for(let i=0;i<100;i++){const el=document.createElement('div');el.textContent='长结果用于核对证据栏内部滚动 '+i;el.style.height='80px';list.append(el);}}''')
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    bounded=page.evaluate("(()=>{const el=document.querySelector('.search-results-pane');return {height:el.getBoundingClientRect().height,content:el.scrollHeight,client:el.clientHeight}})()")
    assert abs(bounded['height']-original)<=2 and bounded['content']>bounded['client'],bounded
    assert errors==[]


def test_full_graph_zoom_tools_stay_left_and_keep_progressive_controls(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'a','kind':'entity','text':'商户','type':'urn:Merchant','version':1},
           {'id':'b','kind':'entity','text':'规则','type':'urn:Rule','version':1}]
    edge={'id':'ab','kind':'relation','subject_id':'a','object_id':'b','type':'follows','version':1}
    manifest={'id':'visual-session','entity_count':2,'relation_count':1,'watermark':1,'layout_version':'stable-id-v1'}
    page.route('**/projects/p/graph-sessions',lambda route:route.fulfill(json=manifest))
    def graph_page(route):
        kind=route.request.post_data_json['kind']
        route.fulfill(json={'session_id':manifest['id'],'kind':kind,'watermark':1,'layout_version':'stable-id-v1',
            'records':[edge] if kind=='connected' else nodes,'entities':nodes if kind=='connected' else [],'next_cursor':None})
    page.route('**/projects/p/graph-sessions/visual-session/page',graph_page)
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]')
    assert page.locator('[data-fg="zoom-in"]').count()==1, 'full mode must retain plus/minus controls'
    for action in ['load','next','auto','pause','cancel','restart','reset']:
        assert page.locator(f'[data-fg="{action}"]').is_visible()
    page.click('[data-fg="load"]')
    page.wait_for_function('()=>FullGraphView.renderer?.dataset.nodeCount===2')
    scale=page.evaluate('FullGraphView.renderer.camera.scale')
    page.click('[data-fg="zoom-in"]')
    assert page.evaluate('FullGraphView.renderer.camera.scale')>scale
    page.click('[data-fg="zoom-out"]')
    assert abs(page.evaluate('FullGraphView.renderer.camera.scale')-scale)<1e-6
    for width in [1440,900,650]:
        page.set_viewport_size({'width':width,'height':950})
        minus=page.locator('[data-fg="zoom-out"]').bounding_box()
        plus=page.locator('[data-fg="zoom-in"]').bounding_box()
        host=page.locator('.full-graph-host').bounding_box()
        assert abs(minus['x']-host['x'])<12, 'zoom group must stay at the left canvas edge'
        assert 0<plus['x']-minus['x']<50
    assert page.evaluate('FullGraphView.renderer.dataset.nodeCount')==2
    assert page.evaluate('FullGraphView.renderer.dataset.edgeCount')==1
    assert errors==[]


def test_full_graph_pixels_match_shared_type_colors_and_keep_dense_relations(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]')
    result=page.evaluate('''()=>{
      const data=new FullGraph.GraphDataset();
      const rows=['urn:Merchant','urn:Rule','urn:Platform'].map((type,i)=>({id:'pixel-'+i,type,text:'类型 '+i,kind:'entity'}));
      data.addNodes(rows,new Float32Array([-.55,0,0,0,.55,0]));
      data.addEdges(Array.from({length:5001},(_,i)=>({id:'pixel-edge-'+i,subject_id:'pixel-0',object_id:'pixel-2'})));
      const r=new FullGraph.Renderer(document.querySelector('.full-graph-host'));
      r.setDataset(data);r.draw();r.gl.finish();
      const gl=r.gl;
      const pixels=rows.map((row,i)=>{
        const [x,y]=r.camera.worldToScreen(data.positions[i*2],data.positions[i*2+1],r.width,r.height);
        const actual=new Uint8Array(4);gl.readPixels(Math.floor(x*r.dpr),Math.floor((r.height-y)*r.dpr),1,1,gl.RGBA,gl.UNSIGNED_BYTE,actual);
        return {actual:[...actual].slice(0,3),expected:[...GraphAppearance.rgbFor(row)]};
      });
      const [x,y]=r.camera.worldToScreen(.275,0,r.width,r.height);
      const sample=()=>{const values=new Uint8Array(36);gl.readPixels(Math.floor(x*r.dpr)-1,Math.floor((r.height-y)*r.dpr)-1,3,3,gl.RGBA,gl.UNSIGNED_BYTE,values);return [...values];};
      const withEdges=sample();r.showEdges=false;r.draw();gl.finish();const withoutEdges=sample();
      const out={pixels,withEdges,withoutEdges,cached:r.cachedRelations,nodes:data.nodeCount,edges:data.edgeCount,error:gl.getError()};
      r.destroy();return out;
    }''')
    assert result['error']==0
    assert result['nodes']==3 and result['edges']==5001 and result['cached']==5001
    assert all(all(abs(a-b)<=2 for a,b in zip(row['actual'],row['expected'])) for row in result['pixels']), result['pixels']
    assert result['withEdges']!=result['withoutEdges'], 'complete cached relations must be visibly painted'
    assert errors==[]


def test_complete_overview_cache_moves_real_markers_and_invalidates_before_append(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="search"]');page.click('[data-fg-mode="full"]')
    result=page.evaluate('''()=>{
      const rows=Array.from({length:5000},(_,i)=>({id:'cache-'+i,kind:'entity',type:i===0?'urn:Merchant':i===1?'urn:Rule':'urn:Filler',text:'节点'+i}));
      const positions=new Float32Array(10000);positions.set([-.7,.55,.7,.55]);
      for(let i=2;i<5000;i++){positions[i*2]=(i%70)/175-.2;positions[i*2+1]=(Math.floor(i/70)%70)/175-.2;}
      const data=new FullGraph.GraphDataset();data.addNodes(rows,positions);
      data.addEdges([{id:'cache-edge',subject_id:'cache-0',object_id:'cache-1'}]);
      const r=new FullGraph.Renderer(document.querySelector('.full-graph-host'));r.showEdges=false;r.setDataset(data);
      const gl=r.gl,paint=()=>{r.draw();gl.finish();};paint();paint();
      const cachedBefore=r.stats();
      r.camera.pan(18,-9,r.width,r.height);r.camera.zoomAt(1.1,r.width/2,r.height/2,r.width,r.height);paint();
      const moved=r.stats(),bg=[248,250,251];
      const marker=(row,x,y)=>{
        const screen=r.camera.worldToScreen(x,y,r.width,r.height),px=Math.floor(screen[0]*r.dpr),py=Math.floor((r.height-screen[1])*r.dpr);
        const bytes=new Uint8Array(7*7*4);gl.readPixels(px-3,py-3,7,7,gl.RGBA,gl.UNSIGNED_BYTE,bytes);
        const expected=[...GraphAppearance.rgbFor(row)].map((c,i)=>bg[i]-c),norm=Math.hypot(...expected);let mass=0,cx=0,cy=0,bestCos=0;
        for(let i=0;i<49;i++){const delta=bg.map((b,k)=>b-bytes[i*4+k]),length=Math.hypot(...delta),dot=delta.reduce((n,c,k)=>n+c*expected[k],0),weight=Math.max(0,dot/(norm*norm));
          if(length>20&&weight>.05){mass+=weight;cx+=(px-3+i%7+.5)*weight;cy+=(py-3+Math.floor(i/7)+.5)*weight;bestCos=Math.max(bestCos,dot/(length*norm));}}
        return {mass,cos:bestCos,error:mass?Math.hypot(cx/mass-screen[0]*r.dpr,cy/mass-(r.height-screen[1])*r.dpr):999};
      };
      const markers=[marker(rows[0],-.7,.55),marker(rows[1],.7,.55)];
      r.showEdges=true;paint();const edgeInvalidated=r.stats();paint();
      r.camera.scale=10;paint();const deep=r.stats();r.reset();paint();const returned=r.stats();
      const added={id:'cache-new',kind:'entity',type:'urn:Merchant',text:'新节点'};
      data.addNodes([added],new Float32Array([.9,-.6]));r.sync();paint();const appended=r.stats(),newMarker=marker(added,.9,-.6);
      const out={cachedBefore,moved,markers,edgeInvalidated,deep,returned,appended,newMarker,error:gl.getError()};r.destroy();return out;
    }''')
    assert result['error']==0
    assert result['cachedBefore']['cached_entities']==5000
    assert result['moved']['node_layer']=='complete-view-cache'
    assert all(m['mass']>.1 and m['cos']>.95 and m['error']<2 for m in result['markers']),result['markers']
    assert result['edgeInvalidated']['node_layer']=='live-color-groups'
    assert result['deep']['node_layer']=='live-color-groups'
    assert result['returned']['node_layer']=='complete-view-cache'
    assert result['appended']['loaded_entities']==5001 and result['appended']['cached_entities']==0
    assert result['newMarker']['mass']>.1 and result['newMarker']['error']<2
    assert errors==[]


def test_graph_toolbar_stays_reachable_while_details_and_paths_are_open(redesign_page):
    page,errors=redesign_page
    nodes=[{'id':'a','kind':'entity','text':'商户','type':'Merchant','version':1,'metadata':{}},
           {'id':'b','kind':'entity','text':'退款规则','type':'Rule','version':1,'metadata':{}}]
    edge={'id':'ab','kind':'relation','subject_id':'a','object_id':'b','text':'遵循','type':'follows','version':1,'metadata':{}}
    page.route('**/projects/p/graph-neighborhood',lambda route:route.fulfill(json={'records':[*nodes,edge],'truncated':False}))
    page.route('**/projects/p/records/page',lambda route:route.fulfill(json={'records':nodes,'total':2,'next_cursor':None}))
    page.route('**/projects/p/records/*/evidence',lambda route:route.fulfill(json={'documents':[]}))
    page.route('**/projects/p/timeline?*',lambda route:route.fulfill(json={'events':[],'truncated':False}))
    page.click('[data-tab="search"]')
    page.evaluate("()=>drawGraph('a',1)")
    page.evaluate("()=>openKnowledgeRecord({id:'a',kind:'entity',projectId:'p'})")
    ids=['graph-expand','graph-zoom-out','graph-zoom-in','graph-reset-view','graph-relayout','graph-path-toggle','graph-settings']

    def assert_tools():
        # Dismiss transient operation notices before measuring panel overlap.
        notice=page.locator('#status .ks-notice__close')
        if notice.count():notice.click()
        page.locator('.graph-toolbar').scroll_into_view_if_needed()
        blocked=page.evaluate('''ids=>ids.filter(id=>{
          const el=document.getElementById(id);if(!el)return true;
          const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return !r.width||!r.height||!hit||!(hit===el||el.contains(hit));
        })''',ids)
        assert blocked==[],f'Toolbar buttons hidden or covered at {page.viewport_size}: {blocked}'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')

    for width in [1440,1280,1024,768,390]:
        page.set_viewport_size({'width':width,'height':950})
        assert_tools()
        for control in ['graph-zoom-in','graph-zoom-out','graph-reset-view','graph-relayout']:
            page.click('#'+control)
            assert_tools()
        page.click('#graph-path-toggle')
        page.wait_for_function("document.getElementById('graph-path-tools').open")
        assert_tools()
        page.click('#graph-settings')
        assert page.locator('#graph-settings-drawer').is_visible()
        page.locator('#graph-settings-drawer .workspace-drawer-close').click()
        page.click('#graph-path-toggle')
        page.wait_for_function("!document.getElementById('graph-path-tools').open")
    page.set_viewport_size({'width':1280,'height':950})
    page.locator('[data-fg-mode="full"]').click()
    assert page.locator('[data-fg="load"]').is_visible()
    page.locator('[data-fg-mode="local"]').click()
    assert_tools()
    page.locator('#detail-drawer-close').click()
    assert_tools()
    assert errors==[]


def test_graph_candidate_previous_page_and_brain_exact_root_keep_applied_search_scope(redesign_page):
    page,errors=redesign_page
    requests=[]
    def records(route):
        body=route.request.post_data_json;requests.append(body)
        if body.get('record_ids'):
            rows=[{'id':body['record_ids'][0],'kind':'entity','type':'Thing','text':'深页实体'}]
            next_cursor=None
        else:
            suffix='second' if body.get('cursor') else 'first'
            rows=[{'id':suffix,'kind':'entity','type':'Thing','text':suffix}]
            next_cursor=None if body.get('cursor') else 'next-page'
        route.fulfill(json={'records':rows,'next_cursor':next_cursor,'total':75})
    brain=[]
    def mindmap(route):
        brain.append(route.request.post_data_json)
        route.fulfill(json={'tree':{'id':'deep-root','text':'深页实体','children':[]},'bounded':True,'truncated':False})
    page.route('**/records/page',records);page.route('**/mindmap',mindmap)
    page.click('[data-tab="search"]');page.click('#graph-settings')
    page.fill('#graph-entity-filter','graph-query')
    page.wait_for_function("document.getElementById('graph-choice-status').textContent.includes('第 1 / 2 页')")
    page.click('#graph-choice-next')
    page.wait_for_function("document.getElementById('graph-choice-status').textContent.includes('第 2 / 2 页')")
    page.click('#graph-choice-prev')
    page.wait_for_function("document.getElementById('graph-choice-status').textContent.includes('第 1 / 2 页')")
    assert page.locator('#graph-entity-choice option[value="first"]').count()==1
    page.keyboard.press('Escape')
    page.evaluate("""()=>{WorkspaceContext.store.edit('p','search',{known_at:'2025-01-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}});WorkspaceContext.store.apply('p','search');}""")
    page.evaluate("()=>window.openEntityMindmap({id:'deep-root',kind:'entity'})")
    assert page.locator('#tab-mindmap').is_visible()
    assert page.locator('#mindmap-root').input_value()=='deep-root'
    assert brain[-1]['root_id']=='deep-root'
    assert brain[-1]['filters']['value']=='杭州'
    assert brain[-1]['known_at']=='2025-01-01'
    assert page.locator('#graph-entity-filter').input_value()=='graph-query'
    assert errors==[]


def test_sources_uses_the_existing_three_mode_upload_and_mobile_navigation(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="sources"]');page.click('#upload-source')
    assert page.locator('#tab-ingest').is_visible()
    assert page.locator('#extraction-mode option').count()==3
    assert page.locator('#tab-sources').is_visible()
    assert page.locator('#scope').is_visible()
    assert page.locator('#library-workspace-drawer').evaluate('(node)=>node.open')
    page.locator('#library-workspace-drawer .workspace-drawer-close').click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('[data-tab="sources"]').is_visible()
    assert page.locator('.nav-management [data-tab="projects"]').is_visible()
    assert errors == []


def test_library_is_list_first_and_details_are_docked_and_closed_with_focus(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="sources"]');row=page.locator('#source-list .source-item').first
    row.wait_for()
    assert not page.locator('#source-detail-drawer').evaluate('(e)=>e.open')
    row.click()
    drawer=page.locator('#source-detail-drawer')
    assert drawer.evaluate('(e)=>e.open')
    assert abs(drawer.bounding_box()['x']+drawer.bounding_box()['width']-1440)<2
    assert page.locator('.source-detail-tabs button').count()>=4
    page.keyboard.press('Escape')
    assert not drawer.evaluate('(e)=>e.open')
    assert row.evaluate('(e)=>e===document.activeElement')
    assert errors==[]


def test_upload_steps_preserve_original_inputs_but_direct_project_switch_cleans_them(redesign_page):
    page,errors=redesign_page
    page.click('[data-tab="sources"]');page.click('#upload-source')
    page.locator('#doc-file').set_input_files({'name':'drawer-check.txt','mimeType':'text/plain','buffer':b'drawer verification'})
    page.locator('[data-upload-step="1"]').click()
    page.locator('#doc-title').fill('未提交资料')
    page.locator('[data-upload-step="2"]').click()
    assert 'drawer-check.txt' in page.locator('#doc-file-list').text_content()
    page.keyboard.press('Escape');page.click('#upload-source')
    assert page.locator('#doc-title').input_value()=='未提交资料'
    assert page.locator('#library-upload-confirm').is_visible()
    page.evaluate("""()=>{const s=document.getElementById('project'),o=new Option('第二个项目','q');s.add(o);s.value='q';s.onchange();}""")
    assert not page.locator('#library-workspace-drawer').evaluate('(e)=>e.open')
    assert page.locator('#doc-title').input_value()==''
    assert page.locator('#doc-file-list .document-queue-row').count()==0
    assert errors==[]


def test_first_step_retry_returns_to_confirmation_without_submitting(redesign_page):
    page,errors=redesign_page
    writes=[]
    page.on('request',lambda request:writes.append(request.url) if request.method=='POST' and '/ingest-batches' in request.url else None)
    page.click('[data-tab="sources"]');page.click('#upload-source')
    page.locator('#doc-file').set_input_files({'name':'confirmation.txt','mimeType':'text/plain','buffer':b'pending'})
    page.locator('#doc-retry-files').click()
    assert page.locator('#library-upload-confirm').is_visible()
    assert writes==[]
    assert errors==[]


def test_preview_late_response_and_direct_project_change_cannot_leave_inert_background(redesign_page):
    page,errors=redesign_page
    pending=[]
    page.route('**/documents/upload/preview',lambda route:pending.append(route))
    page.click('[data-tab="sources"]');page.click('#upload-source')
    page.locator('#doc-file').set_input_files({'name':'preview.txt','mimeType':'text/plain','buffer':b'preview content'})
    page.locator('[data-upload-step="2"]').click()
    page.click('#preview-chunks')
    for _ in range(20):
        if pending:break
        page.wait_for_timeout(50)
    assert pending
    page.locator('#library-workspace-drawer .workspace-drawer-close').click()
    pending.pop(0).fulfill(json={'chunks':[{'text':'preview content','start_char':0,'end_char':15}],'total':1})
    page.wait_for_function("!document.getElementById('preview-chunks').disabled")
    assert page.locator('#chunk-preview').evaluate('(e)=>e.hidden')
    assert not page.locator('body>main').evaluate('(e)=>e.inert')
    page.click('#upload-source');page.click('#preview-chunks')
    for _ in range(20):
        if pending:break
        page.wait_for_timeout(50)
    pending.pop(0).fulfill(json={'chunks':[{'text':'preview content','start_char':0,'end_char':15}],'total':1})
    page.wait_for_selector('#chunk-preview:not([hidden])')
    page.evaluate("()=>{const s=document.getElementById('project');s.add(new Option('第二项目','q'));s.value='q';s.onchange();}")
    assert page.locator('#chunk-preview').evaluate('(e)=>e.hidden')
    assert not page.locator('body>main').evaluate('(e)=>e.inert')
    assert not page.locator('html').evaluate('(e)=>e.classList.contains("chunk-preview-open")')
    assert errors==[]


def test_direct_project_switch_closes_the_independent_record_create_drawer(redesign_page):
    page,errors=redesign_page
    page.evaluate("RecordCreate.open('entity')")
    assert page.locator('#record-create-dialog').is_visible()
    page.evaluate("()=>{const s=document.getElementById('project');s.add(new Option('第二项目','q'));s.value='q';s.onchange();}")
    assert page.locator('#record-create-dialog').count()==0
    assert errors==[]


def test_direct_project_switch_hides_completed_batch_cards(redesign_page):
    page,errors=redesign_page
    batch={'id':'batch-a','status':'completed','total_items':1,'counts':{'completed':1},'requested_concurrency':'auto','effective_concurrency':1}
    def batches(route):
        path=route.request.url.split('?',1)[0]
        value={'items':[]} if path.endswith('/items') else batch if path.endswith('/batch-a') else {'batches':[batch]}
        route.fulfill(json=value)
    page.route('**/projects/p/ingest-batches**',batches)
    page.evaluate('LibraryWorkspace.open("batches")')
    page.locator('#ingest-batches .batch-list button').filter(has_text='查看批次详情').click()
    card=page.locator('#ingest-batches [data-batch-id="batch-a"]');card.wait_for()
    page.evaluate("()=>{const s=document.getElementById('project');s.add(new Option('第二项目','q'));s.value='q';s.onchange();LibraryWorkspace.open('batches');}")
    assert not card.is_visible()
    assert errors==[]
