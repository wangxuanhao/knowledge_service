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
  raw.innerHTML='<summary>完整历史 JSON</summary>';
  raw.appendChild($('history'));
  dialog.appendChild(raw);
  $('revision').setAttribute('aria-label','记录内容 JSON');
  const message=document.createElement('p');message.id='record-dialog-message';message.setAttribute('role','status');
  dialog.querySelector('.record-dialog-heading').after(message);
  let request=0,activeRow=null;
  function close(){request++;dialog.close();}
  $('close-record-dialog').onclick=close;
  dialog.addEventListener('cancel',()=>{request++;});
  function open(mode,title){
    $('record-dialog-title').textContent=title;
    $('record-dialog-message').textContent='';
    structured.hidden=mode!=='edit';advancedEdit.hidden=mode!=='edit';advancedEdit.open=false;
    $('record-history-view').hidden=mode!=='history';
    raw.hidden=mode!=='history';raw.open=false;
    $('record-dialog-help').textContent=mode==='edit'
      ?'保存新版本：不改动原记录，系统创建版本+1 的新版本，旧版本保留可随时恢复。预期版本用于防止覆盖别人的修改（版本号不一致会拒绝保存）。'
      :'这里按系统记录时间展示历次版本。有效期是知识在业务上何时成立，与录入时间不同。查看历史不会修改数据。';
    if(!dialog.open)dialog.showModal();
    dialog.scrollTop=0;
  }
  function populateRaw(row){
    const fields=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];
    $('revision').value=JSON.stringify(Object.fromEntries(fields.filter(key=>row[key]!==undefined).map(key=>[key,row[key]])),null,2);
    $('revision-id').value=row.id;$('revision-version').value=row.version;$('keep-id').value=row.id;
  }
  const option=(value,label,selected)=>`<option value="${esc(value)}"${value===selected?' selected':''}>${esc(label)}</option>`;
  async function renderStructured(row){
    const serial=request,p=current;structured.innerHTML='<p class="subtle">正在加载可选类型和实体…</p>';
    try{
      const [ontology,records]=await Promise.all([
        api(endpoint('/ontology'),undefined,'GET'),
        api(endpoint('/records/query?limit=1000'),{kinds:['entity']})
      ]);
      if(serial!==request||p!==current||activeRow?.id!==row.id)return;
      const collection=row.kind==='entity'?ontology.summary.classes:ontology.summary.relations;
      const labels=new Map(collection.map(item=>[item.id,item.label_zh||item.label||item.name]));
      if(row.type&&!labels.has(row.type))labels.set(row.type,labelOf(row.type));
      const entities=records.records||[];const entityLabels=new Map(entities.map(item=>[item.id,item.text]));
      for(const id of [row.subject_id,row.object_id])if(id&&!entityLabels.has(id))entityLabels.set(id,id);
      const typeOptions=[...labels].map(([id,label])=>option(id,label,row.type)).join('');
      if(row.kind==='entity'){
        structured.innerHTML=`<div class="record-structured-heading"><h3>维护具体实体</h3><p>这里修改的是图谱中的一个实体，不会修改本体类定义。</p></div><label>实体名称<input id="structured-record-text" value="${esc(row.text)}"></label><label>实体类型<select id="structured-record-type">${typeOptions}</select></label><div class="record-time-fields"><label>业务生效时间<input id="structured-valid-from" value="${esc(row.valid_from||'')}" placeholder="可留空"></label><label>业务失效时间<input id="structured-valid-until" value="${esc(row.valid_until||'')}" placeholder="可留空"></label></div><button id="save-structured-record">保存实体修改为新版本</button>`;
      }else if(row.kind==='relation'){
        const entityOptions=selected=>[...entityLabels].map(([id,label])=>option(id,label,selected)).join('');
        structured.innerHTML=`<div class="record-structured-heading"><h3>维护实体关系</h3><p>可修改关系类型、起点实体或终点实体；这不会修改本体中的关系类型定义。</p></div><label>关系显示文本<input id="structured-record-text" value="${esc(row.text)}"></label><label>关系类型<select id="structured-record-type">${typeOptions}</select></label><div class="record-endpoints"><label>起点实体<select id="structured-subject">${entityOptions(row.subject_id)}</select></label><span>→</span><label>终点实体<select id="structured-object">${entityOptions(row.object_id)}</select></label></div><div class="record-time-fields"><label>业务生效时间<input id="structured-valid-from" value="${esc(row.valid_from||'')}" placeholder="可留空"></label><label>业务失效时间<input id="structured-valid-until" value="${esc(row.valid_until||'')}" placeholder="可留空"></label></div><button id="save-structured-record">保存关系修改为新版本</button>`;
      }else{
        const kindLabel=row.kind==='document'?'文档收据':'原文片段';
        const reason=row.kind==='document'
          ?'文档是知识抽取的原始来源，本身不参与图谱编辑。'
          :'原文片段随文档解析自动生成，不支持直接编辑。';
        const advice=row.kind==='document'
          ?'如需修正内容，请通过「知识写入」重新上传并解析；不要在这里手改收据。'
          :'如需调整，请修改源文档后重新解析，或用下方高级 JSON 保存修正版本。';
        structured.innerHTML=`<div class="record-structured-heading"><h3>${kindLabel}</h3><p>${reason}</p><p>${advice}</p><p class="subtle">下方「高级：编辑完整记录 JSON」保存后会创建版本+1 的新版本，旧版本保留可恢复。</p></div>`;
        return;
      }
      $('save-structured-record').onclick=async()=>{
        const button=$('save-structured-record');button.disabled=true;
        try{
          const fields=['id','kind','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];
          const record=Object.fromEntries(fields.filter(key=>activeRow[key]!==undefined).map(key=>[key,activeRow[key]]));
          record.text=$('structured-record-text').value.trim();record.type=$('structured-record-type').value;
          record.valid_from=$('structured-valid-from').value.trim()||null;record.valid_until=$('structured-valid-until').value.trim()||null;
          if(activeRow.kind==='relation'){record.subject_id=$('structured-subject').value;record.object_id=$('structured-object').value;}
          const saved=await api(endpoint('/records/'+encodeURIComponent(activeRow.id)),{record,expected_version:activeRow.version},'PUT');
          activeRow=saved;populateRaw(saved);wb.records.set(saved.id,saved);$('record-dialog-message').classList.remove('error');$('record-dialog-message').textContent=`已保存版本 ${saved.version}；旧版本仍可恢复。`;status(`已保存 ${saved.kind==='relation'?'关系':'实体'}的新版本 ${saved.version}。`);
          $('draw-graph').click();button.disabled=false;
        }catch(error){$('record-dialog-message').textContent=error.message;$('record-dialog-message').classList.add('error');button.disabled=false;}
      };
    }catch(error){if(serial===request)structured.innerHTML=`<p class="error">结构化编辑加载失败：${esc(error.message)}。仍可使用下方高级 JSON 编辑器。</p>`;}
  }
  editRecord=row=>{
    request++;
    if(!row){status('记录已刷新，请重新查询后选择',true);return;}
    activeRow=row;populateRaw(row);
    open('edit','编辑记录 · '+(row.text||row.id).slice(0,70));
    $('delete-confirm').checked=false;
    renderStructured(row);
  };
  historyFor=async row=>{
    const serial=++request,p=current;
    open('history','版本历史 · '+(row.text||row.id).slice(0,70));
    $('record-history-view').textContent='正在读取历史版本…';$('history').textContent='';
    try{
      const result=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/history'),undefined,'GET');
      if(serial!==request||p!==current||!dialog.open)return;
      $('history').textContent=JSON.stringify(result,null,2);
      const versions=[...result.versions].reverse();
      $('record-history-view').innerHTML=`<p>共 ${versions.length} 个版本${versions.length===1?'，尚未修订过。':'，最新版本在前。'}</p>`+versions.map(v=>`<article class="record-version"><h3>版本 ${v.version}${v.superseded_at?'':' · 最新系统版本'}</h3><dl><dt>记录时间</dt><dd>${esc(v.recorded_at)}</dd><dt>被替代时间</dt><dd>${esc(v.superseded_at||'尚未被替代')}</dd><dt>业务有效期</dt><dd>${esc(v.valid_from||'未知')} → ${esc(v.valid_until||'未知')}</dd></dl><p class="version-content">${esc(v.text)}</p><details><summary>此版本的 metadata 与属性</summary><pre>${esc(JSON.stringify({metadata:v.metadata,properties:v.properties},null,2))}</pre></details></article>`).join('');
    }catch(e){if(serial===request&&dialog.open){$('record-history-view').textContent='读取失败：'+e.message;}}
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
  for(const id of ['revise','restore-version','soft-delete']){
    const run=$(id).onclick;
    $(id).onclick=async()=>{
      $('record-dialog-message').textContent='处理中…';
      try{await run();$('record-dialog-message').textContent=$('status').textContent;$('record-dialog-message').classList.toggle('error',$('status').classList.contains('error'));
        if(id==='revise'&&!$('status').classList.contains('error')){
          const value=JSON.parse($('revision').value),row=wb.records.get($('revision-id').value);
          if(row)wb.records.set(row.id,{...row,...value,version:Number($('revision-version').value)});
          await $('load-records').onclick();
        }
      }catch(e){$('record-dialog-message').textContent=e.message;$('record-dialog-message').classList.add('error');}
    };
  }
})();
