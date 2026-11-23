/* Shared community browsing, background build status and report-based questions. */
(() => {
  'use strict';

  function communityScope(value = {}) {
    const {platforms, businesses, audiences} = value.applicability || {};
    const restricted = dimension => Boolean(dimension && dimension.mode !== 'unknown');
    if (value.known_at || value.valid_at || value.filters || value.include_unknown === false ||
        restricted(businesses) || restricted(audiences) || value.kinds?.length || value.ontology_ids?.length ||
        (value.ontology_scope && value.ontology_scope !== 'all')) return null;
    if (!restricted(platforms)) return {kind:'project'};
    if (platforms.mode === 'specific' && platforms.values?.length === 1) {
      return {kind:'platform', platform:platforms.values[0]};
    }
    return null;
  }
  if (typeof module === 'object' && module.exports) module.exports = {communityScope};
  if (typeof window === 'undefined') return;

  const get = id => document.getElementById(id);
  const search = get('tab-search'), qa = get('tab-qa'), project = get('project');
  const browser = get('community-browser'), questions = get('community-questions');
  const buildView = get('tab-community-index');
  if (!browser || !questions || !buildView || !project) return;

  const scopeLabel = range => range.kind === 'platform' ? range.platform : '整个项目';
  const messages = {
    disabled:'这个范围还没有构建社区索引。',
    queued:'社区构建已排队。',
    building:'正在构建社区。',
    ready:'社区索引可用。',
    stale:'图谱已更新，正在等待社区索引更新。',
    partial:'社区已划分，部分报告尚不可用。',
    failed:'社区构建失败，可以重试。',
    sparse:'当前正式图谱的关系较少，可用社区覆盖有限。',
  };
  const buildStateLabels = {
    disabled:'未构建', queued:'排队中', building:'构建中', ready:'可用', stale:'待更新',
    partial:'报告未齐', failed:'构建失败', unavailable:'状态读取失败',
  };
  const stateOf = value => {
    const state = typeof value === 'string' ? value : value?.state;
    return state === 'ready' && value.change_pending ? 'stale' : state;
  };
  const activeBuild = value => ['queued','building'].includes(value?.state) ||
    ['queued','running'].includes(value?.job?.status);
  const reportLabel = value => ({pending:'报告待生成', ready:'报告可用', failed:'报告生成失败',
    building:'正在生成报告'})[value] || '报告待生成';
  const titleOf = community => community.report_title || community.title || '社区';
  const freshView = () => ({
    override:null, anchor:null, keyword:'', trail:[], offset:0, request:0, selected:new Map(),
    revision:null, staleSelection:false,
  });
  let browse = freshView(), ask = freshView(), buildRequest = 0, buildTimer, buildScope;

  function make(tag, content, className) {
    const element = document.createElement(tag);
    if (content != null) element.textContent = content;
    if (className) element.className = className;
    return element;
  }
  function action(label, handler) {
    const button = make('button',label,'secondary');
    button.type = 'button';
    button.addEventListener('click',handler);
    return button;
  }
  function currentScope(page) {
    const context = window.GraphWorkspaceContext?.read?.();
    return communityScope({
      ...window.WorkspaceContext?.read(project.value,page),
      ...(context?.projectId === project.value ? {
        ontology_scope:context.scope?.ontology_scope, ontology_ids:context.scope?.ontology_ids,
      } : {}),
    });
  }
  function request(id, suffix, data, options) {
    return api('/api/projects/' + encodeURIComponent(id) + '/communities' + suffix,data,'POST',
      {timeoutMs:data.generate ? (data.mode === 'global' ? 600000 : 180000) : 30000,...options});
  }
  function note(element, text, error = false) {
    element.textContent = text;
    element.classList.toggle('error',error);
  }
  function appliedRange(view,page,notice,useProject) {
    const range = view.override || currentScope(page);
    useProject.hidden = !view.override && range?.kind === 'project';
    useProject.textContent = view.override ? '恢复当前筛选范围' : '切换到全项目社区';
    note(notice, range ? '范围 · ' + scopeLabel(range) : '已应用的筛选暂无可用的社区构建范围。可调整筛选，或明确切换到全项目社区。');
    return range;
  }
  function showStatus(element, value) {
    const state = stateOf(value);
    const counts = value?.counts || {};
    const job = value?.job;
    const progress = job?.stage && activeBuild(value)
      ? `${job.stage}${typeof job.progress === 'number' ? `（${job.progress}%）` : ''}` : '';
    const error = value?.error || job?.error;
    const description = messages[state];
    element.replaceChildren();
    element.classList.toggle('error',state === 'failed');
    if (description) element.append(make('span',description,'community-status-label'));
    if (state !== 'disabled') {
      if (Number.isFinite(counts.covered_entities) && Number.isFinite(counts.entities)) {
        element.append(make('span',`实体覆盖 ${counts.covered_entities} / ${counts.entities}`));
      }
      if (counts.isolated_entities) element.append(make('span',`${counts.isolated_entities} 个孤立实体`));
      if (Number.isFinite(counts.reports_ready)) {
        element.append(make('span',`可用报告 ${counts.reports_ready} / ${counts.communities ?? 0}`));
      }
      if (value?.built_at) element.append(make('span',`更新于 ${window.DisplayTime.format(value.built_at)}`));
    }
    if (progress) element.append(make('span',progress));
    if (error) element.append(make('span',error));
  }
  function searchForKeyword(text) {
    browse.keyword = text.trim().slice(0,400);
    browse.trail = [];
    browse.offset = 0;
    loadBrowse();
  }
  function setBrowserVisible(visible, text) {
    browser.hidden = !visible;
    if (!visible) {
      browse.request++;
    } else if (text !== undefined && text.trim().slice(0,400) !== browse.keyword) {
      browse.anchor = null;
      searchForKeyword(text);
    } else {
      loadBrowse();
    }
  }
  function showQuestionView(view) {
    qa.dataset.communityActive = view;
    questions.hidden = view !== 'questions';
    for (const button of qa.querySelectorAll('[data-community-view]')) {
      button.setAttribute('aria-pressed',String(button.dataset.communityView === view));
    }
    if (view === 'questions') loadQuestionCommunities();
    else ask.request++;
  }
  function openEvidence(evidence,id) {
    if (project.value !== id) return;
    if (evidence.source_version_id && window.SourceReader) {
      return window.SourceReader.open({
        projectId:id, versionId:evidence.source_version_id, location:evidence.source_location,
        assertionId:evidence.assertion_id, chunkVersionId:evidence.chunk_version_id,
        focusText:evidence.quote, matchKind:'keyword',
      });
    }
    if (evidence.quote) window.openFrozenSourceEvidence?.({title:evidence.title,highlight:evidence.quote});
  }
  function evidenceKey(evidence) {
    const version = evidence.source_version_id || evidence.document_version_id;
    const location = [evidence.chunk_version_id,evidence.source_location,evidence.start_char,evidence.end_char];
    if (version && location.some(value => value != null)) return JSON.stringify([version,location,evidence.quote]);
    return evidence.id;
  }
  function disclosure(label) {
    const panel = make('details',null,'community-disclosure');
    panel.append(make('summary',label));
    return panel;
  }
  function evidenceSection(label, items, id, numbered = false) {
    const panel = disclosure(''), list = make('ul',null,'community-source-groups');
    const groups = new Map(), byKey = new Map(), byId = new Map();
    for (const evidence of items) {
      const key = evidenceKey(evidence);
      if (byKey.has(key)) {
        byId.set(evidence.id,byKey.get(key));
        continue;
      }
      const title = evidence.kind === 'record' ? '图谱记录' : evidence.title || '原文证据';
      const groupKey = evidence.source_version_id || evidence.document_version_id || evidence.document_id || title;
      if (!groups.has(groupKey)) {
        const group = make('li');
        const entries = make('ul',null,'community-source-excerpts');
        group.append(make('p',title,'community-source-title'),entries);
        list.append(group);
        groups.set(groupKey,entries);
      }
      const row = make('li',null,'community-source-excerpt');
      const number = byKey.size + 1;
      row.tabIndex = -1;
      if (numbered) row.append(make('span',`[${number}]`,'community-citation-number'));
      const validity = {expired:'历史原文', not_yet_effective:'尚未生效', unknown:'有效性待核对', conflict:'有效性有冲突'};
      if (validity[evidence.validity_status]) row.append(make('span',validity[evidence.validity_status],'subtle'));
      if (evidence.quote) row.append(make('blockquote',evidence.quote));
      if (evidence.source_version_id || (evidence.kind !== 'record' && evidence.quote)) {
        row.append(action('查看原文',() => openEvidence(evidence,id)));
      }
      groups.get(groupKey).append(row);
      const ref = {row,number};
      byKey.set(key,ref);
      byId.set(evidence.id,ref);
    }
    panel.firstElementChild.textContent = `${label} · ${byKey.size}`;
    panel.append(list);
    return {panel,byKey,byId};
  }
  function citationButton(ref, sources) {
    const button = action(`[${ref.number}]`,() => {
      sources.panel.open = true;
      ref.row.focus();
    });
    button.classList.add('community-citation');
    button.setAttribute('aria-label',`定位引用 ${ref.number}`);
    return button;
  }
  function openInGraph(member,range,id) {
    if (project.value !== id) return;
    const applicability = window.WorkspaceContext?.read(id,'search')?.applicability || {};
    const requested = applicability.platforms;
    if (range.kind === 'platform' && requested && requested.mode !== 'unknown' &&
        (requested.mode !== 'specific' || !requested.values.includes(range.platform))) {
      status('图谱的平台筛选与该社区范围不同，请先调整图谱筛选。',true);
      return;
    }
    window.RetrievalWorkspace?.select('knowledge');
    const applied = range.kind === 'platform' ? {
      applicability:{...applicability,platforms:{mode:'specific',values:[range.platform]}},
    } : {};
    Promise.resolve(window.openKnowledgeRecord?.({id:member.id,kind:'entity',projectId:id,
      version_id:member.version_id,scope:applied,view:'local'})).catch(error => status(error.message,true));
  }
  function showReport(host, community, sources, detailed = false) {
    const report = community.report || community;
    const text = report.summary || community.summary;
    if (text) {
      if (detailed) host.append(make('h3','报告摘要'));
      host.append(make('p',text,'community-report-text'));
    }
    if (!report.findings?.length) return;
    const findings = make('ol',null,'community-findings');
    for (const finding of report.findings) {
      const item = make('li');
      item.append(make('p',finding.text,'community-report-text'));
      const cited = new Set((finding.citations || []).map(key => sources?.byId.get(key)).filter(Boolean));
      if (cited.size) {
        const citations = make('div',null,'community-citations');
        for (const ref of cited) citations.append(citationButton(ref,sources));
        item.append(citations);
      }
      findings.append(item);
    }
    host.append(make(detailed ? 'h3' : 'h5','主要发现'),findings);
  }
  function communityMetrics(community) {
    const fields = make('div',null,'community-metadata');
    const reportStatus = community.report_status ?? community.report?.status;
    const label = make('span',reportLabel(reportStatus),'community-report-status');
    label.dataset.state = reportStatus || 'pending';
    fields.append(label,make('span',`${community.member_count ?? community.members?.length ?? 0} 个实体`),
      make('span',`${community.relationship_count ?? community.relationships?.length ?? 0} 条关系`));
    return fields;
  }
  function showCommunityGraph(rail,community,range,id) {
    const members = disclosure(`成员 · ${community.member_count ?? community.members?.length ?? 0}`);
    const memberList = make('ul',null,'community-record-list');
    for (const member of community.members || []) {
      const item = make('li');
      item.append(make('span',member.text || member.label || member.id),
        action('在图谱中展开',() => openInGraph(member,range,id)));
      memberList.append(item);
    }
    if (memberList.children.length) {
      members.append(memberList);
      rail.append(members);
    }
    const relationships = disclosure(`关系 · ${community.relationship_count ?? community.relationships?.length ?? 0}`);
    const relationList = make('ul',null,'community-record-list');
    for (const relation of community.relationships || []) {
      const text = relation.text || [relation.subject_text,relation.type,relation.object_text].filter(Boolean).join(' · ');
      relationList.append(make('li',text));
    }
    if (relationList.children.length) {
      relationships.append(relationList);
      rail.append(relationships);
    }
  }
  function renderDetail(community,range,id) {
    const host = get('community-results');
    const revision = community.revision || community.status?.revision;
    const title = make('h2',titleOf(community),'community-detail-title');
    title.tabIndex = -1;
    const heading = make('div',null,'community-detail-heading');
    const introduction = make('div');
    const metadata = communityMetrics(community);
    metadata.append(make('span',`范围 · ${scopeLabel(range)}`));
    introduction.append(title,metadata);
    heading.append(introduction);
    const layout = make('div',null,'community-report-layout');
    const report = make('article',null,'community-report-card');
    const rail = make('section',null,'community-report-rail');
    rail.append(make('h3','成员与证据'));
    const findings = community.report?.findings || community.findings || [];
    const cited = new Set(findings.flatMap(finding => finding.citations || []));
    const reportEvidence = (community.report_evidence || community.evidence || []).filter(item => cited.has(item.id));
    const sources = evidenceSection('报告引用',reportEvidence,id,true);
    if (sources.byKey.size) rail.append(sources.panel);
    if (community.report?.error) report.append(make('p',community.report.error,'error'));
    showReport(report,community,sources,true);
    if (!report.children.length) report.append(make('p','当前社区暂无可阅读的报告。','community-report-text'));
    if (community.children?.length) {
      const children = disclosure(`子社区 · ${community.children.length}`);
      const entries = make('ul',null,'community-record-list');
      for (const child of community.children) {
        const row = make('li');
        row.append(action(titleOf(child),() => {
          browse.trail.push({id:child.id,title:titleOf(child),offset:0,revision});
          loadBrowse({focusResult:true});
        }));
        entries.append(row);
      }
      children.append(entries);
      rail.append(children);
    }
    showCommunityGraph(rail,community,range,id);
    const otherEvidence = (community.evidence || []).filter(item => !sources.byKey.has(evidenceKey(item)));
    if (otherEvidence.length) rail.append(evidenceSection('其他证据',otherEvidence,id).panel);
    const detailOffset = community.detail_offset || 0, detailLimit = community.detail_limit || 200;
    const detailTotal = Math.max(community.member_count || 0,community.relationship_count || 0,community.evidence_count || 0);
    if (detailTotal > detailLimit) {
      const pager = make('div',null,'community-pagination');
      const changePage = amount => {browse.trail.at(-1).offset = detailOffset + amount; loadBrowse();};
      const previous = action('上一页成员与证据',() => changePage(-detailLimit));
      const next = action('下一页成员与证据',() => changePage(detailLimit));
      previous.disabled = detailOffset === 0;
      next.disabled = detailOffset + detailLimit >= detailTotal;
      pager.append(previous,next);
      rail.append(pager);
    }
    if ((community.report_status ?? community.report?.status) === 'ready') heading.append(action('就此社区提问',() => {
      window.KnowledgeTabs?.open('qa');
      get('community-question-mode').value = 'keyword';
      const qaRange = ask.override || currentScope('qa');
      if (JSON.stringify(qaRange) === JSON.stringify(range)) {
        ask.selected.clear();
        ask.revision = revision || null;
        ask.staleSelection = false;
        ask.selected.set(community.id,titleOf(community));
        showQuestionView('questions');
      } else {
        showQuestionView('questions');
        note(get('community-ask-status'),'问答范围与此社区不同。请检查问答的已应用范围，再选择社区。');
      }
    }));
    layout.append(report,rail);
    host.replaceChildren(heading,layout);
    return title;
  }
  function renderKeywordMatch(match) {
    const labels = {
      report_title:'报告标题', report_summary:'报告摘要', community_title:'社区名称',
      entity:'成员实体', relationship:'图谱关系', evidence:'原文引句',
    };
    const box = make('div',null,'community-keyword-match');
    box.append(make('span','命中' + (labels[match.field] || '社区内容'),'community-keyword-field'));
    const excerpt = make('p',null,'community-card-summary');
    excerpt.append(match.before,make('mark',match.term),match.after);
    box.append(excerpt);
    if (match.source_title) {
      const source = make('span',match.source_title,'community-keyword-source');
      source.title = match.source_title;
      box.append(source);
    }
    return box;
  }
  function renderBrowseList(result) {
    const host = get('community-results'), grid = make('div',null,'community-card-grid');
    host.replaceChildren();
    for (const item of result.communities || []) {
      const card = make('article',null,'community-card');
      card.append(make('h3',titleOf(item)),communityMetrics(item));
      if (item.keyword_match?.term) card.append(renderKeywordMatch(item.keyword_match));
      else if (item.summary) card.append(make('p',item.summary,'community-card-summary'));
      const footer = make('div',null,'community-card-actions');
      footer.append(action('打开社区',() => {
        browse.trail = [{id:item.id,title:titleOf(item),offset:0,revision:result.status?.revision}];
        loadBrowse({focusResult:true});
      }));
      card.append(footer);
      grid.append(card);
    }
    if (grid.children.length) {
      const summary = make('h3',Number.isFinite(result.total) ? `社区结果 · ${result.total}` : '社区结果','community-results-heading');
      summary.tabIndex = -1;
      host.append(summary,grid);
    }
    if (!grid.children.length && ['ready','partial','sparse'].includes(result.status?.state || result.status)) {
      const sparse = result.status?.counts?.communities === 0;
      host.append(make('p',browse.anchor ? '该实体在此范围内没有已收录的社区。' :
        sparse ? '当前范围的正式关系尚未形成可展示的社区。' : '没有符合条件的社区。','community-empty'));
    }
    if (result.status?.state === 'disabled' && window.Auth?.isAdmin?.()) {
      host.append(action('前往社区索引',() => {
        const range = browse.override || currentScope('search');
        if (range?.kind === 'platform') {
          get('community-build-kind').value = 'platform';
          get('community-build-platform').value = range.platform;
          get('community-build-platform-row').hidden = false;
          loadPlatforms();
        } else {
          get('community-build-kind').value = 'project';
          get('community-build-platform-row').hidden = true;
        }
        window.KnowledgeTabs?.open('runtime');
        window.RuntimeView?.show('communities');
      }));
    } else if (result.status?.browseable === false) {
      host.append(action('刷新社区',loadBrowse));
    }
    if (browse.offset || browse.offset + 24 < result.total) {
      const pagination = make('div',null,'community-pagination');
      const previous = action('上一页',() => {browse.offset -= 24; loadBrowse();});
      const next = action('下一页',() => {browse.offset += 24; loadBrowse();});
      previous.disabled = !browse.offset;
      next.disabled = browse.offset + 24 >= result.total;
      pagination.append(previous,make('span',`第 ${browse.offset + 1}–${Math.min(browse.offset + 24,result.total)} 条`),next);
      host.append(pagination);
    }
    return host.querySelector('.community-results-heading');
  }
  async function loadBrowse({focusResult = false} = {}) {
    const id = project.value, view = browse, ticket = ++view.request;
    const notice = get('community-browse-status');
    const range = appliedRange(view,'search',get('community-browse-range'),get('community-browse-all'));
    const host = get('community-results');
    const entry = view.trail.at(-1);
    const path = get('community-browse-path'), trail = get('community-trail');
    browser.dataset.presentation = entry && id && range ? 'detail' : 'list';
    path.hidden = !entry;
    get('community-browse-back').textContent = view.trail.length > 1 ? '返回上一级' : '返回社区列表';
    get('community-anchor').hidden = !view.anchor;
    get('community-anchor-label').textContent = view.anchor?.label || '';
    trail.hidden = view.trail.length < 2;
    trail.textContent = view.trail.slice(0,-1).map(item => item.title).join(' / ');
    host.replaceChildren();
    if (!id || !range) {
      note(notice,id ? '' : '请先选择项目。');
      return;
    }
    note(notice,'正在读取社区…');
    try {
      const data = entry ? await request(id,'/detail',{scope:range,community_id:entry.id,offset:entry.offset || 0,
        expected_revision:entry.revision}) :
        await request(id,'',{scope:range,keyword:view.keyword,anchor:view.anchor?.id || '',limit:24,offset:view.offset});
      if (view !== browse || ticket !== view.request || id !== project.value) return;
      showStatus(notice,data.status);
      const heading = entry ? renderDetail(data.community || data,range,id) : renderBrowseList(data);
      if (focusResult) (heading || get('query')).focus();
    } catch (error) {
      if (view !== browse || ticket !== view.request || id !== project.value) return;
      if (error.status === 409 || error.status === 404) {
        browser.dataset.presentation = 'list';
        path.hidden = true;
        host.replaceChildren(action('返回社区列表',() => {view.trail = []; loadBrowse();}));
      } else {
        host.replaceChildren(action('重新加载',() => loadBrowse()));
      }
      note(notice,error.message,true);
    }
  }
  function renderSelections() {
    const selected = get('community-selected');
    selected.replaceChildren(...[...ask.selected].map(([id,title]) => action(title + ' ×',() => {
      ask.selected.delete(id);
      renderSelections();
      loadQuestionCommunities();
    })));
    if (ask.staleSelection) {
      selected.append(action('改用当前范围内全部社区',() => {
        ask.staleSelection = false;
        loadQuestionCommunities();
      }));
    } else if (!ask.selected.size) selected.append(make('span','未指定社区，将在当前范围内匹配相关报告。','subtle'));
  }
  async function loadQuestionCommunities() {
    const id = project.value, view = ask, ticket = ++view.request;
    const range = appliedRange(view,'qa',get('community-ask-range'),get('community-ask-all'));
    const select = get('community-question-select'), notice = get('community-ask-status');
    const mode = get('community-question-mode').value;
    const level = get('community-question-level'), previousLevel = level.value;
    const hint = get('community-question-mode-help');
    get('community-question-keyword-options').hidden = get('community-selected').hidden = mode !== 'keyword';
    get('community-question-level-row').hidden = mode !== 'global';
    hint.hidden = mode === 'keyword';
    hint.textContent = mode === 'global' ? '按选定层级逐批处理报告，结果会显示实际覆盖与证据缺口。' :
      '用问题召回语义相关的社区报告，并核对用于综合的原文。';
    const submit = get('community-question-submit'), generate = get('community-question-generate');
    submit.disabled = true;
    generate.disabled = true;
    select.replaceChildren(make('option','选择社区')); select.options[0].value = '';
    renderSelections();
    if (!id || !range) {
      note(notice,id ? '' : '请先选择项目。');
      return;
    }
    note(notice,'正在读取可用社区…');
    try {
      const data = await request(id,'',{scope:range,keyword:mode === 'keyword' ? get('community-question-filter').value.trim() : '',limit:40});
      if (view !== ask || ticket !== view.request || id !== project.value) return;
      showStatus(notice,data.status);
      if (data.status?.browseable === false || data.status?.stale === true || data.status?.change_pending === true) {
        get('community-answer').replaceChildren();
        return;
      }
      const revision = data.status?.revision || null;
      if (view.selected.size && view.revision && revision && view.revision !== revision) {
        view.selected.clear();
        view.staleSelection = true;
        get('community-answer').replaceChildren();
      }
      view.revision = revision;
      if (mode === 'semantic') {
        submit.disabled = generate.disabled = data.status?.vector_index?.state !== 'ready';
        if (submit.disabled) note(notice,'社区报告向量未就绪，请在构建与任务的社区索引中更新。');
        return;
      }
      if (mode === 'global') {
        level.replaceChildren(...(data.status?.levels || []).map(item =>
          new Option(`第 ${item.level + 1} 层 · ${item.reports_ready} / ${item.communities} 份可用报告`,String(item.level))));
        if ([...level.options].some(option => option.value === previousLevel)) level.value = previousLevel;
        submit.disabled = generate.disabled = !level.options.length;
        return;
      }
      if (view.staleSelection) {
        note(notice,'社区已更新，请重新选择社区或明确使用当前范围内全部社区。');
        renderSelections();
      }
      for (const community of data.communities || []) {
        if (ask.selected.has(community.id) || community.report_status !== 'ready') continue;
        const option = make('option',titleOf(community)); option.value = community.id; select.append(option);
      }
      const reportCount = data.status?.counts?.reports_ready ?? (data.communities || [])
        .filter(community => community.report_status === 'ready').length;
      submit.disabled = generate.disabled = reportCount === 0 || view.staleSelection;
    } catch (error) {
      if (view === ask && ticket === view.request && id === project.value) note(notice,error.message,true);
    }
  }
  function renderQuestionResult(host,result,id) {
    const body = make('div',null,'community-question-result-body');
    const evidence = result.evidence || [];
    const cited = new Set(result.citations || []);
    const used = evidence.filter(item => cited.has(item.id));
    const sources = evidenceSection('引用与证据',used.length ? used : evidence,id,true);
    const coverage = result.coverage;
    if (coverage?.mode === 'global') {
      body.append(make('p',`第 ${coverage.level + 1} 层 · ${coverage.reports_ready} / ${coverage.target_communities} 份报告可用 · ${coverage.reports_with_eligible_evidence} 份有可用原文` +
        (result.generated ? ` · 本次归纳 ${coverage.reports_mapped} / ${coverage.target_communities} 份` : ''),'community-empty'));
      if (coverage.preview_communities < coverage.included_communities) {
        body.append(make('p',`下方预览 ${coverage.preview_communities} 份报告。`,'community-mode-help'));
      }
    }
    if (coverage?.retrieval === 'representative') {
      body.append(make('p','关键词未匹配到社区，以下是当前范围里的代表性证据。','community-empty'));
    }
    if (coverage?.mode !== 'global' && coverage?.complete === false && result.communities?.length) {
      const total = coverage.selected_communities || coverage.matched_communities || coverage.scope_communities;
      const included = result.generated ? coverage.reports_included : coverage.included_communities;
      if (total && included < total) {
        const verb = result.generated ? '综合回答使用了' : '显示了';
        body.append(make('p',`${verb} ${included} / ${total} 个社区的${result.generated ? '报告' : '结果'}。`,'community-empty'));
      }
      if (coverage.evidence_returned < coverage.evidence_available) {
        body.append(make('p',`返回了 ${coverage.evidence_returned} / ${coverage.evidence_available} 条证据。`,'community-empty'));
      }
    }
    if (result.answer && (result.generated || !evidence.length)) {
      const answer = make('article',null,'community-report-card');
      if (result.generated) answer.append(make('h3','社区回答'));
      if (result.answer_purpose === 'document_explanation') {
        answer.append(make('p','按报告与原文说明，有效性请见引用状态。','community-mode-help'));
      } else if (result.as_of) {
        answer.append(make('p',`业务核验时点 ${window.DisplayTime.format(result.as_of)}`,'community-mode-help'));
      }
      const content = make('p',null,'community-report-text');
      for (const part of result.answer.split(/(\[E-[a-z0-9]+\])/gi)) {
        const reference = sources.byId.get(part.slice(1,-1));
        content.append(reference ? citationButton(reference,sources) : document.createTextNode(part));
      }
      answer.append(content);
      body.append(answer);
    }
    if (result.communities?.length) {
      body.append(make('h3',coverage?.retrieval === 'representative' ? '代表性社区' : '匹配社区','community-results-heading'));
      for (const community of result.communities) {
        const report = make('article',null,'community-report-card');
        report.append(make('h4',titleOf(community)),communityMetrics(community));
        if (['semantic','global'].includes(coverage?.mode)) {
          report.append(make('p','报告摘要 · 回答依据以核验后的原文为准','community-mode-help'));
        }
        if (Number.isFinite(community.semantic_score)) {
          report.append(make('p',`语义相似度 ${community.semantic_score.toFixed(2)}`,'community-mode-help'));
        }
        showReport(report,community,sources);
        body.append(report);
      }
    }
    if (!sources.byKey.size) {
      host.replaceChildren(body);
      return;
    }
    const rail = make('section',null,'community-report-rail');
    rail.append(make('h4','证据来源'),sources.panel);
    sources.panel.open = !result.generated;
    const layout = make('div',null,'community-report-layout');
    layout.append(body,rail);
    host.replaceChildren(layout);
  }
  async function queryReports(generate) {
    if (!get('community-question-form').reportValidity()) return;
    const id = project.value, view = ask, ticket = ++view.request;
    const range = view.override || currentScope('qa');
    const mode = get('community-question-mode').value, level = get('community-question-level').value;
    if (!id || !range || (mode === 'keyword' && view.staleSelection) || (mode === 'global' && level === '')) return;
    const notice = get('community-ask-status'), host = get('community-answer');
    const question = get('community-question-text').value.trim();
    if (!question) {
      note(notice,'请输入问题。',true);
      get('community-question-text').focus();
      return;
    }
    const buttons = [get('community-question-submit'),get('community-question-generate')];
    for (const button of buttons) button.disabled = true;
    note(notice,generate && mode === 'global' ? '正在核对证据并分批归纳所选层级…' :
      generate ? '正在根据社区报告生成答案…' : '正在查找社区报告…');
    try {
      const result = await request(id,'/ask',{scope:range,community_ids:mode === 'keyword' ? [...view.selected.keys()] : [],
        expected_revision:view.revision || undefined,question,generate,
        ...(mode === 'keyword' ? {} : {mode}),...(mode === 'global' ? {level:Number(level)} : {})});
      if (view !== ask || ticket !== view.request || id !== project.value) return;
      renderQuestionResult(host,result,id);
      const matches = result.communities || [];
      const reports = matches.filter(community => (community.report_status ?? community.report?.status) === 'ready');
      note(notice,result.generation_error ? '生成失败 · ' + result.generation_error :
        result.generated ? '已生成回答。' :
        generate && mode !== 'keyword' && !result.coverage?.reports_with_eligible_evidence ? '已核验原文不足，无法综合回答。' :
        result.coverage?.retrieval === 'representative' ? '已显示代表性社区的证据摘录。' :
        reports.length ? `找到 ${reports.length} 份相关报告。` :
        matches.length ? `找到 ${matches.length} 个社区，报告尚未生成。` : '当前范围内没有匹配的社区报告。',
      Boolean(result.generation_error));
    } catch (error) {
      if (view !== ask || ticket !== view.request || id !== project.value) return;
      if (error.status === 409 || error.status === 404) {
        host.replaceChildren();
        if (mode === 'keyword' && view.selected.size) {
          view.selected.clear();
          view.staleSelection = true;
        }
        const refresh = loadQuestionCommunities();
        const refreshTicket = view.request;
        await refresh;
        if (mode !== 'keyword' && view === ask && view.request === refreshTicket && id === project.value) {
          note(notice,error.message,true);
        }
      } else note(notice,error.message,true);
    } finally {
      if (view === ask && ticket === view.request && id === project.value) {
        for (const button of buttons) button.disabled = false;
      }
    }
  }
  function buildRange() {
    const kind = get('community-build-kind').value;
    const platform = get('community-build-platform').value.trim();
    if (kind === 'project') return {kind};
    return platform ? {kind,platform} : null;
  }
  function showBuildText(id,text) {
    const element = get(id);
    element.textContent = text;
    element.hidden = !text;
  }
  function renderBuildState(state,message,value = {}) {
    get('community-build-monitor').dataset.state = state;
    showBuildText('community-build-state-label',buildStateLabels[state] || '');
    const active = activeBuild(value), job = value.job;
    const parts = [message,active && job?.stage,value.error || job?.error].filter(Boolean);
    const notice = get('community-build-status');
    const description = parts.join(' ');
    if (notice.textContent !== description) notice.textContent = description;
    const progress = get('community-build-progress');
    const percent = job?.progress;
    get('community-build-progress-row').hidden = !active;
    if (Number.isFinite(percent)) progress.value = percent;
    else progress.removeAttribute('value');
    get('community-build-percent').textContent = Number.isFinite(percent) ? `${progress.value}%` : '';

    const counts = value.counts;
    get('community-build-metrics').hidden = !value.revision || !counts;
    if (value.revision && counts) {
      get('community-built-communities').textContent = counts.communities ?? '—';
      get('community-built-coverage').textContent = `${counts.covered_entities ?? '—'} / ${counts.entities ?? '—'}`;
      get('community-built-reports').textContent = `${counts.reports_ready ?? '—'} / ${counts.communities ?? '—'}`;
      showBuildText('community-built-isolates',counts.isolated_entities ? `${counts.isolated_entities} 个孤立实体` : '');
      showBuildText('community-built-report-detail',[
        counts.reports_failed && `${counts.reports_failed} 份失败`,
        counts.reports_pending && `${counts.reports_pending} 份待生成`,
      ].filter(Boolean).join(' · '));
    }
    showBuildText('community-build-updated',value.built_at ? `上次更新 ${window.DisplayTime.format(value.built_at)}` : '');
  }
  async function previewGraph(id,range,ticket) {
    const scope = range.kind === 'platform' ? {
      applicability:{platforms:{mode:'specific',values:[range.platform]}},
    } : {};
    try {
      const data = await api('/api/projects/' + encodeURIComponent(id) + '/dashboard',{...scope,kinds:['entity','relation']},
        'POST',{timeoutMs:15000});
      if (id !== project.value || ticket !== buildRequest) return;
      const counts = data.counts;
      if (counts) showBuildText('community-build-preview',`图谱预览 · ${counts.entity || 0} 个实体 · ${counts.relation || 0} 条关系`);
    } catch {
      if (id === project.value && ticket === buildRequest) {
        showBuildText('community-build-preview','暂时无法预览数量，构建时将核对当前图谱。');
      }
    }
  }
  async function loadPlatforms() {
    const id = project.value, list = get('community-platform-values');
    list.replaceChildren();
    if (!id) return;
    try {
      const values = await api('/api/projects/' + encodeURIComponent(id) + '/applicability-values',
        undefined,'GET',{timeoutMs:15000});
      if (id !== project.value) return;
      list.replaceChildren(...(values.platforms || []).map(platform => {
        const option = make('option'); option.value = platform; return option;
      }));
    } catch {
      // The scope can still be entered directly.
    }
  }
  const buildViewVisible = () => window.RuntimeView?.current === 'communities' &&
    !get('tab-runtime').classList.contains('hidden');
  async function loadBuild({start = false,retry = false,vectorize = false} = {}) {
    clearTimeout(buildTimer);
    const id = project.value, ticket = ++buildRequest, range = buildRange();
    const build = get('community-build'), retryButton = get('community-build-retry');
    const refresh = get('community-build-refresh'), vector = get('community-build-vector');
    build.disabled = retryButton.disabled = refresh.disabled = vector.disabled = true;
    const scopeKey = JSON.stringify([id,range]);
    if (scopeKey !== buildScope) {
      buildScope = scopeKey;
      build.textContent = '构建社区';
      retryButton.hidden = true;
      showBuildText('community-build-preview','');
      showBuildText('community-vector-status','');
      renderBuildState('loading','正在读取构建状态…');
    }
    if (!id || !range) {
      renderBuildState('waiting',id ? '请输入平台名称。' : '请先选择项目。');
      return;
    }
    if (start) get('community-build-status').textContent = '正在提交社区构建…';
    try {
      const data = await request(id,start ? '/build' : '/status',{scope:range,...(retry ? {retry_failed:true} : {}),
        ...(vectorize ? {vectorize:true} : {})});
      if (id !== project.value || ticket !== buildRequest) return;
      const value = data.status || data;
      const state = stateOf(value), active = activeBuild(value);
      renderBuildState(state,messages[state] || '暂未获取构建状态。',value);
      if (value.state === 'disabled') previewGraph(id,range,ticket);
      else showBuildText('community-build-preview','');
      build.textContent = value.enabled ? '检查并构建' : '构建社区';
      retryButton.textContent = state === 'failed' ? '重试构建' : '重试失败报告';
      retryButton.hidden = state !== 'failed' && !(state === 'partial' && value.counts?.reports_failed > 0);
      build.disabled = retryButton.disabled = active;
      const vectors = value.vector_index;
      const vectorLabel = {disabled:'报告向量尚未构建', stale:'报告向量待更新', failed:'报告向量构建失败',
        unavailable:'报告向量需要语义模型和 Milvus', ready:`报告向量已就绪 · ${vectors?.indexed ?? 0} 份`};
      showBuildText('community-vector-status',vectors ? [vectorLabel[vectors.state],vectors.error].filter(Boolean).join(' · ') : '');
      vector.textContent = vectors?.enabled ? '更新报告向量' : '构建报告向量';
      vector.disabled = active || !vectors?.available || !value.counts?.reports_ready;
      refresh.disabled = false;
      if (active && buildViewVisible()) {
        buildTimer = setTimeout(() => { if (buildViewVisible()) loadBuild(); },2500);
      }
    } catch (error) {
      if (id !== project.value || ticket !== buildRequest) return;
      build.disabled = retryButton.disabled = refresh.disabled = false;
      renderBuildState('unavailable',error.message);
    }
  }
  qa.querySelectorAll('[data-community-view]').forEach(button => {
    button.addEventListener('click',() => showQuestionView(button.dataset.communityView));
  });
  get('community-browse-ask').onclick = () => {
    window.KnowledgeTabs?.open('qa');
    showQuestionView('questions');
    get('community-question-text').focus();
  };
  get('community-browse-back').onclick = () => {browse.trail.pop(); loadBrowse({focusResult:true});};
  get('community-anchor-clear').onclick = () => {browse.anchor = null; browse.offset = 0; browse.trail = []; loadBrowse();};
  get('community-browse-all').onclick = () => {
    const {override:previous,keyword} = browse;
    browse = {...freshView(),keyword};
    browse.override = previous ? null : {kind:'project'};
    get('community-results').replaceChildren();
    loadBrowse();
  };
  get('community-ask-all').onclick = () => {
    const previous = ask.override;
    ask = freshView();
    ask.override = previous ? null : {kind:'project'};
    get('community-answer').replaceChildren();
    loadQuestionCommunities();
  };
  get('community-question-filter').addEventListener('change',loadQuestionCommunities);
  for (const id of ['community-question-mode','community-question-level']) {
    get(id).addEventListener('change',() => {
      get('community-answer').replaceChildren();
      loadQuestionCommunities();
    });
  }
  get('community-question-filter').addEventListener('keydown',event => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      loadQuestionCommunities();
    }
  });
  get('community-question-select').addEventListener('change',event => {
    const option = event.target.selectedOptions[0];
    if (ask.selected.size >= 32) {
      note(get('community-ask-status'),'最多选择 32 个社区。请移除一项，或清空选择以查询当前范围。',true);
      event.target.value = '';
      return;
    }
    if (option?.value) {
      ask.selected.set(option.value,option.textContent);
      ask.staleSelection = false;
    }
    loadQuestionCommunities();
  });
  get('community-question-form').addEventListener('submit',event => {
    event.preventDefault(); queryReports(false);
  });
  get('community-question-generate').onclick = () => queryReports(true);
  get('community-build-kind').addEventListener('change',event => {
    get('community-build-platform-row').hidden = event.target.value !== 'platform';
    if (event.target.value === 'platform') loadPlatforms();
    loadBuild();
  });
  get('community-build-platform').addEventListener('change',() => loadBuild());
  get('community-build-refresh').onclick = () => loadBuild();
  get('community-build').onclick = () => loadBuild({start:true});
  get('community-build-vector').onclick = () => loadBuild({start:true,vectorize:true});
  get('community-build-retry').onclick = () => loadBuild({start:true,retry:true});
  window.RuntimeView?.on('communities',loadBuild);
  function reset() {
    browse = {...freshView(),keyword:browse.keyword};
    ask = freshView();
    get('community-results').replaceChildren();
    get('community-answer').replaceChildren();
    if (!search.classList.contains('hidden') && !browser.hidden) loadBrowse();
    if (!qa.classList.contains('hidden') && qa.dataset.communityActive === 'questions') loadQuestionCommunities();
  }
  project.addEventListener('change',() => {
    browse.keyword = '';
    reset();
    get('community-build-kind').value = 'project';
    get('community-build-platform').value = '';
    get('community-build-platform-row').hidden = true;
    get('community-platform-values').replaceChildren();
    if (window.RuntimeView?.current === 'communities' && !get('tab-runtime').classList.contains('hidden')) loadBuild();
  });
  document.addEventListener('workspace:scope-changed',event => {
    if (['search','qa'].includes(event.detail?.page)) reset();
  });
  document.addEventListener('workspace:page-changed',event => {
    const page = event.detail?.page;
    if (page !== 'runtime') {clearTimeout(buildTimer);buildRequest++;}
    if (page === 'search' && !browser.hidden) loadBrowse();
    if (page === 'qa' && qa.dataset.communityActive === 'questions') loadQuestionCommunities();
  });
  window.CommunityWorkspace = {
    searchForKeyword, setBrowserVisible,
    openForEntity(id,label) {
      if (!id) return;
      browse = freshView();
      browse.anchor = {id,label:label || id};
      if (search.classList.contains('hidden')) window.KnowledgeTabs?.open('search');
      window.RetrievalWorkspace?.select('community',{reuseQuery:false});
    },
  };
})();
