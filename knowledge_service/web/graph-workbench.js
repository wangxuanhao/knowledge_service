/* Interactive layout and local path exploration; never changes stored knowledge. */
(() => {
  const session=GraphInteraction.createSession();
  let state=null,path=null,host=null,toggle=null,scopeStamp='',generation=0,closeBound=false;
  const $=id=>document.getElementById(id);
  function roam(chart,type,payload){
    const series=chart.getModel().getSeriesByIndex(0);
    // ECharts graphRoam is registered with update:'none': dispatching it alone
    // changes the model but leaves the rendered group still. Reuse the bundled
    // GraphView controller so transforms, symbol scales, edges and labels follow
    // the same path as a native wheel/pan, without rebuilding the graph data.
    chart.getViewOfSeriesModel(series)._controller.trigger(type,payload);
  }
  function capture(){
    if(!state)return;
    const data=state.chart.getModel()?.getSeriesByIndex(0)?.getData();
    if(!data)return;
    const nodes=[];
    for(let i=0;i<data.count();i++){
      const point=data.getItemLayout(i),item=data.getRawDataItem(i);
      if(point&&item)nodes.push({id:item.id,x:point[0],y:point[1]});
    }
    session.remember(state.project,nodes);
  }
  function positions(graph,project,reset=false){
    if(!reset)capture();
    return graph.nodes.map((node,index)=>{
      const point=session.position(project,node.id);
      const angle=index*2.399963229728653,radius=100*Math.sqrt(index);
      return {...node,x:point?.[0]??Math.cos(angle)*radius,y:point?.[1]??Math.sin(angle)*radius};
    });
  }
  function viewport(project,scope,nodes){
    if(!state||state.project!==project||JSON.stringify(scope)!==state.scope)return {};
    return nodes.length?preserveTransform(nodes,state.chart):{};
  }
  function bounds(nodes,width,height){
    const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y);
    const spanX=Math.max(2,Math.max(...xs)-Math.min(...xs)),spanY=Math.max(2,Math.max(...ys)-Math.min(...ys));
    const scale=Math.min(Math.max(1,width-120)/spanX,Math.max(1,height-100)/spanY);
    const w=spanX*scale,h=spanY*scale;
    return {left:(width-w)/2,top:(height-h)/2,width:w,height:h,right:null,bottom:null};
  }
  function preserveTransform(nodes,chart){
    const transform=chart.getModel().getSeriesByIndex(0).coordinateSystem.transform;
    const zoom=chart.getModel().getSeriesByIndex(0).get('zoom')||1;
    let minX=Math.min(...nodes.map(n=>n.x)),maxX=Math.max(...nodes.map(n=>n.x));
    let minY=Math.min(...nodes.map(n=>n.y)),maxY=Math.max(...nodes.map(n=>n.y));
    if(minX===maxX){minX--;maxX++;}if(minY===maxY){minY--;maxY++;}
    const center=[(minX+maxX)/2,(minY+maxY)/2],sx=transform[0]/zoom,sy=transform[3]/zoom;
    // Encode the existing pixel transform into the new data bounds. Moving an
    // outermost node must not rescale or move every other node on mouse release.
    const tx=transform[4]-sx*center[0]*(1-zoom),ty=transform[5]-sy*center[1]*(1-zoom);
    return {left:tx+minX*sx,top:ty+minY*sy,width:(maxX-minX)*sx,height:(maxY-minY)*sy,
      right:null,bottom:null,center,zoom};
  }
  function commitDrag(){
    capture();if(!state)return;
    const option=state.chart.getModel().getSeriesByIndex(0).option;
    const nodes=option.data.map(node=>{
      const point=session.position(state.project,node.id);
      return point?{...node,x:point[0],y:point[1]}:node;
    });
    state.chart.setOption({series:[{...preserveTransform(nodes,state.chart),data:nodes}]});
  }
  function fit(chart){
    capture();
    const data=chart.getModel()?.getSeriesByIndex(0)?.getData();if(!data)return;
    const current=chart.getModel().getSeriesByIndex(0).option;
    // Keep raw coordinates and disable simulation; ECharts fits their bounding box.
    const nodes=current.data.map((item,i)=>{
      const point=data.getItemLayout(i);return point?{...item,x:point[0],y:point[1]}:item;
    });
    chart.setOption({series:[{layout:'none',...bounds(nodes,chart.getWidth(),chart.getHeight()),center:null,zoom:1,data:nodes}]});
    capture();
  }
  function highlight(){
    if(!state)return;
    if(!path&&!state.highlighted)return;
    capture();
    const selected=new Set(path?.nodes||[]),edges=new Set(path?.steps.map(s=>s.edge.id)||[]);
    const data=state.styles.data.map(node=>{
      const point=session.position(state.project,node.id);
      return {...node,...(point?{x:point[0],y:point[1]}:{}),itemStyle:{...node.itemStyle,
        ...(path?{opacity:selected.has(node.id)?1:.25,...(selected.has(node.id)?{borderColor:'#167d79',borderWidth:3}:{})}:{})}};
    });
    const links=state.styles.links.map(edge=>({...edge,lineStyle:{...edge.lineStyle,
      ...(path?{opacity:edges.has(edge.id)?1:.12,...(edges.has(edge.id)?{color:'#167d79',width:3}:{})}:{})}}));
    state.chart.setOption({series:[{data,links,emphasis:{focus:path?'none':'adjacency'}}]});
    state.highlighted=Boolean(path);
  }
  function clearPath(message='已清除路径高亮。'){
    path=null;highlight();if($('graph-path-result'))$('graph-path-result').textContent=message;
  }
  const name=id=>state.graph.nodes.find(n=>n.id===id)?.text||id;
  function button(text,action){const el=document.createElement('button');el.type='button';el.className='secondary';el.textContent=text;el.onclick=action;return el;}
  function search(){
    if(!state)return;
    const from=$('graph-path-from').value,to=$('graph-path-to').value;
    if(!from||!to){clearPath('请选择起点和终点。');return;}
    if(from===to){clearPath('请选择两个不同的实体。');return;}
    path=GraphPaths.findPath(state.graph,from,to,{directed:$('graph-path-direction').value==='directed',maxHops:Number($('graph-path-hops').value)});
    highlight();const result=$('graph-path-result');result.replaceChildren();
    if(!path){result.textContent='当前已加载图谱及所选方向、跳数内未找到路径；不代表全库没有关联。';return;}
    const summary=document.createElement('p');summary.textContent=`当前图内最短路径 · ${path.steps.length} 跳；关系链不代表因果或规则证明。`;result.append(summary);
    const chain=document.createElement('ol');chain.className='graph-path-chain';result.append(chain);
    function stepRow(control,kind){const row=document.createElement('li');row.className='graph-path-'+kind;row.append(control);chain.append(row);}
    stepRow(button(name(from),()=>state.inspect(state.graph.nodes.find(n=>n.id===from))),'node');
    for(const step of path.steps){
      const label=step.edge.type_label||step.edge.text||step.edge.type||'关系';
      const edge=button(`${step.reverse?'←':'→'} ${label}${step.reverse?'（逆向经过）':''}`,()=>state.inspect(step.edge));
      edge.dataset.pathEdge=step.edge.id;edge.title='查看这条关系的原文、版本和有效期';stepRow(edge,'edge');
      stepRow(button(name(step.to),()=>state.inspect(state.graph.nodes.find(n=>n.id===step.to))),'node');
    }
  }
  function syncTools(){
    if(!host)return;
    toggle?.setAttribute('aria-expanded',String(host.open));
    host.closest('.inspector-rail')?.classList.toggle('is-path-open',host.open);
  }
  function openTools(){host.open=true;syncTools();host.scrollIntoView({block:'nearest'});}
  function ensureTools(){
    if(host?.isConnected)return;
    host=document.createElement('details');host.id='graph-path-tools';host.className='graph-path-tools';
    host.innerHTML='<summary>路径循迹 · 当前已加载图谱</summary><p class="subtle">选择两个实体寻找关联链；只搜索当前画布显示的事实，可点路径上的关系查看证据。</p><div class="graph-path-controls"><label>起点<select id="graph-path-from" aria-label="路径起点"></select></label><label>终点<select id="graph-path-to" aria-label="路径终点"></select></label><label>方向<select id="graph-path-direction"><option value="directed">按关系箭头</option><option value="any">忽略方向</option></select></label><label>最大跳数<select id="graph-path-hops"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option><option>10</option></select></label></div><div class="graph-path-actions"></div><div id="graph-path-result" role="status" aria-live="polite"></div>';
    host.querySelector('.graph-path-actions').append(button('寻找路径',search),button('清除路径',()=>clearPath()));
    for(const id of ['graph-path-from','graph-path-to','graph-path-direction','graph-path-hops'])host.querySelector('#'+id).onchange=()=>clearPath('条件已改变，请重新寻找路径。');
    const rail=document.querySelector('.search-graph-workspace .inspector-rail');
    rail.prepend(host);
    host.addEventListener('toggle',syncTools);
    toggle=button('路径循迹',()=>{host.open=!host.open;syncTools();if(host.open)host.scrollIntoView({block:'nearest'});});
    toggle.id='graph-path-toggle';toggle.setAttribute('aria-controls','graph-path-tools');toggle.setAttribute('aria-expanded','false');
    document.querySelector('.graph-toolbar').append(toggle);
    const close=$('detail-drawer-close');
    if(close&&!closeBound){close.addEventListener('click',()=>{if(host){host.open=false;syncTools();}});closeBound=true;}
    const relayout=button('重新布局',()=>{
      if(!state)return;session.clear();path=null;
      const nodes=positions(state.graph,state.project,true);
      const byId=new Map(nodes.map(n=>[n.id,n]));
      const data=state.styles.data.map(n=>({...n,...byId.get(n.id)}));
      state.chart.setOption({series:[{data,links:state.styles.links,
        ...bounds(data,state.chart.getWidth(),state.chart.getHeight()),center:null,zoom:1,
        emphasis:{focus:'adjacency'}}]});
      state.highlighted=false;capture();clearPath('已重新布局，知识内容未修改。');
    });
    relayout.id='graph-relayout';relayout.title='重新排列节点；只改变本次浏览布局';$('graph-reset-view').after(relayout);
  }
  function mount(chart,graph,project,inspect,scope){
    chart.__maintenanceResetGesture?.();
    const option=chart.getModel().getSeriesByIndex(0).option;
    ensureTools();state={chart,graph,project,inspect,scope:JSON.stringify(scope),
      styles:{data:option.data,links:option.links},highlighted:false};generation++;
    const stamp=JSON.stringify([project,scope,
      graph.nodes.map(n=>[n.id,n.version_id,n.version,n.type,n.text]),
      graph.edges.map(e=>[e.id,e.version_id,e.version,e.subject_id,e.object_id,e.type,e.text])]);
    if(stamp!==scopeStamp){clearPath('图谱或范围已变化，请重新选择路径。');scopeStamp=stamp;}
    const nodes=graph.nodes.filter(n=>!n.virtual&&n.kind!=='attribute_value');
    const labelCounts=new Map();for(const node of nodes)labelCounts.set(node.text,(labelCounts.get(node.text)||0)+1);
    for(const id of ['graph-path-from','graph-path-to']){
      const select=$(id),previous=select.value;select.replaceChildren(new Option('选择实体…',''));
      for(const node of nodes){
        const duplicate=labelCounts.get(node.text)>1;
        const option=new Option((node.text||node.id)+(duplicate?' · '+node.id:''),node.id);
        option.title=node.id;select.append(option);
      }
      if(nodes.some(n=>n.id===previous))select.value=previous;
    }
    host.querySelectorAll('select,button').forEach(el=>el.disabled=nodes.length<2);
    highlight();
    if(!chart.__maintenanceGestures){
      chart.__maintenanceGestures=true;
      const zr=chart.getZr();
      let pan=null;
      const queued=GraphInteraction.createPanQueue({apply:(dx,dy)=>{
        if(state?.chart===chart)roam(chart,'pan',{dx,dy});
      }});
      chart.__maintenanceResetGesture=()=>{queued.clear();pan=null;session.release();};
      zr.on('mousedown',event=>{
        if(state?.chart!==chart)return;
        queued.clear();pan=null;
        session.press(event.offsetX,event.offsetY);
        if(!event.target)pan=[event.offsetX,event.offsetY];
      });
      zr.on('mousemove',event=>{
        session.move(event.offsetX,event.offsetY);
        if(pan){
          queued.move(event.offsetX-pan[0],event.offsetY-pan[1]);
          pan=[event.offsetX,event.offsetY];
        }
      });
      const release=()=>{const panning=Boolean(pan);if(panning)queued.flush();pan=null;
        if(session.release()&&!panning&&state?.chart===chart)commitDrag();};
      zr.on('mouseup',release);
      zr.on('globalout',release);
      if(typeof ResizeObserver!=='undefined'){
        const element=chart.getDom();
        let size=element.clientWidth+':'+element.clientHeight;
        chart.__maintenanceResizeObserver=new ResizeObserver(()=>{
          if(state?.chart!==chart)return;
          const element=chart.getDom(),next=element.clientWidth+':'+element.clientHeight;
          if(next===size)return;size=next;
          capture();
          if(chart.getWidth()!==element.clientWidth||chart.getHeight()!==element.clientHeight)chart.resize();
          const data=chart.getModel().getSeriesByIndex(0)?.option.data||[];
          if(data.length)chart.setOption({series:[bounds(data,chart.getWidth(),chart.getHeight())]});
        });
        chart.__maintenanceResizeObserver.observe(chart.getDom());
      }
    }
    // Commit coordinates in session after layout; generation prevents stale work.
    const serial=generation;requestAnimationFrame(()=>{if(serial===generation)capture();});
  }
  function selection(row){
    const actions=$('graph-detail')?.querySelector('.evidence-actions');
    if(!state||!actions||row.kind!=='entity'||!state.graph.nodes.some(n=>n.id===row.id))return;
    for(const [id,label] of [['graph-path-from','设为路径起点'],['graph-path-to','设为路径终点']]){
      actions.append(button(label,()=>{
        openTools();$(id).value=row.id;clearPath('已设置'+(id.endsWith('from')?'起点':'终点')+'，选择另一端后寻找路径。');
      }));
    }
  }
  function zoom(chart,factor){
    const current=Number(chart.getModel().getSeriesByIndex(0).get('zoom'))||1;
    if(!Number.isFinite(factor)||factor<=0)return current;
    const next=Math.min(4,Math.max(.2,current*factor));
    if(next!==current)roam(chart,'zoom',{scale:next/current,
      originX:chart.getWidth()/2,originY:chart.getHeight()/2});
    return next;
  }
  function clear(){generation++;state?.chart.__maintenanceResetGesture?.();state=null;path=null;scopeStamp='';session.clear();if(host){host.open=false;syncTools();host.remove();host=null;}toggle?.remove();toggle=null;$('graph-relayout')?.remove();}
  window.GraphWorkbench={positions,viewport,bounds,mount,fit,zoom,selection,canClick:()=>session.canClick(),clear};
})();
