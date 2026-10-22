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
  const formatLabel=format=>FORMAT_LABELS[String(format||'').toLowerCase()]||String(format||'').toUpperCase()||'未知类型';
  const sizeLabel=size=>size==null?'未知大小':`${size>=1048576?(size/1048576).toFixed(1)+' MB':(size/1024).toFixed(size<10240?1:0)+' KB'}`;
  function buildState(doc){
    const state=doc.metadata?.status;
    if(state==='failed')return {label:'知识构建失败',className:'failed',detail:'原文已安全保留，但实体、关系和片段没有写入知识图谱。可查看后台任务定位原因后重新提交。'};
    if(state==='processing')return {label:'等待 / 处理中',className:'processing',detail:'原文已保存，知识仍在构建中。'};
    if(state==='completed'||state==='ready'||state==='succeeded')return {label:'处理完成',className:'ready',detail:'处理任务已完成；检索与派生索引状态应分别核验。'};
    return {label:'状态待核验',className:'ready',detail:'已保存原文；未获得完整处理或索引就绪状态。'};
  }
  function renderMeta(doc,state){
    const parser=doc.metadata?.document_parser||'';
    const parserLabel=parser==='builtin-text'?'纯文本直读':parser==='docling'?'Docling 解析':parser||'未知解析器';
    meta.innerHTML=`<div class="source-meta__head">
      <span class="source-format source-format--big">${esc(formatLabel(doc.metadata?.source_format))}</span>
      <span class="source-meta__file">${esc(doc.metadata?.source_file||doc.id)}</span>
      <span class="source-meta__stats">${esc(sizeLabel(doc.metadata?.source_size_bytes))} · ${esc(parserLabel)}${doc.metadata?.page_count?` · ${esc(doc.metadata.page_count)} 页`:''}</span>
    </div><div class="source-meta__mapping"><h4>解析正文</h4><p>${esc(state.detail)} 下方即解析出来的正文全文，可直接阅读或复制。</p></div>`;
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
      button.innerHTML=`<span class="source-item__name"><b>${esc(doc.metadata?.title||doc.metadata?.source_file||doc.id)}</b><small>${esc(doc.metadata?.source_file||doc.id)}</small></span>
        <span class="source-state ${state.className}">${state.label}</span><span class="source-item__type source-format">${esc(formatLabel(doc.metadata?.source_format))}</span>
        <span class="source-item__counts">${doc.text_length==null?'正文按需读取':esc(doc.text_length)+' 字'} · 派生摘要 ${esc(doc.derived_count??'待核验')} 条</span><span class="source-item__open" aria-hidden="true">查看 ›</span>`;
      button.addEventListener('click',()=>openDocument(doc,button));list.append(button);
    }
  }
  async function openDocument(doc,trigger){
    if(loadedProject!==current||loadedScope!==scopeStamp())return;
    const project=current,stamp=scopeStamp(),request=++detailSerial,ticket=window.WorkspaceContext?.ticket(project,'sources');
    selected=doc.id;activeTab='text';drawer.open({trigger});
    title.textContent=doc.metadata?.title||doc.metadata?.source_file||doc.id;body.textContent='正在读取原文…';meta.replaceChildren();
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
    title.textContent=doc.metadata?.title||doc.metadata?.source_file||doc.id;
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
        if(name==='text'){body.textContent=doc.text||'此记录未保存完整原文';return;}
        const accepts=()=>request===detailSerial&&drawer.dialog.open&&selected===id&&activeTab===name&&button.isConnected&&sameContext(project,stamp,ticket);
        body.textContent='正在读取…';
        const suffix=name==='assertions'?'/assertions?document_id='+encodeURIComponent(id):name==='runs'?'/ingest-runs?document_id='+encodeURIComponent(id):'/records/'+encodeURIComponent(id)+'/history';
        try{
          const result=await api('/api/projects/'+encodeURIComponent(project)+suffix,undefined,'GET');
          if(!accepts())return;
          if(name==='assertions')renderAssertions(result.assertions||[],project);
          else body.textContent=JSON.stringify(name==='history'?result.versions||[]:result.runs||result.items||result,null,2);
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
          await window.OntologyIntake.open({projectId:project,documentId:doc.id,trigger:modeling});
        }catch(error){window.OntologyModel?.status(error.message,true);}
      };tabs.append(modeling);
    }
    meta.append(tabs);
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
