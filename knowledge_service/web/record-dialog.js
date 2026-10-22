/* Visible record actions, shared by table rows, graph inspector and citations. */
(() => {
  const dialog=document.createElement('dialog');
  dialog.id='record-dialog';dialog.setAttribute('aria-labelledby','record-dialog-title');
  dialog.innerHTML='<div class="record-dialog-heading"><h2 id="record-dialog-title"></h2><button id="close-record-dialog" class="secondary" aria-label="关闭记录窗口">关闭 ×</button></div><p id="record-dialog-help" class="subtle"></p><section id="record-history-view" aria-live="polite"></section>';
  document.body.appendChild(dialog);
  const panel=$('revision').closest('.panel');
  panel.id='record-edit-view';
  const structured=document.createElement('section');structured.id='record-structured-edit';
  dialog.appendChild(structured);
  const advancedEdit=document.createElement('details');advancedEdit.id='record-advanced-edit';
  advancedEdit.innerHTML='<summary>高级：编辑完整记录 JSON</summary>';
  advancedEdit.appendChild(panel);dialog.appendChild(advancedEdit);
  // The raw response remains available, but is no longer the only history UI.
  const raw=document.createElement('details');
  raw.innerHTML='<summary>完整修订历史 JSON</summary>';
  raw.appendChild($('history'));
  dialog.appendChild(raw);
  $('revision').setAttribute('aria-label','记录内容 JSON');
  const message=document.createElement('p');message.id='record-dialog-message';message.setAttribute('role','status');
  dialog.querySelector('.record-dialog-heading').after(message);
  const conflictView=document.createElement('section');conflictView.id='record-conflict-view';conflictView.hidden=true;dialog.appendChild(conflictView);
  let request=0,activeRow=null,activeOntologies=null,entityPickers=[],activeScope=null;
  function captureScope(){const context=window.WorkspaceContext,page=context?.page||'records',p=current,ticket=context?.ticket(p,page),stamp=JSON.stringify(typeof scope==='function'?scope():{});
    return ()=>p===current&&stamp===JSON.stringify(typeof scope==='function'?scope():{})&&(!ticket||context.accepts(ticket,p,page));}
  function assertEditable(){if(!window.Auth?.isAdmin?.())throw Error('只有管理员可以保存知识修订');if(!activeScope?.())throw Error('查询范围已变化，请重新打开编辑器');}
  function close(){request++;entityPickers.forEach(picker=>picker.invalidate());entityPickers=[];dialog.close();}
  $('close-record-dialog').onclick=close;
  dialog.addEventListener('cancel',()=>{request++;});
  function open(mode,title){
    $('record-dialog-title').textContent=title;
    $('record-dialog-message').textContent='';
    conflictView.replaceChildren();conflictView.hidden=true;
    structured.hidden=mode!=='edit';advancedEdit.hidden=mode!=='edit';advancedEdit.open=false;
    $('record-history-view').hidden=mode!=='history';
    raw.hidden=mode!=='history';raw.open=false;
    $('record-dialog-help').textContent=mode==='edit'
      ?'保存新修订：不改动旧修订，系统创建 rN+1。预期修订号用于防止覆盖别人的修改（修订号不一致会拒绝保存）。'
      :'这里按系统记录时间展示历次修订。每个 rN 都绑定当时依据的本体发布；选择历史修订只读查看，不会修改数据。';
    if(!dialog.open)dialog.showModal();
    dialog.scrollTop=0;
  }
  function populateRaw(row){
    const fields=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until','value','datatype'];
    $('revision').value=JSON.stringify(Object.fromEntries(fields.filter(key=>row[key]!==undefined).map(key=>[key,row[key]])),null,2);
    $('revision-id').value=row.id;$('revision-version').value=row.version;$('keep-id').value=row.id;
  }
  const option=(value,label,selected)=>`<option value="${esc(value)}"${value===selected?' selected':''}>${esc(label)}</option>`;
  function editContext(){
    $('record-dialog-help').textContent=RecordVersionContext.summary(activeRow,activeOntologies)
      +'。普通编辑保留本体归属，保存会生成下一次修订；使用新本体类型请先执行受控迁移。';
  }
  async function refreshViews(){
    const results=await Promise.allSettled([
      $('load-records')?.onclick?.(),window.drawGraph?window.drawGraph():$('draw-graph')?.onclick?.(),
    ]);
    return results.some(result=>result.status==='rejected'||result.value===false);
  }
  function syncInspector(saved){
    const inspector=$('graph-detail'),node=wb.nodes.get(saved.id);
    if(inspector?.dataset.recordId!==saved.id||!inspector.querySelector('.evidence-identity'))return;
    // Preserve bound hierarchy only when the type and immutable release agree.
    const sameModel=node?.type===saved.type&&node?.ontology_id===saved.ontology_id;
    const displayed=sameModel?{...node,...saved}:{...saved,class_ontology_id:null};
    return window.renderEvidenceInspector?.(displayed,[...wb.nodes.values()]);
  }
  async function saveRevision(record,expectedVersion){
    assertEditable();
    const serial=request,p=current;
    const saved=await api(endpoint('/records/'+encodeURIComponent(record.id)),{record,expected_version:expectedVersion},'PUT');
    if(serial!==request||p!==current||!dialog.open||!activeScope?.())return;
    activeRow=saved;populateRaw(saved);wb.records.set(saved.id,saved);editContext();
    // Update the open card before announcing success; closing the modal cannot
    // cancel an already-committed revision's immediate presentation.
    syncInspector(saved);
    const summary=RecordVersionContext.summary(saved,activeOntologies);
    const message=`已保存${summary}；旧修订保留，本体结构未修改。`;
    $('record-dialog-message').classList.remove('error');$('record-dialog-message').textContent=message;status(message);
    const refreshFailed=await refreshViews();
    if(serial!==request||p!==current||!dialog.open||activeRow?.version_id!==saved.version_id)return;
    $('record-dialog-message').textContent=message+(refreshFailed?' 部分视图刷新失败，请刷新图谱或台账。':'');
    await syncInspector(saved);
    if(serial===request&&p===current&&dialog.open&&activeRow?.version_id===saved.version_id)return saved;
  }
  async function compareConflict(local,serial,p){
    try{
      const latest=await api('/api/projects/'+encodeURIComponent(p)+'/records/'+encodeURIComponent(local.id),undefined,'GET');
      if(serial!==request||p!==current||!dialog.open||!latest)return;
      conflictView.replaceChildren();conflictView.hidden=false;
      const heading=document.createElement('h3');heading.textContent=`服务端最新修订 r${latest.version} · 请比较后重新编辑`;
      const explanation=document.createElement('p');explanation.textContent='你的未保存字段保留在上方。载入最新修订会替换表单，需重新核对后保存。';
      const before=document.createElement('pre');before.textContent='你的提交：\n'+JSON.stringify(local,null,2);
      const after=document.createElement('pre');after.textContent='服务端最新内容：\n'+JSON.stringify(latest,null,2);
      const reload=document.createElement('button');reload.type='button';reload.className='secondary admin-only';reload.textContent='载入最新修订重新编辑';reload.onclick=()=>editRecord(latest);
      conflictView.append(heading,explanation,before,after,reload);
    }catch{/* The original conflict remains visible if the comparison read fails. */}
  }
  async function renderStructured(row){
    const serial=request,p=current;structured.innerHTML='<p class="subtle">正在加载可选类型和实体…</p>';
    try{
      const versions=await api(endpoint('/ontologies'),undefined,'GET');
      if(serial!==request||p!==current||activeRow?.id!==row.id||activeRow?.version_id!==row.version_id)return;
      activeOntologies=RecordVersionContext.build(versions.versions||[]);editContext();
      if(['entity','relation','attribute'].includes(row.kind)&&!activeOntologies.byId.has(row.ontology_id)){
        structured.innerHTML='<p class="error">未知本体归属，无法提供可靠的类型选项。请先通过受控治理明确本体归属。</p>';return;
      }
      if(!['entity','relation','attribute'].includes(row.kind)){
        structured.innerHTML='<p class="subtle">文档与原文片段可在下方高级 JSON 中查看。文档内容请修改源文件后重新解析。</p>';return;
      }
      const entityScope=typeof scope==='function'?scope():{},scopeStamp=JSON.stringify(entityScope);
      const context=window.WorkspaceContext,contextPage=context?.page||'records',scopeTicket=context?.ticket(p,contextPage);
      const stableScope=()=>scopeStamp===JSON.stringify(typeof scope==='function'?scope():{})&&(!scopeTicket||context.accepts(scopeTicket,p,contextPage));
      const ontology=await api(endpoint('/ontology')+'?ontology_id='+encodeURIComponent(row.ontology_id),undefined,'GET');
      if(serial!==request||p!==current||activeRow?.id!==row.id||activeRow?.version_id!==row.version_id)return;
      if(!stableScope())throw Error('查询范围已变化，请重新打开编辑器');
      if(ontology.id!==row.ontology_id)throw Error('读取的本体与记录归属不一致');
      const collection=row.kind==='entity'?ontology.summary.classes:row.kind==='attribute'?ontology.summary.attributes:ontology.summary.relations;
      if(!Array.isArray(collection))throw Error('绑定本体缺少对应类型目录');
      const labels=new Map(collection.map(item=>[item.id,item.label_zh||item.label||item.name]));
      if(row.type&&!labels.has(row.type))labels.set(row.type,labelOf(row.type));
      const typeOptions=[...labels].map(([id,label])=>option(id,label,row.type)).join('');
      if(row.kind==='entity'){
        structured.innerHTML=`<div class="record-structured-heading"><h3>维护具体实体</h3><p>这里修改的是图谱中的一个实体，不会修改本体类定义。</p></div><label>实体名称<input id="structured-record-text" value="${esc(row.text)}"></label><label>实体类型<select id="structured-record-type">${typeOptions}</select></label><div class="record-time-fields"><label>业务生效时间<input id="structured-valid-from" value="${esc(row.valid_from||'')}" placeholder="可留空"></label><label>业务失效时间<input id="structured-valid-until" value="${esc(row.valid_until||'')}" placeholder="可留空"></label></div><button id="save-structured-record">保存实体修改为新修订</button>`;
      }else if(row.kind==='relation'){
        structured.innerHTML=`<div class="record-structured-heading"><h3>维护实体关系</h3><p>可修改关系类型、起点实体或终点实体；这不会修改本体中的关系类型定义。</p></div><label>关系显示文本<input id="structured-record-text" value="${esc(row.text)}"></label><label>关系类型<select id="structured-record-type">${typeOptions}</select></label><div class="record-endpoints"><input type="hidden" id="structured-subject" value="${esc(row.subject_id||'')}"><div id="structured-subject-picker"></div><span>→</span><input type="hidden" id="structured-object" value="${esc(row.object_id||'')}"><div id="structured-object-picker"></div></div><div class="record-time-fields"><label>业务生效时间<input id="structured-valid-from" value="${esc(row.valid_from||'')}" placeholder="可留空"></label><label>业务失效时间<input id="structured-valid-until" value="${esc(row.valid_until||'')}" placeholder="可留空"></label></div><button id="save-structured-record">保存关系修改为新修订</button>`;
      }else if(row.kind==='attribute'){
        if(!window.RecordAttributeEditor)throw Error('属性编辑器资源未加载，请刷新页面');
        const xsd='http://www.w3.org/2001/XMLSchema#';
        const modes=[['string','字符串'],['boolean','布尔值（true / false）'],['integer','整数'],['double','数值（自动识别整数）'],['date','日期（校验后保存为字符串）'],['dateTime','日期时间（校验后保存为字符串）'],['json','JSON（校验后保存为字符串）']];
        const mode=row.datatype?.startsWith(xsd)?row.datatype.slice(xsd.length):'';
        const knownMode=modes.some(([value])=>value===mode);
        if(!knownMode)modes.unshift([row.datatype||'unknown','原数据类型（请先选择受支持类型）']);
        structured.innerHTML=`<div class="record-structured-heading"><h3>维护实体属性</h3><p>修改具体实体的属性值；类型取自该记录绑定的本体发布。</p></div><label>属性显示文本<input id="structured-record-text" value="${esc(row.text)}"></label><label>属性类型<select id="structured-record-type">${typeOptions}</select></label><input type="hidden" id="structured-subject" value="${esc(row.subject_id||'')}"><div id="structured-subject-picker"></div><label>数据类型<select id="structured-datatype">${modes.map(([id,label])=>option(id,label,knownMode?mode:row.datatype||'unknown')).join('')}</select></label><p class="subtle">实体可按名称或 ID 在服务端查询并翻页；原所属实体会保留。日期与 JSON 按字符串保存，数值按原生整数或浮点数保存。</p><label>属性值<input id="structured-value" value="${esc(String(row.value??''))}"></label><div class="record-time-fields"><label>业务生效时间<input id="structured-valid-from" value="${esc(row.valid_from||'')}" placeholder="YYYY-MM-DD 或带时区 ISO 时间"></label><label>业务失效时间<input id="structured-valid-until" value="${esc(row.valid_until||'')}" placeholder="可留空"></label></div><button id="save-structured-record">保存属性修改为新修订</button>`;
      }else{
        const kindLabel=row.kind==='document'?'文档收据':'原文片段';
        const reason=row.kind==='document'
          ?'文档是知识抽取的原始来源，本身不参与图谱编辑。'
          :'原文片段随文档解析自动生成，不支持直接编辑。';
        const advice=row.kind==='document'
          ?'如需修正内容，请通过「知识写入」重新上传并解析；不要在这里手改收据。'
          :'如需调整，请修改源文档后重新解析，或用下方高级 JSON 保存修正版本。';
        structured.innerHTML=`<div class="record-structured-heading"><h3>${kindLabel}</h3><p>${reason}</p><p>${advice}</p><p class="subtle">下方「高级：编辑完整记录 JSON」保存后会创建 rN+1 新修订，旧修订保留可恢复。</p></div>`;
        return;
      }
      entityPickers.forEach(picker=>picker.invalidate());entityPickers=[];
      if(row.kind!=='entity'){
        if(!window.RecordCreate)throw Error('实体分页选择器未加载，请刷新页面');
        const session=RecordCreate.createSession({project:p,scope:entityScope,request:(...args)=>api(...args),canWrite:()=>!!window.Auth?.isAdmin?.(),
          accepts:()=>serial===request&&p===current&&dialog.open&&stableScope()});
        for(const [key,title] of [['subject',row.kind==='attribute'?'所属实体':'起点实体'],...(row.kind==='relation'?[['object','终点实体']]:[])]){
          const id=row[key+'_id'],cached=wb.records.get(id)||wb.nodes.get(id);
          const picker=RecordCreate.entityPicker($('structured-'+key+'-picker'),{session,title,selected:id?{id,text:cached?.text||'原实体（保留现有端点）'}:null,
            onChange:selected=>{$('structured-'+key).value=selected?.id||'';}});entityPickers.push(picker);
        }
      }
      $('save-structured-record').classList.add('admin-only');
      $('save-structured-record').onclick=async()=>{
        const button=$('save-structured-record');if(button.disabled)return;
        if(serial!==request||p!==current||!dialog.open)return;
        button.disabled=true;let submittedRecord=null;
        try{
          if(!window.Auth?.isAdmin?.())throw Error('只有管理员可以保存知识修订');
          if(!stableScope())throw Error('查询范围已变化，请重新打开编辑器');
          if(activeRow.kind==='attribute'){
            const record=RecordAttributeEditor.buildRecord(activeRow,{
              text:$('structured-record-text').value,type:$('structured-record-type').value,
              subject_id:$('structured-subject').value,mode:$('structured-datatype').value,value:$('structured-value').value,
              valid_from:$('structured-valid-from').value,valid_until:$('structured-valid-until').value,
            });
            submittedRecord=record;await saveRevision(record,activeRow.version);return;
          }
          const fields=['id','kind','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];
          const record=Object.fromEntries(fields.filter(key=>activeRow[key]!==undefined).map(key=>[key,activeRow[key]]));
          record.text=$('structured-record-text').value.trim();record.type=$('structured-record-type').value;
          record.valid_from=$('structured-valid-from').value.trim()||null;record.valid_until=$('structured-valid-until').value.trim()||null;
          if(activeRow.kind==='relation'){record.subject_id=$('structured-subject').value;record.object_id=$('structured-object').value;}
          submittedRecord=record;await saveRevision(record,activeRow.version);
        }catch(error){if(serial===request&&p===current){$('record-dialog-message').textContent=error.message;$('record-dialog-message').classList.add('error');
          if(submittedRecord&&/版本冲突|version conflict/i.test(error.message))await compareConflict(submittedRecord,serial,p);
        }}
        finally{button.disabled=false;}
      };
    }catch(error){if(serial===request&&p===current&&activeRow?.id===row.id&&activeRow?.version_id===row.version_id)structured.innerHTML=`<p class="error">结构化编辑加载失败：${esc(error.message)}。仍可使用下方高级 JSON 编辑器。</p>`;}
  }
  editRecord=row=>{
    request++;
    entityPickers.forEach(picker=>picker.invalidate());entityPickers=[];
    if(!window.Auth?.isAdmin?.()){status('只有管理员可以编辑知识记录',true);return;}
    if(!row){status('记录已刷新，请重新查询后选择',true);return;}
    activeRow=row;activeOntologies=null;activeScope=captureScope();populateRaw(row);
    open('edit','编辑记录 · '+(row.text||row.id).slice(0,70));
    $('delete-confirm').checked=false;
    renderStructured(row);
  };
  const revisionKind={document:'文档',entity:'实体',relation:'关系',attribute:'属性',chunk:'原文片段'};
  const shortRevisionId=id=>{const text=String(id||'');return !text?'未知':(text.length>18?`${text.slice(0,9)}…${text.slice(-4)}`:text);};
  function ontologyIndex(versions){
    return RecordVersionContext.build(versions||[]);
  }
  function revisionOntology(row,ontologies){
    return RecordVersionContext.ontology(row,ontologies);
  }
  function appendFact(list,label,value){
    const term=document.createElement('dt'),description=document.createElement('dd');
    term.textContent=label;description.textContent=value;list.append(term,description);
  }
  function renderRevisionDetail(host,row,isCurrent,ontologies){
    host.replaceChildren();host.id='record-revision-detail';
    const article=document.createElement('article');article.className='record-revision-detail';
    const heading=document.createElement('div');heading.className='record-revision-detail-head';
    heading.append(nodeText('h3',`修订 r${row.version}${isCurrent?' · 当前':' · 已取代'}`),
      nodeText('span','只读历史快照'));
    const facts=document.createElement('dl');
    appendFact(facts,'不可变修订 ID',shortRevisionId(row.version_id));
    appendFact(facts,'知识类别',revisionKind[row.kind]||row.kind||'未知');
    appendFact(facts,'删除状态',row.metadata?._deleted?'已删除':'有效');
    appendFact(facts,'本体归属',revisionOntology(row,ontologies));
    appendFact(facts,'记录时间',row.recorded_at||'未知');
    appendFact(facts,'被取代时间',row.superseded_at||'尚未被取代');
    appendFact(facts,'业务有效期',`${row.valid_from||'未知'} → ${row.valid_until||'未知'}`);
    const content=nodeText('p',row.text||'（无文本内容）');content.className='version-content';
    const rawDetail=document.createElement('details'),summary=nodeText('summary','查看这次修订的 metadata 与属性');
    const pre=nodeText('pre',JSON.stringify({metadata:row.metadata||{},properties:row.properties||{}},null,2));
    rawDetail.append(summary,pre);article.append(heading,facts,content,rawDetail);host.append(article);
  }
  function nodeText(tag,text){const element=document.createElement(tag);element.textContent=text;return element;}
  function renderRevisionTimeline(record,result,ontologyResult){
    const host=$('record-history-view'),versions=[...(result.versions||[])]
      .sort((a,b)=>Number(b.version||0)-Number(a.version||0));
    const ontologies=ontologyResult?ontologyIndex(ontologyResult.versions||[]):null;
    host.replaceChildren();
    const summary=nodeText('p',`共 ${versions.length} 次修订${versions.length===1?'，尚未再次修订。':'，当前修订在前。'}`);
    summary.className='record-revision-summary';
    if(!versions.length){host.append(summary,nodeText('p','没有可显示的修订。'));return;}
    const layout=document.createElement('section');layout.className='record-revision-layout';
    const timeline=document.createElement('nav');timeline.className='record-revision-timeline';timeline.setAttribute('aria-label','知识修订时间线');
    const detail=document.createElement('section');detail.id='record-revision-detail';
    versions.forEach((revision,index)=>{
      const currentRevision=index===0,deleted=Boolean(revision.metadata?._deleted);
      const button=document.createElement('button');button.type='button';button.className='record-revision-item'+(currentRevision?' is-selected':'');
      button.dataset.revision=String(revision.version);button.setAttribute('aria-pressed',String(currentRevision));
      button.append(nodeText('b',`修订 r${revision.version}${currentRevision?' · 当前':' · 已取代'}`),
        nodeText('small',`修订 ID ${shortRevisionId(revision.version_id)} · ${revision.recorded_at||'时间未知'}`),
        nodeText('small',`${revisionKind[revision.kind]||revision.kind||'未知类别'} · ${revisionOntology(revision,ontologies)}`));
      if(deleted){const badge=nodeText('span','已删除');badge.className='record-revision-deleted';button.append(badge);}
      button.onclick=()=>{
        timeline.querySelectorAll('.record-revision-item').forEach(item=>{item.classList.remove('is-selected');item.setAttribute('aria-pressed','false');});
        button.classList.add('is-selected');button.setAttribute('aria-pressed','true');
        renderRevisionDetail(detail,revision,currentRevision,ontologies);
      };
      timeline.append(button);
    });
    layout.append(timeline,detail);host.append(summary,layout);
    renderRevisionDetail(detail,versions[0],true,ontologies);
    $('history').textContent=JSON.stringify(result,null,2);
  }
  function renderRevisionFailure(projectId,record,error){
    const host=$('record-history-view'),layout=document.createElement('section');layout.className='record-revision-failure';
    const message=nodeText('div','历史修订读取失败：'+error.message);message.className='error';
    const retry=nodeText('button','重试读取修订时间线');retry.type='button';retry.dataset.retryRevisionTimeline='';
    const detail=document.createElement('section');detail.id='record-revision-detail';
    renderRevisionDetail(detail,record,true,null);
    retry.onclick=()=>{const serial=++request;loadRevisionTimeline(projectId,record,serial);};
    layout.append(message,retry,detail);host.replaceChildren(layout);
  }
  async function loadRevisionTimeline(projectId,record,serial){
    const host=$('record-history-view');host.textContent='正在读取修订时间线…';$('history').textContent='';
    try{
      const [result,ontologyResult]=await Promise.all([
        api(endpoint('/records/'+encodeURIComponent(record.id)+'/history'),undefined,'GET'),
        api(endpoint('/ontologies'),undefined,'GET').catch(()=>null),
      ]);
      if(serial!==request||projectId!==current||!dialog.open)return;
      renderRevisionTimeline(record,result,ontologyResult);
    }catch(error){if(serial===request&&projectId===current&&dialog.open)renderRevisionFailure(projectId,record,error);}
  }
  historyFor=row=>{
    const serial=++request,p=current;
    open('history','修订历史 · '+(row.text||row.id).slice(0,70));
    return loadRevisionTimeline(p,row,serial);
  };
  // Stable read-only entrypoint shared with frozen answer evidence.
  window.openRecordHistory=record=>{
    const id=typeof record==='string'?record:record?.id;
    if(typeof id!=='string'||!id)return;
    const text=typeof record?.text==='string'?record.text:id;
    return historyFor({id,text});
  };
  // Programmatic project changes also close the modal, not just DOM change events.
  const change=$('project').onchange;
  $('project').onchange=()=>{if(dialog.open)close();change();};
  $('revise').onclick=async()=>{
    const serial=request,p=current,button=$('revise');button.disabled=true;let submittedRecord=null;
    try{
      assertEditable();
      const record=JSON.parse($('revision').value);
      if(record.id&&record.id!==$('revision-id').value)throw Error('路径与记录 ID 不一致');
      record.id=$('revision-id').value;
      submittedRecord=record;const saved=await saveRevision(record,Number($('revision-version').value));
      if(saved&&serial===request&&p===current)await renderStructured(saved);
    }catch(error){if(serial===request&&p===current){$('record-dialog-message').textContent=error.message;$('record-dialog-message').classList.add('error');if(submittedRecord&&/版本冲突|version conflict/i.test(error.message))await compareConflict(submittedRecord,serial,p);}}
    finally{button.disabled=false;}
  };
  for(const id of ['restore-version','soft-delete']){
    const run=$(id).onclick;
    $(id).onclick=async()=>{
      const serial=request,p=current;
      $('record-dialog-message').textContent='处理中…';
      try{assertEditable();await run();if(serial!==request||p!==current||!activeScope?.())return;
        $('record-dialog-message').textContent=$('status').textContent;$('record-dialog-message').classList.toggle('error',$('status').classList.contains('error'));
        if(!$('status').classList.contains('error'))await refreshViews();
      }catch(e){if(serial===request&&p===current){$('record-dialog-message').textContent=e.message;$('record-dialog-message').classList.add('error');}}
    };
  }
})();
