/* Shared index status; normal updates are automatic. */
(function(global){
  const labels={not_built:'等待自动建立',building:'正在更新',ready:'可检索',requires_rebuild:'等待自动修复',failed:'更新失败',leased:'正在更新'};
  function shouldPoll({open,hidden,state}){return Boolean(open&&!hidden&&['building','leased','not_built','requires_rebuild','failed'].includes(state));}
  if(typeof module!=='undefined'&&module.exports)module.exports={shouldPoll};
  if(!global.document)return;
  let active=null;
  async function open(projectId){
    if(!projectId)throw Error('请先选择项目');active?.close();
    const overlay=document.createElement('div');overlay.className='projects-modal-overlay active';
    overlay.innerHTML='<section class="projects-modal management-drawer search-index-dialog" role="dialog" aria-modal="true" aria-labelledby="vector-index-title"><header class="projects-modal-header"><h3 id="vector-index-title">检索索引</h3><button type="button" data-close aria-label="关闭">×</button></header><div class="projects-modal-body"><p data-state role="status">正在读取索引状态…</p><p>解析分块后自动计算切片向量；确认提交后加入正式检索。知识新增、修订及本体发布后的变更由后台自动更新。</p><p class="subtle">草稿不会进入搜索。本体类型与实体实例分别管理，实际写入的实体实例参与事实检索。</p><dl data-counts class="search-index-counts"></dl><p>关键词与语义向量使用同一份索引内容，标题、文件名及切片正文一起参与检索。</p><progress data-progress max="100" hidden aria-label="索引构建进度"></progress><p data-stage role="status" aria-live="polite"></p><p data-message role="status" aria-live="polite"></p></div><footer class="projects-modal-footer"><button type="button" data-build disabled>重建全部检索索引</button><button type="button" data-refresh class="secondary">刷新状态</button></footer></section>';
    document.body.append(overlay);
    const base='/api/projects/'+encodeURIComponent(projectId)+'/indexes',q=s=>overlay.querySelector(s);
    const previousFocus=document.activeElement;let closed=false,timer=null,state='not_built',job=null,busy=false;
    function message(text,error=false){if(!closed){q('[data-message]').textContent=text;q('[data-message]').classList.toggle('error',error);}}
    function close(){if(closed)return;closed=true;clearTimeout(timer);document.removeEventListener('keydown',keyboard);document.removeEventListener('visibilitychange',visibility);document.getElementById('project')?.removeEventListener('change',close);overlay.remove();if(active?.close===close)active=null;previousFocus?.focus?.();}
    function syncButton(){q('[data-build]').disabled=busy||state==='building'||state==='leased'||Boolean(job)||(global.Auth&&!Auth.isAdmin());}
    function schedule(){clearTimeout(timer);if(shouldPoll({open:!closed,hidden:document.hidden,state:job?'building':state}))timer=setTimeout(()=>refresh().catch(e=>{message(e.message,true);schedule();}),2000);}
    async function refresh(){
      const result=await api(base+'/status',undefined,'GET');if(closed)return;
      state=result.state;job=['queued','running','retry_wait'].includes(result.job?.status)?result.job.id:null;
      q('[data-state]').textContent=(result.automatic===false?'自动索引未启用 · ':'')+(labels[state]||state);
      q('[data-counts]').replaceChildren();
      for(const [kind,label] of [['chunk','文档切片'],['entity','实体实例'],['relation','关系'],['attribute','属性']]){
        const term=document.createElement('dt'),value=document.createElement('dd');term.textContent=label;
        value.textContent=(result.indexed_record_counts?.[kind]||0)+' 条已索引 / '+(result.record_counts?.[kind]||0)+' 条当前记录';q('[data-counts]').append(term,value);
      }
      q('[data-progress]').hidden=!job;q('[data-progress]').value=Number(result.job?.progress)||0;
      q('[data-stage]').textContent=result.job?.stage||(job?'任务已排队，可继续浏览。':state==='ready'?'关键词与语义索引均已同步。':'后台将自动处理；失败时按间隔重试，也可手动重建。');
      message(result.last_error||'',Boolean(result.last_error));syncButton();schedule();
    }
    function keyboard(event){
      if(event.key==='Escape')close();
      if(event.key==='Tab'){
        const nodes=[...overlay.querySelectorAll('button:not(:disabled),[tabindex="0"]')],first=nodes[0],last=nodes.at(-1);
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      }
    }
    const visibility=()=>{clearTimeout(timer);if(!document.hidden)void refresh().catch(e=>message(e.message,true));};
    document.addEventListener('keydown',keyboard);document.addEventListener('visibilitychange',visibility);document.getElementById('project')?.addEventListener('change',close);
    q('[data-close]').onclick=close;overlay.addEventListener('click',e=>{if(e.target===overlay)close();});active={close};
    q('[data-refresh]').onclick=()=>refresh().catch(e=>message(e.message,true));
    q('[data-build]').onclick=async()=>{busy=true;syncButton();try{const task=await api(base+'/rebuild',{});if(closed)return;job=task.id;state='building';message('已提交索引重建任务，可继续浏览。');if(typeof watch==='function')watch(task);schedule();}catch(e){message(e.message,true);}finally{busy=false;if(!closed)syncButton();}};
    q('[data-close]').focus();try{await refresh();}catch(e){message(e.message,true);schedule();}return {close};
  }
  function install(){
    for(const selector of ['#tab-sources .source-actions-row','#graph-workspace-header .searchbar','#qa-page .qa-page-actions','#tab-records .records-actions-row']){
      const parent=document.querySelector(selector);if(!parent||parent.querySelector('[data-search-index]'))continue;
      const button=document.createElement('button');button.type='button';button.className='secondary';button.dataset.searchIndex='';button.textContent='检索索引';
      button.addEventListener('click',()=>open(document.getElementById('project')?.value).catch(e=>{if(typeof status==='function')status(e.message,true);}));parent.append(button);
    }
  }
  global.ProjectVectorIndex={open,shouldPoll};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})(typeof window==='undefined'?globalThis:window);
