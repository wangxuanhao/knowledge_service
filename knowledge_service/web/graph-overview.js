/* One bounded, owner-scoped default view. Explicit navigation has priority. */
(function(root){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const keyOf=value=>JSON.stringify([value?.projectId||'',value?.scope||{},value?.ontologyId||null,value?.versionContext||null]);
  function emptyGuidance(scope={}){
    const filtered=Boolean(scope.known_at||scope.valid_at||scope.applicability||
      (scope.filters&&Object.keys(scope.filters).length)||(scope.ontology_scope&&scope.ontology_scope!=='all'));
    return filtered?{filtered,message:'当前筛选范围没有正式实体或关系。可调整或清除筛选后重试。',
      actions:[{id:'adjust',label:'调整筛选'},{id:'reset',label:'清除全部筛选'}]}:
      {filtered,message:'当前项目尚无正式实体或关系。可先到资料库查看处理结果，或前往构建与任务、候选核对。',
        actions:[{id:'sources',label:'资料库'},{id:'jobs',label:'构建与任务'},{id:'candidates',label:'候选核对'}]};
  }
  function create({readContext,isReady,isVisible,view,setState=()=>{}}){
    let epoch=0,entry=null;const cancelled=new Set();
    const current=(owned,serial)=>entry===owned&&serial===epoch&&owned.key===keyOf(readContext())&&isVisible();
    function report(kind,loader,message){
      const manifest=loader?.manifest,dataset=loader?.dataset;
      setState(kind,{message,loadedEntities:dataset?.nodeCount||0,loadedRelations:dataset?.edgeCount||0,
        totalEntities:manifest?.entity_count??null,totalRelations:manifest?.relation_count??null});
    }
    function invalidate(){epoch++;entry=null;view.cancel();}
    function explicit(){
      epoch++;if(view.interrupt)view.interrupt();else view.cancel();const serial=epoch,owned=entry={key:keyOf(readContext()),kind:'explicit'};
      setState('selection',{message:''});
      return {accepts:()=>current(owned,serial)};
    }
    function cancel(){
      epoch++;view.cancel();entry={key:keyOf(readContext()),kind:'cancelled'};
      cancelled.add(entry.key);
      report('cancelled',view.loader,'已取消加载，当前图不完整；可重新开始。');
    }
    async function ensure({retry=false}={}){
      const context=readContext();if(!context?.projectId||!isReady()||!isVisible())return null;
      const key=keyOf(context);if(cancelled.has(key)&&!retry){entry={key,kind:'cancelled'};return null;}
      if(entry?.key===key&&!retry)return entry.pending||null;
      if(retry)cancelled.delete(key);
      epoch++;view.cancel();const serial=epoch,owned=entry={key,kind:'loading'};
      report('loading',null,'正在读取当前范围的图谱…');
      owned.pending=(async()=>{
        let loader;
        try{
          loader=await view.load({projectId:context.projectId,scope:clone(context.scope),batchSize:200});
          if(!current(owned,serial)||!loader)return null;
          const manifest=loader.manifest,small=manifest?.entity_count<=200&&manifest?.relation_count<=400;
          // begin already read one page. Only certified small manifests may
          // finish automatically, with an absolute budget of three pages.
          for(let pages=1;small&&pages<3&&loader.state==='paused'&&!owned.paused;pages++){
            await loader.next();if(!current(owned,serial))return null;
          }
          owned.kind=loader.state==='complete'?(manifest.entity_count||manifest.relation_count?'complete':'empty'):'partial';
          report(owned.kind,loader,owned.kind==='empty'?emptyGuidance(context.scope).message:owned.kind==='complete'?'当前范围图谱已完整加载。':'当前为局部概览；可加载下一批或自动继续。');
          return loader;
        }catch(error){
          if(current(owned,serial)&&error.name!=='AbortError'){owned.kind='error';report('error',loader||view.loader,error.message+' 当前图不完整，请重试。');}
          return null;
        }finally{if(entry===owned)owned.pending=null;}
      })();
      return owned.pending;
    }
    function leave(){
      if(entry?.pending)invalidate();
      else if(entry?.kind==='explicit'){
        epoch++;if(view.interrupt)view.interrupt();else view.cancel();
        if(!view.hasUsableView?.())entry=null;
      }
      else if(view.loader?.state==='loading'||view.loader?.automatic){epoch++;view.cancel();}
    }
    return {ensure,invalidate,explicit,cancel,pause(){if(entry)entry.paused=true;},leave,snapshot:()=>entry?{key:entry.key,kind:entry.kind}:null};
  }
  const api=root.GraphOverview={create,emptyGuidance};
  function install(){
    if(!root.FullGraphView||!root.GraphWorkspaceContext)return;
    const view=root.FullGraphView,adapter={load:options=>view.load(options),cancel:()=>view.stop(),interrupt:()=>view.interrupt(),
      hasUsableView:()=>view.mode==='local'?Boolean(root.GraphWorkspaceContext.hasLocalView):Boolean(view.renderer?.dataset.nodeCount||view.renderer?.dataset.edgeCount||view.loader?.state==='complete'),
      get loader(){return view.loader;}};
    const coordinator=create({readContext:()=>root.GraphWorkspaceContext.read(),isReady:()=>root.GraphWorkspaceContext.ready,
      isVisible:()=>!document.getElementById('tab-search')?.classList.contains('hidden'),view:adapter,
      setState:(kind,detail)=>{root.GraphWorkspaceLayout?.setState(kind,detail);if(kind!=='selection')view.setOverviewState?.(kind,detail);}});
    Object.assign(api,coordinator);
    document.addEventListener('graph-workspace:ready',()=>coordinator.ensure());
    document.addEventListener('graph-workspace:invalidated',()=>coordinator.invalidate());
    document.addEventListener('workspace:page-changed',event=>{if(event.detail?.page!=='search')coordinator.leave();});
    // The existing navigation owner calls ensure after ontology catch-up.
    // Page departure revokes in-flight work, while completed explored views stay.
    document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{
      if(button.dataset.tab!=='search')coordinator.leave();
    }));
    coordinator.ensure();
  }
  if(typeof document!=='undefined'){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
  }
})(typeof window!=='undefined'?window:globalThis);
