/* Evidence first, internals on demand. Raw document text is always escaped. */
(() => {
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,char=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  function attributeDetailsHtml(groups,escape=escapeHtml){
    if(!Array.isArray(groups)||!groups.length)return '';
    const datatypeName=value=>String(value||'').split(/[#/]/).pop()||'—';
    const displayValue=value=>typeof value==='string'?value:JSON.stringify(value);
    const validity=value=>value.valid_from||value.valid_until?
      `${value.valid_from||'…'} → ${value.valid_until||'…'}`:'未限定';
    const rows=groups.flatMap(group=>(group.values||[]).map(value=>{
      const predicate=group.predicate||group.type||value.predicate||value.type||'';
      const label=group.label||predicate;
      const contradicting=value.status==='contradicting';
      const state=contradicting?`<strong>冲突候选</strong>${value.conflict?.reason?`<small>${escape(value.conflict.reason)}</small>`:''}`:'当前值';
      const support=contradicting?'—':value.accepted_support_count_known===false?'来源支撑尚未计数':`${Number(value.accepted_support_count)||0} 条支撑`;
      return `<tr><th scope="row"><span>${escape(label)}</span><small>${escape(predicate)}</small></th><td>${escape(displayValue(value.value))}</td><td title="${escape(value.datatype||'')}">${escape(datatypeName(value.datatype))}</td><td>${escape(validity(value))}</td><td>${escape(support)}</td><td class="attribute-state ${contradicting?'is-conflict':''}">${state}</td></tr>`;
    }));
    if(!rows.length)return '';
    return `<table class="entity-attributes" aria-label="正式属性"><thead><tr><th>属性</th><th>值</th><th>数据类型</th><th>有效期</th><th>来源</th><th>状态</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }
  function frozenSourceDocument(excerpt){
    if(!excerpt||typeof excerpt!=='object'||Array.isArray(excerpt))return null;
    const text=key=>typeof excerpt[key]==='string'?excerpt[key]:'';
    const version=Number.isSafeInteger(excerpt.version)&&excerpt.version>0?excerpt.version:'未知';
    const source_content=excerpt.source_content==='full_version'?'full_version':'segments_only';
    const fullText=text('full_text');
    const hasAbsoluteSpan=fullText&&Number.isSafeInteger(excerpt.start_char)&&Number.isSafeInteger(excerpt.end_char)&&0<=excerpt.start_char&&excerpt.start_char<excerpt.end_char&&excerpt.end_char<=fullText.length;
    return {title:text('title')||'历史来源',version,source_content,
      reason:text('reason')||(source_content==='full_version'?'回答生成时冻结的历史原文定位':'回答生成时保存的历史原文片段'),
      before:fullText?(hasAbsoluteSpan?fullText.slice(0,excerpt.start_char):fullText):text('excerpt_before'),
      highlight:fullText?(hasAbsoluteSpan?fullText.slice(excerpt.start_char,excerpt.end_char):''):text('highlight'),
      after:fullText&&hasAbsoluteSpan?fullText.slice(excerpt.end_char):text('excerpt_after')};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={frozenSourceDocument,attributeDetailsHtml};
  if(typeof window==='undefined'||typeof document==='undefined')return;
  const host=$('graph-detail');let serial=0;
  const sourceDialog=document.createElement('dialog');sourceDialog.id='source-evidence-dialog';
  sourceDialog.setAttribute('aria-labelledby','source-evidence-title');
  sourceDialog.innerHTML='<header><h2 id="source-evidence-title"></h2><button id="close-source-evidence" class="secondary">关闭 ×</button></header><p id="source-evidence-note"></p><pre id="source-evidence-text"></pre>';
  document.body.appendChild(sourceDialog);
  $('close-source-evidence').onclick=()=>sourceDialog.close();
  function openSource(doc,focusText=null){
    const location=doc.source_location;
    if(location?.document_version_id&&window.SourceReader){
      window.SourceReader.open({projectId:current,versionId:location.document_version_id,location,focusText});return;
    }
    $('source-evidence-title').textContent=doc.title;
    $('source-evidence-note').textContent=`来源版本 ${doc.version} · ${doc.reason}${doc.source_content==='segments_only'?' · 仅保存原文片段，未提供完整正文':''}`;
    const text=$('source-evidence-text');text.replaceChildren(document.createTextNode(doc.before||''));
    if(doc.highlight){const mark=document.createElement('mark');mark.id='source-evidence-anchor';mark.textContent=doc.highlight;text.appendChild(mark);}
    text.appendChild(document.createTextNode(doc.after||''));
    if(!sourceDialog.open)sourceDialog.showModal();
    requestAnimationFrame(()=>{const anchor=$('source-evidence-anchor');if(anchor)anchor.scrollIntoView({block:'center'});else $('source-evidence-text').scrollTop=0;});
  }
  // Only the frozen document projection is accepted; no current-document lookup.
  window.openFrozenSourceEvidence=excerpt=>{
    // excerpt_before / excerpt_after stay local; this path never fetches current state.
    const frozen=frozenSourceDocument(excerpt);if(frozen)openSource(frozen);
  };
  function localTime(value){if(!value)return '未记录';const d=new Date(value);return Number.isNaN(d.valueOf())?value:d.toLocaleString('zh-CN',{hour12:false});}
  const typeHint=type=>{const primary=labelOf(type),local=term(type);return primary===local?esc(primary):`${esc(primary)} <span class="subtle">${esc(local)}</span>`;};
  window.renderEvidenceInspector=async(row,options)=>{
    const ticket=++serial,p=current,stamp=JSON.stringify(scope());
    host.dataset.recordId=row.id;
    const name=id=>options.find(n=>n.id===id)?.text||id;
    const aliases=Array.isArray(row.metadata?.aliases)?row.metadata.aliases:[];
    const formalAttributes=row.kind==='entity'?attributeDetailsHtml(row.attributes,esc):'';
    // 本体归属：这个实体属于哪个类、它的父类是谁（用户反馈"检索没看到有父类的信息"）。
    // 层级在**类**之间，实体本身没有父类，所以这里说的是"它所属类在本体里的上位类"，
    // 数据来自服务端 class_parents / class_ancestors（service.ontology_family，与图谱同一份）。
    const entityFamily=row.kind==='entity'?(()=>{
      const pick=entry=>(entry&&(entry.label||entry.id))||'';
      const parents=(row.class_parents||[]).map(pick).filter(Boolean);
      const ancestors=(row.class_ancestors||[]).map(pick).filter(Boolean);
      const current=pick({label:row.class_label})||labelOf(row.type);
      // 用"字段名 + 值"的清单，而不是一排胶囊：胶囊看起来像可点的输入框，
      // 读者分不清哪个是父类、哪个是当前类（视觉审查就是这么反馈的）。
      // 同时明确写出"父类"两个字，用户原话是"检索没看到有父类的信息"。
      const rows=[['当前类',esc(current)]];
      rows.push(['父类',row.class_ontology_id===null?'绑定本体或类型未知，无法解释层级':parents.length?esc(parents.join('、')):'顶层类（本体里没有父类）']);
      if(ancestors.length>parents.length){
        rows.push(['完整继承链',esc([current,...ancestors].join(' → '))]);
      }
      return `<section class="evidence-family"><h4>本体归属</h4><dl class="evidence-family__rows">`
        +rows.map(([key,value])=>`<dt>${key}</dt><dd>${value}</dd>`).join('')
        +'</dl></section>';
    })():'';
    host.innerHTML=`<section class="evidence-identity"><span class="evidence-kind">${esc({entity:'实体',relation:'关系',chunk:'原文片段',document:'文档'}[row.kind]||row.kind)} · ${typeHint(row.type)}</span><h3>${esc(row.text)}</h3>${row.kind==='relation'?`<div class="relation-path"><b>${esc(name(row.subject_id))}</b><span>↓ ${typeHint(row.type)}</span><b>${esc(name(row.object_id))}</b></div>`:''}${aliases.length?'<p class="evidence-aliases">别名：'+aliases.map(esc).join('、')+'</p>':''}<div class="evidence-action-groups"><div class="evidence-actions"><button data-action="edit" class="admin-only">编辑</button>${row.kind==='entity'?'<button data-action="add-relation" class="admin-only">新增关系</button><button data-action="add-attribute" class="admin-only">新增属性</button>':''}<button data-action="history" class="secondary">修订历史 · r${row.version}</button>${row.kind==='entity'||row.kind==='relation'?'<button data-action="ledger" class="secondary">在台账查看</button>':''}${row.kind==='entity'?'<button data-action="mindmap" class="secondary">展开脑图</button>':''}</div>${row.kind==='entity'||row.kind==='relation'?'<div class="evidence-danger"><span>危险操作</span></div>':''}</div></section>${formalAttributes?`<section class="evidence-attributes"><h4>实体属性</h4>${formalAttributes}</section>`:''}${entityFamily}<section class="evidence-sources"><h4>原文证据</h4><div id="inspector-source-list" aria-live="polite">正在查找来源…</div></section><section class="evidence-time"><h4>时间</h4><dl><dt>业务生效</dt><dd>${esc(row.valid_from?localTime(row.valid_from):'未知')}</dd><dt>业务失效</dt><dd>${esc(row.valid_until?localTime(row.valid_until):'未设定')}</dd><dt>系统记录</dt><dd>${esc(localTime(row.recorded_at))}</dd></dl></section><details class="evidence-technical"><summary>Metadata / 属性 / 技术标识</summary><pre>${esc(JSON.stringify({id:row.id,type:row.type,source_id:row.source_id,ontology_id:row.ontology_id,metadata:row.metadata,properties:row.properties},null,2))}</pre></details>`;
    host.querySelector('[data-action="edit"]').onclick=()=>editRecord(row);
    const version=document.createElement('p');version.id='inspector-version';version.className='subtle';
    version.textContent=RecordVersionContext.revision(row)+' · 本体归属读取中…';
    host.querySelector('.evidence-identity h3').after(version);
    const historyButton=host.querySelector('[data-action="history"]');
    historyButton.textContent='修订历史 · '+RecordVersionContext.revision(row);
    const governance=row.structure_pending;
    if(governance){
      const note=document.createElement('p');note.className='subtle';
      note.textContent=`当前治理视角：${governance.state==='pending'?'结构待定':'待定已解除'}（按当前本体判断，不代表已迁移或通过绑定本体校验）。`;
      version.after(note);
    }
    api(endpoint('/ontologies'),undefined,'GET').then(result=>{
      if(ticket===serial&&current===p&&stamp===JSON.stringify(scope())){
        version.textContent=RecordVersionContext.summary(row,RecordVersionContext.build(result.versions||[]));
        version.title='普通编辑只增加知识修订；本体归属由每次修订保存的本体发布标识确定。';
      }
    }).catch(()=>{
      if(ticket===serial&&current===p)version.textContent=RecordVersionContext.summary(row,null);
    });
    host.querySelector('[data-action="edit"]').textContent=row.kind==='relation'?'编辑这条关系':row.kind==='entity'?'编辑这个实体':'编辑';
    host.querySelector('[data-action="history"]').onclick=()=>historyFor(row);
    host.querySelector('[data-action="add-relation"]')?.addEventListener('click',()=>window.RecordCreate?.open('relation',{subject:row}));
    host.querySelector('[data-action="add-attribute"]')?.addEventListener('click',()=>window.RecordCreate?.open('attribute',{subject:row}));
    // D1「图谱 → 台账」：跳回台账并定位到这一行（记录可能分批加载，focusLedgerRecord 在取回数据后落地）。
    host.querySelector('[data-action="ledger"]')?.addEventListener('click',()=>window.focusLedgerRecord?.(row));
    if(row.kind==='entity'||row.kind==='relation'){
      const remove=document.createElement('button');remove.className='danger admin-only';
      remove.textContent=row.kind==='relation'?'删除这条关系':'删除实体及其关联关系';
      remove.title='软删除后可在「知识台账 → 可撤销的操作」里撤销';
      remove.onclick=async()=>{
        if(!window.Auth?.isAdmin?.()){status('只有管理员可以删除知识记录',true);return;}
        if(ticket!==serial||p!==current||stamp!==JSON.stringify(scope())){status('项目或范围已变化，请重新选择记录',true);return;}
        if(remove.dataset.confirm!=='true'){
          remove.dataset.confirm='true';remove.textContent='再次点击确认删除';
          remove.insertAdjacentHTML('afterend','<small class="delete-hint">这是可撤销的软删除；点击其他记录可取消。</small>');return;
        }
        remove.disabled=true;
        try{
          const result=await api('/api/projects/'+encodeURIComponent(p)+'/delete',{record_id:row.id,expected_version:row.version});
          if(ticket!==serial||p!==current||stamp!==JSON.stringify(scope()))return;
          status(`已软删除 ${result.deleted} 条记录；可在「知识台账 → 可撤销的操作」撤销。`);
          host.innerHTML='<p class="evidence-empty">该知识已软删除，正在刷新图谱…</p>';$('draw-graph').click();
        }catch(error){status(error.message,true);remove.disabled=false;}
      };
      host.querySelector('.evidence-danger').appendChild(remove);
    }
    const mind=host.querySelector('[data-action="mindmap"]');if(mind)mind.onclick=()=>window.openEntityMindmap(row);
    try{
      const result=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/evidence'),scope());
      if(ticket!==serial||current!==p||stamp!==JSON.stringify(scope())||!$('inspector-source-list'))return;
      $('inspector-source-list').innerHTML=result.documents.length?result.documents.map((doc,i)=>`<article class="source-evidence-card"><strong>${esc(doc.title)}</strong><small>来源修订 r${doc.version} · ${esc({exact:'精确证据位置',recovered_in_chunk:'历史切片内恢复定位',chunk:'仅保存切片级位置',offset:'记录偏移定位',passage:'证据文本匹配',text:'名称 / 内容匹配',unlocated:'历史来源无法定位'}[doc.mode])}</small><blockquote>${esc(doc.preview||'原文内容未保存')}</blockquote><button data-source-evidence="${i}">${doc.source_location?.document_version_id?'在原文中打开 ↗':doc.highlight?'查看原文并定位 ↗':'查看来源文档 ↗'}</button></article>`).join(''):`<p class="evidence-empty">${esc(result.message)}</p>`;
      host.querySelectorAll('[data-source-evidence]').forEach(b=>b.onclick=()=>openSource(result.documents[Number(b.dataset.sourceEvidence)],row.kind==='entity'?row.text:null));
    }catch(e){if(ticket===serial&&current===p&&$('inspector-source-list'))$('inspector-source-list').textContent='来源读取失败：'+e.message;}
  };
  const change=$('project').onchange;
  $('project').onchange=()=>{serial++;if(sourceDialog.open)sourceDialog.close();change?.();};
})();
