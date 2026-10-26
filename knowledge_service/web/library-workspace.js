/* Layout adapter only: original upload nodes and server submission remain authoritative. */
(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LibraryWorkspace=api.init();
})(typeof window==='object'?window:globalThis,root=>{
  function createState({canWrite=()=>false,clear=()=>{}}={}){
    let owner=null,active=false,mode='upload',draftMode='upload',step=0,generation=0;
    const snapshot=()=>({project:owner,active,mode,step,generation});
    return {
      snapshot,
      open(next,project){
        if(next===undefined)next=owner===project?draftMode:'upload';
        if(!['upload','batches','structured'].includes(next))throw Error('未知资料操作');
        if(!project)throw Error('请先选择项目');
        if(next!=='batches'&&!canWrite())throw Error('只有管理员可以添加资料或导入记录');
        if(owner&&owner!==project){clear();step=0;draftMode='upload';}
        if(next!=='batches')draftMode=next;
        owner=project;active=true;mode=next;generation++;return snapshot();
      },
      step(value){step=Math.max(0,Math.min(2,Number(value)||0));return snapshot();},
      close(){active=false;generation++;},
      projectChanged(project){active=false;generation++;if(owner&&owner!==project){clear();step=0;draftMode='upload';}owner=project||null;},
      ticket(){return {project:owner,generation};},
      accepts(ticket,project){return Boolean(active&&ticket&&ticket.project===project&&owner===project&&ticket.generation===generation);},
    };
  }
  function init(){
    const document=root.document,get=id=>document.getElementById(id),host=get('tab-ingest');
    const node=(tag,css,text)=>{const e=document.createElement(tag);if(css)e.className=css;if(text!==undefined)e.textContent=text;return e;};
    const button=(text,fn,css='secondary')=>{const e=node('button',css,text);e.type='button';e.onclick=fn;return e;};
    const project=()=>typeof current==='string'?current:'';
    const panel=host.querySelector('.document-upload-panel'),batches=get('ingest-batches'),structured=host.querySelector('.structured-import');
    host.firstElementChild.hidden=true;
    const state=createState({canWrite:()=>Boolean(root.Auth?.isAdmin?.()),clear(){
      root.DocumentUploadQueue?.clear();
      for(const id of ['doc-title','doc-meta','doc-platforms','doc-businesses','doc-audiences','doc-published-at','doc-valid-from','doc-valid-until'])get(id).value='';
      for(const name of ['platforms','businesses','audiences']){const select=get('doc-'+name+'-mode');select.value='unknown';select.dispatchEvent(new Event('change'));}
      get('batch').value=initialBatch;get('doc-submit-results').replaceChildren();
    }});
    const initialBatch=get('batch').value;
    const shell=root.WorkspaceDrawers.create({id:'library-workspace-drawer',title:'添加文件',
      description:'关闭后保留本页未提交内容；已提交的处理批次继续运行。',content:host,dismissOnBackdrop:false,
      onClose(){if(typeof closeChunkPreview==='function')closeChunkPreview();state.close();host.classList.add('hidden');root.IngestBatches?.visibilityChanged();notice.textContent='';}});
    const title=get('library-workspace-drawer-title');
    const nav=node('nav','library-workspace-nav');nav.setAttribute('aria-label','资料操作');
    const sections={upload:node('section','library-operation'),batches:node('section','library-operation'),structured:node('section','library-operation')};
    const tabs={};
    for(const [key,label] of [['upload','添加文件'],['batches','处理批次'],['structured','结构化导入']]){
      tabs[key]=button(label,()=>open(key),key==='batches'?'secondary':'secondary admin-only');
      tabs[key].dataset.libraryView=key;nav.append(tabs[key]);
    }
    sections.upload.append(panel);sections.batches.append(batches,host.querySelector('.ingest-results-bar'));
    structured.open=true;structured.querySelector('summary').hidden=true;sections.structured.append(structured);
    const note=node('p','subtle','导入结构化记录会执行整批校验；已有记录请到知识台账修订。');sections.structured.prepend(note);
    const notice=node('p','library-workspace-notice');notice.id='library-workspace-notice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    const context=node('p','library-project-context');context.id='library-project-context';
    host.append(nav,context,notice,...Object.values(sections));
    const taskLink=button('打开构建与任务 ↗',()=>{shell.close({restoreFocus:false});showRuntimeView('jobs');});sections.batches.append(taskLink);
    const workspace=panel.querySelector('.document-upload-workspace'),fileMain=panel.querySelector('.document-upload-main'),options=panel.querySelector('.document-options');
    const advanced=panel.querySelector('.parse-settings'),actions=panel.querySelector('.document-actions');
    const stages=[0,1,2].map(()=>node('section','library-upload-stage'));
    workspace.append(...stages);
    stages[0].append(fileMain);stages[1].append(get('ingest-mode-slot'),options);
    const summary=node('div','library-upload-confirm');summary.id='library-upload-confirm';summary.setAttribute('aria-live','polite');
    stages[2].append(node('h3',null,'确认本批处理'),summary,advanced,actions);
    const metadata=node('details','library-upload-metadata');metadata.append(node('summary',null,'更多资料字段（Metadata）'));
    const metadataLabel=get('doc-meta').closest('label');metadata.append(metadataLabel,get('doc-meta-help'));options.append(metadata);
    const steps=node('nav','library-upload-steps');steps.setAttribute('aria-label','添加文件步骤');
    const stepButtons=['选择文件','资料与处理方式','确认并处理'].map((text,i)=>{
      const b=button(String(i+1).padStart(2,'0')+'  '+text,()=>setStep(i));b.dataset.uploadStep=i;steps.append(b);return b;
    });
    workspace.replaceChildren(...stages);panel.append(steps,workspace);
    const footer=node('div','library-wizard-navigation'),previous=button('上一步',()=>setStep(state.snapshot().step-1)),next=button('下一步：资料与处理方式',()=>setStep(state.snapshot().step+1),'');
    footer.append(previous,next);panel.append(footer);
    get('ingest').textContent='开始处理';
    get('doc-retry-files').textContent='重试前确认';
    get('doc-retry-files').addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();setStep(2);},true);
    // Preview is an existing non-native overlay. Keeping its original node in the
    // top-layer dialog makes it usable without closing or duplicating this form.
    const preview=get('chunk-preview');if(preview)shell.dialog.append(preview);
    get('open-ingest-results').addEventListener('click',()=>shell.close({restoreFocus:false}),true);
    function updateSummary(){
      summary.replaceChildren();
      const mode=get('extraction-mode'),modeLabel=mode?.selectedOptions?.[0]?.textContent||'按所选方式处理';
      const rows=[['文件',get('doc-upload-summary').textContent],['处理方式',modeLabel],['本体依据',mode?.value==='documents'?'仅文档问答，无需本体':root.IngestOntology?.state?.().release?.id||'请核对当前处理方式中的本体提示'],
        ['范围与时间','以第二步填写的本批公共值为准；查询页筛选不会写入资料。']];
      for(const [label,value] of rows){const row=node('p');row.append(node('strong',null,label+'：'),node('span',null,value));summary.append(row);}
      summary.append(node('p','subtle','提交后在“处理批次”查看进度、失败重试和实际写入结果。'));
    }
    function setStep(value){
      const currentState=state.step(value);stages.forEach((e,i)=>e.hidden=i!==currentState.step);
      stepButtons.forEach((e,i)=>e.setAttribute('aria-current',i===currentState.step?'step':'false'));
      previous.hidden=currentState.step===0;next.hidden=currentState.step===2;
      next.textContent=currentState.step===0?'下一步：资料与处理方式':'下一步：确认处理';
      if(currentState.step===2)updateSummary();shell.body.scrollTop=0;
    }
    function open(mode,{trigger}={}){
      try{
        const view=state.open(mode,project());
        mode=view.mode;
        sections.upload.hidden=mode!=='upload';sections.batches.hidden=mode!=='batches';sections.structured.hidden=mode!=='structured';
        for(const [key,b] of Object.entries(tabs))b.setAttribute('aria-pressed',String(key===mode));
        title.textContent={upload:'添加文件',batches:'处理批次',structured:'结构化记录导入'}[mode];
        context.textContent='当前项目：'+(get('project').selectedOptions?.[0]?.textContent||view.project);
        notice.textContent='';host.classList.remove('hidden');shell.open({trigger});if(mode==='upload')setStep(view.step);
        root.IngestBatches?.visibilityChanged();
        if(mode==='upload'){void root.IngestBatches?.loadCapabilities();void root.loadApplicabilityVocabulary?.();void root.IngestOntology?.refresh();}
        if(mode==='batches')root.IngestBatches?.loadRecent();return true;
      }catch(error){if(typeof status==='function')status(error.message,true);return false;}
    }
    const controls=get('tab-sources').querySelector('.panel > .row');
    const batchesButton=button('处理批次',()=>open('batches'));
    controls.append(batchesButton);
    get('upload-source').onclick=()=>open(undefined,{trigger:get('upload-source')});
    if(root.MutationObserver)new root.MutationObserver(()=>{
      if(!shell.dialog.open)return;notice.textContent=get('status').textContent;
      notice.classList.toggle('error',get('status').classList.contains('error'));
    }).observe(get('status'),{childList:true,subtree:true,characterData:true});
    setStep(0);
    return {open,close:options=>shell.close(options),projectChanged(id){shell.close({restoreFocus:false});state.projectChanged(id);get('ingest').disabled=false;},
      ticket:()=>state.ticket(),
      accepts:(ticket,id)=>state.accepts(ticket,id)&&project()===id,
      showBatch({projectId,batchId,ticket}){if(!state.accepts(ticket,projectId)||project()!==projectId)return;open('batches');
        root.IngestBatches?.monitor(projectId,{id:batchId});
        const card=[...batches.querySelectorAll('[data-batch-id]')].find(n=>n.dataset.batchId===batchId);card?.scrollIntoView({block:'start'});},
      snapshot:()=>state.snapshot()};
  }
  return {createState,init};
});
