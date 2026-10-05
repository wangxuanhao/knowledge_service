/* ============================================================================
 * 本体建模层（ontology-model.js）—— 单画布页的【主控 / 地基】
 *
 * P1（2026-10-04）：把结构层三页（本体编辑台 / 本体审核台 / 本体档案）合成一张画布。
 * 本文件只做三件事，其余都交给子模块：
 *   ① 持有状态 S、编译画布图（summary → graph）；
 *   ② 加载（当前本体 + 草案状态）与装配（顶栏 / 左清单 / 中画布 / 右详情 / 底栏）；
 *   ③ 暴露命名空间 window.OntologyModel（含事件总线与草案读写）。
 *
 * 分工（见 docs/2026-10-04-P1前端实现契约.md）：
 *   · 画布渲染与编辑 → ontology-model-canvas.js（window.OntologyCanvas）
 *   · 右栏详情 / 底栏动作 / 版本管理 → ontology-model-panel.js（window.OntologyPanel）
 *   三者只通过本文件的事件总线 on/emit 与 S 通信，互不直接依赖。
 *
 * 后端一行不改（设计文档 §5.3）：编辑走 ontology-drafts 命令通道，
 * 「校验并审核」= validate + submit + batch-approve，「发布」= publish。
 * 依赖全局（app.js 已注入）：api(path,body,method)、endpoint(suffix)、current、$、esc、status。
 * ========================================================================== */
(() => {
  const root = () => document.getElementById('tab-ontology-model');
  // 术语取名：优先中文名，回落到本地名，最后取 IRI 末段
  const label = it => it.label_zh || it.label || it.name ||
    String(it.id || '').split(/[\/#]/).pop() || '(未命名)';
  const shortIri = iri => {
    const s = String(iri || '');
    return s.length > 20 ? s.slice(0, 11) + '…' + s.slice(-6) : s;
  };
  // 属性（DatatypeProperty）的 range 是字面量数据类型（如 xsd:string），取短名给人看
  const dtypeOf = a => {
    const r = (a.range || [])[0] || '';
    return String(r).split(/[#\/]/).pop().replace(/^xsd:/, '') || '';
  };
  const escHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---- 共享状态（子模块只读；改动一律走本文件的函数 + emit） ------------------
  const S = {
    summary: null,            // GET /ontology → { summary:{classes,relations,attributes} }
    ontologyId: null,         // 当前最新本体 id（发布/决策的 expected_ontology_id）
    graph: null,              // 编译好的画布图 { nodes, edges }
    selected: null,           // 选中的术语 IRI
    draft: null,              // 当前草案（id / revision / status / ontology）
    showingDraft: false,      // true = 画布画的是**草案**（未发布）；false = 已发布本体
    preview: null,            // 只读预览的历史版本 { id, version }；null = 不看历史
    view: 'graph',            // 'graph'（图谱视图）| 'hierarchy'（层级树）
    stageAvailability: null,  // GET /stage-availability（阶段门禁，服务端唯一出处）
    readiness: null,          // GET /publish-readiness（发布清单）
    candidates: [],           // 待收下候选（画布上的虚线节点）
    error: null,
  };

  // ---- 事件总线（主控 ↔ 子模块的唯一通道） ----------------------------------
  const listeners = new Map();
  function on(event, handler) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(handler);
    return () => listeners.get(event).delete(handler);
  }
  function emit(event, payload) {
    for (const handler of (listeners.get(event) || [])) {
      try { handler(payload); } catch (e) { console.error('[ontology-model] handler failed', event, e); }
    }
  }

  // ---- 画布图编译：把本体 summary 编译成画布能直接渲染的 { nodes, edges } ------
  // 节点 = 类（画布的主角）；边 = 继承（实线）+ 关系类型（domain→range，紫色虚线）。
  // 属性不单独成节点（它挂在类上），由右栏详情在被选中的类下面展开。
  function compileGraph() {
    const nodes = [], edges = [];
    const classes = (S.summary && S.summary.classes || []).filter(c => c.active !== false);
    const classIds = new Set(classes.map(c => c.id));
    for (const c of classes) {
      nodes.push({ id: c.id, iri: c.id, label: label(c), kind: 'class', dtype: '',
        state: S.selected === c.id ? 'selected' : 'published' });
    }
    // 待收下候选（机器抽出的新概念）→ 虚线节点，来源标记 pending
    for (const cand of S.candidates) {
      const id = cand.iri || cand.id;
      if (!id || classIds.has(id)) continue;
      nodes.push({ id, iri: id, label: cand.label || label(cand), kind: cand.kind || 'class',
        dtype: cand.dtype || '', state: 'pending' });
    }
    // 继承边：父 → 子（父子都在集合里才画）
    const known = new Set(nodes.map(n => n.id));
    for (const c of classes) {
      for (const p of (c.parents || [])) {
        if (known.has(p)) edges.push({ id: 'inh:' + p + '>' + c.id, source: p, target: c.id, kind: 'inherit' });
      }
    }
    // 关系边：domain → range 两端都在集合里才画
    for (const r of (S.summary && S.summary.relations || []).filter(x => x.active !== false)) {
      const doms = (r.domain || []).filter(d => known.has(d));
      const rngs = (r.range || []).filter(g => known.has(g));
      for (const d of doms) for (const g of rngs) {
        edges.push({ id: 'rel:' + r.id + ':' + d + '>' + g, source: d, target: g,
          kind: 'relation', label: label(r) });
      }
    }
    S.graph = { nodes, edges };
    return S.graph;
  }

  const EMPTY_SUMMARY = { classes: [], relations: [], attributes: [] };
  // 只有待处理的草案能继续改（终态：accepted 已收下 / rejected 已驳回）
  const OPEN_STATES = ['pending'];

  // ---- 加载：当前本体 + 草案状态 --------------------------------------------
  // 「点进来就能改、改了立刻看得见」是用户的核心诉求，所以刷新分三步：
  //   ① 读**当前最新已发布本体** —— 它既是「没有草案时」的展示内容，也是
  //      expected_ontology_id（服务端 _check_current 拿它与 draft.base_ontology_id 比对比对不过即 StaleBase）；
  //   ② 挑一份草案：优先复用选中的，其次项目里**待处理**的那份（用户上次没改完的）；
  //   ③ **有草案就画草案**（GET /ontology-drafts/{id} 的 `ontology` 就是草案自己的结构）。
  // 改动③之前这里只读已发布本体，后果是"编辑写进草案、画布却按已发布版本画" ——
  // 改了半天屏幕上一个字没变，用户以为没改成功。顶栏会标明现在画的是草案还是已发布版本。
  async function refresh() {
    if (typeof current === 'undefined' || !current) {
      S.summary = null; S.error = null; S.showingDraft = false; compileGraph(); render();
      return S;
    }
    // ① 已发布本体（失败也不致命：有草案时照样能画）
    let published = null;
    try { published = await api(endpoint('/ontology'), undefined, 'GET'); }
    catch (e) { published = null; }
    S.ontologyId = published ? (published.id || null) : S.ontologyId;

    // ①' 只读预览历史版本：优先于草案展示（用户刚点了「查看这一版」），
    //     但**不销毁**草稿状态 —— 横幅会说明"改动没丢，点这里回去"。
    if (S.preview && S.preview.id) {
      try {
        const item = await api(endpoint(`/ontology?ontology_id=${encodeURIComponent(S.preview.id)}`), undefined, 'GET');
        S.summary = item.summary || EMPTY_SUMMARY;
        S.showingDraft = false;
        S.error = null;
        await refreshDraftState();
        compileGraph();
        emit('graph:changed', { graph: S.graph });
        render();
        return S;
      } catch (e) {
        // 读不到那一版就退出预览：不能把用户困在一张点不动的画布上
        S.preview = null;
      }
    }

    // ② 选草案（不新建 —— 新建只在真正要写的时候发生，否则一进页面就堆草案）
    let draft = (S.draft && S.draft.id) ? S.draft : null;
    if (!draft) {
      try {
        const list = await api(endpoint('/ontology-drafts'), undefined, 'GET');
        draft = (list.items || []).find(d => OPEN_STATES.includes(d.status)) || null;
      } catch (e) { draft = null; }
    }

    // ③ 有草案就画草案；读不到草案详情才回落到已发布本体（绝不画空白）
    let detail = null;
    if (draft && draft.id) {
      try { detail = await api(endpoint(`/ontology-drafts/${draft.id}`), undefined, 'GET'); }
      catch (e) { detail = null; }
    }
    if (detail) {
      S.draft = detail;
      S.summary = detail.ontology || EMPTY_SUMMARY;
      S.showingDraft = true;
      S.error = null;
    } else {
      S.draft = draft;
      S.showingDraft = false;
      if (published) { S.summary = published.summary || EMPTY_SUMMARY; S.error = null; }
      else { S.summary = null; S.error = '读取当前本体失败'; }
    }
    await refreshDraftState();
    compileGraph();
    emit('graph:changed', { graph: S.graph });
    render();
    return S;
  }

  // 读阶段门禁（服务端唯一出处）+ 发布清单。拿不到就置空 —— 子模块据此显示"读不到"，
  // 而不是自己推规则（skill：门禁只有一处出处，前端不自己算）。
  async function refreshDraftState() {
    if (!S.draft || !S.draft.id) { S.stageAvailability = null; S.readiness = null; return; }
    const base = `/ontology-drafts/${S.draft.id}`;
    // stage-availability 是**项目级**路由，草案用 draft_id 查询参数指定；
    // 写成 /{draft_id}/stage-availability 会 404，还会被 catch 吞掉 —— 门禁永远为空、按钮永远禁用。
    try {
      S.stageAvailability = await api(
        endpoint(`/ontology-drafts/stage-availability?draft_id=${encodeURIComponent(S.draft.id)}`),
        undefined, 'GET');
    } catch (e) { S.stageAvailability = null; }
    try {
      S.readiness = await api(endpoint(base + '/publish-readiness'), undefined, 'GET');
    } catch (e) { S.readiness = null; }
    emit('stage:changed', { stageAvailability: S.stageAvailability, readiness: S.readiness });
  }

  // ---- 草案读写：编辑前先有草案，命令逐条发送 -------------------------------
  // 迁移 0005 后「请求」只有三态：pending（待处理）→ accepted（已收下）/ rejected（已驳回）；
  // 旧词 editing/submitted/reviewed/stale_* 已并入 pending，别再用旧词筛（会永远筛不到、每次编辑都新建草案）。
  async function ensureDraft() {
    // 只有**待处理**的草案能继续改。发布过（accepted）或已驳回（rejected）的草案是终态：
    // 服务端对它们发 commands 一律 422「已经收下」。以前这里只看 `S.draft && S.draft.id`，
    // 于是发布完当场再编辑，命令还打给那份终态草案 —— 用户看到的就是"发布了就不能再改了"，
    // 刷新一下才好（刷新后 S.draft 归零才重开）。这里改成按状态判断 + 直接换新草案。
    if (S.draft && S.draft.id && OPEN_STATES.includes(S.draft.status)) return S.draft;
    S.draft = null;
    // 优先复用项目里未完结的草案（避免每次编辑都新建；选择口径同审核台的 pickDraft）
    try {
      const list = await api(endpoint('/ontology-drafts'), undefined, 'GET');
      const open = (list.items || []).find(d => OPEN_STATES.includes(d.status));
      if (open) {
        // 列表项是轻量记录：补一次详读，才能拿到最新 revision（乐观锁）与草案结构
        const detail = await api(endpoint(`/ontology-drafts/${open.id}`), undefined, 'GET')
          .catch(() => open);
        S.draft = detail;
        await refreshDraftState();
        emit('draft:changed', { draft: detail });
        return detail;
      }
    } catch (e) { /* 读不到列表就按"没有"处理，下面新建 */ }
    // 没有就新建：基线必须是**项目当前最新本体**，否则服务端会 409 stale_base
    const ont = await api(endpoint('/ontology'), undefined, 'GET');
    const draft = await api(endpoint('/ontology-drafts'), {
      base_ontology_id: ont.id, source_kind: 'manual',
      title: '画布编辑', actor: actorName(), summary: '在本体建模层画布上编辑',
    }, 'POST');
    S.draft = draft;
    await refreshDraftState();
    emit('draft:changed', { draft });
    return draft;
  }

  // 发一条命令到当前草案：自动建草案 → 带乐观锁 revision → 返回更新后的草案并刷新
  async function sendCommand(command) {
    const draft = await ensureDraft();
    const updated = await api(endpoint(`/ontology-drafts/${draft.id}/commands`), {
      expected_revision: draft.revision, command,
    }, 'POST');
    S.draft = updated;
    emit('draft:changed', { draft: updated });
    await refresh();
    return updated;
  }

  // 提交人标识：app.js 无用户体系，单用户场景用固定标识（可被 window.KS_ACTOR 覆盖）
  const actorName = () => (typeof window !== 'undefined' && window.KS_ACTOR) || 'canvas-editor';

  // 幂等键：一次发布动作生成一次、重试间复用（服务端要求 idempotency_key 必填）
  const uid = () => (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'uid-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);

  // ---- 选中：唯一入口，三条路径（左清单 / 画布 / 详情跳转）都走这里 ----------
  function select(iri) {
    S.selected = iri || null;
    compileGraph();
    emit('select', { iri: S.selected });
    render();
  }

  // ---- 渲染：骨架构建一次，其余只更新可变部分 --------------------------------
  let built = false;
  function render() {
    const el = root(); if (!el) return;
    if (!built) { buildSkeleton(el); built = true; }
    renderSidebar();
    // 子模块可能尚未加载（脚本顺序 / 测试 mock shell）——用可选链，缺谁都不崩
    if (window.OntologyCanvas) window.OntologyCanvas.render(S);
    if (window.OntologyPanel) window.OntologyPanel.render(S);
  }

  function buildSkeleton(el) {
    el.innerHTML = `
      <div class="om-page">
        <div class="om-topbar">
          <span class="om-brand">◆ 本体建模层</span>
          <span class="om-ctx" id="om-version">当前本体 v?</span>
          <div class="om-seg" role="group" aria-label="视图切换">
            <button type="button" data-om-view="graph" class="on" title="图谱视图：力导向铺开全部关系，适合整体鸟瞰——不保证层级位置固定">图谱视图</button>
            <button type="button" data-om-view="hierarchy" title="层级树：按继承深度分带排列，看清谁在第几层——关系线只作参考，不参与分层">层级树</button>
          </div>
          <button type="button" class="om-btn ghost" id="om-history" title="版本管理：看每一版是谁、什么时候、为什么发的；可以查看任意一版，也可以以某一版为基础开一份新草案（历史不改写）">版本管理</button>
        </div>
        <div class="om-preview-banner" id="om-preview-banner" hidden>
          <span id="om-preview-text"></span>
          <button type="button" class="om-btn ghost" id="om-preview-exit">回到最新</button>
        </div>
        <div class="om-body">
          <!-- 用 div 不用 aside：全站 style.css 有一条给应用侧栏的全局规则
               aside{position:fixed;inset:0 auto 0 0;width:206px}，任何 aside 都会被固定到
               视口左上角、脱离三栏流（P1 实测踩到：三栏塌成空白）。 -->
          <div class="om-side" id="om-side" aria-label="本体三维清单"></div>
          <div class="om-canvas-host" id="om-canvas-host"></div>
          <div class="om-detail" id="om-detail-host" aria-label="节点详情"></div>
        </div>
        <div class="om-actionbar" id="om-actions-host" aria-label="校验与发布"></div>
      </div>`;
    // 视图切换：只改 S.view 并发事件，布局由画布模块负责
    el.querySelectorAll('[data-om-view]').forEach(btn => btn.addEventListener('click', () => {
      S.view = btn.dataset.omView;
      el.querySelectorAll('[data-om-view]').forEach(b => b.classList.toggle('on', b === btn));
      emit('view:changed', { view: S.view });
      if (window.OntologyCanvas) window.OntologyCanvas.setView(S.view);
    }));
    const history = document.getElementById('om-history');
    if (history) history.addEventListener('click', () => window.OntologyPanel && window.OntologyPanel.openHistory());
    // 「回到最新」：退出历史版本预览。只改查看状态，不写任何数据 ——
    // 预览是只读的，退出预览只是把画布切回草案/最新已发布版本。
    const exitPreview = document.getElementById('om-preview-exit');
    if (exitPreview) exitPreview.addEventListener('click', () => {
      clearPreview();
      if (typeof status === 'function') status('已回到最新本体。');
    });
    // 装配子模块（各挂自己那一块 DOM）
    const ctx = window.OntologyModel;
    if (window.OntologyCanvas) window.OntologyCanvas.mount(document.getElementById('om-canvas-host'), ctx);
    if (window.OntologyPanel) window.OntologyPanel.mount(
      document.getElementById('om-detail-host'), document.getElementById('om-actions-host'), ctx);
  }

  // 左栏三维清单（类 / 关系 / 属性）。点谁 = 选中谁（唯一入口 select）。
  function renderSidebar() {
    const host = document.getElementById('om-side'); if (!host) return;
    if (!S.summary) {
      host.innerHTML = `<div class="om-side-empty">${escHtml(S.error || '请先在上方选择一个知识项目')}</div>`;
      return;
    }
    const classes = (S.summary.classes || []).filter(c => c.active !== false);
    const rels = (S.summary.relations || []).filter(r => r.active !== false);
    const attrs = (S.summary.attributes || []).filter(a => a.active !== false);
    const row = (it, chip) => `<li class="${it.id === S.selected ? 'sel' : ''}" data-sel="${escHtml(it.id)}">
        <span class="om-chip ${chip}"></span>${escHtml(label(it))}</li>`;
    host.innerHTML = `
      <h4><i class="om-dot"></i>本体类型 · ${classes.length}</h4>
      <ul>${classes.map(c => row(c, 'cls')).join('') || '<li class="om-none">（还没有类）</li>'}</ul>
      <h4 class="rel"><i class="om-dot"></i>关系类型 · ${rels.length}</h4>
      <ul>${rels.map(r => row(r, 'rel')).join('') || '<li class="om-none">（还没有关系）</li>'}</ul>
      <h4 class="attr"><i class="om-dot"></i>属性类型 · ${attrs.length}</h4>
      <ul>${attrs.map(a => `<li class="${a.id === S.selected ? 'sel' : ''}" data-sel="${escHtml(a.id)}">
          <span class="om-chip attr"></span>${escHtml(label(a))}
          <span class="om-dtype">${escHtml(dtypeOf(a) || '?')}</span></li>`).join('') || '<li class="om-none">（还没有属性）</li>'}</ul>
      <div class="om-tip">点谁 = 选中谁（左栏/画布一致）<br>实线 = 父子 · 紫虚线 = 关系 · 黄虚线 = 待收下</div>`;
    host.querySelectorAll('[data-sel]').forEach(li => li.addEventListener('click', () => select(li.dataset.sel)));
    const ver = document.getElementById('om-version');
    if (ver) {
      // 必须一眼看出"现在画的是什么、算不算数"：用户改完看不见变化时，
      // 第一件事就是分不清屏幕上的东西算不算数（这是 P1 前的真实困惑来源）。
      if (S.preview) {
        ver.textContent = `v${S.preview.version || '?'}（历史版本 · 只读） · ${S.summary.triples || 0} 三元组`;
      } else {
        ver.textContent = `${S.showingDraft ? '草案 · 未发布' : '已发布本体'} · ${S.summary.triples || 0} 三元组`;
      }
      ver.classList.toggle('is-draft', !S.preview && !!S.showingDraft);
      ver.classList.toggle('is-preview', !!S.preview);
      ver.title = S.preview
        ? '这是历史版本的只读预览：不能在这里编辑。要在这一版的基础上改，用「版本管理」里的「回到这一版」开一份新草案。'
        : (S.showingDraft
          ? '画布上就是这份草案的内容：改动写进草案，底栏「校验并审核」后发布才生效'
          : '画布上是已发布的本体版本：任何改动都会自动开一份新草案，不会改到这一版');
    }
    renderPreviewBanner();
  }

  // ---- 挂载：点「本体建模层」页签 / 切项目时加载 ------------------------------
  function wire() {
    const tab = document.querySelector('[data-tab="ontology-model"]');
    if (tab) tab.addEventListener('click', () => { void refresh(); });
    const proj = document.getElementById('project');
    if (proj) proj.addEventListener('change', () => {
      S.summary = null; S.ontologyId = null; S.graph = null; S.selected = null; S.draft = null;
      S.showingDraft = false; S.preview = null;
      S.stageAvailability = null; S.readiness = null; S.candidates = [];
      built = false; render();
    });
  }

  document.addEventListener('DOMContentLoaded', wire);
  /* ==========================================================================
   * 版本管理（只读预览 / 回到某一版）
   * --------------------------------------------------------------------------
   * 三条不可动摇的语义（用户提出的"版本回退 + 切换"必须落在这三条上，否则会变成
   * "点一下就把生产切回去了"这种没人敢用的开关）：
   *   ① **查看 ≠ 生效**：查看历史版本是只读的，只切画布，不碰草案、不碰当前版本。
   *   ② **回退 = 再发一版**：历史版本是不可变的，"回到 v1"不是删掉 v2/v3，
   *      而是以 v1 为基础开一份新草案 → 审核 → 发布成 v4。审核链因此永远完整。
   *   ③ **有草案时不许回退**：手上还有没发布/没驳回的改动时先处理完，
   *      否则两份草案各自基于不同基线，谁也说不清最后发布的是什么。
   * ======================================================================== */
  function renderPreviewBanner() {
    const banner = document.getElementById('om-preview-banner');
    const text = document.getElementById('om-preview-text');
    if (!banner || !text) return;
    if (!S.preview) { banner.hidden = true; text.textContent = ''; return; }
    const openDraft = !!(S.draft && S.draft.id && OPEN_STATES.includes(S.draft.status));
    text.textContent = `正在查看 v${S.preview.version || '?'}（历史版本 · 只读，不能编辑）`
      + (openDraft
        ? '。你那份还没发布的草案没有丢，点右边就能回去接着改。'
        : '。要在这一版的基础上改，请用「版本管理」里的「回到这一版」。');
    banner.hidden = false;
  }

  // 只读预览某一历史版本（不动任何数据）
  function previewVersion(ontologyId, version) {
    S.preview = { id: ontologyId, version: version || null };
    S.selected = null;
    emit('preview:changed', { preview: S.preview });
    return refresh();
  }

  // 退出预览，回到"草案优先、否则最新已发布版本"的正常视图
  function clearPreview() {
    if (!S.preview) return Promise.resolve(S);
    S.preview = null;
    emit('preview:changed', { preview: null });
    return refresh();
  }

  // 回到某一版 = 以它为基线开一份**新草案**（历史不改写，仍要审核 → 发布）
  async function forkFrom(ontologyId) {
    const open = (S.draft && S.draft.id && OPEN_STATES.includes(S.draft.status)) ? S.draft : null;
    if (open) {
      throw new Error('还有一份待处理的草案（没发布、也没驳回）。先把那份处理掉，再回到历史版本 —— '
        + '否则两份草案各自基于不同基线，没人说得清最后发布的是什么。');
    }
    const draft = await api(endpoint('/ontology-drafts'), {
      // source_kind='revert' 是**显式意图**：服务端只对它放行"基线不是最新版"，
      // 并且发布时会要求勾选那条回退警告。普通草案仍必须基于当前版本 ——
      // 那条保护防的是"在旧基线上做普通编辑会静默丢掉别人的改动"，是另一件事。
      base_ontology_id: ontologyId, source_kind: 'revert',
      title: '回到历史版本', actor: actorName(),
      summary: '以某一已发布版本为基础开的新草案（回退不改写历史，发布后成为新版本）',
      source_context: { revert_from: ontologyId },
    }, 'POST');
    S.preview = null;
    S.draft = draft;
    emit('preview:changed', { preview: null });
    emit('draft:changed', { draft });
    await refresh();
    return draft;
  }

  window.OntologyModel = {
    S, on, emit, render, refresh, refreshDraftState, ensureDraft, sendCommand, select, compileGraph,
    actorName, uid, previewVersion, clearPreview, forkFrom,
    status: (m, e) => (typeof status === 'function' ? status(m, e) : console.log(m)),
    helpers: { label, shortIri, dtypeOf, esc: escHtml },
  };
})();
