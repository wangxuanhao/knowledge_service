/* List-first source browsing; original detail nodes live in an owned drawer. */
(() => {
  const page=$('tab-sources'),list=$('source-list'),title=$('source-title'),body=$('source-body'),refresh=$('load-sources');
  const layout=page.querySelector('.source-layout'),article=layout.querySelector('article');
  const summary=document.createElement('p');summary.className='source-summary subtle';summary.setAttribute('aria-live','polite');layout.before(summary);
  const meta=document.createElement('div');meta.id='source-meta';meta.className='source-meta';title.after(meta);
  const empty=document.createElement('div');empty.className='source-empty';empty.setAttribute('aria-live','polite');empty.hidden=true;
  empty.innerHTML='<div class="source-empty-icon" aria-hidden="true">📄</div><h3></h3><p></p>';layout.append(empty);
  refresh.textContent='刷新';refresh.classList.add('secondary');
  let serial=0,detailSerial=0,citationSerial=0,selected=null,activeTab='text',loadedProject=null,loadedScope=null,loadedState=null,allDocuments=[],currentFilter='all';
  let inFlight=null,nextCursor=null,total=0,directoryCounts=null;
  const textReads=new Map();
  const loadMore=document.createElement('button');loadMore.id='source-load-more';loadMore.type='button';loadMore.className='secondary';loadMore.textContent='加载更多资料';loadMore.hidden=true;layout.after(loadMore);
  const visible=()=>!page.classList.contains('hidden');
  const canWrite=()=>Boolean(window.Auth?.isAdmin?.());
  const scopeStamp=()=>{try{return JSON.stringify(scope());}catch{return null;}};
  const drawer=window.WorkspaceDrawers.create({
    id:'source-detail-drawer',title,description:'查看资料原文、提取知识、处理与修订记录。',content:article,onClose:clearDetail,
  });

  function clearDetail(){
    detailSerial++;selected=null;activeTab='text';title.textContent='';meta.replaceChildren();body.textContent='';
    list.querySelectorAll('.source-item').forEach(button=>button.setAttribute('aria-current','false'));
  }
  function closeDetail(){drawer.close({restoreFocus:false});clearDetail();}
  function message(heading,detail){
    empty.querySelector('h3').textContent=heading;empty.querySelector('p').textContent=detail;
    empty.hidden=false;layout.classList.add('is-empty');list.replaceChildren();
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
  function renderMeta(doc,state){
    const metadata=doc.metadata||{},parser=metadata.document_parser||'';
    const parserLabel=parser==='builtin-text'?'纯文本读取':['docling','semantica-docling'].includes(parser)?'文档转换解析':parser;
    const stats=[sizeLabel(metadata.source_size_bytes),parserLabel,metadata.page_count?metadata.page_count+' 页':''].filter(Boolean);
    const missing=!metadata.source_file&&!metadata.source_format&&!stats.length;
    meta.innerHTML=`<div class="source-meta__head">
      <span class="source-format source-format--big">${esc(formatLabel(metadata.source_format))}</span>
      <span class="source-meta__file">${esc(metadata.source_file||metadata.title||'已保存的资料正文')}</span>
      ${stats.length?`<span class="source-meta__stats">${esc(stats.join(' · '))}</span>`:''}
    </div>${missing?'<p class="source-meta__missing">未记录原始文件信息；可以阅读已保存正文，资料编号见下方来源信息。</p>':''}
    <div class="source-meta__mapping"><h4>资料状态</h4><p>${esc(state.detail)}</p></div>
    <details class="source-technical"><summary>资料标识与来源信息</summary><dl><dt>资料编号</dt><dd>${esc(doc.id)}</dd>${metadata.source_document_version_id?`<dt>原始文件版本编号</dt><dd>${esc(metadata.source_document_version_id)}</dd>`:''}${parser?`<dt>解析器</dt><dd>${esc(parser)}</dd>`:''}</dl></details>`;
  }
  function updateCounts(){
    const counts=directoryCounts||{ready:0,processing:0,failed:0,all:allDocuments.length};
    if(!directoryCounts)allDocuments.forEach(doc=>counts[buildState(doc).className]++);
    for(const name of Object.keys(counts))$(name+'-count').textContent=counts[name];
  }
  function createFilterUI(){
    const filterBar=document.createElement('div');filterBar.className='source-filter-bar';
    filterBar.setAttribute('aria-label','资料处理状态');
    filterBar.innerHTML=`
      <button type="button" class="source-filter-btn" data-filter="ready"><span class="filter-dot ready"></span>处理完成 / 待核验 <span class="filter-count" id="ready-count">0</span></button>
      <button type="button" class="source-filter-btn" data-filter="processing"><span class="filter-dot processing"></span>处理中 <span class="filter-count" id="processing-count">0</span></button>
      <button type="button" class="source-filter-btn" data-filter="failed"><span class="filter-dot failed"></span>失败 <span class="filter-count" id="failed-count">0</span></button>
      <button type="button" class="source-filter-btn active" data-filter="all">全部 <span class="filter-count" id="all-count">0</span></button>`;
    layout.before(filterBar);
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
  function renderDocumentList(){
    closeDetail();updateCounts();
    if(!allDocuments.length){
      if(currentFilter!=='all'&&directoryCounts?.all){message('当前筛选无结果','可切换其他处理状态查看资料。');return;}
      message('当前范围暂无原文',canWrite()?'可以切换项目、调整筛选条件，或点击“添加文件”上传资料。':'可以切换项目或调整筛选条件查看已有资料。');return;
    }
    const filtered=allDocuments.filter(doc=>currentFilter==='all'||buildState(doc).className===currentFilter);
    if(!filtered.length){
      const labels={ready:'处理完成或待核验',processing:'处理中',failed:'失败',all:'所有'};
      message('当前筛选无结果',`没有${labels[currentFilter]}的文档。`);return;
    }
    layout.classList.remove('is-empty');empty.hidden=true;list.replaceChildren();
    for(const [index,doc] of allDocuments.entries()){
      if(currentFilter!=='all'&&buildState(doc).className!==currentFilter)continue;
      const state=buildState(doc),button=document.createElement('button');button.type='button';button.className='source-item';
      button.dataset.source=String(index);button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','source-detail-drawer');button.setAttribute('aria-current','false');
      button.innerHTML=`<span class="source-item__name"><b>${esc(documentTitle(doc))}</b><small>${esc(doc.metadata?.source_file||'已保存正文 · 原始文件名未记录')}</small></span>
        <span class="source-state ${state.className}">${state.label}</span><span class="source-item__type source-format">${esc(formatLabel(doc.metadata?.source_format))}</span>
        <span class="source-item__counts">${doc.text_length==null?'正文按需读取':esc(doc.text_length)+' 字'} · 派生摘要 ${esc(doc.derived_count??'待核验')} 条</span><span class="source-item__open" aria-hidden="true">查看 ›</span>`;
      button.addEventListener('click',()=>openDocument(doc,button));list.append(button);
    }
  }
  async function openDocument(doc,trigger){
    if(loadedProject!==current||loadedScope!==scopeStamp())return;
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
      doc={...doc,...full,metadata:{...doc.metadata,...full.metadata}};
    }catch(error){if(accepts())body.textContent='原文读取失败：'+error.message;return;}
    title.textContent=documentTitle(doc);
    renderMeta(doc,buildState(doc));renderDetailTabs(doc,project);
    if(doc.metadata?.source_document_version_id&&window.SourceReader){
      const open=document.createElement('button');open.type='button';open.className='secondary';open.textContent='在原文中打开';
      open.onclick=()=>{window.WorkspaceDrawers.close({restoreFocus:false});window.SourceReader.open({projectId:project,versionId:doc.metadata.source_document_version_id});};meta.append(open);
    }
    body.textContent=doc.text||'旧项目未保存完整原文；可查片段与原始导入记录。';body.scrollTop=0;
    list.querySelectorAll('.source-item').forEach(button=>button.setAttribute('aria-current',String(button===trigger)));
  }
  function sameContext(project,stamp,ticket){
    return project===current&&stamp===scopeStamp()&&(!ticket||window.WorkspaceContext.accepts(ticket,current,'sources'));
  }
  function renderDetailTabs(doc,project){
    const tabs=document.createElement('div');tabs.className='source-detail-tabs';tabs.setAttribute('aria-label','资料详情');
    for(const [name,label] of [['text','原文'],['assertions','提取知识'],['runs','处理记录'],['history','修订记录']]){
      const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=label;button.setAttribute('aria-pressed',String(name==='text'));
      button.onclick=async()=>{
        const request=++detailSerial,id=doc.id,stamp=scopeStamp(),ticket=window.WorkspaceContext?.ticket(project,'sources');activeTab=name;
        tabs.querySelectorAll('button').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
        body.classList.toggle('source-structured',name!=='text');
        if(name==='text'){body.textContent=doc.text||'此记录未保存完整原文';return;}
        const accepts=()=>request===detailSerial&&drawer.dialog.open&&selected===id&&activeTab===name&&button.isConnected&&sameContext(project,stamp,ticket);
        body.textContent='正在读取…';
        const suffix=name==='assertions'?'/assertions?document_id='+encodeURIComponent(id):name==='runs'?'/ingest-runs?document_id='+encodeURIComponent(id):'/records/'+encodeURIComponent(id)+'/history';
        try{
          const result=await api('/api/projects/'+encodeURIComponent(project)+suffix,undefined,'GET');
          if(!accepts())return;
          if(name==='assertions')renderAssertions(result.assertions||[],project);
          else if(name==='runs')renderRuns(result.runs||result.items||[],doc,project);
          else renderHistory(result.versions||[]);
        }catch(error){if(accepts())body.textContent='读取失败：'+error.message;}
      };tabs.append(button);
    }
    if(canWrite()){
      const modeling=document.createElement('button');modeling.type='button';modeling.className='secondary admin-only';modeling.textContent='完善本体结构';
      modeling.title='核对此资料的候选和来源，预览后明确加入哪份工作草案；仅复用正式术语时确认事实写入。';
      modeling.onclick=async()=>{
        if(!canWrite()||project!==current||selected!==doc.id||!drawer.dialog.open)return;
        window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('ontology-model');
        try{
          if(!window.OntologyIntake)throw Error('结构候选模块尚未就绪，请刷新页面。');
          await window.OntologyIntake.open({projectId:project,documentId:doc.id,documentTitle:documentTitle(doc),trigger:modeling});
        }catch(error){window.OntologyModel?.status(error.message,true);}
      };tabs.append(modeling);
    }
    meta.append(tabs);
  }
  const detailNode=(tag,text,css='')=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(css)element.className=css;return element;};
  const detailStates={queued:'等待处理',running:'处理中',processing:'处理中',completed:'处理结束',ready:'处理完成',failed:'处理失败',interrupted:'已中断'};
  const detailStages={document:'保存原文',parse:'文档解析',parsing:'文档解析',ocr:'图片文字识别',chunking:'切片',keyword:'关键词索引',semantic:'语义索引',extraction:'知识抽取',extracting:'知识抽取',discovery:'开放发现',merging:'知识融合',validating:'知识校验',committing:'保存结果',completed:'流程结束',failed:'处理失败'};
  const readinessLabels={document_ready:'原文',keyword_ready:'关键词检索',semantic_ready:'语义检索',candidate_ready:'候选生成',graph_ready:'正式图谱'};
  const displayTime=value=>{const date=value?new Date(value):null;return date&&Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{hour12:false}):'时间未记录';};
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
  function renderRuns(rows,doc,project){
    body.replaceChildren(detailNode('p','处理记录说明每次解析、切片和抽取的执行情况。重试属于新的尝试；流程结束不代表所有检索或图谱能力都已就绪。','source-detail-intro'));
    if(!Array.isArray(rows)||!rows.length){body.append(detailNode('p','暂无独立处理记录。结构化导入的正文或旧资料可能没有解析运行记录。','source-detail-empty'));return;}
    for(const run of [...rows].reverse()){
      const item=detailNode('article',undefined,'source-activity source-processing-run');item.dataset.runId=run.id;
      activityHeading(item,'处理记录 · '+displayTime(run.created_at),run.status);
      const facts=[run.attempt?'同一原文版本第 '+run.attempt+' 次尝试':'',run.active_stage?'阶段：'+(detailStages[run.active_stage]||run.active_stage):'阶段未记录'];
      item.append(detailNode('p',facts.filter(Boolean).join(' · '),'source-activity-meta'));
      const metrics=detailNode('dl',undefined,'source-run-metrics');
      for(const [key,label] of [['chunks_total','切片总数'],['chunks_failed','失败切片'],['records_accepted','写入记录'],['assertions_pending','待审核计数']]){
        if(run.counts?.[key]==null)continue;const cell=detailNode('div');cell.append(detailNode('dt',label),detailNode('dd',run.counts[key]));metrics.append(cell);
      }
      if(metrics.children.length)item.append(metrics);
      const readiness=detailNode('div',undefined,'source-readiness');
      for(const [key,label] of Object.entries(readinessLabels)){
        if(typeof run.readiness?.[key]!=='boolean')continue;
        const chip=detailNode('span',label+'：'+(run.readiness[key]?'就绪':'尚未就绪'));chip.dataset.ready=String(run.readiness[key]);readiness.append(chip);
      }
      if(readiness.children.length)item.append(readiness);
      if(run.failure){const failure=typeof run.failure==='string'?run.failure:run.failure.message||'本次处理未成功，详见原始数据。';item.append(detailNode('p',failure,'source-activity-error'));}
      if(canWrite()&&run.id&&(run.counts?.assertions_pending>0||run.readiness?.candidate_ready)){
        const review=detailNode('button','核对本次处理的候选','secondary admin-only');review.type='button';
        review.onclick=async()=>{
          if(!canWrite()||project!==current||selected!==doc.id||!drawer.dialog.open)return;
          try{
            if(!window.OntologyIntake)throw Error('候选核对模块尚未就绪，请刷新页面。');
            window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('ontology-model');
            await window.OntologyIntake.open({projectId:project,documentId:doc.id,documentTitle:documentTitle(doc),runId:run.id,trigger:review});
          }catch(error){window.OntologyModel?.status(error.message,true);}
        };item.append(review);
      }
      item.append(rawData(run,'查看本次处理数据'));body.append(item);
    }
  }
  function sameValue(left,right){
    if(left===right)return true;
    if(!left||!right||typeof left!=='object'||typeof right!=='object'||Array.isArray(left)!==Array.isArray(right))return false;
    const keys=Object.keys(left);return keys.length===Object.keys(right).length&&keys.every(key=>Object.prototype.hasOwnProperty.call(right,key)&&sameValue(left[key],right[key]));
  }
  const shortValue=value=>value==null?'未设置':Array.isArray(value)?value.length+' 项':typeof value==='object'?'已更新':String(value).slice(0,80);
  function revisionChanges(row,previous){
    const text=String(row.text??''),before=String(previous?.text??'');
    if(!previous)return ['首次保存资料 · 正文 '+text.length+' 字'];
    const changes=[text===before?'正文未变化':'正文已变化：'+before.length+' 字 → '+text.length+' 字'];
    const labels={title:'标题',status:'处理状态',active_stage:'处理阶段',published_at:'发布时间',extraction_mode:'处理方式',applicability:'适用范围',uploaded_at:'上传时间',extracted_at:'处理完成时间',review_candidates:'待审核候选',discovery_candidates:'开放发现候选',readiness:'就绪状态',source_file:'文件名',source_format:'文件格式',source_size_bytes:'文件大小',document_parser:'解析方式',valid_from:'生效时间',valid_until:'失效时间'};
    const internal=new Set(['run_id','batch_id','batch_item_id','file_sha256','document_generation']);let internalChanges=0;
    const metadata=row.metadata||{},oldMetadata=previous.metadata||{};
    for(const key of new Set([...Object.keys(oldMetadata),...Object.keys(metadata)])){
      if(sameValue(metadata[key],oldMetadata[key]))continue;
      if(internal.has(key)){internalChanges++;continue;}
      const label=labels[key]||key;
      if(key==='status')changes.push(label+'：'+(detailStates[oldMetadata[key]]||shortValue(oldMetadata[key]))+' → '+(detailStates[metadata[key]]||shortValue(metadata[key])));
      else if(key==='active_stage')changes.push(label+'：'+(detailStages[oldMetadata[key]]||shortValue(oldMetadata[key]))+' → '+(detailStages[metadata[key]]||shortValue(metadata[key])));
      else if(key==='source_document_version_id')changes.push('原始文件版本已更新');
      else if(key==='review_candidates'||key==='discovery_candidates')changes.push(label+'已更新（'+(Array.isArray(metadata[key])?metadata[key].length+' 条':'未记录')+'）');
      else if(typeof metadata[key]==='object'&&metadata[key]!==null||typeof oldMetadata[key]==='object'&&oldMetadata[key]!==null)changes.push(label+'已更新');
      else changes.push(label+'：'+shortValue(oldMetadata[key])+' → '+shortValue(metadata[key]));
    }
    for(const key of ['valid_from','valid_until','type','kind'])if(!sameValue(row[key],previous[key]))changes.push((labels[key]||key)+'：'+shortValue(previous[key])+' → '+shortValue(row[key]));
    if(internalChanges)changes.push('处理关联信息已更新（'+internalChanges+' 项）');
    return changes;
  }
  function renderHistory(rows){
    body.replaceChildren(detailNode('p','修订记录是资料记录的保存版本。正文、资料字段或系统处理状态变化都可能产生修订，不等于人工修改次数。每版保存完整快照，这里只展示变化摘要。','source-detail-intro'));
    if(!Array.isArray(rows)||!rows.length){body.append(detailNode('p','没有可用的修订记录。','source-detail-empty'));return;}
    for(let index=rows.length-1;index>=0;index--){
      const row=rows[index],item=detailNode('article',undefined,'source-activity source-revision');item.dataset.versionId=row.version_id||row.id;
      activityHeading(item,'修订 r'+(row.version||index+1)+(index===rows.length-1&&!row.superseded_at?' · 当前记录':''));
      item.append(detailNode('p','保存于 '+displayTime(row.recorded_at),'source-activity-meta'));
      const changes=revisionChanges(row,rows[index-1]),list=detailNode('ul',undefined,'source-change-list');
      for(const change of changes.slice(0,8))list.append(detailNode('li',change));
      if(changes.length>8)list.append(detailNode('li','另有 '+(changes.length-8)+' 项字段变化，可展开本版原始数据核对。'));
      item.append(list,rawData(row,'查看本版完整快照'));body.append(item);
    }
  }
  function renderAssertions(rows,project){
    body.replaceChildren();
    if(!rows.length){body.textContent='此资料暂无提取断言；仅文档处理不会抽取实体与关系。';return;}
    for(const row of rows){
      const item=document.createElement('div');item.className='source-record-row';const payload=row.payload||{};
      const label=document.createElement('strong');label.textContent=(row.kind||'断言')+' · '+(row.status||'待核验');
      const text=document.createElement('p');text.textContent=payload.text||payload.name||[payload.subject,payload.predicate,payload.object??payload.value].filter(Boolean).join(' → ')||JSON.stringify(payload);
      const quote=document.createElement('p');quote.className='subtle';quote.textContent=row.quote||'';item.append(label,text,quote);
      if(row.canonical_record_id){
        const open=document.createElement('button');open.type='button';open.className='secondary';open.textContent='查看正式知识';
        open.onclick=async()=>{window.WorkspaceDrawers.close({restoreFocus:false});window.KnowledgeTabs.open('records');await window.openKnowledgeRecord?.({id:row.canonical_record_id,projectId:project});};item.append(open);
      }
      body.append(item);
    }
  }
  function load({append=false,reuse=false}={}){
    const project=current,ticket=window.WorkspaceContext?.ticket(project,'sources');
    if(!project){refresh.disabled=false;summary.textContent='尚未选择项目';message('选择项目后自动展示原文','请在左侧选择已有项目。');return false;}
    let filter,stamp;
    try{filter=scope();stamp=JSON.stringify(filter);}catch(error){refresh.disabled=false;message('筛选条件不正确',error.message);return false;}
    const state=currentFilter,cursor=append?nextCursor:null,key=JSON.stringify([project,stamp,state,cursor]);
    if(inFlight?.key===key&&inFlight.serial===serial)return inFlight.promise;
    if(reuse&&loadedProject===project&&loadedScope===stamp&&loadedState===state)return Promise.resolve(true);
    const request=++serial,accepts=()=>request===serial&&sameContext(project,stamp,ticket)&&currentFilter===state;
    closeDetail();
    if(!append){loadedProject=null;loadedScope=null;loadedState=null;allDocuments=[];directoryCounts=null;nextCursor=null;updateCounts();
      summary.textContent='正在读取当前项目的原文…';message('正在加载原文','加载完成后选择资料查看详情。');
      list.innerHTML='<div class="source-placeholder">正在加载文档目录…</div>';}
    refresh.disabled=true;loadMore.disabled=true;loadMore.hidden=!append;
    const promise=(async()=>{try{
      const query='?limit=50&summary_only=true&state='+encodeURIComponent(state)+(cursor?'&cursor='+encodeURIComponent(cursor):'');
      const result=await api('/api/projects/'+encodeURIComponent(project)+'/sources'+query,filter);
      if(!accepts())return false;
      loadedProject=project;loadedScope=stamp;loadedState=state;
      allDocuments=[...new Map([...(append?allDocuments:[]),...(result.documents||[])].map(doc=>[doc.id,doc])).values()];
      nextCursor=result.next_cursor||null;total=result.total??allDocuments.length;directoryCounts=result.counts||null;
      summary.textContent=`当前范围共 ${total} 篇文档 · 已加载 ${allDocuments.length} 篇 · 选择资料查看详情`;
      loadMore.hidden=!nextCursor;
      renderDocumentList();return true;
    }catch(error){
      if(accepts()){summary.textContent='原文加载失败';message('暂时无法读取原文',error.message+'；请点击右上角“刷新”重试。');}return false;
    }finally{if(request===serial){refresh.disabled=false;loadMore.disabled=false;if(inFlight?.serial===request)inFlight=null;}}
    })();
    inFlight={key,serial:request,promise};return promise;
  }
  createFilterUI();
  window.SourcesView={async open(){window.KnowledgeTabs?.open('sources');return load({reuse:true});}};
  // Citation deep links are explicit selections, so they may open the matching drawer.
  window.selectSource=async sourceId=>{
    if(!sourceId)throw Error('该证据没有可定位的来源');
    const citation=++citationSerial;
    if(currentFilter!=='all'){currentFilter='all';syncFilterButtons();}
    closeDetail();const project=current,stamp=scopeStamp(),loaded=await load({reuse:true}),request=serial,detailRequest=detailSerial;
    const accepts=()=>citation===citationSerial&&detailRequest===detailSerial&&request===serial&&project===current&&stamp===scopeStamp();
    if(!loaded||!accepts())throw Error('资料范围已更新，请重新选择原文');
    let doc=allDocuments.find(item=>item.id===sourceId);
    if(!doc){
      const result=await api('/api/projects/'+encodeURIComponent(project)+'/sources?limit=50&summary_only=true&state=all&document_id='+encodeURIComponent(sourceId),scope());
      if(!accepts())throw Error('资料范围已更新，请重新选择原文');
      doc=result.documents?.find(item=>item.id===sourceId);
      if(doc){allDocuments.push(doc);renderDocumentList();}
    }
    if(!doc)throw Error('当前范围没有这条原文');
    if(currentFilter!=='all'&&buildState(doc).className!==currentFilter){currentFilter='all';syncFilterButtons();renderDocumentList();}
    const button=[...list.querySelectorAll('.source-item')].find(item=>allDocuments[Number(item.dataset.source)]?.id===sourceId);
    if(!button)throw Error('当前范围没有这条原文');await openDocument(doc,button);
  };
  refresh.onclick=()=>load();loadMore.onclick=()=>load({append:true});
  // Auth resolves asynchronously; its body role classes own entry visibility.
  const upload=$('upload-source');upload.classList.add('admin-only');
  upload.onclick=()=>{if(!canWrite())return;if(window.LibraryWorkspace)window.LibraryWorkspace.open('upload');else window.KnowledgeTabs.open('ingest');};
  document.querySelector('[data-tab="sources"]')?.addEventListener('click',()=>load());
  document.addEventListener('workspace:scope-changed',event=>{if(event.detail.page==='sources'){serial++;closeDetail();if(visible())load();}});
  $('project').addEventListener('change',()=>{serial++;closeDetail();loadedProject=null;loadedScope=null;loadedState=null;allDocuments=[];directoryCounts=null;nextCursor=null;loadMore.hidden=true;updateCounts();message('正在切换项目','进入资料库后会自动加载。');if(visible())load();});
  if(visible())load();
})();
