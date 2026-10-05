"""受控重分类（C2）· 迁移抽屉的真浏览器契约。

被钉住的是**迁移入口在本体建模层**这件事，以及三条硬约束在界面上的表现：
  ① 默认不跑   —— 不勾选、不预演，就点不了「确认迁移」；
  ② 不原地改   —— 文案必须说清"每条产生新版本"；
  ③ 可整批撤销 —— 迁移回执上必须给「撤销这次迁移」，且走 /operations/{id}/undo。

为什么值得一条浏览器测试（而不是只做静态字符串断言）：这一块的错误几乎全是**时序与门禁**
错误——"没预演就能迁"、"勾选变了还用旧预演结论"、"没有去处的组也能勾"。这些只有真跑 DOM
才抓得到。静态断言在 test_frontend_static_contract.py 里另有一条。
"""
from pathlib import Path

import pytest

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<nav><button data-tab="ontology-model">本体建模层</button></nav>
<main>
  <section id="tab-ontology-model" class="tab ontology-model">
    <div class="om-canvas">画布占位</div>
  </section>
</main>
<select id="project"><option value="p1" selected>测试项目</option></select>
<script>
window.current='p1';
window.$=id=>document.getElementById(id);
function status(message,isError){window.__status={message,isError:Boolean(isError)};}
window.endpoint=suffix=>'/api/projects/p1'+suffix;
window.api=async(url,body,method)=>{
  const options={method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'}};
  if(options.method!=='GET')options.body=JSON.stringify(body||{});
  const response=await fetch(url,options);
  const payload=await response.json();
  if(!response.ok)throw Object.assign(new Error(payload.detail||'请求失败'),payload);
  return payload;
};
window.confirm=()=>true;   // 撤销确认：测试里直接放行，断言靠 __undone
</script>
</body></html>"""

MOCKS = r"""() => {
  const group = (over={}) => ({key:'o1:entity:包裹',kind:'entity',kind_label:'类',action:'carry',
    action_label:'原样搬到新版本',migratable:true,from_ontology_id:'o1',
    from_version:'本体 v1 · 2026-01-01（旧版）',from_type:'urn:ex:包裹',from_label:'包裹',
    to_type:'urn:ex:包裹',to_label:'包裹',record_count:2,sample_limit:20,
    records:[{id:'e1',text:'一号包裹',version:1},{id:'e2',text:'二号包裹',version:1}],
    note:'类型在新本体里还在：只把归属改到新版本，类型不动。',...over});
  const rename = () => group({key:'o1:entity:货运单',from_type:'urn:ex:货运单',from_label:'货运单',
    action:'rename',action_label:'按替代映射改类型',to_type:'urn:ex:运单',to_label:'运单',
    record_count:1,records:[{id:'e9',text:'一张货运单',version:1}],
    note:'旧类型有替代映射：连同类型一起改到替代概念。'});
  const unmapped = () => group({key:'o1:entity:冷链',from_type:'urn:ex:冷链',from_label:'冷链',
    action:'unmapped',action_label:'没有去处',migratable:false,to_type:null,to_label:null,
    record_count:1,records:[{id:'e3',text:'冷链记录',version:1}],
    note:'新本体里没有这个类型、也没有替代映射：先在本体建模层把概念建出来并发布。'});
  const plan = () => ({project_id:'p1',current_ontology_id:'o2',
    current_version:'本体 v2 · 2026-03-01（当前）',stale_records:4,total_records:9,
    migratable_records:3,unmapped_records:1,groups:[group(),rename(),unmapped()],
    definitions:{carry:'原样搬到新版本',rename:'按替代映射改类型',unmapped:'没有去处'},
    constraints:['默认不跑：这一页只是看；要迁移必须逐组勾选后点「确认迁移」',
      '不原地改：每条迁移都产生新版本，旧版本仍在版本历史里可查、可恢复',
      '可整批撤销：一次迁移写成一条操作，在「可撤销的操作」里一键回退'],
    note:'迁移只改"这条知识挂在哪一版本体上、用哪个类型"，不改它的正文与有效期。'});
  const reply = body => new Response(JSON.stringify(body),{status:200,
    headers:{'Content-Type':'application/json'}});
  window.__planRead=false;window.__previewed=null;window.__applied=null;window.__undone=null;
  window.fetch = async (input,options={}) => {
    const url = typeof input === 'string' ? input : input.url;
    const parsed = new URL(url,'http://localhost');
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    if(parsed.pathname.endsWith('/reclassify/preview')){
      window.__previewed = body && body.groups;
      if(window.mockBlocked) return reply({blocked:true,would_change:3,
        ontology_version:'本体 v2 · 2026-03-01（当前）',
        validation:{conforms:false,errors:[
          'e1：subject_id 类型 包裹 不满足 domain 中的任何一个：urn:ex:商户']},
        note:'',undoable:''});
      return reply({blocked:false,would_change:3,
        ontology_version:'本体 v2 · 2026-03-01（当前）',
        validation:{conforms:true,errors:[]},
        note:'预演用的是与真正写入同一个校验函数（_timeline_check）。',
        undoable:'真正迁移后整批写成一条操作，可在「可撤销的操作」里一键撤销。'});
    }
    if(parsed.pathname.includes('/operations/') && parsed.pathname.endsWith('/undo')){
      window.__undone = parsed.pathname;
      return reply({restored:3});
    }
    if(parsed.pathname.endsWith('/reclassify')){
      if(method === 'POST'){
        window.__applied = body;
        return reply({project_id:'p1',operation_id:'op-9',migrated:3,
          ontology_version:'本体 v2 · 2026-03-01（当前）',groups:[],
          note:'已把 3 条知识迁到本体 v2 · 2026-03-01（当前）：每条都产生了新版本（旧版本仍在版本历史里）。',
          undo:{operation_id:'op-9',label:'撤销这次迁移'}});
      }
      window.__planRead = true;
      if(window.mockPlanFail) return new Response('{"detail":"boom"}',{status:500,
        headers:{'Content-Type':'application/json'}});
      if(window.mockNoStale) return reply({...plan(),stale_records:0,
        migratable_records:0,unmapped_records:0,groups:[]});
      return reply(plan());
    }
    return reply({});
  };
}"""


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(EDGE))
        try:
            yield browser
        finally:
            browser.close()


def _open(browser, tmp_path, before=None):
    page = browser.new_page()
    page.on('dialog', lambda dialog: dialog.accept())
    shell = tmp_path / 'reclassify-migration.html'
    shell.write_text(SHELL, encoding='utf-8')
    page.goto(shell.as_uri())
    page.evaluate(MOCKS)
    if before:
        page.evaluate(before)
    page.add_script_tag(path=str(WEB / 'ontology-model-reclassify.js'))
    # 首次 refresh 是异步的：等计划真的读过一遍再做断言，否则读到的是"读取中"那一瞬。
    page.wait_for_function('() => window.__planRead === true', timeout=5000)
    page.wait_for_timeout(80)
    return page


def test_strip_only_appears_when_there_is_something_to_migrate(browser, tmp_path):
    """迁移条三态：有旧知识才出现；没有 / 读不到都**不显示**（尤其不许编数字）。"""
    page = _open(browser, tmp_path)
    strip = page.wait_for_selector('#om-rc-strip', state='visible', timeout=5000)
    text = strip.inner_text()
    assert '还有 4 条知识挂在旧本体版本上' in text
    assert '3 条能搬到' in text and '1 条在新本体里没有去处' in text
    page.close()

    # 没有旧知识：这一条整个不出现（0 是最正常的情况，不该占地方）
    page = _open(browser, tmp_path, before="() => { window.mockNoStale = true; }")
    assert page.query_selector('#om-rc-strip').is_hidden()
    page.close()

    # 读不到：什么都不说，更不能显示数字
    page = _open(browser, tmp_path, before="() => { window.mockPlanFail = true; }")
    assert page.query_selector('#om-rc-strip').is_hidden()
    page.close()


def test_drawer_lists_groups_and_keeps_unmapped_unselectable(browser, tmp_path):
    """分组表：能搬的可勾；「没有去处」的必须不可勾，且说明去哪处理。"""
    page = _open(browser, tmp_path)
    page.click('#om-rc-open')
    page.wait_for_selector('#om-rc-overlay:not([hidden])', timeout=5000)

    items = page.query_selector_all('.om-rc-item')
    assert len(items) == 3, '三档处置的三组都要列出来'

    checks = page.query_selector_all('.om-rc-check')
    assert checks[0].is_enabled() and checks[1].is_enabled(), '能搬的组应当可勾'
    assert checks[2].is_disabled(), '「没有去处」的组居然可勾'
    assert 'blocked' in (page.query_selector_all('.om-rc-item')[2].get_attribute('class') or '')

    body = page.inner_text('#om-rc-body')
    assert '原样搬到新版本' in body and '按替代映射改类型' in body and '没有去处' in body
    # 明细默认收起（面板主用途是决定迁哪几组，不是逐条读记录）
    assert page.eval_on_selector_all('.om-rc-detail', 'els => els.every(el => !el.open)')
    page.close()


def test_apply_requires_a_fresh_preview_after_the_selection_changes(browser, tmp_path):
    """门禁：没预演不让迁；勾选一变，旧预演结论立刻作废。"""
    page = _open(browser, tmp_path)
    page.click('#om-rc-open')
    page.wait_for_selector('#om-rc-overlay:not([hidden])', timeout=5000)

    apply = page.query_selector('[data-rc="apply"]')
    assert apply.is_disabled(), '一组都没勾就能点「确认迁移」'

    page.query_selector_all('.om-rc-check')[0].check()
    page.wait_for_timeout(50)
    assert page.query_selector('[data-rc="apply"]').is_disabled(), '还没预演就能迁'

    page.click('[data-rc="preview"]')
    page.wait_for_function('() => window.__previewed !== null', timeout=5000)
    page.wait_for_timeout(80)
    assert page.eval_on_selector('[data-rc="apply"]', 'el => !el.disabled'), '预演通过了却不让迁'
    assert '预演通过' in page.inner_text('.om-rc-preview')

    # 勾选变了 → 预演结论作废，必须重跑（否则会拿着旧结论去迁新范围）
    page.query_selector_all('.om-rc-check')[1].check()
    page.wait_for_timeout(80)
    assert page.eval_on_selector('[data-rc="apply"]', 'el => el.disabled'), '勾选变过还能直接迁'
    assert '请重新预演' in page.inner_text('#om-rc-body')
    page.close()


def test_apply_posts_only_the_selected_groups_and_offers_batch_undo(browser, tmp_path):
    """迁移：只提交勾选的组；回执给「撤销这次迁移」，撤销走 /operations/{id}/undo。"""
    page = _open(browser, tmp_path)
    page.click('#om-rc-open')
    page.wait_for_selector('#om-rc-overlay:not([hidden])', timeout=5000)
    page.query_selector_all('.om-rc-check')[0].check()
    page.wait_for_timeout(50)
    page.click('[data-rc="preview"]')
    page.wait_for_function('() => window.__previewed !== null', timeout=5000)
    page.wait_for_timeout(80)
    page.click('[data-rc="apply"]')
    page.wait_for_function('() => window.__applied !== null', timeout=5000)
    page.wait_for_timeout(120)

    applied = page.evaluate('window.__applied')
    assert applied['groups'] == ['o1:entity:包裹'], '提交的组不是勾选的那一组'
    assert applied.get('actor'), '迁移没有带上执行人'

    body = page.inner_text('#om-rc-body')
    assert '已把 3 条知识迁到' in body, '迁移回执没有说清迁了几条'
    assert '撤销这次迁移' in body, '回执上没有「撤销这次迁移」（硬约束③）'

    page.click('[data-rc="undo"]')
    page.wait_for_function('() => window.__undone !== null', timeout=5000)
    assert page.evaluate('window.__undone').endswith('/operations/op-9/undo')
    page.close()


def test_blocked_preview_lists_reasons_and_locks_apply(browser, tmp_path):
    """预演被本体校验拦下：如实列出原因，并且不许迁（硬约束：一条都不写）。"""
    page = _open(browser, tmp_path, before="() => { window.mockBlocked = true; }")
    page.click('#om-rc-open')
    page.wait_for_selector('#om-rc-overlay:not([hidden])', timeout=5000)
    page.query_selector_all('.om-rc-check')[0].check()
    page.wait_for_timeout(50)
    page.click('[data-rc="preview"]')
    page.wait_for_function('() => window.__previewed !== null', timeout=5000)
    page.wait_for_timeout(80)

    body = page.inner_text('#om-rc-body')
    assert '预演被拦下' in body
    assert '不满足 domain 中的任何一个' in body, '没把被拦的具体原因给人看'
    assert page.eval_on_selector('[data-rc="apply"]', 'el => el.disabled'), '被拦下了还能点迁移'
    page.close()
