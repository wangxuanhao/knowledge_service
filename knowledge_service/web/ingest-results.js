/* ============================================================================
 * 知识写入 · 抽取结果工作区抽屉（ingest-results.js）
 * ----------------------------------------------------------------------------
 * 2026-10-05：原先把抽取卡片堆在页面顶部，又大又挤、把上传区顶下去，用户嫌丑。
 * 改为**右侧滑出的工作区抽屉**：
 *   ① 点「📊 抽取结果工作区」打开；提交成功后也会自动滑出；
 *   ② 抽屉顶部**打印汇总**：几个文档、实体／关系／属性各多少、待审与失败多少；
 *   ③ 下方按文档列明细（默认折叠，最新一份自动展开），展开看实体/关系胶囊；
 *   ④ 任务在跑时每 2s 自动刷新，结束即停；Esc 或 × 关闭。
 *
 * 字段口径（实测确认，勿臆测）：
 *   run.counts = chunks_* / assertions_pending / records_accepted；run.active_stage；
 *   kind='document' 的记录 id 即文档 ID（metadata.title / text 可作标题）；
 *   派生记录（实体/关系/属性）顶层 source_id 多为空 —— 来源编码在**记录 ID 前缀**：
 *     文档ID=7573…；实体ID=7573…:chunk:0:entity_xxx；关系ID=7573…:chunk:0:relation_xxx；
 *   chunk 则用 source_id=文档ID；实体.text=名称；关系.subject_id/object_id 引用实体。
 * 依赖全局（app.js / workbench.js 注入）：api、endpoint、current。
 * ========================================================================== */
(() => {
  'use strict';

  const MAX_DOCS = 8;            // 工作区最多展示最近几份文档
  const state = {
    nameById: new Map(),         // 实体记录 ID -> 实体名
    titleById: new Map(),        // 文档 ID -> 标题
    timer: null,                 // 轮询句柄
    polls: 0,                    // 剩余轮询次数
    serial: 0,
    openDocId: null,             // 当前展开明细的文档 ID
    opened: false,               // 抽屉是否处于打开状态
  };

  // ---- 小工具 --------------------------------------------------------------
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  };
  const list = (v) => (Array.isArray(v) ? v : []);
  const safe = (v, fb = '—') => (v === null || v === undefined || v === '' ? fb : String(v));
  const nameOf = (id) => state.nameById.get(id) || safe(id).split(/[\/:#]/).pop();

  // 派生记录是否来自指定文档（多重口径兜底，实测确认）：
  //   ① 实体/关系/属性：记录 ID 以「文档ID:」开头（来源编码在 ID 前缀）；
  //   ② chunk / 显式挂源的记录：source_id === 文档ID。
  function belongsToDoc(row, docId) {
    if (!row || !docId) return false;
    if (typeof row.id === 'string' && row.id.indexOf(docId + ':') === 0) return true;
    return row.source_id === docId;
  }

  // ---- 抽屉 DOM（挂到 body，右侧滑出；与 provenance-drawer 同一范式） --------
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
  closeBtn.setAttribute('aria-label', '关闭抽取结果工作区');
  header.append(title, closeBtn);

  const intro = el('p', 'ingest-drawer__intro',
    '按文档汇总本次解析抽出了什么：顶部是总数，点开某份文档看具体的实体／关系。');
  const liveStatus = el('p', 'ingest-drawer__status');
  liveStatus.setAttribute('role', 'status');
  liveStatus.setAttribute('aria-live', 'polite');
  const summaryBox = el('div', 'ingest-drawer__summary');   // 汇总指标网格
  const docsBox = el('div', 'ingest-drawer__docs');         // 文档明细列表

  root.append(header, intro, liveStatus, summaryBox, docsBox);
  document.body.appendChild(root);

  // ---- 数据读取 ------------------------------------------------------------
  const readRuns = async () => {
    const r = await api(endpoint('/ingest-runs'), undefined, 'GET');
    return list(r.runs);
  };
  const readRecords = async () => {
    // Scope 无独立 kinds 字段（会被忽略），全量拉回后在前端按文档归属过滤
    const r = await api(endpoint('/records/query?limit=1000'), { include_unknown: true });
    return list(r.records);
  };

  // 建两张索引：文档标题（document 记录）、实体名（entity 记录）
  function buildIndexes(records) {
    state.nameById = new Map();
    state.titleById = new Map();
    for (const row of records) {
      if (row.kind === 'document') {
        const t = (row.metadata && row.metadata.title) || row.text || row.id;
        state.titleById.set(row.id, t);
      } else if (row.kind === 'entity') {
        state.nameById.set(row.id, row.text || row.id);
      }
    }
  }

  // ---- 汇总计算（只统计最近 MAX_DOCS 份） -----------------------------------
  function computeSummary(runs, records) {
    const recent = runs.slice(-MAX_DOCS);
    const docIds = recent.map(r => r.document_id);
    let entity = 0, relation = 0, attribute = 0, pending = 0, failed = 0;
    for (const r of recent) {
      if (r.status === 'failed') failed += 1;
      pending += Number((r.counts || {}).assertions_pending || 0);
    }
    for (const row of records) {
      if (!docIds.some(d => belongsToDoc(row, d))) continue;
      if (row.kind === 'entity') entity += 1;
      else if (row.kind === 'relation') relation += 1;
      else if (row.kind === 'attribute') attribute += 1;
    }
    return { docs: recent.length, entity, relation, attribute, pending, failed };
  }

  // 汇总指标：一格一个数，一眼看全
  function renderSummary(s) {
    summaryBox.replaceChildren();
    const items = [
      ['文档', s.docs, ''],
      ['实体', s.entity, 'primary'],
      ['关系', s.relation, ''],
      ['属性', s.attribute, ''],
      ['待确认', s.pending, 'warn'],
      ['失败', s.failed, 'danger'],
    ];
    for (const [label, value, tone] of items) {
      const card = el('div', 'ingest-drawer__metric' + (tone ? ' is-' + tone : ''));
      card.append(el('b', null, String(value)), el('span', null, label));
      summaryBox.append(card);
    }
  }

  // ---- 单个文档的折叠明细 --------------------------------------------------
  const STATUS_ZH = { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', interrupted: '已中断' };
  const statusZh = (r) => STATUS_ZH[r.status] || safe(r.status);
  const relationType = (t) => safe(t).split(/[\/:#]/).pop();

  function stageLine(run) {
    const c = run.counts || {};
    if (run.status === 'completed') {
      return '切片 ' + safe(c.chunks_total, 0) + ' · 入库 ' + safe(c.records_accepted, 0)
        + (c.assertions_pending ? ' · 待审 ' + c.assertions_pending : '');
    }
    if (run.active_stage === 'chunking') return '正在切片…';
    if (run.active_stage === 'extraction') return '正在抽取实体／关系／属性…';
    if (run.status === 'queued') return '排队等待…';
    return '处理中…';
  }

  function entityChip(row) { return el('span', 'ingest-drawer__chip is-entity', safe(row.text)); }
  function relationChip(row) {
    return el('span', 'ingest-drawer__chip is-relation',
      nameOf(row.subject_id) + ' —' + relationType(row.type) + '→ ' + nameOf(row.object_id));
  }
  function attributeChip(row) {
    const v = row.value === undefined ? '' : (typeof row.value === 'string' ? row.value : JSON.stringify(row.value));
    return el('span', 'ingest-drawer__chip is-attribute',
      nameOf(row.subject_id) + ' · ' + safe(row.type) + ' = ' + safe(v));
  }

  function docBlock(run, records) {
    const docId = run.document_id;
    const box = el('details', 'ingest-drawer__doc');
    if (state.openDocId === docId) box.open = true;
    box.addEventListener('toggle', () => { if (box.open) state.openDocId = docId; });

    const sum = el('summary', 'ingest-drawer__doc-head');
    const nameWrap = el('span', 'ingest-drawer__doc-name');
    nameWrap.append(el('b', null, safe(state.titleById.get(docId) || docId)));
    sum.append(nameWrap);
    sum.append(el('span', 'ingest-drawer__doc-badge is-' + run.status, statusZh(run)));
    sum.append(el('small', 'ingest-drawer__doc-stage', stageLine(run)));
    box.append(sum);

    const body = el('div', 'ingest-drawer__doc-body');
    if (run.status === 'completed') {
      const mine = records.filter(r => belongsToDoc(r, docId)
        && r.kind !== 'document' && r.kind !== 'chunk');
      appendGroups(body, mine);
      if ((run.counts || {}).assertions_pending > 0) {
        body.append(el('p', 'ingest-drawer__pending',
          '有 ' + run.counts.assertions_pending + ' 条候选待人工确认（修改、删除、合并去「知识台账」）。'));
      }
    } else if (run.status === 'failed') {
      const reason = run.failure && (run.failure.message || run.failure.type);
      body.append(el('p', 'ingest-drawer__failed',
        '抽取失败' + (reason ? '：' + reason : '') + '。原文收据已保留，改后重新上传即可。'));
    } else {
      body.append(el('p', 'ingest-drawer__running', stageLine(run)));
    }
    box.append(body);
    return box;
  }

  // 三类知识分组（聚合胶囊、自动换行，不一条一行）
  function appendGroups(host, items) {
    const groups = [
      ['实体', items.filter(r => r.kind === 'entity'), entityChip],
      ['关系', items.filter(r => r.kind === 'relation'), relationChip],
      ['属性', items.filter(r => r.kind === 'attribute'), attributeChip],
    ];
    if (!items.length) {
      host.append(el('p', 'ingest-drawer__empty',
        '本次只保存了原文和切片，没有抽出实体／关系／属性（「仅文档检索」模式，或原文里没有可抽取的知识）。'));
      return;
    }
    for (const [label, rows, chipFn] of groups) {
      if (!rows.length) continue;
      const g = el('section', 'ingest-drawer__group');
      const lead = el('p', 'ingest-drawer__group-label');
      lead.append(el('b', null, label), el('span', null, rows.length + ' 条'));
      const wrap = el('div', 'ingest-drawer__chips');
      for (const row of rows) wrap.append(chipFn(row));
      g.append(lead, wrap);
      host.append(g);
    }
  }

  // ---- 渲染抽屉内容 --------------------------------------------------------
  async function render() {
    if (typeof current === 'undefined' || !current) return;
    const serial = ++state.serial;
    try {
      const [runs, records] = await Promise.all([readRuns(), readRecords()]);
      if (serial !== state.serial) return;
      buildIndexes(records);

      const s = computeSummary(runs, records);
      renderSummary(s);

      const recent = runs.slice(-MAX_DOCS);
      const busy = recent.some(r => r.status === 'running' || r.status === 'queued');
      liveStatus.textContent = busy
        ? '解析进行中，结果每 2 秒自动刷新…'
        : (s.failed ? '最近 ' + s.docs + ' 份已处理（其中 ' + s.failed + ' 份失败）。'
                    : '最近 ' + s.docs + ' 份文档已处理完成。');
      liveStatus.dataset.tone = busy ? 'busy' : (s.failed ? 'warn' : 'ok');

      // 首次默认展开最新一份已出结果的文档
      if (state.openDocId === null && recent.length) {
        const latest = recent[recent.length - 1];
        if (latest.status === 'completed' || latest.status === 'failed') state.openDocId = latest.document_id;
      }
      docsBox.replaceChildren();
      for (const run of recent.slice().reverse()) docsBox.append(docBlock(run, records));

      schedulePoll(busy);
    } catch (e) {
      summaryBox.replaceChildren();
      docsBox.replaceChildren();
      liveStatus.textContent = '抽取结果暂时读不到：' + safe(e && e.message, '接口不可用');
      liveStatus.dataset.tone = 'warn';
    }
  }

  // ---- 打开 / 关闭 ---------------------------------------------------------
  function open() {
    state.opened = true;
    root.hidden = false;
    render().catch(() => {});
  }
  function close() {
    state.opened = false;
    root.hidden = true;
    stopPoll();
  }
  closeBtn.addEventListener('click', close);
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } });

  const openBtn = document.getElementById('open-ingest-results');
  if (openBtn) openBtn.addEventListener('click', open);

  // ---- 轮询：有任务时每 2s 刷新，最多约 4 分钟 ------------------------------
  function schedulePoll(busy) {
    if (busy && state.polls <= 0) state.polls = 120;
    if (!busy) { stopPoll(); return; }
    if (state.timer) return;
    state.timer = setInterval(() => {
      state.polls -= 1;
      if (state.polls <= 0) { stopPoll(); return; }
      if (state.opened) render().catch(() => {});
    }, 2000);
  }
  function stopPoll() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.polls = 0;
  }

  // ---- 接线：提交后自动滑出（稍等让 run 建出来） ----------------------------
  const ingestBtn = document.getElementById('ingest');
  if (ingestBtn) {
    ingestBtn.addEventListener('click', () => {
      state.polls = 120;
      state.openDocId = null;   // 新一批：默认展开最新文档
      setTimeout(open, 800);
    });
  }
})();
