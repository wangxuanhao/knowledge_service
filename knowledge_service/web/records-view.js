/* 知识台账（实例层的"家"）：查 / 改 / 并重复项 / 撤销误操作。
   边界一句话（用户反复追问的就是边界）：**这里管"具体的东西"，分类不在这** —— 要改类/属性/关系去「本体建模层」。
   与审核台的分工：审核台管"结构要不要过"，台账管"已经进来的具体知识长什么样、要不要纠错"。
   创建、修订和治理均复用正式服务；表格与实体选择器从服务端分页，不预载全项目。 */
(() => {
  const host=$('records'), tab=$('tab-records');let rows=[],total=0,request=0,page=0,view='entity';
  const visible=()=>!tab.hidden&&!tab.classList.contains('hidden');
  const pageCounts=new Map();let pager=null,loading=false,searchTimer=null,operationsLoaded=false;
  const openCreator=(kind,options)=>{if(window.RecordCreate?.open)return window.RecordCreate.open(kind,options);status('知识创建组件未加载，请刷新页面重试。',true);};
  // 台账头部要用的四份"旁数据"，一次读取后缓存到本项目为止（点「刷新记录」重取）：
  //   ontology  本体版本表（本体归属列：这条知识挂在哪一版本体上）
  //   assertions 关联断言（血缘数量与审核状态，不把全部关联都称为有效支撑）
  //   operations 可撤销的操作（撤销入口）
  //   duplicates 同名同类型实体分组（顶部「疑似重复」数字）
  let ontology=null,assertions=null,operations=[],duplicates=null;
  // 图谱里点「在台账查看」→ 跳回台账定位到那一行。跨页签跳转要先记下目标，等 load() 把
  // 数据取回来再滚动高亮（rows 在 load 完成前是空的，立刻定位会扑空）。
  let pendingLedgerFocus=null;
  const node=(tag,cls,text)=>{const item=document.createElement(tag);if(cls)item.className=cls;if(text!==undefined)item.textContent=text;return item;};
  const libraryPanel=host.closest('.panel'),libraryDetails=document.createElement('details'),librarySummary=document.createElement('summary');
  libraryDetails.className='record-section record-library-section';libraryDetails.open=true;
  librarySummary.innerHTML='<span>知识台账</span><small id="record-section-meta">查、改、并重复项、撤销误操作</small>';
  libraryPanel.before(libraryDetails);libraryDetails.append(librarySummary,libraryPanel);libraryPanel.classList.add('record-library-panel');
  // Documents are ingestion receipts and belong to 原文数据源.  This view is
  // limited to knowledge that was actually committed.
  // 台账维护的四类「具体的东西」：实体 / 关系 / 属性 / 原文片段。
  // 属性（attribute）是一条独立记录（subject_id + 属性名 + 值 + 数据类型），以前台账里根本没有这一视角，
  // 于是"实体身上的属性"只能靠代码/接口看 —— 用户点名的就是这一条。
  const kinds={chunk:'原文片段',entity:'实体',relation:'关系',attribute:'属性'};
  const VIEW_ORDER=['entity','relation','attribute','chunk'];
  const VIEW_HELP={
    entity:'每个实体挂在本体的哪个类上、用的是哪一版本体。',
    relation:'谁和谁之间是什么关系（端点、类型、本体归属）。',
    attribute:'实体身上的属性值（属性名 = 值）：归属在哪个实体、哪一版本体上。',
    chunk:'知识背后的原文片段：只读 —— 要改原文请重新上传并解析文档。',
  };
  const COLUMNS={
    entity:['名称','本体类型','本体归属','知识修订','关联断言','操作'],
    relation:['关系','关系类型','本体归属','知识修订','关联断言','操作'],
    attribute:['属性','所属实体','本体归属','知识修订','关联断言','操作'],
    chunk:['原文片段','来源文档','知识修订','关联断言','操作'],
  };
  /* 台账头部三件套：一句话边界 + 顶部可点数字 + 三视角页签。
     插在旧面板之前，`#records` 仍然是表格宿主（编辑/版本历史那套沿用旧通道，不动）。 */
  const intro=node('p','ledger-intro');
  intro.innerHTML='这里管<b>具体的东西</b>：查、改、并重复项、撤销误操作。'
    +'<span>分类不在这 —— 要改类 / 属性 / 关系去「本体建模层」；要审结构变更去「本体建模层」。</span>';
  const kpis=node('div','ledger-kpis');kpis.id='ledger-kpis';
  const tabs=node('div','ledger-tabs');tabs.id='ledger-tabs';tabs.setAttribute('role','tablist');
  const operations_section=node('section','ledger-operations');operations_section.id='ledger-operations';
  /* 「还有知识挂在旧本体版本上」：只在真有时给**一行**提示，并说清去哪儿处理。
     台账不再摆一整块「把这批知识迁到新本体」面板 —— 台账管"具体的东西"（查 / 改 / 并重复项 / 撤销），
     本体版本迁移是本体层的事：触发点在「本体建模层」发布新版本的地方，那边一键迁移（可撤销）。
     用户原话：「把这批知识迁到新本体这块是为了展示什么的，感觉也不需要吧」。 */
  const stale_notice=node('p','ledger-stale-notice');stale_notice.id='ledger-stale-notice';stale_notice.hidden=true;
  libraryPanel.before(intro,kpis,tabs);
  const entries=node('div','ledger-entry-actions');entries.setAttribute('aria-label','知识维护入口');
  for(const [kind,label] of [['entity','新增实体'],['relation','新增关系'],['attribute','新增属性']]){
    const button=node('button','admin-only',label);button.type='button';button.dataset.createRecord=kind;
    button.onclick=()=>openCreator(kind);entries.append(button);
  }
  const reviewButton=node('button','secondary','知识审核');reviewButton.type='button';reviewButton.id='ledger-open-reviews';
  reviewButton.onclick=()=>{if(window.TaskReview?.open)window.TaskReview.open();else status('知识审核组件未加载，请刷新页面重试。',true);};
  const modelCheck=node('button','secondary','检查本体归属');modelCheck.type='button';modelCheck.onclick=()=>loadGovernanceInfo('reclassify');
  entries.append(reviewButton,modelCheck);libraryPanel.before(entries);
  libraryPanel.after(stale_notice);
  libraryDetails.append(operations_section);
  const reviews_section=node('section','ledger-resolution-reviews');
  libraryDetails.append(reviews_section);
  const bar=document.createElement('div');bar.className='record-library-tools';
  bar.innerHTML='<label>查询当前范围全部记录<input id="record-search" placeholder="名称、正文、别名或记录编号"></label><button type="button" id="record-search-submit" class="secondary record-search-submit">查询</button>'
    +'<label class="record-kind-search">知识类别<select id="record-kind"><option value="">全部类别</option>'
    +Object.entries(kinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')+'</select></label>'
    +'<label class="record-ontology-search">本体版本<select id="record-ontology" aria-label="按本体版本筛选知识"></select></label>'
    +'<span id="record-count" aria-live="polite"></span>';
  host.before(bar);$('load-records').textContent='刷新记录';
  // 旧面板里的类别下拉与页签是同一件事的两种说法：选择页签时同步它，保证老逻辑（含测试）仍然成立。
  const syncKindSelect=()=>{const select=$('record-kind');if(select&&select.value!==view)select.value=view;};

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
    // 查重结果显示在「01 按名称查找」步骤下方，与三步流程同区，不再脱离标题
    const resultPanel=governance.querySelector('#resolve-result');
    if(resultPanel)searchStep.append(resultPanel);
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
    /* 当前页只能作为选择辅助；正式融合影响面由 EntityGovernance 的服务端预检给出。 */
    function impactOf(keepId,dropId){
      const relations=rows.filter(row=>row.kind==='relation'&&(row.subject_id===dropId||row.object_id===dropId));
      const aliases=(selectedRow(dropId)?.metadata?.aliases||[]).length;
      const support=assertions?supportCount(selectedRow(dropId)):null;
      return {relations:relations.length,aliases:aliases,support,
        selfRelations:relations.filter(row=>row.subject_id===dropId&&row.object_id===dropId).length};
    }
    function syncSelection(){
      paint(keepInput,'governance-keep');paint(dropInput,'governance-drop');
      const keep=selectedRow(keepInput.value),drop=selectedRow(dropInput.value),same=keepInput.value&&keepInput.value===dropInput.value;
      preview.classList.toggle('is-error',!!same);
      if(same){preview.innerHTML='<b>不能选择同一个实体</b><span>保留实体和待合并实体必须是两条不同记录。</span>';}
      else if(keep&&drop){
        const impact=impactOf(keepInput.value,dropInput.value);
        const supportText=impact.support===null?'':' · 转移 '+impact.support+' 条关联断言';
        const selfText=impact.selfRelations?' （其中 '+impact.selfRelations+' 条自关联的两端都会重定向）':'';
        preview.innerHTML=`<b>${esc(drop.text)}</b><span>将合并到</span><b>${esc(keep.text)}</b>`
          +`<small>影响面：转移 ${impact.relations} 条关系${selfText}${supportText} · 保留方沿用 ${impact.aliases?impact.aliases+' 个别名':'原名'}`
          +`（仅当前页 ${rows.length} 条的参考，完整影响与冲突在确认前由服务端预检）</small>`;
      }else{preview.innerHTML='<b>尚未选满两个实体</b><span>先在查重结果中分别点击“合并后保留”和“作为重复项合并”。</span>';}
      mergeButton.disabled=!keepInput.value||!dropInput.value||!!same||!confirm.checked;
      aliasButton.disabled=!keepInput.value||!aliasInput.value.trim();
      governance.querySelectorAll('[data-keep]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.keep===keepInput.value)));
      governance.querySelectorAll('[data-drop]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.drop===dropInput.value)));
    }
    function decorateResults(){
      const result=$('resolve-result'),candidates=[...result.querySelectorAll('.candidate')];if(!candidates.length)return;
      const statusLine=result.querySelector(':scope>p');if(statusLine)statusLine.textContent='已完成查重，请从结果中选择实体。';
      if(!result.querySelector('.governance-result-help'))result.insertAdjacentHTML('afterbegin','<div class="governance-result-help"><b>找到以下实体</b><span>每张卡片选择一种角色：保留其中一个，再把另一个作为重复项合并。如果这里只有一条，可先选择角色，再搜索另一个名称。</span></div>');
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
    /* 顶部「疑似重复」点进来直接可用：把服务端同名分组渲染成查重结果同款卡片，
       点一下就能选定"保留/合并"两个角色，不用再自己输入名称查一遍。 */
    window.ledgerRenderDuplicateGroups=()=>{
      if(!duplicates||!duplicates.length)return false;
      const cards=duplicates.flatMap(group=>group.members.map(member=>({...member,group:group.name})));
      cards.forEach(card=>wb.records.set(card.id,{...card,...(selectedRow(card.id)||{})}));
      $('resolve-result').innerHTML='<p>同名同类型实体分组（服务端口径：名称完全相同、本体类型相同）</p>'
        +cards.map(card=>`<div class="candidate"><strong>${esc(card.text)}</strong> ${typeHint(card.type)}`
          +`<small>${esc(card.id)} · v${card.version||1} · ${esc(ownership(card))}</small>`
          +`<button data-keep="${esc(card.id)}">设为保留实体</button> <button data-drop="${esc(card.id)}" class="secondary">设为合并实体</button></div>`).join('');
      decorateResults();return true;
    };
  }
  const nameOf=id=>{const row=wb.records.get(id)||rows.find(x=>x.id===id);return row?row.text:(id||'(未加载)');};
  const shortId=id=>{const text=String(id==null?'':id);return !text?'未知 ID':(text.length>16?`${text.slice(0,9)}…${text.slice(-4)}`:text);};
  const versionFields=()=>{
    const context=wb.versionContext||{};
    if(context.ontologyScope==='ids'&&Array.isArray(context.ontologyIds)&&context.ontologyIds.length){
      return {ontology_scope:'ids',ontology_ids:[...context.ontologyIds]};
    }
    if(context.ontologyScope==='unknown')return {ontology_scope:'unknown',ontology_ids:null};
    return {ontology_scope:'all',ontology_ids:null};
  };
  /* 本体归属只信 /ontologies 返回的权威 version；同一个 vN 可以有多次不可变发布，
     因此碰撞时必须带短 ID，后发布的同版本条目标作「标注修订」，不能按数组位置推成 vN+1。 */
  function buildOntology(versions){
    return RecordVersionContext.build(versions);
  }
  function releaseLabel(release){
    return RecordVersionContext.releaseLabel(release);
  }
  function renderOntologyFilter(){
    const select=$('record-ontology');if(!select)return;
    const fields=versionFields(),selected=fields.ontology_scope==='ids'
      ?`id:${fields.ontology_ids[0]}`:fields.ontology_scope;
    const option=(value,label)=>{const item=document.createElement('option');item.value=value;item.textContent=label;return item;};
    const items=[option('all','全部本体')];
    for(const release of ontology?.list||[])items.push(option(`id:${release.id}`,releaseLabel(release)));
    if(selected.startsWith('id:')&&!items.some(item=>item.value===selected)){
      items.push(option(selected,`已选本体 ${shortId(fields.ontology_ids[0])}（项目版本列表中不存在）`));
    }
    items.push(option('unknown','未知本体'));
    select.replaceChildren(...items);select.value=items.some(item=>item.value===selected)?selected:'all';
  }
  function ownershipSummary(row){
    if(!row||!row.ontology_id)return '未知本体';
    if(!ontology)return '本体归属读取中…';
    const found=ontology.byId.get(row.ontology_id);
    return found?`依据${releaseLabel(found)}`:'未知本体';
  }
  function ownership(row){
    if(!row)return '—';
    if(!row.ontology_id)return '未知本体';
    if(!ontology)return '本体归属读取中…';
    const found=ontology.byId.get(row.ontology_id);
    if(!found)return '未知本体';
    const date=found.created_at?new Date(found.created_at).toLocaleDateString('zh-CN'):'';
    return `${ownershipSummary(row)}${date?' · '+date:''}`;
  }
  function ownershipDetail(row){
    if(!row||!row.ontology_id)return '这条记录没有本体归属信息';
    const found=ontology&&ontology.byId.get(row.ontology_id);
    return found?`${releaseLabel(found)}：${found.created_at||''}`:'该本体版本不在这个项目的版本列表里';
  }
  renderOntologyFilter();
  /* 关联断言数：这条知识能追溯到几条断言（发现时冻结的原句）。
     “关联”只描述血缘，不等于每条都已审核通过，也不保证它对应当前修订的全部内容。
     两条血缘都要数，且要**去重**，否则同一句会被算两次：
       ① 发现候选：记录里的 discovery_candidate_id(s) === 断言的 id；
       ② 已归位：断言的 canonical_record_id === 这条记录的 id。 */
  function linkedAssertions(row){
    if(!assertions||!row||assertions.failures?.has(row.id))return null;
    const metadata=row.metadata||{},ids=new Set();
    if(metadata.discovery_candidate_id)ids.add(metadata.discovery_candidate_id);
    for(const id of metadata.discovery_candidate_ids||[])ids.add(id);
    const hits=new Set();
    for(const id of ids)if(assertions.byId.has(id))hits.add(id);
    for(const item of assertions.byCanonical.get(row.id)||[])hits.add(item.id);
    return [...hits].map(id=>assertions.byId.get(id)).filter(Boolean);
  }
  function supportCount(row){
    const linked=linkedAssertions(row);return linked===null?null:linked.length;
  }
  function supportBreakdown(row){
    const linked=linkedAssertions(row);if(linked===null)return null;
    const counts={accepted:0,pending:0,superseded:0,rejected:0,other:0};
    for(const item of linked){
      const key=item.status==='contradicting'?'pending':item.status;
      if(Object.hasOwn(counts,key))counts[key]++;else counts.other++;
    }
    return {total:linked.length,counts,incomplete:assertions.incomplete?.has(row.id)};
  }
  function renderKpis(){
    kpis.replaceChildren();
    const tile=(key,value,label,title,onclick)=>{
      const button=node('button','ledger-kpi'+(view===key?' is-active':''));
      button.type='button';button.dataset.ledgerKpi=key;
      button.append(node('b',null,String(value)),node('small',null,label));
      if(title)button.title=title;
      if(onclick)button.onclick=onclick;
      return button;
    };
    VIEW_ORDER.forEach(key=>kpis.append(tile(key,pageCounts.has(key)?pageCounts.get(key):'—',kinds[key]+'（服务端匹配总数）',VIEW_HELP[key]+' 点一下切到这个视角。',()=>setView(key))));
    const groups=duplicates===null?null:duplicates.length;
    const duplicateTile=tile('duplicates',groups===null?'—':groups,'组疑似重复',
      groups===null?'点击读取服务端同名同类型实体分组':(groups?'同名同类型的实体分组：点进去直接选保留哪一个':'没有同名同类型的实体'),
      groups===null?()=>loadGovernanceInfo('duplicates'):groups?()=>{
        const details=governance&&governance.closest('details');
        if(details)details.open=true;
        const rendered=window.ledgerRenderDuplicateGroups&&window.ledgerRenderDuplicateGroups();
        const target=governance||null;
        if(target)target.scrollIntoView({behavior:'smooth',block:'start'});
        if(!rendered)status('这些分组需要重新读取，点「刷新记录」再试。',true);
      }:null);
    duplicateTile.disabled=groups===0;
    kpis.append(duplicateTile);
    /* 结构待定（C1）：这类知识"已经进来了但概念还没建模"。
       它和「疑似重复」不是一回事 —— 查重是在**已有**知识里找同一实体，这个是在**本体**里找缺失概念。
       所以点一下不去查重面板，而是去本体建模层的待建模收件箱（那边才看得见"该建哪个概念"）。 */
    const pendingTerms=(pendingReport?.terms||[]).map(item=>item.term);
    // "读不到"和 0 是两件事：0 = 本体里全都建好了；读不到 = 这个数字没拿到（接口失败/项目没本体）。
    // 用 '读不到' 而不是 '—'：'-' 在数字位上会被当成"加载中"，而加载完了也不会再变。
    const pendingValue=pendingReport===undefined?'未读取':(pendingReport?pendingReport.pending_structure:'读不到');
    const pendingTile=tile('pending',pendingValue,'个概念待定',
      pendingReport===undefined?'点击读取服务端待建模概念':pendingReport?(pendingReport.pending_structure
        ?`抽出来的概念还没进本体：${pendingTerms.slice(0,3).join('、')}${pendingTerms.length>3?' 等':''}。核对可处理候选，或手动补充结构定义。`
        :'所有知识的类型都在本体里了'):'还没读到待建模概念（服务端没给这个数字）',
      pendingReport===undefined?()=>loadGovernanceInfo('pending'):pendingReport&&pendingReport.pending_structure?async()=>{
        const project=current;window.showTab?.('ontology-model');
        try{
          if(!window.OntologyIntake)throw Error('结构候选模块尚未就绪，请刷新页面。');
          await window.OntologyIntake.open({projectId:project,trigger:pendingTile});
        }catch(error){status(error.message,true);}
      }:null);
    pendingTile.disabled=pendingReport!==undefined&&!(pendingReport&&pendingReport.pending_structure);
    kpis.append(pendingTile);
    /* 「还挂在旧本体上」不再是一个 KPI 磁贴：一个数字点进去只有一块看不懂的面板，
       用户已经说过看不明白。改成表格下方一行真话（见 renderStaleNotice），只在真有时出现。 */
  }
  /* 结构待定（C1）收件箱数据：服务端给才显示，读不到就写"还没读到"，**不编造 0**。
     数字口径 = 待建模的概念个数（不是记录数）；每条记录自己的状态在 row.structure_pending 上。 */
  // 三态：undefined=还没读到（正在读）/ null=读不到（接口挂了）/ 对象=拿到了。
  // 「正在读」与「读不到」必须分开：不然加载中的一瞬会被读成"服务端没给这个数字"。
  let pendingReport;
  /* 「还有知识挂在旧本体版本上」的数字：三态 undefined=正在读 / null=读不到 / 对象=拿到了。
     台账只拿它渲染一行提示（不在这一页迁移），所以不需要勾选状态与干跑结果。 */
  let reclassifyPlan;
  function renderTabs(){
    tabs.replaceChildren();
    VIEW_ORDER.forEach(key=>{
      const button=node('button','ledger-tab'+(view===key?' is-active':''),kinds[key]);
      button.type='button';button.dataset.ledgerView=key;button.setAttribute('role','tab');
      button.setAttribute('aria-selected',String(view===key));
      button.onclick=()=>setView(key);
      tabs.append(button);
    });
    const help=node('span','ledger-tab-help',VIEW_HELP[view]);
    tabs.append(help);
  }
  function setView(key){
    if(!VIEW_ORDER.includes(key))return;
    view=key;syncKindSelect();renderTabs();return loadPage();
  }
  function actionsCell(row,index){
      const box=node('div','record-actions');
      const edit=node('button','admin-only','编辑');edit.dataset.editRow=String(index);edit.type='button';
      const history=node('button','secondary','修订历史');history.dataset.historyRow=String(index);history.type='button';
      box.append(edit,history);
      if(row.kind==='entity')for(const [kind,label] of [['relation','新增关系'],['attribute','新增属性']]){
        const create=node('button','secondary admin-only',label);create.type='button';create.dataset.createForEntity=kind;
        create.onclick=()=>openCreator(kind,{subject:row});box.append(create);
      }
      // D1「台账 → 图谱」：把这一个实体/关系画到图谱中心（复用检索命中那套跳转，不重新造定位逻辑）。
      // 属性也能定位：它挂在实体上，所以把「所属实体」画到图谱中心（属性本身不是图上的点）。
      const seed=row.kind==='attribute'?row.subject_id:(row.kind==='entity'?row.id:(row.kind==='relation'?row.subject_id:null));
      if(seed){
        const locate=node('button','secondary','在图谱定位');locate.type='button';
        locate.title='跳到「检索与交互图谱」并把这一个实体/关系画在画布中心（不改变台账的查询范围）';
        locate.dataset.locateGraph=String(seed);
        locate.onclick=()=>{
          window.suppressNextAutoGraph?.();        // 别让进检索页的自动补画把刚选中的实体冲掉
          window.showTab?.('search');
          Promise.resolve(window.selectEntityDetail?.(seed))
            .catch(error=>window.status?.(error?.message||String(error),true));
        };
        box.append(locate);
      }
      // 撤销：这条记录是某次操作（合并/删除/撤销）改过的，就能连那次操作一起撤掉 —— 接口早就有了，以前 UI 点不到。
      const operationId=row.metadata&&row.metadata._operation_id;
      if(operationId){
        const undo=node('button','secondary admin-only','撤销这次操作');undo.type='button';undo.dataset.undoOperation=operationId;
        undo.title='把这条记录连同那次操作影响到的其它记录恢复到操作之前（版本 +1，不覆盖历史）';
        undo.onclick=()=>undoOperation(operationId);
        box.append(undo);
      }
      return box;
    }
  /* 结构待定徽标（C1）：状态是**服务端推导**给的（row.structure_pending），前端不自己判断
     "类型在不在本体里" —— 否则台账、问答、收件箱会各说一套。
     已解除的也留一个灰徽标：不然"我当初标记的东西呢"没人答得上来。 */
  function pendingBadge(row){
    const state=row.structure_pending;if(!state)return null;
    const pending=state.state==='pending';
    const badge=node('span','ledger-pending-badge'+(pending?'':' is-cleared'),
      pending?'结构待定':'结构待定已解除');
    badge.title=pending
      ?`类型「${(state.terms||[]).join('、')||'（未命名）'}」在本体里还没有${state.via?.length?'（因为端点 '+(state.via||[]).map(nameOf).join('、')+' 的概念还没有）':''}。这条知识已收下：能查、能看原文、能被引用，但不作为正式证据；去「本体建模层」把概念建出来并发布，标记自动解除。`
      :`本体里已经有这些概念了（${(state.marked||[]).join('、')}），标记自动解除，不需要人工清理。`;
    badge.title+=' 当前治理状态按当前本体判断，不代表已迁移或通过绑定本体校验。';
    return badge;
  }
  function migrationBadge(row,release){
    if(!release||release.isCurrent)return null;
    let text='迁移状态未知',state=' is-unknown';
    if(reclassifyPlan===undefined){text='迁移状态未检查';state=' is-unknown';}
    else if(reclassifyPlan){
      const key=`${row.ontology_id||'none'}:${row.kind}:${row.type||''}`;
      const group=(reclassifyPlan.groups||[]).find(item=>item.key===key);
      if(group){text=group.migratable?'待迁移':'迁移阻塞';state=group.migratable?' is-pending':' is-blocked';}
    }
    const badge=node('span','ledger-migration-badge'+state,text);
    badge.title=text==='待迁移'?'本体建模层已给出迁移去向；迁移会生成新的知识修订，不覆盖当前修订。'
      :text==='迁移阻塞'?'新本体里没有可确认的去向，需要先在本体建模层补齐映射。'
        :text==='迁移状态未检查'?'点击台账上方“检查本体归属”读取服务端迁移计划。':'迁移计划不可用或没有覆盖这条知识，不能推断迁移状态。';
    return badge;
  }
  function cells(row){
    const ownershipCell=()=>{const cell=node('td');const label=node('b',null,ownership(row));label.title=ownershipDetail(row);cell.append(label);return cell;};
    const supportCell=()=>{const cell=node('td'),summary=supportBreakdown(row);
      if(summary===null){cell.textContent=assertions?'关联断言读取失败；可刷新重试':'读取中…';return cell;}
      const {total:count,counts,incomplete}=summary,strong=node('b',null,(incomplete?'已读 ':'')+(count?String(count):'0'));
      const statusText=[['accepted','已采纳'],['pending','待审核'],['superseded','已失效'],['rejected','已拒绝'],['other','其他状态']]
        .filter(([key])=>counts[key]).map(([key,label])=>`${label} ${counts[key]}`).join(' · ');
      cell.title=incomplete?'候选血缘未完整读取，不能据此判断是否没有来源。':count?'这些断言与该知识存在血缘关联；审核状态见下方。是否支持当前修订需在来源详情中核对。':'未找到关联断言，可能是人工写入或旧数据未归位。';
      cell.append(strong,node('small',null,'条关联断言'));
      if(statusText)cell.append(node('small',null,statusText));
      if(incomplete)cell.append(node('small','error','候选血缘未完整读取，计数不完整'));
      return cell;};
    const versionCell=()=>{const cell=node('td'),release=ontology?.byId.get(row.ontology_id);
      cell.append(node('b',null,'修订 r'+row.version),node('small',null,ownershipSummary(row)),
        node('small',null,row.recorded_at?new Date(row.recorded_at).toLocaleString():'—'));
      const migration=migrationBadge(row,release);if(migration)cell.append(migration);return cell;};
    if(view==='chunk'){
      const text=node('td');const excerpt=node('div','record-excerpt',String(row.text||'').slice(0,200));
      const detail=document.createElement('details');detail.innerText='';detail.append(node('summary',null,'编号与有效期'),node('small',null,`${row.id}\n${row.valid_from||'未知'} → ${row.valid_until||'未知'}`));
      text.append(excerpt,detail);
      const source=node('td');const sourceRow=wb.records.get(row.source_id)||rows.find(item=>item.id===row.source_id);
      source.append(node('b',null,sourceRow?(sourceRow.metadata?.title||sourceRow.text||row.source_id):(row.source_id||'—')));
      return [text,source,versionCell(),supportCell()];
    }
    /* 属性视角：一条属性记录 = 某个实体 + 属性名 + 值 + 数据类型（不是实体、也不是关系）。
       列也不同（第二列是「所属实体」而不是「本体类型」），所以在这里单独一支。 */
    if(view==='attribute'){
      const first=node('td');
      const shown=row.value===undefined?'':(typeof row.value==='string'?row.value:JSON.stringify(row.value));
      const excerpt=node('div','record-excerpt');
      excerpt.innerHTML=`<b>${typeHint(row.type)||esc(row.type||'（未命名属性）')}</b> = ${esc(String(shown).slice(0,160))}`;
      const detail=document.createElement('details');
      detail.append(node('summary',null,'编号、数据类型与有效期'),
        node('small',null,`${row.id}\n数据类型 ${row.datatype||'—'} · ${row.valid_from||'未知'} → ${row.valid_until||'未知'}`));
      first.append(excerpt,detail);
      const badge=pendingBadge(row);if(badge)first.append(badge);
      const subject=node('td');
      subject.append(node('b',null,nameOf(row.subject_id)),node('small',null,'所属实体'));
      return [first,subject,ownershipCell(),versionCell(),supportCell()];
    }
    const first=node('td');
    if(view==='relation'){
      const excerpt=node('div','record-excerpt');
      excerpt.innerHTML=`<b>${esc(nameOf(row.subject_id))}</b> —[${typeHint(row.type)}]→ <b>${esc(nameOf(row.object_id))}</b>`;
      const detail=document.createElement('details');detail.append(node('summary',null,'原文上下文'),node('small',null,String(row.text||'')));
      first.append(excerpt,detail);
    }else{
      const excerpt=node('div','record-excerpt',String(row.text||'').slice(0,160));
      const detail=document.createElement('details');detail.append(node('summary',null,'编号与有效期'),node('small',null,`${row.id}\n${row.valid_from||'未知'} → ${row.valid_until||'未知'}`));
      first.append(excerpt,detail);
    }
    const badge=pendingBadge(row);if(badge)first.append(badge);
    const typeCell=node('td'),typeLabel=node('small');
    // The shared typeHint helper returns HTML with escaped labels and identifiers.
    typeLabel.innerHTML=typeHint(row.type)||'—';
    typeCell.append(node('span','record-kind-tag',kinds[row.kind]||row.kind),typeLabel);
    return [first,typeCell,ownershipCell(),versionCell(),supportCell()];
  }
  function render(){
    const state=pager?.snapshot()||{},pages=Number.isFinite(total)?Math.max(1,Math.ceil(total/50)):'—';
    $('record-count').textContent=`服务端匹配 ${total??'未知'} 条 · 当前页 ${rows.length} 条`;
    $('record-section-meta').textContent=`${kinds[view]}视角 · 匹配 ${total??'未知'} 条 · 第 ${page+1} 页`;
    renderKpis();
    if(!rows.length){host.innerHTML='<div class="record-empty">当前范围没有匹配记录<p>查询已在服务端覆盖当前全部范围；可切换视角或调整条件。</p></div>';return;}
    const visible=rows;
    const head=COLUMNS[view].map(label=>`<th>${label}</th>`).join('');
    host.innerHTML='<div class="table-scroll"><table class="record-library ledger-'+view+'"><thead><tr>'+head+'</tr></thead><tbody></tbody></table></div>'
      +'<div class="record-pages"><button id="records-prev" class="secondary">上一页</button><span>'
      +`${page+1} / ${pages} 页 · 每页最多 50 条`+'</span><button id="records-next" class="secondary">下一页</button></div>';
    const body=host.querySelector('tbody');
    visible.forEach((row,index)=>{
          const tr=node('tr');tr.dataset.recordId=row.id;   // 给「在台账查看/定位」一个稳定锚点（CSS.escape 兜底 id 里的特殊字符）
          cells(row).forEach(cell=>tr.append(cell));
          const actions=node('td');actions.append(actionsCell(row,index));tr.append(actions);
          body.append(tr);
        });
    host.querySelectorAll('[data-edit-row]').forEach(b=>b.onclick=()=>editRecord(visible[Number(b.dataset.editRow)]));
    host.querySelectorAll('[data-history-row]').forEach(b=>b.onclick=()=>historyFor(visible[Number(b.dataset.historyRow)]));
    $('records-prev').disabled=loading||page===0;$('records-next').disabled=loading||!state.next_cursor;
    $('records-prev').onclick=()=>loadPage('previous');$('records-next').onclick=()=>loadPage('next');
  }
  /* 可撤销的操作：合并 / 删除这类"一批记录一起变"的动作，一次撤销整批回滚。
     空态要把话说清楚 —— 用户看到"没有东西"时必须知道是"本来就没有"还是"读取失败"。 */
  function renderOperations(){
    operations_section.replaceChildren();
    const head=node('div','ledger-operations-head');
    head.append(node('h3',null,'可撤销的操作'),node('small',null,'合并 / 删除这类会一次改多条记录的动作：'
      +`撤销 = 整批恢复到操作之前（版本 +1，历史仍可查）${operations.length?'；共 '+operations.length+' 条，最近的在最上面':''}`));
    operations_section.append(head);
    const refresh=node('button','secondary','读取操作历史');refresh.type='button';refresh.onclick=loadOperations;head.append(refresh);
    if(!operationsLoaded){operations_section.append(node('p','ledger-empty','操作历史按需读取，包含可撤销的合并、删除和恢复。'));return;}
    if(!operations.length){
      operations_section.append(node('p','ledger-empty','这个项目还没有可撤销的合并或删除操作。做过一次合并或删除之后，这里会出现「撤销」按钮。'));
      return;
    }
    operations.forEach(operation=>{
      const row=node('div','ledger-operation'+(operation.metadata?.operation==='undo'?' is-reversal':''));
      const kind=operation.metadata?.operation||'操作';
      const label={'merge':'合并实体','delete':'软删除','undo':'撤销过的一次操作','revise':'修订',
        'reclassify':'迁到新本体'}[kind]||kind;
      const note=kind==='undo'?'再撤销 = 把这次撤销回退（等于重新做一次被撤销的操作）':'';
      const info=node('div');
      info.append(node('b',null,label),
        node('small',null,`${operation.recorded_at?new Date(operation.recorded_at).toLocaleString():'—'} · 影响 ${(operation.metadata?.before||[]).length} 条记录${note?' · '+note:''}`));
      const undo=node('button','secondary admin-only','撤销');undo.type='button';
      undo.dataset.undoOperation=String(operation.metadata?.operation_id||'');
      undo.title='把这次操作影响到的记录整批恢复到操作之前（版本 +1，历史仍可查）';
      undo.disabled=!operation.metadata?.operation_id;
      undo.onclick=()=>undoOperation(operation.metadata?.operation_id);
      row.append(info,undo);
      operations_section.append(row);
    });
  }
  async function undoOperation(operationId){
    if(!operationId)return;
    if(!window.Auth?.isAdmin?.()){status('只有管理员可以撤销治理操作',true);return;}
    if(!window.confirm('撤销这次操作会把受影响的记录恢复到操作之前（产生新版本，历史不丢）。继续？'))return;
    const p=current,serial=request;
    try{
      const result=await api('/api/projects/'+encodeURIComponent(p)+'/operations/'+encodeURIComponent(operationId)+'/undo',{});
      if(p!==current||serial!==request)return;
      status(`已撤销：恢复 ${result.restored} 条记录到这次操作之前。`);
      await load();if(p===current)await loadOperations();
    }catch(error){if(p===current&&serial===request)status('撤销失败：'+error.message,true);}
  }
  /* 「还有知识挂在旧本体版本上」：只在真有时给**一行**真话，并且说清去哪儿处理。
     三态照旧：读不到就什么都不说（不编数字），0 也不显示（那本来就是最正常的情况）。
     为什么台账不在这里迁：迁移是**本体版本**的事，触发点是「本体建模层」发布新版本的那一刻
     （那边发完就告诉你还差多少条、给一个可撤销的迁移按钮）。台账这一页只做「具体的东西」的
     查 / 改 / 并重复项 / 撤销 —— 用户原话：「这块是为了展示什么的，感觉也不需要吧」。 */
  function renderStaleNotice(){
    if(!stale_notice)return;
    const plan=reclassifyPlan;
    if(!plan||!plan.stale_records){stale_notice.hidden=true;stale_notice.replaceChildren();return;}
    const migratable=plan.migratable_records||0,unmapped=plan.unmapped_records||0;
    stale_notice.hidden=false;
    stale_notice.replaceChildren(
      node('b',null,`另有 ${plan.stale_records} 条知识还挂在旧本体版本上`),
      node('span',null,`（${migratable} 条能搬到 ${plan.current_version||'当前版本'}${unmapped?`，${unmapped} 条在新本体里没有去处`:''}）。`
        +'它们能查、能用，只是类型还是旧结构。要不要搬，去「本体建模层」发布新版本那一步处理（迁移可整批撤销）。'));
  }

  const readCriteria=()=>({...scope(),...versionFields(),kinds:[view],query:$('record-search').value.trim()});
  function guard(serial,p,stamp,ticket){return visible()&&serial===request&&p===current&&stamp===JSON.stringify(readCriteria())
    &&(!ticket||window.WorkspaceContext.accepts(ticket,p,'records'));}
  async function pageAssertions(p,visible,accepts){
    const items=[],failures=new Set(),incomplete=new Set();
    for(let index=0;index<visible.length&&accepts();index+=100){
      const batch=visible.slice(index,index+100);
      try{
        const result=await api('/api/projects/'+encodeURIComponent(p)+'/assertions/batch',{record_ids:batch.map(row=>row.id)});
        if(!accepts())break;
        for(const row of batch){
          const found=result.assertions_by_record?.[row.id]||[];items.push(...found);
          const metadata=row.metadata||{},candidateIds=[metadata.discovery_candidate_id,...(metadata.discovery_candidate_ids||[])].filter(Boolean);
          if(candidateIds.some(id=>!found.some(item=>item.id===id))||result.truncated_record_ids?.includes(row.id))incomplete.add(row.id);
        }
      }catch{for(const row of batch)failures.add(row.id);}
    }
    return {byId:new Map(items.map(item=>[item.id,item])),failures,incomplete,
      byCanonical:items.reduce((acc,item)=>{if(item.canonical_record_id){if(!acc.has(item.canonical_record_id))acc.set(item.canonical_record_id,[]);acc.get(item.canonical_record_id).push(item);}return acc;},new Map())};
  }
  async function pageReferences(p,visible,criteria,accepts){
    const ids=[...new Set(visible.flatMap(row=>[row.subject_id,row.object_id,row.kind==='chunk'?row.source_id:null]).filter(Boolean))],found=[];
    for(let index=0;index<ids.length&&accepts();index+=100){try{
      const result=await api('/api/projects/'+encodeURIComponent(p)+'/records/page',{
        valid_at:criteria.valid_at||null,known_at:criteria.known_at||null,include_unknown:true,
        record_ids:ids.slice(index,index+100),limit:100,include_total:false,
      });
      if(!accepts())break;
      found.push(...(result.records||[]).filter(record=>!record.metadata?._deleted));
    }catch{/* A missing reference remains an explicit ID in the table. */}}
    return found;
  }
  async function loadPage(direction){
    if(!visible())return false;
    clearTimeout(searchTimer);const serial=++request,p=current,criteria=readCriteria(),stamp=JSON.stringify(criteria),ticket=window.WorkspaceContext?.ticket(p,'records');
    if(!p){pager?.invalidate();rows=[];total=0;$('record-section-meta').textContent='请先选择项目';host.innerHTML='<div class="record-empty">请先在左侧选择项目</div>';return;}
    if(!window.RecordCreate?.createPager){host.textContent='记录分页组件未加载，请刷新页面重试。';return false;}
    pager??=window.RecordCreate.createPager(body=>api('/api/projects/'+encodeURIComponent(current)+'/records/page',body));
    loading=true;host.setAttribute('aria-busy','true');$('record-section-meta').textContent='正在查询当前完整范围…';
    try{
      if(!direction){pageCounts.clear();rows=[];assertions=null;host.innerHTML='<div class="record-empty" role="status">正在读取服务端分页…</div>';}
      const result=await (direction==='next'?pager.next():direction==='previous'?pager.previous():pager.load(criteria));
      if(!guard(serial,p,stamp,ticket)||!result)return;
      rows=result.records;total=result.total;page=result.page;pageCounts.set(view,total);assertions=null;
      // Keep only bounded visible records in the ledger cache; other readers may own node caches separately.
      wb.records=new Map(rows.map(row=>[row.id,row]));loading=false;renderTabs();syncKindSelect();render();
      const accepts=()=>guard(serial,p,stamp,ticket);
      if(!accepts())return;
      const versionsRequest=!ontology?api('/api/projects/'+encodeURIComponent(p)+'/ontologies',undefined,'GET').catch(()=>null):Promise.resolve(null);
      const countsRequest=direction?Promise.resolve([]):Promise.all(VIEW_ORDER.filter(kind=>kind!==view).map(async kind=>{
        try{const count=await api('/api/projects/'+encodeURIComponent(p)+'/records/page',{...criteria,kinds:[kind],cursor:null,limit:1});return [kind,count.total];}catch{return [kind,null];}
      }));
      const [versions,lineage,counts,references]=await Promise.all([versionsRequest,pageAssertions(p,rows,accepts),countsRequest,pageReferences(p,rows,criteria,accepts)]);
      if(!guard(serial,p,stamp,ticket))return;
      if(versions)ontology=buildOntology(versions.versions||[]);
      assertions=lineage;for(const [kind,count] of counts)if(Number.isFinite(count))pageCounts.set(kind,count);
      for(const record of references)wb.records.set(record.id,record);
      renderOntologyFilter();render();
      if(pendingLedgerFocus){const id=pendingLedgerFocus;pendingLedgerFocus=null;locateLedgerRow(id);}
      return true;
    }catch(error){if(guard(serial,p,stamp,ticket)){host.textContent='读取失败：'+error.message+'；可点击“刷新记录”重试。';status(error.message,true);}return false;}
    finally{if(serial===request){loading=false;host.setAttribute('aria-busy','false');}}
  }
  async function load(){
    if(!visible())return false;
    pager?.invalidate();ontology=null;operations=[];operationsLoaded=false;duplicates=null;pendingReport=undefined;reclassifyPlan=undefined;
    reviews_section.replaceChildren();if(window.EntityGovernance)void window.EntityGovernance.mount(reviews_section,{reload:load});
    renderOperations();renderStaleNotice();if($('operations'))$('operations').replaceChildren();return loadPage();
  }
  async function loadOperations(){
    const p=current,serial=request;try{
      const result=await api('/api/projects/'+encodeURIComponent(p)+'/operations',undefined,'GET');
      if(p!==current||serial!==request)return;
      operations=(result.operations||[]).filter(item=>item.metadata?.operation).sort((a,b)=>String(b.metadata.created_at||b.recorded_at||'').localeCompare(String(a.metadata.created_at||a.recorded_at||'')));
      operationsLoaded=true;renderOperations();
    }catch(error){if(p===current&&serial===request)status('操作历史读取失败：'+error.message,true);}
  }
  async function loadGovernanceInfo(kind){
    const p=current,serial=request,path={duplicates:'duplicate-groups',pending:'structure-pending',reclassify:'reclassify'}[kind];
    try{const data=await api('/api/projects/'+encodeURIComponent(p)+'/'+path,undefined,'GET');if(p!==current||serial!==request)return;
      if(kind==='duplicates'){duplicates=data.groups||[];renderKpis();if(duplicates.length){const details=governance?.closest('details');if(details)details.open=true;window.ledgerRenderDuplicateGroups?.();governance?.scrollIntoView({behavior:'smooth',block:'start'});}else status('服务端没有找到同名同类型疑似重复组。');}
      if(kind==='pending'){pendingReport=typeof data.pending_structure==='number'?data:null;renderKpis();}
      if(kind==='reclassify'){reclassifyPlan=typeof data.stale_records==='number'?data:null;renderStaleNotice();render();if(!data.stale_records)status('当前没有待迁移的旧本体归属记录。');}
    }catch(error){
      if(p===current&&serial===request){
        if(kind==='pending'){pendingReport=null;renderKpis();}
        status('治理信息读取失败：'+error.message,true);
      }
    }
  }
  window.focusLedgerRecord=async record=>{
    const id=typeof record==='string'?record:record?.id;if(!id)return;
    const p=current;let kind=typeof record==='string'?null:record.kind;
    if(!kind){try{const row=await api('/api/projects/'+encodeURIComponent(p)+'/records/'+encodeURIComponent(id),undefined,'GET');if(p!==current)return;kind=row.kind;}catch(error){status('定位失败：'+error.message,true);return;}}
    if(!VIEW_ORDER.includes(kind))return;view=kind;syncKindSelect();renderTabs();pendingLedgerFocus=id;
    $('record-search').value=id;document.querySelector('[data-tab="records"]')?.click();
  };
  function locateLedgerRow(id){
    if(!rows.some(row=>row.id===id)){status('当前台账范围内未找到这条记录，请检查业务时间、本体和筛选条件。',true);return;}
    requestAnimationFrame(()=>requestAnimationFrame(()=>{const tr=host.querySelector('[data-record-id="'+CSS.escape(id)+'"]');if(!tr)return;
      tr.scrollIntoView({block:'center',behavior:'smooth'});tr.classList.add('ledger-row-located');window.setTimeout(()=>tr.classList.remove('ledger-row-located'),1800);}));
  }
  $('record-search-submit').onclick=()=>loadPage();
  $('record-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>void loadPage(),350);};
  $('record-search').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();void loadPage();}};
  $('record-kind').onchange=()=>setView($('record-kind').value);
  $('record-ontology').onchange=()=>{
    const value=$('record-ontology').value;
    if(value==='unknown')wb.setVersionContext({ontologyScope:'unknown',ontologyIds:null,ontologyVersion:null,source:'knowledge-ledger'});
    else if(value.startsWith('id:')){
      const id=value.slice(3),release=ontology?.byId.get(id);
      wb.setVersionContext({ontologyScope:'ids',ontologyIds:[id],ontologyVersion:release?.version??null,source:'knowledge-ledger'});
    }else wb.setVersionContext({ontologyScope:'all',ontologyIds:null,ontologyVersion:null,source:'knowledge-ledger'});
  };
  document.addEventListener('version-context:changed',()=>{
    renderOntologyFilter();
    if(!tab.classList.contains('hidden'))load();
  });
  $('load-records').onclick=load;
  /* 「操作历史」按钮（在合并步骤里）不再摆第二份列表：合并/删除之后要撤销，去台账那一节。
     旧实现把同样的东西渲染进 #operations（一个隐藏节点），于是同一个动作有了两个入口 —— 正是用户说的"环节冲突"。 */
  $('load-operations').onclick=async()=>{await loadOperations();operations_section.scrollIntoView({behavior:'smooth',block:'center'});};
  document.querySelector('[data-tab="records"]').addEventListener('click',load);
  document.addEventListener('workspace:page-changed',()=>{
    // Every visit has its own request generation, including a leave-and-return
    // before the old response finishes or navigation without a menu click.
    request++;pager?.invalidate();clearTimeout(searchTimer);loading=false;host.setAttribute('aria-busy','false');
  });
  $('project').addEventListener('change',()=>{request++;pager?.invalidate();clearTimeout(searchTimer);rows=[];total=0;ontology=null;assertions=null;operations=[];duplicates=null;pendingLedgerFocus=null;reclassifyPlan=undefined;$('record-search').value='';$('record-kind').value='';if(!tab.classList.contains('hidden'))load();});
  document.addEventListener('records:created',event=>{if(event.detail?.projectId===current&&!tab.classList.contains('hidden'))void loadPage();});
  document.addEventListener('workspace:scope-changed',event=>{
    if(event.detail?.page==='records'&&!tab.classList.contains('hidden'))void load();
  });
})();
