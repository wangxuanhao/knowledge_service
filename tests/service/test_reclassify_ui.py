"""C2「受控重分类」界面契约（真浏览器）：入口已搬到「本体建模层」，台账只留一行提示。

为什么要钉这些（对应《完整落地规划》§4.2 与 §8 的 C2 验收口径）：

  · **台账不再有迁移面板**：用户原话「把这批知识迁到新本体这块是为了展示什么的，感觉也不需要吧」。
    台账只管"具体的东西"（查 / 改 / 并重复项 / 撤销）；迁移是本体版本的事，触发点跟着
    「本体建模层」发布完新版本那一步走（那条提示本来就在那儿）。
  · **能力没被删掉**：`/reclassify` 与 `/reclassify/preview` 仍有一处 UI 入口（本体建模层），
    三条硬约束（默认不跑 / 不原地改 / 可整批撤销）继续写在界面上。
  · 台账剩下的是一行**真话**：只在真有旧版本知识时出现，说清条数与去哪儿处理；
    读不到就什么都不说（不编数字），0 条也不显示（那是最正常的情况）。
  · 顺带钉住台账的第四个视角「属性」：属性是一条独立记录（主体 + 属性名 + 值 + 数据类型），
    以前台账查询时把它滤掉了，于是"实体身上的属性"在界面上根本看不到 —— 用户点名的就是它。

浏览器用系统 Edge（与其它 UI 契约同一套做法）；mock DOM 测不出版面，像素级证伪在真机探针里。
"""
import re
from pathlib import Path

import pytest

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<aside><nav><button data-tab="records">知识台账</button>
<button data-tab="ontology-model">本体建模层</button></nav></aside>
<main>
  <section id="tab-records" class="tab">
    <div class="panel">
      <div class="row"><h2>知识台账</h2><button id="load-records">刷新记录</button></div>
      <div id="records"></div>
    </div>
    <section class="panel entity-governance-panel">
      <h2>实体消歧与融合</h2>
      <div class="entity-governance-bar">
        <label><input id="resolve-text"></label><label><input id="resolve-threshold" type="number" value="0.7"></label>
        <button id="resolve-entity">查重</button>
        <label><input id="keep-id"></label><label><input id="drop-id"></label>
        <label><input id="alias-name"></label><button id="add-alias" class="secondary">存别名</button>
        <label class="check"><input id="merge-confirm" type="checkbox">确认</label>
        <button id="merge-entities">执行合并</button>
        <button id="load-operations" class="secondary">看可撤销的操作</button>
      </div>
      <div id="resolve-result"></div>
      <div id="operations"></div>
    </section>
  </section>
  <section id="tab-ontology" class="tab hidden">
    <button id="load-ontology">载入本体</button><div id="ontology-summary"></div>
    <textarea id="turtle"></textarea><select id="ontology-versions"></select>
  </section>
  <select id="project"><option value="p1" selected>测试项目</option></select>
  <button id="apply-scope"></button><button id="reset-scope"></button>
  <script>
  window.wb={records:new Map(),nodes:new Map()};
  window.current='p1';
  window.$=id=>document.getElementById(id);
  window.esc=value=>String(value===undefined||value===null?'':value);
  // typeHint 取末段（urn:ex:重量 → 重量）：断言才读得出"属性名 = 值"。
  window.typeHint=value=>value?String(value).split(':').pop():'';
  window.labelOf=value=>value?String(value).split(':').pop():'';
  function status(message,isError){window.__status={message,isError:Boolean(isError)};}
  window.editRecord=row=>{};
  window.historyFor=row=>{};
  window.scope=()=>({metadata:{}});
  window.endpoint=suffix=>'/api/projects/p1'+suffix;
  window.api=async(url,body,method)=>{
    const options={method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'}};
    if(options.method!=='GET')options.body=JSON.stringify(body||{});
    const response=await fetch(url,options);
    const payload=await response.json();
    if(!response.ok)throw Object.assign(new Error(payload.detail||'请求失败'),payload);
    return payload;
  };
  document.getElementById('resolve-entity').onclick=async()=>{};
  </script>
</body></html>"""

MOCKS = r"""() => {
  const entity = (id,text,type,ontology_id='o1') => ({id,kind:'entity',text,type,ontology_id,
    version:1,recorded_at:'2026-10-01T10:00:00Z',valid_from:null,valid_until:null,properties:{},metadata:{}});
  // 属性是一条独立记录：主体 + 属性名（type）+ 值 + 数据类型。台账以前根本没这一视角。
  const attribute = (id,subject_id,type,value,datatype) => ({id,kind:'attribute',subject_id,type,value,
    datatype,text:String(value),ontology_id:'o2',version:1,recorded_at:'2026-10-01T10:00:00Z',
    valid_from:null,valid_until:null,metadata:{}});
  const records = [
    entity('e1','一号包裹','urn:ex:包裹'),entity('e2','二号包裹','urn:ex:包裹'),
    attribute('a1','e1','urn:ex:重量',12,'integer'),
  ];
  const group = (over={}) => ({key:'o1:entity:包裹',kind:'entity',kind_label:'类',action:'carry',
    action_label:'原样搬到新版本',migratable:true,from_ontology_id:'o1',
    from_version:'本体 v1 · 2026-01-01（旧版）',from_type:'urn:ex:包裹',from_label:'包裹',
    to_type:'urn:ex:包裹',to_label:'包裹',record_count:2,sample_limit:20,
    records:[{id:'e1',text:'一号包裹',version:1},{id:'e2',text:'二号包裹',version:1}],
    note:'类型在新本体里还在：只把归属改到新版本，类型不动。',...over});
  const plan = () => ({
    project_id:'p1',current_ontology_id:'o2',current_version:'本体 v2 · 2026-03-01（当前）',
    stale_records:4,total_records:6,migratable_records:3,unmapped_records:1,
    groups:[group()],
    definitions:{carry:'原样搬到新版本',rename:'按替代映射改类型',unmapped:'没有去处'},
    constraints:['默认不跑：这一页只是看；要迁移必须逐组勾选后点「确认迁移」',
      '不原地改：每条迁移都产生新版本，旧版本仍在版本历史里可查、可恢复',
      '可整批撤销：一次迁移写成一条操作，在「可撤销的操作」里一键回退'],
    note:'迁移只改"这条知识挂在哪一版本体上、用哪个类型"，不改它的正文与有效期。',
  });
  const reply = body => new Response(JSON.stringify(body),{status:200,
    headers:{'Content-Type':'application/json'}});
  window.__applied=null;window.__previewed=null;window.__planRead=false;
  window.fetch = async (input,options={}) => {
    const url = typeof input === 'string' ? input : input.url;
    const parsed = new URL(url,'http://localhost');
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    if(parsed.pathname.endsWith('/records/query')) return reply({total:records.length,records});
    if(parsed.pathname.endsWith('/ontologies')) return reply({versions:[
      {id:'o1',created_at:'2026-01-01T00:00:00Z'},{id:'o2',created_at:'2026-03-01T00:00:00Z'}]});
    if(parsed.pathname.endsWith('/assertions')) return reply({total:0,assertions:[]});
    if(parsed.pathname.endsWith('/duplicate-groups')) return reply({groups:[]});
    if(parsed.pathname.endsWith('/structure-pending')) return reply({pending_structure:0,pending_records:0,terms:[]});
    if(parsed.pathname.endsWith('/operations')) return reply({operations:[]});
    // 迁移接口：台账**不该**再碰它们。这里记下来，谁调用谁失败（"默认不跑"）。
    if(parsed.pathname.endsWith('/reclassify/preview')){window.__previewed=body&&body.groups;return reply({blocked:false});}
    if(parsed.pathname.endsWith('/reclassify')){
      if(method==='POST'){window.__applied=body;return reply({migrated:0,groups:[],note:'不该发生'});}
      window.__planRead=true;
      if(window.mockPlanFail) return new Response('{"detail":"boom"}',{status:500,
        headers:{'Content-Type':'application/json'}});
      if(window.mockNoStale) return reply({...plan(),stale_records:0,migratable_records:0,unmapped_records:0,groups:[]});
      return reply(plan());
    }
    return reply({});
  };
}"""


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        # headless=True 与其它 UI 契约一致：有头模式会和桌面上开着的 Edge 争默认配置锁。
        browser = playwright.chromium.launch(headless=True, executable_path=str(EDGE))
        try:
            yield browser
        finally:
            browser.close()


def _open(browser, tmp_path, before=None, ready=None):
    page = browser.new_page()
    page.on('dialog', lambda dialog: dialog.accept())
    shell = tmp_path / 'reclassify-ui.html'
    shell.write_text(SHELL, encoding='utf-8')
    page.goto(shell.as_uri())
    page.add_script_tag(path=str(WEB / 'records-view.js'))
    page.evaluate(MOCKS)
    if before:
        page.evaluate(before)
    page.click('#load-records')
    page.wait_for_selector('#records table', timeout=5000)
    # 台账的旁数据（含 /reclassify）是第二次请求：等它落地再断言，
    # 否则读到的是"读取中"那一瞬 —— 那不是被测行为。
    page.wait_for_function('() => window.__planRead === true', timeout=5000)
    if ready:
        page.wait_for_function(ready, timeout=5000)
    else:
        page.wait_for_timeout(150)
    return page


def test_ledger_has_no_migration_panel_and_points_at_the_workbench(browser, tmp_path):
    """台账没有迁移面板了；旧版本知识只留一行真话，并说清去哪儿处理。"""
    page = _open(browser, tmp_path,
                 ready="() => { const n=document.getElementById('ledger-stale-notice');"
                       " return n && !n.hidden && n.textContent.includes('4'); }")
    # ① 面板与它的三档处置、勾选框、迁移按钮都不在了
    for gone in ('#ledger-reclassify', '#reclassify-apply', '#reclassify-preview',
                 '#reclassify-result', '.ledger-reclassify-group'):
        assert page.locator(gone).count() == 0, f'{gone} 还留在台账里'
    assert page.locator('[data-ledger-kpi="reclassify"]').count() == 0, '迁移那块 KPI 磁贴该撤掉'

    # ② 剩下一行提示：条数 + 能搬多少 + 去哪儿 + 说清这类知识是什么状态
    notice = page.locator('#ledger-stale-notice')
    assert notice.is_visible()
    text = notice.inner_text()
    assert '另有 4 条知识还挂在旧本体版本上' in text
    assert '3 条能搬到 本体 v2 · 2026-03-01（当前）' in text and '1 条在新本体里没有去处' in text
    assert '本体建模层' in text, '没有说清去哪儿处理'
    assert '能查' in text and '旧结构' in text, '没有说清这类知识是什么状态'

    # ③ 默认不跑：进页面只是读了一次计划，绝不写、也不干跑
    assert page.evaluate('window.__applied') is None, '台账不该再发起任何迁移写入'
    assert page.evaluate('window.__previewed') is None, '台账不该再发起干跑'
    page.close()


def test_stale_line_stays_hidden_when_nothing_is_stale(browser, tmp_path):
    """真的 0 条不显示这行（所有知识都在当前版本上是最正常的情况，不该有提示）。"""
    page = _open(browser, tmp_path, before='() => { window.mockNoStale = true; }')
    notice = page.locator('#ledger-stale-notice')
    assert notice.count() == 1, '提示节点应在（只是隐藏），方便用同一处口径渲染'
    assert notice.is_hidden()
    assert notice.inner_text().strip() == ''
    # 光有 hidden 属性不够：作者样式里的 display:flex 会盖掉 UA 的 [hidden]，在表格下面留一条空边框。
    # 这一条是真机探针 2026-10-05 抓到的（契约测属性测不出来，只有断言"看不见"才拦得住）。
    assert not notice.is_visible(), '没有要迁的东西时，这行提示必须整个不占版面'
    page.close()


def test_stale_line_stays_hidden_when_the_plan_is_unreadable(browser, tmp_path):
    """读不到就什么都别说：不编数字、不给入口 —— 与 C1 同一条规矩。"""
    page = _open(browser, tmp_path, before='() => { window.mockPlanFail = true; }')
    notice = page.locator('#ledger-stale-notice')
    assert notice.is_hidden()
    assert not notice.is_visible(), '读不到时这行也不许占版面'
    assert notice.inner_text().strip() == '', '读不到时不许编一个数字出来'
    assert page.locator('#ledger-reclassify').count() == 0
    page.close()


def test_ledger_has_an_attribute_view_with_subject_and_value(browser, tmp_path):
    """台账的第四个视角：属性（属性名 = 值 + 所属实体）。

    属性以前被查询滤掉了（kinds 只有 entity/relation/chunk），界面上根本看不到 ——
    用户点名台账要维护「实体、关系、属性」。
    """
    page = _open(browser, tmp_path)
    tabs = page.locator('.ledger-tab')
    labels = [tabs.nth(index).inner_text().strip() for index in range(tabs.count())]
    assert labels == ['实体', '关系', '属性', '原文片段'], f'台账视角不对：{labels}'
    page.click('[data-ledger-view="attribute"]')
    page.wait_for_selector('#records table.ledger-attribute', timeout=5000)
    headers = [h.strip() for h in page.locator('#records thead th').all_inner_texts()]
    assert headers == ['属性', '所属实体', '挂在哪一版本体上', '版本', '原文断言', '操作']
    row = page.locator('#records tbody tr').first
    excerpt = re.sub(r'\s+', '', row.locator('.record-excerpt').inner_text())
    assert excerpt == '重量=12', f'属性行读不出「属性名 = 值」：{excerpt}'
    assert '一号包裹' in row.inner_text(), '属性行没有显示它挂在哪个实体上'
    # 属性也能跳图谱：定位到它所属的实体（属性本身不是图上的点）
    assert row.locator('[data-locate-graph="e1"]').count() == 1
    page.close()


def test_ledger_only_reads_the_reclassify_plan():
    """台账只读那份重分类计划（渲染那一行提示），不干跑、不写入。

    迁移/重分类的 UI 入口原在旧审核台（ontology-workbench.js），该文件已随旧三页删除；
    入口待搬回「本体建模层」发布流程（已知缺口）。台账这一侧的口径保持不变：
    只读服务端计划、不自己判断替代映射、撤销列表仍认得出这次迁移。
    """
    view = (WEB / 'records-view.js').read_text(encoding='utf-8')
    # 台账只读那份计划（渲染那一行提示），不再有干跑与写入
    assert '/reclassify' in view and '/reclassify/preview' not in view, '台账不该再有干跑'
    assert "'人工（知识台账）'" not in view, '台账不该再有迁移写入的调用点'
    assert 'isReplacedBy' not in view, '替代映射是服务端的事：前端自己判断必然与台账/问答说岔'
    # 撤销列表里仍要认得这次迁移（历史操作要读得懂）
    assert "'reclassify':'迁到新本体'" in view
