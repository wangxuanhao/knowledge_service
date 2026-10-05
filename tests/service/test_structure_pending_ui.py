"""C1「结构待定」界面契约（真浏览器）：台账徽标与数字 + 收件箱入口 + 口径不许回退。

为什么钉这些：
  · 顶部「N 个概念待定」必须来自服务端 `/structure-pending` —— 读不到就写"读不到"，
    **不编造 0**；0 与"读不到"在页面上必须是两件事（数字骗人比没有数字更糟）。
  · 每行的徽标只能来自服务端给的 `row.structure_pending`：前端不许自己判断
    "这个类型在不在本体里"，否则台账、问答、收件箱会各说一套。
  · 点数字要去**本体建模层的收件箱**（C1 规定收件箱在审核台），不是再造一个台账内的列表。
  · 已解除也要留灰徽标：否则"我当初标记的东西去哪了"没人答得上来。
"""
from pathlib import Path

import pytest

from knowledge_service.services import structure_pending as sp

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<aside><nav><button data-tab="records">知识台账</button>
<button data-tab="ontology-model">本体建模层</button></nav></aside>
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
      <div class="columns"><label>记录 ID<input id="revision-id"></label><label>预期版本<input type="number" id="revision-version" value="1"></label></div>
      <textarea id="revision"></textarea><button id="revise">保存新版本</button>
      <label>恢复历史版本号<input type="number" id="restore-number" value="1"></label><button id="restore-version" class="secondary">恢复为新版本</button>
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
  <script>
  window.wb={records:new Map(),nodes:new Map()};
  window.current='p1';
  window.$=id=>document.getElementById(id);
  window.esc=value=>String(value===undefined||value===null?'':value);
  window.typeHint=value=>value?'关系类型':'';
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
    if(!response.ok)throw Object.assign(new Error(payload.error||payload.detail||'请求失败'),payload);
    return payload;
  };
  document.getElementById('resolve-entity').onclick=async()=>{};
  // 台账的待建模入口现在切到「本体建模层」页签（不再有审核台收件箱）。
  window.__pendingOpened=0;
  window.showTab=name=>{const b=document.querySelector(`[data-tab="${name}"]`);if(b)b.click();};
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
    window.__tabClicked=button.dataset.tab;
    document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));
    document.getElementById('tab-'+button.dataset.tab)?.classList.remove('hidden');
  });
  </script>
</body></html>"""

MOCKS = r"""() => {
  const entity = (id,text,type,extra={}) => ({id,kind:'entity',text,type,ontology_id:'o1',
    version:1,recorded_at:'2026-10-01T10:00:00Z',valid_from:null,valid_until:null,properties:{},metadata:{},...extra});
  const records = [
    entity('e1','一个包裹','urn:knowledge:ontology:甲:包裹',
      {structure_pending:{state:'pending',terms:['urn:knowledge:ontology:甲:包裹'],marked:[]}}),
    entity('e2','张三','urn:ontology:人员',
      {structure_pending:{state:'cleared',terms:[],marked:['urn:ontology:人员']}}),
    entity('e3','李四','urn:ontology:人员',{}),
  ];
  window.fetch = async (input,options={}) => {
    const url = typeof input === 'string' ? input : input.url;
    const parsed = new URL(url,'http://localhost');
    const payload = (body) => new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
    if(parsed.pathname.endsWith('/records/query')) return payload({total:records.length,records});
    if(parsed.pathname.endsWith('/ontologies')) return payload({versions:[{id:'o1',created_at:'2026-01-01T00:00:00Z'}]});
    if(parsed.pathname.endsWith('/assertions')) return payload({total:0,assertions:[]});
    if(parsed.pathname.endsWith('/duplicate-groups')) return payload({groups:[]});
    if(parsed.pathname.endsWith('/structure-pending')){
      if(window.mockPendingFail) return new Response('{"detail":"boom"}',{status:500,headers:{'Content-Type':'application/json'}});
      if(window.mockPendingEmpty) return payload({pending_structure:0,pending_records:0,terms:[]});
      return payload({pending_structure:2,pending_records:3,terms:[
        {term:'urn:knowledge:ontology:甲:包裹',kind:null,kind_label:null,record_count:2,
         suggested_action:'去「本体建模层」把这个概念建出来；发布新版本体以后，用到它的知识会自动解除「结构待定」，不需要逐条改。',
         records:[{id:'e1',kind:'entity',text:'一个包裹'}]},
        {term:'urn:knowledge:ontology:甲:寄件人',kind:null,kind_label:null,record_count:1,
         suggested_action:'去「本体建模层」把这个概念建出来；发布新版本体以后，用到它的知识会自动解除「结构待定」，不需要逐条改。',
         records:[{id:'e4',kind:'entity',text:'王某'}]}],
        note:'「结构待定」= 抽出来的概念还没进本体。'});
    }
    if(parsed.pathname.endsWith('/operations')) return payload({operations:[]});
    return payload({});
  };
}"""


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(EDGE))
        try:
            yield browser
        finally:
            browser.close()


def _open(browser, tmp_path, before=None):
    page = browser.new_page()
    page.on('dialog', lambda dialog: dialog.accept())
    shell = tmp_path / 'structure-ui.html'
    shell.write_text(SHELL, encoding='utf-8')
    page.goto(shell.as_uri())
    page.add_script_tag(path=str(WEB / 'records-view.js'))
    if before:
        page.evaluate(before)
    page.evaluate(MOCKS)
    page.click('#load-records')
    page.wait_for_selector('#records table', timeout=5000)
    return page


def test_pending_tile_comes_from_server_and_opens_the_inbox(browser, tmp_path):
    """数字来自 /structure-pending；点它去审核台收件箱（不是台账里再造一份列表）。"""
    page = _open(browser, tmp_path)
    tile = page.locator('[data-ledger-kpi="pending"]')
    assert tile.count() == 1
    assert tile.locator('b').inner_text().strip() == '2'
    assert tile.locator('small').inner_text().strip() == '个概念待定'
    assert '本体建模层' in tile.get_attribute('title')
    tile.click()
    assert page.evaluate('window.__tabClicked') == 'ontology-model', '点数字要先切到本体建模层'
    page.close()


def test_unreadable_inbox_says_unreadable_not_zero(browser, tmp_path):
    """读不到就写"读不到"：0 与"读不到"必须是两件事 —— 数字骗人比没有数字更糟。"""
    page = _open(browser, tmp_path, before='() => { window.mockPendingFail = true; }')
    tile = page.locator('[data-ledger-kpi="pending"]')
    assert tile.locator('b').inner_text().strip() == '读不到'
    assert tile.locator('b').inner_text().strip() != '0'
    assert '还没读到' in tile.get_attribute('title')
    assert tile.is_disabled()
    page.close()


def test_zero_pending_is_a_real_zero_from_the_server(browser, tmp_path):
    """真的 0（本体里概念齐了）要显示 0，并且不能被当成"读不到"。"""
    page = _open(browser, tmp_path, before='() => { window.mockPendingEmpty = true; }')
    tile = page.locator('[data-ledger-kpi="pending"]')
    assert tile.locator('b').inner_text().strip() == '0'
    assert '都在本体里' in tile.get_attribute('title')
    assert tile.is_disabled(), '没有待建模概念时点进去没有意义'
    page.close()


def test_row_badges_use_server_state_only(browser, tmp_path):
    """徽标只认服务端给的 row.structure_pending：待定实心、已解除留灰、没有标记就不显示。"""
    page = _open(browser, tmp_path)
    badges = page.locator('#records .ledger-pending-badge')
    assert badges.count() == 2, '三条记录里只有两条带状态，第三条不该凭空出现徽标'
    first = badges.nth(0)
    assert first.inner_text().strip() == '结构待定'
    assert 'urn:knowledge:ontology:甲:包裹' in first.get_attribute('title')
    assert '不作为正式证据' in first.get_attribute('title')
    cleared = badges.nth(1)
    assert cleared.inner_text().strip() == '结构待定已解除'
    assert '自动解除' in cleared.get_attribute('title')
    page.close()


def test_ledger_static_contract_keeps_the_boundary_copy():
    """静态契约：文案与出处不许回退（前端不得自己判断"类型在不在本体里"）。"""
    view = (WEB / 'records-view.js').read_text(encoding='utf-8')
    assert "/structure-pending" in view
    assert '个概念待定' in view
    assert '结构待定已解除' in view
    # 前端不许自己解析本体/判断术语是否存在：只消费服务端给的 structure_pending。
    assert 'row.structure_pending' in view


def test_workbench_and_qa_surfaces_are_wired():
    """待建模概念的收件箱已并入「本体建模层」画布；问答里的单列块口径与出处仍在。"""
    qa = (WEB / 'workbench.js').read_text(encoding='utf-8')
    assert 'qa-pending-block' in qa
    assert '不算正式证据' in qa
    assert 'structure_pending' in qa
    # 服务端口径本身也钉一下：模块头注释写死了"算什么/不算什么"。
    assert '不算：不进问答的正式证据链' in sp.__doc__


def test_module_contract_states_the_boundary():
    """模块头的口径就是这个功能的说明书，改口径必须改这里（不然没人知道边界在哪）。"""
    doc = sp.__doc__
    for line in ['可检索', '不进问答的正式证据链', '不作为约束校验的输入', '不提供"手动清除标记"入口']:
        assert line in doc, line
