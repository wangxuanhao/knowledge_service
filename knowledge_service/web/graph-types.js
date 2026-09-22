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
  function selectType(graph,type){
    if(!type)return graph;
    const nodes=entityNodes(graph).filter(n=>n.type===type), ids=new Set(nodes.map(n=>n.id));
    return {...graph,nodes,edges:relationEdges(graph).filter(e=>ids.has(e.subject_id)&&ids.has(e.object_id))};
  }
  root.GraphTypeFilter={selectType,entityNodes,relationEdges,isAttributeNode,isAttributeEdge};
  if(typeof module!=='undefined')module.exports={selectType,entityNodes,relationEdges,isAttributeNode,isAttributeEdge};
})(globalThis);
