/* Exploration controls use the view's single loaded tree; they never fetch facts. */
(function(root,factory){
  const api=factory(root);if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityMindmapExplorer=api;
})(typeof window==='object'?window:globalThis,environment=>{
  const mounted=new WeakMap(),SVG_NS='http://www.w3.org/2000/svg',RESULT_LIMIT=30;
  const TRACE_NOTE='脑图路径，不表示关系方向，仅包含当前已加载关系';
  function mount({view,root,host,labelOf=value=>value||'实体',colorFor=()=> '#548e88'}){
    if(!root||!host||!view)throw new TypeError('脑图探索需要视图、容器和画布');
    mounted.get(root)?.destroy();
    const model=environment.EntityMindmapModel,exporter=environment.EntityMindmapExport;
    if(!model||!exporter)throw new Error('脑图探索模块尚未加载');
    const document=root.ownerDocument,window=document.defaultView||environment;
    const get=id=>root.querySelector('#'+id);
    const search=get('mindmap-node-search'),results=get('mindmap-search-results'),searchStatus=get('mindmap-search-status');
    const from=get('mindmap-trace-from'),to=get('mindmap-trace-to'),run=get('mindmap-trace-run'),clear=get('mindmap-trace-clear');
    const traceStatus=get('mindmap-trace-status'),tracePath=get('mindmap-trace-path');
    const toggle=get('mindmap-toggle-overview'),overview=get('mindmap-overview'),map=get('mindmap-overview-map');
    const svgButton=get('mindmap-export-svg'),pngButton=get('mindmap-export-png'),exportStatus=get('mindmap-export-status');
    const listeners=[];
    let index=null,generation=0,destroyed=false,frame=null,exporting=false,hasTrace=false;
    let overviewVisible=toggle?.getAttribute('aria-pressed')!=='false',mapTransform=null,matches=[],activeResult=-1;
    const text=(element,value)=>{if(element)element.textContent=value;};
    const label=value=>String(labelOf(value)||value||'实体');
    const name=node=>String(node.name||'未命名实体');
    const hasTree=()=>!!index?.nodes.size;
    const hasNode=id=>!!id&&!!index?.nodes.has(id);
    const listen=(element,event,handler,options)=>{
      if(!element)return;element.addEventListener(event,handler,options);
      listeners.push(()=>element.removeEventListener(event,handler,options));
    };
    const element=(tag,className,value)=>{
      const node=document.createElement(tag);if(className)node.className=className;
      if(value!==undefined)node.textContent=value;return node;
    };
    const svg=(tag,attributes)=>{
      const node=document.createElementNS(SVG_NS,tag);
      Object.entries(attributes).forEach(([key,value])=>node.setAttribute(key,String(value)));return node;
    };
    [searchStatus,traceStatus,exportStatus].forEach(status=>{
      if(status){status.setAttribute('role','status');status.setAttribute('aria-live','polite');}
    });
    if(search){
      search.setAttribute('autocomplete','off');search.setAttribute('role','combobox');
      search.setAttribute('aria-autocomplete','list');search.setAttribute('aria-controls','mindmap-search-results');
      search.setAttribute('aria-expanded','false');
    }
    results?.setAttribute('role','listbox');
    if(map){map.setAttribute('viewBox','0 0 200 120');map.setAttribute('tabindex','0');map.setAttribute('title','点击定位；方向键平移，Shift 加速');}
    function hideResults(){
      if(results)results.hidden=true;
      search?.setAttribute('aria-expanded','false');search?.removeAttribute('aria-activedescendant');
      activeResult=-1;
    }
    function resetSearch(){
      if(search)search.value='';matches=[];results?.replaceChildren();hideResults();text(searchStatus,'');
    }
    function syncButtons(){
      const empty=!hasTree();
      if(search)search.disabled=empty;
      if(from)from.disabled=empty;if(to)to.disabled=empty;
      if(run)run.disabled=empty||!hasNode(from?.value)||!hasNode(to?.value);
      if(clear)clear.disabled=empty||!hasTrace;
      if(toggle){toggle.disabled=empty;toggle.setAttribute('aria-pressed',String(overviewVisible));}
      [svgButton,pngButton].forEach(button=>{if(button)button.disabled=empty||exporting;});
    }
    function chooseResult(node){
      if(!hasNode(node.id))return;
      view.focus(node.id);search?.focus({preventScroll:true});hideResults();
      text(searchStatus,'已聚焦：'+name(node)+' · 共 '+matches.length+' 个匹配');
    }
    function showSearch(){
      const query=search?.value.trim()||'';matches=query&&hasTree()?model.search(index,query,labelOf):[];
      results?.replaceChildren();activeResult=-1;search?.removeAttribute('aria-activedescendant');
      if(!query||!hasTree()){hideResults();text(searchStatus,'');return;}
      text(searchStatus,matches.length?'找到 '+matches.length+' 个匹配'+(matches.length>RESULT_LIMIT?'，显示前 30 个':''):'没有找到匹配节点');
      if(!results)return;
      const fragment=document.createDocumentFragment();
      matches.slice(0,RESULT_LIMIT).forEach((node,position)=>{
        const button=element('button','mindmap-search-result');button.type='button';button.tabIndex=-1;
        button.id='mindmap-search-result-'+position;button.setAttribute('role','option');button.setAttribute('aria-selected','false');
        button.append(element('span','mindmap-search-result-name',name(node)));
        button.append(element('span','mindmap-search-result-meta',label(node.type)+' · ID '+String(node.entityId??'—')+' · 位置 '+String(node.id)));
        button.addEventListener('click',()=>chooseResult(node));fragment.append(button);
      });
      results.append(fragment);results.hidden=!matches.length;
      search?.setAttribute('aria-expanded',String(!!matches.length));
    }
    function moveSearch(direction){
      if(!matches.length)return;
      if(results?.hidden)showSearch();
      const count=Math.min(matches.length,RESULT_LIMIT);
      activeResult=activeResult<0?(direction>0?0:count-1):(activeResult+direction+count)%count;
      Array.from(results?.children||[]).forEach((option,i)=>option.setAttribute('aria-selected',String(i===activeResult)));
      const selected=results?.children[activeResult];
      if(selected){search?.setAttribute('aria-activedescendant',selected.id);selected.scrollIntoView({block:'nearest'});}
    }
    function resetTrace(message=hasTree()?TRACE_NOTE:'',notify=true){
      if(notify&&hasTrace)view.clearTrace();hasTrace=false;
      tracePath?.replaceChildren();text(traceStatus,message);syncButtons();
    }
    function makeEndpoints(){
      [from,to].forEach((select,i)=>{
        if(!select)return;
        const fragment=document.createDocumentFragment(),placeholder=element('option','',i?'选择终点':'选择起点');
        placeholder.value='';fragment.append(placeholder);
        if(index)index.nodes.forEach(node=>{
          const option=element('option','',name(node)+' · '+label(node.type)+' · ID '+String(node.entityId??'—')+' · 位置 '+String(node.id));
          option.value=String(node.id);fragment.append(option);
        });
        select.replaceChildren(fragment);select.value='';
      });
    }
    function trace(){
      resetTrace();
      if(!hasNode(from?.value)||!hasNode(to?.value)){text(traceStatus,'请选择有效的起点和终点');return;}
      const path=view.trace(from.value,to.value);
      if(!path){text(traceStatus,'未找到脑图路径，请重新选择端点');return;}
      hasTrace=true;
      const fragment=document.createDocumentFragment(),edges=new Map(index.edges.map(edge=>[edge.id,edge]));
      path.nodeIds.forEach((id,i)=>{
        const node=index.nodes.get(id);if(!node)return;
        const item=element('li','mindmap-trace-step');
        if(i){
          const edge=edges.get(path.edgeIds[i-1]);
          item.append(element('span','mindmap-trace-relation','关系：'+(edge?.predicate?label(edge.predicate):'未标注关系')));
        }
        item.append(element('span','mindmap-trace-node',name(node)));
        item.append(element('span','mindmap-trace-meta',label(node.type)+' · ID '+String(node.entityId??'—')));
        fragment.append(item);
      });
      tracePath?.replaceChildren(fragment);
      text(traceStatus,'脑图路径，不表示关系方向 · '+path.edgeIds.length+' 条关系'+(path.edgeIds.length?'':'（起点与终点相同）'));
      syncButtons();scheduleOverview();
    }
    function drawOverview(){
      frame=null;
      if(destroyed)return;
      if(!hasTree()||!overviewVisible||!map||!overview){if(overview)overview.hidden=true;mapTransform=null;return;}
      const geometry=view.geometry(),nodes=geometry?.nodes||[],width=geometry?.width||host.clientWidth,height=geometry?.height||host.clientHeight;
      if(!nodes.length||!width||!height){overview.hidden=true;mapTransform=null;return;}
      // View geometry uses card CENTER coordinates in main-canvas pixels.
      const bounds={left:0,top:0,right:width,bottom:height};
      nodes.forEach(node=>{
        bounds.left=Math.min(bounds.left,node.x-node.width/2);bounds.right=Math.max(bounds.right,node.x+node.width/2);
        bounds.top=Math.min(bounds.top,node.y-node.height/2);bounds.bottom=Math.max(bounds.bottom,node.y+node.height/2);
      });
      const scale=Math.min(184/Math.max(1,bounds.right-bounds.left),104/Math.max(1,bounds.bottom-bounds.top));
      const offsetX=100-(bounds.left+bounds.right)*scale/2,offsetY=60-(bounds.top+bounds.bottom)*scale/2;
      mapTransform={scale,offsetX,offsetY,width,height};
      const x=value=>value*scale+offsetX,y=value=>value*scale+offsetY;
      const fragment=document.createDocumentFragment(),visible=new Map(nodes.map(node=>[node.id,node]));
      const group=svg('g',{'aria-hidden':'true'});
      index.edges.forEach(edge=>{
        const parent=visible.get(edge.parentId),child=visible.get(edge.childId);if(!parent||!child)return;
        group.append(svg('line',{x1:x(parent.x),y1:y(parent.y),x2:x(child.x),y2:y(child.y),class:'mindmap-overview-edge'}));
      });
      nodes.forEach(node=>{
        const type=index.nodes.get(node.id)?.type;
        group.append(svg('rect',{x:x(node.x-node.width/2),y:y(node.y-node.height/2),
          width:Math.max(2,node.width*scale),height:Math.max(2,node.height*scale),rx:1.5,
          fill:colorFor(type)||'#548e88',class:'mindmap-overview-node'}));
      });
      group.append(svg('rect',{x:x(0),y:y(0),width:width*scale,height:height*scale,rx:2,class:'mindmap-overview-viewport'}));
      fragment.append(group);map.replaceChildren(fragment);overview.hidden=false;
    }
    function scheduleOverview(){if(!destroyed&&frame===null)frame=window.requestAnimationFrame(drawOverview);}
    async function download(format){
      if(!hasTree()||exporting)return;
      const snapshot=view.snapshot();if(!snapshot)return;
      const started=generation;exporting=true;syncButtons();text(exportStatus,'正在准备 '+format.toUpperCase()+'…');
      const isCurrent=()=>!destroyed&&generation===started;
      try{
        const size=await exporter.download(snapshot,format,{filename:'实体关系脑图',isCurrent,labelOf,colorFor});
        const scaled=size?.scaled||size?.sourceWidth>size?.width||size?.sourceHeight>size?.height;
        if(isCurrent())text(exportStatus,format.toUpperCase()+' 已导出'+(size?.width&&size?.height?' · '+size.width+' × '+size.height:'')+(scaled?'（已整体缩小，保留全部节点）':''));
      }catch(error){
        if(isCurrent())text(exportStatus,'导出失败，请重试'+(error?.message?'：'+String(error.message):''));
      }finally{if(isCurrent()){exporting=false;syncButtons();}}
    }
    function updateData(){
      if(destroyed)return;
      generation++;exporting=false;const tree=view.getTree();index=tree?model.indexTree(tree):null;
      resetSearch();resetTrace(tree?TRACE_NOTE:'',false);makeEndpoints();text(exportStatus,'');syncButtons();
      if(overview)overview.hidden=true;map?.replaceChildren();mapTransform=null;scheduleOverview();
    }
    listen(search,'input',showSearch);
    listen(search,'focus',()=>{if(search.value.trim())showSearch();});
    listen(search,'keydown',event=>{
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();moveSearch(event.key==='ArrowDown'?1:-1);}
      else if(event.key==='Enter'&&matches.length&&!results?.hidden){event.preventDefault();chooseResult(matches[Math.max(0,activeResult)]);}
      else if(event.key==='Escape'){event.preventDefault();hideResults();}
    });
    listen(document,'pointerdown',event=>{if(event.target!==search&&!results?.contains(event.target))hideResults();});
    [from,to].forEach(select=>listen(select,'change',()=>resetTrace()));
    listen(run,'click',trace);listen(clear,'click',()=>resetTrace('已清除路径高亮 · '+TRACE_NOTE));
    listen(toggle,'click',()=>{overviewVisible=!overviewVisible;syncButtons();if(!overviewVisible&&overview)overview.hidden=true;scheduleOverview();});
    listen(map,'click',event=>{
      if(!mapTransform||!hasTree()||!overviewVisible)return;
      const box=map.getBoundingClientRect();if(!box.width||!box.height)return;
      const pointX=(event.clientX-box.left)*200/box.width,pointY=(event.clientY-box.top)*120/box.height;
      const {scale,offsetX,offsetY,width,height}=mapTransform;
      view.pan(width/2-(pointX-offsetX)/scale,height/2-(pointY-offsetY)/scale);scheduleOverview();
    });
    listen(map,'keydown',event=>{
      if(!mapTransform||!hasTree()||!overviewVisible)return;
      const step=(event.shiftKey?.4:.2),{width,height}=mapTransform;
      const moves={ArrowLeft:[width*step,0],ArrowRight:[-width*step,0],ArrowUp:[0,height*step],ArrowDown:[0,-height*step]};
      const move=moves[event.key];if(!move)return;event.preventDefault();view.pan(...move);scheduleOverview();
    });
    listen(svgButton,'click',()=>download('svg'));listen(pngButton,'click',()=>download('png'));
    const unsubscribe=view.subscribe(event=>{
      if(event?.type==='render'||event?.type==='clear')updateData();else scheduleOverview();
    });
    const observer=typeof window.ResizeObserver==='function'?new window.ResizeObserver(scheduleOverview):null;
    observer?.observe(host);
    const controller={destroy(){
      if(destroyed)return;destroyed=true;generation++;
      if(frame!==null)window.cancelAnimationFrame(frame);frame=null;
      unsubscribe?.();observer?.disconnect();listeners.splice(0).forEach(remove=>remove());
      index=null;exporting=false;resetSearch();resetTrace('');text(exportStatus,'');syncButtons();
      if(overview)overview.hidden=true;map?.replaceChildren();mapTransform=null;
      if(mounted.get(root)===controller)mounted.delete(root);
    }};
    mounted.set(root,controller);updateData();return controller;
  }
  return {mount};
});
