/* ============================================================================
 * 本体建模层（ontology-model-panel.js）—— 右栏详情 / 底栏动作条 / 版本管理抽屉
 * P1（2026-10-04）实现者 B。契约见 docs/2026-10-04-P1前端实现契约.md §2.5：
 *   window.OntologyPanel = { mount(detailHost, actionsHost, ctx), render(state), openHistory(ontologyId?) }
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
  const put = (host, ...nodes) => {
    host.replaceChildren(...nodes);
    if (host === actionsHost) window.OntologyWorkspace?.syncActions?.();
    return host;
  };

  let helpId = 0;
  function detailTitle(text, explanation) {
    const heading = el('h5', 'om-detail-title', text);
    if (!explanation) return heading;
    const wrap = el('span', 'om-help-wrap');
    const button = el('button', 'om-help-button', '?');
    button.type = 'button';button.setAttribute('aria-label', text + '说明');
    const popup = el('span', 'om-help-popover', explanation);
    popup.id = 'om-help-' + (++helpId);popup.hidden = true;
    popup.setAttribute('role', 'tooltip');popup.setAttribute('popover', 'manual');
    button.setAttribute('aria-describedby', popup.id);
    let timer;
    const hide = () => {
      clearTimeout(timer);
      if (popup.hidePopover) { try { popup.hidePopover(); } catch (_) { /* fallback DOM */ } }
      popup.hidden = true;
    };
    const show = () => {
      clearTimeout(timer);popup.hidden = false;
      if (!popup.showPopover) return;
      popup.showPopover();
      const anchor = button.getBoundingClientRect(), bounds = popup.getBoundingClientRect();
      popup.style.left = Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12)) + 'px';
      popup.style.top = Math.max(8, anchor.bottom + bounds.height + 10 < window.innerHeight
        ? anchor.bottom + 8 : anchor.top - bounds.height - 8) + 'px';
    };
    wrap.addEventListener('mouseenter', show);
    wrap.addEventListener('mouseleave', () => { timer = setTimeout(hide, 120); });
    popup.addEventListener('mouseenter', () => clearTimeout(timer));
    popup.addEventListener('mouseleave', hide);
    button.addEventListener('focus', show);button.addEventListener('blur', hide);
    button.addEventListener('click', event => { event.stopPropagation();show(); });
    button.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault();event.stopPropagation();hide(); }
    });
    wrap.append(button, popup);heading.append(wrap);
    return heading;
  }

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
  let historyProject = '', versionsRequest = 0, historyOpening = 0;
  const versionLabel = version => version === undefined || version === null
    ? '版本未标注' : `v${version}`;
  const hasVersionCollision = version => versions.list.filter(
    item => item.version === version).length > 1;
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
  const actionTasks = new Map();

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
  const actionKey = (project,draftId) => JSON.stringify([project,draftId]);
  const currentAction = st => actionTasks.get(actionKey(activeProject(),st.draft?.id));
  const actionVisible = task => activeProject()===task.project&&S().draft?.id===task.draftId;
  function beginAction(kind,draft,message) {
    const key=actionKey(activeProject(),draft.id);if(actionTasks.has(key))return null;
    const task={key,kind,project:activeProject(),draftId:draft.id,message,slow:false};actionTasks.set(key,task);
    if(typeof setTimeout==='function')task.timer=setTimeout(()=>{if(actionTasks.get(key)!==task)return;task.slow=true;if(actionVisible(task))renderActions();},8000);
    renderActions();return task;
  }
  function actionPhase(task,message) {
    task.message=message;if(actionVisible(task)){status(message);renderActions();}
  }
  function finishAction(task) {
    if(typeof clearTimeout==='function')clearTimeout(task.timer);
    if(actionTasks.get(task.key)===task)actionTasks.delete(task.key);
    if(actionVisible(task))renderActions();
  }
  const workflowBlockers = new Set(['not_submitted','decision_pending','no_approval']);
  function readinessNextStep(item,draft) {
    const submitted=!!draft?.submitted_at;
    const steps={
      not_submitted:'先点击「校验并审核」，完成校验并提交审核。',
      decision_pending:submitted?'可批量批准符合条件的变更，或进入「按定义审核」集中核对每个类型和关系。':'先点击「校验并审核」提交本轮，再到「按定义审核」核对仍未决定的变更。',
      no_approval:submitted?'进入「按定义审核」，至少批准一个有效定义，或关闭草案。':'先点击「校验并审核」提交本轮，再按定义审核。',
      base_outdated:'先点击「重新基线」对齐当前正式本体，再重新校验和审核。',
      stale_source:'先点击「刷新来源」核对最新来源，再重新校验和审核。',
      validation_changed:'重新点击「校验并审核」，核对更新后的校验结果。',
      validation_unavailable:'修复服务端提示的本体或数据问题，再重新校验。',
      rebase_conflict:'核对冲突定义并重新基线，解决冲突后重新校验。',
      no_operation:'补充需要的定义变更，或在草案管理中关闭本轮。',
      draft_settled:'在草案管理中另开一轮修改，再编辑和审核。',
    };
    return steps[item.code]||item.remediation||'核对服务端诊断，修复相关定义后重新校验。';
  }
  function renderReadinessItems(st,host) {
    const items=(st.readiness?.items||[]).filter(item=>item.severity==='error');if(!items.length)return;
    const box=el('section','om-ab-blockers');box.setAttribute('aria-label','发布前待处理条件');
    box.append(el('strong',null,`发布前还需处理 ${items.length} 项条件`));
    for(const item of items){
      const row=el('article','om-ab-blocker'+(workflowBlockers.has(item.code)?' is-workflow':' is-error'));
      row.append(el('p','om-ab-blocker-message',item.message||'服务端未提供阻断说明'),
        el('p','om-ab-blocker-next','下一步：'+readinessNextStep(item,st.draft)));
      const technical=el('details','om-ab-blocker-technical');technical.append(el('summary',null,'技术诊断'),el('pre',null,JSON.stringify(item,null,2)));
      row.append(technical);box.append(row);
    }
    host.append(box);
  }

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
    if (S().preview) return el('p', 'om-detail-note', '历史版本只读，端点以本版定义为准。');
    const sec = el('details', 'om-detail-sec om-endpoints');
    sec.append(el('summary', null, '根据实际用法补全类型'));
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
    const inferred = (usage && usage.inferred) || { domain: [], range: [] };
    const used = Number.isFinite(usage?.used) && usage.used >= 0 ? usage.used : null;
    const missing = (usage && usage.missing) || { domain: false, range: false };
    const head = el('div', 'om-usage-head');
    head.append(el('strong', null, '推荐依据'),
      el('span', 'om-usage-count', used === null ? '使用次数未知' : `${used} 条知识`));
    nodes.push(head, el('p', 'om-usage-context', usage?.source === 'draft' ? '对照草案定义' : '对照已发布定义'));
    if (used === null || used === 0) {
      nodes.push(el('p', 'om-usage-empty', used === 0
        ? '暂无使用记录，暂不提供类型推荐。' : '尚未获取使用次数，暂不提供类型推荐。'));
      return nodes;
    }
    const sides = el('div', 'om-usage-sides');
    for (const [key, title] of [['domain', '主体类型'], ['range', '客体类型']]) {
      const side = el('section', 'om-usage-side');
      side.append(el('h6', 'om-usage-side-title', title));
      const items = inferred[key] || [];
      if (items.length) {
        const list = el('ul', 'om-usage-types');
        items.forEach(item => {
          const row = termRow('cls', item.label || labelOfIri(item.iri, summary),
            item.count == null ? '次数未知' : `${item.count} 次`, item.iri);
          row.classList.add('om-usage-type');list.append(row);
        });
        side.append(list);
      } else side.append(el('p', 'om-usage-note', '暂未识别类型'));
      sides.append(side);
    }
    nodes.push(sides, el('p', 'om-usage-note', '根据已有关系的两端类型汇总，请核对后再采用。'));

    const samples = (usage && usage.samples) || [];
    if (samples.length) {
      const evidence = el('details', 'om-usage-samples');
      evidence.append(el('summary', null, `核对样例（${Math.min(3, samples.length)} 条）`));
      const list = el('ol', 'om-usage-sample-list');
      samples.slice(0, 3).forEach(sample => {
        const card = el('li', 'om-usage-sample');
        for (const [key, title] of [['subject', '主体'], ['object', '客体']]) {
          if (key === 'object') {
            const arrow = el('span', 'om-usage-sample-arrow', '↓');arrow.setAttribute('aria-hidden', 'true');card.append(arrow);
          }
          const endpoint = el('div', 'om-usage-sample-endpoint');
          endpoint.append(el('small', null, `${title} · ${sample[key + '_type'] || '类型未知'}`),
            el('p', null, sample[key] || `（无${title}）`));
          card.append(endpoint);
        }
        list.append(card);
      });
      evidence.append(list);nodes.push(evidence);
    }

    const canDom = !!(inferred.domain || []).length;
    const canRng = !!(inferred.range || []).length;
    if (!missing.domain && !missing.range) {
      nodes.push(el('p', 'om-usage-status', '两端已声明，无需补全。'));
      return nodes;
    }
    const btn = el('button', 'om-btn primary om-usage-apply', '将推荐写入草案');
    btn.type = 'button';
    btn.disabled = !(canDom && canRng);
    btn.title = (canDom && canRng)
      ? '将上方推荐类型加入草案，审核发布后生效。'
      : '两端依据尚不完整，宁可不补，也不猜。';
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
    const cell = el('div', 'om-usage-actions');
    cell.append(btn);
    nodes.push(cell);
    if (!(canDom && canRng)) {
      nodes.push(el('p', 'om-usage-note', '两端依据尚不完整，暂不能写入草案。'));
    }
    nodes.push(el('p', 'om-usage-note', '写入草案后，仍需审核发布。'));
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
   * 新建定义经主控编译为一组原子命令，一次写入草案，仍需审核与发布。
   * ======================================================================== */
  const NEW_KINDS = [
    { value: 'class', text: '实体类', hint: '一个概念，可以被别的类继承。建完可以用「改父类（连线）」挂到父类下面。' },
    { value: 'relation', text: '关系类型', hint: '两个类之间允许的连接。主体类/客体类可以先留空，之后在关系详情的「两端」里按实际用法补。' },
    { value: 'attribute', text: '实体属性', hint: '挂在某个类上的一个字段。数据类型暂时不限定也可以，之后按需要补。' },
  ];
  let createPreset = {};
  let createOpen = false,createProject=null;
  let createValues={};
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
    if ((state || S()).preview) return el('p', 'om-detail-note', '历史版本只读。回到最新或从版本管理创建新草案后继续建模。');
    const summary = (state && state.summary) || {};
    const classes = (summary.classes || []).filter(c => c.active !== false);
    const classOptions = classes.map(c => ({ id: c.id, text: labelOfIri(c.id, summary) }));

    const box = el('div', 'om-detail-card om-new-term');
    box.append(el('h5', 'om-detail-title', '新增定义'));
    box.append(el('p', 'om-detail-line',
      '从零建一个类 / 关系 / 属性，写进当前草案。底栏「校验并审核」后发布才生效。'));

    const kindSel = el('select', 'om-new-input');
    NEW_KINDS.forEach(k => { const o = el('option', null, k.text); o.value = k.value; kindSel.append(o); });
    const kindHint = el('p', 'om-detail-note', NEW_KINDS[0].hint);

    const zh = el('input', 'om-new-input');
    zh.type = 'text'; zh.placeholder = '中文名（必填），例如：优惠券';
    zh.value=createValues.zh||'';zh.addEventListener('input',()=>{createValues.zh=zh.value;});
    createInput = zh;
    const en = el('input', 'om-new-input');
    en.type = 'text'; en.placeholder = '英文标识（选填，留空用中文名）';
    en.value=createValues.en||'';en.addEventListener('input',()=>{createValues.en=en.value;});
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
    kindSel.value=createValues.kind||createPreset.kind||'class';
    kindHint.textContent=(NEW_KINDS.find(item=>item.value===kindSel.value)||NEW_KINDS[0]).hint;
    syncOwnership(kindSel.value);
    if(parentSel)parentSel.value=createValues.parent||createPreset.parent||'';
    if(domainSel)domainSel.value=createValues.domain||createPreset.domain||'';
    if(rangeSel)rangeSel.value=createValues.range||'';
    const saveOwnership=()=>{createValues.parent=parentSel?.value||'';createValues.domain=domainSel?.value||'';createValues.range=rangeSel?.value||'';};
    slot.addEventListener('change',saveOwnership);
    kindSel.addEventListener('change', () => {
      const k = NEW_KINDS.find(x => x.value === kindSel.value) || NEW_KINDS[0];
      kindHint.textContent = k.hint;
      createValues.kind=kindSel.value;
      syncOwnership(kindSel.value);
    });

    const submit = el('button', 'om-btn primary', '新建并写入草案');
    submit.type = 'button';
    submit.disabled = !zh.value.trim();
    const syncButton = () => { submit.disabled = !zh.value.trim(); };
    zh.addEventListener('input', syncButton);

    submit.addEventListener('click', async () => {
      const M = model();
      const text = zh.value.trim();
      if (!text) { status('请先填写中文名。', true); return; }
      if (!M || typeof M.createTerm !== 'function') { status('主控未就绪，暂时无法新建。', true); return; }
      const project=current,kind=kindSel.value;
      const kindText=(NEW_KINDS.find(x=>x.value===kind)||NEW_KINDS[0]).text;
      let saved=false;
      submit.disabled = true;
      try {
        status('正在写入草案…');
        await M.createTerm({
          kind, uri: '', label: en.value.trim() || text, label_zh: text,
          // 归属：按 kind 只带相关字段，避免服务端同时收到互相矛盾的约束
          parent: (kind === 'class' && parentSel && parentSel.value) ? parentSel.value : '',
          domain: (kind === 'attribute' && domainSel && domainSel.value) ? domainSel.value : '',
          domains: (kind === 'relation' && domainSel && domainSel.value) ? [domainSel.value] : [],
          ranges: (kind === 'relation' && rangeSel && rangeSel.value) ? [rangeSel.value] : [],
        });
        saved=true;
        if(current!==project)return;
        createPreset={};createValues={};createOpen=false;
        zh.value = ''; en.value = '';
        syncButton();
        status(`已新建${kindText}「${text}」：写进了草案，底栏「校验并审核」后发布生效。`);
      } catch (error) {
        if(current!==project)return;
        status(saved?`已保存到草案，但画布刷新失败：${error.message||error}。请刷新读取，避免重复新增。`:`新建失败：${error.message||error}`, true);
        syncButton();
      }
    });

    const actions = el('div', 'om-detail-actions');
    const target=(state||S()).draft;
    box.append(el('p','om-create-target',target?.status==='pending'
      ? `目标：${target.id} · r${target.revision??'?'}（未发布）`
      : (state||S()).draftChoices?.length>1?'目标尚未选择：保存前在本轮修改中选择草案。':'目标：保存时基于当前正式发布建立工作草案。'));
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
    if(createProject!==activeProject()){createProject=activeProject();createOpen=false;createValues={};createPreset={};}
    const selected = st.selected;
    if (!selected) {
      // 没选中对象时，右栏就是「新建术语」的入口（不再只是一句提示）。
      // 用户反馈"现在父类也加不了"的根因：单画布整页只有「改父类（连线）」，
      // 而它必须先选中一个**已存在**的类 —— 空本体上没有任何地方能新建一个类，
      // 想给它加父类自然无从谈起。表单放这里 + 画布「＋ 新建类」按钮清空选中，
      // 就让"从零建第一层"有了唯一且显眼的入口。
      put(detailHost, createOpen ? newTermSection(st) : el('p','om-detail-empty','选择一个定义查看详情；新增结构请使用顶部「新增定义」。'));
      return;
    }
    createOpen=false;
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
    const draft=!!S().showingDraft;
    head.append(el('span', 'om-detail-badge ' + (active ? 'is-active' : 'is-deprecated'),draft?(active?'草案中启用':'草案中停用'):(active?'已发布 · 启用':'已发布 · 停用')));
    if(draft)head.append(el('span','om-detail-badge is-unpublished','未发布'));
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
    sec.append(detailTitle(`${title}（${count}）`, note));
    if (rows && rows.length) {
      const ul = el('ul', 'om-detail-rows');
      rows.forEach(row => ul.append(row));
      sec.append(ul);
    } else {
      sec.append(el('p', 'om-detail-inline-empty', '（无）'));
    }
    return sec;
  }
  // 「适用规则」：本层没有按术语的规则数据，所以如实说明来源，不自己推一套判定。
  function rulesSection(term) {
    const sec = el('div', 'om-detail-sec');
    sec.append(detailTitle('状态', '草案中的修改经审核、发布后才生效。停用的术语仍保留历史知识。发布条件以服务端校验为准。'));
    const active = term.active !== false;
    sec.append(el('p', 'om-detail-line',
      S().showingDraft ? `草案中${active?'启用':'停用'} · 尚未发布` : active ? '已发布并启用' : '已停用 · 历史知识保留'));
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
    if (S().preview) return el('p', 'om-detail-note', '历史版本只读，生命周期变更需回到最新后加入草案。');
    const active = term.active !== false;
    const iri = term.id;
    const project = activeProject();
    const sec = el('details', 'om-detail-sec om-retire');
    sec.append(el('summary', 'om-detail-title', active ? '停用这个术语' : '恢复这个术语'));
    sec.append(el('p', 'om-detail-line', active
      ? '停用后不再用于新写入；历史知识与版本轨迹保留。先核对影响，再加入草案审核。'
      : '选择同项目的来源发布及要恢复的定义字段；恢复激活与字段变更仍需逐项审核。'));

    let source = null;
    const selectedFields = new Set();
    if (!active) {
      source = el('select', 'om-restore-source');
      const placeholder = el('option', null, '请选择来源发布'); placeholder.value = ''; source.append(placeholder);
      sec.append(newField('恢复来源', source));
      const fields = el('fieldset', 'om-restore-fields');
      fields.append(el('legend', null, '恢复哪些定义字段（至少一项）'));
      const kind = (S().summary?.classes || []).some(item => item.id === iri) ? 'class'
        : (S().summary?.relations || []).some(item => item.id === iri) ? 'relation' : 'attribute';
      const choices = [['annotations', '名称与说明'], ...(kind === 'class' ? [['parents', '父类']]
        : kind === 'relation' ? [['domain', '主体类'], ['range', '客体类']]
          : [['domain', '所属类'], ['datatype', '数据类型']])];
      for (const [value, label] of choices) {
        const input = el('input'); input.type = 'checkbox'; input.value = value;
        input.addEventListener('change', () => { input.checked ? selectedFields.add(value) : selectedFields.delete(value); syncButton(); });
        const row = el('label'); row.append(input, el('span', null, label)); fields.append(row);
      }
      sec.append(fields);
      source.addEventListener('change', () => syncButton());
      void ensureVersions().then(() => {
        if (activeProject() !== project) return;
        for (const release of versions.list) {
          const option = el('option', null, `${versionLabel(release.version)} · ${shortId(release.id)}`);
          option.value = release.id; source.append(option);
        }
        if (!versions.loaded) impactLine.textContent = '来源发布读取失败，请刷新后重试。';
      });
    }

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
    const syncButton = () => { confirm.disabled = !(impactReady && reason.value.trim()
      && (active || (source.value && selectedFields.size))); };
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
      if (activeProject() !== project || S().preview) { status('查看范围已变化，请重新选择术语。', true); return; }
      const text = reason.value.trim();
      if (!text) { status('必须填写原因。', true); return; }
      confirm.disabled = true;
      try {
        if (!active && (!source.value || !selectedFields.size)) throw new Error('请选择来源发布及至少一个恢复字段。');
        await M.sendCommand({ action: active ? 'retire_term' : 'restore_term', target_iri: iri, reason: text,
          ...(!active ? { source_ontology_id: source.value, selected_fields: [...selectedFields] } : {}) });
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
    if (S().preview) return el('p', 'om-detail-note', '历史版本只读，保留本版的多父类及端点定义。');
    const classes = (summary.classes || []).filter(c => c.active !== false);
    let options = classes.map(c => ({ id: c.id, text: labelOfIri(c.id, summary) }));
    // 父类下拉必须排除当前类自身：一个类不能是自己的父类（否则形成自环，真机探针抓到
    // target 与 value 是同一个 IRI）。domain/range 不做此排除（关系本就可指向自身所在类）。
    if (kind === 'class') options = options.filter(o => o.id !== term.id);

    const sec = el('details', 'om-constraint-edit');
    // 折叠区标题按对象类型动态显示——类没有 domain/range，别把四类约束都列上去造成误解。
    const CE_TITLE = {
      class: '编辑继承（父类）',
      relation: '选择关系两端类型',
      attribute: '编辑所属类 / 数据类型',
    };
    const summary2 = el('summary', null, CE_TITLE[kind] || '编辑约束');
    const parentPreview = kind === 'class' ? el('span', 'om-parent-summary') : null;
    function showParents(iris) {
      if (!parentPreview) return;
      parentPreview.replaceChildren(...(iris.length
        ? iris.map(iri => el('span', 'om-ce-chip', labelOfIri(iri, summary)))
        : [el('span', 'om-ce-empty', '暂无父类')]));
    }
    if (parentPreview) {
      sec.classList.add('om-parent-edit');
      parentPreview.setAttribute('aria-label', '当前父类');
      showParents(term.parents || []);
      summary2.append(parentPreview);
    }
    const endpointPreviews = new Map();
    function showEndpoint(action, iris) {
      const preview = endpointPreviews.get(action);
      if (!preview) return;
      preview.replaceChildren(...(iris.length
        ? iris.map(iri => el('span', 'om-ce-chip', labelOfIri(iri, summary)))
        : [el('span', 'om-ce-empty', '未限定')]));
    }
    if (kind === 'relation') {
      sec.classList.add('om-endpoint-edit');
      const preview = el('span', 'om-endpoint-summary');
      for (const [field, action, title] of [['domain', 'add_domain', '主体'], ['range', 'add_range', '客体']]) {
        const row = el('span', 'om-endpoint-summary-row'), values = el('span', 'om-endpoint-summary-values');
        values.setAttribute('aria-label', '当前' + title + '类型');endpointPreviews.set(action, values);
        row.append(el('span', 'om-endpoint-summary-label', title), values);preview.append(row);
        showEndpoint(action, term[field] || []);
      }
      summary2.append(preview);
    }
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
        return true;
      } catch (error) { status((error && error.message) || '编辑失败', true); }
    }

    // Keep edits local until Apply so a multi-type change is one atomic draft command.
    function linkRow(labelText, currentIris, addAction, removeAction) {
      const row = el('div', 'om-ce-row');
      row.append(detailTitle(labelText, kind === 'class'
        ? '可搜索并勾选多个父类。点击「应用选择」保存到草案；未声明父类时为顶层类。'
        : '可搜索并勾选多个实体类型。点击「应用选择」保存到草案；清空后表示未限定。'));
      const picker = el('details', 'om-type-picker'), caption = el('summary');
      const search = el('input', 'om-type-search');search.type = 'search';search.placeholder = '搜索实体类型';
      search.setAttribute('aria-label', '搜索' + labelText);
      const list = el('div', 'om-type-options');list.setAttribute('role', 'group');list.setAttribute('aria-label', labelText);
      const empty = el('p', 'om-detail-inline-empty', '没有匹配的实体类型');empty.hidden = true;
      let baseline = new Set(currentIris || []), chosen = new Set(baseline), busy = false;
      const choices = [...options];
      // Unknown or retired declarations remain selected until explicitly unchecked.
      for (const iri of baseline) if (!choices.some(option => option.id === iri))
        choices.push({id:iri,text:labelOfIri(iri,summary)});
      const controls = [], apply = el('button', 'om-btn primary', '应用选择');apply.type = 'button';
      const reset = el('button', 'om-btn ghost', '重置');reset.type = 'button';
      const clear = el('button', 'om-btn ghost', '清空选择');clear.type = 'button';
      const message = el('p', 'om-type-message');message.hidden = true;message.setAttribute('role','alert');
      function sync() {
        const changed = chosen.size !== baseline.size || [...chosen].some(iri => !baseline.has(iri));
        caption.textContent = chosen.size ? `已选 ${chosen.size} 项 · ` + [...chosen].slice(0,2).map(iri=>labelOfIri(iri,summary)).join('、')
          : kind === 'class' ? '暂无父类 · 选择父类' : '未限定 · 选择类型';
        apply.disabled = busy || !changed;reset.disabled = busy || !changed;clear.disabled = busy || !chosen.size;
        search.disabled = busy;controls.forEach(({input})=>{input.disabled=busy;input.checked=chosen.has(input.value);});
        picker.setAttribute('aria-busy',String(busy));
      }
      choices.forEach(option => {
        const label = el('label', 'om-type-option'), input = el('input');
        input.type = 'checkbox';input.value = option.id;input.checked = chosen.has(option.id);
        input.addEventListener('change', () => {
          input.checked ? chosen.add(option.id) : chosen.delete(option.id);message.hidden=true;sync();
        });
        label.append(input,el('span',null,option.text));list.append(label);controls.push({input,label,text:option.text+' '+option.id});
      });
      search.addEventListener('input', () => {
        const query = search.value.trim().toLocaleLowerCase();let visible=0;
        controls.forEach(item=>{item.label.hidden=!item.text.toLocaleLowerCase().includes(query);if(!item.label.hidden)visible++;});
        empty.hidden=visible>0;
      });
      clear.addEventListener('click',()=>{chosen.clear();sync();});
      reset.addEventListener('click',()=>{chosen=new Set(baseline);message.hidden=true;sync();});
      const project = activeProject();
      apply.addEventListener('click', async () => {
        if (apply.disabled || busy) return;
        if (activeProject()!==project || S().preview || S().selected!==term.id) return;
        const commands = [...baseline].filter(iri=>!chosen.has(iri)).map(value=>({action:removeAction,target_iri:term.id,value}));
        for (const value of chosen) if (!baseline.has(value)) commands.push({action:addAction,target_iri:term.id,value});
        if (!commands.length) return;
        busy=true;message.hidden=true;sync();
        try {
          await model().sendCommand({action:'batch',commands});
          baseline=new Set(chosen);showParents([...baseline]);showEndpoint(addAction,[...baseline]);status('类型选择已写入草案，审核发布后生效。');
        } catch(error) {
          message.textContent=error?.message||'保存失败，请重试';message.hidden=false;
        } finally {busy=false;sync();}
      });
      const actions = el('div','om-type-actions');actions.append(clear,reset,apply);
      picker.append(caption,search,list,empty,actions,message);row.append(picker);sync();
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
        linkRow('主体类型', term.domain, 'add_domain', 'remove_domain'),
        linkRow('客体类型', term.range, 'add_range', 'remove_range'));
    } else if (kind === 'attribute') {
      body.append(
        linkRow('所属类型', term.domain, 'add_domain', 'remove_domain'),
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
    box.append(section('属性', own.length,
      own.map(a => termRow('attr', help().label(a), dtypeLabel(a), a.id)),
      '此类直接声明的属性及数据类型。点击属性名称可查看或编辑。'));

    const rels = relations.filter(r => (r.domain || []).includes(cls.id));
    box.append(section('允许的关系', rels.length,
      rels.map(r => termRow('rel', help().label(r), endpointText(r, summary), r.id)),
      '当前类型作为主体的关系。点击关系名称，可选择主体和客体的类型限制。实例合法性由服务端校验。'));

    box.append(rulesSection(cls));
    // 内联编辑父类（继承）；已发布本体下自动开草案
    box.append(constraintEditSection(cls, 'class', summary));

    const parents = cls.parents || [];
    const children = classes.filter(c => (c.parents || []).includes(cls.id)).map(c => c.id);
    const related = el('div', 'om-detail-sec');
    related.append(detailTitle(`上下位类型（${parents.length + children.length}）`, '当前类型的直接父类与子类。点击名称可定位到对应节点。'));
    if (parents.length || children.length) {
      const ul = el('ul', 'om-detail-rows');
      parents.forEach(p => ul.append(termRow('cls', '父类：' + labelOfIri(p, summary), null, p)));
      children.forEach(c => ul.append(termRow('cls', '子类：' + labelOfIri(c, summary), null, c)));
      related.append(ul);
    } else {
      related.append(el('p', 'om-detail-inline-empty', '（没有父类和子类）'));
    }
    box.append(related);

    box.append(retireSection(cls));
    put(detailHost, box);
  }

  function renderRelation(rel, summary) {
    const box = el('div', 'om-detail-card');
    box.append(detailHead('关系类型', 'rel', rel, rel.id));
    if (rel.description) box.append(el('p', 'om-detail-desc', rel.description));

    if (S().preview) {
      const dom = rel.domain || [], rng = rel.range || [];
      box.append(section('主体类型', dom.length,
        dom.map(d => termRow('cls', labelOfIri(d, summary), null, d)),
        '关系起点的类型（domain）。继承与实例匹配由服务端推理校验。'));
      box.append(section('客体类型', rng.length,
        rng.map(g => termRow('cls', labelOfIri(g, summary), null, g)),
        '关系终点的类型（range）。历史版本保持本版定义。'));
    } else box.append(constraintEditSection(rel, 'relation', summary));

    // 两端为空 = 画布上画不出这条关系线。这里给出"为什么没有"和"怎么补"，
    // 而不是让用户对着一条空关系猜（这正是"关系没办法显示"的直接解法）。
    box.append(endpointsSection(rel, summary));

    box.append(rulesSection(rel));
    box.append(retireSection(rel));
    put(detailHost, box);
  }

  function renderAttr(attr, summary) {
    const box = el('div', 'om-detail-card');
    box.append(detailHead('实体属性', 'attr', attr, attr.id));
    if (attr.description) box.append(el('p', 'om-detail-desc', attr.description));

    const dom = attr.domain || [];
    box.append(section('所属类型', dom.length,
      dom.map(d => termRow('cls', labelOfIri(d, summary), null, d)),
      '此属性所属的实体类型（domain）。'));

    box.append(section('数据类型', 1,
      [termRow('attr', dtypeLabel(attr), help().dtypeOf(attr) || '未限定')],
      '属性值的数据类型，例如文本、整数或日期。在下方选择器中修改，发布后生效。'));

    // 内联改所属类 + 数据类型（约束编辑）
    box.append(constraintEditSection(attr, 'attribute', summary));

    box.append(rulesSection(attr));
    box.append(retireSection(attr));
    put(detailHost, box);
  }

  /* ---- 候选收下 / 忽略（B8）：待收下概念在右栏给两个按钮 --------------------- */
  function renderCandidate(cand) {
    if (S().preview) { put(detailHost, el('p', 'om-detail-note', '历史版本只读，候选决策需回到最新。')); return; }
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
    if (st.reviewGateLoading ?? st.gatesLoading) return { enabled: false, reason: '正在读取审核条件，画布可以先浏览…' };
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
    if (st.gatesLoading) return { enabled: false, reason: '正在计算发布校验结果…' };
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
    const candidateCounts = disc && disc.candidate_status_counts;
    const pending = candidateCounts ? (candidateCounts.pending || 0) : (disc ? (disc.unpublished_candidate_count || 0) : 0);
    const included = candidateCounts && candidateCounts.included_in_draft || 0;
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
    if (pending > 0) {
      const btn = el('button', 'om-btn primary', '选择候选并预览');
      btn.type = 'button';
      btn.disabled=!!(st.preview||st.loading);
      btn.title = '选择候选、核对来源并只读预览；明确目标并确认后才加入工作草案';
      btn.addEventListener('click', () => {
        const M = model();
        if (M && M.openIntake) M.openIntake();
      });
      actions.append(btn);
    }
    bar.append(actions);
    bar.append(el('div', 'om-ab-note', pending > 0
      ? '打开清单和预览不会创建草案。确认加入本轮后，可继续手动建模、发现父类、审核与发布。'
      : (included ? `${included} 个候选已纳入草案，请加载草案后校验、审核并发布。`
        : '当前没有待处理候选，可使用顶部「新增定义」手动建模，或在资料库添加文件做开放抽取。')));
    put(actionsHost, bar);
  }

  function renderActions(state) {
    if (!actionsHost) return;
    const st = state || S();
    const localTask=currentAction(st),busy=!!localTask;
    actionsHost.setAttribute('aria-busy',String(busy));
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
    const candidateCounts = disc && disc.candidate_status_counts;
    const pendingCandidates = candidateCounts ? (candidateCounts.pending || 0)
      : (disc ? (disc.unpublished_candidate_count || 0) : (candidates.length || 0));
    const includedCandidates = candidateCounts && candidateCounts.included_in_draft || 0;
    const draft = st.draft || null;
    const ops = (draft && draft.operations) || [];
    // 「冲突」= 草案里与基线冲突、需重新基线的变更（真实字段：operation.validation.rebase_status）
    const conflicts = ops.filter(o => ((o.validation || {}).rebase_status === 'conflict')).length;
    const readiness = st.readiness || null;
    // 「阻断」= 服务端发布清单里 error 级的条目（读不到就显示未知，不编）
    const blockers = (readiness && readiness.items)
      ? readiness.items.filter(i => i.severity === 'error').length : null;
    const onlyWorkflow=blockers>0&&(readiness.items||[]).filter(item=>item.severity==='error').every(item=>workflowBlockers.has(item.code));

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
      chip('实体类型', summary ? classes.length : null, 'cls', '当前画布中的实体类型定义数量，不是实体事实条数'),
      chip('关系类型', summary ? rels.length : null, 'rel', '当前画布中的关系类型定义数量，不是关系事实条数'),
      chip('属性', summary ? attrs.length : null, 'attr', '当前本体的实体属性数量'),
      chip('待生成', pendingCandidates, 'pending', '新抽出、尚未纳入本体草案的候选'),
      ...(includedCandidates ? [chip('草案中', includedCandidates, 'pending', '已纳入草案，等待审核发布的候选')] : []),
      chip('冲突', draft ? conflicts : 0, 'conflict', '草案里与基线冲突、需要重新基线的变更数'),
      chip(onlyWorkflow?'发布待办':'发布阻断', blockers, onlyWorkflow?'workflow':'block', '下方逐项列出服务端发布条件及下一步；读不到显示 —'),
    );
    const associated=draft?.associated_knowledge;
    const effects=draft?.source_context?.publication_effects;
    const bindings=draft?.source_context?.intake_bindings||[];
    const boundIds=bindings.every(item=>Array.isArray(item.selected_candidate_ids))
      ? new Set(bindings.flatMap(item=>item.selected_candidate_ids)).size : null;
    const linked=associated?.selected??effects?.summary?.candidate_count??(bindings.length?boundIds:effects||associated?null:0);
    counts.append(chip('关联候选事实',linked,'','明确加入本轮的实体、关系或属性事实条数；定义数与事实数分别计量'));
    bar.append(counts);
    const kinds=associated?.by_kind;
    const knownKinds=kinds&&['entity','relation','attribute'].every(kind=>Number.isInteger(kinds[kind])&&kinds[kind]>=0)
      &&kinds.entity+kinds.relation+kinds.attribute===linked;
    const kindValue=kind=>linked===0?0:knownKinds?kinds[kind]:'未提供';
    bar.append(el('p','om-ab-fact-kinds',knownKinds||linked===0
      ?`关联候选事实明细：实体 ${kindValue('entity')} · 关系 ${kindValue('relation')} · 属性 ${kindValue('attribute')}。`
      :`${linked===null?'候选事实数量尚未读取，请重读草案核对。':'候选事实是从原文抽出的实体、关系或属性陈述，不是段落或定义数。'}种类明细可从「待完善结构」进入候选核对页查看。`));
    if(draft){
      const technical=el('details','om-ab-change-detail');technical.append(el('summary',null,`草案明细 · ${ops.length} 项底层操作`),
        el('p',null,'一个定义可能包含创建、名称说明、继承等多项底层操作；操作数不等于定义数，也不等于关联候选事实数。'));
      bar.append(technical);
    }
    if(associated?.per_batch?.length){
      const scope=el('details','om-ab-linked-scope');scope.append(el('summary',null,'同时处理的关联知识 · 按来源运行核对'));
      associated.per_batch.forEach((batch,index)=>{const entry=el('details');entry.append(el('summary',null,`第 ${index+1} 次候选加入 · 明确选中 ${batch.selected??'未提供'} 条`),el('pre',null,JSON.stringify(batch,null,2)));scope.append(entry);});
      bar.append(scope);
    }
    renderReadinessItems(st,bar);
    if(localTask){const progress=el('p','om-ab-progress',localTask.message+(localTask.slow?' 仍在处理，请勿重复点击；收到结果后会自动更新。':''));progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');bar.append(progress);}

    const actions = el('div', 'om-ab-actions');
    // 历史版本是只读预览：底栏的动作也要锁住，否则用户会在"看 v1"的状态下点发布，
    // 以为发布的是 v1（实际发的是手上那份草案）。锁住 + 说明去哪解锁。
    const previewing = !!st.preview;
    const previewLock = '正在查看历史版本（只读）。点顶栏「回到最新」退出预览，'
      + '或用「版本管理」→「回到这一版」以它为基础开一份新草案。';
    const reviewGate = gateReview(st);
    const submitted=!!draft?.submitted_at&&!!draft?.validation_fingerprint;
    const reviewPlan=st.reviewPlan?.revision===draft?.revision?st.reviewPlan:null;
    const bulkCount=reviewPlan?(reviewPlan.items||[]).filter(item=>!item.decision&&item.auto_approvable===true).length:null;
    const btnReview = el('button', 'om-btn', localTask?.kind==='review'?localTask.message:submitted?
      bulkCount===null?'读取批量审核条件…':`批量批准（${bulkCount} 项变更）`:'校验并审核');
    btnReview.type = 'button';
    btnReview.setAttribute('aria-busy',String(localTask?.kind==='review'));
    btnReview.disabled = !!(previewing || busy || !reviewGate.enabled || (submitted&&(bulkCount===null||bulkCount===0||st.reviewPlanLoading)));
    btnReview.title = previewing ? previewLock
      : (reviewGate.enabled
        ? '由服务端选择符合条件的变更批量批准；高风险、警告或阻断项在「按定义审核」中核对。'
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
    const btnPub = el('button', 'om-btn primary',localTask?.kind==='publish'?localTask.message:
      versions.loaded
        ? `发布为 v${nextVersion}${annotationOnly ? '（仅标注 · 版本号不变）' : ''}`
        : '发布新版本');
    // title 三态交给下面的 syncPub()：门禁没过时说为什么灰着；门禁过了但没写发布说明，
    // 就直说"先写说明"，而不是让用户对着一个不动的灰色按钮猜。
    btnPub.type = 'button';
    btnPub.setAttribute('aria-busy',String(localTask?.kind==='publish'));
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
      noteInput.disabled = previewing || busy;
      btnPub.disabled = previewing || busy || !!pendingPublication() || !(pubGate.enabled && hasNote && warnOk);
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
        tick.disabled=busy||previewing;
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

    // 草案过期（服务端推导 needs_rebase）：两类 ——
    //   * stale_base：草案基线落后于项目当前本体，发布被 base_outdated 硬阻断；
    //   * stale_source：discovery/candidate 草案的来源快照变了，发布被 stale_source 硬阻断。
    // 「校验并审核」「发布」此刻都灰着。补一个主按钮作为唯一出路 —— 没有它，用户只能
    // "回到最新重开草案"（等于丢掉已做的停用等变更）。这正是操作手册第 9 章标注的
    // "发布被阻断、界面无该按钮"卡点的修复。两类都调同一个 /rebase 接口，只是文案不同。
    const rebaseKind = (st.stageAvailability && st.stageAvailability.needs_rebase) || null;
    const needsRebase = rebaseKind === 'stale_base' || rebaseKind === 'stale_source';
    if (needsRebase && !previewing) {
      const isSource = rebaseKind === 'stale_source';
      const rebaseLabel = isSource ? '刷新来源' : '重新基线';
      const rebaseWhy = isSource
        ? '这份草案的来源快照已经变了：要先把来源快照刷新到最新，才能继续「校验并审核 → 发布」。'
        : '这份草案的基线已经不是项目当前本体：要先把草案对齐到最新已发布版本，才能继续「校验并审核 → 发布」。';
      const rebaseBox = el('div', 'om-ab-rebase');
      rebaseBox.append(el('p', 'om-ab-rebase-title', rebaseWhy));
      const btnRebase = el('button', 'om-btn primary', rebaseLabel);
      btnRebase.type = 'button';
      btnRebase.disabled=busy;
      btnRebase.title = isSource
        ? '做什么：重新读取来源快照（来源文档/候选变了才需要），把草案操作重新对齐到新来源。不做什么：不发布、不改生效本体——刷新后要重新「校验并审核」。'
        : '做什么：把草案基线对齐到项目当前本体，保留已做的变更（与基线冲突的变更会被标记出来）。不做什么：不发布、不改生效本体——重新基线后要重新「校验并审核」。';
      btnRebase.addEventListener('click', rebaseDraft);
      rebaseBox.append(btnRebase);
      actions.append(rebaseBox);
    }

    // 操作区：发布说明 + 校验并审核 + 发布，包成一行（.om-ab-ops 不换行，避免按钮被挤到两行）。
    const opRow = el('div', 'om-ab-ops');
    const manualReview = el('button', 'om-btn', '按定义审核');
    manualReview.type = 'button';
    manualReview.disabled = previewing || busy || !draft || !draft.submitted_at || draft.status !== 'pending';
    manualReview.title = '按类型、关系或属性集中核对，支持批准并查看下一项；先提交当前草案。';
    manualReview.addEventListener('click', openManualReview);
    opRow.append(btnReview, manualReview, noteInput, btnPub);
    const attempt=pendingPublication();
    if(attempt&&attempt.phase!=='sending'){
      const recovery=el('div','om-publication-recovery');
      recovery.append(el('p',null,'发布结果尚未确认。原发布说明：'+attempt.body.note));
      const check=el('button','om-btn','核对发布结果');check.type='button';check.disabled=busy||previewing;
      check.addEventListener('click',()=>reconcilePublication(attempt));recovery.append(check);
      if(attempt.phase==='retryable'){
        const retry=el('button','om-btn','重试原发布请求');retry.type='button';retry.disabled=busy||previewing;
        retry.addEventListener('click',()=>retryPublication(attempt));recovery.append(retry);
      }
      bar.append(recovery);
    }
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
      if (st.reviewGateLoading ?? st.gatesLoading) msgs.push('正在读取审核条件，草案可继续编辑…');
      else if (!st.stageAvailability) msgs.push('读不到服务端阶段门禁（stage-availability）——按未判定处理，按钮不猜可用性。');
      else if (!reviewGate.enabled && reviewGate.reason) msgs.push(reviewGate.reason);
      if (!pubGate.enabled && pubGate.reason) msgs.push(pubGate.reason);
    }
    const note = el('div', 'om-ab-note', msgs.join(' '));
    note.hidden = !msgs.length;
    bar.append(note);
    if(st.gateLoadError||st.reviewLoadError){
      const retry=el('button','om-btn','重试读取审核条件');retry.type='button';
      retry.addEventListener('click',async()=>{retry.disabled=true;try{await model().refreshDraftState();}finally{renderActions();}});
      const problem=el('div','om-ab-note',st.reviewLoadError||st.gateLoadError);problem.append(retry);bar.append(problem);
    }

    put(actionsHost, bar);

    // 版本号是异步的：只在没有缓存时取一次，拿到后重画一次底栏（ensureVersions 自带去重，不会成环）
    ensureVersions().then(changed => { if (changed && actionsHost) renderActions(); });
  }

  /* ---- 「重新基线 / 刷新来源」：POST /{draft_id}/rebase（草案过期时对齐到最新本体/来源） ---- */
  // 什么时候出现：服务端 stage-availability 返回 needs_rebase ——
  //   * 'stale_base'：草案基线落后于项目当前本体（发布被 base_outdated 硬阻断）；
  //   * 'stale_source'：discovery/candidate 草案的来源快照变了（发布被 stale_source 硬阻断）。
  // 两类都调同一个 /rebase 接口（服务端 rebase() 对 stale_source 会刷新来源上下文并重新对齐操作），
  // 只是界面文案不同。这是单画布此前缺失的唯一前端入口（操作手册第 9 章如实标注的已知卡点）。
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
    const isSource = M.S.stageAvailability
      && M.S.stageAvailability.needs_rebase === 'stale_source';
    const id = M.S.draft.id;
    const base = `/ontology-drafts/${encodeURIComponent(id)}`;
    try {
      status(isSource ? '正在刷新来源快照…' : '正在把草案基线对齐到项目当前本体（保留你已做的变更）…');
      const result = await draftApi(endpoint(base + '/rebase'), {
        expected_revision: M.S.draft.revision,
        expected_ontology_id: ontologyId,
      }, 'POST');
      const rebaseInfo = (result && result.rebase) || [];
      const clean = rebaseInfo.filter(r => r.classification === 'clean').length;
      const conflict = rebaseInfo.filter(r => r.classification === 'conflict').length;
      if (conflict) {
        status(`已${isSource ? '刷新来源' : '重新基线'}：${clean} 条变更在新来源/基线上仍然成立，${conflict} 条冲突需重新处理；请重新「校验并审核」。`);
      } else if (isSource) {
        status('已刷新来源快照；请重新「校验并审核」再发布。');
      } else {
        status('已重新基线到项目当前本体；请重新「校验并审核」再发布。');
      }
    } catch (error) {
      status(error && error.message ? error.message : (isSource ? '刷新来源失败' : '重新基线失败'), true);
    }
    if (typeof M.refresh === 'function') await M.refresh();
  }

  async function openManualReview() {
    const M=model();if(!M?.S.draft||M.S.preview)return;
    if(!window.OntologyReview){status('审核工作区加载失败，请刷新页面。',true);return;}
    document.querySelector('.om-definition-review')?.close();
    const project=activeProject(),id=M.S.draft.id,base=endpoint(`/ontology-drafts/${encodeURIComponent(id)}`);
    const stageRoute=endpoint(`/ontology-drafts/stage-availability?draft_id=${encodeURIComponent(id)}`);
    window.OntologyReview.open({
      host:document.getElementById('tab-ontology-model')||document.body,
      getContext:()=>({projectId:activeProject(),draftId:M.S.draft?.id,revision:M.S.draft?.revision,preview:!!M.S.preview}),
      request:(route,body,method)=>(route===''||route.startsWith('/decisions/')?draftApi:api)(route==='/stage-availability'?stageRoute:base+route,body,method),
      onSaved:draft=>{
        if(activeProject()!==project||M.S.draft?.id!==id||M.S.preview)return;
        const changed=M.S.draft.revision!==draft.revision;M.S.draft=draft;
        // Decisions do not change the drawn ontology; update action gates only.
        if(draft.status!=='pending')void M.refresh().catch(error=>{
          if(activeProject()===project)status('审核决定已保存，页面刷新失败，请重新读取：'+error.message,true);
        });
        else if(changed)void M.refreshDraftState().catch(()=>{});
      },
    });
  }
  /* ---- 「校验并审核」：validate → submit → batch-approve ------------------- */
  async function validateAndReview() {
    const M = model();
    if (!M) return;
    let draft = M.S && M.S.draft;
    if (!draft || !draft.id) { status('还没有草案：先在画布上新增或调整术语，草案会自动建立。', true); return; }
    if(M.S.preview)return;
    const task=beginAction('review',draft,draft.submitted_at?'正在批量批准变更…':'正在校验草案…');if(!task)return;
    const id = draft.id;
    const project=activeProject(),revision=draft.revision,projectBase=endpoint('');
    const stillCurrent=()=>activeProject()===project&&M.S.draft?.id===id&&M.S.draft.revision===revision&&!M.S.preview;
    const assertCurrent=()=>{if(!stillCurrent())throw new Error('项目、草案或修订已变化，已停止后续审核步骤，请重新核对。');};
    const base = `/ontology-drafts/${encodeURIComponent(id)}`;
    try {
      if(!draft.submitted_at||!draft.validation_fingerprint){
        actionPhase(task,'正在校验草案…');
        draft = await draftApi(projectBase+base+'/validate', { expected_revision: draft.revision }, 'POST');assertCurrent();
        actionPhase(task,'正在提交审核…');
        draft = await draftApi(projectBase+base+'/submit', { expected_revision: draft.revision }, 'POST');assertCurrent();
      }
      actionPhase(task,'正在批量批准变更…');
      // 一键批准的集合由服务端 review_plan 决定，前端只表达意图（契约 §3.2）。
      // expected_ontology_id = 项目当前最新本体 id（主控从 GET /ontology 的 id 取到 S.ontologyId），
      // 服务端 _check_current 会拿它与 draft.base_ontology_id 比对，不一致即 StaleBase。别自己编。
      const result = await draftApi(projectBase+base+'/decisions/batch-approve', {
        expected_revision: draft.revision,
        expected_ontology_id: (M.S && M.S.ontologyId) || draft.base_ontology_id,
        validation_fingerprint: draft.validation_fingerprint,
        acknowledged_warning_codes: [],
      }, 'POST');
      assertCurrent();
      const updated = (result && result.draft) || draft;
      const batch = (result && result.batch) || {};
      if (M.S) M.S.draft = updated;
      if (batch.approved) {
        status(`已批量批准 ${batch.approved} 项变更；其余 ${batch.skipped || 0} 项可进入「按定义审核」集中核对。审核后仍需发布草案。`);
      } else {
        status(`没有可批量批准的变更；${batch.skipped || 0} 项可进入「按定义审核」查看风险、警告和处理要求。`);
      }
    } catch (error) {
      if(actionVisible(task))status(error && error.message ? error.message : '校验并审核失败', true);
    } finally {
      try{if(actionVisible(task)&&typeof M.refresh==='function')await M.refresh();}
      catch(error){if(actionVisible(task))status('审核结果刷新失败，请重新读取草案核对：'+error.message,true);}
      finally{finishAction(task);}
    }
  }

  /* ---- 「发布」：POST /{draft_id}/publish（要指纹 + 幂等键 + 说明） --------- */
  const publicationAttempts=new Map();
  const draftApi=(...args)=>window.OntologyDraftData?window.OntologyDraftData.request(...args):api(...args);
  function pendingPublication(){return publicationAttempts.get(actionKey(activeProject(),S().draft?.id));}
  function publicationSummary(version){
    const meta=version.metadata||{},facts=meta.publication?.receipt?.facts;
    return `已发布为 ${versionLabel(version.version??meta.version)}。发布时间：${fmtTime(version.created_at)}。`
      +(facts?`知识新增 ${facts.added??'待核对'} / 复用 ${facts.reused??'待核对'} / 跳过 ${facts.skipped??'待核对'}。`:'')
      +'正式版本已生效，页面同步单独进行。';
  }
  function completePublication(attempt,version,M){
    if(!version?.id)throw Error('发布响应缺少版本身份，请先核对结果。');
    attempt.phase='confirmed';publicationAttempts.delete(attempt.key);
    if(publishKey.key===attempt.body.idempotency_key)publishKey={sig:'',key:''};
    if(activeProject()!==attempt.project)return;
    const meta=version.metadata||{};
    const listed={...version,version:version.version??meta.version,version_reused:version.version_reused??meta.version_reused,
      note:version.note??meta.publication?.note??attempt.body.note,actor:version.actor??meta.publication?.actor};
    if(versions.key===attempt.project){const index=versions.list.findIndex(item=>item.id===version.id);if(index<0)versions.list.push(listed);else versions.list[index]=listed;}
    const matches=M.S.draft?.id===attempt.draftId&&M.S.draft?.revision===attempt.revision&&!M.S.preview;
    const broadcast=()=>document.dispatchEvent(new CustomEvent('ontology-model:published',{detail:{versionId:version.id,projectId:attempt.project,draftId:attempt.draftId}}));
    if(!matches){broadcast();return;}
    publishNote='';
    if(typeof M.acceptPublication==='function'){
      const receipt=M.acceptPublication(version,{projectId:attempt.project,draftId:attempt.draftId,revision:attempt.revision});
      if(receipt)void M.refreshPublished(receipt);
    }else{
      M.S.draft={...M.S.draft,status:'accepted',published_ontology_id:version.id};
      if(typeof M.refresh==='function')void M.refresh().catch(error=>{
        if(activeProject()===attempt.project)status('版本已发布，页面同步失败，可重新读取：'+error.message,true);
      });
    }
    status(publicationSummary(version));
    broadcast();
  }
  async function sendPublication(attempt,M,task){
    attempt.phase='sending';actionPhase(task,'正在校验并保存正式版本…');
    try{
      const version=await api(attempt.base+'/publish',attempt.body,'POST');
      completePublication(attempt,version,M);
    }catch(error){
      const definite=error.status>=400&&error.status<500&&error.status!==408;
      if(definite)publicationAttempts.delete(attempt.key);else attempt.phase='uncertain';
      if(actionVisible(task))status(definite?(error.message||'发布请求被拒绝'):
        '发布结果尚未确认：'+(error.message||error)+'。请先核对发布结果，勿重复提交。',true);
    }
  }
  async function reconcilePublication(attempt){
    const M=model();if(!M||activeProject()!==attempt.project||S().draft?.id!==attempt.draftId||S().preview)return;
    const task=beginAction('publish',S().draft,'正在核对发布结果…');if(!task)return;
    try{
      const fresh=await api(attempt.base+'?view=canvas',undefined,'GET');
      if(!actionVisible(task)||S().preview)return;
      if(fresh.status==='accepted'&&fresh.published_ontology_id){
        const version=await api(attempt.projectBase+'/ontology?ontology_id='+encodeURIComponent(fresh.published_ontology_id),undefined,'GET');
        if(!actionVisible(task)||S().preview)return;
        completePublication(attempt,version,M);
      }else if(fresh.status!=='pending'||fresh.revision!==attempt.revision){
        publicationAttempts.delete(attempt.key);
        status('草案状态或修订已变化，原发布请求已失效。请刷新草案并重新核对。',true);
      }else{attempt.phase='retryable';status('尚未查到已完成的发布记录；可重试原发布请求，使用原说明和同一请求身份。');}
    }catch(error){attempt.phase='uncertain';if(actionVisible(task))status('无法核对发布结果，请稍后重试读取：'+error.message,true);}
    finally{finishAction(task);}
  }
  async function retryPublication(attempt){
    const M=model();if(!M||attempt.phase!=='retryable'||activeProject()!==attempt.project||S().draft?.id!==attempt.draftId||S().preview)return;
    const task=beginAction('publish',S().draft,'正在重试原发布请求…');if(!task)return;
    try{await sendPublication(attempt,M,task);}finally{finishAction(task);}
  }
  async function publishDraft() {
    const M=model(),draft=M?.S.draft;if(!draft?.id){status('还没有可发布的草案。',true);return;}
    const note=String(publishNote||'').trim();if(!note){status('先写发布说明再发布：版本记录要能回答这一版改了什么、为什么发。',true);return;}
    if(M.S.preview||pendingPublication())return;
    const task=beginAction('publish',draft,'正在确认发布条件…');if(!task)return;
    const project=activeProject(),id=draft.id,revision=draft.revision,projectBase=endpoint(''),base=projectBase+`/ontology-drafts/${encodeURIComponent(id)}`;
    const stillCurrent=()=>activeProject()===project&&M.S.draft?.id===id&&M.S.draft.revision===revision&&!M.S.preview;
    try{
      const readiness=await api(base+'/publish-readiness',undefined,'GET');
      if(!stillCurrent())throw Error('项目、草案或修订已变化，请重新核对发布。');
      if(readiness.ready!==true){M.S.readiness=readiness;const blocker=(readiness.items||[]).find(item=>item.severity==='error');throw Error('还不能发布：'+(blocker?.message||blocker?.code||'服务端判定未就绪'));}
      if(readiness.revision!=null&&readiness.revision!==revision)throw Error('草案修订已变化，请重新核对审核与发布。');
      if(readiness.validation_fingerprint&&draft.validation_fingerprint&&readiness.validation_fingerprint!==draft.validation_fingerprint)throw Error('校验快照已变化，请重新校验并核对。');
      const fingerprint=readiness.validation_fingerprint||draft.validation_fingerprint;
      const key=nextIdempotencyKey(`${id}@${fingerprint||revision}@${note}`);
      const attempt={key:actionKey(project,id),project,projectBase,base,draftId:id,revision,phase:'sending',body:{
        expected_revision:revision,expected_ontology_id:M.S.ontologyId||draft.base_ontology_id,
        validation_fingerprint:fingerprint,acknowledged_warning_codes:[...new Set([
          ...(draft.validation_report?.warnings||[]).map(w=>w.code).filter(Boolean),...ackWarnings.codes])],idempotency_key:key,note}};
      publicationAttempts.set(attempt.key,attempt);await sendPublication(attempt,M,task);
    }catch(error){if(actionVisible(task))status(error.message||'无法核对发布条件',true);}
    finally{finishAction(task);}
  }
  /* ==========================================================================
   * 版本管理（B6）：/ontologies 版本列表 + 发布记录 + 查看/回到某一版
   * ======================================================================== */
  function syncHistoryProject() {
    const project = activeProject();
    if (historyProject !== project) {
      historyProject = project;
      ++versionsRequest;
      Object.assign(versions, { key: '', list: [], loaded: false, loading: false, promise: null });
      ui.selectedVersion = '';
      closeHistory();
    }
    if (drawer && !drawer.overlay.isConnected) {
      drawer = null;
      ui.historyOpen = false;
    }
    return project;
  }

  async function ensureVersions(force) {
    const project = syncHistoryProject();
    if (!project) { versions.list = []; versions.loaded = false; versions.key = ''; return false; }
    if (!force && versions.key === project && versions.loaded) return false;
    // 已有一份在途请求就复用它：调用方（底栏/抽屉渲染很频繁）不会并发打请求，
    // 而且 openHistory 一定能等到这次结果，不会因为"正在读"就永远停在空抽屉。
    if (versions.loading && versions.promise) return versions.promise;
    const request = ++versionsRequest;
    versions.loading = true;
    versions.promise = (async () => {
      try {
        const res = await api(endpoint('/ontologies'), undefined, 'GET');
        if (activeProject() !== project || request !== versionsRequest) return false;
        versions.list = (res && res.versions) || [];   // 返回键是 versions（见 ontology-archive.js）
        versions.key = project;
        versions.loaded = true;
        return true;
      } catch (error) {
        if (request === versionsRequest) versions.loaded = false;
        return false;
      } finally {
        if (request === versionsRequest) {
          versions.loading = false;
          versions.promise = null;
        }
      }
    })();
    return versions.promise;
  }

  function ensureDrawer() {
    syncHistoryProject();
    if (drawer && drawer.overlay.isConnected) return drawer;
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
      const label = versionLabel(v.version)
        + (hasVersionCollision(v.version) ? ` · ${shortId(v.id)}` : '');
      row.append(el('b', null, label));
      if (v.version_reused) row.append(el('span', 'om-history-reused', '标注修订'));
      row.append(el('span', 'om-history-time', fmtTime(v.created_at)));
      if (v.actor) row.append(el('span', 'om-history-actor', v.actor));
      const sum = v.summary || {};
      row.append(el('span', 'om-history-counts',
        `${(sum.classes || []).length} 类 · ${(sum.relations || []).length} 关系 · ${(sum.attributes || []).length} 属性`));
      if (index === versions.list.length - 1) row.append(el('span', 'om-history-cur', '正在生效'));
      if (st.preview && st.preview.id === v.id) row.append(el('span', 'om-history-viewing', '正在查看'));
      row.addEventListener('click', () => { ++historyOpening; ui.selectedVersion = v.id; renderHistory(); });
      li.append(row);
      // 列表行直接带上发布说明：版本列表的第一用途就是"扫一眼每版为什么发"，
      // 点开才看得到的话，一屏版本多起来就没人愿意看了。
      if (v.note) li.append(el('p', 'om-history-line-note', v.note));
      if (v.id === ui.selectedVersion) li.append(renderVersionDetail(v, index));
      list.append(li);
    });
    put(body, list);
  }

  function loadKnowledgeContext(host, v, isCurrent=()=>true) {
    host.dataset.state = 'loading';
    put(host, el('p', 'om-history-note', '正在读取关联知识…'));
    api(endpoint(`/ontologies/${encodeURIComponent(v.id)}/knowledge-context`), undefined, 'GET')
      .then(data => {
        if (!host.isConnected||!isCurrent()) return;
        host.dataset.state = 'ready';
        const heading = el('div', 'om-knowledge-context-head');
        heading.append(el('b', null, '关联知识'));
        heading.append(el('span', null, data.ontology && data.ontology.is_current
          ? '当前本体' : '历史本体'));
        const grid = el('div', 'om-knowledge-context-grid');
        const metric = (label, value, hint) => {
          const item = el('div', 'om-knowledge-context-metric');
          item.append(el('span', null, label), el('b', null, value));
          if (hint) item.append(el('small', null, hint));
          return item;
        };
        const current = data.current_bound || {};
        const kinds = current.by_kind || {};
        const migration = data.migration || {};
        const history = data.history || {};
        grid.append(
          metric('当前关联', `${current.total ?? '—'} 条`,
            `实体 ${kinds.entity ?? '—'} · 关系 ${kinds.relation ?? '—'} · 属性 ${kinds.attribute ?? '—'}`),
          metric('待迁移', migration.pending ?? '—',
            migration.pending == null ? '迁移状态未知' : '可迁移知识'),
          metric('迁移阻塞', migration.blocked ?? '—',
            migration.blocked == null ? '迁移状态未知' : '需先处理冲突'),
          metric('历史修订', history.revision_count ?? '—',
            `涉及知识 ${history.record_count ?? '—'} · 已迁出 ${history.migrated_away ?? '—'}`),
        );
        const ledger = el('button', 'om-btn om-knowledge-ledger-link', '在知识台账查看');
        ledger.type = 'button';
        ledger.addEventListener('click', () => {
          wb.setVersionContext({
            ontologyScope: 'ids', ontologyIds: [v.id],
            ontologyVersion: v.version ?? null, source: 'ontology-history',
          });
          closeHistory();
          showTab('records');
        });
        put(host, heading, grid, ledger);
      })
      .catch(() => {
        if (!host.isConnected||!isCurrent()) return;
        host.dataset.state = 'error';
        const title = el('b', null, '读不到关联知识');
        const empty = el('p', 'om-history-note',
          '当前关联 — · 待迁移 — · 迁移阻塞 — · 历史修订 —');
        const retry=el('button','om-btn','重试读取关联知识');retry.type='button';
        retry.addEventListener('click',()=>{if(isCurrent())loadKnowledgeContext(host,v,isCurrent);});
        put(host, title, empty,retry);
      });
  }

  function renderVersionDetail(v, index) {
    const box = el('div', 'om-history-detail');
    const project = activeProject(), opened = drawer, opening = historyOpening, controller = model();
    const sum = v.summary || {};
    const pub = (v.metadata && v.metadata.publication) || {};
    const stillCurrent=()=>activeProject()===project&&drawer===opened&&historyOpening===opening&&ui.historyOpen&&ui.selectedVersion===v.id&&box.isConnected;
    const technical=el('details','om-history-technical');technical.append(el('summary',null,'版本身份与技术信息'));
    const facts = el('div', 'om-history-facts');
    const fact = (k, value) => {
      const f = el('div', 'om-history-fact');
      f.append(el('span', null, k));
      f.append(el('b', null, value === undefined || value === null ? '—' : String(value)));
      return f;
    };
    facts.append(fact('版本', versionLabel(v.version)
      + (v.version_reused ? ' · 标注修订' : '')));
    technical.append(fact('版本 ID', shortId(v.id)));
    facts.append(fact('发布时间', fmtTime(v.created_at)));
    const writePath = pub.write_path || (v.metadata && v.metadata.write_path);
    if (writePath) technical.append(fact('写入路径', writePath));
    const actor = v.actor || pub.actor;
    if (actor) facts.append(fact('发布人', actor));
    if (pub.source_kind) technical.append(fact('来源', pub.source_kind));
    technical.append(fact('规模', `${sum.triples || 0} 三元组`));
    box.append(facts);

    const source = el('div', 'om-history-source-draft');
    if (pub.draft_id) {
      const sourceFact = fact('来源草案', shortId(pub.draft_id));
      sourceFact.title = String(pub.draft_id);
      const sourceBtn = el('button', 'om-btn', '查看来源草案');
      sourceBtn.type = 'button';
      sourceBtn.addEventListener('click', () => {
        if (activeProject() !== project || model() !== controller || drawer !== opened
          || historyOpening !== opening || !ui.historyOpen || ui.selectedVersion !== v.id
          || !box.isConnected || !opened?.overlay.isConnected) return;
        if (typeof controller?.openDraftHistory !== 'function') {
          status('主控未就绪，暂时无法查看来源草案。', true); return;
        }
        closeHistory();
        return controller.openDraftHistory(pub.draft_id);
      });
      technical.append(sourceFact);source.append(el('span',null,'来源草案'),sourceBtn);
    } else source.append(el('p', 'om-history-note', '未记录来源草案'));
    box.append(source);

    // 发布说明：版本管理的主角。老版本（本功能之前发布的）没有说明，如实说"没有"，
    // 不补一句编出来的话。
    const note = el('p', 'om-history-pub-note');
    note.append(el('b', null, '发布说明：'));
    note.append(el('span', null, v.note || '（这一版发布于发布说明功能上线之前，没有留下说明）'));
    box.append(note);
    const primaryActions=el('div','om-history-actions');box.append(primaryActions);

    if(pub.receipt){
      const receipt=pub.receipt,scope=el('section','om-publication-receipt');
      const value=n=>n==null?'待核对':String(n),actual=receipt.facts||{};
      scope.append(el('h4',null,'本次发布实际处理的关联知识'),
        el('p',null,`明确选中 ${value(actual.selected)} 条 · 实际新增 ${value(actual.added)} / 复用 ${value(actual.reused)} / 跳过 ${value(actual.skipped)}`));
      const reasonLabels={materialized:'已写入正式知识',ontology_validation_failed:'未通过最终结构校验',
        required_operation_rejected:'依赖的结构操作未获批',required_operation_superseded:'依赖的结构操作已被替换',
        required_operation_missing:'依赖的结构操作已缺失',required_operation_withdrawn:'依赖的结构操作已撤销',draft_closed:'草案已关闭'};
      for(const [batchNumber,batch] of (actual.per_batch||[]).entries()){
        const section=el('details','om-publication-batch');let rendered=false;
        section.append(el('summary',null,`来源批次 ${batchNumber+1} · 选中 ${value(batch.selected)} 条 · 新增 ${value(batch.added)} / 复用 ${value(batch.reused)} / 跳过 ${value(batch.skipped)}`));
        section.addEventListener('toggle',()=>{
          if(!section.open||rendered||!stillCurrent())return;rendered=true;
          const outcomes=el('div','om-publication-outcomes');
          outcomes.append(el('p','om-history-note','来源运行：'+(batch.run_id||batch.kind||'未记录身份')));
          for(const outcome of batch.candidate_outcomes||[]){
          const status={materialized:'已处理',skipped:'已跳过',deferred:'暂缓'}[outcome.status]||outcome.status||'未记录状态';
          const reason=outcome.message||outcome.reason||reasonLabels[outcome.reason_code]||'未提供诊断说明';
            outcomes.append(el('p','om-publication-outcome',`${outcome.candidate_id} · ${status} · ${reason}${outcome.reason_code?' ['+outcome.reason_code+']':''}`));
          }
          if(!(batch.candidate_outcomes||[]).length&&batch.selected)outcomes.append(el('p','om-history-note','这份回执未提供逐条候选诊断。'));
          section.append(outcomes);
        });
        scope.append(section);
      }
      if(actual.selected===0)scope.append(el('p','om-history-note','本次仅处理结构，没有关联候选事实。'));
      const index=receipt.index||{};
      technical.append(el('p','om-history-note',`发布回执记录的索引状态：${index.status||'未记录'}${index.task_id?' · '+index.task_id:''}。这是发布时的快照，当前进度请在构建与任务中核对。`));
      box.append(scope);
    }

    const top = (sum.classes || []).slice(0, 8)
      .map(c => c.label_zh || c.label || c.name || c.id);
    if (top.length) technical.append(el('p', 'om-history-note', `实体类（前 ${top.length} 个）：${top.join('、')}`));

    const knowledge = el('div', 'om-knowledge-context');
    knowledge.setAttribute('role', 'region');
    knowledge.setAttribute('aria-label', '关联知识');
    const knowledgeDetails=el('details','om-history-knowledge');knowledgeDetails.append(el('summary',null,'当前关联知识与迁移状态'),knowledge);box.append(knowledgeDetails,technical);
    knowledgeDetails.addEventListener('toggle',()=>{
      if(knowledgeDetails.open&&!knowledge.dataset.state&&stillCurrent())loadKnowledgeContext(knowledge,v,stillCurrent);
    });

    // 两个动作，语义必须分清（这是版本管理唯一容易出事的地方）：
    //   查看这一版 = 只读预览，不写任何数据；
    //   回到这一版 = 以它为基线开一份**新草案** → 审核 → 发布成新版本。历史不改写。
    const st = S();
    const actions = primaryActions;
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
      await M.previewVersion(v.id, v.version);
      status(`正在查看 ${versionLabel(v.version)}（只读）。要在这版基础上改，用「版本管理」里的「回到这一版」。`);
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
        status(`已以 ${versionLabel(v.version)} 为基础开了一份新草案：改完在底栏「校验并审核」，再发布就成为一个新版本（历史版本原样保留）。`);
      } catch (error) {
        status((error && error.message) || '回到历史版本失败', true);
      } finally {
        backBtn.disabled = false;
      }
    });
    actions.append(viewBtn, backBtn);

    technical.append(el('p', 'om-history-note',
      '做什么：查看任意已发布版本；把某一版作为基础开一份新草案。'
      + '不做什么：不改写历史——「回到这一版」不是删掉后面的版本，而是发布一条以它为基础的新版本，'
      + '所以每一次结构变化在版本列表里都留得住。'));
    return box;
  }

  async function openHistory(ontologyId) {
    ensureDrawer();
    const opened = drawer, project = activeProject(), opening = ++historyOpening;
    opened.overlay.removeAttribute('hidden');
    ui.historyOpen = true;
    if (ontologyId && !versions.list.some(v => v.id === ontologyId)) {
      put(opened.body, el('p', 'om-history-empty', '正在读取目标版本…'));
    } else {
      if (ontologyId) ui.selectedVersion = ontologyId;
      renderHistory();         // 先用缓存画一帧
    }
    await ensureVersions(true); // 等这次读取结束（含在途请求），再无条件重画一次
    if (activeProject() !== project || drawer !== opened || opening !== historyOpening
      || !ui.historyOpen || !opened.overlay.isConnected) return;
    if (ontologyId && !versions.list.some(v => v.id === ontologyId)) {
      put(opened.body, el('p', 'om-history-empty', versions.loaded
        ? `未找到这条已发布本体版本：${shortId(ontologyId)}。请重新打开版本管理核对。`
        : '暂时无法读取这条版本记录，请稍后重新打开版本管理。'));
    } else {
      if (ontologyId) ui.selectedVersion = ontologyId;
      renderHistory();
    }
    const closeBtn = drawer.overlay.querySelector('button');
    if (closeBtn) closeBtn.focus();
  }
  function closeHistory() {
    ++historyOpening;
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
      syncHistoryProject();
      // 切项目会重建骨架并再次 mount：先撤旧订阅，避免同一事件被反复处理。
      unsubscribers.forEach(off => { try { off(); } catch (error) { /* 已失效 */ } });
      unsubscribers = [];
      if (ctx && typeof ctx.on === 'function') {
        const rerender = () => renderPanel();
        unsubscribers.push(ctx.on('select', rerender));
        unsubscribers.push(ctx.on('draft:changed', rerender));
        // Background publication checks only change the action bar; keep in-progress edits intact.
        unsubscribers.push(ctx.on('stage:changed', () => renderActions()));
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
    openCreate(preset={}) {
      if(S().preview)return;
      createProject=activeProject();createOpen=true;createValues={};
      createPreset={...preset};
      const M=model();if(M&&typeof M.select==='function')M.select(null);
      else renderDetail();
      focusCreate();
    },
    openHistory(ontologyId) { return openHistory(ontologyId); },
    closeHistory() { closeHistory(); },
  };
})();
