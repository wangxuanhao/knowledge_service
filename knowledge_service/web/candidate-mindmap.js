/* Candidate-only graph: visual clustering is never written as canonical knowledge. */
(()=>{
  const get=id=>document.getElementById(id);
  const page=document.createElement('section');page.id='tab-candidate-mindmap';page.className='tab hidden';
  page.innerHTML=`<div class="candidate-map-shell">
    <header class="candidate-map-hero"><div><small>DISCOVERY PREVIEW · 非正式知识</small><h2>候选脑图</h2><p>相同类型和名称的开放候选仅在这里聚合展示；节点与关系仍需经过本体发布、正式重解析和融合才能进入正式图谱。</p></div><button id="refresh-candidate-map">刷新候选</button></header>
    <section class="panel candidate-map-controls"><label>候选类型<select id="candidate-map-type"><option value="">全部类型</option></select></label><label>生命周期<select id="candidate-map-status"><option value="">全部状态</option><option value="pending">待纳入</option><option value="included_in_draft">草案中</option><option value="approved">已批准</option><option value="materialized">已物化</option></select></label><label>最多显示节点<input id="candidate-map-limit" type="number" min="1" max="2000" value="500"></label><button id="draw-candidate-map">更新视图</button></section>
    <div id="candidate-map-summary" class="candidate-map-summary"></div>
    <section class="panel"><div class="candidate-map-legend"><span data-state="pending">待纳入</span><span data-state="included_in_draft">草案中</span><span data-state="approved">已批准</span><span data-state="materialized">已物化</span></div><div id="candidate-map-canvas" class="graph-canvas candidate-map-canvas"></div></section>
    <section id="candidate-map-detail" class="panel candidate-map-detail"><p class="subtle">点击候选节点或关系查看出现次数、属性和来源证据。</p></section>
  </div>`;
  document.querySelector('main').append(page);
  const nav=document.createElement('button');nav.dataset.tab='candidate-mindmap';nav.textContent='候选脑图';
  document.querySelector('[data-tab="mindmap"]').after(nav);
  let chart=null,data=null,request=0;
  const labels={pending:'待纳入',included_in_draft:'草案中',approved:'已批准',materialized:'已物化'};
  const colors={pending:'#d98a29',included_in_draft:'#477dc1',approved:'#25835b',materialized:'#65756e'};
  const stateText=counts=>Object.entries(counts||{}).filter(([,count])=>count).map(([state,count])=>`${labels[state]||state} ${count}`).join(' · ');
  const evidence=sources=>(sources||[]).length?`<div class="candidate-evidence-list">${sources.map(source=>`<article><b>${esc(source.document_title||source.document_id||'未知来源')}</b><small>${esc(source.chunk_id||'')} · 字符 ${esc(source.start_char??'—')}–${esc(source.end_char??'—')} · 置信度 ${esc(source.confidence??'—')}</small><p>${esc(source.evidence||'未保存证据片段')}</p></article>`).join('')}</div>`:'<p class="subtle">没有可展示的来源证据。</p>';
  function detail(item,kind){
    const host=get('candidate-map-detail');
    if(kind==='edge'){
      host.innerHTML=`<small>候选关系 · ${esc(stateText(item.status_counts))}</small><h3>${esc(item.type)}</h3><p>累计出现 ${item.occurrence_count} 次。这里只表示开放候选中的重复三元组，不代表关系已经批准。</p>${evidence(item.sources)}`;
      return;
    }
    const attributes=(item.attributes||[]).length?`<div class="candidate-attributes"><h4>属性候选</h4>${item.attributes.map(attribute=>`<span><b>${esc(attribute.name)}</b> = ${esc(JSON.stringify(attribute.value))} <small>${esc(labels[attribute.status]||attribute.status)}</small></span>`).join('')}</div>`:'';
    host.innerHTML=`<small>${esc(item.type)} · ${esc(stateText(item.status_counts))}</small><h3>${esc(item.text)}</h3><p>聚合 ${item.occurrence_count} 个开放候选 · ${item.document_ids.filter(Boolean).length} 份来源文档。该聚合键不是正式实体 ID。</p>${attributes}${evidence(item.sources)}`;
  }
  function render(){
    if(!data)return;
    const type=get('candidate-map-type').value,state=get('candidate-map-status').value;
    const nodes=data.nodes.filter(node=>(!type||node.type===type)&&(!state||(node.status_counts?.[state]||0)>0));
    const ids=new Set(nodes.map(node=>node.id));
    const edges=data.edges.filter(edge=>ids.has(edge.subject_id)&&ids.has(edge.object_id)&&(!state||(edge.status_counts?.[state]||0)>0));
    if(!chart)chart=echarts.init(get('candidate-map-canvas'));chart.clear();chart.off('click');
    if(!nodes.length){get('candidate-map-summary').innerHTML='<div class="discovery-empty">当前筛选下没有候选节点。请继续开放发现或调整筛选。</div>';return;}
    const categories=[...new Set(nodes.map(node=>node.type))];
    chart.setOption({tooltip:{formatter:param=>{const raw=param.data.raw;if(!raw)return '';return param.dataType==='edge'?`${esc(raw.type)}<br>${raw.occurrence_count} 次候选关系`:`${esc(raw.text)}<br>${esc(raw.type)} · ${raw.occurrence_count} 次`; }},
      series:[{type:'graph',layout:'force',roam:true,draggable:true,focusNodeAdjacency:true,
        force:{repulsion:260,edgeLength:[90,210],gravity:.06},categories:categories.map(name=>({name})),
        data:nodes.map(node=>({id:node.id,name:node.text,category:categories.indexOf(node.type),symbolSize:Math.min(58,24+Math.log2(node.occurrence_count+1)*8),itemStyle:{color:colors[node.status]||colors.pending},raw:node})),
        links:edges.map(edge=>({id:edge.id,source:edge.subject_id,target:edge.object_id,value:edge.type,name:edge.type,lineStyle:{width:Math.min(6,1+Math.log2(edge.occurrence_count+1)),color:colors[edge.status]||colors.pending},raw:edge})),
        edgeSymbol:['none','arrow'],edgeSymbolSize:8,label:{show:true,position:'right',formatter:'{b}'},edgeLabel:{show:true,formatter:param=>param.data.value,fontSize:10},lineStyle:{curveness:.12,opacity:.58}}]},true);
    chart.on('click',param=>{if(param.data?.raw)detail(param.data.raw,param.dataType==='edge'?'edge':'node');});chart.resize();
    const summary=data.summary;get('candidate-map-summary').innerHTML=`<div><strong>${nodes.length}</strong><span>可见实体簇</span></div><div><strong>${edges.length}</strong><span>可见关系簇</span></div><div><strong>${summary.entity_occurrences}</strong><span>实体出现次数</span></div><div><strong>${summary.unresolved_relations}</strong><span>未解析端点</span></div>${data.truncated?'<p>候选规模已截断，请提高节点上限或按类型查看。</p>':''}`;
  }
  async function load(){
    const host=get('candidate-map-summary');if(!current){host.innerHTML='<div class="discovery-empty">请先选择项目。</div>';return;}
    const serial=++request,p=current;host.innerHTML='<div class="discovery-empty">正在聚合候选实体与关系…</div>';
    try{
      const limit=Math.max(1,Math.min(2000,Number(get('candidate-map-limit').value)||500));
      const result=await api(endpoint('/ontology-discovery/candidate-mindmap?limit='+limit),undefined,'GET');
      if(serial!==request||p!==current)return;data=result;
      const selected=get('candidate-map-type').value,types=[...new Set(data.nodes.map(node=>node.type))].sort();
      get('candidate-map-type').innerHTML='<option value="">全部类型</option>'+types.map(type=>`<option value="${esc(type)}">${esc(type)}</option>`).join('');
      if(types.includes(selected))get('candidate-map-type').value=selected;render();
    }catch(error){host.innerHTML=`<div class="discovery-empty error">${esc(error.message)}</div>`;}
  }
  nav.onclick=()=>{document.querySelectorAll('.tab').forEach(tab=>tab.classList.add('hidden'));page.classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button===nav));get('title').textContent='候选脑图';get('scope').classList.add('hidden');load();};
  get('refresh-candidate-map').onclick=load;get('draw-candidate-map').onclick=load;
  get('candidate-map-type').onchange=render;get('candidate-map-status').onchange=render;
  get('project').addEventListener('change',()=>{data=null;chart?.clear();get('candidate-map-detail').innerHTML='<p class="subtle">点击候选节点或关系查看出现次数、属性和来源证据。</p>';if(!page.classList.contains('hidden'))load();});
  window.addEventListener('resize',()=>chart?.resize());
})();
