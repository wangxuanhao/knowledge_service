"""知识台账（实例层）的真实浏览器契约：三视角 / 本体归属 / 关联断言 / 撤销入口 / 疑似重复。

为什么要有这一份：台账的价值全在"数字与列说的是不是真的"——
  · 顶部四个数字必须来自服务端（不是前端猜的、也不是写死的 0）；
  · 「挂在哪一版本体上」必须能区分**当前本体**与**旧版本体**（C2 受控重分类要解决的就是旧结构）；
  · 「关联断言」是对"删了会不会丢血缘"的回答，必须能命中两种情况且去重；
  · 「撤销」以前接口有、UI 点不到；这里钉住"点得到 + 真的打到撤销接口"。
"""
from pathlib import Path

import pytest

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<aside><nav><button data-tab="records">知识台账</button><button data-tab="ontology">本体</button></nav></aside>
<main>
  <h1 id="title"></h1>
  <section id="tab-records" class="tab">
    <div class="panel">
      <div class="row"><h2>知识台账</h2><button id="load-records">刷新记录</button><button id="load-graph" class="secondary">查看关系</button></div>
      <p class="subtle">遵循上方查询范围。</p>
      <div id="records"></div>
    </div>
    <div class="panel">
      <h2>修订记录</h2>
      <div class="columns"><label>记录 ID<input id="revision-id"></label><label>预期修订号<input type="number" id="revision-version" value="1"></label></div>
      <textarea id="revision"></textarea><button id="revise">保存新修订</button>
      <label>恢复历史修订号<input type="number" id="restore-number" value="1"></label><button id="restore-version" class="secondary">恢复为新修订</button>
      <label><input id="delete-confirm" type="checkbox">确认软删除</label><button id="soft-delete">软删除</button>
      <pre id="history"></pre>
    </div>
    <section class="panel entity-governance-panel">
      <h2>实体消歧与融合</h2>
      <div class="entity-governance-bar">
        <label class="resolve-name">名称 / 别名<input id="resolve-text"></label>
        <label class="resolve-score">阈值<input id="resolve-threshold" type="number" value="0.7"></label>
        <button id="resolve-entity">查重</button>
        <label>保留实体 ID<input id="keep-id"></label>
        <label>合并实体 ID<input id="drop-id"></label>
        <label class="alias-name">新增别名<input id="alias-name"></label>
        <button id="add-alias" class="secondary">存别名</button>
        <label class="check merge-check"><input id="merge-confirm" type="checkbox">确认</label>
        <button id="merge-entities">执行合并</button>
        <button id="load-operations" class="secondary">看可撤销的操作</button>
      </div>
      <p class="governance-note">Semantica EntityMerger</p>
      <div id="resolve-result"></div>
      <div id="operations"></div>
    </section>
  </section>
  <section id="tab-ontology" class="tab hidden">
    <button id="load-ontology">载入本体</button><div id="ontology-summary"></div>
    <textarea id="turtle"></textarea><select id="ontology-versions"></select>
  </section>
  <select id="project"><option value="p1" selected>测试项目</option></select>
  <div id="health"></div>
  <div id="status"></div>
  <script>
  // 台账脚本依赖的几个 app 级全局：只做最小实现，够它跑起来即可（不复制 app.js）。
  window.wb={records:new Map(),nodes:new Map(),versionContext:{
    ontologyScope:'all',ontologyIds:null,ontologyVersion:null,source:null}};
  window.wb.setVersionContext=next=>{
    window.wb.versionContext={ontologyScope:'all',ontologyIds:null,ontologyVersion:null,source:null,...(next||{})};
    document.dispatchEvent(new CustomEvent('version-context:changed',{detail:window.wb.versionContext}));
    return window.wb.versionContext;
  };
  window.wb.clearVersionContext=()=>window.wb.setVersionContext();
  window.current='p1';
  window.Auth={isAdmin:()=>true};
  window.$=id=>document.getElementById(id);
  window.esc=value=>String(value===undefined||value===null?'':value);
  // 真实 app 里 typeHint 把类型 IRI 翻成中文标签；这里取末段就够用了，而且属实行要能读出「属性名 = 值」。
  window.typeHint=value=>value?String(value).split(':').pop():'';
  window.labelOf=value=>value?String(value).split(':').pop():'';
  // 注意：不能写 `window.status = fn` —— window.status 是浏览器内置的字符串属性，
  // 赋值会被它的设值器吃掉；函数声明才会在 window 上建一个自己的属性（app.js 也是这么做的）。
  function status(message,isError){window.__status={message,isError:Boolean(isError)};}
  window.editRecord=row=>{window.__edited=(row||{}).id||null;};
  window.historyFor=row=>{window.__history=(row||{}).id||null;};
  window.scope=()=>({metadata:{}});
  // D1「台账 → 图谱」的跳转目标桩：台账点「在图谱定位」要先把实体画到图谱中心，这里只记录它传了什么。
  window.suppressNextAutoGraph=()=>{window.__suppressed=true;};
  window.showTab=tab=>{window.__tab=tab;};
  window.selectEntityDetail=async id=>{window.__graphLocated=id;};
  window.openKnowledgeRecord=async record=>{window.__graphRecord=record;window.__graphLocated=record.id;};
  window.endpoint=suffix=>'/api/projects/p1'+suffix;
  window.api=async(url,body,method)=>{
    const options={method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'}};
    if(options.method!=='GET')options.body=JSON.stringify(body||{});
    const response=await fetch(url,options);
    const payload=await response.json();
    if(!response.ok)throw Object.assign(new Error(payload.error||payload.detail||'请求失败'),payload,{status:response.status});
    return payload;
  };
  // 真实页面里这个按钮的处理器在 workbench.js；台账在外面包了一层（先跑原处理器，再把结果卡片装饰一遍）。
  // 这里补上最小实现，契约正是"包一层"这件事。
  document.getElementById('resolve-entity').onclick=async()=>{
    const result=await api(endpoint('/resolve'),{text:window.$('resolve-text').value,
      threshold:Number(window.$('resolve-threshold').value)});
    const rows=result.canonical?[result.canonical]:result.candidates;
    window.$('resolve-result').innerHTML='<p>'+esc(result.status)+' · '+esc(result.backend)+'</p>'
      +rows.map(x=>'<div class="candidate"><strong>'+esc(x.text)+'</strong> '+typeHint(x.type)
      +'<small>'+esc(x.id)+' · v'+x.version+'</small><button data-keep="'+esc(x.id)+'">设为保留实体</button> '
      +'<button data-drop="'+esc(x.id)+'" class="secondary">设为合并实体</button></div>').join('');
  };
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
    document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));
    document.getElementById('tab-'+button.dataset.tab)?.classList.remove('hidden');
  });
  </script>
</body></html>"""

MOCKS = r"""() => {
  window.apiLog = [];
  window.recordQueryBodies = [];
  window.undoCalls = [];
  window.deleteCalls = [];
  window.historyRequests = 0;
  window.timelineRecord = {id:'timeline',kind:'entity',text:'当前仍可查看',type:'urn:ontology:文档标题',
    ontology_id:'o2-note',version:3,version_id:'revision-r3-aaaaaaaaaaaaaaaa-0003',recorded_at:'2026-03-01T09:00:00Z',
    superseded_at:null,valid_from:null,valid_until:null,metadata:{},properties:{}};
  window.historyRevisions = [
    {id:'timeline',kind:'entity',text:'第一次修订',type:'urn:ontology:文档标题',ontology_id:'o1',
      version:1,version_id:'revision-r1-aaaaaaaaaaaaaaaa-0001',recorded_at:'2026-01-01T09:00:00Z',superseded_at:'2026-02-01T09:00:00Z',
      valid_from:null,valid_until:null,metadata:{},properties:{}},
    {id:'timeline',kind:'entity',text:'第二次修订',type:'urn:ontology:文档标题',ontology_id:'o1',
      version:2,version_id:'revision-r2-aaaaaaaaaaaaaaaa-0002',recorded_at:'2026-02-01T09:00:00Z',superseded_at:'2026-03-01T09:00:00Z',
      valid_from:null,valid_until:null,metadata:{_deleted:true},properties:{note:'历史删除态'}},
    {...window.timelineRecord,manual_change:{actor:'知识维护员 (editor; user:editor)'}},
  ];
  const entity = (id,text,type,ontologyId,extra={}) => ({id,kind:'entity',text,type,ontology_id:ontologyId,
    version:1,recorded_at:'2026-10-01T10:00:00Z',valid_from:null,valid_until:null,properties:{},
    metadata:{...(extra.metadata||{})},...extra});
  const records = [
    entity('e1','价格说明','urn:ontology:文档标题','o1',{version:4,metadata:{discovery_candidate_id:'assert-1'}}),
    entity('e2','团购价','urn:ontology:价格类型','o2-note'),
    // e3 与 e1 同名同类型：服务端的「疑似重复」分组靠它
    // e3 的 metadata 里又指了一条断言，那条断言的 canonical_record_id 也是 e3 —— 同一句话两条血缘，
    // 台账只能算一条（这是最容易写错、也最容易让"有几条原文在支撑"变成夸大的地方）。
    entity('e3','价格说明','urn:ontology:文档标题','o2-note',{metadata:{discovery_candidate_id:'assert-3'}}),
    {id:'r1',kind:'relation',text:'参考价 可能是 销售价',type:'urn:ontology:可能是',
      subject_id:'e1',object_id:'e2',ontology_id:'o1',version:2,recorded_at:'2026-10-01T11:00:00Z',
      valid_from:null,valid_until:null,properties:{},metadata:{}},
    // 属性是一条独立记录（主体 + 属性名 + 值 + 数据类型）：台账以前查询时把它滤掉了，
    // 于是"实体身上的属性"在界面上根本看不到。
    {id:'a1',kind:'attribute',text:'12',type:'urn:ontology:重量',value:12,datatype:'integer',
      subject_id:'e1',ontology_id:'o2-note',version:1,recorded_at:'2026-10-01T10:30:00Z',
      valid_from:null,valid_until:null,properties:{},metadata:{}},
    {id:'c1',kind:'chunk',text:'# 价格说明 团购价为商品/服务的销售价',type:'',source_id:'doc1',
      ontology_id:null,version:1,recorded_at:'2026-10-01T09:00:00Z',valid_from:null,valid_until:null,
      properties:{},metadata:{source_file:'价格说明.md',title:'价格说明.md'}},
  ];
  const assertions = [
    {id:'assert-1',canonical_record_id:null,status:'pending'},
    {id:'assert-2',canonical_record_id:'e1',status:'accepted'},
    {id:'assert-3',canonical_record_id:'e3',status:'accepted'},
  ];
  const mergeBefore = [
    {id:'e4',kind:'entity',text:'标准价格',type:'urn:ontology:保证金',version:2,valid_until:'2026-06-17T02:56:00Z',
      metadata:{aliases:['标准价'],source_file:'平台规则.md',applicability:{platforms:{mode:'specific',values:['抖音电商']}}}},
    {id:'e5',kind:'entity',text:'参考价格',version:1,metadata:{}},
    {id:'r2',kind:'relation',text:'属于价格体系',subject_id:'e5',object_id:'e7',version:5,metadata:{}},
  ];
  const mergeAfter = [
    {...mergeBefore[0],version:3,metadata:{...mergeBefore[0].metadata,aliases:['标准价','参考价格'],_operation_id:'op1'}},
    {...mergeBefore[1],version:2,metadata:{_deleted:true,merged_into:'e4',_operation_id:'op1'}},
    {...mergeBefore[2],version:6,subject_id:'e4',metadata:{_operation_id:'op1'}},
  ];
  const mergeRow = (row,index) => window.sameNameMerge&&index<2?{
    ...row,text:'违规儿童模特肖像',metadata:{...row.metadata,discovery_candidate_id:index?'original-retired':'original-kept'},
  }:row;
  const deleteBefore = [{id:'e6',kind:'entity',text:'旧价格',version:3,metadata:{}}];
  const original = window.fetch;
  window.fetch = async (input,options={}) => {
    const url = typeof input === 'string' ? input : input.url;
    const parsed = new URL(url,'http://localhost');
    const method = (options.method||'GET').toUpperCase();
    window.apiLog.push(method+' '+parsed.pathname);
    const payload = (body) => new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
    if(parsed.pathname.endsWith('/records/timeline/history')) {
      window.historyRequests++;
      if(window.mockFailSide==='history') return new Response('{"error":"history boom"}',{status:500,headers:{'Content-Type':'application/json'}});
      return payload({versions:window.historyRevisions});
    }
    if(parsed.pathname.endsWith('/records/page')||parsed.pathname.endsWith('/records/counts')) {
      const body = JSON.parse(options.body||'{}');
      window.recordQueryBodies.push(body);
      let filtered = body.ontology_scope==='ids'
        ? records.filter(row=>(body.ontology_ids||[]).includes(row.ontology_id))
        : body.ontology_scope==='unknown'
          ? records.filter(row=>!row.ontology_id||!['o1','o2','o2-note'].includes(row.ontology_id))
          : records;
      filtered=filtered.filter(row=>(!body.record_ids?.length||body.record_ids.includes(row.id))&&(!body.kinds?.length||body.kinds.includes(row.kind))&&(!body.query||(row.id+' '+row.text).includes(body.query)));
      if(parsed.pathname.endsWith('/records/counts'))return payload({counts:Object.fromEntries(
        body.kinds.map(kind=>[kind,filtered.filter(row=>row.kind===kind).length]))});
      const offset=Number(body.cursor||0),limit=body.limit||50;
      return payload({total:filtered.length,records:filtered.slice(offset,offset+limit),next_cursor:offset+limit<filtered.length?String(offset+limit):null});
    }
    const recordId=parsed.pathname.match(/\/records\/([^/]+)$/)?.[1];
    if(recordId)return payload(records.find(row=>row.id===decodeURIComponent(recordId))||{});
    if(parsed.pathname.endsWith('/ontologies')) return payload({versions:[
      {id:'o1',version:1,version_reused:false,created_at:'2026-01-01T00:00:00Z'},
      {id:'o2',version:2,version_reused:false,created_at:'2026-02-01T00:00:00Z'},
      {id:'o2-note',version:2,version_reused:true,created_at:'2026-03-01T00:00:00Z'}]});
    if(parsed.pathname.endsWith('/assertions/batch')) {
      const body=JSON.parse(options.body||'{}');
      const assertions_by_record=Object.fromEntries([...new Set(body.record_ids||[])].map(id=>{
        const metadata=records.find(row=>row.id===id)?.metadata||{};
        const candidates=new Set([metadata.discovery_candidate_id,...(metadata.discovery_candidate_ids||[])].filter(Boolean));
        // One filter over the stored assertions preserves both associations
        // without counting an assertion twice when both point to this record.
        return [id,assertions.filter(item=>item.canonical_record_id===id||candidates.has(item.id))];
      }));
      return payload({assertions_by_record,total:Object.values(assertions_by_record).reduce((sum,items)=>sum+items.length,0),truncated_record_ids:[]});
    }
    if(parsed.pathname.endsWith('/assertions')) return payload({total:3,assertions:assertions.filter(item=>item.canonical_record_id===parsed.searchParams.get('canonical_record_id')||parsed.searchParams.getAll('assertion_ids').includes(item.id))});
    if(parsed.pathname.endsWith('/duplicate-groups')) {
      if(window.mockFailSide==='duplicate-groups') return new Response('{"error":"boom"}',{status:500,headers:{'Content-Type':'application/json'}});
      return payload({groups:[
      {name:'价格说明',type:'urn:ontology:文档标题',members:[
        {id:'e1',text:'价格说明',type:'urn:ontology:文档标题',version:1,ontology_id:'o1',source_id:null},
        {id:'e3',text:'价格说明',type:'urn:ontology:文档标题',version:1,ontology_id:'o2-note',source_id:null}]}]});
    }
    if(parsed.pathname.endsWith('/operations')) return payload({operations:[
      // 故意把**旧的排在前面**：服务端回来的顺序按 id/写入顺序，不等于时间顺序（真机上就是这样）
      {id:'audit:op1',recorded_at:'2026-10-01T12:00:00Z',metadata:{operation:'merge',operation_id:'op1',
        _audit:true,created_at:'2026-10-01T12:00:00Z',before:mergeBefore.map(mergeRow)}},
      {id:'audit:op2',recorded_at:'2026-10-02T09:00:00Z',metadata:{operation:'delete',operation_id:'op2',
        _audit:true,created_at:'2026-10-02T09:00:00Z',before:deleteBefore}}]});
    if(parsed.pathname.endsWith('/operations/op1')) {
      if(window.noOperationDetailEndpoint) return new Response('{"detail":"Not Found"}',{status:404});
      return payload({before:mergeBefore.map(mergeRow),redirects:{e5:'e4'},after:mergeAfter.map(mergeRow)});
    }
    const historyId=parsed.pathname.match(/\/records\/([^/]+)\/history$/)?.[1];
    const mergeIndex=mergeBefore.findIndex(row=>row.id===historyId);
    if(mergeIndex>=0) {
      const after=mergeRow(mergeAfter[mergeIndex],mergeIndex);
      return payload({versions:[mergeRow(mergeBefore[mergeIndex],mergeIndex),after,{...after,version:after.version+1,text:'后续编辑'}]});
    }
    if(parsed.pathname.endsWith('/assertions/original-kept/evidence')) return payload({assertion_id:'original-kept',
      document:{title:'《实施细则》意见征集通知',source_content:'full_version'},source_location:{document_version_id:'source-kept'}});
    if(parsed.pathname.endsWith('/assertions/original-retired/evidence')) return payload({assertion_id:'original-retired',
      document:{title:'【已失效】旧规则解读',source_content:'full_version'},source_location:{document_version_id:'source-retired'}});
    if(parsed.pathname.endsWith('/operations/op2')) {
      if(window.mockFailSide==='operation-detail') return new Response('{"error":"读取超时"}',{status:500});
      return payload({before:deleteBefore,after:[],redirects:{}});
    }
    if(parsed.pathname.endsWith('/reclassify')) {
      if(window.mockFailSide==='reclassify') return new Response('{"error":"boom"}',{status:500,headers:{'Content-Type':'application/json'}});
      return payload({current_ontology_id:'o2-note',current_version:'本体 v2',stale_records:2,
        migratable_records:1,unmapped_records:1,groups:[
          {key:'o1:entity:urn:ontology:文档标题',migratable:true,record_count:1,records:[{id:'e1'}]},
          {key:'o1:relation:urn:ontology:可能是',migratable:false,record_count:1,records:[{id:'r1'}]},
        ]});
    }
    if(parsed.pathname.endsWith('/operations/op1/undo')) { window.undoCalls.push('op1'); return payload({restored:2}); }
    if(parsed.pathname.endsWith('/delete')) {
      const request = JSON.parse(options.body);
      window.deleteCalls.push(request);
      const index = records.findIndex(row=>row.id===request.record_id&&row.version===request.expected_version);
      if(index<0||window.mockFailSide==='delete') return new Response('{"detail":"版本冲突：请刷新记录"}',{status:409});
      const removed = records.splice(index,1);
      return payload({deleted:removed.length,records:removed});
    }
    if(parsed.pathname.endsWith('/resolve')) return payload({status:'exact_duplicates',backend:'semantica+aliases',
      canonical:null,candidates:[{id:'e1',text:'价格说明',type:'urn:ontology:文档标题',version:1},
        {id:'e3',text:'价格说明',type:'urn:ontology:文档标题',version:1}]});
    return payload({});
  };
}"""


@pytest.fixture(scope='module')
def ledger_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE))
        yield browser
        browser.close()


@pytest.fixture()
def page(ledger_browser):
    context = ledger_browser.new_context(viewport={'width': 1560, 'height': 1000})
    context.add_init_script(f"({MOCKS})()")
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda error: errors.append(str(error)))
    pg.on('dialog', lambda dialog: dialog.accept())   # 撤销前的确认框：测试里一律确认
    pg.set_content(SHELL)
    pg.add_style_tag(path=str(WEB / 'style.css'))
    pg.add_style_tag(path=str(WEB / 'workspace.css'))
    pg.add_style_tag(path=str(WEB / 'library-workspace.css'))
    pg.add_style_tag(path=str(WEB / 'records-view.css'))
    pg.add_script_tag(path=str(WEB / 'display-time.js'))
    pg.add_script_tag(path=str(WEB / 'workspace-drawers.js'))
    pg.add_script_tag(path=str(WEB / 'record-version-context.js'))
    pg.add_script_tag(path=str(WEB / 'record-attribute-editor.js'))
    pg.add_script_tag(path=str(WEB / 'record-create.js'))
    pg.add_script_tag(path=str(WEB / 'records-view.js'))
    pg.click('[data-tab="records"]')
    pg.wait_for_selector('.ledger-tab-count:not([hidden])', timeout=10000)
    pg.wait_for_function("() => !document.getElementById('records').hasAttribute('aria-busy') || document.getElementById('records').getAttribute('aria-busy')==='false'")
    pg.set_default_timeout(5000)
    pg._errors = errors
    yield pg
    context.close()


@pytest.fixture()
def revision_page(page):
    page.add_style_tag(path=str(WEB / 'record-dialog.css'))
    page.add_script_tag(path=str(WEB / 'record-dialog.js'))
    page.evaluate("() => window.historyFor(window.timelineRecord)")
    page.wait_for_selector('#record-dialog[open]', timeout=10000)
    return page


def ledger_rows(page):
    """[{名称, 行, 单元格}] —— **不能按名字做字典**：同名实体本来就是台账要暴露的东西，会被吃掉一条。"""
    rows = []
    for row in page.query_selector_all('.record-library tbody tr'):
        cells = row.query_selector_all('td')
        rows.append({'name': cells[0].inner_text().strip().splitlines()[0].strip(),
                     'row': row, 'cells': cells})
    return rows


def category_counts(page):
    return {tab.get_attribute('data-ledger-view'): tab.locator('.ledger-tab-count').inner_text()
            for tab in page.locator('.ledger-tab').all()}


def headers(page):
    return [cell.inner_text().strip() for cell in page.query_selector_all('.record-library thead th')]


def select_view(page, kind):
    page.click(f'[data-ledger-view="{kind}"]')
    page.wait_for_selector(f'table.ledger-{kind}')
    page.wait_for_function("() => document.getElementById('records').getAttribute('aria-busy')==='false'")


@pytest.mark.parametrize('kind,short', [('entity', True), ('entity', False),
                                       ('attribute', False), ('chunk', False)])
def test_long_ledger_content_can_be_expanded_in_full_as_literal_text(page, kind, short):
    page.add_style_tag(path=str(WEB / 'records-view.css'))
    page.set_viewport_size({'width': 1280, 'height': 1000})
    text = '排版对齐与知识阅读。' * 6 if short else (
        '很长的知识内容，用于核对列表折叠后仍能查看全部文字。' * 18
        + '<img src=x onerror="window.ledgerHtmlExecuted=true">全文最后一句')
    page.evaluate("""({kind, text}) => {
      const original = window.fetch;
      window.fetch = async (input, options) => {
        const response = await original(input, options);
        if (!String(input).endsWith('/records/page')) return response;
        const data = await response.json();
        data.records = data.records.map(row => row.kind !== kind ? row : {
          ...row, text, ...(kind === 'attribute' ? {value: text} : {})
        });
        return new Response(JSON.stringify(data), {status: 200,
          headers: {'Content-Type': 'application/json'}});
      };
    }""", {'kind': kind, 'text': text})
    if kind == 'entity':
        page.click('#load-records')
    else:
        select_view(page, kind)
    page.wait_for_function("() => document.getElementById('records').getAttribute('aria-busy')==='false'")
    cell = page.locator('.record-library tbody tr').first.locator('td').first
    fulltext = cell.locator('details.record-fulltext')
    assert fulltext.count() == 1, '长内容必须有查看全文入口'
    assert not fulltext.locator('.record-fulltext-content').is_visible()
    fulltext.locator('summary').click()
    assert fulltext.locator('.record-fulltext-content').inner_text() == text
    assert fulltext.locator('.record-fulltext-content').is_visible()
    assert cell.locator('img').count() == 0
    assert not page.evaluate('Boolean(window.ledgerHtmlExecuted)')
    if short:
        fulltext.locator('summary').click()
        page.set_viewport_size({'width': 2560, 'height': 1000})
        fulltext.wait_for(state='hidden')


def test_ledger_speaks_its_scope_and_names_the_other_pages(page):
    intro = page.inner_text('.ledger-intro')
    assert '维护已入库的知识' in intro
    assert '本体建模层' in intro


def test_category_counts_come_from_the_server(page):
    assert category_counts(page) == {'entity': '3', 'relation': '1', 'attribute': '1', 'chunk': '1'}


def test_four_views_are_tabs_and_switch_the_columns(page):
    """台账维护的是「具体的东西」：实体 / 关系 / **属性** / 原文片段。

    属性这一维以前没有视角（查询把它滤掉了），用户点名要它 ——
    所以四个页签的顺序与列都是硬契约，别再把属性漏掉。
    """
    tabs = [node.inner_text().strip() for node in page.query_selector_all('.ledger-tab-label')]
    assert tabs == ['实体', '关系', '属性', '原文片段']
    assert headers(page) == ['名称', '本体类型', '本体归属', '知识修订', '关联断言', '操作']
    select_view(page, 'relation')
    assert headers(page) == ['关系', '关系类型', '本体归属', '知识修订', '关联断言', '操作']
    select_view(page, 'attribute')
    assert headers(page) == ['属性', '所属实体', '本体归属', '知识修订', '关联断言', '操作']
    select_view(page, 'chunk')
    assert headers(page) == ['原文片段', '来源文档', '知识修订', '关联断言', '操作']


def test_attribute_view_reads_predicate_subject_and_value(page):
    """属性行要能读：属性名 = 值、挂在哪个实体上、也能在图谱里定位到那个实体。

    属性本身不是图上的点，所以「在图谱定位」转发的是它所属实体（与关系行转发 subject_id 同一条规矩）。
    """
    select_view(page, 'attribute')
    rows = ledger_rows(page)
    assert len(rows) == 1, rows
    assert rows[0]['name'] == '重量 = 12', rows[0]['name']
    assert '价格说明' in rows[0]['cells'][1].inner_text(), '第二列要说清它挂在哪个实体上'
    page.click('[data-locate-graph="e1"]')
    assert page.evaluate("() => window.__graphLocated") == 'e1', '属性行应转发它所属实体的 id'


def test_every_row_says_which_ontology_version_it_hangs_on(page):
    """「挂在哪一版本体上」＝本体归属：当前版本与旧版本必须能分辨（C2 的前提）。"""
    rows = {(item['name'], item['cells'][2].inner_text().strip()): item for item in ledger_rows(page)}
    assert any(name == '价格说明' and '本体 v1' in ownership and '（历史）' in ownership
               for name, ownership in rows), rows.keys()
    assert any(name == '团购价' and '本体 v2' in ownership and '（当前）' in ownership
               for name, ownership in rows), rows.keys()


def test_ontology_filter_lists_every_immutable_release_and_unknown(page):
    options = page.locator('#record-ontology option').all_inner_texts()
    assert options[0] == '全部本体'
    assert options[-1] == '未知本体'
    assert any('本体 v1' in label and '历史' in label for label in options)
    v2 = [label for label in options if '本体 v2' in label]
    assert len(v2) == 2, options
    assert all('o2' in label for label in v2), '重复的 v2 必须用不可变本体 ID 区分'
    assert any('标注修订' in label and '当前' in label for label in v2)


def test_ontology_filter_always_sends_the_paired_scope_fields(page):
    def last_body():
        return page.evaluate("() => window.recordQueryBodies.at(-1)")

    assert last_body()['ontology_scope'] == 'all'
    assert last_body()['ontology_ids'] is None

    before = page.evaluate("() => window.recordQueryBodies.length")
    page.select_option('#record-ontology', 'id:o1')
    page.wait_for_function("before => window.recordQueryBodies.length > before", arg=before)
    assert last_body()['ontology_scope'] == 'ids'
    assert last_body()['ontology_ids'] == ['o1']

    before = page.evaluate("() => window.recordQueryBodies.length")
    page.select_option('#record-ontology', 'unknown')
    page.wait_for_function("before => window.recordQueryBodies.length > before", arg=before)
    assert last_body()['ontology_scope'] == 'unknown'
    assert last_body()['ontology_ids'] is None

    before = page.evaluate("() => window.recordQueryBodies.length")
    page.select_option('#record-ontology', 'all')
    page.wait_for_function("before => window.recordQueryBodies.length > before", arg=before)
    assert last_body()['ontology_scope'] == 'all'
    assert last_body()['ontology_ids'] is None


def test_ontology_history_navigation_preselects_the_ledger_filter(page):
    before = page.evaluate("() => window.recordQueryBodies.length")
    page.evaluate("() => window.wb.setVersionContext({ontologyScope:'ids',ontologyIds:['o1'],ontologyVersion:1,source:'ontology-history'})")
    page.wait_for_function("before => window.recordQueryBodies.length > before", arg=before)
    page.wait_for_function("() => document.querySelector('#record-ontology')?.value === 'id:o1'")
    assert page.input_value('#record-ontology') == 'id:o1'
    assert page.evaluate("() => window.recordQueryBodies.at(-1)")['ontology_ids'] == ['o1']


def test_record_revision_and_migration_status_are_truthful(page):
    page.click('#ledger-open-model')
    page.get_by_role('button',name='检查本体归属',exact=True).click()
    page.wait_for_function("() => document.querySelector('.ledger-migration-badge').textContent==='待迁移'")
    page.keyboard.press('Escape')
    rows = ledger_rows(page)
    old = next(item for item in rows if item['name'] == '价格说明' and '本体 v1' in item['cells'][2].inner_text())
    assert '修订 r4' in old['row'].inner_text()
    assert '依据本体 v1（历史）' in old['row'].inner_text()
    assert '待迁移' in old['row'].inner_text()

    current = next(item for item in rows if item['name'] == '团购价')
    assert '修订 r1' in current['row'].inner_text()
    assert '依据本体 v2（当前）' in current['row'].inner_text()
    assert '待迁移' not in current['row'].inner_text()
    assert '迁移阻塞' not in current['row'].inner_text()

    select_view(page, 'relation')
    relation = ledger_rows(page)[0]
    assert '修订 r2' in relation['row'].inner_text()
    assert '迁移阻塞' in relation['row'].inner_text()


def test_missing_ontology_and_unavailable_plan_are_explicit(page):
    select_view(page, 'chunk')
    assert '未知本体' in ledger_rows(page)[0]['row'].inner_text()

    page.evaluate("() => { window.mockFailSide = 'reclassify'; }")
    page.click('#load-records')
    page.wait_for_function("() => document.getElementById('records').getAttribute('aria-busy')==='false'",timeout=10000)
    select_view(page, 'entity')
    old = next(item for item in ledger_rows(page) if item['name'] == '价格说明' and '本体 v1' in item['cells'][2].inner_text())
    assert '迁移状态未检查' in old['row'].inner_text()


def test_revision_timeline_orders_immutable_revisions_and_resolves_ontology(revision_page):
    revision_page.wait_for_selector('.record-revision-item[data-revision="3"]', timeout=10000)
    items = revision_page.locator('.record-revision-item').all_inner_texts()
    assert [text.splitlines()[0] for text in items] == ['修订 r3 · 当前', '修订 r2 · 已取代', '修订 r1 · 已取代']
    assert 'revision-…0003' in items[0]
    assert '实体' in items[0]
    assert '本体 v2（当前）' in items[0]
    assert '已删除' in items[1], '删除状态与“已取代”是两个独立维度，都要显示'
    assert '本体 v1（历史）' in items[1]
    detail = revision_page.inner_text('#record-revision-detail')
    assert '手动变更' in detail
    assert '知识维护员 (editor; user:editor)' in detail


def test_revision_timeline_old_selection_is_read_only(revision_page):
    revision_page.wait_for_selector('.record-revision-item[data-revision="2"]', timeout=10000)
    revision_page.click('.record-revision-item[data-revision="2"]')
    detail = revision_page.inner_text('#record-revision-detail')
    assert '修订 r2 · 已取代' in detail
    assert '第二次修订' in detail
    assert '只读历史快照' in detail
    assert revision_page.is_hidden('#record-structured-edit')
    assert revision_page.is_hidden('#record-advanced-edit')
    assert revision_page.query_selector('#record-revision-detail button[type="submit"]') is None


def test_revision_timeline_failure_keeps_current_detail_and_can_retry(revision_page):
    revision_page.evaluate("() => { window.mockFailSide = 'history'; window.historyFor(window.timelineRecord); }")
    revision_page.wait_for_selector('[data-retry-revision-timeline]', timeout=10000)
    assert '当前仍可查看' in revision_page.inner_text('#record-revision-detail')
    assert '历史修订读取失败' in revision_page.inner_text('#record-history-view')
    before = revision_page.evaluate("() => window.historyRequests")
    revision_page.evaluate("() => { window.mockFailSide = null; }")
    revision_page.click('[data-retry-revision-timeline]')
    revision_page.wait_for_function("before => window.historyRequests > before", arg=before)
    revision_page.wait_for_selector('.record-revision-item[data-revision="3"]', timeout=10000)


def test_supporting_assertion_count_merges_both_lineages_without_double_counting(page):
    """关联断言：候选血缘 + 已归位血缘，同一句只算一次（assert-3 两条血缘都指着 e3 → 只能算 1）。"""
    rows = ledger_rows(page)
    # assert-1 走"候选血缘"（metadata.discovery_candidate_id），assert-2 走"已归位血缘"（canonical_record_id）。
    # 按"这一行挂在哪一版本体上"定位：e1（挂 v1）两条血缘都命中 → 2；e3（挂 v2）一条都没命中 → 0。
    support = {(item['name'], item['cells'][2].inner_text().strip()): item['cells'][4].inner_text().strip()
               for item in rows}
    assert [value for (name, ownership), value in support.items()
            if name == '价格说明' and '本体 v1' in ownership][0].startswith('2'), support
    assert [value for (name, ownership), value in support.items()
            if name == '价格说明' and '本体 v2' in ownership][0].startswith('1'), support
    assert [value for (name, ownership), value in support.items()
            if name == '团购价'][0].startswith('0'), support


def test_linked_assertions_show_review_state_without_claiming_all_are_support(page):
    """关联断言不等于当前修订的有效支撑；列表必须把审核状态说出来。"""
    rows = ledger_rows(page)
    first = [item for item in rows
             if item['name'] == '价格说明' and '本体 v1' in item['cells'][2].inner_text()][0]
    text = first['cells'][4].inner_text().replace('\n', ' ')
    assert '2 条关联断言' in text, text
    assert '已采纳 1' in text and '待审核 1' in text, text
    assert '支撑这条知识' not in first['cells'][4].get_attribute('title'), \
        '关联断言可能仍待审或属于旧修订，不能一概宣称为有效支撑'


def test_undo_is_reachable_and_hits_the_undo_endpoint(page):
    page.get_by_role('button', name='可撤销的操作', exact=True).click()
    page.wait_for_selector('.ledger-operation')
    assert '合并实体' in page.get_by_role('dialog', name='可撤销的操作').inner_text()
    page.click('[data-undo-operation="op1"]')
    page.wait_for_function("() => window.undoCalls && window.undoCalls.length === 1", timeout=5000)
    assert page.evaluate("() => window.undoCalls") == ['op1']
    assert 'POST /api/projects/p1/operations/op1/undo' in page.evaluate("() => window.apiLog")
    # 请求发出 ≠ 回执已渲染：等那句话说出口，别抢在微任务前面断言
    page.wait_for_function("() => (window.__status?.message||'').includes('已撤销')", timeout=5000)


@pytest.mark.parametrize(('kind', 'record_id', 'version'), [('entity', 'e1', 4), ('relation', 'r1', 2), ('attribute', 'a1', 1)])
def test_ledger_deletes_the_selected_revision_and_refreshes(page, kind, record_id, version):
    page.add_script_tag(path=str(WEB / 'knowledge-changes.js'))
    page.evaluate('window.refreshKnowledgeGraph = detail => { window.graphChange = detail; }')
    select_view(page, kind)
    row = page.locator(f'[data-record-id="{record_id}"]')
    row.get_by_role('button', name='删除', exact=True).click()
    confirmation = page.get_by_role('dialog', name='删除', exact=False)
    confirmation.wait_for()
    assert page.evaluate('window.deleteCalls') == []
    confirmation.get_by_role('button', name='确认删除', exact=True).click()
    page.wait_for_function("window.__status?.message.includes('可撤销的操作')")
    assert not confirmation.is_visible()
    assert row.count() == 0
    assert page.evaluate('window.deleteCalls') == [{'record_id': record_id, 'expected_version': version}]
    assert page.evaluate('window.graphChange.kind') == 'delete'


def test_ledger_delete_can_be_cancelled_and_requires_a_current_admin_scope(page):
    row = page.locator('[data-record-id="e1"]')
    remove = row.get_by_role('button', name='删除', exact=True)
    remove.click()
    confirmation = page.get_by_role('dialog', name='删除实体？')
    assert '价格说明' in confirmation.inner_text()
    assert '关联关系和属性' in confirmation.inner_text()
    assert '可撤销的操作' in confirmation.inner_text()
    cancel = confirmation.get_by_role('button', name='取消', exact=True)
    assert cancel.evaluate('button => document.activeElement === button')
    cancel.click()
    assert not confirmation.is_visible()
    assert remove.evaluate('button => document.activeElement === button')
    remove.click()
    page.keyboard.press('Escape')
    assert not confirmation.is_visible()
    assert page.evaluate('window.deleteCalls') == []
    page.evaluate("window.scope = () => ({known_at: '2026-09-01T00:00:00Z'})")
    page.click('#load-records')
    row.wait_for()
    assert row.get_by_role('button', name='删除', exact=True).count() == 0
    page.evaluate('window.scope = () => ({}); window.Auth.isAdmin = () => false;')
    page.click('#load-records')
    row.wait_for()
    assert row.get_by_role('button', name='删除', exact=True).count() == 0
    page.evaluate('window.Auth.isAdmin = () => true')
    select_view(page, 'chunk')
    page.locator('[data-record-id="c1"]').wait_for()
    assert page.locator('[data-record-id="c1"]').get_by_role('button', name='删除', exact=True).count() == 0


def test_ledger_delete_keeps_the_row_on_revision_conflict(page):
    page.evaluate("window.mockFailSide = 'delete'")
    remove = page.locator('[data-record-id="e1"]').get_by_role('button', name='删除', exact=True)
    remove.click()
    confirmation = page.get_by_role('dialog', name='删除实体？')
    confirm = confirmation.get_by_role('button', name='确认删除', exact=True)
    confirm.click()
    feedback = confirmation.get_by_role('alert')
    feedback.wait_for()
    assert '版本冲突' in feedback.inner_text()
    assert confirm.is_enabled()
    confirmation.get_by_role('button', name='取消', exact=True).click()
    assert remove.is_enabled()


def test_ledger_delete_rechecks_scope_after_opening_confirmation(page):
    page.set_viewport_size({'width': 320, 'height': 800})
    page.locator('[data-record-id="e1"]').get_by_role('button', name='删除', exact=True).click()
    confirmation = page.get_by_role('dialog', name='删除实体？')
    box = confirmation.bounding_box()
    assert box and box['x'] >= 0 and box['x'] + box['width'] <= 320
    page.evaluate("window.scope = () => ({known_at: '2026-09-01T00:00:00Z'})")
    confirmation.get_by_role('button', name='确认删除', exact=True).click()
    assert '范围' in confirmation.get_by_role('alert').inner_text()
    assert page.evaluate('window.deleteCalls') == []


def test_ledger_tools_open_on_demand_and_return_to_the_same_list(page):
    assert not page.locator('#ledger-operations').is_visible()
    assert not page.locator('#resolve-text').is_visible()
    assert 'GET /api/projects/p1/operations' not in page.evaluate('window.apiLog')
    select_view(page, 'relation')
    tool = page.get_by_role('button', name='可撤销的操作', exact=True)
    tool.click()
    drawer = page.get_by_role('dialog', name='可撤销的操作')
    assert drawer.is_visible()
    drawer.locator('[data-undo-operation="op1"]').wait_for()
    page.keyboard.press('Escape')
    drawer.wait_for(state='hidden')
    assert tool.evaluate('button => document.activeElement === button')
    assert page.locator('table.ledger-relation').is_visible()
    assert not page._errors


def test_review_drawer_keeps_ledger_open_at_mobile_width(page):
    page.evaluate("""() => {
      const base = window.api;
      document.getElementById('title').textContent = '知识台账';
      window.bind = () => {};
      window.reviewReads = 0;
      window.api = (url, ...args) => {
        if (url.endsWith('/reviews')) { window.reviewReads++; return Promise.resolve({reviews: []}); }
        if (url.endsWith('/ontology')) return Promise.resolve({id: 'o1'});
        if (url.endsWith('/health')) return Promise.resolve({capabilities: ['typed_reviews']});
        if (url.endsWith('/attribute-conflicts')) return Promise.resolve({conflict_sets: []});
        return base(url, ...args);
      };
    }""")
    page.add_script_tag(path=str(WEB / 'task-review.js'))
    assert page.evaluate('window.reviewReads') == 0
    assert page.get_by_role('button', name='新增实体', exact=True).is_visible()
    assert not page.locator('.ledger-entry-actions [data-create-record="relation"]').is_visible()
    page.click('[data-ledger-view="relation"]')
    assert page.get_by_role('button', name='新增关系', exact=True).is_visible()
    assert not page.locator('.ledger-entry-actions [data-create-record="entity"]').is_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.click('#ledger-open-reviews')
    drawer = page.get_by_role('dialog', name='知识审核')
    drawer.wait_for()
    page.wait_for_function("document.getElementById('review-view-facts').textContent === '待审核事实 0'")
    assert drawer.locator('.entity-review-card').count() == 0
    assert page.locator('#tab-records').is_visible()
    assert page.inner_text('#title') == '知识台账'
    assert abs(drawer.bounding_box()['width'] - 390) <= 1
    drawer.get_by_role('button', name='关闭 ×').click()
    assert page.locator('#ledger-open-reviews').evaluate('button => document.activeElement === button')
    assert page.locator('[data-ledger-view="relation"]').get_attribute('aria-selected') == 'true'
    assert page.evaluate('window.reviewReads') == 1
    assert not page._errors


def test_review_drawer_lists_conflicts_compactly_and_loads_more(page):
    page.evaluate("""() => {
      window.bind = () => {};
      window.api = async (url, body) => {
        if (url.endsWith('/reviews')) return {reviews: []};
        if (url.endsWith('/ontology')) return {id: 'o1'};
        if (url.endsWith('/health')) return {capabilities: ['typed_reviews']};
        if (url.endsWith('/attribute-conflicts')) {
          return {conflict_sets: [{id: body.cursor || 'first', entity_id: body.cursor ? 'e2' : 'e1',
            predicate: '重量', members: [{}, {}], version: 1}], next_cursor: body.cursor ? null : 'second'};
        }
        throw Error(url);
      };
    }""")
    page.add_script_tag(path=str(WEB / 'task-review.js'))
    page.click('#ledger-open-reviews')
    drawer = page.get_by_role('dialog', name='知识审核')
    drawer.get_by_role('heading', name='属性冲突集合', exact=True).wait_for()
    assert drawer.locator('.entity-review-card').count() == 0
    assert drawer.get_by_role('button', name='处理属性冲突集合').count() == 1
    more = drawer.get_by_role('button', name='更多属性冲突集合')
    more.click()
    assert drawer.get_by_role('button', name='处理属性冲突集合').count() == 2
    assert not more.is_visible()


def test_operations_list_is_newest_first_and_says_how_many(page):
    """审计记录回来的顺序不等于时间顺序：列表必须自己按时间倒序，并写明共几条。

    这条来自真机：一次验收下来列表里堆了十几条，"哪条才是刚做的那个动作"看不出来。
    """
    page.get_by_role('button', name='可撤销的操作', exact=True).click()
    page.wait_for_selector('.ledger-operation')
    rows = [row.inner_text().replace('\n', ' ') for row in page.query_selector_all('.ledger-operation')]
    assert len(rows) == 2, rows
    assert rows[0].startswith('软删除'), f'最新的（delete 2026-10-02）必须排在最上面：{rows}'
    assert rows[1].startswith('合并实体'), rows
    assert '共 2 条' in page.inner_text('.ledger-operations-head'), page.inner_text('.ledger-operations-head')


@pytest.mark.parametrize('legacy_server', [False, True])
def test_operations_show_merge_direction_and_exact_changes_on_demand(page, legacy_server):
    page.add_script_tag(path=str(WEB / 'ingest-mode.js'))
    page.evaluate('legacy => { window.noOperationDetailEndpoint = legacy; }', legacy_server)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.click('#ledger-open-operations')
    operation = page.locator('.ledger-operation').filter(has_text='合并实体')
    expand = operation.get_by_role('button', name='查看详情', exact=True)
    assert 'GET /api/projects/p1/operations/op1' not in page.evaluate('window.apiLog')
    expand.click()
    operation.get_by_text('操作前 → 操作后', exact=True).wait_for()
    detail = operation.locator('.ledger-operation-details')
    text = detail.inner_text()
    assert '合并后继续使用「标准价格」' in text and '停用 1 条重复记录（参考价格）' in text
    assert all(value in text for value in ['保证金', '抖音电商', '2026年06月17日', '平台规则.md', '转接 1 条关系'])
    assert all(value in text for value in ['r5 → r6', '起点', '已停用', '别名'])
    assert '后续编辑' not in text
    assert detail.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    operation.get_by_role('button', name='收起详情', exact=True).click()
    assert not detail.is_visible()
    expand.click()
    assert detail.is_visible()
    assert page.evaluate('window.apiLog').count('GET /api/projects/p1/operations/op1') == 1
    assert ('GET /api/projects/p1/records/r2/history' in page.evaluate('window.apiLog')) == legacy_server
    assert page.evaluate('window.undoCalls') == []


def test_operations_retry_details_and_show_legacy_before_snapshot(page):
    page.evaluate("window.mockFailSide = 'operation-detail'")
    page.click('#ledger-open-operations')
    operation = page.locator('.ledger-operation').filter(has_text='软删除')
    operation.get_by_role('button', name='查看详情', exact=True).click()
    operation.get_by_text('读取超时', exact=False).wait_for()
    page.evaluate('window.mockFailSide = null')
    operation.get_by_role('button', name='重试读取详情').click()
    operation.get_by_text('旧价格', exact=True).wait_for()
    assert '操作后快照未保存' in operation.inner_text()


def test_same_name_operation_shows_each_original_source_under_its_role(page):
    page.evaluate('''() => {
      window.sameNameMerge=true;window.noOperationDetailEndpoint=true;
      window.SourceReader={open:options=>{window.openedSource=options;}};
    }''')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.click('#ledger-open-operations')
    operation = page.locator('.ledger-operation').filter(has_text='合并实体')
    operation.get_by_role('button', name='查看详情', exact=True).click()
    operation.get_by_text('合并了 2 条同名记录「违规儿童模特肖像」', exact=True).wait_for()
    continued = operation.get_by_role('listitem').filter(has_text='合并后继续使用')
    retired = operation.get_by_role('listitem').filter(has_text='停用的重复记录')
    continued.get_by_text('《实施细则》意见征集通知', exact=True).wait_for()
    retired.get_by_text('【已失效】旧规则解读', exact=True).wait_for()
    retired.get_by_role('button', name='查看原文', exact=True).click()
    assert page.evaluate('window.openedSource.versionId') == 'source-retired'
    assert page.evaluate('window.undoCalls') == []


def test_duplicate_lookup_in_the_drawer_shows_selectable_cards(page):
    page.click('#ledger-open-governance')
    page.click('#ledger-manual-merge')
    page.click('#ledger-find-duplicates')
    page.wait_for_function("document.getElementById('ledger-duplicate-feedback').textContent.includes('找到 1 组')")
    page.wait_for_selector('#resolve-result .candidate [data-keep]', timeout=5000)
    cards = page.locator('#resolve-result .candidate')
    cards.nth(0).locator('[data-keep]').click()
    cards.nth(1).locator('[data-drop]').click()
    keep = page.input_value('#keep-id')
    drop = page.input_value('#drop-id')
    assert keep and drop and keep != drop, (keep, drop)


def test_duplicate_scan_controls_align_in_the_drawer(page, tmp_path):
    page.add_style_tag(path=str(WEB / 'theme.css'))
    page.add_style_tag(path=str(WEB / 'entity-governance.css'))
    page.add_script_tag(path=str(WEB / 'entity-governance.js'))
    page.evaluate('''() => {
      const api = window.api;
      window.api = (path, ...args) => {
        if (path.endsWith('/duplicates/scan')) return Promise.resolve({groups: [], next_cursor: 'next'});
        if (path.endsWith('/resolution-reviews')) return Promise.resolve({reviews: []});
        return api(path, ...args);
      };
    }''')
    page.click('#ledger-open-governance')
    drawer = page.locator('#ledger-governance-drawer')
    scanner = drawer.locator('.duplicate-scanner')
    scan = scanner.get_by_role('button', name='扫描已有重复实体', exact=True)
    more = scanner.get_by_role('button', name='继续扫描')
    width_before = scan.bounding_box()['width']
    scan.click()
    more.wait_for()
    assert scan.bounding_box()['width'] == pytest.approx(width_before, abs=1)

    for width in (1560, 360):
        page.set_viewport_size({'width': width, 'height': 900})
        modes = [drawer.locator(f'#{key}').bounding_box() for key in ('ledger-open-resolution', 'ledger-manual-merge')]
        actions = [control.bounding_box() for control in (scan, more)]
        scope = scanner.locator('#duplicate-scan-scope').bounding_box()
        for left, right in (modes, actions):
            assert left['width'] == pytest.approx(right['width'], abs=1)
            assert left['y'] == pytest.approx(right['y'], abs=1)
        assert all(box['height'] == pytest.approx(scope['height'], abs=1) for box in [*modes, *actions])
        assert scope['x'] + scope['width'] == pytest.approx(actions[1]['x'] + actions[1]['width'], abs=1)
        assert actions[1]['x'] + actions[1]['width'] <= width
        scanner.locator('#duplicate-scan-scope').select_option('related')
        assert scan.is_disabled()
        assert scanner.get_by_role('searchbox', name='文档关键词').is_visible()
        scanner.locator('#duplicate-scan-scope').select_option('all')
        scan.click()
        more.wait_for()
    drawer.screenshot(path=str(tmp_path / 'duplicate-scan-controls.png'))


def test_merge_preview_states_the_impact_before_confirming(page):
    """合并是不可逆动作：确认之前必须看到影响面，且口径写清楚是按哪些记录统计的。"""
    page.click('#ledger-open-governance')
    page.click('#ledger-manual-merge')
    page.click('#resolve-entity')
    page.wait_for_selector('#resolve-result [data-keep]', timeout=5000)
    cards = page.locator('#resolve-result .candidate')
    cards.nth(0).locator('[data-keep]').click()               # e1 保留
    prompt = page.locator('.governance-merge-preview')
    heading = prompt.locator('b').bounding_box()
    instruction = prompt.locator('span').bounding_box()
    assert heading['width'] >= 105 and heading['y'] + heading['height'] <= instruction['y'] + 1
    cards.nth(1).locator('[data-drop]').click()               # e3 合并
    assert prompt.locator('small').bounding_box()['width'] >= prompt.bounding_box()['width'] - 30
    preview = page.inner_text('.governance-merge-preview')
    assert '影响面' in preview and '条关系' in preview
    assert '关联断言' in preview and '原文断言' not in preview
    assert '仅当前页' in preview, '当前页参考不能冒充完整服务端预检'
    assert page.is_disabled('#merge-entities') is True        # 还没勾确认
    page.check('#merge-confirm')
    assert page.is_disabled('#merge-entities') is False


def test_ledger_never_blanks_the_table_when_a_side_request_fails(page):
    """旁数据（本体版本 / 断言 / 操作 / 分组）任一读不到，表格也不能消失 —— 那一格如实写"还没读到"。"""
    page.evaluate("() => { window.mockFailSide = 'duplicate-groups'; }")
    page.click('#load-records')
    page.wait_for_function("() => document.getElementById('records').getAttribute('aria-busy')==='false'")
    page.click('#ledger-open-governance')
    page.click('#ledger-manual-merge')
    page.click('#ledger-find-duplicates')
    page.wait_for_function("document.getElementById('ledger-duplicate-feedback').textContent.includes('治理信息读取失败')")
    assert page.query_selector_all('.record-library tbody tr'), '分组接口挂了，表格也不能消失'
    assert not page._errors


def test_ledger_updates_enrichment_without_replacing_focused_links_or_open_details(page):
    select_view(page, 'relation')
    page.evaluate("""() => {
      const fetch = window.fetch;
      const gate = new Promise(resolve => { window.releaseLedgerDetails = resolve; });
      window.fetch = async (url, options) => {
        const body = JSON.parse(options?.body || '{}');
        if (/\\/ontologies|\\/assertions\\/batch/.test(url) || body.include_total === false) {
          await gate;
        }
        const response = await fetch(url, options);
        if (!url.endsWith('/records/page') || body.kinds?.[0] !== 'relation') return response;
        const result = await response.json();
        const row = result.records[0];
        result.records = [row, ...Array.from({length: 49}, (_, index) => ({...row, id: 'extra-' + index}))];
        result.total = 50;
        return new Response(JSON.stringify(result), {headers: {'Content-Type': 'application/json'}});
      };
    }""")
    page.click('#load-records')
    row = page.locator('tr[data-record-id="r1"]')
    row.wait_for()
    assert '本体归属读取中' in row.locator('td').nth(2).inner_text()
    assert '读取中' in row.locator('td').nth(4).inner_text()
    page.evaluate("""() => {
      const row = document.querySelector('tr[data-record-id="r1"]');
      const detail = [...row.querySelectorAll('details')].find(item => item.textContent.includes('原文上下文'));
      detail.open = true;
      const link = row.querySelector('[data-ledger-entity="e1"]');
      link.focus();
      const rows = [...document.querySelectorAll('#records tbody tr')];
      window.ledgerBeforeEnrichment = {rows, detail, link};
      const start = performance.now();
      const records = document.getElementById('records');
      const observer = new MutationObserver(() => {
        if (records.getAttribute('aria-busy') !== 'false') return;
        observer.disconnect();
        window.ledgerEnrichmentMs = performance.now() - start;
      });
      observer.observe(records, {attributes: true, attributeFilter: ['aria-busy']});
      window.releaseLedgerDetails();
    }""")
    page.wait_for_function("document.querySelector('#records').getAttribute('aria-busy') === 'false'")
    assert '本体 v1' in row.locator('td').nth(2).inner_text()
    assert row.locator('td').nth(4).locator('b').inner_text() == '0'
    assert row.locator('[data-ledger-entity="e1"]').inner_text() == '价格说明'
    metrics = page.evaluate("""() => {
      const {rows, detail, link} = window.ledgerBeforeEnrichment;
      return {rows: rows.length, removed: rows.filter(row => !row.isConnected).length,
        open: detail.isConnected && detail.open, focused: document.activeElement === link,
        elapsed_ms: window.ledgerEnrichmentMs};
    }""")
    print('Ledger enrichment:', metrics)
    assert metrics['rows'] == 50 and metrics['removed'] == 0
    assert metrics['open'] and metrics['focused']
    assert not page._errors


# —— D1「台账 ⇄ 图谱双向直达」的浏览器契约 ——
# 台账里一个实体/关系要点得到「在图谱定位」，点下去要把**它自己**（实体用 id、关系用 subject_id，
# 与检索命中的口径一致）传给图谱，而不是概略地切页就算了。

def test_entity_row_locate_button_forwards_the_entity_id_to_the_graph(page):
    buttons = page.query_selector_all('[data-locate-graph]')
    assert buttons, '实体行的「在图谱定位」按钮必须存在'
    # e2 = 团购价；它自己的 id 必须被转发，且进图谱前先按住自动补画、切到检索页。
    page.click('[data-locate-graph="e2"]')
    assert page.evaluate("() => window.__graphLocated") == 'e2'
    assert page.evaluate("() => window.__tab") == 'search'
    assert page.evaluate("() => window.__suppressed") is True, '进图谱前必须先按下下一次自动补画，否则实体刚选中就被冲掉'


def test_relation_row_locate_button_forwards_the_exact_relation(page):
    select_view(page, 'relation')          # 切到「关系」视角，只有 r1 一行
    assert page.query_selector_all('[data-locate-graph="r1"]'), '关系行应定位这条关系及其两个端点'
    page.click('[data-locate-graph="r1"]')
    assert page.evaluate("() => window.__graphLocated") == 'r1'
    assert page.evaluate("() => window.__graphRecord.kind") == 'relation'


def test_chunk_rows_have_no_locate_button(page):
    select_view(page, 'chunk')          # 「原文片段」不是图谱节点，不该给「在图谱定位」
    assert page.query_selector_all('[data-locate-graph]') == []


def test_focus_ledger_record_locates_and_highlights_the_row(page):
    """图谱里点「在台账查看」→ 跳到台账、切到正确视角并高亮那一行（不是把用户晾在列表顶上）。"""
    page.evaluate("() => window.focusLedgerRecord({id:'e2',kind:'entity'})")
    page.wait_for_selector('tr.ledger-row-located[data-record-id="e2"]', timeout=5000)
    assert page.evaluate("() => document.querySelector('[data-record-id=e2]')") is not None
    # 定位要清掉上一次搜索，否则目标行可能被过滤掉、找不到。
    assert page.input_value('#record-search') == 'e2'
    assert page.evaluate("() => window.recordQueryBodies.some(b => b.kinds?.[0]==='entity' && b.limit===50 && b.record_ids?.[0]==='e2' && !b.query)")
    assert not page.evaluate("() => window.recordQueryBodies.some(b => b.kinds?.[0]!=='entity' && b.record_ids?.includes('e2') && b.limit===1)")


def test_category_switch_clears_exact_location_but_keeps_user_search(page):
    page.evaluate("() => window.focusLedgerRecord({id:'e2',kind:'entity'})")
    page.wait_for_selector('tr.ledger-row-located[data-record-id="e2"]')
    select_view(page, 'relation')
    page.wait_for_selector('table.ledger-relation tr[data-record-id="r1"]')
    assert page.input_value('#record-search') == ''
    page.fill('#record-search', '价格说明')
    select_view(page, 'entity')
    assert page.input_value('#record-search') == '价格说明'


@pytest.mark.parametrize('kind,target', [('relation', 'e1'), ('relation', 'e2'), ('attribute', 'e1')])
def test_ledger_endpoint_links_open_exact_owner_even_with_duplicate_names(page, kind, target):
    select_view(page, kind)
    button = page.locator(f'.record-library [data-ledger-entity="{target}"]')
    assert button.count() == 1
    button.click()
    page.wait_for_selector(f'table.ledger-entity tr.ledger-row-located[data-record-id="{target}"]')
    assert page.locator('table.ledger-entity tbody tr').count() == 1
    page.fill('#record-search', '价格说明')
    page.wait_for_function("() => document.querySelectorAll('table.ledger-entity tbody tr').length===2")
