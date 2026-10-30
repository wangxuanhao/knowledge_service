/* Card-based tree presentation. Requests and scope ownership stay in EntityMindmap. */
(function(root,factory){
  const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityMindmapView=api;
})(typeof window==='object'?window:globalThis,()=>{
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const richText=value=>String(value??'').replace(/[{}|]/g,c=>({'{':'｛','}':'｝','|':'｜'}[c]));
  const units=char=>char.codePointAt(0)>255?2:1;
  function wrapName(value,limit=22,maxLines=2){
    const chars=Array.from(String(value||'未命名实体').replace(/\s+/g,' ').trim()),lines=[];
    let line='',width=0;
    for(let i=0;i<chars.length;i++){
      const char=chars[i],size=units(char);
      if(width+size>limit){
        if(lines.length===maxLines-1){
          while(width>limit-2){const last=Array.from(line).pop();line=line.slice(0,-last.length);width-=units(last);}
          lines.push(line.trimEnd()+'…');return lines.join('\n');
        }
        lines.push(line.trim());line='';width=0;
      }
      line+=char;width+=size;
    }
    lines.push(line.trim());
    if(lines.length===2){
      const widthOf=text=>Array.from(text).reduce((n,char)=>n+units(char),0);
      // Avoid leaving a closing book-title mark or a single CJK character alone.
      while(widthOf(lines[1])<8&&widthOf(lines[0])>widthOf(lines[1])+2){
        const last=Array.from(lines[0]).pop();lines[0]=lines[0].slice(0,-last.length);lines[1]=last+lines[1];
      }
    }
    return lines.join('\n');
  }
  function prepareTree(source){
    function copy(node,key,depth){
      const children=(node.children||[]).map((child,i)=>copy(child,key+'.'+i,depth+1));
      return {id:key,entityId:node.id,name:node.text||'未命名实体',type:node.type,
        predicate:node.predicate,children,collapsed:children.length>0&&depth>=2};
    }
    const tree=copy(source,'mindmap-root',0);
    const secondLevelLeaves=tree.children.reduce((n,node)=>n+Math.max(1,node.children.length),0);
    if(secondLevelLeaves>7)tree.children.forEach(node=>{node.collapsed=node.children.length>0;});
    return tree;
  }
  function layout(tree,canvasWidth,canvasHeight){
    let total=0,visible=0,depth=0;
    const leafParents=[];
    function visit(node,level,shown,parent=null){
      total++;
      if(shown){visible++;depth=Math.max(depth,level);if(node.collapsed||!node.children.length)leafParents.push(parent);}
      node.children.forEach(child=>visit(child,level+1,shown&&!node.collapsed,node.id));
    }
    visit(tree,0,true);
    // ECharts' tidy tree gives cousins twice the separation of siblings. Reserve
    // that same span, so a pair of siblings never gets squeezed by other branches.
    let span=leafParents[0]===leafParents.at(-1)?1:2;
    for(let i=1;i<leafParents.length;i++)span+=leafParents[i]===leafParents[i-1]?1:2;
    const step=Math.max(244,Math.min(288,(canvasWidth-264)/Math.max(1,depth)));
    const width=Math.max(1,depth*step),height=Math.max(160,span*128);
    return {width,height,left:Math.max(132,(canvasWidth-width)/2),top:(canvasHeight-height)/2,total,visible};
  }
  function create({chart,host,summary,empty,buttons,zoomLabel,labelOf=value=>value||'实体',colorFor=()=> '#548e88'}){
    let tree=null,truncated=false,styledZoom=1,roamFrame=null;
    let indexed=null,focusedId=null,viewFrame=null;
    const Model=globalThis.EntityMindmapModel;
    const nodes=new Map(),listeners=new Set(),pathNodes=new Set(),pathEdges=new Set();
    function notify(type){listeners.forEach(listener=>listener({type}));}
    function changed(){
      if(!tree||viewFrame!==null)return;
      viewFrame=requestAnimationFrame(()=>{viewFrame=null;notify('view');});
    }
    function syncTools(){
      Object.values(buttons).forEach(button=>{button.disabled=!tree;});
      zoomLabel.textContent=tree?Math.round((chart.getOption().series?.[0]?.zoom||1)*100)+'%':'100%';
    }
    function styled(node,isRoot=false,scale=1){
      const color=isRoot?'#15534f':colorFor(node.type),type=labelOf(node.type)||'实体',textWidth=(isRoot?196:176)*scale;
      const relation=isRoot?'根实体':(node.predicate?labelOf(node.predicate):'关联实体');
      const branches=node.children.length?` · ${node.collapsed?'＋':'−'} ${node.children.length} 个分支`:'';
      const marked=node.id===focusedId||pathNodes.has(node.id),onPath=pathEdges.has(node.id);
      return {...node,children:node.children.map(child=>styled(child,false,scale)),
        symbol:'path://M12 0H196Q208 0 208 12V90Q208 102 196 102H12Q0 102 0 90V12Q0 0 12 0Z',symbolSize:isRoot?[228,106]:[208,102],
        itemStyle:{color:isRoot?'#15534f':marked?'#fff4df':'#ffffff',borderColor:marked?'#bd742b':isRoot?'#15534f':color+'80',borderWidth:marked?2.5*scale:isRoot?0:1.2*scale,
          shadowColor:'rgba(21,66,66,.08)',shadowBlur:10*scale,shadowOffsetY:3*scale},
        lineStyle:{color:onPath?'#bd742b':'#b6cfcc',width:(onPath?2.8:1.6)*scale,opacity:(pathEdges.size&&!onPath)? .4:1},
        label:{position:'inside',align:'center',verticalAlign:'middle',width:textWidth,
          formatter:`{relation|${richText(wrapName(relation,27,1))}}\n{name|${richText(wrapName(node.name,isRoot?26:22))}}\n{meta|${richText(wrapName(type+branches,29,1))}}`,
          rich:{relation:{align:'left',width:textWidth,fontSize:11*scale,fontWeight:600,color:isRoot?'#b6dad5':color,lineHeight:20*scale},
            name:{align:'left',width:textWidth,fontSize:15*scale,fontWeight:600,color:isRoot?'#ffffff':'#173f3d',lineHeight:22*scale},
            meta:{align:'left',width:textWidth,fontSize:11*scale,color:isRoot?'#b6dad5':'#788f8c',lineHeight:20*scale}}},
        emphasis:{itemStyle:{borderColor:isRoot?'#15534f':color,borderWidth:2*scale,shadowBlur:14*scale}}};
    }
    function syncView(){
      if(!tree)return;
      const {zoom=1,center}=chart.getOption().series[0];
      if(zoom===styledZoom)return;
      styledZoom=zoom;
      // ECharts keeps tree labels in screen pixels; scale all card typography
      // with the same camera zoom as the symbols, spacing and relationship lines.
      chart.setOption({series:[{id:'entity-mindmap-tree',zoom,center,data:[styled(tree,true,zoom)],
        lineStyle:{width:1.6*zoom},emphasis:{lineStyle:{width:2*zoom}}}]});
      syncTools();
    }
    function draw(keepView=false,animate=true){
      if(!tree||!host.clientWidth||!host.clientHeight)return;
      const prior=keepView?chart.getOption().series?.[0]:null;
      const zoom=prior?.zoom||1;styledZoom=zoom;
      chart.resize();
      const geometry=layout(tree,host.clientWidth,host.clientHeight);
      chart.setOption({animation:animate,animationDuration:220,animationDurationUpdate:220,animationEasingUpdate:'cubicOut',
        aria:{enabled:true},tooltip:{trigger:'item',confine:true,borderColor:'#d3e3e0',padding:14,
          textStyle:{fontFamily:'"Microsoft YaHei",sans-serif',fontSize:12,color:'#234e49'},
          formatter:({data})=>`<div style="max-width:280px;white-space:normal;overflow-wrap:anywhere"><b>${escapeHtml(data.name)}</b><br>${escapeHtml(labelOf(data.type)||'实体')}${data.predicate?'<br>关系：'+escapeHtml(labelOf(data.predicate)):''}${data.children.length?'<br>点击'+(data.collapsed?'展开':'收起')+' '+data.children.length+' 个分支':''}</div>`},
        series:[{id:'entity-mindmap-tree',type:'tree',layout:'orthogonal',orient:'LR',data:[styled(tree,true,zoom)],
          left:geometry.left,top:geometry.top,width:geometry.width,height:geometry.height,
          right:null,bottom:null,roam:true,nodeScaleRatio:1,scaleLimit:{min:.25,max:2},zoom,center:prior?.center||null,
          expandAndCollapse:false,initialTreeDepth:-1,edgeShape:'curve',
          lineStyle:{color:'#b6cfcc',width:1.6*zoom,curveness:.45},
          emphasis:{focus:'none',scale:false,lineStyle:{color:'#428e84',width:2*zoom}},
        }]},{notMerge:true});
      summary.textContent=`已显示 ${geometry.visible} / ${geometry.total} 个节点${truncated?' · 部分结果，请缩小深度继续探索':''}`;
      empty.hidden=true;syncTools();
      changed();
    }
    function onClick(event){
      const node=nodes.get(event.data?.id);if(!node?.children.length)return;
      node.collapsed=!node.collapsed;draw(true);
    }
    function onRoam(){
      syncTools();
      changed();
      if(!tree||roamFrame!==null||chart.getOption().series[0].zoom===styledZoom)return;
      // Let the native wheel handler finish its transform before re-rendering.
      roamFrame=requestAnimationFrame(()=>{roamFrame=null;syncView();});
    }
    chart.on('click',onClick);chart.on('treeroam',onRoam);chart.on('rendered',changed);
    const observer=typeof ResizeObserver==='function'?new ResizeObserver(()=>draw(true)):null;
    observer?.observe(host);
    function geometry(){
      const result={nodes:[],width:host.clientWidth,height:host.clientHeight};
      if(!tree)return result;
      const series=chart.getModel().getSeriesByIndex(0),data=series.getData(),zoom=Number(series.get('zoom'))||1;
      data.each(index=>{
        const raw=data.getRawDataItem(index),element=data.getItemGraphicEl(index);
        if(!nodes.has(raw?.id)||!element)return;
        const point=element.transformCoordToGlobal(0,0);
        if(!point.every(Number.isFinite))return;
        result.nodes.push({id:raw.id,name:raw.name,x:point[0],y:point[1],width:(raw.id===tree.id?228:208)*zoom,height:(raw.id===tree.id?106:102)*zoom});
      });
      return result;
    }
    function pan(dx,dy){
      if(!tree||!Number.isFinite(dx)||!Number.isFinite(dy))return;
      chart.dispatchAction({type:'treeRoam',seriesIndex:0,dx,dy});
      const {center}=chart.getOption().series[0];
      chart.setOption({series:[{id:'entity-mindmap-tree',center}]});changed();
    }
    function frame(ids,fit=false){
      let visible=geometry().nodes.filter(node=>ids.includes(node.id));if(!visible.length)return;
      if(fit){
        const left=Math.min(...visible.map(n=>n.x-n.width/2)),right=Math.max(...visible.map(n=>n.x+n.width/2));
        const top=Math.min(...visible.map(n=>n.y-n.height/2)),bottom=Math.max(...visible.map(n=>n.y+n.height/2));
        const current=chart.getOption().series[0].zoom||1;
        const zoom=Math.min(2,Math.max(.25,current*Math.min((host.clientWidth-64)/Math.max(1,right-left),(host.clientHeight-64)/Math.max(1,bottom-top))));
        chart.dispatchAction({type:'treeRoam',seriesIndex:0,zoom:zoom/current,originX:host.clientWidth/2,originY:host.clientHeight/2});
        syncView();visible=geometry().nodes.filter(node=>ids.includes(node.id));
      }
      pan(host.clientWidth/2-(Math.min(...visible.map(n=>n.x))+Math.max(...visible.map(n=>n.x)))/2,
        host.clientHeight/2-(Math.min(...visible.map(n=>n.y))+Math.max(...visible.map(n=>n.y)))/2);
    }
    function reveal(id){Model.reveal(indexed,id).forEach(parent=>{nodes.get(parent).collapsed=false;});}
    const view={
      render(result){
        tree=prepareTree(result.tree);truncated=!!result.truncated;nodes.clear();
        indexed=Model.indexTree(tree);indexed.nodes.forEach((node,id)=>nodes.set(id,node));
        focusedId=null;pathNodes.clear();pathEdges.clear();draw();notify('render');
      },
      zoom(factor){
        if(!tree)return;
        chart.dispatchAction({type:'treeRoam',seriesIndex:0,zoom:factor,originX:host.clientWidth/2,originY:host.clientHeight/2});
      },
      reset(){draw();},
      getTree(){return tree;},
      snapshot(){return tree?{tree:JSON.parse(JSON.stringify(tree)),truncated}:null;},
      geometry,pan,
      subscribe(listener){listeners.add(listener);return ()=>listeners.delete(listener);},
      focus(id){
        if(!nodes.has(id))return false;
        focusedId=id;reveal(id);draw(true,false);frame([id]);return true;
      },
      trace(fromId,toId){
        const path=tree?Model.pathBetween(indexed,fromId,toId):null;
        pathNodes.clear();pathEdges.clear();focusedId=null;
        if(!path){if(tree)draw(true,false);return null;}
        path.nodeIds.forEach(id=>{pathNodes.add(id);reveal(id);});path.edgeIds.forEach(id=>pathEdges.add(id));
        draw(true,false);frame(path.nodeIds,true);return path;
      },
      clearTrace(){pathNodes.clear();pathEdges.clear();if(tree)draw(true,false);},
      clear(){
        tree=null;indexed=null;focusedId=null;nodes.clear();pathNodes.clear();pathEdges.clear();
        if(roamFrame!==null){cancelAnimationFrame(roamFrame);roamFrame=null;}
        if(viewFrame!==null){cancelAnimationFrame(viewFrame);viewFrame=null;}
        chart.clear();summary.textContent='选择根实体，开始探索关系';empty.hidden=false;syncTools();notify('clear');
      },
      destroy(){
        observer?.disconnect();chart.off('click',onClick);chart.off('treeroam',onRoam);chart.off('rendered',changed);
        if(roamFrame!==null)cancelAnimationFrame(roamFrame);if(viewFrame!==null)cancelAnimationFrame(viewFrame);listeners.clear();
      },
    };
    return view;
  }
  return {prepareTree,layout,wrapName,escapeHtml,create};
});
