/* Linked graph workspace: preserve existing editing/governance controls. */
(() => {
  const ui = {request:0, options:[], ontology:null, graph:null, busy:false};
  const get = id => document.getElementById(id);
  const make = (tag, html, className='') => {const e=document.createElement(tag);e.innerHTML=html;e.className=className;return e;};
  const act = (id, fn) => {get(id).onclick=async()=>{get(id).disabled=true;try{await fn();}catch(e){status(e.message,true);}finally{get(id).disabled=false;}};};
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

  // Put the old graph and search controls together without duplicating their IDs.
  const graphTab=get('tab-graph'), searchTab=get('tab-search');
  const graphPanel=graphTab.querySelector('.panel');
  graphPanel.classList.add('graph-stage');
  const resultRail=make('section','<div class="rail-title">检索与证据 <span>随结果联动图谱</span></div>','result-rail');
  const searchPanel=searchTab.querySelector('.panel');
  searchPanel.querySelector('h2').remove();
  searchPanel.appendChild(make('div','<label>检索方式<select id="explore-mode"><option value="keyword">关键词 · 即时</option><option value="semantic">语义 · 需要索引</option></select></label><label>扩展范围<select id="explore-hops"><option value="0">仅命中实体</option><option value="1" selected>命中 + 1 跳</option><option value="2">命中 + 2 跳</option></select></label>','search-settings'));
  searchPanel.appendChild(make('div','<button id="build-index" class="secondary">构建语义索引</button><button id="clear-search" class="secondary">返回全图</button>','row'));
  resultRail.append(searchPanel, get('hits'));
  const inspector=make('section','<div class="rail-title">节点 / 关系详情</div><p class="subtle">点击节点或连线查看来源、版本与有效期。</p>','inspector-rail');
  inspector.appendChild(get('graph-detail'));
  const grid=make('div','','explore-grid');
  grid.append(resultRail,graphPanel,inspector);
  graphTab.appendChild(grid);
  get('graph-node').type='hidden';
  const controls=make('div','<input id="graph-entity-filter" placeholder="搜索实体名称…" aria-label="筛选图实体"><select id="graph-entity-choice" aria-label="选择图实体"><option value="">选择实体查看邻域</option></select>','row entity-controls');
  get('graph-node').parentElement.prepend(controls);
  get('draw-graph').textContent='全图 / 复位';
  graphPanel.querySelector('.row').appendChild(make('span','<button id="graph-focus" class="secondary">专注图谱</button>'));
  graphPanel.querySelector('.row').after(make('div','<label class="check"><input id="graph-labels" type="checkbox" role="switch" checked>显示节点中文 / 名称</label>','graph-display-switch'));
  get('graph-labels').onchange=()=>{const show=get('graph-labels').checked;wb.chart?.setOption({series:[{label:{show},emphasis:{label:{show}}}]});};
  document.querySelector('[data-tab="search"]').textContent='图谱检索';
  document.querySelector('[data-tab="graph"]').classList.add('hidden');
  titles.search='图谱检索'; titles.graph='图谱检索';
  const oldShowTab=showTab;
  showTab=name=>{oldShowTab(name==='search'?'graph':name);if(['search','graph'].includes(name))document.querySelector('[data-tab="search"]').classList.add('active');};
  document.querySelector('[data-tab="search"]').onclick=()=>{oldShowTab('graph');document.querySelector('[data-tab="search"]').classList.add('active');};

  // ── Timeline scrubber ──
  const uiTimeline={min:null,max:null,buckets:[],playTimer:null,playing:false,dragging:false,debounce:null};
  // datetime-local holds LOCAL wall time; ISO/UTC would shift by the timezone offset.
  const pad2=n=>String(n).padStart(2,'0');
  const localInput=d=>`${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  function createTimeline(){
    let el=get('graph-timeline');if(el)return el;
    el=make('div','','graph-timeline');el.id='graph-timeline';
    el.innerHTML='<div class="tl-controls"><button class="tl-play" aria-label="播放时间线">▶</button><span class="tl-time">—</span></div><div class="tl-track" role="slider" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" tabindex="0"><div class="tl-density"></div><div class="tl-playhead"></div></div><div class="tl-controls"><button class="tl-now secondary">现在</button></div>';
    const track=el.querySelector('.tl-track');
    const toTime=e=>{const rect=track.getBoundingClientRect();const ratio=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));return new Date(uiTimeline.min.getTime()+ratio*(uiTimeline.max-uiTimeline.min));};
    const setTime=d=>{get('known-at').value=localInput(d);updateTimelinePlayhead();clearTimeout(uiTimeline.debounce);uiTimeline.debounce=setTimeout(()=>linkedSearch().catch(err=>status(err.message,true)),180);};
    track.addEventListener('pointerdown',e=>{if(!uiTimeline.min)return;track.setPointerCapture(e.pointerId);uiTimeline.dragging=true;setTime(toTime(e));});
    track.addEventListener('pointermove',e=>{if(!uiTimeline.dragging||!uiTimeline.min)return;setTime(toTime(e));});
    track.addEventListener('pointerup',()=>{uiTimeline.dragging=false;});
    track.addEventListener('click',e=>{if(!uiTimeline.min||uiTimeline.dragging)return;setTime(toTime(e));});
    track.addEventListener('keydown',e=>{
      if(!uiTimeline.min)return;
      const step=(uiTimeline.max-uiTimeline.min)/100;
      const cur=get('known-at').value?new Date(get('known-at').value).getTime():uiTimeline.max.getTime();
      if(e.key==='ArrowRight'||e.key==='ArrowUp'){e.preventDefault();setTime(new Date(Math.min(cur+step,uiTimeline.max)));}
      if(e.key==='ArrowLeft'||e.key==='ArrowDown'){e.preventDefault();setTime(new Date(Math.max(cur-step,uiTimeline.min)));}
    });
    el.querySelector('.tl-play').onclick=()=>{uiTimeline.playing?stopPlayback():startPlayback();};
    el.querySelector('.tl-now').onclick=()=>{stopPlayback();get('known-at').value='';updateTimelinePlayhead();linkedSearch().catch(err=>status(err.message,true));};
    return el;
  }
  function updateTimelinePlayhead(){
    const el=get('graph-timeline');if(!el||!uiTimeline.min)return;
    const known=get('known-at').value,track=el.querySelector('.tl-track'),playhead=el.querySelector('.tl-playhead'),timeLabel=el.querySelector('.tl-time');
    if(!known){playhead.style.display='none';timeLabel.textContent='现在';track.setAttribute('aria-valuenow','100');return;}
    const t=new Date(known).getTime(),range=uiTimeline.max-uiTimeline.min||1;
    const pct=Math.max(0,Math.min(100,(t-uiTimeline.min.getTime())/range*100));
    playhead.style.display='';playhead.style.left=pct+'%';
    timeLabel.textContent=known.replace('T',' ').slice(0,16);
    track.setAttribute('aria-valuenow',String(Math.round(pct)));
  }
  function renderDensityBars(){
    const el=get('graph-timeline');if(!el)return;
    const c=el.querySelector('.tl-density');
    if(!uiTimeline.buckets.length){c.innerHTML='';el.querySelector('.tl-time').textContent='暂无时间数据';el.classList.add('tl-disabled');return;}
    el.classList.remove('tl-disabled');
    const mx=Math.max(...uiTimeline.buckets,1);
    c.innerHTML=uiTimeline.buckets.map(v=>`<div style="height:${Math.max(2,v/mx*100)}%"></div>`).join('');
  }
  function startPlayback(){
    if(!uiTimeline.min)return;uiTimeline.playing=true;
    const btn=get('graph-timeline').querySelector('.tl-play');btn.textContent='❚❚';btn.setAttribute('aria-label','暂停时间线');
    const step=(uiTimeline.max-uiTimeline.min)/60;
    let cur=get('known-at').value?new Date(get('known-at').value).getTime():uiTimeline.min.getTime();
    (function tick(){cur+=step;if(cur>=uiTimeline.max.getTime()){cur=uiTimeline.max.getTime();stopPlayback();}get('known-at').value=localInput(new Date(cur));updateTimelinePlayhead();linkedSearch().catch(err=>status(err.message,true));if(uiTimeline.playing)uiTimeline.playTimer=setTimeout(tick,500);})();
  }
  function stopPlayback(){
    uiTimeline.playing=false;clearTimeout(uiTimeline.playTimer);
    const el=get('graph-timeline');if(!el)return;
    const btn=el.querySelector('.tl-play');btn.textContent='▶';btn.setAttribute('aria-label','播放时间线');
  }
  function resetTimeline(){
    stopPlayback();uiTimeline.min=null;uiTimeline.max=null;uiTimeline.buckets=[];
    const el=get('graph-timeline');if(!el)return;
    el.querySelector('.tl-density').innerHTML='';el.querySelector('.tl-time').textContent='—';
    el.querySelector('.tl-playhead').style.display='none';el.classList.remove('tl-disabled');
  }

  // A searchable native select, not an internal record ID input.
  const select=get('mindmap-root');
  select.parentElement.before(make('label','<span>筛选名称 / 类型</span><input id="mindmap-filter" placeholder="输入名称缩小候选范围">'));
  const oldMindmap=get('draw-mindmap').onclick;
  get('draw-mindmap').onclick=async()=>{if(!select.value){status('当前筛选范围内没有可选实体',true);return;}await oldMindmap();wb.tree?.setOption({series:[{initialTreeDepth:1,label:{position:'right',align:'left',width:170,overflow:'truncate',fontSize:11},leaves:{label:{position:'right',align:'left',width:170,overflow:'truncate',fontSize:11}}}]});};
  function choices(){
    for(const [selectId,filterId] of [['mindmap-root','mindmap-filter'],['graph-entity-choice','graph-entity-filter']]){
      const previous=get(selectId).value, query=get(filterId).value.trim().toLocaleLowerCase();
      const rows=ui.options.filter(r=>(r.text+' '+labelOf(r.type)+' '+term(r.type)).toLocaleLowerCase().includes(query));
      get(selectId).innerHTML=(selectId==='graph-entity-choice'?'<option value="">选择实体</option>':'')+rows.map(r=>`<option value="${esc(r.id)}" title="${esc(term(r.type))}">${esc(r.text)} · ${esc(labelOf(r.type))}</option>`).join('');
      if(rows.some(r=>r.id===previous))get(selectId).value=previous;
    }
  }
  get('mindmap-filter').oninput=choices;
  let entityTimer;
  get('graph-entity-filter').oninput=()=>{choices();clearTimeout(entityTimer);entityTimer=setTimeout(()=>{get('query').value=get('graph-entity-filter').value;get('explore-mode').value='keyword';linkedSearch().catch(e=>status(e.message,true));},300);};
  get('graph-entity-filter').onkeydown=e=>{if(e.key==='Enter'){clearTimeout(entityTimer);get('query').value=get('graph-entity-filter').value;get('explore-mode').value='keyword';linkedSearch().catch(e=>status(e.message,true));}};
  get('graph-entity-choice').onchange=async()=>{clearTimeout(entityTimer);const id=get('graph-entity-choice').value;if(!id)return;get('graph-node').value=id;ui.request++;const hops=Number(get('graph-hops').value);try{await drawGraph(id,hops);const row=wb.nodes.get(id);if(row){inspect(row);status('已定位：'+row.text+' · 展示 '+hops+' 跳邻域');}}catch(e){status(e.message,true);}};

  async function options(){
    const p=current, stamp=JSON.stringify(scope());if(!p)return;
    const result=await api(endpoint('/entity-options'),scope());
    if(current!==p||stamp!==JSON.stringify(scope()))return;
    ui.options=result.entities;choices();
    for(const [id,items,label] of [['type-scope',[...new Set(result.entities.map(r=>r.type))],'全部实体类型'],['predicate-scope',result.predicates,'全部关系类型']]){
      const previous=get(id).value;get(id).innerHTML=`<option value="">${label}</option>`+items.map(t=>`<option value="${esc(t)}" title="${esc(term(t))}">${esc(labelOf(t))}</option>`).join('');
      if(items.includes(previous))get(id).value=previous;
    }
  }
  function inspect(row){
    get('graph-node').value=row.kind==='entity'?row.id:row.subject_id;
    if(row.kind==='entity'){get('mindmap-root').value=row.id;get('graph-entity-choice').value=row.id;}
    window.renderEvidenceInspector(row,ui.options);
  }
  function renderGraph(result, selectedType='', base=result){
    const displayType=type=>result.type_labels?.[type]||labelOf(type);
    result=GraphTypeFilter.selectType(base,selectedType);
    let bar=get('graph-type-buttons');
    if(!bar){bar=make('section','','graph-type-buttons');bar.id='graph-type-buttons';get('graph-canvas').after(bar);}
    const groups=[...new Set(base.nodes.map(n=>n.type))];
    bar.innerHTML='<small>按实体类型查看 · 只显示所选类型，不展开邻居</small><div>'+['',...groups].map((type,i)=>`<button class="secondary" data-type-index="${i}" aria-pressed="${type===selectedType}" title="${esc(type?term(type):'恢复当前检索范围全部类型')}">${esc(type?displayType(type):'全部类型')} <b>${type?base.nodes.filter(n=>n.type===type).length:base.nodes.length}</b></button>`).join('')+'</div>';
    bar.querySelectorAll('button').forEach(button=>button.onclick=()=>{get('graph-detail').textContent='';get('graph-node').value='';get('graph-entity-choice').value='';renderGraph(base,['',...groups][Number(button.dataset.typeIndex)],base);});
    ui.graph=result;wb.nodes=new Map(result.nodes.map(n=>[n.id,n]));
    const chart=initChart(), types=[...new Set(result.nodes.map(n=>n.type_label||displayType(n.type)))], matched=new Set(result.matched_ids||[]);
    chart.resize();
    chart.off('click');chart.on('click',p=>{const row=p.dataType==='edge'?result.edges.find(e=>e.id===p.data.id):wb.nodes.get(p.data.id);if(row)inspect(row);});
    const showLabels=get('graph-labels').checked;
chart.setOption({animation:false,tooltip:{formatter:p=>esc(p.dataType==='edge'?(p.data.rawType&&p.data.rawType!==p.data.name?p.data.name+' ('+p.data.rawType+')':p.data.name):p.data.name+' · '+p.data.categoryName)},legend:[{show:false,data:types}],series:[{type:'graph',layout:'force',roam:true,draggable:true,center:['50%','50%'],zoom:.85,label:{show:showLabels,position:'right',fontSize:11},edgeSymbol:['none','arrow'],edgeLabel:{show:result.nodes.length<=20,formatter:'{c}',fontSize:10},emphasis:{focus:'adjacency',label:{show:showLabels},edgeLabel:{show:true}},force:{repulsion:320,edgeLength:100,gravity:.1,initLayout:'circular',layoutAnimation:false},categories:types.map(name=>({name})),data:result.nodes.map(n=>{const label=n.type_label||displayType(n.type);return {id:n.id,name:n.text,category:types.indexOf(label),categoryName:label,symbolSize:matched.has(n.id)?34:22,itemStyle:matched.has(n.id)?{borderColor:'#df7d37',borderWidth:4}:undefined}}),links:result.edges.map(e=>({id:e.id,source:e.subject_id,target:e.object_id,name:e.type_label||displayType(e.type),value:e.type_label||displayType(e.type),rawType:term(e.type)})),lineStyle:{opacity:.35,curveness:.12}}]},true);
    chart.resize();fitGraph(chart);get('graph-summary').textContent=`${selectedType?'只看 '+labelOf(selectedType)+' · ':''}${result.nodes.length} 实体 / ${result.edges.length} 关系${selectedType?' · 隐藏其他类型及跨类型连线':''}${matched.size?' · 橙色边框为检索命中':''}${result.timing_ms?' · 服务 '+result.timing_ms.total+' ms':''}`;
    // Timeline scrubber: update from result data
    const tl=createTimeline();get('graph-type-buttons')?.after(tl);
    if(!get('known-at').value){
      const tlTimes=[];
      (base.nodes||[]).forEach(n=>{if(n.recorded_at)tlTimes.push(n.recorded_at);});
      (base.edges||[]).forEach(e=>{if(e.recorded_at)tlTimes.push(e.recorded_at);});
      if(tlTimes.length){
        uiTimeline.min=new Date(Math.min(...tlTimes.map(t=>new Date(t).getTime())));
        uiTimeline.max=new Date(Math.max(...tlTimes.map(t=>new Date(t).getTime())));
        const BUCKETS=48,range=uiTimeline.max-uiTimeline.min||1;
        uiTimeline.buckets=new Array(BUCKETS).fill(0);
        tlTimes.forEach(t=>{const idx=Math.min(BUCKETS-1,Math.floor((new Date(t)-uiTimeline.min)/range*BUCKETS));uiTimeline.buckets[idx]++;});
        renderDensityBars();
      }else{uiTimeline.buckets=[];renderDensityBars();}
    }
    updateTimelinePlayhead();
  }
  function fitGraph(chart){
    const series=chart.getModel().getSeriesByIndex(0), data=series.getData(), points=[];
    for(let i=0;i<data.count();i++){const p=data.getItemLayout(i);if(p&&p.every(Number.isFinite))points.push(p);}
    if(!points.length||points.length!==data.count())return;
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const minX=Math.min(...xs),minY=Math.min(...ys);
    const spanX=Math.max(1,Math.max(...xs)-minX),spanY=Math.max(1,Math.max(...ys)-minY);
    // Persist the solved positions so fitting does not restart the simulation.
    const option=chart.getOption();
    Object.assign(option.series[0],{left:25,right:120,top:30,bottom:30,center:null,zoom:1,force:{initLayout:null,layoutAnimation:true},data:series.option.data.map((node,i)=>{
      const p=data.getItemLayout(i);
      return {...node,x:(p[0]-minX)/spanX*Math.max(1,chart.getWidth()-145),y:(p[1]-minY)/spanY*Math.max(1,chart.getHeight()-60),fixed:false};
    })});
    chart.setOption(option,true);
  }
  function renderHits(result){
    get('hits').innerHTML=['entity','chunk','relation'].map(kind=>{
      const rows=result.hits.filter(r=>r.kind===kind);
      return `<section><h3 class="hit-kind">${{entity:'匹配实体',chunk:'原文片段',relation:'关系链路'}[kind]} <small>${rows.length}</small></h3>${rows.map(r=>`<button class="hit-entry" data-hit="${esc(r.id)}"><b>${esc(r.text.slice(0,80))}</b><span>${esc(labelOf(r.type)||'原文')} · ${r.score.toFixed(2)}</span><p>${esc(r.text.slice(0,180))}</p></button>`).join('')||'<p class="subtle">暂无命中</p>'}</section>`;
    }).join('');
    get('hits').querySelectorAll('[data-hit]').forEach(b=>b.onclick=()=>inspect(result.hits.find(r=>r.id===b.dataset.hit)));
  }
  async function linkedSearch(){
    if(!current)return;
    const serial=++ui.request,p=current,started=performance.now();
    const body={...scope(),query:get('query').value,semantic:get('explore-mode').value==='semantic',hops:Number(get('explore-hops').value),entity_type:get('type-scope').value,predicate:get('predicate-scope').value};
    get('search-summary').textContent='读取所选范围…';
    get('graph-detail').textContent='';get('answer').textContent='';get('hits').textContent='';
    const result=await api(endpoint('/explore'),body);
    if(serial!==ui.request||p!==current)return;
    renderGraph(result);renderHits(result);
    get('search-summary').textContent=`${result.mode==='semantic'?'语义':'关键词'} · ${result.candidate_count} 候选 · ${result.hits.length} 命中 · ${(performance.now()-started).toFixed(0)} ms`;
    updateChip();
  }
  drawGraph=async(node=null,hops=1)=>{
    if(node){const result=await scopedRead('/subgraph',{node_id:node,hops});renderGraph(result);}
    else {get('query').value='';await linkedSearch();}
  };
  act('search',linkedSearch);act('draw-graph',()=>drawGraph());act('clear-search',()=>drawGraph());
  act('apply-scope',async()=>{details.open=false;await Promise.all([options(),discoverMetadata()]);await linkedSearch();if(!get('tab-mindmap').classList.contains('hidden')&&get('mindmap-root').value)await get('draw-mindmap').onclick();});
  const previousAddFilter=get('add-filter')?.onclick;
  if(previousAddFilter)get('add-filter').onclick=async()=>{await previousAddFilter();if(!get('status').classList.contains('error'))await get('apply-scope').onclick();};
  act('reset-scope',async()=>{for(const id of ['known-at','filters','type-scope','predicate-scope'])get(id).value='';cb.conditions=[];renderCB();resetTimeline();await options();await linkedSearch();});
  act('build-index',async()=>{watch(await api(endpoint('/indexes/rebuild'),{}));status('索引任务已提交；可继续浏览图谱或使用关键词检索。');});
  act('graph-focus',async()=>{graphTab.classList.toggle('graph-focused');get('graph-focus').textContent=graphTab.classList.contains('graph-focused')?'退出专注':'专注图谱';wb.chart?.resize();});
  get('explore-hops').onchange=()=>get('search').click();
  get('query').onkeydown=e=>{if(e.key==='Enter')get('search').click();};
  let debounce;
  get('query').oninput=()=>{clearTimeout(debounce);if(get('explore-mode').value==='keyword')debounce=setTimeout(()=>get('search').click(),350);};
  // Ignore out-of-order project responses, and load graph immediately on selection.
  const priorChange=get('project').onchange;
  get('project').onchange=()=>{
    priorChange();ui.request++;ui.options=[];choices();ui.ontology=null;resetTimeline();get('ontology-browser')?.replaceChildren();
    clearTimeout(entityTimer);get('graph-entity-filter').value='';cb.facets=[];cb.conditions=[];renderCB();get('metadata-field-choice').innerHTML='<option value="">读取项目字段…</option>';get('metadata-value-choice').innerHTML='<option value="">先选择字段</option>';get('metadata-facet-summary').textContent='';
    get('project').title=get('project').selectedOptions[0]?.textContent||'';
    get('type-scope').value='';get('predicate-scope').value='';
    get('query').value='';get('graph-node').value='';updateChip();
    if(current){sessionStorage.setItem('knowledge-project',current);loadOntologyTerms().then(()=>Promise.all([options(),linkedSearch(),discoverMetadata()])).catch(e=>status(e.message,true));}
  };
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{
    const graphView=['graph','search'].includes(b.dataset.tab);
    get('type-scope').style.display=graphView?'':'none';get('predicate-scope').style.display=graphView?'':'none';
    if(b.dataset.tab==='mindmap'&&current)options().catch(e=>status(e.message,true));
  }));
  // Evidence QA remains available; also update the graph from the same scoped query.
  const priorQA=get('qa').onclick;
  get('qa').onclick=async()=>{try{await linkedSearch();await priorQA();}catch(e){status(e.message,true);}};

  // Structured ontology browser and versioned term additions, alongside Turtle/SPARQL.
  const ontologyPanel=get('tab-ontology').querySelector('.panel');
  ontologyPanel.insertBefore(make('div','', 'ontology-browser'),get('turtle'));
  ontologyPanel.querySelector('.ontology-browser').id='ontology-browser';
  const termForm=make('details','<summary>添加本体类 / 关系 / 属性</summary><div class="columns"><label>类型<select id="term-kind"><option value="class">实体类</option><option value="relation">关系</option><option value="attribute">实体属性</option></select></label><label>完整 IRI<input id="term-uri" placeholder="urn:my:Merchant"></label><label>显示名称<input id="term-label" placeholder="商户"></label><label>中文名称<input id="term-label-zh" maxlength="200" placeholder="商户"></label></div><div class="columns"><label>父类（仅实体类）<select id="term-parent"></select></label><label><span id="term-domain-label">适用实体类型</span><select id="term-domain"></select></label><label><span id="term-range-label">目标实体类型</span><select id="term-range"></select></label></div><button id="add-ontology-term">新增并保存为新版本</button><p class="subtle">实体属性这里只定义“哪些实体可使用该属性、值采用什么数据类型”，不保存具体实体的属性值。IRI 是稳定标识，创建后不可直接更名。</p>');
  ontologyPanel.insertBefore(termForm,get('turtle'));

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
              <div class="impact-stat-value" id="impact-records">-</div>
              <div class="impact-stat-label">受影响知识</div>
            </div>
            <div class="impact-stat">
              <div class="impact-stat-value" id="impact-constraints">-</div>
              <div class="impact-stat-label">约束引用</div>
            </div>
            <div class="impact-stat">
              <div class="impact-stat-value" id="impact-pending">-</div>
              <div class="impact-stat-label">待审核映射</div>
            </div>
          </div>
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

  const priorLoad=loadOntology;
  loadOntology=async(id='')=>{const p=current,result=await priorLoad(id);if(p!==current)return result;ui.ontology=result;
    if(!result){get('ontology-browser').innerHTML='<p class="subtle">本项目尚未发布本体。开放本体发现模式请先累计候选，再到「本体发现」生成并发布本体版本；仅文档检索模式不产生图谱本体。</p>';return result;}
    const classes=result.summary.classes||[],classLabels=new Map(classes.flatMap(c=>[[c.id,ontoName(c)],[c.name,ontoName(c)]]));
    const className=id=>classLabels.get(id)||term(id)||'未指定';
    const datatypeName=id=>new Map(datatypes).get(id)||term(id)||'未指定';
    const maintenance=(kind,item,detail)=>`<div class="ontology-term-card"><b>${esc(ontoName(item))}</b><span>${detail}</span><details class="ontology-technical"><summary>查看技术标识</summary><code>${esc(item.name)}</code><code>${esc(item.id)}</code></details><button class="secondary" data-maintain-kind="${kind}" data-maintain-uri="${esc(item.id)}">维护</button></div>`;
    get('ontology-browser').innerHTML=`<div class="ontology-columns"><section><h3>实体类与继承</h3>${classes.map(c=>maintenance('class',c,(c.parents||[]).length?'父类：'+(c.parents||[]).map(className).map(esc).join('、'):'层级：根类')).join('')}</section><section><h3>关系定义</h3>${(result.summary.relations||[]).map(r=>maintenance('relation',r,`<em>起点类型</em> ${esc((r.domain||[]).map(className).join('、')||'不限')} <i>→</i> <em>终点类型</em> ${esc((r.range||[]).map(className).join('、')||'不限')}`)).join('')}</section><section><h3>实体属性定义</h3>${(result.summary.attributes||[]).map(a=>maintenance('attribute',a,`<em>适用实体</em> ${esc((a.domain||[]).map(className).join('、')||'不限')} <i>·</i> <em>数据类型</em> ${esc((a.range||[]).map(datatypeName).join('、')||'未指定')}`)).join('')||'<p class="subtle">尚未定义属性</p>'}</section></div>`;
    configureTermForm();
    get('ontology-browser').querySelectorAll('[data-maintain-uri]').forEach(button=>button.onclick=()=>selectTerm(button.dataset.maintainKind,button.dataset.maintainUri));
    return result;};
  const datatypes=[['http://www.w3.org/2000/01/rdf-schema#Literal','任意字面量'],['http://www.w3.org/2001/XMLSchema#string','字符串'],['http://www.w3.org/2001/XMLSchema#boolean','布尔值'],['http://www.w3.org/2001/XMLSchema#integer','整数'],['http://www.w3.org/2001/XMLSchema#decimal','小数'],['http://www.w3.org/2001/XMLSchema#double','浮点数'],['http://www.w3.org/2001/XMLSchema#date','日期'],['http://www.w3.org/2001/XMLSchema#dateTime','日期时间']];
  const classOptions=()=>'<option value="">不指定</option>'+(ui.ontology.summary.classes||[]).map(c=>`<option value="${esc(c.id)}">${esc(ontoName(c))}</option>`).join('');
  function configureFields(kind,prefix,values={}){const parent=get(prefix+'term-parent'),domain=get(prefix+'term-domain'),range=get(prefix+'term-range');parent.innerHTML=classOptions();domain.innerHTML=classOptions();range.innerHTML=kind==='attribute'?'<option value="">不指定</option>'+datatypes.map(([id,label])=>`<option value="${id}">${label}</option>`).join(''):classOptions();parent.disabled=kind!=='class';domain.disabled=kind==='class';range.disabled=kind==='class';parent.value=values.parent||'';domain.value=values.domain||'';range.value=values.range||'';const rangeLabel=get(prefix+'term-range-label');if(rangeLabel)rangeLabel.textContent=kind==='attribute'?'数据类型':kind==='relation'?'目标实体类型':'不适用';const domainLabel=get(prefix+'term-domain-label');if(domainLabel)domainLabel.textContent=kind==='attribute'?'适用实体类型':kind==='relation'?'起点实体类型':'不适用';}
  function configureTermForm(){if(ui.ontology)configureFields(get('term-kind').value,'');}
  get('term-kind').onchange=configureTermForm;
  act('add-ontology-term',async()=>{if(!ui.ontology)throw Error('请先加载本体');const kind=get('term-kind').value;await api(endpoint('/ontology/terms'),{kind,uri:get('term-uri').value,label:get('term-label').value,label_zh:get('term-label-zh').value,parent:kind==='class'?get('term-parent').value:'',domain:kind!=='class'?get('term-domain').value:'',range:kind!=='class'?get('term-range').value:'',expected_ontology_id:ui.ontology.id});await get('load-ontology').onclick();status('新本体版本已保存；已有知识保留原本体版本。');});
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
    configureFields(kind,'edit-',{parent:item.parents?.[0],domain:item.domain?.[0],range:item.range?.[0]});

    // 重置状态
    get('retire-term-confirm').checked=false;
    retireBtn.disabled=true;
    get('term-impact').style.display='none';
    get('term-impact').innerHTML='<h4>变更影响</h4><div class="impact-stats"><div class="impact-stat"><div class="impact-stat-value" id="impact-records">-</div><div class="impact-stat-label">受影响知识</div></div><div class="impact-stat"><div class="impact-stat-value" id="impact-constraints">-</div><div class="impact-stat-label">约束引用</div></div><div class="impact-stat"><div class="impact-stat-value" id="impact-pending">-</div><div class="impact-stat-label">待审核映射</div></div></div><div id="impact-details" style="margin-top:10px"></div>';

    // 打开悬浮框
    modalOverlay.classList.add('active');

    // 加载影响分析
    try{const impact=await api(endpoint('/ontology/term-impact')+'?uri='+encodeURIComponent(uri),undefined,'GET');
      if(selectedTerm?.uri!==uri)return;selectedTerm.impact=impact;
      get('term-impact').style.display='block';
      get('impact-records').textContent=impact.record_count;
      get('impact-constraints').textContent=impact.constraint_count;
      get('impact-pending').textContent=impact.pending_review_count;
      if(impact.record_ids.length){
        get('impact-details').innerHTML=`<details><summary style="font-size:12px;color:#216952;cursor:pointer">查看受影响知识 ID</summary><pre style="margin-top:8px;padding:10px;background:#f8faf7;border-radius:6px;font-size:11px;max-height:120px;overflow:auto">${esc(impact.record_ids.join('\n'))}${impact.record_ids_truncated?'\n…仅展示前 50 条':''}</pre></details>`;
      }
    }catch(error){
      get('term-impact').style.display='block';
      get('impact-records').textContent='错误';
      get('impact-constraints').textContent='';
      get('impact-pending').textContent='';
      get('impact-details').innerHTML='<p style="color:#dc2626;font-size:12px">影响分析失败：'+esc(error.message)+'</p>';
    }
  }
  act('update-ontology-term',async()=>{if(!selectedTerm)throw Error('请先选择术语');const kind=selectedTerm.kind;
    await api(endpoint('/ontology/term')+'?uri='+encodeURIComponent(selectedTerm.uri),{label:get('edit-term-label').value,label_zh:get('edit-term-label-zh').value,description:get('edit-term-description').value,parent:kind==='class'?get('edit-term-parent').value:'',domain:kind!=='class'?get('edit-term-domain').value:'',range:kind!=='class'?get('edit-term-range').value:'',expected_ontology_id:ui.ontology.id},'PUT');
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
  document.querySelector('[data-tab="dashboard"]').addEventListener('click',refreshDashboard);
  get('project').addEventListener('change',()=>{dashboardRequest++;if(!get('tab-dashboard').classList.contains('hidden'))refreshDashboard();});
  for(const id of ['apply-scope','reset-scope'])get(id).addEventListener('click',()=>{if(!get('tab-dashboard').classList.contains('hidden'))refreshDashboard();});
  // Boot into the linked workbench and restore the last selected existing project.
  showTab('graph');
  projects().then(()=>{const saved=sessionStorage.getItem('knowledge-project');if(saved&&[...get('project').options].some(o=>o.value===saved))get('project').value=saved;else if(get('project').options.length>1)get('project').selectedIndex=1;if(get('project').value)get('project').onchange();}).catch(e=>status(e.message,true));
})();

/* Open ontology discovery: schema-free candidates stay separate until a draft is published. */
(()=>{
  const get=id=>document.getElementById(id);
  const oldToggle=get('extract'),mode=document.createElement('select');mode.id='extraction-mode';
  mode.innerHTML='<option value="ontology">使用项目本体 · 正式入图</option><option value="discovery">开放本体发现 · 候选暂存</option><option value="documents">仅文档检索 · 不抽取图谱</option>';
  const modeLabel=document.createElement('label');modeLabel.className='extraction-mode-control';modeLabel.innerHTML='<span>解析模式 <b>必填</b></span>';modeLabel.append(mode);
  const help=document.createElement('p');help.id='extraction-mode-help';help.className='extraction-mode-help';
  oldToggle.closest('label').before(modeLabel,help);oldToggle.closest('label').classList.add('hidden');
  const descriptions={ontology:'按当前项目本体抽取、校验和实体融合，合法知识进入正式图谱。',discovery:'不要求预设本体；Semantica 自由抽取并持续累计候选，不直接写入正式图谱。',documents:'只保存原文、切片和向量，不抽取实体、关系或属性。'};
  mode.onchange=()=>{
    help.textContent=descriptions[mode.value];oldToggle.checked=mode.value!=='documents';
    for(const id of ['parse-resolve','parse-merge','parse-threshold','parse-relation-constraints'])if(get(id))get(id).disabled=mode.value!=='ontology';
    if(get('parse-attributes'))get('parse-attributes').disabled=mode.value==='documents';
  };mode.onchange();

  const page=document.createElement('section');page.id='tab-discovery';page.className='tab hidden';
  page.innerHTML='<div class="discovery-shell"><header class="discovery-hero"><div><small>SCHEMA INDUCTION</small><h2>从证据中发现本体</h2><p>自由候选持续累计但不污染正式图谱。草案基于上一版本合并术语；发布后需用新本体受控重解析，实体与关系才会入图。</p></div><div class="discovery-actions"><label class="discovery-name"><span>草案名称</span><input id="discovery-name" maxlength="200" placeholder="例如：直播规则本体" aria-label="本体草案名称" title="生成的本体草案使用的名称"></label><button id="create-discovery-draft">生成累计草案</button><button id="refresh-discovery" class="secondary">刷新</button></div></header><div id="discovery-content" aria-live="polite"></div></div>';
  document.querySelector('main').append(page);
  const nav=document.createElement('button');nav.dataset.tab='discovery';nav.textContent='本体发现';
  document.querySelector('[data-tab="ontology"]').after(nav);
  const activate=()=>{document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));page.classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===nav));get('title').textContent='本体发现';get('scope').classList.add('hidden');render();};
  nav.onclick=activate;
  async function render(){
    const host=get('discovery-content');if(!current){host.innerHTML='<div class="discovery-empty">请先选择项目</div>';return;}
    host.innerHTML='<div class="discovery-empty">正在聚合开放抽取候选…</div>';
    try{
      const data=await api(endpoint('/ontology-discovery'),undefined,'GET');
      const types=data.entity_types||[],relations=data.relation_types||[],attributes=data.attribute_types||[],drafts=data.drafts||[];
      const published=drafts.some(d=>d.status==='published');
      const pending=data.unpublished_candidate_count!==undefined?data.unpublished_candidate_count:((data.candidate_count||0)>0&&!published?data.candidate_count:0);
      const quality=(data.quality_warnings||[]).map(x=>`<div class="discovery-hint"><span><b>质量提醒：</b>${esc(x.message)}</span></div>`).join('');
      const hasLifecycle=!!data.candidate_status_counts,publishedCandidates=Math.max(0,...drafts.filter(d=>d.status==='published').map(d=>Number(d.candidate_count)||0)),legacyMapped=Math.max(0,...drafts.filter(d=>d.status==='published').map(d=>(Number(d.mapped_entities)||0)+(Number(d.mapped_relations)||0)+(Number(d.mapped_attributes)||0)));
      const states=data.candidate_status_counts||{pending, included_in_draft:0,approved:Math.max(0,publishedCandidates-legacyMapped),materialized:Math.min(publishedCandidates,legacyMapped)};
      const lifecycle=`<section class="discovery-metrics"><div><strong>${states.pending||0}</strong><span>待纳入</span></div><div><strong>${states.included_in_draft||0}</strong><span>草案中</span></div><div><strong>${states.approved||0}</strong><span>已批准</span></div><div><strong>${states.materialized||0}</strong><span>已物化</span></div></section>`;
      const lifecycleHelp='<details class="discovery-lifecycle-help"><summary>这些状态如何变化？</summary><p><b>待纳入</b>：尚未进入任何草案；<b>草案中</b>：已进入未发布草案；<b>已批准</b>：已随本体版本发布但尚未正式入图；<b>已物化</b>：已有正式实体或关系明确关联该候选。继续开放发现只会增加待纳入候选，不会改动正式图谱。</p></details>';
      const compatibility=hasLifecycle?'':'<div class="discovery-hint discovery-compat"><span><b>兼容估算：</b>当前服务仍返回旧版发现数据，生命周期根据草案和映射数量推算。重启后端后会按候选 ID 精确统计。</span></div>';
      const notice=lifecycle+lifecycleHelp+compatibility+quality+(pending>0?`<div class="discovery-hint"><span>还有 <b>${pending}</b> 条候选尚未纳入已发布本体。可继续上传并开放发现，准备稳定后再生成累计草案。</span></div>`:(data.requires_controlled_reingest?'<div class="discovery-hint"><span>候选已纳入本体版本，但不会自动变成正式知识。请切换到「使用项目本体」模式，对原文执行受控重解析。</span></div>':(data.candidate_count>0&&!drafts.length?`<div class="discovery-hint"><span>已累计 ${data.candidate_count} 条候选，可继续发现或生成第一份本体草案。</span></div>`:'')));
      const schema=d=>OntologyDetails.renderTechnicalDetails(d,esc);
      host.innerHTML=notice+`<section class="discovery-metrics"><div><strong>${data.candidate_count}</strong><span>候选总数</span></div><div><strong>${data.entity_count}</strong><span>实体候选</span></div><div><strong>${data.relation_count}</strong><span>关系候选</span></div><div><strong>${data.attribute_count||0}</strong><span>属性候选</span></div></section><div class="discovery-columns"><section><h3>实体类型簇</h3>${types.length?types.map(x=>`<div class="discovery-cluster"><b>${esc(x.name)}</b><span>${x.count} 个实例</span><small>${(x.examples||[]).map(esc).join('、')}</small><i style="--share:${Math.min(100,x.count/Math.max(1,data.entity_count)*100)}%"></i></div>`).join(''):'<p class="subtle">尚无实体候选</p>'}</section><section><h3>关系与属性簇</h3>${relations.length?relations.map(x=>`<article class="discovery-relation"><div><b>${esc(x.name)}</b><span>${x.count} 条关系</span></div><small>${x.examples.map(esc).join('；')}</small></article>`).join(''):'<p class="subtle">尚无关系候选</p>'}${attributes.map(x=>`<article class="discovery-relation"><div><b>${esc(x.name)}</b><span>${x.count} 个属性值</span></div></article>`).join('')}</section></div><section class="discovery-drafts"><h3>本体草案与发布记录</h3>${drafts.length?drafts.map(d=>`<article data-draft="${esc(d.id)}"><div><small>${d.generator_backend==='semantica'?'Semantica 归纳':'规则归纳'} · ${esc(d.created_at)}</small><h4>${esc(d.name)}</h4><span class="draft-state ${d.status}">${d.status==='published'?'已发布':'待审阅'}</span></div><p>${d.candidate_count} 个候选 · ${(Object.keys(d.mappings?.entity_types||{})).length} 类 · ${(Object.keys(d.mappings?.relation_types||{})).length} 关系 · ${(Object.keys(d.mappings?.attributes||{})).length} 属性</p>${schema(d)}<details><summary>版本差异 · Turtle 与 IRI 映射</summary><pre>${esc(JSON.stringify(d.diff||{},null,2))}</pre><pre>${esc(d.turtle)}</pre><pre>${esc(JSON.stringify(d.mappings,null,2))}</pre></details>${d.status==='draft'?'<label class="publish-check"><input type="checkbox" data-review-confirm>我已核对版本差异、类型边界、关系方向和示例证据</label><button data-publish disabled>发布本体版本</button>':`<small>本体版本 ${esc(d.ontology_id)} · 已保留候选快照 · 等待使用该本体受控重解析</small>`}</article>`).join(''):'<div class="discovery-empty">候选积累后，在上方生成第一份本体草案。</div>'}</section>`;
      host.querySelectorAll('[data-review-confirm]').forEach(check=>check.onchange=()=>{check.closest('[data-draft]').querySelector('[data-publish]').disabled=!check.checked});
      host.querySelectorAll('[data-draft]').forEach((article,index)=>OntologyDetails.bindCopyButtons(article,drafts[index]));
      host.querySelectorAll('[data-publish]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const id=button.closest('[data-draft]').dataset.draft,result=await api(endpoint('/ontology-discovery/drafts/'+encodeURIComponent(id)+'/publish'),{});status(`本体 ${result.ontology_id} 已发布；开放候选仍保持隔离，请用该本体受控重解析原文。`);await render();}catch(error){status(error.message,true);button.disabled=false;}});
    }catch(error){host.innerHTML='<div class="discovery-empty error">'+esc(error.message)+'</div>';}
  }
  get('refresh-discovery').onclick=render;
  get('create-discovery-draft').onclick=async()=>{const button=get('create-discovery-draft');button.disabled=true;status('正在通过 Semantica 归纳候选类型与关系…');try{await api(endpoint('/ontology-discovery/drafts'),{name:get('discovery-name').value.trim()||'发现本体'});status('本体草案已生成，请核对类型、关系和 Turtle 后再发布。');await render();}catch(error){status(error.message,true);}finally{button.disabled=false;}};
  get('project').addEventListener('change',()=>{if(!page.classList.contains('hidden'))render();});
})();
