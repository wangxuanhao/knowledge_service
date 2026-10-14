/* Project-safe deep links from durable task results to their follow-up views. */
(()=>{
  const targets=new Set(['records','reviews','ontology-model','sources','jobs']);
  const nextTurn=()=>new Promise(resolve=>setTimeout(resolve,0));

  function projectSelector(projectId){
    if(!projectId)throw Error('缺少任务所属项目，无法继续导航');
    const selector=document.getElementById('project');
    const exists=[...(selector?.options||[])].some(option=>option.value===projectId);
    if(!exists)throw Error('该任务所属项目已不存在，无法打开后续页面');
    return selector;
  }

  async function selectProject(projectId,selector=projectSelector(projectId)){
    if(selector.value===projectId)return false;
    selector.value=projectId;
    selector.dispatchEvent(new Event('change'));
    // Project listeners clear and reload their own state. Open the destination only
    // after those synchronous listeners and their first microtasks have run.
    await nextTurn();
    return true;
  }

  const isVisible=name=>{
    const page=document.getElementById('tab-'+name);
    return Boolean(page&&!page.classList.contains('hidden'));
  };

  async function open(target,{projectId}={}){
    if(!targets.has(target))throw Error(`未知的导航目标：${target}`);
    const selector=projectSelector(projectId);
    if(target==='jobs'){
      if(!window.KnowledgeTabs?.open)throw Error('页面导航尚未加载');
      if(!window.RuntimeView?.show)throw Error('后台任务页面尚未加载');
      // Select the jobs panel before dispatching project change. Existing project
      // listeners then refresh jobs (and never the previously visible overview).
      window.KnowledgeTabs.open('runtime',{trigger:false});
      window.RuntimeView.show('jobs',{load:false});
      const switched=await selectProject(projectId,selector);
      if(switched)return 'jobs';
      return window.RuntimeView.show('jobs');
    }
    const switched=await selectProject(projectId,selector);
    if(target==='reviews'){
      // The review page owns a project-change listener that reloads its queue.
      // Reopening an already visible page would issue the same request twice.
      if(switched&&isVisible('reviews'))return 'reviews';
      if(!window.TaskReview?.open)throw Error('知识审核页面尚未加载');
      return window.TaskReview.open();
    }
    if(target==='sources'){
      if(!window.SourcesView?.open)throw Error('原文数据源页面尚未加载');
      return window.SourcesView.open();
    }
    if(!window.KnowledgeTabs?.open)throw Error('页面导航尚未加载');
    // Visible project-scoped pages refresh from their project-change listener.
    // Clicking the tab again here would issue the same requests twice.
    if(switched&&isVisible(target)){
      if(target==='ontology-model'){
        if(!window.OntologyModel?.refresh)throw Error('本体建模页面尚未加载');
        return window.OntologyModel.refresh();
      }
      return target;
    }
    return window.KnowledgeTabs.open(target);
  }

  window.KnowledgeNavigation={open};
})();
