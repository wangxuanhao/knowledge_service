/* Committed writes refresh the two presentation owners independently. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.KnowledgeChanges=api.create({project:()=>typeof current==='string'?current:'',
    invalidateGraph:detail=>root.invalidateKnowledgeGraph?.(detail),
    refreshLedger:detail=>root.refreshKnowledgeLedger?.(detail),
    refreshGraph:detail=>root.refreshKnowledgeGraph?.(detail)});
})(typeof window==='object'?window:globalThis,()=>{
  function create({project,invalidateGraph,refreshLedger,refreshGraph}){
    const jobs=new Map();
    function notify(detail){
      const id=detail.projectId;
      if(!id||id!==project())return Promise.resolve(false);
      invalidateGraph(detail);
      const existing=jobs.get(id);
      if(existing){existing.again=true;existing.detail=detail;return existing.promise;}
      const job={again:true,detail};jobs.set(id,job);
      job.promise=(async()=>{
        let failed=false;
        do{
          job.again=false;
          if(project()!==id)break;
          const results=await Promise.allSettled([
            Promise.resolve().then(()=>refreshLedger(job.detail)),
            Promise.resolve().then(()=>refreshGraph(job.detail)),
          ]);
          failed=results.some(result=>result.status==='rejected'||result.value===false);
        }while(job.again);
        return failed;
      })().finally(()=>jobs.delete(id));
      return job.promise;
    }
    return {notify};
  }
  return {create};
});
