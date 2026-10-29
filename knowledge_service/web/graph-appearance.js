/* Shared presentation only: canonical type identity is independent of labels and pages. */
(function(root){
  'use strict';
  const tokens=Object.freeze({background:'#ffffff',edge:'#a8c9e2',text:'#888995',outline:'#ffffff',selected:'#1689e9'});
  const rgbCache=new Map();
  const attribute=record=>record?.kind==='attribute_value'||record?.virtual===true&&record?.type==='KS_ATTRIBUTE_VALUE';
  function categoryKey(record){return attribute(record)?'attribute:':'entity:'+String(record?.type||'');}
  function colorFor(record){
    const palette=root.GraphPalette;
    if(attribute(record))return palette?.attribute||'#8c7b68';
    const type=record?.type||'';
    return palette?.colorFor(type==='属性值'?'entity:'+type:type)||'#0e7c7b';
  }
  function rgbFor(record){
    const hex=colorFor(record);
    if(!rgbCache.has(hex))rgbCache.set(hex,new Uint8Array([1,3,5].map(i=>parseInt(hex.slice(i,i+2),16))));
    return rgbCache.get(hex);
  }
  function sizesFor(nodes,edges){
    if(nodes.length>200||edges.length>400)return null;
    const degrees=new Map(nodes.map(node=>[node.id,0]));
    for(const edge of edges){if(edge.subject_id===edge.object_id||!degrees.has(edge.subject_id)||!degrees.has(edge.object_id))continue;
      degrees.set(edge.subject_id,degrees.get(edge.subject_id)+1);degrees.set(edge.object_id,degrees.get(edge.object_id)+1);}
    return new Map(Array.from(degrees,([id,degree])=>[id,5+Math.min(degree*.3,5)]));
  }
  function labelsFor(nodes,preferred=[],limit=50){
    const present=new Set(nodes.map(node=>node.id)),ordered=new Set();
    for(const id of preferred)if(present.has(id))ordered.add(id);
    const maximum=Math.max(0,Math.min(70,Math.trunc(limit)||0));
    if(ordered.size>=maximum)return new Set([...ordered].slice(0,maximum));
    const ordinary=nodes.filter(node=>node.label?.show!==false&&!ordered.has(node.id));
    const count=Math.min(maximum-ordered.size,ordinary.length);
    for(let i=0;i<count;i++)ordered.add(ordinary[count===1?Math.floor(ordinary.length/2):Math.floor(i*(ordinary.length-1)/(count-1))].id);
    return ordered;
  }
  function avoidLabelOverlap(nodes,candidates,{project,measure,width,height,size=()=>[20,20]}){
    const points=new Map(nodes.map(node=>[node.id,project(node)])),byId=new Map(nodes.map(node=>[node.id,node])),boxes=[],visible=new Set();
    const intersects=(a,b)=>a.left<b.right+2&&a.right+2>b.left&&a.top<b.bottom+2&&a.bottom+2>b.top;
    for(const id of candidates){
      const node=byId.get(id),point=points.get(id);if(!node||!point)continue;
      const [x,y]=point,[w]=size(node),text=String(node.text||node.name||node.id),left=x+w/2+6;
      const box={left,right:left+Math.min(128,measure(text)),top:y-7,bottom:y+7};
      if(box.left<0||box.right>width||box.top<0||box.bottom>height||boxes.some(other=>intersects(box,other)))continue;
      if(nodes.some(other=>{if(other.id===id)return false;const [nx,ny]=points.get(other.id),[nw,nh]=size(other);return intersects(box,{left:nx-nw/2,right:nx+nw/2,top:ny-nh/2,bottom:ny+nh/2});}))continue;
      boxes.push(box);visible.add(id);
    }
    // Keep one on-screen priority name in an extremely dense local cluster.
    if(!visible.size)for(const id of candidates){const p=points.get(id);if(p&&p[0]>=0&&p[0]<width&&p[1]>=0&&p[1]<height){visible.add(id);break;}}
    return visible;
  }
  root.GraphAppearance=Object.freeze({tokens,colorFor,rgbFor,sizesFor,categoryKey,labelsFor,avoidLabelOverlap});
})(typeof window!=='undefined'?window:globalThis);
