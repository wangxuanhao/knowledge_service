"""知识台账（实例层）的真实浏览器契约：三视角 / 本体归属 / 原文断言 / 撤销入口 / 疑似重复。

为什么要有这一份：台账的价值全在"数字与列说的是不是真的"——
  · 顶部四个数字必须来自服务端（不是前端猜的、也不是写死的 0）；
  · 「挂在哪一版本体上」必须能区分**当前本体**与**旧版本体**（C2 受控重分类要解决的就是旧结构）；
  · 「原文断言」是对"删了会不会丢证据"的回答，必须能命中两种情况且去重；
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
  window.endpoint=suffix=>'/api/projects/p1'+suffix;
  window.api=async(url,body,method)=>{
    const options={method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'}};
    if(options.method!=='GET')options.body=JSON.stringify(body||{});
    const response=await fetch(url,options);
    const payload=await response.json();
    if(!response.ok)throw Object.assign(new Error(payload.error||payload.detail||'请求失败'),payload);
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
    window.timelineRecord,
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
    if(parsed.pathname.endsWith('/records/query')) {
      const body = JSON.parse(options.body||'{}');
      window.recordQueryBodies.push(body);
      const filtered = body.ontology_scope==='ids'
        ? records.filter(row=>(body.ontology_ids||[]).includes(row.ontology_id))
        : body.ontology_scope==='unknown'
          ? records.filter(row=>!row.ontology_id||!['o1','o2','o2-note'].includes(row.ontology_id))
          : records;
      return payload({total:filtered.length,records:filtered});
    }
    if(parsed.pathname.endsWith('/ontologies')) return payload({versions:[
      {id:'o1',version:1,version_reused:false,created_at:'2026-01-01T00:00:00Z'},
      {id:'o2',version:2,version_reused:false,created_at:'2026-02-01T00:00:00Z'},
      {id:'o2-note',version:2,version_reused:true,created_at:'2026-03-01T00:00:00Z'}]});
    if(parsed.pathname.endsWith('/assertions')) return payload({total:3,assertions:[
      {id:'assert-1',canonical_record_id:null,status:'pending'},
      {id:'assert-2',canonical_record_id:'e1',status:'accepted'},
      {id:'assert-3',canonical_record_id:'e3',status:'accepted'}]});
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
        _audit:true,created_at:'2026-10-01T12:00:00Z',before:[{id:'e4'},{id:'e5'}]}},
      {id:'audit:op2',recorded_at:'2026-10-02T09:00:00Z',metadata:{operation:'delete',operation_id:'op2',
        _audit:true,created_at:'2026-10-02T09:00:00Z',before:[{id:'e6'}]}}]});
    if(parsed.pathname.endsWith('/reclassify')) {
      if(window.mockFailSide==='reclassify') return new Response('{"error":"boom"}',{status:500,headers:{'Content-Type':'application/json'}});
      return payload({current_ontology_id:'o2-note',current_version:'本体 v2',stale_records:2,
        migratable_records:1,unmapped_records:1,groups:[
          {key:'o1:entity:urn:ontology:文档标题',migratable:true,record_count:1,records:[{id:'e1'}]},
          {key:'o1:relation:urn:ontology:可能是',migratable:false,record_count:1,records:[{id:'r1'}]},
        ]});
    }
    if(parsed.pathname.endsWith('/operations/op1/undo')) { window.undoCalls.push('op1'); return payload({restored:2}); }
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
    pg.add_script_tag(path=str(WEB / 'records-view.js'))
    pg.click('[data-tab="records"]')
    pg.wait_for_selector('.ledger-kpi', timeout=10000)
    pg.wait_for_function("() => [...document.querySelectorAll('.ledger-kpi b')].every(node => node.textContent.trim() !== '—')",
                         timeout=10000)
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


def kpis(page):
    return {node.get_attribute('data-ledger-kpi'): node.inner_text().strip()
            for node in page.query_selector_all('.ledger-kpi')}


def headers(page):
    return [cell.inner_text().strip() for cell in page.query_selector_all('.record-library thead th')]


def test_ledger_speaks_its_scope_and_names_the_other_pages(page):
    """一句话边界：这里管具体的东西；改分类、审结构都去「本体建模层」。"""
    intro = page.inner_text('.ledger-intro')
    assert '具体的东西' in intro
    assert '本体建模层' in intro


def test_top_numbers_come_from_the_server_not_from_guesswork(page):
    numbers = kpis(page)
    assert numbers['entity'].startswith('3') and '实体' in numbers['entity']
    assert numbers['relation'].startswith('1') and '关系' in numbers['relation']
    assert numbers['attribute'].startswith('1') and '属性' in numbers['attribute']
    assert numbers['chunk'].startswith('1') and '原文片段' in numbers['chunk']
    assert numbers['duplicates'].startswith('1') and '疑似重复' in numbers['duplicates']


def test_four_views_are_tabs_and_switch_the_columns(page):
    """台账维护的是「具体的东西」：实体 / 关系 / **属性** / 原文片段。

    属性这一维以前没有视角（查询把它滤掉了），用户点名要它 ——
    所以四个页签的顺序与列都是硬契约，别再把属性漏掉。
    """
    tabs = [node.inner_text().strip() for node in page.query_selector_all('.ledger-tab')]
    assert tabs == ['实体', '关系', '属性', '原文片段']
    assert headers(page) == ['名称', '本体类型', '本体归属', '知识修订', '原文断言', '操作']
    page.click('.ledger-tab:nth-child(2)')
    assert headers(page) == ['关系', '关系类型', '本体归属', '知识修订', '原文断言', '操作']
    page.click('.ledger-tab:nth-child(3)')
    assert headers(page) == ['属性', '所属实体', '本体归属', '知识修订', '原文断言', '操作']
    page.click('.ledger-tab:nth-child(4)')
    assert headers(page) == ['原文片段', '来源文档', '知识修订', '原文断言', '操作']


def test_attribute_view_reads_predicate_subject_and_value(page):
    """属性行要能读：属性名 = 值、挂在哪个实体上、也能在图谱里定位到那个实体。

    属性本身不是图上的点，所以「在图谱定位」转发的是它所属实体（与关系行转发 subject_id 同一条规矩）。
    """
    page.click('.ledger-tab:nth-child(3)')
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

    page.click('.ledger-tab:nth-child(2)')
    relation = ledger_rows(page)[0]
    assert '修订 r2' in relation['row'].inner_text()
    assert '迁移阻塞' in relation['row'].inner_text()


def test_missing_ontology_and_unavailable_plan_are_explicit(page):
    page.click('.ledger-tab:nth-child(4)')
    assert '未知本体' in ledger_rows(page)[0]['row'].inner_text()

    page.evaluate("() => { window.mockFailSide = 'reclassify'; }")
    page.click('#load-records')
    page.wait_for_function("() => document.querySelector('.ledger-kpi[data-ledger-kpi=duplicates] b').textContent.trim() !== '—'",
                           timeout=10000)
    page.click('.ledger-tab:nth-child(1)')
    old = next(item for item in ledger_rows(page) if item['name'] == '价格说明' and '本体 v1' in item['cells'][2].inner_text())
    assert '迁移状态未知' in old['row'].inner_text()


def test_revision_timeline_orders_immutable_revisions_and_resolves_ontology(revision_page):
    revision_page.wait_for_selector('.record-revision-item[data-revision="3"]', timeout=10000)
    items = revision_page.locator('.record-revision-item').all_inner_texts()
    assert [text.splitlines()[0] for text in items] == ['修订 r3 · 当前', '修订 r2 · 已取代', '修订 r1 · 已取代']
    assert 'revision-…0003' in items[0]
    assert '实体' in items[0]
    assert '本体 v2（当前）' in items[0]
    assert '已删除' in items[1], '删除状态与“已取代”是两个独立维度，都要显示'
    assert '本体 v1（历史）' in items[1]


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
    """原文断言：候选血缘 + 已归位血缘，同一句只算一次（assert-3 两条血缘都指着 e3 → 只能算 1）。"""
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


def test_undo_is_reachable_and_hits_the_undo_endpoint(page):
    section = page.inner_text('#ledger-operations')
    assert '可撤销的操作' in section and '合并实体' in section
    page.click('[data-undo-operation="op1"]')
    page.wait_for_function("() => window.undoCalls && window.undoCalls.length === 1", timeout=5000)
    assert page.evaluate("() => window.undoCalls") == ['op1']
    assert 'POST /api/projects/p1/operations/op1/undo' in page.evaluate("() => window.apiLog")
    # 请求发出 ≠ 回执已渲染：等那句话说出口，别抢在微任务前面断言
    page.wait_for_function("() => (window.__status?.message||'').includes('已撤销')", timeout=5000)


def test_operations_list_is_newest_first_and_says_how_many(page):
    """审计记录回来的顺序不等于时间顺序：列表必须自己按时间倒序，并写明共几条。

    这条来自真机：一次验收下来列表里堆了十几条，"哪条才是刚做的那个动作"看不出来。
    """
    rows = [row.inner_text().replace('\n', ' ') for row in page.query_selector_all('.ledger-operation')]
    assert len(rows) == 2, rows
    assert rows[0].startswith('软删除'), f'最新的（delete 2026-10-02）必须排在最上面：{rows}'
    assert rows[1].startswith('合并实体'), rows
    assert '共 2 条' in page.inner_text('.ledger-operations-head'), page.inner_text('.ledger-operations-head')


def test_duplicate_number_opens_the_merge_wizard_with_ready_made_cards(page):
    """「疑似重复」不是一个死数字：点它就摆出可直接选角色的卡片。"""
    page.click('.ledger-kpi[data-ledger-kpi="duplicates"]')
    page.wait_for_selector('#resolve-result .candidate [data-keep]', timeout=5000)
    cards = page.locator('#resolve-result .candidate')
    cards.nth(0).locator('[data-keep]').click()
    cards.nth(1).locator('[data-drop]').click()
    keep = page.input_value('#keep-id')
    drop = page.input_value('#drop-id')
    assert keep and drop and keep != drop, (keep, drop)


def test_merge_preview_states_the_impact_before_confirming(page):
    """合并是不可逆动作：确认之前必须看到影响面，且口径写清楚是按哪些记录统计的。"""
    page.click('.record-governance > summary')       # 这一节默认折叠，先展开
    page.click('#resolve-entity')
    page.wait_for_selector('#resolve-result [data-keep]', timeout=5000)
    cards = page.locator('#resolve-result .candidate')
    cards.nth(0).locator('[data-keep]').click()               # e1 保留
    cards.nth(1).locator('[data-drop]').click()               # e3 合并
    preview = page.inner_text('.governance-merge-preview')
    assert '影响面' in preview and '条关系' in preview
    assert '已读的' in preview, '影响面要说清是按哪些记录统计出来的'
    assert page.is_disabled('#merge-entities') is True        # 还没勾确认
    page.check('#merge-confirm')
    assert page.is_disabled('#merge-entities') is False


def test_ledger_never_blanks_the_table_when_a_side_request_fails(page):
    """旁数据（本体版本 / 断言 / 操作 / 分组）任一读不到，表格也不能消失 —— 那一格如实写"还没读到"。"""
    page.evaluate("() => { window.mockFailSide = 'duplicate-groups'; }")
    page.click('#load-records')
    page.wait_for_function("() => document.querySelector('.ledger-kpi[data-ledger-kpi=duplicates] b').textContent.trim() === '—'",
                           timeout=10000)
    assert page.query_selector_all('.record-library tbody tr'), '分组接口挂了，表格也不能消失'
    assert page.evaluate("() => window.__errors === undefined")
    assert page.evaluate("() => window.__status === undefined"), '读取失败不该弹一条假成功回执'


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


def test_relation_row_locate_button_forwards_the_subject_entity(page):
    page.click('.ledger-tab:nth-child(2)')          # 切到「关系」视角，只有 r1 一行
    assert page.query_selector_all('[data-locate-graph="e1"]'), '关系行应转发 subject_id（e1）而不是关系自身 id'
    page.click('[data-locate-graph="e1"]')
    assert page.evaluate("() => window.__graphLocated") == 'e1'


def test_chunk_rows_have_no_locate_button(page):
    page.click('.ledger-tab:nth-child(4)')          # 「原文片段」不是图谱节点，不该给「在图谱定位」
    assert page.query_selector_all('[data-locate-graph]') == []


def test_focus_ledger_record_locates_and_highlights_the_row(page):
    """图谱里点「在台账查看」→ 跳到台账、切到正确视角并高亮那一行（不是把用户晾在列表顶上）。"""
    page.evaluate("() => window.focusLedgerRecord({id:'e2',kind:'entity'})")
    page.wait_for_selector('tr.ledger-row-located[data-record-id="e2"]', timeout=5000)
    assert page.evaluate("() => document.querySelector('[data-record-id=e2]')") is not None
    # 定位要清掉上一次搜索，否则目标行可能被过滤掉、找不到。
    assert page.input_value('#record-search') == ''
