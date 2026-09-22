/* Exact type filtering of the displayed result, independent of neighbourhood exploration. */
(function(root){
  function isAttributeNode(node){
    return node?.kind==='attribute_value'||(node?.virtual===true&&node?.type==='KS_ATTRIBUTE_VALUE');
  }
  function isAttributeEdge(edge){
    return edge?.kind==='attribute_edge'||edge?.type==='KS_ATTRIBUTE';
  }
  function entityNodes(graph){return graph.nodes.filter(node=>!isAttributeNode(node));}
  function relationEdges(graph){return graph.edges.filter(edge=>!isAttributeEdge(edge));}
  function stableJson(value){
    if(Array.isArray(value))return value.map(stableJson);
    if(value&&typeof value==='object')return Object.fromEntries(
      Object.keys(value).sort().filter(key=>value[key]!==undefined)
        .map(key=>[key,stableJson(value[key])]));
    return value;
  }
  function createGraphRequestGate(){
    let generation=0,busyOwner=null;
    const snapshot=(project,scope,node)=>({project:project||'',
      scope:JSON.stringify(stableJson(scope||{})),node:node||null});
    return {
      begin(project,scope,node){const ticket={generation:++generation,...snapshot(project,scope,node)};busyOwner=ticket;return ticket;},
      invalidate(){generation+=1;busyOwner=null;},
      isBusy(){return busyOwner!==null;},
      release(ticket){
        if(busyOwner!==ticket||ticket?.generation!==generation)return false;
        busyOwner=null;return true;
      },
      isCurrent(ticket,project,scope,node){
        if(!ticket||ticket.generation!==generation)return false;
        const current=snapshot(project,scope,node);
        return ticket.project===current.project&&ticket.scope===current.scope&&ticket.node===current.node;
      },
    };
  }
  function selectType(graph,type){
    if(!type)return graph;
    const nodes=entityNodes(graph).filter(n=>n.type===type), ids=new Set(nodes.map(n=>n.id));
    return {...graph,nodes,edges:relationEdges(graph).filter(e=>ids.has(e.subject_id)&&ids.has(e.object_id))};
  }
  root.GraphTypeFilter={selectType,entityNodes,relationEdges,isAttributeNode,isAttributeEdge,createGraphRequestGate};
  if(typeof module!=='undefined')module.exports={selectType,entityNodes,relationEdges,isAttributeNode,isAttributeEdge,createGraphRequestGate};
})(globalThis);
