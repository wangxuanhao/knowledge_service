/* Project vectorization: one selected project, explicit action, visible status. */
(function(global){
  const labels={not_built:'未向量化',building:'构建中',ready:'可用',requires_rebuild:'需重新向量化',failed:'构建失败',leased:'更新中'};
  function shouldPoll({open,hidden,state}){return Boolean(open&&!hidden&&state==='building');}
  if(typeof module!=='undefined'&&module.exports)module.exports={shouldPoll};
  if(!global.document)return;
  let active=null;
  async function open(projectId){
    if(!projectId)throw Error('请先选择项目');
    active?.close();
    const overlay=document.createElement('div');overlay.className='projects-modal-overlay active';
    overlay.innerHTML='<section class="projects-modal management-drawer" role="dialog" aria-modal="true" aria-labelledby="vector-index-title"><header class="projects-modal-header"><h3 id="vector-index-title">项目向量化</h3><button type="button" data-close aria-label="关闭">×</button></header><div class="projects-modal-body"><p data-state role="status">正在读取索引状态…</p><p>使用当前项目资料建立检索索引，支持页面范围筛选及关键词、语义和混合检索。</p><p data-message role="status" aria-live="polite"></p></div><footer class="projects-modal-footer"><button type="button" data-build disabled>向量化当前项目</button><button type="button" data-refresh class="secondary">刷新状态</button></footer></section>';
    document.body.append(overlay);
    const base='/api/projects/'+encodeURIComponent(projectId)+'/indexes',q=s=>overlay.querySelector(s);
    const previousFocus=document.activeElement;let closed=false,timer=null,state='not_built',job=null,busy=false;
    function message(text,error=false){if(!closed){q('[data-message]').textContent=text;q('[data-message]').classList.toggle('error',error);}}
    function close(){if(closed)return;closed=true;clearTimeout(timer);document.removeEventListener('keydown',escape);document.removeEventListener('visibilitychange',visibility);overlay.remove();if(active?.close===close)active=null;previousFocus?.focus?.();}
    function syncButton(){q('[data-build]').disabled=busy||state==='building'||Boolean(job)||(global.Auth&&!Auth.isAdmin());}
    function schedule(){clearTimeout(timer);if(shouldPoll({open:!closed,hidden:document.hidden,state:job?'building':state}))timer=setTimeout(()=>refresh().catch(e=>message(e.message,true)),2000);}
    async function refresh(){
      if(job){const task=await api('/api/jobs/'+encodeURIComponent(job),undefined,'GET');if(closed)return;if(['completed','failed','cancelled'].includes(task.status)){job=null;message(task.status==='completed'?'当前项目向量化完成。':'向量化未完成，请查看后台任务详情。',task.status!=='completed');}}
      const result=await api(base+'/status',undefined,'GET');if(closed)return;
      state=result.state;q('[data-state]').textContent=(labels[state]||state)+(result.last_error?' · '+result.last_error:'');syncButton();schedule();
    }
    const escape=e=>{if(e.key==='Escape')close();};
    const visibility=()=>{clearTimeout(timer);if(!document.hidden)schedule();};
    document.addEventListener('keydown',escape);document.addEventListener('visibilitychange',visibility);q('[data-close]').onclick=close;overlay.addEventListener('click',e=>{if(e.target===overlay)close();});active={close};
    q('[data-refresh]').onclick=()=>refresh().catch(e=>message(e.message,true));
    q('[data-build]').onclick=async()=>{busy=true;syncButton();try{const task=await api(base+'/rebuild',{});if(closed)return;job=task.id;message('已提交当前项目向量化任务，可继续浏览。');if(typeof watch==='function')watch(task);schedule();}catch(e){message(e.message,true);}finally{busy=false;if(!closed)syncButton();}};
    q('[data-close]').focus();try{await refresh();}catch(e){message(e.message,true);}
    return {close};
  }
  global.ProjectVectorIndex={open,shouldPoll};
})(typeof window==='undefined'?globalThis:window);
