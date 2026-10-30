/* Pure queries over prepareTree occurrence IDs; entity IDs never merge nodes. */
(function(root,factory){
  const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityMindmapModel=api;
})(typeof window==='object'?window:globalThis,()=>{
  function indexTree(tree){
    const nodes=new Map(),parents=new Map(),edges=[];
    const pending=tree?[{node:tree,parent:null}]:[];
    while(pending.length){
      const {node,parent}=pending.pop();
      if(nodes.has(node.id))throw new Error('脑图节点出现位置 ID 重复');
      nodes.set(node.id,node);
      if(parent){
        parents.set(node.id,parent.id);
        edges.push({id:node.id,parentId:parent.id,childId:node.id,predicate:node.predicate});
      }
      const children=node.children||[];
      for(let i=children.length-1;i>=0;i--)pending.push({node:children[i],parent:node});
    }
    return {nodes,parents,edges};
  }

  function search(index,query,labelOf=value=>value){
    const needle=String(query??'').trim().toLocaleLowerCase();
    if(!needle)return [];
    return Array.from(index.nodes.values()).filter(node=>
      [node.name,node.type,labelOf(node.type),node.entityId].some(value=>String(value??'').toLocaleLowerCase().includes(needle)));
  }

  function reveal(index,id){
    if(!index.nodes.has(id))return [];
    const ancestors=[];
    while(index.parents.has(id)){id=index.parents.get(id);ancestors.push(id);}
    return ancestors.reverse();
  }

  function pathBetween(index,fromId,toId){
    if(!index.nodes.has(fromId)||!index.nodes.has(toId))return null;
    const from=[...reveal(index,fromId),fromId],to=[...reveal(index,toId),toId];
    let shared=0;
    while(shared<from.length&&shared<to.length&&from[shared]===to[shared])shared++;
    if(!shared)return null;
    const nodeIds=from.slice(shared-1).reverse().concat(to.slice(shared));
    const edgeIds=nodeIds.slice(1).map((id,i)=>index.parents.get(nodeIds[i])===id?nodeIds[i]:id);
    return {nodeIds,edgeIds};
  }

  function canonicalTree(tree){
    if(!tree)return null;
    function clone(value){
      if(Array.isArray(value))return value.map(clone);
      if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,clone(item)]));
      return value;
    }
    const copy=clone(tree),pending=[copy];
    while(pending.length){
      const node=pending.pop();node.collapsed=false;
      if(!node.children)node.children=[];
      pending.push(...node.children);
    }
    return copy;
  }
  return {indexTree,search,pathBetween,reveal,canonicalTree};
});
