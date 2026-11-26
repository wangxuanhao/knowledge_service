/* Project-scoped neighborhood searches and reversible filter stages. */
(function (root) {
  'use strict';

  const emptyTrail = () => ({ nodes: [], edges: [], steps: [], records: [] });
  const recordKey = row => JSON.stringify([row.kind, row.id]);

  function combinePath(trail, path, rows) {
    if (!path?.start || !Array.isArray(path.steps)) return trail;
    const nodes = [path.start, ...path.steps.map(step => step.to)];
    const edges = path.steps.map(step => step.relation_id);
    const nodeIds = new Set(nodes), edgeIds = new Set(edges);
    const records = new Map(trail.records.map(row => [recordKey(row), row]));
    for (const row of rows) {
      if ((row.kind === 'entity' && nodeIds.has(row.id)) || (row.kind === 'relation' && edgeIds.has(row.id))) {
        if (!records.has(recordKey(row))) records.set(recordKey(row), row);
      }
    }
    return {
      nodes: [...trail.nodes, ...nodes.slice(trail.nodes.length ? 1 : 0)],
      edges: [...trail.edges, ...edges],
      steps: [...trail.steps, ...path.steps],
      records: [...records.values()],
    };
  }

  function createSession({ getContext, request, onChange = () => {} }) {
    let owner = '', stages = [], controller = null, failed = null, error = '', errorCode = '';
    const contextKey = context => JSON.stringify([context.projectId, context.scope]);
    const currentStage = () => stages.at(-1) || null;
    const state = () => ({ stage: currentStage(), canBack: stages.length > 1, pending: Boolean(controller),
      error, errorCode, failedSeed: failed?.criteria.seed.id });
    const notify = () => onChange(state());

    function reset() {
      controller?.abort();
      controller = null;
      stages = [];
      failed = null;
      error = '';
      errorCode = '';
    }

    function sync() {
      const context = getContext(), key = contextKey(context);
      if (key !== owner) {
        reset();
        owner = key;
      }
      return context;
    }

    async function run(command) {
      const context = sync(), { projectId } = context;
      if (!projectId) return false;
      const scope = structuredClone(command.scope ?? context.scope);
      controller?.abort();
      const ticket = controller = new AbortController();
      failed = null;
      error = '';
      errorCode = '';
      const { criteria, trail, replace } = command;
      notify();
      let accepted = false;
      try {
        const result = await request(projectId, {
          ...scope,
          seeds: [criteria.seed.id],
          direction: criteria.direction,
          hops: criteria.hops,
          relationship_types: criteria.relationshipTypes,
          filter_steps: criteria.filters,
        }, ticket.signal);
        sync();
        if (ticket !== controller) return false;
        if (!Array.isArray(result?.matches) || !Array.isArray(result.records)) throw Error('服务返回的邻域结果不完整');
        const seed = criteria.seed;
        if (seed.version_id || seed.version != null) {
          const returned = result.records.find(row => row.kind === 'entity' && row.id === seed.id);
          const matchesVersion = seed.version_id ? returned?.version_id === seed.version_id : returned?.version === seed.version;
          if (!matchesVersion) {
            throw Object.assign(Error('所选起点的版本已变化或在该范围内不可读取。'), { code: 'seed_version_changed' });
          }
        }
        if (replace) stages = [];
        stages.push({ criteria, trail, result, scope });
        accepted = true;
      } catch (failure) {
        sync();
        if (ticket !== controller) return false;
        failed = { ...command, scope };
        error = failure.message || '请重试';
        errorCode = failure.code || '';
      } finally {
        if (ticket === controller) {
          controller = null;
          notify();
        }
      }
      return accepted;
    }

    return {
      snapshot() { sync(); return state(); },
      reset() { reset(); owner = contextKey(getContext()); notify(); },
      start(criteria, { scope } = {}) { return run({ criteria: { ...criteria, filters: [] }, trail: emptyTrail(), replace: true, scope }); },
      refine(filters) {
        sync();
        const stage = currentStage();
        return stage && run({ criteria: { ...stage.criteria, filters }, trail: stage.trail, scope: stage.scope });
      },
      expand(id) {
        sync();
        const stage = currentStage();
        if (!stage || id === stage.criteria.seed.id) return false;
        const match = stage.result.matches.find(row => row.id === id);
        if (!match) return false;
        const trail = combinePath(stage.trail, match.path, stage.result.records);
        const seed = { id, label: match.label || id, version_id: match.version_id };
        return run({ criteria: { ...stage.criteria, seed }, trail, scope: stage.scope });
      },
      back() {
        sync();
        controller?.abort();
        controller = null;
        failed = null;
        error = '';
        errorCode = '';
        if (stages.length > 1) stages.pop();
        notify();
      },
      retry() { sync(); return failed && run(failed); },
    };
  }

  if (typeof module === 'object' && module.exports) module.exports = { createSession, combinePath };

  if (typeof document === 'undefined') return;
  const fields = {
    entity_type: '实体类型',
    entity_name: '实体名称',
    attribute_value: '属性值（精确）',
    source_platform: '来源平台',
    source_published_date: '来源发布日期',
    source_keyword: '来源关键词',
  };
  const directions = { both: '双向', forward: '沿关系箭头', backward: '逆关系箭头' };
  const byId = id => document.getElementById(id);

  function element(tag, text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function button(label, action) {
    const node = element('button', label, 'secondary');
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }
  function seedMatchDetails(row, query, semantic) {
    const {rankBadges, appendText} = root.SourceMatchView;
    const mode = semantic ? 'semantic' : 'keyword';
    const details = element('span', '', 'neighborhood-lookup-match');
    for (const text of rankBadges({mode, score:row.score})) {
      details.append(element('span', text));
    }
    if (semantic) {
      details.append(element('span', '匹配实体向量'));
    } else {
      const hasQuery = value => String(value ?? '').toLowerCase().includes(query.toLowerCase());
      const aliases = Array.isArray(row.metadata?.aliases) ? row.metadata.aliases : [];
      const fields = [['命中实体名称', row.text], ['命中实体 ID', row.id],
        ['命中实体别名', aliases.find(hasQuery)]];
      for (const [label, value] of fields) {
        if (!hasQuery(value)) continue;
        const field = element('span');
        const excerpt = element('span', ' · ');
        appendText(excerpt, value, query, true);
        field.append(element('span', label), excerpt);
        details.append(field);
      }
    }
    return details;
  }

  function withoutDisplayFilters(scope) {
    const { entity_type, predicate, ...applied } = scope;
    return applied;
  }

  function readContext() {
    const { projectId = '', scope = {} } = root.GraphWorkspaceContext?.read() || {};
    return { projectId, scope: withoutDisplayFilters(scope) };
  }

  function install() {
    const layout = root.GraphWorkspaceLayout;
    const header = byId('graph-workspace-header');
    if (!header || !layout) return;

    const pane = document.createElement('section');
    pane.innerHTML = `
      <div class="neighborhood-heading">
        <h3 tabindex="-1">渐进邻域检索</h3>
        <button type="button" class="secondary" data-ns="map">查看图谱</button>
      </div>
      <details data-ns="settings" open>
        <summary>设置检索起点与范围</summary>
        <form data-ns="lookup">
          <p id="neighborhood-lookup-hint" data-ns="lookup-hint" class="subtle" hidden></p>
          <div class="neighborhood-lookup-field">
            <label for="neighborhood-query">定位起点实体</label>
            <div class="neighborhood-row"><input id="neighborhood-query" data-ns="query" required maxlength="2000" placeholder="实体名称或 ID" autocomplete="off"><button class="secondary">查找</button></div>
          </div>
          <p data-ns="lookup-status" class="subtle" role="status"></p>
          <ul data-ns="choices" class="neighborhood-choices" aria-label="候选实体" hidden></ul>
          <div data-ns="lookup-pager" class="neighborhood-row" hidden>
            <button type="button" class="secondary" data-ns="lookup-prev">上一页</button>
            <button type="button" class="secondary" data-ns="lookup-next">下一页</button>
          </div>
        </form>
        <form data-ns="start">
          <p data-ns="seed" class="neighborhood-seed" role="status">从图上选择起点，或在顶部搜索实体。</p>
          <div class="neighborhood-settings-grid">
            <label>遍历方向<select data-ns="direction"><option value="both">双向</option><option value="forward">沿关系箭头</option><option value="backward">逆关系箭头</option></select></label>
            <label>最大跳数<input data-ns="hops" type="number" min="0" max="10" step="1" value="2" required></label>
          </div>
          <details class="neighborhood-types"><summary data-ns="types-summary">允许的关系类型 · 全部</summary><div data-ns="types"></div></details>
          <button data-ns="run">检索邻域</button>
        </form>
      </details>
      <p data-ns="status" class="neighborhood-notice" role="status" aria-live="polite" hidden></p>
      <p data-ns="map-status" class="subtle" role="status" hidden></p>
      <div data-ns="results" hidden>
        <div class="neighborhood-heading"><h4 data-ns="total" tabindex="-1"></h4><button type="button" class="secondary" data-ns="back">返回上一步</button></div>
        <p data-ns="criteria" class="subtle"></p>
        <div data-ns="trail" class="neighborhood-trail" hidden></div>
        <ul data-ns="filters" class="neighborhood-conditions" aria-label="已应用条件" hidden></ul>
        <details data-ns="refine">
          <summary data-ns="filter-summary">添加筛选条件</summary>
          <form data-ns="filter">
            <div class="neighborhood-settings-grid">
              <label>筛选字段<select data-ns="filter-field"></select></label>
              <label>筛选值<input data-ns="filter-value" required maxlength="2000" autocomplete="off"><textarea data-ns="attribute-value" rows="3" maxlength="2000" hidden></textarea></label>
            </div>
            <datalist id="neighborhood-entity-types"></datalist>
            <div class="neighborhood-row"><button data-ns="apply">应用筛选</button><button type="button" class="secondary" data-ns="cancel-edit" hidden>取消编辑</button></div>
            <p class="subtle">筛选会重新检查当前起点的完整邻域，各条件同时生效。</p>
          </form>
        </details>
        <ul data-ns="matches" class="neighborhood-matches" aria-label="命中实体"></ul>
      </div>`;
    layout.registerPanel('neighborhood', '邻域检索', pane);
    const part = name => pane.querySelector(`[data-ns="${name}"]`);
    const lookupField = part('query'), choices = part('choices'), lookupStatus = part('lookup-status');
    const lookupMode = byId('neighborhood-search-mode'), lookupHint = part('lookup-hint');
    const filterField = part('filter-field'), filterValue = part('filter-value'), attributeValue = part('attribute-value');
    const filterControl = () => filterField.value === 'attribute_value' ? attributeValue : filterValue;
    const relationTypes = part('types'), matchList = part('matches');
    const narrow = root.matchMedia('(max-width: 768px)');
    const statusNode = part('status'), mapStatus = part('map-status');
    let seed = null, seedScope = null, editing = null, selected = null, visibleStage = null;
    let lookupController = null, lookupStack = [], lookupNext = null;
    const rowElements = new Map(), savedSelections = new WeakMap();
    const matchDetail = element('div', '', 'neighborhood-match-detail');

    const endpoint = (projectId, path) => '/api/projects/' + encodeURIComponent(projectId) + path;
    const session = createSession({
      getContext: readContext,
      request: (projectId, data, signal) => api(endpoint(projectId, '/graph-neighborhood/search'), data, 'POST', { signal }),
      onChange: render,
    });
    for (const [value, label] of Object.entries(fields)) filterField.add(new Option(label, value));

    function isOpen() { return layout.snapshot().panel === 'neighborhood'; }

    function openPanel() {
      const wasOpen = isOpen();
      syncLookupMode();
      refreshRelationshipTypes();
      render(session.snapshot());
      layout.showPanel('neighborhood');
      if (!wasOpen && visibleStage && !narrow.matches) drawMap(visibleStage);
    }

    function catalog(id) {
      return [...(byId(id)?.options || [])].filter(option => option.value);
    }

    function refreshRelationshipTypes(selected = readRelationshipTypes()) {
      const selectedTypes = new Set(selected);
      const options = new Map(catalog('predicate-scope').map(option => [option.value, option.textContent]));
      for (const type of selectedTypes) if (!options.has(type)) options.set(type, labelOf(type));
      relationTypes.replaceChildren();
      for (const [value, title] of options) {
        const label = element('label');
        const checked = element('input');
        checked.type = 'checkbox'; checked.value = value; checked.checked = selectedTypes.has(value);
        label.append(checked, element('span', title || value));
        relationTypes.append(label);
      }
      if (!options.size) relationTypes.append(element('p', '当前范围暂无可选关系类型。', 'subtle'));
      updateTypesSummary();
    }

    function readRelationshipTypes() {
      return [...relationTypes.querySelectorAll('input:checked')].map(input => input.value);
    }

    function updateTypesSummary() {
      const count = readRelationshipTypes().length;
      part('types-summary').textContent = '允许的关系类型 · ' + (count ? `已选 ${count} 种` : '全部');
    }

    function chooseSeed(row, scope = null) {
      seed = row ? { id: row.id, label: row.text || row.label || row.id, version_id: row.version_id, version: row.version } : null;
      seedScope = seed && scope ? withoutDisplayFilters(scope) : null;
      const range = seedScope && JSON.stringify(seedScope) !== JSON.stringify(readContext().scope) ? ' · 沿用实体详情范围' : '';
      part('seed').textContent = seed ? `起点 · ${seed.label}${range}` : '从图上选择起点，或在顶部搜索实体。';
      part('seed').classList.toggle('is-selected', Boolean(seed));
      part('seed').title = seed?.id || '';
      part('run').disabled = !seed || session.snapshot().pending;
    }

    function openForEntity(row, { scope } = {}) {
      if (!row?.id) return;
      clearLookup();
      lookupField.value = row.text || row.label || row.id;
      chooseSeed(row, scope);
      if (root.RetrievalWorkspace) root.RetrievalWorkspace.select('neighborhood');
      else openPanel();
      part('settings').open = true;
      part('direction').focus({ preventScroll: true });
    }

    function pickFromGraph(row, scope) {
      const panel = layout.snapshot().panel;
      const neighborhoodMode = root.RetrievalWorkspace?.mode === 'neighborhood';
      if (panel !== 'neighborhood' && !(panel === 'closed' && neighborhoodMode)) return false;
      part('settings').open = true;
      if (row.kind !== 'entity') {
        lookupStatus.textContent = '点击图上的实体节点来设置起点。';
        return true;
      }
      clearLookup();
      chooseSeed(row, scope);
      lookupField.value = seed.label;
      if (neighborhoodMode) byId('query').value = seed.label;
      if (panel === 'closed') layout.showPanel('neighborhood');
      return true;
    }

    function searchForEntity(text) {
      openPanel();
      part('settings').open = true;
      if (!text.trim()) {
        byId('query').focus({ preventScroll: true });
        return;
      }
      clearLookup();
      chooseSeed(null);
      lookupField.value = text.trim().slice(0,2000);
      void locateSeed();
    }

    function clearLookup() {
      lookupController?.abort();
      lookupController = null;
      lookupStack = [];
      lookupNext = null;
      choices.replaceChildren();
      choices.hidden = part('lookup-pager').hidden = true;
      lookupStatus.textContent = '';
    }

    function syncLookupMode(scope = readContext().scope) {
      const historical = Boolean(scope.known_at);
      lookupMode.options[1].disabled = historical;
      if (historical) lookupMode.value = 'keyword';
      const semantic = lookupMode.value === 'semantic';
      lookupField.placeholder = semantic ? '描述想找的实体' : '实体名称或 ID';
      if (root.RetrievalWorkspace?.mode === 'neighborhood') {
        byId('query').placeholder = semantic ? '描述想找的实体，定位邻域起点' : '输入实体名称或 ID，查找邻域起点';
      }
      lookupHint.textContent = historical ? '历史时点使用关键词查找起点。' : '可用自然语言查找实体。选定起点后，按图谱关系展开。';
      lookupHint.hidden = !historical && !semantic;
      return semantic;
    }

    async function locateSeed(cursors = [null]) {
      const { projectId, scope } = readContext(), query = lookupField.value.trim();
      if (!projectId) { lookupStatus.textContent = '请先选择项目。'; return; }
      if (!query) return;
      const semantic = syncLookupMode(scope);
      lookupController?.abort();
      const ticket = lookupController = new AbortController(), owner = JSON.stringify([projectId, scope]);
      const accepts = () => {
        const current = readContext();
        return lookupController === ticket && owner === JSON.stringify([current.projectId, current.scope]);
      };
      lookupStatus.textContent = semantic ? '正在语义查找实体…' : '正在查找实体…';
      part('lookup-prev').disabled = part('lookup-next').disabled = true;
      try {
        const result = semantic
          ? await api(endpoint(projectId, '/search'), {
            ...scope, query, kinds: ['entity'], retrieval_mode: 'semantic', k: 25, k_entities: 25,
          }, 'POST', { signal: ticket.signal, timeoutMs: 45000 })
          : await api(endpoint(projectId, '/records/page'), {
            ...scope, query, kinds: ['entity'], cursor: cursors.at(-1), limit: 25, include_total: false,
          }, 'POST', { signal: ticket.signal });
        if (!accepts()) return;
        let records = result.records || [];
        if (semantic) {
          if (result.active_mode !== 'semantic' || result.degraded) throw new Error('语义结果不可用，请检查项目的实体向量索引。');
          records = (result.hits || []).filter(row => row.kind === 'entity' && row.id && row.version_id)
            .sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 25);
        }
        lookupStack = cursors;
        lookupNext = semantic ? null : result.next_cursor;
        choices.replaceChildren();
        for (const row of records) {
          const item = element('li');
          const title = row.text || row.id;
          const option = button(title, () => {
            clearLookup(); lookupField.value = title; chooseSeed(row);
            part('direction').focus({ preventScroll: true });
          });
          option.append(element('small', [row.type_label || labelOf(row.type), row.id].filter(Boolean).join(' · ')));
          option.append(seedMatchDetails(row, query, semantic));
          item.append(option); choices.append(item);
        }
        choices.hidden = !choices.childElementCount;
        part('lookup-pager').hidden = semantic || (!lookupNext && cursors.length < 2);
        if (semantic) {
          lookupStatus.textContent = choices.hidden ? '未找到语义相关实体。可换个描述或改用关键词。'
            : `语义查找 · ${choices.childElementCount} 个候选实体`;
        } else {
          lookupStatus.textContent = choices.hidden ? '未找到实体。可试试完整名称或实体 ID。'
            : `第 ${cursors.length} 页 · ${choices.childElementCount} 个候选实体`;
        }
      } catch (error) {
        if (accepts()) lookupStatus.textContent = '查找失败。' + error.message;
      } finally {
        if (accepts()) {
          lookupController = null;
          part('lookup-prev').disabled = lookupStack.length < 2;
          part('lookup-next').disabled = !lookupNext;
        }
      }
    }

    function setEditor(index = null) {
      editing = index;
      const condition = session.snapshot().stage?.criteria.filters[index];
      filterField.value = condition?.field || 'entity_type';
      updateFilterInput();
      filterControl().value = condition ? conditionLabel(condition) : '';
      part('filter-summary').textContent = editing === null ? '添加筛选条件' : '编辑筛选条件';
      part('cancel-edit').hidden = editing === null;
    }

    function updateFilterInput() {
      const attribute = filterField.value === 'attribute_value';
      attributeValue.hidden = !attribute;
      attributeValue.required = attribute;
      filterValue.hidden = attribute;
      filterValue.required = !attribute;
      filterValue.type = filterField.value === 'source_published_date' ? 'date' : 'text';
      filterValue.removeAttribute('list');
      if (filterField.value !== 'entity_type') return;
      const list = byId('neighborhood-entity-types');
      list.replaceChildren(...catalog('type-scope').map(option => new Option(option.textContent, option.value)));
      filterValue.setAttribute('list', list.id);
    }

    function conditionLabel(condition) {
      if (condition.field === 'source_published_date') return root.DisplayTime.format(condition.value, condition.value);
      if (condition.field !== 'entity_type') return condition.value;
      const option = catalog('type-scope').find(entry => entry.value === condition.value);
      return option?.textContent || condition.value;
    }

    function conditions(stage) {
      const rows = part('filters');
      rows.replaceChildren();
      for (const [index, condition] of stage.criteria.filters.entries()) {
        const item = element('li'), description = `${fields[condition.field]} · ${conditionLabel(condition)}`;
        const edit = button('编辑', () => { setEditor(index); part('refine').open = true; filterControl().focus(); });
        edit.setAttribute('aria-label', '编辑 ' + description);
        const remove = button('移除', () => {
          void session.refine(stage.criteria.filters.filter((_, itemIndex) => itemIndex !== index));
        });
        remove.setAttribute('aria-label', '移除 ' + description);
        item.append(element('span', description), edit, remove); rows.append(item);
      }
      rows.hidden = !stage.criteria.filters.length;
    }

    function sourceInfo(source) {
      if (!source) return '';
      const date = root.DisplayTime.format(source.published_at, '');
      return [source.title, (source.platforms || []).join('、'), date].filter(Boolean).join(' · ');
    }

    function openSource(source) {
      const { projectId } = readContext();
      if (source.source_version_id && root.SourceReader) {
        return root.SourceReader.open({
          projectId, versionId: source.source_version_id, chunkVersionId: source.chunk_version_id,
          location: source.source_location, focusText: source.quote,
        });
      }
      if (source.quote || source.excerpt) {
        return root.openFrozenSourceEvidence?.({
          title: source.title, version: source.version, reason: '图谱匹配依据的固定来源版本',
          highlight: source.quote || '', excerpt_before: source.quote ? '' : source.excerpt,
        });
      }
    }

    function evidenceLabels(row) {
      return (row.evidence || []).map(entry => `${fields[entry.field] || entry.field} · ${conditionLabel(entry)}`);
    }

    function trailDescription(stage, path) {
      const names = new Map(path.records.filter(row => row.kind === 'entity').map(row => [row.id, row.text || row.id]));
      names.set(stage.criteria.seed.id, stage.criteria.seed.label);
      return path.nodes.map(id => names.get(id) || id).join(' → ');
    }

    function details(stage, row) {
      const path = combinePath(stage.trail, row.path, stage.result.records);
      const relationNames = new Map(path.records.filter(item => item.kind === 'relation').map(item => [item.id, item]));
      const names = new Map(path.records.filter(item => item.kind === 'entity').map(item => [item.id, item.text || item.id]));
      const detailStatus = element('p', '', 'subtle');
      detailStatus.setAttribute('role', 'status'); detailStatus.hidden = true;
      const inspect = async record => {
        detailStatus.hidden = true;
        try { await root.inspectKnowledgeRecord?.(record, { scope: stage.scope, qualify: record.kind === 'entity' ? 'entity' : 'record' }); }
        catch (error) { detailStatus.textContent = '详情读取失败。' + error.message; detailStatus.hidden = false; }
      };
      const actions = element('div', '', 'neighborhood-row');
      if (row.id !== stage.criteria.seed.id) {
        const continueButton = button('从选中节点继续展开', async () => {
          if (await session.expand(row.id) && isOpen()) part('total').focus();
        });
        continueButton.disabled = session.snapshot().pending;
        actions.append(continueButton);
      }
      actions.append(button('查看实体详情', () => inspect({ id: row.id, kind: 'entity', version_id: row.version_id })));
      if (root.CommunityWorkspace?.openForEntity) {
        actions.append(button('查看所属社区', () => root.CommunityWorkspace.openForEntity(row.id, row.label || row.id)));
      }
      matchDetail.replaceChildren(actions, detailStatus);
      if (path.nodes.length) {
        const chain = element('ol', '', 'neighborhood-path');
        for (const step of path.steps) {
          const item = element('li'), edge = relationNames.get(step.relation_id);
          const label = edge?.type_label || labelOf(step.relation) || step.relation_id;
          item.append(element('span', names.get(step.from) || step.from), element('span', step.direction === 'backward' ? ' ← ' : ' → '),
            button(label, () => inspect(edge || { id: step.relation_id, kind: 'relation', version_id: step.relation_version_id })),
            element('span', step.direction === 'backward' ? ' ← ' : ' → '), element('span', names.get(step.to) || step.to));
          chain.append(item);
        }
        matchDetail.append(element('h5', path.steps.length ? '关系路径' : '起点实体'));
        matchDetail.append(path.steps.length ? chain : element('p', trailDescription(stage, path), 'subtle'));
      }
      const shownSources = new Set();
      for (const source of [row.source, ...(row.evidence || []).map(item => item.source)].filter(Boolean)) {
        const key = source.assertion_id || source.document_version_id;
        if (shownSources.has(key)) continue;
        shownSources.add(key);
        const item = element('div', '', 'neighborhood-source');
        item.append(element('span', sourceInfo(source) || '来源文档'));
        if (source.source_version_id || source.quote || source.excerpt) {
          const open = button('查看原文', () => openSource(source));
          open.classList.add('button-inline-action');
          item.append(open);
        }
        matchDetail.append(item);
      }
    }

    function markPending(pending) {
      for (const name of ['apply', 'cancel-edit']) part(name).disabled = pending;
      for (const input of part('filters').querySelectorAll('button')) input.disabled = pending;
      part('run').disabled = pending || !seed;
    }

    function renderMatches(stage) {
      rowElements.clear(); matchList.replaceChildren();
      for (const row of stage.result.matches) {
        const item = element('li'), control = button('', () => {
          selected = row.id;
          savedSelections.set(stage, selected);
          renderSelection(stage);
          if (!narrow.matches) drawMap(stage);
        });
        control.classList.add('neighborhood-match');
        const title = element('span', '', 'neighborhood-match-title');
        const distance = row.id === stage.criteria.seed.id ? '当前起点 · 0 跳' : `${row.hops} 跳`;
        title.append(element('strong', row.label || row.id), element('span', distance, 'neighborhood-hop'));
        const type = row.type_label || labelOf(row.type);
        const explanations = evidenceLabels(row);
        if (!explanations.length) explanations.push(...stage.criteria.filters.map(condition => `${fields[condition.field]} · ${conditionLabel(condition)}`));
        control.append(title, element('small', [type, ...explanations, sourceInfo(row.source)].filter(Boolean).join(' · ')));
        item.append(control); matchList.append(item); rowElements.set(row.id, { item, control });
      }
    }

    function renderSelection(stage) {
      matchDetail.remove();
      const row = stage.result.matches.find(item => item.id === selected);
      for (const [id, { control }] of rowElements) control.setAttribute('aria-pressed', String(id === selected));
      if (row) {
        details(stage, row);
        rowElements.get(row.id)?.item.append(matchDetail);
      }
    }

    function drawMap(stage) {
      const row = stage.result.matches.find(item => item.id === selected);
      const path = row ? combinePath(stage.trail, row.path, stage.result.records) : emptyTrail();
      let rendered = false, highlighted = false;
      try { ({ rendered, highlighted } = root.showNeighborhoodSearchGraph?.(stage, path, readContext()) || {}); }
      catch (error) { console.warn('Unable to show neighborhood graph', error); }
      mapStatus.textContent = !rendered ? '图谱暂时无法显示，可在列表展开关系路径和来源。' :
        path.nodes.length && !highlighted ? '画布无法完整定位这条路径，可展开结果查看关系与来源。' : '';
      mapStatus.hidden = !mapStatus.textContent;
      return rendered;
    }

    function render({ stage, canBack, pending, error, errorCode, failedSeed }) {
      matchList.setAttribute('aria-busy', String(pending));
      statusNode.replaceChildren();
      statusNode.hidden = !pending && !error;
      statusNode.classList.toggle('is-error', Boolean(error));
      if (pending) statusNode.append(element('span', '正在检索邻域…'));
      if (error) {
        const retry = errorCode === 'seed_version_changed' ? button('在当前范围重新定位', () => {
          searchForEntity(failedSeed || '');
        }) : button('重试', () => { void session.retry(); });
        statusNode.append(element('span', `邻域检索失败。${error}`), retry);
      }
      part('results').hidden = !stage;
      part('back').hidden = !canBack;
      if (stage !== visibleStage) {
        visibleStage = stage;
        selected = stage ? savedSelections.get(stage) || null : null;
        mapStatus.hidden = true;
        setEditor();
        if (stage) {
          part('settings').open = false;
          part('refine').open = false;
          const { criteria, result, trail } = stage;
          clearLookup();
          lookupField.value = criteria.seed.label;
          chooseSeed(criteria.seed, stage.scope);
          part('hops').value = criteria.hops;
          part('direction').value = criteria.direction;
          refreshRelationshipTypes(criteria.relationshipTypes);
          const count = result.total ?? result.matches.length;
          const partial = result.partial === true;
          part('total').textContent = partial ? `部分结果 · 已找到 ${count} 个实体` : count ? `${count} 个匹配实体` : '0 个匹配实体';
          const omitted = count > result.matches.length ? ` · 显示 ${result.matches.length} 个` : '';
          const budget = partial ? ' · 已达到检索预算' : '';
          const types = criteria.relationshipTypes.map(type => labelOf(type) || type).join('、') || '全部关系类型';
          const range = JSON.stringify(stage.scope) !== JSON.stringify(readContext().scope) ? ' · 沿用实体详情范围' : '';
          part('criteria').textContent = `起点：${criteria.seed.label} · ${criteria.hops} 跳 · ${directions[criteria.direction]} · ${types}${range}${omitted}${budget}`;
          part('trail').hidden = !trail.nodes.length;
          part('trail').textContent = trail.nodes.length ? `已探索路径 · ${trailDescription(stage, trail)}` : '';
          conditions(stage);
          renderMatches(stage);
          if (isOpen() && !narrow.matches) drawMap(stage);
        } else {
          rowElements.clear(); matchList.replaceChildren();
        }
      }
      if (stage && selected) renderSelection(stage);
      markPending(pending);
    }

    relationTypes.addEventListener('change', updateTypesSummary);
    lookupMode.addEventListener('change', () => { clearLookup(); chooseSeed(null); syncLookupMode(); });
    lookupField.addEventListener('input', () => { clearLookup(); chooseSeed(null); });
    part('lookup').addEventListener('submit', event => { event.preventDefault(); void locateSeed(); });
    part('lookup-prev').addEventListener('click', () => { void locateSeed(lookupStack.slice(0, -1)); });
    part('lookup-next').addEventListener('click', () => { if (lookupNext) void locateSeed([...lookupStack, lookupNext]); });
    part('start').addEventListener('submit', async event => {
      event.preventDefault();
      if (!seed) return;
      if (await session.start({ seed, hops: part('hops').valueAsNumber, direction: part('direction').value, relationshipTypes: readRelationshipTypes() }, { scope: seedScope })) {
        if (isOpen()) part('total').focus({ preventScroll: true });
      }
    });
    part('map').addEventListener('click', async () => {
      const { stage } = session.snapshot();
      if (stage && !drawMap(stage)) return;
      layout.closePanel();
      await layout.settled();
      byId('graph-heading')?.focus({ preventScroll: true });
    });
    part('back').addEventListener('click', () => { session.back(); part('total').focus({ preventScroll: true }); });
    filterField.addEventListener('change', () => { updateFilterInput(); filterControl().value = ''; });
    part('cancel-edit').addEventListener('click', () => setEditor());
    part('filter').addEventListener('submit', async event => {
      event.preventDefault();
      const stage = session.snapshot().stage;
      if (!stage) return;
      const field = filterField.value, value = filterControl().value.trim();
      if (!value) { filterControl().focus(); return; }
      const condition = { field, value };
      const filters = [...stage.criteria.filters];
      if (editing === null) filters.push(condition);
      else filters[editing] = condition;
      if (await session.refine(filters)) {
        if (isOpen()) part('total').focus({ preventScroll: true });
      }
    });

    function reset() {
      session.reset();
      clearLookup(); chooseSeed(null); lookupField.value = '';
      relationTypes.replaceChildren(); updateTypesSummary();
      syncLookupMode();
      part('settings').open = true;
    }

    byId('project').addEventListener('change', reset);
    document.addEventListener('workspace:scope-changed', event => { if (event.detail?.page === 'search') reset(); });
    document.addEventListener('graph-workspace:invalidated', reset);
    document.addEventListener('graph-workspace:ready', () => { if (isOpen()) refreshRelationshipTypes(); });
    if (byId('predicate-scope')) new MutationObserver(() => { if (isOpen()) refreshRelationshipTypes(); })
      .observe(byId('predicate-scope'), { childList: true });
    updateFilterInput();
    syncLookupMode();
    render(session.snapshot());
    root.NeighborhoodSearch = Object.freeze({ open: openPanel, openForEntity, searchForEntity, pickFromGraph });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})(globalThis);
