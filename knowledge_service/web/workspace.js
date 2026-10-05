/* Linked graph workspace: preserve existing editing/governance controls. */
(() => {
  // 图谱是**只读画布**：节点不可拖动（力导向布局只负责初始摆放，节点位置不承载编辑语义）。
  // 空白处拖动＝平移画布（ECharts 的 roam:true）；点节点/连线＝看详情；双击空白＝适应画布。
  // 以前那套「移动节点」zrender 拖动 + 开关按钮已整体移除：对一张检索浏览用的图，
  // 拖动节点不是能力而是负担——布局会被拖乱，也偏离"正常画布操作"的直觉。
  const ui = {request:0, options:[], ontology:null, graph:null};
  const graphRequests=GraphTypeFilter.createGraphRequestGate();
  const get = id => document.getElementById(id);
  const make = (tag, html, className='') => {const e=document.createElement(tag);e.innerHTML=html;e.className=className;return e;};
  const graphExpandState={busy:false,pendingDetail:false};
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
  const cb={conditions:[],nextId:1,facets:[]};
  const opLabel=op=>({eq:'等于',ne:'不等于',contains:'包含',in:'属于集合',gte:'大于等于',lte:'小于等于',exists:'存在',interval:'区间'}[op]||op);
  const TIME_OPS=[{v:'eq',t:'等于'},{v:'ne',t:'不等于'},{v:'gte',t:'大于等于'},{v:'lte',t:'小于等于'},{v:'interval',t:'区间'}];
  const TEXT_OPS=[{v:'eq',t:'等于'},{v:'ne',t:'不等于'},{v:'contains',t:'包含'},{v:'in',t:'属于集合'},{v:'gte',t:'大于等于'},{v:'lte',t:'小于等于'},{v:'exists',t:'字段存在'}];
  function fieldOptions(){
    return [{v:'valid_from',l:'valid_from（业务生效时间）'},{v:'valid_until',l:'valid_until（业务失效时间）'},
      ...cb.facets.map(f=>({v:f.field,l:f.field.replace(/^metadata\./,'')+' · '+f.records+' 条'}))];
  }
  function addCondition(field='valid_from',op='eq',value='',from='',to=''){
    cb.conditions.push({id:cb.nextId++,field,op,value,from,to});
    renderCB();syncFilters();
  }
  function removeCondition(id){cb.conditions=cb.conditions.filter(c=>c.id!==id);renderCB();syncFilters();}
  function renderCB(){
    const rows=get('cb-rows');
    if(!cb.conditions.length){rows.innerHTML='<p class="cb-empty">暂无条件，点击下方按钮添加</p>';}
    else{rows.innerHTML='';for(const c of cb.conditions){
      const isTime=TIME_FIELDS.has(c.field),ops=isTime?TIME_OPS:TEXT_OPS,showInterval=c.op==='interval';
      const row=make('div','','cb-row');row.dataset.id=c.id;
      row.innerHTML=`<select class="cb-field">${fieldOptions().map(o=>`<option value="${esc(o.v)}"${o.v===c.field?' selected':''}>${esc(o.l)}</option>`).join('')}</select><select class="cb-op">${ops.map(o=>`<option value="${o.v}"${o.v===c.op?' selected':''}>${o.t}</option>`).join('')}</select>${showInterval?`<span class="cb-date-label">从</span><input type="date" class="cb-date-from" value="${esc(c.from||'')}"><span class="cb-date-label">到</span><input type="date" class="cb-date-to" value="${esc(c.to||'')}">`:`<input type="text" class="cb-value" value="${esc(c.value||'')}" placeholder="${c.op==='exists'?'（无需填写）':'值'}"${c.op==='exists'?' disabled':''}>`}<button class="cb-remove secondary" title="删除">×</button>`;
      rows.appendChild(row);
    }}
    renderChips();updateChip();
  }
  function renderChips(){
    get('cb-active-chips').innerHTML=cb.conditions.map(c=>{
      const label=TIME_FIELDS.has(c.field)?(c.op==='interval'?`${c.field} ${c.from||'?'} → ${c.to||'?'}`:`${c.field} ${opLabel(c.op)} ${c.value||''}`):`${c.field} ${opLabel(c.op)} ${c.value||''}`;
      return `<span class="cb-chip"><span>${esc(label)}</span><button data-id="${c.id}" title="删除">×</button></span>`;
    }).join('');
  }
  function updateChip(){
    const n=cb.conditions.length;
    get('scope-chip').textContent=n?`已启用 ${n} 个条件`:'无 metadata 条件';
  }
  function syncFilters(){
    const conds=[];
    for(const c of cb.conditions){
      if(c.op==='interval'){if(c.from)conds.push({field:c.field,op:'gte',value:c.from});if(c.to)conds.push({field:c.field,op:'lte',value:c.to});}
      else{const val=c.op==='exists'?true:c.value;if(c.field&&(c.op==='exists'||val))conds.push({field:c.field,op:c.op,value:val});}
    }
    const json=conds.length?JSON.stringify({and:conds},null,2):'';
    get('filters').value=json;
    const raw=get('cb-raw-json');if(raw&&document.activeElement!==raw)raw.value=json;
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
  async function discoverMetadata(){
    const p=current,stamp=JSON.stringify(scope());if(!p)return;
    const result=await api(endpoint('/metadata/facets'),scope());
    if(p!==current||stamp!==JSON.stringify(scope()))return;
    cb.facets=result.fields;
    const previous=get('metadata-field-choice').value;
    get('metadata-field-choice').innerHTML='<option value="">选择字段（自动读取项目数据）</option>'+cb.facets.map(f=>`<option value="${esc(f.field)}">${esc(f.field.replace(/^metadata\./,''))} · ${f.records} 条</option>`).join('');
    if(cb.facets.some(f=>f.field===previous)){get('metadata-field-choice').value=previous;selectFacet();}
    else{get('metadata-value-choice').innerHTML='<option value="">先选择字段</option>';get('metadata-facet-summary').textContent=`发现 ${cb.facets.length} 个字段 · ${result.records} 条记录${result.truncated?' · 已限制展示规模':''}`;}
    renderCB();
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape')details.open=false;});
  document.addEventListener('pointerdown',e=>{if(details.open&&!details.contains(e.target))details.open=false;});
  // Advanced JSON edits flow into the canonical #filters textarea.
  get('cb-raw-json').addEventListener('change',()=>{const v=get('cb-raw-json').value.trim();try{get('filters').value=v?JSON.stringify(JSON.parse(v),null,2):'';}catch{get('filters').value=v;}});

  // Search and graph live in one workspace; graph changes only after an explicit selection.
  const graphTab=get('tab-search');
  const graphPanel=get('graph-stage');
  get('graph-labels').onchange=()=>{const show=get('graph-labels').checked;wb.chart?.setOption({series:[{label:{show},emphasis:{label:{show}}}]});};

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
    get('known-at').value=value;renderTimelinePoints();
    window.clearGraphWorkspace({preserveSearchResults:true});
  }
  function moveTimeline(direction){
    const values=[...uiTimeline.events.map(event=>event.known_at),''],currentValue=get('graph-timeline').querySelector('.tl-points').value,index=values.indexOf(currentValue),next=Math.max(0,Math.min(values.length-1,(index<0?values.length-1:index)+direction));applyTimelinePoint(values[next]);
  }
  function resetTimeline(){
    uiTimeline.events=[];uiTimeline.project=null;uiTimeline.truncated=false;get('known-at').value='';const el=get('graph-timeline');if(el)el.querySelector('.tl-points').innerHTML='<option value="">当前状态</option>';
  }

  // A searchable native select, not an internal record ID input.
  const select=get('mindmap-root');
  select.parentElement.before(make('label','<span>筛选名称 / 类型</span><input id="mindmap-filter" placeholder="输入名称缩小候选范围">'));
  const oldMindmap=get('draw-mindmap').onclick;
  get('draw-mindmap').onclick=async()=>{if(!select.value){status('当前筛选范围内没有可选实体',true);return;}await oldMindmap();wb.tree?.setOption({series:[{initialTreeDepth:1,label:{position:'right',align:'left',width:170,overflow:'truncate',fontSize:11},leaves:{label:{position:'right',align:'left',width:170,overflow:'truncate',fontSize:11}}}]});};
  function choices(){
    for(const [selectId,filterId] of [['mindmap-root','mindmap-filter'],['graph-entity-choice','graph-entity-filter']]){
      const element=get(selectId),previous=element.value, query=get(filterId).value.trim().toLocaleLowerCase();
      let rows=ui.options.filter(r=>(r.text+' '+labelOf(r.type)+' '+term(r.type)).toLocaleLowerCase().includes(query));
      if(selectId==='graph-entity-choice'&&selectedEntityId&&!rows.some(r=>r.id===selectedEntityId)){
        const committed=ui.options.find(r=>r.id===selectedEntityId);
        if(committed)rows=[...rows,committed];
      }
      // 同名同类型重复实体显示来源文档，方便区分（比 ID 更友好）
      const counts=new Map();
      rows.forEach(r=>{const key=r.text+'|'+labelOf(r.type);counts.set(key,(counts.get(key)||0)+1);});
      element.innerHTML=(selectId==='graph-entity-choice'?'<option value="">选择实体</option>':'')+rows.map(r=>{const key=r.text+'|'+labelOf(r.type);const dup=(counts.get(key)||0)>1;const src=r.source?(' · '+String(r.source).replace(/\.(md|txt|docx?)$/i,'').slice(0,16)):'';return `<option value="${esc(r.id)}" title="${esc(term(r.type))}">${esc(r.text)} · ${esc(labelOf(r.type))}${dup?esc(src):''}</option>`;}).join('');
      if(rows.some(r=>r.id===previous))element.value=previous;
      if(selectId==='graph-entity-choice'&&pendingEntityId&&element.value!==pendingEntityId)invalidatePendingDetail();
    }
  }
  get('mindmap-filter').oninput=choices;
  let selectedEntityId='',pendingEntityId='',detailSelectionEpoch=0;
  const detailScope=()=>({valid_at:scope().valid_at||null,known_at:get('known-at').value||null});
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
  get('graph-entity-filter').oninput=choices;
  get('graph-entity-filter').onkeydown=e=>{if(e.key==='Enter'&&get('graph-entity-choice').value)get('graph-entity-choice').onchange();};
  get('graph-entity-choice').onchange=async()=>{
    const choice=get('graph-entity-choice'),id=choice.value;
    if(!id){
      // 用户主动选回空白项 = 清空选择、回到当前范围的全图。
      // 以前这里无条件 restoreCommittedSelection()，值会被弹回上一个实体，
      // 看起来就是"点其他之后切不回空白了"（用户原话）。
      // 仍然保留 restoreCommittedSelection()：它现在只服务于"取消一次没加载完的详情"。
      selectedEntityId='';
      get('graph-node').value='';
      invalidatePendingDetail();
      // 详情面板也跟着清空：留着上一条实体的详情会让人以为"还选着它"。
      get('graph-detail').replaceChildren();
      status('已清空实体选择：重新绘制当前范围的全图。');
      await window.drawGraph(null,Number(get('graph-hops').value));
      return;
    }
    const local=wb.nodes.get(id);
    if(local){inspect(local);status('已选择：'+local.text+' · 点击“展开邻域”查看邻域');return;}
    const token=beginPendingDetail(id),project=current;
    try{
      const temporal=detailScope(),stamp=JSON.stringify(temporal);
      const isCurrent=()=>token===detailSelectionEpoch&&pendingEntityId===id&&project===current&&choice.value===id&&JSON.stringify(detailScope())===stamp;
      const params=new URLSearchParams();
      for(const key of ['valid_at','known_at'])if(temporal[key])params.set(key,temporal[key]);
      const query=params.size?'?'+params.toString():'';
      const row=await api(endpoint('/records/'+encodeURIComponent(id)+query),undefined,'GET');
      if(!isCurrent()){settlePendingDetail(token);return;}
      inspect(row);status('已选择：'+row.text+' · 点击“展开邻域”查看邻域');
    }catch(error){if(settlePendingDetail(token))status(error.message,true);}
  };
  async function selectEntityDetail(id){
    const choice=get('graph-entity-choice');
    if(![...choice.options].some(option=>option.value===id)){
      get('graph-entity-filter').value='';choices();
    }
    choice.value=id;
    if(choice.value!==id)throw Error('当前范围内没有该实体');
    await choice.onchange();
  }
  window.selectEntityDetail=selectEntityDetail;

  async function options(){
    const p=current, stamp=JSON.stringify(scope());if(!p)return;
    const done=kgTime('entity-options');
    const result=await api(endpoint('/entity-options'),scope());
    done({entities:result.entities.length,predicates:result.predicates.length});
    if(current!==p||stamp!==JSON.stringify(scope()))return;
    ui.options=result.entities;choices();
    for(const [id,items,label] of [['type-scope',[...new Set(result.entities.map(r=>r.type))],'全部实体类型'],['predicate-scope',result.predicates,'全部关系类型']]){
      const previous=get(id).value;get(id).innerHTML=`<option value="">${label}</option>`+items.map(t=>`<option value="${esc(t)}" title="${esc(term(t))}">${esc(labelOf(t))}</option>`).join('');
      if(items.includes(previous))get(id).value=previous;
    }
  }
  function inspect(row){
    invalidatePendingDetail();
    if(GraphTypeFilter.isAttributeNode(row)){
      const entity=wb.nodes.get(row.subject_id);
      if(entity)return inspect(entity);
      return;
    }
    const isEntity=row.kind==='entity';
    if(isEntity){selectedEntityId=row.id;get('graph-node').value=row.id;get('mindmap-root').value=row.id;get('graph-entity-choice').value=row.id;}
    window.renderEvidenceInspector(row,ui.options);
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
    rows.push(parents.length
      ? `父类：${esc(parents.join(' → '))}`
      : '父类：顶层类（本体里没有父类）');
    const ancestors=(node.class_ancestors||[]).map(label).filter(Boolean);
    if(ancestors.length>parents.length){
      rows.push(`完整继承链：${esc([node.class_label,...ancestors].filter(Boolean).join(' → '))}`);
    }
    return rows.join('<br>');
  }
  function renderGraph(result, selectedType='', base=result){
    const displayType=type=>result.type_labels?.[type]||labelOf(type);
    // 按实体类型取色，节点、图例、类型按钮颜色一致，避免同名/近名节点无法区分
    // 颜色只有一个出处：graph-palette.js 的 GraphPalette.colorFor(类型名)。
    // 以前这里是"按数组下标取色"，于是同一个类型换个顺序就换色，且和"按实体类型查看"
    // 那排圆点错位一格（圆点用的是按钮下标，第 0 项还是「全部类型」）。
    // 关键：取色的 key 必须是"页面上显示的那个类型名"（label），
    // 不能有的地方传原始 type、有的地方传 label —— 那样同一个类型会算出两种颜色。
    result=GraphTypeFilter.selectType(base,selectedType);
    let bar=get('graph-type-buttons');
    if(!bar){bar=make('section','','graph-type-buttons');bar.id='graph-type-buttons';get('graph-canvas').after(bar);}
    const baseEntities=GraphTypeFilter.entityNodes(base);
    const groups=[...new Set(baseEntities.map(n=>n.type))];
    // 颜色只有一个出处：graph-palette.js。本轮视图里出现过的类型先统一排序取色，
    // 撞色时自动挪位，保证同屏不重色；类型名相同则在任何页面都是同一个颜色。
    const scopeLabels=[...new Set(baseEntities.map(n=>n.type_label||displayType(n.type)))].filter(Boolean);
    const colorMap=window.GraphPalette?GraphPalette.colorMap(scopeLabels):null;
    const colorFor=label=>(colorMap&&colorMap.get(label))||(window.GraphPalette?GraphPalette.colorFor(label):'#5470c6');
    const attrColor=window.GraphPalette?GraphPalette.attribute:'#8c7b68';
    // 「全部类型」不是某个类型，不画色点（以前它会占用 palette[0]，把整排颜色挤错一位）。
    // 圆点跟节点用同一个 label 取色（以前这里传的是原始 type，和节点的 label 不是同一个 key）
    const dot=label=>label?`<i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${colorFor(label)};margin-right:5px;vertical-align:middle"></i>`:'';
    bar.innerHTML='<small>按实体类型查看 · 只显示所选类型，不展开邻居</small><div>'+['',...groups].map((type,i)=>`<button class="secondary" data-type-index="${i}" aria-pressed="${type===selectedType}" title="${esc(type?term(type):'恢复当前检索范围全部类型')}">${dot(type?displayType(type):'')}${esc(type?displayType(type):'全部类型')} <b>${type?baseEntities.filter(n=>n.type===type).length:baseEntities.length}</b></button>`).join('')+'</div>';
    bar.querySelectorAll('button').forEach(button=>button.onclick=()=>renderGraph(base,['',...groups][Number(button.dataset.typeIndex)],base));
    ui.graph=result;wb.nodes=new Map(result.nodes.map(n=>[n.id,n]));
    const chart=initChart(), types=[...new Set(result.nodes.map(n=>GraphTypeFilter.isAttributeNode(n)?'属性值':(n.type_label||displayType(n.type))))], matched=new Set(result.matched_ids||[]);
    chart.resize();
    bindPanAffordances();   // 双击空白＝适应画布（幂等）
    chart.off('click');chart.on('click',p=>{
      const row=p.dataType==='edge'?result.edges.find(e=>e.id===p.data.id):wb.nodes.get(p.data.id);
      if(!row)return;
      if(GraphTypeFilter.isAttributeEdge(row)||GraphTypeFilter.isAttributeNode(row)){
        const entity=wb.nodes.get(row.subject_id);if(entity)inspect(entity);return;
      }
      inspect(row);
    });
    const showLabels=get('graph-labels').checked;
    // The force layout plus the fit pass below are the client half of "the graph feels
    // slow"; the server half is the `subgraph` line logged by drawGraph.
    const layoutDone=kgTime('graph-layout');
    const catIndex=label=>Math.max(0,types.indexOf(label));
chart.setOption({animation:false,tooltip:{formatter:graphTooltip},legend:[{show:types.length>1,type:'scroll',data:types,bottom:0,textStyle:{fontSize:11}}],series:[{type:'graph',layout:'force',roam:true,center:['50%','50%'],zoom:.85,label:{show:showLabels,position:'right',fontSize:11,width:110,overflow:'truncate'},edgeSymbol:['none','arrow'],edgeLabel:{show:result.nodes.length<=20,formatter:'{c}',fontSize:10},emphasis:{focus:'adjacency',label:{show:showLabels},edgeLabel:{show:true}},force:{repulsion:320,edgeLength:100,gravity:.1,initLayout:'circular',layoutAnimation:false},categories:types.map(name=>({name,itemStyle:{color:name==='属性值'?attrColor:colorFor(name)}})),data:result.nodes.map(n=>{const attribute=GraphTypeFilter.isAttributeNode(n),label=attribute?'属性值':(n.type_label||displayType(n.type));const index=catIndex(label);return {id:n.id,name:n.text,category:index,categoryName:label,symbol:attribute?'roundRect':'circle',symbolSize:attribute?[72,22]:(matched.has(n.id)?34:22),itemStyle:{color:attribute?attrColor:colorFor(label),...(attribute&&n.status==='contradicting'?{borderColor:'#b42318',borderWidth:2,borderType:'dashed'}:{}),...(matched.has(n.id)?{borderColor:'#df7d37',borderWidth:4}:{})}}}),links:result.edges.map(e=>{const attribute=GraphTypeFilter.isAttributeEdge(e);return {id:e.id,source:e.subject_id,target:e.object_id,name:e.type_label||displayType(e.type),value:e.type_label||displayType(e.type),rawType:term(e.type),lineStyle:attribute?{type:'dashed',opacity:.55,width:1}:undefined};}),lineStyle:{opacity:.35,curveness:.12}}]},true);
    const visibleEntities=GraphTypeFilter.entityNodes(result),visibleRelations=GraphTypeFilter.relationEdges(result),attributeCount=result.nodes.length-visibleEntities.length;
    chart.resize();fitGraph(chart);layoutDone({nodes:result.nodes.length,edges:result.edges.length});get('graph-summary').textContent=`${selectedType?'只看 '+labelOf(selectedType)+' · ':''}${visibleEntities.length} 实体 / ${visibleRelations.length} 关系${attributeCount?' · '+attributeCount+' 个属性值':''}${selectedType?' · 隐藏其他类型及跨类型连线':''}${matched.size?' · 橙色边框为检索命中':''}${result.timing_ms?' · 服务 '+result.timing_ms.total+' ms':''}`;
    // Compact, discrete history navigation; no drag/playback re-query loop.
    const tl=createTimeline();get('graph-type-buttons')?.after(tl);
    // The type and timeline rows reduce the remaining flex height after ECharts has
    // already measured it. Resize on the next layout frame so its canvas cannot
    // retain the old height and paint over those rows.
    requestAnimationFrame(()=>{if(ui.graph===result||ui.graph===base)chart.resize();});
    loadTimelineEvents().catch(error=>status(error.message,true));
  }
  function fitGraph(chart){
    // PERF: getOption() deep-clones the entire option, and the second
    // setOption(...,true) at the end re-inits the force layout. Left exactly as it was:
    // changing it alters where nodes land, so it is a styling decision, not a capability
    // one. `kgTime` reports what it costs so that decision can be made with numbers.
    const done=kgTime('fit-graph');
    const series=chart.getModel().getSeriesByIndex(0), data=series.getData(), points=[];
    for(let i=0;i<data.count();i++){const p=data.getItemLayout(i);if(p&&p.every(Number.isFinite))points.push(p);}
    if(!points.length||points.length!==data.count()){kgLog('fit-graph',{skipped:'layout-incomplete'});return;}
    // 节点很少(1-2个)时不做强制拉伸适配：孤立点会被放大到怪异位置/大小，
    // 保持自然正圆居中即可，避免"变抽象/变椭圆"。
    if(data.count()<=3){chart.resize();done({nodes:data.count(),skipped:'few-nodes'});return;}
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const minX=Math.min(...xs),minY=Math.min(...ys);
    const spanX=Math.max(1,Math.max(...xs)-minX),spanY=Math.max(1,Math.max(...ys)-minY);
    // Persist the solved positions so fitting does not restart the simulation.
    const option=chart.getOption();
    const nodeCount=series.option.data.length;
    // 斥力按节点数放大：节点越多要撑得越开，否则标签互相压住。
    const spread=Math.round(420+nodeCount*22);
    Object.assign(option.series[0],{left:25,right:120,top:30,bottom:30,center:null,zoom:1,
      // 这里以前写成 force:{initLayout:null,layoutAnimation:true}，把 repulsion/edgeLength
      // 一起抹掉了 → ECharts 回落到默认斥力与边长，25 个节点就挤成一团。
      // 用户原话："初始化的图谱能不能分散点，为什么一开始就都聚集在一起"。
      // 现在显式给出斥力/边长/重力，配合下面的坐标归一化，初始就铺满画布。
      force:{repulsion:spread,edgeLength:[70,150],gravity:.07,initLayout:null,layoutAnimation:true},
      data:series.option.data.map((node,i)=>{
      const p=data.getItemLayout(i);
      return {...node,x:(p[0]-minX)/spanX*Math.max(1,chart.getWidth()-145),y:(p[1]-minY)/spanY*Math.max(1,chart.getHeight()-60),fixed:false};
    })});
    chart.setOption(option,true);
    done({nodes:series.option.data.length});
  }
  function renderHits(result){
    get('hits').innerHTML=['entity','chunk','relation'].map(kind=>{
      const rows=result.hits.filter(r=>r.kind===kind);
      return `<section><h3 class="hit-kind">${{entity:'匹配实体',chunk:'原文片段',relation:'关系链路'}[kind]} <small>${rows.length} / ${result.channel_quotas?.[kind]??5}</small></h3>${rows.map(r=>`<article class="hit-entry"><b>${esc(r.text.slice(0,80))}</b><span>${esc(labelOf(r.type)||'原文')} · ${Number(r.score||r.keyword_score||0).toFixed(2)}</span><p>${esc(r.text.slice(0,180))}</p><div class="row">${kind==='entity'||kind==='relation'?`<button data-graph-node="${esc(kind==='entity'?r.id:r.subject_id)}">在图谱中查看</button>`:''}${kind==='chunk'&&r.source_id?`<button data-source-record="${esc(r.source_id)}" class="secondary">查看原文</button>`:''}</div></article>`).join('')||'<p class="subtle">暂无命中</p>'}</section>`;
    }).join('');
    get('hits').querySelectorAll('[data-graph-node]').forEach(b=>b.onclick=async()=>{try{await selectEntityDetail(b.dataset.graphNode);}catch(error){status(error.message,true);}});
    get('hits').querySelectorAll('[data-source-record]').forEach(b=>b.onclick=()=>historyFor({id:b.dataset.sourceRecord}));
  }
  // 本体扩展的结果说明：告诉用户"这次是靠本体层级补到的"——命中了哪些类、带出了多少子类。
  // 没有这个说明，勾了开关也只是多几条结果，用户无法判断它到底做了什么。
  function expansionSummary(result){
    const expansion=result&&result.ontology_expansion;if(!expansion)return '';
    const terms=(expansion.terms||[]).join('、')||'—';
    const subclasses=(expansion.subclasses||[]).length;
    return ` · 本体扩展：命中 ${terms}${subclasses?`，带出 ${subclasses} 个子类`:''}`;
  }
  async function runSearch(){
    if(!current)return;
    const serial=++ui.request,p=current,started=performance.now();
    const body={...scope(),valid_at:null,query:get('query').value,retrieval_mode:get('search-mode').value,
      // 本体扩展（默认关闭）：勾上后检索会顺着本体的父子关系补召回 —— 查父类名也能命中子类实例。
      // 它只补召回，不放宽可见范围（权限/时态仍由 scope 决定）；关闭时读路径与旧行为一致。
      ontology_expansion:Boolean(get('ontology-expansion')?.checked),
      k_entities:5,k_chunks:5,k_relations:5};
    get('search-summary').textContent='读取所选范围…';
    get('hits').textContent='';
    const done=kgTime('search');
    const result=await api(endpoint('/search'),body);
    done({mode:result.active_mode,candidates:result.candidate_count,hits:result.hits.length});
    if(serial!==ui.request||p!==current)return;
    delete result.nodes;delete result.edges;
    renderHits(result);
    const modeLabel={hybrid:'混合',semantic:'语义',keyword:'关键词'}[result.active_mode]||result.active_mode;
    get('search-summary').textContent=`${modeLabel}${result.degraded?' · 已降级':''} · ${result.candidate_count} 候选 · ${result.hits.length} 命中 · ${(performance.now()-started).toFixed(0)} ms${expansionSummary(result)}`;
    updateChip();
  }
  // 图谱渲染器的唯一实现，显式挂到 window（不再依赖非严格模式下的隐式全局赋值）。
  // workbench.js 的 #graph-expand / #load-graph / 证据条目三个入口都按名字引用这个槽位，
  // 见下方 act('graph-expand') 与 workbench.js 注释。
  const graphScope=()=>({...scope(),valid_at:null,
    entity_type:get('type-scope').value||null,
    predicate:get('predicate-scope').value||null});
  window.drawGraph=async(node=null,hops=1)=>{
    // node 省略时渲染当前 scope 的全图（后端 node_id=null 返回全 scope，见 explorer.py:16）
    const id=node||null;
    const project=current,scopeSnapshot=graphScope();
    const ticket=graphRequests.begin(project,scopeSnapshot,id);
    const done=kgTime('subgraph');
    try{
      let result;
      try{result=await scopedRead('/subgraph',{...scopeSnapshot,node_id:id,hops,
        attribute_mode:id?'expanded':'summary'});}
      catch(error){
        if(!graphRequests.isCurrent(ticket,current,graphScope(),id))return null;
        throw error;
      }
      if(!graphRequests.isCurrent(ticket,current,graphScope(),id))return null;
      done({node:id||'all',hops,nodes:result.nodes.length,edges:result.edges.length,server_ms:result.timing_ms?.total??'—'});
      renderGraph(result);
      // 开放本体发现的提示条要跟着图谱一起刷新：这原本只在 workbench.js 的旧 drawGraph 副本里做，
      // 收敛成单一份实现后带到这里，避免 discovery 模式下提示停留在旧状态。
      renderDiscoveryHint().catch(()=>{});
      return result;
    }finally{
      graphRequests.release(ticket);
    }
  };
  window.clearGraphWorkspace=({preserveSearchResults=false}={})=>{
    graphRequests.invalidate();invalidatePendingDetail();
    wb.epoch++;ui.graph=null;wb.nodes.clear();wb.chart?.clear();
    selectedEntityId='';get('graph-node').value='';get('graph-entity-choice').value='';get('graph-detail').replaceChildren();
    get('graph-summary').textContent='从左侧检索结果选择实体或关系';
    get('graph-type-buttons')?.remove();get('graph-timeline')?.remove();
    if(!preserveSearchResults){get('hits').replaceChildren();get('search-summary').textContent='';}
  };
  act('search',runSearch);act('draw-graph',()=>drawGraph(null,Number(get('graph-hops').value)));
  // 「展开邻域」与「绘制图谱」共用同一份渲染器：绑定从 workbench.js 移到这里，
  // 同一个按钮不再被两个文件各绑一次。没选中实体时给出明确提示，而不是等后端返回空图。
  act('graph-expand',async()=>{
    const id=get('graph-node').value;
    if(!id)throw Error('请先选择实体或点击图中的节点，再展开邻域。');
    const hops=Number(get('graph-hops').value);
    if(!await window.drawGraph(id,hops))return;
    status('已展开 '+hops+' 跳邻域');
  });
  act('apply-scope',async()=>{details.open=false;window.clearGraphWorkspace({preserveSearchResults:true});window.clearKnowledgeChat?.({notify:true});await Promise.all([options(),discoverMetadata()]);if(!get('tab-mindmap').classList.contains('hidden')&&get('mindmap-root').value)await get('draw-mindmap').onclick();});
  const previousAddFilter=get('add-filter')?.onclick;
  if(previousAddFilter)get('add-filter').onclick=async()=>{await previousAddFilter();if(!get('status').classList.contains('error'))await get('apply-scope').onclick();};
  act('reset-scope',async()=>{for(const id of ['known-at','filters','type-scope','predicate-scope'])get(id).value='';cb.conditions=[];renderCB();resetTimeline();window.clearGraphWorkspace({preserveSearchResults:true});window.clearKnowledgeChat?.({notify:true});await options();});
  act('build-index',async()=>{watch(await api(endpoint('/indexes/rebuild'),{}));status('索引任务已提交；可继续浏览图谱或使用关键词检索。');});
  // 原来只 dispatch restore：返回 series 里写的 zoom:1，而 fitGraph 之后本来就是 zoom:1，
  // 于是"点了没什么作用"（用户原话）。现在＝适应画布：重新铺开节点 + 缩放到刚好占满画布。
  act('graph-reset-view',async()=>{
    const chart=wb.chart;
    if(!chart||!ui.graph){status('图谱还没渲染：先点「重新渲染全图」，或从左侧检索结果里选一个实体/关系。');return;}
    chart.resize();fitGraph(chart);
    status('已适应画布：缩放比例与节点分布都重置为铺满画布；拖乱或缩没了都可以再点这个按钮。');
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
    if(!chart||!ui.graph)throw Error('先渲染图谱（点「重新渲染全图」或选择实体），再缩放。');
    const series=chart.getOption()?.series?.[0];
    const current=Number(series?.zoom)||1;
    const next=Math.min(4,Math.max(.2,current*factor));
    chart.setOption({series:[{zoom:next}]});
    status(`图谱缩放 ${(next*100).toFixed(0)}%（滚轮也可缩放，拖动可平移）`);
  }
  act('graph-zoom-in',()=>zoomGraph(1.18));
  act('graph-zoom-out',()=>zoomGraph(.85));
  get('detail-drawer-close').onclick=()=>{invalidatePendingDetail();get('graph-detail').replaceChildren();};
  get('query').onkeydown=e=>{if(e.key==='Enter')get('search').click();};
  // Ignore out-of-order project responses, and load graph immediately on selection.
  const priorChange=get('project').onchange;
  get('project').onchange=()=>{
    priorChange();ui.request++;ui.options=[];choices();ui.ontology=null;resetTimeline();window.clearGraphWorkspace();get('ontology-browser')?.replaceChildren();
    get('graph-entity-filter').value='';cb.facets=[];cb.conditions=[];renderCB();get('metadata-field-choice').innerHTML='<option value="">读取项目字段…</option>';get('metadata-value-choice').innerHTML='<option value="">先选择字段</option>';get('metadata-facet-summary').textContent='';
    get('project').title=get('project').selectedOptions[0]?.textContent||'';
    get('type-scope').value='';get('predicate-scope').value='';
    get('query').value='';get('graph-node').value='';updateChip();
    syncGeneratedIri();
    if(current){sessionStorage.setItem('knowledge-project',current);loadOntologyTerms().then(()=>Promise.all([options(),discoverMetadata()])).then(autoLoadGraph).catch(e=>status(e.message,true));}
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
      drawGraph(null,Number(get('graph-hops').value)||1)
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
      const latest=await window.loadOntology('');
      const id=latest?.id||'';
      if(!id||id===before)return false;
      await loadOntologyTerms();
      await Promise.all([options(),discoverMetadata()]);
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
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{
    const graphView=b.dataset.tab==='search';
    get('type-scope').style.display=graphView?'':'none';get('predicate-scope').style.display=graphView?'':'none';
    if(b.dataset.tab==='mindmap'&&current)options().catch(e=>status(e.message,true));
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
  // "发布完了，切回检索与交互图谱还是旧的，必须整页刷新"。事件由 ontology-workbench.js
  // 在发布成功后派发（ontology-workbench:published）。
  document.addEventListener('ontology-workbench:published',async event=>{
    if(!current)return;
    const versionId=event?.detail?.versionId||'';
    const short=versionId?versionId.slice(0,8)+'…':'';
    ui.ontology=null;ui.options=[];ui.request++;
    try{
      // 顺序有意义：先刷新本体术语缓存（labelOf/term 都依赖它），再取实体选项，
      // 最后才重画图——否则图上的类型名还是旧版本。
      await loadOntologyTerms();
      await Promise.all([options(),discoverMetadata()]);
      resetTimeline();
      // 旧图谱是按旧范围/旧本体渲染的，先清掉；检索页可见时立刻重画，不可见时
      // 由 autoLoadGraph()（tab 点击时触发）按最新状态补画。
      window.clearGraphWorkspace({preserveSearchResults:true});
      if(!get('tab-search').classList.contains('hidden'))await window.drawGraph(null,Number(get('graph-hops').value)||1);
      status(`本体新版本已发布${short?`（${short}）`:''}：类型下拉、时间轴与图谱已按新版本刷新。`);
    }catch(error){
      status(`新版本已发布，但这一页的检索/图谱刷新失败：${error.message}。可点「重新渲染全图」重试。`,true);
    }
  });
  // Structured ontology browser and versioned term additions, alongside Turtle/SPARQL.
  const ontologyPanel=get('tab-ontology').querySelector('.panel');
  const sourceHeading=ontologyPanel.querySelector('.ontology-editor-heading');
  const sourceDetails=make('details','','ontology-source-details');
  sourceDetails.innerHTML='<summary>高级：本体历史版本与 Turtle 源码</summary><p class="subtle">用于查看历史版本或直接编辑本体源码。日常新增、修改和停用术语请使用上方的结构化维护工具。</p>';
  ontologyPanel.insertBefore(sourceDetails,sourceHeading);
  sourceHeading.querySelector('h3').textContent='Turtle 源码编辑';
  for(const element of [sourceHeading,get('ontology-versions'),get('ontology-summary'),get('turtle'),get('save-ontology')])sourceDetails.appendChild(element);
  const structureHeading=make('div','<h2>本体结构维护</h2><p class="subtle">这里维护实体类、关系类型和属性类型，不修改具体实体或实体之间的关系。点击术语卡片上的“维护”可修改名称、说明和类型范围。</p>','ontology-structure-heading');
  ontologyPanel.insertBefore(structureHeading,sourceDetails);
  ontologyPanel.insertBefore(make('div','', 'ontology-browser'),sourceDetails);
  ontologyPanel.querySelector('.ontology-browser').id='ontology-browser';
  const termForm=make('section','<header class="ontology-form-heading"><div><h2>新增本体术语</h2><p>创建新的实体类、关系类型或实体属性。保存后会形成新的本体版本，不会覆盖历史版本。</p></div></header><div class="columns"><label>术语种类<select id="term-kind"><option value="class">实体类</option><option value="relation">关系类型</option><option value="attribute">实体属性</option></select></label><label>英文名（选填，留空就用中文名）<input id="term-label" placeholder="例如 Merchant"></label><label>中文名称（必填）<input id="term-label-zh" maxlength="200" placeholder="例如 商户"></label></div><div class="columns"><label class="ontology-term-iri">系统生成的标识 IRI（自动生成，不用手写）<input id="term-uri" placeholder="填写名称后显示" readonly></label><p class="ontology-term-iri-note"><strong>不用手写，也不用拼写：</strong>先填名称，这一格只是预览；最终 IRI 由后端按名称统一生成并固定下来，创建后不会再变。支持中文、阿拉伯文等 Unicode 字符。</p></div><div class="columns"><label>父类（仅实体类）<select id="term-parent"></select></label><label><span id="term-domain-label">适用实体类型（domain）</span><select id="term-domain"></select><small id="term-domain-help"></small></label><label><span id="term-range-label">目标实体类型（range）</span><select id="term-range"></select><small id="term-range-help"></small></label></div><button id="add-ontology-term">新增并保存为新版本</button><p class="subtle">这里只维护本体结构。具体实体和实体关系请在“交互图谱”中点击对应节点或连线进行维护。最终 IRI 由后端生成并校验，创建后保持稳定。</p>','ontology-term-create');
  ontologyPanel.insertBefore(termForm,sourceDetails);
  get('term-kind').closest('label').classList.add('ontology-kind-source');
  const kindTabs=make('div','<button type="button" data-term-kind-choice="class">新增实体类</button><button type="button" data-term-kind-choice="relation" class="secondary">新增关系类型</button><button type="button" data-term-kind-choice="attribute" class="secondary">新增实体属性</button>','ontology-kind-tabs');
  termForm.querySelector('.ontology-form-heading').after(kindTabs);
  get('term-uri').readOnly=true;get('term-uri').title='后端生成结果的预览，无需手写';
  const createCopy={class:['新增实体类','定义一类事物，可选择一个父类。'],relation:['新增关系类型','定义实体之间的连接方式；起点和终点类型可以不限制或勾选多个。'],attribute:['新增实体属性','定义实体可填写的数据字段及其数据类型。']};
  function readableIriSegment(value){return [...value.normalize('NFC').trim()].map(character=>{if(/[\p{L}\p{N}\p{M}._~-]/u.test(character))return character;if(/\s/u.test(character))return '-';return encodeURIComponent(character);}).join('').replace(/-+/g,'-');}
  function syncGeneratedIri(){const label=get('term-label-zh').value.trim()||get('term-label').value.trim(),segment=readableIriSegment(label);get('term-uri').value=segment&&current?`urn:knowledge:ontology:${current}:${segment}`:'';}
  function chooseTermKind(kind){get('term-kind').value=kind;kindTabs.querySelectorAll('button').forEach(button=>button.classList.toggle('secondary',button.dataset.termKindChoice!==kind));const copy=createCopy[kind];termForm.querySelector('.ontology-form-heading h2').textContent=copy[0];termForm.querySelector('.ontology-form-heading p').textContent=copy[1]+' 保存后会形成新的本体版本。';configureTermForm();}
  kindTabs.querySelectorAll('button').forEach(button=>button.onclick=()=>chooseTermKind(button.dataset.termKindChoice));
  get('term-label-zh').addEventListener('input',()=>{if(!get('term-label').value.trim())get('term-label').value=get('term-label-zh').value.trim();syncGeneratedIri();});
  get('term-label').addEventListener('input',syncGeneratedIri);

  // 创建悬浮框替代折叠面板
  const modalOverlay=make('div','','ontology-modal-overlay');
  modalOverlay.innerHTML=`
    <div class="ontology-modal">
      <div class="ontology-modal-header">
        <h3 class="ontology-modal-title">修改术语</h3>
        <button class="ontology-modal-close">×</button>
      </div>
      <div class="ontology-modal-body">
        <div class="term-modal-header">
          <div class="term-modal-icon class" id="modal-term-icon">📦</div>
          <div class="term-modal-info">
            <h3 id="modal-term-label"></h3>
            <code id="modal-term-uri"></code>
          </div>
        </div>
        <div class="form-row">
          <div>
            <label>显示名称</label>
            <input type="text" id="edit-term-label">
          </div>
          <div>
            <label>中文名称</label>
            <input type="text" id="edit-term-label-zh" maxlength="200">
          </div>
        </div>
        <div class="form-row">
          <div>
            <label>说明</label>
            <input type="text" id="edit-term-description">
          </div>
          <div></div>
        </div>
        <div class="form-row">
          <div>
            <label>父类</label>
            <select id="edit-term-parent"></select>
          </div>
          <div>
            <label id="edit-term-domain-label">起点实体类型</label>
            <select id="edit-term-domain"></select>
          </div>
        </div>
        <div class="form-row">
          <div>
            <label id="edit-term-range-label">目标实体类型</label>
            <select id="edit-term-range"></select>
          </div>
          <div></div>
        </div>
        <div id="term-impact" class="impact-summary" style="display:none">
          <h4>变更影响</h4>
          <div class="impact-stats">
            <div class="impact-stat">
              <div class="impact-stat-value" id="impact-entities">-</div>
              <div class="impact-stat-label">受影响实体</div>
            </div>
            <div class="impact-stat">
              <div class="impact-stat-value" id="impact-relations">-</div>
              <div class="impact-stat-label">受影响关系</div>
            </div>
            <div class="impact-stat">
              <div class="impact-stat-value" id="impact-attributes">-</div>
              <div class="impact-stat-label">受影响属性</div>
            </div>
          </div>
          <p class="impact-secondary" id="impact-secondary"></p>
          <p class="impact-note" id="impact-note"></p>
          <div id="impact-details" style="margin-top:10px"></div>
        </div>
        <div class="confirm-row">
          <input type="checkbox" id="retire-term-confirm">
          <label for="retire-term-confirm">我已查看变更影响，确认要停用此术语</label>
        </div>
      </div>
      <div class="ontology-modal-footer">
        <button class="btn secondary" id="modal-cancel">取消</button>
        <button class="btn" id="update-ontology-term">保存修改</button>
        <button class="btn-retire" id="retire-ontology-term" disabled>停用</button>
      </div>
    </div>
  `;
  document.body.appendChild(modalOverlay);

  // 悬浮框关闭逻辑
  const closeModal=()=>{modalOverlay.classList.remove('active');selectedTerm=null;};
  modalOverlay.querySelector('.ontology-modal-close').onclick=closeModal;
  get('modal-cancel').onclick=closeModal;
  modalOverlay.onclick=(e)=>{if(e.target===modalOverlay)closeModal();};
  document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&modalOverlay.classList.contains('active'))closeModal();});

  // 停用按钮的启用/禁用逻辑
  const retireConfirm=get('retire-term-confirm');
  const retireBtn=get('retire-ontology-term');
  retireConfirm.onchange=()=>{retireBtn.disabled=!retireConfirm.checked;};

  // 这是在 app.js:58 的 loadOntology 外面包一层（先跑原版，再补本体结构面板的术语卡片），
  // 改写成 window.loadOntology 显式挂载：原先的隐式全局赋值在非严格模式下才成立，
  // 将来给本文件加 'use strict' / 改成模块就会静默失效（下拉换版本后面板不刷新且不报错）。
  const priorLoad=loadOntology;
  window.loadOntology=async(id='')=>{const p=current,result=await priorLoad(id);if(p!==current)return result;ui.ontology=result;
    if(!result){get('ontology-browser').innerHTML='<p class="subtle">本项目尚未发布本体。开放本体发现模式请先累计候选，再到「本体建模层」生成并发布本体版本（要手工设计本体则用「本体建模层」）；仅文档检索模式不产生图谱本体。</p>';return result;}
    const classes=result.summary.classes||[],classLabels=new Map(classes.flatMap(c=>[[c.id,ontoName(c)],[c.name,ontoName(c)]]));
    const className=id=>classLabels.get(id)||term(id)||'未指定';
    const datatypeName=id=>new Map(datatypes).get(id)||term(id)||'未指定';
    const maintenance=(kind,item,detail)=>`<div class="ontology-term-card"><b>${esc(ontoName(item))}</b><span>${detail}</span><details class="ontology-technical"><summary>查看技术标识</summary><code>${esc(item.name)}</code><code>${esc(item.id)}</code></details><button class="secondary" data-maintain-kind="${kind}" data-maintain-uri="${esc(item.id)}">维护</button></div>`;
    get('ontology-browser').innerHTML=`<div class="ontology-columns"><section><h3>实体类与继承</h3>${classes.map(c=>maintenance('class',c,(c.parents||[]).length?'父类：'+(c.parents||[]).map(className).map(esc).join('、'):'层级：根类')).join('')}</section><section><h3>关系定义</h3>${(result.summary.relations||[]).map(r=>maintenance('relation',r,`<em>起点类型</em> ${esc((r.domain||[]).map(className).join('、')||'不限')} <i>→</i> <em>终点类型</em> ${esc((r.range||[]).map(className).join('、')||'不限')}`)).join('')}</section><section><h3>实体属性定义</h3>${(result.summary.attributes||[]).map(a=>maintenance('attribute',a,`<em>适用实体</em> ${esc((a.domain||[]).map(className).join('、')||'不限')} <i>·</i> <em>数据类型</em> ${esc((a.range||[]).map(datatypeName).join('、')||'未指定')}`)).join('')||'<p class="subtle">尚未定义属性</p>'}</section></div>`;
    configureTermForm();
    get('ontology-browser').querySelectorAll('[data-maintain-uri]').forEach(button=>button.onclick=()=>selectTerm(button.dataset.maintainKind,button.dataset.maintainUri));
    return result;};
  const datatypes=[['http://www.w3.org/2000/01/rdf-schema#Literal','任意字面量'],['http://www.w3.org/2001/XMLSchema#string','字符串'],['http://www.w3.org/2001/XMLSchema#boolean','布尔值'],['http://www.w3.org/2001/XMLSchema#integer','整数'],['http://www.w3.org/2001/XMLSchema#decimal','小数'],['http://www.w3.org/2001/XMLSchema#double','浮点数'],['http://www.w3.org/2001/XMLSchema#date','日期'],['http://www.w3.org/2001/XMLSchema#dateTime','日期时间']];
  // 空列表时不能只留一个空白下拉框——用户看到"没值的框"，分不清是坏了还是本来就空。
  // 这里给出明确说明，并把"空值"写成一句人话：同一个空选项在父类/domain/range 里
  // 含义完全不同，所以按用途传入不同文案，别让人猜"不指定"到底不指定什么。
  const classOptions=(emptyLabel='不指定（不选父类＝作为顶层类）')=>{
    const classes=ui.ontology?.summary?.classes||[];
    if(!classes.length)return '<option value="">（本项目还没有实体类，请先在上方「新增实体类」创建）</option>';
    return `<option value="">${emptyLabel}</option>`
      +classes.map(c=>`<option value="${esc(c.id)}">${esc(ontoName(c))}</option>`).join('');
  };
  const selectedValues=select=>[...select.selectedOptions].map(option=>option.value).filter(Boolean);
  function configureChecklist(select,selected){
    let list=select.parentElement.querySelector(`.ontology-checklist[data-for="${select.id}"]`);
    if(!list){list=make('div','','ontology-checklist');list.dataset.for=select.id;select.after(list);}
    select.classList.add('ontology-checklist-source');
    select.hidden=false;list.hidden=true;
    list.innerHTML=[...select.options].filter(option=>option.value).map(option=>`<label><input type="checkbox" value="${esc(option.value)}"${selected.includes(option.value)?' checked':''}><span>${esc(option.textContent)}</span></label>`).join('')||'<p class="subtle">请先创建实体类。</p>';
    list.querySelectorAll('input').forEach(input=>input.onchange=()=>{const option=[...select.options].find(item=>item.value===input.value);if(option)option.selected=input.checked;});
    select.hidden=true;list.hidden=false;
  }
  function configureFields(kind,prefix,values={}){
    const parent=get(prefix+'term-parent'),domain=get(prefix+'term-domain'),range=get(prefix+'term-range'),multiple=kind==='relation';
    parent.innerHTML=classOptions();
    domain.innerHTML=classOptions('不指定（不限制能用在哪类实体上）');
    range.innerHTML=kind==='attribute'
      ?'<option value="">不指定（不限定数据类型）</option>'+datatypes.map(([id,label])=>`<option value="${id}">${label}</option>`).join('')
      :classOptions('不指定（不限制另一端类型）');
    parent.disabled=kind!=='class';domain.disabled=kind==='class';range.disabled=kind==='class';
    for(const select of [domain,range]){select.multiple=multiple;select.classList.remove('ontology-checklist-source');select.size=multiple?Math.min(6,Math.max(3,(ui.ontology?.summary?.classes||[]).length)):1;const list=select.parentElement.querySelector(`.ontology-checklist[data-for="${select.id}"]`);if(list)list.hidden=true;select.hidden=false;}
    parent.value=values.parent||'';
    const domains=values.domains||[values.domain].filter(Boolean),ranges=values.ranges||[values.range].filter(Boolean);
    [...domain.options].forEach(option=>option.selected=domains.includes(option.value));
    [...range.options].forEach(option=>option.selected=ranges.includes(option.value));
    if(multiple){configureChecklist(domain,domains);configureChecklist(range,ranges);}
    const rangeLabel=get(prefix+'term-range-label');if(rangeLabel)rangeLabel.textContent=kind==='attribute'?'数据类型':kind==='relation'?'允许的终点实体类型（可多选）':'目标实体类型（range）';
    const domainLabel=get(prefix+'term-domain-label');if(domainLabel)domainLabel.textContent=kind==='attribute'?'适用实体类型':kind==='relation'?'允许的起点实体类型（可多选）':'适用实体类型（domain）';
    // 空态/不适用态：整块字段（含标签）收起来，只留一行说明它是什么、什么时候才有用。
    // 之前只把 <select> 设 hidden，但全局 select 样式会把它重新显示出来，
    // 结果出现"「不适用」+ 灰掉的下拉框"，和旁边可选的「不指定」长得几乎一样，极易误读。
    const hasClasses=(ui.ontology?.summary?.classes||[]).length>0;
    const helpFor=(select,suffix,text)=>{let node=get(prefix+'term-'+suffix+'-help');if(!node){node=document.createElement('small');node.id=prefix+'term-'+suffix+'-help';select.after(node);}node.textContent=text;return node;};
    const note=(select,text)=>{
      const field=select.closest('label')||select;
      let node=field.parentElement?.querySelector(`.ontology-field-note[data-for="${select.id}"]`);
      if(!node){node=document.createElement('p');node.className='subtle ontology-field-note';node.dataset.for=select.id;field.after(node);}
      node.textContent=text;node.hidden=!text;return node;
    };
    // 整块字段的显示/隐藏：包住标签，避免"标签还在、控件被全局样式弹回来"。
    const showField=(select,visible)=>{const field=select.closest('label')||select;select.hidden=!visible;field.hidden=!visible;};
    if(kind==='class'){
      // 实体类不需要 Domain/Range：整块收起，只留一行解释，避免"这里是不是坏了"。
      showField(domain,false);showField(range,false);
      helpFor(domain,'domain','');helpFor(range,'range','');
      note(domain,'实体类不适用 Domain / Range：它们只描述「关系或属性能用在哪类实体上、指向什么」，对实体类本身没有意义。新增实体类只需要填名称、选父类。');
    }else if(!hasClasses){
      showField(domain,false);showField(range,false);
      helpFor(domain,'domain','');helpFor(range,'range','');
      note(domain,'还没有实体类可以勾选：请先在上方「新增实体类」创建至少一个实体类，再回来为关系/属性限定适用范围。');
    }else{
      showField(domain,true);showField(range,true);
      note(domain,'');note(range,'');
      helpFor(domain,'domain','domain＝这条关系/属性可以挂在哪些实体类上。不选表示不限。');
      helpFor(range,'range',kind==='attribute'?'range＝这个属性存什么类型的数据。':'range＝关系的另一端允许是哪些实体类。不选表示不限。');
    }
  }
  // 本体还没读进来时也要能把表单配好（显示"还没有实体类"的空态说明），
  // 否则抽屉一打开就是两个空白下拉框——用户分不清是坏了还是本来就空。
  function configureTermForm(){configureFields(get('term-kind').value,'');}
  get('term-kind').onchange=configureTermForm;
  chooseTermKind('class');
  act('add-ontology-term',async()=>{if(!ui.ontology)throw Error('请先加载本体');const kind=get('term-kind').value;await api(endpoint('/ontology/terms'),{kind,label:get('term-label').value,label_zh:get('term-label-zh').value,parent:kind==='class'?get('term-parent').value:'',domain:kind==='attribute'?get('term-domain').value:'',range:kind==='attribute'?get('term-range').value:'',domains:kind==='relation'?selectedValues(get('term-domain')):[],ranges:kind==='relation'?selectedValues(get('term-range')):[],expected_ontology_id:ui.ontology.id});await get('load-ontology').onclick();status('后端已生成 IRI 并保存新本体版本；已有知识保留原本体版本。');});
  // An IRI is immutable. Editing descriptions and constraints creates a new ontology version;
  // retiring removes it only from that new version after an explicit impact review.
  let selectedTerm=null;
  async function selectTerm(kind,uri){
    const collection=ui.ontology.summary[{class:'classes',relation:'relations',attribute:'attributes'}[kind]];
    const item=collection.find(x=>x.id===uri);if(!item)return;
    selectedTerm={kind,uri};

    // 更新悬浮框内容
    const iconClass={class:'class',relation:'relation',attribute:'attribute'}[kind];
    const iconEmoji={class:'📦',relation:'🔗',attribute:'🏷️'}[kind];
    const kindLabel={class:'实体类',relation:'关系',attribute:'实体属性'}[kind];

    get('modal-term-icon').className='term-modal-icon '+iconClass;
    get('modal-term-icon').textContent=iconEmoji;
    get('modal-term-label').textContent=ontoName(item)+' ('+kindLabel+')';
    get('modal-term-uri').textContent=uri;
    get('edit-term-label').value=item.label;
    get('edit-term-label-zh').value=item.label_zh||'';
    get('edit-term-description').value=item.description||'';
    configureFields(kind,'edit-',{parent:item.parents?.[0],domains:item.domain||[],ranges:item.range||[]});

    // 重置状态
    get('retire-term-confirm').checked=false;
    retireBtn.disabled=true;
    get('term-impact').style.display='none';
    get('term-impact').innerHTML='<h4>变更影响</h4><div class="impact-stats"><div class="impact-stat"><div class="impact-stat-value" id="impact-entities">-</div><div class="impact-stat-label">受影响实体</div></div><div class="impact-stat"><div class="impact-stat-value" id="impact-relations">-</div><div class="impact-stat-label">受影响关系</div></div><div class="impact-stat"><div class="impact-stat-value" id="impact-attributes">-</div><div class="impact-stat-label">受影响属性</div></div></div><p class="impact-secondary" id="impact-secondary"></p><p class="impact-note" id="impact-note"></p><div id="impact-details" style="margin-top:10px"></div>';

    // 打开悬浮框
    modalOverlay.classList.add('active');

    // 加载影响分析
    try{const impact=await api(endpoint('/ontology/term-impact')+'?uri='+encodeURIComponent(uri),undefined,'GET');
      if(selectedTerm?.uri!==uri)return;selectedTerm.impact=impact;
      get('term-impact').style.display='block';
      // 分类计数：实体类停用时"关系"常是大头（该类型只是关系端点），必须分开看
      const kc=impact.kind_counts||{entity:0,relation:0,attribute:0};
      get('impact-entities').textContent=kc.entity??0;
      get('impact-relations').textContent=kc.relation??0;
      get('impact-attributes').textContent=kc.attribute??0;
      get('impact-secondary').innerHTML=`约束引用 ${impact.constraint_count} 处 · 待审核映射 ${impact.pending_review_count} 个`;
      // 停用语义必须写在界面上：只摘定义、不删历史（记录 retained，时间旅行仍可见）
      const linked=impact.linked_relation_count||0;
      get('impact-note').innerHTML=impact.record_count
        ? `停用后：该类型从本体移除，上述 <b>${impact.record_count}</b> 条历史知识<b>保留可查</b>（时间旅行仍可见），不会删除任何数据；此后不能再用该类型写入新知识。`
          + (linked?`<br>其中 <b>${linked}</b> 条关系是<b>端点连带</b>受影响（该类型是它们的端点实体类型），不是关系类型本身被停用。`:'')
        : '当前没有知识记录使用该类型，可直接停用。';
      const preview=impact.record_preview||[];
      if(preview.length){
        const label={entity:'实体',relation:'关系',attribute:'属性'};
        const byKind={entity:[],relation:[],attribute:[]};
        preview.forEach(x=>{(byKind[x.kind]||(byKind[x.kind]=[])).push(x)});
        const groups=Object.entries(byKind).filter(([,v])=>v.length)
          .map(([k,v])=>`<div class="impact-detail-group"><b>${label[k]||k}（${v.length}）</b><span>${v.map(x=>esc(x.text||x.id)+(x.via==='endpoint'?'<em class="impact-via">端点连带</em>':'')).join('、')}</span></div>`).join('');
        get('impact-details').innerHTML=`<details><summary style="font-size:12px;color:#216952;cursor:pointer">查看受影响明细</summary>${groups}${impact.record_ids_truncated?'<p class="subtle" style="font-size:11px">…仅展示前 50 条</p>':''}<details style="margin-top:8px"><summary style="font-size:11px;color:#829087;cursor:pointer">原始记录 ID</summary><pre style="margin-top:8px;padding:10px;background:#f8faf7;border-radius:6px;font-size:11px;max-height:120px;overflow:auto">${esc((impact.record_ids||[]).join('\n'))}</pre></details></details>`;
      }
    }catch(error){
      get('term-impact').style.display='block';
      get('impact-entities').textContent='错误';
      get('impact-relations').textContent='';
      get('impact-attributes').textContent='';
      get('impact-secondary').textContent='';
      get('impact-note').textContent='';
      get('impact-details').innerHTML='<p style="color:#dc2626;font-size:12px">影响分析失败：'+esc(error.message)+'</p>';
    }
  }
  act('update-ontology-term',async()=>{if(!selectedTerm)throw Error('请先选择术语');const kind=selectedTerm.kind;
    await api(endpoint('/ontology/term')+'?uri='+encodeURIComponent(selectedTerm.uri),{label:get('edit-term-label').value,label_zh:get('edit-term-label-zh').value,description:get('edit-term-description').value,parent:kind==='class'?get('edit-term-parent').value:'',domain:kind==='attribute'?get('edit-term-domain').value:'',range:kind==='attribute'?get('edit-term-range').value:'',domains:kind==='relation'?selectedValues(get('edit-term-domain')):[],ranges:kind==='relation'?selectedValues(get('edit-term-range')):[],expected_ontology_id:ui.ontology.id},'PUT');
    closeModal();await get('load-ontology').onclick();status('本体修改已保存为新版本；旧知识未迁移。');});
  act('retire-ontology-term',async()=>{if(!selectedTerm)throw Error('请先选择术语');if(!get('retire-term-confirm').checked)throw Error('请先查看影响并勾选确认');
    const result=await api(endpoint('/ontology/term-retire')+'?uri='+encodeURIComponent(selectedTerm.uri),{expected_ontology_id:ui.ontology.id,confirm_references:true});
    closeModal();await get('load-ontology').onclick();status(`术语已从新本体版本停用；${result.impact.record_count} 条旧知识仍保留原本体版本。`);});
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
  for(const id of ['apply-scope','reset-scope'])get(id).addEventListener('click',()=>{if(!get('tab-dashboard').classList.contains('hidden'))refreshDashboard();});
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
  oldToggle.closest('label').classList.add('hidden');
  const descriptions={ontology:'按当前项目本体抽取、校验和实体融合，合法知识进入正式图谱。',discovery:'不要求预设本体；Semantica 自由抽取并持续累计候选，不直接写入正式图谱。',documents:'只保存原文、切片和向量，不抽取实体、关系或属性。'};
  mode.onchange=()=>{
    help.textContent=descriptions[mode.value];oldToggle.checked=mode.value!=='documents';
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
