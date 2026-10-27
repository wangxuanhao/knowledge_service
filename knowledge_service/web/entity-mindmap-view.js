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
    let tree=null,truncated=false;
    const nodes=new Map();
    function syncTools(){
      Object.values(buttons).forEach(button=>{button.disabled=!tree;});
      zoomLabel.textContent=tree?Math.round((chart.getOption().series?.[0]?.zoom||1)*100)+'%':'100%';
    }
    function styled(node,isRoot=false){
      const color=isRoot?'#15534f':colorFor(node.type),type=labelOf(node.type)||'实体',textWidth=isRoot?196:176;
      const relation=isRoot?'根实体':(node.predicate?labelOf(node.predicate):'关联实体');
      const branches=node.children.length?` · ${node.collapsed?'＋':'−'} ${node.children.length} 个分支`:'';
      return {...node,children:node.children.map(child=>styled(child)),
        symbol:'path://M12 0H196Q208 0 208 12V90Q208 102 196 102H12Q0 102 0 90V12Q0 0 12 0Z',symbolSize:isRoot?[228,106]:[208,102],
        itemStyle:{color:isRoot?'#15534f':'#ffffff',borderColor:isRoot?'#15534f':color+'80',borderWidth:isRoot?0:1.2,
          shadowColor:'rgba(21,66,66,.08)',shadowBlur:10,shadowOffsetY:3},
        label:{position:'inside',align:'center',verticalAlign:'middle',width:textWidth,
          formatter:`{relation|${richText(wrapName(relation,27,1))}}\n{name|${richText(wrapName(node.name,isRoot?26:22))}}\n{meta|${richText(wrapName(type+branches,29,1))}}`,
          rich:{relation:{align:'left',width:textWidth,fontSize:11,fontWeight:600,color:isRoot?'#b6dad5':color,lineHeight:20},
            name:{align:'left',width:textWidth,fontSize:15,fontWeight:600,color:isRoot?'#ffffff':'#173f3d',lineHeight:22},
            meta:{align:'left',width:textWidth,fontSize:11,color:isRoot?'#b6dad5':'#788f8c',lineHeight:20}}},
        emphasis:{itemStyle:{borderColor:isRoot?'#15534f':color,borderWidth:2,shadowBlur:14}}};
    }
    function draw(keepView=false){
      if(!tree||!host.clientWidth||!host.clientHeight)return;
      const prior=keepView?chart.getOption().series?.[0]:null;
      chart.resize();
      const geometry=layout(tree,host.clientWidth,host.clientHeight);
      chart.setOption({animationDuration:220,animationDurationUpdate:220,animationEasingUpdate:'cubicOut',
        aria:{enabled:true},tooltip:{trigger:'item',confine:true,borderColor:'#d3e3e0',padding:14,
          textStyle:{fontFamily:'"Microsoft YaHei",sans-serif',fontSize:12,color:'#234e49'},
          formatter:({data})=>`<div style="max-width:280px;white-space:normal;overflow-wrap:anywhere"><b>${escapeHtml(data.name)}</b><br>${escapeHtml(labelOf(data.type)||'实体')}${data.predicate?'<br>关系：'+escapeHtml(labelOf(data.predicate)):''}${data.children.length?'<br>点击'+(data.collapsed?'展开':'收起')+' '+data.children.length+' 个分支':''}</div>`},
        series:[{id:'entity-mindmap-tree',type:'tree',layout:'orthogonal',orient:'LR',data:[styled(tree,true)],
          left:geometry.left,top:geometry.top,width:geometry.width,height:geometry.height,
          right:null,bottom:null,roam:true,scaleLimit:{min:.25,max:2},zoom:prior?.zoom||1,center:prior?.center||null,
          expandAndCollapse:false,initialTreeDepth:-1,edgeShape:'curve',
          lineStyle:{color:'#b6cfcc',width:1.6,curveness:.45},
          emphasis:{focus:'none',lineStyle:{color:'#428e84',width:2}},
        }]},{notMerge:true});
      summary.textContent=`已显示 ${geometry.visible} / ${geometry.total} 个节点${truncated?' · 部分结果，请缩小深度继续探索':''}`;
      empty.hidden=true;syncTools();
    }
    chart.on('click',event=>{
      const node=nodes.get(event.data?.id);if(!node?.children.length)return;
      node.collapsed=!node.collapsed;draw(true);
    });
    chart.on('treeroam',syncTools);
    const observer=typeof ResizeObserver==='function'?new ResizeObserver(()=>draw()):null;
    observer?.observe(host);
    return {
      render(result){
        tree=prepareTree(result.tree);truncated=!!result.truncated;nodes.clear();
        const index=node=>{nodes.set(node.id,node);node.children.forEach(index);};index(tree);draw();
      },
      zoom(factor){
        if(!tree)return;
        chart.dispatchAction({type:'treeRoam',seriesIndex:0,zoom:factor,originX:host.clientWidth/2,originY:host.clientHeight/2});syncTools();
      },
      reset(){draw();},
      clear(){tree=null;nodes.clear();chart.clear();summary.textContent='选择根实体，开始探索关系';empty.hidden=false;syncTools();},
    };
  }
  return {prepareTree,layout,wrapName,escapeHtml,create};
});
