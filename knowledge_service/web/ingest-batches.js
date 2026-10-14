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
  const helpers={pool,retryLabels,failureCsv};
  if(typeof module!=='undefined')module.exports=helpers;
  if(typeof window==='undefined')return;
  const states={draft:'待上传',uploading:'上传中',staged:'已上传',queued:'排队',running:'处理中',parsing:'解析',parse:'解析',chunking:'切片',extracting:'抽取',extraction:'抽取',merging:'融合',validating:'校验',committing:'写入',commit:'写入',completed:'完成',completed_with_errors:'部分失败',retry_wait:'等待自动重试',needs_attention:'需要处理',interrupted:'已中断',paused:'已暂停',skipped:'已跳过',cancelled:'已取消'};
  const pauseReasons={llm_timeout:'多个文件遇到模型超时，检查模型连接后继续。',llm_rate_limited:'模型持续限流，稍后检查连接并继续。',llm_unavailable:'模型服务暂时不可用，检查服务后继续。',llm_auth_failed:'模型认证失败，请修正服务配置后继续。',llm_model_not_found:'配置的模型不存在，请修正配置后继续。'};
  const root=document.getElementById('ingest-batches');
  let capabilities=null,capabilityError=null;
  const mounted=new Map();
  const base=p=>'/api/projects/'+encodeURIComponent(p)+'/ingest-batches';
  const currentProject=()=>document.getElementById('project')?.value;
  const node=(tag,text,className='')=>{const n=document.createElement(tag);n.textContent=text??'';n.className=className;return n;};
  async function request(path,body,headers={}){
    const multipart=typeof FormData!=='undefined'&&body instanceof FormData;
    const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{...(!multipart&&body!==undefined?{'Content-Type':'application/json'}:{}),...headers},body:body===undefined?undefined:multipart?body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:data.detail?.message||'批次请求失败（'+response.status+'）');return data;
  }
  async function loadCapabilities(){
    try{
      capabilities=await request('/api/ingest/capabilities');
      window.DocumentUploadQueue?.setCapabilities?.(capabilities);
      const select=document.getElementById('ingest-concurrency');
      if(select){select.replaceChildren();for(const c of capabilities.concurrency_choices){const o=node('option',c==='auto'?'自动推荐':c+' 个文档');o.value=c;select.append(o);}}
      return capabilities;
    }catch(error){capabilityError=error;const help=document.getElementById('doc-upload-limits');if(help)help.textContent='未取得服务能力，暂不能提交批次。'+error.message;return null;}
  }
  const ready=loadCapabilities();
  function button(text,action){const b=node('button',text,'secondary');b.type='button';b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){status(e.message,true);}finally{b.disabled=b.dataset.inactive==='true';}};return b;}
  function monitor(project,batch){
    const key=project+':'+batch.id;if(mounted.has(key))return mounted.get(key);
    const card=node('section','','ingest-batch-card');card.dataset.batchId=batch.id;
    const heading=node('h3'),summary=node('p','','batch-counts'),parallel=node('p','','subtle'),error=node('p','','batch-error');error.setAttribute('role','status');
    const actions=node('div','','batch-actions'),filter=node('select');filter.setAttribute('aria-label','筛选文件状态');
    for(const [value,label] of [['','全部文件'],['needs_attention','需要处理'],['retry_wait','等待重试'],['completed','完成'],['interrupted','中断']]){const o=node('option',label);o.value=value;filter.append(o);}
    const rows=node('div','','batch-items'),pages=node('div','','batch-actions');
    let cursor='',nextCursor=null,timer=null,busy=false,allFailures=[];
    const url=base(project)+'/'+encodeURIComponent(batch.id);
    card.append(heading,summary,parallel,error,actions,filter,rows,pages);root?.prepend(card);
    async function action(name,body={}){await request(url+'/'+name,body);await refresh();}
    const start=button('开始处理',()=>action('start')),pause=button('暂停',()=>action('pause')),resume=button('继续',()=>action('resume'));
    actions.append(start,pause,resume,button('重试全部失败',()=>action('retry-failed',{mode:'resume_checkpoint'})),button('取消未开始文件',()=>action('cancel')),button('下载失败明细',async()=>{
      allFailures=[];let c='';do{const r=await request(url+'/items?status=needs_attention&limit=100'+(c?'&cursor='+encodeURIComponent(c):''));allFailures.push(...(r.items||[]));c=r.next_cursor;}while(c);
      const blob=new Blob([failureCsv(allFailures)],{type:'text/csv;charset=utf-8'}),link=node('a');link.href=URL.createObjectURL(blob);link.download='批次失败明细.csv';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
    }));
    filter.onchange=()=>{cursor='';refresh();};
    async function refresh(){
      clearTimeout(timer);if(busy)return;
      if(project!==currentProject()){card.hidden=true;return;}card.hidden=false;busy=true;
      try{
        batch=await request(url);
        const list=await request(url+'/items?limit=50&status='+encodeURIComponent(filter.value)+(cursor?'&cursor='+encodeURIComponent(cursor):''));
        if(project!==currentProject())return;
        heading.textContent='知识写入批次 · '+(states[batch.status]||batch.status);
        summary.textContent=Object.entries(batch.counts||batch.summary||{}).map(([s,n])=>(states[s]||s)+' '+n).join('　')||'总文件 '+(batch.total_items||0);
        parallel.textContent='申请并发 '+batch.requested_concurrency+' · 实际并发 '+batch.effective_concurrency;
        for(const [b,enabled] of [[start,['draft','uploading','queued'].includes(batch.status)],[pause,batch.status==='running'],[resume,batch.status==='paused']]){b.dataset.inactive=String(!enabled);b.disabled=!enabled;}
        error.textContent=batch.status==='paused'?(pauseReasons[batch.paused_reason]||batch.paused_reason||''):'';rows.replaceChildren();
        for(const item of list.items||[]){
          const row=node('article','','batch-item');row.dataset.status=item.status;
          row.append(node('strong',item.original_name),node('span',states[item.status]||item.status));
          if(item.source_version_id)row.append(button('查看原文与 OCR',()=>window.SourceReader?.open({projectId:project,versionId:item.source_version_id})));
          if(item.error_code){row.append(node('p',`${states[item.stage]||item.stage||'处理'}${item.failed_chunk_ordinal!=null?' · 切片 '+item.failed_chunk_ordinal:''} · ${item.public_error||item.error_code}`,'batch-error'),node('small','自动重试 '+(item.auto_retry_count||0)+' 次'+(item.next_retry_at?' · 下次 '+new Date(item.next_retry_at).toLocaleString():'')));
            const tools=node('div','','batch-actions');for(const [mode,label] of Object.entries(retryLabels))tools.append(button(label,()=>request(url+'/items/'+encodeURIComponent(item.id)+'/retry',{mode}).then(refresh)));row.append(tools);
          }
          if(item.status==='completed'&&(item.document_id||item.result?.document?.id))row.append(button('查看抽取结果',()=>window.IngestResults?.showBatchItem(item,{projectId:project,mode:batch.settings?.extraction_mode||'ontology'})));
          rows.append(row);
        }
        nextCursor=list.next_cursor;pages.replaceChildren();if(cursor)pages.append(button('回到首页',()=>{cursor='';return refresh();}));if(nextCursor)pages.append(button('下一页',()=>{cursor=nextCursor;return refresh();}));
      }catch(e){error.textContent=e.message;}finally{busy=false;if(!['completed','completed_with_errors','cancelled'].includes(batch.status))timer=setTimeout(refresh,2000);}
    }
    const control={refresh,stop(){clearTimeout(timer);card.remove();mounted.delete(key);}};mounted.set(key,control);refresh();return control;
  }
  async function submit(project,documents,settings){
    await ready;if(!capabilities)await loadCapabilities();
    if(!capabilities)throw Error('未取得服务能力，请刷新后重试。'+(capabilityError?.message||''));
    const queue=window.DocumentUploadQueue;
    let session=queue?.batchSession;
    if(!session||session.project!==project){const created=await request(base(project),{settings});session={project,batch:created};if(queue)queue.batchSession=session;}
    const batch=session.batch;
    const results=await pool(documents,3,async(doc)=>{
      queue?.setStatus(doc.file,'uploading');
      const form=new FormData();form.append('file',doc.file,doc.file.name);form.append('options',JSON.stringify({title:doc.title,metadata:doc.metadata}));
      const idempotencyKey=doc.queueItem?.idempotencyKey||crypto.randomUUID();if(doc.queueItem)doc.queueItem.idempotencyKey=idempotencyKey;
      try{const item=await request(base(project)+'/'+encodeURIComponent(batch.id)+'/files',form,{'Idempotency-Key':idempotencyKey});queue?.setStatus(doc.file,'queued',item.id||'已上传');return item;}
      catch(e){queue?.setStatus(doc.file,'failed',e.message);throw e;}
    });
    const failed=results.filter(r=>r.status==='rejected').length;
    monitor(project,batch);
    if(failed){throw Error(failed+' 个文件上传失败。已上传文件保留，点击“重试失败”补传后开始处理。');}
    await request(base(project)+'/'+encodeURIComponent(batch.id)+'/start',{});if(queue)queue.batchSession=null;
    return batch;
  }
  async function loadRecent(){
    const project=currentProject();if(!project)return;
    try{
      const result=await request(base(project)+'?limit=20');if(project!==currentProject())return;
      const tasks=document.getElementById('ingest-batch-tasks');if(tasks)tasks.replaceChildren();
      for(const batch of result.items||result.batches||[]){
        monitor(project,batch);
        if(tasks){const card=node('article','','ingest-batch-card');card.append(node('strong','知识写入批次 · '+(states[batch.status]||batch.status)),node('p','总文件 '+(batch.total||batch.total_items||0)+' · 实际并发 '+batch.effective_concurrency));
          card.append(button('查看批次详情',()=>{window.KnowledgeTabs?.open('ingest');const host=root?.querySelector('[data-batch-id="'+batch.id+'"]');host?.scrollIntoView({block:'start'});}));tasks.append(card);}
      }
    }catch(e){if(project===currentProject()&&root){const error=node('p','批次列表读取失败：'+e.message,'batch-error');root.prepend(error);}}
  }
  document.getElementById('project')?.addEventListener('change',()=>{for(const c of mounted.values())c.refresh();if(!capabilities)loadCapabilities();loadRecent();});
  document.getElementById('load-jobs')?.addEventListener('click',loadRecent);
  window.RuntimeView?.on('jobs',loadRecent);
  ready.then(loadRecent);
  window.IngestBatches={...helpers,ready,submit,monitor,loadRecent,get capabilities(){return capabilities;}};
})();
