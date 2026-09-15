/* Visible record actions, shared by table rows, graph inspector and citations. */
(() => {
  const dialog=document.createElement('dialog');
  dialog.id='record-dialog';dialog.setAttribute('aria-labelledby','record-dialog-title');
  dialog.innerHTML='<div class="record-dialog-heading"><h2 id="record-dialog-title"></h2><button id="close-record-dialog" class="secondary" aria-label="关闭记录窗口">关闭 ×</button></div><p id="record-dialog-help" class="subtle"></p><section id="record-history-view" aria-live="polite"></section>';
  document.body.appendChild(dialog);
  const panel=$('revision').closest('.panel');
  panel.id='record-edit-view';
  dialog.appendChild(panel);
  // The raw response remains available, but is no longer the only history UI.
  const raw=document.createElement('details');
  raw.innerHTML='<summary>完整历史 JSON</summary>';
  raw.appendChild($('history'));
  dialog.appendChild(raw);
  $('revision').setAttribute('aria-label','记录内容 JSON');
  const message=document.createElement('p');message.id='record-dialog-message';message.setAttribute('role','status');
  dialog.querySelector('.record-dialog-heading').after(message);
  let request=0;
  function close(){request++;dialog.close();}
  $('close-record-dialog').onclick=close;
  dialog.addEventListener('cancel',()=>{request++;});
  function open(mode,title){
    $('record-dialog-title').textContent=title;
    $('record-dialog-message').textContent='';
    panel.hidden=mode!=='edit';
    $('record-history-view').hidden=mode!=='history';
    raw.hidden=mode!=='history';raw.open=false;
    $('record-dialog-help').textContent=mode==='edit'
      ?'修改内容后点击“保存新版本”才会写入。旧版本保留；预期版本用于防止覆盖别人的修改。'
      :'这里按系统记录时间展示历次版本。有效期是知识在业务上何时成立，与录入时间不同。查看历史不会修改数据。';
    if(!dialog.open)dialog.showModal();
    dialog.scrollTop=0;
  }
  const populate=editRecord;
  editRecord=row=>{
    request++;
    if(!row){status('记录已刷新，请重新查询后选择',true);return;}
    populate(row);
    open('edit','编辑记录 · '+(row.text||row.id).slice(0,70));
    $('delete-confirm').checked=false;
    $('revision').focus({preventScroll:true});
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
