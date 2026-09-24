/* Unified ontology governance: discovery -> design -> review -> validate -> publish. */
(()=>{
  'use strict';
  const byId=id=>document.getElementById(id);
  const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node;};
  const state={projectId:'',ontologyId:null,draftId:null,revision:null,selectedIri:null,displayPath:[],mode:'object',stage:'discover',filters:{query:'',source:'',confidence:''},cursors:{roots:null,children:new Map(),search:null,matrix:null},draft:null,discovery:null,candidates:null,controller:null};
  const stages=[['discover','1','发现','候选与证据'],['design','2','设计','对象与层级'],['review','3','审核','逐项决策'],['validate','4','校验','图约束检查'],['publish','5','发布','版本与溯源']];
  const page=el('section','tab hidden ontology-workbench');
  page.id='tab-ontology-workbench';
  page.innerHTML=`<header class="ontology-workbench__masthead"><div><small class="ontology-workbench__eyebrow">ONTOLOGY GOVERNANCE WORKBENCH</small><h2>把发现变成可审计的领域语言</h2><p>开放发现保留探索性，正式本体通过草案、逐项审核和不可变版本进入唯一真相源。多父类按 DAG 管理；删除统一为可恢复的停用。</p></div><div class="ontology-workbench__context"><span>当前工作上下文</span><strong id="ontology-workbench-context">请选择项目</strong><small id="ontology-workbench-revision">未选择草案</small></div></header><ol class="ontology-workbench__stages" aria-label="本体治理阶段">${stages.map(([id,step,label,copy])=>`<li><button type="button" class="ontology-workbench__stage" data-workbench-stage="${id}" data-step="${step}"><strong>${label}</strong><small>${copy}</small></button></li>`).join('')}</ol><div id="ontology-workbench-notice" class="ontology-workbench__notice" role="status" aria-live="polite"></div><div class="ontology-workbench__body"><aside class="ontology-workbench__pane ontology-workbench__library" aria-label="对象和变更库"><div class="ontology-workbench__pane-head"><div><h3 id="ontology-workbench-library-title">发现队列</h3><p id="ontology-workbench-library-copy">按来源和置信度检查候选</p></div><button type="button" id="ontology-workbench-refresh" class="secondary">刷新</button></div><div class="ontology-workbench__search"><input id="ontology-workbench-search" type="search" placeholder="搜索对象、IRI 或变更" aria-label="搜索本体对象"><button type="button" id="ontology-workbench-search-button" class="secondary">搜索</button></div><div id="ontology-workbench-filters" class="ontology-workbench__filters"><label>来源<select id="candidate-map-source"><option value="">全部来源</option></select></label><label>最低置信度<select id="candidate-map-confidence"><option value="">不限</option><option value="0.9">≥ 0.90</option><option value="0.75">≥ 0.75</option><option value="0.5">≥ 0.50</option></select></label></div><div id="ontology-workbench-library-list" class="ontology-workbench__list"></div></aside><main class="ontology-workbench__pane ontology-workbench__canvas"><div class="ontology-workbench__canvas-head"><div><small id="ontology-workbench-kicker" class="ontology-workbench__eyebrow">DISCOVERY · 非正式知识</small><h3 id="ontology-workbench-canvas-title">候选知识地图</h3></div><div class="ontology-workbench__view-switch" role="group" aria-label="本体视图"><button type="button" data-workbench-mode="object" aria-pressed="true">对象</button><button type="button" data-workbench-mode="hierarchy" aria-pressed="false">层级</button><button type="button" data-workbench-mode="matrix" aria-pressed="false">矩阵</button></div></div><div id="ontology-workbench-canvas-content"><div class="ontology-workbench__empty">选择项目后加载发现候选、聚类统计和来源证据。</div></div><div id="ontology-workbench-actionbar" class="ontology-workbench__actionbar"></div></main><aside class="ontology-workbench__pane ontology-workbench__inspector" aria-label="证据与审核检查器"><div id="ontology-workbench-inspector"><section class="ontology-workbench__inspector-section"><h4>检查器</h4><p>从左侧选择候选或对象，在这里查看来源、影响面与审核状态。</p></section></div></aside></div>`;
  document.querySelector('main').append(page);
  const nav=document.querySelector('[data-tab="ontology-workbench"]');

  const notice=(message='',tone='')=>{const host=byId('ontology-workbench-notice');host.textContent=message;host.dataset.tone=tone;};
  const replace=(host,...nodes)=>host.replaceChildren(...nodes.filter(Boolean));
  const projectPath=suffix=>{if(!state.projectId)throw new Error('请先选择项目');return `/api/projects/${encodeURIComponent(state.projectId)}${suffix}`;};
  async function request(path,options={}){
    const response=await fetch(path,{...options,signal:options.signal||state.controller?.signal,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
    const raw=await response.text();let payload={};
    try{payload=raw?JSON.parse(raw):{};}catch{throw new Error(`服务返回了无法解析的数据（HTTP ${response.status}）`);}
    if(!response.ok){const detail=payload?.detail;throw new Error(typeof detail==='string'?detail:(detail?.message||payload?.code||`请求失败（HTTP ${response.status}）`));}
    return payload;
  }
  const loadNode=message=>{const node=el('div','ontology-workbench__loading',message);return node;};
  function syncContext(){
    state.projectId=byId('project')?.value||'';
    const projectLabel=byId('project')?.selectedOptions?.[0]?.textContent||'请选择项目';
    byId('ontology-workbench-context').textContent=projectLabel;
    byId('ontology-workbench-revision').textContent=state.draftId?`草案 ${state.draftId.slice(0,8)} · 修订 ${state.revision}`:'未选择草案';
  }
  function setStage(stage){
    state.stage=stage;
    page.querySelectorAll('[data-workbench-stage]').forEach(button=>{if(button.dataset.workbenchStage===stage)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
    const discover=stage==='discover';
    byId('ontology-workbench-filters').hidden=!discover;
    byId('ontology-workbench-kicker').textContent={discover:'DISCOVERY · 非正式知识',design:'DESIGN · 草案叠加层',review:'REVIEW · 变更驱动',validate:'VALIDATION · 可重复快照',publish:'PUBLISH · 不可变版本'}[stage];
    byId('ontology-workbench-canvas-title').textContent={discover:'候选知识地图',design:'对象设计画布',review:'逐项审核队列',validate:'校验报告',publish:'版本发布与溯源'}[stage];
    byId('ontology-workbench-library-title').textContent=discover?'发现队列':stage==='review'?'变更队列':'对象与草案';
    byId('ontology-workbench-library-copy').textContent=discover?'按来源和置信度检查候选':'在统一草案上继续工作';
    if(stage==='discover')renderDiscovery();else renderDraftStage();
  }
  function metric(value,label){const card=el('div','ontology-workbench__metric');card.append(el('strong','',value??0),el('span','',label));return card;}
  function cluster(title,rows){
    const box=el('section','ontology-workbench__cluster');box.append(el('h4','',title));
    if(!rows.length){box.append(el('p','ontology-workbench__empty','暂无数据'));return box;}
    rows.slice(0,10).forEach(item=>{const row=el('div','ontology-workbench__cluster-row');row.append(el('span','',item.name||item.type||'未命名'),el('b','',item.count??item.occurrence_count??0));box.append(row);});return box;
  }
  const candidateSources=item=>item.sources||[];
  function visibleCandidates(){
    const query=state.filters.query.toLocaleLowerCase();const threshold=Number(state.filters.confidence||0);const source=state.filters.source;
    return (state.candidates?.nodes||[]).filter(item=>{
      const sources=candidateSources(item);const confidence=Math.max(0,...sources.map(row=>Number(row.confidence)||0));
      const matchesQuery=!query||`${item.text||''} ${item.type||''}`.toLocaleLowerCase().includes(query);
      const matchesSource=!source||sources.some(row=>(row.document_id||row.document_title)===source);
      return matchesQuery&&matchesSource&&confidence>=threshold;
    });
  }
  function renderCandidateInspector(item){
    state.selectedIri=item.id||null;const host=byId('ontology-workbench-inspector');const summary=el('section','ontology-workbench__inspector-section');
    summary.append(el('h4','',item.text||item.type||'候选详情'),el('p','',`${item.type||'未分类'} · 累计出现 ${item.occurrence_count||0} 次`));
    const evidence=el('section','ontology-workbench__inspector-section');evidence.append(el('h4','',`来源证据 ${candidateSources(item).length}`));const list=el('div','ontology-workbench__evidence');
    candidateSources(item).forEach(source=>{const article=el('article');article.append(el('b','',source.document_title||source.document_id||'未知来源'),el('small','',`${source.chunk_id||'未记录片段'} · 置信度 ${source.confidence??'—'}`),el('p','',source.evidence||'未保存证据片段'));list.append(article);});
    if(!list.childElementCount)list.append(el('p','', '没有可展示的来源证据。'));evidence.append(list);replace(host,summary,evidence);
  }
  function renderDiscoveryData(){
    const canvas=byId('ontology-workbench-canvas-content');const library=byId('ontology-workbench-library-list');
    const data=state.discovery||{};const candidates=visibleCandidates();
    const metrics=el('section','ontology-workbench__metrics discovery-metrics');metrics.append(metric(data.candidate_count,'候选总数'),metric(data.entity_count,'实体候选'),metric(data.relation_count,'关系候选'),metric(data.attribute_count,'属性候选'));
    const clusters=el('section','ontology-workbench__clusters');clusters.append(cluster('实体类型簇',data.type_clusters||data.entity_types||[]),cluster('关系与属性簇',[...(data.relation_clusters||data.relation_types||[]),...(data.attribute_clusters||data.attributes||[])]));
    const heading=el('div','ontology-workbench__pane-head');const title=el('div');title.append(el('h3','',`候选脑图 · ${candidates.length} 个可见对象`),el('p','', '聚类只是浏览投影，不会自动成为正式本体。'));heading.append(title);
    const grid=el('section','ontology-workbench__candidate-grid candidate-map-canvas');
    candidates.slice(0,120).forEach(item=>{const button=el('button','ontology-workbench__candidate');button.type='button';button.dataset.candidateId=item.id||'';button.append(el('span','',item.text||item.id),el('small','',`${item.type||'未分类'} · ${item.occurrence_count||0} 次`));button.addEventListener('click',()=>renderCandidateInspector(item));grid.append(button);});
    if(!grid.childElementCount)grid.append(el('div','ontology-workbench__empty','当前筛选下没有候选，请调整来源、置信度或搜索条件。'));
    replace(canvas,metrics,clusters,heading,grid);
    replace(library);
    candidates.slice(0,80).forEach(item=>{const button=el('button','ontology-workbench__list-item');button.type='button';button.append(el('b','',item.text||item.id),el('small','',`${item.type||'未分类'} · ${candidateSources(item).length} 份来源`));button.addEventListener('click',()=>renderCandidateInspector(item));library.append(button);});
    renderDiscoveryActions();
  }
  function discoverySources(){const values=new Map();(state.candidates?.nodes||[]).flatMap(candidateSources).forEach(row=>{const key=row.document_id||row.document_title;if(key)values.set(key,row.document_title||row.document_id);});return values;}
  function refreshSourceFilter(){const select=byId('candidate-map-source');const selected=state.filters.source;replace(select,el('option','','全部来源'));select.firstChild.value='';discoverySources().forEach((label,value)=>{const option=el('option','',label);option.value=value;select.append(option);});select.value=selected;}
  function renderDiscoveryActions(){const bar=byId('ontology-workbench-actionbar');const legacy=el('button','secondary','打开完整候选脑图');legacy.type='button';legacy.addEventListener('click',()=>document.querySelector('[data-tab="candidate-mindmap"]')?.click());const create=el('button','','生成累计草案');create.type='button';create.id='ontology-workbench-create-discovery-draft';create.addEventListener('click',createDiscoveryDraft);replace(bar,legacy,create);}
  async function loadDiscovery(){
    if(!state.projectId){replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty','请先从左侧选择项目。'));replace(byId('ontology-workbench-library-list'));return;}
    state.controller?.abort();state.controller=new AbortController();
    replace(byId('ontology-workbench-canvas-content'),loadNode('正在读取发现统计与候选证据…'));notice();
    try{
      const [discovery,candidates,drafts]=await Promise.all([request(projectPath('/ontology-discovery')),request(projectPath('/ontology-discovery/candidate-mindmap?limit=500')),request(projectPath('/ontology-drafts'))]);
      state.discovery=discovery;state.candidates=candidates;renderDraftList(drafts.items||[]);refreshSourceFilter();renderDiscoveryData();
    }catch(error){if(error.name!=='AbortError'){notice(error.message,'error');replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty',error.message));}}
  }
  function renderDraftList(drafts){
    if(state.stage==='discover')return;
    const host=byId('ontology-workbench-library-list');replace(host);
    drafts.forEach(draft=>{const button=el('button','ontology-workbench__draft');button.type='button';button.append(el('b','',draft.title||'未命名草案'),el('span','',draft.status||'editing'),el('small','',`${draft.source_kind||'manual'} · 修订 ${draft.revision}`));button.addEventListener('click',()=>selectDraft(draft.id));host.append(button);});
    if(!drafts.length)host.append(el('div','ontology-workbench__empty','还没有草案。可从发现候选生成，或创建手工草案。'));
  }
  async function createDiscoveryDraft(){
    state.controller?.abort();state.controller=new AbortController();
    const name=window.prompt('草案名称','发现本体');if(name===null)return;notice('正在通过 Semantica 归纳候选并冻结来源快照…');
    try{const draft=await request(projectPath('/ontology-discovery/drafts'),{method:'POST',body:JSON.stringify({name:name.trim()||'发现本体'})});state.draftId=draft.unified_draft_id||draft.id;state.revision=draft.draft_revision||draft.revision;state.ontologyId=draft.parent_ontology_id||null;syncContext();notice('累计草案已生成，已进入对象设计。');setStage('design');}
    catch(error){if(error.name!=='AbortError')notice(error.message,'error');}
  }
  async function selectDraft(id){
    state.controller?.abort();state.controller=new AbortController();
    notice('正在加载草案叠加层…');try{const draft=await request(projectPath(`/ontology-drafts/${encodeURIComponent(id)}`));state.draft=draft;state.draftId=draft.id;state.revision=draft.revision;state.ontologyId=draft.base_ontology_id;syncContext();notice();renderDraftStage();}catch(error){if(error.name!=='AbortError')notice(error.message,'error');}
  }
  function draftSummary(){
    const draft=state.draft;if(!draft)return el('div','ontology-workbench__empty','选择现有草案，或从“发现”阶段生成累计草案。');
    const box=el('div');const metrics=el('section','ontology-workbench__metrics');const operations=draft.operations||[];metrics.append(metric(operations.length,'当前变更'),metric(operations.filter(x=>x.risk==='high').length,'高风险'),metric(operations.filter(x=>x.risk==='medium').length,'中风险'),metric((draft.decisions||[]).length,'已决策'));box.append(metrics);
    const heading=el('div','ontology-workbench__pane-head');const copy=el('div');copy.append(el('h3','',draft.title||'未命名草案'),el('p','',`${draft.source_kind||'manual'} · ${draft.status||'editing'} · 基于 ${draft.base_ontology_id||'首个版本'}`));heading.append(copy);box.append(heading);
    const list=el('div','ontology-workbench__list');operations.forEach(operation=>{const button=el('button','ontology-workbench__list-item');button.type='button';button.append(el('b','',operation.action),el('small','',`${operation.target_iri} · ${operation.risk||'unknown'}`));button.addEventListener('click',()=>renderOperationInspector(operation));list.append(button);});if(!operations.length)list.append(el('div','ontology-workbench__empty','草案尚无变更。设计阶段可新增对象或调整父级。'));box.append(list);return box;
  }
  function renderOperationInspector(operation){state.selectedIri=operation.target_iri;const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section');section.append(el('h4','',operation.action),el('p','',operation.target_iri),el('p','',`风险：${operation.risk||'未评估'} · 来源：${operation.source||'manual'}`));const payload=el('pre','');payload.textContent=JSON.stringify({before:operation.before,after:operation.after,impact:operation.impact,validation:operation.validation},null,2);section.append(payload);replace(host,section);}
  async function loadDrafts(){if(!state.projectId)return;try{const result=await request(projectPath('/ontology-drafts'));renderDraftList(result.items||[]);if(!state.draftId&&result.items?.length)await selectDraft(result.items[0].id);}catch(error){if(error.name!=='AbortError')notice(error.message,'error');}}
  function renderDraftStage(){
    const canvas=byId('ontology-workbench-canvas-content');replace(canvas,draftSummary());const bar=byId('ontology-workbench-actionbar');replace(bar);
    if(state.stage==='design'){const legacy=el('button','secondary','打开本体管理');legacy.type='button';legacy.addEventListener('click',()=>document.querySelector('[data-tab="ontology"]')?.click());const next=el('button','','进入逐项审核');next.type='button';next.disabled=!state.draftId;next.addEventListener('click',()=>setStage('review'));bar.append(legacy,next);}
    else{const previous=el('button','secondary','返回设计');previous.type='button';previous.addEventListener('click',()=>setStage('design'));bar.append(previous);}
    loadDrafts();
  }
  async function renderDiscovery(){syncContext();await loadDiscovery();}
  function activate(){document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));page.classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button===nav));byId('title').textContent='本体工作台';byId('scope').classList.add('hidden');syncContext();setStage(state.stage);}

  nav.addEventListener('click',activate);
  page.querySelectorAll('[data-workbench-stage]').forEach(button=>button.addEventListener('click',()=>setStage(button.dataset.workbenchStage)));
  page.querySelectorAll('[data-workbench-mode]').forEach(button=>button.addEventListener('click',()=>{state.mode=button.dataset.workbenchMode;page.querySelectorAll('[data-workbench-mode]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));if(state.stage!=='discover')renderDraftStage();}));
  byId('ontology-workbench-refresh').addEventListener('click',()=>state.stage==='discover'?loadDiscovery():loadDrafts());
  byId('ontology-workbench-search-button').addEventListener('click',()=>{state.filters.query=byId('ontology-workbench-search').value.trim();state.stage==='discover'?renderDiscoveryData():renderDraftStage();});
  byId('ontology-workbench-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();byId('ontology-workbench-search-button').click();}});
  byId('candidate-map-source').addEventListener('change',event=>{state.filters.source=event.target.value;renderDiscoveryData();});
  byId('candidate-map-confidence').addEventListener('change',event=>{state.filters.confidence=event.target.value;renderDiscoveryData();});
  byId('project').addEventListener('change',()=>{state.controller?.abort();Object.assign(state,{projectId:'',ontologyId:null,draftId:null,revision:null,selectedIri:null,displayPath:[],draft:null,discovery:null,candidates:null});syncContext();if(!page.classList.contains('hidden'))setStage('discover');});
  setStage('discover');
  window.OntologyWorkbench={state,open:activate,selectDraft,setStage};
})();
