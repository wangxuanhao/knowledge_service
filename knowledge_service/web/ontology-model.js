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
    ontologyVersion: null,    // 当前生效本体的权威展示版本（由 API 返回，不按数组位置推算）
    graph: null,              // 编译好的画布图 { nodes, edges }
    selected: null,           // 选中的术语 IRI
    draft: null,              // 当前草案（id / revision / status / ontology）
    draftChoices: [],         // 多份草案必须由工作区明确选择
    draftLoadError: null,
    showingDraft: false,      // true = 画布画的是**草案**（未发布）；false = 已发布本体
    preview: null,            // 只读预览的历史版本 { id, version }；null = 不看历史
    view: 'types',            // 连接的类型卡片 / 层级布局 / 完整术语卡片清单
    nodeDisplay: 'cards',     // 卡片 / 紧凑节点，与布局独立
    stageAvailability: null,  // GET /stage-availability（阶段门禁，服务端唯一出处）
    readiness: null,          // GET /publish-readiness（发布清单）
    gatesLoading: false,
    gateLoadError: null,
    candidates: [],           // 待收下候选（画布上的虚线节点）
    discovery: null,          // GET /ontology-discovery：开放发现候选（published、待纳入候选数等）
    error: null,
    loading: false,
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
      const card=window.OntologyCatalog?.typeCard(c,S.summary);
      nodes.push({ id: c.id, iri: c.id, label: label(c), kind: 'class', dtype: '',
        parents:[...(c.parents||[])],attributes:card?.attributes||[],inheritedAttributes:card?.inheritedAttributes||[],
        state: S.selected === c.id ? 'selected' : S.showingDraft ? 'draft' : 'published' });
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
          kind: 'relation', label: label(r), termIri:r.id });
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
  let refreshEpoch = 0;
  let gateEpoch = 0;
  let projectEpoch = 0;
  async function refresh() {
    const epoch = ++refreshEpoch;
    ++gateEpoch;
    const project = typeof current === 'undefined' ? null : current;
    const stillCurrent = () => epoch === refreshEpoch && current === project;
    if (typeof current === 'undefined' || !current) {
      S.summary = null; S.ontologyId = null; S.ontologyVersion = null;
      S.error = null; S.showingDraft = false; compileGraph(); render();
      return S;
    }
    S.loading = true;
    S.gatesLoading = true;
    S.stageAvailability = null; S.readiness = null;
    render();
    // ① 已发布本体（失败也不致命：有草案时照样能画）
    let [published, discovery, directory] = await Promise.all([
      api(endpoint('/ontology'), undefined, 'GET').catch(() => null),
      loadDiscovery(),
      api(endpoint('/ontology-drafts'), undefined, 'GET')
        .then(list => ({items:list.items || []})).catch(() => ({error:true})),
    ]);
    if (!stillCurrent()) return S;
    if (directory.error) S.draftLoadError='工作草案列表读取失败，请重试后再保存。';
    else { S.draftChoices=directory.items.filter(d=>OPEN_STATES.includes(d.status)); S.draftLoadError=null; }
    S.ontologyId = published ? (published.id || null) : S.ontologyId;
    S.ontologyVersion = published ? (published.version ?? null) : null;
    // 读开放发现概览（是否已发布本体、待纳入候选数）。它是\"没发布本体时画布该画什么\"的唯一依据，
    // 所以先于草案选择读出来；读不到就置空，绝不因发现信息缺失挡住正常本体展示。
    S.discovery = discovery;

    // ①' 只读预览历史版本：优先于草案展示（用户刚点了「查看这一版」），
    //     但**不销毁**草稿状态 —— 横幅会说明"改动没丢，点这里回去"。
    if (S.preview && S.preview.id) {
      try {
        const item = await api(endpoint(`/ontology?ontology_id=${encodeURIComponent(S.preview.id)}`), undefined, 'GET');
        if (!stillCurrent()) return S;
        S.summary = item.summary || EMPTY_SUMMARY;
        S.showingDraft = false;
        S.error = null;
        compileGraph();
        emit('graph:changed', { graph: S.graph });
        S.loading = false;
        render();
        refreshGatesInBackground();
        return S;
      } catch (e) {
        if (!stillCurrent()) return S;
        // 读不到那一版就退出预览：不能把用户困在一张点不动的画布上
        S.preview = null;
      }
    }

    // ② 选草案（不新建 —— 新建只在真正要写的时候发生，否则一进页面就堆草案）
    let draft = (S.draft && S.draft.id && OPEN_STATES.includes(S.draft.status)) ? S.draft : null;
    if (!draft && !directory.error) draft = S.draftChoices.length===1 ? S.draftChoices[0] : null;

    // ③ 有草案就画草案；读不到草案详情才回落到已发布本体（绝不画空白）
    let detail = null,terminalDraft=false;
    if (draft && draft.id) {
      try { detail = await api(endpoint(`/ontology-drafts/${draft.id}?view=canvas`), undefined, 'GET'); }
      catch (e) { detail = null; }
    }
    if (!stillCurrent()) return S;
    if(detail&&!OPEN_STATES.includes(detail.status)){
      terminalDraft=true;S.draftChoices=S.draftChoices.filter(item=>item.id!==detail.id);
      draft=null;detail=null;
      // A concurrent publication may have happened after the first formal read.
      published=await api(endpoint('/ontology'),undefined,'GET').catch(()=>null);
      if(!stillCurrent())return S;
      S.ontologyId=published?.id||null;S.ontologyVersion=published?.version??null;
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
      else if (terminalDraft){S.summary=null;S.error='这份草案已结束，读取最新正式本体失败；请重试。';}
      else if (S.discovery && S.discovery.published === false) {
        // 没发布本体：这不是\"读取失败\"，而是\"这个项目还没生成本体\"（开放发现的正常中间态）。
        // 画布该画的是引导态（有多少候选、怎么归纳生成本体），由 renderSidebar 依 S.discovery 渲染。
        S.summary = null; S.error = null;
      }
      else { S.summary = null; S.error = '读取当前本体失败'; }
    }
    compileGraph();
    emit('graph:changed', { graph: S.graph });
    S.loading = false;
    render();
    refreshGatesInBackground();
    return S;
  }

  // 读开放发现概览（GET /ontology-discovery）：是否已发布本体、待纳入候选数、各状态计数。
  // 读不到就置空——发现信息是增强，缺它不该挡住正常本体展示；但\"没发布本体\"时它是
  // 画布上该画什么的唯一依据（见 renderEmptyState），所以读不到时也必须诚实区分，
  // 不能把\"接口失败\"和\"空项目\"混成一句。
  async function loadDiscovery() {
    try {
      return await api(endpoint('/ontology-discovery'), undefined, 'GET');
    } catch (e) {
      return null;
    }
  }

  // 读阶段门禁（服务端唯一出处）+ 发布清单。拿不到就置空 —— 子模块据此显示"读不到"，
  // 而不是自己推规则（skill：门禁只有一处出处，前端不自己算）。
  function readGate(route) {
    const request=api(endpoint(route),undefined,'GET');
    if(typeof setTimeout!=='function')return request;
    let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('审核条件读取超时')),15000);});
    return Promise.race([request,deadline]).finally(()=>clearTimeout(timer));
  }

  function refreshGatesInBackground() {
    const project=current,draftId=S.draft?.id;
    void refreshDraftState().catch(()=>{
      if(current!==project||S.draft?.id!==draftId)return;
      S.gatesLoading=false;S.gateLoadError='未能读取审核条件，可以重试；草案内容已保留。';
      emit('stage:changed', { stageAvailability: S.stageAvailability, readiness: S.readiness });
    });
  }

  async function refreshDraftState() {
    const epoch = ++gateEpoch;
    if (!S.draft || !S.draft.id) { S.stageAvailability = null; S.readiness = null; S.gatesLoading = false; S.gateLoadError=null; return; }
    S.gatesLoading = true;
    S.gateLoadError=null;
    emit('stage:changed', { stageAvailability: S.stageAvailability, readiness: S.readiness });
    const project = current, draftId = S.draft.id, revision = S.draft.revision;
    const base = `/ontology-drafts/${S.draft.id}`;
    // stage-availability 是**项目级**路由，草案用 draft_id 查询参数指定；
    // 写成 /{draft_id}/stage-availability 会 404，还会被 catch 吞掉 —— 门禁永远为空、按钮永远禁用。
    const isCurrent=()=>epoch===gateEpoch&&current===project&&S.draft?.id===draftId&&S.draft?.revision===revision;
    const [stage, editingReadiness] = await Promise.all([
      readGate(`/ontology-drafts/stage-availability?draft_id=${encodeURIComponent(draftId)}`).catch(() => null),
      readGate(base + '/publish-readiness?view=editing').catch(() => null),
    ]);
    if(!isCurrent())return;
    let readiness=editingReadiness;
    // Only the server decides when a fresh full validation can unlock publish.
    // Editing projections never count as a completed publication validation.
    if(editingReadiness?.requires_full_validation===true){
      readiness=await readGate(base+'/publish-readiness').catch(()=>null);
      if(!isCurrent())return;
    }
    S.stageAvailability = stage; S.readiness = readiness;
    S.gatesLoading = false;
    S.gateLoadError=stage&&readiness?null:'未能在限定时间内读取完整审核条件，请重试；草案内容已保留。';
    emit('stage:changed', { stageAvailability: S.stageAvailability, readiness: S.readiness });
  }

  // ---- 草案读写：编辑前先有草案，命令逐条发送 -------------------------------
  // 迁移 0005 后「请求」只有三态：pending（待处理）→ accepted（已收下）/ rejected（已驳回）；
  // 旧词 editing/submitted/reviewed/stale_* 已并入 pending，别再用旧词筛（会永远筛不到、每次编辑都新建草案）。
  async function ensureDraft() {
    if (S.preview) throw new Error('历史版本只读：请先回到最新，或从版本管理创建新草案。');
    const project = current, projectBase = endpoint('');
    const assertProject = () => {
      if (current !== project) throw new Error('项目已切换，已停止准备原项目草案。');
      if (S.preview) throw new Error('历史版本只读：已停止准备草案。');
    };
    // 只有**待处理**的草案能继续改。发布过（accepted）或已驳回（rejected）的草案是终态：
    // 服务端对它们发 commands 一律 422「已经收下」。以前这里只看 `S.draft && S.draft.id`，
    // 于是发布完当场再编辑，命令还打给那份终态草案 —— 用户看到的就是"发布了就不能再改了"，
    // 刷新一下才好（刷新后 S.draft 归零才重开）。这里改成按状态判断 + 直接换新草案。
    if (S.draft && S.draft.id && OPEN_STATES.includes(S.draft.status)) return S.draft;
    S.draft = null;
    // 列表失败不能冒充“没有草案”；多份草案不替用户挑第一份。
    {
      const list = await api(projectBase + '/ontology-drafts', undefined, 'GET');
      assertProject();
      const pending=(list.items||[]).filter(d=>OPEN_STATES.includes(d.status));
      S.draftChoices=pending;
      if(pending.length>1){
        const management=document.getElementById('om-work-changes');if(management)management.open=true;
        throw new Error('项目有多份工作草案，请先在「本轮修改 → 草案管理」选择目标；当前表单已保留。');
      }
      const open = pending[0];
      if (open) {
        // 列表项是轻量记录：补一次详读，才能拿到最新 revision（乐观锁）与草案结构
        const detail = await api(projectBase + `/ontology-drafts/${open.id}?view=canvas`, undefined, 'GET');
        assertProject();
        if(!OPEN_STATES.includes(detail.status))throw new Error('这份草案已结束，请重新选择目标。');
        S.draft = detail;
        refreshGatesInBackground();
        assertProject();
        emit('draft:changed', { draft: detail });
        return detail;
      }
    }
    // 没有就新建：基线必须是**项目当前最新本体**，否则服务端会 409 stale_base
    let baseOntologyId;
    try {
      const ont = await api(projectBase + '/ontology', undefined, 'GET');
      if (!ont || typeof ont.id !== 'string' || !ont.id) throw new Error('当前本体响应缺少发布身份，请刷新后重试。');
      baseOntologyId = ont.id;
    } catch (error) {
      if (error.status !== 404 || error.code !== 'ontology_not_published') throw error;
      baseOntologyId = null;
    }
    assertProject();
    const draft = await api(projectBase + '/ontology-drafts', {
      base_ontology_id: baseOntologyId, source_kind: 'manual',
      title: '画布编辑', summary: '在本体建模层画布上编辑',
    }, 'POST');
    assertProject();
    S.draft = draft;
    refreshGatesInBackground();
    assertProject();
    emit('draft:changed', { draft });
    return draft;
  }

  // 发一条命令到当前草案：自动建草案 → 带乐观锁 revision → 返回更新后的草案并刷新
  async function sendCommand(command) {
    if (S.preview) throw new Error('历史版本只读：请先回到最新，或从版本管理创建新草案。');
    const project = current;
    const draft = await ensureDraft();
    if (current !== project) throw new Error('项目已切换，已停止原项目编辑。');
    if (S.preview) throw new Error('历史版本只读：已停止编辑。');
    const updated = await api(endpoint(`/ontology-drafts/${draft.id}/commands`), {
      expected_revision: draft.revision, command,
    }, 'POST');
    if (current !== project) throw new Error('项目已切换，请重新读取当前项目。');
    if(S.preview||S.draft?.id!==draft.id||S.draft?.revision!==draft.revision)
      throw new Error(`修改已保存到原草案 ${draft.id}，当前查看目标已变化；请返回原草案核对，避免重复提交。`);
    S.draft = updated;
    emit('draft:changed', { draft: updated });
    await refresh();
    return updated;
  }

  async function selectDraft(id) {
    if (S.preview) throw new Error('历史版本只读，请先回到最新。');
    const project=current,base=endpoint('');
    const draft=await api(base+'/ontology-drafts/'+encodeURIComponent(id),undefined,'GET');
    if(current!==project)throw new Error('项目已切换，已停止选择草案。');
    if(!OPEN_STATES.includes(draft.status))throw new Error('这份草案已结束，请选择待处理草案。');
    S.draft=draft;S.selected=null;await refresh();return S.draft;
  }

  async function createTerm(spec) {
    if (S.preview) throw new Error('历史版本只读：请先回到最新。');
    const kind = spec.kind, labelZh = String(spec.label_zh || '').trim();
    const label = String(spec.label || labelZh).trim();
    if (!['class', 'relation', 'attribute'].includes(kind) || !label)
      throw new Error('请选择术语类型并填写名称。');
    // Match ontology.generated_term_iri, retaining readable Unicode names.
    const segment = Array.from((labelZh || label).normalize('NFC'), character => {
      if (/[\p{L}\p{N}\p{M}._~-]/u.test(character)) return character;
      if (/\s/u.test(character)) return '-';
      return encodeURIComponent(character).replace(/[!'()*]/g, value => '%' + value.charCodeAt(0).toString(16).toUpperCase());
    }).join('').replace(/-+/g, '-').replace(/^-|-$/g, '');
    if (!segment) throw new Error('术语名必须包含可用 IRI 字符。');
    const iri = spec.uri || `urn:knowledge:ontology:${current}:${segment}`;
    const commands = [{action: 'create_term', target_iri: iri, kind},
      {action: 'add_annotation', target_iri: iri, predicate: 'http://www.w3.org/2000/01/rdf-schema#label', value: label}];
    if (labelZh) commands.push({action: 'add_annotation', target_iri: iri,
      predicate: 'http://www.w3.org/2000/01/rdf-schema#label', value: labelZh, language: 'zh'});
    if (kind === 'class' && spec.parent)
      commands.push({action: 'add_parent', target_iri: iri, value: spec.parent});
    if (kind !== 'class') {
      for (const value of new Set(spec.domains || (spec.domain ? [spec.domain] : [])))
        commands.push({action: 'add_domain', target_iri: iri, value});
      if (kind === 'relation') for (const value of new Set(spec.ranges || (spec.range ? [spec.range] : [])))
        commands.push({action: 'add_range', target_iri: iri, value});
      if (kind === 'attribute' && spec.datatype)
        commands.push({action: 'set_datatype', target_iri: iri, datatype: spec.datatype});
    }
    return sendCommand({action: 'batch', commands, reason: `新增本体术语：${labelZh || label}`});
  }
  async function createDraft() {
    if(S.preview)throw new Error('历史版本只读，请从版本管理创建历史新草案。');
    const project=current,base=endpoint('');
    const published=await api(base+'/ontology',undefined,'GET').catch(error=>{if(error.status===404&&error.code==='ontology_not_published')return null;throw error;});
    if(current!==project)throw new Error('项目已切换，已停止新建草案。');
    const draft=await api(base+'/ontology-drafts',{base_ontology_id:published?.id||null,
      source_kind:'manual',title:'手工建模草案',summary:'在本体目录中显式创建'},'POST');
    if(current!==project)throw new Error('项目已切换，请回原项目查看新草案。');
    S.draft=draft;S.selected=null;await refresh();return S.draft;
  }
  async function closeDraft() {
    if(S.preview)throw new Error('历史版本只读，请先回到最新。');
    const draft=S.draft,project=current,base=endpoint('');
    if(!draft||!OPEN_STATES.includes(draft.status))throw new Error('没有可关闭的待处理草案。');
    const reason=window.prompt('关闭当前草案的原因（变更和审计保留）');
    if(reason===null)return;
    if(!reason.trim())throw new Error('关闭草案必须填写原因。');
    await api(base+'/ontology-drafts/'+encodeURIComponent(draft.id)+'/close',{
      expected_revision:draft.revision,reason:reason.trim()},'POST');
    if(current!==project)return;
    if(S.draft?.id!==draft.id){status(`原草案 ${draft.id} 已关闭；当前工作草案选择保留。`);return;}
    S.draft=null;S.selected=null;await refresh();status('草案已关闭，变更审计保留。');
  }
  function openCreate(preset={}) {
    if(S.preview){status('历史版本只读，请先回到最新。',true);return;}
    setInspector(true);
    if(window.OntologyPanel?.openCreate)window.OntologyPanel.openCreate(preset);
  }
  function openIntake(scope={}) {
    if(S.preview){status('历史版本只读，请先回到最新。',true);return;}
    if(window.OntologyIntake)void window.OntologyIntake.open({projectId:current,...scope});
    else status('候选处理模块尚未加载，请刷新页面重试。',true);
  }

  // 幂等键：一次发布动作生成一次、重试间复用（服务端要求 idempotency_key 必填）
  const uid = () => (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'uid-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);

  // ---- 选中：唯一入口，三条路径（左清单 / 画布 / 详情跳转）都走这里 ----------
  function select(iri) {
    S.selected = iri || null;
    if(iri)setInspector(true);
    compileGraph();
    emit('select', { iri: S.selected });
    render();
  }

  // ---- 渲染：骨架构建一次，其余只更新可变部分 --------------------------------
  let built = false;
  const collapsedBranches=new Set();
  let inspectorOpen=false;
  function setInspector(open,focus=false) {
    inspectorOpen=!!open;root()?.classList?.toggle('om-inspector-open',inspectorOpen);
    const toggle=document.getElementById('om-inspector-toggle');
    toggle?.setAttribute('aria-expanded',String(inspectorOpen));
    if(focus){const target=inspectorOpen?document.getElementById('om-inspector-close'):toggle;target?.focus?.();}
  }
  function render() {
    const el = root(); if (!el) return;
    if (!built) { buildSkeleton(el); built = true; }
    el.setAttribute?.('data-ontology-view',S.view);
    el.querySelectorAll('[data-om-view]').forEach(button=>button.classList.toggle('on',button.dataset.omView===S.view));
    const display=document.getElementById('om-node-display');
    el.querySelectorAll('[data-om-create]').forEach(button=>button.disabled=!!S.preview);
    const pendingButton=document.getElementById('om-pending-intake');if(pendingButton)pendingButton.disabled=!!S.preview;
    if(display){display.value=S.nodeDisplay;display.disabled=S.view==='cards';}
    setInspector(inspectorOpen);
    const parentButton = document.getElementById('om-discover-parents');
    if (parentButton) {
      parentButton.disabled = !!(S.loading || S.preview || S.discoveringParents)
        || (S.summary && S.summary.classes || []).filter(c => c.active !== false).length < 2;
      parentButton.textContent = S.discoveringParents ? '正在发现父类…' : '发现父类';
    }
    renderSidebar();
    renderVersionContext();
    // 子模块可能尚未加载（脚本顺序 / 测试 mock shell）——用可选链，缺谁都不崩
    if (window.OntologyCanvas) window.OntologyCanvas.render(S);
    if (window.OntologyPanel) window.OntologyPanel.render(S);
    const canvas=document.getElementById('om-canvas-host'),cards=document.getElementById('om-cards-host');
    if(canvas)canvas.hidden=S.view==='cards';if(cards)cards.hidden=S.view!=='cards';
    if(window.OntologyCatalog)window.OntologyCatalog.render(S);
  }

  function renderVersionContext() {
    const host = document.getElementById('om-version-context');
    if (!host) return;
    const currentLabel = S.ontologyVersion == null
      ? '尚未发布正式本体' : `正式本体 v${S.ontologyVersion} · 已发布`;
    let viewedLabel;
    if (S.preview) viewedLabel = `正在查看：v${S.preview.version ?? '?'}（历史·只读）`;
    else if (S.showingDraft) {
      const base=S.draft.base_ontology_id;
      viewedLabel=`本轮修改 ${S.draft.id} · 修订 r${S.draft.revision??'?'} · 基于 ${!base?'空基线':base===S.ontologyId&&S.ontologyVersion!=null?'v'+S.ontologyVersion:base} · 未发布`;
      if(base&&base!==S.ontologyId)viewedLabel+=' · 基线变化';
    } else viewedLabel=S.draftLoadError||(S.draftChoices.length>1?'有多份工作草案，请在本轮修改中明确选择。':'当前没有未发布的结构修改；保存第一项结构调整时自动建立。');
    host.replaceChildren();
    const currentPart = document.createElement('span');
    currentPart.className = 'om-version-context-current';
    currentPart.textContent = currentLabel;
    const divider = document.createElement('span');
    divider.className = 'om-version-context-divider';
    divider.textContent = '→';
    const viewedPart = document.createElement('span');
    viewedPart.className = 'om-version-context-viewed';
    viewedPart.textContent = viewedLabel;
    host.append(currentPart, divider, viewedPart);
  }

  function buildSkeleton(el) {
    el.innerHTML = `
      <div class="om-page">
        <div class="om-topbar">
          <span class="om-brand">◆ 本体建模层</span>
          <span class="om-ctx" id="om-version">当前本体 v?</span>
          <details class="om-definition-menu admin-only" id="om-definition-menu"><summary class="om-btn primary">新增定义 ▾</summary><div role="group" aria-label="新增定义种类"><button type="button" data-om-create="class">实体类型</button><button type="button" data-om-create="relation">关系类型</button><button type="button" data-om-create="attribute">属性字段</button></div></details>
          <button type="button" class="om-btn ghost admin-only" id="om-pending-intake">待完善结构</button>
          <button type="button" class="om-btn ghost" id="om-discover-parents" title="根据当前草案或本体重新发现父类（调用模型）；只补充新建议到草案，保留已有关系，审核发布后生效">发现父类</button>
          <div class="om-seg" role="group" aria-label="视图切换">
            <button type="button" data-om-view="types" class="on" title="连接的实体类卡片，显示直属属性、数据类型与继承摘要">类型卡片</button>
            <button type="button" data-om-view="hierarchy" title="按继承深度排列；左侧层级树可展开和折叠，多父类保留引用">继承层级</button>
            <button type="button" data-om-view="cards" title="完整的类、关系、属性定义清单，包括无端点与已停用术语">术语卡片</button>
          </div>
          <label class="om-display-control">节点 <select id="om-node-display" aria-label="节点显示方式"><option value="cards">属性卡片</option><option value="compact">紧凑节点</option></select></label>
          <button type="button" class="om-btn ghost om-inspector-toggle" id="om-inspector-toggle" aria-controls="om-inspector-shell" aria-expanded="false">定义详情</button>
          <button type="button" class="om-btn ghost" id="om-history" title="版本管理：看每一版是谁、什么时候、为什么发的；可以查看任意一版，也可以以某一版为基础开一份新草案（历史不改写）">版本管理</button>
        </div>
        <div class="om-preview-banner" id="om-preview-banner" hidden>
          <span id="om-preview-text"></span>
          <button type="button" class="om-btn ghost" id="om-preview-exit">回到最新</button>
        </div>
        <div class="om-version-context" id="om-version-context" aria-live="polite"></div>
        <details class="om-work-changes" id="om-work-changes"><summary>本轮修改 · 草案管理与变更</summary><div id="om-work-changes-host"></div></details>
        <div class="om-body">
          <!-- 用 div 不用 aside：全站 style.css 有一条给应用侧栏的全局规则
               aside{position:fixed;inset:0 auto 0 0;width:206px}，任何 aside 都会被固定到
               视口左上角、脱离三栏流（P1 实测踩到：三栏塌成空白）。 -->
          <div class="om-side" aria-label="本体术语目录">
            <div id="om-catalog-host"></div>
            <details class="om-hierarchy-directory"><summary>继承导航与项目候选</summary><div id="om-side" aria-label="本体三维清单"></div></details>
          </div>
          <div class="om-stage"><div class="om-cards-host" id="om-cards-host"></div><div class="om-canvas-host" id="om-canvas-host" hidden></div></div>
          <button type="button" class="om-inspector-backdrop" id="om-inspector-backdrop" aria-label="关闭定义详情"></button>
          <div class="om-inspector-shell" id="om-inspector-shell" role="complementary" aria-label="定义详情"><div class="om-inspector-heading"><strong>定义详情</strong><button type="button" class="om-btn ghost" id="om-inspector-close" aria-label="关闭定义详情">×</button></div><div class="om-detail" id="om-detail-host" aria-label="节点详情"></div></div>
        </div>
        <div class="om-actionbar" id="om-actions-host" aria-label="校验与发布"></div>
      </div>`;
    // 视图切换：只改 S.view 并发事件，布局由画布模块负责
    const parentButton = document.getElementById('om-discover-parents');
    el.querySelectorAll('[data-om-create]').forEach(button=>button.addEventListener('click',()=>{
      const menu=document.getElementById('om-definition-menu');if(menu)menu.open=false;openCreate({kind:button.dataset.omCreate});
    }));
    document.getElementById('om-pending-intake')?.addEventListener('click',()=>openIntake());
    if (parentButton) parentButton.addEventListener('click', () => { void discoverParents(); });
    const display=document.getElementById('om-node-display');
    display?.addEventListener('change',()=>{S.nodeDisplay=display.value;window.OntologyCanvas?.setDisplay(S.nodeDisplay);});
    document.getElementById('om-inspector-toggle')?.addEventListener('click',()=>setInspector(!inspectorOpen,true));
    for(const id of ['om-inspector-close','om-inspector-backdrop'])document.getElementById(id)?.addEventListener('click',()=>setInspector(false,true));
    el.addEventListener?.('keydown',event=>{if(event.key==='Escape'&&inspectorOpen)setInspector(false,true);});
    el.querySelectorAll('[data-om-view]').forEach(btn => btn.addEventListener('click', () => {
      S.view = btn.dataset.omView;
      el.querySelectorAll('[data-om-view]').forEach(b => b.classList.toggle('on', b === btn));
      emit('view:changed', { view: S.view });
      if (S.view!=='cards'&&window.OntologyCanvas) window.OntologyCanvas.setView(S.view);
      if(S.view==='hierarchy'){const directory=el.querySelector?.('.om-hierarchy-directory');if(directory)directory.open=true;}
      if(display)display.disabled=S.view==='cards';
      render();
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
    if(window.OntologyCatalog)window.OntologyCatalog.mount(document.getElementById('om-catalog-host'),document.getElementById('om-cards-host'),ctx);
    if (window.OntologyCanvas) window.OntologyCanvas.mount(document.getElementById('om-canvas-host'), ctx);
    if (window.OntologyPanel) window.OntologyPanel.mount(
      document.getElementById('om-detail-host'), document.getElementById('om-actions-host'), ctx);
  }

  // 左栏三维清单（类 / 关系 / 属性）。点谁 = 选中谁（唯一入口 select）。
  function renderSidebar() {
    const host = document.getElementById('om-side'); if (!host) return;
    if (!S.summary) {
      // 没有本体结构可画：区分三种空态（读不到 / 没发布本体但有候选 / 没候选），
      // 每种给一句人话 + 能点的下一步，绝不再把"没发布本体"误报成"读取失败"。
      host.innerHTML = renderEmptyState();
      const gen = document.getElementById('om-generate-draft');
      if (gen) gen.addEventListener('click', () => openIntake());
      // 第二个慢入口：显式让模型推断父子层级（传 true）
      const inferH = document.getElementById('om-infer-hierarchy');
      if (inferH) inferH.addEventListener('click', () => { void discoverParents(); });
      // 顶栏版本徽标不能停在占位「当前本体 v?」：没本体就说清"尚未发布"，别留个 v?。
      const ver = document.getElementById('om-version');
      if (ver) {
        ver.textContent = S.loading ? '正在加载本体与草案…' : S.discovery && S.discovery.published === false
          ? '尚未发布本体' : (S.error || '等待读取本体状态');
        ver.classList.toggle('is-draft', false);
        ver.classList.toggle('is-preview', false);
      }
      return;
    }
    const classes = (S.summary.classes || []).filter(c => c.active !== false);
    const rels = (S.summary.relations || []).filter(r => r.active !== false);
    const attrs = (S.summary.attributes || []).filter(a => a.active !== false);
    const row = (it, chip) => `<li class="${it.id === S.selected ? 'sel' : ''}" data-sel="${escHtml(it.id)}">
        <span class="om-chip ${chip}"></span>${escHtml(label(it))}</li>`;
    // 导航树保留多父类引用；展开状态是纯视图状态，不写回本体。
    const classTree = () => {
      if(window.OntologyCatalog?.hierarchyRows){
        return window.OntologyCatalog.hierarchyRows(S.summary,{collapsed:collapsedBranches}).map(c=>`<li role="treeitem" aria-level="${c.depth+1}"${c.childCount&&!c.reference?` aria-expanded="${c.expanded}"`:''} class="om-tree-row ${c.id===S.selected?'sel':''}" style="--tree-depth:${c.depth}">
          <div class="om-tree-line">${c.childCount&&!c.reference?`<button type="button" class="om-tree-toggle" data-tree-toggle="${escHtml(c.id)}" aria-label="${c.expanded?'折叠':'展开'}${escHtml(label(c))}" aria-expanded="${c.expanded}">${c.expanded?'⌄':'›'}</button>`:'<span class="om-tree-spacer"></span>'}
          <button type="button" class="om-tree-select" data-sel="${escHtml(c.id)}" title="${escHtml(c.id)}"><span class="om-chip cls"></span>${escHtml(label(c))}${c.reference?`<small>${c.cycle?'循环引用':'多父类引用'}</small>`:''}${c.childCount&&!c.reference?`<small class="om-tree-count" title="${c.childCount} 个直属子类 · ${c.descendantCount} 个后代">${c.childCount}</small>`:''}</button></div>${c.externalParents.length?`<small class="om-tree-external">${c.externalParents.length} 个外部父类</small>`:''}</li>`).join('');
      }
      return classes.map(c=>row(c,'cls')).join('');
    };
    // 已发布本体之后又抽出新候选：左栏顶部给一条"增量归纳"入口（基于当前版本，不影响已发布）。
    // 否则新知识只会静默躺在 discovery 里，用户不知道"后续加知识怎么显示"。
    const counts = S.discovery && S.discovery.candidate_status_counts;
    const pendingN = counts ? (counts.pending || 0) : ((S.discovery && S.discovery.unpublished_candidate_count) || 0);
    const directoryHeading=host.parentElement?.querySelector?.('summary');
    if(directoryHeading)directoryHeading.textContent='继承导航与项目候选'+(pendingN?` · 待处理 ${pendingN}`:'');
    const includedN = counts && counts.included_in_draft || 0;
    const draftTip = includedN > 0 ? `<div class="om-discovery-inline"><b>${includedN} 个候选已纳入草案</b><p>等待校验、审核和发布，无需重复生成。正式知识会在发布后显示。</p></div>` : '';
    const discTip = (pendingN > 0 ? `<div class="om-discovery-inline">
      <b>又抽出 ${pendingN} 个新候选</b>
      <p>先选择候选并核对来源；复用正式术语可直接确认知识，需补结构时明确加入工作草案。</p>
      <button type="button" id="om-generate-draft-inline" class="om-btn primary" ${S.preview || S.loading || S.discoveringParents ? 'disabled' : ''}>选择候选并完善结构</button>
    </div>` : '') + draftTip;
    host.innerHTML = `${discTip}
      <h4><i class="om-dot"></i>本体类型 · ${classes.length}</h4>
      <ul class="om-tree" role="tree" aria-label="实体类继承导航">${classTree() || '<li class="om-none">（还没有类）</li>'}</ul>
      <h4 class="rel"><i class="om-dot"></i>关系类型 · ${rels.length}</h4>
      <ul>${rels.map(r => row(r, 'rel')).join('') || '<li class="om-none">（还没有关系）</li>'}</ul>
      <h4 class="attr"><i class="om-dot"></i>属性类型 · ${attrs.length}</h4>
      <ul>${attrs.map(a => `<li class="${a.id === S.selected ? 'sel' : ''}" data-sel="${escHtml(a.id)}">
          <span class="om-chip attr"></span>${escHtml(label(a))}
          <span class="om-dtype">${escHtml(dtypeOf(a) || '?')}</span></li>`).join('') || '<li class="om-none">（还没有属性）</li>'}</ul>
      <div class="om-tip">点名称查看定义 · 箭头展开子类<br>实线箭头 = 业务关系 · 虚线空心箭头 = 继承</div>`;
    host.querySelectorAll('[data-tree-toggle]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();const id=button.dataset.treeToggle;if(collapsedBranches.has(id))collapsedBranches.delete(id);else collapsedBranches.add(id);renderSidebar();}));
    host.querySelectorAll('[data-sel]').forEach(li => li.addEventListener('click', (e) => {
      e.stopPropagation(); // 树是嵌套 li：阻止子类点击冒泡到父类，否则点到子类却选中父类
      select(li.dataset.sel);
    }));
    const genInline = document.getElementById('om-generate-draft-inline');
    if (genInline) genInline.addEventListener('click', () => {
      openIntake();
    });
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

  // 没有本体结构时的三种空态（左栏）。\"没发布本体\"是开放发现的正常中间态，不是故障——
  // 这一句不能省，否则用户看到空画布会以为是坏了（本轮\"本体建模没内容\"就是它造成的）。
  function renderEmptyState() {
    if (S.loading) return '<div class="om-side-empty" role="status">正在加载本体与草案…</div>';
    // 归纳中：顶部给一个明确的"正在归纳"说明，回答"点了之后呢"——
    // 不只让按钮变灰，而是把"正在做什么 / 接下来会怎样"讲清楚。

    const d = S.discovery;
    // ① 读不到发现概览（接口失败 / 网络问题）：诚实说"读不到"，不假装是空项目。
    if (!d) {
      return `<div class="om-side-empty">${escHtml(S.error || '读不到项目本体状态，请稍后重试。')}</div>`;
    }
    // ② 已发布本体但结构读不到（罕见，接口异常）：按错误提示。
    if (d.published !== false) {
      return `<div class="om-side-empty">${escHtml(S.error || '读取本体结构失败')}</div>`;
    }
    if (d.candidate_status_counts && !d.candidate_status_counts.pending
        && d.candidate_status_counts.included_in_draft) {
      return `<div class="om-side-empty"><h4>${d.candidate_status_counts.included_in_draft} 个候选已纳入草案</h4><p>无需重复生成。请重新加载草案结构后校验、审核并发布。</p></div>`;
    }
    // ③ 没发布本体：分"有候选"和"没候选"两种。
    const hasCandidates = (d.candidate_count || 0) > 0;
    if (hasCandidates) {
      // 讲"类型"不讲"实体"：这一步整理出的是【实体类型 / 关系类型】，不是一条条实体。
      const entityTypeN = (d.entity_types || []).length;
      const relationTypeN = (d.relation_types || []).length;
      return `<div class="om-side-empty om-discovery-guide">
        <h4>这个项目还没生成本体</h4>
        <p>知识写入已抽出 ${d.candidate_count} 个候选，将整理成
          <b>${entityTypeN}</b> 个实体类型、<b>${relationTypeN}</b> 个关系类型
          （同名概念会合并，不是把 ${d.entity_count || 0} 个实体名逐个建类）。</p>
        <button type="button" id="om-generate-draft" class="om-btn primary" ${S.preview || S.discoveringParents ? 'disabled' : ''}>选择候选并预览</button>
        <p class="om-guide-do">打开清单与预览不会创建草案；明确目标并确认加入后，才保存本轮结构修改。</p>
        <p class="om-guide-note">加入后可通过顶部「发现父类」补充建议，或在画布上编辑继承。</p>
      </div>`;
    }
    return `<div class="om-side-empty">
      <h4>这个项目还没有本体，也没有待纳入的候选</h4>
      <p>可使用顶部「新增定义」手动建模，或在资料库添加文件并开放抽取候选，再从「待完善结构」处理。</p>
    </div>`;
  }

  async function discoverParents() {
    if (S.discoveringParents || S.loading || S.preview) return;
    if ((S.summary && S.summary.classes || []).filter(c => c.active !== false).length < 2) return;
    const project = current;
    S.discoveringParents = true; render();
    if (typeof status === 'function') status('正在根据当前结构发现父类（调用模型），已有父类关系会保留…');
    try {
      const draft = await ensureDraft();
      if (current !== project || S.preview) return;
      const result = await api(endpoint(`/ontology-drafts/${encodeURIComponent(draft.id)}/discover-parents`),
        { expected_revision: draft.revision }, 'POST');
      if (current !== project || S.preview || !S.draft || S.draft.id !== draft.id) return;
      S.draft = result.draft;
      await refresh();
      if (current !== project) return;
      if (typeof status === 'function') status(result.added_count
        ? `已补充 ${result.added_count} 条父类建议到草案，请校验并审核、发布后生效。`
        : '没有发现新增的父类关系；已存在的关系不会重复添加，可稍后再次发现。');
    } catch (error) {
      if (current === project && typeof status === 'function') status('发现父类失败：' + (error.message || error), true);
    } finally { S.discoveringParents = false; render(); }
  }

  // ---- 挂载：点「本体建模层」页签 / 切项目时加载 ------------------------------
  function wire() {
    const tab = document.querySelector('[data-tab="ontology-model"]');
    if (tab) tab.addEventListener('click', () => { void refresh(); });
    const proj = document.getElementById('project');
    if (proj) proj.addEventListener('change', () => {
      ++refreshEpoch; ++gateEpoch; ++projectEpoch;
      S.discoveringParents = false;
      S.discovery = null; S.loading = false; S.gatesLoading = false;
      S.summary = null; S.ontologyId = null; S.ontologyVersion = null;
      S.graph = null; S.selected = null; S.draft = null;
      S.draftChoices=[];S.draftLoadError=null;window.OntologyIntake?.projectChanged();
      S.showingDraft = false; S.preview = null;
      S.stageAvailability = null; S.readiness = null; S.candidates = [];
      collapsedBranches.clear();inspectorOpen=false;
      built = false;
      if (root() && !root().classList.contains('hidden')) render();
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
    wb.setVersionContext({
      ontologyScope: 'ids', ontologyIds: [ontologyId],
      ontologyVersion: version || null, source: 'ontology-history',
    });
    emit('preview:changed', { preview: S.preview });
    return refresh();
  }

  // 退出预览，回到"草案优先、否则最新已发布版本"的正常视图
  function clearPreview() {
    wb.clearVersionContext();
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
      title: '回到历史版本',
      summary: '以某一已发布版本为基础开的新草案（回退不改写历史，发布后成为新版本）',
      source_context: { revert_from: ontologyId },
    }, 'POST');
    S.preview = null;
    wb.clearVersionContext();
    S.draft = draft;
    emit('preview:changed', { preview: null });
    emit('draft:changed', { draft });
    await refresh();
    return draft;
  }

  window.OntologyModel = {
    S, on, emit, render, refresh, refreshDraftState, ensureDraft, sendCommand, createTerm, select, compileGraph,
    selectDraft, createDraft, closeDraft, openCreate,openIntake,
    uid, previewVersion, clearPreview, forkFrom, discoverParents,setInspector,
    status: (m, e) => (typeof status === 'function' ? status(m, e) : console.log(m)),
    helpers: { label, shortIri, dtypeOf, esc: escHtml },
  };
})();
