/* Automatic source browsing, with explicit loading/empty/error states. */
(() => {
  const list=$('source-list'), title=$('source-title'), body=$('source-body'), refresh=$('load-sources');
  const summary=document.createElement('p');summary.className='source-summary subtle';summary.setAttribute('aria-live','polite');
  $('tab-sources').querySelector('.source-layout').before(summary);
  const layout=$('tab-sources').querySelector('.source-layout');
  const empty=document.createElement('div');empty.className='source-empty';empty.setAttribute('aria-live','polite');empty.hidden=true;empty.innerHTML='<div class="source-empty-icon">📄</div><h3></h3><p></p>';
  layout.append(empty);
  refresh.textContent='刷新';refresh.classList.add('secondary');
  let serial=0, selected=null, loadedProject=null, allDocuments=[];
  const visible=()=>!$('tab-sources').classList.contains('hidden');

  // 添加过滤器状态
  let currentFilter='ready'; // 默认只显示构建成功

  function message(heading,detail){empty.querySelector('h3').textContent=heading;empty.querySelector('p').textContent=detail;empty.hidden=false;layout.classList.add('is-empty');list.replaceChildren();}
  function buildState(document){
    const state=document.metadata?.status;
    if(state==='failed')return {label:'知识构建失败',className:'failed',detail:'原文已安全保留，但实体、关系和片段没有写入知识图谱。可查看后台任务定位原因后重新提交。'};
    if(state==='processing')return {label:'等待 / 处理中',className:'processing',detail:'原文已保存，知识仍在构建中。'};
    return {label:'构建成功',className:'ready',detail:'原文及其派生知识已完成写入。'};
  }

  // 创建过滤器 UI
  function createFilterUI(){
    const filterBar=document.createElement('div');
    filterBar.className='source-filter-bar';
    filterBar.innerHTML=`
      <button class="source-filter-btn active" data-filter="ready">
        <span class="filter-dot ready"></span>
        已构建 <span class="filter-count" id="ready-count">0</span>
      </button>
      <button class="source-filter-btn" data-filter="processing">
        <span class="filter-dot processing"></span>
        处理中 <span class="filter-count" id="processing-count">0</span>
      </button>
      <button class="source-filter-btn" data-filter="failed">
        <span class="filter-dot failed"></span>
        失败 <span class="filter-count" id="failed-count">0</span>
      </button>
      <button class="source-filter-btn" data-filter="all">
        全部 <span class="filter-count" id="all-count">0</span>
      </button>
    `;
    $('tab-sources').querySelector('.source-layout').before(filterBar);

    // 绑定点击事件
    filterBar.querySelectorAll('.source-filter-btn').forEach(btn=>{
      btn.addEventListener('click',()=>{
        filterBar.querySelectorAll('.source-filter-btn').forEach(b=>b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter=btn.dataset.filter;
        renderDocumentList();
      });
    });
  }

  // 渲染文档列表
  function renderDocumentList(){
    if(!allDocuments.length){
      message('当前范围暂无原文','可以切换项目、调整筛选条件，或前往"知识写入"添加文档。');
      return;
    }

    let filteredDocs=allDocuments;
    if(currentFilter!=='all'){
      filteredDocs=allDocuments.filter(d=>buildState(d).className===currentFilter);
    }

    // 更新计数
    const counts={ready:0,processing:0,failed:0};
    allDocuments.forEach(d=>{counts[buildState(d).className]++;});
    const readyEl=document.getElementById('ready-count');
    const processingEl=document.getElementById('processing-count');
    const failedEl=document.getElementById('failed-count');
    const allEl=document.getElementById('all-count');
    if(readyEl)readyEl.textContent=counts.ready;
    if(processingEl)processingEl.textContent=counts.processing;
    if(failedEl)failedEl.textContent=counts.failed;
    if(allEl)allEl.textContent=allDocuments.length;

    if(!filteredDocs.length){
      const filterLabels={ready:'已构建成功',processing:'处理中',failed:'失败',all:'所有'};
      message('当前筛选无结果',`没有${filterLabels[currentFilter]}的文档。`);
      list.replaceChildren();
      return;
    }

    // 只显示构建成功的文档（如果当前过滤器是 ready 或 all）
    // 对于其他过滤器，显示对应状态的文档
    layout.classList.remove('is-empty');empty.hidden=true;
    list.innerHTML=filteredDocs.map((d,i)=>{
      const state=buildState(d);
      const originalIndex=allDocuments.indexOf(d);
      return `<button data-source="${originalIndex}" class="source-item"><b>${esc(d.metadata?.title||d.metadata?.source_file||d.id)}</b><small><span class="source-state ${state.className}">${state.label}</span> · ${d.text.length} 字 · ${d.derived_count} 条派生知识</small></button>`;
    }).join('');

    const buttons=[...list.querySelectorAll('button')];
    buttons.forEach(button=>{
      button.addEventListener('click',()=>{
        const idx=Number(button.dataset.source);
        const d=allDocuments[idx];
        selected=d.id;
        title.textContent=d.metadata?.title||d.metadata?.source_file||d.id;
        const state=buildState(d);
        body.textContent=(state.detail+'\n\n')+(d.text||'旧项目未保存完整原文；可查片段与原始导入记录。');body.scrollTop=0;
        buttons.forEach(b=>b.setAttribute('aria-current','false'));
        button.setAttribute('aria-current','true');
      });
    });

    // 选中第一个
    if(buttons.length>0){
      const firstIdx=Number(buttons[0].dataset.source);
      buttons[0].click();
    }
  }

  async function loadOnce(){
    const request=++serial,p=current;
    if(!p){refresh.disabled=false;summary.textContent='尚未选择项目';message('选择项目后自动展示原文','请在左侧选择已有项目，或创建项目后通过"知识写入"添加文档。');return;}
    let filter,stamp;
    try{filter=scope();stamp=JSON.stringify(filter);}catch(error){message('筛选条件不正确',error.message);return;}
    if(loadedProject!==p)selected=null;
    summary.textContent='正在读取当前项目的原文…';message('正在加载原文','加载完成后自动打开第一篇文档，无需再点击加载。');
    list.innerHTML='<div class="source-placeholder">正在加载文档目录…</div>';
    refresh.disabled=true;
    try{
      const result=await api(endpoint('/sources'),filter);
      if(request!==serial||p!==current||stamp!==JSON.stringify(scope()))return;
      loadedProject=p;
      allDocuments=result.documents;

      // 创建过滤器 UI（如果还没创建）
      if(!document.querySelector('.source-filter-bar')){
        createFilterUI();
      }

      summary.textContent=`当前范围共 ${result.documents.length} 篇文档 · 左侧选择文档，右侧阅读原文`;
      renderDocumentList();
    }catch(error){if(request===serial&&p===current){summary.textContent='原文加载失败';message('暂时无法读取原文',error.message+'；请点击右上角"刷新"重试。');}}
    finally{if(request===serial)refresh.disabled=false;}
  }
  let inFlight=null;
  async function load(){
    if(inFlight)return inFlight;
    inFlight=loadOnce().finally(()=>{inFlight=null;});
    return inFlight;
  }
  // Cross-menu deep link: the knowledge chat opens the exact cited source instead of a raw record dump.
  window.selectSource=async sourceId=>{
    if(!sourceId)throw Error('该证据没有可定位的来源');
    await load();
    if(!allDocuments.some(item=>item.id===sourceId)){
      currentFilter='all';
      document.querySelectorAll('.source-filter-btn').forEach(button=>button.classList.toggle('active',button.dataset.filter==='all'));
      renderDocumentList();
    }
    const button=[...list.querySelectorAll('button')].find(item=>allDocuments[Number(item.dataset.source)]?.id===sourceId);
    if(!button)throw Error('当前范围没有这条原文');
    button.click();
  };
  refresh.onclick=load;
  document.querySelector('[data-tab="sources"]').addEventListener('click',load);
  for(const id of ['apply-scope','reset-scope'])$(id).addEventListener('click',()=>{if(visible())load();});
  $('project').addEventListener('change',()=>{serial++;selected=null;loadedProject=null;allDocuments=[];message('正在切换项目','进入原文数据源后会自动加载。');if(visible())load();});
  if(visible())load();
})();
