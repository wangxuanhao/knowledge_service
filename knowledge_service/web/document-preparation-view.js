/* Human upload workflow; preparation endpoints are the same ones used by API clients. */
(function(root){
  'use strict';
  root.PreparationView={mount};
  function mount(host,{project,settings=()=>({}),modeSlot,advanced,onPublished=()=>{}}){
    const D=root.document,P=root.DocumentPreparation;
    const node=(tag,text,css)=>{const e=D.createElement(tag);if(text!==undefined)e.textContent=text;if(css)e.className=css;return e;};
    const makeButton=(text,fn,css='secondary')=>{const e=node('button',text,css);e.type='button';e.onclick=async()=>{const ticket=generation;e.disabled=true;try{await fn();}catch(error){if(ticket===generation)showError(error);}finally{e.disabled=false;if(e===next||e===retryAll)updateFooter();}};return e;};
    let session=null,viewData={items:[]},owner=null,step=0,active=false,timer=null,uploading=0,generation=0,editing=null,editorItemId=null,previewScope=null,busy=false,configurationVersion=0;
    const selected=new Set(),independent=new Set(),locals=new Map(),uploads=new Map();
    const container=node('section',undefined,'preparation-workflow');container.id='preparation-workflow';
    const notice=node('p',undefined,'preparation-notice');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');notice.hidden=true;
    const nav=node('nav',undefined,'library-upload-steps');nav.setAttribute('aria-label','添加文件步骤');
    const titles=['上传核对','配置资料','解析与检查','确认处理'];
    const tabs=titles.map((text,i)=>{const b=makeButton(`${i+1}  ${text}`,()=>go(i));nav.append(b);return b;});
    const stages=titles.map((_,i)=>{const e=node('section',undefined,'preparation-stage');e.dataset.preparationStep=i;return e;});
    const instruction=node('p','上传仅暂存和校验。配置完成并点击“开始解析”后，才运行文字识别或视频转写。','subtle');
    const input=node('input');input.type='file';input.id='preparation-files';input.multiple=true;
    const drop=node('label',undefined,'preparation-drop');drop.htmlFor=input.id;
    drop.append(node('strong','选择文件，或拖放到这里'),node('span','每个文件一份资料；ZIP 仅用于一个 MD / HTML 及关联资源。'),input);
    const limits=node('p',undefined,'subtle'),uploadList=node('div',undefined,'preparation-files');
    const drafts=node('details',undefined,'preparation-drafts');drafts.append(node('summary','继续未完成上传'));
    const draftList=node('div');drafts.append(node('p','未提交的文件会保留，关闭页面后可在这里继续；已提交任务请到“处理批次”查看。','subtle'),draftList);drafts.ontoggle=()=>{if(drafts.open)loadDrafts().catch(showError);};
    stages[0].append(instruction,drop,limits,uploadList,drafts);
    const configTools=node('div',undefined,'preparation-toolbar');
    const all=makeButton('全选',()=>{items().filter(i=>i.status!=='published').forEach(i=>selected.add(i.id));render();});
    const none=makeButton('清除选择',()=>{selected.clear();render();});
    const bulk=makeButton('批量设置所选文件',()=>openEditor(null));configTools.append(all,none,bulk);
    const configList=node('div',undefined,'preparation-files'),configSummary=node('p',undefined,'subtle'),bulkEditorHost=node('div',undefined,'preparation-bulk-editor');
    stages[1].append(node('p','同一批可以包含不同平台、不同业务。勾选文件可批量设置，也可逐份修改。','subtle'),configTools,configSummary,bulkEditorHost,configList);
    const editor=node('section',undefined,'preparation-editor');editor.id='preparation-settings-editor';editor.hidden=true;bulkEditorHost.append(editor);
    bulk.setAttribute('aria-controls',editor.id);bulk.setAttribute('aria-expanded','false');
    if(modeSlot)stages[1].append(modeSlot);
    if(advanced){
      stages[1].append(advanced);
      advanced.append(makeButton('将切片设置应用到所选文件',()=>run(async(target,current)=>{
        const ids=items().filter(i=>selected.has(i.id)&&i.status!=='published').map(i=>i.id);
        if(!ids.length)throw Error('请先勾选文件。');
        const values=root.readParseSettings();
        await target.bulk(ids,{chunk_strategy:values.chunk_strategy,chunk_size:values.chunk_size,chunk_overlap:values.chunk_overlap},'overwrite');
        if(!current())return;
        message('所选文件的切片设置已更新；已有正文将复用，只需更新切片结果。');
      })));
    }
    const progressSummary=node('p',undefined,'preparation-counts');progressSummary.setAttribute('role','status');
    const resultList=node('div',undefined,'preparation-files');
    const retryAll=makeButton('重试失败或过期项',()=>reparse(items().filter(retryable).map(i=>i.id)));
    stages[2].append(progressSummary,retryAll,resultList);
    const publishList=node('div',undefined,'preparation-files'),publicationSummary=node('p',undefined,'preparation-counts');
    const confirmationIntro=node('p',undefined,'subtle');stages[3].append(confirmationIntro,publicationSummary,publishList);
    const footer=node('div',undefined,'library-wizard-navigation');
    const previous=makeButton('上一步',()=>go(step-1));
    const next=makeButton('下一步：配置资料',async()=>{
      if(step===0){if(uploading)throw Error('文件正在上传，请稍候。');if(!items().length)throw Error('请先上传有效文件。');await go(1);}
      else if(step===1)await startParse();
      else if(step===2)go(3);
      else await publish();
    },'');footer.append(previous,next);
    const dropHint=node('div','松开鼠标即可添加文件','preparation-drop-hint');dropHint.hidden=true;dropHint.setAttribute('aria-hidden','true');drop.append(dropHint);
    const overlay=node('dialog',undefined,'preparation-preview');overlay.id='preparation-preview';
    const previewTitle=node('h3'),previewContent=node('div',undefined,'preparation-preview-body');
    const closePreview=()=>{previewScope?.dispose?.();previewScope=null;overlay.close();previewContent.replaceChildren();};
    overlay.append(previewTitle,makeButton('关闭预览',closePreview),previewContent);overlay.addEventListener('cancel',event=>{event.preventDefault();closePreview();});
    const refreshTools=node('div',undefined,'preparation-toolbar');refreshTools.append(makeButton('刷新未完成文件',async()=>{
      if(editing||busy||uploading)throw Error('请先完成当前上传或保存设置。');
      if(!session)return;const target=session,ticket=generation;await target.restore();if(ticket!==generation)return;const finished=clearSubmittedSession(target);if(!finished)restoreSettings();render();schedule();message(finished?'文件均已提交，可继续添加新文件；进度请到“处理批次”查看。':'已刷新未完成文件，请核对设置后继续。');
    }));
    container.append(nav,notice,refreshTools,...stages,footer,overlay);host.append(container);
    const items=()=>(viewData.items||[]).filter(item=>item.status!=='published');
    const processing=()=>P.processingCopy(viewData.publication_settings?.extraction_mode||D.getElementById('extraction-mode')?.value||'documents');
    const retryable=item=>['failed','cancelled','stale'].includes(item.status);
    const ready=item=>['completed','reused'].includes(item.status)&&item.result_ready!==false;
    const sameContent=item=>item.file_sha256||item.id;
    const requiresIndependent=item=>{
      if(item.existing_documents?.length)return true;
      const position=items().findIndex(i=>i.id===item.id);
      return items().slice(0,position).some(other=>selected.has(other.id)&&ready(other)&&sameContent(other)===sameContent(item));
    };
    function normalisePublication(){
      const seen=new Set();for(const item of items())if(selected.has(item.id)&&ready(item)){
        if((item.existing_documents?.length||seen.has(sameContent(item)))&&!independent.has(item.id))selected.delete(item.id);
        else seen.add(sameContent(item));
      }
    }
    const key=()=>`kg_preparation:${root.Auth?.user?.()?.id||'local'}:${owner}`;
    const saveId=id=>{try{root.localStorage?.setItem(key(),id);}catch{}};
    let noticeTimer=null;
    function hideNotice(){clearTimeout(noticeTimer);noticeTimer=null;notice.hidden=true;notice.textContent='';notice.classList.remove('error');}
    // 提示按类别自动收起：普通说明 5 秒，异常 9 秒；点击可立即关闭。
    function announce(text,isError,milliseconds){
      notice.hidden=false;notice.classList.toggle('error',isError);notice.textContent=text;notice.title='点击关闭提示';
      clearTimeout(noticeTimer);noticeTimer=setTimeout(hideNotice,milliseconds);
    }
    const showError=error=>announce(error.message||String(error),true,9000);
    const message=text=>announce(text,false,5000);
    notice.addEventListener('click',hideNotice);
    notice.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();hideNotice();}});
    function attach(id=null){
      session?.dispose();viewData={id,items:[]};const currentGeneration=++generation;
      session=P.createSession({project:owner,sessionId:id,upload:P.uploadRequest,onChange:data=>{
        if(currentGeneration!==generation)return;viewData=data;saveId(data.id);render();
      }});return session;
    }
    async function ensure(){
      if(owner!==project())reset();owner=project();if(!owner)throw Error('请先选择项目。');
      if(!session)attach();return session.ensure();
    }
    async function run(fn){
      if(busy)throw Error('正在保存操作，请稍候。');const ticket=generation,target=session;busy=true;render();
      try{return await fn(target,()=>ticket===generation&&target===session);}finally{if(ticket===generation){busy=false;render();}}
    }
    async function go(value){
      if(busy)throw Error('正在保存操作，请稍候。');
      if(value>0&&!items().length){showError(Error('请先上传有效文件。'));return;}
      if(value>=2&&!items().some(i=>!['staged','cancelled'].includes(i.status))){showError(Error('请先完成配置并开始解析。'));return;}
      if(value===3&&!items().some(ready)){showError(Error('没有解析完整的资料，请先重试失败项。'));return;}
      if(editing){showError(Error('请先保存或取消当前设置。'));return;}
      const ticket=generation;if(step===1&&value>1)await run(async(target,current)=>{await target.configureSettings(settings(),configurationVersion);if(current())configurationVersion=target.snapshot().settings_version||0;});if(ticket!==generation)return;
      if(value===1)configurationVersion=session.snapshot().settings_version||0;
      step=Math.max(0,Math.min(3,value));render();schedule();
    }
    function summary(options={}){
      const parts=[];
      for(const [name,label] of [['platforms','平台'],['businesses','业务'],['audiences','受众']]){
        const scope=(options.applicability||options.metadata?.applicability)?.[name];parts.push(label+'：'+(scope?.mode==='all'?'不限':scope?.mode==='specific'?scope.values.join('、'):'待确定'));
      }
      if(options.valid_from)parts.push('生效：'+new Date(options.valid_from).toLocaleString());
      return parts.join(' · ');
    }
    function rowFor(item,phase){
      const row=node('article',undefined,'preparation-file');row.dataset.status=item.status;row.dataset.itemId=item.id;
      const heading=node('div',undefined,'preparation-file-heading');
      if(phase===1||phase===3){const label=node('label',undefined,'preparation-select'),check=node('input');check.type='checkbox';check.checked=selected.has(item.id);check.disabled=item.status==='published'||phase===3&&(!ready(item)||(requiresIndependent(item)&&!independent.has(item.id)));check.setAttribute('aria-label','选择 '+item.original_name);check.onchange=()=>{check.checked?selected.add(item.id):selected.delete(item.id);if(phase===3)render();else updateFooter();};label.append(check);heading.append(label);}
      heading.append(node('strong',item.options?.title||item.original_name),node('span',P.progressLabel(item),'preparation-state'));row.append(heading);
      const size=item.size_bytes<1024?item.size_bytes+' B':item.size_bytes<1048576?(item.size_bytes/1024).toFixed(1)+' KB':(item.size_bytes/1048576).toFixed(1)+' MB';
      row.append(node('p',item.original_name+(item.size_bytes?' · '+size:''),'preparation-filename'));
      if(phase!==0)row.append(node('p',summary(item.options),'preparation-scope'));
      if(item.reusable||item.duplicate_of)row.append(node('p','检测到相同文件；兼容的解析结果或运行任务将复用。','subtle'));
      if(phase===3&&ready(item)&&(requiresIndependent(item)||item.duplicate_of||independent.has(item.id))){
        row.append(node('p',item.existing_documents?.length?'已有相同内容的资料，默认不重复入库。可查看原资料后修订；需要保留另一份时明确选择独立入库。':'清单中有相同内容，默认只入库一份；需要另一份时明确选择独立入库。','subtle'));
        const label=node('label',undefined,'preparation-enable'),copy=node('input');copy.type='checkbox';copy.checked=independent.has(item.id);copy.setAttribute('aria-label','确认将 '+item.original_name+' 作为独立资料入库');
        copy.onchange=()=>{if(copy.checked){independent.add(item.id);selected.add(item.id);}else{independent.delete(item.id);selected.delete(item.id);}render();};label.append(copy,node('span','作为独立资料入库'));row.append(label);
      }
      if(['running','queued','cancelling'].includes(item.status)){
        const progress=node('progress');progress.setAttribute('aria-label',P.progressLabel(item));
        if(item.progress?.total>0){progress.max=item.progress.total;progress.value=item.progress.completed;}
        row.append(progress);
        if(item.started_at){const seconds=Math.max(0,Math.floor((Date.now()-new Date(item.started_at))/1000));row.append(node('small',`已耗时 ${seconds} 秒`+(item.heartbeat_at?' · 最近活动 '+new Date(item.heartbeat_at).toLocaleTimeString():'')));}
      }
      if(item.error)row.append(node('p',typeof item.error==='string'?item.error:item.error.message||JSON.stringify(item.error),'preparation-error'));
      const actions=node('div',undefined,'preparation-file-actions');
      actions.append(makeButton('原件预览',()=>previewOriginal(item)));
      if(phase===0)actions.append(makeButton('移除',()=>run(()=>session.remove(item.id))));
      if(phase===1&&item.status!=='published'){
        const settingsButton=makeButton('设置',()=>openEditor(item));settingsButton.classList.add('preparation-settings-toggle');settingsButton.setAttribute('aria-controls',editor.id);settingsButton.setAttribute('aria-expanded',String(Boolean(editing&&editorItemId===item.id)));actions.append(settingsButton);
      }
      if(phase>=2&&ready(item))actions.append(makeButton('查看正文与切片',()=>previewResult(item)),makeButton('重新拆分',()=>rechunk([item.id])));
      if(phase===2&&['queued','running'].includes(item.status))actions.append(makeButton('取消解析',()=>run(()=>session.cancel([item.id]))));
      if(phase>=1&&retryable(item))actions.append(makeButton('重新解析',()=>reparse([item.id])));
      if(item.batch_id)actions.append(makeButton('查看处理进度',()=>onPublished({batch_id:item.batch_id})));
      if(phase===3&&root.SourceReader)for(const original of item.existing_documents||[])if(original.source_version_id)actions.append(makeButton('查看已有资料：'+original.title,()=>{root.LibraryWorkspace?.close({restoreFocus:false});return root.SourceReader.open({projectId:owner,versionId:original.source_version_id});}));
      row.append(actions);return row;
    }
    function render(){
      for(const item of viewData.items||[])if(item.status==='published')selected.delete(item.id);
      if(step===3)normalisePublication();
      editor.inert=busy;configTools.inert=busy;
      if(!editing){editor.hidden=true;bulkEditorHost.append(editor);}
      bulk.setAttribute('aria-expanded',String(Boolean(editing&&editorItemId===null&&!editor.hidden)));
      stages.forEach((e,i)=>e.hidden=step!==i);tabs.forEach((e,i)=>e.setAttribute('aria-current',step===i?'step':'false'));
      for(const [phase,list] of [[0,uploadList],[1,configList],[2,resultList],[3,publishList]]){
        if(phase===1&&step===1&&editing)continue;
        list.replaceChildren();if(phase===step)for(const item of items())list.append(rowFor(item,phase));
      }
      if(step===0)for(const [file,entry] of uploads){
        const row=node('article',undefined,'preparation-file'),progress=node('progress');
        if(entry.total){progress.max=entry.total;progress.value=entry.loaded;}
        row.append(node('strong',file.name),node('p',entry.error||(entry.total&&entry.loaded>=entry.total?'上传完成，正在校验…':'正在上传…')),progress);
        if(entry.error)row.append(makeButton('重试上传',()=>uploadOne(file,entry)),makeButton('移除失败文件',()=>{uploads.delete(file);render();}));
        uploadList.append(row);
      }
      const count=states=>items().filter(i=>states.includes(i.status)).length;
      progressSummary.textContent=`已完成 ${count(['completed','reused','published'])} / ${items().length} · 正在解析 ${count(['running'])} · 排队 ${count(['queued'])} · 失败 ${count(['failed'])} · 需重新解析 ${count(['stale'])}`;
      const publication=viewData.publication_settings||{};
      confirmationIntro.textContent=processing().description+' 解析结果直接复用，提交后在“处理批次”查看进度。';
      publicationSummary.textContent='处理方式：'+processing().label+(publication.ontology_id?' · 本体版本：'+publication.ontology_id:'');
      updateFooter();
    }
    function updateFooter(){
      tabs.forEach(e=>e.disabled=busy);
      previous.hidden=step===0;next.disabled=busy||uploading>0;
      const parseCount=items().filter(i=>selected.has(i.id)&&i.status!=='published').length;
      const chosen=items().filter(i=>selected.has(i.id)&&i.status!=='published'),reusable=chosen.filter(i=>i.reusable).length,
        pending=chosen.filter(i=>!i.reusable&&['queued','running'].includes(i.existing_task_status||i.status)).length;
      configSummary.textContent=`已选 ${parseCount} 份 · 需要解析 ${parseCount-reusable-pending} 份 · 可复用 ${reusable} 份 · 已有运行任务 ${pending} 份`;
      const retryCount=items().filter(retryable).length;
      retryAll.textContent=`重试失败或过期项（${retryCount}）`;retryAll.disabled=busy||retryCount===0;
      const publishCount=items().filter(i=>selected.has(i.id)&&ready(i)).length;
      next.textContent=['下一步：配置资料',`开始解析（${parseCount} 份）`,'下一步：确认处理',`${processing().action}（${publishCount} 份）`][step];
      if(step===2)next.disabled=busy||uploading>0||!items().some(ready);
      if(step===3)next.disabled=busy||uploading>0||publishCount===0;
    }
    async function uploadOne(file,old){
      const ticket=generation,target=session,entry=old||{key:root.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2),loaded:0,total:0};
      uploads.set(file,entry);entry.error=null;uploading++;render();
      try{
        const item=await target.upload(file,file.name,(loaded,total)=>{if(ticket!==generation)return;entry.loaded=loaded;entry.total=total;render();},entry.key);
        if(ticket!==generation)return;locals.set(item.id,file);uploads.delete(file);selected.add(item.id);
      }catch(error){if(ticket===generation){entry.error=error.message;showError(error);}}
      finally{if(ticket===generation){uploading--;render();}}
    }
    async function addFiles(files){
      const requestedProject=project(),owned=await ensure();
      if(requestedProject!==project()||!session||owned.id!==session.snapshot().id)return;
      const ticket=generation,capabilities=await P.request('/api/ingest/capabilities');
      if(ticket!==generation)return;
      input.accept=capabilities.supported_extensions.join(',');
      limits.textContent=`单文件最大 ${(capabilities.max_file_bytes/1000000).toFixed(1)} MB；ZIP 无需 manifest.json。`+
        (capabilities.asr_max_duration_seconds?` 视频转写最长 ${capabilities.asr_max_duration_seconds} 秒。`:'');
      for(const file of files){
        if(ticket!==generation)return;
        const suffix='.'+file.name.split('.').pop().toLowerCase();
        if(!capabilities.supported_extensions.includes(suffix)){showError(Error(file.name+(suffix==='.doc'?'：暂不支持旧版 DOC，请用 Word 等工具另存为 DOCX 后上传，修改后缀无效。':'：当前不支持该格式。')));continue;}
        if(file.size>capabilities.max_file_bytes){showError(Error(file.name+'：超过单文件大小上限。'));continue;}
        if([...locals.values(),...uploads.keys()].some(old=>old===file)){message(file.name+' 已在清单中。');continue;}
        await uploadOne(file);
      }
    }
    input.onchange=()=>{const files=[...input.files],p=project();input.value='';addFiles(files).catch(error=>{if(p===project())showError(error);});};
    // The whole workflow accepts drops, not just the dashed hint: dropping next to
    // the box used to navigate the browser to the file or silently do nothing.
    // 接收范围只限"选择文件"虚线框本身：热区与拖动高亮都不许超出这个区域。
    const carriesFiles=event=>[...(event.dataTransfer?.types||[])].includes('Files');
    let dragTimer=null;
    const dragging=on=>{drop.classList.toggle('drag-over',on);dropHint.hidden=!on;};
    const holdDrag=()=>{dragging(true);clearTimeout(dragTimer);dragTimer=setTimeout(()=>dragging(false),180);};
    drop.addEventListener('dragenter',event=>{if(!carriesFiles(event))return;event.preventDefault();holdDrag();});
    drop.addEventListener('dragover',event=>{if(!carriesFiles(event))return;event.preventDefault();if(event.dataTransfer)event.dataTransfer.dropEffect='copy';holdDrag();});
    drop.addEventListener('dragleave',event=>{if(!carriesFiles(event))return;event.preventDefault();if(!drop.contains(event.relatedTarget)){clearTimeout(dragTimer);dragging(false);}});
    drop.addEventListener('drop',event=>{
      if(!carriesFiles(event))return;event.preventDefault();clearTimeout(dragTimer);dragging(false);
      const files=[...event.dataTransfer.files];if(!files.length)return;
      if(step!==0){step=0;render();}
      const p=project();addFiles(files).catch(error=>{if(p===project())showError(error);});
    });
    // 抽屉内其它位置不接收拖放，但也不让浏览器把文件当成新页面打开。
    const guardDrag=event=>{if(active&&carriesFiles(event))event.preventDefault();};
    D.addEventListener('dragover',guardDrag);D.addEventListener('drop',guardDrag);
    function field(label,value,type='text'){
      const wrap=node('label',undefined,'preparation-field'),input=node(type==='select'?'select':'input');
      if(type!=='select')input.type=type;input.value=value??'';input.setAttribute('aria-label',label);wrap.append(node('span',label),input);return {wrap,input};
    }
    function openEditor(item){
      if(busy)throw Error('正在保存设置，请稍候。');
      if(editing){
        if(editorItemId===(item?.id||null)){
          editor.hidden=!editor.hidden;
          const control=item?[...configList.children].find(e=>e.dataset.itemId===item.id)?.querySelector('.preparation-settings-toggle'):bulk;
          control?.setAttribute('aria-expanded',String(!editor.hidden));
          if(!editor.hidden)editor.scrollIntoView?.({block:'nearest'});return;
        }
        throw Error('请先展开当前设置，保存或取消后再设置其他文件。');
      }
      const ids=item?[item.id]:items().filter(i=>selected.has(i.id)).map(i=>i.id);
      const editorVersions=Object.fromEntries(items().filter(i=>ids.includes(i.id)).map(i=>[i.id,i.metadata_version]));
      if(!ids.length)throw Error('请先勾选要批量设置的文件。');
      if(items().some(i=>ids.includes(i.id)&&i.status==='published'))throw Error('已提交入库的资料请到资料库修订。');
      editing=ids;editorItemId=item?.id||null;editor.replaceChildren();editor.hidden=false;
      if(item){
        const row=[...configList.children].find(e=>e.dataset.itemId===item.id);row.append(editor);row.querySelector('.preparation-settings-toggle').setAttribute('aria-expanded','true');
      }else{bulkEditorHost.append(editor);bulk.setAttribute('aria-expanded','true');}
      editor.append(node('h3',item?'设置：'+item.original_name:`批量设置 ${ids.length} 份资料`));
      const options=item?.options||{},controls=[],grid=node('div',undefined,'preparation-editor-grid');
      const more=node('details',undefined,'preparation-more'),moreGrid=node('div',undefined,'preparation-editor-grid');more.append(node('summary','时间与识别、切片设置'),moreGrid);
      const add=(key,label,control,read)=>{
        const wrap=node('div',undefined,'preparation-edit-field'),enable=node('input');enable.type='checkbox';enable.checked=!!item;enable.setAttribute('aria-label','修改'+label);
        if(!item){const enabled=node('label',undefined,'preparation-enable');enabled.append(enable,node('span','修改'+label));wrap.append(enabled);}
        wrap.append(control);(key==='title'||key.startsWith('applicability.')?grid:moreGrid).append(wrap);controls.push({key,enable,read});
      };
      if(item){const title=field('资料标题',options.title||item.original_name);title.input.id='preparation-edit-title';add('title','标题',title.wrap,()=>title.input.value.trim());}
      for(const [dimension,label] of [['platforms','平台'],['businesses','业务'],['audiences','受众']]){
        const scope=(options.applicability||options.metadata?.applicability)?.[dimension],mode=field(label+'范围',null,'select'),values=field(label+'值（逗号分隔）',scope?.values?.join('，')||'');
        for(const [value,text] of [['unknown','暂不确定'],['all','不限'],['specific','指定范围']]){const option=node('option',text);option.value=value;mode.input.append(option);}
        mode.input.value=scope?.mode||'unknown';values.input.setAttribute('list',{platforms:'doc-platform-options',businesses:'doc-business-options',audiences:'doc-audience-options'}[dimension]);
        values.input.disabled=mode.input.value!=='specific';mode.input.onchange=()=>values.input.disabled=mode.input.value!=='specific';
        const group=node('div');group.append(mode.wrap,values.wrap);
        add('applicability.'+dimension,label,group,()=>{
          const result={mode:mode.input.value,values:mode.input.value==='specific'?[...new Set(values.input.value.split(/[,，]/).map(x=>x.trim()).filter(Boolean))]:[]};
          if(result.mode==='specific'&&!result.values.length)throw Error('请填写'+label+'值。');return result;
        });
      }
      const time=field('时间识别方式',null,'select');for(const [value,label] of [['auto','从正文识别（填写项优先）'],['manual','手工填写']]){const option=node('option',label);option.value=value;time.input.append(option);}time.input.value=options.document_time_mode||'auto';add('document_time_mode','时间方式',time.wrap,()=>time.input.value);
      const ocr=field('扫描文字识别',null,'select');for(const [value,label] of [['default','沿用系统设置'],['auto','自动识别扫描文字'],['disabled','关闭扫描文字识别']]){const option=node('option',label);option.value=value;ocr.input.append(option);}ocr.input.value=options.parse_config?.ocr_mode||'default';add('parse_config','识别方式',ocr.wrap,()=>ocr.input.value==='default'?{}:{ocr_mode:ocr.input.value});
      for(const [key,label] of [['published_at','发布时间'],['valid_from','生效时间'],['valid_until','失效时间']]){
        const raw=options[key],value=raw?new Date(new Date(raw).getTime()-new Date(raw).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
        const control=field(label,value,'datetime-local');add(key,label,control.wrap,()=>control.input.value?new Date(control.input.value).toISOString():null);
      }
      for(const [key,label,fallback] of [['chunk_size','切片最大字符数',1800],['chunk_overlap','重叠字符数',200]]){
        const control=field(label,options[key]??fallback,'number');control.input.min=key==='chunk_size'?100:0;control.input.max=key==='chunk_size'?10000:2000;add(key,label,control.wrap,()=>Number(control.input.value));
      }
      editor.append(grid,more);
      const bulkMode=field('应用方式',null,'select');
      for(const [value,label] of [['fill','只填未设置项'],['overwrite','覆盖所选文件的勾选字段']]){const o=node('option',label);o.value=value;bulkMode.input.append(o);}
      if(!item)editor.append(bulkMode.wrap,node('p','只修改勾选字段；覆盖操作会更新以上所选文件。','subtle'));
      const tools=node('div',undefined,'preparation-toolbar');tools.append(makeButton('取消设置',()=>{editing=null;editorItemId=null;editor.hidden=true;render();}),makeButton('保存设置',()=>run(async(target,current)=>{
        const fields={};for(const control of controls)if(control.enable.checked){
          const value=control.read();if(control.key.startsWith('applicability.')){fields.applicability??={};fields.applicability[control.key.split('.')[1]]=value;}else fields[control.key]=value;
        }
        if(!Object.keys(fields).length)throw Error('请至少勾选一个要修改的字段。');
        if(item)await target.configure(item.id,P.applyFields(options,fields),item.metadata_version);else await target.bulk(ids,fields,bulkMode.input.value,editorVersions);
        if(!current())return;
        editing=null;editorItemId=null;editor.hidden=true;message('设置已保存。修改平台或时间不会重新识别正文。');
      }),''));editor.append(tools);editor.scrollIntoView?.({block:'nearest'});
    }
    async function reparse(ids){
      if(!ids.length)throw Error('没有需要重新解析的文件。');
      if(editing)throw Error('请先保存或取消当前设置。');
      await run(async(target,current)=>{
        await target.parse(ids);if(!current())return;
        if(overlay.open)closePreview();
        step=2;message('已提交重新解析，沿用原文件和资料配置；兼容的结果将直接复用。');schedule();
      });
    }
    async function rechunk(ids){
      // 已解析文件按当前切片设置重新拆分：正文与识别结果复用，不重复 OCR/转写。
      if(!ids.length)throw Error('没有需要重新拆分的文件。');
      if(editing)throw Error('请先保存或取消当前设置。');
      await run(async(target,current)=>{
        await target.configureSettings(settings(),configurationVersion);if(!current())return;
        configurationVersion=target.snapshot().settings_version||0;
        await target.parse(ids);if(!current())return;
        if(overlay.open)closePreview();
        step=2;message('已按当前切片设置重新拆分；正文与识别结果直接复用，无需重新识别。');schedule();
      });
    }
    async function startParse(){
      if(editing)throw Error('请先保存或取消当前设置。');
      await run(async(target,current)=>{
        const ids=items().filter(i=>selected.has(i.id)&&i.status!=='published').map(i=>i.id);if(!ids.length)throw Error('请勾选要解析的文件。');
        await target.configureSettings(settings(),configurationVersion);if(!current())return;configurationVersion=target.snapshot().settings_version||0;
        await target.parse(ids);if(!current())return;step=2;
        message(target.snapshot().items.filter(i=>ids.includes(i.id)).every(i=>i.status==='reused')?'已复用解析结果，无需重复识别；请核对正文与切片。':'解析任务已提交；可离开页面，稍后恢复进度。');schedule();
      });
    }
    async function publish(){
      let sent=null;
      await run(async(target,current)=>{
        const ids=items().filter(i=>selected.has(i.id)&&ready(i)).map(i=>i.id);if(!ids.length)throw Error('请选择已核对、解析完成的文件。');
        const result=await target.publish(ids,{},ids.filter(id=>independent.has(id)));if(!current())return;
        sent={target,result,copy:processing()};
      });
      if(!sent||session!==sent.target)return;
      const finished=clearSubmittedSession(sent.target);if(!finished)step=items().length?2:0;
      message(sent.copy.done+'，请到“处理批次”查看进度。');render();onPublished(sent.result);
    }
    function schedule(){
      clearTimeout(timer);if(!active||!session||!items().some(i=>['queued','running','cancelling'].includes(i.status)))return;
      const captured=session;
      timer=setTimeout(async()=>{
        try{if(captured===session)await captured.restore();}catch(error){if(captured===session)showError(error);}finally{if(captured===session)schedule();}
      },2000);
    }
    async function previewOriginal(item){
      previewScope?.dispose?.();previewScope=root.SourceContent?.createResourceScope()||(()=>{const urls=[];return {objectUrl(blob){const url=URL.createObjectURL(blob);urls.push(url);return url;},dispose(){urls.forEach(url=>URL.revokeObjectURL(url));}};})();
      const ticket=previewScope;previewTitle.textContent=item.original_name;previewContent.replaceChildren(node('p','正在打开原件…'));overlay.showModal();
      const suffix=item.original_name.split('.').pop().toLowerCase();
      try{
        if(['zip','md','html','htm','docx'].includes(suffix)){
          const result=await session.preview(item.id);if(!overlay.open||ticket!==previewScope)return;
          const frame=node('iframe');frame.title='原件预览';frame.setAttribute('sandbox','');frame.srcdoc=result.html;
          if(suffix==='docx'){
            const download=node('a','下载原件');download.href=await session.original(item.id);download.download=item.original_name;
            download.className='preparation-download';
            previewContent.replaceChildren(node('p','按 Word 原文结构渲染，字体以本机系统字体呈现；分页、页眉页脚与自动编号以 Word 打开为准。','subtle'),download,frame);
          }else previewContent.replaceChildren(frame);
          for(const warning of result.warnings||[])previewContent.append(node('p',String(warning),'subtle'));return;
        }
        const path=await session.original(item.id),response=await root.fetch(path);if(!response.ok)throw Error('原件读取失败。');
        const blob=await response.blob();if(!overlay.open||ticket!==previewScope)return;
        const url=previewScope?previewScope.objectUrl(blob):URL.createObjectURL(blob);
        const download=node('a','下载原件');download.href=url;download.download=item.original_name;
        if(['pdf','png','jpg','jpeg','webp','mp4','webm'].includes(suffix)){
          const media=node(suffix==='pdf'?'iframe':['mp4','webm'].includes(suffix)?'video':'img');media.src=url;media.title=item.original_name;media.alt=item.original_name;if(media.tagName==='VIDEO')media.controls=true;previewContent.replaceChildren(download,media);
        }else if(suffix==='txt')previewContent.replaceChildren(download,node('pre',await blob.text()));
        else previewContent.replaceChildren(node('p','该格式暂不支持原版式预览，请下载原件核对；开始解析后可查看正文。'),download);
      }catch(error){if(overlay.open&&ticket===previewScope)previewContent.replaceChildren(node('p',error.message,'preparation-error'));}
    }
    async function previewResult(item){
      previewScope?.dispose?.();previewScope=null;const marker={};previewScope=marker;
      previewTitle.textContent=(item.options?.title||item.original_name)+' · 正文与切片';previewContent.replaceChildren(node('p','读取结果…'));overlay.showModal();
      try{const result=await session.result(item.id);if(!overlay.open||previewScope!==marker)return;
        previewContent.replaceChildren(node('h4','解析正文'),node('pre',result.text||'未识别到正文'));
        for(const warning of result.warnings||[])previewContent.append(node('p',typeof warning==='string'?warning:(warning.message||warning.code||'解析提示'),warning?.severity==='warning'?'subtle':'preparation-error'));
        const chunks=result.chunks||[];previewContent.append(node('h4',`切片（${chunks.length}）`));
        chunks.forEach((chunk,i)=>{const detail=node('details'),location=P.sourceLabel(chunk),summary=node('summary',`片段 ${i+1} · ${(chunk.text||'').length} 字`+(location?' · '+location:''));detail.append(summary,node('pre',chunk.text||''));previewContent.append(detail);});
      }catch(error){if(overlay.open&&previewScope===marker){
        previewContent.replaceChildren(node('p',error.message,'preparation-error'));
        if(error.status===409&&item.status!=='published')previewContent.append(makeButton('重新解析此文件',()=>reparse([item.id])));
      }}
    }
    async function loadDrafts(){
      const p=owner,result=await P.request('/api/projects/'+encodeURIComponent(p)+'/preparations');if(owner!==p)return;
      draftList.replaceChildren();for(const draft of result.items||[]){const unfinished=(draft.items||[]).filter(i=>i.status!=='published');if(!unfinished.length)continue;draftList.append(makeButton(`继续上传 · ${unfinished.length} 份 · ${unfinished[0].original_name}`,async()=>{
        if(uploading||busy)throw Error('请等待当前操作完成。');const restored=attach(draft.id),ticket=generation;await restored.restore();if(ticket!==generation)return;if(clearSubmittedSession(restored)){render();message('这些文件已提交，请到“处理批次”查看进度。');return;}restoreSettings();selected.clear();items().forEach(i=>selected.add(i.id));step=items().some(i=>['running','queued','completed','reused','failed','stale','cancelled'].includes(i.status))?2:0;render();schedule();
      }));}
      if(!draftList.children.length)draftList.append(node('p','没有未完成的上传。'));
    }
    function clearSubmittedSession(target=session){
      if(!target||target!==session||!viewData.items?.length||items().length||uploading>0||uploads.size>0)return false;
      clearTimeout(timer);target.dispose();session=null;viewData={items:[]};step=0;configurationVersion=0;
      selected.clear();independent.clear();locals.clear();uploads.clear();editing=null;editorItemId=null;editor.hidden=true;
      try{root.localStorage?.removeItem(key());}catch{}
      return true;
    }
    function reset(){
      clearTimeout(timer);session?.dispose();session=null;viewData={items:[]};owner=null;generation++;step=0;selected.clear();independent.clear();locals.clear();uploads.clear();uploading=0;busy=false;editing=null;editorItemId=null;editor.hidden=true;hideNotice();previewScope?.dispose?.();previewScope=null;if(overlay.open)overlay.close();render();
    }
    function restoreSettings(){
      configurationVersion=session?.snapshot().settings_version||0;
      const saved=session?.snapshot().publication_settings;if(!saved)return;
      const mode=D.getElementById('extraction-mode');if(mode&&saved.extraction_mode){mode.value=saved.extraction_mode;mode.dispatchEvent(new Event('change'));}
      for(const [key,element] of Object.entries({chunk_strategy:'parse-strategy',chunk_size:'parse-size',chunk_overlap:'parse-overlap',extract_attributes:'parse-attributes',relation_constraint_mode:'parse-relation-constraints',resolve_entities:'parse-resolve',auto_merge:'parse-merge',merge_threshold:'parse-threshold'})){
        const input=D.getElementById(element);if(!input||saved[key]===undefined)continue;if(input.type==='checkbox')input.checked=saved[key];else input.value=saved[key];
      }
    }
    render();
    return {
      async open(){
        active=true;if(owner!==project())reset();owner=project();
        if(!session){let old;try{old=root.localStorage?.getItem(key());}catch{}
          if(old){const restored=attach(old),ticket=generation;try{await restored.restore();if(ticket!==generation)return;if(clearSubmittedSession(restored)){render();return;}restoreSettings();items().forEach(i=>selected.add(i.id));step=items().some(i=>['queued','running','completed','reused','published','failed','stale','cancelled'].includes(i.status))?2:0;}catch(error){if(ticket!==generation)return;if([403,404,410].includes(error.status)){try{root.localStorage.removeItem(key());}catch{}session.dispose();session=null;}else showError(error);}}
        }
        render();schedule();
      },
      close(){active=false;clearTimeout(timer);clearTimeout(dragTimer);dragging(false);previewScope?.dispose?.();previewScope=null;if(overlay.open)overlay.close();},
      projectChanged:reset,snapshot:()=>({step,project:owner,session:session?.snapshot()}),
    };
  }
})(window);
