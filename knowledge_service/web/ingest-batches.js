/* Durable batch control: browser uploads bytes; server owns processing state. */
(() => {
  async function pool(items,limit,run){
    const results=new Array(items.length);let next=0;
    await Promise.all(Array.from({length:Math.min(limit,items.length)},async()=>{
      while(next<items.length){const i=next++;try{results[i]={status:'fulfilled',value:await run(items[i],i)};}
        catch(reason){results[i]={status:'rejected',reason};}}
    }));return results;
  }
  const retryLabels={resume_checkpoint:'从断点继续',retry_failed_chunks:'只重试失败切片',restart_document:'整份重新处理'};
  function failureCsv(items){
    const cell=value=>'"'+String(value??'').replace(/^[=+@-]/,"'$&").replace(/"/g,'""')+'"';
    return '\uFEFF'+[['文件','阶段','切片','错误码','原因','重试次数','下次重试'],...items.map(x=>[
      x.original_name,x.stage,x.failed_chunk_ordinal,x.error_code,x.public_error,x.auto_retry_count,x.next_retry_at])]
      .map(row=>row.map(cell).join(',')).join('\r\n');
  }
  function assertSessionSettings(previous,current){
    if(previous?.extraction_mode!==current.extraction_mode||(previous?.ontology_id??null)!==(current.ontology_id??null)){
      throw Error('未完成上传批次的提取模式或本体版本与当前选择不同。请先清空文件列表，再重新选择文件提交新批次。');
    }
  }
  function queueLease(queue,project,currentProject){
    const generation=queue?.generation;
    const current=()=>Boolean(queue&&project===currentProject()&&queue.generation===generation);
    return {current,setSession(value){if(current())queue.batchSession=value;},
      release(value){if(current()&&queue.batchSession===value)queue.batchSession=null;},
      setStatus(...args){if(current())queue.setStatus(...args);}};
  }
  const helpers={pool,retryLabels,failureCsv,assertSessionSettings,queueLease};
  if(typeof module!=='undefined')module.exports=helpers;
  if(typeof window==='undefined')return;
  const states={draft:'待上传',uploading:'上传中',staged:'已上传',queued:'排队',running:'处理中',parsing:'解析',parse:'解析',chunking:'切片',extracting:'抽取',extraction:'抽取',merging:'融合',validating:'校验',committing:'写入',commit:'写入',completed:'完成',completed_with_errors:'部分失败',retry_wait:'等待自动重试',needs_attention:'需要处理',interrupted:'已中断',paused:'已暂停',skipped:'已跳过',cancelled:'已取消'};
  const pauseReasons={llm_timeout:'多个文件遇到模型超时，检查模型连接后继续。',llm_rate_limited:'模型持续限流，稍后检查连接并继续。',llm_unavailable:'模型服务暂时不可用，检查服务后继续。',llm_auth_failed:'模型认证失败，请修正服务配置后继续。',llm_model_not_found:'配置的模型不存在，请修正配置后继续。'};
  const root=document.getElementById('ingest-batches');
  let capabilities=null,capabilityError=null,capabilityRead=null;
  const mounted=new Map();
  const overviews=new Map();let overviewSequence=0;
  const base=p=>'/api/projects/'+encodeURIComponent(p)+'/ingest-batches';
  const currentProject=()=>document.getElementById('project')?.value;
  const node=(tag,text,className='')=>{const n=document.createElement(tag);n.textContent=text??'';n.className=className;return n;};
  const pageVisible=id=>{const page=document.getElementById(id);return Boolean(page&&!page.hidden&&!page.classList.contains('hidden'));};
  const batchesVisible=()=>{
    if(window.LibraryWorkspace){const view=window.LibraryWorkspace.snapshot();return view.active&&view.mode==='batches'&&pageVisible('tab-ingest');}
    const page=document.getElementById('tab-ingest');return Boolean(root&&!root.hidden&&(!page||pageVisible('tab-ingest')));
  };
  const jobsVisible=()=>pageVisible('tab-runtime')&&pageVisible('tab-jobs');
  const listVisible=()=>batchesVisible()||jobsVisible();
  const pollingStates=new Set(['uploading','queued','running','parsing','parse','chunking','extracting','extraction','merging','validating','committing','commit','retry_wait']);
  async function request(path,body,headers={},signal){
    const multipart=typeof FormData!=='undefined'&&body instanceof FormData;
    const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{...(!multipart&&body!==undefined?{'Content-Type':'application/json'}:{}),...headers},body:body===undefined?undefined:multipart?body:JSON.stringify(body),...(signal?{signal}:{})});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:data.detail?.message||'批次请求失败（'+response.status+'）');return data;
  }
  function loadCapabilities(){
    if(capabilities)return Promise.resolve(capabilities);
    if(capabilityRead)return capabilityRead;
    capabilityRead=(async()=>{try{
      capabilities=await request('/api/ingest/capabilities');
      capabilityError=null;
      window.DocumentUploadQueue?.setCapabilities?.(capabilities);
      const select=document.getElementById('ingest-concurrency');
      if(select){select.replaceChildren();for(const c of capabilities.concurrency_choices){const o=node('option',c==='auto'?'自动推荐':c+' 个文档');o.value=c;select.append(o);}}
      return capabilities;
    }catch(error){capabilityError=error;const help=document.getElementById('doc-upload-limits');if(help)help.textContent='未取得服务能力，暂不能提交批次。'+error.message;return null;}
    finally{capabilityRead=null;}})();return capabilityRead;
  }
  // Preserve the awaitable controller-ready contract without a hidden fetch.
  const ready=Promise.resolve();
  function button(text,action){const b=node('button',text,'secondary');b.type='button';b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){status(e.message,true);}finally{b.disabled=b.dataset.inactive==='true';}};return b;}
  function createOverview(project,batch){
    const key=project+':'+batch.id,card=node('section','','ingest-batch-card');
    const overview=node('div','','batch-overview'),identity=node('div','','batch-identity');
    const heading=node('h3'),created=node('p','','batch-created'),badge=node('span','','batch-status');
    const counts=node('div','','batch-counts'),progress=node('progress'),progressLabel=node('span'),progressRow=node('div','','batch-progress');
    const detail=node('div','','batch-detail');detail.id='batch-detail-'+(++overviewSequence);detail.hidden=true;
    const toggle=button('查看批次详情',()=>{
      if(project!==currentProject())return;
      const control=mounted.get(key);
      if(overviews.get(key)===view&&control?.expanded&&batchesVisible()){control.collapse();return;}
      if(!batchesVisible()){if(window.LibraryWorkspace)window.LibraryWorkspace.open('batches');else window.KnowledgeTabs?.open('ingest');}
      monitor(project,batch);
    });
    toggle.classList.add('batch-toggle');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',detail.id);
    progress.setAttribute('aria-label','本批已结束文件数');
    identity.append(heading,badge);progressRow.append(progress,progressLabel);
    overview.append(identity,created,counts,progressRow,toggle);card.append(overview,detail);
    function update(value){
      batch=value;
      heading.textContent='批次 #'+String(batch.id).slice(0,8);heading.setAttribute('title',batch.id);
      const date=batch.created_at?new Date(batch.created_at):null;
      created.textContent=date&&Number.isFinite(date.getTime())?'创建于 '+date.toLocaleString('zh-CN',{hour12:false}):'创建时间未提供';
      badge.textContent=states[batch.status]||batch.status;badge.dataset.status=batch.status;
      const values=batch.counts||batch.summary||{},total=Number(batch.total??batch.total_items??Object.values(values).reduce((sum,n)=>sum+Number(n||0),0));
      const finished=['completed','succeeded','needs_attention','failed','interrupted','cancelled','skipped'].reduce((sum,status)=>sum+Number(values[status]||0),0);
      counts.replaceChildren();
      for(const [state,n] of Object.entries(values)){if(!n)continue;const chip=node('span',(states[state]||state)+' '+n,'batch-count');chip.dataset.status=state;counts.append(chip);}
      counts.hidden=!counts.children.length;
      progress.max=Math.max(1,total);progress.value=Math.min(total,finished);progress.hidden=!total;
      progressLabel.textContent='总文件 '+total+' · 已结束 '+Math.min(total,finished)+' / '+total;
    }
    const view={card,detail,toggle,update};update(batch);return view;
  }
  function batchOverview(project,batch){
    const key=project+':'+batch.id;let view=overviews.get(key);
    if(!view){view=createOverview(project,batch);overviews.set(key,view);}else view.update(batch);
    return view;
  }
  function monitor(project,batch){
    const key=project+':'+batch.id;if(mounted.has(key)){const control=mounted.get(key);control.show();return control;}
    const view=batchOverview(project,batch),{card,detail,toggle}=view;card.dataset.batchId=batch.id;
    const parallel=node('p','','subtle'),error=node('p','','batch-error');error.setAttribute('role','status');
    const fileHeading=node('h4','本批文件'),fileToolbar=node('div','','batch-files-toolbar');
    const actions=node('div','','batch-actions'),filter=node('select');filter.setAttribute('aria-label','筛选文件状态');
    for(const [value,label] of [['','全部文件'],['needs_attention','需要处理'],['retry_wait','等待重试'],['completed','完成'],['interrupted','中断']]){const o=node('option',label);o.value=value;filter.append(o);}
    const rows=node('div','','batch-items'),pages=node('div','','batch-actions');
    let cursor='',nextCursor=null,timer=null,inFlight=null,generation=0,active=false,expanded=false,allFailures=[];
    const url=base(project)+'/'+encodeURIComponent(batch.id);
    fileToolbar.append(fileHeading,filter);
    detail.append(node('h4','批次操作','batch-controls-heading'),parallel,error,actions,fileToolbar,rows,pages);
    if(![...(listHost?.children||[])].includes(card))listHost?.prepend(card);
    function requireWriter(){if(project!==currentProject())throw Error('项目已变化，请重新打开当前项目批次');if(!window.Auth?.isAdmin?.())throw Error('只有管理员可以更改处理批次');}
    async function action(name,body={}){requireWriter();await request(url+'/'+name,body);await refresh();}
    const start=button('开始处理',()=>action('start')),pause=button('暂停',()=>action('pause')),resume=button('继续',()=>action('resume'));
    const retry=button('重试全部失败',()=>action('retry-failed',{mode:'resume_checkpoint'})),cancel=button('取消未开始文件',()=>action('cancel')),download=button('下载失败明细',async()=>{
      allFailures=[];let c='';do{const r=await request(url+'/items?status=needs_attention&limit=100'+(c?'&cursor='+encodeURIComponent(c):''));allFailures.push(...(r.items||[]));c=r.next_cursor;}while(c);
      const blob=new Blob([failureCsv(allFailures)],{type:'text/csv;charset=utf-8'}),link=node('a');link.href=URL.createObjectURL(blob);link.download='批次失败明细.csv';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
    });
    const review=button('核对本批次候选',async()=>{
      requireWriter();window.WorkspaceDrawers?.close({restoreFocus:false});await window.KnowledgeNavigation.open('ontology-model',{projectId:project});
      if(!window.OntologyIntake)throw Error('候选核对模块尚未就绪，请刷新页面。');
      await window.OntologyIntake.open({projectId:project,batchId:batch.id,trigger:review});
    });review.classList.add('admin-only');review.hidden=batch.settings?.extraction_mode!=='discovery';
    actions.append(start,pause,resume,retry,cancel,download,review);
    for(const control of [start,pause,resume,retry,cancel])control.classList.add('admin-only');
    filter.onchange=()=>{cursor='';refresh();};
    const visible=()=>active&&expanded&&project===currentProject()&&batchesVisible();
    function refresh(){
      clearTimeout(timer);
      if(!visible()){card.hidden=project!==currentProject()||!batchesVisible();return Promise.resolve();}
      if(inFlight)return inFlight.promise;
      card.hidden=false;
      const read={generation,controller:typeof AbortController==='function'?new AbortController():null};
      const accepts=()=>read===inFlight&&read.generation===generation&&visible();
      inFlight=read;
      read.promise=(async()=>{try{
        const detail=await request(url,undefined,{},read.controller?.signal);
        if(!accepts())return;
        const list=await request(url+'/items?limit=50&status='+encodeURIComponent(filter.value)+(cursor?'&cursor='+encodeURIComponent(cursor):''),undefined,{},read.controller?.signal);
        if(!accepts())return;
        batch=detail;
        view.update(batch);
        card.dataset.status=batch.status;
        const counts=batch.counts||batch.summary||{};
        review.hidden=batch.settings?.extraction_mode!=='discovery';review.disabled=!(counts.completed||counts.succeeded);
        parallel.textContent='并发设置：'+(batch.requested_concurrency==='auto'?'自动推荐':batch.requested_concurrency)+' · 当前并发 '+batch.effective_concurrency;
        const failures=(counts.needs_attention||0)+(counts.interrupted||0);
        const pending=['draft','uploading','staged','queued','retry_wait'].some(s=>counts[s]>0);
        for(const [b,enabled] of [[start,['draft','uploading','queued'].includes(batch.status)],[pause,batch.status==='running'],[resume,batch.status==='paused'],[retry,failures>0],[cancel,pending],[download,counts.needs_attention>0]]){b.dataset.inactive=String(!enabled);b.disabled=!enabled;}
        error.textContent=batch.status==='paused'?(pauseReasons[batch.paused_reason]||batch.paused_reason||''):'';rows.replaceChildren();
        for(const item of list.items||[]){
          const row=node('article','','batch-item');row.dataset.status=item.status;
          row.append(node('strong',item.original_name),node('span',states[item.status]||item.status));
          if(item.stage&&!item.error_code)row.append(node('p','当前阶段：'+(states[item.stage]||item.stage),'batch-file-stage'));
          const rowActions=node('div','','batch-item-actions');
          if(item.source_version_id)rowActions.append(button('查看原文与 OCR',()=>{window.WorkspaceDrawers?.close({restoreFocus:false});return window.SourceReader?.open({projectId:project,versionId:item.source_version_id});}));
          if(item.error_code){const diagnostics=node('details','','batch-item-details');diagnostics.open=true;
            diagnostics.append(node('summary','错误与重试'),node('p',`${states[item.stage]||item.stage||'处理'}${item.failed_chunk_ordinal!=null?' · 切片 '+item.failed_chunk_ordinal:''} · ${item.public_error||item.error_code}`,'batch-error'),node('small','自动重试 '+(item.auto_retry_count||0)+' 次'+(item.next_retry_at?' · 下次 '+new Date(item.next_retry_at).toLocaleString():'')));
            const tools=node('div','','batch-actions admin-only');for(const [mode,label] of Object.entries(retryLabels))tools.append(button(label,()=>{requireWriter();return request(url+'/items/'+encodeURIComponent(item.id)+'/retry',{mode}).then(refresh);}));diagnostics.append(tools);row.append(diagnostics);
          }
          const documentId=item.document_id||item.result?.document?.id||item.result?.document_id;
          if(item.status==='completed'&&documentId){
            rowActions.append(button('查看抽取结果',()=>{window.WorkspaceDrawers?.close({restoreFocus:false});return window.IngestResults?.showBatchItem(item,{projectId:project,mode:batch.settings?.extraction_mode||'ontology',ontologyId:batch.settings?.ontology_id});}));
            if(batch.settings?.extraction_mode==='discovery'){
              const structure=button('核对候选与完善结构',async()=>{
                requireWriter();window.WorkspaceDrawers?.close({restoreFocus:false});
                await window.KnowledgeNavigation.open('ontology-model',{projectId:project});
                if(!window.OntologyIntake)throw Error('结构候选模块尚未就绪，请刷新页面。');
                const runId=item.result?.run_id||item.result?.document?.metadata?.run_id;
                if(!runId)throw Error('此文件结果缺少本次处理的运行标识；请从资料库查看当前文档候选。');
                await window.OntologyIntake.open({projectId:project,documentId,documentTitle:item.original_name,batchId:item.batch_id||batch.id,runId,trigger:structure});
              });structure.className+=' admin-only';rowActions.append(structure);
            }
          }
          if(rowActions.childElementCount)row.append(rowActions);
          rows.append(row);
        }
        nextCursor=list.next_cursor;pages.replaceChildren();if(cursor)pages.append(button('回到首页',()=>{cursor='';return refresh();}));if(nextCursor)pages.append(button('下一页',()=>{cursor=nextCursor;return refresh();}));
      }catch(e){if(accepts()&&e.name!=='AbortError')error.textContent='批次更新失败：'+(e.message==='Failed to fetch'?'无法连接服务，请检查连接后重试。':e.message);}
      finally{if(read===inFlight){inFlight=null;if(visible()&&pollingStates.has(batch.status))timer=setTimeout(refresh,2000);}}
      })();return read.promise;
    }
    function suspend(){active=false;generation++;clearTimeout(timer);inFlight?.controller?.abort();inFlight=null;}
    function hide(){suspend();card.hidden=true;}
    const control={card,refresh,get expanded(){return expanded;},
      show(){for(const other of mounted.values())if(other!==control)other.collapse();
        expanded=true;active=batchesVisible()&&project===currentProject();detail.hidden=false;toggle.textContent='收起文件';toggle.setAttribute('aria-expanded','true');return refresh();},
      collapse(){suspend();expanded=false;detail.hidden=true;toggle.textContent='查看批次详情';toggle.setAttribute('aria-expanded','false');},
      resume(){card.hidden=false;if(expanded&&!active){active=true;void refresh();}},hide,
      stop(){hide();card.remove();mounted.delete(key);overviews.delete(key);}};
    mounted.set(key,control);control.show();return control;
  }
  async function submit(project,documents,settings){
    const queue=window.DocumentUploadQueue,lease=queueLease(queue,project,currentProject);
    await loadCapabilities();
    if(!capabilities)throw Error('未取得服务能力，请刷新后重试。'+(capabilityError?.message||''));
    let session=lease.current()?queue.batchSession:null;
    if(session?.project===project)assertSessionSettings(session.batch.settings,settings);
    if(!session||session.project!==project){const created=await request(base(project),{settings});session={project,batch:created};lease.setSession(session);}
    const batch=session.batch;
    const results=await pool(documents,3,async(doc)=>{
      lease.setStatus(doc.file,'uploading');
      const form=new FormData();form.append('file',doc.file,doc.file.name);form.append('options',JSON.stringify({title:doc.title,metadata:doc.metadata}));
      const idempotencyKey=doc.queueItem?.idempotencyKey||crypto.randomUUID();if(doc.queueItem)doc.queueItem.idempotencyKey=idempotencyKey;
      try{const item=await request(base(project)+'/'+encodeURIComponent(batch.id)+'/files',form,{'Idempotency-Key':idempotencyKey});lease.setStatus(doc.file,'queued',item.id||'已上传');return item;}
      catch(e){lease.setStatus(doc.file,'failed',e.message);throw e;}
    });
    const failed=results.filter(r=>r.status==='rejected').length;
    if(failed){throw Error(failed+' 个文件上传失败。已上传文件保留，点击“重试失败”补传后开始处理。');}
    await request(base(project)+'/'+encodeURIComponent(batch.id)+'/start',{});lease.release(session);
    return batch;
  }
  const listError=node('div','','batch-list-error');listError.setAttribute('role','alert');listError.hidden=true;
  const listHost=node('div','','batch-list');
  const listHint=node('p','每个批次包含一次提交的文件；展开批次后查看文件状态、结果和重试操作。','batch-list-hint');
  const listEmpty=node('p','当前项目还没有处理批次，添加文件并提交后会在这里展示。','batch-list-empty');
  root?.prepend(listHint,listError,listHost);
  let listRead=null,listGeneration=0,mountedProject=currentProject();
  function invalidateList(){listGeneration++;listRead?.controller?.abort();listRead=null;}
  function visibilityChanged(){
    for(const control of mounted.values()){if(batchesVisible())control.resume();else control.hide();}
    if(!listVisible())invalidateList();
  }
  function summaryCard(project,batch){
    return createOverview(project,batch).card;
  }
  function loadRecent(){
    const project=currentProject();if(!project||!listVisible())return Promise.resolve();
    if(listRead?.project===project)return listRead.promise;
    const read={project,generation:listGeneration,controller:typeof AbortController==='function'?new AbortController():null};
    const accepts=()=>listRead===read&&read.generation===listGeneration&&project===currentProject()&&listVisible();
    listRead=read;
    read.promise=(async()=>{try{
      const result=await request(base(project)+'?limit=20',undefined,{},read.controller?.signal);if(!accepts())return;
      listError.hidden=true;listError.replaceChildren();
      const tasks=document.getElementById('ingest-batch-tasks');if(jobsVisible())tasks?.replaceChildren();
      const cards=[];
      for(const batch of result.items||result.batches||[]){
        if(batchesVisible()){const view=batchOverview(project,batch);view.card.hidden=false;cards.push(view.card);}
        if(jobsVisible())tasks?.append(summaryCard(project,batch));
      }
      if(batchesVisible()){
        for(const control of mounted.values())if(control.expanded&&!cards.includes(control.card))cards.unshift(control.card);
        listHost.replaceChildren(...(cards.length?cards:[listEmpty]));
      }
    }catch(e){if(accepts()&&e.name!=='AbortError'&&root){listError.hidden=false;listError.replaceChildren(node('span','批次列表读取失败：'+(e.message==='Failed to fetch'?'无法连接服务，请检查连接。':e.message)),button('重新加载',loadRecent));}}
    finally{if(listRead===read)listRead=null;}})();return read.promise;
  }
  function projectChanged(){const project=currentProject();if(project===mountedProject)return;mountedProject=project;
    invalidateList();for(const c of [...mounted.values()])c.stop();overviews.clear();listError.hidden=true;listHost.replaceChildren();document.getElementById('ingest-batch-tasks')?.replaceChildren();loadRecent();}
  document.getElementById('project')?.addEventListener('change',projectChanged);
  document.getElementById('load-jobs')?.addEventListener('click',loadRecent);
  window.RuntimeView?.on('jobs',loadRecent);
  document.addEventListener('workspace:page-changed',visibilityChanged);
  window.IngestBatches={...helpers,ready,loadCapabilities,submit,monitor,loadRecent,projectChanged,visibilityChanged,get capabilities(){return capabilities;}};
})();
