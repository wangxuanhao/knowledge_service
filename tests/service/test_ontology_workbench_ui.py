"""Real-browser contracts for the unified ontology DAG editor."""
from pathlib import Path

import pytest


EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><body>
<aside><nav><button data-tab="projects">项目管理</button><button data-tab="ontology-workbench">本体工作台</button></nav></aside>
<main><h1 id="title"></h1><section id="scope"></section><section id="tab-projects" class="tab hidden"></section><section id="tab-ontology" class="tab hidden"></section></main>
<select id="project"><option value="p1" selected>测试项目</option></select>
<script>
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
  document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));
  document.getElementById('tab-'+button.dataset.tab)?.classList.remove('hidden');
});
</script>
</body></html>"""

MOCKS = r"""() => {
  window.apiCalls = [];
  const draft = {id:'d1',title:'DAG 草案',status:'editing',revision:2,
    source_kind:'manual',base_ontology_id:'o1',operations:[],decisions:[]};
  window.mockDraft = draft;
  window.mockDrafts = [draft];
  window.mockChanges = [{id:'change-1',status:'pending',operation:'add',kind:'attribute',
    label:'封禁期限',uri:'urn:封禁期限',rationale:'正式事实需要该属性',revision:1,
    impact:{risk:'low',record_count:0,constraint_count:0,linked_candidates:1}}];
  window.mockDiscoveryRun = null;
  window.mockDraftCreationResult = null;
  window.mockFinalizeStale = false;
  window.mockCreateError = null;
  window.mockLifecycleCounts = null;
  window.mockFinalizeOutcomes = [];
  window.evidenceDelays = {};
  window.abortedEvidence = [];
  window.openedFrozenSource = null;
  window.openFrozenSourceEvidence = source => { window.openedFrozenSource = source; };
  const longChunk = '前'.repeat(620) + '恢复证据' + '后'.repeat(80);
  window.mockEvidence = {
    'assertion-1': {assertion_id:'assertion-1',document:{id:'doc-1',version:2,
      version_id:'doc-v2',title:'来源文档',source_content:'full_version'},
      chunk:{id:'chunk-1',start_char:100,end_char:108,text:'前文精确证据后文'},
      location:{mode:'exact',reason:'stored_quote_verified',start_char:102,end_char:106,
        before:'前文',highlight:'精确证据',after:'后文'},
      integrity:{complete:true,source_hash_status:'matched',warnings:[]}},
    'assertion-fail': null,
    'assertion-recovered': {assertion_id:'assertion-recovered',document:{id:'doc-2',version:4,
      version_id:'doc-v4',title:'长文档',source_content:'full_version'},
      chunk:{id:'chunk-long',start_char:2000,end_char:2000+longChunk.length,text:longChunk},
      location:{mode:'recovered_in_chunk',reason:'legacy_evidence_recovered',
        start_char:2620,end_char:2624,before:longChunk.slice(0,620),highlight:'恢复证据',after:longChunk.slice(624)},
      integrity:{complete:true,source_hash_status:'unavailable',warnings:[{code:'source_hash_unavailable',message:'该历史断言未保存来源文档哈希，无法进行额外的哈希一致性校验；固定文档版本与历史切片仍可正常溯源。'}]}},
    'assertion-chunk': {assertion_id:'assertion-chunk',document:{id:'doc-3',version:1,
      version_id:'doc-v1',title:'片段文档',source_content:'full_version'},
      chunk:{id:'chunk-only',start_char:300,end_char:318,text:'完整历史切片但无法唯一定位'},
      location:{mode:'chunk',reason:'highlight_not_unique',start_char:null,end_char:null,before:'',highlight:'',after:''},
      integrity:{complete:true,source_hash_status:'matched',warnings:[]}},
    'assertion-unlocated': {assertion_id:'assertion-unlocated',document:{id:'doc-4',version:7,
      version_id:'doc-v7',title:'失联文档',source_content:'full_version'},
      chunk:{id:'chunk-missing',start_char:null,end_char:null,text:null},
      location:{mode:'unlocated',reason:'chunk_history_missing',start_char:null,end_char:null,before:'',highlight:'',after:''},
      integrity:{complete:false,source_hash_status:'matched',warnings:[{code:'chunk_history_missing',message:'断言所引用的片段没有历史记录。'}]}},
  };
  const root = iri => ({id:iri,iri,canonical_iri:iri,name:iri.split(':').pop(),
    label:iri.split(':').pop(),label_zh:'',child_count:1,other_parent_count:0,
    is_reference:false,display_path:[{iri,label:iri.split(':').pop()}]});
  const child = parent => ({id:'urn:Child',iri:'urn:Child',canonical_iri:'urn:Child',
    name:'Child',label:'Child',label_zh:'子类',child_count:0,other_parent_count:1,
    is_reference:true,parents:['urn:RootA','urn:RootB'],
    display_path:[{iri:parent,label:parent.split(':').pop()},{iri:'urn:Child',label:'Child'}]});
  window.fetch = async (url, options={}) => {
    const parsed = new URL(url, 'http://local.test');
    window.apiCalls.push({url:parsed.pathname + parsed.search, method:options.method||'GET',
      body:options.body ? JSON.parse(options.body) : null});
    let payload = {};
    if(parsed.pathname.endsWith('/ontology-discovery')) payload={
      candidate_count:10,entity_count:7,relation_count:2,attribute_count:1,exception_count:1,
      entity_types:[
        {name:'Beta',count:1},{name:'Gamma',count:3},{name:'Alpha',count:3},
        {name:'Delta',count:4},{name:'Epsilon',count:5},{name:'Zeta',count:6},
        {name:'Eta',count:7},{name:'Theta',count:8},{name:'Iota',count:9},
        {name:'Kappa',count:10},{name:'Lambda',count:11},{name:'Omega',count:12}
      ],
      relation_types:[{name:'RelationB',count:1},{name:'RelationA',count:4}],
      attribute_types:[{name:'AttributeA',count:2}],
      candidate_status_counts:window.mockLifecycleCounts,
      latest_run:window.mockDiscoveryRun ? {
        id:window.mockDiscoveryRun.id,status:window.mockDiscoveryRun.status,
        result_kind:window.mockDiscoveryRun.result_kind || (window.mockDiscoveryRun.unified_draft_id ? 'draft' : window.mockDiscoveryRun.status==='diagnosed_no_change' ? 'diagnosed_no_change' : 'mapping_only'),
        unified_draft_id:window.mockDiscoveryRun.unified_draft_id,
        diagnostics:window.mockDiscoveryRun.diagnostics
      } : null
    };
    else if(parsed.pathname.endsWith('/ontology-discovery/runs')) payload={
      items:window.mockDiscoveryRun?[window.mockDiscoveryRun]:[],
      total:window.mockDiscoveryRun?1:0
    };
    else if(parsed.pathname.includes('/ontology-discovery/runs/') && parsed.pathname.endsWith('/finalize')) {
      if(window.mockFinalizeStale) return new Response(JSON.stringify({
        detail:'discovery base changed before finalization',code:'stale_base',
        details:{base_ontology_id:'o1',current_ontology_id:'o2'}
      }),{status:409,headers:{'Content-Type':'application/json'}});
      window.mockDiscoveryRun={...window.mockDiscoveryRun,status:'finalized_no_change',candidate_outcomes:window.mockFinalizeOutcomes};
      delete window.mockDiscoveryRun.result_kind;
      window.mockLifecycleCounts={pending:3,included_in_draft:0,approved:0,materialized:2};
      payload={result_kind:'mapping_only',run:window.mockDiscoveryRun,
        discovery_run:window.mockDiscoveryRun,materialized_count:2};
    }
    else if(parsed.pathname.includes('/ontology-discovery/runs/')) payload=window.mockDiscoveryRun||{};
    else if(parsed.pathname.endsWith('/candidate-mindmap')) payload={nodes:[
      {id:'c1',text:'Alpha one',type:'Alpha',occurrence_count:3,source_count:11,
       sources_truncated:true,sources:[{assertion_id:'assertion-1',resolvable:true,document_id:'doc-1',document_version_id:'doc-v2',
         document_title:'来源文档',chunk_id:'chunk-1',confidence:.91,evidence_status:'exact',
         evidence_preview:'这是候选来源预览',evidence_preview_truncated:true},
        {assertion_id:'assertion-fail',resolvable:true,document_id:'doc-fail',document_version_id:'doc-fail-v1',
         document_title:'失败来源',chunk_id:'chunk-fail',confidence:.82,evidence_status:'exact',evidence_preview:'失败预览'}]},
      {id:'c2',text:'Gamma one',type:'Gamma',occurrence_count:2,sources:[
        {assertion_id:'assertion-recovered',resolvable:true,document_id:'doc-2',document_version_id:'doc-v4',
         document_title:'长文档',chunk_id:'chunk-long',confidence:.88,evidence_status:'exact',evidence_preview:'恢复证据'}]},
      {id:'c3',text:'Relation A',type:'RelationA',occurrence_count:1,sources:[
        {assertion_id:null,resolvable:false,document_id:'doc-old',document_title:'旧候选',
         evidence_preview:'旧候选只保留预览',evidence_status:'unverified'}]}
    ],edges:[{id:'r1',subject:'Alpha one',object:'Gamma one',type:'RelationA',occurrence_count:1,sources:[
      {assertion_id:'assertion-chunk',resolvable:true,document_id:'doc-3',document_version_id:'doc-v1',
       document_title:'片段文档',chunk_id:'chunk-only',confidence:.7,evidence_preview:'切片预览'}]}],
      attributes:[{id:'a1',subject:'Alpha one',type:'AttributeA',value:7,value_type:'integer',occurrence_count:1,sources:[
        {assertion_id:'assertion-unlocated',resolvable:true,document_id:'doc-4',document_version_id:'doc-v7',
         document_title:'失联文档',chunk_id:'chunk-missing',confidence:.6,evidence_preview:'不能作为当前原文展示'}]}],
      exceptions:[{id:'x1',text:'Alpha one',source_kind:'attribute',type:'负责人',reason:'证据无法定位',reason_code:'evidence_not_in_source',occurrence_count:1,sources:[
        {assertion_id:null,resolvable:false,document_id:'doc-x',document_title:'异常来源',evidence_preview:'异常预览'}]}],summary:{}};
    else if(parsed.pathname.includes('/assertions/') && parsed.pathname.endsWith('/evidence')) {
      const assertionId=decodeURIComponent(parsed.pathname.split('/assertions/')[1].split('/')[0]);
      const delay=window.evidenceDelays[assertionId]||0;
      if(delay) await new Promise((resolve,reject)=>{
        const timer=setTimeout(resolve,delay);
        options.signal?.addEventListener('abort',()=>{clearTimeout(timer);window.abortedEvidence.push(assertionId);reject(new DOMException('Aborted','AbortError'));},{once:true});
      });
      if(assertionId==='assertion-fail') return new Response(JSON.stringify({detail:'证据服务暂不可用'}),{status:503,headers:{'Content-Type':'application/json'}});
      payload=window.mockEvidence[assertionId]||{};
    }
    else if(parsed.pathname.endsWith('/records/doc-1/history')) payload={versions:[
      {id:'doc-1',version:3,version_id:'doc-v3',kind:'document',text:'错误的最新版本',metadata:{title:'新版'}},
      {id:'doc-1',version:2,version_id:'doc-v2',kind:'document',text:'A'.repeat(102)+'精确证据'+'Z'.repeat(20),metadata:{title:'来源文档'}}]};
    else if(parsed.pathname.endsWith('/ontology-change-proposals/change-1/decision')) {
      window.mockChanges[0].status=window.apiCalls.at(-1).body.action==='approve'?'approved':'rejected';payload={...window.mockChanges[0]};
    }
    else if(parsed.pathname.endsWith('/ontology-change-proposals')) payload={proposals:window.mockChanges};
    else if(parsed.pathname.endsWith('/ontology-discovery/drafts') && (options.method||'GET')==='POST') {
      if(window.mockCreateError) return new Response(JSON.stringify(window.mockCreateError),{status:409,headers:{'Content-Type':'application/json'}});
      payload=window.mockDraftCreationResult||{
        result_kind:'draft',id:'d1',unified_draft_id:'d1',draft_revision:draft.revision,
        parent_ontology_id:'o1',run:{id:'run-draft',status:'draft_created',
          result_kind:'draft',unified_draft_id:'d1',diagnostics:{accepted_count:2},
          conflicts:[],merged_groups:[]}
      };
      window.mockDiscoveryRun=payload.run||payload.discovery_run||null;
    }
    else if(parsed.pathname.endsWith('/ontology-drafts')) payload={items:window.mockDrafts,total:window.mockDrafts.length};
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/commands')) {
      draft.revision += 1; draft.operations.push({id:'op'+draft.revision,
        fingerprint:'fp'+draft.revision,risk:'low',source:'manual',
        ...window.apiCalls.at(-1).body.command}); payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/validate')) {
      draft.validation_report = window.mockValidation || {conforms:true,
        graph_integrity:{conforms:true,errors:[]},
        prospective_new_write_contract:{conforms:true,errors:[]},
        historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},
        errors:[],warnings:[],info:[]};
      draft.validation_fingerprint = window.mockFingerprint || 'vf1'; payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/submit')) {
      draft.status='submitted';draft.revision+=1;
      draft.validation_report = window.mockValidation || {conforms:true,
        graph_integrity:{conforms:true,errors:[]},
        prospective_new_write_contract:{conforms:true,errors:[]},
        historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},
        errors:[],warnings:[],info:[]};
      draft.validation_fingerprint=window.mockFingerprint||'vf1';payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/decisions')) {
      draft.revision+=1;draft.decisions.push(...window.apiCalls.at(-1).body.decisions);
      const decided=new Set(draft.decisions.filter(x=>['approve','reject'].includes(x.action)).map(x=>x.operation_id));
      draft.status=window.apiCalls.at(-1).body.decisions.some(x=>x.action==='request_changes')?'editing':(decided.size===draft.operations.length?'reviewed':'submitted');payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/publish')) {
      draft.status='published';payload={id:'o2',project_id:'p1',draft_id:'d1',created_at:'2026-09-24T01:02:03Z'};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1')) payload={...draft};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/roots')) payload={items:[root('urn:RootA'),root('urn:RootB')],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/children')) payload={items:[child(parsed.searchParams.get('iri'))],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/search')) payload={items:[child('urn:RootA')],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/neighborhood')) payload={term:child('urn:RootA'),items:[root('urn:RootA'),root('urn:RootB')],relations:[],attributes:[],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-matrix')) payload={items:[{iri:'urn:rel',canonical_iri:'urn:rel',kind:'relation',label:'关联',domain:['urn:RootA','urn:RootB'],range:['urn:Child']}],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology/term-impact')) payload={record_count:3,constraint_count:2,pending_review_count:1,linked_relation_count:2};
    else if(parsed.pathname.endsWith('/ontologies')) payload={versions:[{id:'o0',created_at:'2026-01-01T00:00:00Z'},{id:'o1',created_at:'2026-02-01T00:00:00Z'}]};
    return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
  };
}"""


@pytest.fixture(scope='module')
def workbench_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE))
        yield browser
        browser.close()


@pytest.fixture()
def page(workbench_browser):
    context = workbench_browser.new_context(viewport={'width': 1500, 'height': 1000})
    context.add_init_script(f"({MOCKS})()")
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda error: errors.append(str(error)))
    pg.set_content(SHELL)
    pg.add_style_tag(path=str(WEB / 'style.css'))
    pg.add_style_tag(path=str(WEB / 'ontology-workbench.css'))
    pg.add_script_tag(path=str(WEB / 'ontology-workbench.js'))
    pg.wait_for_function("() => OntologyWorkbench.state.discovery !== null")
    pg.set_default_timeout(5000)
    pg._errors = errors
    yield pg
    context.close()


def open_design(page):
    page.click('[data-tab="ontology-workbench"]')
    page.evaluate("() => OntologyWorkbench.setStage('design')")
    page.wait_for_selector('[data-hierarchy-row="urn:RootA"]')


def test_switching_to_projects_hides_ontology_workbench(page):
    page.click('[data-tab="ontology-workbench"]')
    page.click('[data-tab="projects"]')
    workbench = page.locator('#tab-ontology-workbench')
    assert 'hidden' in workbench.get_attribute('class').split()
    assert workbench.evaluate("element => getComputedStyle(element).display") == 'none'
    assert not workbench.is_visible()


def test_empty_draft_list_keeps_draft_stages_locked_without_loading_forever(page):
    page.wait_for_function("() => OntologyWorkbench.state.discovery !== null")
    page.evaluate("() => { mockDrafts = []; apiCalls = []; }")
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_function(
        "() => apiCalls.filter(x => x.url.endsWith('/ontology-drafts')).length === 1")
    page.wait_for_function("() => OntologyWorkbench.state.hasDrafts === false")

    draft_stages = page.locator(
        '[data-workbench-stage="design"], '
        '[data-workbench-stage="review"], '
        '[data-workbench-stage="validate"], '
        '[data-workbench-stage="publish"]')
    assert draft_stages.count() == 4
    assert all(draft_stages.nth(index).is_disabled() for index in range(4))

    page.evaluate("() => OntologyWorkbench.setStage('design')")
    page.wait_for_function(
        "() => apiCalls.filter(x => x.url.endsWith('/ontology-drafts')).length === 1")
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'
    assert '正在加载草案' not in page.locator(
        '#ontology-workbench-canvas-content').inner_text()


def test_discovery_distribution_switches_share_one_stably_sorted_detail(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-discovery-distribution-kind="entity"]')
    switches = page.locator('[data-discovery-distribution-kind]')
    assert switches.count() == 3
    assert switches.all_inner_texts() == [
        '实体类别\n7 个候选 · 12 类',
        '关系类型\n2 个候选 · 2 类',
        '属性定义\n1 个候选 · 1 类',
    ]
    assert switches.nth(0).get_attribute('aria-pressed') == 'true'
    assert page.locator('.discovery-distribution-detail').count() == 1

    entity_rows = page.locator(
        '.discovery-distribution-detail .ontology-workbench__cluster-row')
    assert entity_rows.all_inner_texts() == [
        'Omega\n12', 'Lambda\n11', 'Kappa\n10',
        'Iota\n9', 'Theta\n8', 'Eta\n7']

    toggle = page.locator('.ontology-workbench__cluster-toggle')
    assert toggle.inner_text() == '展开全部（共 12 类）'
    assert toggle.get_attribute('aria-expanded') == 'false'

    toggle.click()
    assert entity_rows.all_inner_texts() == [
        'Omega\n12', 'Lambda\n11', 'Kappa\n10', 'Iota\n9', 'Theta\n8',
        'Eta\n7', 'Zeta\n6', 'Epsilon\n5', 'Delta\n4', 'Alpha\n3',
        'Gamma\n3', 'Beta\n1']
    assert toggle.inner_text() == '收起'
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert 'is-expanded' in page.locator(
        '.ontology-workbench__cluster-list').get_attribute('class').split()

    toggle.click()
    assert entity_rows.count() == 6
    assert toggle.get_attribute('aria-expanded') == 'false'


def test_discovery_distribution_switches_candidate_kind_and_cluster_filter(page):
    page.click('[data-tab="ontology-workbench"]')
    page.click('[data-discovery-distribution-kind="relation"]')

    assert page.evaluate("() => OntologyWorkbench.state.discoveryKind") == 'relation'
    assert page.locator(
        '[data-discovery-distribution-kind="relation"]').get_attribute(
            'aria-pressed') == 'true'
    assert page.locator('.discovery-distribution-detail h4').inner_text() == '关系类型分布'
    assert page.locator('[data-candidate-kind="relation"]').count() == 1

    page.click('[data-cluster-filter="RelationA"]')
    assert page.evaluate("() => OntologyWorkbench.state.filters.cluster") == 'RelationA'
    assert page.locator('[data-cluster-filter="RelationA"]').get_attribute(
        'aria-pressed') == 'true'
    assert '类别：RelationA' in page.locator(
        '[data-active-cluster-filter]').inner_text()
    assert page.locator('[data-candidate-id="r1"]').count() == 1

    page.click('[data-discovery-distribution-kind="attribute"]')
    assert page.evaluate("() => OntologyWorkbench.state.discoveryKind") == 'attribute'
    assert page.evaluate("() => OntologyWorkbench.state.filters.cluster") == ''
    assert page.locator('[data-candidate-kind="attribute"]').count() == 1


def test_discovery_evidence_exceptions_remain_reachable(page):
    page.click('[data-tab="ontology-workbench"]')
    page.click('[data-discovery-kind="exception"]')
    assert page.evaluate("() => OntologyWorkbench.state.discoveryKind") == 'exception'
    assert page.locator('[data-candidate-kind="exception"]').count() == 1

    page.click('[data-discovery-distribution-kind="entity"]')
    assert page.evaluate("() => OntologyWorkbench.state.discoveryKind") == 'entity'
    assert page.locator('[data-candidate-kind="entity"]').count() == 3


def test_discovery_uses_one_explained_workspace_instead_of_duplicate_side_queue(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('.ontology-workbench__discovery-guide')
    assert page.locator('.ontology-workbench__library').is_hidden()
    assert page.locator('#ontology-workbench-canvas-title').inner_text() == '候选术语整理'
    canvas = page.locator('#ontology-workbench-canvas-content').inner_text()
    assert '这里是什么' in canvas
    assert '从业务文档中提取' in canvas
    assert '等待确认的术语' in canvas
    assert page.locator('[data-candidate-id]').count() == 3
    assert '从左侧选择' not in page.locator('#ontology-workbench-inspector').inner_text()


def test_discovery_candidate_uses_preview_full_count_and_truncation_note(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c1"]')
    card = page.locator('[data-candidate-id="c1"]')
    assert '11 份来源' in card.inner_text()
    card.click()
    page.wait_for_selector('[data-candidate-evidence="assertion-1"][data-state="ready"]')
    inspector = page.locator('#ontology-workbench-inspector').inner_text()
    assert '来源证据 11' in inspector
    assert '精确证据位置' in inspector
    assert '来源文档' in inspector and '来源版本 2' in inspector
    assert 'chunk-1' in inspector and '100–108' in inspector
    assert '仅显示 2/11 条来源' in inspector


def test_candidate_evidence_is_lazy_and_each_source_settles_independently(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c1"]')
    assert page.evaluate("() => apiCalls.filter(x => x.url.includes('/assertions/')).length") == 0

    page.evaluate("() => { evidenceDelays['assertion-1']=150; evidenceDelays['assertion-fail']=150; }")
    page.click('[data-candidate-id="c1"]')
    assert page.locator('[data-candidate-evidence][data-state="loading"]').count() == 2
    assert '正在核验固定文档版本与历史切片' in page.locator(
        '[data-candidate-evidence="assertion-1"]').inner_text()
    page.wait_for_function("() => apiCalls.filter(x => x.url.includes('/assertions/')).length === 2")
    page.wait_for_selector('[data-candidate-evidence="assertion-1"][data-state="ready"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-fail"][data-state="error"]')
    ready = page.locator('[data-candidate-evidence="assertion-1"]')
    failed = page.locator('[data-candidate-evidence="assertion-fail"]')
    assert '精确证据位置' in ready.inner_text()
    assert '证据读取失败：证据服务暂不可用' in failed.inner_text()


def test_recovered_evidence_renders_the_complete_chunk_and_late_highlight(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c2"]')
    page.click('[data-candidate-id="c2"]')
    card = page.locator('[data-candidate-evidence="assertion-recovered"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-recovered"][data-state="ready"]')
    assert '历史切片内恢复定位' in card.inner_text()
    assert '绝对字符范围 2620–2624' in card.inner_text()
    assert len(card.locator('pre').inner_text()) == 704
    assert card.locator('mark').inner_text() == '恢复证据'
    assert card.locator('mark').evaluate("node => node.parentNode.textContent.indexOf(node.textContent)") == 620
    assert '固定文档版本与历史切片仍可正常溯源' in card.inner_text()


def test_chunk_and_unlocated_modes_never_fabricate_a_highlight_or_current_text(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-discovery-kind="relation"]')
    page.click('[data-discovery-kind="relation"]')
    page.click('[data-candidate-kind="relation"]')
    chunk = page.locator('[data-candidate-evidence="assertion-chunk"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-chunk"][data-state="ready"]')
    assert '仅保存切片级位置' in chunk.inner_text()
    assert '只能确认到历史切片级别' in chunk.inner_text()
    assert chunk.locator('mark').count() == 0
    assert chunk.locator('pre').inner_text() == '完整历史切片但无法唯一定位'

    page.click('[data-discovery-kind="attribute"]')
    page.click('[data-candidate-kind="attribute"]')
    missing = page.locator('[data-candidate-evidence="assertion-unlocated"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-unlocated"][data-state="ready"]')
    assert '历史来源无法定位' in missing.inner_text()
    assert '断言所引用的片段没有历史记录。' in missing.inner_text()
    assert '不能作为当前原文展示' not in missing.inner_text()
    assert missing.locator('pre').count() == 0


def test_candidate_switch_aborts_old_evidence_and_stale_response_cannot_overwrite(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c1"]')
    page.evaluate("() => { evidenceDelays['assertion-1']=300; evidenceDelays['assertion-fail']=300; }")
    page.click('[data-candidate-id="c1"]')
    page.click('[data-candidate-id="c2"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-recovered"][data-state="ready"]')
    page.wait_for_function("() => abortedEvidence.includes('assertion-1') && abortedEvidence.includes('assertion-fail')")
    inspector = page.locator('#ontology-workbench-inspector')
    assert 'Gamma one' in inspector.inner_text()
    assert inspector.locator('[data-candidate-evidence="assertion-1"]').count() == 0


def test_unresolvable_candidate_and_exception_do_not_request_assertion_evidence(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c3"]')
    before = page.evaluate("() => apiCalls.filter(x => x.url.includes('/assertions/')).length")
    page.click('[data-candidate-id="c3"]')
    assert '旧候选只保留预览' in page.locator('#ontology-workbench-inspector').inner_text()
    page.click('[data-discovery-kind="exception"]')
    page.click('[data-candidate-kind="exception"]')
    assert '证据无法定位' in page.locator('#ontology-workbench-inspector').inner_text()
    assert page.evaluate("() => apiCalls.filter(x => x.url.includes('/assertions/')).length") == before


def test_full_historical_source_selects_exact_pinned_version_and_reuses_viewer(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-candidate-id="c1"]')
    page.click('[data-candidate-id="c1"]')
    page.wait_for_selector('[data-candidate-evidence="assertion-1"][data-state="ready"]')
    page.locator('[data-candidate-evidence="assertion-1"] [data-open-candidate-source]').click()
    page.wait_for_function("() => openedFrozenSource !== null")
    calls = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/records/doc-1/history'))")
    assert len(calls) == 1 and calls[0]['method'] == 'GET'
    opened = page.evaluate("() => openedFrozenSource")
    assert opened['version_id'] == 'doc-v2'
    assert opened['version'] == 2
    assert opened['full_text'].startswith('A' * 102 + '精确证据')
    assert opened['start_char'] == 102 and opened['end_char'] == 106
    button = page.locator('[data-candidate-evidence="assertion-1"] [data-open-candidate-source]')
    assert button.is_enabled()
    button.click()
    page.wait_for_function("() => apiCalls.filter(x => x.url.endsWith('/records/doc-1/history')).length === 2")
    assert button.is_enabled()


def test_discovery_separates_entities_relations_attributes_and_evidence_exceptions(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-discovery-kind="entity"]')
    assert page.locator('[data-candidate-id]').count() == 3
    page.click('[data-discovery-kind="relation"]')
    assert page.locator('[data-candidate-kind="relation"]').count() == 1
    assert 'Alpha one → Gamma one' in page.locator('[data-candidate-kind="relation"]').inner_text()
    page.click('[data-discovery-kind="attribute"]')
    assert page.locator('[data-candidate-kind="attribute"]').count() == 1
    assert 'Alpha one = 7' in page.locator('[data-candidate-kind="attribute"]').inner_text()
    page.click('[data-discovery-kind="exception"]')
    assert page.locator('[data-candidate-kind="exception"]').count() == 1
    assert '证据无法定位' in page.locator('[data-candidate-kind="exception"]').inner_text()


def test_ontology_change_approval_is_owned_by_the_ontology_workbench(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('.ontology-workbench__change-proposal')
    card=page.locator('.ontology-workbench__change-proposal')
    assert '封禁期限' in card.inner_text()
    card.locator('input[placeholder^="审批意见"]').fill('同意新增正式属性')
    card.get_by_role('button',name='批准并生成本体版本').click()
    page.wait_for_function("() => apiCalls.some(x => x.url.endsWith('/ontology-change-proposals/change-1/decision'))")
    page.wait_for_function("() => mockChanges[0].status === 'approved'")


def test_discovery_cluster_filters_the_candidate_list(page):
    page.click('[data-tab="ontology-workbench"]')
    page.click('.ontology-workbench__cluster-toggle')
    page.wait_for_selector('[data-cluster-filter="Alpha"]')
    page.click('[data-cluster-filter="Alpha"]')
    assert page.locator('[data-candidate-id]').count() == 1
    assert page.locator('[data-candidate-id]').inner_text().startswith('Alpha one')
    assert page.locator('[data-cluster-filter="Alpha"]').get_attribute('aria-pressed') == 'true'
    page.click('[data-clear-cluster-filter]')
    assert page.locator('[data-candidate-id]').count() == 3


def test_discovery_refresh_remains_available_without_the_side_queue(page):
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-refresh-discovery]')
    before = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/ontology-discovery')).length")
    page.click('[data-refresh-discovery]')
    page.wait_for_function(
        "before => apiCalls.filter(x => x.url.endsWith('/ontology-discovery')).length > before",
        arg=before,
    )


def test_discovery_draft_form_explains_name_and_uses_unfiltered_scope(page):
    page.click('[data-tab="ontology-workbench"]')
    page.click('[data-discovery-distribution-kind="relation"]')
    page.click('[data-cluster-filter="RelationA"]')
    page.click('#ontology-workbench-create-discovery-draft')

    form = page.locator('[data-discovery-draft-form]')
    assert '当前候选及来源证据会冻结' in form.inner_text()
    assert form.locator('[data-draft-scope="candidates"]').inner_text() == '10\n全部候选'
    assert form.locator('[data-draft-scope="definitions"]').inner_text() == '15\n分布项'
    assert form.locator('[data-draft-scope="sources"]').inner_text() == '7\n来源文档'

    name = page.get_by_label('草案名称（必填）')
    assert name.input_value()
    assert '知识草案' in name.input_value()
    assert '用于草案列表、审核记录和版本追溯' in form.inner_text()
    name.fill('直播规则草案')
    assert page.locator('[data-draft-name-preview]').inner_text() == '直播规则草案'

    before = page.evaluate("() => apiCalls.length")
    page.click('[data-cancel-discovery-draft]')
    assert page.locator('[data-discovery-draft-form]').count() == 0
    assert page.evaluate("() => apiCalls.length") == before
    assert page.evaluate("() => OntologyWorkbench.state.discoveryKind") == 'relation'
    assert page.evaluate("() => OntologyWorkbench.state.filters.cluster") == 'RelationA'


def prepare_discovery_creation(page, result):
    page.evaluate(
        "result => { mockDraftCreationResult=result; mockDiscoveryRun=null; "
        "mockDrafts=[]; mockFinalizeStale=false; }",
        result,
    )
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_function("() => OntologyWorkbench.state.hasDrafts === false")
    page.click('#ontology-workbench-create-discovery-draft')
    page.wait_for_selector('#ontology-discovery-draft-name')
    page.get_by_role('button', name='生成并进入设计').click()


def mapping_only_result():
    run = {
        'id': 'run-map', 'status': 'ready_to_finalize',
        'result_kind': 'mapping_only', 'unified_draft_id': None,
        'diagnostics': {
            'total_candidates': 6, 'accepted_count': 2, 'merged_count': 1,
            'quarantined_count': 2, 'low_frequency_attribute_count': 1,
        },
        'merged_groups': [{
            'kind': 'class', 'canonical_name': '用户行为',
            'candidate_ids': ['entity-1', 'entity-2'],
            'accepted_candidate_id': 'entity-1',
            'evidence_refs': ['assertion-1', 'assertion-2'],
        }],
        'conflicts': [{
            'candidate_id': 'attribute-1', 'name': '通知渠道',
            'kind': 'attribute', 'code': 'class_property_name_collision',
            'involved_kinds': ['attribute', 'class'],
            'evidence_refs': ['assertion-conflict-1'],
        }, {
            'candidate_id': 'relation-1', 'name': '禁止出现',
            'kind': 'relation', 'code': 'existing_term_kind_collision',
            'existing_kind': 'attribute',
            'evidence_refs': ['assertion-conflict-2'],
        }],
    }
    return {'result_kind': 'mapping_only', 'run': run, 'discovery_run': run}


def test_discovery_draft_result_selects_governed_draft_and_enters_design(page):
    result = {
        'result_kind': 'draft', 'id': 'd1', 'unified_draft_id': 'd1',
        'draft_revision': 2, 'parent_ontology_id': 'o1',
        'title': '归纳草案', 'status': 'editing', 'source_kind': 'discovery',
        'operations': [], 'decisions': [],
        'run': {
            'id': 'run-draft', 'status': 'draft_created',
            'result_kind': 'draft', 'unified_draft_id': 'd1',
            'diagnostics': {'accepted_count': 2},
            'conflicts': [], 'merged_groups': [],
        },
    }

    prepare_discovery_creation(page, result)

    page.wait_for_function("() => OntologyWorkbench.state.stage === 'design'")
    assert page.evaluate("() => OntologyWorkbench.state.draftId") == 'd1'
    assert page.evaluate("() => OntologyWorkbench.state.draft?.id") == 'd1'


def test_mapping_only_result_stays_in_discovery_and_can_finalize(page):
    prepare_discovery_creation(page, mapping_only_result())

    page.wait_for_selector('[data-finalize-discovery-run]')
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'
    assert '沿用当前本体并提交知识' in page.locator(
        '[data-finalize-discovery-run]').inner_text()

    page.click('[data-finalize-discovery-run]')
    page.wait_for_function(
        "() => apiCalls.some(x => x.url.endsWith('/ontology-discovery/runs/run-map/finalize'))")
    page.wait_for_function(
        "() => OntologyWorkbench.state.discoveryRun.status === 'finalized_no_change'")
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'


def test_diagnosed_no_change_keeps_empty_draft_stages_locked(page):
    run = {
        'id': 'run-diagnostic', 'status': 'diagnosed_no_change',
        'result_kind': 'diagnosed_no_change', 'unified_draft_id': None,
        'diagnostics': {
            'total_candidates': 2, 'accepted_count': 0, 'merged_count': 0,
            'quarantined_count': 1, 'low_frequency_attribute_count': 1,
        },
        'conflicts': [], 'merged_groups': [],
    }

    prepare_discovery_creation(page, {
        'result_kind': 'diagnosed_no_change', 'run': run, 'discovery_run': run,
    })

    page.wait_for_selector('[data-discovery-run-diagnostic]')
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'
    assert page.evaluate("() => OntologyWorkbench.state.hasDrafts") is False
    assert page.locator('[data-workbench-stage="design"]').is_disabled()
    assert '未生成本体草案' in page.locator(
        '[data-discovery-run-diagnostic]').inner_text()


def test_mapping_only_stale_conflict_tells_user_to_refresh_and_reanalyse(page):
    prepare_discovery_creation(page, mapping_only_result())
    page.evaluate("() => { mockFinalizeStale=true; }")

    page.click('[data-finalize-discovery-run]')

    page.wait_for_function(
        "() => document.querySelector('#ontology-workbench-notice').textContent.includes('刷新并重新分析')")
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'
    assert page.locator('[data-finalize-discovery-run]').count() == 0
    assert page.locator('#ontology-workbench-create-discovery-draft').count() == 1


def test_discovery_run_shows_normalization_metrics_and_conflict_evidence(page):
    prepare_discovery_creation(page, mapping_only_result())

    conflict = page.locator('[data-discovery-conflict="attribute-1"]')
    page.wait_for_selector('[data-discovery-conflict="attribute-1"]')
    text = conflict.inner_text()
    assert '通知渠道' in text
    assert '属性' in text and '实体类' in text
    assert '实体类型同名' in text
    assert 'assertion-conflict-1' in text
    existing_conflict = page.locator('[data-discovery-conflict="relation-1"]').inner_text()
    assert '禁止出现' in existing_conflict
    assert '当前本体中的属性同名' in existing_conflict
    assert 'assertion-conflict-2' in existing_conflict
    metrics = page.locator('[data-discovery-run-metrics]').inner_text()
    assert all(label in metrics for label in ('接受 2', '合并 1', '隔离 2', '低频属性 1'))


@pytest.mark.parametrize('status,label', [
    ('published', '已发布'), ('closed', '已关闭'),
    ('stale_base', '本体版本已变化'), ('stale_source', '候选来源已变化'),
])
def test_finished_discovery_run_allows_next_round(page, status, label):
    page.evaluate("run => { mockDiscoveryRun=run; }", {
        'id': 'run-old', 'status': status, 'unified_draft_id': 'd1',
        'diagnostics': {}, 'conflicts': [],
    })
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-discovery-run="run-old"]')
    assert page.locator('#ontology-workbench-create-discovery-draft').count() == 1
    assert label in page.locator('[data-discovery-run]').inner_text()
    page.click('#ontology-workbench-create-discovery-draft')
    page.get_by_role('button', name='生成并进入设计').click()
    page.wait_for_function("() => OntologyWorkbench.state.draft?.id === 'd1'")
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'design'


def test_new_discovery_draft_replaces_previously_selected_draft(page):
    open_design(page)
    page.evaluate("""() => {
      OntologyWorkbench.state.draft={...mockDraft,id:'old-draft',title:'旧草案'};
      OntologyWorkbench.state.draftId='old-draft';
      mockDraft.title='本轮新草案';
    }""")
    page.click('[data-workbench-stage="discover"]')
    page.wait_for_selector('#ontology-workbench-create-discovery-draft')
    page.click('#ontology-workbench-create-discovery-draft')
    page.get_by_role('button', name='生成并进入设计').click()
    page.wait_for_function("() => OntologyWorkbench.state.stage === 'design'")
    page.wait_for_selector('[data-hierarchy-row="urn:RootA"]')
    assert page.evaluate("() => OntologyWorkbench.state.draft.id") == 'd1'
    assert page.evaluate("() => OntologyWorkbench.state.draft.title") == '本轮新草案'
    assert page.evaluate("() => OntologyWorkbench.state.draftId") == 'd1'


@pytest.mark.parametrize('status,kind', [
    ('published', 'draft'), ('closed', 'draft'),
    ('finalized_no_change', 'mapping_only'),
])
def test_reanalysis_of_unchanged_processed_candidates_does_not_reopen_editor(page, status, kind):
    run = {'id': 'run-replayed', 'status': status, 'conflicts': [],
           'unified_draft_id': 'd1' if kind == 'draft' else None}
    prepare_discovery_creation(page, {
        'result_kind': kind, 'id': 'd1', 'unified_draft_id': run['unified_draft_id'],
        'draft_revision': 2, 'parent_ontology_id': 'o1', 'run': run,
    })
    page.wait_for_function("() => OntologyWorkbench.state.discoveryRun?.id === 'run-replayed'")
    assert page.evaluate("() => OntologyWorkbench.state.stage") == 'discover'
    assert '本轮已处理' in page.locator('#ontology-workbench-notice').inner_text()
    assert page.locator('[data-finalize-discovery-run]').count() == 0


def test_finalize_refreshes_counts_and_renders_persisted_outcomes(page):
    result = mapping_only_result()
    del result['run']['result_kind']  # Actual run detail has no result_kind.
    prepare_discovery_creation(page, result)
    page.wait_for_selector('[data-finalize-discovery-run]')
    page.evaluate("""() => { mockFinalizeOutcomes=[
      {candidate_id:'entity-1',status:'materialized',reason_code:'materialized'},
      {candidate_id:'attribute-1',status:'skipped',reason_code:'ontology_term_conflict'},
      {candidate_id:'a2',status:'skipped',reason_code:'ontology_validation_failed'}
    ]; }""")
    before = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/ontology-discovery')).length")
    page.click('[data-finalize-discovery-run]')
    page.wait_for_function("() => OntologyWorkbench.state.discoveryRun.status === 'finalized_no_change'")
    assert page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/ontology-discovery')).length") > before
    assert '只复用现有本体' in page.locator('[data-discovery-run]').inner_text()
    assert '已提交知识' in page.locator('[data-discovery-run]').inner_text()
    assert page.locator('[data-finalize-discovery-run]').count() == 0
    page.locator('.ontology-workbench__lifecycle-help summary').click()
    assert '2' in page.locator('.ontology-workbench__lifecycle .ontology-workbench__metric').filter(has_text='已物化').inner_text()
    assert '已入图' in page.locator('[data-discovery-outcome="entity-1"]').inner_text()
    assert '本体校验未通过' in page.locator('[data-discovery-outcome="a2"]').inner_text()
    page.click('[data-refresh-discovery]')
    page.wait_for_selector('[data-discovery-outcome="a2"]')
    assert '已跳过' in page.locator('[data-discovery-outcome="attribute-1"]').inner_text()


@pytest.mark.parametrize('status', ['stale_base', 'stale_source'])
def test_mapping_stale_run_offers_reanalysis_without_finalize(page, status):
    run = mapping_only_result()['run']
    run['status'] = status
    page.evaluate("run => { mockDiscoveryRun=run; }", run)
    page.click('[data-tab="ontology-workbench"]')
    page.wait_for_selector('[data-discovery-run]')
    assert page.locator('[data-finalize-discovery-run]').count() == 0
    assert '重新分析' in page.locator('#ontology-workbench-create-discovery-draft').inner_text()


def test_create_discovery_conflict_offers_refresh_guidance(page):
    page.evaluate("""() => { mockCreateError={code:'stale_source',detail:'source changed',details:{}}; }""")
    prepare_discovery_creation(page, mapping_only_result())
    page.wait_for_function("() => apiCalls.some(x => x.method==='POST' && x.url.endsWith('/ontology-discovery/drafts'))")
    assert '刷新并重新分析' in page.locator('#ontology-workbench-notice').inner_text()


@pytest.mark.parametrize('assertion_id', ['assertion-1', 'assertion-fail', None])
def test_conflict_evidence_uses_pinned_assertion_or_reports_unavailable(page, assertion_id):
    result = mapping_only_result()
    result['run']['candidate_snapshot'] = [{
        'id': 'attribute-1', 'kind': 'attribute', 'proposed_type': '通知渠道',
        'assertion_id': assertion_id, 'document_title': '冲突来源',
    }]
    result['run']['conflicts'][0]['evidence_refs'] = [assertion_id or 'attribute-1']
    prepare_discovery_creation(page, result)
    page.wait_for_selector('[data-discovery-conflict="attribute-1"]')
    page.locator('[data-discovery-conflict="attribute-1"]').get_by_role('button', name='查看冲突证据').click()
    inspector = page.locator('#ontology-workbench-inspector')
    if assertion_id == 'assertion-1':
        page.wait_for_selector('[data-candidate-evidence="assertion-1"][data-state="ready"]')
        assert '精确证据' in inspector.inner_text()
        inspector.get_by_role('button', name='查看完整历史原文').click()
        page.wait_for_function("() => openedFrozenSource !== null")
        assert page.evaluate("() => openedFrozenSource.version_id") == 'doc-v2'
    elif assertion_id:
        page.wait_for_selector('[data-candidate-evidence="assertion-fail"][data-state="error"]')
        assert '证据读取失败' in inspector.inner_text()
    else:
        assert '仅保留原始预览' in inspector.inner_text()
        assert not page.evaluate("() => apiCalls.some(x => x.url.includes('/assertions/'))")
    assert page._errors == []


def test_view_switch_only_appears_when_hierarchy_and_matrix_are_available(page):
    page.click('[data-tab="ontology-workbench"]')
    switch = page.locator('.ontology-workbench__view-switch')
    assert switch.is_hidden()
    page.evaluate("() => OntologyWorkbench.setStage('design')")
    page.wait_for_selector('[data-hierarchy-row="urn:RootA"]')
    assert switch.is_visible()
    assert page.locator('.ontology-workbench__library').is_visible()


def test_hierarchy_is_lazy_paginated_and_multi_parent_selection_is_shared(page):
    open_design(page)
    calls = page.evaluate("() => apiCalls.map(x => x.url)")
    assert any('/ontology-hierarchy/roots?' in call for call in calls)
    assert not any('/ontology-hierarchy/children?' in call for call in calls)

    page.click('[data-expand-iri="urn:RootA"]')
    page.click('[data-expand-iri="urn:RootB"]')
    page.wait_for_function("() => document.querySelectorAll('[data-hierarchy-row=\"urn:Child\"]').length === 2")
    assert page.locator('[data-hierarchy-row="urn:Child"]').count() == 2
    page.locator('[data-hierarchy-row="urn:Child"]').first.click()
    assert page.locator('[data-hierarchy-row="urn:Child"][aria-current="true"]').count() == 2
    inspector = page.locator('#ontology-workbench-inspector').inner_text()
    assert 'RootA' in inspector and 'Child' in inspector
    assert '另有 1 个父级' in inspector
    assert page._errors == []


def test_search_and_matrix_use_paged_endpoints_not_full_summary(page):
    open_design(page)
    page.fill('#ontology-workbench-search', 'Child')
    page.click('#ontology-workbench-search-button')
    page.wait_for_selector('[data-hierarchy-row="urn:Child"]')
    page.click('[data-workbench-mode="matrix"]')
    page.wait_for_selector('[data-matrix-row="urn:rel"]')
    calls = page.evaluate("() => apiCalls.map(x => x.url)")
    assert any('/ontology-hierarchy/search?' in call and 'limit=50' in call for call in calls)
    assert any('/ontology-matrix?' in call and 'limit=50' in call for call in calls)
    assert not any('/ontology?' in call for call in calls)


def test_object_editor_sends_revisioned_multi_parent_and_constraint_commands(page):
    open_design(page)
    page.click('[data-new-object]')
    page.select_option('#ontology-object-kind', 'relation')
    page.fill('#ontology-object-iri', 'urn:worksFor')
    page.fill('#ontology-object-label-zh', '任职于')
    page.fill('#ontology-object-label-en', 'works for')
    page.fill('#ontology-object-domain', 'urn:RootA\nurn:RootB')
    page.fill('#ontology-object-range', 'urn:Child')
    page.click('#ontology-object-save')
    page.wait_for_function("() => apiCalls.filter(x => x.url.endsWith('/commands')).length >= 6")
    commands = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/commands')).map(x => x.body)")
    actions = [item['command']['action'] for item in commands]
    assert actions[:6] == ['create_term','add_domain','add_domain','add_range','add_annotation','add_annotation']
    assert [item['expected_revision'] for item in commands[:6]] == [2,3,4,5,6,7]


def test_attribute_editor_uses_datatype_and_never_sends_object_range(page):
    open_design(page)
    page.click('[data-new-object]')
    page.select_option('#ontology-object-kind', 'attribute')
    page.fill('#ontology-object-iri', 'urn:notificationChannel')
    page.fill('#ontology-object-domain', 'urn:RootA')
    page.fill('#ontology-object-range', 'urn:Child')
    page.fill('#ontology-object-datatype', 'http://www.w3.org/2001/XMLSchema#string')
    page.click('#ontology-object-save')
    page.wait_for_function(
        "() => apiCalls.filter(x => x.url.endsWith('/commands')).length >= 3")

    commands = page.evaluate(
        "() => apiCalls.filter(x => x.url.endsWith('/commands')).map(x => x.body.command)")
    actions = [item['action'] for item in commands]
    assert actions == ['create_term', 'add_domain', 'set_datatype']
    assert 'add_range' not in actions


def test_retire_preview_and_restore_require_explicit_source_version(page):
    open_design(page)
    page.click('[data-expand-iri="urn:RootA"]')
    page.locator('[data-hierarchy-row="urn:Child"]').first.click()
    page.click('[data-preview-retire]')
    page.wait_for_selector('[data-retire-confirm]')
    text = page.locator('#ontology-workbench-inspector').inner_text()
    assert '3 条正式知识' in text and '2 条约束' in text
    page.click('[data-open-restore]')
    page.wait_for_selector('#ontology-restore-version')
    assert page.locator('#ontology-restore-version option').count() == 2
    assert page.locator('#ontology-restore-fields input[type="checkbox"]:checked').count() == 0


def prepare_review(page, operations, warnings=None):
    open_design(page)
    page.evaluate("([operations,warnings]) => { mockDraft.operations=operations; mockDraft.decisions=[]; mockDraft.status='editing'; mockValidation={conforms:true,graph_integrity:{conforms:true,errors:[]},prospective_new_write_contract:{conforms:true,errors:[]},historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},errors:[],warnings:warnings||[],info:[]}; }", [operations, warnings or []])
    page.evaluate("() => OntologyWorkbench.selectDraft('d1')")
    page.wait_for_selector('[data-submit-review]:not([disabled])')
    page.click('[data-submit-review]')
    page.wait_for_selector('[data-review-operation]')


def operation(id_, risk='low', action='add_annotation'):
    return {'id': id_, 'fingerprint': 'fp-'+id_, 'action': action,
            'target_iri': 'urn:'+id_, 'risk': risk, 'source': 'manual',
            'before': {'value': '旧值'}, 'after': {'value': '新值'},
            'impact': {'formal_records': 0},
            'validation': {'warnings': []},
            'evidence': [{'document_title': '依据.pdf', 'evidence': '原文证据'}]}


def test_review_shortcuts_autosave_and_reasons_are_enforced(page):
    prepare_review(page, [operation('low'), operation('high','high','retire_term')])
    page.locator('[data-review-operation]').first.click()
    page.keyboard.press('a')
    page.wait_for_function("() => apiCalls.some(x => x.url.endsWith('/decisions'))")
    low = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/decisions')).at(-1).body.decisions[0]")
    assert low['action'] == 'approve' and not low.get('reason')

    page.keyboard.press('j')
    assert page.locator('[data-review-operation][aria-current="true"]').get_attribute('data-operation-id') == 'high'
    page.keyboard.press('a')
    page.wait_for_selector('#ontology-decision-reason')
    assert page.locator('[data-save-decision]').is_disabled()
    page.fill('#ontology-decision-reason', '已核对历史影响')
    assert page.locator('[data-save-decision]').is_enabled()
    page.click('[data-save-decision]')
    page.wait_for_function("() => apiCalls.filter(x => x.url.endsWith('/decisions')).length === 2")


def test_batch_approve_only_visible_no_warning_low_risk(page):
    prepare_review(page, [operation('a'), operation('b'), operation('c','medium')])
    assert page.locator('[data-batch-approve]').is_visible()
    assert page.locator('[data-review-operation][data-batch-eligible="true"]').count() == 2
    page.click('[data-batch-approve]')
    page.wait_for_function("() => apiCalls.some(x => x.url.endsWith('/decisions'))")
    decisions = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/decisions')).at(-1).body.decisions")
    assert len(decisions) == 2 and all(item['action'] == 'approve' for item in decisions)


def test_validation_sections_publish_acknowledgement_and_provenance_link(page):
    warning = {'code':'range_widened','severity':'warning','message':'Range 扩大','operation_ids':['low']}
    prepare_review(page, [operation('low')], [warning])
    page.locator('[data-review-operation]').first.click()
    page.keyboard.press('a')
    page.fill('#ontology-decision-reason', '已核对警告')
    page.check('[data-ack-warning="range_widened"]')
    page.click('[data-save-decision]')
    page.wait_for_function("() => mockDraft.status === 'reviewed'")
    page.click('[data-workbench-stage="validate"]')
    page.wait_for_selector('[data-validation-section="graph"]')
    assert page.locator('[data-validation-section]').count() == 3
    assert 'vf1' in page.locator('#ontology-workbench-canvas-content').inner_text()
    page.click('[data-workbench-stage="publish"]')
    page.check('[data-publish-warning="range_widened"]')
    page.fill('#ontology-publisher', 'owner@example.test')
    page.click('[data-publish-draft]')
    page.wait_for_selector('[data-published-version="o2"]')
    assert '草案 d1' in page.locator('#ontology-workbench-canvas-content').inner_text()
