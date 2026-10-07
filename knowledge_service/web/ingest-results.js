/* ============================================================================
 * 知识写入 · 抽取结果工作区抽屉（ingest-results.js）
 * ----------------------------------------------------------------------------
 * 2026-10-05 重做，解决三个问题：
 *   ① 不再靠“记录 ID 前缀”猜来源、也不再每 2 秒轮询：来源用正式的 **assertions
 *      断言表**，进度用 **SSE 实时推送**（任务多久推多久，无超时窗口）；
 *   ② 实体显示**本体类型**（如 商户 · Merchant），不只是名字；
 *   ③ 关系、属性分别成组；accepted=已进台账，pending=待人工确认，颜色区分。
 *
 * 数据口径（均经真实接口验证）：
 *   SSE  GET  /api/jobs/{jobId}/stream → snapshot/progress/completed/failed；
 *   断言 GET  /api/projects/{p}/assertions?document_id={docId}
 *        每条：kind(entity/relation/attribute) · status(accepted/pending) ·
 *              payload.text / payload.type / payload.subject_id / payload.object_id /
 *              payload.value；quote=原文引用。
 *
 * 由 workbench.js 提交后调用 window.IngestResults.watch(jobId, docTitle)。
 * ========================================================================== */
(() => {
  'use strict';

  const MAX_JOBS = 12;
  // 每个任务一个条目：进度用 SSE，结果用 assertions
  const jobs = new Map();   // jobId -> { title, status, progress, stage, error, asserts }

  // ---- 小工具 --------------------------------------------------------------
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  };
  const safe = (v, fb = '—') => (v === null || v === undefined || v === '' ? fb : String(v));
  // IRI 取末段短名：http://meituan.com/kg#Merchant → Merchant
  const shortIri = (v) => safe(v).split(/[\/:#]/).pop();

  // ---- 抽屉容器（右侧滑出；复用 provenance-drawer 范式） --------------------
  const root = el('section', 'ingest-drawer');
  root.id = 'ingest-drawer';
  root.hidden = true;
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'false');
  root.setAttribute('aria-labelledby', 'ingest-drawer-title');

  const header = el('header');
  const title = el('h2', null, '📊 抽取结果工作区');
  title.id = 'ingest-drawer-title';
  const closeBtn = el('button', 'ingest-drawer__close', '关闭 ×');
  closeBtn.type = 'button';
  header.append(title, closeBtn);

  const intro = el('p', 'ingest-drawer__intro',
    '提交后这里实时显示每个文档的解析进度；完成后按实体／关系／属性列出抽到了什么，实体带本体类型。');
  const listEl = el('div', 'ingest-drawer__list');
  root.append(header, intro, listEl);
  document.body.appendChild(root);

  const open = () => { root.hidden = false; };
  const close = () => { root.hidden = true; };
  closeBtn.addEventListener('click', close);
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } });

  const openBtn = document.getElementById('open-ingest-results');
  if (openBtn) openBtn.addEventListener('click', open);

  // ===========================================================================
  // 订阅一个任务：先看 SSE 实时进度，完成后拉 assertions 明细
  // ===========================================================================
  function watch(jobId, docTitle) {
    if (!jobId) return;
    if (!jobs.has(jobId)) {
      jobs.set(jobId, {
        title: docTitle || jobId,
        status: 'queued', progress: 0, stage: '已排队', error: '', asserts: null,
      });
    }
    open();
    render();
    streamJob(jobId);
  }

  // ---- SSE：fetch + 复用全局 SSE 解析器 splitSseBuffer ----------------------
  async function streamJob(jobId) {
    const ctrl = new AbortController();
    const data = jobs.get(jobId);
    let buffer = '';
    const parser = window.KnowledgeChat;
    try {
      const resp = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/stream', {
        headers: { 'Accept': 'text/event-stream' }, signal: ctrl.signal,
      });
      if (!resp.ok) throw Error('进度流连接失败（HTTP ' + resp.status + '）');
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const step = parser.splitSseBuffer(buffer);
        buffer = step.rest;
        for (const item of step.events) handleSse(jobId, item);
      }
      buffer += decoder.decode();
      const tail = parser.splitSseBuffer(buffer + '\n\n');
      for (const item of tail.events) handleSse(jobId, item);
    } catch (e) {
      if (e.name === 'AbortError') return;
      const d = jobs.get(jobId);
      if (d && d.status === 'running') d.stage = '进度流中断：' + e.message;
    } finally {
      render();
    }
  }

  function handleSse(jobId, item) {
    const d = jobs.get(jobId);
    if (!d) return;
    const p = item.data || {};
    switch (item.event) {
      case 'snapshot':
        // snapshot 是连接建立时的“初始回放”，只能填空、不能覆盖已收到的实时进度
        // （否则任务已跑到 97%，迟到的初始快照会把进度打回 15%）。
        if (d.status === 'queued') d.status = p.status || d.status;
        if ((d.progress === 0 || d.progress === undefined)
            && typeof p.progress === 'number') d.progress = p.progress;
        if (!d.stage || d.stage === '已排队') d.stage = p.stage || d.stage;
        if (!d.error && p.error) d.error = p.error;
        break;
      case 'progress':
        d.status = p.status || d.status;
        // 进度单调递增：乱序/重放的旧百分比不许把进度条往回拉
        const next = typeof p.percent === 'number' ? p.percent : p.progress;
        if (typeof next === 'number' && next >= (d.progress || 0)) d.progress = next;
        if (p.stage) d.stage = p.stage;
        if (p.error) d.error = p.error;
        break;
      case 'completed':
        d.status = 'completed'; d.progress = 100; d.stage = '处理完成';
        loadAssertions(jobId);
        break;
      case 'failed':
        d.status = 'failed';
        d.error = p.error || d.error;
        d.stage = '处理失败';
        break;
    }
    render();
  }

  // ---- 完成后拉断言明细（正式来源口径） ------------------------------------
  async function loadAssertions(jobId) {
    const d = jobs.get(jobId);
    try {
      // ingest run 的 document_id 即文档 ID；job 工件里 result.document.id 可拿到。
      // 这里直接取任务结果中的文档 ID（若取不到则用项目最近文档兜底由后端处理）。
      const job = await api('/api/jobs/' + encodeURIComponent(jobId), undefined, 'GET');
      const docId = job?.result?.document?.id || job?.result?.document_id;
      if (!docId) throw Error('任务结果里没有文档 ID');
      const r = await api(endpoint('/assertions?document_id=' + encodeURIComponent(docId)),
                         undefined, 'GET');
      d.asserts = Array.isArray(r.assertions) ? r.assertions : [];
    } catch (e) {
      d.asserts = null;
      d.stage = '结果读取失败：' + e.message;
    }
    render();
  }

  // ===========================================================================
  // 渲染
  // ===========================================================================
  const STATUS_ZH = {
    queued: '排队中', running: '处理中', completed: '已完成',
    failed: '失败', interrupted: '已中断',
  };

  function jobCard(jobId, d) {
    const card = el('article', 'ingest-job is-' + d.status);
    card.dataset.status = d.status;

    // 头部：文档名 + 状态徽标
    const head = el('div', 'ingest-job__head');
    head.append(el('b', 'ingest-job__title', d.title));
    head.append(el('span', 'ingest-job__badge', STATUS_ZH[d.status] || d.status));
    card.append(head);

    // 进度条（实时；完成 100%，失败不打满）
    const barWrap = el('div', 'ingest-job__bar');
    const bar = el('div', 'ingest-job__bar-fill');
    bar.style.width = Math.max(0, Math.min(100, Number(d.progress) || 0)) + '%';
    barWrap.append(bar, el('span', 'ingest-job__pct', Math.round(d.progress) + '%'));
    card.append(barWrap);
    card.append(el('p', 'ingest-job__stage', d.stage || ''));

    if (d.status === 'failed' && d.error) {
      card.append(el('p', 'ingest-job__error', '原文收据已保留，可改后重新上传：' + d.error));
    }

    // 明细分组（来自 assertions）
    if (d.asserts) card.append(renderAsserts(d.asserts));
    return card;
  }

  // 胶囊展示上限：超过则先折叠多余部分，点开才看全（避免候选上百条时首屏过长）
  const CHIP_PREVIEW_LIMIT = 12;

  // 三类知识分组：可折叠 details + 胶囊超量截断；入图/待确认计数写在标题
  function renderAsserts(asserts) {
    const box = el('div', 'ingest-result');
    // 实体默认展开；关系、有大量待确认时默认收起，按需点开
    const groups = [
      ['实体', 'entity', true], ['关系', 'relation', false], ['属性', 'attribute', false],
    ];
    let anyShown = false;
    for (const [label, kind, openByDefault] of groups) {
      const rows = asserts.filter(a => a.kind === kind);
      if (!rows.length) continue;
      anyShown = true;

      // 分组用可折叠 details；summary 里放名称与计数
      const g = el('details', 'ingest-result__group');
      if (openByDefault || rows.length <= CHIP_PREVIEW_LIMIT) g.open = true;
      const sum = el('summary', 'ingest-result__label');
      const nAccepted = rows.filter(r => r.status === 'accepted').length;
      const nPending = rows.length - nAccepted;
      sum.append(el('b', null, label), el('span', null,
        rows.length + ' 条' + (nAccepted ? ' · 入图 ' + nAccepted : '')
        + (nPending ? ' · 待确认 ' + nPending : '')));
      g.append(sum);

      // 胶囊容器：先放前 N 条，超出的折叠到“展开全部”按钮后
      const wrap = el('div', 'ingest-result__chips');
      const visible = rows.slice(0, CHIP_PREVIEW_LIMIT);
      for (const a of visible) wrap.append(assertChip(a, kind));
      if (rows.length > CHIP_PREVIEW_LIMIT) {
        const moreBtn = el('button', 'ingest-result__more',
          '展开其余 ' + (rows.length - CHIP_PREVIEW_LIMIT) + ' 个');
        moreBtn.type = 'button';
        moreBtn.addEventListener('click', () => {
          // 补齐剩余胶囊后移除按钮；只在首次展开时操作，不重复追加
          for (const a of rows.slice(CHIP_PREVIEW_LIMIT)) {
            wrap.insertBefore(assertChip(a, kind), moreBtn);
          }
          moreBtn.remove();
        });
        wrap.append(moreBtn);
      }
      g.append(wrap);
      box.append(g);
    }
    if (!anyShown) {
      box.append(el('p', 'ingest-result__empty',
        '本次只保存了原文和切片，没有抽出实体／关系／属性（「仅文档检索」模式，或原文里没有可抽取的知识）。'));
    }
    return box;
  }

  // 单个胶囊：pending 候选与 accepted 规范记录字段结构不同，分开渲染。
  function assertChip(a, kind) {
    const p = a.payload || {};
    const cls = 'ingest-result__chip is-' + kind + (a.status === 'pending' ? ' is-pending' : '');
    let text, tip;
    if (a.status === 'pending') {
      // 待确认候选：字段是 proposed_type / subject / object / predicate / reason
      if (kind === 'entity') {
        text = safe(p.text) + ' · ' + safe(p.proposed_type);
        tip = safe(p.reason, '待人工确认');
      } else if (kind === 'relation') {
        text = safe(p.subject) + ' ' + safe(p.predicate) + ' ' + safe(p.object);
        const issues = Array.isArray(p.constraint_issues) ? p.constraint_issues : [];
        tip = issues.length
          ? issues.map(i => safe(i.message) || safe(i.actual_type)).join('；')
          : safe(p.reason, '待人工确认');
      } else {
        // 待确认属性：真实字段是 subject(实体名) + proposed_type(属性名) + value
        const v = p.value === undefined ? '' : (typeof p.value === 'string' ? p.value : JSON.stringify(p.value));
        text = safe(p.subject) + ' · ' + safe(p.proposed_type) + ' = ' + safe(v);
        tip = safe(p.reason, '待人工确认');
      }
    } else {
      // 已入图规范记录：type 是 IRI、关系两端是记录 ID
      if (kind === 'entity') {
        text = safe(p.text) + ' · ' + shortIri(p.type);
      } else if (kind === 'relation') {
        text = safe(p.text) ||
          (shortRef(p.subject_id) + ' —' + shortIri(p.type) + '→ ' + shortRef(p.object_id));
      } else {
        const v = p.value === undefined ? '' : (typeof p.value === 'string' ? p.value : JSON.stringify(p.value));
        text = shortRef(p.subject_id) + ' · ' + shortIri(p.type) + ' = ' + safe(v);
      }
      tip = '原文：' + safe(a.quote, '');
    }
    const chip = el('span', cls, text);
    chip.title = tip;
    return chip;
  }
  // 端点引用取短标识：优先用断言里能对应的实体名，取不到退化为 ID 末段
  const shortRef = (id) => safe(id).split(/[\/:#]/).pop();

  // ---- 整体渲染：最近 MAX_JOBS 个，倒序（最新在最上） ----------------------
  function render() {
    const entries = Array.from(jobs.entries()).slice(-MAX_JOBS).reverse();
    listEl.replaceChildren();
    if (!entries.length) {
      listEl.append(el('p', 'ingest-drawer__empty',
        '还没有任务：在下面选择文件、点「上传并处理」，进度和抽取结果会实时出现在这里。'));
      return;
    }
    for (const [jobId, d] of entries) listEl.append(jobCard(jobId, d));
  }

  render();

  // ---- 对外接口 ------------------------------------------------------------
  window.IngestResults = { watch, open, close };
})();
