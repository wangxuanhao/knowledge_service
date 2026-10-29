/* Linked graph workspace: preserve existing editing/governance controls. */
(() => {
  // 节点拖动调整视图布局；空白拖动平移。事实修改仍走版本化编辑器。
  const ui = {request:0, options:[], pinned:new Map(),picker:{request:0,cursor:null,query:'',page:0},mindmapPicker:{request:0,cursor:null,query:'',page:0,options:[]},mindmapRootId:'',ontology:null, graph:null};
  let ontologyLoadGeneration=0;
  const graphRequests=GraphTypeFilter.createGraphRequestGate();
  const get = id => document.getElementById(id);
  const make = (tag, html, className='') => {const e=document.createElement(tag);e.innerHTML=html;e.className=className;return e;};
  const graphExpandState={busy:false,pendingDetail:false};
  for(const [label,html] of [
    ['邻域方向','<select id="graph-direction" aria-label="邻域方向"><option value="both">双向</option><option value="forward">沿箭头</option><option value="backward">逆箭头</option></select>'],
    ['实体上限','<select id="graph-max-nodes" aria-label="局部图实体上限"><option>250</option><option selected>500</option><option>1000</option><option>2000</option></select>'],
    ['关系上限','<select id="graph-max-edges" aria-label="局部图关系上限"><option selected>1000</option><option>2000</option><option>5000</option></select>'],
  ])get('graph-local-budget-controls').append(make('label',label+html,'graph-hop-control'));
  const platformGroupLabel=group=>group?.source_scope==='all'?'通用规则':
    group?.source_scope==='unrestricted'?'全部范围':
    group?.source_scope==='unknown'?'范围待确认（不参与确定性答案）':
    '平台：'+(group?.platform||'未命名');
  function syncGraphExpandDisabled(){get('graph-expand').disabled=graphExpandState.busy||graphExpandState.pendingDetail;}
  function setActionBusy(id,busy){
    if(id==='graph-expand'){graphExpandState.busy=busy;syncGraphExpandDisabled();}
    else get(id).disabled=busy;
  }
  const act = (id, fn) => {get(id).onclick=async()=>{setActionBusy(id,true);try{await fn();}catch(e){status(e.message,true);}finally{setActionBusy(id,false);}};};
  const ontoName=t=>t.label_zh||((t.label&&t.label!==t.name)?t.label:'')||({'name':'名称'}[t.name])||t.description||t.name||'';

  // ── Metadata condition builder ──
  const scopePanel=get('scope');
  const details=get('cb-details');
  const TIME_FIELDS=new Set(['valid_from','valid_until']);
  const cb={conditions:[],nextId:1,facets:[],custom:false};
  const opLabel=op=>({eq:'等于',ne:'不等于',contains:'包含',in:'属于集合',gte:'大于等于',lte:'小于等于',exists:'存在',interval:'区间'}[op]||op);
  const TIME_OPS=[{v:'eq',t:'等于'},{v:'ne',t:'不等于'},{v:'gte',t:'大于等于'},{v:'lte',t:'小于等于'},{v:'interval',t:'区间'}];
  const TEXT_OPS=[{v:'eq',t:'等于'},{v:'ne',t:'不等于'},{v:'contains',t:'包含'},{v:'in',t:'属于集合'},{v:'gte',t:'大于等于'},{v:'lte',t:'小于等于'},{v:'exists',t:'字段存在'}];
  const fieldLabel=field=>window.ScopeFields.label(field);
  function usesPostgresFields(){
    const page=window.WorkspaceContext?.page;
    return window.ScopeFields.usesPostgresFilters({page,purpose:get('scope-purpose')?.value,
      retrievalMode:get(page==='qa'?'qa-mode':'search-mode')?.value,knownAt:get('scope-known-at')?.value});
  }
  const visibleFacets=()=>usesPostgresFields()?window.ScopeFields.visibleFacets(cb.facets,{
    showSystem:Boolean(get('cb-show-system')?.checked),selected:cb.conditions.map(c=>c.field),
  }):[];
  function fieldOptions(selectedField){
    const choices=[...window.ScopeFields.retrievalFields(),...visibleFacets().map(f=>({v:f.field,l:fieldLabel(f.field)+' · '+f.records+' 条'}))];
    if(selectedField&&!choices.some(row=>row.v===selectedField))choices.push({v:selectedField,l:fieldLabel(selectedField)+'（已保留的业务字段）'});
    return choices;
  }
  function refreshFilterPurpose(){
    const postgres=usesPostgresFields(),help=get('scope-field-help'),discovery=get('metadata-discovery'),system=get('cb-system-fields');
    if(discovery)discovery.hidden=!postgres;if(system)system.hidden=!postgres;
    if(help)help.textContent=postgres?'可使用项目 Metadata；复杂业务条件由 PostgreSQL 判断资格，向量排名仍由 Milvus 完成。图谱与检索共用检索页的已应用条件。':
      '当前检索支持记录 ID、种类、知识类型、片段所属文档和有效时间；平台、业务、对象使用上方专用筛选。本体使用本体范围。其他 Metadata 条件会保留，并在检索前提示。';
    const raw=get('cb-raw-json');if(raw)raw.placeholder=postgres?'例如：{"field":"metadata.region.city","op":"eq","value":"北京"}':'例如：{"field":"type","op":"eq","value":"Merchant"}';
    const page=window.WorkspaceContext?.page,warning=get('scope-compatibility');
    if(warning){
      warning.textContent='';warning.hidden=true;
      if(page==='search'||page==='qa')try{window.ScopeFields.requireRetrievalScope({...scope(),retrieval_mode:get(page==='qa'?'qa-mode':'search-mode')?.value});}
      catch(error){warning.textContent=error.message;warning.hidden=false;}
    }
  }
  function addCondition(field='valid_from',op='eq',value='',from='',to=''){
    cb.conditions.push({id:cb.nextId++,field,op,value,from,to});
    renderCB();syncFilters();
  }
  function removeCondition(id){cb.conditions=cb.conditions.filter(c=>c.id!==id);renderCB();syncFilters();}
  function renderCB(){
    const rows=get('cb-rows');
    for(const id of ['cb-add','metadata-field-choice','metadata-value-choice'])get(id).disabled=cb.custom;
    if(cb.custom){rows.innerHTML='<p class="cb-empty">正在使用高级 JSON 条件；清空 JSON 后可恢复逐项编辑。</p>';}
    else if(!cb.conditions.length){rows.innerHTML='<p class="cb-empty">未添加其他条件</p>';}
    else{rows.innerHTML='';for(const c of cb.conditions){
      const isTime=TIME_FIELDS.has(c.field),ops=isTime?TIME_OPS:TEXT_OPS,showInterval=c.op==='interval';
      const row=make('div','','cb-row');row.dataset.id=c.id;
      row.innerHTML=`<select class="cb-field">${fieldOptions(c.field).map(o=>`<option value="${esc(o.v)}"${o.v===c.field?' selected':''}>${esc(o.l)}</option>`).join('')}</select><select class="cb-op">${ops.map(o=>`<option value="${o.v}"${o.v===c.op?' selected':''}>${o.t}</option>`).join('')}</select>${showInterval?`<span class="cb-date-label">从</span><input type="date" class="cb-date-from" value="${esc(c.from||'')}"><span class="cb-date-label">到</span><input type="date" class="cb-date-to" value="${esc(c.to||'')}">`:`<input type="text" class="cb-value" value="${esc(c.value||'')}" placeholder="${c.op==='exists'?'（无需填写）':'值'}"${c.op==='exists'?' disabled':''}>`}<button class="cb-remove secondary" title="删除">×</button>`;
      rows.appendChild(row);
    }}
    renderChips();updateChip();refreshFilterPurpose();
  }
  function renderChips(){
    get('cb-active-chips').innerHTML=cb.conditions.map(c=>{
      const label=c.op==='interval'?`${fieldLabel(c.field)} ${c.from||'?'} → ${c.to||'?'}`:`${fieldLabel(c.field)} ${opLabel(c.op)} ${c.value||''}`;
      return `<span class="cb-chip"><span>${esc(label)}</span><button data-id="${c.id}" title="删除">×</button></span>`;
    }).join('');
  }
  function updateChip(){
    const parts=[];
    for(const [id,label] of [['query-platforms','平台'],['query-businesses','业务'],['query-audiences','适用对象']]){
      const value=get(id)?.value.trim();if(value)parts.push(label+'：'+value);
    }
    for(const id of ['type-scope','predicate-scope']){const select=get(id);if(select.value)parts.push(select.selectedOptions[0]?.textContent||select.value);}
    try{
      const filters=get('filters').value.trim(),count=filters?window.ScopeFields.countConditions(JSON.parse(filters)):0;
      if(cb.custom&&filters)parts.push('高级 JSON'+(count?' · '+count+' 个条件':''));
      else if(count)parts.push(count+' 个其他条件');
    }catch{parts.push('JSON 格式待修正');}
    get('scope-chip').textContent=parts.join(' · ')||'未设置限制';
  }
  for(const id of ['query-platforms','query-businesses','query-audiences','type-scope','predicate-scope']){
    get(id)?.addEventListener('input',updateChip);get(id)?.addEventListener('change',updateChip);
  }
  function syncFilters(){
    if(cb.custom)return;
    const conds=[];
    for(const c of cb.conditions){
      if(c.op==='interval'){if(c.from)conds.push({field:c.field,op:'gte',value:c.from});if(c.to)conds.push({field:c.field,op:'lte',value:c.to});}
      else{const val=c.op==='exists'?true:c.value;if(c.field&&(c.op==='exists'||val))conds.push({field:c.field,op:c.op,value:val});}
    }
    const json=conds.length?JSON.stringify({and:conds},null,2):'';
    get('filters').value=json;
    const raw=get('cb-raw-json');if(raw&&document.activeElement!==raw)raw.value=json;
    updateChip();
  }
  get('cb-rows').addEventListener('change',e=>{const row=e.target.closest('.cb-row');if(!row)return;const id=Number(row.dataset.id),c=cb.conditions.find(x=>x.id===id);if(!c)return;
    if(e.target.classList.contains('cb-field')){c.field=e.target.value;const isTime=TIME_FIELDS.has(c.field);if(isTime&&!['eq','ne','gte','lte','interval'].includes(c.op))c.op='eq';else if(!isTime&&c.op==='interval')c.op='eq';renderCB();}
    else if(e.target.classList.contains('cb-op')){c.op=e.target.value;renderCB();}
    else if(e.target.classList.contains('cb-date-from')){c.from=e.target.value;syncFilters();renderChips();}
    else if(e.target.classList.contains('cb-date-to')){c.to=e.target.value;syncFilters();renderChips();}
    syncFilters();});
  get('cb-rows').addEventListener('input',e=>{const row=e.target.closest('.cb-row');if(!row)return;const id=Number(row.dataset.id),c=cb.conditions.find(x=>x.id===id);if(!c)return;
    if(e.target.classList.contains('cb-value')){c.value=e.target.value;syncFilters();renderChips();}});
  get('cb-rows').addEventListener('click',e=>{const btn=e.target.closest('.cb-remove');if(btn)removeCondition(Number(btn.dataset.id));});
  get('cb-active-chips').addEventListener('click',e=>{const btn=e.target.closest('button');if(btn)removeCondition(Number(btn.dataset.id));});
  get('cb-add').onclick=()=>addCondition();

  // Metadata discovery: quick-pick a project field + existing value into a new condition.
  function selectFacet(){
    const field=cb.facets.find(f=>f.field===get('metadata-field-choice').value);
    get('metadata-value-choice').innerHTML='<option value="">选择已有值，或在下方手填</option>'+(field?.values||[]).map((v,i)=>`<option value="${i}">${esc(JSON.stringify(v.value))}</option>`).join('');
    if(field){get('metadata-facet-summary').textContent=`${field.records} 条记录有此字段 · ${field.types.join(' / ')}${field.truncated?' · 值较多，仅列出部分':''}`;}
  }
  get('metadata-field-choice').onchange=()=>{selectFacet();const val=get('metadata-field-choice').value;if(val)addCondition(val,'eq','');};
  get('metadata-value-choice').onchange=()=>{const field=cb.facets.find(f=>f.field===get('metadata-field-choice').value),i=get('metadata-value-choice').value;if(field&&i!==''){const item=field.values[Number(i)];const last=cb.conditions[cb.conditions.length-1];if(last){last.value=typeof item.value==='string'?item.value:JSON.stringify(item.value);last.op=item.op||'eq';renderCB();syncFilters();}}};
  function renderFacetChoices(records){
    const previous=get('metadata-field-choice').value,fields=visibleFacets();
    get('metadata-field-choice').innerHTML='<option value="">选择其他 Metadata 字段</option>'+fields.map(f=>`<option value="${esc(f.field)}">${esc(fieldLabel(f.field))} · ${f.records} 条</option>`).join('');
    if(fields.some(f=>f.field===previous)){get('metadata-field-choice').value=previous;selectFacet();}
    else{get('metadata-value-choice').innerHTML='<option value="">先选择字段</option>';get('metadata-facet-summary').textContent=`${fields.length} 个可选字段${records!=null?' · '+records+' 条记录':''}${get('cb-show-system')?.checked?' · 包含系统字段':' · 系统字段已收起'}`;}
  }
  get('cb-show-system')?.addEventListener('change',()=>{renderFacetChoices();renderCB();});
  async function discoverMetadata(){
    const p=current,page=window.WorkspaceContext?.page,stamp=JSON.stringify(scope());
    // A closed page-owned filter rail must not scan the project at startup.
    if(!p||!details.open||!window.WorkspaceContext?.isContentPage(window.WorkspaceContext.page))return;
    if(!usesPostgresFields()){cb.facets=[];renderFacetChoices();renderCB();return;}
    const result=await api(endpoint('/metadata/facets'),scope());
    if(p!==current||page!==window.WorkspaceContext?.page||!details.open||!usesPostgresFields()||stamp!==JSON.stringify(scope()))return;
    cb.facets=result.fields;
    renderFacetChoices(result.records);
    renderCB();
  }
  details.addEventListener('toggle',()=>{if(details.open)discoverMetadata().catch(error=>status(error.message,true));});
  for(const id of ['scope-purpose','scope-known-at','search-mode','qa-mode'])get(id)?.addEventListener('change',()=>{
    renderFacetChoices();renderCB();if(details.open)discoverMetadata().catch(error=>status(error.message,true));
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape')details.open=false;});
  document.addEventListener('pointerdown',e=>{if(details.open&&!details.contains(e.target)&&!get('scope-toggle')?.contains(e.target))details.open=false;});
  // Advanced JSON edits flow into the canonical #filters textarea.
  get('cb-raw-json').addEventListener('change',()=>{const v=get('cb-raw-json').value.trim();try{get('filters').value=v?JSON.stringify(JSON.parse(v),null,2):'';}catch{get('filters').value=v;}cb.custom=Boolean(v);cb.conditions=[];renderCB();});

  // Search and graph live in one workspace; graph changes only after an explicit selection.
  const graphTab=get('tab-search');
  const graphPanel=get('graph-stage');
  let graphSettingsDrawer=null;
  get('graph-settings').onclick=()=>{
    if(!graphSettingsDrawer){
      const content=get('graph-settings-content');content.hidden=false;
      graphSettingsDrawer=WorkspaceDrawers.create({id:'graph-settings-drawer',title:'图谱设置',description:'选择起点，控制局部探索的方向、规模和显示类型。',content});
    }
    graphSettingsDrawer.open({trigger:get('graph-settings')});
    void options().catch(error=>status(error.message,true));
  };
  get('graph-labels').onchange=()=>{const show=get('graph-labels').checked;if(window.GraphWorkbench?.setLabels)GraphWorkbench.setLabels(show);else wb.chart?.setOption({series:[{label:{show},emphasis:{label:{show}},select:{label:{show}}}]});};
  get('graph-edge-labels').onchange=()=>{
    if(!ui.graph||!wb.chart)return;
    const show=get('graph-edge-labels').checked;
    wb.chart.setOption({series:[{edgeLabel:{show:show&&ui.graph.nodes.length<=20},emphasis:{edgeLabel:{show}},select:{edgeLabel:{show}}}]});
    if(!show){
      // ECharts keeps cached emphasis/select text states when all labels turn
      // off. Drop that text so hovering cannot revive it; enabling recreates it.
      const edges=wb.chart.getModel().getSeriesByIndex(0).getEdgeData();
      for(let i=0;i<edges.count();i++)edges.getItemGraphicEl(i)?.removeTextContent();
    }
  };

  // ── Compact system-time change points ──
  const uiTimeline={events:[],project:null,loading:false,truncated:false};
  function createTimeline(){
    let el=get('graph-timeline');if(el)return el;
    el=make('div','','graph-timeline');el.id='graph-timeline';
    el.innerHTML='<span class="tl-title">知识时间</span><button class="tl-prev secondary" title="上一个已知变更节点">‹</button><select class="tl-points" aria-label="选择知识系统时间"><option value="">当前状态</option></select><button class="tl-next secondary" title="下一个已知变更节点">›</button><button class="tl-now secondary">回到当前</button><button class="tl-refresh secondary" title="刷新时间节点">↻</button><small class="tl-note">仅在切换节点时刷新</small>';
    el.querySelector('.tl-points').onchange=()=>applyTimelinePoint(el.querySelector('.tl-points').value);
    el.querySelector('.tl-prev').onclick=()=>moveTimeline(-1);
    el.querySelector('.tl-next').onclick=()=>moveTimeline(1);
    el.querySelector('.tl-now').onclick=()=>applyTimelinePoint('');
    el.querySelector('.tl-refresh').onclick=()=>loadTimelineEvents(true).catch(error=>status(error.message,true));
    return el;
  }
  function timelineLabel(event,index){
    const time=new Date(event.known_at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    return `节点 ${index+1} · ${time} · 变更 ${event.changed} 条`;
  }
  function renderTimelinePoints(){
    const el=createTimeline(),select=el.querySelector('.tl-points'),known=get('known-at').value;
    select.innerHTML=uiTimeline.events.map((event,index)=>`<option value="${esc(event.known_at)}">${esc(timelineLabel(event,index))}</option>`).join('')+'<option value="">当前状态</option>';
    select.value=uiTimeline.events.some(event=>event.known_at===known)?known:'';
    const values=[...uiTimeline.events.map(event=>event.known_at),''],index=values.indexOf(select.value);
    el.querySelector('.tl-prev').disabled=index<=0;el.querySelector('.tl-next').disabled=index<0||index>=values.length-1;el.querySelector('.tl-now').disabled=!known;
    el.querySelector('.tl-note').textContent=uiTimeline.truncated?'显示最近 100 个变更节点':'仅在切换节点时刷新';
  }
  async function loadTimelineEvents(force=false){
    if(!current)return;if(!force&&uiTimeline.project===current&&uiTimeline.events.length){renderTimelinePoints();return;}
    if(uiTimeline.loading)return;uiTimeline.loading=true;
    try{const p=current,result=await api(endpoint('/timeline?limit=100'),undefined,'GET');if(p!==current)return;uiTimeline.project=p;uiTimeline.events=result.events||[];uiTimeline.truncated=result.truncated;renderTimelinePoints();}
    finally{uiTimeline.loading=false;}
  }
  function applyTimelinePoint(value){
    if(get('known-at').value===value){renderTimelinePoints();return;}
    get('known-at').value=value;if(window.WorkspaceContext){WorkspaceContext.store.edit(current,'search',{...WorkspaceContext.read(current,'search'),known_at:value||null});WorkspaceContext.store.apply(current,'search');WorkspaceContext.restore();}renderTimelinePoints();
    window.clearGraphWorkspace({preserveSearchResults:true});
  }
  function moveTimeline(direction){
    const values=[...uiTimeline.events.map(event=>event.known_at),''],currentValue=get('graph-timeline').querySelector('.tl-points').value,index=values.indexOf(currentValue),next=Math.max(0,Math.min(values.length-1,(index<0?values.length-1:index)+direction));applyTimelinePoint(values[next]);
  }
  function resetTimeline(){
    uiTimeline.events=[];uiTimeline.project=null;uiTimeline.truncated=false;get('known-at').value='';const el=get('graph-timeline');if(el)el.querySelector('.tl-points').innerHTML='<option value="">当前状态</option>';
  }

  const ontologyScope=()=>({
    ontology_scope:wb.versionContext?.ontologyScope||'all',
    ontology_ids:wb.versionContext?.ontologyIds||null,
  });
  wb.ontologyScope=ontologyScope;
  const ontologyScopeLabel=()=>{
    const context=wb.versionContext||{};
    if(context.ontologyScope==='ids')return context.ontologyVersion==null
      ?'指定本体':'本体 v'+context.ontologyVersion;
    if(context.ontologyScope==='unknown')return '未知本体';
    return '全部本体';
  };
  function renderOntologyScopeBanners(){
    for(const id of ['graph-version-scope','mindmap-version-scope']){
      const host=get(id);if(!host)continue;
      const label=make('span','知识范围：'+ontologyScopeLabel());
      label.title='按所选时间范围内的知识修订筛选本体归属；本体筛选不等于整库历史快照，跨归属端点可能被过滤。';
      const reset=make('button','恢复全部本体','secondary');reset.type='button';
      reset.dataset.resetOntologyScope='';
      reset.hidden=(wb.versionContext?.ontologyScope||'all')==='all';
      reset.onclick=async()=>{
        wb.clearVersionContext();
        if(!current)return;
        if(id==='graph-version-scope')await window.drawGraph(null,get('graph-hops').value);
        else if(get('mindmap-root').value)await get('draw-mindmap').onclick();
      };
      host.replaceChildren(label,reset);
    }
  }

  // A searchable native select, not an internal record ID input.
  const select=get('mindmap-root');
  select.parentElement.before(make('label','<span>搜索实体</span><input id="mindmap-filter" placeholder="搜索实体名称、别名或类型">','mindmap-filter-control'));
  let mindmapView=null;
  get('mindmap-zoom-in').onclick=()=>mindmapView?.zoom(1.2);
  get('mindmap-zoom-out').onclick=()=>mindmapView?.zoom(1/1.2);
  get('mindmap-reset-view').onclick=()=>mindmapView?.reset();
  const mindmap=EntityMindmap.createController({getProject:()=>current,
    getScope:()=>({...window.WorkspaceContext?.read(current,'search')||scope(),...ontologyScope()}),
    ticket:()=>window.WorkspaceContext?.ticket(current,'search'),accepts:ticket=>!ticket||window.WorkspaceContext.accepts(ticket,current,'search'),api,
    prepare:row=>{ui.pinned.set(row.id,row);ui.mindmapRootId=row.id;choices();select.value=row.id;window.KnowledgeTabs.open('mindmap');},
    render:result=>{
      if(!wb.tree)wb.tree=echarts.init(get('mindmap-canvas'));
      if(!mindmapView)mindmapView=EntityMindmapView.create({chart:wb.tree,host:get('mindmap-canvas'),
        summary:get('mindmap-summary'),empty:get('mindmap-empty'),zoomLabel:get('mindmap-zoom-level'),
        buttons:{in:get('mindmap-zoom-in'),out:get('mindmap-zoom-out'),reset:get('mindmap-reset-view')},
        labelOf,colorFor:type=>GraphPalette.colorFor(type)});
      mindmapView.render(result);
      renderDiscoveryHint().catch(()=>{});
    }});
  window.openEntityMindmap=row=>mindmap.open(row,get('mindmap-depth').value).catch(error=>status(error.message,true));
  act('draw-mindmap',()=>mindmap.draw(select.value,get('mindmap-depth').value));
  select.onchange=()=>{ui.mindmapRootId=select.value;mindmap.invalidate();};
  function choices(){
    for(const [selectId,filterId] of [['mindmap-root','mindmap-filter'],['graph-entity-choice','graph-entity-filter']]){
      const element=get(selectId),previous=element.value;
      const candidates=selectId==='graph-entity-choice'?ui.options:ui.mindmapPicker.options;
      // The scoped server query also matches aliases; never hide its matches
      // by applying a second, narrower client-side text filter.
      let rows=[...candidates];
      const committedId=selectId==='graph-entity-choice'?selectedEntityId:ui.mindmapRootId||previous;
      if(committedId&&!rows.some(r=>r.id===committedId)){
        const committed=ui.pinned.get(committedId)||candidates.find(r=>r.id===committedId);
        if(committed)rows=[...rows,committed];
      }
      // 同名同类型重复实体显示来源文档，方便区分（比 ID 更友好）
      const counts=new Map();
      rows.forEach(r=>{const key=r.text+'|'+labelOf(r.type);counts.set(key,(counts.get(key)||0)+1);});
      element.innerHTML='<option value="">'+(selectId==='graph-entity-choice'?'选择实体':'选择根实体')+'</option>'+rows.map(r=>{const key=r.text+'|'+labelOf(r.type);const dup=(counts.get(key)||0)>1;const src=r.source?(' · '+String(r.source).replace(/\.(md|txt|docx?)$/i,'').slice(0,16)):'';return `<option value="${esc(r.id)}" title="${esc(term(r.type))}">${esc(r.text)} · ${esc(labelOf(r.type))}${dup?esc(src):''}</option>`;}).join('');
      if(rows.some(r=>r.id===committedId))element.value=committedId;
      else if(rows.some(r=>r.id===previous))element.value=previous;
      if(selectId==='graph-entity-choice'&&pendingEntityId&&element.value!==pendingEntityId)invalidatePendingDetail();
    }
  }
  for(const target of ['graph','mindmap']){
    const prefix=target==='graph'?'graph':'mindmap',choice=target==='graph'?'graph-entity-choice':'mindmap-root';
    const pagination=make('div',`<div><button type="button" id="${prefix}-choice-first" class="secondary">首页</button><button type="button" id="${prefix}-choice-prev" class="secondary" disabled>上一页</button><button type="button" id="${prefix}-choice-next" class="secondary" disabled>下一页</button></div><span id="${prefix}-choice-status" class="subtle" role="status" aria-live="polite"></span>`,'entity-picker-pagination');
    if(target==='mindmap')get('mindmap-picker-footer').append(pagination);else get(choice).after(pagination);
    const filter=target==='graph'?'graph-entity-filter':'mindmap-filter',state=()=>target==='graph'?ui.picker:ui.mindmapPicker;
    get(prefix+'-choice-first').onclick=()=>loadEntityChoices(get(filter).value,null,target).catch(error=>status(error.message,true));
    get(prefix+'-choice-prev').onclick=()=>{const picker=state();if(picker.page>1)loadEntityChoices(picker.query,picker.pages[picker.page-2],target).catch(error=>status(error.message,true));};
    get(prefix+'-choice-next').onclick=()=>{const picker=state();if(picker.cursor)loadEntityChoices(picker.query,picker.cursor,target).catch(error=>status(error.message,true));};
  }
  get('graph-choice-status').after(make('button','按 ID 打开','secondary'));get('graph-choice-status').nextElementSibling.id='graph-choice-id';
  get('graph-entity-filter').placeholder='输入实体名称或 ID 搜索当前范围…';
  const choiceTimers={};
  const scheduleChoices=(query,target='graph')=>{
    const picker=target==='graph'?ui.picker:ui.mindmapPicker;picker.request++;
    for(const action of ['first','prev','next'])get(target+'-choice-'+action).disabled=true;
    get(target+'-choice-status').textContent='正在搜索候选实体…';
    clearTimeout(choiceTimers[target]);choiceTimers[target]=setTimeout(()=>loadEntityChoices(query,null,target).catch(error=>status(error.message,true)),200);
  };
  get('mindmap-filter').oninput=()=>scheduleChoices(get('mindmap-filter').value,'mindmap');
  get('graph-choice-id').onclick=()=>{const id=get('graph-entity-filter').value.trim();if(id)window.openKnowledgeRecord({id,kind:'entity'}).catch(error=>status(error.message,true));};
  let selectedEntityId='',pendingEntityId='',detailSelectionEpoch=0,recordOpenEpoch=0;
  const detailScope=()=>({...ontologyScope(),valid_at:scope().valid_at||null,known_at:scope().known_at||null});
  function restoreCommittedSelection(){
    get('graph-entity-choice').value=selectedEntityId;
    get('graph-node').value=selectedEntityId;
  }
  function settlePendingDetail(token){
    if(token!==detailSelectionEpoch||!pendingEntityId)return false;
    restoreCommittedSelection();
    pendingEntityId='';
    graphExpandState.pendingDetail=false;
    syncGraphExpandDisabled();
    return true;
  }
  function invalidatePendingDetail(){
    recordOpenEpoch++;
    settlePendingDetail(detailSelectionEpoch);
    detailSelectionEpoch++;
  }
  function beginPendingDetail(id){
    const token=++detailSelectionEpoch;
    pendingEntityId=id;
    graphExpandState.pendingDetail=true;
    syncGraphExpandDisabled();
    return token;
  }
  get('graph-entity-filter').oninput=()=>scheduleChoices(get('graph-entity-filter').value);
  get('graph-entity-filter').onkeydown=e=>{if(e.key==='Enter'&&get('graph-entity-choice').value)get('graph-entity-choice').onchange();};
  get('graph-entity-choice').onchange=async()=>{
    const choice=get('graph-entity-choice'),id=choice.value;
    if(!id){
      // 用户主动选回空白项 = 清空选择，回到局部探索起点。
      // 以前这里无条件 restoreCommittedSelection()，值会被弹回上一个实体，
      // 看起来就是"点其他之后切不回空白了"（用户原话）。
      // 仍然保留 restoreCommittedSelection()：它现在只服务于"取消一次没加载完的详情"。
      selectedEntityId='';
      get('graph-node').value='';
      invalidatePendingDetail();
      // 详情面板也跟着清空：留着上一条实体的详情会让人以为"还选着它"。
      get('graph-detail').replaceChildren();
      window.clearGraphWorkspace({preserveSearchResults:true});
      status('已清空实体选择：请选择实体后展开局部邻域。');
      await window.drawGraph(null,get('graph-hops').value);
      return;
    }
    const local=wb.nodes.get(id);
    if(local){inspect(local);status('已选择：'+local.text+' · 点击“展开邻域”查看邻域');return;}
    const token=beginPendingDetail(id),project=current;
    try{
      const row=await window.openKnowledgeRecord({id,kind:'entity'});
      if(!row){settlePendingDetail(token);return;}
      status('已选择：'+row.text+' · 点击“展开邻域”查看邻域');
    }catch(error){if(settlePendingDetail(token))status(error.message,true);}
  };
  async function selectEntityDetail(id){
    const local=wb.nodes.get(id);
    if(local){
      if(get('tab-search').classList.contains('hidden')){window.suppressNextAutoGraph?.();window.KnowledgeTabs.open('search');}
      inspect(local);
      return local;
    }
    const choice=get('graph-entity-choice');
    if(![...choice.options].some(option=>option.value===id)){
      get('graph-entity-filter').value='';choices();
    }
    choice.value=id;
    if(choice.value!==id)return window.openKnowledgeRecord({id,kind:'entity'});
    await choice.onchange();
  }
  window.selectEntityDetail=selectEntityDetail;
  // Full graph, source assertions and exact lookup share the same live inspector
  // and its revision/permission-checked record editor, without loading an option prefix.
  window.openKnowledgeRecord=async({id,kind,projectId=current,scope:fixedScope}={})=>{
    if(!id)throw Error('缺少知识记录 ID');
    if(projectId!==current)throw Error('项目已切换，请在当前项目重新打开记录');
    const project=current,applied=window.WorkspaceContext?.read(current,'search')||scope(),openEpoch=++recordOpenEpoch;
    const temporal=fixedScope||{...applied,...ontologyScope()},ticket=window.WorkspaceContext?.ticket(project,'search');
    const base='/api/projects/'+encodeURIComponent(project);
    const found=await api(base+'/records/page',{...temporal,record_ids:[id],limit:1,include_total:false});
    const isCurrent=()=>openEpoch===recordOpenEpoch&&project===current&&(!ticket||window.WorkspaceContext.accepts(ticket,current,'search'));
    if(!isCurrent())return null;
    let row=found.records?.find(record=>record.id===id);
    if(!row)throw Error('当前已应用范围内没有该知识记录');
    if(kind&&row.kind!==kind)throw Error('记录类型已变化，请刷新图谱');
    if(row.kind==='entity'){
      const detail=await api(base+'/graph-neighborhood',{...temporal,seeds:[id],hops:0,direction:'both',max_nodes:1,max_edges:1});
      if(!isCurrent())return null;
      row=detail.records.find(record=>record.id===id&&record.kind==='entity');
      if(!row)throw Error('实体资格已变化，请在当前范围重新选择');
    }
    if(!get('tab-search').classList.contains('hidden'))inspect(row);
    else{window.suppressNextAutoGraph?.();window.KnowledgeTabs.open('search');inspect(row);}
    return row;
  };

  async function loadEntityChoices(query='',cursor=null,target='graph'){
    const project=current;if(!project)return null;
    const picker=target==='graph'?ui.picker:(ui.mindmapPicker||= {request:0,options:[]}),prefix=target==='graph'?'graph':'mindmap',selectId=target==='graph'?'graph-entity-choice':'mindmap-root';
    const request=++picker.request,applied={...window.WorkspaceContext?.read(project,'search')||scope(),...ontologyScope()},ticket=window.WorkspaceContext?.ticket(project,'search'),stamp=JSON.stringify(applied);
    const isCurrent=()=>request===picker.request&&project===current&&(!ticket||window.WorkspaceContext.accepts(ticket,current,'search'))&&stamp===JSON.stringify({...window.WorkspaceContext?.read(project,'search')||scope(),...ontologyScope()});
    if(picker.stamp!==stamp||picker.query!==query.trim()){cursor=null;picker.pages=[null];}
    const pages=picker.pages||[null];let index=pages.indexOf(cursor);if(index<0){index=pages.length;pages.push(cursor);}
    for(const id of ['first','prev','next']){const button=get(prefix+'-choice-'+id);if(button)button.disabled=true;}
    const note=get(prefix+'-choice-status');if(note)note.textContent='正在加载候选实体…';
    try{
      const result=await api('/api/projects/'+encodeURIComponent(project)+'/records/page',{...applied,kinds:['entity'],query:query.trim(),cursor,limit:50,include_total:true});
      if(!isCurrent())return null;
      const candidates=target==='graph'?ui.options:picker.options,selected=get(selectId).value,row=candidates?.find(item=>item.id===selected);if(row)ui.pinned.set(selected,row);
      Object.assign(picker,{cursor:result.next_cursor,query:query.trim(),total:result.total,page:index+1,pages,stamp});
      if(target==='graph')ui.options=result.records;else picker.options=result.records;
      choices();if(note)note.textContent=`第 ${picker.page} / ${Math.max(1,Math.ceil((result.total||0)/50))} 页 · 本页 ${result.records.length} 条 / 共 ${(result.total||0).toLocaleString()} 实体`;
      return result;
    }catch(error){if(!isCurrent())return null;if(note)note.textContent='候选加载失败，请重试';throw error;}
    finally{if(isCurrent()){const first=get(prefix+'-choice-first'),prev=get(prefix+'-choice-prev'),next=get(prefix+'-choice-next');if(first)first.disabled=picker.page<=1;if(prev)prev.disabled=picker.page<=1;if(next)next.disabled=!picker.cursor;}}
  }

  async function options(){
    const p=current,applied=window.WorkspaceContext?.read(p,'search')||scope(),ticket=window.WorkspaceContext?.ticket(p,'search');if(!p)return;
    const result=graphSettingsDrawer?.dialog.open&&!get('tab-search').classList.contains('hidden')
      ?await loadEntityChoices(get('graph-entity-filter').value):{records:ui.options};
    if(!result||current!==p||(ticket&&!window.WorkspaceContext.accepts(ticket,p,'search')))return;
    const summary=ui.ontology?.summary||{};
    for(const [id,values,label] of [['type-scope',[...result.records.map(r=>r.type),...(summary.classes||[]).map(row=>row.id)],'全部实体类型'],['predicate-scope',(summary.relations||[]).map(row=>row.id),'全部关系类型']]){
      const items=[...new Set([...values,get(id).value].filter(Boolean))];
      const previous=get(id).value;get(id).innerHTML=`<option value="">${label}</option>`+items.map(t=>`<option value="${esc(t)}" title="${esc(term(t))}">${esc(labelOf(t))}</option>`).join('');
      if(items.includes(previous))get(id).value=previous;
    }
    if(ui.graph)updateGraphTypeChoices(ui.graph);
  }
  function inspect(row){
    invalidatePendingDetail();
    if(GraphTypeFilter.isAttributeNode(row)){
      const entity=wb.nodes.get(row.subject_id);
      if(entity)return inspect(entity);
      return;
    }
    const isEntity=row.kind==='entity';
    if(isEntity){ui.pinned.set(row.id,row);selectedEntityId=row.id;ui.mindmapRootId=row.id;choices();get('graph-node').value=row.id;get('mindmap-root').value=row.id;get('graph-entity-choice').value=row.id;}
    window.renderEvidenceInspector(row,[...ui.options,...ui.pinned.values()]);
    GraphWorkbench.selection(row);
  }
  // 图谱悬停提示：把"这个实体是什么、它的类在本体里挂在谁下面"一次说清。
  // 用户反馈："检索 没看到有父类的信息" + "知识图谱也没父类相关的信息" ——
  // 层级在**类**之间，实体节点本身没有父类，所以这里显示的是"它所属的类"的父类链
  // （数据来自 /subgraph 的 class_parents / class_ancestors，唯一出处是服务端 ontology_family）。
  function graphTooltip(point){
    if(point.dataType==='edge'){
      const raw=point.data.rawType;
      return esc(raw&&raw!==point.data.name?point.data.name+'（'+raw+'）':point.data.name);
    }
    const node=wb.nodes.get(point.data.id)||{};
    const label=name=>name&&(name.label||name.id)||'';
    const parents=(node.class_parents||[]).map(label).filter(Boolean);
    const rows=[`<b>${esc(node.text||point.data.name)}</b>`,
      `类型：${esc(node.class_label||point.data.categoryName||'—')}`];
    rows.push(node.class_ontology_id===null?'父类：绑定本体或类型未知，无法解释层级':parents.length
      ? `父类：${esc(parents.join(' → '))}`
      : '父类：顶层类（本体里没有父类）');
    const ancestors=(node.class_ancestors||[]).map(label).filter(Boolean);
    if(ancestors.length>parents.length){
      rows.push(`完整继承链：${esc([node.class_label,...ancestors].filter(Boolean).join(' → '))}`);
    }
    const links=(ui.graph?.edges||[]).filter(edge=>edge.subject_id===node.id||edge.object_id===node.id).length;
    return '<div class="fg-hover-meta"><span class="fg-hover-kind">实体</span><span>'+links+' 条关系</span></div>'+rows.join('<br>')+'<div class="fg-hover-id">'+esc(node.id||point.data.id)+'</div>';
  }
  function updateGraphTypeChoices(graph){
    for(const [id,rows,kind] of [['type-scope',graph.nodes,'entity'],['predicate-scope',graph.edges,'relation']]){
      const select=get(id),known=new Set([...select.options].map(option=>option.value));
      for(const row of rows){if(row.kind!==kind||!row.type||known.has(row.type))continue;const option=document.createElement('option');option.value=row.type;option.textContent=row.type_label||labelOf(row.type);select.appendChild(option);known.add(row.type);}
    }
  }
  function renderGraph(result, selectedType='', base=result, options={}){
    updateGraphTypeChoices(base);
    const displayType=type=>result.type_labels?.[type]||labelOf(type);
    // Color identity uses canonical type IDs; translated labels remain presentation text.
    result=GraphTypeFilter.selectType(base,selectedType);
    let bar=get('graph-type-buttons');
    if(!bar){bar=make('section','','graph-type-buttons');bar.id='graph-type-buttons';get('graph-canvas').after(bar);}
    const baseEntities=GraphTypeFilter.entityNodes(base);
    const groups=[...new Set(baseEntities.map(n=>n.type))];
    const nodeColor=n=>window.GraphAppearance?GraphAppearance.colorFor(n):(window.GraphPalette?GraphPalette.colorFor(n.type):'#5470c6');
    const typeLabels=new Map(baseEntities.map(n=>[n.type,n.type_label||displayType(n.type)]));
    const typeLabel=type=>typeLabels.get(type)||displayType(type);
    const attrColor=window.GraphPalette?GraphPalette.attribute:'#8c7b68';
    const dot=type=>type?`<i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${nodeColor({type})};margin-right:5px;vertical-align:middle"></i>`:'';
    bar.innerHTML='<small>按实体类型查看 · 只显示所选类型，不展开邻居</small><div>'+['',...groups].map((type,i)=>`<button class="secondary" data-type-index="${i}" aria-pressed="${type===selectedType}" title="${esc(type?term(type):'恢复当前检索范围全部类型')}">${dot(type)}${esc(type?typeLabel(type):'全部类型')} <b>${type?baseEntities.filter(n=>n.type===type).length:baseEntities.length}</b></button>`).join('')+'</div>';
    bar.querySelectorAll('button').forEach(button=>button.onclick=()=>renderGraph(base,['',...groups][Number(button.dataset.typeIndex)],base));
    const positioned=GraphWorkbench.positions(result,current);
    const viewport=GraphWorkbench.viewport(current,graphScope(),positioned);
    ui.graph=result;wb.nodes=new Map(result.nodes.map(n=>[n.id,n]));
    const categoryKey=n=>window.GraphAppearance?GraphAppearance.categoryKey(n):(GraphTypeFilter.isAttributeNode(n)?'attribute:':'entity:'+String(n.type||''));
    const chart=initChart(), types=[...new Set(result.nodes.map(categoryKey))], matched=new Set(result.matched_ids||[]);
    const categoryLabel=key=>key==='attribute:'?'属性值':typeLabel(key.slice(7));
    chart.resize();
    bindPanAffordances();   // 双击空白＝适应画布（幂等）
    chart.off('click');chart.on('click',p=>{
      if(!GraphWorkbench.canClick())return;
      const row=p.dataType==='edge'?result.edges.find(e=>e.id===p.data.id):wb.nodes.get(p.data.id);
      if(!row)return;
      if(GraphTypeFilter.isAttributeEdge(row)||GraphTypeFilter.isAttributeNode(row)){
        const entity=wb.nodes.get(row.subject_id);if(entity)inspect(entity);return;
      }
      inspect(row);
    });
    chart.dispatchAction({type:'hideTip'});
    const appearance=window.GraphAppearance?.tokens||{text:'#888995',edge:'#a8c9e2',outline:'#ffffff',selected:'#1689e9'};
    const showLabels=get('graph-labels').checked;
    const showEdgeLabels=get('graph-edge-labels').checked;
    // The force layout plus the fit pass below are the client half of "the graph feels
    // slow"; the server half is the `subgraph` line logged by drawGraph.
    const layoutDone=kgTime('graph-layout');
    const catIndex=label=>Math.max(0,types.indexOf(label));
    const nodeSize=result.nodes.length>300?10:result.nodes.length>200?14:16;
    const nodeSizes=window.GraphAppearance?.sizesFor?.(result.nodes,result.edges);
    const preferredLabels=[get('graph-node').value,...matched];
    const labelIds=window.GraphAppearance?GraphAppearance.labelsFor(positioned,preferredLabels,result.nodes.length<=20?50:20):new Set([...new Set([...preferredLabels,...positioned.map(n=>n.id)])].slice(0,50));
    // ECharts 5's series-wide label manager detaches graph edge text and applies
    // its inverse graph scale a second time. GraphWorkbench culls node names; let
    // native graph edge labels retain their own screen-space transform.
chart.setOption({animation:false,tooltip:{formatter:graphTooltip,backgroundColor:'#ffffff',borderColor:'#e1e2e8',textStyle:{color:'#27272a'},padding:16,extraCssText:'border-radius:10px;box-shadow:0 8px 24px #10182824;max-width:310px'},legend:[{show:false,type:'scroll',data:types,formatter:categoryLabel,bottom:0,textStyle:{fontSize:12,color:'#627b82'}}],series:[{type:'graph',layout:'none',draggable:true,roam:'scale',scaleLimit:{min:.05,max:4},...GraphWorkbench.bounds(positioned,chart.getWidth(),chart.getHeight()),center:null,zoom:1,...viewport,selectedMode:'single',select:{itemStyle:{borderColor:appearance.selected,borderWidth:3},lineStyle:{color:appearance.selected,width:2,opacity:1},label:{show:showLabels,fontWeight:600},edgeLabel:{show:showEdgeLabels}},labelLayout:null,label:{show:showLabels,position:'right',fontSize:12,color:appearance.text,distance:6,width:128,overflow:'truncate',backgroundColor:null,padding:0,borderRadius:0},edgeSymbol:['none','arrow'],edgeLabel:{show:showEdgeLabels&&result.nodes.length<=20,formatter:'{c}',fontSize:11,color:'#92939f'},emphasis:{focus:'adjacency',lineStyle:{color:appearance.selected,width:1.5,opacity:.7},label:{show:showLabels,color:'#3f3f46'},edgeLabel:{show:showEdgeLabels}},force:{repulsion:320,edgeLength:100,gravity:.1,initLayout:'circular',layoutAnimation:false},categories:types.map(name=>({name,itemStyle:{color:name==='attribute:'?attrColor:nodeColor({type:name.slice(7)})}})),data:positioned.map(n=>{const attribute=GraphTypeFilter.isAttributeNode(n),label=attribute?'属性值':(n.type_label||displayType(n.type));const index=catIndex(categoryKey(n)),size=nodeSizes?.get(n.id)||nodeSize;return {id:n.id,name:n.text,x:n.x,y:n.y,category:index,categoryName:label,symbol:attribute?'roundRect':'circle',symbolSize:attribute?[72,22]:(matched.has(n.id)?Math.max(18,size+6):size),label:{show:labelIds.has(n.id)?undefined:false},itemStyle:{color:attribute?attrColor:nodeColor(n),borderColor:appearance.outline,borderWidth:result.nodes.length>100?0:1.2,...(attribute&&n.status==='contradicting'?{borderColor:'#b42318',borderWidth:2,borderType:'dashed'}:{}),...(matched.has(n.id)?{borderColor:appearance.selected,borderWidth:3}:{})}}}),links:result.edges.map(e=>{const attribute=GraphTypeFilter.isAttributeEdge(e);return {id:e.id,source:e.subject_id,target:e.object_id,name:e.type_label||displayType(e.type),value:e.type_label||displayType(e.type),rawType:term(e.type),lineStyle:attribute?{type:'dashed',opacity:.55,width:1}:undefined};}),lineStyle:{color:appearance.edge,opacity:.35,width:1,curveness:.1}}]},{replaceMerge:['series']});
    const visibleEntities=GraphTypeFilter.entityNodes(result),visibleRelations=GraphTypeFilter.relationEdges(result),attributeCount=result.nodes.length-visibleEntities.length;
    GraphWorkbench.mount(chart,result,current,inspect,graphScope(),options);
    bindLocalExploration(result,selectedType);
    chart.resize();layoutDone({nodes:result.nodes.length,edges:result.edges.length});get('graph-summary').textContent=`${selectedType?'只看 '+labelOf(selectedType)+' · ':''}${visibleEntities.length} 实体 / ${visibleRelations.length} 关系${attributeCount?' · '+attributeCount+' 个属性值':''}${selectedType?' · 隐藏其他类型及跨类型连线':''}${matched.size?' · 蓝色边框为检索命中':''}${result.timing_ms?' · 服务 '+result.timing_ms.total+' ms':''}${graphBudgetSummary(base)}`;
    // Compact, discrete history navigation; no drag/playback re-query loop.
    const tl=createTimeline();get('graph-type-buttons')?.after(tl);
    // The type and timeline rows reduce the remaining flex height after ECharts has
    // already measured it. Resize on the next layout frame so its canvas cannot
    // retain the old height and paint over those rows.
    requestAnimationFrame(()=>{if(ui.graph===result||ui.graph===base)chart.resize();});
    loadTimelineEvents().catch(error=>status(error.message,true));
  }
  function fitGraph(chart){GraphWorkbench.fit(chart);}
  function renderHits(result){
    const sourceChunks=result.source_chunks||[];
    const contextChunks=result.context_chunks||[];
    const hits=result.hits||[];
    const presentation=window.QaPresentation;
    const isExpansion=presentation?.isExpansionRow||((row)=>['ontology_entity_type','ontology_type'].includes(row?.retrieval_channel));
    const expansion=result.ontology_expansion;
    const hasLedger=Boolean(expansion&&typeof expansion==='object'&&!Array.isArray(expansion));
    const hasExpansion=hasLedger||hits.some(isExpansion);
    const chunkHits=hits.filter(r=>r.kind==='chunk');
    // 单条命中条目：isSource 区分「命中知识的来源原文」与「原文全文匹配」。
    // 实体/关系给「在图谱中查看」，原文片段给「查看原文」。
    const entry=(r,kind,isSource=false)=>{
      const score=Number(r.score||r.keyword_score||0).toFixed(2);
      const tag=isSource?'命中知识的来源原文':(labelOf(r.type)||'原文');
      const btn=kind==='entity'||kind==='relation'
        ?`<button data-graph-node="${esc(kind==='entity'?r.id:r.subject_id)}">在图谱中查看</button>`
        :(kind==='chunk'&&r.source_id?`<button data-source-record="${esc(r.source_id)}" class="secondary">查看原文</button>`:'');
      const applicability=window.ApplicabilityUI?.describe?.(r);
      const expansionLabel=isExpansion(r)?' · 类型扩展':'';
      return `<article class="hit-entry${isSource?' hit-source':''}"><b>${esc((r.text||'').slice(0,80))}</b><span>${esc(tag)} · ${score}${applicability?' · '+esc(applicability):''}${expansionLabel}</span><p>${esc((r.text||'').slice(0,180))}</p>${btn?`<div class="row">${btn}</div>`:''}</article>`;
    };
    const entities=hits.filter(r=>r.kind==='entity');
    const relations=hits.filter(r=>r.kind==='relation');
    // 「原文片段」组 = 命中实体/关系的来源原文 + chunk 全文命中，两部分分块展示。
    // 这是检索溯源的落点：搜「商家」命中「商户」时，来源原文里其实没有「商家」这个词，
    // 但它支撑了命中的实体，必须一并展示，否则用户只看到实体、看不到它来自哪段原文。
    const chunkTotal=sourceChunks.length+chunkHits.length+contextChunks.length;
    const chunkSection=`<section><h3 class="hit-kind">原文片段 <small>${chunkTotal} / ${result.channel_quotas?.chunk??5}</small></h3>`+
      (sourceChunks.length?`<div class="hit-source-label">命中知识的来源原文</div>`+sourceChunks.map(r=>entry(r,'chunk',true)).join(''):'')+
      (chunkHits.length?`<div class="hit-source-label">原文全文匹配</div>`+chunkHits.map(r=>entry(r,'chunk')).join(''):'')+
      (contextChunks.length?`<div class="hit-source-label">命中条款的前后文（不参与排名）</div>`+contextChunks.map(r=>entry(r,'chunk',true)).join(''):'')+
      (chunkTotal?'':'<p class="subtle">暂无命中</p>')+`</section>`;
    const platformSummary=(result.platform_groups||[]).length
      ?`<section><h3 class="hit-kind">按适用范围分组</h3>${result.platform_groups.map(group=>
        `<p class="subtle">${esc(platformGroupLabel(group))} · ${group.hits?.length||0} 条命中${group.hits?.length?'':'（当前范围无资料）'}</p>`).join('')}</section>`:'';
    let content=[
      platformSummary,
      `<section><h3 class="hit-kind">匹配实体 <small>${entities.length} / ${result.channel_quotas?.entity??5}</small></h3>${entities.map(r=>entry(r,'entity')).join('')||'<p class="subtle">暂无命中</p>'}</section>`,
      chunkSection,
      `<section><h3 class="hit-kind">关系链路 <small>${relations.length} / ${result.channel_quotas?.relation??5}</small></h3>${relations.map(r=>entry(r,'relation')).join('')||'<p class="subtle">暂无命中</p>'}</section>`,
    ].join('');
    if(hasExpansion){
      const split=presentation?.partitionExpansion?.(hits)||{direct:hits.filter(row=>!isExpansion(row)),supplemental:hits.filter(isExpansion)};
      const key=row=>row.version_id||row.id?`${row.kind||'chunk'}:${row.version_id||row.id}`:null;
      const seen=new Set();
      const unique=rows=>rows.filter(row=>{const identity=key(row);if(identity&&seen.has(identity))return false;if(identity)seen.add(identity);return true;});
      // A record already in direct retrieval remains direct even if a duplicate candidate carries an expansion marker.
      const direct=unique(split.direct),supplemental=unique(split.supplemental);
      const sources=unique(sourceChunks),neighbors=unique(contextChunks);
      const channels=rows=>`<div class="ontology-result-grid">`+[
        ['entity','匹配实体'],['chunk','原文片段'],['relation','关系链路'],
      ].map(([kind,label])=>{const items=rows.filter(row=>row.kind===kind);
        if(!items.length)return '';
        return `<section><h4 class="hit-kind">${label} <small>${items.length} 条</small></h4>${items.map(row=>entry(row,kind)).join('')||'<p class="subtle">暂无命中</p>'}</section>`;
      }).join('')+`</div>`;
      const summary=hasLedger?presentation?.expansionSummary?.({...expansion,used_hits:supplemental.length})||'':
        `补充相关类型内容，实际补入 ${supplemental.length} 条。`;
      const empty=expansion?.matched===false?'未匹配到可扩展的实体或已发布类名，本次没有新增补充结果。':
        Number.isSafeInteger(expansion?.type_hits)&&expansion.type_hits>0?'已找到类型候选，本次未补入新结果。':
        '当前范围内没有召回类型内容，本次没有新增补充结果。';
      const resultSection=(group,label,rows,description,emptyText)=>
        `<section class="ontology-result-section" data-expansion-group="${group}"><h3 class="ontology-result-heading">${label} <small>${rows.length} 条</small></h3>`+
        (description?`<p class="ontology-result-summary">${esc(description)}</p>`:'')+
        (rows.length?channels(rows):`<p class="ontology-result-empty">${esc(emptyText)}</p>`)+`</section>`;
      const contextTotal=sources.length+neighbors.length;
      const provenance=contextTotal?`<section class="ontology-result-section ontology-result-context"><h3 class="ontology-result-heading">来源与上下文 <small>${contextTotal} 条</small></h3>`+
        `<p class="ontology-result-summary">以下内容用于溯源，不参与排名和命中计数。</p><div class="ontology-result-grid">`+
        (sources.length?`<section><h4 class="hit-kind">命中知识的来源原文</h4>${sources.map(row=>entry(row,'chunk',true)).join('')}</section>`:'')+
        (neighbors.length?`<section><h4 class="hit-kind">命中条款的前后文（不参与排名）</h4>${neighbors.map(row=>entry(row,'chunk',true)).join('')}</section>`:'')+`</div></section>`:'';
      content=platformSummary+resultSection('direct','直接命中',direct,'','本次检索没有直接命中。')+
        resultSection('supplemental','本体扩展补充',supplemental,summary,empty)+provenance;
    }
    get('hits').innerHTML=content;
    get('hits').querySelectorAll('[data-graph-node]').forEach(b=>b.onclick=async()=>{try{await selectEntityDetail(b.dataset.graphNode);}catch(error){status(error.message,true);}});
    get('hits').querySelectorAll('[data-source-record]').forEach(b=>b.onclick=()=>historyFor({id:b.dataset.sourceRecord}));
  }
  // 检索和问答共用实体 → 类型路径及补召回计数，避免把同类内容误认为实体自身事实。
  function expansionSummary(result){
    const expansion=window.QaPresentation.expansionSummary(result&&result.ontology_expansion);
    return expansion?' · '+expansion:'';
  }
  async function runSearch(){
    if(!current)return;
    const serial=++ui.request,p=current,started=performance.now(),scopeTicket=window.WorkspaceContext?.ticket(current,'search');
    const body={...scope(),query:get('query').value,retrieval_mode:get('search-mode').value,
      // 默认关闭；命中实体名、别名或类名后，沿所属类型及子类补充相关内容。
      // 它只补召回，不放宽可见范围（权限/时态仍由 scope 决定）；关闭时读路径与旧行为一致。
      ontology_expansion:Boolean(get('ontology-expansion')?.checked),
      k_entities:5,k_chunks:5,k_relations:5};
    try{window.ScopeFields.requireRetrievalScope(body);}
    catch(error){get('search-summary').textContent=error.message;throw error;}
    get('search-summary').textContent=body.retrieval_mode==='keyword'?'读取所选范围…':'正在进行语义检索，首次加载模型可能需要较长时间…';
    get('hits').textContent='';
    const done=kgTime('search');
    let result;
    try{result=await api(endpoint('/search'),body);}
    catch(error){if(serial===ui.request&&p===current)get('search-summary').textContent='检索未完成：'+error.message;throw error;}
    window.WorkspaceContext?.requireRetrievalMode(body.retrieval_mode,result);
    done({mode:result.active_mode,candidates:result.candidate_count,hits:result.hits.length});
    if(serial!==ui.request||p!==current||(scopeTicket&&!window.WorkspaceContext.accepts(scopeTicket,current,'search')))return;
    delete result.nodes;delete result.edges;
    renderHits(result);
    const modeLabel={hybrid:'混合',semantic:'语义',keyword:'关键词'}[result.active_mode]||result.active_mode;
    const explicitScope=body.applicability&&Object.values(body.applicability).some(value=>Array.isArray(value)?value.length:value?.mode==='specific');
    const resolvedScope=explicitScope?window.ApplicabilityUI?.describe?.(result.query_scope||result.scope||result):
      body.filters||body.valid_at||body.known_at?'按已设置的 Metadata 条件检索':'全项目 · 未设置 Metadata 限制';
    get('search-summary').textContent=`${modeLabel} · ${result.candidate_count} 候选 · ${result.hits.length} 命中 · ${(performance.now()-started).toFixed(0)} ms${expansionSummary(result)}${resolvedScope?' · '+resolvedScope:''}`;
    updateChip();
  }
  // 图谱渲染器的唯一实现，显式挂到 window（不再依赖非严格模式下的隐式全局赋值）。
  // workbench.js 的 #graph-expand / #load-graph / 证据条目三个入口都按名字引用这个槽位，
  // 见下方 act('graph-expand') 与 workbench.js 注释。
  const graphScope=()=>({...window.WorkspaceContext?.read(current,'search')||scope(),...ontologyScope(),
    entity_type:get('type-scope').value||null,
    predicate:get('predicate-scope').value||null});
  function graphBudgetSummary(graph){
    const value=graph.neighborhood;if(!value)return '';
    const direction={both:'双向',forward:'沿箭头',backward:'逆箭头'}[value.direction];
    return ` · 局部邻域 ${value.hops} 跳 / ${direction} · 上限 ${value.max_nodes} 实体 / ${value.max_edges} 关系`+
      (value.truncated?' · 已达预算，结果已截断；可提高上限重新请求，或选择节点继续探索。':' · 当前局部结果；可选择节点继续探索。')+
      (graph.attribute_budget?` 属性另计 ${graph.attribute_count} / ${graph.attribute_budget} 条${graph.attributes_truncated?'，属性已截断；选中实体后重新展开可读取该实体属性':''}。`:'')+
      (graph.attribute_candidates_loaded===false?' 属性冲突候选请在实体治理中复核。':'')+
      (graph.exploration_truncated?' · 分支已达局部总预算，可收起分支后继续。':'');
  }
  let localBranches=null,localBranchScope='',localGraphRevision=0,localSelectedType='';
  function bindLocalExploration(graph,selectedType=''){
    if(!window.GraphExplorer||!window.GraphExploration||get('graph-stage')?.dataset.graphMode==='full')return;
    localSelectedType=selectedType;
    const source={nodes:graph.nodes,edges:graph.edges};let index=null;
    const getIndex=()=>index||(index=new GraphExploration.NeighborIndex(source));
    GraphExplorer.setContext({mode:'local',key:JSON.stringify([current,graphScope()]),revision:++localGraphRevision,
      selected:wb.nodes.get(get('graph-node').value)||null,adapter:{
        neighbors:(id,options)=>getIndex().page(id,options),focus:(id,options)=>getIndex().focus(id,options),
        setFocus:value=>GraphWorkbench.setFocus(value),select:row=>{GraphWorkbench.locate(row.id);inspect(row);},
        expand:id=>window.expandGraphBranch(id,get('graph-hops').value),canCollapse:id=>Boolean(localBranches?.canCollapse(id)),
        collapse:id=>{if(!localBranches)return;graphRequests.invalidate();localBranches.collapse(id);const result=localBranches.graph();renderGraph(result,localSelectedType,result,{preservePath:true});}
      }});
  }
  window.refreshLocalGraphExplorer=()=>{if(ui.graph)bindLocalExploration(ui.graph,localSelectedType);else window.GraphExplorer?.clear({mode:'local'});};
  window.suspendLocalGraphExplorer=()=>{graphRequests.invalidate();localBranches?.invalidatePending();};
  window.drawGraph=async(node=null,hops=1,options={})=>{
    if(hops===''||!Number.isInteger(Number(hops))||Number(hops)<0||Number(hops)>10)throw Error('扩展跳数请输入 0–10 的整数');
    const id=node||get('graph-node').value||null;
    if(!id){const empty=graphRequests.begin(current,graphScope(),null);graphRequests.release(empty);get('graph-summary').textContent='请选择实体后展开局部邻域；查看当前范围全部实体请切换“全量大图”。';return null;}
    const project=current,scopeSnapshot=graphScope();
    const branchKey=JSON.stringify([project,scopeSnapshot]),expanding=options.expand&&localBranches&&localBranchScope===branchKey&&ui.graph?.nodes.some(row=>row.id===id);
    const branchTicket=expanding?localBranches.begin(id,branchKey):null;
    if(expanding&&!branchTicket)return null;
    const ticket=graphRequests.begin(project,scopeSnapshot,id),scopeTicket=window.WorkspaceContext?.ticket(project,'search');
    const isCurrent=()=>graphRequests.isCurrent(ticket,current,graphScope(),id)&&(!scopeTicket||window.WorkspaceContext.accepts(scopeTicket,current,'search'));
    const {entity_type,predicate,...appliedScope}=scopeSnapshot;
    const neighborhood={seeds:[id],hops:Number(hops),direction:get('graph-direction')?.value||'both',
      max_nodes:Number(get('graph-max-nodes')?.value)||500,max_edges:Number(get('graph-max-edges')?.value)||1000};
    const done=kgTime('graph-neighborhood');
    try{
      let result;
      try{result=await api('/api/projects/'+encodeURIComponent(project)+'/graph-neighborhood',{...appliedScope,...neighborhood});}
      catch(error){
        if(!isCurrent())return null;
        throw error;
      }
      if(!isCurrent())return null;
      const nodes=(result.records||[]).filter(row=>row.kind==='entity'||GraphTypeFilter.isAttributeNode(row));
      const visible=nodes.filter(row=>!entity_type||row.type===entity_type),ids=new Set(visible.map(row=>row.id));
      const edges=(result.records||[]).filter(row=>(row.kind==='relation'||GraphTypeFilter.isAttributeEdge(row))&&ids.has(row.subject_id)&&ids.has(row.object_id)&&(!predicate||row.type===predicate));
      result={...result,nodes:visible,edges,neighborhood:{...neighborhood,truncated:Boolean(result.truncated)}};
      done({node:id,hops,nodes:result.nodes.length,edges:result.edges.length,truncated:result.truncated});
      if(window.GraphExploration){
        if(expanding){if(!localBranches.accept(branchTicket,result))return null;result=localBranches.graph();}
        else{localBranches=new GraphExploration.BranchSession({maxNodes:neighborhood.max_nodes,maxEdges:neighborhood.max_edges,maxAttributes:result.attribute_budget||200});localBranchScope=branchKey;localBranches.reset(result,id,branchKey);localSelectedType='';}
      }
      renderGraph(result,localSelectedType,result,{preservePath:Boolean(expanding)});
      // 开放本体发现的提示条要跟着图谱一起刷新：这原本只在 workbench.js 的旧 drawGraph 副本里做，
      // 收敛成单一份实现后带到这里，避免 discovery 模式下提示停留在旧状态。
      renderDiscoveryHint().catch(()=>{});
      return result;
    }finally{
      graphRequests.release(ticket);
    }
  };
  window.clearGraphWorkspace=({preserveSearchResults=false}={})=>{
    localBranches?.clear();localBranches=null;localBranchScope='';localSelectedType='';
    GraphWorkbench.clear();
    graphRequests.invalidate();invalidatePendingDetail();
    ui.pinned.clear();ui.picker.request++;ui.mindmapPicker.request++;ui.mindmapRootId='';mindmap.invalidate();mindmapView?.clear();
    wb.epoch++;ui.graph=null;wb.nodes.clear();wb.chart?.dispatchAction({type:'hideTip'});wb.chart?.clear();
    selectedEntityId='';get('graph-node').value='';get('graph-entity-choice').value='';get('graph-detail').replaceChildren();
    get('graph-summary').textContent='从左侧检索结果选择实体或关系';
    get('graph-type-buttons')?.remove();get('graph-timeline')?.remove();
    if(!preserveSearchResults){get('hits').replaceChildren();get('search-summary').textContent='';}
  };
  document.addEventListener('version-context:changed',()=>{
    renderOntologyScopeBanners();
    window.clearGraphWorkspace({preserveSearchResults:true});
    wb.tree?.clear();
  });
  renderOntologyScopeBanners();
  async function refreshGraphCanvasTypes(){
    if(!current)return;const node=get('graph-node').value,selected=ui.pinned.get(node)||wb.nodes.get(node);
    window.clearGraphWorkspace({preserveSearchResults:true});if(selected)inspect(selected);
    try{await window.drawGraph(node||null,get('graph-hops').value);}catch(error){status(error.message,true);}
  }
  for(const id of ['type-scope','predicate-scope'])get(id).addEventListener('change',refreshGraphCanvasTypes);
  act('search',runSearch);act('draw-graph',()=>drawGraph(null,get('graph-hops').value));
  // 「展开邻域」与「绘制图谱」共用同一份渲染器：绑定从 workbench.js 移到这里，
  // 同一个按钮不再被两个文件各绑一次。没选中实体时给出明确提示，而不是等后端返回空图。
  act('graph-expand',async()=>{
    const id=get('graph-node').value;
    if(!id)throw Error('请先选择实体或点击图中的节点，再展开邻域。');
    const hops=get('graph-hops').value;
    if(!await window.drawGraph(id,hops,{expand:true}))return;
    status('已展开 '+hops+' 跳邻域');
  });
  window.expandGraphBranch=(id,hops=1)=>window.drawGraph(id,hops,{expand:true});
  window.ScopeEditor={restore(value){cb.conditions=[];cb.custom=Boolean(value.filters);renderCB();updateChip();}};
  async function refreshOwnedScope(){
    const page=window.WorkspaceContext?.page;
    if(page==='search'){ui.request++;window.clearGraphWorkspace();await Promise.all([options(),discoverMetadata()]);if(get('query').value.trim()&&get('scope-purpose')?.value!=='graph')await runSearch();}
    else if(page==='records'||page==='sources')await discoverMetadata();
  }
  document.addEventListener('workspace:scope-changed',()=>{refreshOwnedScope().catch(error=>status(error.message,true));});
  act('apply-scope',async()=>{window.WorkspaceContext.apply();});
  const previousAddFilter=get('add-filter')?.onclick;
  if(previousAddFilter)get('add-filter').onclick=async()=>{await previousAddFilter();};
  act('reset-scope',async()=>{window.WorkspaceContext.reset();});
  act('build-index',async()=>{await window.ProjectVectorIndex.open(current);});
  // 原来只 dispatch restore：返回 series 里写的 zoom:1，而 fitGraph 之后本来就是 zoom:1，
  // 于是"点了没什么作用"（用户原话）。现在＝适应画布：重新铺开节点 + 缩放到刚好占满画布。
  act('graph-reset-view',async()=>{
    const chart=wb.chart;
    if(!chart||!ui.graph){status('请从左侧检索结果选择实体或关系，再展开邻域。也可在图谱设置中按名称或 ID 选择实体。');return;}
    chart.resize();fitGraph(chart);
    status('已适应画布：保留节点位置并恢复视图。需要重新排列时点“重新布局”。');
  });
  // 平移/缩放的可发现性：双击空白处＝适应画布。
  // ECharts 的 zr 事件点在节点上时 event.target 有值，空白处没有 —— 用这个区分，
  // 免得双击节点也把整图重置了。
  function bindPanAffordances(){
    const chart=wb.chart;
    if(!chart||chart.__panAffordances)return;
    chart.__panAffordances=true;
    chart.getZr().on('dblclick',event=>{
      if(event.target)return;                 // 点在节点/连线上：交给默认行为（看详情）
      const reset=get('graph-reset-view');
      if(reset)reset.onclick?.();
    });
  }
  // 图谱缩放：ECharts 的 roam 只给了滚轮/拖pinch，没有可见按钮 —— 触屏和"只想点一下"
  // 的场景就没有入口。这里直接改 series.zoom（保留用户的中心点），并把范围夹在 0.2~4，
  // 免得缩到看不见或放大到只剩一个节点。
  function zoomGraph(factor){
    const chart=wb.chart;
    if(!chart||!ui.graph)throw Error('请先选择实体并展开邻域，再缩放图谱。');
    const next=GraphWorkbench.zoom(chart,factor);
    status(`图谱缩放 ${(next*100).toFixed(0)}%（滚轮也可缩放，拖动可平移）`);
  }
  act('graph-zoom-in',()=>zoomGraph(1.18));
  act('graph-zoom-out',()=>zoomGraph(.85));
  get('detail-drawer-close').onclick=()=>{invalidatePendingDetail();get('graph-detail').replaceChildren();};
  get('query').onkeydown=e=>{if(e.key==='Enter')get('search').click();};
  // Ignore out-of-order project responses, and load graph immediately on selection.
  const priorChange=get('project').onchange;
  get('project').onchange=()=>{
    const generation=++ontologyLoadGeneration;
    priorChange();ui.request++;ui.options=[];ui.mindmapPicker.options=[];ui.mindmapPicker.request++;ui.mindmapRootId='';select.value='';get('mindmap-filter').value='';choices();ui.ontology=null;resetTimeline();window.clearGraphWorkspace();
    for(const id of ['filters','cb-raw-json','query-platforms','query-businesses','query-audiences'])get(id).value='';
    if(get('cb-show-system'))get('cb-show-system').checked=false;
    get('graph-entity-filter').value='';cb.facets=[];cb.conditions=[];cb.custom=false;renderCB();get('metadata-field-choice').innerHTML='<option value="">读取项目字段…</option>';get('metadata-value-choice').innerHTML='<option value="">先选择字段</option>';get('metadata-facet-summary').textContent='';
    get('project').title=get('project').selectedOptions[0]?.textContent||'';
    get('type-scope').value='';get('predicate-scope').value='';
    get('query').value='';get('graph-node').value='';updateChip();
    if(current){const project=current;sessionStorage.setItem('knowledge-project',project);loadOntologyTerms().then(result=>{
      if(project!==current||generation!==ontologyLoadGeneration)return;ui.ontology=result;return Promise.all([options(),discoverMetadata()]);
    }).then(()=>{if(project===current&&generation===ontologyLoadGeneration)autoLoadGraph();}).catch(e=>{if(project===current&&generation===ontologyLoadGeneration)status(e.message,true);});}
  };
  // 打开「检索与交互图谱」且尚未渲染时，自动加载当前 scope 全图。
  // 挂在 click / 项目切换 / 页面恢复项目 三个时机，避免默认 active tab 不触发 click 导致空白。
  // 由**调用方指定要看什么**时的抑制开关：例如问答里点"在图谱中查看"，
  // 它会 showTab('search') 再显示某个实体的详情——此时补画全图是多余的请求，
  // 而且会先把画布清空（先清后画），反而把刚选中的实体冲掉。
  // 用法：调用方在导航前 window.suppressNextAutoGraph()，本次补画被吃掉后自动复位。
  let suppressNextAutoGraph=false;
  window.suppressNextAutoGraph=()=>{suppressNextAutoGraph=true;};

  function autoLoadGraph(){
    if(suppressNextAutoGraph){suppressNextAutoGraph=false;return;}
    const graphView=!get('tab-search').classList.contains('hidden');
    if(graphView&&current&&!ui.graph&&!graphRequests.isBusy()){
      drawGraph(null,get('graph-hops').value)
        .catch(e=>status(e.message,true));
    }
  }
  /* 兜底刷新：不管新版本是谁发布的（本体建模层、本体建模层、别人、另一个浏览器标签），
     用户切回检索 / 问答 / 脑图页时都要自己跟上。以前只有"审核台发布 + 本页收到事件"这一条路，
     所以症状是"发布完了，点回检索还是旧的，必须整页刷新"（用户原话）。
     这里再查一次当前本体 id，变了就按新版本刷新术语/类型/图谱；没变则什么都不做（零成本）。 */
  async function catchUpOntology(){
    if(!current)return false;
    try{
      const before=ui.ontology?.id||'';
      const project=current,generation=ontologyLoadGeneration,latest=await loadOntologyTerms();
      if(project!==current||generation!==ontologyLoadGeneration)return false;
      ui.ontology=latest;
      const id=latest?.id||'';
      // First observation establishes the baseline. Boot already loaded the
      // current terms; absence of a cached editor DTO is not a new publication.
      if(!id||!before||id===before)return false;
      await Promise.all([options(),discoverMetadata()]);
      if(project!==current||generation!==ontologyLoadGeneration)return false;
      resetTimeline();
      window.clearGraphWorkspace({preserveSearchResults:true});
      status(`检测到本体新版本（${id.slice(0,8)}…）：类型下拉、时间轴与图谱已按新版本刷新。`);
      return true;
    }catch(error){
      // 兜底失败不打断用户正在做的事：说一句能自己解决的提示就够了。
      status(`切回这一页时没能刷新到最新本体：${error.message}。点「重新渲染全图」可重试。`,true);
      return false;
    }
  }
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',event=>{
    const graphView=b.dataset.tab==='search';
    get('type-scope').style.display=graphView?'':'none';get('predicate-scope').style.display=graphView?'':'none';
    if(b.dataset.tab==='mindmap'&&current)loadEntityChoices(get('mindmap-filter').value,null,'mindmap').catch(e=>status(e.message,true));
    if(['search','qa','mindmap'].includes(b.dataset.tab)){
      // 只有**用户真的点了导航**才做"进入页签时对齐本体"。程序化切换（问答/图谱里
      // "在图谱中查看"会先 showTab('search') 再显示某个实体）绝不能顺手重画全图：
      // 那条路径是 await 的（先查本体 id 再补画），落地时会晚于 selectEntityDetail，
      // 于是先把画布清掉、再发一次 /subgraph，把刚选中的实体冲掉——
      // 现象就是"点证据里的节点，图谱却重新加载了"（契约用例抓到过）。
      if(event&&event.isTrusted===false){autoLoadGraph();return;}
      // 先对齐本体版本，再决定是否重画：旧图谱不能拿新类型名去渲染。
      catchUpOntology().then(autoLoadGraph).catch(()=>autoLoadGraph());
    }else autoLoadGraph();
  }));
  // 本体建模层发布出一个新版本后，这一页必须自己跟上：术语缓存、实体类型/关系类型
  // 下拉、时间轴与图谱都读的是"已发布本体"。以前没有任何通知通道，用户看到的就是
  // "发布完了，切回检索与交互图谱还是旧的，必须整页刷新"。事件由 ontology-model-panel.js
  // 在发布成功后派发（ontology-model:published）。
  document.addEventListener('ontology-model:published',async event=>{
    if(!current||(event.detail?.projectId&&event.detail.projectId!==current))return;
    const project=current;
    const generation=++ontologyLoadGeneration;
    const versionId=event?.detail?.versionId||'';
    const short=versionId?versionId.slice(0,8)+'…':'';
    ui.ontology=null;ui.options=[];ui.request++;
    try{
      // 顺序有意义：先刷新本体术语缓存（labelOf/term 都依赖它），再取实体选项，
      // 最后才重画图——否则图上的类型名还是旧版本。
      const latest=await loadOntologyTerms({fresh:true});if(project!==current||generation!==ontologyLoadGeneration)return;ui.ontology=latest;
      await Promise.all([options(),discoverMetadata()]);
      if(project!==current||generation!==ontologyLoadGeneration)return;
      resetTimeline();
      // 旧图谱是按旧范围/旧本体渲染的，先清掉；检索页可见时立刻重画，不可见时
      // 由 autoLoadGraph()（tab 点击时触发）按最新状态补画。
      window.clearGraphWorkspace({preserveSearchResults:true});
      if(!get('tab-search').classList.contains('hidden'))await window.drawGraph(null,get('graph-hops').value);
      status(`本体新版本已发布${short?`（${short}）`:''}：类型下拉、时间轴与图谱已按新版本刷新。`);
    }catch(error){
      status(`新版本已发布，但这一页的检索/图谱刷新失败：${error.message}。可点「重新渲染全图」重试。`,true);
    }
  });
  // Dashboard loads on entry and follows project / scope changes.
  async function refreshDashboard(){
    const p=current;
    if(!p){get('dashboard').innerHTML='<p class="subtle">请先在左侧选择项目；已入库项目无需再次导入。</p>';return;}
    get('dashboard').innerHTML='<p class="subtle" role="status">正在读取当前项目统计…</p>';
    try{await dashboard();}catch(error){if(p===current)get('dashboard').textContent='统计加载失败：'+error.message+'，请点击“刷新统计”重试。';}
  }
  // 运行总览并入「项目与运行」后不再有独立页签：由 RuntimeView 在进入「运行总览」分区时调用。
  // 下面两处 get('tab-dashboard') 判断的还是同一个面板 id（面板沿用旧 id），所以口径没变。
  window.RuntimeView?.on('overview',refreshDashboard);
  get('project').addEventListener('change',()=>{dashboardRequest++;if(!get('tab-dashboard').classList.contains('hidden'))refreshDashboard();});

  // Boot into the linked workbench and restore the last selected existing project.
  showTab('search');
  // 等认证就绪再拉项目列表：未登录时先挂着。以前这里直接 projects()，
  // 会带着空令牌打 /api/projects 拿 401（登录框因此误报"登录状态已失效"）。
  (window.Auth&&window.Auth.whenReady?window.Auth.whenReady():Promise.resolve())
   .then(()=>projects()).then(()=>{const saved=sessionStorage.getItem('knowledge-project');if(saved&&[...get('project').options].some(o=>o.value===saved))get('project').value=saved;else if(get('project').options.length>1)get('project').selectedIndex=1;if(get('project').value)get('project').onchange();}).catch(e=>status(e.message,true));
})();

/* Open ontology discovery: schema-free candidates stay separate until a draft is published. */
(()=>{
  const get=id=>document.getElementById(id);
  const oldToggle=get('extract'),mode=document.createElement('select');mode.id='extraction-mode';
  mode.innerHTML='<option value="ontology">使用项目本体 · 正式入图</option><option value="discovery">开放本体发现 · 候选暂存</option><option value="documents">仅文档检索 · 不抽取图谱</option>';
  const modeLabel=document.createElement('label');modeLabel.className='extraction-mode-control';modeLabel.innerHTML='<span>解析模式 <b>必选</b></span>';modeLabel.append(mode);
  const help=document.createElement('p');help.id='extraction-mode-help';help.className='extraction-mode-help';
  // 2026-10-05：写入页改为纯文件上传，解析模式是\"文件会被怎样处理\"的唯一总开关，
  // 从高级区挪到面板最顶部的占位槽（#ingest-mode-slot）——发送前一眼可见、不会再误操作。
  const slot=document.getElementById('ingest-mode-slot');
  if(slot)slot.append(modeLabel,help);
  else{oldToggle.closest('label').before(modeLabel,help);}
  window.IngestOntology.mount(mode,help);
  oldToggle.closest('label').classList.add('hidden');
  const descriptions={ontology:'按当前项目本体抽取、校验和实体融合，合法知识进入正式图谱。',discovery:'不要求预设本体；Semantica 自由抽取并持续累计候选，不直接写入正式图谱。',documents:'只保存原文、切片和向量，不抽取实体、关系或属性。'};
  mode.onchange=()=>{
    help.textContent=descriptions[mode.value];oldToggle.checked=mode.value!=='documents';
    window.IngestOntology.refresh();
    for(const id of ['parse-resolve','parse-merge','parse-relation-constraints'])if(get(id))get(id).disabled=mode.value!=='ontology';
    syncMergeThreshold();
    if(get('parse-attributes'))get('parse-attributes').disabled=mode.value==='documents';
  };
  // 语义合并阈值只在「使用项目本体」模式且勾选「语义相似合并」时展示/启用
  function syncMergeThreshold(){
    const label=get('merge-threshold-group');
    if(!label)return;
    const active=mode.value==='ontology'&&get('parse-merge').checked;
    label.hidden=!active;
    if(get('parse-threshold'))get('parse-threshold').disabled=!active;
  }
  if(get('parse-merge'))get('parse-merge').onchange=syncMergeThreshold;
  mode.onchange();

})();
