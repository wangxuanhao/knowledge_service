/* ============================================================================
 * 本体建模层（ontology-model-panel.js）—— 右栏详情 / 底栏动作条 / 版本管理抽屉
 * P1（2026-10-04）实现者 B。契约见 docs/2026-10-04-P1前端实现契约.md §2.5：
 *   window.OntologyPanel = { mount(detailHost, actionsHost, ctx), render(state), openHistory() }
 *
 * 职责（B4/B6/B7/B8）：
 *   · 右栏详情：按 S.selected 显示该术语的定义（类的属性/允许的关系/适用规则/相关对象；
 *     关系显示 domain/range；属性显示 domain/datatype）。
 *   · 底栏动作条：状态摘要 + 「校验并审核」(validate→submit→batch-approve) + 「发布为 vN+1」。
 *   · 版本管理抽屉：列出已发布版本（GET /ontologies）与发布记录，可查看某一版（只读）、
 *     也可以回到某一版（以它为基线开 revert 草案）；Turtle/SPARQL 指路到档案页。
 *   · 候选收下/忽略：S.candidates 里待收下的概念 → sendCommand(create_term) 建进草案，或本地记下不建。
 *
 * 铁律（本仓库的血泪教训）：
 *   ① 门禁只渲染服务端 stageAvailability / readiness 的结果，前端绝不自己推规则（契约 §3.2）；
 *      读不到服务端判定就禁用按钮并把原因写在页面上，不猜。
 *   ② 后端字段名全部按 services/ontology_drafts.py 的真实实现写，不臆造。
 *   ③ 与主控（window.OntologyModel）只通过 S 与事件总线 on/emit 通信，不直接改别人的 DOM。
 *
 * 依赖全局（app.js 已注入）：api(path,body,method)、endpoint(suffix)、current、esc、status。
 * 本文件不改任何别人的文件（index.html / ontology-model.js / canvas / ingest-flow 一律不碰）。
 * ========================================================================== */
(() => {
  // ---- 极小的 DOM 工具：一律用 textContent 落文本，杜绝把数据当 HTML 解析（也就没有内联事件） ----
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const put = (host, ...nodes) => { host.replaceChildren(...nodes); return host; };

  // 数据类型的中文说法：summary 里 range 存的是 xsd 本地名，直接摆给用户看等于没说。
  const DATATYPE_ZH = {
    string: '文本', integer: '整数', int: '整数', long: '整数', decimal: '小数',
    double: '小数', float: '小数', boolean: '是／否', date: '日期',
    dateTime: '日期时间', time: '时间', anyURI: '链接地址',
    positiveInteger: '正整数', nonNegativeInteger: '非负整数',
  };
  const shortId = id => { const t = String(id == null ? '' : id); return !t ? '—' : (t.length > 16 ? `${t.slice(0, 9)}…${t.slice(-4)}` : t); };
  const fmtTime = value => {
    if (!value) return '时间未记录';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  // ---- 模块内部状态 ----------------------------------------------------------
  let detailHost = null, actionsHost = null, ctx = null;
  let unsubscribers = [];                     // 事件订阅的取消函数（重新 mount 时先撤）
  const ignoredCandidates = new Set();        // 「忽略」的候选 id：只记本地，不建术语（B8）
  const versions = { key: '', list: [], loading: false, loaded: false }; // /ontologies 缓存
  let drawer = null;                          // 版本管理抽屉（惰性创建）
  const ui = { historyOpen: false, selectedVersion: '' };
  // 发布幂等键：同一轮发布（同一草案 + 同一校验指纹）在重试之间复用同一个键；
  // 换一轮/发布成功后再生成新的。键用 ctx.uid()（主控提供），读不到才退回随机。
  let publishKey = { sig: '', key: '' };
  // 发布说明（必填）：与「停用原因」同一套做法 —— 输入留在界面上，发布成功后清空。
  // 服务端（PublishRequest.note）也强制必填，界面这层只是让"没写就不让点"更早可见。
  let publishNote = '';
  // 发布前的警告确认：服务端 publish-readiness 给出的每条 warning 都要人勾过。
  // 为什么非勾不可：回退那条警告（revert_drops_later_versions）说的正是"你这一发
  // 会让当前版本少掉它之后几版的结构改动"—— 程序替用户自动勾选等于没有确认。
  // sig 跟着 草案 id + revision 走：草案一动，勾选记录就作废（旧确认不适用于新内容）。
  let ackWarnings = { sig: '', codes: new Set() };

  const model = () => (typeof window !== 'undefined' ? window.OntologyModel : null);
  const S = () => { const M = model(); return (M && M.S) || {}; };
  // 主控 helpers 还没到位时的兜底（任何一处都不得因此崩）
  const help = () => {
    const M = model();
    const h = (M && M.helpers) || {};
    return {
      label: h.label || (it => (it && (it.label_zh || it.label || it.name || it.id)) || ''),
      shortIri: h.shortIri || (iri => String(iri || '')),
      dtypeOf: h.dtypeOf || (a => String(((a && a.range) || [])[0] || '').split(/[#/]/).pop().replace(/^xsd:/, '')),
      esc: h.esc || (s => String(s == null ? '' : s)),
    };
  };
  // app.js 的 `let current` 是脚本级绑定（不是 window 属性），只能用裸标识符并防它未声明。
  const activeProject = () => { try { return current || ''; } catch (error) { return ''; } };

  function dtypeLabel(a) {
    const local = help().dtypeOf(a) || '未限定';
    const zh = DATATYPE_ZH[local];
    return zh ? `${zh}（${local}）` : local;
  }
  function status(msg, err) {
    const M = model();
    if (M && typeof M.status === 'function') M.status(msg, err);
    else if (typeof console !== 'undefined') console.log(msg);
  }
  // 幂等键：优先用主控 ctx.uid()；同一签名（草案+指纹）在重试间复用同一把键。
  function nextIdempotencyKey(sig) {
    if (publishKey.key && publishKey.sig === sig) return publishKey.key;
    const M = model();
    let key = (M && typeof M.uid === 'function') ? M.uid() : '';
    if (!key && globalThis.crypto && globalThis.crypto.randomUUID) key = globalThis.crypto.randomUUID();
    if (!key) key = `publish-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    publishKey = { sig, key };
    return key;
  }
  function labelOfIri(iri, summary) {
    const s = summary || S().summary || {};
    for (const key of ['classes', 'relations', 'attributes']) {
      const hit = (s[key] || []).find(t => t.id === iri);
      if (hit) return help().label(hit);
    }
    return help().shortIri(iri);
  }
  // 关系两端的可读文本：domain → range
  function endpointText(r, summary) {
    const side = values => (values || []).map(v => labelOfIri(v, summary)).join(' / ') || '—';
    return `${side(r.domain)} → ${side(r.range)}`;
  }

  /* ==========================================================================
   * 右栏详情（B4）
   * ======================================================================== */
  /* ==========================================================================
   * 「两端」—— 关系类型为什么画不出线，以及怎么从实际用法补上
   * --------------------------------------------------------------------------
   * 根因：知识写入抽出来的关系类型常常只写了名字，没有 rdfs:domain / rdfs:range，
   * 而画布**只在两端都在图里时才画得出关系线** → 9 个关系类型一条线都看不到，
   * 用户的第一反应就是"关系丢了"（左栏写 9、统计栏写 0，同一屏两个数字打架）。
   *
   * 做什么：① 显示本体已声明的两端；② 读服务端从**已入库实例**反推的候选（带次数与样例）；
   *        ③ 一键把候选写进草案（命令通道 add_domain / add_range，每条各留一条可审的痕）。
   * 不做什么：不在前端自己统计（统计口径只在 services/ontology.relation_usage 一处）；
   *         不直接改生效本体（仍要审核 → 发布）；读不到就明说读不到，绝不编数字。
   * ======================================================================== */
  function endpointsSection(rel, summary) {
    const sec = el('div', 'om-detail-sec om-endpoints');
    sec.append(el('h5', 'om-detail-title', '两端（domain / range）能不能补上'));
    const host = el('div', 'om-endpoints-body');
    host.append(el('p', 'om-detail-note', '正在读取这条关系的实际用法…'));
    sec.append(host);

    const repaint = () => {
      // 画布画的是草案时，两端要按**草案**问 —— 否则回填刚写进草案，这里还按已发布版本
      // 回一句"未声明"，看起来就是"改了没反应"（正是本轮要修的病）。
      const M = model();
      const draft = (M && M.S && M.S.draft) || null;
      const suffix = (draft && draft.id) ? `&draft_id=${encodeURIComponent(draft.id)}` : '';
      api(endpoint(`/ontology/relation-usage?uri=${encodeURIComponent(rel.id)}${suffix}`), undefined, 'GET')
        .then(data => { host.replaceChildren(...endpointBody(rel, summary, data, repaint)); })
        .catch(error => {
          host.replaceChildren(el('p', 'om-detail-note',
            `读不到实际用法（${(error && error.message) || error}）——按未判定处理，这里不提供回填。`));
        });
    };
    repaint();
    return sec;
  }

  function endpointBody(rel, summary, usage, repaint) {
    const nodes = [];
    const declared = (usage && usage.declared) || { domain: [], range: [] };
    const inferred = (usage && usage.inferred) || { domain: [], range: [] };
    const used = (usage && usage.used) || 0;
    const missing = (usage && usage.missing) || { domain: false, range: false };
    const names = list => (list || []).map(x => labelOfIri(x, summary)).join('、') || '未声明';

    // 说清这份读数来自哪里：画布可能正在看草案（改了立刻看得见），也可能在看已发布版本。
    const where = (usage && usage.source === 'draft') ? '草案里已声明' : '已发布本体里已声明';
    nodes.push(el('p', 'om-detail-line', `${where}：${names(declared.domain)} → ${names(declared.range)}`));

    if (!used) {
      nodes.push(el('p', 'om-detail-note',
        '还没有任何知识用到这条关系 —— 没有可回填的依据。等知识写入抽出用到它的知识，再回来看这里。'));
      return nodes;
    }
    nodes.push(el('p', 'om-detail-line', `实际用法：${used} 条知识用到它。反推出来的两端（按出现次数排序）：`));

    const ul = el('ul', 'om-detail-rows');
    (inferred.domain || []).forEach(item => ul.append(
      termRow('cls', `${labelOfIri(item.iri, summary)}（作主体 ${item.count} 次）`, '', item.iri)));
    (inferred.range || []).forEach(item => ul.append(
      termRow('cls', `${labelOfIri(item.iri, summary)}（作客体 ${item.count} 次）`, '', item.iri)));
    nodes.push(ul);

    const samples = (usage && usage.samples) || [];
    if (samples.length) {
      nodes.push(el('p', 'om-detail-line', '核对样例（推断对不对，看这几条就够）：'));
      const sm = el('ul', 'om-detail-rows');
      samples.slice(0, 3).forEach(s => sm.append(el('li', 'om-detail-row',
        `${s.subject || '（无主体）'} · ${s.subject_type || '类型未知'} → ${s.object || '（无客体）'} · ${s.object_type || '类型未知'}`)));
      nodes.push(sm);
    }

    const canDom = !!(inferred.domain || []).length;
    const canRng = !!(inferred.range || []).length;
    if (!missing.domain && !missing.range) {
      nodes.push(el('p', 'om-detail-note', '两端都已声明，无需回填。画布上这条关系应该已经画出线了。'));
      return nodes;
    }
    const btn = el('button', 'om-btn primary', '按实际用法写入草案');
    btn.type = 'button';
    btn.disabled = !(canDom && canRng);
    btn.title = (canDom && canRng)
      ? '做什么：把上面反推出来的两端写进草案（主体类/客体类各一条命令，可逐条审）。不做什么：不直接改生效本体——仍要审核并发布。'
      : '实际用法里两端还不齐（只有一端出现过），依据不足，先不写入。';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const count = await applyEndpoints(rel, inferred);
        status(`已把 ${rel.label || rel.id} 的两端写进草案（${count} 条改动），底栏「校验并审核」后发布生效。`);
        repaint();
      } catch (error) {
        status(`回填失败：${(error && error.message) || error}`, true);
        btn.disabled = false;
      }
    });
    const cell = el('div', 'om-detail-actions');
    cell.append(btn);
    nodes.push(cell);
    if (!(canDom && canRng)) {
      nodes.push(el('p', 'om-detail-note',
        '实际用法里两端还不齐：只有一端出现过，依据不足，所以不给回填按钮（宁可不补，也不猜）。'));
    }
    nodes.push(el('p', 'om-detail-note',
      '做什么：从已入库的实例关系反推这条关系实际连接了哪些类，补齐后画布就能画出关系线。不做什么：不在前端自己统计（口径在服务端一处），不直接改生效本体。'));
    return nodes;
  }

  async function applyEndpoints(rel, inferred) {
    const M = model();
    if (!M || typeof M.sendCommand !== 'function') throw new Error('主控未就绪');
    // 逐条发（每条在草案里留一条可审的改动痕）：治理要"每次改动可核对"，
    // 所以刻意不合并成一条批量命令 —— 审核时能一条条看。
    const targets = [];
    (inferred.domain || []).forEach(item => targets.push({ action: 'add_domain', side: '主体类', value: item.iri }));
    (inferred.range || []).forEach(item => targets.push({ action: 'add_range', side: '客体类', value: item.iri }));
    for (const t of targets) {
      await M.sendCommand({
        action: t.action, target_iri: rel.id, value: t.value,
        reason: `按实际用法回填关系两端（${t.side}）`,
      });
    }
    return targets.length;
  }

  /* ==========================================================================
   * 「新建术语」表单（补回 P1 重写时漏掉的入口）
   * --------------------------------------------------------------------------
   * 用户原话："现在父类也加不了"。真机读数：单画布三个文件里 create_term 只出现在
   * 「收下候选」那条路径上 —— 也就是说只能创建**模型已经猜出来**的类；画布工具条只有
   * 「改父类（连线）」，而它要求先选中一个**已存在**的类。空本体上没有任何地方能新建，
   * 想给它加父类自然无从谈起。
   *
   * 做什么：新建实体类 / 关系类型 / 实体属性，可选带上父类或两端，写进当前草案。
   * 不做什么：不直接改生效本体（仍要审核 → 发布）；不猜 IRI（命名规则在服务端，
   *         走 POST /ontology/terms，与审核台/设计台同一条路）。
   * ======================================================================== */
  const NEW_KINDS = [
    { value: 'class', text: '实体类', hint: '一个概念，可以被别的类继承。建完可以用「改父类（连线）」挂到父类下面。' },
    { value: 'relation', text: '关系类型', hint: '两个类之间允许的连接。主体类/客体类可以先留空，之后在关系详情的「两端」里按实际用法补。' },
    { value: 'attribute', text: '实体属性', hint: '挂在某个类上的一个字段。数据类型暂时不限定也可以，之后按需要补。' },
  ];
  let createInput = null;   // 「中文名」输入框：画布「＋ 新建类」按钮要聚焦它

  function newSelect(options, placeholder) {
    const sel = el('select', 'om-new-input');
    const blank = el('option', null, placeholder);
    blank.value = '';
    sel.append(blank);
    (options || []).forEach(item => {
      const opt = el('option', null, item.text);
      opt.value = item.id;
      sel.append(opt);
    });
    return sel;
  }

  function newField(labelText, control) {
    const row = el('label', 'om-new-field');
    row.append(el('span', 'om-new-field-label', labelText), control);
    return row;
  }

  function newTermSection(state) {
    const summary = (state && state.summary) || {};
    const classes = (summary.classes || []).filter(c => c.active !== false);
    const classOptions = classes.map(c => ({ id: c.id, text: labelOfIri(c.id, summary) }));

    const box = el('div', 'om-detail-card om-new-term');
    box.append(el('h5', 'om-detail-title', '新建术语'));
    box.append(el('p', 'om-detail-line',
      '从零建一个类 / 关系 / 属性，写进当前草案。底栏「校验并审核」后发布才生效。'));

    const kindSel = el('select', 'om-new-input');
    NEW_KINDS.forEach(k => { const o = el('option', null, k.text); o.value = k.value; kindSel.append(o); });
    const kindHint = el('p', 'om-detail-note', NEW_KINDS[0].hint);

    const zh = el('input', 'om-new-input');
    zh.type = 'text'; zh.placeholder = '中文名（必填），例如：优惠券';
    createInput = zh;
    const en = el('input', 'om-new-input');
    en.type = 'text'; en.placeholder = '英文标识（选填，留空用中文名）';
    const slot = el('div', 'om-new-slot');

    let parentSel = null, domainSel = null, rangeSel = null;
    function syncOwnership(kind) {
      slot.replaceChildren();
      parentSel = domainSel = rangeSel = null;
      if (kind === 'class') {
        parentSel = newSelect(classOptions, '父类（选填：不选就是顶层类）');
        slot.append(newField('父类', parentSel));
      } else if (kind === 'relation') {
        domainSel = newSelect(classOptions, '主体类 domain（选填）');
        rangeSel = newSelect(classOptions, '客体类 range（选填）');
        slot.append(newField('主体类 domain', domainSel), newField('客体类 range', rangeSel));
      } else {
        domainSel = newSelect(classOptions, '所属类（选填）');
        slot.append(newField('所属类', domainSel));
      }
    }
    syncOwnership('class');
    kindSel.addEventListener('change', () => {
      const k = NEW_KINDS.find(x => x.value === kindSel.value) || NEW_KINDS[0];
      kindHint.textContent = k.hint;
      syncOwnership(kindSel.value);
    });

    const submit = el('button', 'om-btn primary', '新建并写入草案');
    submit.type = 'button';
    submit.disabled = true;
    const syncButton = () => { submit.disabled = !zh.value.trim(); };
    zh.addEventListener('input', syncButton);

    submit.addEventListener('click', async () => {
      const M = model();
      const text = zh.value.trim();
      if (!text) { status('请先填写中文名。', true); return; }
      if (!M || typeof M.ensureDraft !== 'function') { status('主控未就绪，暂时无法新建。', true); return; }
      submit.disabled = true;
      try {
        status('正在写入草案…');
        const draft = await M.ensureDraft();     // 没有可改的草案会自动基于当前最新本体新建
        const kind = kindSel.value;
        await api(endpoint('/ontology/terms'), {
          kind, uri: '', label: en.value.trim() || text, label_zh: text,
          // 归属：按 kind 只带相关字段，避免服务端同时收到互相矛盾的约束
          parent: (kind === 'class' && parentSel && parentSel.value) ? parentSel.value : '',
          domain: (kind === 'attribute' && domainSel && domainSel.value) ? domainSel.value : '',
          domains: (kind === 'relation' && domainSel && domainSel.value) ? [domainSel.value] : [],
          ranges: (kind === 'relation' && rangeSel && rangeSel.value) ? [rangeSel.value] : [],
          // 服务端是拿它与「项目当前最新本体 id」比对的（不是草案的 base），传错会 409「版本冲突」
          expected_ontology_id: S().ontologyId || draft.base_ontology_id || '',
          draft_id: draft.id, expected_revision: draft.revision,
        }, 'POST');
        await M.refresh();
        zh.value = ''; en.value = '';
        syncButton();
        const kindText = (NEW_KINDS.find(x => x.value === kind) || NEW_KINDS[0]).text;
        status(`已新建${kindText}「${text}」：写进了草案，底栏「校验并审核」后发布生效。`);
      } catch (error) {
        status(`新建失败：${(error && error.message) || error}`, true);
        syncButton();
      }
    });

    const actions = el('div', 'om-detail-actions');
    actions.append(submit);
    box.append(
      newField('类型', kindSel), kindHint,
      newField('中文名', zh), newField('英文标识', en),
      slot, actions);
    box.append(el('p', 'om-detail-note',
      '做什么：新建一个本体术语（类 / 关系 / 属性），归属可以留空。不做什么：不直接改生效本体——先写进草案，审核发布后才生效。'));
    return box;
  }

  // 画布「＋ 新建类」按钮用它把焦点送进名称输入框（上一步已清空选中，表单必然已渲染）。
  function focusCreate() {
    if (createInput && typeof createInput.focus === 'function') {
      try { createInput.focus(); } catch (error) { /* 忽略：拿不到焦点不影响新建 */ }
    }
  }

  function renderDetail(state) {
    if (!detailHost) return;
    const st = state || S();
    const selected = st.selected;
    if (!selected) {
      // 没选中对象时，右栏就是「新建术语」的入口（不再只是一句提示）。
      // 用户反馈"现在父类也加不了"的根因：单画布整页只有「改父类（连线）」，
      // 而它必须先选中一个**已存在**的类 —— 空本体上没有任何地方能新建一个类，
      // 想给它加父类自然无从谈起。表单放这里 + 画布「＋ 新建类」按钮清空选中，
      // 就让"从零建第一层"有了唯一且显眼的入口。
      put(detailHost, newTermSection(st));
      return;
    }
    const summary = st.summary || { classes: [], relations: [], attributes: [] };
    const classes = summary.classes || [], relations = summary.relations || [], attributes = summary.attributes || [];
    const cls = classes.find(c => c.id === selected);
    const rel = relations.find(r => r.id === selected);
    const attr = attributes.find(a => a.id === selected);
    const cand = (st.candidates || []).find(c => (c.iri || c.id) === selected);

    if (cls) return renderClass(cls, summary);
    if (rel) return renderRelation(rel, summary);
    if (attr) return renderAttr(attr, summary);
    if (cand) return renderCandidate(cand);
    put(detailHost, el('div', 'om-detail-empty',
      `选中了 ${help().shortIri(selected)}，但当前本体里没有它的定义（可能属于其它版本，或已被停用/删除）。`));
  }

  function detailHead(kindText, chipCls, term, iri) {
    const head = el('div', 'om-detail-head');
    head.append(el('span', 'om-detail-kind om-kind-' + chipCls, kindText));
    const active = term.active !== false;
    head.append(el('span', 'om-detail-badge ' + (active ? 'is-active' : 'is-deprecated'), active ? '生效' : '已停用'));
    head.append(el('b', 'om-detail-name', help().label(term)));
    head.append(el('code', 'om-detail-iri', help().shortIri(iri || term.id)));
    return head;
  }
  // 一行术语：chip + 可点击的名字（点它=选中，纯页内导航，不写库）+ 尾部说明
  function termRow(chipCls, text, tail, iri) {
    const li = el('li', 'om-detail-row');
    li.append(el('span', 'om-chip ' + chipCls));
    const M = model();
    if (iri && M && typeof M.select === 'function') {
      const link = el('button', 'om-detail-link', text);
      link.type = 'button';
      link.title = '选中这个对象，在右栏看它的定义（不修改任何数据）';
      link.addEventListener('click', () => M.select(iri));
      li.append(link);
    } else {
      li.append(el('span', 'om-detail-row-label', text));
    }
    if (tail !== undefined && tail !== null) li.append(el('span', 'om-detail-row-tail', tail));
    return li;
  }
  function section(title, count, rows, note) {
    const sec = el('div', 'om-detail-sec');
    sec.append(el('h5', 'om-detail-title', `${title}（${count}）`));
    if (rows && rows.length) {
      const ul = el('ul', 'om-detail-rows');
      rows.forEach(row => ul.append(row));
      sec.append(ul);
    } else {
      sec.append(el('p', 'om-detail-inline-empty', '（无）'));
    }
    if (note) sec.append(el('p', 'om-detail-note', note));
    return sec;
  }
  // 「适用规则」：本层没有按术语的规则数据，所以如实说明来源，不自己推一套判定。
  function rulesSection(term) {
    const sec = el('div', 'om-detail-sec');
    sec.append(el('h5', 'om-detail-title', '适用规则'));
    const active = term.active !== false;
    sec.append(el('p', 'om-detail-line',
      active ? '当前状态：生效（可被检索/抽取使用）。'
             : '当前状态：已停用（owl:deprecated）——历史知识仍保留、可查，只是不再作为新写入的类型。'));
    sec.append(el('p', 'om-detail-note',
      '做什么：说明这个术语当前是否生效。不做什么：本页不自行推断校验规则——能不能发布只认服务端「发布清单」，读不到就按未判定处理。'));
    return sec;
  }

  /* ==========================================================================
   * 「停用 / 恢复」动作区（D3）—— 不可逆动作先看影响面，再入草案
   * --------------------------------------------------------------------------
   * 为什么补这一块：旧工作台有「停用影响预览」（读 term-impact 出「N 条正式知识 /
   * N 条约束 / N 条待审核 / N 条关联关系」+ 必填原因），搬到单画布时漏了；而命令词表里
   * `retire_term` / `restore_term` 一直在（services/ontology_drafts.py），后端一直支持。
   *
   * 做什么：① 读服务端影响面并如实显示；② 原因必填（留痕要求）；③ 发草案命令。
   * 不做什么：不直接改生效本体（仍走 草案 → 审核 → 发布）；读不到影响面就不许停
   * （按未判定处理），不替服务端判断"能不能停"，也不自己估算条数。
   * ======================================================================== */
  function retireSection(term) {
    const active = term.active !== false;
    const iri = term.id;
    const sec = el('div', 'om-detail-sec om-retire');
    sec.append(el('h5', 'om-detail-title', active ? '停用这个术语' : '恢复这个术语'));
    sec.append(el('p', 'om-detail-line', active
      ? '停用后：不再作为新写入的类型；已写的历史知识与版本轨迹保留、可查。这是不可逆动作，所以先看影响面。'
      : '恢复后：它重新生效，可被检索与抽取使用。'));

    const impactLine = el('p', 'om-detail-note om-retire-impact', '正在读取影响面…');
    sec.append(impactLine);

    const reason = el('textarea', 'om-retire-reason');
    reason.rows = 2;
    reason.placeholder = `必填：说明${active ? '停用' : '恢复'}原因（会写进草案留痕）`;

    const buttons = el('div', 'om-retire-actions');
    const confirm = el('button', active ? 'danger' : 'secondary', active ? '确认加入停用变更' : '确认恢复');
    confirm.type = 'button';
    const cancel = el('button', 'secondary', '取消');
    cancel.type = 'button';
    buttons.append(confirm, cancel);
    sec.append(reason, buttons);

    // 只有当"影响面读到了"且"原因非空"才允许提交：停了就回不来，不许盲停。
    let impactReady = false;
    const syncButton = () => { confirm.disabled = !(impactReady && reason.value.trim()); };
    confirm.disabled = true;

    api(endpoint(`/ontology/term-impact?uri=${encodeURIComponent(iri)}`), undefined, 'GET')
      .then(impact => {
        const n = k => Number(impact && impact[k]) || 0;
        impactLine.textContent =
          `${n('record_count')} 条正式知识 · ${n('constraint_count')} 条约束 · `
          + `${n('pending_review_count')} 条待审核 · ${n('linked_relation_count')} 条关联关系`
          + (n('constraint_count') ? '。有约束还在引用它，建议先处理这些约束。' : '。');
        impactReady = true;
        syncButton();
      })
      .catch(error => {
        impactLine.textContent = '读不到影响面（'
          + ((error && error.message) || '服务端未响应') + '）——按未判定处理，暂不允许停用。';
      });

    reason.addEventListener('input', syncButton);
    // 取消 = 重新选中自身（主控 select 必发事件 → 重渲染本栏），不改任何数据
    cancel.addEventListener('click', () => {
      const M = model();
      if (M && typeof M.select === 'function') M.select(iri); else renderDetail();
    });
    confirm.addEventListener('click', async () => {
      const M = model();
      if (!M || typeof M.sendCommand !== 'function') { status('主控未就绪，无法提交。', true); return; }
      const text = reason.value.trim();
      if (!text) { status('必须填写原因。', true); return; }
      confirm.disabled = true;
      try {
        await M.sendCommand({ action: active ? 'retire_term' : 'restore_term', target_iri: iri, reason: text });
        status(`${active ? '停用' : '恢复'}变更已加入草案，仍需逐条审核后才生效。`);
      } catch (error) {
        status((error && error.message) || '提交失败', true);
        syncButton();
      }
    });
    return sec;
  }

  /* ==========================================================================
   * 内联约束编辑 —— 让本体建模层名副其实
   * --------------------------------------------------------------------------
   * 后端命令通道（ontology_drafts_commands）一直支持 domain/range/datatype/parent 的增删，
   * 但右栏只把它们渲染成只读清单，于是用户"看得到、改不了"。这里接成统一的内联编辑：
   *   · 关系类型 → 增删 domain（主体类）/ range（客体类）
   *   · 实体属性 → 增删所属类 + set_datatype 改数据类型
   *   · 实体类   → 增删父类（继承）
   * 所有改动走 sendCommand：已发布本体下会自动基于当前版本开一份草案，不直接改生效版本。
   * ======================================================================== */
  // 常用数据类型（value 是完整 xsd IRI——set_datatype 按完整 IRI 落库）
  const XSD_BASE = 'http://www.w3.org/2001/XMLSchema#';
  const DATATYPE_OPTIONS = [
    ['string', '文本 string'], ['integer', '整数 integer'], ['decimal', '小数 decimal'],
    ['boolean', '是／否 boolean'], ['date', '日期 date'], ['dateTime', '日期时间 dateTime'],
    ['anyURI', '链接 anyURI'],
  ];

  function constraintEditSection(term, kind, summary) {
    const classes = (summary.classes || []).filter(c => c.active !== false);
    let options = classes.map(c => ({ id: c.id, text: labelOfIri(c.id, summary) }));
    // 父类下拉必须排除当前类自身：一个类不能是自己的父类（否则形成自环，真机探针抓到
    // target 与 value 是同一个 IRI）。domain/range 不做此排除（关系本就可指向自身所在类）。
    if (kind === 'class') options = options.filter(o => o.id !== term.id);

    const sec = el('details', 'om-constraint-edit');
    // 折叠区标题按对象类型动态显示——类没有 domain/range，别把四类约束都列上去造成误解。
    const CE_TITLE = {
      class: '编辑继承（父类）',
      relation: '编辑 domain / range（两端类）',
      attribute: '编辑所属类 / 数据类型',
    };
    const summary2 = el('summary', null, CE_TITLE[kind] || '编辑约束');
    const body = el('div', 'om-constraint-body');
    sec.append(summary2, body);

    // 发一条约束命令；sendCommand 会在已发布本体下自动开草案
    async function runCmd(cmd) {
      const M = model();
      if (!M || typeof M.sendCommand !== 'function') { status('主控未就绪，暂时无法编辑。', true); return; }
      try {
        status('正在把改动写进草案…');
        await M.sendCommand(cmd);
        status('已写进草案：底栏「校验并审核」、发布后才生效。');
      } catch (error) { status((error && error.message) || '编辑失败', true); }
    }

    // 「当前值（可移除）＋ 下拉新增」的一行约束编辑
    function linkRow(labelText, currentIris, addAction, removeAction) {
      const row = el('div', 'om-ce-row');
      row.append(el('span', 'om-ce-label', labelText));
      const curHost = el('div', 'om-ce-current');
      const cur = currentIris || [];
      if (!cur.length) curHost.append(el('span', 'om-ce-empty', '未限定'));
      cur.forEach(iri => {
        const chip = el('span', 'om-ce-chip');
        chip.append(el('span', null, labelOfIri(iri, summary)));
        const x = el('button', 'om-ce-x');
        x.type = 'button'; x.textContent = '×'; x.title = '移除这个约束（写进草案）';
        x.addEventListener('click', () => { void runCmd({ action: removeAction, target_iri: term.id, value: iri }); });
        chip.append(x); curHost.append(chip);
      });
      // 下拉新增：已声明的类不再列出（避免重复添加）
      const addHost = el('div', 'om-ce-add');
      const sel = el('select', 'om-ce-select');
      const blank = el('option', null, '选一个类…'); blank.value = ''; sel.append(blank);
      options.filter(o => !cur.includes(o.id)).forEach(o => {
        const opt = el('option', null, o.text); opt.value = o.id; sel.append(opt);
      });
      const addBtn = el('button', 'om-btn secondary', '添加');
      addBtn.type = 'button'; addBtn.disabled = true;
      sel.addEventListener('change', () => { addBtn.disabled = !sel.value; });
      addBtn.addEventListener('click', () => {
        if (sel.value) void runCmd({ action: addAction, target_iri: term.id, value: sel.value });
      });
      addHost.append(sel, addBtn);
      row.append(curHost, addHost);
      return row;
    }

    // 数据类型下拉（set_datatype）
    function datatypeRow() {
      const row = el('div', 'om-ce-row');
      row.append(el('span', 'om-ce-label', '数据类型'));
      const addHost = el('div', 'om-ce-add');
      const sel = el('select', 'om-ce-select');
      const blank = el('option', null, '不限定 / 选择…'); blank.value = ''; sel.append(blank);
      const currentLocal = help().dtypeOf(term);
      DATATYPE_OPTIONS.forEach(([local, text]) => {
        const opt = el('option', null, text); opt.value = XSD_BASE + local;
        if (local === currentLocal) opt.selected = true;
        sel.append(opt);
      });
      const applyBtn = el('button', 'om-btn secondary', '应用类型');
      applyBtn.type = 'button';
      applyBtn.disabled = !sel.value;
      sel.addEventListener('change', () => { applyBtn.disabled = !sel.value; });
      applyBtn.addEventListener('click', () => {
        if (sel.value) void runCmd({ action: 'set_datatype', target_iri: term.id, datatype: sel.value });
      });
      addHost.append(sel, applyBtn);
      row.append(addHost);
      return row;
    }

    if (kind === 'relation') {
      body.append(
        linkRow('domain 主体类', term.domain, 'add_domain', 'remove_domain'),
        linkRow('range 客体类', term.range, 'add_range', 'remove_range'));
    } else if (kind === 'attribute') {
      body.append(
        linkRow('所属类 domain', term.domain, 'add_domain', 'remove_domain'),
        datatypeRow());
    } else {
      body.append(linkRow('父类（继承）', term.parents, 'add_parent', 'remove_parent'));
    }
    return sec;
  }

  function renderClass(cls, summary) {
    const classes = summary.classes || [], relations = summary.relations || [], attributes = summary.attributes || [];
    const box = el('div', 'om-detail-card');
    box.append(detailHead('实体类', 'cls', cls, cls.id));
    if (cls.description) box.append(el('p', 'om-detail-desc', cls.description));

    const own = attributes.filter(a => (a.domain || []).includes(cls.id));
    box.append(section('属性（带数据类型）', own.length,
      own.map(a => termRow('attr', help().label(a), dtypeLabel(a), a.id)),
      '做什么：列出以这个类为 domain 的实体属性及数据类型。不做什么：这里只读，改属性请回画布（走草案命令）。'));

    const rels = relations.filter(r => (r.domain || []).includes(cls.id));
    box.append(section('允许的关系类型（domain → range）', rels.length,
      rels.map(r => termRow('rel', help().label(r), endpointText(r, summary), r.id)),
      '做什么：列出这个类可以作为 domain 的关系类型及其 range。不做什么：不判断实例是否合法，校验由服务端做。'));

    box.append(rulesSection(cls));
    // 内联编辑父类（继承）；已发布本体下自动开草案
    box.append(constraintEditSection(cls, 'class', summary));

    const parents = cls.parents || [];
    const children = classes.filter(c => (c.parents || []).includes(cls.id)).map(c => c.id);
    const related = el('div', 'om-detail-sec');
    related.append(el('h5', 'om-detail-title', `相关对象（${parents.length + children.length}）`));
    if (parents.length || children.length) {
      const ul = el('ul', 'om-detail-rows');
      parents.forEach(p => ul.append(termRow('cls', '父类：' + labelOfIri(p, summary), null, p)));
      children.forEach(c => ul.append(termRow('cls', '子类：' + labelOfIri(c, summary), null, c)));
      related.append(ul);
    } else {
      related.append(el('p', 'om-detail-inline-empty', '（没有父类和子类）'));
    }
    related.append(el('p', 'om-detail-note',
      '做什么：给出与本类直接相连的上下位对象（来自当前本体结构）。不做什么：这里不是实例数据，不列出属于这个类的具体知识条目。'));
    box.append(related);

    box.append(retireSection(cls));
    put(detailHost, box);
  }

  function renderRelation(rel, summary) {
    const box = el('div', 'om-detail-card');
    box.append(detailHead('关系类型', 'rel', rel, rel.id));
    if (rel.description) box.append(el('p', 'om-detail-desc', rel.description));

    const dom = rel.domain || [];
    box.append(section('domain（主体类）', dom.length,
      dom.map(d => termRow('cls', labelOfIri(d, summary), null, d)),
      '做什么：这个关系允许的主语类型。不做什么：不展开继承——子类能否使用由服务端推理决定。'));

    const rng = rel.range || [];
    box.append(section('range（客体类）', rng.length,
      rng.map(g => termRow('cls', labelOfIri(g, summary), null, g)),
      '做什么：这个关系允许的宾语类型。不做什么：不校验实例两端是否匹配。'));

    // 两端为空 = 画布上画不出这条关系线。这里给出"为什么没有"和"怎么补"，
    // 而不是让用户对着一条空关系猜（这正是"关系没办法显示"的直接解法）。
    box.append(endpointsSection(rel, summary));
    // 内联增删 domain / range（约束编辑）
    box.append(constraintEditSection(rel, 'relation', summary));

    box.append(rulesSection(rel));
    box.append(retireSection(rel));
    put(detailHost, box);
  }

  function renderAttr(attr, summary) {
    const box = el('div', 'om-detail-card');
    box.append(detailHead('实体属性', 'attr', attr, attr.id));
    if (attr.description) box.append(el('p', 'om-detail-desc', attr.description));

    const dom = attr.domain || [];
    box.append(section('domain（所属类）', dom.length,
      dom.map(d => termRow('cls', labelOfIri(d, summary), null, d)),
      '做什么：这个属性挂在哪类实体上。'));

    box.append(section('数据类型（datatype）', 1,
      [termRow('attr', dtypeLabel(attr), help().dtypeOf(attr) || '未限定')],
      '做什么：这个属性填的数据类型。不做什么：不做取值校验，也不改类型（改类型走下面的约束编辑 set_datatype）。'));

    // 内联改所属类 + 数据类型（约束编辑）
    box.append(constraintEditSection(attr, 'attribute', summary));

    box.append(rulesSection(attr));
    box.append(retireSection(attr));
    put(detailHost, box);
  }

  /* ---- 候选收下 / 忽略（B8）：待收下概念在右栏给两个按钮 --------------------- */
  function renderCandidate(cand) {
    const id = cand.iri || cand.id;
    const box = el('div', 'om-detail-card om-detail-card--pending');
    const head = el('div', 'om-detail-head');
    head.append(el('span', 'om-detail-kind om-kind-pending', '待收下候选'));
    head.append(el('b', 'om-detail-name', cand.label || help().label(cand) || id));
    head.append(el('code', 'om-detail-iri', help().shortIri(id)));
    box.append(head);
    box.append(el('p', 'om-detail-desc', '这是知识写入抽出的新概念，还没有进本体的草案。'));

    const acts = el('div', 'om-detail-actions');
    const accept = el('button', 'om-btn primary', '收下：建进草案');
    accept.type = 'button';
    accept.title = '把它作为新术语写进当前草案（走命令通道 create_term）；收下不会自动发布，仍需在底栏校验并审核';
    accept.addEventListener('click', () => acceptCandidate(id, cand));
    const ignore = el('button', 'om-btn ghost', '忽略：只记下不建');
    ignore.type = 'button';
    ignore.title = '只在本地记下"这条不收"，不调用任何写接口；它不再显示，也不会进本体';
    ignore.addEventListener('click', () => ignoreCandidate(id));
    acts.append(accept, ignore);
    box.append(acts);
    box.append(el('p', 'om-detail-note',
      '做什么：把机器抽出的概念收进草案，或忽略掉。不做什么：忽略不写库；收下也不会自动发布。'));
    put(detailHost, box);
  }

  async function acceptCandidate(id, cand) {
    const M = model();
    if (!M || typeof M.sendCommand !== 'function') { status('收下功能不可用：主控未就绪。', true); return; }
    try {
      status('正在把候选收进草案…');
      // create_term 的真实形状见 services/ontology_drafts.py:_operation_args（kind ∈ class/relation/attribute）
      await M.sendCommand({ action: 'create_term', target_iri: id, kind: (cand && cand.kind) || 'class' });
      status('已收下候选：术语已建进当前草案，可在底栏「校验并审核」。');
    } catch (error) {
      status(error && error.message ? error.message : '收下候选失败', true);
    }
  }

  function ignoreCandidate(id) {
    ignoredCandidates.add(id);
    const M = model();
    const st = S();
    // 只从本地候选里摘掉；不发任何写请求（「忽略 = 记下不建」）
    if (Array.isArray(st.candidates)) st.candidates = st.candidates.filter(c => (c.iri || c.id) !== id);
    if (st.selected === id && M && typeof M.select === 'function') { M.select(null); return; }
    if (M && typeof M.compileGraph === 'function') M.compileGraph();
    if (M && typeof M.render === 'function') M.render();
  }

  /* ==========================================================================
   * 底栏动作条（B7）—— 门禁只渲染服务端判定
   * ======================================================================== */
  // 「校验并审核」是否能点：只认服务端 stage-availability 的 review 阶段
  function gateReview(state) {
    const st = state || S();
    if (!st.draft || !st.draft.id) {
      return { enabled: false, reason: '还没有草案：先在画布上新增或调整术语，草案会自动建立，之后这里才能校验并审核。' };
    }
    const sa = st.stageAvailability;
    if (!sa) return { enabled: false, reason: '读不到服务端阶段门禁（stage-availability），按未判定处理，暂不启用「校验并审核」。' };
    const entry = (sa.stages || {}).review || {};
    if (entry.allowed !== true) return { enabled: false, reason: entry.reason || '服务端当前不放行审核阶段。' };
    return { enabled: true, reason: '' };
  }
  // 「发布」是否能点：只认服务端 publish-readiness（ready 且已收下至少一条）
  function gatePublish(state) {
    const st = state || S();
    if (!st.draft || !st.draft.id) {
      return { enabled: false, reason: '还没有草案：先在画布上改，草案建立且服务端判定就绪时这里才启用发布。' };
    }
    const readiness = st.readiness;
    if (!readiness) return { enabled: false, reason: '读不到服务端发布清单（publish-readiness），按未判定处理，暂不启用发布。' };
    if (readiness.ready !== true) {
      const first = (readiness.items || []).find(item => item.severity === 'error');
      return { enabled: false, reason: first
        ? `还不能发布：${first.message || first.code}${first.remediation ? ' —— ' + first.remediation : ''}`
        : '服务端判定这份草案还不能发布。' };
    }
    const approved = ((readiness.approval_counts || {}).approved) || 0;
    // 回退草案允许零变更发布（内容由它回到的那一版决定），所以 approved=0 在这里
    // 不能一律拦住 —— 服务端用 readiness.revert 明确表态"这份可以零变更发布"。
    if (!approved && !readiness.revert) {
      return { enabled: false, reason: '还不能发布：没有任何变更被收下（先点「校验并审核」）。' };
    }
    return { enabled: true, reason: '' };
  }

  // 没本体时的底栏：候选/类型统计 + 「生成本体（秒级）」主按钮 + 一句说明。
  // 不渲染「校验并审核 / 发布 / 发布说明输入框」——那三个在没本体时全是灰的、没意义，
  // 还因为发布说明输入框最小 180px 把按钮挤成两行（用户反馈「校验和发布是两行」）。
  function renderEmptyActions(st) {
    const disc = st.discovery || null;
    const pending = disc ? (disc.unpublished_candidate_count || 0) : 0;
    const generating = !!st.generating;
    const inferring = !!st.inferringHierarchy;
    // 这一步整理出的是【实体类型 / 关系类型】，不是一条条实体，所以 chip 直接讲类型。
    const entityTypeN = disc ? (disc.entity_types || []).length : null;
    const relationTypeN = disc ? (disc.relation_types || []).length : null;
    const bar = el('div', 'om-actionbar-inner');
    const counts = el('div', 'om-ab-counts');
    const chip = (label, value, cls, tip) => {
      const c = el('span', 'om-ab-chip' + (cls ? ' ' + cls : ''));
      c.append(el('b', null, value === null ? '—' : String(value)));
      c.append(el('i', null, label));
      if (tip) c.title = tip;
      return c;
    };
    counts.append(
      chip('实体类型', entityTypeN, 'cls', '将生成的实体类型数（同名概念合并）'),
      chip('关系类型', relationTypeN, 'rel', '将生成的关系类型数'),
      chip('候选概念', disc ? (disc.candidate_count || 0) : null, 'pending', '知识写入抽出的候选总数'),
    );
    bar.append(counts);
    const actions = el('div', 'om-ab-actions');
    if (generating) {
      actions.append(el('span', 'om-ab-busy', inferring
        ? '正在生成，并让模型推断父子层级（类型很快好，层级要几分钟，别关页面）…'
        : '正在生成本体（约 1 秒，不调用模型）…'));
    } else if (pending > 0) {
      const btn = el('button', 'om-btn primary', '生成本体（约 1 秒）');
      btn.type = 'button';
      btn.title = '把候选整理成一份待审核的本体（实体类型＋关系类型），默认平级、不调模型';
      btn.addEventListener('click', () => {
        const M = model();
        if (M && M.generateDraftFromDiscovery) M.generateDraftFromDiscovery(false);
      });
      actions.append(btn);
    }
    bar.append(actions);
    bar.append(el('div', 'om-ab-note', generating
      ? (inferring
        ? '生成后是一份待审核草案，接着走「校验 → 审核 → 发布」。'
        : '马上好。')
      : (pending > 0
        ? '本体生成、审核、发布后，这里才会出现「校验并审核 / 发布」；想要自动父子层级，用左栏的慢按钮。'
        : '这个项目还没有候选：先去「知识写入」页上传文档做开放发现。')));
    put(actionsHost, bar);
  }

  function renderActions(state) {
    if (!actionsHost) return;
    const st = state || S();
    const summary = st.summary || null;
    // 没本体（开放发现引导态）走简化底栏，避免一堆 disabled 控件 + 按钮换行。
    if (!summary) { renderEmptyActions(st); return; }
    const classes = (summary && summary.classes) || [];
    const rels = (summary && summary.relations) || [];
    const attrs = (summary && summary.attributes) || [];
    const candidates = st.candidates || [];
    // 「待收下」读开放发现概览的真实计数（unpublished_candidate_count），
    // 而不是画布虚线节点的 candidates.length（那一路从没接过数据源，恒为 0）。
    const disc = st.discovery || null;
    const pendingCandidates = disc ? (disc.unpublished_candidate_count || 0) : (candidates.length || 0);
    const draft = st.draft || null;
    const ops = (draft && draft.operations) || [];
    // 「冲突」= 草案里与基线冲突、需重新基线的变更（真实字段：operation.validation.rebase_status）
    const conflicts = ops.filter(o => ((o.validation || {}).rebase_status === 'conflict')).length;
    const readiness = st.readiness || null;
    // 「阻断」= 服务端发布清单里 error 级的条目（读不到就显示未知，不编）
    const blockers = (readiness && readiness.items)
      ? readiness.items.filter(i => i.severity === 'error').length : null;

    const bar = el('div', 'om-actionbar-inner');
    const counts = el('div', 'om-ab-counts');
    const chip = (label, value, cls, tip) => {
      const c = el('span', 'om-ab-chip' + (cls ? ' ' + cls : ''));
      c.append(el('b', null, value === null ? '—' : String(value)));
      c.append(el('i', null, label));
      if (tip) c.title = tip;
      return c;
    };
    counts.append(
      chip('类型', summary ? classes.length : null, 'cls', '当前本体的实体类数量'),
      chip('关系', summary ? rels.length : null, 'rel', '当前本体的关系类型数量'),
      chip('属性', summary ? attrs.length : null, 'attr', '当前本体的实体属性数量'),
      chip('待收下', pendingCandidates, 'pending', '知识写入抽出、还没收进本体的候选概念'),
      chip('冲突', draft ? conflicts : 0, 'conflict', '草案里与基线冲突、需要重新基线的变更数'),
      chip('阻断', blockers, 'block', '服务端发布清单里 error 级阻断项数量（读不到显示 —）'),
    );
    bar.append(counts);

    const actions = el('div', 'om-ab-actions');
    // 历史版本是只读预览：底栏的动作也要锁住，否则用户会在"看 v1"的状态下点发布，
    // 以为发布的是 v1（实际发的是手上那份草案）。锁住 + 说明去哪解锁。
    const previewing = !!st.preview;
    const previewLock = '正在查看历史版本（只读）。点顶栏「回到最新」退出预览，'
      + '或用「版本管理」→「回到这一版」以它为基础开一份新草案。';
    const reviewGate = gateReview(st);
    const btnReview = el('button', 'om-btn', '校验并审核');
    btnReview.type = 'button';
    btnReview.disabled = previewing || !reviewGate.enabled;
    btnReview.title = previewing ? previewLock
      : (reviewGate.enabled
        ? '校验草案 → 提交审核 → 一键收下服务端挑出的安全变更（阻断项/高风险项仍要逐条处理）'
        : reviewGate.reason);
    btnReview.addEventListener('click', validateAndReview);

    const pubGate = gatePublish(st);
    // E1「标注/结构分开计量」：版本号只跟**图结构**走。
    //   * 版本号读本体行的 `metadata.version`，**不是行数** —— 纯标注发布会多一行但版本号不变，
    //     拿 `list.length` 当版本号会多报一号（发布前说 v3、发布后还是 v2，用户会以为丢了一次发布）。
    //   * 这批操作全是标注动作时，按钮直接写"版本号不变"，别让 user 期待一个不会出现的号。
    const latestOntology = versions.loaded && versions.list.length
      ? versions.list[versions.list.length - 1] : null;
    const currentVersion = (latestOntology && latestOntology.metadata
      && Number(latestOntology.metadata.version))
      || (versions.loaded ? versions.list.length : 0);
    const annotationOnly = ops.length > 0 && ops.every(o =>
      o.action === 'add_annotation' || o.action === 'remove_annotation');
    const nextVersion = annotationOnly ? Math.max(currentVersion, 1) : currentVersion + 1;
    const btnPub = el('button', 'om-btn primary',
      versions.loaded
        ? `发布为 v${nextVersion}${annotationOnly ? '（仅标注 · 版本号不变）' : ''}`
        : '发布新版本');
    // title 三态交给下面的 syncPub()：门禁没过时说为什么灰着；门禁过了但没写发布说明，
    // 就直说"先写说明"，而不是让用户对着一个不动的灰色按钮猜。
    btnPub.type = 'button';
    btnPub.disabled = !pubGate.enabled;
    btnPub.addEventListener('click', publishDraft);

    // 发布说明（必填）：版本记录要能被看懂。做法与「停用原因」一致 ——
    // 界面负责"没写就不让点"，服务端（PublishRequest.note min_length=1）负责兜底。
    const noteInput = el('input', 'om-pub-note');
    noteInput.type = 'text';
    noteInput.placeholder = '必填：这一版改了什么';
    noteInput.value = publishNote;
    noteInput.addEventListener('input', () => { publishNote = noteInput.value; syncPub(); });

    // 灰着的原因要写在 title 上（不能只把按钮置灰不解释）
    const gateTitle = !pubGate.enabled
      ? pubGate.reason
      : (annotationOnly
        ? '这批变更只动了注释/标注，图结构没变：发布后沿用当前版本号，但变更一样留痕'
        : '把草案里已收下的变更原子发布成不可变的新版本');
    // 发布警告逐条确认（服务端要有人确认过才放行）。有内容的草案一动（revision 变），
    // 旧勾选立即作废：确认针对的是**当时**那份内容。
    const warnItems = ((st.readiness && st.readiness.items) || [])
      .filter(item => item.severity === 'warning');
    const warnSig = `${(st.draft && st.draft.id) || ''}@${(st.draft && st.draft.revision) || ''}`;
    if (ackWarnings.sig !== warnSig) ackWarnings = { sig: warnSig, codes: new Set() };

    function syncPub() {
      const hasNote = !!noteInput.value.trim();
      const warnOk = !warnItems.length
        || warnItems.every(item => ackWarnings.codes.has(item.code));
      noteInput.disabled = previewing;
      btnPub.disabled = previewing || !(pubGate.enabled && hasNote && warnOk);
      btnPub.title = previewing ? previewLock
        : !pubGate.enabled ? pubGate.reason
          : !hasNote
            ? '先写发布说明（必填）：版本记录要能回答"这一版为什么发、谁发的"'
            : (!warnOk
              ? `先逐条确认这 ${warnItems.length} 条发布警告：服务端要求有人确认过才能发布`
              : gateTitle);
    }
    syncPub();

    if (warnItems.length) {
      // 门禁没通过也要把它摆出来：回退的代价是"做这件事之前就该知道"的信息，
      // 藏到门禁通过之后才显示，用户会在不知道代价的情况下把变更做完。
      const warnBox = el('div', 'om-ab-warnings');
      warnBox.append(el('p', 'om-ab-warnings-title',
        `发布前必须确认这 ${warnItems.length} 条警告（服务端要求有人确认过才能发布）：`));
      warnItems.forEach(item => {
        const row = el('label', 'om-ab-warning');
        const tick = el('input');
        tick.type = 'checkbox';
        tick.checked = ackWarnings.codes.has(item.code);
        tick.addEventListener('change', () => {
          if (tick.checked) ackWarnings.codes.add(item.code);
          else ackWarnings.codes.delete(item.code);
          syncPub();
        });
        row.append(tick, el('span', null,
          `${item.message || item.code}${item.remediation ? ' —— ' + item.remediation : ''}`));
        warnBox.append(row);
      });
      actions.append(warnBox);
    }

    // 草案基线过期（服务端推导 needs_rebase==='stale_base'）：发布被 base_outdated 硬阻断，
    // 「校验并审核」「发布」此刻都灰着。补一个「重新基线」主按钮作为唯一出路 —— 没有它，
    // 用户只能"回到最新重开草案"（等于丢掉已做的停用等变更）。这正是操作手册第 9 章标注的
    // "发布被阻断、界面无该按钮"卡点的修复。
    const needsRebase = !!(st.stageAvailability
      && st.stageAvailability.needs_rebase === 'stale_base');
    if (needsRebase && !previewing) {
      const rebaseBox = el('div', 'om-ab-rebase');
      rebaseBox.append(el('p', 'om-ab-rebase-title',
        '这份草案的基线已经不是项目当前本体：要先把草案对齐到最新已发布版本，才能继续「校验并审核 → 发布」。'));
      const btnRebase = el('button', 'om-btn primary', '重新基线');
      btnRebase.type = 'button';
      btnRebase.title = '做什么：把草案基线对齐到项目当前本体，保留已做的变更（与基线冲突的变更会被标记出来）。不做什么：不发布、不改生效本体——重新基线后要重新「校验并审核」。';
      btnRebase.addEventListener('click', rebaseDraft);
      rebaseBox.append(btnRebase);
      actions.append(rebaseBox);
    }

    // 操作区：发布说明 + 校验并审核 + 发布，包成一行（.om-ab-ops 不换行，避免按钮被挤到两行）。
    const opRow = el('div', 'om-ab-ops');
    opRow.append(btnReview, noteInput, btnPub);
    actions.append(opRow);
    bar.append(actions);

    // 为什么按钮灰着，必须写在页面上（不能只塞进 title）
    const msgs = [];
    if (previewing) msgs.push('正在查看历史版本（只读）：这里不能改、也不能发布。点顶栏「回到最新」退出预览，或用「版本管理」→「回到这一版」以它为基础开一份新草案。');
    // 回退草案要自报身份：用户从版本管理点「回到这一版」之后，界面上必须能看出
    // "这份草案是在回到某一版"，否则他会以为自己在编辑当前的正式本体。
    if (!previewing && st.readiness && st.readiness.revert) {
      msgs.push('这是一份「回到某一版」的草案：内容等于你要回到的那一版。发布后当前版本就变成它的结构，'
        + '中间几版的结构改动不再留在当前版本里（那些版本仍在「版本管理」里，随时可以再回到它们）。'
        + '要在这个基础上再改，直接在画布上编辑即可。');
    }
    if (!summary) {
      // 区分「没选项目」和「选了项目但还没本体」：后者是开放发现的正常中间态，
      // 说「先选项目」是误导（用户明明选了）。和左栏引导态说同一句话。
      if (st.discovery && st.discovery.published === false) {
        msgs.push('这个项目还没生成本体：先点左栏「归纳并生成本体草案」，或从零新建类；本体就绪后这里才能校验、发布。');
      } else {
        msgs.push('读不到本体结构（先在左上角选择知识项目）。');
      }
    }
    if (!draft) {
      // 没草案时只提示一条：不再把「校验」「发布」各自的"还没有草案"变体重复 push（原文案几乎一样）。
      msgs.push('还没有草案：先在画布上新增或调整术语，草案会自动建立；之后这里才能「校验并审核」和发布。');
    } else {
      if (!st.stageAvailability) msgs.push('读不到服务端阶段门禁（stage-availability）——按未判定处理，按钮不猜可用性。');
      else if (!reviewGate.enabled && reviewGate.reason) msgs.push(reviewGate.reason);
      if (!pubGate.enabled && pubGate.reason) msgs.push(pubGate.reason);
    }
    const note = el('div', 'om-ab-note', msgs.join(' '));
    note.hidden = !msgs.length;
    bar.append(note);

    put(actionsHost, bar);

    // 版本号是异步的：只在没有缓存时取一次，拿到后重画一次底栏（ensureVersions 自带去重，不会成环）
    ensureVersions().then(changed => { if (changed && actionsHost) renderActions(); });
  }

  /* ---- 「重新基线」：POST /{draft_id}/rebase（草案基线过期时对齐到最新本体） ---- */
  // 什么时候出现：服务端 stage-availability 返回 needs_rebase === 'stale_base'（草案基线
  // 落后于项目当前本体，发布会被 publish-readiness 的 base_outdated 硬阻断）。
  // 这是「重新基线」在单画布的唯一前端入口：服务端 rebase 接口与审核台同款处理器一直都在，
  // 单画布此前从未接上 —— 于是「停用实体类型 → 发布」卡在"先重新基线"却找不到按钮
  // （操作手册第 9 章如实标注的已知卡点）。本函数就是补这个缺口。
  async function rebaseDraft() {
    const M = model();
    if (!M || !M.S || !M.S.draft || !M.S.draft.id) { status('还没有可重新基线的草案。', true); return; }
    // 重新基线的目标必须是项目当前最新本体：服务端 rebase() 拿 expected_ontology_id 与当前
    // 最新本体 id 比对，不一致直接 409 StaleBase。S.ontologyId 是 refresh() 从 GET /ontology
    // 取到的当前本体 id —— 读不到就别猜，让用户刷新后重试。
    const ontologyId = M.S && M.S.ontologyId;
    if (!ontologyId) {
      status('读不到项目当前本体（无法确定重新基线的目标版本），请刷新页面再试。', true);
      return;
    }
    const id = M.S.draft.id;
    const base = `/ontology-drafts/${encodeURIComponent(id)}`;
    try {
      status('正在把草案基线对齐到项目当前本体（保留你已做的变更）…');
      const result = await api(endpoint(base + '/rebase'), {
        expected_revision: M.S.draft.revision,
        expected_ontology_id: ontologyId,
      }, 'POST');
      const rebaseInfo = (result && result.rebase) || [];
      const clean = rebaseInfo.filter(r => r.classification === 'clean').length;
      const conflict = rebaseInfo.filter(r => r.classification === 'conflict').length;
      status(conflict
        ? `已重新基线：${clean} 条变更在新基线上仍然成立，${conflict} 条与基线冲突需重新处理；请重新「校验并审核」。`
        : '已重新基线到项目当前本体；请重新「校验并审核」再发布。');
    } catch (error) {
      status(error && error.message ? error.message : '重新基线失败', true);
    }
    if (typeof M.refresh === 'function') await M.refresh();
  }

  /* ---- 「校验并审核」：validate → submit → batch-approve ------------------- */
  async function validateAndReview() {
    const M = model();
    if (!M) return;
    let draft = M.S && M.S.draft;
    if (!draft || !draft.id) { status('还没有草案：先在画布上新增或调整术语，草案会自动建立。', true); return; }
    const id = draft.id;
    const base = `/ontology-drafts/${encodeURIComponent(id)}`;
    try {
      status('正在校验草案…');
      draft = await api(endpoint(base + '/validate'), { expected_revision: draft.revision }, 'POST');
      status('正在提交审核（冻结这一轮变更）…');
      draft = await api(endpoint(base + '/submit'), { expected_revision: draft.revision }, 'POST');
      status('正在一键收下服务端挑出的安全变更…');
      // 一键批准的集合由服务端 review_plan 决定，前端只表达意图（契约 §3.2）。
      // expected_ontology_id = 项目当前最新本体 id（主控从 GET /ontology 的 id 取到 S.ontologyId），
      // 服务端 _check_current 会拿它与 draft.base_ontology_id 比对，不一致即 StaleBase。别自己编。
      const result = await api(endpoint(base + '/decisions/batch-approve'), {
        expected_revision: draft.revision,
        expected_ontology_id: (M.S && M.S.ontologyId) || draft.base_ontology_id,
        validation_fingerprint: draft.validation_fingerprint,
        acknowledged_warning_codes: [],
        actor: (typeof M.actorName === 'function' ? M.actorName() : 'canvas-editor'),
      }, 'POST');
      const updated = (result && result.draft) || draft;
      const batch = (result && result.batch) || {};
      if (M.S) M.S.draft = updated;
      const skipped = Object.entries(batch.skipped_reasons || {})
        .map(([code, n]) => `${n} 条：${code}`).join('；');
      if (batch.approved) {
        status(`已收下 ${batch.approved} 条安全变更；还有 ${batch.skipped || 0} 条需要人工逐条处理${skipped ? '（' + skipped + '）' : ''}。`);
      } else {
        status(`没有可一键收下的安全变更；${batch.skipped || 0} 条需要人工逐条处理${skipped ? '（' + skipped + '）' : ''}。`);
      }
    } catch (error) {
      status(error && error.message ? error.message : '校验并审核失败', true);
    }
    if (typeof M.refresh === 'function') await M.refresh();
  }

  /* ---- 「发布」：POST /{draft_id}/publish（要指纹 + 幂等键 + actor + 说明） -- */
  async function publishDraft() {
    const M = model();
    if (!M || !M.S || !M.S.draft || !M.S.draft.id) { status('还没有可发布的草案。', true); return; }
    // 发布说明必填：服务端也一样（PublishRequest.note min_length=1）。
    // 在界面这一层先拦一道，用户不用等一次 422 才知道自己漏写了。
    const note = String(publishNote || '').trim();
    if (!note) {
      status('先写发布说明再发布：版本记录要能回答"这一版改了什么、为什么发"。', true);
      return;
    }
    const id = M.S.draft.id;
    const base = `/ontology-drafts/${encodeURIComponent(id)}`;
    try {
      status('正在确认发布门禁…');
      const readiness = await api(endpoint(base + '/publish-readiness'), undefined, 'GET');
      if (readiness.ready !== true) {
        const first = (readiness.items || []).find(item => item.severity === 'error');
        status('还不能发布：' + (first ? (first.message || first.code) : '服务端判定未就绪'), true);
        return;
      }
      // expected_revision / validation_fingerprint 从 draft 详情与 publish-readiness 取；
      // expected_ontology_id 用主控暴露的「项目当前最新本体 id」（S.ontologyId，refresh 时取自 GET /ontology），
      // 读不到才退回草案基线。三者都是服务端真值，前端不编。
      let fresh = M.S.draft;
      try { fresh = await api(endpoint(base), undefined, 'GET'); } catch (readError) { /* 退回本地 */ }
      const expectedRevision = (fresh && fresh.revision != null) ? fresh.revision : readiness.revision;
      const fingerprint = (fresh && fresh.validation_fingerprint) || readiness.validation_fingerprint;
      const ontologyId = (M.S && M.S.ontologyId) || (fresh && fresh.base_ontology_id)
        || M.S.draft.base_ontology_id;
      // 服务端要求"有人确认过才算数"：把草案校验里的警告码与用户刚逐条勾过的
      // 发布警告码并起来（回退那条 revert_drops_later_versions 只来自勾选 —— 它
      // 不属于 SHACL 校验报告，是发布门禁自己的警告）。
      const warnings = [...new Set([
        ...(((fresh.validation_report && fresh.validation_report.warnings) || [])
          .map(w => w && w.code).filter(Boolean)),
        ...ackWarnings.codes,
      ])];
      const key = nextIdempotencyKey(`${id}@${fingerprint || expectedRevision}@${note}`);
      status('正在原子发布不可变版本…');
      const version = await api(endpoint(base + '/publish'), {
        expected_revision: expectedRevision,
        expected_ontology_id: ontologyId,
        validation_fingerprint: fingerprint,
        acknowledged_warning_codes: warnings,
        idempotency_key: key,
        actor: (typeof M.actorName === 'function' ? M.actorName() : 'canvas-editor'),
        note,
      }, 'POST');
      publishKey = { sig: '', key: '' };   // 一把键只对应一次成功发布；下次发布用新的
      publishNote = '';                    // 说明跟着版本走了，输入框清空
      // 本地状态跟上：这份草案已成为终态，新版本成为项目当前版本。
      if (M.S.draft) { M.S.draft.status = 'accepted'; M.S.draft.published_ontology_id = version && version.id; }
      await ensureVersions(true);
      // 通知检索/问答/图谱等页面刷新（本体建模层发布成功后统一广播）。
      document.dispatchEvent(new CustomEvent('ontology-model:published', {
        detail: { versionId: version && version.id, projectId: activeProject(), draftId: id },
      }));
      // 发布结果要报**真实**版本号（"仅标注"时它会等于上一版），不能只说"已发布"
      const meta = (version && version.metadata) || {};
      const label = meta.version ? `v${meta.version}` : '';
      const reused = meta.version_reused ? '（仅标注变更，沿用上一版本号）' : '';
      status(`已发布为不可变版本${label ? ' ' + label : ''}${reused}`
        + `${version && version.id ? ' · ' + version.id : ''}：检索、问答与画布读到的本体立即更新；`
        + '发布说明已写进版本记录，在「版本管理」里可以查。');
    } catch (error) {
      status(error && error.message ? error.message : '发布失败', true);
    }
    if (typeof M.refresh === 'function') await M.refresh();
  }

  /* ==========================================================================
   * 版本管理（B6）：/ontologies 版本列表 + 发布记录 + 查看/回到某一版
   * ======================================================================== */
  async function ensureVersions(force) {
    const project = activeProject();
    if (!project) { versions.list = []; versions.loaded = false; versions.key = ''; return false; }
    if (!force && versions.key === project && versions.loaded) return false;
    // 已有一份在途请求就复用它：调用方（底栏/抽屉渲染很频繁）不会并发打请求，
    // 而且 openHistory 一定能等到这次结果，不会因为"正在读"就永远停在空抽屉。
    if (versions.loading && versions.promise) return versions.promise;
    versions.loading = true;
    versions.promise = (async () => {
      try {
        const res = await api(endpoint('/ontologies'), undefined, 'GET');
        versions.list = (res && res.versions) || [];   // 返回键是 versions（见 ontology-archive.js）
        versions.key = project;
        versions.loaded = true;
        return true;
      } catch (error) {
        versions.loaded = false;
        return false;
      } finally {
        versions.loading = false;
        versions.promise = null;
      }
    })();
    return versions.promise;
  }

  function ensureDrawer() {
    if (drawer) return drawer;
    // 挂在页签根节点（带 .ontology-model 类）内部：CSS 作用域才能命中，随页签一起隐藏。
    const root = document.getElementById('tab-ontology-model') || document.body;
    const overlay = el('div', 'om-history');
    overlay.id = 'om-history-overlay';
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '版本管理');
    const backdrop = el('div', 'om-history-backdrop');
    const panel = el('section', 'om-history-panel');
    const head = el('header', 'om-history-head');
    head.append(el('b', null, '版本管理'));
    head.append(el('span', 'om-history-sub', '每一版：谁、什么时候、为什么发（可查看 / 可回到）'));
    const close = el('button', 'om-btn ghost', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭版本管理');
    close.title = '关闭';
    close.addEventListener('click', closeHistory);
    head.append(close);
    const body = el('div', 'om-history-body');
    body.id = 'om-history-body';
    panel.append(head, body);
    overlay.append(backdrop, panel);
    backdrop.addEventListener('click', closeHistory);
    overlay.addEventListener('keydown', event => { if (event.key === 'Escape') closeHistory(); });
    root.append(overlay);
    drawer = { overlay, body };
    return drawer;
  }

  function renderHistory() {
    if (!drawer) return;
    const body = drawer.body;
    const project = activeProject();
    if (!project) { put(body, el('p', 'om-history-empty', '请先在左上角选择知识项目。')); return; }
    if (!versions.list.length) {
      put(body, el('p', 'om-history-empty',
        '这个项目还没有已发布的本体版本。每发布一次，这里就多一条不可变的记录。'));
      return;
    }
    const st = S();
    const list = el('ol', 'om-history-list');
    const ordered = versions.list.map((v, index) => ({ v, index })).reverse(); // 最新在前
    if (!ui.selectedVersion || !versions.list.some(v => v.id === ui.selectedVersion)) {
      ui.selectedVersion = ordered[0].v.id;
    }
    ordered.forEach(({ v, index }) => {
      const li = el('li', 'om-history-item' + (v.id === ui.selectedVersion ? ' sel' : ''));
      const row = el('button', 'om-history-row');
      row.type = 'button';
      row.append(el('b', null, `v${v.version || index + 1}`));
      row.append(el('span', 'om-history-time', fmtTime(v.created_at)));
      if (v.actor) row.append(el('span', 'om-history-actor', v.actor));
      const sum = v.summary || {};
      row.append(el('span', 'om-history-counts',
        `${(sum.classes || []).length} 类 · ${(sum.relations || []).length} 关系 · ${(sum.attributes || []).length} 属性`));
      if (index === versions.list.length - 1) row.append(el('span', 'om-history-cur', '正在生效'));
      if (st.preview && st.preview.id === v.id) row.append(el('span', 'om-history-viewing', '正在查看'));
      row.addEventListener('click', () => { ui.selectedVersion = v.id; renderHistory(); });
      li.append(row);
      // 列表行直接带上发布说明：版本列表的第一用途就是"扫一眼每版为什么发"，
      // 点开才看得到的话，一屏版本多起来就没人愿意看了。
      if (v.note) li.append(el('p', 'om-history-line-note', v.note));
      if (v.id === ui.selectedVersion) li.append(renderVersionDetail(v, index));
      list.append(li);
    });
    put(body, list);
  }

  function renderVersionDetail(v, index) {
    const box = el('div', 'om-history-detail');
    const sum = v.summary || {};
    const pub = (v.metadata && v.metadata.publication) || {};
    const facts = el('div', 'om-history-facts');
    const fact = (k, value) => {
      const f = el('div', 'om-history-fact');
      f.append(el('span', null, k));
      f.append(el('b', null, value === undefined || value === null ? '—' : String(value)));
      return f;
    };
    facts.append(fact('版本', `v${v.version || index + 1}`));
    facts.append(fact('版本 ID', shortId(v.id)));
    facts.append(fact('发布时间', fmtTime(v.created_at)));
    const writePath = pub.write_path || (v.metadata && v.metadata.write_path);
    if (writePath) facts.append(fact('写入路径', writePath));
    const actor = v.actor || pub.actor;
    if (actor) facts.append(fact('发布人', actor));
    if (pub.source_kind) facts.append(fact('来源', pub.source_kind));
    facts.append(fact('规模', `${sum.triples || 0} 三元组`));
    box.append(facts);

    // 发布说明：版本管理的主角。老版本（本功能之前发布的）没有说明，如实说"没有"，
    // 不补一句编出来的话。
    const note = el('p', 'om-history-pub-note');
    note.append(el('b', null, '发布说明：'));
    note.append(el('span', null, v.note || '（这一版发布于发布说明功能上线之前，没有留下说明）'));
    box.append(note);

    const top = (sum.classes || []).slice(0, 8)
      .map(c => c.label_zh || c.label || c.name || c.id);
    if (top.length) box.append(el('p', 'om-history-note', `实体类（前 ${top.length} 个）：${top.join('、')}`));

    // 两个动作，语义必须分清（这是版本管理唯一容易出事的地方）：
    //   查看这一版 = 只读预览，不写任何数据；
    //   回到这一版 = 以它为基线开一份**新草案** → 审核 → 发布成新版本。历史不改写。
    const st = S();
    const actions = el('div', 'om-history-actions');
    const viewBtn = el('button', 'om-btn', '查看这一版（只读）');
    viewBtn.type = 'button';
    viewBtn.title = '做什么：把画布切到这一版看结构，不写任何数据、不影响当前版本。不做什么：不会切换生产版本，也不能在这一版上直接编辑。';
    const alreadyViewing = !!(st.preview && st.preview.id === v.id);
    viewBtn.disabled = alreadyViewing;
    viewBtn.textContent = alreadyViewing ? '正在查看这一版' : '查看这一版（只读）';
    viewBtn.addEventListener('click', async () => {
      const M = model();
      if (!M || typeof M.previewVersion !== 'function') { status('主控未就绪，暂时无法查看历史版本。', true); return; }
      closeHistory();
      await M.previewVersion(v.id, v.version || index + 1);
      status(`正在查看 v${v.version || index + 1}（只读）。要在这版基础上改，用「版本管理」里的「回到这一版」。`);
    });

    const backBtn = el('button', 'om-btn primary', '回到这一版');
    backBtn.type = 'button';
    backBtn.title = '做什么：以这一版为基础开一份新草案，改完审核发布后成为新版本。不做什么：不删掉后面的版本、不改写历史——"回到"等于"再发一版"。';
    backBtn.addEventListener('click', async () => {
      const M = model();
      if (!M || typeof M.forkFrom !== 'function') { status('主控未就绪，暂时无法回到历史版本。', true); return; }
      backBtn.disabled = true;
      try {
        await M.forkFrom(v.id);
        closeHistory();
        status(`已以 v${v.version || index + 1} 为基础开了一份新草案：改完在底栏「校验并审核」，再发布就成为一个新版本（历史版本原样保留）。`);
      } catch (error) {
        status((error && error.message) || '回到历史版本失败', true);
      } finally {
        backBtn.disabled = false;
      }
    });
    actions.append(viewBtn, backBtn);
    box.append(actions);

    box.append(el('p', 'om-history-note',
      '做什么：查看任意已发布版本；把某一版作为基础开一份新草案。'
      + '不做什么：不改写历史——「回到这一版」不是删掉后面的版本，而是发布一条以它为基础的新版本，'
      + '所以每一次结构变化在版本列表里都留得住。'));
    return box;
  }

  async function openHistory() {
    ensureDrawer();
    drawer.overlay.removeAttribute('hidden');
    ui.historyOpen = true;
    renderHistory();           // 先用缓存画一帧
    await ensureVersions(true); // 等这次读取结束（含在途请求），再无条件重画一次
    renderHistory();
    const closeBtn = drawer.overlay.querySelector('button');
    if (closeBtn) closeBtn.focus();
  }
  function closeHistory() {
    if (!drawer) return;
    drawer.overlay.setAttribute('hidden', '');
    ui.historyOpen = false;
  }

  /* ==========================================================================
   * 命名空间
   * ======================================================================== */
  // 整块重画：右栏详情 + 底栏动作条。事件处理器与 mount 都走这里（模块级函数，
  // 不能写成裸 render()——那是命名空间对象上的方法，在小作用域里并不存在）。
  function renderPanel(state) {
    const st = state || S();
    renderDetail(st);
    renderActions(st);
  }

  window.OntologyPanel = {
    mounted: false,
    mount(detail, actions, controller) {
      detailHost = detail || null;
      actionsHost = actions || null;
      ctx = controller || window.OntologyModel || null;
      // 切项目会重建骨架并再次 mount：先撤旧订阅，避免同一事件被反复处理。
      unsubscribers.forEach(off => { try { off(); } catch (error) { /* 已失效 */ } });
      unsubscribers = [];
      if (ctx && typeof ctx.on === 'function') {
        const rerender = () => renderPanel();
        unsubscribers.push(ctx.on('select', rerender));
        unsubscribers.push(ctx.on('draft:changed', rerender));
        unsubscribers.push(ctx.on('stage:changed', rerender));
        // 进/出历史版本预览会改变"能不能编辑"与列表上的「正在查看」徽标：
        // 底栏要重画（按钮锁/解锁），抽屉开着时也要重画。
        unsubscribers.push(ctx.on('preview:changed', () => {
          renderPanel();
          if (ui.historyOpen) renderHistory();
        }));
      }
      this.mounted = true;
      renderPanel();
    },
    render(state) { renderPanel(state); },
    // 画布「＋ 新建类」调它：清空选中后把焦点送进「新建术语」的名称输入框。
    focusCreate() { focusCreate(); },
    openHistory() { return openHistory(); },
    closeHistory() { closeHistory(); },
  };
})();
