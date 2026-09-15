/* Evidence first, internals on demand. Raw document text is always escaped. */
(() => {
  const host=$('graph-detail');let serial=0;
  const sourceDialog=document.createElement('dialog');sourceDialog.id='source-evidence-dialog';
  sourceDialog.setAttribute('aria-labelledby','source-evidence-title');
  sourceDialog.innerHTML='<header><h2 id="source-evidence-title"></h2><button id="close-source-evidence" class="secondary">关闭 ×</button></header><p id="source-evidence-note"></p><pre id="source-evidence-text"></pre>';
  document.body.appendChild(sourceDialog);
  $('close-source-evidence').onclick=()=>sourceDialog.close();
  function openSource(doc){
    $('source-evidence-title').textContent=doc.title;
    $('source-evidence-note').textContent=`来源版本 ${doc.version} · ${doc.reason}${doc.source_content==='segments_only'?' · 旧数据只保存了片段，并非完整原文':''}`;
    $('source-evidence-text').innerHTML=esc(doc.before)+(doc.highlight?'<mark id="source-evidence-anchor">'+esc(doc.highlight)+'</mark>':'')+esc(doc.after);
    sourceDialog.showModal();
    requestAnimationFrame(()=>{const anchor=$('source-evidence-anchor');if(anchor)anchor.scrollIntoView({block:'center'});else $('source-evidence-text').scrollTop=0;});
  }
  function localTime(value){if(!value)return '未记录';const d=new Date(value);return Number.isNaN(d.valueOf())?value:d.toLocaleString('zh-CN',{hour12:false});}
  const typeHint=type=>{const primary=labelOf(type),local=term(type);return primary===local?esc(primary):`${esc(primary)} <span class="subtle">${esc(local)}</span>`;};
  window.renderEvidenceInspector=async(row,options)=>{
    const ticket=++serial,p=current,stamp=JSON.stringify(scope());
    const name=id=>options.find(n=>n.id===id)?.text||id;
    const aliases=Array.isArray(row.metadata?.aliases)?row.metadata.aliases:[];
    host.innerHTML=`<section class="evidence-identity"><span class="evidence-kind">${esc({entity:'实体',relation:'关系',chunk:'原文片段',document:'文档'}[row.kind]||row.kind)} · ${typeHint(row.type)}</span><h3>${esc(row.text)}</h3>${row.kind==='relation'?`<div class="relation-path"><b>${esc(name(row.subject_id))}</b><span>↓ ${typeHint(row.type)}</span><b>${esc(name(row.object_id))}</b></div>`:''}${aliases.length?'<p class="evidence-aliases">别名：'+aliases.map(esc).join('、')+'</p>':''}<div class="evidence-actions"><button data-action="edit" class="secondary">编辑</button><button data-action="history" class="secondary">版本历史 · v${row.version}</button>${row.kind==='entity'?'<button data-action="mindmap" class="secondary">展开脑图</button>':''}</div></section><section class="evidence-sources"><h4>原文证据</h4><div id="inspector-source-list" aria-live="polite">正在查找来源…</div></section><section class="evidence-time"><h4>时间</h4><dl><dt>业务生效</dt><dd>${esc(row.valid_from?localTime(row.valid_from):'未知')}</dd><dt>业务失效</dt><dd>${esc(row.valid_until?localTime(row.valid_until):'未设定')}</dd><dt>系统记录</dt><dd>${esc(localTime(row.recorded_at))}</dd></dl></section><details class="evidence-technical"><summary>Metadata / 属性 / 技术标识</summary><pre>${esc(JSON.stringify({id:row.id,type:row.type,source_id:row.source_id,ontology_id:row.ontology_id,metadata:row.metadata,properties:row.properties},null,2))}</pre></details>`;
    host.querySelector('[data-action="edit"]').onclick=()=>editRecord(row);
    host.querySelector('[data-action="history"]').onclick=()=>historyFor(row);
    const mind=host.querySelector('[data-action="mindmap"]');if(mind)mind.onclick=()=>{$('mindmap-root').value=row.id;showTab('mindmap');$('draw-mindmap').click();};
    try{
      const result=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/evidence'),scope());
      if(ticket!==serial||current!==p||stamp!==JSON.stringify(scope())||!$('inspector-source-list'))return;
      $('inspector-source-list').innerHTML=result.documents.length?result.documents.map((doc,i)=>`<article class="source-evidence-card"><strong>${esc(doc.title)}</strong><small>来源 v${doc.version} · ${esc({offset:'记录偏移定位',passage:'证据文本匹配',text:'名称 / 内容匹配',unlocated:'尚无精确位置'}[doc.mode])}</small><blockquote>${esc(doc.preview||'原文内容未保存')}</blockquote><button data-source-evidence="${i}">${doc.highlight?'查看原文并定位 ↗':'查看来源文档 ↗'}</button></article>`).join(''):`<p class="evidence-empty">${esc(result.message)}</p>`;
      host.querySelectorAll('[data-source-evidence]').forEach(b=>b.onclick=()=>openSource(result.documents[Number(b.dataset.sourceEvidence)]));
    }catch(e){if(ticket===serial&&current===p&&$('inspector-source-list'))$('inspector-source-list').textContent='来源读取失败：'+e.message;}
  };
  const change=$('project').onchange;
  $('project').onchange=()=>{serial++;if(sourceDialog.open)sourceDialog.close();change();};
})();
