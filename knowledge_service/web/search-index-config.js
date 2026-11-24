/* Shared index status; normal updates are automatic. */
(function(global){
  function shouldPoll({open,hidden,state}){return Boolean(open&&!hidden&&['building','leased','not_built','requires_rebuild','failed'].includes(state));}
  function indexStatus(result,error){
    const view=(label,tone,detail,progress=null)=>({label,tone,detail,progress});
    if(error)return view('向量索引状态读取失败','attention','稍后自动重试，也可打开检索索引刷新。');
    if(!result)return view('正在读取向量索引','working','正在核对当前项目的检索状态。');
    const {state,job,watermark,applied_seq:applied}=result;
    const remaining=Number.isSafeInteger(watermark)&&Number.isSafeInteger(applied)?Math.max(0,watermark-applied):null;
    if(result.automatic===false)return view('向量检索未启用','attention','当前服务未配置向量索引。');
    if(state==='ready'&&Number.isSafeInteger(watermark)&&applied===watermark){
      return view('向量检索可用','ready','当前项目可以使用语义和混合检索。');
    }
    if(job?.status==='retry_wait')return view('向量索引等待重试','attention','后台将自动重试，重试完成后可使用语义和混合检索。');
    if(job?.status==='queued')return view('向量索引排队中','working','等待后台处理，完成后会自动更新状态。');
    if(state==='failed'||state==='requires_rebuild'||job?.status==='failed'){
      return view('向量索引需要恢复','attention','可在检索索引中查看原因和恢复操作。');
    }
    if(state==='not_built')return view('向量索引等待建立','working','有正式知识后由后台建立索引。');
    if(state==='building'||state==='leased'||state==='ready'){
      const progress=job?.status==='running'&&Number.isFinite(job.progress)&&job.progress>=0&&job.progress<=100?job.progress:null;
      const detail=job?.status==='running'&&job.stage?job.stage
        :remaining>0?`还有 ${remaining} 项变更待同步。`:'后台正在处理索引变更。';
      return view('向量索引同步中','working',detail,progress);
    }
    return view('向量索引状态待核对','attention','可在检索索引中重新读取状态。');
  }

  function createMonitor({read,render,schedule=setTimeout,cancel=clearTimeout}){
    let project='',visible=false,result=null,failed=false,request=null,timer=null,generation=0;
    function stop(){
      generation++;
      request=null;
      cancel(timer);
      timer=null;
    }
    function load(){
      if(!visible||!project)return;
      if(request)return request;
      cancel(timer);
      timer=null;
      const stamp=generation;
      request=read(project).then(value=>{
        if(stamp!==generation)return;
        result=value;
        failed=false;
      }).catch(()=>{
        if(stamp!==generation)return;
        result=null;
        failed=true;
      }).finally(()=>{
        if(stamp!==generation)return;
        request=null;
        render(result,failed);
        const status=indexStatus(result,failed);
        const settled=status.tone==='ready'||result?.automatic===false;
        const empty=result?.state==='not_built'&&result.record_counts&&Object.values(result.record_counts).every(count=>count===0)&&!result.job;
        const state=result?.state==='ready'?'building':result?.state;
        if(!settled&&!empty&&(!result||shouldPoll({open:visible,hidden:false,state}))){
          timer=schedule(load,status.tone==='attention'?15000:5000);
        }
      });
      return request;
    }
    function sync(nextProject,nextVisible,{refresh=false,invalidate=false}={}){
      const changed=project!==nextProject,resuming=!visible&&nextVisible;
      if(changed||invalidate||visible&&!nextVisible)stop();
      if(changed||invalidate){result=null;failed=false;}
      project=nextProject;
      visible=nextVisible;
      if(!project||!visible)return;
      render(result,failed);
      if(changed||resuming||refresh||invalidate||!result&&!timer)return load();
    }
    return {sync};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={shouldPoll,indexStatus,createMonitor};
  if(!global.document)return;
  const project=()=>document.getElementById('project')?.value||'';
  const readStatus=projectId=>api('/api/projects/'+encodeURIComponent(projectId)+'/indexes/status',undefined,'GET',
    {timeoutMs:10000,message:'读取检索索引状态超时。'});
  const indicators=()=>[...document.querySelectorAll('[data-vector-index-view]')];
  let active=null;
  function paint(result,failed){
    const {label,tone,detail,progress}=indexStatus(result,failed);
    for(const host of indicators()){
      const button=host.querySelector('[data-search-index]');
      if(button.textContent!==label)button.textContent=label;
      button.title=detail+' 点击查看检索索引。';
      host.dataset.tone=tone;
      const stage=host.querySelector('.project-vector-stage');
      const text=detail+(progress===null?'':` ${progress}%`);
      if(stage.textContent!==text)stage.textContent=text;
    }
    active?.update(result,failed);
  }
  const monitor=createMonitor({read:readStatus,render:paint});
  function refresh(options){
    const selected=project(),nodes=indicators();
    if(active&&active.projectId!==selected)active.close();
    for(const node of nodes)node.hidden=!selected;
    const visible=Boolean(selected&&!document.hidden&&(active||nodes.some(node=>node.getClientRects().length>0)));
    return monitor.sync(selected,visible,options);
  }

  function mount(parent,{compact=true}={}){
    if(!parent||parent.querySelector(':scope > [data-vector-index-view]'))return;
    const host=document.createElement('span'),button=document.createElement('button'),stage=document.createElement('span');
    host.className='project-vector-status'+(compact?' is-compact':'');
    host.dataset.vectorIndexView='';
    button.type='button';button.className='secondary';button.dataset.searchIndex='';
    button.textContent='正在读取向量索引';button.setAttribute('aria-live','polite');
    button.addEventListener('click',()=>open(project()).catch(error=>{if(typeof status==='function')status(error.message,true);}));
    stage.className='project-vector-stage';
    host.append(button,stage);parent.append(host);
    void refresh();
  }
  async function open(projectId){
    if(!projectId||projectId!==project())throw Error('请先选择项目');active?.close();
    const overlay=document.createElement('div');overlay.className='projects-modal-overlay active';
    overlay.innerHTML='<section class="projects-modal management-drawer search-index-dialog" role="dialog" aria-modal="true" aria-labelledby="vector-index-title"><header class="projects-modal-header"><h3 id="vector-index-title">检索索引</h3><button type="button" data-close aria-label="关闭">×</button></header><div class="projects-modal-body"><p data-state role="status">正在读取索引状态…</p><p>解析分块后自动计算切片向量；确认提交后加入正式检索。知识新增、修订及本体发布后的变更由后台自动更新。</p><p class="subtle">草稿不会进入搜索。本体类型与实体实例分别管理，实际写入的实体实例参与事实检索。</p><dl data-counts class="search-index-counts"></dl><p>关键词与语义向量使用同一份索引内容，标题、文件名及切片正文一起参与检索。</p><progress data-progress max="100" hidden aria-label="索引构建进度"></progress><p data-stage role="status" aria-live="polite"></p><p data-message role="status" aria-live="polite"></p></div><footer class="projects-modal-footer"><button type="button" data-build disabled>重建全部检索索引</button><button type="button" data-refresh class="secondary">刷新状态</button></footer></section>';
    document.body.append(overlay);
    const base='/api/projects/'+encodeURIComponent(projectId)+'/indexes',q=s=>overlay.querySelector(s);
    const previousFocus=document.activeElement;
    let closed=false,busy=false,canBuild=false;
    function message(text,error=false){if(!closed){q('[data-message]').textContent=text;q('[data-message]').classList.toggle('error',error);}}
    function close(){
      if(closed)return;
      closed=true;
      document.removeEventListener('keydown',keyboard);
      overlay.remove();
      if(active?.close===close)active=null;
      previousFocus?.focus?.();
    }
    function syncButton(){q('[data-build]').disabled=busy||!canBuild||(global.Auth&&!Auth.isAdmin());}
    function update(result,failed){
      if(closed||project()!==projectId)return;
      const view=indexStatus(result,failed);
      q('[data-state]').textContent=view.label;
      q('[data-counts]').replaceChildren();
      if(result){
        for(const [kind,label] of [['chunk','文档切片'],['entity','实体实例'],['relation','关系'],['attribute','属性']]){
          const term=document.createElement('dt'),value=document.createElement('dd');term.textContent=label;
          value.textContent=(result.indexed_record_counts?.[kind]||0)+' 条已索引 / '+(result.record_counts?.[kind]||0)+' 条当前记录';q('[data-counts]').append(term,value);
        }
      }
      const jobActive=['queued','running','retry_wait'].includes(result?.job?.status);
      canBuild=Boolean(result&&result.automatic!==false&&!jobActive&&!['building','leased'].includes(result.state));
      q('[data-progress]').hidden=view.progress===null;
      if(view.progress!==null)q('[data-progress]').value=view.progress;
      q('[data-stage]').textContent=view.detail;
      message(result?.last_error||'',Boolean(result?.last_error));syncButton();
    }
    function keyboard(event){
      if(event.key==='Escape'){close();void refresh();}
      if(event.key==='Tab'){
        const nodes=[...overlay.querySelectorAll('button:not(:disabled),[tabindex="0"]')],first=nodes[0],last=nodes.at(-1);
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      }
    }
    const dismiss=()=>{close();void refresh();};
    document.addEventListener('keydown',keyboard);
    q('[data-close]').onclick=dismiss;overlay.addEventListener('click',e=>{if(e.target===overlay)dismiss();});
    active={projectId,close,update};
    q('[data-refresh]').onclick=()=>refresh({refresh:true});
    q('[data-build]').onclick=async()=>{
      busy=true;syncButton();
      try{
        const task=await api(base+'/rebuild',{});
        if(closed)return;
        if(typeof watch==='function')watch(task);
        await refresh({invalidate:true});
      }catch(error){message(error.message,true);}
      finally{busy=false;if(!closed)syncButton();}
    };
    q('[data-close]').focus();
    await refresh({refresh:true});
    return {close:dismiss};
  }
  function install(){
    for(const selector of ['#tab-sources .source-actions-row','#tab-search .retrieval-query-panel .searchbar','#qa-page .qa-page-actions','#tab-records .records-actions-row']){
      mount(document.querySelector(selector));
    }
    document.addEventListener('visibilitychange',()=>refresh({refresh:true}));
    document.addEventListener('workspace:page-changed',()=>refresh({refresh:true}));
    document.addEventListener('ontology-model:published',event=>{
      if(event.detail?.projectId===project())void refresh({invalidate:true});
    });
  }
  global.ProjectVectorIndex={open,mount,refresh,shouldPoll};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})(typeof window==='undefined'?globalThis:window);
