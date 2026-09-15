/* Rich exploration on the new service. No native browser prompt/confirm APIs. */
const wb={epoch:0,chart:null,tree:null,nodes:new Map(),records:new Map(),jobs:new Map()};
const term=s=>{try{const value=decodeURIComponent(String(s||'').split(/[\/#]/).pop());return value.startsWith('urn:')?value.split(':').pop():value}catch{return String(s||'')}};
function showTab(name){document.querySelector(`[data-tab="${name}"]`).click();}
function controlsProject(){if(!current)throw Error('请先选择项目');return current;}
async function scopedRead(path,extra={}){const p=controlsProject(),epoch=wb.epoch;const result=await api('/api/projects/'+encodeURIComponent(p)+path,{...scope(),...extra});if(p!==current||epoch!==wb.epoch)throw Error('项目已切换，旧响应已忽略');return result;}
function discoveryHintHost(canvasId){const canvas=$(canvasId);if(!canvas||!canvas.parentNode)return null;let host=$(canvasId+'-discovery-hint');if(!host){host=document.createElement('div');host.id=canvasId+'-discovery-hint';host.className='discovery-hint';host.hidden=true;canvas.parentNode.insertBefore(host,canvas.parentNode.firstChild);}return host;}
async function renderDiscoveryHint(){const p=current,mode=$('project').selectedOptions?.[0]?.dataset?.ontologyMode,hosts=['graph-canvas','mindmap-canvas'].map(discoveryHintHost);if(!p||mode!=='discovery'){hosts.forEach(h=>{if(h)h.hidden=true;});return;}let data;try{data=await api(endpoint('/ontology-discovery'),undefined,'GET');}catch(e){hosts.forEach(h=>{if(h)h.hidden=true;});return;}if(p!==current)return;const pending=data.unpublished_candidate_count||0;hosts.forEach(host=>{if(!host)return;if(pending>0||data.requires_controlled_reingest){host.hidden=false;host.innerHTML=pending>0?`<span>开放本体发现：还有 <b>${pending}</b> 条候选尚未纳入已发布本体。可继续开放发现，或到「本体发现」生成累计草案。</span><button data-goto-discovery>前往本体发现 ↗</button>`:'<span>本体版本已经发布，但候选不会自动入图。请使用项目本体受控重解析原文，再查看正式脑图。</span><button data-goto-discovery>查看本体版本 ↗</button>';host.querySelector('[data-goto-discovery]').onclick=()=>showTab('discovery');}else host.hidden=true;});}
for(const tab of ['graph','mindmap'])document.querySelector(`[data-tab="${tab}"]`)?.addEventListener('click',()=>renderDiscoveryHint().catch(()=>{}));
const oldChange=$('project').onchange;
$('project').onchange=()=>{oldChange();wb.epoch++;wb.records.clear();wb.nodes.clear();for(const id of ['dashboard','source-list','source-body','graph-detail','resolve-result','tasks','operations','evaluation-result'])$(id).textContent='';const preferred=$('project').selectedOptions?.[0]?.dataset?.ontologyMode,mode=document.getElementById('extraction-mode');if(preferred&&mode){mode.value=preferred;mode.onchange?.();}wb.chart?.clear();wb.tree?.clear();renderDiscoveryHint().catch(()=>{});};
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
async function drawGraph(node=null,hops=1){const r=await scopedRead('/subgraph',{node_id:node,hops});wb.nodes=new Map(r.nodes.map(n=>[n.id,n]));const types=[...new Set(r.nodes.map(n=>labelOf(n.type)))];const chart=initChart();chart.setOption({animation:false,tooltip:{trigger:'item',formatter:p=>esc(p.dataType==='edge'?(p.data.rawType&&p.data.rawType!==p.data.name?p.data.name+' ('+p.data.rawType+')':p.data.name):p.data.name+' · '+p.data.categoryName)},legend:[{type:'scroll',data:types,bottom:0}],series:[{type:'graph',layout:'force',roam:true,draggable:true,label:{show:true,fontSize:11},edgeSymbol:['none','arrow'],edgeLabel:{show:r.edges.length<100,formatter:'{c}',fontSize:10},force:{repulsion:230,edgeLength:[70,160],gravity:.07},categories:types.map(name=>({name})),data:r.nodes.map(n=>({id:n.id,name:n.text,category:types.indexOf(labelOf(n.type)),categoryName:labelOf(n.type),symbolSize:25})),links:r.edges.map(e=>({id:e.id,source:e.subject_id,target:e.object_id,name:labelOf(e.type),value:labelOf(e.type),rawType:term(e.type)})),lineStyle:{curveness:.12,opacity:.55}}]},true);chart.resize();$('graph-summary').textContent=`${r.nodes.length} 个节点 · ${r.edges.length} 条关系 · 拖动节点 / 滚轮缩放 / 点击查看证据`;renderDiscoveryHint().catch(()=>{});}
bind('draw-graph',()=>drawGraph());bind('graph-expand',async()=>{const id=$('graph-node').value;if(!id)throw Error('请先选择实体或点击图中的节点，再展开邻域。');const hops=Number($('graph-hops').value);await drawGraph(id,hops);status('已展开 '+hops+' 跳邻域');});
async function historyFor(row){const p=current;const r=await api(endpoint('/records/'+encodeURIComponent(row.id)+'/history'),undefined,'GET');if(p!==current)return;$('history').textContent=JSON.stringify(r,null,2);showTab('records');}
function editRecord(row){showTab('records');const fields=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];$('revision').value=JSON.stringify(Object.fromEntries(fields.filter(k=>row[k]!==undefined).map(k=>[k,row[k]])),null,2);$('revision-id').value=row.id;$('revision-version').value=row.version;$('keep-id').value=row.id;}
bind('load-records',async()=>{const r=await scopedRead('/records/query?limit=1000');wb.records=new Map(r.records.map(x=>[x.id,x]));$('records').innerHTML=`<p class="subtle">共 ${r.total} 条，展示 ${r.records.length} 条；超过 1000 条请使用 API 分页。</p><div class="table-scroll"><table><thead><tr><th>名称 / 内容</th><th>类型</th><th>版本 / 有效期</th><th>操作</th></tr></thead><tbody>${r.records.map(row=>`<tr><td>${esc(row.text.slice(0,100))}<small>${esc(row.id)}</small></td><td>${esc(row.kind)}<small>${typeHint(row.type)}</small></td><td>v${row.version}<small>${esc(row.valid_from||'未知')} → ${esc(row.valid_until||'未知')}</small></td><td><button data-edit="${esc(row.id)}">编辑</button> <button data-history="${esc(row.id)}" class="secondary">历史</button></td></tr>`).join('')}</tbody></table></div>`;$('records').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>editRecord(wb.records.get(b.dataset.edit)));$('records').querySelectorAll('[data-history]').forEach(b=>b.onclick=()=>historyFor(wb.records.get(b.dataset.history)));});
bind('load-graph',async()=>{showTab('graph');await drawGraph();});
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
const acceptedUploads=new WeakMap();
function readParseSettings(){
  const settings={chunk_strategy:$('parse-strategy').value,chunk_size:Number($('parse-size').value),chunk_overlap:Number($('parse-overlap').value),resolve_entities:$('parse-resolve').checked,auto_merge:$('parse-merge').checked,merge_threshold:Number($('parse-threshold').value)};
  if(!Number.isInteger(settings.chunk_size)||settings.chunk_size<100||settings.chunk_size>10000)throw Error('最大字符数需为 100–10000 的整数');
  if(!Number.isInteger(settings.chunk_overlap)||settings.chunk_overlap<0||settings.chunk_overlap>2000||settings.chunk_overlap>=settings.chunk_size)throw Error('重叠字符数需为 0–2000 的整数，且小于最大字符数');
  if(settings.auto_merge&&!settings.resolve_entities)throw Error('请先开启实体消歧，再开启语义合并');
  if(!Number.isFinite(settings.merge_threshold)||settings.merge_threshold<0||settings.merge_threshold>1)throw Error('合并阈值需在 0–1 之间');
  return settings;
}
bind('preview-chunks',async()=>{
  const p=controlsProject(),settings=readParseSettings();
  const documents=await readDocumentBatch(),doc=documents[0];
  const result=await api('/api/projects/'+encodeURIComponent(p)+'/documents/preview',{title:doc.title,text:doc.text,...settings});
  if(p!==current)return;
  $('chunk-preview').innerHTML=`<p>${esc(doc.title)} · 共 ${result.total} 片，预览前 ${result.chunks.length} 片</p>`+result.chunks.map((c,i)=>`<details><summary>片段 ${i+1} · 原文位置 ${c.start_char}–${c.end_char} · ${c.end_char-c.start_char} 字符</summary><pre>${esc(c.text)}</pre></details>`).join('');
  status('切片预览完成，未调用模型或写入知识');
});
bind('ingest',async()=>{
  const project=controlsProject(),mode=document.getElementById('extraction-mode').value,extract=mode!=='documents',settings=readParseSettings();
  const documents=await readDocumentBatch(),results=$('doc-submit-results');
  results.textContent='';let accepted=0,failed=0;
  for(const document of documents){
    const entry=window.document.createElement('p');results.append(entry);
    const prior=document.file&&acceptedUploads.get(document.file)?.[project];
    if(prior){entry.textContent=document.title+' · 已提交，任务 '+prior;accepted++;continue;}
    entry.textContent=document.title+' · 正在提交…';
    try{
      const job=await api('/api/projects/'+encodeURIComponent(project)+'/documents/jobs',{title:document.title,text:document.text,metadata:document.metadata,extract,extraction_mode:mode,...settings});
      if(document.file){const previous=acceptedUploads.get(document.file)||{};acceptedUploads.set(document.file,{...previous,[project]:job.id});}
      wb.jobs.set(job.id,job);accepted++;entry.textContent=document.title+' · 已加入解析队列 · '+job.id;
    }catch(error){failed++;entry.textContent=document.title+' · 提交失败：'+error.message;}
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
bind('qa',async()=>{const p=controlsProject(),epoch=wb.epoch;$('answer').textContent='';$('hits').textContent='';const response=await fetch(endpoint('/qa/stream'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...scope(),query:$('query').value,generate:$('generate').checked,k_entities:5,k_chunks:5,hops:2})});if(!response.ok)throw Error(await response.text());const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {done,value}=await reader.read();if(done)break;if(p!==current||epoch!==wb.epoch){await reader.cancel();throw Error('项目已切换，已停止显示旧回答');}buffer+=decoder.decode(value,{stream:true});let split;while((split=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,split);buffer=buffer.slice(split+2);const event=block.match(/^event: (.+)$/m)?.[1],data=JSON.parse(block.match(/^data: (.+)$/m)?.[1]||'{}');if(event==='delta')$('answer').textContent+=data.text;if(event==='error')throw Error(data.detail);if(event==='evidence'){$('search-summary').textContent=`实体候选 ${data.channels.entity} · 原文候选 ${data.channels.chunk} · 图关系证据 ${data.channels.graph_evidence}`;$('hits').innerHTML=data.evidence.map(r=>`<div><b>[${esc(r.citation)}]</b>${card(r)}${r.kind==='entity'||r.kind==='relation'?`<button data-evidence-node="${esc(r.kind==='entity'?r.id:r.subject_id)}">在图谱定位</button>`:''}${r.source_id?`<button data-evidence-source="${esc(r.source_id)}" class="secondary">来源版本</button>`:''}</div>`).join('');$('hits').querySelectorAll('[data-evidence-node]').forEach(b=>b.onclick=async()=>{try{showTab('graph');await drawGraph(b.dataset.evidenceNode,1);}catch(e){status(e.message,true);}});$('hits').querySelectorAll('[data-evidence-source]').forEach(b=>b.onclick=()=>historyFor({id:b.dataset.evidenceSource}));}}}});
window.addEventListener('resize',()=>{wb.chart?.resize();wb.tree?.resize();});
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{setTimeout(()=>{wb.chart?.resize();wb.tree?.resize();},0);}));
