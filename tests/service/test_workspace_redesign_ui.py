"""Isolated browser UI contract. API fixtures test interface state, not Milvus integration."""
import json
import threading
from contextlib import ExitStack
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
EDGE = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

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
        browser = runtime.chromium.launch(headless=True,executable_path=str(EDGE))
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
        page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='load')
        page.wait_for_function("() => document.querySelectorAll('#project option').length>1")
        page.select_option('#project','p')
        page.wait_for_function("() => typeof current === 'string' && current === 'p'")
        yield page,errors


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
