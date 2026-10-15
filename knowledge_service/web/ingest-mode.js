/* File uploads stay as binary File objects; parsing belongs to the server. */
(() => {
  // Conservative fallback permits selection; submission requires capabilities.
  let limits={max_file_bytes:25_000_000,max_batch_items:20,max_batch_bytes:100_000_000,
    supported_extensions:['.txt','.md','.pdf','.docx','.html','.htm']};
  const file=document.getElementById('doc-file'),text=document.getElementById('doc-text');
  const modes=[...document.querySelectorAll('input[name="document-input-mode"]')];
  const rows=[];
  const mode=()=>modes.find(input=>input.checked)?.value||'file';
  const key=f=>[f.name,f.size||0,f.lastModified||0].join(':');
  const size=value=>value<1000?value+' B':value<1_000_000?(value/1000).toFixed(1)+' KB':(value/1_000_000)+' MB';
  const icon=name=>/\.pdf$/i.test(name)?'PDF':/\.docx$/i.test(name)?'DOCX':/\.html?$/i.test(name)?'HTML':/\.md$/i.test(name)?'MD':'TXT';
  const statusText=item=>({ready:'待提交',uploading:'正在上传',queued:'已进入队列',failed:'提交失败'}[item.status]||item.status);
  const field=id=>document.getElementById(id);
  const splitList=value=>[...new Set(String(value||'').split(/[,，]/).map(item=>item.trim()).filter(Boolean))];
  const instant=(id,label)=>{
    const value=field(id)?.value?.trim();
    if(!value)return undefined;
    const parsed=new Date(value);
    if(!Number.isFinite(parsed.getTime())){field(id)?.focus();throw Error('请输入有效的'+label+'。');}
    return parsed.toISOString();
  };
  const localTime=value=>value?String(value).replace('T',' '):'';
  const applicabilityOf=value=>value?.applicability||value?.metadata?.applicability||{};
  const dimensionValues=value=>Array.isArray(value)?value:(value?.mode==='specific'?value.values||[]:[]);
  const dimensionText=(label,value)=>{
    if(Array.isArray(value))return value.length?label+'：'+value.join('、'):label+'：暂不确定';
    if(value?.mode==='all')return label+'：全部适用';
    if(value?.mode==='specific')return label+'：'+dimensionValues(value).join('、');
    return label+'：暂不确定';
  };
  const describe=value=>{
    const applicability=applicabilityOf(value),parts=[];
    if(Object.keys(applicability).length){
      parts.push(dimensionText('平台',applicability.platforms));
      parts.push(dimensionText('业务',applicability.businesses));
      parts.push(dimensionText('对象',applicability.audiences));
    }
    const effective=value?.valid_from||value?.effective_at||value?.published_at;
    if(effective)parts.push('生效：'+localTime(effective));
    if(value?.valid_until)parts.push('失效：'+localTime(value.valid_until));
    else if(effective&&value&&'valid_until' in value)parts.push('持续有效');
    return parts.join(' · ');
  };
  const readDimension=name=>{
    const mode=field('doc-'+name+'-mode')?.value||'unknown';
    const values=mode==='specific'?splitList(field('doc-'+name)?.value):[];
    if(mode==='specific'&&!values.length){field('doc-'+name)?.focus();throw Error('指定范围时请至少填写一个值。');}
    if(values.length>64)throw Error('适用范围每个维度最多包含 64 个值。');
    return {mode,values};
  };
  const readApplicability=()=>({
    platforms:readDimension('platforms'),businesses:readDimension('businesses'),
    audiences:readDimension('audiences'),
  });
  const cloneApplicability=value=>JSON.parse(JSON.stringify(value));
  const syncDimension=name=>{
    const input=field('doc-'+name),mode=field('doc-'+name+'-mode')?.value||'unknown';
    if(input){input.disabled=mode!=='specific';input.setAttribute?.('aria-disabled',String(input.disabled));}
  };
  const updateEffectiveHelp=()=>{
    const help=field('doc-effective-help');if(!help)return;
    const published=field('doc-published-at')?.value?.trim(),explicit=field('doc-valid-from')?.value?.trim();
    const until=field('doc-valid-until')?.value?.trim(),effective=explicit||published;
    const source=explicit?'显式生效时间':published?'沿用发布时间':'尚未提供发布时间或生效时间';
    help.textContent=(effective?'实际生效：'+localTime(effective)+'（'+source+'）':source)+'；'+
      (until?'失效：'+localTime(until):effective?'未设失效时间，持续有效。':'未设失效时间。')+
      '时间区间按左闭右开 [生效, 失效) 解释。';
  };
  window.ApplicabilityUI={
    splitList,describe,dimensionValues,
    readQueryScope(){
      const platforms=splitList(field('query-platforms')?.value);
      return platforms.length?{applicability:{platforms,businesses:[],audiences:[]}}:{};
    },
  };

  async function loadApplicabilityVocabulary(){
    const project=field('project')?.value;
    const targets={platforms:'doc-platform-options',businesses:'doc-business-options',audiences:'doc-audience-options'};
    let values={platforms:[],businesses:[],audiences:[]};
    if(project){
      try{values=await api(endpoint('/applicability-values'),undefined,'GET');}
      catch(error){window.uploadQueueError?.(error);}
    }
    for(const [dimension,id] of Object.entries(targets)){
      const list=field(id);if(!list)continue;list.replaceChildren();
      for(const value of values[dimension]||[]){const option=document.createElement('option');option.value=value;list.append(option);}
    }
  }
  window.loadApplicabilityVocabulary=loadApplicabilityVocabulary;

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
    if(help)help.textContent=rows.length>1?'多文件分别以文件名作为标题；适用范围、发布时间、生效/失效时间和 Metadata 为本批共用。':'';
    const title=document.getElementById('doc-title');
    if(title){title.disabled=mode()==='file'&&rows.length>1;title.required=mode()==='text';}
    const submit=document.getElementById('ingest');
    if(submit)submit.textContent=mode()==='file'?(rows.length?`上传并解析 ${queue.eligible().length} 个文件`:'上传并处理'):'提交正文并处理';
  }

  const queue=window.DocumentUploadQueue={
    add(files){
      const incoming=[...files];
      for(const value of incoming){
        const extension='.'+(value.name||'').split('.').pop().toLowerCase();
        if(!limits.supported_extensions.map(x=>x.startsWith('.')?x:'.'+x).includes(extension))throw Error(value.name+'：不支持该文件格式。');
        if((value.size||0)>limits.max_file_bytes)throw Error(value.name+'：单个文件不能超过 '+size(limits.max_file_bytes)+'。');
      }
      const known=new Set(rows.map(item=>key(item.file)));
      const unique=incoming.filter(value=>!known.has(key(value))&&(known.add(key(value)),true));
      if(rows.length+unique.length>limits.max_batch_items)throw Error('单次最多选择 '+limits.max_batch_items+' 个文件。');
      const total=[...rows.map(item=>item.file),...unique].reduce((sum,value)=>sum+(value.size||0),0);
      if(total>limits.max_batch_bytes)throw Error('单批文件总大小不能超过 '+size(limits.max_batch_bytes)+'。');
      unique.forEach(value=>rows.push({file:value,status:'ready',detail:''}));
      render();return queue.items();
    },
    remove(value){const at=rows.findIndex(item=>item.file===value);if(at>=0)rows.splice(at,1);render();},
    clear(){rows.splice(0);queue.batchSession=null;if(file)file.value='';render();},
    setCapabilities(value){
      limits=value;
      const extensions=value.supported_extensions.map(x=>x.startsWith('.')?x:'.'+x);
      if(value.markdown_package&&!extensions.includes('.zip'))extensions.push('.zip');
      limits={...value,supported_extensions:extensions};
      if(file)file.accept=extensions.join(',');
      const help=document.getElementById('doc-upload-limits');
      if(help)help.textContent=`单文件最大 ${size(value.max_file_bytes)}，最多 ${value.max_batch_items} 个，单批合计不超过 ${size(value.max_batch_bytes)}。`;
      const chip=document.querySelector('.document-limit-chip');if(chip)chip.textContent='单文件 '+size(value.max_file_bytes);
      render();
    },
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
  window.readDocumentUploadOptions=()=>{
    const applicability=readApplicability();
    const options={metadata:validateMetadata(),applicability};
    options.metadata.applicability=cloneApplicability(applicability);
    const published_at=instant('doc-published-at','发布时间');
    const valid_from=instant('doc-valid-from','生效时间');
    const valid_until=instant('doc-valid-until','失效时间');
    const effective=valid_from||published_at;
    if(effective&&valid_until&&valid_until<=effective)throw Error('失效时间必须晚于生效时间。');
    if(published_at)options.published_at=published_at;
    if(valid_from)options.valid_from=valid_from;
    if(valid_until)options.valid_until=valid_until;
    updateEffectiveHelp();
    return options;
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
    const options=window.readDocumentUploadOptions();
    if(mode()==='text')return [{title:document.getElementById('doc-title').value.trim(),text:text.value,...options}];
    const custom=document.getElementById('doc-title').value.trim();
    if(custom.length>300)throw Error('文档标题不能超过 300 个字符。');
    const selected=includeSubmitted?queue.items():queue.eligible();
    if(!selected.length)throw Error('没有待提交或失败重试的文件。');
    return selected.map(item=>({file:item.file,queueItem:item,title:rows.length===1&&custom?custom:item.file.name,
      ...options,metadata:{...options.metadata,applicability:cloneApplicability(options.applicability)},
      applicability:cloneApplicability(options.applicability)}));
  };
  window.uploadQueueError=error=>{const target=document.getElementById('doc-batch-help');if(target)target.textContent=error.message;};
  for(const id of ['doc-published-at','doc-valid-from','doc-valid-until'])field(id)?.addEventListener('input',updateEffectiveHelp);
  for(const name of ['platforms','businesses','audiences']){
    field('doc-'+name+'-mode')?.addEventListener('change',()=>syncDimension(name));syncDimension(name);
  }
  field('project')?.addEventListener('change',loadApplicabilityVocabulary);
  updateEffectiveHelp();refresh();
})();
