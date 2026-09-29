/* Unified ontology governance: discovery -> design -> review -> validate -> publish. */
(()=>{
  'use strict';
  const byId=id=>document.getElementById(id);
  const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node;};
  const state={projectId:'',ontologyId:null,draftId:null,revision:null,selectedIri:null,displayPath:[],mode:'object',stage:'discover',hasDrafts:null,discoveryKind:'entity',filters:{query:'',source:'',confidence:'',cluster:''},cursors:{roots:null,children:new Map(),search:null,matrix:null},hierarchy:{items:[],expanded:new Map()},matrix:[],draft:null,discovery:null,candidates:null,changeProposals:[],controller:null,candidateEvidenceController:null,candidateEvidenceToken:0};
  const stages=[['discover','1','发现','候选与证据'],['design','2','设计','对象与层级'],['review','3','审核','逐项决策'],['validate','4','校验','图约束检查'],['publish','5','发布','版本与溯源']];
  const page=el('section','tab hidden ontology-workbench');
  page.id='tab-ontology-workbench';
  page.innerHTML=`<header class="ontology-workbench__masthead"><div><small class="ontology-workbench__eyebrow">ONTOLOGY GOVERNANCE WORKBENCH</small><h2>把发现变成可审计的领域语言</h2><p>开放发现保留探索性，正式本体通过草案、逐项审核和不可变版本进入唯一真相源。多父类按 DAG 管理；删除统一为可恢复的停用。</p></div><div class="ontology-workbench__context"><span>当前工作上下文</span><strong id="ontology-workbench-context">请选择项目</strong><small id="ontology-workbench-revision">未选择草案</small></div></header><ol class="ontology-workbench__stages" aria-label="本体治理阶段">${stages.map(([id,step,label,copy])=>`<li><button type="button" class="ontology-workbench__stage" data-workbench-stage="${id}" data-step="${step}"><strong>${label}</strong><small>${copy}</small></button></li>`).join('')}</ol><div id="ontology-workbench-notice" class="ontology-workbench__notice" role="status" aria-live="polite"></div><div class="ontology-workbench__body"><aside class="ontology-workbench__pane ontology-workbench__library" aria-label="对象和变更库"><div class="ontology-workbench__pane-head"><div><h3 id="ontology-workbench-library-title">发现队列</h3><p id="ontology-workbench-library-copy">按来源和置信度检查候选</p></div><button type="button" id="ontology-workbench-refresh" class="secondary">刷新</button></div><div class="ontology-workbench__search"><input id="ontology-workbench-search" type="search" placeholder="搜索对象、IRI 或变更" aria-label="搜索本体对象"><button type="button" id="ontology-workbench-search-button" class="secondary">搜索</button></div><div id="ontology-workbench-filters" class="ontology-workbench__filters"><label>来源<select id="candidate-map-source"><option value="">全部来源</option></select></label><label>最低置信度<select id="candidate-map-confidence"><option value="">不限</option><option value="0.9">≥ 0.90</option><option value="0.75">≥ 0.75</option><option value="0.5">≥ 0.50</option></select></label></div><div id="ontology-workbench-library-list" class="ontology-workbench__list"></div></aside><main class="ontology-workbench__pane ontology-workbench__canvas"><div class="ontology-workbench__canvas-head"><div><small id="ontology-workbench-kicker" class="ontology-workbench__eyebrow">DISCOVERY · 非正式知识</small><h3 id="ontology-workbench-canvas-title">候选术语整理</h3></div><div class="ontology-workbench__view-switch" role="group" aria-label="本体视图"><button type="button" data-workbench-mode="object" aria-pressed="true">对象</button><button type="button" data-workbench-mode="hierarchy" aria-pressed="false">层级</button><button type="button" data-workbench-mode="matrix" aria-pressed="false">矩阵</button></div></div><div id="ontology-workbench-canvas-content"><div class="ontology-workbench__empty">选择项目后加载发现候选、聚类统计和来源证据。</div></div><div id="ontology-workbench-actionbar" class="ontology-workbench__actionbar"></div></main><aside class="ontology-workbench__pane ontology-workbench__inspector" aria-label="证据与审核检查器"><div id="ontology-workbench-inspector"><section class="ontology-workbench__inspector-section"><h4>检查器</h4><p>选择候选或对象，在这里查看来源、影响面与审核状态。</p></section></div></aside></div>`;
  document.querySelector('main').append(page);
  const nav=document.querySelector('[data-tab="ontology-workbench"]');

  const notice=(message='',tone='')=>{const host=byId('ontology-workbench-notice');host.textContent=message;host.dataset.tone=tone;};
  const replace=(host,...nodes)=>host.replaceChildren(...nodes.filter(Boolean));
  const projectPath=suffix=>{if(!state.projectId)throw new Error('请先选择项目');return `/api/projects/${encodeURIComponent(state.projectId)}${suffix}`;};
  async function request(path,options={}){
    const response=await fetch(path,{...options,signal:options.signal||state.controller?.signal,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
    const raw=await response.text();let payload={};
    try{payload=raw?JSON.parse(raw):{};}catch{throw new Error(`服务返回了无法解析的数据（HTTP ${response.status}）`);}
    if(!response.ok){const detail=payload?.detail;const error=new Error(typeof detail==='string'?detail:(detail?.message||payload?.code||`请求失败（HTTP ${response.status}）`));error.code=payload?.code;error.details=payload?.details;error.status=response.status;throw error;}
    return payload;
  }
  function cancelCandidateEvidence(){state.candidateEvidenceToken+=1;state.candidateEvidenceController?.abort();state.candidateEvidenceController=null;}
  const loadNode=message=>{const node=el('div','ontology-workbench__loading',message);return node;};
  function syncContext(){
    state.projectId=byId('project')?.value||'';
    const projectLabel=byId('project')?.selectedOptions?.[0]?.textContent||'请选择项目';
    byId('ontology-workbench-context').textContent=projectLabel;
    byId('ontology-workbench-revision').textContent=state.draftId?`草案 ${state.draftId.slice(0,8)} · 修订 ${state.revision}`:'未选择草案';
  }
  function syncStageAvailability(){
    const locked=state.hasDrafts===false&&!state.draftId;
    page.querySelectorAll('[data-workbench-stage]').forEach(button=>{button.disabled=button.dataset.workbenchStage!=='discover'&&locked;});
  }
  function setStage(stage){
    if(stage!=='discover'&&state.hasDrafts===false&&!state.draftId){syncStageAvailability();notice('请先从“发现”阶段生成草案，再进入设计。','warning');return;}
    if(stage!=='discover')cancelCandidateEvidence();state.stage=stage;
    page.dataset.stage=stage;
    page.querySelectorAll('[data-workbench-stage]').forEach(button=>{if(button.dataset.workbenchStage===stage)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
    const discover=stage==='discover';
    byId('ontology-workbench-filters').hidden=!discover;
    page.querySelector('.ontology-workbench__view-switch').classList.toggle('hidden',stage!=='design');
    byId('ontology-workbench-kicker').textContent={discover:'DISCOVERY · 非正式知识',design:'DESIGN · 草案叠加层',review:'REVIEW · 变更驱动',validate:'VALIDATION · 可重复快照',publish:'PUBLISH · 不可变版本'}[stage];
    byId('ontology-workbench-canvas-title').textContent={discover:'候选术语整理',design:'对象设计画布',review:'逐项审核队列',validate:'校验报告',publish:'版本发布与溯源'}[stage];
    byId('ontology-workbench-library-title').textContent=discover?'发现队列':stage==='review'?'变更队列':'对象与草案';
    byId('ontology-workbench-library-copy').textContent=discover?'按来源和置信度检查候选':'在统一草案上继续工作';
    if(stage==='discover')renderDiscovery();else renderDraftStage();
  }
  function metric(value,label){const card=el('div','ontology-workbench__metric');card.append(el('strong','',value??0),el('span','',label));return card;}
  function cluster(title,copy,rows){
    const box=el('section','ontology-workbench__cluster discovery-cluster');box.append(el('h4','',title),el('p','ontology-workbench__cluster-copy',copy));
    if(!rows.length){box.append(el('p','ontology-workbench__empty','暂无数据'));return box;}
    [...rows].sort((left,right)=>{
      const count=(right.count??right.occurrence_count??0)-(left.count??left.occurrence_count??0);
      if(count)return count;
      const leftName=left.name||left.type||'未命名';const rightName=right.name||right.type||'未命名';
      return leftName.localeCompare(rightName,'zh-CN',{numeric:true,sensitivity:'base'});
    }).slice(0,10).forEach(item=>{const name=item.name||item.type||'未命名';const row=el('button','ontology-workbench__cluster-row');row.type='button';row.dataset.clusterFilter=name;row.setAttribute('aria-pressed',String(state.filters.cluster===name));row.append(el('span','',name),el('b','',item.count??item.occurrence_count??0));row.addEventListener('click',()=>{state.filters.cluster=state.filters.cluster===name?'':name;renderDiscoveryData();});box.append(row);});return box;
  }
  const candidateSources=item=>item.sources||[];
  const candidateSourceCount=item=>{
    const count=Number(item.source_count);
    return item.source_count==null||!Number.isFinite(count)?candidateSources(item).length:count;
  };
  function discoveryItems(){return ({entity:state.candidates?.nodes,relation:state.candidates?.edges,attribute:state.candidates?.attributes,exception:state.candidates?.exceptions}[state.discoveryKind]||[]);}
  function visibleCandidates(){
    const query=state.filters.query.toLocaleLowerCase();const threshold=Number(state.filters.confidence||0);const source=state.filters.source;const cluster=state.filters.cluster;
    return discoveryItems().filter(item=>{
      const sources=candidateSources(item);const confidence=Math.max(0,...sources.map(row=>Number(row.confidence)||0));
      const matchesQuery=!query||`${item.text||''} ${item.subject||''} ${item.type||''} ${item.object||''} ${JSON.stringify(item.value??'')} ${item.reason||''}`.toLocaleLowerCase().includes(query);
      const matchesSource=!source||sources.some(row=>(row.document_id||row.document_title)===source);
      const matchesCluster=!cluster||item.type===cluster||item.text===cluster||item.source_kind===cluster;
      return matchesQuery&&matchesSource&&matchesCluster&&confidence>=threshold;
    });
  }
  function discoveryGuide(){
    const guide=el('section','ontology-workbench__discovery-guide');const copy=el('div');copy.append(el('small','','这里是什么'),el('h3','','从业务文档中提取、等待确认的术语'),el('p','','这里不是正式本体。系统先把文档里的实体、关系和属性整理成候选；你查看分布与原文证据后，再生成草案进入设计。'));
    const steps=el('ol','ontology-workbench__discovery-steps');[['1','看分布','先判断系统识别了哪些业务概念'],['2','查候选','筛选术语并点击查看右侧证据'],['3','生成草案','把确认范围带入下一步设计']].forEach(([step,title,detail])=>{const item=el('li');item.append(el('i','',step),el('b','',title),el('span','',detail));steps.append(item);});guide.append(copy,steps);return guide;
  }
  function discoveryToolbar(){
    const toolbar=el('section','ontology-workbench__discovery-toolbar');const search=el('label');search.append(el('span','','搜索候选'));const input=el('input');input.type='search';input.value=state.filters.query;input.placeholder='输入术语或类型';input.setAttribute('aria-label','搜索发现候选');search.append(input);
    const sourceLabel=el('label');sourceLabel.append(el('span','','来源'));const source=el('select');source.append(el('option','','全部来源'));source.firstChild.value='';discoverySources().forEach((label,value)=>{const option=el('option','',label);option.value=value;source.append(option);});source.value=state.filters.source;sourceLabel.append(source);
    const confidenceLabel=el('label');confidenceLabel.append(el('span','','最低置信度'));const confidence=el('select');[['','不限'],['0.9','≥ 0.90'],['0.75','≥ 0.75'],['0.5','≥ 0.50']].forEach(([value,label])=>{const option=el('option','',label);option.value=value;confidence.append(option);});confidence.value=state.filters.confidence;confidenceLabel.append(confidence);
    const apply=el('button','secondary','筛选');apply.type='button';const run=()=>{state.filters.query=input.value.trim();state.filters.source=source.value;state.filters.confidence=confidence.value;renderDiscoveryData();};apply.addEventListener('click',run);input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();run();}});source.addEventListener('change',run);confidence.addEventListener('change',run);
    const refresh=el('button','secondary','刷新数据');refresh.type='button';refresh.dataset.refreshDiscovery='';refresh.addEventListener('click',loadDiscovery);toolbar.append(search,sourceLabel,confidenceLabel,apply,refresh);return toolbar;
  }
  function discoveryKindTabs(){
    const tabs=el('div','ontology-workbench__candidate-tabs');
    const counts={entity:state.candidates?.nodes?.length||0,relation:state.candidates?.edges?.length||0,attribute:state.candidates?.attributes?.length||0,exception:state.candidates?.exceptions?.length||0};
    [['entity','实体候选'],['relation','关系候选'],['attribute','属性候选'],['exception','证据异常']].forEach(([kind,label])=>{const button=el('button','secondary',`${label} ${counts[kind]}`);button.type='button';button.dataset.discoveryKind=kind;button.setAttribute('aria-pressed',String(state.discoveryKind===kind));button.addEventListener('click',()=>{cancelCandidateEvidence();state.discoveryKind=kind;state.filters.cluster='';renderDiscoveryData();});tabs.append(button);});return tabs;
  }
  function renderChangeProposals(){
    const pending=(state.changeProposals||[]).filter(item=>item.status==='pending');
    if(!pending.length)return null;
    const section=el('section','ontology-workbench__change-proposals');section.append(el('h3','',`待审批本体变更申请 · ${pending.length}`),el('p','', '这些申请来自正式事实审核。批准会生成新本体版本，拒绝不会改变当前本体。'));
    pending.forEach(proposal=>{const card=el('article','ontology-workbench__change-proposal');const title=el('div');title.append(el('small','',`${proposal.operation==='add'?'新增':'调整'} · ${{class:'实体类',relation:'关系',attribute:'属性'}[proposal.kind]||proposal.kind}`),el('h4','',proposal.label||proposal.uri),el('p','',proposal.rationale||'未填写申请说明'));const impact=el('p','ontology-workbench__change-impact',`影响：现有知识 ${proposal.impact?.record_count||0} · 约束 ${proposal.impact?.constraint_count||0} · 关联候选 ${proposal.impact?.linked_candidates||0}`);const note=el('input');note.placeholder='审批意见（必填）';note.maxLength=2000;const actions=el('div','ontology-workbench__inspector-actions');const approve=el('button','','批准并生成本体版本');const reject=el('button','secondary','拒绝申请');approve.type=reject.type='button';const confirm=el('label','ontology-workbench__impact-confirm');const check=el('input');check.type='checkbox';confirm.append(check,document.createTextNode('确认高影响变更'));confirm.hidden=proposal.impact?.risk!=='high';const decide=async action=>{if(!note.value.trim()){notice('请填写本体变更审批意见','warning');note.focus();return;}approve.disabled=reject.disabled=true;try{await request(projectPath(`/ontology-change-proposals/${encodeURIComponent(proposal.id)}/decision`),{method:'POST',body:JSON.stringify({action,note:note.value.trim(),expected_revision:proposal.revision,expected_ontology_id:state.discovery?.ontology_id||null,confirm_impact:check.checked})});notice(action==='approve'?'本体变更已批准并生成新版本。':'本体变更申请已拒绝。');await loadDiscovery();}catch(error){notice(error.message,'error');approve.disabled=reject.disabled=false;}};approve.addEventListener('click',()=>decide('approve'));reject.addEventListener('click',()=>decide('reject'));actions.append(approve,reject);card.append(title,impact,confirm,note,actions);section.append(card);});return section;
  }
  const candidateLocationLabels={exact:'精确证据位置',recovered_in_chunk:'历史切片内恢复定位',chunk:'仅保存切片级位置',unlocated:'历史来源无法定位'};
  const rangeText=(start,end)=>Number.isSafeInteger(start)&&Number.isSafeInteger(end)?`${start}–${end}`:'未记录';
  function candidateSourceCard(source,index,kind){
    const article=el('article','ontology-workbench__candidate-evidence');article.dataset.candidateEvidence=source.assertion_id||`unresolvable-${index}`;
    article.append(el('b','',source.document_title||source.document_id||'未知来源'),el('small','',`${source.chunk_id||'未记录片段'} · 置信度 ${source.confidence??'—'}`));
    if(kind==='exception'||source.resolvable===false||!source.assertion_id){article.dataset.state='unresolvable';article.append(el('p','',source.evidence_preview||'该来源没有可解析的断言证据。'),el('small','','历史候选未绑定可解析断言，仅保留原始预览与异常原因，不会请求或推测当前原文。'));return article;}
    article.dataset.state='loading';article.append(el('p','ontology-workbench__evidence-state','正在核验固定文档版本与历史切片…'));return article;
  }
  function appendCandidateChunk(article,resolution){
    const mode=resolution.location?.mode||'unlocated';const chunk=resolution.chunk||{};const location=resolution.location||{};
    if(mode==='unlocated'){article.append(el('p','ontology-workbench__evidence-warning',`定位原因：${location.reason||'source_unavailable'}`));return;}
    const chunkText=typeof chunk.text==='string'?chunk.text:'';const pre=el('pre','ontology-workbench__source-chunk');
    if(mode==='exact'||mode==='recovered_in_chunk'){
      const before=typeof location.before==='string'?location.before:'';const highlight=typeof location.highlight==='string'?location.highlight:'';const after=typeof location.after==='string'?location.after:'';
      pre.replaceChildren(document.createTextNode(before));if(highlight){const mark=document.createElement('mark');mark.textContent=highlight;pre.append(mark);}pre.append(document.createTextNode(after));
    }else{pre.replaceChildren(document.createTextNode(chunkText));article.append(el('p','ontology-workbench__evidence-warning','只能确认到历史切片级别；下方展示完整历史切片，不标记或猜测更小范围。'));}
    article.append(pre);
  }
  async function openCandidateHistoricalSource(article,resolution,token,signal){
    const documentRef=resolution.document||{};const button=article.querySelector('[data-open-candidate-source]');button.disabled=true;
    try{
      const history=await request(projectPath(`/records/${encodeURIComponent(documentRef.id)}/history`),{signal});
      if(token!==state.candidateEvidenceToken||signal.aborted||!article.isConnected)return;
      const pinned=(history.versions||[]).find(version=>version.version_id===documentRef.version_id);
      if(!pinned)throw new Error('固定历史版本不存在，未回退到最新版本');
      if(typeof window.openFrozenSourceEvidence!=='function')throw new Error('历史原文查看器尚未加载');
      const located=['exact','recovered_in_chunk'].includes(resolution.location?.mode);
      window.openFrozenSourceEvidence({title:documentRef.title||documentRef.id||'历史来源',version:pinned.version,version_id:pinned.version_id,source_content:'full_version',reason:`候选证据 · ${candidateLocationLabels[resolution.location?.mode]||'历史来源'}`,full_text:typeof pinned.text==='string'?pinned.text:'',start_char:located?resolution.location.start_char:null,end_char:located?resolution.location.end_char:null});
    }catch(error){if(error.name!=='AbortError'&&token===state.candidateEvidenceToken&&article.isConnected){const failure=el('p','ontology-workbench__evidence-warning',`历史原文读取失败：${error.message}`);article.append(failure);button.disabled=false;}}
  }
  function renderCandidateResolution(article,resolution,token,signal){
    const documentRef=resolution.document||{};const chunk=resolution.chunk||{};const location=resolution.location||{};const mode=location.mode||'unlocated';
    article.dataset.state='ready';article.replaceChildren(el('b','',documentRef.title||documentRef.id||'未知来源'),el('small','',`来源版本 ${documentRef.version??'未知'} · ${candidateLocationLabels[mode]||mode}`),el('small','',`片段 ${chunk.id||'未记录'} · 绝对切片范围 ${rangeText(chunk.start_char,chunk.end_char)}`));
    if(mode==='exact'||mode==='recovered_in_chunk')article.append(el('small','',`绝对字符范围 ${rangeText(location.start_char,location.end_char)}`));
    (resolution.integrity?.warnings||[]).forEach(warning=>article.append(el('p','ontology-workbench__evidence-warning',warning.message||warning.code||String(warning))));
    appendCandidateChunk(article,resolution);
    if(documentRef.id&&documentRef.version_id){const open=el('button','secondary','查看完整历史原文');open.type='button';open.dataset.openCandidateSource='';open.addEventListener('click',()=>openCandidateHistoricalSource(article,resolution,token,signal));article.append(open);}
  }
  async function loadCandidateEvidence(source,article,token,signal){
    try{const resolution=await request(projectPath(`/assertions/${encodeURIComponent(source.assertion_id)}/evidence`),{signal});if(token!==state.candidateEvidenceToken||signal.aborted||!article.isConnected)return;renderCandidateResolution(article,resolution,token,signal);}
    catch(error){if(error.name!=='AbortError'&&token===state.candidateEvidenceToken&&article.isConnected){article.dataset.state='error';const failure=el('p','ontology-workbench__evidence-warning',`证据读取失败：${error.message}`);article.replaceChildren(el('b','',source.document_title||source.document_id||'未知来源'),failure);}}
  }
  function renderCandidateInspector(item,kind=state.discoveryKind){
    cancelCandidateEvidence();const controller=new AbortController();state.candidateEvidenceController=controller;const token=state.candidateEvidenceToken;
    state.selectedIri=item.id||null;const host=byId('ontology-workbench-inspector');const summary=el('section','ontology-workbench__inspector-section');
    const heading=kind==='relation'?`${item.subject||'？'} — ${item.type||'未分类关系'} → ${item.object||'？'}`:kind==='attribute'?`${item.subject||'？'} — ${item.type||'未分类属性'} = ${JSON.stringify(item.value)}`:item.text||item.type||'候选详情';
    summary.append(el('small','ontology-workbench__eyebrow',({entity:'实体候选',relation:'关系候选',attribute:'属性候选',exception:'证据异常'}[kind]||'候选')),el('h4','',heading));
    if(kind==='entity')summary.append(el('p','',`建议实体类别：${item.type||'未分类'} · 累计出现 ${item.occurrence_count||0} 次`));
    if(kind==='relation')summary.append(el('p','',`实体到实体的事实 · 累计出现 ${item.occurrence_count||0} 次`));
    if(kind==='attribute')summary.append(el('p','',`实体的标量字段 · ${item.value_type||typeof item.value} · 累计出现 ${item.occurrence_count||0} 次`));
    if(kind==='exception')summary.append(el('p','',item.reason||'该候选无法通过正文证据或结构校验'),el('code','',item.reason_code||'unclassified_exception'));
    const sources=candidateSources(item);const sourceCount=candidateSourceCount(item);const evidence=el('section','ontology-workbench__inspector-section');evidence.append(el('h4','',`来源证据 ${sourceCount}`));const list=el('div','ontology-workbench__evidence');
    sources.forEach((source,index)=>{const article=candidateSourceCard(source,index,kind);list.append(article);if(kind!=='exception'&&source.resolvable!==false&&source.assertion_id)loadCandidateEvidence(source,article,token,controller.signal);});
    if(!list.childElementCount)list.append(el('p','', '没有可展示的来源证据。'));evidence.append(list);if(item.sources_truncated)evidence.append(el('p','ontology-workbench__source-truncation',`仅显示 ${sources.length}/${sourceCount} 条来源；其余来源未随候选列表返回。`));replace(host,summary,evidence);
  }
  function renderDiscoveryData(){
    const canvas=byId('ontology-workbench-canvas-content');const library=byId('ontology-workbench-library-list');
    const data=state.discovery||{};const candidates=visibleCandidates();
    const states=data.candidate_status_counts||{pending:data.unpublished_candidate_count??data.candidate_count??0,included_in_draft:0,approved:0,materialized:0};
    const metrics=el('section','ontology-workbench__metrics discovery-metrics');metrics.append(metric(data.candidate_count,'正常候选'),metric(data.entity_count,'实体候选'),metric(data.relation_count,'关系候选'),metric(data.attribute_count,'属性候选'),metric(data.exception_count,'证据异常'));
    const lifecycleHelp=el('details','ontology-workbench__lifecycle-help');const lifecycleSummary=el('summary','',`查看候选流转状态 · ${states.pending} 条待纳入`);const lifecycle=el('section','ontology-workbench__metrics ontology-workbench__lifecycle');lifecycle.append(metric(states.pending,'待纳入'),metric(states.included_in_draft,'草案中'),metric(states.approved,'已批准'),metric(states.materialized,'已物化'));lifecycleHelp.append(lifecycleSummary,lifecycle,el('p','', '待纳入：尚未进入草案；草案中：已进入未发布草案；已批准：已随本体版本发布但尚未正式入图；已物化：已有正式知识关联该候选。'));if(!data.candidate_status_counts)lifecycleHelp.append(el('p','', '兼容估算：当前服务未返回精确生命周期计数，暂按候选总量估算。'));
    const guidance=el('section','ontology-workbench__notice');guidance.textContent=states.pending>0?`还有 ${states.pending} 条候选待审核或未通过校验。候选会继续保留，不需要重新上传原文。`:'候选、草案和正式本体保持隔离；只有审核发布后的版本才进入正式治理链路。';
    const warnings=el('section','ontology-workbench__warnings');(data.quality_warnings||[]).forEach(item=>warnings.append(el('p','',item.message||String(item))));
    const clusters=el('section','ontology-workbench__clusters');clusters.append(cluster('实体类别分布','实体候选附带的建议业务类别。',data.type_clusters||data.entity_types||[]),cluster('关系类型分布','实体到实体的连接谓词。',data.relation_clusters||data.relation_types||[]),cluster('属性定义分布','实体自身的日期、数量、状态、编号等标量字段。',data.attribute_clusters||data.attribute_types||data.attributes||[]));
    const kindLabel={entity:'实体候选',relation:'关系候选',attribute:'属性候选',exception:'证据异常'}[state.discoveryKind];
    const heading=el('div','ontology-workbench__pane-head ontology-workbench__candidate-heading');const title=el('div');title.append(el('h3','',`${kindLabel} · ${candidates.length} 条`),el('p','',state.discoveryKind==='exception'?'异常项不会进入候选统计、本体草案或发布。':'点击候选，在右侧核对结构与来源预览。'));heading.append(title);if(state.filters.cluster){const clear=el('button','secondary',`清除“${state.filters.cluster}”筛选`);clear.type='button';clear.dataset.clearClusterFilter='';clear.addEventListener('click',()=>{state.filters.cluster='';renderDiscoveryData();});heading.append(clear);}
    const grid=el('section','ontology-workbench__candidate-grid candidate-map-canvas');
    candidates.slice(0,120).forEach(item=>{const button=el('button','ontology-workbench__candidate');button.type='button';button.dataset.candidateId=item.id||'';button.dataset.candidateKind=state.discoveryKind;const identity=el('span','ontology-workbench__candidate-name');const name=state.discoveryKind==='relation'?`${item.subject||'？'} → ${item.object||'？'}`:state.discoveryKind==='attribute'?`${item.subject||'？'} = ${JSON.stringify(item.value)}`:item.text||item.subject||item.id;identity.append(el('b','',name),el('small','',state.discoveryKind==='exception'?(item.reason||'需人工核对'):`${candidateSourceCount(item)} 份来源`));button.append(identity,el('span','ontology-workbench__candidate-type',item.type||item.source_kind||'未分类'),el('strong','',`${item.occurrence_count||0} 次`));button.addEventListener('click',()=>renderCandidateInspector(item,state.discoveryKind));grid.append(button);});
    if(!grid.childElementCount)grid.append(el('div','ontology-workbench__empty','当前筛选下没有候选，请调整来源、置信度或搜索条件。'));
    replace(canvas,discoveryGuide(),renderChangeProposals(),discoveryToolbar(),metrics,guidance,warnings.childElementCount?warnings:null,clusters,lifecycleHelp,discoveryKindTabs(),heading,grid);
    replace(library);
    renderDiscoveryActions();
  }
  function discoverySources(){const values=new Map();[...(state.candidates?.nodes||[]),...(state.candidates?.edges||[]),...(state.candidates?.attributes||[]),...(state.candidates?.exceptions||[])].flatMap(candidateSources).forEach(row=>{const key=row.document_id||row.document_title;if(key)values.set(key,row.document_title||row.document_id);});return values;}
  function refreshSourceFilter(){const select=byId('candidate-map-source');const selected=state.filters.source;replace(select,el('option','','全部来源'));select.firstChild.value='';discoverySources().forEach((label,value)=>{const option=el('option','',label);option.value=value;select.append(option);});select.value=selected;}
  function renderDiscoveryActions(){const bar=byId('ontology-workbench-actionbar');const legacy=el('button','secondary','打开完整候选脑图');legacy.type='button';legacy.addEventListener('click',()=>document.querySelector('[data-tab="candidate-mindmap"]')?.click());const create=el('button','','生成累计草案');create.type='button';create.id='ontology-workbench-create-discovery-draft';create.addEventListener('click',openDiscoveryDraftForm);replace(bar,legacy,create);}
  function openDiscoveryDraftForm(){cancelCandidateEvidence();const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section');section.append(el('h4','','生成累计草案'),el('p','','当前候选及来源证据会冻结到统一草案，后续进入对象设计和逐项审核。'));const input=el('input');input.id='ontology-discovery-draft-name';input.maxLength=200;input.placeholder='例如：直播规则本体';input.value='发现本体';const create=el('button','','生成并进入设计');create.type='button';create.addEventListener('click',()=>createDiscoveryDraft(input.value));section.append(input,create);replace(host,section);input.focus();}
  async function loadDiscovery(){
    if(!state.projectId){replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty','请先从左侧选择项目。'));replace(byId('ontology-workbench-library-list'));return;}
    cancelCandidateEvidence();state.controller?.abort();state.controller=new AbortController();
    replace(byId('ontology-workbench-canvas-content'),loadNode('正在读取发现统计与候选证据…'));notice();
    try{
      const [discovery,candidates,drafts,changes]=await Promise.all([request(projectPath('/ontology-discovery')),request(projectPath('/ontology-discovery/candidate-mindmap?limit=500')),request(projectPath('/ontology-drafts')),request(projectPath('/ontology-change-proposals')).catch(()=>({proposals:[]}))]);
      state.discovery=discovery;state.candidates=candidates;state.changeProposals=changes.proposals||[];renderDraftList(drafts.items||[]);refreshSourceFilter();renderDiscoveryData();
    }catch(error){if(error.name!=='AbortError'){notice(error.message,'error');replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty',error.message));}}
  }
  function renderDraftList(drafts){
    state.hasDrafts=Boolean(drafts.length||state.draftId);syncStageAvailability();
    if(state.stage==='discover')return;
    const host=byId('ontology-workbench-library-list');replace(host);
    drafts.forEach(draft=>{const button=el('button','ontology-workbench__draft');button.type='button';button.append(el('b','',draft.title||'未命名草案'),el('span','',draft.status||'editing'),el('small','',`${draft.source_kind||'manual'} · 修订 ${draft.revision}`));button.addEventListener('click',()=>selectDraft(draft.id));host.append(button);});
    if(!drafts.length)host.append(el('div','ontology-workbench__empty','还没有草案。可从发现候选生成，或创建手工草案。'));
  }
  async function createDiscoveryDraft(name){
    state.controller?.abort();state.controller=new AbortController();
    notice('正在通过 Semantica 归纳候选并冻结来源快照…');
    try{const draft=await request(projectPath('/ontology-discovery/drafts'),{method:'POST',body:JSON.stringify({name:String(name||'').trim()||'发现本体'})});state.draftId=draft.unified_draft_id||draft.id;state.revision=draft.draft_revision||draft.revision;state.ontologyId=draft.parent_ontology_id||null;state.hasDrafts=true;syncStageAvailability();syncContext();notice('累计草案已生成，已进入对象设计。');setStage('design');}
    catch(error){if(error.name!=='AbortError')notice(error.message,'error');}
  }
  async function selectDraft(id){
    state.controller?.abort();state.controller=new AbortController();
    notice('正在加载草案叠加层…');try{const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(id)}`));state.draft=draft;state.draftId=draft.id;state.revision=draft.revision;state.ontologyId=draft.base_ontology_id;state.hasDrafts=true;syncStageAvailability();syncContext();notice();renderDraftStage();}catch(error){if(error.name!=='AbortError')notice(error.message,'error');}
  }
  function draftSummary(){
    const draft=state.draft;if(!draft)return el('div','ontology-workbench__empty','选择现有草案，或从“发现”阶段生成累计草案。');
    const box=el('div');const metrics=el('section','ontology-workbench__metrics');const operations=draft.operations||[];metrics.append(metric(operations.length,'当前变更'),metric(operations.filter(x=>x.risk==='high').length,'高风险'),metric(operations.filter(x=>x.risk==='medium').length,'中风险'),metric((draft.decisions||[]).length,'已决策'));box.append(metrics);
    const heading=el('div','ontology-workbench__pane-head');const copy=el('div');copy.append(el('h3','',draft.title||'未命名草案'),el('p','',`${draft.source_kind||'manual'} · ${draft.status||'editing'} · 基于 ${draft.base_ontology_id||'首个版本'}`));heading.append(copy);box.append(heading);
    const list=el('div','ontology-workbench__list');operations.forEach(operation=>{const button=el('button','ontology-workbench__list-item');button.type='button';button.append(el('b','',operation.action),el('small','',`${operation.target_iri} · ${operation.risk||'unknown'}`));button.addEventListener('click',()=>renderOperationInspector(operation));list.append(button);});if(!operations.length)list.append(el('div','ontology-workbench__empty','草案尚无变更。设计阶段可新增对象或调整父级。'));box.append(list);return box;
  }
  function renderOperationInspector(operation){state.selectedIri=operation.target_iri;const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section');section.append(el('h4','',operation.action),el('p','',operation.target_iri),el('p','',`风险：${operation.risk||'未评估'} · 来源：${operation.source||'manual'}`));const payload=el('pre','');payload.textContent=JSON.stringify({before:operation.before,after:operation.after,impact:operation.impact,validation:operation.validation},null,2);section.append(payload);replace(host,section);}
  const queryString=(extra={})=>{const params=new URLSearchParams({limit:'50'});if(state.ontologyId)params.set('ontology_id',state.ontologyId);if(state.draftId)params.set('draft_id',state.draftId);Object.entries(extra).forEach(([key,value])=>{if(value!==null&&value!==undefined&&value!=='')params.set(key,String(value));});return params.toString();};
  const parseIris=value=>[...new Set(String(value||'').split(/[\n,，]/).map(item=>item.trim()).filter(Boolean))];
  const localName=iri=>String(iri||'').split(/[\/#:]/).filter(Boolean).pop()||String(iri||'');
  function syncCanonicalSelection(){page.querySelectorAll('[data-canonical-iri]').forEach(row=>row.setAttribute('aria-current',String(row.dataset.canonicalIri===state.selectedIri)));}
  function hierarchyRow(item,depth=0){
    const wrapper=el('div','ontology-workbench__tree-node');wrapper.style.setProperty('--tree-depth',String(depth));
    const line=el('div','ontology-workbench__tree-line');
    if(item.child_count){const expand=el('button','ontology-workbench__expand','＋');expand.type='button';expand.dataset.expandIri=item.canonical_iri;expand.setAttribute('aria-label',`展开 ${item.label_zh||item.label||item.name}`);line.append(expand);}
    else line.append(el('span','ontology-workbench__leaf','·'));
    const select=el('button','ontology-workbench__tree-label');select.type='button';select.dataset.hierarchyRow=item.canonical_iri;select.dataset.canonicalIri=item.canonical_iri;select.append(el('b','',item.label_zh||item.label||item.name||localName(item.canonical_iri)),el('small','',item.is_reference?`引用路径 · 另有 ${item.other_parent_count} 个父级`:`${item.child_count||0} 个直接子类`));select.addEventListener('click',()=>selectOntologyItem(item));line.append(select);wrapper.append(line);
    const children=el('div','ontology-workbench__tree-children');children.dataset.childrenOf=item.canonical_iri;wrapper.append(children);
    const expand=line.querySelector('[data-expand-iri]');if(expand)expand.addEventListener('click',()=>toggleChildren(item,children,expand,depth));return wrapper;
  }
  function renderHierarchyItems(items,host,depth=0,append=false){if(!append)replace(host);items.forEach(item=>host.append(hierarchyRow(item,depth)));syncCanonicalSelection();}
  async function toggleChildren(item,host,button,depth){
    if(host.childElementCount){host.hidden=!host.hidden;button.textContent=host.hidden?'＋':'−';return;}
    button.disabled=true;button.textContent='…';
    try{const result=await request(projectPath(`/ontology-hierarchy/children?${queryString({iri:item.canonical_iri})}`));state.hierarchy.expanded.set(item.canonical_iri,result.items||[]);renderHierarchyItems(result.items||[],host,depth+1);if(result.next_cursor)appendMore(host,result.next_cursor,'children',item.canonical_iri,depth+1);button.textContent='−';}
    catch(error){if(error.name!=='AbortError')notice(error.message,'error');button.textContent='＋';}
    finally{button.disabled=false;}
  }
  function appendMore(host,cursor,kind,iri,depth=0){const button=el('button','ontology-workbench__load-more','加载更多');button.type='button';button.addEventListener('click',async()=>{button.disabled=true;try{const path=kind==='matrix'?'/ontology-matrix':kind==='search'?'/ontology-hierarchy/search':kind==='roots'?'/ontology-hierarchy/roots':'/ontology-hierarchy/children';const result=await request(projectPath(`${path}?${queryString({cursor,iri,q:state.filters.query})}`));button.remove();if(kind==='matrix')renderMatrixRows(result.items||[],true);else{renderHierarchyItems(result.items||[],host,depth,true);if(result.next_cursor)appendMore(host,result.next_cursor,kind,iri,depth);}}catch(error){notice(error.message,'error');button.disabled=false;}});host.append(button);}
  async function loadHierarchy(){
    const host=byId('ontology-workbench-library-list');replace(host,loadNode(state.filters.query?'正在搜索对象…':'正在加载根类…'));
    try{const searching=Boolean(state.filters.query);const path=searching?'/ontology-hierarchy/search':'/ontology-hierarchy/roots';const result=await request(projectPath(`${path}?${queryString(searching?{q:state.filters.query}:{})}`));state.hierarchy.items=result.items||[];state.cursors[searching?'search':'roots']=result.next_cursor;renderHierarchyItems(state.hierarchy.items,host);if(result.next_cursor)appendMore(host,result.next_cursor,searching?'search':'roots',null,0);if(!result.items?.length)host.append(el('div','ontology-workbench__empty',searching?'没有匹配对象。':'当前版本没有活动根类。'));}
    catch(error){if(error.name!=='AbortError'){notice(error.message,'error');replace(host,el('div','ontology-workbench__empty',error.message));}}
  }
  function renderPath(item){const path=el('div','ontology-workbench__breadcrumb');(item.display_path||state.displayPath||[]).forEach((part,index)=>{if(index)path.append(el('span','','›'));const button=el('button','',part.label||localName(part.iri));button.type='button';button.addEventListener('click',()=>selectOntologyItem({canonical_iri:part.iri,iri:part.iri,label:part.label,display_path:(item.display_path||[]).slice(0,index+1),other_parent_count:0}));path.append(button);});return path;}
  async function selectOntologyItem(item){
    state.selectedIri=item.canonical_iri||item.iri;state.displayPath=item.display_path||[];syncCanonicalSelection();renderObjectInspector(item);
    try{const detail=await request(projectPath(`/ontology-hierarchy/neighborhood?${queryString({iri:state.selectedIri})}`));if(state.selectedIri!==(detail.term?.canonical_iri||detail.term?.iri))return;renderObjectCanvas(detail);renderObjectInspector({...item,...detail.term},detail);}
    catch(error){if(error.name!=='AbortError')notice(error.message,'error');}
  }
  function chip(value,tone=''){const node=el('span',`ontology-workbench__chip ${tone}`.trim(),localName(value));node.title=value;return node;}
  function renderObjectCanvas(detail){
    const host=byId('ontology-workbench-canvas-content');const term=detail.term||{};const card=el('article','ontology-workbench__object-card');card.append(renderPath(term));
    const badge=el('small','ontology-workbench__object-kind','实体类');card.append(badge,el('h3','',term.label_zh||term.label||term.name||localName(term.iri)),el('code','',term.iri));
    const parents=el('div','ontology-workbench__chip-row');parents.append(el('b','','父级'));(term.parents||[]).forEach(value=>parents.append(chip(value)));if(!(term.parents||[]).length)parents.append(chip('独立根','root'));card.append(parents);
    const links=el('div','ontology-workbench__object-links');links.append(objectLinks('关系',detail.relations||[]),objectLinks('属性',detail.attributes||[]));card.append(links);replace(host,card);
  }
  function objectLinks(title,items){const group=el('section');group.append(el('h4','',`${title} ${items.length}`));items.forEach(item=>{const button=el('button','ontology-workbench__link-card');button.type='button';button.append(el('b','',item.label_zh||item.label||item.name),el('small','',localName(item.iri)));button.addEventListener('click',()=>selectOntologyItem({canonical_iri:item.iri,...item}));group.append(button);});if(!items.length)group.append(el('p','',`没有直接${title}`));return group;}
  function renderObjectInspector(item,detail=null){
    const host=byId('ontology-workbench-inspector');const summary=el('section','ontology-workbench__inspector-section');summary.append(el('h4','',item.label_zh||item.label||item.name||localName(item.canonical_iri)),renderPath(item),el('p','',item.canonical_iri||item.iri));if(item.other_parent_count)summary.append(el('p','ontology-workbench__parent-note',`当前显示一条定位路径；另有 ${item.other_parent_count} 个父级。选择任意引用行都会选中同一对象。`));
    const adjust=el('section','ontology-workbench__inspector-section');adjust.append(el('h4','','调整当前对象'));const addParent=el('div','ontology-workbench__inline-form');const parentInput=el('input');parentInput.placeholder='父类 IRI';parentInput.dataset.parentInput='';const parentButton=el('button','secondary','添加父级');parentButton.type='button';parentButton.addEventListener('click',async()=>{if(!parentInput.value.trim())return;await sendCommand({action:'add_parent',target_iri:state.selectedIri,parent_iri:parentInput.value.trim(),reason:'工作台调整父级'});parentInput.value='';await selectOntologyItem(item);});addParent.append(parentInput,parentButton);adjust.append(addParent);
    const actions=el('div','ontology-workbench__inspector-actions');const retire=el('button','danger','预览停用影响');retire.type='button';retire.dataset.previewRetire='';retire.addEventListener('click',previewRetirement);const restore=el('button','secondary','从历史版本恢复');restore.type='button';restore.dataset.openRestore='';restore.addEventListener('click',openRestore);actions.append(retire,restore);adjust.append(actions);replace(host,summary,adjust);
  }
  async function sendCommand(command){
    if(!state.draftId||!state.revision)throw new Error('请先选择可编辑草案');
    const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(state.draftId)}/commands`),{method:'POST',body:JSON.stringify({expected_revision:state.revision,command})});state.draft=draft;state.revision=draft.revision;syncContext();return draft;
  }
  function field(labelText,id,placeholder=''){const label=el('label','',labelText);const input=el('input');input.id=id;input.placeholder=placeholder;label.append(input);return label;}
  function multiField(labelText,id,placeholder=''){const label=el('label','',labelText);const input=el('textarea');input.id=id;input.rows=3;input.placeholder=placeholder;label.append(input);return label;}
  function openNewObject(){
    const host=byId('ontology-workbench-canvas-content');const form=el('form','ontology-workbench__editor');form.id='ontology-object-editor';
    const header=el('div','ontology-workbench__editor-head');header.append(el('div','',undefined));header.firstChild.append(el('small','ontology-workbench__eyebrow','NEW ONTOLOGY OBJECT'),el('h3','','新增本体对象'),el('p','','一次填写完整语义，保存时拆成可审核的原子变更。'));form.append(header);
    const grid=el('div','ontology-workbench__form-grid');const kindLabel=el('label','','对象类型');const kind=el('select');kind.id='ontology-object-kind';[['class','实体类'],['relation','关系'],['attribute','属性']].forEach(([value,label])=>{const option=el('option','',label);option.value=value;kind.append(option);});kindLabel.append(kind);grid.append(kindLabel,field('绝对 IRI','ontology-object-iri','urn:domain:Concept'),field('中文名称','ontology-object-label-zh','领域对象'),field('英文名称','ontology-object-label-en','Domain object'),multiField('父类（每行一个）','ontology-object-parents','urn:domain:Parent'),multiField('Domain OR（每行一个）','ontology-object-domain','urn:domain:Subject'),multiField('Range OR（每行一个）','ontology-object-range','urn:domain:Object'),field('数据类型（属性）','ontology-object-datatype','http://www.w3.org/2001/XMLSchema#string'),multiField('定义说明','ontology-object-description','明确边界、口径和用途'));form.append(grid);
    const footer=el('div','ontology-workbench__actionbar');const cancel=el('button','secondary','取消');cancel.type='button';cancel.addEventListener('click',()=>renderDesign());const save=el('button','','保存为草案变更');save.type='submit';save.id='ontology-object-save';footer.append(cancel,save);form.append(footer);form.addEventListener('submit',saveNewObject);replace(host,form);}
  async function saveNewObject(event){
    event.preventDefault();const button=byId('ontology-object-save');button.disabled=true;notice('正在保存原子变更…');const kind=byId('ontology-object-kind').value;const target=byId('ontology-object-iri').value.trim();
    try{if(!target)throw new Error('IRI 不能为空');const commands=[{action:'create_term',target_iri:target,kind,reason:'工作台新增对象'}];if(kind==='class')parseIris(byId('ontology-object-parents').value).forEach(value=>commands.push({action:'add_parent',target_iri:target,parent_iri:value,reason:'设置多父类'}));if(kind!=='class'){parseIris(byId('ontology-object-domain').value).forEach(value=>commands.push({action:'add_domain',target_iri:target,domain_iri:value,reason:'设置 domain OR'}));parseIris(byId('ontology-object-range').value).forEach(value=>commands.push({action:'add_range',target_iri:target,range_iri:value,reason:'设置 range OR'}));}if(kind==='attribute'&&byId('ontology-object-datatype').value.trim())commands.push({action:'set_datatype',target_iri:target,datatype:byId('ontology-object-datatype').value.trim(),reason:'设置属性数据类型'});const annotations=[['ontology-object-label-zh','http://www.w3.org/2000/01/rdf-schema#label','zh'],['ontology-object-label-en','http://www.w3.org/2000/01/rdf-schema#label','en'],['ontology-object-description','http://www.w3.org/2000/01/rdf-schema#comment','zh']];annotations.forEach(([id,predicate,language])=>{const value=byId(id).value.trim();if(value)commands.push({action:'add_annotation',target_iri:target,predicate,value,language,reason:'补充多语言定义'});});for(const command of commands)await sendCommand(command);notice(`已保存 ${commands.length} 个可审核变更。`);state.selectedIri=target;await loadHierarchy();renderDraftStage();}
    catch(error){if(error.name!=='AbortError')notice(error.message,'error');button.disabled=false;}
  }
  async function previewRetirement(){
    try{const impact=await request(projectPath(`/ontology/term-impact?uri=${encodeURIComponent(state.selectedIri)}`));const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section ontology-workbench__retire-preview');section.append(el('h4','','停用影响预览'),el('p','',`${impact.record_count||0} 条正式知识 · ${impact.constraint_count||0} 条约束 · ${impact.pending_review_count||0} 条待审核 · ${impact.linked_relation_count||0} 条关联关系`),el('p','','停用只从当前有效本体中摘除定义；历史版本和 provenance 仍然可查，不会删除任何数据。'));const reason=el('textarea');reason.placeholder='必填：说明停用原因';reason.dataset.retireReason='';const actions=el('div','ontology-workbench__inspector-actions');const confirm=el('button','danger','确认加入停用变更');confirm.type='button';confirm.dataset.retireConfirm='';confirm.addEventListener('click',async()=>{if(!reason.value.trim()){notice('停用原因不能为空','warning');reason.focus();return;}await sendCommand({action:'retire_term',target_iri:state.selectedIri,reason:reason.value.trim()});notice('停用变更已加入草案，仍需逐项审核。');renderDraftStage();});const restore=el('button','secondary','改为从历史恢复');restore.type='button';restore.dataset.openRestore='';restore.addEventListener('click',openRestore);actions.append(confirm,restore);section.append(reason,actions);replace(host,section);}
    catch(error){notice(error.message,'error');}
  }
  async function openRestore(){
    try{const result=await request(projectPath('/ontologies'));const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section');section.append(el('h4','','从历史版本恢复'));const select=el('select');select.id='ontology-restore-version';(result.versions||[]).forEach(version=>{const option=el('option','',`${version.created_at||''} · ${version.id}`);option.value=version.id;select.append(option);});const fields=el('div','ontology-workbench__restore-fields');fields.id='ontology-restore-fields';[['annotations','名称与说明'],['parents','父类'],['domain','Domain'],['range','Range'],['datatype','数据类型']].forEach(([value,labelText])=>{const label=el('label','');const check=el('input');check.type='checkbox';check.value=value;label.append(check,document.createTextNode(labelText));fields.append(label);});const reason=el('textarea');reason.placeholder='必填：说明恢复原因';const restore=el('button','','加入恢复变更');restore.type='button';restore.addEventListener('click',async()=>{const selected=[...fields.querySelectorAll('input:checked')].map(input=>input.value);if(!select.value||!selected.length||!reason.value.trim()){notice('请选择来源版本、至少一个恢复字段并填写原因','warning');return;}await sendCommand({action:'restore_term',target_iri:state.selectedIri,source_ontology_id:select.value,selected_fields:selected,reason:reason.value.trim()});notice('恢复变更已加入草案，仍需审核后发布。');renderDraftStage();});section.append(select,fields,reason,restore);replace(host,section);}
    catch(error){notice(error.message,'error');}
  }
  function renderMatrixRows(items,append=false){const body=byId('ontology-matrix-body');if(!body)return;if(!append)replace(body);items.forEach(item=>{const row=el('tr');row.dataset.matrixRow=item.iri;const name=el('td');const button=el('button','ontology-workbench__matrix-name',item.label_zh||item.label||item.name||localName(item.iri));button.type='button';button.addEventListener('click',()=>selectOntologyItem({canonical_iri:item.iri,...item}));name.append(button);const kind=el('td','',item.kind);const domain=el('td');(item.domain||[]).forEach(value=>domain.append(chip(value)));const range=el('td');(item.range||[]).forEach(value=>range.append(chip(value)));row.append(name,kind,domain,range);body.append(row);});}
  async function loadMatrix(){
    const canvas=byId('ontology-workbench-canvas-content');const table=el('table','ontology-workbench__matrix');const head=el('thead');const hr=el('tr');['对象','类型','Domain（OR）','Range / 数据类型（OR）'].forEach(label=>hr.append(el('th','',label)));head.append(hr);const body=el('tbody');body.id='ontology-matrix-body';table.append(head,body);replace(canvas,table);replace(byId('ontology-workbench-library-list'),el('div','ontology-workbench__empty','矩阵按关系和属性分页；点击对象可在检查器中继续调整。'));
    try{const result=await request(projectPath(`/ontology-matrix?${queryString()}`));state.matrix=result.items||[];state.cursors.matrix=result.next_cursor;renderMatrixRows(state.matrix);if(result.next_cursor)appendMore(canvas,result.next_cursor,'matrix');}
    catch(error){notice(error.message,'error');}
  }
  function renderDesignActions(){const bar=byId('ontology-workbench-actionbar');const create=el('button','','新增对象');create.type='button';create.dataset.newObject='';create.addEventListener('click',openNewObject);const legacy=el('button','secondary','版本治理 / Turtle');legacy.type='button';legacy.addEventListener('click',openVersionGovernance);const next=el('button','','提交审核');next.type='button';next.dataset.submitReview='';next.disabled=!state.draftId||!(state.draft?.operations||[]).length||state.draft?.status!=='editing';next.addEventListener('click',submitReview);replace(bar,legacy,create,next);}
  function renderDesign(){
    renderDesignActions();if(state.mode==='matrix'){loadMatrix();return;}const canvas=byId('ontology-workbench-canvas-content');if(state.mode==='hierarchy'){const tree=el('div','ontology-workbench__tree-canvas');tree.append(el('div','ontology-workbench__empty','从左侧按需展开父子层级；多父类会显示为指向同一 canonical IRI 的引用行。'));replace(canvas,tree);}else if(!state.selectedIri)replace(canvas,el('div','ontology-workbench__empty','从左侧选择对象，或新增实体类、关系和属性。'));loadHierarchy();}
  const reportWarnings=()=>state.draft?.validation_report?.warnings||[];
  const decisionMap=()=>{const result=new Map();(state.draft?.decisions||[]).forEach(item=>result.set(item.operation_id,item));return result;};
  const operationWarnings=operation=>operation?.validation?.warnings||operation?.warnings||[];
  function operationLabel(operation){const labels={create_term:'新增对象',add_parent:'添加父级',remove_parent:'移除父级',add_domain:'添加 Domain',remove_domain:'移除 Domain',add_range:'添加 Range',remove_range:'移除 Range',add_annotation:'补充定义',remove_annotation:'移除定义',set_datatype:'调整数据类型',retire_term:'停用对象',restore_term:'恢复对象',advanced_rdf_patch:'高级 RDF 变更'};return labels[operation.action]||operation.action;}
  function isBatchEligible(operation){return operation.risk==='low'&&!operationWarnings(operation).length&&!['retire_term','restore_term','advanced_rdf_patch'].includes(operation.action);}
  function reviewOperations(){const query=state.filters.query.toLocaleLowerCase();const risk=state.filters.risk||'';return (state.draft?.operations||[]).filter(operation=>(!query||`${operation.action} ${operation.target_iri}`.toLocaleLowerCase().includes(query))&&(!risk||operation.risk===risk));}
  async function submitReview(){
    notice('正在冻结校验快照并提交审核…');
    try{const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(state.draftId)}/submit`),{method:'POST',body:JSON.stringify({expected_revision:state.revision})});state.draft=draft;state.revision=draft.revision;syncContext();notice('草案已提交，审核只针对当前变更队列。');setStage('review');}
    catch(error){handleGovernanceError(error);}
  }
  function handleGovernanceError(error){
    if(['revision_conflict','stale_base','stale_source','validation_changed'].includes(error.code)){notice(`${error.message}。请刷新草案后继续。`,'warning');const bar=byId('ontology-workbench-actionbar');const reload=el('button','','刷新并恢复');reload.type='button';reload.addEventListener('click',()=>selectDraft(state.draftId));replace(bar,reload);return;}
    notice(error.message,'error');
  }
  function renderReviewQueue(){
    const host=byId('ontology-workbench-library-list');replace(host);const operations=reviewOperations();const decisions=decisionMap();
    operations.forEach((operation,index)=>{const button=el('button','ontology-workbench__review-row');button.type='button';button.dataset.reviewOperation='';button.dataset.operationId=operation.id;button.dataset.batchEligible=String(isBatchEligible(operation));button.setAttribute('aria-current',String(index===(state.reviewIndex||0)));const decision=decisions.get(operation.id);button.append(el('span',`ontology-workbench__risk ${operation.risk||'medium'}`,operation.risk||'medium'),el('b','',operationLabel(operation)),el('small','',localName(operation.target_iri)),el('i','',decision?({approve:'已批准',reject:'已拒绝',request_changes:'待调整'}[decision.action]||decision.action):'待决策'));button.addEventListener('click',()=>selectReviewOperation(index));host.append(button);});
    if(!operations.length)host.append(el('div','ontology-workbench__empty','当前筛选下没有变更。'));
  }
  function selectReviewOperation(index){const operations=reviewOperations();if(!operations.length)return;state.reviewIndex=Math.max(0,Math.min(index,operations.length-1));renderReviewQueue();renderReviewOperation(operations[state.reviewIndex]);}
  function valuePanel(title,value,tone=''){const panel=el('section',`ontology-workbench__diff ${tone}`.trim());panel.append(el('h4','',title));const pre=el('pre');pre.textContent=value===null||value===undefined?'—':JSON.stringify(value,null,2);panel.append(pre);return panel;}
  function renderReviewOperation(operation){
    const canvas=byId('ontology-workbench-canvas-content');const article=el('article','ontology-workbench__review-detail');const head=el('header');const title=el('div');title.append(el('small','ontology-workbench__eyebrow',`${operation.risk?.toUpperCase()||'MEDIUM'} RISK · ${operation.source||'manual'}`),el('h3','',operationLabel(operation)),el('code','',operation.target_iri));head.append(title);article.append(head);
    const diff=el('div','ontology-workbench__diff-grid');diff.append(valuePanel('变更前',operation.before,'before'),valuePanel('变更后',operation.after,'after'));article.append(diff,valuePanel('影响上下文',operation.impact||{},'context'));replace(canvas,article);
    const inspector=byId('ontology-workbench-inspector');const evidence=el('section','ontology-workbench__inspector-section');evidence.append(el('h4','',`来源证据 ${(operation.evidence||[]).length}`));const list=el('div','ontology-workbench__evidence');(operation.evidence||[]).forEach(item=>{const card=el('article');card.append(el('b','',item.document_title||item.document_id||'来源快照'),el('small','',`置信度 ${item.confidence??operation.confidence??'—'}`),el('p','',item.evidence||item.excerpt||'未保存摘录'));list.append(card);});if(!list.childElementCount)list.append(el('p','','手工变更没有外部证据；审核依据应写入理由。'));evidence.append(list);
    const actions=el('section','ontology-workbench__inspector-section');actions.append(el('h4','','审核决策'));const buttons=el('div','ontology-workbench__decision-buttons');[['approve','A','批准'],['request_changes','E','要求调整'],['reject','R','拒绝']].forEach(([action,key,label])=>{const button=el('button',action==='approve'?'':'secondary',`${key} · ${label}`);button.type='button';button.dataset.decisionAction=action;button.addEventListener('click',()=>openDecision(action,operation));buttons.append(button);});actions.append(buttons,el('p','', 'J / K 切换变更；A / E / R 执行当前决策。决定会立即自动保存。'));replace(inspector,evidence,actions);
  }
  function warningChecks(attribute){const group=el('div','ontology-workbench__warning-list');reportWarnings().forEach(warning=>{const label=el('label','');const input=el('input');input.type='checkbox';input.dataset[attribute]=warning.code||'';label.append(input,document.createTextNode(`${warning.code||'warning'} · ${warning.message||'需要人工确认'}`));group.append(label);});return group;}
  function openDecision(action,operation){
    const warnings=reportWarnings();const needsReason=action!=='approve'||operation.risk==='high'||warnings.length>0;if(!needsReason){saveDecisions([{operation_id:operation.id,operation_fingerprint:operation.fingerprint,action,reason:null}],[]);return;}
    const host=byId('ontology-workbench-inspector');const form=el('section','ontology-workbench__inspector-section ontology-workbench__decision-form');form.append(el('h4','',`${action==='approve'?'批准':action==='reject'?'拒绝':'要求调整'} · ${operationLabel(operation)}`));const reason=el('textarea');reason.id='ontology-decision-reason';reason.placeholder=action==='approve'?'必填：说明为何可接受高风险或警告':'必填：写明可执行的审核意见';const checks=warningChecks('ackWarning');const save=el('button','','保存决定');save.type='button';save.dataset.saveDecision='';save.disabled=true;const update=()=>{const allWarnings=[...checks.querySelectorAll('input')].every(input=>input.checked);save.disabled=!reason.value.trim()||!allWarnings;};reason.addEventListener('input',update);checks.addEventListener('change',update);save.addEventListener('click',()=>saveDecisions([{operation_id:operation.id,operation_fingerprint:operation.fingerprint,action,reason:reason.value.trim()}],[...checks.querySelectorAll('input:checked')].map(input=>input.dataset.ackWarning)));form.append(reason,checks,save);replace(host,form);reason.focus();
  }
  async function saveDecisions(decisions,acknowledged){
    notice('正在保存审核决定…');try{const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(state.draftId)}/decisions`),{method:'POST',body:JSON.stringify({expected_revision:state.revision,expected_ontology_id:state.ontologyId,validation_fingerprint:state.draft.validation_fingerprint,acknowledged_warning_codes:acknowledged,decisions,actor:'reviewer@workbench'})});state.draft=draft;state.revision=draft.revision;syncContext();notice(`已保存 ${decisions.length} 项决定。`);if(draft.status==='editing'){setStage('design');return;}renderReview();}
    catch(error){handleGovernanceError(error);}
  }
  function renderReview(){
    if(state.draft.status==='editing'){renderDesign();notice('草案处于编辑状态，请提交后审核。','warning');return;}state.reviewIndex=Math.min(state.reviewIndex||0,Math.max(0,reviewOperations().length-1));renderReviewQueue();const operations=reviewOperations();if(operations.length)renderReviewOperation(operations[state.reviewIndex]);else replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty','当前筛选下没有待审变更。'));
    const bar=byId('ontology-workbench-actionbar');const risk=el('select');risk.id='ontology-review-risk-filter';[['','全部风险'],['low','低风险'],['medium','中风险'],['high','高风险']].forEach(([value,label])=>{const option=el('option','',label);option.value=value;risk.append(option);});risk.value=state.filters.risk||'';risk.addEventListener('change',()=>{state.filters.risk=risk.value;state.reviewIndex=0;renderReview();});const eligible=operations.filter(operation=>isBatchEligible(operation)&&!decisionMap().has(operation.id)).slice(0,100);const batch=el('button','','批准当前筛选的低风险项');batch.type='button';batch.dataset.batchApprove='';batch.hidden=eligible.length<2||reportWarnings().length>0;batch.addEventListener('click',()=>saveDecisions(eligible.map(operation=>({operation_id:operation.id,operation_fingerprint:operation.fingerprint,action:'approve',reason:null})),[]));const validate=el('button','secondary','查看校验');validate.type='button';validate.addEventListener('click',()=>setStage('validate'));replace(bar,risk,batch,validate);
  }
  function validationSection(id,title,report){const section=el('section','ontology-workbench__validation-card');section.dataset.validationSection=id;const errors=report?.errors||[];section.append(el('small','',errors.length?'NEEDS ATTENTION':'PASS'),el('h4','',title),el('p','',errors.length?`${errors.length} 个问题`:'检查通过'));errors.slice(0,20).forEach(issue=>section.append(el('p','ontology-workbench__validation-issue',`${issue.code||'error'} · ${issue.message||JSON.stringify(issue)}`)));if(id==='historical')section.append(el('p','',`检查 ${report?.checked_records||0} 条历史记录 · ${report?.nonconforming_records||0} 条不符合新约束`));return section;}
  async function ensureValidation(){if(state.draft.validation_report&&state.draft.validation_fingerprint)return state.draft;const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(state.draftId)}/validate`),{method:'POST',body:JSON.stringify({expected_revision:state.revision})});state.draft=draft;state.revision=draft.revision;syncContext();return draft;}
  async function renderValidation(){
    const canvas=byId('ontology-workbench-canvas-content');replace(canvas,loadNode('正在运行图完整性、前瞻写入和历史影响检查…'));try{const draft=await ensureValidation();const report=draft.validation_report||{};const head=el('header','ontology-workbench__validation-head');head.append(el('div','',''));head.firstChild.append(el('small','ontology-workbench__eyebrow','VALIDATION SNAPSHOT'),el('h3','',report.conforms?'校验通过':'存在阻断问题'),el('code','',draft.validation_fingerprint||'未生成指纹'));const cards=el('div','ontology-workbench__validation-grid');cards.append(validationSection('graph','图结构完整性',report.graph_integrity),validationSection('prospective','新写入约束',report.prospective_new_write_contract),validationSection('historical','历史数据影响',report.historical_impact));replace(canvas,head,cards);const bar=byId('ontology-workbench-actionbar');const back=el('button','secondary','返回审核');back.type='button';back.addEventListener('click',()=>setStage('review'));const publish=el('button','','进入发布');publish.type='button';publish.disabled=state.draft.status!=='reviewed'||!report.conforms;publish.addEventListener('click',()=>setStage('publish'));replace(bar,back,publish);}
    catch(error){handleGovernanceError(error);}
  }
  function openVersionGovernance(){document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));byId('tab-ontology')?.classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button===nav));byId('title').textContent='本体工作台 · 版本治理';byId('scope').classList.add('hidden');document.dispatchEvent(new CustomEvent('ontology-version-governance:open'));}
  function versionGovernanceButton(){const button=el('button','secondary','打开版本治理（历史 / Diff / Turtle / SPARQL）');button.type='button';button.addEventListener('click',openVersionGovernance);return button;}
  function renderPublish(){
    const canvas=byId('ontology-workbench-canvas-content');if(state.draft.status!=='reviewed'){replace(canvas,el('div','ontology-workbench__empty','所有当前变更必须先形成最终批准或拒绝决定，才能发布。'));replace(byId('ontology-workbench-actionbar'),versionGovernanceButton());return;}
    const form=el('section','ontology-workbench__publish-card');form.append(el('small','ontology-workbench__eyebrow','IMMUTABLE RELEASE'),el('h3','','发布为不可变本体版本'),el('p','',`草案 ${state.draft.id} · 修订 ${state.revision} · 校验指纹 ${state.draft.validation_fingerprint}`));const chain=el('ol','ontology-workbench__provenance-chain');['来源快照','统一草案','原子变更','审核决定','不可变版本'].forEach(label=>chain.append(el('li','',label)));form.append(chain);const warnings=warningChecks('publishWarning');form.append(warnings);const actor=el('input');actor.id='ontology-publisher';actor.placeholder='发布人，例如 owner@example.com';const publish=el('button','','确认发布');publish.type='button';publish.dataset.publishDraft='';publish.disabled=true;const update=()=>{publish.disabled=!actor.value.trim()||![...warnings.querySelectorAll('input')].every(input=>input.checked);};actor.addEventListener('input',update);warnings.addEventListener('change',update);publish.addEventListener('click',()=>publishDraft(actor.value.trim(),[...warnings.querySelectorAll('input:checked')].map(input=>input.dataset.publishWarning)));form.append(actor,publish);replace(canvas,form);replace(byId('ontology-workbench-actionbar'),versionGovernanceButton());
  }
  async function publishDraft(actor,acknowledged){
    const key=globalThis.crypto?.randomUUID?.()||`publish-${Date.now()}-${Math.random().toString(16).slice(2)}`;notice('正在原子发布本体版本并写入 provenance…');try{const version=await request(projectPath(`/ontology-drafts/${encodeURIComponent(state.draftId)}/publish`),{method:'POST',body:JSON.stringify({expected_revision:state.revision,expected_ontology_id:state.ontologyId,validation_fingerprint:state.draft.validation_fingerprint,acknowledged_warning_codes:acknowledged,idempotency_key:key,actor})});state.draft.status='published';const canvas=byId('ontology-workbench-canvas-content');const card=el('section','ontology-workbench__published');card.dataset.publishedVersion=version.id;card.append(el('small','ontology-workbench__eyebrow','PUBLISHED'),el('h3','',`版本 ${version.id} 已发布`),el('p','',`项目 ${state.projectId} · 草案 ${state.draftId} · 校验 ${state.draft.validation_fingerprint}`),el('p','',`来源 → 草案 → 变更 → 决策 → 版本的 provenance 链已冻结；同步任务失败也不会回滚已发布版本。`),versionGovernanceButton());replace(canvas,card);replace(byId('ontology-workbench-actionbar'));notice('本体版本发布完成。');}
    catch(error){handleGovernanceError(error);}
  }
  async function loadDrafts(){if(!state.projectId)return;try{const result=await request(projectPath('/ontology-drafts'));const drafts=result.items||[];renderDraftList(drafts);if(!state.draftId&&drafts.length)await selectDraft(drafts[0].id);else if(!drafts.length&&state.stage!=='discover')setStage('discover');}catch(error){if(error.name!=='AbortError')notice(error.message,'error');}}
  function renderDraftStage(){
    if(!state.projectId){replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty','请先选择项目。'));return;}
    if(!state.draft){replace(byId('ontology-workbench-canvas-content'),loadNode('正在加载草案…'));if(state.draftId)selectDraft(state.draftId);else loadDrafts();return;}
    if(state.stage==='design'){renderDesign();return;}
    if(state.stage==='review'){renderReview();return;}
    if(state.stage==='validate'){renderValidation();return;}
    if(state.stage==='publish'){renderPublish();return;}
  }
  async function renderDiscovery(){syncContext();await loadDiscovery();}
  function activate(stage=state.stage){document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));page.classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button===nav));byId('title').textContent='本体工作台';byId('scope').classList.add('hidden');syncContext();setStage(stage);}

  nav.addEventListener('click',()=>activate());
  page.querySelectorAll('[data-workbench-stage]').forEach(button=>button.addEventListener('click',()=>setStage(button.dataset.workbenchStage)));
  page.querySelectorAll('[data-workbench-mode]').forEach(button=>button.addEventListener('click',()=>{state.mode=button.dataset.workbenchMode;page.querySelectorAll('[data-workbench-mode]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));if(state.stage!=='discover')renderDraftStage();}));
  byId('ontology-workbench-refresh').addEventListener('click',()=>state.stage==='discover'?loadDiscovery():loadDrafts());
  byId('ontology-workbench-search-button').addEventListener('click',()=>{state.filters.query=byId('ontology-workbench-search').value.trim();state.stage==='discover'?renderDiscoveryData():renderDraftStage();});
  byId('ontology-workbench-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();byId('ontology-workbench-search-button').click();}});
  byId('candidate-map-source').addEventListener('change',event=>{state.filters.source=event.target.value;renderDiscoveryData();});
  byId('candidate-map-confidence').addEventListener('change',event=>{state.filters.confidence=event.target.value;renderDiscoveryData();});
  document.addEventListener('keydown',event=>{if(page.classList.contains('hidden')||state.stage!=='review'||event.altKey||event.ctrlKey||event.metaKey)return;const tag=document.activeElement?.tagName;if(['INPUT','TEXTAREA','SELECT'].includes(tag))return;const operations=reviewOperations();if(!operations.length)return;const key=event.key.toLowerCase();if(key==='j'){event.preventDefault();selectReviewOperation((state.reviewIndex||0)+1);}else if(key==='k'){event.preventDefault();selectReviewOperation((state.reviewIndex||0)-1);}else if(['a','e','r'].includes(key)){event.preventDefault();openDecision({a:'approve',e:'request_changes',r:'reject'}[key],operations[state.reviewIndex||0]);}});
  document.querySelectorAll('[data-tab]').forEach(button=>{if(button!==nav)button.addEventListener('click',cancelCandidateEvidence);});
  byId('project').addEventListener('change',()=>{cancelCandidateEvidence();state.controller?.abort();Object.assign(state,{projectId:'',ontologyId:null,draftId:null,revision:null,selectedIri:null,displayPath:[],discoveryKind:'entity',filters:{query:'',source:'',confidence:'',cluster:''},draft:null,discovery:null,candidates:null,changeProposals:[],hasDrafts:null});syncStageAvailability();syncContext();if(!page.classList.contains('hidden'))setStage('discover');});
  setStage('discover');
  window.OntologyWorkbench={state,open:activate,openVersionGovernance,selectDraft,setStage};
})();
