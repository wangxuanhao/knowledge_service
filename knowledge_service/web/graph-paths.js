/* Paths over the currently displayed facts. No queries or inferred relationships. */
(function(root){
  function findPath(graph,from,to,{directed=true,maxHops=5}={}){
    const nodes=new Set((graph?.nodes||[]).filter(n=>!n.virtual&&n.kind!=='attribute_value').map(n=>n.id));
    if(!nodes.has(from)||!nodes.has(to))return null;
    if(from===to)return {nodes:[from],steps:[]};
    const hops=Math.max(0,Math.min(10,Number(maxHops)||0)),adjacency=new Map();
    function add(a,b,edge,reverse){if(!adjacency.has(a))adjacency.set(a,[]);adjacency.get(a).push({from:a,to:b,edge,reverse});}
    for(const edge of graph.edges||[]){
      if(edge.virtual||edge.kind==='attribute_edge'||edge.type==='KS_ATTRIBUTE'||!nodes.has(edge.subject_id)||!nodes.has(edge.object_id))continue;
      add(edge.subject_id,edge.object_id,edge,false);
      if(!directed)add(edge.object_id,edge.subject_id,edge,true);
    }
    const queue=[{id:from,depth:0}],seen=new Set([from]),parents=new Map();
    for(let cursor=0;cursor<queue.length;cursor++){
      const current=queue[cursor];if(current.depth>=hops)continue;
      for(const step of adjacency.get(current.id)||[]){
        if(seen.has(step.to))continue;
        seen.add(step.to);parents.set(step.to,step);
        if(step.to===to){
          const steps=[];let id=to;
          while(id!==from){const previous=parents.get(id);steps.push(previous);id=previous.from;}
          steps.reverse();return {nodes:[from,...steps.map(s=>s.to)],steps};
        }
        queue.push({id:step.to,depth:current.depth+1});
      }
    }
    return null;
  }
  root.GraphPaths={findPath};
  if(typeof module!=='undefined')module.exports=root.GraphPaths;
})(globalThis);
