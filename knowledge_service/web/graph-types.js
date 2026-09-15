/* Exact type filtering of the displayed result, independent of neighbourhood exploration. */
(function(root){
  function selectType(graph,type){
    if(!type)return graph;
    const nodes=graph.nodes.filter(n=>n.type===type), ids=new Set(nodes.map(n=>n.id));
    return {...graph,nodes,edges:graph.edges.filter(e=>ids.has(e.subject_id)&&ids.has(e.object_id))};
  }
  root.GraphTypeFilter={selectType};
  if(typeof module!=='undefined')module.exports={selectType};
})(globalThis);
