/* Rich exploration on the new service. No native browser prompt/confirm APIs. */
const wb={epoch:0,chart:null,tree:null,nodes:new Map(),records:new Map(),jobs:new Map()};
const baseScope=scope;
scope=()=>window.WorkspaceContext?baseScope():({...baseScope(),...(window.ApplicabilityUI?.readQueryScope?.()||{})});
const emptyVersionContext=()=>({
  ontologyScope:'all',ontologyIds:null,ontologyVersion:null,source:null,
});
wb.versionContext=emptyVersionContext();
wb.setVersionContext=next=>{
  wb.versionContext={...emptyVersionContext(),...(next||{})};
  document.dispatchEvent(new CustomEvent('version-context:changed',{
    detail:wb.versionContext,
  }));
  return wb.versionContext;
};
wb.clearVersionContext=()=>wb.setVersionContext(emptyVersionContext());
window.wb=wb;
const term=s=>{try{const value=decodeURIComponent(String(s||'').split(/[\/#]/).pop());return value.startsWith('urn:')?value.split(':').pop():value}catch{return String(s||'')}};
const platformGroupLabel=group=>group?.source_scope==='all'?'通用规则':
  group?.source_scope==='unrestricted'?'全部范围':
  group?.source_scope==='unknown'?'范围待确认（不参与确定性答案）':
  '平台：'+(group?.platform||'未命名');
function showTab(name){document.querySelector(`[data-tab="${name}"]`).click();}
// 打开「项目与运行」里的某个分区。合并后 后台任务 / 快照·评测 不再是独立页签，
// 但仍需要一个把用户送到那儿的入口（例如文档提交成功 → 直接看任务日志）。
function showRuntimeView(view){window.RuntimeView?.show(view,{load:false});showTab('runtime');}
function controlsProject(){if(!current)throw Error('请先选择项目');return current;}
async function scopedRead(path,extra={}){const p=controlsProject(),epoch=wb.epoch,page=window.WorkspaceContext?.page,ticket=window.WorkspaceContext?.ticket(p);const result=await api('/api/projects/'+encodeURIComponent(p)+path,{...scope(),...extra});if(p!==current||epoch!==wb.epoch||(ticket&&!window.WorkspaceContext.accepts(ticket,current,page)))throw Error('项目已切换，旧响应已忽略');return result;}
function discoveryHintHost(canvasId){const canvas=$(canvasId);if(!canvas||!canvas.parentNode)return null;let host=$(canvasId+'-discovery-hint');if(!host){host=document.createElement('div');host.id=canvasId+'-discovery-hint';host.className='discovery-hint';host.hidden=true;canvas.parentNode.insertBefore(host,canvas.parentNode.firstChild);}return host;}
let discoveryHintRequest=0;
async function renderDiscoveryHint(){
  const request=++discoveryHintRequest,p=current,epoch=wb.epoch;
  const visible=id=>{const page=document.getElementById(id);return !!page&&!page.classList.contains('hidden');};
  const canvases=[];
  if(visible('tab-search')||visible('tab-graph'))canvases.push('graph-canvas');
  if(visible('tab-mindmap'))canvases.push('mindmap-canvas');
  const mode=$('project').selectedOptions?.[0]?.dataset?.ontologyMode;
  if(!p||mode!=='discovery'||!canvases.length){
    for(const id of ['graph-canvas','mindmap-canvas']){const host=$(id+'-discovery-hint');if(host)host.hidden=true;}
    return;
  }
  const hosts=canvases.map(discoveryHintHost);
  const stillCurrent=()=>request===discoveryHintRequest&&p===current&&epoch===wb.epoch&&canvases.some(id=>visible(id==='graph-canvas'?'tab-search':'tab-mindmap')||(id==='graph-canvas'&&visible('tab-graph')));
  let data;
  try{data=await api(endpoint('/ontology-discovery'),undefined,'GET');}
  catch(error){if(stillCurrent())hosts.forEach(host=>{if(host)host.hidden=true;});return;}
  if(!stillCurrent())return;
  const pending=data.unpublished_candidate_count||0;
  hosts.forEach(host=>{if(!host)return;if(pending>0){host.hidden=false;host.innerHTML=`<span>开放本体发现：还有 <b>${pending}</b> 条候选待审核或未通过校验。它们仍保留在候选区，不需要重新上传原文。</span><button data-goto-discovery>前往本体建模层 ↗</button>`;host.querySelector('[data-goto-discovery]').onclick=()=>showTab('ontology-model');}else host.hidden=true;});
}
for(const tab of ['graph','search','mindmap'])document.querySelector(`[data-tab="${tab}"]`)?.addEventListener('click',()=>renderDiscoveryHint().catch(()=>{}));
const oldChange=$('project').onchange;
$('project').onchange=()=>{wb.clearVersionContext();oldChange();wb.records.clear();for(const id of ['dashboard','source-list','source-body','resolve-result','tasks','operations','evaluation-result'])$(id).textContent='';const preferred=$('project').selectedOptions?.[0]?.dataset?.ontologyMode,mode=document.getElementById('extraction-mode');if(preferred&&mode){mode.value=preferred;mode.onchange?.();}wb.tree?.clear();renderDiscoveryHint().catch(()=>{});};
let dashboardRequest=0;
async function dashboard(){const request=++dashboardRequest,p=current;controlsProject();const r=await api(endpoint('/dashboard'),{});if(request!==dashboardRequest||p!==current)return;

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
// 图谱入口统一走 workspace.js 的 window.drawGraph；这里显式引用，避免再依赖"后加载文件覆盖全局"的隐式约定。
bind('load-graph',async()=>{showTab('search');await window.drawGraph();});
bind('resolve-entity',async()=>{const r=await scopedRead('/resolve',{text:$('resolve-text').value,threshold:Number($('resolve-threshold').value)});const rows=r.canonical?[r.canonical]:r.candidates;$('resolve-result').innerHTML=`<p>${esc(r.status)} · ${esc(r.backend)}</p>`+rows.map(x=>`<div class="candidate"><strong>${esc(x.text)}</strong> ${typeHint(x.type)} ${x.score!==undefined?'· '+x.score.toFixed(3):''}<small>${esc(x.id)} · v${x.version}</small><button data-keep="${esc(x.id)}">设为保留实体</button> <button data-drop="${esc(x.id)}" class="secondary">设为合并实体</button></div>`).join('');for(const row of rows)wb.records.set(row.id,row);$('resolve-result').querySelectorAll('[data-keep]').forEach(b=>b.onclick=()=>{$('keep-id').value=b.dataset.keep;});$('resolve-result').querySelectorAll('[data-drop]').forEach(b=>b.onclick=()=>{$('drop-id').value=b.dataset.drop;});});
async function latest(id){const p=current;const r=await api(endpoint('/records/'+encodeURIComponent(id)+'/history'),undefined,'GET');if(p!==current)throw Error('项目已切换');return r.versions[r.versions.length-1];}
bind('add-alias',async()=>{const id=$('keep-id').value;const row=await latest(id);await api(endpoint('/aliases'),{entity_id:id,alias:$('alias-name').value,expected_version:row.version});status('别名已保存，消歧可直接命中规范实体。');});
bind('merge-entities',async()=>{if(!$('merge-confirm').checked)throw Error('请先勾选合并确认');const keep=$('keep-id').value,drop=$('drop-id').value;if(!window.EntityGovernance)throw Error('实体治理组件未就绪，请刷新后重试');const selection=await window.EntityGovernance.prepareMerge(keep,drop);if(selection===null)return;const r=await api(endpoint('/merge'),{keep_id:keep,drop_id:drop,expected_versions:selection.expected_versions,attribute_winners:selection.attribute_winners,preview_fingerprint:selection.preview_fingerprint});$('merge-confirm').checked=false;status('Semantica 融合完成，操作 '+r.operation_id+'；可在「知识台账 → 可撤销的操作」撤销。');await operations();});
bind('soft-delete',async()=>{if(!$('delete-confirm').checked)throw Error('请勾选软删除确认');const id=$('revision-id').value;const r=await api(endpoint('/delete'),{record_id:id,expected_version:Number($('revision-version').value)});$('delete-confirm').checked=false;status(`已软删除 ${r.deleted} 条记录（含关联关系）；可在「知识台账 → 可撤销的操作」撤销。`);await operations();});
bind('restore-version',async()=>{const r=await api(endpoint('/restore'),{record_id:$('revision-id').value,version:Number($('restore-number').value),expected_version:Number($('revision-version').value)});$('revision-version').value=r.version;status('历史内容已恢复为新版本 '+r.version);});
async function operations(){const p=current,r=await api(endpoint('/operations'),undefined,'GET');if(p!==current)return;$('operations').innerHTML=r.operations.map(x=>`<div class="candidate"><b>${esc(x.metadata.operation)}</b> · ${esc(x.recorded_at)}<small>${esc(x.metadata.operation_id)}</small><button data-undo="${esc(x.metadata.operation_id)}">撤销该操作</button></div>`).join('')||'<p>暂无治理操作。</p>';$('operations').querySelectorAll('[data-undo]').forEach(b=>b.onclick=async()=>{try{const r=await api(endpoint('/operations/'+b.dataset.undo+'/undo'),{});status('已恢复 '+r.restored+' 条记录。');await operations();}catch(e){status(e.message,true);}});}
bind('load-operations',operations);
bind('load-sources',async()=>{const r=await scopedRead('/sources');$('source-list').innerHTML=r.documents.map((d,i)=>`<button data-source="${i}" class="source-item">${esc(d.metadata.title||d.metadata.source_file||d.id)}<small>${d.text.length} 字 · ${d.derived_count} 条派生知识</small></button>`).join('');$('source-list').querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>{const d=r.documents[Number(b.dataset.source)];$('source-body').textContent=d.text||'旧项目未保存完整原文；可查片段与原始导入记录。';$('source-title').textContent=d.metadata.title||d.id;});if(r.documents.length)$('source-list').querySelector('button').click();});
bind('draw-mindmap',async()=>{const ontologyScope=wb.ontologyScope?wb.ontologyScope():{};const r=await scopedRead('/mindmap',{...ontologyScope,root_id:$('mindmap-root').value,depth:Number($('mindmap-depth').value)});if(!wb.tree)wb.tree=echarts.init($('mindmap-canvas'));const convert=n=>({name:n.text+(n.predicate?' · '+labelOf(n.predicate):''),id:n.id,children:n.children.map(convert)});wb.tree.setOption({tooltip:{formatter:p=>esc(p.name)},series:[{type:'tree',data:[convert(r.tree)],top:'8%',left:'20%',bottom:'8%',right:'20%',symbolSize:9,roam:true,label:{position:'right',verticalAlign:'middle',align:'left'},leaves:{label:{position:'right',align:'left'}},expandAndCollapse:true,initialTreeDepth:3}]},true);wb.tree.resize();renderDiscoveryHint().catch(()=>{});});
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
    const map={queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已取消'};
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

        ${Object.hasOwn(j,'payload')?`
          <div class="task-controls admin-only">
            ${['queued','running'].includes(j.status)?`<button type="button" class="secondary" data-job-action="cancel" data-job-id="${esc(j.id)}">取消任务</button>`:''}
            ${['failed','interrupted'].includes(j.status)?`<button type="button" class="secondary" data-job-action="retry" data-job-id="${esc(j.id)}">重试任务</button>`:''}
            ${j.status==='cancelled'?`<button type="button" class="secondary" data-job-action="resume" data-job-id="${esc(j.id)}">重新排队</button>`:''}
          </div>`:''}

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
  $('tasks').querySelectorAll('[data-job-action]').forEach(button=>{
    button.onclick=async()=>{
      button.disabled=true;
      try{
        const job=await api('/api/jobs/'+encodeURIComponent(button.dataset.jobId)+'/'+button.dataset.jobAction,{},'POST');
        if(['queued','running'].includes(job.status))wb.jobs.set(job.id,job);
        status(job.status==='cancelled'?'取消已受理，将在下一处理检查点停止；已提交知识保留。':'任务已重新排队。');
        await jobList();
      }catch(error){button.disabled=false;status(error.message,true);}
    };
  });
  return r.jobs;
}
function watch(job){wb.jobs.set(job.id,job);jobList();}
bind('load-jobs',jobList);
setInterval(async()=>{if(!wb.jobs.size)return;try{const jobs=await jobList();for(const j of jobs){if(wb.jobs.has(j.id)&&!['queued','running'].includes(j.status)){wb.jobs.delete(j.id);if(j.status==='completed'){await projects();if(j.result?.project){$('project').value=j.result.project.id;$('project').onchange();status('导入完成：'+j.result.project.name+'。进入图谱页查看。');}}}}}catch(e){status(e.message,true);}},3000);
function readParseSettings(){
  const settings={chunk_strategy:$('parse-strategy').value,chunk_size:Number($('parse-size').value),chunk_overlap:Number($('parse-overlap').value),extract_attributes:$('extraction-mode').value!=='documents'&&$('parse-attributes').checked,relation_constraint_mode:$('parse-relation-constraints').value,resolve_entities:$('parse-resolve').checked,auto_merge:$('parse-merge').checked,merge_threshold:Number($('parse-threshold').value)};
  if(!Number.isInteger(settings.chunk_size)||settings.chunk_size<100||settings.chunk_size>10000)throw Error('最大字符数需为 100–10000 的整数');
  if(!Number.isInteger(settings.chunk_overlap)||settings.chunk_overlap<0||settings.chunk_overlap>2000||settings.chunk_overlap>=settings.chunk_size)throw Error('重叠字符数需为 0–2000 的整数，且小于最大字符数');
  if(settings.auto_merge&&!settings.resolve_entities)throw Error('请先开启实体消歧，再开启语义合并');
  if(!Number.isFinite(settings.merge_threshold)||settings.merge_threshold<0||settings.merge_threshold>1)throw Error('合并阈值需在 0–1 之间');
  return settings;
}
function documentUploadOptions(document){
  const options={metadata:document.metadata,applicability:document.applicability};
  for(const key of ['published_at','valid_from','valid_until'])if(document[key])options[key]=document[key];
  return options;
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
  const previewTicket=window.LibraryWorkspace?.ticket();
  const documents=await readDocumentBatch(true),doc=documents[0];
  let result;
  if(doc.file){
    const form=new FormData();form.append('file',doc.file,doc.file.name);
    form.append('options',JSON.stringify({title:doc.title,...documentUploadOptions(doc),...settings}));
    result=await api('/api/projects/'+encodeURIComponent(p)+'/documents/upload/preview',form);
  }else result=await api('/api/projects/'+encodeURIComponent(p)+'/documents/preview',{title:doc.title,text:doc.text,...documentUploadOptions(doc),...settings});
  if(p!==current||(previewTicket&&!window.LibraryWorkspace.accepts(previewTicket,p)))return;
  renderChunkPreview(result,doc.title);
  openChunkPreview();
  status('切片预览完成，未调用模型或写入知识');
});
bind('ingest',async()=>{
  const project=controlsProject(),mode=document.getElementById('extraction-mode').value,settings=readParseSettings();
  Object.assign(settings,window.IngestOntology.settings(project,mode));
  const ontologyRelease=mode==='ontology'?{...window.IngestOntology.state().release}:null;
  const documents=await readDocumentBatch(),results=$('doc-submit-results');
  results.textContent='';let accepted=0,failed=0;
  if(documents.every(doc=>doc.file)&&window.IngestBatches){
    const uiTicket=window.LibraryWorkspace?.ticket();
    const queueGeneration=window.DocumentUploadQueue?.generation;
    const requested=$('ingest-concurrency')?.value||'auto';
    const batch=await window.IngestBatches.submit(project,documents,{...settings,...documentUploadOptions(documents[0]),extraction_mode:mode,
      requested_concurrency:requested==='auto'?'auto':Number(requested)});
    if(project!==current||queueGeneration!==window.DocumentUploadQueue?.generation)return;
    results.textContent='文件上传完成，批次已开始。可在“处理批次”查看进度、暂停或处理失败文件。';
    window.LibraryWorkspace?.showBatch({projectId:project,batchId:batch.id,ticket:uiTicket});
    status('批次已开始 · '+batch.id);return;
  }
  for(const document of documents){
    const entry=window.document.createElement('p');results.append(entry);
    entry.textContent=document.title+' · 正在提交…';
    try{
      let job;
      if(document.file){
        window.DocumentUploadQueue.setStatus(document.file,'uploading');
        const form=new FormData();form.append('file',document.file,document.file.name);
        form.append('options',JSON.stringify({title:document.title,...documentUploadOptions(document),extraction_mode:mode,...settings}));
        job=await api('/api/projects/'+encodeURIComponent(project)+'/documents/upload/jobs',form);
        window.DocumentUploadQueue.setStatus(document.file,'queued',job.id);
      }else job=await api('/api/projects/'+encodeURIComponent(project)+'/documents/jobs',{title:document.title,text:document.text,...documentUploadOptions(document),extraction_mode:mode,...settings});
      wb.jobs.set(job.id,job);accepted++;entry.textContent=document.title+' · 已加入解析队列 · '+job.id;
      // 提交后在右侧工作区实时看进度（SSE）与抽取结果，不跳后台任务页
      window.IngestResults?.watch(job.id,document.title,{projectId:project,mode,ontologyRelease});
    }catch(error){if(document.file)window.DocumentUploadQueue.setStatus(document.file,'failed',error.message);failed++;entry.textContent=document.title+' · 提交失败：'+error.message;}
  }
  // 提交后留在「知识写入」本页：逐文件结果已写进 #doc-submit-results，不再把人跳到后台任务页。
  if(accepted){
    const view=window.document.createElement('p');
    view.className='ingest-after-submit';
    view.innerHTML='';
    const link=window.document.createElement('button');
    link.type='button';link.className='secondary';link.textContent='查看后台任务进度 ↗';
    link.onclick=()=>{showRuntimeView('jobs');};
    view.append('已提交 '+accepted+' 个文档进入解析队列。想看实时日志可点：');
    view.append(link);
    results.append(view);
  }
  status(`已提交 ${accepted} 个文档，提交失败 ${failed} 个。本页会保留每个文件的提交结果。`,failed>0);
});
async function snapshots(){const p=current,r=await api(endpoint('/snapshots'),undefined,'GET');if(p!==current)return;const container=$('snapshot-cards');if(!r.snapshots.length){container.innerHTML='<div class="eval-snapshot-empty"><p>📋</p><p>还没有快照，先保存一个快照</p></div>';return;}const items=r.snapshots.map(s=>{const isAuto=s.kind==='auto'||s.name.startsWith('Before restoring ')||s.name.startsWith('恢复前自动备份 · ');let displayName=s.name;if(isAuto)displayName=s.name.replace(/^Before restoring /,'').replace(/^恢复前自动备份 · /,'');return{s,isAuto,displayName};});const nameCounts={};items.forEach(i=>{nameCounts[i.displayName]=(nameCounts[i.displayName]||0)+1;});items.sort((a,b)=>{if(a.isAuto!==b.isAuto)return a.isAuto?1:-1;return new Date(b.s.created_at)-new Date(a.s.created_at);});container.innerHTML='<div class="eval-snapshot-cards">'+items.map(({s,isAuto,displayName})=>{const dt=new Date(s.created_at);const dateStr=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0')+' '+String(dt.getHours()).padStart(2,'0')+':'+String(dt.getMinutes()).padStart(2,'0');const diffMs=Date.now()-dt.getTime();let rel='';if(diffMs>=0&&diffMs<60000)rel='刚刚';else if(diffMs<3600000)rel=Math.floor(diffMs/60000)+' 分钟前';else if(diffMs<86400000)rel=Math.floor(diffMs/3600000)+' 小时前';else if(diffMs<2592000000)rel=Math.floor(diffMs/86400000)+' 天前';const badge=isAuto?'<span class="eval-snapshot-badge auto">自动备份</span>':'<span class="eval-snapshot-badge manual">手动快照</span>';const recordCount=s.record_count!=null?'<span class="eval-snapshot-records">· '+s.record_count+' 条记录</span>':'';const idSuffix=nameCounts[displayName]>1?'<span class="eval-snapshot-id">#'+esc(s.id.slice(0,6))+'</span>':'';return '<div class="eval-snapshot-card" data-id="'+esc(s.id)+'"><div class="eval-snapshot-header"><span class="eval-snapshot-name">'+esc(displayName)+'</span>'+badge+'</div><div class="eval-snapshot-meta">'+esc(dateStr)+(rel?' · '+esc(rel):'')+idSuffix+recordCount+'</div><div class="eval-snapshot-actions"><button data-snapshot-restore="'+esc(s.id)+'" class="secondary">恢复</button></div></div>';}).join('')+'</div>';container.querySelectorAll('[data-snapshot-restore]').forEach(b=>{b.onclick=async()=>{const id=b.dataset.snapshotRestore;const card=b.closest('.eval-snapshot-card');const actions=b.closest('.eval-snapshot-actions');if(card.querySelector('.eval-restore-confirm'))return;const snap=items.find(i=>i.s.id===id);const displayName=snap?snap.displayName:'';const warning=document.createElement('div');warning.className='eval-restore-confirm';warning.innerHTML='<p>确定恢复到此快照？<br><span class="subtle">恢复前会自动创建恢复点，可随时撤销。</span></p><div class="eval-confirm-actions"><button class="eval-confirm-yes">确认恢复</button><button class="eval-confirm-no secondary">取消</button></div>';card.appendChild(warning);warning.scrollIntoView({behavior:'smooth',block:'nearest'});actions.style.display='none';warning.querySelector('.eval-confirm-yes').onclick=async()=>{const yesBtn=warning.querySelector('.eval-confirm-yes');const noBtn=warning.querySelector('.eval-confirm-no');yesBtn.disabled=true;noBtn.disabled=true;yesBtn.textContent='正在恢复…';yesBtn.classList.add('eval-restoring');try{const r=await api(endpoint('/snapshots/'+id+'/restore'),{});status('已恢复到「'+displayName+'」；恢复前状态已存为「'+(r.recovery_snapshot_name||'恢复前自动备份')+'」');await snapshots();}catch(e){status(e.message,true);yesBtn.disabled=false;noBtn.disabled=false;yesBtn.textContent='确认恢复';yesBtn.classList.remove('eval-restoring');}};warning.querySelector('.eval-confirm-no').onclick=()=>{warning.remove();actions.style.display='';};};});}
// 快照与评测并入「项目与运行」：进入该分区就自动读一次快照列表
// （原来要先点一下「刷新」才看得到——合并后的规则是「进这一屏，就把这一屏该给的数据给出来」）。
window.RuntimeView?.on('snapshots',()=>snapshots().catch(e=>status(e.message,true)));
bind('load-snapshots',snapshots);bind('save-snapshot',async()=>{await api(endpoint('/snapshots'),{name:$('snapshot-name').value||'手动快照'});$('snapshot-name').value='';await snapshots();status('项目快照已保存。');});
bind('run-evaluation',async()=>{const input=$('gold-json').value.trim();if(!input)throw Error('请输入标准答案 JSON');let parsed;try{parsed=JSON.parse(input);}catch(e){throw Error('JSON 格式错误：'+e.message);}if(!parsed.entities||!Array.isArray(parsed.entities))throw Error('JSON 缺少 entities 数组');if(!parsed.relations||!Array.isArray(parsed.relations))throw Error('JSON 缺少 relations 数组');$('gold-json-error').textContent='';const r=await scopedRead('/evaluate',{...json('gold-json'),known_at:null,valid_at:null,filters:null,applicability:null});$('evaluation-result').innerHTML=renderEvalResult(r);});
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
  const answerContext=item=>({projectId:current,answerId:item.answer_id,evidence:['evidence','uncertain_references','historical_references','future_references'].flatMap(key=>item[key]||[])});
  const referenceKinds=[['historical_references','历史参考'],['future_references','尚未生效参考'],['uncertain_references','有效性待核对']];
  const platformText=row=>{
    const platforms=row.metadata?.applicability?.platforms;
    if(row.source_scope==='all'||platforms?.mode==='all')return '通用规则';
    if(row.query_platform)return '平台：'+row.query_platform;
    if(platforms?.mode==='specific'&&platforms.values?.length)return '平台：'+platforms.values.join('、');
    return '平台范围待确认';
  };
  const validityText=row=>{
    const labels={effective:'本次时点有效',expired:'已失效',not_yet_effective:'尚未生效',unknown:'有效性待核对',conflict:'有效性冲突待核对'};
    const basisLabels={explicit:'明确维护',valid_from:'规范生效时间',valid_until:'规范失效时间',source_validity:'来源有效期',missing_valid_from:'未维护生效时间',published_at:'发布日期推定',unknown:'待核对'};
    return (labels[row.validity_status]||'有效性待核对')
      +' · 生效：'+(row.valid_from||'未维护')+' · 失效（不含）：'+(row.valid_until||'未维护')
      +(row.metadata?.published_at?' · 发布：'+row.metadata.published_at:'')
      +(row.validity_basis?' · 判定依据：'+(basisLabels[row.validity_basis]||row.validity_basis):'')
      +(row.metadata?.effective_time_source?' · 时间来源：'+(basisLabels[row.metadata.effective_time_source]||row.metadata.effective_time_source):'');
  };
  function addSourceEligibility(article,row){
    for(const source of row.source_eligibility||[]){
      const line=document.createElement('p');line.className='qa-source-governance subtle';
      line.textContent='有效期核对：版本 '+(source.governance_version_id||'未提供')+' · 生效：'+(source.valid_from||'未维护')
        +' · 失效（不含）：'+(source.valid_until||'未维护')+' · 原文版本：'+(source.document_version_id||'未提供');
      article.append(line);
    }
  }
  function markDraft(turn,failed=false){
    let note=turn.querySelector('.qa-draft-status');
    if(!note){note=document.createElement('p');note.className='qa-draft-status subtle';note.setAttribute('role','status');turn.append(note);}
    note.textContent=failed?'未完成的草稿，未通过最终依据校验':'生成中，尚未完成依据校验';
  }
  function frozenCitation(row,projectId,answerId){
    const citation=provenance.sanitizeEvidenceSummary(row).citation;
    const available=answerId&&citation&&row.provenance_ref;
    const link=document.createElement(available?'button':'b');
    link.className='qa-citation';link.textContent='['+(citation||'—')+']';
    if(available){
      link.type='button';link.className+=' secondary provenance-citation';
      link.setAttribute('aria-label','查看引用 '+citation+' 的冻结原文与溯源');
      link.onclick=()=>provenance.open({projectId,answerId,citation,trigger:link});
    }
    return link;
  }

  function markUnresolvedProvenance(turn){
    const note=document.createElement('p');note.className='qa-provenance-unresolved subtle';note.setAttribute('role','status');
    note.textContent='这条回答缺少答案标识，无法定位已冻结的证据链。请重新提问以生成可溯源的回答。';
    turn.append(note);
  }

  const atEnd=()=>{const box=$c('qa-scroll');if(box)box.scrollTop=box.scrollHeight;};
  function addScopeNote(turn,value){const note=document.createElement('small');note.className='qa-submitted-scope subtle';const label=window.ApplicabilityUI?.describe?.(value)||'全项目';note.textContent='提交范围：'+label+(value?.filters?' · 更多字段条件':'')+(value?.known_at?' · 系统时点 '+value.known_at:'')+(value?.valid_at?' · 业务时点 '+value.valid_at:'');turn.append(note);}
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
      const turn=addTurn(item.kind,item.kind==='user'?item.text:'');if(item.kind==='user'&&item.scope)addScopeNote(turn,item.scope);
      if(item.kind==='assistant'){
        const answer=turn.querySelector('.qa-answer');
        turn.dataset.state=item.error?'error':'done';
        if(item.answer_id){
          const evidence=evidenceSummary(item.evidence);
          provenance.decorateAnswer(answer,item.text||'',answerContext({...item,evidence}));
          addEvidence(turn,{...item,evidence});
        }else{
          if(answer)answer.textContent=item.text||'';
          markUnresolvedProvenance(turn);
        }
        window.QaPresentation?.annotate(turn,item);
        if(item.error){
          turn.dataset.state='error';
          markDraft(turn,true);
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
    const rows=Array.isArray(payload?.evidence)?payload.evidence:[];
    const pendingCount=payload?.structure_pending?.count||0;
    const platformGroups=Array.isArray(payload?.platform_groups)?payload.platform_groups:[];
    // 以前"没有证据"就整块不渲染；现在还要看有没有结构待定的知识 —— 有就得说出来，
    // 否则用户看到的就是"问答没反应"，而真相是"有知识、但概念还没建模"。
    const references=referenceKinds.map(([key,title])=>({key,title,items:Array.isArray(payload?.[key])?payload[key]:[]}));
    const warnings=Array.isArray(payload?.coverage_warnings)?payload.coverage_warnings:[];
    const referenceCount=references.reduce((sum,group)=>sum+group.items.length,0);
    if(!rows.length&&!pendingCount&&!platformGroups.length&&!referenceCount&&!warnings.length&&!payload?.as_of)return;
    const panelId='qa-evidence-'+(++evidenceSerial),block=document.createElement('div');block.className='qa-evidence-block';
    const toggle=document.createElement('button');
    toggle.type='button';toggle.className='qa-evidence-toggle secondary';
    toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',panelId);
    const counts=rows.length+' 条回答依据'+(referenceCount?' · '+referenceCount+' 条独立参考':'')+(pendingCount?' · '+pendingCount+' 条结构待定':'');
    toggle.textContent='查看 '+counts;
    const panel=document.createElement('div');panel.className='qa-evidence-panel';panel.id=panelId;panel.hidden=true;
    // 这块面板用户反馈"看不懂 E1/E2 是什么、下面这些为什么要展示"。它其实是
    // 模型回答的输入清单，所以先给一段说明，再列条目。
    const note=document.createElement('div');note.className='qa-evidence-note';
    for(const line of [
      '[E1] [E2] … 是这条答案里的引用编号：证据从 E1 开始按检索顺序编号，答案正文里的 [E1] 就指下面第 1 条。点编号可以看它的完整溯源链（原文 → 记录版本 → 断言 → 审核决定）。',
      'E 为本次时点的回答依据；历史 R、未来 F、有效性待核对 U 在下方单列，不计入已确认有效的依据。缺少属性不影响合格原文查询。',
      '规则判断使用 E 依据；文档说明可以引用带状态标识的原文，不代表当前有效规则。点引用查看回答生成时冻结的原文与记录版本。',
    ]){const p=document.createElement('p');p.textContent=line;note.append(p);}
    panel.append(note);
    if(payload.as_of||payload.known_at){
      const time=document.createElement('p');time.className='qa-time-context subtle';
      time.textContent='规则判断时点：'+(payload.as_of||'—')+' · 系统知识时点：'+(payload.known_at||'—');panel.append(time);
    }
    if(warnings.length){
      const coverage=document.createElement('div');coverage.className='qa-coverage-warnings';coverage.setAttribute('role','status');
      for(const warning of warnings){const line=document.createElement('p');line.textContent=typeof warning==='string'?warning:JSON.stringify(warning);coverage.append(line);}
      panel.append(coverage);
    }
    if(platformGroups.length){
      const platformBlock=document.createElement('div');platformBlock.className='qa-reasoning-block';
      const head=document.createElement('p');head.className='qa-reasoning-head';head.innerHTML='<b>按平台检索结果</b><span>各平台分别筛选和排名。</span>';platformBlock.append(head);
      for(const group of platformGroups){
        const count=Number(group.evidence_count??group.evidence_rows?.length??group.hits?.length??0);
        const line=document.createElement('p');line.className='qa-evidence-meta subtle';
        line.textContent=platformGroupLabel(group)+' · '+count+' 条证据'+(count?'':'（未检索到本次时点确认有效的依据）');
        platformBlock.append(line);
      }
      panel.append(platformBlock);
    }
    // 图谱关系路径：命中实体沿现有关系边可到达的多跳路径，不表示 OWL 或逻辑推理。
    const paths=payload?.reasoning_paths||[];
    if(paths.length){
      const reasonBlock=document.createElement('div');reasonBlock.className='qa-reasoning-block';
      const head=document.createElement('p');head.className='qa-reasoning-head';
      head.innerHTML='<b>图谱关系路径</b><span>命中实体沿现有关系边可到达的多跳路径。</span>';
      reasonBlock.append(head);
      for(const path of paths){
        const line=document.createElement('p');line.className='qa-reasoning-path';
        const nodes=[];
        path.steps.forEach((step,index)=>{
          if(index===0)nodes.push(esc(step.from_label||step.from));
          nodes.push('<span class="qa-reasoning-relation">—['+esc(step.relation||'关系')+']→</span> '+esc(step.to_label||step.to));
        });
        line.innerHTML=nodes.join(' ');
        reasonBlock.append(line);
      }
      panel.append(reasonBlock);
    }
    const projectId=current,answerId=payload.answer_id;
    for(const [rowIndex,row] of rows.entries()){
      const article=document.createElement('article');article.className='qa-evidence-item';
      article.hidden=rowIndex>=3;
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
      const applicability=window.ApplicabilityUI?.describe?.(row);
      meta.textContent=(row.kind||'')+' · '+(labelOf(row.type)||'—')+' · 版本 '+(row.version??'—')+(applicability?' · '+applicability:'');
      article.append(link,excerpt,meta);
      if(row.validity_status){const validity=document.createElement('p');validity.className='qa-validity subtle';validity.textContent=validityText(row);article.append(validity);}
      addSourceEligibility(article,row);
      const sources=Array.isArray(row.sources)?row.sources:[];
      if(sources.length){
        const sourceBox=document.createElement('div');sourceBox.className='qa-source-list';
        sources.forEach((source,sourceIndex)=>{
          const sourceItem=document.createElement('section');sourceItem.className='qa-source-item';sourceItem.hidden=sourceIndex>0;
          const title=document.createElement('b');title.textContent=source.title||source.document_id||'历史原文';
          const quote=document.createElement('p');quote.textContent=(source.quote||'').slice(0,240);
          const sourceMeta=document.createElement('p');sourceMeta.className='qa-evidence-meta subtle';
          const sourceApplicability=window.ApplicabilityUI?.describe?.(source);
          sourceMeta.textContent='文档版本 '+(source.version??'—')+' · '+(source.channel||row.retrieval_channel||'冻结来源')+(sourceApplicability?' · '+sourceApplicability:'');
          sourceItem.append(title,quote,sourceMeta);sourceBox.append(sourceItem);
        });
        if(sources.length>1){
          const more=document.createElement('button');more.type='button';more.className='qa-source-more secondary';
          more.textContent='另有 '+(sources.length-1)+' 个来源';more.setAttribute('aria-expanded','false');
          more.onclick=()=>{const open=more.getAttribute('aria-expanded')==='true';[...sourceBox.querySelectorAll('.qa-source-item')].slice(1).forEach(node=>node.hidden=open);more.setAttribute('aria-expanded',String(!open));more.textContent=open?'另有 '+(sources.length-1)+' 个来源':'收起其他来源';};
          sourceBox.append(more);
        }
        article.append(sourceBox);
      }else{
        const missing=document.createElement('p');missing.className='qa-source-missing subtle';
        missing.textContent='原文状态：'+(row.source_reason||'手工录入或历史来源缺失')+'（未使用当前版本补写）';article.append(missing);
      }
      const seed=row.kind==='entity'?row.id:(row.kind==='relation'?row.subject_id:'');
      const actions=document.createElement('div');actions.className='row';
      if(seed){const button=document.createElement('button');button.type='button';button.dataset.evidenceNode=seed;button.textContent='在图谱中查看';actions.append(button);}
      if(row.provenance_ref&&answerId){const button=frozenCitation(row,projectId,answerId);button.textContent='查看冻结原文';actions.append(button);}
      if(actions.childElementCount)article.append(actions);
      panel.append(article);
    }
    if(rows.length>3){
      const more=document.createElement('button');more.type='button';more.className='qa-evidence-more secondary';
      more.textContent='再看 '+(rows.length-3)+' 条依据';more.setAttribute('aria-expanded','false');
      more.onclick=()=>{const open=more.getAttribute('aria-expanded')==='true';[...panel.querySelectorAll('.qa-evidence-item')].slice(3).forEach(node=>node.hidden=open);more.setAttribute('aria-expanded',String(!open));more.textContent=open?'再看 '+(rows.length-3)+' 条依据':'收起其余依据';};
      panel.append(more);
    }
    for(const group of references){
      if(!group.items.length)continue;
      const section=document.createElement('section');section.className='qa-reference-section';section.dataset.referenceKind=group.key;
      const heading=document.createElement('h4');heading.textContent=group.title+' · '+group.items.length+' 条';section.append(heading);
      for(const row of group.items){
        const article=document.createElement('article');article.className='qa-reference-item';
        const excerpt=document.createElement('p');excerpt.textContent=row.text_preview??row.text??'';
        const use=document.createElement('p');use.className='qa-reference-usage';
        use.textContent=row.usage==='qualified_quote'?'仅用于文档说明，未作为已确认有效规则依据':'未参与本次回答';
        const meta=document.createElement('p');meta.className='qa-evidence-meta subtle';
        meta.textContent=platformText(row)+' · '+validityText(row)+' · 记录版本：'+(row.version_id||row.version||'—');
        article.append(frozenCitation(row,projectId,answerId),excerpt,use,meta);
        addSourceEligibility(article,row);
        if(row.exclusion_reasons?.length){const reasons=document.createElement('p');reasons.className='qa-reference-exclusions subtle';reasons.textContent='排除原因：'+row.exclusion_reasons.join('、');article.append(reasons);}
        for(const source of row.sources||[]){
          const sourceLine=document.createElement('p');sourceLine.className='qa-source-item subtle';
          sourceLine.textContent=(source.title||source.document_id||'冻结原文')+' · 文档版本：'+(source.document_version_id||source.record_version_id||source.version||'—')+' · '+(source.quote||'');article.append(sourceLine);
        }
        if(!row.provenance_ref||!answerId){const missing=document.createElement('p');missing.className='qa-source-missing subtle';missing.textContent='冻结来源不可用，无法打开历史原文（未使用当前版本补写）';article.append(missing);}
        section.append(article);
      }
      panel.append(section);
    }
    /* C1「结构待定」：这些知识**不是证据**（它们的类型还没进本体），但也不能让它们凭空消失 ——
       用户问的东西可能正好落在它们身上，那就必须显式说出来，并说清"为什么不算、去哪儿补"。
       编号用 P（peer）而不是 E：一眼能看出它不在证据链里，避免有人拿 [P1] 当引用。 */
    const pending=payload?.structure_pending;
    if(pending&&pending.count){
      const block=document.createElement('div');block.className='qa-pending-block';
      const head=document.createElement('p');head.className='qa-pending-head';
      head.innerHTML='<b>另有 '+pending.count+' 条知识涉及本体里还没有的概念（结构待定）</b>'
        +'<span>它们能查、能看原文，但<b>不算正式证据</b>，所以没有编号进上面那份清单。'
        +(pending.terms&&pending.terms.length?'缺的概念：'+esc(pending.terms.join('、'))+'。':'')
        +'去「本体建模层」把概念建出来并发布后，这些知识会自动回到证据链里。</span>';
      block.append(head);
      for(const row of (pending.items||[])){
        const article=document.createElement('article');article.className='qa-pending-item';
        const label=document.createElement('b');label.className='qa-pending-citation';label.textContent='['+row.citation+']';
        const excerpt=document.createElement('p');excerpt.textContent=row.text_preview??row.text??'';
        const meta=document.createElement('p');meta.className='qa-evidence-meta subtle';
        meta.textContent=(row.kind||'')+' · 缺概念：'+((row.terms||[]).join('、')||'—');
        article.append(label,excerpt,meta);
        const seed=row.kind==='entity'?row.id:(row.kind==='relation'?row.subject_id:'');
        if(seed){
          const actions=document.createElement('div');actions.className='row';
          const button=document.createElement('button');button.type='button';button.dataset.evidenceNode=seed;button.textContent='在图谱中查看';
          actions.append(button);article.append(actions);
        }
        block.append(article);
      }
      if(!rows.length)toggle.textContent='查看 '+pending.count+' 条结构待定的知识';
      panel.append(block);
    }
    toggle.onclick=()=>{const open=toggle.getAttribute('aria-expanded')==='true';toggle.setAttribute('aria-expanded',String(!open));panel.hidden=open;toggle.textContent=(open?'查看 ':'收起 ')+counts;};
    panel.querySelectorAll('[data-evidence-node]').forEach(node=>{node.onclick=async()=>{
      // 这次跳转由我们指定"看这个实体"，别让进入检索页的自动补画再抢一次画布：
      // 那条路径会先清空图谱再发全图请求，把刚选中的实体冲掉（契约用例抓到过）。
      window.suppressNextAutoGraph?.();
      showTab('search');
      try{await window.selectEntityDetail(node.dataset.evidenceNode);}catch(error){status(error.message,true);}
      $c('graph-heading')?.focus();
    };});
    panel.querySelectorAll('[data-evidence-source]').forEach(node=>{node.onclick=async()=>{
      // P0（2026-10-04）：原文数据源并入「知识写入」，侧栏独立入口已移除；溯源落点待 P1 接入
      // 「知识写入」页的"看解析正文"区。期间点来源按钮不再跳 sources（会 null.click 崩），
      // 改为明确提示，避免静默失效。
      const sourceTab=document.querySelector('[data-tab="sources"]');
      if(!sourceTab){status('原文正文查看将并入「知识写入」（下一期接入）；当前可在检索或知识台账查看该证据的原文片段。');return;}
      sourceTab.click();
      try{await window.selectSource?.(node.dataset.evidenceSource);}catch(error){status(error.message,true);}
        };});
    block.append(toggle,panel);turn.append(block);atEnd();
  }
  function addFailure(turn,message,replay){
    if(/Milvus|语义|索引|维度/.test(message)){const notice=$c('qa-notice'),text=$c('qa-notice-text');if(notice&&text){text.textContent=message;notice.hidden=false;}}
    turn.dataset.state='error';
    const box=document.createElement('div');box.className='qa-error';box.setAttribute('role','alert');
    const text=document.createElement('span');text.className='qa-error-text';text.textContent='回答失败：'+message;
    const retry=document.createElement('button');retry.type='button';retry.className='qa-retry';retry.textContent='重试';
    retry.onclick=()=>{if(!busy)replay();};
    box.append(text,retry);turn.append(box);retry.focus();atEnd();
  }
  async function stream(body,turn,historyIndex=qaHistory.length){
    if(busy)return;
    try{window.ScopeFields.requireRetrievalScope(body);}
    catch(error){summary.textContent=error.message;progress.textContent='';return;}
    // Chat owns its cancellation generation. A graph repaint is not a change
    // to the chat scope; project/scope controls explicitly reset chat serial.
    const project=current,mine=++serial,scopeTicket=window.WorkspaceContext?.ticket(current,'qa');
    if(turn)provenance.close(false);
    const record={kind:'assistant',text:'',scope:JSON.parse(JSON.stringify(body)),project_id:project};
    // Reserve the retry slot in memory; only terminal answers belong in saved history.
    qaHistory[historyIndex]=record;
    const capture=payload=>{
      for(const key of ['answer_id','retrieval_run_id'])if(typeof payload?.[key]==='string'&&payload[key])record[key]=payload[key];
    };
    busy=true;send.disabled=true;controller=new AbortController();
    progress.textContent='正在检索证据…';summary.textContent='';
    // 开启 LLM 组织答案时，检索完证据后还要等模型生成，明确提示避免误以为卡住
    if(body.generate)progress.textContent='正在检索，找到有效依据后调用模型…';
    const target=turn||addTurn('assistant','');
    target.dataset.state='streaming';target.replaceChildren();
    const answer=document.createElement('div');answer.className='qa-answer';answer.dataset.answer='';target.append(answer);
    markDraft(target);
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
        window.WorkspaceContext?.requireRetrievalMode(body.retrieval_mode,item.data);
        capture(item.data);record.evidence=evidenceSummary(item.data?.evidence);
        for(const [key] of referenceKinds)if(Array.isArray(item.data?.[key]))record[key]=evidenceSummary(item.data[key]);
        for(const key of ['as_of','known_at'])if(typeof item.data?.[key]==='string')record[key]=item.data[key];
        if(Array.isArray(item.data?.coverage_warnings))record.coverage_warnings=item.data.coverage_warnings.slice(0,100);
        const platformGroups=item.data?.platform_groups||[];
        if(platformGroups.length)record.platform_groups=platformGroups.map(group=>({
          platform:group.platform,source_scope:group.source_scope,candidate_count:group.candidate_count,
          evidence_count:Array.isArray(group.evidence_rows)?group.evidence_rows.length:0,
        }));
        // C1：结构待定的知识单列，不进证据清单 —— 但历史重绘时要原样恢复（否则切一次标签就"消失"）。
        record.structure_pending=item.data?.structure_pending||null;
        const channels=item.data?.channels||{};
        summary.textContent='实体证据 '+(channels.entity??0)+' · 原文证据 '+(channels.chunk??0)+' · 图关系证据 '+(channels.graph_evidence??0)
          +(item.data?.structure_pending?.count?' · 结构待定 '+(item.data.structure_pending.count)+' 条（不算证据）':'');
        addEvidence(target,{...item.data,answer_id:record.answer_id});
        progress.textContent=body.generate&&record.evidence.length?'模型正在组织答案…':'正在整理检索结果…';
        showRetrievalNotice(item.data);
      }else if(item.event==='delta'){
        const chunk=String(item.data?.text??'');
        text+=chunk;pending+=chunk;answer.textContent=text;speak(false);atEnd();
      }else if(item.event==='done'){
        capture(item.data);
        record.generation=item.data?.generation;record.timings_ms=item.data?.timings_ms;record.mode=item.data?.mode;
        record.answer_purpose=item.data?.answer_purpose;record.answer_label=item.data?.answer_label;
        if(typeof item.data?.answer==='string'){text=item.data.answer;answer.textContent=text;spoken='';pending=text;live.textContent='';}
        finished=true;
      }
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
        if(mine!==serial||project!==current||(scopeTicket&&!window.WorkspaceContext.accepts(scopeTicket,current,'qa'))){await reader.cancel();throw Error('范围已变化，已停止显示旧回答');}
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
      target.querySelector('.qa-draft-status')?.remove();
      record.text=text;
      provenance.decorateAnswer(answer,text,{...answerContext(record),projectId:project});
      if(!record.answer_id)markUnresolvedProvenance(target);
      window.QaPresentation?.annotate(target,record);
      persist();
    }catch(error){
      if(mine!==serial||project!==current)return;
      failed=true;target.dataset.state='error';progress.textContent='';
      markDraft(target,true);
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
    const asOf=$c('qa-as-of')?.value;
    const body={...scope(),valid_at:asOf?new Date(asOf).toISOString():(scope().valid_at||null),include_references:$c('qa-include-references')?.checked??true,
      query,generate:$c('generate').checked,retrieval_mode:$c('qa-mode')?.value||'hybrid',k_entities:5,k_chunks:5};
    try{window.ScopeFields.requireRetrievalScope(body);}
    catch(error){summary.textContent=error.message;progress.textContent='';return;}
    addScopeNote(addTurn('user',query),scope());qaHistory.push({kind:'user',text:query,scope:scope(),project_id:current});persist();input.value='';
    stream(body);
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
  document.addEventListener('workspace:scope-changed',event=>{if(event.detail.page!=='qa')return;serial++;controller?.abort();controller=null;busy=false;send.disabled=false;progress.textContent='';summary.textContent='筛选已更新，仅影响下一次提问；已有回答保留提交时范围。';});
  $c('qa-composer').addEventListener('submit',event=>{event.preventDefault();submit();});
  $c('qa-clear')?.addEventListener('click',()=>window.clearKnowledgeChat({clearPersist:true,notify:true}));
  // Milvus failure is explicit; keyword remains a user-selected independent mode.
  function showRetrievalNotice(payload){
    const notice=$c('qa-notice'),text=$c('qa-notice-text');
    if(!notice||!text)return;
    const errors=payload?.retrieval_errors||{};
    const semanticErrors=Object.entries(errors).filter(([key])=>key==='semantic'||key.endsWith(':semantic')).map(([,value])=>String(value)).join('；');
    const noVector=/语义索引尚未就绪|Embedding model differs|嵌入模型|嵌入维度/.test(semanticErrors);
    if(noVector){
      text.textContent='Milvus 语义索引尚未就绪，本次检索未完成。请构建或修复索引后重新提问。';
      notice.hidden=false;
    }else if(payload?.degraded){
      text.textContent='Milvus 检索未完成。请在「构建与任务」核验连接、模型与索引。';
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
      await window.ProjectVectorIndex.open(current);
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
    page.style.setProperty('--qa-fit',Math.max(0,Math.floor(height+offsetTop-page.getBoundingClientRect().top-10))+'px');
    page.style.setProperty('--qa-keyboard-inset',Math.max(0,Math.round(window.innerHeight-height-offsetTop))+'px');
  }
  window.addEventListener('resize',fit);
  window.visualViewport?.addEventListener('resize',fit);
  window.visualViewport?.addEventListener('scroll',fit);
  document.querySelector('[data-tab="qa"]')?.addEventListener('click',()=>setTimeout(fit,0));
  // On mobile the sidebar is above QA; account/navigation updates can move the
  // page without resizing the viewport or the content filter toolbar.
  if(window.ResizeObserver){const observer=new ResizeObserver(fit);for(const host of [$c('scope'),$c('status'),document.querySelector('body>aside'),document.querySelector('main>header')])if(host)observer.observe(host);}
  fit();
})();
window.addEventListener('resize',()=>{wb.chart?.resize();wb.tree?.resize();});
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{setTimeout(()=>{wb.chart?.resize();wb.tree?.resize();},0);}));
