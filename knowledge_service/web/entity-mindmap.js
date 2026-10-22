/* Exact-root, scope-owned mindmap requests; independent of selector page contents. */
(function(root,factory){
  const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityMindmap=api;
})(typeof window==='object'?window:globalThis,()=>{
  function createController({getProject,getScope,ticket=()=>null,accepts=()=>true,api,prepare,render}){
    let epoch=0;
    function begin(){
      const project=getProject();if(!project)throw Error('请先选择项目');
      const token=++epoch,scope=JSON.parse(JSON.stringify(getScope())),scopeTicket=ticket();
      const current=()=>token===epoch&&project===getProject()&&accepts(scopeTicket)&&JSON.stringify(scope)===JSON.stringify(getScope());
      return {project,scope,current};
    }
    function validDepth(value){
      const depth=Number(value);if(value===''||!Number.isInteger(depth)||depth<0||depth>5)throw Error('脑图深度请输入 0–5 的整数');return depth;
    }
    async function drawRoot(id,depth,request){
      const {project,scope,current}=request;
      let result;
      try{result=await api('/api/projects/'+encodeURIComponent(project)+'/mindmap',{...scope,root_id:id,depth});}
      catch(error){if(!current())return null;throw error;}
      if(!current())return null;
      render(result);return result;
    }
    return {
      invalidate(){epoch++;},
      async draw(id,value){
        const depth=validDepth(value);if(!id)throw Error('请搜索并选择根实体，或从实体详情点击“展开脑图”');
        return drawRoot(id,depth,begin());
      },
      async open(row,value){
        const depth=validDepth(value);if(!row?.id)throw Error('缺少根实体 ID');
        const request=begin();let result;
        try{result=await api('/api/projects/'+encodeURIComponent(request.project)+'/records/page',{...request.scope,kinds:['entity'],record_ids:[row.id],limit:1,include_total:false});}
        catch(error){if(!request.current())return null;throw error;}
        if(!request.current())return null;
        const exact=result.records?.find(item=>item.id===row.id&&item.kind==='entity');
        if(!exact)throw Error('当前已应用范围内没有该实体，请调整筛选后重试');
        prepare(exact);if(!request.current())return null;
        return drawRoot(exact.id,depth,request);
      },
    };
  }
  return {createController};
});
