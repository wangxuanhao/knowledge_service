/* File uploads stay as binary File objects; parsing belongs to the server. */
(() => {
  const ALLOWED=/\.(txt|md|pdf|docx|html|htm)$/i;
  const MAX_FILE=25_000_000,MAX_FILES=20,MAX_BATCH=100_000_000;
  const file=document.getElementById('doc-file'),text=document.getElementById('doc-text');
  const modes=[...document.querySelectorAll('input[name="document-input-mode"]')];
  const rows=[];
  const mode=()=>modes.find(input=>input.checked)?.value||'file';
  const key=f=>[f.name,f.size||0,f.lastModified||0].join(':');
  const size=value=>value<1024?value+' B':value<1024*1024?(value/1024).toFixed(1)+' KB':(value/1024/1024).toFixed(1)+' MB';
  const icon=name=>/\.pdf$/i.test(name)?'PDF':/\.docx$/i.test(name)?'DOCX':/\.html?$/i.test(name)?'HTML':/\.md$/i.test(name)?'MD':'TXT';
  const statusText=item=>({ready:'待提交',uploading:'正在上传',queued:'已进入队列',failed:'提交失败'}[item.status]||item.status);

  function render(){
    const list=document.getElementById('doc-file-list');
    if(list){
      list.hidden=mode()!=='file';list.replaceChildren();
      for(const item of rows){
        const row=document.createElement('div');row.className='document-queue-row';row.dataset.state=item.status;
        const badge=document.createElement('span');badge.className='document-file-type';badge.textContent=icon(item.file.name);
        const info=document.createElement('span');info.className='document-file-info';info.textContent=item.file.name+' · '+size(item.file.size||0);
        const state=document.createElement('span');state.className='document-file-status';state.textContent=statusText(item)+(item.detail?' · '+item.detail:'');
        const remove=document.createElement('button');remove.type='button';remove.className='secondary document-file-remove';remove.textContent='移除';
        remove.addEventListener('click',()=>queue.remove(item.file));row.append(badge,info,state,remove);list.append(row);
      }
    }
    const total=rows.reduce((sum,item)=>sum+(item.file.size||0),0);
    const summary=document.getElementById('doc-upload-summary');
    if(summary)summary.textContent=rows.length?`${rows.length} 个文件 · ${size(total)} · ${rows.filter(item=>item.status==='queued').length} 个已提交`:'尚未选择文件';
    const help=document.getElementById('doc-batch-help');
    if(help)help.textContent=rows.length>1?'多文件分别以文件名作为标题；发布时间和 Metadata 为本批共用。':'';
    const title=document.getElementById('doc-title');
    if(title){title.disabled=mode()==='file'&&rows.length>1;title.required=mode()==='text';}
    const submit=document.getElementById('ingest');
    if(submit)submit.textContent=mode()==='file'?(rows.length?`上传并解析 ${queue.eligible().length} 个文件`:'上传并处理'):'提交正文并处理';
  }

  const queue=window.DocumentUploadQueue={
    add(files){
      const incoming=[...files];
      for(const value of incoming){
        if(!ALLOWED.test(value.name||''))throw Error(value.name+'：仅支持 TXT、Markdown、PDF、DOCX、HTML 文件。');
        if((value.size||0)>MAX_FILE)throw Error(value.name+'：单个文件不能超过 25 MB。');
      }
      const known=new Set(rows.map(item=>key(item.file)));
      const unique=incoming.filter(value=>!known.has(key(value))&&(known.add(key(value)),true));
      if(rows.length+unique.length>MAX_FILES)throw Error('单次最多选择 20 个文件。');
      const total=[...rows.map(item=>item.file),...unique].reduce((sum,value)=>sum+(value.size||0),0);
      if(total>MAX_BATCH)throw Error('单批文件总大小不能超过 100 MB。');
      unique.forEach(value=>rows.push({file:value,status:'ready',detail:''}));
      render();return queue.items();
    },
    remove(value){const at=rows.findIndex(item=>item.file===value);if(at>=0)rows.splice(at,1);render();},
    clear(){rows.splice(0);if(file)file.value='';render();},
    items(){return rows.slice();},
    eligible(){return rows.filter(item=>item.status==='ready'||item.status==='failed');},
    setStatus(value,statusValue,detail=''){const item=rows.find(entry=>entry.file===value);if(item){item.status=statusValue;item.detail=detail;render();}},
  };

  const refresh=()=>{
    const upload=mode()==='file';
    const fileHost=document.getElementById('doc-dropzone')||file?.closest('label');
    if(fileHost)fileHost.classList.toggle('hidden',!upload);
    text?.closest('label')?.classList.toggle('hidden',upload);
    if(file){file.disabled=!upload;file.required=upload;}
    if(text){text.disabled=upload;text.required=!upload;}
    render();
  };
  modes.forEach(input=>input.addEventListener('change',refresh));
  if(file)file.onchange=()=>{try{queue.add(file.files);}catch(error){window.uploadQueueError?.(error);throw error;}finally{file.value='';}};
  const dropzone=document.getElementById('doc-dropzone');
  if(dropzone){
    for(const name of ['dragenter','dragover'])dropzone.addEventListener(name,event=>{event.preventDefault();dropzone.classList.add('drag-over');});
    for(const name of ['dragleave','drop'])dropzone.addEventListener(name,event=>{event.preventDefault();dropzone.classList.remove('drag-over');});
    dropzone.addEventListener('drop',event=>{try{queue.add(event.dataTransfer.files);}catch(error){window.uploadQueueError?.(error);}});
    dropzone.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();file?.click();}});
  }
  document.getElementById('doc-clear-files')?.addEventListener('click',()=>queue.clear());
  document.getElementById('doc-retry-files')?.addEventListener('click',()=>document.getElementById('ingest')?.click());

  function validateMetadata(){
    const input=document.getElementById('doc-meta');
    if(!input.value.trim())return {};
    let value;
    try{value=JSON.parse(input.value);}catch{input.focus();throw Error('Metadata 格式不正确，请填写 JSON 对象；不需要时可以留空。');}
    if(!value||Array.isArray(value)||typeof value!=='object'){input.focus();throw Error('Metadata 必须是 JSON 对象，例如 {"平台":"美团"}，或直接留空。');}
    return value;
  }
  window.readDocumentMetadata=()=>{
    const metadata=validateMetadata();
    const published=document.getElementById('doc-published-at').value;
    if(published){const instant=new Date(published);if(!Number.isFinite(instant.getTime()))throw Error('请输入有效的发布时间。');metadata.published_at=instant.toISOString();}
    return metadata;
  };
  window.readDocumentInput=async()=>{
    if(!document.getElementById('project').value)throw Error('请先在左侧选择或创建项目（必填）。');
    validateMetadata();
    if(mode()==='file'){
      if(!rows.length)throw Error('请先选择文档，或切换到“粘贴正文”。');
      return rows[0].file;
    }
    const title=document.getElementById('doc-title');
    if(!title.value.trim()){title.focus();throw Error('请填写文档标题（必填）。');}
    if(title.value.trim().length>300)throw Error('文档标题不能超过 300 个字符。');
    if(!text.value.trim())throw Error('正文不能为空，请填写正文。');
    return text.value;
  };
  window.readDocumentBatch=async(includeSubmitted=false)=>{
    await window.readDocumentInput();
    const metadata=window.readDocumentMetadata();
    if(mode()==='text')return [{title:document.getElementById('doc-title').value.trim(),text:text.value,metadata}];
    const custom=document.getElementById('doc-title').value.trim();
    if(custom.length>300)throw Error('文档标题不能超过 300 个字符。');
    const selected=includeSubmitted?queue.items():queue.eligible();
    if(!selected.length)throw Error('没有待提交或失败重试的文件。');
    return selected.map(item=>({file:item.file,queueItem:item,title:rows.length===1&&custom?custom:item.file.name,metadata:{...metadata}}));
  };
  window.uploadQueueError=error=>{const target=document.getElementById('doc-batch-help');if(target)target.textContent=error.message;};
  refresh();
})();
