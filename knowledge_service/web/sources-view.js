/* Automatic source browsing, with explicit loading/empty/error states. */
(() => {
  const list=$('source-list'), title=$('source-title'), body=$('source-body'), refresh=$('load-sources');
  const summary=document.createElement('p');summary.className='source-summary subtle';summary.setAttribute('aria-live','polite');
  $('tab-sources').querySelector('.source-layout').before(summary);
  const layout=$('tab-sources').querySelector('.source-layout');
  // 「原文 → 解析后映射」展示位：插在标题与正文之间，随选中文档重填。
  const meta=document.createElement('div');meta.id='source-meta';meta.className='source-meta';
  $('source-title').after(meta);
  const empty=document.createElement('div');empty.className='source-empty';empty.setAttribute('aria-live','polite');empty.hidden=true;empty.innerHTML='<div class="source-empty-icon">📄</div><h3></h3><p></p>';
  layout.append(empty);
  refresh.textContent='刷新';refresh.classList.add('secondary');
  let serial=0, selected=null, loadedProject=null, allDocuments=[];
  const visible=()=>!$('tab-sources').classList.contains('hidden');

  // 添加过滤器状态
  let currentFilter='ready'; // 默认只显示构建成功

  function message(heading,detail){empty.querySelector('h3').textContent=heading;empty.querySelector('p').textContent=detail;empty.hidden=false;layout.classList.add('is-empty');list.replaceChildren();}
  const FORMAT_LABELS={pdf:'PDF',docx:'Word',doc:'Word',md:'Markdown',markdown:'Markdown',txt:'纯文本',text:'纯文本',html:'HTML',htm:'HTML'};
  const formatLabel=format=>FORMAT_LABELS[String(format||'').toLowerCase()]||String(format||'').toUpperCase()||'未知类型';
  const sizeLabel=size=>size==null?'未知大小':`${size>=1048576?(size/1048576).toFixed(1)+' MB':(size/1024).toFixed(size<10240?1:0)+' KB'}`;
  // 一篇原文的文件信息：格式 + 文件名 + 大小 + 解析器 + 页数。
  // 这里只做「源文件加载 + 解析正文阅读」，不再展示实体/关系等派生分析
  // （用户原话：原文不是看实体和关系的解析，是源文件加载器 + 解析文本展示）。
  function renderMeta(doc,state){
    const host=$('source-meta');if(!host)return;
    const parser=doc.metadata?.document_parser||'';
    const parserLabel=parser==='builtin-text'?'纯文本直读':(parser==='docling'?'Docling 解析':(parser||'未知解析器'));
    host.innerHTML=`
      <div class="source-meta__head">
        <span class="source-format source-format--big">${esc(formatLabel(doc.metadata?.source_format))}</span>
        <span class="source-meta__file">${esc(doc.metadata?.source_file||doc.id)}</span>
        <span class="source-meta__stats">${sizeLabel(doc.metadata?.source_size_bytes)} · ${parserLabel}${doc.metadata?.page_count?` · ${doc.metadata.page_count} 页`:''}</span>
      </div>
      <div class="source-meta__mapping">
        <h4>解析正文</h4>
        <p>${state.detail} 下方即解析出来的正文全文，可直接阅读或复制。</p>
      </div>`;
  }
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
      return `<button data-source="${originalIndex}" class="source-item"><b>${esc(d.metadata?.title||d.metadata?.source_file||d.id)}</b><small><span class="source-state ${state.className}">${state.label}</span><span class="source-format">${esc(formatLabel(d.metadata?.source_format))}</span> · ${d.text.length} 字 · ${d.derived_count} 条派生知识</small></button>`;
    }).join('');

    const buttons=[...list.querySelectorAll('button')];
    buttons.forEach(button=>{
      button.addEventListener('click',()=>{
        const idx=Number(button.dataset.source);
        const d=allDocuments[idx];
        selected=d.id;
        title.textContent=d.metadata?.title||d.metadata?.source_file||d.id;
        const state=buildState(d);
        renderMeta(d,state);
        body.textContent=d.text||'旧项目未保存完整原文；可查片段与原始导入记录。';body.scrollTop=0;
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
  // 「上传源文件」＝把本地文档加载进来、解析出正文后直接可读。这里不发实体/关系分析
  // （extract:false 只解析文本），那些派生工作去「知识写入」或「本体建模层」做。
  const uploadPicker=$('upload-source'),uploadInput=$('source-file');
  if(uploadPicker&&uploadInput){
    uploadPicker.addEventListener('click',()=>uploadInput.click());
    uploadInput.addEventListener('change',async()=>{
      const files=[...uploadInput.files];if(!files.length)return;
      if(!current){summary.textContent='尚未选择项目';message('选择项目后上传源文件','请先在左侧选择已有项目，或创建项目。');return;}
      uploadPicker.disabled=true;let ok=0,failed=0;summary.textContent='正在上传并解析源文件…';
      for(const file of files){
        try{
          const form=new FormData();
          form.append('file',file,file.name);
          // extract:false＝只解析正文，不抽取实体/关系；这正是「原文」页的定位。
          form.append('options',JSON.stringify({title:file.name,extract:false}));
          await api(endpoint('/documents/upload'),form);
          ok++;
        }catch(error){failed++;summary.textContent=`上传「${file.name}」失败：${error.message}`;}
      }
      uploadInput.value='';uploadPicker.disabled=false;
      await load();
      summary.textContent=`已上传 ${ok} 份源文件${failed?`，失败 ${failed} 份`:''}；正文已解析并载入左侧列表。`;
    });
  }
  // P0（2026-10-04）：原文数据源并入「知识写入」，侧栏已无 sources 按钮——用可选链跳过绑定，
  // 避免 null.addEventListener 崩掉本页脚本。页面"上传源文件→看解析正文"能力将在 P1 搬进「知识写入」。
  document.querySelector('[data-tab="sources"]')?.addEventListener('click',load);
  for(const id of ['apply-scope','reset-scope'])$(id).addEventListener('click',()=>{if(visible())load();});
  $('project').addEventListener('change',()=>{serial++;selected=null;loadedProject=null;allDocuments=[];message('正在切换项目','进入原文数据源后会自动加载。');if(visible())load();});
  if(visible())load();
})();
