/* ============================================================================
 * 受控重分类（C2）· 迁移抽屉（ontology-model-reclassify.js）
 *
 * 解决什么问题
 * ------------
 * 本体发布 v4 之后，v3 时期写进来的知识仍然挂着 v3（record_versions.ontology_id）。
 * 台账只能把这件事"露出来"（每行标「本体 v3 ·（旧版）」+ 一行提示），真正把它**搬过去**
 * 的动作属于**本体版本**，所以入口放在「本体建模层」——刚发完新版本的那一刻。
 *
 * 做什么 / 不做什么（一句话口径，与 services/reclassify.py 逐条对应）
 * ----------------------------------------------------------------
 *   做    ：把「还挂在旧版本上的知识」按旧类型分组 → 逐组勾选 → 预演 → 迁移。
 *   做    ：迁移只改「这条知识挂在哪一版本体上、用哪个类型」，**正文一个字节不动**。
 *   做    ：每条迁移产生**新版本**（不原地改，旧版本仍可查可恢复）；整批写成**一条**操作，
 *           可在台账「可撤销的操作」里一键整批撤销。
 *   不做  ：不做「一键迁全部」——默认不跑，必须逐组勾选（硬约束①）。
 *   不做  ：不迁「没有去处」的组，也不偷偷改它的类型；页面上如实说"先去建模层把概念建出来"。
 *   不做  ：不自己推断校验规则——预演/迁移的结论只认服务端（与真正写入同一个校验函数）。
 *
 * 渲染约定：勾选/预演这类"局部变化"只重画受影响的那一块（就地切类 + 重画底栏门禁），
 * 不整块重建 DOM —— 整块重建会把用户正在点的复选框从 DOM 上摘掉（焦点丢失）。
 *
 * 与主控的关系：只读全局 api/endpoint/status 与裸标识符 current（同 ontology-model-panel.js），
 * 不直接改别人的 DOM；发布成功后由面板广播的 `ontology-model:published` 事件驱动刷新。
 * ========================================================================== */
(() => {
  // ---- 极小的 DOM 工具（同 panel：一律 textContent 落文本，不解析 HTML）----
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const put = (host, ...nodes) => { host.replaceChildren(...nodes); return host; };

  // app.js 的 `let current` 是脚本级绑定（不是 window 属性）：只能裸标识符 + 防未声明。
  const activeProject = () => { try { return current || ''; } catch (error) { return ''; } };
  const model = () => (typeof window !== 'undefined' ? window.OntologyModel : null);
  function status(msg, err) {
    const M = model();
    if (M && typeof M.status === 'function') M.status(msg, err);
    else if (typeof console !== 'undefined') console.log(msg);
  }

  // ---- 模块状态 --------------------------------------------------------------
  let strip = null;          // 「还有 N 条挂在旧版本」提示条
  let drawer = null;         // { overlay, body, previewBox, actionsBox }
  const state = {
    plan: null,            // GET /reclassify 的返回；null = 读不到（不编数字）
    loading: false,
    selected: new Set(),   // 勾选的组 key
    preview: null,         // 预演结果
    previewSig: '',        // 预演对应的勾选签名（勾选一变就作废）
    applying: false,
    result: null,          // 迁移回执（含 operation_id）
    error: '',
  };
  const sig = () => [...state.selected].sort().join('|');

  // ==========================================================================
  // 一、迁移条：常驻在建模层页签顶部，只在**真有事**时出现
  // ==========================================================================
  function ensureStrip() {
    if (strip) return strip;
    const root = document.getElementById('tab-ontology-model');
    if (!root) return null;
    const bar = el('div', 'om-rc-strip');
    bar.id = 'om-rc-strip';
    bar.setAttribute('hidden', '');
    bar.setAttribute('role', 'status');
    // 插在页签最前面：这是"当前项目的一件事"，不该埋在画布下面。
    root.insertBefore(bar, root.firstChild);
    strip = bar;
    return strip;
  }

  function renderStrip() {
    const bar = ensureStrip();
    if (!bar) return;
    if (!activeProject()) { bar.hidden = true; bar.replaceChildren(); return; }
    const plan = state.plan;
    // 读不到 / 没有 stale：什么都不显示（0 本来就是最正常的情况，不该占地方）。
    if (!plan || !plan.stale_records) { bar.hidden = true; bar.replaceChildren(); return; }
    const migratable = plan.migratable_records || 0;
    const unmapped = plan.unmapped_records || 0;
    const text = el('span', 'om-rc-strip-text');
    text.append(el('b', null, `还有 ${plan.stale_records} 条知识挂在旧本体版本上`));
    text.append(el('span', null,
      `（${migratable} 条能搬到 ${plan.current_version || '当前版本'}`
      + `${unmapped ? `，${unmapped} 条在新本体里没有去处` : ''}）。`
      + '搬迁只改它们"挂在哪一版本体上"，正文不动，且可整批撤销。'));
    const open = el('button', 'om-rc-strip-open', '查看迁移');
    open.type = 'button';
    open.id = 'om-rc-open';
    open.addEventListener('click', () => openDrawer());
    bar.hidden = false;
    put(bar, text, open);
  }

  /** 重新读一次计划（项目切换 / 发布成功后 / 手动刷新）。读不到就保持"不显示"，绝不编数字。 */
  async function refresh() {
    const project = activeProject();
    if (!project) {
      state.plan = null; state.selected.clear(); state.preview = null;
      renderStrip(); if (drawer) renderDrawer();
      return;
    }
    if (state.loading) return;
    state.loading = true;
    try {
      const res = await api(endpoint('/reclassify'), undefined, 'GET');
      // 请求返回时项目可能已经切走：过期的结果丢掉，不覆盖新项目的状态。
      if (project !== activeProject()) return;
      state.plan = (res && typeof res.stale_records === 'number') ? res : null;
      state.error = '';
      // 勾选里已经不存在的组（比如刚迁完）要清掉，否则会拿着过期 key 去预演。
      const alive = new Set((state.plan && state.plan.groups || [])
        .filter(g => g.migratable).map(g => g.key));
      [...state.selected].forEach(key => { if (!alive.has(key)) state.selected.delete(key); });
    } catch (error) {
      if (project !== activeProject()) return;
      // 读不到就是读不到：不显示数字，也不假装"没有需要迁移的"。
      state.plan = null;
      state.error = (error && error.message) || '读取失败';
    } finally {
      state.loading = false;
      renderStrip();
      if (drawer) renderDrawer();
    }
  }

  // ==========================================================================
  // 二、抽屉：三档处置 → 逐组勾选 → 预演（干跑）→ 确认迁移 → 可撤销
  // ==========================================================================
  function ensureDrawer() {
    if (drawer) return drawer;
    const root = document.getElementById('tab-ontology-model') || document.body;
    const overlay = el('div', 'om-rc');
    overlay.id = 'om-rc-overlay';
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'om-rc-title');
    const backdrop = el('div', 'om-rc-backdrop');
    const panel = el('section', 'om-rc-panel');
    const head = el('header', 'om-rc-head');
    const title = el('b', null, '把旧知识迁到当前本体');
    title.id = 'om-rc-title';
    head.append(title);
    head.append(el('span', 'om-rc-sub', '只改「挂在哪一版本体上、用哪个类型」；正文不动，可整批撤销'));
    const close = el('button', 'om-btn ghost', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭迁移');
    close.title = '关闭';
    close.addEventListener('click', closeDrawer);
    head.append(close);
    const body = el('div', 'om-rc-body');
    body.id = 'om-rc-body';
    panel.append(head, body);
    overlay.append(backdrop, panel);
    backdrop.addEventListener('click', closeDrawer);
    overlay.addEventListener('keydown', event => { if (event.key === 'Escape') closeDrawer(); });
    root.append(overlay);
    drawer = { overlay, body, previewBox: null, actionsBox: null };
    return drawer;
  }

  function openDrawer() {
    const d = ensureDrawer();
    d.overlay.removeAttribute('hidden');
    renderDrawer();
    refresh();
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.overlay.setAttribute('hidden', '');
    // 关掉就把"这一轮迁移"的临时状态清掉：下次打开应当是干净的一次操作。
    state.preview = null; state.previewSig = ''; state.result = null; state.error = '';
  }

  function renderDrawer() {
    if (!drawer) return;
    const body = drawer.body;
    if (!activeProject()) { put(body, el('p', 'om-rc-empty', '请先在左上角选择知识项目。')); return; }
    if (state.loading && !state.plan) { put(body, el('p', 'om-rc-empty', '正在读取还需要迁移的知识…')); return; }
    if (!state.plan) {
      const box = el('div');
      box.append(el('p', 'om-rc-empty',
        `读不到迁移清单${state.error ? '（' + state.error + '）' : ''}。读不到时这里不显示任何数字——`
        + '请确认项目里已经发布过本体，再重试。'));
      const retry = el('button', 'om-rc-btn', '重新读取');
      retry.type = 'button';
      retry.dataset.rc = 'retry';
      retry.addEventListener('click', () => refresh());
      box.append(retry);
      put(body, box);
      return;
    }
    if (!state.plan.stale_records) {
      put(body, el('p', 'om-rc-empty',
        `当前没有挂在旧版本上的知识：所有知识都挂在 ${state.plan.current_version || '当前版本'} 上。`
        + '本体再发新版本时，这里会自动出现要迁的条目。'));
      return;
    }
    // 两个"随勾选变化"的块留出固定容器：局部变化只重画它们，不重建整棵树。
    const previewBox = el('div', 'om-rc-preview');
    const actionsBox = el('div', 'om-rc-actions');
    drawer.previewBox = previewBox;
    drawer.actionsBox = actionsBox;
    put(body, renderSummary(state.plan), renderGroups(state.plan), previewBox, actionsBox);
    renderPreviewInto(previewBox);
    renderActionsInto(actionsBox);
  }

  /** 一句话说清「这是什么、一共有多少、迁完会怎样」。 */
  function renderSummary(plan) {
    const box = el('div', 'om-rc-summary');
    const facts = el('div', 'om-rc-facts');
    const fact = (k, v) => {
      const f = el('div', 'om-rc-fact');
      f.append(el('span', null, k)); f.append(el('b', null, v));
      return f;
    };
    facts.append(fact('当前版本', plan.current_version || '—'));
    facts.append(fact('挂着旧版本', `${plan.stale_records} 条`));
    facts.append(fact('能搬', `${plan.migratable_records || 0} 条`));
    facts.append(fact('没有去处', `${plan.unmapped_records || 0} 条`));
    box.append(facts);
    if (plan.definitions) {
      const defs = el('p', 'om-rc-note');
      defs.append(el('b', null, '三档处置：'));
      defs.append(el('span', null, Object.keys(plan.definitions)
        .map(k => `${plan.definitions[k]}（${k}）`).join(' · ')));
      box.append(defs);
    }
    box.append(el('p', 'om-rc-note', plan.note || ''));
    return box;
  }

  /** 分组表：每行一组（旧版本 + 记录种类 + 类型）。能搬的可勾；没有去处的如实说清。 */
  function renderGroups(plan) {
    const box = el('div', 'om-rc-groups');
    box.append(el('h4', 'om-rc-h', '① 选要迁移的组（默认一组都不选）'));
    const list = el('ul', 'om-rc-list');
    (plan.groups || []).forEach(group => {
      const li = el('li', 'om-rc-item' + (group.migratable ? '' : ' blocked')
        + (state.selected.has(group.key) ? ' sel' : ''));
      const row = el('label', 'om-rc-row');
      const check = el('input', 'om-rc-check');
      check.type = 'checkbox';
      check.checked = state.selected.has(group.key);
      check.disabled = !group.migratable;
      check.addEventListener('change', () => {
        if (check.checked) state.selected.add(group.key); else state.selected.delete(group.key);
        // 就地切类：整块重建会把这个复选框从 DOM 上摘掉，用户点一下焦点就没了。
        li.classList.toggle('sel', check.checked);
        // 勾选一变，之前的预演结论就不适用了（签名对不上）→ 只重画门禁那块。
        renderActionsInto(drawer.actionsBox);
      });
      row.append(check);
      const main = el('div', 'om-rc-row-main');
      const line1 = el('div', 'om-rc-row-title');
      line1.append(el('b', null, group.from_label || '(未命名类型)'));
      line1.append(el('span', 'om-rc-from', ` 来自 ${group.from_version || '未知版本'}`));
      line1.append(el('span', 'om-rc-kind', ` · ${group.kind_label || ''}`));
      main.append(line1);
      const line2 = el('div', 'om-rc-row-move');
      line2.append(el('span', 'om-rc-action', group.action_label || group.action));
      if (group.migratable) {
        line2.append(el('span', 'om-rc-arrow', '→'));
        line2.append(el('span', 'om-rc-to', group.to_label || group.to_type || '(当前类型)'));
      }
      main.append(line2);
      main.append(el('p', 'om-rc-row-note', group.note || ''));
      row.append(main);
      row.append(el('span', 'om-rc-count', `${group.record_count} 条`));
      li.append(row);
      // 明细默认收起：面板的主用途是"决定迁哪几组"，不是逐条读记录；想核对再展开。
      const rows = group.records || [];
      if (rows.length) {
        const detail = el('details', 'om-rc-detail');
        detail.append(el('summary', null, `看这 ${group.record_count} 条里的前 ${rows.length} 条`));
        const inner = el('ul', 'om-rc-samples');
        rows.forEach(r => inner.append(el('li', null, `${r.id}：${r.text || '（无正文）'}`)));
        detail.append(inner);
        if (group.record_count > rows.length) {
          detail.append(el('p', 'om-rc-note',
            `（只列前 ${rows.length} 条，共 ${group.record_count} 条；这里是给你核对用的，迁移按组整体进行。）`));
        }
        li.append(detail);
      }
      if (!group.migratable) {
        li.append(el('p', 'om-rc-blocked-hint',
          '没有去处，不会被迁移：先去「本体建模层」把概念建出来并发布，'
          + '这些知识会自动脱离「结构待定」，再回来迁。'));
      }
      list.append(li);
    });
    box.append(list);
    return box;
  }

  /** 预演结果：会改几条 + 会不会被本体校验拦下（逐条人话）。 */
  function renderPreviewInto(box) {
    if (!box) return;
    put(box, el('h4', 'om-rc-h', '② 预演（干跑，不写入任何东西）'));
    if (!state.preview) {
      box.append(el('p', 'om-rc-note',
        '预演用的是与真正迁移**同一个**校验函数（时间轴一致性 + 关系的 domain/range + SHACL），'
        + '所以这里说通过、迁移就不会被拦；说被拦，就一定看得到是哪几条、为什么。'));
      return;
    }
    const p = state.preview;
    const head = el('p', 'om-rc-preview-head');
    if (p.blocked) {
      head.append(el('b', 'om-rc-bad', '预演被拦下：这批迁移不会写入（一条都不写）。'));
    } else {
      head.append(el('b', 'om-rc-ok',
        `预演通过：会把 ${p.would_change || 0} 条知识迁到 ${p.ontology_version || '当前版本'}。`));
    }
    box.append(head);
    const errors = (p.validation && p.validation.errors) || [];
    if (errors.length) {
      box.append(el('p', 'om-rc-note', '校验拦下的条目（最多列 10 条）：'));
      const ul = el('ul', 'om-rc-errors');
      errors.forEach(message => ul.append(el('li', null, message)));
      box.append(ul);
    }
    if (p.note) box.append(el('p', 'om-rc-note', p.note));
    if (p.undoable) box.append(el('p', 'om-rc-note', p.undoable));
  }

  /** 底栏：预演 / 确认迁移 / 撤销。门禁全部来自服务端结论，前端不自己判断能不能迁。 */
  function renderActionsInto(box) {
    if (!box) return;
    if (state.result) {
      const undo = el('button', 'om-rc-btn ghost', '撤销这次迁移');
      undo.type = 'button';
      undo.dataset.rc = 'undo';
      undo.title = '把这次迁移影响到的记录整批恢复到迁移之前（版本 +1，历史不丢）';
      undo.addEventListener('click', () => doUndo(state.result.operation_id));
      const done = el('button', 'om-rc-btn', '关闭');
      done.type = 'button';
      done.dataset.rc = 'done';
      done.addEventListener('click', closeDrawer);
      put(box, el('p', 'om-rc-ok-line', state.result.note || '迁移完成。'), undo, done);
      return;
    }
    const hasSelection = state.selected.size > 0;
    const previewFresh = !!state.preview && state.previewSig === sig();

    const preview = el('button', 'om-rc-btn', '预演（不写入）');
    preview.type = 'button';
    preview.dataset.rc = 'preview';
    preview.disabled = !hasSelection || state.applying;
    preview.title = hasSelection ? '先看看这批迁移会不会被本体校验拦下' : '先勾选至少一组';
    preview.addEventListener('click', () => doPreview());

    const apply = el('button', 'om-rc-btn primary', '确认迁移');
    apply.type = 'button';
    apply.dataset.rc = 'apply';
    apply.disabled = !hasSelection || !previewFresh
      || !!(state.preview && state.preview.blocked) || state.applying;
    apply.title = !hasSelection ? '先勾选至少一组'
      : !previewFresh ? '勾选变过了：请先重跑一次预演，确认结论仍然成立'
      : (state.preview && state.preview.blocked) ? '预演被拦下了：先按上面的原因修好，再来迁移'
      : '开始迁移：每条产生新版本，整批写成一条可撤销的操作';
    apply.addEventListener('click', () => doApply());

    const nodes = [preview, apply];
    if (state.preview && !previewFresh) {
      nodes.push(el('span', 'om-rc-stale', '勾选变过了，预演结论已作废，请重新预演。'));
    }
    nodes.push(el('span', 'om-rc-note', '默认不跑：不勾选、不预演、不点确认，什么都不会发生。'));
    put(box, ...nodes);
  }

  // ==========================================================================
  // 三、三个动作
  // ==========================================================================
  async function doPreview() {
    if (!state.selected.size) return;
    const project = activeProject();
    const keys = [...state.selected];
    try {
      state.error = '';
      const res = await api(endpoint('/reclassify/preview'), { groups: keys }, 'POST');
      if (project !== activeProject()) return;
      state.preview = res;
      state.previewSig = sig();          // 记住这次预演对应的勾选，勾选一变就作废
      renderPreviewInto(drawer.previewBox);
      renderActionsInto(drawer.actionsBox);
    } catch (error) {
      state.error = (error && error.message) || '预演失败';
      status('预演失败：' + state.error, true);
    }
  }

  async function doApply() {
    if (!state.selected.size || state.applying) return;
    const project = activeProject();
    const keys = [...state.selected];
    state.applying = true;
    renderActionsInto(drawer.actionsBox);
    try {
      const res = await api(endpoint('/reclassify'), { groups: keys, actor: actorName() }, 'POST');
      if (project !== activeProject()) return;
      state.result = res;
      state.preview = null; state.previewSig = '';
      state.selected.clear();
      status(res.note || `已迁移 ${res.migrated || 0} 条知识。`);
      await refresh();                    // 迁完立刻重读：迁移条/分组表都要跟上
      renderDrawer();
    } catch (error) {
      state.error = (error && error.message) || '迁移失败';
      status('迁移失败：' + state.error, true);
    } finally {
      state.applying = false;
      if (drawer) renderActionsInto(drawer.actionsBox);
    }
  }

  async function doUndo(operationId) {
    if (!operationId) return;
    if (!window.confirm('撤销这次迁移会把受影响的记录整批恢复到迁移之前（产生新版本，历史不丢）。继续？')) return;
    try {
      const res = await api(endpoint('/operations/' + encodeURIComponent(operationId) + '/undo'), {});
      status(`已撤销这次迁移：恢复 ${res.restored || 0} 条记录。`);
      state.result = null;
      await refresh();
      renderDrawer();
    } catch (error) {
      status('撤销失败：' + ((error && error.message) || error), true);
    }
  }

  /** 迁移人：跟发布/合并同一套取值（谁在用这个工作台），取不到就说"人工"。 */
  function actorName() {
    const M = model();
    if (M && typeof M.actorName === 'function') {
      const name = M.actorName();
      if (name) return name;
    }
    return '人工';
  }

  // ==========================================================================
  // 四、接线
  // ==========================================================================
  if (typeof document !== 'undefined') {
    // 发布成功（panel 广播 ontology-model:published）→ 立刻重读：新版本一发，可能就有知识"落后"了。
    document.addEventListener('ontology-model:published', () => { refresh(); });

    // 切进「本体建模层」页签时读一次：用户可能是直接点进来找迁移入口的（不经过发布）。
    const tabButton = document.querySelector('[data-tab="ontology-model"]');
    if (tabButton) tabButton.addEventListener('click', () => { refresh(); });

    // 项目一切换，上一套数字立刻作废：先清空（不显示旧项目的条数），再按新项目重读。
    const projectSelect = document.getElementById('project');
    if (projectSelect) {
      projectSelect.addEventListener('change', () => {
        state.plan = null; state.selected.clear();
        state.preview = null; state.previewSig = ''; state.result = null; state.error = '';
        renderStrip();
        if (drawer) renderDrawer();
        refresh();
      });
    }

    // 首次进入：项目的加载是异步的（app.js 的 projects()），这里轮询几次直到 current 就绪。
    let bootTries = 0;
    const boot = () => {
      if (activeProject()) { refresh(); return; }
      if (bootTries++ < 20) setTimeout(boot, 250);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
  }

  window.OntologyReclassify = {
    refresh,
    open: openDrawer,
    /** 供面板在发布成功后直接调用（事件之外显式再来一次，防止监听时序问题）。 */
    afterPublish: () => refresh(),
  };
})();
