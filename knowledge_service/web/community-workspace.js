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
  function questionMode(mode, selectedCount, vectorsReady) {
    if (mode !== 'auto') return mode;
    return selectedCount || !vectorsReady ? 'keyword' : 'semantic';
  }
  if (typeof module === 'object' && module.exports) module.exports = {communityScope, questionMode};
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
  const reportLabel = value => ({pending:'报告待生成', ready:'报告已生成', failed:'报告生成失败',
    building:'正在生成报告'})[value] || '报告待生成';
  const titleOf = community => community.report_title || community.title || '社区';
  const freshView = () => ({
    override:null, anchor:null, keyword:'', mode:'keyword', trail:[], offset:0, request:0, selected:new Map(),
    revision:null, staleSelection:false, vectorsReady:false,
  });
  let browse = freshView(), ask = freshView(), buildRequest = 0, buildTimer, buildScope;
  let supportsMatchOnly = true;

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
  async function askQuestion(id, body, isCurrent) {
    if (!body.match_only || supportsMatchOnly) {
      try {
        return await request(id,'/ask',body);
      } catch (error) {
        if (!body.match_only || error.status !== 422 || !error.message.includes('"match_only"')) throw error;
        supportsMatchOnly = false;
      }
    }
    if (!isCurrent()) return;
    const {match_only, ...legacy} = body;
    const preview = await request(id,'/ask',{...legacy,generate:false});
    if (preview.coverage?.retrieval === 'representative') {
      return {answer:'当前范围没有与问题匹配的社区报告。',communities:[],evidence:[],generated:false};
    }
    if (!legacy.generate || !preview.communities?.length || !preview.evidence?.length || !isCurrent()) return preview;
    return request(id,'/ask',{...legacy,community_ids:preview.communities.map(community => community.id),
      expected_revision:preview.status?.revision || legacy.expected_revision});
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
  function searchForQuery(text, mode = 'keyword') {
    browse.keyword = text.trim().slice(0,400);
    browse.mode = mode;
    browse.trail = [];
    browse.offset = 0;
    loadBrowse();
  }
  function setBrowserVisible(visible, text, mode = 'keyword') {
    browser.hidden = !visible;
    if (!visible) {
      browse.request++;
    } else if (text !== undefined && (text.trim().slice(0,400) !== browse.keyword || mode !== browse.mode)) {
      browse.anchor = null;
      searchForQuery(text,mode);
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
    document.dispatchEvent(new Event('community:question-view-changed'));
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
    if (evidence.quote) window.openFrozenSourceEvidence?.({title:evidence.title,highlight:evidence.quote,
      reason:'社区证据保存的原文片段'});
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
        const heading = make('p',numbered ? window.QaPresentation?.sourceTitle(title) || title : title,'community-source-title');
        heading.title = title;
        group.append(heading,entries);
        list.append(group);
        groups.set(groupKey,entries);
      }
      const row = make('li',null,'community-source-excerpt');
      const number = byKey.size + 1;
      row.tabIndex = -1;
      if (numbered) row.append(make('span',`[${number}]`,'community-citation-number'));
      const validity = {expired:'历史原文', not_yet_effective:'尚未生效', unknown:'有效性待核对', conflict:'有效性有冲突'};
      if (validity[evidence.validity_status]) row.append(make('span',validity[evidence.validity_status],'subtle'));
      if (evidence.quote) row.append(make('blockquote',numbered
        ? window.QaPresentation?.evidencePreview(evidence.quote) || evidence.quote : evidence.quote));
      if (evidence.source_version_id || (evidence.kind !== 'record' && evidence.quote)) {
        row.append(action(evidence.source_version_id ? '查看原文' : '查看引用片段',() => openEvidence(evidence,id)));
      }
      groups.get(groupKey).append(row);
      const ref = {row,number};
      byKey.set(key,ref);
      byId.set(evidence.id,ref);
    }
    panel.firstElementChild.textContent = `${label} · ${byKey.size}`;
    panel.append(list);
    return {panel,byKey,byId,sourceCount:groups.size};
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
    if ((community.report_status ?? community.report?.status) === 'ready') {
      const askButton = action('就此社区提问',() => {
        window.KnowledgeTabs?.open('qa');
        get('community-question-mode').value = 'auto';
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
      });
      askButton.classList.add('community-detail-ask');
      get('community-detail-actions').append(askButton);
    }
    layout.append(report,rail);
    host.replaceChildren(heading,layout);
    return title;
  }
  function renderKeywordMatch(match) {
    const labels = {
      report_title:'报告标题', report_summary:'报告摘要', community_title:'社区名称',
      entity:'成员实体名称', entity_alias:'成员实体别名', entity_id:'成员实体 ID', entity_type:'成员实体类型',
      relationship:'图谱关系文本', relationship_id:'图谱关系 ID', relationship_type:'图谱关系类型',
      evidence:'原文引句', graph_evidence:'图谱记录',
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
  function renderCommunityMatch(community, mode) {
    const score = community.semantic_score;
    const badges = window.SourceMatchView.rankBadges({mode, score});
    const details = make('div',null,'community-match-details');
    for (const badge of badges) details.append(make('span',badge,'community-match-score'));
    if (mode === 'keyword') {
      if (community.keyword_match?.term) details.append(renderKeywordMatch(community.keyword_match));
      else details.append(make('span','命中社区索引文本','community-keyword-field'));
    } else if (mode === 'semantic' && badges.length) {
      const field = make('span','匹配社区报告向量','community-keyword-field');
      field.title = '报告标题、摘要和主要发现共同参与向量匹配。';
      details.append(field);
    }
    return details.childElementCount ? details : null;
  }
  function renderBrowseList(result) {
    const host = get('community-results'), grid = make('div',null,'community-card-grid');
    host.replaceChildren();
    for (const item of result.communities || []) {
      const card = make('article',null,'community-card');
      card.append(make('h3',titleOf(item)),communityMetrics(item));
      const mode = browse.mode === 'semantic' && browse.keyword ? 'semantic' :
        browse.mode === 'keyword' && item.keyword_match?.term ? 'keyword' : null;
      const match = mode && renderCommunityMatch(item, mode);
      if (match) card.append(match);
      if (mode !== 'keyword' && item.summary) card.append(make('p',item.summary,'community-card-summary'));
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
      host.append(make('p',browse.mode === 'semantic' && browse.keyword ? '当前范围没有匹配的社区报告，可换一种描述或改用关键词。' :
        browse.anchor ? '该实体在此范围内没有已收录的社区。' :
        sparse ? '当前范围的正式关系尚未形成可展示的社区。' : '没有符合条件的社区。','community-empty'));
    }
    if (result.status?.browseable === false && result.status.state !== 'disabled') {
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
    get('community-browse-scope').hidden = range?.kind === 'project' && !view.override;
    const askButton = get('community-browse-ask'), buildButton = get('community-browse-build');
    askButton.hidden = buildButton.hidden = true;
    browser.removeAttribute('data-index-missing');
    const host = get('community-results');
    const entry = view.trail.at(-1);
    const path = get('community-browse-path'), trail = get('community-trail');
    get('community-anchor').hidden = !view.anchor;
    get('community-anchor-label').textContent = view.anchor?.label || '';
    if (!id || !range) {
      browser.dataset.presentation = 'list';
      path.hidden = true;
      host.replaceChildren();
      host.removeAttribute('aria-busy');
      note(notice,id ? '' : '请先选择项目。');
      return;
    }
    host.setAttribute('aria-busy','true');
    note(notice,'正在读取社区…');
    try {
      const data = entry ? await request(id,'/detail',{scope:range,community_id:entry.id,offset:entry.offset || 0,
        expected_revision:entry.revision}) :
        await request(id,'',{scope:range,keyword:view.keyword,anchor:view.anchor?.id || '',limit:24,offset:view.offset,
          ...(view.mode === 'semantic' && view.keyword ? {mode:'semantic'} : {})});
      if (view !== browse || ticket !== view.request || id !== project.value) return;
      browser.dataset.presentation = entry ? 'detail' : 'list';
      path.hidden = !entry;
      get('community-detail-actions').replaceChildren();
      get('community-browse-back').textContent = view.trail.length > 1 ? '返回上一级' : '返回社区列表';
      trail.hidden = view.trail.length < 2;
      trail.textContent = view.trail.slice(0,-1).map(item => item.title).join(' / ');
      const state = stateOf(data.status);
      const missingIndex = state === 'disabled';
      askButton.hidden = Boolean(entry) || !data.status?.browseable;
      buildButton.hidden = !missingIndex || !window.Auth?.isAdmin?.();
      browser.toggleAttribute('data-index-missing',missingIndex);
      if (missingIndex) note(notice,'当前范围尚未构建社区索引');
      else if (!entry && state === 'ready') note(notice,'');
      else showStatus(notice,data.status);
      const heading = entry ? renderDetail(data.community || data,range,id) : renderBrowseList(data);
      if (focusResult) (heading || get('query')).focus();
    } catch (error) {
      if (view !== browse || ticket !== view.request || id !== project.value) return;
      browser.dataset.presentation = 'list';
      path.hidden = true;
      if (entry && (error.status === 409 || error.status === 404)) {
        host.replaceChildren(action('返回社区列表',() => {view.trail = []; loadBrowse();}));
      } else {
        host.replaceChildren(action('重新加载',() => loadBrowse()));
      }
      note(notice,error.message,true);
    } finally {
      if (view === browse && ticket === view.request) host.removeAttribute('aria-busy');
    }
  }
  function renderSelections() {
    const selected = get('community-selected');
    const canSelect = ['auto','keyword'].includes(get('community-question-mode').value);
    selected.hidden = !canSelect || (!ask.selected.size && !ask.staleSelection);
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
    }
  }
  async function loadQuestionCommunities() {
    const id = project.value, view = ask, ticket = ++view.request;
    const scopeSummary = get('community-ask-range');
    const range = appliedRange(view,'qa',scopeSummary,get('community-ask-all'));
    scopeSummary.hidden = Boolean(range && !view.override);
    const select = get('community-question-select'), notice = get('community-ask-status');
    const requestedMode = get('community-question-mode').value;
    const canSelect = ['auto','keyword'].includes(requestedMode);
    const level = get('community-question-level'), previousLevel = level.value;
    const hint = get('community-question-mode-help');
    const indexStatus = get('community-ask-index-status');
    get('community-question-keyword-options').hidden = !canSelect;
    get('community-question-level-row').hidden = requestedMode !== 'global';
    hint.textContent = '';
    indexStatus.replaceChildren();
    const submit = get('community-question-submit');
    submit.disabled = true;
    select.replaceChildren(make('option','选择社区')); select.options[0].value = '';
    renderSelections();
    if (!id || !range) {
      note(notice,id ? '' : '请先选择项目。');
      return;
    }
    note(notice,'正在读取可用社区…');
    try {
      const data = await request(id,'',{scope:range,keyword:canSelect ? get('community-question-filter').value.trim() : '',limit:40});
      if (view !== ask || ticket !== view.request || id !== project.value) return;
      showStatus(indexStatus,data.status);
      if (data.status?.browseable === false || data.status?.stale === true || data.status?.change_pending === true) {
        get('community-answer').replaceChildren();
        note(notice,messages[stateOf(data.status)] || '当前范围的社区索引暂不可用。');
        return;
      }
      const revision = data.status?.revision || null;
      if (view.selected.size && view.revision && revision && view.revision !== revision) {
        view.selected.clear();
        view.staleSelection = true;
        get('community-answer').replaceChildren();
      }
      view.revision = revision;
      view.vectorsReady = data.status?.vector_index?.state === 'ready';
      const mode = questionMode(requestedMode,view.selected.size,view.vectorsReady);
      note(notice,'');
      if (mode === 'global') {
        level.replaceChildren(...(data.status?.levels || []).map(item =>
          new Option(`第 ${item.level + 1} 层 · ${item.reports_ready} / ${item.communities} 份可用报告`,String(item.level))));
        if ([...level.options].some(option => option.value === previousLevel)) level.value = previousLevel;
        submit.disabled = !level.options.length;
        hint.textContent = '按选定层级归纳社区报告，结果会显示实际覆盖。';
        return;
      }
      if (canSelect && view.staleSelection) {
        note(notice,'社区已更新，请重新选择社区或明确使用当前范围内全部社区。');
        renderSelections();
      }
      if (canSelect) {
        for (const community of data.communities || []) {
          if (view.selected.has(community.id) || community.report_status !== 'ready') continue;
          const option = make('option',titleOf(community)); option.value = community.id; select.append(option);
        }
      }
      const reportCount = data.status?.counts?.reports_ready ?? (data.communities || [])
        .filter(community => community.report_status === 'ready').length;
      submit.disabled = (canSelect && view.staleSelection) || (mode === 'semantic' ? !view.vectorsReady : reportCount === 0);
      hint.textContent = mode === 'semantic' ? '按问题的含义匹配相关社区报告。' :
        view.selected.size ? '在已选社区的报告和原文中查找依据。' : '按问题中的关键词匹配相关社区报告。';
      if (mode === 'semantic' && !view.vectorsReady) note(notice,'社区报告向量未就绪，请选择自动匹配或在构建与任务中更新。');
      if (mode === 'keyword' && reportCount === 0) note(notice,'当前范围没有可用的社区报告。');
    } catch (error) {
      if (view === ask && ticket === view.request && id === project.value) note(notice,error.message,true);
    }
  }
  function renderCoverage(coverage, generated, evidenceShown) {
    if (!coverage) return null;
    const line = make('div',null,'community-coverage-line');
    const add = (label, shown, total, detail) => {
      if (!Number.isFinite(shown) || (total != null && !Number.isFinite(total))) return;
      const metric = make('span',label + ' ' + shown + (total == null ? '' : '/' + total));
      if (detail) metric.title = detail;
      line.append(metric);
    };
    if (coverage.mode === 'global') {
      add('层级',Number.isInteger(coverage.level) ? coverage.level + 1 : null);
      add('已生成报告',coverage.reports_ready,coverage.target_communities,'该层已生成报告 / 目标社区');
      add('含原文依据',coverage.reports_with_eligible_evidence);
      if (generated) add('归纳',coverage.reports_mapped,coverage.target_communities,'纳入归纳的报告 / 目标社区');
      if (coverage.preview_communities < coverage.included_communities) {
        add('预览',coverage.preview_communities,coverage.included_communities);
      }
    } else if (coverage.complete === false) {
      const total = coverage.selected_communities || coverage.matched_communities || coverage.scope_communities;
      const included = generated ? coverage.reports_included : coverage.included_communities;
      if (total && included < total) {
        add(generated ? '采用报告' : '展示社区',included,total,
          generated ? '用于回答的报告 / 本次候选社区' : '显示的社区 / 本次候选社区');
      }
    }
    if (coverage.mode !== 'global' && evidenceShown > 0 && evidenceShown < coverage.evidence_available) {
      const countDetail = Number.isFinite(coverage.evidence_returned)
        ? `接口返回 ${coverage.evidence_returned} 条，页面选取并去重后展示 ${evidenceShown} 条，本次候选 ${coverage.evidence_available} 条。`
        : '页面展示的片段 / 本次候选证据';
      add('展示证据',evidenceShown,coverage.evidence_available,countDetail);
    }
    return line.childElementCount ? line : null;
  }
  function renderQuestionResult(host,result,id) {
    const body = make('div',null,'community-question-result-body');
    const evidence = result.evidence || [];
    const cited = new Set(result.citations || []);
    const used = evidence.filter(item => cited.has(item.id));
    const sourceLabel = !result.generated ? '证据摘录' : used.length ? '回答引用' : '可核对证据';
    const sources = evidenceSection(sourceLabel,used.length ? used : evidence,id,true);
    sources.panel.firstElementChild.textContent += ` 条 · ${sources.sourceCount} 个来源`;
    const coverage = result.coverage;
    if (result.answer && (result.generated || !evidence.length)) {
      const answer = make('article',null,'community-answer-reply');
      if (result.generated) answer.append(make('h3','社区回答'));
      if (result.answer_purpose === 'document_explanation') {
        answer.append(make('p','按报告与原文说明，有效性请见引用状态。','community-mode-help'));
      } else if (result.as_of) {
        answer.append(make('p',`业务核验时点 ${window.DisplayTime.format(result.as_of)}`,'community-mode-help'));
      }
      const content = make('div',null,'qa-answer');
      window.QaPresentation.render(content,result.answer,part => {
        const reference = sources.byId.get(part.slice(1,-1));
        return reference ? citationButton(reference,sources) : null;
      });
      answer.append(content);
      body.append(answer);
    }
    if (coverage?.retrieval === 'representative') {
      body.append(make('p','关键词未匹配到社区，以下是当前范围里的代表性证据。','community-mode-help'));
    }
    const metrics = renderCoverage(coverage,result.generated,sources.byKey.size);
    if (metrics) body.append(metrics);
    if (sources.byKey.size) {
      sources.panel.open = !result.generated;
      body.append(sources.panel);
    }
    if (result.communities?.length) {
      const matches = disclosure((coverage?.retrieval === 'representative' ? '代表性社区' : '社区列表') +
        ' · ' + result.communities.length + ' 个');
      matches.open = !result.generated && !sources.byKey.size;
      const reports = make('div',null,'community-matched-reports');
      for (const community of result.communities) {
        const report = make('article',null,'community-matched-report');
        const heading = make('div',null,'community-matched-heading');
        heading.append(make('h4',titleOf(community)));
        report.append(heading);
        if (['keyword','semantic'].includes(coverage?.retrieval)) {
          const match = renderCommunityMatch(community,coverage.retrieval);
          if (match) report.append(match);
        }
        showReport(report,community,sources);
        reports.append(report);
      }
      matches.append(reports);
      body.append(matches);
    }
    host.replaceChildren(body);
  }
  async function queryReports(generate) {
    const submit = get('community-question-submit');
    if (submit.disabled || !get('community-question-form').reportValidity()) return;
    const id = project.value, view = ask, ticket = ++view.request;
    const isCurrent = () => view === ask && ticket === view.request && id === project.value;
    const range = view.override || currentScope('qa');
    const mode = questionMode(get('community-question-mode').value,view.selected.size,view.vectorsReady);
    const level = get('community-question-level').value;
    if (!id || !range || (mode === 'keyword' && view.staleSelection) || (mode === 'global' && level === '')) return;
    const notice = get('community-ask-status'), host = get('community-answer');
    const input = get('community-question-text'), question = input.value.trim();
    if (!question) {
      note(notice,'请输入问题。',true);
      input.focus();
      return;
    }
    const ids = mode === 'keyword' ? [...view.selected.keys()] : [];
    submit.disabled = true;
    get('community-ask-filters').open = false;
    const questionTurn = make('div',null,'qa-turn qa-turn-user');
    questionTurn.append(make('p',question,'qa-bubble'));
    const answerTurn = make('div',null,'qa-turn qa-turn-assistant');
    answerTurn.hidden = true;
    host.replaceChildren(questionTurn,answerTurn);
    get('community-qa-scroll').scrollTop = 0;
    note(notice,generate && mode === 'global' ? '正在核对证据并分批归纳所选层级…' :
      generate ? '正在根据社区报告生成答案…' : '正在查找社区报告…');
    try {
      const result = await askQuestion(id,{scope:range,community_ids:ids,mode,
        expected_revision:view.revision || undefined,question,generate,
        ...(mode === 'keyword' && !ids.length ? {match_only:true} : {}),
        ...(mode === 'global' ? {level:Number(level)} : {})},isCurrent);
      if (!isCurrent()) return;
      renderQuestionResult(answerTurn,result,id);
      answerTurn.hidden = false;
      if (input.value.trim() === question) input.value = '';
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
      if (!isCurrent()) return;
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
      if (isCurrent()) {
        submit.disabled = false;
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
      vector.textContent = '构建报告向量';
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
      const reportsReady = value.counts?.reports_ready ?? 0;
      const vectorLabel = {
        disabled:reportsReady ? '报告向量尚未构建。构建后可使用语义匹配。' : '社区报告生成后可构建报告向量。',
        stale:reportsReady ? '报告向量待更新' : '有可用社区报告后可更新报告向量。',
        failed:'报告向量构建失败', unavailable:'报告向量需要语义模型和 Milvus',
        ready:`报告向量已就绪 · ${vectors?.indexed ?? 0} 份`,
      };
      showBuildText('community-vector-status',vectors ? [vectorLabel[vectors.state],vectors.error].filter(Boolean).join(' · ') : '');
      vector.textContent = vectors?.enabled ? '更新报告向量' : '构建报告向量';
      vector.disabled = active || !vectors?.available || !reportsReady;
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
  const stateIcon = window.WorkspaceIcons?.create('network');
  if (stateIcon) get('community-browse-state-icon').append(stateIcon);
  window.WorkspaceIcons?.decorate(get('community-browse-ask'),'chat');
  get('community-browse-build').onclick = () => {
    const range = browse.override || currentScope('search');
    const platform = range?.kind === 'platform';
    get('community-build-kind').value = platform ? 'platform' : 'project';
    get('community-build-platform-row').hidden = !platform;
    if (platform) {
      get('community-build-platform').value = range.platform;
      loadPlatforms();
    }
    window.KnowledgeTabs?.open('runtime');
    window.RuntimeView?.show('communities');
  };
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
    event.preventDefault(); queryReports(get('community-question-generate').checked);
  });
  get('community-question-text').addEventListener('keydown',event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    get('community-question-form').requestSubmit();
  });
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
    browse = {...freshView(),keyword:browse.keyword,mode:browse.mode};
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
    searchForQuery, setBrowserVisible,
    openForEntity(id,label) {
      if (!id) return;
      browse = freshView();
      browse.anchor = {id,label:label || id};
      if (search.classList.contains('hidden')) window.KnowledgeTabs?.open('search');
      window.RetrievalWorkspace?.select('community',{reuseQuery:false});
    },
  };
})();
