/* Read-only draft history. Saved drafts never enter the model's editing state. */
(() => {
  let host = null, model = null, onVersion = null, cache = null;
  let requestEpoch = 0, viewEpoch = 0;
  const rows = value => Array.isArray(value) ? value : [];
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const project = () => { try { return current || ''; } catch (_) { return ''; } };
  const base = () => typeof endpoint === 'function' ? endpoint('/ontology-drafts') : '';
  const statusLabel = draft => draft.status === 'accepted' && draft.published_ontology_id ? '已发布'
    : ({pending: '待处理', accepted: '已收下', rejected: '已驳回'}[draft.status] || '状态未提供');
  const actions = {create_term: '新增定义', add_parent: '添加父类', remove_parent: '移除父类',
    add_domain: '添加所属类', remove_domain: '移除所属类', add_range: '添加客体类',
    remove_range: '移除客体类', set_datatype: '修改数据类型', retire_term: '停用定义',
    restore_term: '恢复定义', add_annotation: '添加名称或说明', remove_annotation: '移除名称或说明',
    advanced_rdf_patch: '调整底层定义'};
  const kinds = {class: '实体类型', relation: '关系类型', attribute: '属性字段'};
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function button(text, handler, className = '') {
    const node = el('button', 'om-btn ghost ' + className, text); node.type = 'button';
    node.addEventListener('click', handler); return node;
  }
  function technical(title, value) {
    const node = el('details', 'om-dh-technical');
    node.append(el('summary', null, title));let built=false;
    node.addEventListener('toggle',()=>{if(node.open&&!built){built=true;node.append(el('pre',null,JSON.stringify(value,null,2)));}});
    return node;
  }
  function reset() {
    ++requestEpoch; ++viewEpoch; cache = null;
    host?.replaceChildren();
  }
  // Closing the workspace cancels pending paints, while completed content stays available.
  function suspend() { ++requestEpoch; }
  function mount(nextHost, options = {}) {
    const nextModel = options.model || null;
    if (host !== nextHost || model !== nextModel || (cache && (cache.project !== project() || cache.base !== base()))) reset();
    host = nextHost || null; model = nextModel; onVersion = options.onVersion || null;
  }
  function begin() {
    const p = project(), path = base();
    if (!cache || cache.project !== p || cache.base !== path) {
      cache = {project: p, base: path, directory: null, versions: null, versionPromise: null};
    }
    return {host, model, project: p, base: path, cache, request: ++requestEpoch, view: ++viewEpoch};
  }
  const viewActive = ctx => host === ctx.host && model === ctx.model && cache === ctx.cache
    && ctx.host && ctx.host.isConnected !== false && ctx.project === project() && ctx.base === base() && ctx.view === viewEpoch;
  const requestActive = ctx => viewActive(ctx) && ctx.request === requestEpoch;
  function shell(ctx, title, withDirectory = false) {
    if (!requestActive(ctx)) return null;
    const root = el('div', 'om-dh-root');
    const header = el('div', 'om-dh-header');
    header.append(el('h3', null, title), el('span', 'om-dh-readonly', '历史草案 · 只读'));
    if (withDirectory) header.append(button('草案目录', () => { if (viewActive(ctx)) void showList(); }));
    root.append(header); ctx.host.replaceChildren(root); return root;
  }
  function failure(ctx, title, retry) {
    const root = shell(ctx, title, true); if (!root) return;
    const message = el('p', 'om-dh-error', '读取失败，请重试。'); message.setAttribute('role', 'alert');
    root.append(message, button('重试', () => { if (viewActive(ctx)) void retry(); }));
  }
  async function showList() {
    const ctx = begin(), root = shell(ctx, '历史草案'); if (!root) return;
    if (!ctx.project) { root.append(el('p', 'om-dh-empty', '请先选择项目。')); return; }
    root.append(el('p', 'om-dh-empty', '正在读取草案目录…'));
    try {
      let items = ctx.cache.directory;
      if (!items) {
        const result = await api(ctx.base, undefined, 'GET');
        if (!requestActive(ctx)) return;
        if (!Array.isArray(result?.items)) throw new Error('Invalid draft directory');
        items = result.items; ctx.cache.directory = items;
      }
      if (!requestActive(ctx)) return;
      const body = shell(ctx, '历史草案');
      body.append(button('刷新目录', () => {
        if (!viewActive(ctx)) return;
        ctx.cache.directory = null; void showList();
      }));
      body.append(el('p', 'om-dh-summary', '查看各轮草案保存的变更、审核结果和版本关系。待处理草案在此也只读。'));
      if (!items.length) { body.append(el('p', 'om-dh-empty', '项目中还没有历史草案。')); return; }
      const list = el('div', 'om-dh-list');
      items.forEach(item => {
        const row = el('article', 'om-dh-item');
        const link = button(item.title || '未命名草案', () => { if (viewActive(ctx)) void show(item.id); });
        link.disabled = !item.id;
        row.append(link, el('span', 'om-dh-status', statusLabel(item)),
          el('p', 'om-dh-meta', `修订 ${item.revision ?? '未提供'}${item.created_at ? ' · ' + item.created_at : ''}`));
        list.append(row);
      }); body.append(list);
    } catch (_) { if (requestActive(ctx)) failure(ctx, '历史草案目录', showList); }
  }
  // Names come from the saved draft, never from today's canvas or source assertions.
  function nameIndex(draft) {
    const names = new Map(), priorities = new Map(), ontology = object(draft.ontology);
    const summary = object(ontology.summary || ontology);
    for (const kind of ['classes', 'relations', 'attributes']) for (const term of rows(summary[kind])) {
      if (!term.id || !(term.label_zh || term.label || term.name)) continue;
      names.set(term.id, model?.helpers?.label?.(term) || term.label_zh || term.label || term.name);
      priorities.set(term.id, term.label_zh ? 2 : 1);
    }
    for (const operation of rows(draft.operations)) {
      if (operation.action !== 'add_annotation') continue;
      const after = object(operation.after);
      if (!/(?:#|\/|:)label$/.test(after.predicate || '') || typeof after.value !== 'string') continue;
      const priority = String(after.language || '').toLowerCase().startsWith('zh') ? 2 : 1;
      if (!names.has(operation.target_iri) || priority > priorities.get(operation.target_iri)) {
        names.set(operation.target_iri, after.value); priorities.set(operation.target_iri, priority);
      }
    }
    return names;
  }
  function localName(value) {
    if (typeof value !== 'string') return '';
    let name = value.split(/[/#:]/).pop();
    try { name = decodeURIComponent(name); } catch (_) { /* Keep the saved spelling. */ }
    return name && !/^[0-9a-f-]{24,}$/i.test(name) ? name : '';
  }
  function nameOf(iri, names, fallback = '未命名定义') { return names.get(iri) || localName(iri) || fallback; }
  function decisionIndex(draft) {
    const all = rows(draft.decisions), superseded = new Set(all.map(item => item.supersedes_decision_id).filter(Boolean));
    const result = new Map(), operations = new Map(rows(draft.operations).map(item => [item.id, item]));
    for (const item of all) {
      const op = operations.get(item.operation_id);
      if (superseded.has(item.id) || !op || (op.fingerprint && op.fingerprint !== item.operation_fingerprint)) continue;
      // Do not invent a final decision when a malformed history contains two current records.
      if (result.has(op.id)) result.set(op.id, null); else result.set(op.id, item);
    }
    return result;
  }
  function operationText(op, names) {
    const after = op.after, before = op.before;
    const value = object(after).value ?? (typeof after === 'string' ? after : object(before).value);
    if (op.action === 'create_term') return kinds[object(after).kind] || '定义';
    if (op.action === 'add_annotation' || op.action === 'remove_annotation') {
      const annotation = object(after || before);
      const field = /(?:#|\/|:)label$/.test(annotation.predicate || '') ? '名称'
        : /(?:#|\/|:)comment$/.test(annotation.predicate || '') ? '说明' : '标注';
      return typeof value === 'string' ? `${field}：${value}` : field;
    }
    if (typeof value === 'string') return nameOf(value, names, '未提供名称');
    return '';
  }
  function renderOperations(root, draft, names) {
    const section = el('section', 'om-dh-section'); section.append(el('h4', null, '定义变更与审核结果'));
    const operations = rows(draft.operations), decisions = decisionIndex(draft);
    const counts = {approve: 0, reject: 0, pending: 0}, groups = new Map();
    for (const op of operations) {
      const decision = decisions.get(op.id);
      if (decision?.action === 'approve') ++counts.approve;
      else if (decision?.action === 'reject') ++counts.reject;
      else ++counts.pending;
      const target = op.target_iri || op.id;
      if (!groups.has(target)) groups.set(target, []);
      groups.get(target).push(op);
    }
    section.append(el('p', 'om-dh-summary', `收下 ${counts.approve} · 不收 ${counts.reject} · 待决定 ${counts.pending}`),
      el('p', 'om-dh-meta', `${groups.size} 个定义 · ${operations.length} 项底层操作。审核结果仅对应记录中的操作内容。`));
    if (!operations.length) section.append(el('p', 'om-dh-empty', '这份草案没有保存定义变更。'));
    for (const [target, ops] of groups) {
      const group = el('details', 'om-dh-group'); group.append(el('summary', null, nameOf(target, names)));
      for (const op of ops) {
        const line = el('div', 'om-dh-operation'), decision = decisions.get(op.id), detail = operationText(op, names);
        line.append(el('p', null, `${actions[op.action] || '其他定义变更'}${detail ? ' · ' + detail : ''}`),
          el('span', 'om-dh-status', decision?.action === 'approve' ? '已收下' : decision?.action === 'reject' ? '已不收' : '未保存有效决定'));
        if (decision?.reason) line.append(el('p', 'om-dh-meta', '理由：' + decision.reason));
        line.append(technical('操作与审核技术明细', {operation: op, decision: decision || null})); group.append(line);
      } section.append(group);
    } root.append(section);
  }
  function renderSources(root, draft) {
    const section = el('section', 'om-dh-section'), context = object(draft.source_context);
    section.append(el('h4', null, '保存的来源记录'), el('p', 'om-dh-meta', '仅展示这份草案保存的来源快照；缺少标题或原文时不以当前资料补全。'));
    const documents = rows(context.documents).length ? rows(context.documents)
      : rows(context.document_refs).length ? rows(context.document_refs)
        : context.document_id ? [context] : [];
    documents.forEach((reference, index) => {
      const source = object(reference), row = el('article', 'om-dh-source');
      row.append(el('strong', null, source.document_title || source.title || `来源文档 ${index + 1}`));
      const version = source.expected_document_version ?? source.document_version ?? source.version;
      if (version != null) row.append(el('p', 'om-dh-meta', `保存的文档版本：${version}`));
      const quote = source.quote || source.evidence || source.excerpt;
      row.append(el('p', null, typeof quote === 'string' && quote ? quote : '未保存原文摘录。'));
      row.append(technical('来源快照技术明细', reference)); section.append(row);
    });
    rows(context.intake_bindings).forEach((binding, index) => {
      const row = el('article', 'om-dh-source');
      row.append(el('strong', null, `纳入批次 ${index + 1}`),
        el('p', 'om-dh-meta', Array.isArray(binding.selected_candidate_ids)
          ? `明确选中 ${binding.selected_candidate_ids.length} 条候选事实` : '未保存候选事实数量。'),
        technical('纳入批次技术明细', binding)); section.append(row);
    });
    if (!documents.length && !rows(context.intake_bindings).length) section.append(el('p', 'om-dh-empty', '未保存可展示的来源摘要；可在技术明细中核对原始记录。'));
    section.append(technical('完整来源上下文', context)); root.append(section);
  }
  async function loadVersions(ctx) {
    if (ctx.cache.versions) return ctx.cache.versions;
    if (ctx.cache.versionPromise) return ctx.cache.versionPromise;
    const path = endpoint('/ontologies');
    const promise = (async () => {
      const result = await api(path, undefined, 'GET');
      if (!Array.isArray(result?.versions)) throw new Error('Invalid version directory');
      const index = new Map(result.versions.map(item => [item.id, item]));
      if (cache === ctx.cache && project() === ctx.project && base() === ctx.base) ctx.cache.versions = index;
      return index;
    })();
    ctx.cache.versionPromise = promise;
    try { return await promise; }
    finally { if (ctx.cache.versionPromise === promise) ctx.cache.versionPromise = null; }
  }
  function renderVersions(ctx, root, draft) {
    const section = el('section', 'om-dh-versions'); section.append(el('h4', null, '版本关系'));
    const links = [];
    for (const [field, title, absent] of [['base_ontology_id', '基线版本', '基于空白本体'],
      ['published_ontology_id', '产出版本', '未记录产出版本']]) {
      const id = draft[field];
      if (!id) { section.append(el('p', 'om-dh-meta', `${title}：${absent}`)); continue; }
      const link = button(title + ' · 查看确切版本', () => { if (viewActive(ctx) && typeof onVersion === 'function') onVersion(id); }, 'om-dh-version');
      link.disabled = typeof onVersion !== 'function';
      links.push({link, id, title}); section.append(link);
    }
    root.append(section); if (!links.length) return;
    const feedback = el('div', 'om-dh-meta'); section.append(feedback);
    async function resolveNames() {
      const epoch = requestEpoch;
      const stillCurrent = () => viewActive(ctx) && epoch === requestEpoch;
      feedback.replaceChildren();
      try {
        const versions = await loadVersions(ctx); if (!stillCurrent()) return;
        for (const {link, id, title} of links) {
          const item = versions.get(id), version = item?.metadata?.version ?? item?.version;
          link.textContent = title + (version != null ? ` · v${version}` : ' · 查看确切版本');
        }
      } catch (_) {
        if (!stillCurrent()) return;
        feedback.append(el('span', null, '版本名称读取失败，仍可按记录中的版本链接查看。'),
          button('重试版本名称', () => { if (viewActive(ctx)) void resolveNames(); }));
      }
    }
    void resolveNames();
  }
  async function show(draftId) {
    const ctx = begin(), root = shell(ctx, '历史草案', true); if (!root) return;
    if (!draftId) { root.append(el('p', 'om-dh-empty', '未提供来源草案标识，无法读取历史。')); return; }
    if (!ctx.project) { root.append(el('p', 'om-dh-empty', '请先选择项目。')); return; }
    root.append(el('p', 'om-dh-empty', '正在读取历史草案…'));
    try {
      const read=window.OntologyDraftData?.request||api;
      const draft = await read(ctx.base + '/' + encodeURIComponent(draftId), undefined, 'GET');
      if (!requestActive(ctx)) return;
      if (!draft || draft.id !== draftId) throw new Error('Draft identity does not match');
      const body = shell(ctx, draft.title || '未命名草案', true);
      body.append(el('p', 'om-dh-status', statusLabel(draft)),
        el('p', 'om-dh-meta', `修订 ${draft.revision ?? '未提供'} · 本页仅供查看，工作草案和画布保持原状。`));
      if (draft.summary) body.append(el('p', 'om-dh-summary', draft.summary));
      renderVersions(ctx, body, draft); renderOperations(body, draft, nameIndex(draft)); renderSources(body, draft);
      body.append(technical('草案身份与版本技术明细', {draft_id: draft.id, status: draft.status,
        revision: draft.revision, source_kind: draft.source_kind,
        base_ontology_id: draft.base_ontology_id, published_ontology_id: draft.published_ontology_id}));
      return draft;
    } catch (_) { if (requestActive(ctx)) failure(ctx, '历史草案', () => show(draftId)); }
  }
  window.OntologyDraftHistory = {mount, show, showList, reset, suspend};
})();
