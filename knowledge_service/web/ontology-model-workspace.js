/* On-demand draft review presentation; OntologyModel remains the only editing owner. */
(() => {
  const tabs=new Set(['definitions','facts','sources','archive']);
  const count=value=>Number.isInteger(value)&&value>=0?value:null;
  function sourceRecords(draft) {
    const context=draft?.source_context||{},documents=new Map(),runs=new Map();
    for(const item of context.documents||[]){
      const id=item.document_id||item.id;if(!id)continue;
      const version=item.expected_document_version??item.document_version??item.version??null;
      documents.set(JSON.stringify([id,version]),{id,version,title:item.document_title||item.title||item.name||null});
    }
    if(context.document_id&&!documents.size)documents.set(context.document_id,{id:context.document_id,
      version:context.expected_document_version??context.document_version??context.version??null,title:context.document_title||null});
    for(const item of context.intake_bindings||[]){
      const id=item.discovery_run_id||item.run_id;if(!id)continue;
      const previous=runs.get(id),ids=new Set([...(previous?.candidateIds||[]),...(item.selected_candidate_ids||[])]);
      runs.set(id,{id,taskId:item.task_id||item.job_id||previous?.taskId||null,candidateIds:[...ids],selected:ids.size});
    }
    return {documents:[...documents.values()],runs:[...runs.values()]};
  }
  function draftSummary(state={}) {
    const draft=state.draft,ops=draft?.operations||[],targets=new Map(),kinds={class:0,relation:0,attribute:0,unknown:0};
    const draftOntology=draft?.ontology?.summary||draft?.ontology||(!state.preview&&state.showingDraft?state.summary:null);
    const known=new Map([['classes','class'],['relations','relation'],['attributes','attribute']]
      .flatMap(([key,kind])=>(draftOntology?.[key]||[]).map(item=>[item.id,kind])));
    for(const op of ops){const id=op.target_iri||op.target||op.id;if(!id)continue;
      const kind=known.get(id)||(op.action==='create_term'?op.after?.kind||op.kind:null);
      if(!targets.has(id)||kind)targets.set(id,kind||targets.get(id)||'unknown');
    }
    for(const kind of targets.values())kinds[Object.hasOwn(kinds,kind)?kind:'unknown']++;
    let facts=count(draft?.associated_knowledge?.selected);
    if(facts===null&&Array.isArray(draft?.source_context?.intake_bindings))facts=new Set(
      draft.source_context.intake_bindings.flatMap(item=>item.selected_candidate_ids||[])).size;
    const pending=Array.isArray(state.readiness?.items)?state.readiness.items.filter(item=>item.severity==='error').length:null;
    return {definitions:targets.size,operations:ops.length,kinds,facts,pending,...sourceRecords(draft)};
  }
  function createController({getContext,onChange=()=>{}}) {
    const state={project:getContext().project||'',open:false,tab:'definitions',archiveId:null,expanded:false};
    const sync=()=>{const project=getContext().project||'';if(project!==state.project){Object.assign(state,{project,open:false,tab:'definitions',archiveId:null,expanded:false});onChange(state);}return state;};
    const update=values=>{sync();Object.assign(state,values);onChange(state);return state;};
    return {state,sync,open:tab=>update({open:true,...(tabs.has(tab)?{tab}:{})}),
      close:()=>update({open:false,expanded:false}),setTab:tab=>update({tab:tabs.has(tab)?tab:'definitions'}),
      toggleExpanded:()=>update({expanded:!state.expanded}),openDraft:id=>update({open:true,tab:'archive',archiveId:id||null})};
  }
  // Move the original live controls, retaining listeners, input values and server guards.
  const placements=new WeakMap();
  function relocateActions(source,target) {
    if(!source||!target)return null;
    const owner=source.firstElementChild||null,previous=placements.get(source);
    const controls=source.querySelector?.('.om-ab-ops')||
      (previous?.owner===owner?null:source.querySelector?.('.om-ab-actions'));
    if(controls){target.replaceChildren(controls);placements.set(source,{owner});}
    else if(previous&&previous.owner!==owner){target.replaceChildren();placements.set(source,{owner});}
    return controls||null;
  }
  let model=null,controller=null,nodes=null,lastProject='',archiveNeedsRefresh=true,definitionsOpen=true;
  const project=()=>typeof current==='undefined'?'':current;
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;};
  const button=(text,action,cls='om-btn ghost')=>{const node=el('button',cls,text);node.type='button';node.addEventListener('click',action);return node;};
  function versionLink(id,label) {
    return button(label,()=>{if(project()!==controller.state.project)return;close({focus:false});void window.OntologyPanel?.openHistory(id);});
  }
  function renderSummary(state,summary) {
    nodes.summary.replaceChildren();const draft=state.draft;
    nodes.summary.append(el('h3',null,draft?.title||'本轮建模变更'));
    nodes.summary.append(el('p','om-ws-state',state.preview?'正在查看历史本体 · 工作草案只读':draft?'草案未发布':'尚未选择工作草案'));
    if(draft){const links=el('div','om-ws-version-links');
      if(draft.base_ontology_id)links.append(versionLink(draft.base_ontology_id,
        draft.base_ontology_id===state.ontologyId&&state.ontologyVersion!=null?`基于 v${state.ontologyVersion} · 查看版本`:'查看基准版本'));
      else links.append(el('span',null,'初始草案 · 首次发布后生成 v1'));
      if(draft.published_ontology_id)links.append(versionLink(draft.published_ontology_id,'查看发布结果'));
      nodes.summary.append(links);
      nodes.summary.append(el('p','om-ws-scope',`${summary.definitions} 个定义 · 实体类型 ${summary.kinds.class} / 关系类型 ${summary.kinds.relation} / 属性 ${summary.kinds.attribute}${summary.kinds.unknown?' · 部分种类待核对':''}`));
    }else nodes.summary.append(el('p','om-ws-scope',state.draftChoices?.length>1?'从草案管理中选择本轮目标。':'新增定义或确认候选后，会进入工作草案。'));
  }
  function renderFacts(summary,state) {
    const host=nodes.facts;host.replaceChildren();
    host.append(el('h3',null,'本草案关联的候选知识'),el('p','om-ws-lead',summary.facts===null?'候选数量尚未记录，请重新读取草案核对。':`${summary.facts} 条候选事实 · 尚未正式入库`),
      el('p','om-ws-help','候选事实是原文中的实体、关系或属性陈述；数量不代表段落数或本体定义数。'));
    const openedProject=project(),draftId=state.draft?.id,revision=state.draft?.revision;
    for(const [i,run] of summary.runs.entries()){
      const row=el('section','om-ws-source-row');row.append(el('strong',null,`纳入批次 ${i+1}`),el('span',null,`本草案选中 ${run.selected} 条候选`));
      const technical=el('details','om-ws-technical');technical.append(el('summary',null,'纳入批次身份'),el('code',null,run.id));row.append(technical);
      if(!state.preview&&run.candidateIds.length)row.append(button('核对当前关联候选',()=>{
        if(project()!==openedProject||model.S.draft?.id!==draftId||model.S.draft?.revision!==revision||model.S.preview)return;
        model.openIntake?.({projectId:openedProject,candidateIds:[...run.candidateIds]});
      }));
      host.append(row);
    }
    if(summary.facts===0)host.append(el('p','om-ws-help','这份草案仅修改本体结构，没有绑定候选事实。'));
  }
  function renderSources(summary) {
    const host=nodes.sources;host.replaceChildren();host.append(el('h3',null,'来源与处理记录'),
      el('p','om-ws-help','保留加入草案时的资料版本与纳入批次；处理任务只在记录提供明确身份时关联。'));
    if(!summary.documents.length&&!summary.runs.length)host.append(el('p','om-ws-help','本草案未记录抽取来源，可能来自手动建模或历史恢复。'));
    for(const [i,doc] of summary.documents.entries()){
      const row=el('section','om-ws-source-row');row.append(el('strong',null,doc.title&&doc.title!==doc.id?doc.title:`来源资料 ${i+1}`),
        el('span',null,doc.version==null?'资料版本未记录':`加入草案时的资料版本 ${doc.version}`));
      const identity=el('details','om-ws-technical');identity.append(el('summary',null,'资料身份'),el('code',null,doc.id));row.append(identity);host.append(row);
    }
    for(const [i,run] of summary.runs.entries()){
      const row=el('section','om-ws-source-row');row.append(el('strong',null,`纳入批次 ${i+1}`),el('span',null,`关联本草案 ${run.selected} 条候选`));
      const identity=el('details','om-ws-technical');identity.append(el('summary',null,'批次与任务身份'),el('code',null,run.id));
      if(run.taskId)identity.append(el('p',null,'任务标识'),el('code',null,run.taskId));
      else identity.append(el('p',null,'这条来源未记录独立任务标识。'));
      row.append(identity);host.append(row);
    }
  }
  function apply() {
    if(!nodes||!controller)return;
    const state=controller.state,archive=state.tab==='archive',root=nodes.root;
    nodes.shell.hidden=!state.open;nodes.backdrop.hidden=!state.open;
    root.classList.toggle('om-workspace-open',state.open);root.classList.toggle('om-workspace-expanded',state.open&&state.expanded);
    nodes.toggle.setAttribute('aria-expanded',String(state.open));
    nodes.expand.textContent=state.expanded?'收起完整审核':'展开完整审核';nodes.expand.setAttribute('aria-pressed',String(state.expanded));
    nodes.heading.textContent=archive?'历史草案 · 只读':'草案变更';
    nodes.summary.hidden=archive;
    for(const item of nodes.tabs){const selected=item.dataset.workspaceTab===state.tab;item.setAttribute('aria-pressed',String(selected));}
    for(const [tab,host] of Object.entries(nodes.panels))host.hidden=tab!==state.tab;
    nodes.actions.hidden=archive;nodes.footer.hidden=archive||!!model.S.preview;
    if(state.open&&archive&&archiveNeedsRefresh){
      archiveNeedsRefresh=false;
      if(state.archiveId)void window.OntologyDraftHistory?.show(state.archiveId);
      else void window.OntologyDraftHistory?.showList();
    }
  }
  function syncActions() {if(nodes){relocateActions(nodes.actions,nodes.controls);nodes.footer.setAttribute('aria-busy',nodes.actions.getAttribute('aria-busy')||'false');}}
  function open(tab) {
    if(!controller)return;
    model.setInspector?.(false);controller.open(tab);render(model.S);nodes.close.focus?.();
  }
  function close({focus=true}={}) {
    if(!controller)return;
    const wasOpen=controller.state.open;controller.close();archiveNeedsRefresh=true;window.OntologyDraftHistory?.suspend?.();
    if(focus&&wasOpen)nodes.toggle.focus?.();
  }
  function openDraft(id) {
    if(!controller)return;
    model.setInspector?.(false);archiveNeedsRefresh=true;controller.openDraft(id);render(model.S);nodes.close.focus?.();
  }
  function reset() {if(controller)controller.close();archiveNeedsRefresh=true;lastProject='';window.OntologyDraftHistory?.reset();}
  function render(state) {
    if(!nodes||!controller)return;
    controller.sync();
    if(lastProject!==project()){lastProject=project();archiveNeedsRefresh=true;definitionsOpen=true;window.OntologyDraftHistory?.reset();}
    const summary=draftSummary(state),badge=summary.pending===null?'':summary.pending?` · ${summary.pending} 项发布待办`:'';
    nodes.toggle.textContent=`草案变更 ${summary.definitions}${badge}`;
    nodes.toggle.title='查看本轮定义变更、关联知识、来源和历史草案';
    nodes.tabFacts.textContent=summary.facts===null?'关联知识':`关联知识 ${summary.facts}`;
    nodes.tabDefinitions.textContent=`定义变更 ${summary.definitions}`;
    if(controller.state.open&&controller.state.tab!=='archive'){
      renderSummary(state,summary);renderFacts(summary,state);renderSources(summary);
      const list=document.querySelector?.('#om-work-changes-host .om-work-definition-list');
      if(list&&!list.dataset.workspaceManaged){list.dataset.workspaceManaged='true';list.open=definitionsOpen;list.addEventListener('toggle',()=>{definitionsOpen=list.open;});}
    }
    syncActions();apply();
  }
  function mount(controllerModel) {
    const get=id=>document.getElementById(id),shell=get('om-workspace-shell');if(!shell)return;
    if(nodes?.shell===shell&&model===controllerModel){render(model.S);return;}
    model=controllerModel;lastProject=project();archiveNeedsRefresh=true;
    nodes={root:get('tab-ontology-model'),shell,backdrop:get('om-workspace-backdrop'),toggle:get('om-workspace-toggle'),
      heading:get('om-workspace-heading'),expand:get('om-workspace-expand'),close:get('om-workspace-close'),summary:get('om-workspace-summary'),
      facts:get('om-workspace-facts'),sources:get('om-workspace-sources'),actions:get('om-actions-host'),footer:get('om-workspace-footer'),controls:get('om-workspace-controls'),
      tabFacts:get('om-workspace-tab-facts'),tabDefinitions:get('om-workspace-tab-definitions'),
      tabs:[...shell.querySelectorAll('[data-workspace-tab]')],panels:{definitions:get('om-workspace-definitions'),facts:get('om-workspace-facts'),sources:get('om-workspace-sources'),archive:get('om-workspace-archive')}};
    controller=createController({getContext:()=>({project:project()}),onChange:apply});
    window.OntologyDraftHistory?.mount(nodes.panels.archive,{model,onVersion:id=>{close({focus:false});void window.OntologyPanel?.openHistory(id);}});
    nodes.toggle.addEventListener('click',()=>controller.state.open?close():open());
    nodes.close.addEventListener('click',()=>close());nodes.backdrop.addEventListener('click',()=>close());
    nodes.expand.addEventListener('click',()=>{controller.toggleExpanded();nodes.expand.focus?.();window.OntologyCanvas?.resize?.();});
    for(const item of nodes.tabs)item.addEventListener('click',()=>{if(item.dataset.workspaceTab==='archive')archiveNeedsRefresh=true;controller.setTab(item.dataset.workspaceTab);render(model.S);});
    nodes.shell.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close();return;}
      if(event.key!=='Tab'||(typeof window.innerWidth==='number'&&window.innerWidth>1280))return;
      const items=[...nodes.shell.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,a[href]')]
        .filter(item=>item.getClientRects?.().length);
      const first=items[0],last=items[items.length-1];if(!first)return;
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    });
    render(model.S);
  }
  const apiObject={sourceRecords,draftSummary,createController,relocateActions,mount,render,open,close,openDraft,reset,syncActions};
  if(typeof module!=='undefined'&&module.exports)module.exports=apiObject;
  if(typeof window!=='undefined')window.OntologyWorkspace=apiObject;
})();
