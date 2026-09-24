/* Unified ontology governance: discovery -> design -> review -> validate -> publish. */
(()=>{
  'use strict';
  const byId=id=>document.getElementById(id);
  const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node;};
  const state={projectId:'',ontologyId:null,draftId:null,revision:null,selectedIri:null,displayPath:[],mode:'object',stage:'discover',filters:{query:'',source:'',confidence:''},cursors:{roots:null,children:new Map(),search:null,matrix:null},hierarchy:{items:[],expanded:new Map()},matrix:[],draft:null,discovery:null,candidates:null,controller:null};
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
  function renderDiscoveryActions(){const bar=byId('ontology-workbench-actionbar');const legacy=el('button','secondary','打开完整候选脑图');legacy.type='button';legacy.addEventListener('click',()=>document.querySelector('[data-tab="candidate-mindmap"]')?.click());const create=el('button','','生成累计草案');create.type='button';create.id='ontology-workbench-create-discovery-draft';create.addEventListener('click',openDiscoveryDraftForm);replace(bar,legacy,create);}
  function openDiscoveryDraftForm(){const host=byId('ontology-workbench-inspector');const section=el('section','ontology-workbench__inspector-section');section.append(el('h4','','生成累计草案'),el('p','','当前候选及来源证据会冻结到统一草案，后续进入对象设计和逐项审核。'));const input=el('input');input.id='ontology-discovery-draft-name';input.maxLength=200;input.placeholder='例如：直播规则本体';input.value='发现本体';const create=el('button','','生成并进入设计');create.type='button';create.addEventListener('click',()=>createDiscoveryDraft(input.value));section.append(input,create);replace(host,section);input.focus();}
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
  async function createDiscoveryDraft(name){
    state.controller?.abort();state.controller=new AbortController();
    notice('正在通过 Semantica 归纳候选并冻结来源快照…');
    try{const draft=await request(projectPath('/ontology-discovery/drafts'),{method:'POST',body:JSON.stringify({name:String(name||'').trim()||'发现本体'})});state.draftId=draft.unified_draft_id||draft.id;state.revision=draft.draft_revision||draft.revision;state.ontologyId=draft.parent_ontology_id||null;syncContext();notice('累计草案已生成，已进入对象设计。');setStage('design');}
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
  function renderDesignActions(){const bar=byId('ontology-workbench-actionbar');const create=el('button','','新增对象');create.type='button';create.dataset.newObject='';create.addEventListener('click',openNewObject);const legacy=el('button','secondary','版本治理 / Turtle');legacy.type='button';legacy.addEventListener('click',()=>document.querySelector('[data-tab="ontology"]')?.click());const next=el('button','','提交审核');next.type='button';next.disabled=!state.draftId;next.addEventListener('click',()=>setStage('review'));replace(bar,legacy,create,next);}
  function renderDesign(){
    renderDesignActions();if(state.mode==='matrix'){loadMatrix();return;}const canvas=byId('ontology-workbench-canvas-content');if(state.mode==='hierarchy'){const tree=el('div','ontology-workbench__tree-canvas');tree.append(el('div','ontology-workbench__empty','从左侧按需展开父子层级；多父类会显示为指向同一 canonical IRI 的引用行。'));replace(canvas,tree);}else if(!state.selectedIri)replace(canvas,el('div','ontology-workbench__empty','从左侧选择对象，或新增实体类、关系和属性。'));loadHierarchy();}
  async function loadDrafts(){if(!state.projectId)return;try{const result=await request(projectPath('/ontology-drafts'));renderDraftList(result.items||[]);if(!state.draftId&&result.items?.length)await selectDraft(result.items[0].id);}catch(error){if(error.name!=='AbortError')notice(error.message,'error');}}
  function renderDraftStage(){
    if(!state.projectId){replace(byId('ontology-workbench-canvas-content'),el('div','ontology-workbench__empty','请先选择项目。'));return;}
    if(!state.draft){replace(byId('ontology-workbench-canvas-content'),loadNode('正在加载草案…'));if(state.draftId)selectDraft(state.draftId);else loadDrafts();return;}
    if(state.stage==='design'){renderDesign();return;}
    const canvas=byId('ontology-workbench-canvas-content');replace(canvas,draftSummary());const bar=byId('ontology-workbench-actionbar');const previous=el('button','secondary','返回设计');previous.type='button';previous.addEventListener('click',()=>setStage('design'));replace(bar,previous);
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
