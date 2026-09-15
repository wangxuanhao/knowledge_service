/* A browsable record library; existing revision/history/governance actions remain intact. */
(() => {
  const host=$('records'), tab=$('tab-records');let rows=[],total=0,request=0,page=0;
  const libraryPanel=host.closest('.panel'),libraryDetails=document.createElement('details'),librarySummary=document.createElement('summary');
  libraryDetails.className='record-section record-library-section';libraryDetails.open=true;
  librarySummary.innerHTML='<span>知识与版本</span><small id="record-section-meta">查询、编辑和查看版本历史</small>';
  libraryPanel.before(libraryDetails);libraryDetails.append(librarySummary,libraryPanel);libraryPanel.classList.add('record-library-panel');
  // Documents are ingestion receipts and belong to 原文数据源.  This view is
  // limited to knowledge that was actually committed.
  const kinds={chunk:'原文片段',entity:'实体',relation:'关系'};
  const bar=document.createElement('div');bar.className='record-library-tools';
  bar.innerHTML='<label>搜索已加载的记录<input id="record-search" placeholder="名称、正文或记录编号"></label><label>知识类别<select id="record-kind"><option value="">全部类别</option>'+Object.entries(kinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')+'</select></label><span id="record-count" aria-live="polite"></span>';
  host.before(bar);$('load-records').textContent='刷新记录';
  const governance=[...tab.querySelectorAll('.panel')].find(p=>p.querySelector('#resolve-text'));
  if(governance){
    const details=document.createElement('details');details.className='record-section record-governance';
    const summary=document.createElement('summary');summary.innerHTML='<span>实体消歧与融合</span><small>查重、规范实体、别名与可撤销合并</small>';
    governance.before(details);details.append(summary,governance);
    const controls=governance.querySelector('.entity-governance-bar'),control=id=>$(id).closest('label')||$(id);
    const step=(number,title,description,ids,risk=false)=>{const section=document.createElement('section');section.className='governance-step'+(risk?' governance-step-risk':'');section.innerHTML=`<header><b>${number}</b><div><h3>${title}</h3><small>${description}</small></div></header>`;ids.map(control).forEach(item=>section.append(item));return section;};
    const searchStep=step('01','按名称查找','输入名称或别名，系统会列出可能重复的实体',['resolve-text','resolve-threshold','resolve-entity']);
    const keepStep=step('02','选择保留的实体','合并完成后，这个实体继续存在并作为规范实体',['keep-id','alias-name','add-alias']);
    const mergeStep=step('03','选择另一个重复实体','第二个实体会停用，其来源和关系转到保留实体',['drop-id','merge-confirm','merge-entities','load-operations'],true);
    controls.replaceChildren(searchStep,keepStep,mergeStep);
    const keepInput=$('keep-id'),dropInput=$('drop-id'),mergeButton=$('merge-entities'),confirm=$('merge-confirm'),aliasInput=$('alias-name'),aliasButton=$('add-alias');
    keepInput.closest('label').classList.add('governance-technical-id');dropInput.closest('label').classList.add('governance-technical-id');
    const selectedCard=(role,id)=>{const card=document.createElement('div');card.className='governance-selection is-empty';card.innerHTML=`<div><small>${role}</small><strong id="${id}-name">尚未选择</strong><span id="${id}-description">请先从查重结果中选择</span></div><button type="button" id="clear-${id}" class="secondary" disabled>清除</button>`;return card;};
    const keepCard=selectedCard('合并后保留','governance-keep'),dropCard=selectedCard('将被合并并停用','governance-drop');
    keepStep.querySelector('header').after(keepCard);mergeStep.querySelector('header').after(dropCard);
    const preview=document.createElement('div');preview.className='governance-merge-preview';mergeStep.insertBefore(preview,confirm.closest('label'));
    const selectedRow=id=>wb.records.get(id)||rows.find(row=>row.id===id);
    const sourceContext=row=>{const source=selectedRow(row?.source_id),metadata=row?.metadata||{},sourceMetadata=source?.metadata||{};const value=metadata.source_file||metadata.title||sourceMetadata.source_file||sourceMetadata.title||source?.text;return value?` · 来源 ${String(value).replace(/\s+/g,' ').slice(0,34)}`:'';};
    const recordContext=row=>`${labelOf(row.type)||'未分类'} · 版本 ${row.version}${sourceContext(row)}`;
    const paint=(input,prefix)=>{const row=selectedRow(input.value),card=prefix==='governance-keep'?keepCard:dropCard;card.classList.toggle('is-empty',!input.value);$(prefix+'-name').textContent=row?.text||(input.value?'已填写技术编号':'尚未选择');$(prefix+'-description').textContent=row?recordContext(row):(input.value?'将在提交前检查该编号':'请先从查重结果中选择');$('clear-'+prefix).disabled=!input.value;};
    function syncSelection(){
      paint(keepInput,'governance-keep');paint(dropInput,'governance-drop');
      const keep=selectedRow(keepInput.value),drop=selectedRow(dropInput.value),same=keepInput.value&&keepInput.value===dropInput.value;
      preview.classList.toggle('is-error',!!same);
      preview.innerHTML=same?'<b>不能选择同一个实体</b><span>保留实体和待合并实体必须是两条不同记录。</span>':keep&&drop?`<b>${esc(drop.text)}</b><span>将合并到</span><b>${esc(keep.text)}</b>`:'<b>尚未选满两个实体</b><span>先在查重结果中分别点击“合并后保留”和“作为重复项合并”。</span>';
      mergeButton.disabled=!keepInput.value||!dropInput.value||!!same||!confirm.checked;
      aliasButton.disabled=!keepInput.value||!aliasInput.value.trim();
      governance.querySelectorAll('[data-keep]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.keep===keepInput.value)));
      governance.querySelectorAll('[data-drop]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.drop===dropInput.value)));
    }
    function decorateResults(){
      const result=$('resolve-result'),candidates=[...result.querySelectorAll('.candidate')];if(!candidates.length)return;
      const statusLine=result.querySelector(':scope>p');if(statusLine)statusLine.textContent='已完成查重，请从结果中选择实体。';
      result.insertAdjacentHTML('afterbegin','<div class="governance-result-help"><b>找到以下实体</b><span>每张卡片选择一种角色：保留其中一个，再把另一个作为重复项合并。如果这里只有一条，可先选择角色，再搜索另一个名称。</span></div>');
      candidates.forEach(card=>{const id=card.querySelector('[data-keep]')?.dataset.keep,row=selectedRow(id),detail=card.querySelector('small');if(!row||!detail)return;detail.textContent=recordContext(row);detail.title='技术 ID：'+row.id;card.title='技术 ID：'+row.id;});
      result.querySelectorAll('[data-keep]').forEach(button=>{button.textContent='合并后保留';button.title='这条记录会继续存在';button.onclick=()=>{keepInput.value=button.dataset.keep;syncSelection();};});
      result.querySelectorAll('[data-drop]').forEach(button=>{button.textContent='作为重复项合并';button.title='这条记录会停用，关系转到保留实体';button.onclick=()=>{dropInput.value=button.dataset.drop;syncSelection();};});
      syncSelection();
    }
    const resolveAction=$('resolve-entity').onclick;
    $('resolve-entity').onclick=async()=>{await resolveAction();decorateResults();};
    $('clear-governance-keep').onclick=()=>{keepInput.value='';syncSelection();};$('clear-governance-drop').onclick=()=>{dropInput.value='';syncSelection();};
    keepInput.oninput=syncSelection;dropInput.oninput=syncSelection;aliasInput.oninput=syncSelection;confirm.onchange=syncSelection;
    confirm.closest('label').lastChild.textContent='我确认：重复实体将停用，现有关系统一转到保留实体';
    mergeButton.textContent='确认合并这两个实体';syncSelection();
  }
  const nameOf=id=>{const row=wb.records.get(id)||rows.find(x=>x.id===id);return row?row.text:(id||'(未加载)');};
  function render(){
    const q=$('record-search').value.trim().toLocaleLowerCase(),kind=$('record-kind').value;
    const filtered=rows.filter(r=>(!kind||r.kind===kind)&&(!q||(r.text+' '+r.id).toLocaleLowerCase().includes(q)));
    const pages=Math.max(1,Math.ceil(filtered.length/25));page=Math.min(page,pages-1);
    $('record-count').textContent=`匹配 ${filtered.length} 条 · 已加载 ${rows.length} / ${total} 条`;
    $('record-section-meta').textContent=`当前匹配 ${filtered.length} 条 · 已加载 ${rows.length} / ${total} 条`;
    if(!filtered.length){host.innerHTML='<div class="record-empty">没有匹配的知识<p>可调整搜索、类别或上方时间与 Metadata 条件。</p></div>';return;}
    const visible=filtered.slice(page*25,page*25+25);
    host.innerHTML='<div class="table-scroll"><table class="record-library"><thead><tr><th>知识内容</th><th>类别 / 本体类型</th><th>版本 / 记录时间</th><th>操作</th></tr></thead><tbody>'+visible.map((r,i)=>`<tr><td>${r.kind==='relation'?`<div class="record-excerpt"><b>${esc(nameOf(r.subject_id))}</b> —[${typeHint(r.type)}]→ <b>${esc(nameOf(r.object_id))}</b></div><details><summary>原文上下文</summary><small>${esc(r.text)}</small></details>`:`<div class="record-excerpt">${esc(r.text.slice(0,160))}</div>`}<details><summary>编号与有效期</summary><small>${esc(r.id)}<br>${esc(r.valid_from||'未知')} → ${esc(r.valid_until||'未知')}</small></details></td><td><span class="record-kind-tag">${kinds[r.kind]||esc(r.kind)}</span><small>${typeHint(r.type)||'—'}</small></td><td><b>v${r.version}</b><small>${r.recorded_at?esc(new Date(r.recorded_at).toLocaleString()):'—'}</small></td><td><div class="record-actions"><button data-edit-row="${i}">编辑</button><button data-history-row="${i}" class="secondary">版本历史</button></div></td></tr>`).join('')+'</tbody></table></div><div class="record-pages"><button id="records-prev" class="secondary">上一页</button><span>'+`${page+1} / ${pages} 页 · 每页 25 条`+'</span><button id="records-next" class="secondary">下一页</button></div>';
    host.querySelectorAll('[data-edit-row]').forEach(b=>b.onclick=()=>editRecord(visible[Number(b.dataset.editRow)]));
    host.querySelectorAll('[data-history-row]').forEach(b=>b.onclick=()=>historyFor(visible[Number(b.dataset.historyRow)]));
    $('records-prev').disabled=page===0;$('records-next').disabled=page===pages-1;
    $('records-prev').onclick=()=>{page--;render();};$('records-next').onclick=()=>{page++;render();};
  }
  async function load(){
    const serial=++request,p=current;page=0;rows=[];wb.records.clear();$('record-count').textContent='';
    if(!p){$('record-section-meta').textContent='请先选择项目';host.innerHTML='<div class="record-empty">请先在左侧选择项目</div>';return;}
    $('record-section-meta').textContent='正在读取当前范围…';
    host.innerHTML='<div class="record-empty" role="status">正在读取当前范围的知识…</div>';
    try{const filter=scope(),stamp=JSON.stringify(filter);filter.kinds=['entity','relation','chunk'];const r=await api(endpoint('/records/query?limit=1000'),filter);
      if(serial!==request||p!==current||stamp!==JSON.stringify(scope()))return;
      rows=r.records;total=r.total;wb.records=new Map(rows.map(r=>[r.id,r]));render();
    }catch(error){if(serial===request&&p===current)host.textContent='读取失败：'+error.message+'；可点击“刷新记录”重试。';}
  }
  $('record-search').oninput=()=>{page=0;render();};$('record-kind').onchange=()=>{page=0;render();};
  $('load-records').onclick=load;
  document.querySelector('[data-tab="records"]').addEventListener('click',load);
  $('project').addEventListener('change',()=>{request++;rows=[];total=0;$('record-search').value='';$('record-kind').value='';if(!tab.classList.contains('hidden'))load();});
  for(const id of ['apply-scope','reset-scope'])$(id).addEventListener('click',()=>{if(!tab.classList.contains('hidden'))load();});
  // Existing loader builds structured ontology cards and preserves version selection controls.
  $('load-ontology').textContent='刷新本体';
  const ontologyVisible=()=>!$('tab-ontology').classList.contains('hidden');
  async function autoOntology(){
    if(!current){$('ontology-summary').textContent='请先在左侧选择项目';return;}
    const p=current;
    $('ontology-summary').textContent='正在读取项目本体…';await $('load-ontology').onclick();
    if(p===current&&$('ontology-summary').textContent==='正在读取项目本体…')$('ontology-summary').textContent='本体未加载成功，请检查是否已创建项目本体，或点击“刷新本体”重试。';
  }
  document.querySelector('[data-tab="ontology"]').addEventListener('click',autoOntology);
  $('project').addEventListener('change',()=>{if(ontologyVisible()){ $('turtle').value='';$('ontology-versions').replaceChildren();autoOntology();}});
})();
