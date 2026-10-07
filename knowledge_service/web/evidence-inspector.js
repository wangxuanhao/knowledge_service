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
      const support=contradicting?'—':`${Number(value.accepted_support_count)||0} 条支撑`;
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
  const relationDialog=document.createElement('dialog');relationDialog.id='create-relation-dialog';relationDialog.setAttribute('aria-labelledby','create-relation-title');
  relationDialog.innerHTML='<header><div><small>正式知识维护</small><h2 id="create-relation-title">新增实体关系</h2></div><button id="close-create-relation" class="secondary">关闭 ×</button></header><p class="subtle">直接创建正式关系，不会重新解析文档或调用 LLM。开放关系不限制实体类型；受控关系保存时会校验起点和终点类型。</p><form id="create-relation-form"><label>起点实体<input id="create-relation-subject" readonly></label><fieldset class="relation-picker"><legend>选择关系类型</legend><input id="create-relation-type" type="hidden"><div id="create-relation-types" class="relation-choice-grid" role="radiogroup"></div></fieldset><p id="create-relation-constraint" class="relation-constraint-note"></p><fieldset class="relation-picker"><legend>选择终点实体</legend><input id="create-relation-target-filter" placeholder="输入实体名称或类型筛选" aria-label="筛选终点实体"><input id="create-relation-object" type="hidden"><p id="create-relation-selected" class="relation-selected">尚未选择终点实体</p><div id="create-relation-targets" class="relation-target-list" role="listbox"></div></fieldset><div class="relation-dialog-actions"><button type="button" id="cancel-create-relation" class="secondary">取消</button><button type="submit" id="save-create-relation">保存并添加到图谱</button></div><p id="create-relation-message" role="status"></p></form>';
  document.body.appendChild(relationDialog);
  $('close-create-relation').onclick=$('cancel-create-relation').onclick=()=>relationDialog.close();
  let relationRequest=0,relationSubject=null,relationEntities=new Map(),relationsForDialog=[];
  async function openRelationCreator(subject){
    relationSubject=subject;
    const ticket=++relationRequest,p=current;$('create-relation-title').textContent='从“'+subject.text.slice(0,55)+'”新增关系';$('create-relation-subject').value=subject.text;$('create-relation-message').textContent='正在加载关系类型和实体…';$('create-relation-message').classList.remove('error');
    $('create-relation-form').dataset.subjectId=subject.id;if(!relationDialog.open)relationDialog.showModal();
    try{
      const [ontology,result]=await Promise.all([api(endpoint('/ontology'),undefined,'GET'),api(endpoint('/records/query?limit=1000'),{kinds:['entity']})]);
      if(ticket!==relationRequest||p!==current||!relationDialog.open)return;
      const relations=ontology.summary.relations||[],entities=result.records||[];relationsForDialog=relations;relationEntities=new Map(entities.map(item=>[item.id,item]));
      $('create-relation-form').dataset.ontologyId=ontology.id;
      $('create-relation-type').value='';$('create-relation-object').value='';$('create-relation-selected').textContent='尚未选择终点实体';$('save-create-relation').disabled=true;
      const syncSave=()=>{$('save-create-relation').disabled=!$('create-relation-type').value||!$('create-relation-object').value;};
      const showConstraint=()=>{const item=relations.find(value=>value.id===$('create-relation-type').value),names=values=>(values||[]).map(labelOf).join('、')||'不限';$('create-relation-constraint').textContent=item?`允许起点：${names(item.domain)}；允许终点：${names(item.range)}`:'';};
      $('create-relation-types').innerHTML=relations.map(item=>`<button type="button" class="relation-choice" data-relation-type="${esc(item.id)}" aria-pressed="false"><b>${esc(item.label_zh||item.label||labelOf(item.id))}</b><small>${esc(labelOf(item.id))}</small></button>`).join('')||'<p class="picker-empty">当前本体没有关系类型</p>';
      $('create-relation-types').querySelectorAll('[data-relation-type]').forEach(button=>button.onclick=()=>{$('create-relation-type').value=button.dataset.relationType;$('create-relation-types').querySelectorAll('button').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));showConstraint();syncSave();});
      const renderTargets=()=>{const query=$('create-relation-target-filter').value.trim().toLocaleLowerCase(),selected=$('create-relation-object').value,filtered=entities.filter(item=>!query||item.text.toLocaleLowerCase().includes(query)||labelOf(item.type).toLocaleLowerCase().includes(query));$('create-relation-targets').innerHTML=filtered.slice(0,100).map(item=>`<button type="button" class="relation-target" role="option" data-target-id="${esc(item.id)}" aria-selected="${item.id===selected}"><span><b>${esc(item.text)}</b><small>${esc(labelOf(item.type))}</small></span><i>${item.id===selected?'已选择':'选择'}</i></button>`).join('')||'<p class="picker-empty">没有匹配的实体</p>';$('create-relation-targets').querySelectorAll('[data-target-id]').forEach(button=>button.onclick=()=>{$('create-relation-object').value=button.dataset.targetId;const target=relationEntities.get(button.dataset.targetId);$('create-relation-selected').innerHTML=`当前选择：<b>${esc(target.text)}</b> · ${esc(labelOf(target.type))}`;syncSave();renderTargets();});};
      $('create-relation-target-filter').value='';$('create-relation-target-filter').oninput=renderTargets;renderTargets();showConstraint();
      $('create-relation-message').textContent=relations.length&&entities.length?'选择关系类型和终点实体后保存。':!relations.length?'当前本体没有可用的关系类型。':'当前项目没有可选的终点实体。';
      if(!relations.length||!entities.length)$('save-create-relation').disabled=true;
    }catch(error){if(ticket===relationRequest){$('create-relation-message').textContent='加载失败：'+error.message;$('create-relation-message').classList.add('error');$('save-create-relation').disabled=true;}}
  }
  $('create-relation-form').onsubmit=async event=>{
    event.preventDefault();const button=$('save-create-relation');button.disabled=true;$('create-relation-message').classList.remove('error');$('create-relation-message').textContent='正在校验并保存…';
    try{
      const subjectId=event.currentTarget.dataset.subjectId,objectId=$('create-relation-object').value,type=$('create-relation-type').value,subject=relationSubject||wb.nodes.get(subjectId)||wb.records.get(subjectId),target=relationEntities.get(objectId);
      if(!subjectId||!objectId||!type)throw Error('请选择关系类型和终点实体');
      const relationDefinition=relationsForDialog.find(item=>item.id===type),relationLabel=relationDefinition?.label_zh||relationDefinition?.label||labelOf(type),objectLabel=target?.text||objectId;
      const starts=[subject?.valid_from,target?.valid_from].filter(Boolean).sort(),ends=[subject?.valid_until,target?.valid_until].filter(Boolean).sort();
      const valid_from=starts.at(-1)||null,valid_until=ends[0]||null;if(valid_from&&valid_until&&valid_from>=valid_until)throw Error('两个实体的业务有效期没有重叠，不能建立关系');
      const result=await api(endpoint('/records'),{records:[{kind:'relation',text:`${subject?.text||subjectId} ${relationLabel} ${objectLabel}`,type,subject_id:subjectId,object_id:objectId,ontology_id:event.currentTarget.dataset.ontologyId,valid_from,valid_until,metadata:{created_via:'interactive_graph'}}]});
      const saved=result.records[0];wb.records.set(saved.id,saved);relationDialog.close();status(`已新增关系：${subject?.text||subjectId} → ${relationLabel} → ${objectLabel}；版本 v${saved.version}。`);$('draw-graph').click();
    }catch(error){$('create-relation-message').textContent='保存失败：'+error.message;$('create-relation-message').classList.add('error');button.disabled=false;}
  };
  function openSource(doc){
    $('source-evidence-title').textContent=doc.title;
    $('source-evidence-note').textContent=`来源版本 ${doc.version} · ${doc.reason}${doc.source_content==='segments_only'?' · 旧数据只保存了片段，并非完整原文':''}`;
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
      rows.push(['父类',parents.length?esc(parents.join('、')):'顶层类（本体里没有父类）']);
      if(ancestors.length>parents.length){
        rows.push(['完整继承链',esc([current,...ancestors].join(' → '))]);
      }
      return `<section class="evidence-family"><h4>本体归属</h4><dl class="evidence-family__rows">`
        +rows.map(([key,value])=>`<dt>${key}</dt><dd>${value}</dd>`).join('')
        +'</dl></section>';
    })():'';
    const legacyAttributes=row.kind==='entity'&&!formalAttributes&&row.properties&&Object.keys(row.properties).length?
      `<section class="evidence-attributes"><h4>兼容属性</h4><dl>${Object.entries(row.properties).map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(typeof value==='string'?value:JSON.stringify(value))}</dd>`).join('')}</dl></section>`:'';
    host.innerHTML=`<section class="evidence-identity"><span class="evidence-kind">${esc({entity:'实体',relation:'关系',chunk:'原文片段',document:'文档'}[row.kind]||row.kind)} · ${typeHint(row.type)}</span><h3>${esc(row.text)}</h3>${row.kind==='relation'?`<div class="relation-path"><b>${esc(name(row.subject_id))}</b><span>↓ ${typeHint(row.type)}</span><b>${esc(name(row.object_id))}</b></div>`:''}${aliases.length?'<p class="evidence-aliases">别名：'+aliases.map(esc).join('、')+'</p>':''}<div class="evidence-action-groups"><div class="evidence-actions"><button data-action="edit">编辑</button>${row.kind==='entity'?'<button data-action="add-relation">新增关系</button>':''}<button data-action="history" class="secondary">版本历史 · v${row.version}</button>${row.kind==='entity'||row.kind==='relation'?'<button data-action="ledger" class="secondary">在台账查看</button>':''}${row.kind==='entity'?'<button data-action="mindmap" class="secondary">展开脑图</button>':''}</div>${row.kind==='entity'||row.kind==='relation'?'<div class="evidence-danger"><span>危险操作</span></div>':''}</div></section>${formalAttributes?`<section class="evidence-attributes"><h4>实体属性</h4>${formalAttributes}</section>`:legacyAttributes}${entityFamily}<section class="evidence-sources"><h4>原文证据</h4><div id="inspector-source-list" aria-live="polite">正在查找来源…</div></section><section class="evidence-time"><h4>时间</h4><dl><dt>业务生效</dt><dd>${esc(row.valid_from?localTime(row.valid_from):'未知')}</dd><dt>业务失效</dt><dd>${esc(row.valid_until?localTime(row.valid_until):'未设定')}</dd><dt>系统记录</dt><dd>${esc(localTime(row.recorded_at))}</dd></dl></section><details class="evidence-technical"><summary>Metadata / 属性 / 技术标识</summary><pre>${esc(JSON.stringify({id:row.id,type:row.type,source_id:row.source_id,ontology_id:row.ontology_id,metadata:row.metadata,properties:row.properties},null,2))}</pre></details>`;
    host.querySelector('[data-action="edit"]').onclick=()=>editRecord(row);
    host.querySelector('[data-action="edit"]').textContent=row.kind==='relation'?'编辑这条关系':row.kind==='entity'?'编辑这个实体':'编辑';
    host.querySelector('[data-action="history"]').onclick=()=>historyFor(row);
    host.querySelector('[data-action="add-relation"]')?.addEventListener('click',()=>openRelationCreator(row));
    // D1「图谱 → 台账」：跳回台账并定位到这一行（记录可能分批加载，focusLedgerRecord 在取回数据后落地）。
    host.querySelector('[data-action="ledger"]')?.addEventListener('click',()=>window.focusLedgerRecord?.(row));
    if(row.kind==='entity'||row.kind==='relation'){
      const remove=document.createElement('button');remove.className='danger';
      remove.textContent=row.kind==='relation'?'删除这条关系':'删除实体及其关联关系';
      remove.title='软删除后可在「知识台账 → 可撤销的操作」里撤销';
      remove.onclick=async()=>{
        if(remove.dataset.confirm!=='true'){
          remove.dataset.confirm='true';remove.textContent='再次点击确认删除';
          remove.insertAdjacentHTML('afterend','<small class="delete-hint">这是可撤销的软删除；点击其他记录可取消。</small>');return;
        }
        remove.disabled=true;
        try{
          const result=await api(endpoint('/delete'),{record_id:row.id,expected_version:row.version});
          status(`已软删除 ${result.deleted} 条记录；可在「知识台账 → 可撤销的操作」撤销。`);
          host.innerHTML='<p class="evidence-empty">该知识已软删除，正在刷新图谱…</p>';$('draw-graph').click();
        }catch(error){status(error.message,true);remove.disabled=false;}
      };
      host.querySelector('.evidence-danger').appendChild(remove);
    }
    const mind=host.querySelector('[data-action="mindmap"]');if(mind)mind.onclick=()=>{$('mindmap-root').value=row.id;showTab('mindmap');$('draw-mindmap').click();};
    try{
      const result=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/evidence'),scope());
      if(ticket!==serial||current!==p||stamp!==JSON.stringify(scope())||!$('inspector-source-list'))return;
      $('inspector-source-list').innerHTML=result.documents.length?result.documents.map((doc,i)=>`<article class="source-evidence-card"><strong>${esc(doc.title)}</strong><small>来源 v${doc.version} · ${esc({exact:'精确证据位置',recovered_in_chunk:'历史切片内恢复定位',chunk:'仅保存切片级位置',offset:'记录偏移定位',passage:'证据文本匹配',text:'名称 / 内容匹配',unlocated:'历史来源无法定位'}[doc.mode])}</small><blockquote>${esc(doc.preview||'原文内容未保存')}</blockquote><button data-source-evidence="${i}">${doc.highlight?'查看原文并定位 ↗':'查看来源文档 ↗'}</button></article>`).join(''):`<p class="evidence-empty">${esc(result.message)}</p>`;
      host.querySelectorAll('[data-source-evidence]').forEach(b=>b.onclick=()=>openSource(result.documents[Number(b.dataset.sourceEvidence)]));
    }catch(e){if(ticket===serial&&current===p&&$('inspector-source-list'))$('inspector-source-list').textContent='来源读取失败：'+e.message;}
  };
  const change=$('project').onchange;
  $('project').onchange=()=>{serial++;relationRequest++;if(sourceDialog.open)sourceDialog.close();if(relationDialog.open)relationDialog.close();change();};
})();
