/* List-first source browsing; original detail nodes live in an owned drawer. */
(() => {
  const page=$('tab-sources'),list=$('source-list'),title=$('source-title'),body=$('source-body'),refresh=$('load-sources');
  const layout=page.querySelector('.source-layout'),article=layout.querySelector('article');
  const summary=document.createElement('p');summary.className='source-summary subtle';summary.setAttribute('aria-live','polite');layout.before(summary);
  const meta=document.createElement('div');meta.id='source-meta';meta.className='source-meta';title.after(meta);
  const empty=document.createElement('div');empty.className='source-empty';empty.setAttribute('aria-live','polite');empty.hidden=true;
  empty.innerHTML='<div class="source-empty-icon" aria-hidden="true">📄</div><h3></h3><p></p>';layout.append(empty);
  refresh.textContent='刷新';refresh.classList.add('secondary');
  let serial=0,detailSerial=0,citationSerial=0,selected=null,activeTab='text',loadedProject=null,loadedScope=null,loadedState=null,currentFilter='all';
  const allDocuments=new Map(),directoryRows=new Map();
  let inFlight=null,nextCursor=null,total=0,directoryCounts=null,appliedQuery='',loadedQuery=null,composing=false;
  let retrievalMode='keyword',loadedMode=null,pendingModeChange=false,loadedRelevance=null;
  let searchSteps=[],loadedSteps=null,addingStep=false,searchSucceeded=false,searchLoading=false,boundedBySemantic=false;
  const stepsStamp=()=>JSON.stringify(searchSteps);
  const rankedMode=value=>['semantic','hybrid'].includes(value);
  const modeLabel=value=>({keyword:'关键词',semantic:'向量检索',hybrid:'混合检索'})[value];
  const boundedSearch=()=>boundedBySemantic||semanticSearch()||searchSteps.some(step=>rankedMode(step.retrieval_mode));
  const semanticSearch=()=>rankedMode(retrievalMode)&&Boolean(appliedQuery);
  const searchForm=document.createElement('form');searchForm.id='source-search-form';searchForm.className='source-search';searchForm.setAttribute('role','search');
  searchForm.innerHTML='<label for="source-search-input" class="sr-only">搜索资料</label><label for="source-search-mode" class="sr-only">搜索方式</label><div class="source-search__row"><span class="source-search__seg" role="radiogroup" aria-label="搜索方式切换"><button type="button" role="radio" data-mode="keyword" aria-checked="true">关键词</button><button type="button" role="radio" data-mode="semantic" aria-checked="false">向量检索</button><button type="button" role="radio" data-mode="hybrid" aria-checked="false">混合检索</button></span><select id="source-search-mode" class="source-search__mode-select" tabindex="-1"><option value="keyword">关键词</option><option value="semantic">向量检索</option><option value="hybrid">混合检索</option></select><div class="source-search__field"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg><input id="source-search-input" type="search" maxlength="2000" placeholder="搜索名称、文件名或正文" autocomplete="off"></div><button type="submit">搜索</button><button id="source-search-clear" type="button" class="secondary">清空</button></div>';
  layout.before(searchForm);
  const searchInput=searchForm.querySelector('input');
  const searchMode=$('source-search-mode');searchMode.value=retrievalMode;
  const seg=searchForm.querySelector('.source-search__seg');
  const segButtons=[...seg.querySelectorAll('button')];
  function syncSeg(){segButtons.forEach(btn=>{const on=btn.dataset.mode===searchMode.value;btn.classList.toggle('active',on);btn.setAttribute('aria-checked',String(on));});}
  segButtons.forEach(btn=>btn.addEventListener('click',()=>{
    if(composing){pendingModeChange=true;searchMode.value=btn.dataset.mode;syncSeg();return;}
    if(addingStep&&!searchInput.value.trim()){searchMode.value=btn.dataset.mode;syncSeg();return;}
    searchMode.value=btn.dataset.mode;syncSeg();searchMode.dispatchEvent(new Event('change',{bubbles:true}));
  }));
  syncSeg();
  const stepsUI=document.createElement('div');stepsUI.id='source-search-steps';stepsUI.className='source-search__steps';stepsUI.setAttribute('aria-label','检索条件链');searchForm.append(stepsUI);
  const searchActions=document.createElement('div');searchActions.className='source-search__actions';searchForm.append(searchActions);
  const refine=document.createElement('button');refine.id='source-search-refine';refine.type='button';refine.className='secondary';refine.textContent='在结果中继续检索';searchActions.append(refine);
  const back=document.createElement('button');back.id='source-search-back';back.type='button';back.className='secondary';searchActions.append(back);
  function renderSearchSteps(){
    stepsUI.replaceChildren();syncSeg();
    const chain=[...searchSteps,...(appliedQuery?[{query:appliedQuery,retrieval_mode:retrievalMode,...(loadedRelevance?JSON.parse(loadedRelevance):window.SearchRelevance?.read('source'))}]:[])];
    stepsUI.hidden=!chain.length&&!addingStep;
    searchActions.hidden=!chain.length&&!addingStep;
    refine.disabled=addingStep||searchLoading||!searchSucceeded||!appliedQuery||!allDocuments.size||chain.length>=8;
    back.textContent=addingStep?'取消继续检索':'退回上一步';back.disabled=!addingStep&&!chain.length;
    if(!chain.length&&!addingStep)return;
    const head=document.createElement('div');head.className='source-search__head';
    const headTitle=document.createElement('span');headTitle.className='source-search__head-title';
    headTitle.textContent=addingStep?'正在追加条件：在上一步命中的全部正文中检索':'检索步骤';
    const progress=document.createElement('span');progress.className='source-search__progress';
    const bar=document.createElement('span');bar.className='source-search__bar';
    const pct=Math.min(100,chain.length/8*100)+'%';const barFill=document.createElement('i');barFill.setAttribute('style','width:'+pct);bar.append(barFill);
    const count=document.createElement('b');count.textContent=chain.length+' / 8 步';
    progress.append(bar,count);head.append(headTitle,progress);stepsUI.append(head);
    const trail=document.createElement('div');trail.className='source-search__chain';
    chain.forEach((step,index)=>{
      const node=document.createElement('div');node.className='source-search__step-node';
      const card=document.createElement('div');card.className='source-search__step'+(index===chain.length-1?' source-search__step--current':'');
      const num=document.createElement('span');num.className='source-search__num';num.textContent=index+1;
      const badge=document.createElement('span');badge.className='source-search__badge'+(rankedMode(step.retrieval_mode)?' source-search__badge--semantic':'');
      badge.textContent=modeLabel(step.retrieval_mode);
      const term=document.createElement('span');term.className='source-search__term';term.textContent=step.query;
      const remove=document.createElement('button');remove.type='button';remove.className='source-search__remove';remove.textContent='×';remove.setAttribute('aria-label','删除第 '+(index+1)+' 步：'+step.query);
      remove.onclick=()=>replaySteps(chain.filter((_,position)=>position!==index));
      card.append(num,badge,term,remove);node.append(card);
      if(index<chain.length-1){const arrow=document.createElement('span');arrow.className='source-search__arrow';arrow.setAttribute('aria-hidden','true');arrow.textContent='›';node.append(arrow);}
      trail.append(node);
    });
    stepsUI.append(trail);
    searchActions.replaceChildren(refine,back);
    stepsUI.append(searchActions);
    const hint=document.createElement('span');hint.className='source-search__hint subtle';hint.textContent=addingStep?'正在继续推进：最多8步。':'最多8步';
    stepsUI.append(hint);
  }
  function replaySteps(chain){
    addingStep=false;const last=chain.at(-1);searchSteps=chain.slice(0,-1);appliedQuery=last?.query||'';
    if(last)window.SearchRelevance?.write('source',last);
    if(last)retrievalMode=last.retrieval_mode;searchMode.value=retrievalMode;searchInput.value=appliedQuery;return load();
  }
  refine.onclick=()=>{if(refine.disabled)return;addingStep=true;searchInput.value='';renderSearchSteps();searchInput.focus();};
  back.onclick=()=>{if(addingStep){addingStep=false;searchInput.value=appliedQuery;searchMode.value=retrievalMode;renderSearchSteps();return;}return replaySteps(searchSteps);};
  searchMode.addEventListener('change',()=>{
    if(composing){pendingModeChange=true;return;}
    if(addingStep&&!searchInput.value.trim()){syncSeg();return;}
    return applySearch();
  });
  document.addEventListener('sources:relevance-changed',()=>{if(!addingStep)return load();});
  searchInput.addEventListener('compositionstart',()=>{composing=true;});
  searchInput.addEventListener('compositionend',()=>{composing=false;if(pendingModeChange){pendingModeChange=false;if(addingStep&&!searchInput.value.trim())return;return applySearch();}});
  searchInput.addEventListener('keydown',event=>{
    if(event.key!=='Enter')return;
    event.preventDefault();if(!composing&&!event.isComposing&&event.keyCode!==229)applySearch();
  });
  searchForm.addEventListener('submit',event=>{event.preventDefault();if(!composing)applySearch();});
  function applySearch(){
    const term=searchInput.value.trim();
    if(addingStep){if(!term)return;searchSteps=[...searchSteps,{query:appliedQuery,retrieval_mode:loadedMode||retrievalMode,...(loadedRelevance?JSON.parse(loadedRelevance):window.SearchRelevance?.read('source'))}];addingStep=false;}
    if(!term&&searchSteps.length){searchInput.value=appliedQuery;searchMode.value=retrievalMode;renderSearchSteps();return;}
    retrievalMode=rankedMode(searchMode.value)?searchMode.value:'keyword';appliedQuery=term;searchInput.value=appliedQuery;return load();
  }
  function clearSearch(){retrievalMode=rankedMode(searchMode.value)?searchMode.value:'keyword';searchSteps=[];addingStep=false;boundedBySemantic=false;appliedQuery='';searchInput.value='';}
  function resetSearch(){clearSearch();retrievalMode='keyword';searchMode.value='keyword';pendingModeChange=false;}
  function clearAndLoad(){clearSearch();searchInput.focus();return load();}
  $('source-search-clear').onclick=clearAndLoad;
  const emptyClear=document.createElement('button');emptyClear.id='source-empty-clear-search';emptyClear.type='button';emptyClear.className='secondary';emptyClear.textContent='清空搜索';emptyClear.hidden=true;emptyClear.onclick=clearAndLoad;empty.append(emptyClear);
  let richContent=null,detailContent=null,readingControls=null,readingMode='rich',readingNote=null;
  const textReads=new Map();
  const loadMore=document.createElement('button');loadMore.id='source-load-more';loadMore.type='button';loadMore.className='secondary';loadMore.textContent='加载更多资料';loadMore.hidden=true;layout.after(loadMore);
  const visible=()=>!page.classList.contains('hidden');
  const canWrite=()=>Boolean(window.Auth?.isAdmin?.());
  const scopeStamp=()=>{try{return JSON.stringify(scope());}catch{return null;}};
  const drawer=window.WorkspaceDrawers.create({
    id:'source-detail-drawer',title,content:article,onClose:clearDetail,
  });

  function clearDetail(){
    clearReading();detailContent?.dispose();detailContent=null;readingControls=null;readingNote=null;
    detailSerial++;selected=null;activeTab='text';title.textContent='';meta.replaceChildren();body.textContent='';
    list.querySelectorAll('.source-item').forEach(button=>button.setAttribute('aria-current','false'));
  }
  function clearReading(){richContent?.dispose();richContent=null;body.classList.remove('source-rich-content','source-parsed-content');}
  function closeDetail(){drawer.close({restoreFocus:false});clearDetail();}
  function message(heading,detail){
    empty.querySelector('h3').textContent=heading;empty.querySelector('p').textContent=detail;
    emptyClear.hidden=!appliedQuery;empty.hidden=false;layout.classList.add('is-empty');list.replaceChildren();
  }
  const FORMAT_LABELS={pdf:'PDF',docx:'Word',doc:'Word',md:'Markdown',markdown:'Markdown',txt:'纯文本',text:'纯文本',html:'HTML',htm:'HTML'};
  const formatLabel=format=>FORMAT_LABELS[String(format||'').toLowerCase()]||String(format||'').toUpperCase()||'资料正文';
  const sizeLabel=size=>typeof size!=='number'||!Number.isFinite(size)||size<0?'':size<1024?size+' B':size>=1048576?(size/1048576).toFixed(1)+' MB':(size/1024).toFixed(1)+' KB';
  const documentTitle=doc=>doc.metadata?.title||doc.metadata?.source_file||'资料 #'+String(doc.id).slice(0,8);
  function buildState(doc){
    const state=doc.metadata?.status;
    if(state==='failed')return {label:'知识构建失败',className:'failed',detail:'原文已安全保留，但实体、关系和片段没有写入知识图谱。可查看后台任务定位原因后重新提交。'};
    if(state==='processing')return {label:'等待 / 处理中',className:'processing',detail:'原文已保存，知识仍在构建中。'};
    if(state==='completed'||state==='ready'||state==='succeeded')return {label:'处理完成',className:'ready',detail:'处理任务已完成；检索与派生索引状态应分别核验。'};
    return {label:'状态待核验',className:'ready',detail:'已保存原文；未获得完整处理或索引就绪状态。'};
  }
  function platformLabel(doc){
    const platforms=doc.metadata?.applicability?.platforms;
    if(platforms?.mode==='all')return '全部平台';
    if(platforms?.mode==='specific'&&Array.isArray(platforms.values))return platforms.values.join('、');
    return '';
  }
  function renderMeta(doc,state){
    const metadata=doc.metadata||{},parser=metadata.document_parser||'';
    const parserLabel=parser==='builtin-text'?'纯文本读取':['docling','semantica-docling'].includes(parser)?'文档转换解析':parser;
    const stats=[sizeLabel(metadata.source_size_bytes),parserLabel,metadata.page_count?metadata.page_count+' 页':''].filter(Boolean);
    const missing=!metadata.source_file&&!metadata.source_format&&!stats.length;
    const effective=doc.valid_from||metadata.valid_from;
    const effectiveLabel=window.DisplayTime.format(effective,'');
    meta.innerHTML=`<section class="source-summary-box"><div class="source-meta__head">
      <span class="source-format source-format--big">${esc(formatLabel(metadata.source_format))}</span>
      <span class="source-state ${state.className}">${esc(state.label)}</span>
      ${effectiveLabel?`<span class="source-meta__stats">业务生效 ${esc(effectiveLabel)}</span>`:''}
    </div>
    <details class="source-technical"><summary>资料信息</summary><div class="source-technical__body">
      ${metadata.source_file?`<p class="source-meta__file">${esc(metadata.source_file)}</p>`:''}
      ${stats.length?`<p class="source-meta__stats">${esc(stats.join(' · '))}</p>`:''}
      ${missing?'<p class="source-meta__missing">未记录原始文件信息，可阅读已保存的正文。</p>':''}
      <p class="source-platform"><strong>适用平台</strong> <span>${esc(platformLabel(doc)||'未配置')}</span></p>
      <div class="source-meta__mapping"><h4>资料状态</h4><p>${esc(state.detail)}</p></div>
      ${window.DocumentTemporalUI?.render(doc)||''}
      <dl><dt>资料编号</dt><dd>${esc(doc.id)}</dd>${metadata.source_document_version_id?`<dt>原始文件版本编号</dt><dd>${esc(metadata.source_document_version_id)}</dd>`:''}${parser?`<dt>解析器</dt><dd>${esc(parser)}</dd>`:''}</dl>
    </div></details></section>`;
    const warnings=Array.isArray(metadata.warnings)?metadata.warnings:[];
    const paths=[...new Set(warnings.filter(w=>w?.code==='source_asset_missing'&&w.severity==='warning')
      .map(w=>w.path||w.message||'未记录图片路径'))];
    if(paths.length){
      const notice=detailNode('details',undefined,'source-asset-warning');
      const files=detailNode('ul');
      for(const path of paths)files.append(detailNode('li',path));
      notice.append(detailNode('summary',`已跳过 ${paths.length} 张缺失图片`),files);
      meta.append(notice);
    }
  }
  function updateCounts(){
    page.querySelector('.source-filter-bar')?.setAttribute('aria-label',boundedSearch()?'本次召回资料处理状态（最多100片段）':'资料处理状态');
    const counts=directoryCounts||{ready:0,processing:0,failed:0,all:allDocuments.size};
    if(!directoryCounts)for(const doc of allDocuments.values())counts[buildState(doc).className]++;
    for(const name of Object.keys(counts))$(name+'-count').textContent=counts[name];
  }
  // 检索进行中计数用“—”占位，而不是先归零：否则“全部 0”会让用户以为已经搜完且没有结果。
  function setCountsLoading(loading){
    const bar=page.querySelector('.source-filter-bar');
    if(bar){bar.setAttribute('aria-busy',String(loading));bar.classList.toggle('is-loading',loading);}
    if(loading)for(const name of ['ready','processing','failed','all']){const el=$(name+'-count');if(el)el.textContent='—';}
  }
  function createFilterUI(){
    const filterBar=document.createElement('div');filterBar.className='source-filter-bar';
    filterBar.setAttribute('aria-label','资料处理状态');
    filterBar.innerHTML=`
      <button type="button" class="source-filter-btn active" data-filter="all">全部 <span class="filter-count" id="all-count">0</span></button>
      <button type="button" class="source-filter-btn" data-filter="ready"><span class="filter-dot ready"></span>处理完成 / 待核验 <span class="filter-count" id="ready-count">0</span></button>
      <button type="button" class="source-filter-btn" data-filter="processing"><span class="filter-dot processing"></span>处理中 <span class="filter-count" id="processing-count">0</span></button>
      <button type="button" class="source-filter-btn" data-filter="failed"><span class="filter-dot failed"></span>失败 <span class="filter-count" id="failed-count">0</span></button>`;
    const resultToolbar=document.createElement('div');resultToolbar.className='source-results-toolbar';
    layout.before(resultToolbar);resultToolbar.append(filterBar,summary);
    filterBar.querySelectorAll('.source-filter-btn').forEach(button=>{
      button.setAttribute('aria-pressed',String(button.dataset.filter===currentFilter));
      button.addEventListener('click',()=>{
        currentFilter=button.dataset.filter;
        syncFilterButtons();return load();
      });
    });
  }
  function syncFilterButtons(){
    page.querySelectorAll('.source-filter-btn').forEach(button=>{
      const active=button.dataset.filter===currentFilter;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    });
  }
  function renderDocumentList(documents,{reset=false}={}){
    if(reset)list.replaceChildren();
    const added=document.createDocumentFragment();
    for(const [id,doc] of new Map(documents.map(doc=>[doc.id,doc]))){
      const previous=allDocuments.get(id),row=directoryRows.get(id);
      if(previous&&sameValue(previous,doc))continue;
      allDocuments.set(id,doc);
      const state=buildState(doc);
      if(currentFilter!=='all'&&state.className!==currentFilter){
        row?.element.remove();directoryRows.delete(id);continue;
      }
      const updated=renderDocumentRow(doc,state,row);
      directoryRows.set(id,updated);
      if(row)continue;
      // An overlapping entry may become visible again after a status change.
      // Keep its original position even when its new data arrived out of order.
      let next;
      if(previous){
        let after=false;
        for(const key of allDocuments.keys()){
          if(after&&directoryRows.has(key)){next=directoryRows.get(key).element;break;}
          if(key===id)after=true;
        }
      }
      if(next)next.before(updated.element);else added.append(updated.element);
    }
    list.append(added);updateCounts();
    if(!allDocuments.size){
      if(appliedQuery){
        if(boundedSearch())message('本次召回范围内无资料',`本次最多100片段召回范围内，当前条件和处理状态下没有可展示的资料。可调整条件、切换状态或清空搜索。`);
        else message('没有匹配的资料',`当前范围和处理状态下，搜索“${appliedQuery}”无结果。可调整关键词、切换状态或清空搜索。`);return;
      }
      if(currentFilter!=='all'&&directoryCounts?.all){message('当前筛选无结果','可切换其他处理状态查看资料。');return;}
      message('当前范围暂无原文',canWrite()?'可以切换项目、调整筛选条件，或点击“添加文件”上传资料。':'可以切换项目或调整筛选条件查看已有资料。');return;
    }
    if(!directoryRows.size){
      const labels={ready:'处理完成或待核验',processing:'处理中',failed:'失败',all:'所有'};
      message('当前筛选无结果',`没有${labels[currentFilter]}的文档。`);return;
    }
    layout.classList.remove('is-empty');empty.hidden=true;
  }
  function renderDocumentRow(doc,state,row){
    const button=row?.button||document.createElement('button');
    if(!row){
      const id=doc.id;
      button.type='button';button.className='source-item';button.dataset.source=String(id);
      button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','source-detail-drawer');
      button.setAttribute('aria-current',String(selected===id));
      button.addEventListener('click',()=>{
        if(directoryRows.get(id)?.button===button)return openDocument(allDocuments.get(id),button);
      });
    }
    const format=String(doc.metadata?.source_format||'').toLowerCase();
    const platform=platformLabel(doc);
    const stage=sourceProcessingStage(doc,state);
    const bits=[stage,platform?'适用平台 '+platform:'',formatLabel(doc.metadata?.source_format),doc.metadata?.source_size_bytes!=null?sizeLabel(doc.metadata.source_size_bytes):'',
      doc.metadata?.page_count?doc.metadata.page_count+' 页':'',doc.text_length==null?'正文按需读取':doc.text_length+' 字',
      doc.derived_count!=null?'派生摘要 '+doc.derived_count+' 条':''].filter(Boolean);
    button.innerHTML=`<span class="source-item__icon source-item__icon--${esc(format||'doc')}" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg></span>
        <span class="source-item__main"><span class="source-item__name"><b>${esc(documentTitle(doc))}</b><small>${esc(doc.metadata?.source_file||'已保存正文 · 原始文件名未记录')}</small></span>
        <span class="source-item__sub">${bits.length?esc(bits.join(' · ')):''}</span></span>
        <span class="source-item__side"><span class="source-state ${state.className}">${state.label}</span><span class="source-item__open" aria-hidden="true">查看 ›</span></span>`;
    if(appliedQuery&&doc.match&&window.SourceMatchView){
      const snippet=document.createElement('span');snippet.className='source-item__match';window.SourceMatchView.render(snippet,doc.match);button.append(snippet);
      if(doc.match.mode==='keyword')for(const selector of ['.source-item__name b','.source-item__name small']){
        const field=button.querySelector(selector),text=field.textContent;field.replaceChildren();window.SourceMatchView.appendText(field,text,doc.match.query,true);
      }
    }
    const project=loadedProject,stamp=loadedScope;
    const progress=doc.processing_task||state.className!=='ready'?
      progressButton(doc,project,()=>loadedProject===project&&sameContext(project,stamp),doc.processing_task||doc.metadata,row?.progress):null;
    const focused=document.activeElement===button||document.activeElement===row?.progress?document.activeElement:null;
    let element=row?.element||button;
    // Keep the document and progress actions separately focusable.
    if(progress&&!row?.progress){
      progress.classList.add('source-item-progress');
      element=detailNode('div',undefined,'source-directory-row');
      if(row)button.before(element);
      element.append(button,progress);
    }else if(!progress&&row?.progress){
      element.before(button);element.remove();element=button;
    }
    if(focused&&focused!==document.activeElement)(focused===button?button:progress||button).focus({preventScroll:true});
    return {element,button,progress};
  }
  function sourceProcessingStage(doc,state){
    const task=doc.processing_task;
    if(task?.status==='pending')return '处理任务排队中';
    if(task?.status==='retry_wait')return '等待自动重试';
    if(task?.status==='interrupted')return '处理任务已中断';
    const stage=task?.stage||(state.className==='processing'?doc.metadata?.active_stage:null);
    return detailStages[stage]||'';
  }
  function progressButton(doc,project,accepts,task=doc.processing_task||doc.metadata,existing=null){
    const batchId=task?.batch_id,itemId=task?.batch_item_id;
    const batchAvailable=batchId&&window.LibraryWorkspace?.openBatch;
    const jobId=task?.durable_task_id;
    if(!batchAvailable&&!(jobId&&window.IngestResults?.watch))return null;
    const button=existing||detailNode('button','查看进度','secondary');button.type='button';
    button.setAttribute('aria-label','查看进度 · '+documentTitle(doc));
    button.setAttribute('aria-haspopup','dialog');
    button.onclick=()=>{
      if(!accepts()||!button.isConnected)return;
      const trigger=button.classList.contains('source-item-progress')?button:directoryRows.get(doc.id)?.button;
      if(batchAvailable)window.LibraryWorkspace.openBatch({projectId:project,batchId,itemId,trigger});
      else{
        window.WorkspaceDrawers.close({restoreFocus:false});
        window.IngestResults.watch(jobId,documentTitle(doc),{projectId:project,mode:doc.metadata?.extraction_mode,focus:true,trigger});
      }
    };
    return button;
  }
  async function openDocument(doc,trigger){
    if(!doc||loadedProject!==current||loadedScope!==scopeStamp())return;
    clearReading();detailContent?.dispose();detailContent=null;readingMode='rich';
    const project=current,stamp=scopeStamp(),request=++detailSerial,ticket=window.WorkspaceContext?.ticket(project,'sources');
    selected=doc.id;activeTab='text';drawer.open({trigger});
    title.textContent=documentTitle(doc);body.classList.remove('source-structured');body.textContent='正在读取原文…';meta.replaceChildren();
    const accepts=()=>request===detailSerial&&drawer.dialog.open&&selected===doc.id&&sameContext(project,stamp,ticket);
    try{
      const key=JSON.stringify([project,stamp,doc.id]);
      let read=textReads.get(key);
      if(!read){
        const query=['valid_at','known_at'].filter(name=>scope()[name]).map(name=>name+'='+encodeURIComponent(scope()[name])).join('&');
        read=api('/api/projects/'+encodeURIComponent(project)+'/records/'+encodeURIComponent(doc.id)+(query?'?'+query:''),undefined,'GET');
        textReads.set(key,read);read.finally(()=>{if(textReads.get(key)===read)textReads.delete(key);}).catch(()=>{});
      }
      const full=await read;if(!accepts())return;
      const changed=doc.version_id&&full.version_id&&doc.version_id!==full.version_id;
      doc={...doc,...full,metadata:{...doc.metadata,...full.metadata},...(changed?{match:null}:{})};
    }catch(error){if(accepts())body.textContent='原文读取失败：'+error.message;return;}
    title.textContent=documentTitle(doc);
    renderMeta(doc,buildState(doc));
    if(doc.match&&window.SourceMatchView){
      const explanation=detailNode('section',undefined,'source-match-explanation');
      explanation.append(detailNode('h4','为什么找到这份资料'));window.SourceMatchView.render(explanation,doc.match,{detail:true});meta.prepend(explanation);
      if(doc.match.chunk_version_id&&doc.metadata?.source_document_version_id&&window.SourceReader){
        const locate=detailNode('button','定位相关片段','secondary');locate.type='button';explanation.append(locate);
        locate.onclick=()=>{window.WorkspaceDrawers.close({restoreFocus:false});window.SourceReader.open({projectId:project,versionId:doc.metadata.source_document_version_id,
          chunkVersionId:doc.match.chunk_version_id,matchKind:'semantic'});};
      }
    }
    renderDetailTabs(doc,project,stamp,ticket);
    await showText(doc,project,accepts);if(!accepts())return;body.scrollTop=0;
    list.querySelectorAll('.source-item').forEach(button=>button.setAttribute('aria-current',String(button===trigger)));
  }
  function sameContext(project,stamp,ticket){
    return project===current&&stamp===scopeStamp()&&(!ticket||window.WorkspaceContext.accepts(ticket,current,'sources'));
  }
  function renderReprocessing(host,actions,doc,project,stamp,ticket){
    if(!canWrite()||doc.processing_task)return;
    const panel=detailNode('section',undefined,'source-reprocess admin-only');
    panel.id='source-reprocess-form';panel.hidden=true;
    const toggle=detailNode('button','重新处理','secondary admin-only');toggle.type='button';
    toggle.setAttribute('aria-controls',panel.id);toggle.setAttribute('aria-expanded','false');
    toggle.onclick=()=>{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));};
    actions.append(toggle);host.append(panel);
    if(!doc.metadata?.source_document_version_id){
      panel.append(detailNode('p','此旧资料没有关联固定原始来源。请重新上传原文件，再选择处理方式；已有正文仍可阅读。'));return;
    }
    panel.append(detailNode('p','沿用固定原件并保留处理历史。补全 OCR 或转写后，可重新处理以更新切片和索引。'));
    const label=detailNode('label','处理方式'),select=detailNode('select');select.setAttribute('aria-label','重新处理方式');
    for(const [value,text] of [['documents','仅文档检索'],['discovery','开放发现'],['ontology','使用项目本体']]){
      const option=detailNode('option',text);option.value=value;select.append(option);
    }
    select.value='documents';label.append(select);panel.append(label);
    const modes={documents:'重建正文切片与检索索引。',discovery:'生成待核对的知识与术语候选。',ontology:'按项目已发布的本体抽取和校验事实。'};
    const modeNote=detailNode('p',modes[select.value]);modeNote.id='source-reprocess-mode-note';
    select.setAttribute('aria-describedby',modeNote.id);select.onchange=()=>{modeNote.textContent=modes[select.value];};panel.append(modeNote);
    const status=detailNode('p','','source-reprocess-status');status.setAttribute('role','status');
    const start=detailNode('button','创建处理任务','secondary');start.type='button';panel.append(start,status);
    let pending=false,submitted=false,keyMode=null,key=null;
    const accepts=()=>panel.isConnected&&drawer.dialog.open&&selected===doc.id&&sameContext(project,stamp,ticket);
    start.onclick=async()=>{
      if(pending||submitted||!canWrite()||!accepts())return;
      const mode=select.value;
      if(mode!==keyMode){keyMode=mode;key=window.crypto?.randomUUID?.()||Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);}
      pending=true;start.disabled=true;select.disabled=true;status.textContent='正在创建处理任务…';
      try{
        const result=await api('/api/projects/'+encodeURIComponent(project)+'/documents/'+encodeURIComponent(doc.id)+'/reprocess',
          {extraction_mode:mode,idempotency_key:key});
        if(!accepts())return;
        submitted=true;status.textContent='已创建处理任务。';
        const task={batch_id:result.batch?.id,batch_item_id:result.item?.id};
        const view=progressButton(doc,project,accepts,task);
        if(view){view.textContent='查看处理任务';panel.append(view);}
      }catch(error){if(accepts())status.textContent='创建失败：'+error.message;}
      finally{pending=false;if(accepts()){start.disabled=submitted;select.disabled=submitted;}}
    };
  }
  function renderReadingControls(doc,project,stamp,ticket){
    const version=doc.metadata?.source_document_version_id;
    const toolbar=detailNode('section',undefined,'source-reading-toolbar');
    readingNote=detailNode('p','','source-reading-note');readingNote.setAttribute('role','status');
    readingControls=detailNode('div',undefined,'source-reading-controls');readingControls.setAttribute('aria-label','原文显示方式');
    toolbar.append(readingControls,readingNote);
    if(!version)return toolbar;
    const accepts=()=>drawer.dialog.open&&selected===doc.id&&sameContext(project,stamp,ticket);
    const modes=[['rich','图文'],['plain','纯文本']];
    if(window.SourceReader?.renderParsed)modes.push(['parsed','解析']);
    if(window.SourceContent)for(const [mode,label] of modes){
      const button=detailNode('button',label,'secondary');button.type='button';button.dataset.readingMode=mode;button.setAttribute('aria-pressed',String(mode===readingMode));
      button.onclick=()=>{if(!accepts()||activeTab!=='text')return;readingMode=mode;const request=++detailSerial;
        return showText(doc,project,()=>accepts()&&request===detailSerial&&activeTab==='text');};readingControls.append(button);
    }
    const actions=detailNode('div',undefined,'source-reading-actions');
    if(window.SourceContent){
      detailContent=window.SourceContent.createSession({host:toolbar,projectId:project,versionId:version,isCurrent:()=>accepts()&&activeTab==='text'});
      for(const option of window.SourceContent.downloadOptions(doc.metadata))actions.append(detailContent.downloadLink(option));
    }
    toolbar.append(actions);return toolbar;
  }
  async function showText(doc,project,accepts,media=null){
    clearReading();body.classList.remove('source-structured');
    readingControls?.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.readingMode===readingMode)));
    const plain=()=>{body.textContent=doc.text||'旧项目未保存完整原文；可查片段与原始导入记录。';};
    const version=doc.metadata?.source_document_version_id;
    if(readingMode==='plain'||!version||!window.SourceContent){plain();readingNote.textContent=!version?'旧资料仅保留已保存正文，未关联固定来源版本':readingMode==='plain'?'':'图文阅读暂不可用，当前显示已保存正文。';return;}
    const parsed=readingMode==='parsed';
    body.classList.toggle('source-parsed-content',parsed);
    body.textContent=parsed?'正在读取解析结果…':'正在读取图文原文…';readingNote.textContent='';
    const content=window.SourceContent.createSession({host:body,projectId:project,versionId:version,isCurrent:()=>accepts()&&activeTab==='text'});richContent=content;
    try{
      if(parsed){
        await window.SourceReader.renderParsed({host:body,projectId:project,versionId:version,view:content,
          onLocate(location,source){
            if(!content.active())return;
            readingMode='rich';
            return showText(doc,project,accepts,{location,source});
          },
          onRefresh:()=>content.active()?showText(doc,project,accepts):undefined});
        return;
      }
      if(media&&window.SourceContent.isPdfSource(media.source)){
        const {source,location}=media;
        await content.renderPdf(source,window.SourceReader.pdfPage(source,location),location);
        return;
      }
      await content.render();
      if(media&&content.active()){
        const {location}=media;
        const positioned=Number.isFinite(location.start_ms)?content.seekVideo(location.asset_id,location.start_ms):
          content.focusRegion(location.asset_id,location.boxes);
        if(!positioned){
          window.WorkspaceDrawers.close({restoreFocus:false});
          await window.SourceReader.open({projectId:project,versionId:version,location});
        }
      }
    }catch(error){
      if(error.name==='AbortError'||!content.active())return;
      clearReading();
      if(parsed){body.textContent='解析结果读取失败：'+error.message;return;}
      plain();readingNote.textContent='图文原文暂不可用，当前显示已保存正文。可重试图文阅读或下载原文件。';
    }
  }
  function renderDetailTabs(doc,project,stamp,ticket){
    const tabs=document.createElement('div');tabs.className='source-detail-tabs';tabs.setAttribute('aria-label','资料详情');
    meta.append(tabs);
    const readingToolbar=renderReadingControls(doc,project,stamp,ticket);
    const runTools=detailNode('section',undefined,'source-run-tools');runTools.hidden=true;
    const runActions=detailNode('div',undefined,'source-run-actions');runTools.append(runActions);
    const currentDocument=()=>drawer.dialog.open&&selected===doc.id&&sameContext(project,stamp,ticket);
    if(doc.version_id&&window.openDocumentDuplicateReview){
      const duplicate=detailNode('button','查重本文件','secondary');duplicate.type='button';
      duplicate.setAttribute('aria-haspopup','dialog');duplicate.setAttribute('aria-controls','ledger-governance-drawer');
      duplicate.onclick=()=>{
        if(currentDocument())window.openDocumentDuplicateReview({projectId:project,id:doc.id,version_id:doc.version_id,title:documentTitle(doc)});
      };
      meta.querySelector('.source-meta__head').append(duplicate);
    }
    const progress=progressButton(doc,project,currentDocument);
    if(progress)runActions.append(progress);
    renderReprocessing(runTools,runActions,doc,project,stamp,ticket);
    meta.append(readingToolbar,runTools);
    for(const [name,label] of [['text','原文'],['assertions','抽取结果'],['runs','处理记录'],['history','修订记录']]){
      const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=label;button.setAttribute('aria-pressed',String(name==='text'));
      button.onclick=async()=>{
        const request=++detailSerial,id=doc.id,stamp=scopeStamp(),ticket=window.WorkspaceContext?.ticket(project,'sources');activeTab=name;
        clearReading();readingToolbar.hidden=name!=='text';runTools.hidden=name!=='runs'||!runActions.children.length;
        tabs.querySelectorAll('button').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
        body.classList.toggle('source-structured',name!=='text');
        const accepts=()=>request===detailSerial&&drawer.dialog.open&&selected===id&&activeTab===name&&button.isConnected&&sameContext(project,stamp,ticket);
        if(name==='text')return showText(doc,project,accepts);
        body.textContent='正在读取…';
        const suffix=name==='assertions'?'/assertions?document_id='+encodeURIComponent(id):name==='runs'?'/ingest-runs?document_id='+encodeURIComponent(id):'/records/'+encodeURIComponent(id)+'/history';
        try{
          // A run describes ingestion; later review/publication updates source assertions.
          const confirmation=name==='runs'?api('/api/projects/'+encodeURIComponent(project)+'/assertions?document_id='+encodeURIComponent(id)+'&status=accepted',undefined,'GET').catch(()=>null):null;
          const result=await api('/api/projects/'+encodeURIComponent(project)+suffix,undefined,'GET');
          if(!accepts())return;
          if(name==='assertions')renderAssertions(result.assertions||[],project,doc);
          else if(name==='runs'){
            const status=renderRuns(result.runs||result.items||[],doc,project,result.processing_task),facts=await confirmation;
            if(!accepts())return;
            if(!Array.isArray(facts?.assertions))status.textContent='该资料事实入图状态暂未核验，请到「抽取结果」重试查看。';
            else{
              const confirmed=new Set(facts.assertions.filter(row=>row.document_id===id&&row.status==='accepted'&&['entity','relation','attribute'].includes(row.kind)&&row.canonical_record_id).map(row=>row.canonical_record_id));
              status.textContent=confirmed.size?'该资料已确认入图事实：'+confirmed.size+' 条（按当前来源断言核验）。不代表全部候选已入图或派生索引已同步。':'该资料尚无已确认入图事实。本体版本发布与资料事实入图需分别核验。';
            }
          }
          else renderHistory(result.versions||[]);
        }catch(error){if(accepts())body.textContent='读取失败：'+error.message;}
      };tabs.append(button);
    }
  }
  function renderCandidateAction(rows,doc,project){
    if(doc&&canWrite()&&rows.some(row=>row.status==='pending')){
      const modeling=document.createElement('button');modeling.type='button';modeling.className='secondary admin-only';modeling.textContent='完善本体结构';
      modeling.title='核对此资料的候选和来源，预览后明确加入哪份工作草案；仅复用正式术语时确认事实写入。';
      modeling.onclick=async()=>{
        if(!canWrite()||project!==current||selected!==doc.id||activeTab!=='assertions'||!drawer.dialog.open||!modeling.isConnected)return;
        window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('ontology-model');
        try{
          if(!window.OntologyIntake)throw Error('结构候选模块尚未就绪，请刷新页面。');
          await window.OntologyIntake.open({projectId:project,documentId:doc.id,documentTitle:documentTitle(doc),trigger:modeling});
        }catch(error){window.OntologyModel?.status(error.message,true);}
      };body.append(modeling);
    }
  }
  const detailNode=(tag,text,css='')=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(css)element.className=css;return element;};
  const detailStates={queued:'等待处理',pending:'等待处理',retry_wait:'等待重试',running:'处理中',processing:'处理中',completed:'处理结束',ready:'处理完成',failed:'处理失败',interrupted:'已中断',cancelled:'已取消'};
  const detailStages={document:'保存原文',parse:'文档解析',parsing:'文档解析',ocr:'图片文字识别',asr:'视频语音转写',chunking:'生成切片',reusing_chunks:'复用已解析切片',keyword:'关键词索引',semantic:'语义索引',extraction:'知识抽取',extracting:'知识抽取',discovery:'开放发现',merging:'知识融合',validating:'知识校验',committing:'保存结果',completed:'流程结束',failed:'处理失败'};
  const readinessLabels={document_ready:'原文',keyword_ready:'关键词检索',semantic_ready:'语义检索',candidate_ready:'候选生成',graph_ready:'当次入图'};
  const displayTime=value=>window.DisplayTime.format(value,'时间未记录');
  function rawData(value,label){
    const details=detailNode('details',undefined,'source-raw-data');details.open=false;
    details.append(detailNode('summary',label+'（原始 JSON）'));let loaded=false;
    details.addEventListener('toggle',()=>{if(details.open&&!loaded){details.append(detailNode('pre',JSON.stringify(value,null,2)));loaded=true;}});
    return details;
  }
  function activityHeading(item,label,state){
    const heading=detailNode('div',undefined,'source-activity-heading');heading.append(detailNode('h4',label));
    if(state){const badge=detailNode('span',detailStates[state]||state,'source-activity-state');badge.dataset.status=state;heading.append(badge);}
    item.append(heading);
  }
  function renderRuns(rows,doc,project,currentTask){
    const stamp=scopeStamp(),ticket=window.WorkspaceContext?.ticket(project,'sources');
    body.replaceChildren(detailNode('p','这里只显示当前处理状态和当次成功产物。','source-detail-intro'));
    const status=detailNode('p','正在核验该资料已确认入图的事实…','source-detail-intro source-graph-status');status.setAttribute('aria-live','polite');body.append(status);
    const latest=Array.isArray(rows)?rows.at(-1):null;
    const metadata=doc.metadata||{},task=currentTask||doc.processing_task;
    let run=latest;
    const taskTime=task?.updated_at||task?.created_at;
    if(task?.status&&task.status!=='succeeded'&&(!taskTime||!latest?.created_at||Date.parse(taskTime)>=Date.parse(latest.created_at))){
      run={status:task.status,active_stage:task.stage,created_at:task.updated_at||task.created_at,
        failure:task.public_error};
    }else if(['processing','failed'].includes(metadata.status)&&(!run||metadata.run_id!==run.id)){
      run={status:metadata.status,active_stage:metadata.active_stage,created_at:doc.recorded_at,
        failure:metadata.public_error};
    }
    if(!run){body.append(detailNode('p','暂无独立处理记录。结构化导入的正文或旧资料可能没有解析运行记录。','source-detail-empty'));return status;}
    const succeeded=run.status==='completed'||run.status==='ready';
    const item=detailNode('article',undefined,'source-activity source-processing-run');if(run.id)item.dataset.runId=run.id;
    activityHeading(item,'处理记录 · '+displayTime(run.created_at),run.status);
    const facts=[run.active_stage?'阶段：'+(detailStages[run.active_stage]||run.active_stage):'阶段未记录'];
    item.append(detailNode('p',facts.filter(Boolean).join(' · '),'source-activity-meta'));
    const metrics=detailNode('dl',undefined,'source-run-metrics');
    for(const [key,label] of Object.entries(succeeded?{chunks_total:'切片总数',records_accepted:'当次写入记录',assertions_pending:'当次待审核计数'}:{})){
      if(run.counts?.[key]==null)continue;const cell=detailNode('div');cell.append(detailNode('dt',label),detailNode('dd',run.counts[key]));metrics.append(cell);
    }
    if(metrics.children.length)item.append(metrics);
    const readiness=detailNode('div',undefined,'source-readiness');readiness.setAttribute('aria-label','当次处理结果');
    for(const [key,label] of Object.entries(readinessLabels)){
      if(!succeeded||typeof run.readiness?.[key]!=='boolean')continue;
      const value=key==='graph_ready'?(run.readiness[key]?'已完成':'未直接入图'):(run.readiness[key]?'就绪':'尚未就绪');
      const chip=detailNode('span',label+'：'+value);chip.dataset.ready=String(run.readiness[key]);readiness.append(chip);
    }
    if(readiness.children.length)item.append(readiness);
    if(run.failure){const failure=typeof run.failure==='string'?run.failure:run.failure.message||'本次处理未成功，详见原始数据。';item.append(detailNode('p',failure,'source-activity-error'));}
    // Pending assertions count formal review candidates only. Current document
    // metadata can describe this run only when its saved run identity matches.
    const mode=doc.metadata?.run_id===run.id?doc.metadata?.extraction_mode:null;
    const formalPending=run.counts?.assertions_pending>0;
    const formal=formalPending||run.readiness?.graph_ready===true||mode==='ontology';
    const discovery=!formal&&run.readiness?.candidate_ready===true
      &&(mode==='discovery'||run.readiness?.graph_ready===false);
    if(succeeded&&canWrite()&&run.id&&(formalPending||discovery)){
      const review=detailNode('button',formalPending?'查看项目待审核事实':'核对本次处理的候选','secondary admin-only');review.type='button';
      if(formalPending)item.append(detailNode('p','显示当前项目全部待审核事实，不按本次处理筛选','subtle'));
      review.onclick=async()=>{
        if(!canWrite()||!sameContext(project,stamp,ticket)||selected!==doc.id||!drawer.dialog.open)return;
        try{
          if(formalPending){
            if(!window.KnowledgeNavigation)throw Error('知识审核导航尚未就绪，请刷新页面。');
            window.WorkspaceDrawers.close({restoreFocus:false});
            await window.KnowledgeNavigation.open('reviews',{projectId:project});return;
          }
          if(!window.OntologyIntake)throw Error('候选核对模块尚未就绪，请刷新页面。');
          window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('ontology-model');
          await window.OntologyIntake.open({projectId:project,documentId:doc.id,documentTitle:documentTitle(doc),runId:run.id,trigger:review});
        }catch(error){window.OntologyModel?.status(error.message,true);}
      };item.append(review);
    }
    if(run.id)item.append(rawData(run,'查看本次处理数据'));
    body.append(item);
    return status;
  }
  function sameValue(left,right){
    if(left===right)return true;
    if(!left||!right||typeof left!=='object'||typeof right!=='object'||Array.isArray(left)!==Array.isArray(right))return false;
    const keys=Object.keys(left);return keys.length===Object.keys(right).length&&keys.every(key=>Object.prototype.hasOwnProperty.call(right,key)&&sameValue(left[key],right[key]));
  }
  const shortValue=value=>value==null?'未设置':Array.isArray(value)?value.length+' 项':typeof value==='object'?'已更新':String(value).slice(0,80);
  const processingFields=new Set([
    'status','active_stage','run_id','batch_id','batch_item_id','document_generation','reprocessing',
    'uploaded_at','indexed_at','extracted_at','failed_at','error_type','validation_error_count','validation_errors',
    'readiness','llm','extraction_mode','ocr_mode','review_candidates','discovery_candidates','warnings',
    'document_parser','parser_version','page_count','page_summaries','table_count','table_summaries'
  ]);
  function revisionChanges(row,previous){
    const text=String(row.text??''),before=String(previous?.text??'');
    if(!previous)return ['首次保存资料 · 正文 '+text.length+' 字'];
    const changes=text===before?[]:['正文已变化：'+before.length+' 字 → '+text.length+' 字'];
    const labels={title:'标题',published_at:'发布时间',revised_at:'原文修订时间',applicability:'适用范围',source_file:'文件名',source_format:'文件格式',source_size_bytes:'文件大小',valid_from:'生效时间',valid_until:'失效时间'};
    const timeFields=new Set(['published_at','revised_at','valid_from','valid_until']);
    const changeValue=(key,value)=>timeFields.has(key)?window.DisplayTime.format(value,'未设置'):shortValue(value);
    const metadata=row.metadata||{},oldMetadata=previous.metadata||{};
    for(const key of new Set([...Object.keys(oldMetadata),...Object.keys(metadata)])){
      if(processingFields.has(key)||sameValue(metadata[key],oldMetadata[key]))continue;
      const label=labels[key]||key;
      if(key==='source_document_version_id')changes.push('原始文件版本已更新');
      else if(typeof metadata[key]==='object'&&metadata[key]!==null||typeof oldMetadata[key]==='object'&&oldMetadata[key]!==null)changes.push(label+'已更新');
      else changes.push(label+'：'+changeValue(key,oldMetadata[key])+' → '+changeValue(key,metadata[key]));
    }
    for(const key of ['valid_from','valid_until','type','kind'])if(!sameValue(row[key],previous[key]))changes.push((labels[key]||key)+'：'+changeValue(key,previous[key])+' → '+changeValue(key,row[key]));
    return changes;
  }
  function renderHistory(rows){
    body.replaceChildren(detailNode('p','这里显示已保存的正文和资料字段变更。','source-detail-intro'));
    const revisions=[];
    for(const row of Array.isArray(rows)?rows:[]){
      const status=row.metadata?.status;
      if(status&&status!=='ready'&&status!=='completed')continue;
      const changes=revisionChanges(row,revisions.at(-1)?.row);
      if(changes.length)revisions.push({row,changes});
    }
    if(!revisions.length){body.append(detailNode('p','没有可用的修订记录。','source-detail-empty'));return;}
    for(let index=revisions.length-1;index>=0;index--){
      const {row,changes}=revisions[index],item=detailNode('article',undefined,'source-activity source-revision');item.dataset.versionId=row.version_id||row.id;
      activityHeading(item,'修订 '+(index+1)+(index===revisions.length-1?' · 最新修订':''));
      item.append(detailNode('p','保存于 '+displayTime(row.recorded_at),'source-activity-meta'));
      const list=detailNode('ul',undefined,'source-change-list');
      for(const change of changes.slice(0,8))list.append(detailNode('li',change));
      if(changes.length>8)list.append(detailNode('li','另有 '+(changes.length-8)+' 项字段变化，可展开本版原始数据核对。'));
      item.append(list,rawData(row,'查看本版完整快照'));body.append(item);
    }
  }
  function distinctSourceEvidence(rows){
    const occurrences=new Map();
    return rows.filter(row=>{
      if(row.status!=='accepted'||!row.document_id||!row.document_version_id||!row.canonical_record_id
        ||!Number.isInteger(row.start_char)||!Number.isInteger(row.end_char)
        ||row.start_char>=row.end_char||!row.quote)return true;
      const key=JSON.stringify([row.document_id,row.document_version_id,row.source_hash,row.kind,
        row.canonical_record_id,row.start_char,row.end_char,row.quote]);
      const fact={...row.payload};
      for(const field of ['id','chunk_id','created_at','confidence'])delete fact[field];
      if(fact.metadata&&typeof fact.metadata==='object'&&!Array.isArray(fact.metadata)){
        fact.metadata={...fact.metadata};
        delete fact.metadata.chunk_id;
      }
      const seen=occurrences.get(key)||[];
      if(seen.some(prior=>sameValue(prior,fact)))return false;
      seen.push(fact);occurrences.set(key,seen);
      return true;
    });
  }
  function renderAssertions(rows,project,doc){
    const sourceScope=JSON.parse(JSON.stringify(window.WorkspaceContext?.read(project,'sources')||scope()));
    body.replaceChildren();
    if(!rows.length){body.textContent=doc?.metadata?.extraction_mode==='documents'?
      '当前仅用于文档检索，暂无抽取结果。':'此资料暂无抽取结果。';return;}
    if(doc)renderCandidateAction(rows,doc,project);
    for(const row of distinctSourceEvidence(rows)){
      const item=document.createElement('div');item.className='source-record-row';const payload=row.payload||{};
      const label=document.createElement('strong');
      const kinds={entity:'实体',relation:'关系',attribute:'属性'},states={pending:'待核对',accepted:'已确认',rejected:'已拒绝'};
      label.textContent=(kinds[row.kind]||row.kind||'断言')+' · '+(states[row.status]||row.status||'待核验');
      const text=document.createElement('p');text.textContent=payload.text||payload.name||[payload.subject,payload.predicate,payload.object??payload.value].filter(Boolean).join(' → ')||JSON.stringify(payload);
      item.append(label,text);
      if(row.quote){
        const quote=document.createElement('blockquote');quote.className='subtle';quote.textContent=row.quote;item.append(quote);
      }
      const version=doc?.metadata?.source_document_version_id;
      if(row.id&&row.chunk_id&&version&&window.SourceReader){
        const locate=document.createElement('button');locate.type='button';locate.className='secondary';locate.textContent='定位原文';
        locate.onclick=()=>{
          if(project!==current||selected!==doc.id||activeTab!=='assertions'||!locate.isConnected)return;
          window.WorkspaceDrawers.close({restoreFocus:false});
          return window.SourceReader.open({projectId:project,versionId:version,assertionId:row.id,focusText:row.quote});
        };
        item.append(locate);
      }
      if(row.canonical_record_id){
        const open=document.createElement('button');open.type='button';open.className='secondary';open.textContent='查看正式知识';
        open.onclick=async()=>{window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('records');await window.openKnowledgeRecord?.({id:row.canonical_record_id,projectId:project,scope:sourceScope});};item.append(open);
      }
      body.append(item);
    }
  }
  function load({append=false,reuse=false}={}){
    const project=current,ticket=window.WorkspaceContext?.ticket(project,'sources');
    if(!project){searchSucceeded=false;searchLoading=false;renderSearchSteps();refresh.disabled=false;summary.textContent='尚未选择项目';message('选择项目后自动展示原文','请在左侧选择已有项目。');return false;}
    let filter,stamp,relevance,relevanceStamp;
    try{filter=scope();stamp=JSON.stringify(filter);relevance=window.SearchRelevance?.read('source')||{};relevanceStamp=JSON.stringify(relevance);}catch(error){searchSucceeded=false;searchLoading=false;renderSearchSteps();refresh.disabled=false;message('筛选条件不正确',error.message);return false;}
    const state=currentFilter,term=appliedQuery,mode=retrievalMode,chain=stepsStamp(),cursor=append?nextCursor:null,key=JSON.stringify([project,stamp,state,term,mode,chain,cursor,relevanceStamp]);
    if(inFlight?.key===key&&inFlight.serial===serial)return inFlight.promise;
    if(reuse&&loadedProject===project&&loadedScope===stamp&&loadedState===state&&loadedQuery===term&&loadedMode===mode&&loadedSteps===chain&&loadedRelevance===relevanceStamp)return Promise.resolve(true);
    const request=++serial,accepts=()=>request===serial&&sameContext(project,stamp,ticket)&&currentFilter===state&&appliedQuery===term&&retrievalMode===mode&&stepsStamp()===chain&&JSON.stringify(window.SearchRelevance?.read('source')||{})===relevanceStamp;
    searchLoading=true;
    if(!append){searchSucceeded=false;boundedBySemantic=false;}
    renderSearchSteps();
    if(!append){closeDetail();loadedProject=null;loadedScope=null;loadedState=null;allDocuments.clear();directoryRows.clear();directoryCounts=null;nextCursor=null;setCountsLoading(true);
      summary.textContent=semanticSearch()?'正在进行'+modeLabel(mode)+'召回…':'正在读取当前项目的原文…';
      // 检索进行中不显示空状态卡片，只保留列表占位，避免先呈现一张“没有结果”样式的卡片。
      empty.hidden=true;layout.classList.remove('is-empty');
      list.innerHTML='<div class="source-placeholder">'+(semanticSearch()?modeLabel(mode)+'召回中，首次查询需要编码，请稍候…':'正在加载文档目录…')+'</div>';}
    refresh.disabled=true;loadMore.disabled=true;loadMore.hidden=!append;
    const promise=(async()=>{try{
      const query='?limit=50&summary_only=true&state='+encodeURIComponent(state)+(term?'&query='+encodeURIComponent(term):'')+(rankedMode(mode)?'&retrieval_mode='+encodeURIComponent(mode):'')+(cursor?'&cursor='+encodeURIComponent(cursor):'');
      const result=await api('/api/projects/'+encodeURIComponent(project)+'/sources'+query,searchSteps.length?{...filter,...relevance,search_steps:JSON.parse(chain)}:{...filter,...relevance});
      if(!accepts())return false;
      loadedProject=project;loadedScope=stamp;loadedState=state;loadedQuery=term;loadedMode=mode;loadedSteps=chain;loadedRelevance=relevanceStamp;searchSucceeded=true;boundedBySemantic=result.bounded_by_semantic===true;
      nextCursor=result.next_cursor||null;directoryCounts=result.counts||null;
      renderDocumentList(result.documents||[],{reset:!append});total=result.total??allDocuments.size;
      summary.textContent=searchSteps.length&&boundedSearch()
        ?`召回范围内继续检索 · ${searchSteps.length+1} 步条件 · 已命中 ${directoryCounts?.all??total} 份 · 已加载 ${allDocuments.size} 份${result.complete===false?' · 召回不完整，请刷新重试':''}`
        :semanticSearch()
        ?`${modeLabel(mode)}召回 · “${term}” · 本次召回 ${directoryCounts?.all??total} 份资料（最多100片段）· 已加载 ${allDocuments.size} 份${result.complete===false?' · 召回不完整，请刷新重试':''}`
        :(term?`搜索“${term}” · `:'')+`当前范围共 ${total} 篇 · 已加载 ${allDocuments.size} 篇 · 选择资料查看详情${result.complete===false?' · 结果不完整，请刷新重试':''}`;
      loadMore.hidden=!nextCursor;
      return true;
    }catch(error){
      if(accepts()){
        if(append)summary.textContent='加载更多资料失败：'+error.message+'；可重试。';
        else{summary.textContent=semanticSearch()?modeLabel(mode)+'召回失败':'原文加载失败';message(semanticSearch()?'暂时无法进行'+modeLabel(mode)+'召回':'暂时无法读取原文',error.message+'；请点击右上角“刷新”重试。');}
      }return false;
    }finally{if(request===serial){searchLoading=false;setCountsLoading(false);renderSearchSteps();refresh.disabled=false;loadMore.disabled=false;if(inFlight?.serial===request)inFlight=null;}}
    })();
    inFlight={key,serial:request,promise};return promise;
  }
  createFilterUI();
  window.SourcesView={async open(){window.KnowledgeTabs?.open('sources');return load({reuse:true});}};
  // Citation deep links are explicit selections, so they may open the matching drawer.
  window.selectSource=async sourceId=>{
    if(!sourceId)throw Error('该证据没有可定位的来源');
    const citation=++citationSerial;
    resetSearch();
    if(currentFilter!=='all'){currentFilter='all';syncFilterButtons();}
    closeDetail();const project=current,stamp=scopeStamp(),loaded=await load({reuse:true}),request=serial,detailRequest=detailSerial;
    const accepts=()=>citation===citationSerial&&detailRequest===detailSerial&&request===serial&&project===current&&stamp===scopeStamp();
    if(!loaded||!accepts())throw Error('资料范围已更新，请重新选择原文');
    let doc=allDocuments.get(sourceId);
    if(!doc){
      const result=await api('/api/projects/'+encodeURIComponent(project)+'/sources?limit=50&summary_only=true&state=all&document_id='+encodeURIComponent(sourceId),scope());
      if(!accepts())throw Error('资料范围已更新，请重新选择原文');
      doc=result.documents?.find(item=>item.id===sourceId);
      if(doc)renderDocumentList([doc]);
    }
    if(!doc)throw Error('当前范围没有这条原文');
    const button=directoryRows.get(sourceId)?.button;
    if(!button)throw Error('当前范围没有这条原文');await openDocument(doc,button);
  };
  refresh.onclick=()=>load();loadMore.onclick=()=>nextCursor&&load({append:true});
  // Auth resolves asynchronously; its body role classes own entry visibility.
  const upload=$('upload-source');upload.classList.add('admin-only');
  upload.onclick=()=>{if(!canWrite())return;if(window.LibraryWorkspace)window.LibraryWorkspace.open('upload');else window.KnowledgeTabs.open('ingest');};
  document.querySelector('[data-tab="sources"]')?.addEventListener('click',()=>load());
  document.addEventListener('workspace:scope-changed',event=>{if(event.detail.page==='sources'){serial++;closeDetail();if(visible())load();}});
  $('project').addEventListener('change',()=>{serial++;resetSearch();closeDetail();loadedProject=null;loadedScope=null;loadedState=null;loadedQuery=null;loadedMode=null;allDocuments.clear();directoryRows.clear();directoryCounts=null;nextCursor=null;loadMore.hidden=true;updateCounts();message('正在切换项目','进入资料库后会自动加载。');if(visible())load();});
  if(visible())load();
})();
