/* Rich exploration on the new service. No native browser prompt/confirm APIs. */
const wb={epoch:0,chart:null,tree:null,nodes:new Map(),records:new Map(),jobs:new Map()};
const term=s=>{try{const value=decodeURIComponent(String(s||'').split(/[\/#]/).pop());return value.startsWith('urn:')?value.split(':').pop():value}catch{return String(s||'')}};
function showTab(name){document.querySelector(`[data-tab="${name}"]`).click();}
function controlsProject(){if(!current)throw Error('请先选择项目');return current;}
async function scopedRead(path,extra={}){const p=controlsProject(),epoch=wb.epoch;const result=await api('/api/projects/'+encodeURIComponent(p)+path,{...scope(),...extra});if(p!==current||epoch!==wb.epoch)throw Error('项目已切换，旧响应已忽略');return result;}
function discoveryHintHost(canvasId){const canvas=$(canvasId);if(!canvas||!canvas.parentNode)return null;let host=$(canvasId+'-discovery-hint');if(!host){host=document.createElement('div');host.id=canvasId+'-discovery-hint';host.className='discovery-hint';host.hidden=true;canvas.parentNode.insertBefore(host,canvas.parentNode.firstChild);}return host;}
async function renderDiscoveryHint(){const p=current,mode=$('project').selectedOptions?.[0]?.dataset?.ontologyMode,hosts=['graph-canvas','mindmap-canvas'].map(discoveryHintHost);if(!p||mode!=='discovery'){hosts.forEach(h=>{if(h)h.hidden=true;});return;}let data;try{data=await api(endpoint('/ontology-discovery'),undefined,'GET');}catch(e){hosts.forEach(h=>{if(h)h.hidden=true;});return;}if(p!==current)return;const pending=data.unpublished_candidate_count||0;hosts.forEach(host=>{if(!host)return;if(pending>0){host.hidden=false;host.innerHTML=`<span>开放本体发现：还有 <b>${pending}</b> 条候选待审核或未通过校验。它们仍保留在候选区，不需要重新上传原文。</span><button data-goto-discovery>前往本体工作台 ↗</button>`;host.querySelector('[data-goto-discovery]').onclick=()=>window.OntologyWorkbench?.open('discover');}else host.hidden=true;});}
for(const tab of ['graph','mindmap'])document.querySelector(`[data-tab="${tab}"]`)?.addEventListener('click',()=>renderDiscoveryHint().catch(()=>{}));
const oldChange=$('project').onchange;
$('project').onchange=()=>{oldChange();wb.records.clear();for(const id of ['dashboard','source-list','source-body','resolve-result','tasks','operations','evaluation-result'])$(id).textContent='';const preferred=$('project').selectedOptions?.[0]?.dataset?.ontologyMode,mode=document.getElementById('extraction-mode');if(preferred&&mode){mode.value=preferred;mode.onchange?.();}wb.tree?.clear();renderDiscoveryHint().catch(()=>{});};
let dashboardRequest=0;
async function dashboard(){const request=++dashboardRequest,p=current,stamp=JSON.stringify(scope());const r=await scopedRead('/dashboard');if(request!==dashboardRequest||p!==current||stamp!==JSON.stringify(scope()))return;

  // 计算成功解析率
  const totalDocs=r.counts.total_documents||r.counts.document;
  const successDocs=r.counts.document;
  const successRate=totalDocs>0?Math.round(successDocs/totalDocs*100):0;

  // 构建指标卡片
  const metrics=[
    {
      key:'document',
      value:successDocs,
      label:'已解析文档',
      icon:'📄',
      highlight:true,
      extra:totalDocs!==successDocs?`/ ${totalDocs}`:'',
      rate:totalDocs!==successDocs?`${successRate}% 成功`:null
    },
    {key:'entity',value:r.counts.entity,label:'实体',icon:'📦'},
    {key:'relation',value:r.counts.relation,label:'关系',icon:'🔗'},
    {key:'chunk',value:r.counts.chunk,label:'原文片段',icon:'📝'}
  ];

  $('dashboard').innerHTML=`
    <div class="dashboard-metrics">
      ${metrics.map(m=>`
        <div class="metric-card ${m.highlight?'highlight':''}">
          <span class="metric-icon">${m.icon}</span>
          <div class="metric-value-row">
            <strong>${m.value}</strong>
            ${m.extra?`<span class="metric-extra">${m.extra}</span>`:''}
          </div>
          <span class="metric-label">${m.label}</span>
          ${m.rate?`<span class="metric-rate">${m.rate}</span>`:''}
        </div>
      `).join('')}
    </div>

    <div class="dashboard-columns">
      <div class="dashboard-section">
        <h3>实体类型</h3>
        ${Object.entries(r.types).length
          ?Object.entries(r.types).map(([k,v])=>`<div class="distribution"><span>${typeHint(k)}</span><b>${v}</b></div>`).join('')
          :'<p class="subtle">暂无实体数据</p>'}
      </div>
      <div class="dashboard-section">
        <h3>关系类型</h3>
        ${Object.entries(r.predicates).length
          ?Object.entries(r.predicates).map(([k,v])=>`<div class="distribution"><span>${typeHint(k)}</span><b>${v}</b></div>`).join('')
          :'<p class="subtle">暂无关系数据</p>'}
      </div>
    </div>

    <p class="subtle" style="margin-top:16px">有效期未知：${r.unknown_validity} 条。旧数据不会用导入时间冒充生效时间。</p>
  `;}
bind('load-dashboard',dashboard);
function initChart(){if(!window.echarts)throw Error('本地图形资源未加载');if(!wb.chart){wb.chart=echarts.init($('graph-canvas'));wb.chart.on('click',params=>{if(params.dataType==='node'){const row=wb.nodes.get(params.data.id);if(row){$('graph-node').value=row.id;$('graph-detail').innerHTML=card(row,true);$('graph-detail').querySelector('[data-history]').onclick=()=>historyFor(row);$('graph-detail').querySelector('[data-edit]').onclick=()=>editRecord(row);}}});}return wb.chart;}
// 图谱渲染器已收敛到 workspace.js 的 window.drawGraph（唯一实现，见 workspace.js:260）。
// 这里原本还有一份同名的旧 drawGraph 副本（旧样式，没有类型筛选/时间轴/命中描边），
// 它其实永远执行不到——名字槽位被后加载的 workspace.js 覆盖了——却让"改一份另一份还是旧的"反复发生，故作删除。
// #draw-graph、#graph-expand 的绑定同时移交给 workspace.js，避免同一个按钮被两个文件各绑一次。
async function historyFor(row){const p=current;const r=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/history'),undefined,'GET');if(p!==current)return;$('history').textContent=JSON.stringify(r,null,2);showTab('records');}
function editRecord(row){showTab('records');const fields=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];$('revision').value=JSON.stringify(Object.fromEntries(fields.filter(k=>row[k]!==undefined).map(k=>[k,row[k]])),null,2);$('revision-id').value=row.id;$('revision-version').value=row.version;$('keep-id').value=row.id;}
bind('load-records',async()=>{const r=await scopedRead('/records/query?limit=1000');wb.records=new Map(r.records.map(x=>[x.id,x]));$('records').innerHTML=`<p class="subtle">共 ${r.total} 条，展示 ${r.records.length} 条；超过 1000 条请使用 API 分页。</p><div class="table-scroll"><table><thead><tr><th>名称 / 内容</th><th>类型</th><th>版本 / 有效期</th><th>操作</th></tr></thead><tbody>${r.records.map(row=>`<tr><td>${esc(row.text.slice(0,100))}<small>${esc(row.id)}</small></td><td>${esc(row.kind)}<small>${typeHint(row.type)}</small></td><td>v${row.version}<small>${esc(row.valid_from||'未知')} → ${esc(row.valid_until||'未知')}</small></td><td><button data-edit="${esc(row.id)}">编辑</button> <button data-history="${esc(row.id)}" class="secondary">历史</button></td></tr>`).join('')}</tbody></table></div>`;$('records').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>editRecord(wb.records.get(b.dataset.edit)));$('records').querySelectorAll('[data-history]').forEach(b=>b.onclick=()=>historyFor(wb.records.get(b.dataset.history)));});
// 图谱入口统一走 workspace.js 的 window.drawGraph；这里显式引用，避免再依赖"后加载文件覆盖全局"的隐式约定。
bind('load-graph',async()=>{showTab('search');await window.drawGraph();});
bind('resolve-entity',async()=>{const r=await scopedRead('/resolve',{text:$('resolve-text').value,threshold:Number($('resolve-threshold').value)});const rows=r.canonical?[r.canonical]:r.candidates;$('resolve-result').innerHTML=`<p>${esc(r.status)} · ${esc(r.backend)}</p>`+rows.map(x=>`<div class="candidate"><strong>${esc(x.text)}</strong> ${typeHint(x.type)} ${x.score!==undefined?'· '+x.score.toFixed(3):''}<small>${esc(x.id)} · v${x.version}</small><button data-keep="${esc(x.id)}">设为保留实体</button> <button data-drop="${esc(x.id)}" class="secondary">设为合并实体</button></div>`).join('');for(const row of rows)wb.records.set(row.id,row);$('resolve-result').querySelectorAll('[data-keep]').forEach(b=>b.onclick=()=>{$('keep-id').value=b.dataset.keep;});$('resolve-result').querySelectorAll('[data-drop]').forEach(b=>b.onclick=()=>{$('drop-id').value=b.dataset.drop;});});
async function latest(id){const p=current;const r=await api(endpoint('/records/'+encodeURIComponent(id)+'/history'),undefined,'GET');if(p!==current)throw Error('项目已切换');return r.versions[r.versions.length-1];}
bind('add-alias',async()=>{const id=$('keep-id').value;const row=await latest(id);await api(endpoint('/aliases'),{entity_id:id,alias:$('alias-name').value,expected_version:row.version});status('别名已保存，消歧可直接命中规范实体。');});
bind('merge-entities',async()=>{if(!$('merge-confirm').checked)throw Error('请先勾选合并确认');const keep=$('keep-id').value,drop=$('drop-id').value;const a=await latest(keep),b=await latest(drop);const r=await api(endpoint('/merge'),{keep_id:keep,drop_id:drop,expected_versions:{[keep]:a.version,[drop]:b.version}});$('merge-confirm').checked=false;status('Semantica 融合完成，操作 '+r.operation_id+'；可在操作历史撤销。');await operations();});
bind('soft-delete',async()=>{if(!$('delete-confirm').checked)throw Error('请勾选软删除确认');const id=$('revision-id').value;const r=await api(endpoint('/delete'),{record_id:id,expected_version:Number($('revision-version').value)});$('delete-confirm').checked=false;status(`已软删除 ${r.deleted} 条记录（含关联关系）；可在操作历史撤销。`);await operations();});
bind('restore-version',async()=>{const r=await api(endpoint('/restore'),{record_id:$('revision-id').value,version:Number($('restore-number').value),expected_version:Number($('revision-version').value)});$('revision-version').value=r.version;status('历史内容已恢复为新版本 '+r.version);});
async function operations(){const p=current,r=await api(endpoint('/operations'),undefined,'GET');if(p!==current)return;$('operations').innerHTML=r.operations.map(x=>`<div class="candidate"><b>${esc(x.metadata.operation)}</b> · ${esc(x.recorded_at)}<small>${esc(x.metadata.operation_id)}</small><button data-undo="${esc(x.metadata.operation_id)}">撤销该操作</button></div>`).join('')||'<p>暂无治理操作。</p>';$('operations').querySelectorAll('[data-undo]').forEach(b=>b.onclick=async()=>{try{const r=await api(endpoint('/operations/'+b.dataset.undo+'/undo'),{});status('已恢复 '+r.restored+' 条记录。');await operations();}catch(e){status(e.message,true);}});}
bind('load-operations',operations);
bind('load-sources',async()=>{const r=await scopedRead('/sources');$('source-list').innerHTML=r.documents.map((d,i)=>`<button data-source="${i}" class="source-item">${esc(d.metadata.title||d.metadata.source_file||d.id)}<small>${d.text.length} 字 · ${d.derived_count} 条派生知识</small></button>`).join('');$('source-list').querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>{const d=r.documents[Number(b.dataset.source)];$('source-body').textContent=d.text||'旧项目未保存完整原文；可查片段与原始导入记录。';$('source-title').textContent=d.metadata.title||d.id;});if(r.documents.length)$('source-list').querySelector('button').click();});
bind('draw-mindmap',async()=>{const r=await scopedRead('/mindmap',{root_id:$('mindmap-root').value,depth:Number($('mindmap-depth').value)});if(!wb.tree)wb.tree=echarts.init($('mindmap-canvas'));const convert=n=>({name:n.text+(n.predicate?' · '+labelOf(n.predicate):''),id:n.id,children:n.children.map(convert)});wb.tree.setOption({tooltip:{formatter:p=>esc(p.name)},series:[{type:'tree',data:[convert(r.tree)],top:'8%',left:'20%',bottom:'8%',right:'20%',symbolSize:9,roam:true,label:{position:'right',verticalAlign:'middle',align:'left'},leaves:{label:{position:'right',align:'left'}},expandAndCollapse:true,initialTreeDepth:3}]},true);wb.tree.resize();renderDiscoveryHint().catch(()=>{});});
async function jobList(){const r=await api('/api/jobs',undefined,'GET');

  // 统计各状态任务数量
  const stats={queued:0,running:0,completed:0,failed:0,interrupted:0};
  r.jobs.forEach(j=>{if(stats[j.status]!==undefined)stats[j.status]++;});

  // 任务类型图标映射
  const getTaskIcon=(kind)=>{
    if(kind.includes('document')||kind.includes('ingest'))return {class:'document',emoji:'📄'};
    if(kind.includes('import'))return {class:'import',emoji:'📥'};
    if(kind.includes('index')||kind.includes('vector'))return {class:'index',emoji:'🔍'};
    return {class:'other',emoji:'⚙️'};
  };

  // 状态中文映射
  const getStatusLabel=(status)=>{
    const map={queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断'};
    return map[status]||status;
  };

  // 进度条颜色
  const getProgressClass=(progress)=>{
    if(progress>=100)return 'complete';
    if(progress>=70)return 'high';
    if(progress>=40)return 'medium';
    return 'low';
  };

  // 渲染统计栏
  const statsHtml=`
    <div class="tasks-stats">
      ${stats.queued?`<div class="tasks-stat"><span class="tasks-stat-dot queued"></span><span class="tasks-stat-label">排队</span><span class="tasks-stat-value">${stats.queued}</span></div>`:''}
      ${stats.running?`<div class="tasks-stat"><span class="tasks-stat-dot running"></span><span class="tasks-stat-label">运行中</span><span class="tasks-stat-value">${stats.running}</span></div>`:''}
      ${stats.completed?`<div class="tasks-stat"><span class="tasks-stat-dot completed"></span><span class="tasks-stat-label">完成</span><span class="tasks-stat-value">${stats.completed}</span></div>`:''}
      ${stats.failed?`<div class="tasks-stat"><span class="tasks-stat-dot failed"></span><span class="tasks-stat-label">失败</span><span class="tasks-stat-value">${stats.failed}</span></div>`:''}
    </div>
  `;

  // 渲染任务列表
  const tasksHtml=r.jobs.length?r.jobs.map(j=>{
    const icon=getTaskIcon(j.kind);
    const progress=j.progress||0;
    const progressClass=getProgressClass(progress);
    const logs=(j.logs||[]).join('\n');
    const taskId='task-'+j.id.replace(/[^a-zA-Z0-9]/g,'-');

    return `
      <article class="task-card" data-task-id="${esc(j.id)}">
        <div class="task-header">
          <div class="task-title">
            <div class="task-icon ${icon.class}">${icon.emoji}</div>
            <div>
              <div class="task-name">${esc(j.kind)}</div>
              <div class="task-type">ID: ${esc(j.id.slice(0,12))}...</div>
            </div>
          </div>
          <span class="task-status-badge ${j.status}">
            <span class="task-status-dot"></span>
            ${getStatusLabel(j.status)}
          </span>
        </div>

        <div class="task-progress-section">
          <div class="task-progress-header">
            <span class="task-progress-label">处理进度</span>
            <span class="task-progress-value">${progress}%</span>
          </div>
          <div class="task-progress-bar">
            <div class="task-progress-fill ${progressClass}" style="width:${progress}%"></div>
          </div>
        </div>

        ${logs?`
          <div class="task-logs">
            <div class="task-logs-header">
              <span class="task-logs-title">执行日志</span>
              <button class="task-logs-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">展开/收起</button>
            </div>
            <div class="task-logs-content" style="display:none">${esc(logs)}</div>
          </div>
        `:''}

        ${j.error?`
          <div class="task-error">
            <span class="task-error-icon">⚠️</span>${esc(j.error)}
          </div>
        `:''}

        ${j.result?`
          <div class="task-result">
            <button class="task-result-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">
              📋 查看任务结果
            </button>
            <div class="task-result-content" style="display:none">${esc(JSON.stringify(j.result,null,2).slice(0,30000))}</div>
          </div>
        `:''}
      </article>
    `;
  }).join(''):`
    <div class="tasks-empty">
      <div class="tasks-empty-icon">📭</div>
      <div class="tasks-empty-title">暂无后台任务</div>
      <div class="tasks-empty-text">通过"知识写入"提交文档后，解析任务会在这里显示</div>
    </div>
  `;

  $('tasks').innerHTML=`<div class="tasks-container">${statsHtml}${tasksHtml}</div>`;
  return r.jobs;
}
function watch(job){wb.jobs.set(job.id,job);jobList();}
bind('load-jobs',jobList);
setInterval(async()=>{if(!wb.jobs.size)return;try{const jobs=await jobList();for(const j of jobs){if(wb.jobs.has(j.id)&&!['queued','running'].includes(j.status)){wb.jobs.delete(j.id);if(j.status==='completed'){await projects();if(j.result?.project){$('project').value=j.result.project.id;$('project').onchange();status('导入完成：'+j.result.project.name+'。进入图谱页查看。');}}}}}catch(e){status(e.message,true);}},3000);
function readParseSettings(){
  const settings={chunk_strategy:$('parse-strategy').value,chunk_size:Number($('parse-size').value),chunk_overlap:Number($('parse-overlap').value),resolve_entities:$('parse-resolve').checked,auto_merge:$('parse-merge').checked,merge_threshold:Number($('parse-threshold').value)};
  if(!Number.isInteger(settings.chunk_size)||settings.chunk_size<100||settings.chunk_size>10000)throw Error('最大字符数需为 100–10000 的整数');
  if(!Number.isInteger(settings.chunk_overlap)||settings.chunk_overlap<0||settings.chunk_overlap>2000||settings.chunk_overlap>=settings.chunk_size)throw Error('重叠字符数需为 0–2000 的整数，且小于最大字符数');
  if(settings.auto_merge&&!settings.resolve_entities)throw Error('请先开启实体消歧，再开启语义合并');
  if(!Number.isFinite(settings.merge_threshold)||settings.merge_threshold<0||settings.merge_threshold>1)throw Error('合并阈值需在 0–1 之间');
  return settings;
}
const chunkPreviewState={chunks:[],selected:0,trigger:null};
function chunkPreviewBackground(active){
  for(const element of [document.querySelector('body>aside'),document.querySelector('body>main')]){
    if(!element)continue;
    if(active)element.setAttribute('inert','');
    else element.removeAttribute('inert');
  }
}
function chunkPreviewFocusable(){
  const drawer=$('chunk-preview').querySelector('.chunk-preview-drawer');
  return [...drawer.querySelectorAll('button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(element=>!element.hidden&&element.getAttribute('aria-hidden')!=='true');
}
function closeChunkPreview(){
  const host=$('chunk-preview');
  if(host.hidden)return;
  host.hidden=true;
  document.documentElement.classList.remove('chunk-preview-open');
  document.body.classList.remove('chunk-preview-open');
  chunkPreviewBackground(false);
  const trigger=chunkPreviewState.trigger;
  chunkPreviewState.trigger=null;
  if(trigger?.isConnected&&!trigger.disabled)trigger.focus();
}
function openChunkPreview(){
  const host=$('chunk-preview');
  chunkPreviewState.trigger=$('preview-chunks');
  host.hidden=false;
  document.documentElement.classList.add('chunk-preview-open');
  document.body.classList.add('chunk-preview-open');
  chunkPreviewBackground(true);
  $('chunk-preview-close').focus();
}
function selectChunkPreview(index){
  const chunk=chunkPreviewState.chunks[index];
  if(!chunk)return;
  chunkPreviewState.selected=index;
  const buttons=[...$('chunk-preview-list').querySelectorAll('button')];
  buttons.forEach((button,buttonIndex)=>{
    const selected=buttonIndex===index;
    button.classList.toggle('selected',selected);
    if(selected)button.setAttribute('aria-current','true');
    else button.removeAttribute('aria-current');
  });
  const content=$('chunk-preview-content');
  const heading=document.createElement('div');
  heading.className='chunk-preview-current';
  const title=document.createElement('strong');
  title.textContent=`片段 ${index+1}`;
  const meta=document.createElement('span');
  const start=Number(chunk.start_char)||0,end=Number(chunk.end_char)||0;
  meta.textContent=`原文位置 ${start}–${end} · ${Math.max(0,end-start)} 字符`;
  heading.append(title,meta);
  const text=document.createElement('pre');
  text.textContent=String(chunk.text??'');
  content.replaceChildren(heading,text);
}
function renderChunkPreview(result,title){
  const chunks=Array.isArray(result?.chunks)?result.chunks:[];
  chunkPreviewState.chunks=chunks;
  chunkPreviewState.selected=0;
  const summary=$('chunk-preview-summary');
  summary.replaceChildren();
  for(const value of [title,`共 ${Number(result?.total)||0} 片`,`本次预览 ${chunks.length} 片`]){
    const chip=document.createElement('span');
    chip.className='chunk-preview-chip';
    chip.textContent=String(value||'未命名内容');
    summary.append(chip);
  }
  const list=$('chunk-preview-list'),content=$('chunk-preview-content');
  list.replaceChildren();
  content.replaceChildren();
  if(!chunks.length){
    const empty=document.createElement('div');
    empty.className='chunk-preview-empty';
    empty.textContent='未生成切片，请调整切片设置后重试。';
    content.append(empty);
    return;
  }
  chunks.forEach((chunk,index)=>{
    const button=document.createElement('button');
    button.type='button';
    button.setAttribute('aria-controls','chunk-preview-content');
    const start=Number(chunk.start_char)||0,end=Number(chunk.end_char)||0;
    button.textContent=`片段 ${index+1} · ${Math.max(0,end-start)} 字符 · ${start}–${end}`;
    button.addEventListener('click',()=>selectChunkPreview(index));
    list.append(button);
  });
  selectChunkPreview(0);
}
function trapChunkPreviewFocus(event){
  const host=$('chunk-preview');
  if(host.hidden)return;
  if(event.key==='Escape'){
    event.preventDefault();
    closeChunkPreview();
    return;
  }
  if(event.key!=='Tab')return;
  const focusable=chunkPreviewFocusable();
  if(!focusable.length)return;
  const current=focusable.indexOf(document.activeElement);
  let next=current+(event.shiftKey?-1:1);
  if(current<0)next=event.shiftKey?focusable.length-1:0;
  else if(next<0)next=focusable.length-1;
  else if(next>=focusable.length)next=0;
  event.preventDefault();
  focusable[next].focus();
}
$('chunk-preview-close').addEventListener('click',closeChunkPreview);
$('chunk-preview').addEventListener('click',event=>{if(event.target===$('chunk-preview'))closeChunkPreview();});
document.addEventListener('keydown',trapChunkPreviewFocus);
bind('preview-chunks',async()=>{
  const p=controlsProject(),settings=readParseSettings();
  const documents=await readDocumentBatch(true),doc=documents[0];
  let result;
  if(doc.file){
    const form=new FormData();form.append('file',doc.file,doc.file.name);
    form.append('options',JSON.stringify({title:doc.title,metadata:doc.metadata,...settings}));
    result=await api('/api/projects/'+encodeURIComponent(p)+'/documents/upload/preview',form);
  }else result=await api('/api/projects/'+encodeURIComponent(p)+'/documents/preview',{title:doc.title,text:doc.text,...settings});
  if(p!==current)return;
  renderChunkPreview(result,doc.title);
  openChunkPreview();
  status('切片预览完成，未调用模型或写入知识');
});
bind('ingest',async()=>{
  const project=controlsProject(),mode=document.getElementById('extraction-mode').value,extract=mode!=='documents',settings=readParseSettings();
  const documents=await readDocumentBatch(),results=$('doc-submit-results');
  results.textContent='';let accepted=0,failed=0;
  for(const document of documents){
    const entry=window.document.createElement('p');results.append(entry);
    entry.textContent=document.title+' · 正在提交…';
    try{
      let job;
      if(document.file){
        window.DocumentUploadQueue.setStatus(document.file,'uploading');
        const form=new FormData();form.append('file',document.file,document.file.name);
        form.append('options',JSON.stringify({title:document.title,metadata:document.metadata,extract,extraction_mode:mode,...settings}));
        job=await api('/api/projects/'+encodeURIComponent(project)+'/documents/upload/jobs',form);
        window.DocumentUploadQueue.setStatus(document.file,'queued',job.id);
      }else job=await api('/api/projects/'+encodeURIComponent(project)+'/documents/jobs',{title:document.title,text:document.text,metadata:document.metadata,extract,extraction_mode:mode,...settings});
      wb.jobs.set(job.id,job);accepted++;entry.textContent=document.title+' · 已加入解析队列 · '+job.id;
    }catch(error){if(document.file)window.DocumentUploadQueue.setStatus(document.file,'failed',error.message);failed++;entry.textContent=document.title+' · 提交失败：'+error.message;}
  }
  if(accepted){showTab('jobs');await jobList();}
  status(`已提交 ${accepted} 个文档，提交失败 ${failed} 个。解析进度请查看“后台任务”。`,failed>0);
});
async function snapshots(){const p=current,r=await api(endpoint('/snapshots'),undefined,'GET');if(p!==current)return;$('snapshot-list').innerHTML=r.snapshots.map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.created_at)}</option>`).join('');const container=$('snapshot-cards');if(!r.snapshots.length){container.innerHTML='<div class="eval-snapshot-empty"><p>📋</p><p>还没有快照，先保存一个快照</p></div>';return;}const items=r.snapshots.map(s=>{const isAuto=s.kind==='auto'||s.name.startsWith('Before restoring ')||s.name.startsWith('恢复前自动备份 · ');let displayName=s.name;if(isAuto)displayName=s.name.replace(/^Before restoring /,'').replace(/^恢复前自动备份 · /,'');return{s,isAuto,displayName};});const nameCounts={};items.forEach(i=>{nameCounts[i.displayName]=(nameCounts[i.displayName]||0)+1;});items.sort((a,b)=>{if(a.isAuto!==b.isAuto)return a.isAuto?1:-1;return new Date(b.s.created_at)-new Date(a.s.created_at);});container.innerHTML='<div class="eval-snapshot-cards">'+items.map(({s,isAuto,displayName})=>{const dt=new Date(s.created_at);const dateStr=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0')+' '+String(dt.getHours()).padStart(2,'0')+':'+String(dt.getMinutes()).padStart(2,'0');const diffMs=Date.now()-dt.getTime();let rel='';if(diffMs>=0&&diffMs<60000)rel='刚刚';else if(diffMs<3600000)rel=Math.floor(diffMs/60000)+' 分钟前';else if(diffMs<86400000)rel=Math.floor(diffMs/3600000)+' 小时前';else if(diffMs<2592000000)rel=Math.floor(diffMs/86400000)+' 天前';const badge=isAuto?'<span class="eval-snapshot-badge auto">自动备份</span>':'<span class="eval-snapshot-badge manual">手动快照</span>';const recordCount=s.record_count!=null?'<span class="eval-snapshot-records">· '+s.record_count+' 条记录</span>':'';const idSuffix=nameCounts[displayName]>1?'<span class="eval-snapshot-id">#'+esc(s.id.slice(0,6))+'</span>':'';return '<div class="eval-snapshot-card" data-id="'+esc(s.id)+'"><div class="eval-snapshot-header"><span class="eval-snapshot-name">'+esc(displayName)+'</span>'+badge+'</div><div class="eval-snapshot-meta">'+esc(dateStr)+(rel?' · '+esc(rel):'')+idSuffix+recordCount+'</div><div class="eval-snapshot-actions"><button data-snapshot-restore="'+esc(s.id)+'" class="secondary">恢复</button></div></div>';}).join('')+'</div>';container.querySelectorAll('[data-snapshot-restore]').forEach(b=>{b.onclick=async()=>{const id=b.dataset.snapshotRestore;const card=b.closest('.eval-snapshot-card');const actions=b.closest('.eval-snapshot-actions');if(card.querySelector('.eval-restore-confirm'))return;const snap=items.find(i=>i.s.id===id);const displayName=snap?snap.displayName:'';const warning=document.createElement('div');warning.className='eval-restore-confirm';warning.innerHTML='<p>确定恢复到此快照？<br><span class="subtle">恢复前会自动创建恢复点，可随时撤销。</span></p><div class="eval-confirm-actions"><button class="eval-confirm-yes">确认恢复</button><button class="eval-confirm-no secondary">取消</button></div>';card.appendChild(warning);warning.scrollIntoView({behavior:'smooth',block:'nearest'});actions.style.display='none';warning.querySelector('.eval-confirm-yes').onclick=async()=>{const yesBtn=warning.querySelector('.eval-confirm-yes');const noBtn=warning.querySelector('.eval-confirm-no');yesBtn.disabled=true;noBtn.disabled=true;yesBtn.textContent='正在恢复…';yesBtn.classList.add('eval-restoring');try{const r=await api(endpoint('/snapshots/'+id+'/restore'),{});status('已恢复到「'+displayName+'」；恢复前状态已存为「'+(r.recovery_snapshot_name||'恢复前自动备份')+'」');await snapshots();}catch(e){status(e.message,true);yesBtn.disabled=false;noBtn.disabled=false;yesBtn.textContent='确认恢复';yesBtn.classList.remove('eval-restoring');}};warning.querySelector('.eval-confirm-no').onclick=()=>{warning.remove();actions.style.display='';};};});}
bind('load-snapshots',snapshots);bind('save-snapshot',async()=>{await api(endpoint('/snapshots'),{name:$('snapshot-name').value||'手动快照'});$('snapshot-name').value='';await snapshots();status('项目快照已保存。');});
bind('restore-snapshot',async()=>{if(!$('snapshot-confirm').checked)throw Error('请勾选恢复确认');const id=$('snapshot-list').value;if(!id)throw Error('选择快照');const opt=$('snapshot-list').selectedOptions?.[0];const name=opt?opt.textContent.split(' · ').slice(0,-1).join(' · '):id;const r=await api(endpoint('/snapshots/'+id+'/restore'),{});$('snapshot-confirm').checked=false;status('已恢复到「'+name+'」；恢复前状态已存为「'+(r.recovery_snapshot_name||'恢复前自动备份')+'」');await snapshots();});
bind('run-evaluation',async()=>{const input=$('gold-json').value.trim();if(!input)throw Error('请输入标准答案 JSON');let parsed;try{parsed=JSON.parse(input);}catch(e){throw Error('JSON 格式错误：'+e.message);}if(!parsed.entities||!Array.isArray(parsed.entities))throw Error('JSON 缺少 entities 数组');if(!parsed.relations||!Array.isArray(parsed.relations))throw Error('JSON 缺少 relations 数组');$('gold-json-error').textContent='';const r=await scopedRead('/evaluate',json('gold-json'));$('evaluation-result').innerHTML=renderEvalResult(r);});
bind('export-project',async()=>{status('正在导出…');const r=await api(endpoint('/export'),undefined,'GET');const blob=new Blob([JSON.stringify(r,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=(current||'knowledge-project')+'.json';document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);status('项目 JSON 已导出为文件。');});
$('eval-load-example').onclick=()=>{$('gold-json').value=JSON.stringify({entities:["商户","平台","监管部门"],relations:[["商户","onboards","平台"],["商户","complies_with","监管部门"],["平台","oversees","商户"]]},null,2);$('gold-json-error').textContent='';status('已载入示例标准答案。');};
function renderEvalResult(r){function metricCard(title,data){const p=typeof data.precision==='number'?data.precision:0;const rl=typeof data.recall==='number'?data.recall:0;const f1=typeof data.f1==='number'?data.f1:0;const pp=Math.round(p*100),rp=Math.round(rl*100),fp=Math.round(f1*100);const cls=v=>v>=80?'':v>=50?' mid':' low';const formatItem=item=>{if(Array.isArray(item))return esc(item.join(' → '));return esc(String(item));};let html='<div class="eval-metric-group"><h3>'+esc(title)+'</h3>';html+='<div class="eval-metric-row">';html+='<div class="eval-metric"><div class="eval-metric-value'+cls(pp)+'">'+pp+'%</div><div class="eval-metric-label">Precision</div><div class="eval-metric-sub">'+(data.predicted||0)+' 中 '+data.true_positive+' 准确</div></div>';html+='<div class="eval-metric"><div class="eval-metric-value'+cls(rp)+'">'+rp+'%</div><div class="eval-metric-label">Recall</div><div class="eval-metric-sub">'+(data.gold||0)+' 中 '+data.true_positive+' 命中</div></div>';html+='<div class="eval-metric"><div class="eval-metric-value'+cls(fp)+'">'+fp+'%</div><div class="eval-metric-label">F1</div></div>';html+='</div>';html+='<div class="eval-progress"><div class="eval-progress-fill'+cls(fp)+'" style="width:'+fp+'%"></div></div>';if(data.missing&&data.missing.length){html+='<div class="eval-list-section"><div class="eval-list-label">漏项 ('+data.missing.length+')</div><div class="eval-list">'+data.missing.map(i=>'<div class="eval-list-item">'+formatItem(i)+'</div>').join('')+'</div></div>';}else{html+='<div class="eval-list-section"><div class="eval-list-label">漏项</div><div class="eval-list-empty">无</div></div>';}if(data.extra&&data.extra.length){html+='<div class="eval-list-section"><div class="eval-list-label">多余 ('+data.extra.length+')</div><div class="eval-list">'+data.extra.map(i=>'<div class="eval-list-item">'+formatItem(i)+'</div>').join('')+'</div></div>';}else{html+='<div class="eval-list-section"><div class="eval-list-label">多余</div><div class="eval-list-empty">无</div></div>';}html+='</div>';return html;}let html='<div class="eval-metrics">'+metricCard('实体',r.entities||{})+metricCard('关系',r.relations||{})+'</div>';html+='<details class="eval-raw-json"><summary>原始 JSON</summary><pre>'+esc(JSON.stringify(r,null,2))+'</pre></details>';return html;}
/* Knowledge chat: one independent request per turn, decoupled from the search workspace. */
(()=>{
  const chat=window.KnowledgeChat={};
  const provenance=window.ProvenanceDrawer;
  const $c=id=>document.getElementById(id);
  const BOUNDARY=/[。！？!?；;]/;
  const GRAPH_HOPS=2;

  // Pure helpers: the SSE contract is tested directly instead of through the network.
  chat.splitSseBuffer=buffer=>{
    const parts=String(buffer??'').split(/\r?\n\r?\n/),rest=parts.pop()??'',events=[];
    for(const block of parts){const parsed=chat.parseSseBlock(block);if(parsed)events.push(parsed);}
    return {events,rest};
  };
  chat.parseSseBlock=block=>{
    let name='message';const data=[];
    for(const line of String(block??'').split(/\r?\n/)){
      if(!line||line.startsWith(':'))continue;
      const at=line.indexOf(':'),field=at<0?line:line.slice(0,at);
      let value=at<0?'':line.slice(at+1);
      if(value.startsWith(' '))value=value.slice(1);
      if(field==='event')name=value;else if(field==='data')data.push(value);
    }
    if(!data.length)return null;
    const raw=data.join('\n');
    try{return {event:name,data:JSON.parse(raw)};}catch{return {event:'malformed',raw};}
  };
  chat.completeSentences=text=>{
    const value=String(text??'');let cut=-1;
    for(let i=0;i<value.length;i++)if(BOUNDARY.test(value[i]))cut=i;
    return cut<0?{announce:'',rest:value}:{announce:value.slice(0,cut+1),rest:value.slice(cut+1)};
  };

  const transcript=$c('qa-transcript'),hero=$c('qa-hero'),input=$c('qa-query'),send=$c('qa');
  const progress=$c('qa-progress'),summary=$c('qa-summary'),live=$c('qa-live');
  let serial=0,busy=false,composing=false,controller=null,evidenceSerial=0;
  // ── 会话记录持久化：localStorage 按项目隔离，刷新后恢复，清空按钮删除 ──
  const historyKey=()=>'kg_qa_v1_'+current;
  let qaHistory=(()=>{try{const v=JSON.parse(localStorage.getItem(historyKey()));return Array.isArray(v)?v:[];}catch{return [];}})();
  const persist=()=>{try{localStorage.setItem(historyKey(),JSON.stringify(qaHistory));}catch{}};
  const evidenceSummary=rows=>(Array.isArray(rows)?rows:[]).map(row=>
    provenance.sanitizeEvidenceSummary({...row,text_preview:row?.text_preview??row?.text}));
  const answerContext=item=>({projectId:current,answerId:item.answer_id,evidence:item.evidence});

  const atEnd=()=>{const box=$c('qa-scroll');if(box)box.scrollTop=box.scrollHeight;};
  function addTurn(kind,text){
    const turn=document.createElement('li');
    turn.className='qa-turn qa-turn-'+kind;turn.dataset.role=kind;
    if(kind==='user'){const bubble=document.createElement('p');bubble.className='qa-bubble';bubble.textContent=text;turn.append(bubble);}
    else{const answer=document.createElement('div');answer.className='qa-answer';answer.dataset.answer='';turn.append(answer);}
    transcript.append(turn);hero.hidden=true;atEnd();return turn;
  }
  function loadHistory(){
    try{const v=JSON.parse(localStorage.getItem(historyKey()));qaHistory=Array.isArray(v)?v:[];}catch{qaHistory=[];}
    transcript.replaceChildren();
    qaHistory.forEach(item=>{
      const turn=addTurn(item.kind,item.kind==='user'?item.text:'');
      if(item.kind==='assistant'){
        const answer=turn.querySelector('.qa-answer');
        turn.dataset.state=item.error?'error':'done';
        if(item.answer_id){
          const evidence=evidenceSummary(item.evidence);
          provenance.decorateAnswer(answer,item.text||'',answerContext({...item,evidence}));
          addEvidence(turn,{...item,evidence});
        }else{
          if(answer)answer.textContent=item.text||'';
          provenance.renderLegacyNote(turn);
        }
        if(item.error){
          turn.dataset.state='error';
          const box=document.createElement('div');box.className='qa-error';box.setAttribute('role','alert');
          const t=document.createElement('span');t.className='qa-error-text';t.textContent='回答失败：'+item.error;
          box.append(t);turn.append(box);
        }
      }
    });
    hero.hidden=qaHistory.length>0;
    if(qaHistory.length)atEnd();
  }
  function addEvidence(turn,payload){
    const rows=Array.isArray(payload?.evidence)?payload.evidence:[];if(!rows.length)return;
    const panelId='qa-evidence-'+(++evidenceSerial),block=document.createElement('div');block.className='qa-evidence-block';
    const toggle=document.createElement('button');
    toggle.type='button';toggle.className='qa-evidence-toggle secondary';
    toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',panelId);
    toggle.textContent='查看 '+rows.length+' 条依据';
    const panel=document.createElement('div');panel.className='qa-evidence-panel';panel.id=panelId;panel.hidden=true;
    const projectId=current,answerId=payload.answer_id;
    for(const row of rows){
      const article=document.createElement('article');article.className='qa-evidence-item';
      const citation=provenance.sanitizeEvidenceSummary(row).citation;
      const link=document.createElement(answerId&&citation?'button':'b');
      link.className='qa-citation';link.textContent='['+(row.citation||'')+']';
      if(answerId&&citation){
        link.type='button';link.className+=' secondary provenance-citation';
        link.setAttribute('aria-label','查看引用 '+citation+' 的证据溯源');
        link.onclick=()=>provenance.open({projectId,answerId,citation,trigger:link});
      }
      const excerpt=document.createElement('p');excerpt.textContent=row.text_preview??row.text??'';
      const meta=document.createElement('p');meta.className='qa-evidence-meta subtle';
      meta.textContent=(row.kind||'')+' · '+(labelOf(row.type)||'—')+' · 版本 '+(row.version??'—');
      article.append(link,excerpt,meta);
      const seed=row.kind==='entity'?row.id:(row.kind==='relation'?row.subject_id:'');
      const actions=document.createElement('div');actions.className='row';
      if(seed){const button=document.createElement('button');button.type='button';button.dataset.evidenceNode=seed;button.textContent='在图谱中查看';actions.append(button);}
      if(row.source_id){const button=document.createElement('button');button.type='button';button.className='secondary';button.dataset.evidenceSource=row.source_id;button.textContent='查看来源';actions.append(button);}
      if(actions.childElementCount)article.append(actions);
      panel.append(article);
    }
    toggle.onclick=()=>{const open=toggle.getAttribute('aria-expanded')==='true';toggle.setAttribute('aria-expanded',String(!open));panel.hidden=open;};
    panel.querySelectorAll('[data-evidence-node]').forEach(node=>{node.onclick=async()=>{
      showTab('search');
      try{await window.drawGraph(node.dataset.evidenceNode,GRAPH_HOPS);}catch(error){status(error.message,true);}
      $c('graph-heading')?.focus();
    };});
    panel.querySelectorAll('[data-evidence-source]').forEach(node=>{node.onclick=async()=>{
      showTab('sources');
      try{await window.selectSource?.(node.dataset.evidenceSource);}catch(error){status(error.message,true);}
    };});
    block.append(toggle,panel);turn.append(block);atEnd();
  }
  function addFailure(turn,message,replay){
    turn.dataset.state='error';
    const box=document.createElement('div');box.className='qa-error';box.setAttribute('role','alert');
    const text=document.createElement('span');text.className='qa-error-text';text.textContent='回答失败：'+message;
    const retry=document.createElement('button');retry.type='button';retry.className='qa-retry';retry.textContent='重试';
    retry.onclick=()=>{if(!busy)replay();};
    box.append(text,retry);turn.append(box);retry.focus();atEnd();
  }
  async function stream(body,turn,historyIndex=qaHistory.length){
    if(busy)return;
    const project=current,epoch=wb.epoch,mine=++serial;
    if(turn)provenance.close(false);
    const record={kind:'assistant',text:''};
    // Reserve the retry slot in memory; only terminal answers belong in saved history.
    qaHistory[historyIndex]=record;
    const capture=payload=>{
      for(const key of ['answer_id','retrieval_run_id'])if(typeof payload?.[key]==='string'&&payload[key])record[key]=payload[key];
    };
    busy=true;send.disabled=true;controller=new AbortController();
    progress.textContent='正在检索证据…';summary.textContent='';
    // 开启 LLM 组织答案时，检索完证据后还要等模型生成，明确提示避免误以为卡住
    if($c('generate').checked)progress.textContent='正在检索证据…（勾选了 LLM 组织答案，检索完还会等待模型生成，可能需数秒~数十秒）';
    const target=turn||addTurn('assistant','');
    target.dataset.state='streaming';target.replaceChildren();
    const answer=document.createElement('div');answer.className='qa-answer';answer.dataset.answer='';target.append(answer);
    live.textContent='';
    let buffer='',spoken='',pending='',text='',finished=false,failed=false;
    const speak=force=>{
      const step=chat.completeSentences(pending);
      if(step.announce){spoken+=step.announce;live.textContent=spoken;}
      pending=step.rest;
      if(force&&pending){live.textContent=spoken+pending;pending='';}
    };
    const handle=item=>{
      if(item.event==='malformed')throw Error('服务返回了无法解析的流数据');
      if(item.event==='error')throw Error(item.data?.detail||'回答失败');
      if(item.event==='evidence'){
        capture(item.data);record.evidence=evidenceSummary(item.data?.evidence);
        const channels=item.data?.channels||{};
        summary.textContent='实体证据 '+(channels.entity??0)+' · 原文证据 '+(channels.chunk??0)+' · 图关系证据 '+(channels.graph_evidence??0);
        addEvidence(target,{...item.data,answer_id:record.answer_id});
        showRetrievalNotice(item.data);
      }else if(item.event==='delta'){
        const chunk=String(item.data?.text??'');
        text+=chunk;pending+=chunk;answer.textContent=text;speak(false);atEnd();
      }else if(item.event==='done'){capture(item.data);finished=true;}
    };
    try{
      const response=await fetch(endpoint('/qa/stream'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      if(!response.ok){
        let detail='';
        try{detail=(await response.json()).detail||'';}catch{try{detail=await response.text();}catch{detail='';}}
        throw Error(typeof detail==='string'&&detail?detail:'服务请求失败（HTTP '+response.status+'）');
      }
      const reader=response.body.getReader(),decoder=new TextDecoder();
      while(true){
        const {done,value}=await reader.read();
        if(mine!==serial||project!==current||epoch!==wb.epoch){await reader.cancel();throw Error('范围已变化，已停止显示旧回答');}
        if(done)break;
        buffer+=decoder.decode(value,{stream:true});
        const step=chat.splitSseBuffer(buffer);buffer=step.rest;
        for(const item of step.events)handle(item);
      }
      buffer+=decoder.decode();
      const tail=chat.splitSseBuffer(buffer+'\n\n');
      for(const item of tail.events)handle(item);
      if(!finished)throw Error('回答流提前结束');
      speak(true);
      target.dataset.state='done';progress.textContent='回答完成';
      record.text=text;
      provenance.decorateAnswer(answer,text,{projectId:project,answerId:record.answer_id,evidence:record.evidence});
      persist();
    }catch(error){
      if(mine!==serial||project!==current||epoch!==wb.epoch)return;
      failed=true;target.dataset.state='error';progress.textContent='';
      const emsg=error?.message||String(error);
      record.text=text;record.error=emsg;persist();
      addFailure(target,emsg,()=>stream(body,target,historyIndex));
    }finally{
      if(mine===serial){busy=false;controller=null;send.disabled=false;if(!failed&&project===current)input.focus();}
    }
  }
  function submit(){
    if(busy)return;
    const query=input.value.trim();if(!query)return;
    addTurn('user',query);qaHistory.push({kind:'user',text:query});persist();input.value='';
    stream({...scope(),valid_at:null,query,generate:$c('generate').checked,retrieval_mode:$c('qa-mode')?.value||'hybrid',k_entities:5,k_chunks:5});
  }
  window.clearKnowledgeChat=({notify=false,clearPersist=false}={})=>{
    const had=transcript.childElementCount>0;
    provenance.onProjectChange(current);
    serial++;busy=false;controller?.abort();controller=null;
    transcript.replaceChildren();live.textContent='';progress.textContent='';summary.textContent='';
    if(clearPersist){try{localStorage.removeItem(historyKey());}catch{}}
    loadHistory();
    send.disabled=false;
    if(notify&&had)summary.textContent='范围已变化，已清空当前会话。';
  };
  $c('qa-composer').addEventListener('submit',event=>{event.preventDefault();submit();});
  $c('qa-clear')?.addEventListener('click',()=>window.clearKnowledgeChat({clearPersist:true,notify:true}));
  // 无向量提示：语义索引未构建时，降级关键词 + 引导一键构建
  function showRetrievalNotice(payload){
    const notice=$c('qa-notice'),text=$c('qa-notice-text');
    if(!notice||!text)return;
    const errors=payload?.retrieval_errors||{};
    const noVector=String(errors.semantic||'').includes('语义索引尚未就绪')
      ||String(errors.semantic||'').includes('Embedding model differs');
    if(noVector){
      text.textContent='语义索引未构建，已降级关键词检索。可点右侧按钮构建索引后重新提问。';
      notice.hidden=false;
    }else if(payload?.degraded){
      text.textContent='部分检索通道不可用，已降级（'+(payload.active_modes||[]).join('+')+'）。';
      notice.hidden=false;
    }else{
      notice.hidden=true;
    }
  }
  $c('qa-build')?.addEventListener('click',async()=>{
    const btn=$c('qa-build'),text=$c('qa-notice-text');
    if(!btn)return;
    btn.disabled=true;
    try{
      if(text)text.textContent='正在提交索引构建任务…';
      const r=await fetch(endpoint('/indexes/rebuild'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(!r.ok)throw Error('HTTP '+r.status);
      if(text)text.textContent='索引构建任务已提交，完成后可切回语义检索重新提问。';
      setTimeout(()=>{const n=$c('qa-notice');if(n)n.hidden=true;},4000);
    }catch(e){
      if(text)text.textContent='构建失败：'+(e.message||e);
    }finally{
      btn.disabled=false;
    }
  });
  loadHistory();
  // ── 自定义下拉：原生 select 的展开面板无法用 CSS 美化，这里用 div 组件替换视觉层 ──
  // 原生 select 保留在 DOM 里（hidden 仅隐藏视觉），submit 仍读 $c('qa-mode').value；
  // 选中后同步 select.value 并派发 change，保持与既有 value 读取/事件契约一致。
  enhanceSelect($c('qa-mode'));
  function enhanceSelect(select){
    if(!select||select.dataset.enhanced)return;
    select.dataset.enhanced='1';
    const wrap=document.createElement('div');wrap.className='ks-select';
    const trigger=document.createElement('button');trigger.type='button';trigger.className='ks-select-trigger';
    trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
    const panel=document.createElement('ul');panel.className='ks-select-panel';panel.setAttribute('role','listbox');panel.hidden=true;
    const currentOpt=()=>select.options[select.selectedIndex];
    const syncTrigger=()=>{
      const opt=currentOpt();
      const label=document.createElement('span');label.className='ks-select-value';label.textContent=opt?opt.textContent:select.value;
      const arrow=document.createElement('span');arrow.className='ks-select-arrow';arrow.setAttribute('aria-hidden','true');
      trigger.replaceChildren(label,arrow);
    };
    const buildPanel=()=>{
      panel.replaceChildren();
      [...select.options].forEach((opt,idx)=>{
        const li=document.createElement('li');li.className='ks-select-option';li.setAttribute('role','option');
        li.dataset.value=opt.value;li.textContent=opt.textContent;
        li.setAttribute('aria-selected',idx===select.selectedIndex?'true':'false');
        li.onclick=()=>{select.value=opt.value;select.dispatchEvent(new Event('change',{bubbles:true}));syncTrigger();close();trigger.focus();};
        panel.append(li);
      });
    };
    const markActive=el=>{panel.querySelectorAll('.ks-select-option').forEach(o=>o.classList.toggle('active',o===el));};
    const open=()=>{buildPanel();panel.hidden=false;wrap.classList.add('open');trigger.setAttribute('aria-expanded','true');
      const cur=panel.querySelector('.ks-select-option[aria-selected="true"]');markActive(cur||panel.firstElementChild);};
    const close=()=>{panel.hidden=true;wrap.classList.remove('open');trigger.setAttribute('aria-expanded','false');};
    const move=delta=>{const opts=[...panel.querySelectorAll('.ks-select-option')];if(!opts.length)return;
      const i=opts.findIndex(o=>o.classList.contains('active'));const next=opts[(i+delta+opts.length)%opts.length];markActive(next);next.scrollIntoView({block:'nearest'});};
    trigger.onclick=e=>{e.preventDefault();panel.hidden?open():close();};
    trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='Enter'||e.key===' '){e.preventDefault();open();}else if(e.key==='ArrowUp'){e.preventDefault();open();}};
    panel.onkeydown=e=>{
      if(e.key==='Escape'){e.preventDefault();close();trigger.focus();}
      else if(e.key==='ArrowDown'){e.preventDefault();move(1);}
      else if(e.key==='ArrowUp'){e.preventDefault();move(-1);}
      else if(e.key==='Enter'||e.key===' '){e.preventDefault();const a=panel.querySelector('.ks-select-option.active');if(a)a.click();}
    };
    document.addEventListener('click',e=>{if(!wrap.contains(e.target))close();});
    wrap.append(trigger,panel);
    select.insertAdjacentElement('afterend',wrap);
    select.hidden=true;
    syncTrigger();
  }
  document.querySelectorAll('.qa-example').forEach(example=>{example.onclick=()=>{input.value=example.textContent;submit();};});
  input.addEventListener('compositionstart',()=>{composing=true;});
  input.addEventListener('compositionend',()=>{composing=false;});
  input.addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.shiftKey)return;
    if(composing||event.isComposing||event.keyCode===229)return;
    event.preventDefault();submit();
  });
  // Keep the composer inside the viewport on narrow screens and above a soft keyboard.
  function fit(){
    const page=$c('qa-page');if(!page)return;
    const viewport=window.visualViewport,height=viewport?viewport.height:window.innerHeight,offsetTop=viewport?viewport.offsetTop:0;
    page.style.setProperty('--qa-fit',Math.max(320,Math.round(height+offsetTop-page.getBoundingClientRect().top-10))+'px');
    page.style.setProperty('--qa-keyboard-inset',Math.max(0,Math.round(window.innerHeight-height-offsetTop))+'px');
  }
  window.addEventListener('resize',fit);
  window.visualViewport?.addEventListener('resize',fit);
  window.visualViewport?.addEventListener('scroll',fit);
  document.querySelector('[data-tab="qa"]')?.addEventListener('click',()=>setTimeout(fit,0));
  if(window.ResizeObserver){const observer=new ResizeObserver(fit);for(const host of [$c('scope'),$c('status')])if(host)observer.observe(host);}
  fit();
})();
window.addEventListener('resize',()=>{wb.chart?.resize();wb.tree?.resize();});
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{setTimeout(()=>{wb.chart?.resize();wb.tree?.resize();},0);}));
