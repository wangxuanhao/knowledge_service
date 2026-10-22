const $ = id => document.getElementById(id);
// Browser-side counterpart of knowledge_service/diagnostics.py: shows how much of a
// "slow page" is the server read versus the client render. Off by default so the
// production console stays quiet — enable with localStorage.kgDebug='1' or ?debug=1.
const kgDebug=(()=>{try{return localStorage.getItem('kgDebug')==='1'||new URLSearchParams(location.search).get('debug')==='1';}catch{return false;}})();
const kgLog=(label,fields={})=>{if(!kgDebug)return;const detail=Object.entries(fields).map(([key,value])=>key+'='+value).join(' ');console.info('[kg] '+label+(detail?' · '+detail:''));};
const kgTime=label=>{const started=performance.now();return fields=>kgLog(label,{...(fields||{}),ms:(performance.now()-started).toFixed(1)});};
let current = '';
// 键名含连字符必须加引号（本体三页是 data-tab 名，不是合法标识符）。
// 「项目与运行」把原来的 项目管理 / 项目总览 / 后台任务 / 快照·评测 四页合并成一个入口（D2）。
// 分区的"后缀"由 runtime-view.js 在切分区时补上（如「项目与运行 · 运行总览」）。
const titles = {runtime:'构建与任务',projects:'项目管理',quality:'质量评测',useradmin:'人员管理','ontology-model':'本体建模层',search:'检索与交互图谱',qa:'知识问答',ingest:'添加文件',records:'知识台账',sources:'资料库',mindmap:'实体脑图'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = id => $(id).value.trim() ? JSON.parse($(id).value) : null;
const iso = id => $(id).value ? new Date($(id).value).toISOString() : null;
let ontologyTerms = new Map();
function cacheOntologyTerms(summary={}){ontologyTerms.clear();const push=t=>ontologyTerms.set(t.id,t).set(t.name,t);(summary.classes||[]).forEach(push);(summary.relations||[]).forEach(push);(summary.attributes||[]).forEach(push);}
let ontologyTermsRequest=null,ontologyTermsGeneration=0;
function loadOntologyTerms({fresh=false}={}){
  const p=current;if(!p){ontologyTerms.clear();return Promise.resolve(null);}
  if(fresh){ontologyTermsGeneration++;ontologyTermsRequest=null;}
  const generation=ontologyTermsGeneration;
  if(ontologyTermsRequest?.project===p)return ontologyTermsRequest.promise;
  const promise=api(endpoint('/ontology'),undefined,'GET').then(result=>{
    if(p!==current||generation!==ontologyTermsGeneration||ontologyTermsRequest?.promise!==promise)return null;cacheOntologyTerms(result.summary);return result;
  }).catch(()=>{if(p===current&&generation===ontologyTermsGeneration&&ontologyTermsRequest?.promise===promise)ontologyTerms.clear();return null;})
    .finally(()=>{if(ontologyTermsRequest?.promise===promise)ontologyTermsRequest=null;});
  ontologyTermsRequest={project:p,promise};return promise;
}
function labelOf(type){ const local=typeof term==='function'?term(type):String(type||'').split(/[\/#]/).pop(); const t=ontologyTerms.get(type)||ontologyTerms.get(local); return t?(t.label_zh||((t.label&&t.label!==t.name)?t.label:(t.description||t.name||local))):({'name':'名称'}[local]||local); }
const typeHint=type=>{const primary=labelOf(type),local=typeof term==='function'?term(type):String(type||'').split(/[\/#]/).pop();return primary===local?esc(primary):`${esc(primary)} <span class="subtle">${esc(local)}</span>`;};
const recordName=id=>{const row=typeof wb!=='undefined'&&wb.records?wb.records.get(id):null;return row?row.text:(id||'(未加载)');};
let statusTimer;
// 提示条＝实体卡片，不再是一条压在页面中间的半透明细条。
// 用户反馈（原话）："提示内容位置不合适，这种的都重新设计下 这些搞成实体框 在上面提示下啊，
// 提示框样式你优化下"。旧样式是 fixed 居中压在标题栏上、12px 灰字、几乎看不见边界；
// 现在：页面流内（不遮任何内容）+ 实心白底卡片 + 左侧色条 + 图标 + 关闭按钮，
// 文案用正文色、可换行，长句子（比如带完整 UUID 的版本号）读得清。
function noticeCard(message,{tone='info',onClose=null}={}){
  const card=document.createElement('div');
  card.className='ks-notice'+(tone&&tone!=='info'?` is-${tone}`:'');
  card.setAttribute('role',tone==='error'?'alert':'status');
  const icon=document.createElement('span');
  icon.className='ks-notice__icon';icon.setAttribute('aria-hidden','true');
  icon.textContent={error:'!',warning:'!',success:'✓'}[tone]||'i';
  const text=document.createElement('span');
  text.className='ks-notice__text';text.textContent=message;
  const close=document.createElement('button');
  close.type='button';close.className='ks-notice__close';close.textContent='×';
  close.title='关闭这条提示';close.setAttribute('aria-label','关闭这条提示');
  close.addEventListener('click',()=>{if(onClose)onClose();card.remove();});
  card.append(icon,text,close);
  return card;
}
function status(message, error=false){
  clearTimeout(statusTimer);
  const element=$('status');
  element.replaceChildren();
  element.classList.toggle('error',error);
  if(message){
    element.append(noticeCard(message,{tone:error?'error':'info',
      onClose:()=>{clearTimeout(statusTimer);element.classList.remove('error');}}));
  }
  // Keep errors and ongoing progress visible; each new notice owns its timer.
  if(message&&!error&&!/^(处理中|正在|读取中|生成中)/.test(message)){
    statusTimer=setTimeout(()=>{element.replaceChildren();element.classList.remove('error');},4000);
  }
}
async function api(path,body,method='POST'){
  const multipart=typeof FormData!=='undefined'&&body instanceof FormData;
  let response;
  try{response=await fetch(path,{method,headers:body===undefined||multipart?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:multipart?body:JSON.stringify(body)});}
  catch(error){if(error.name==='AbortError')throw error;throw Error('无法连接知识服务，请检查服务状态和网络连接后重试。');}
  const text=await response.text();let result;
  try{result=text?JSON.parse(text):{};}catch{throw Error(response.ok?'服务返回了无法解析的数据':`服务请求失败（HTTP ${response.status}）：${text.slice(0,180)||'无错误详情'}`);}
  if(!response.ok){
    const error=Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
    error.status=response.status;
    if(typeof result.code==='string')error.code=result.code;
    throw error;
  }
  return result;
}
function endpoint(suffix){if(!current)throw Error('请先选择或创建项目');return '/api/projects/'+encodeURIComponent(current)+suffix;}
function scope(){return window.WorkspaceContext?WorkspaceContext.read(current):{known_at:iso('known-at'),filters:json('filters'),include_unknown:true};}
function bind(id,fn){let sequence=0;$(id).onclick=async()=>{const ticket=++sequence,project=current,button=$(id);button.disabled=true;status('处理中…');try{await fn();if(ticket===sequence&&project===current&&$('status').textContent==='处理中…')status('完成');}catch(error){if(ticket===sequence&&project===current)status(error.message,true);}finally{if(ticket===sequence)button.disabled=false;}};}
async function projects(){const result=await api('/api/projects?include_counts=false',undefined,'GET');$('project').innerHTML='<option value="">选择项目</option>'+result.projects.map(p=>`<option value="${esc(p.id)}" data-ontology-mode="${esc(p.metadata?.ontology_mode||'ontology')}">${esc(p.name)}</option>`).join('');if(current)$('project').value=current;}
$('project').onchange=()=>{window.WorkspaceDrawers?.close({restoreFocus:false});window.RecordCreate?.close({restoreFocus:false});current=$('project').value;window.LibraryWorkspace?.projectChanged(current);window.IngestBatches?.projectChanged();for(const id of ['hits','records','history','search-summary'])$(id).textContent='';window.clearKnowledgeChat?.({notify:true});for(const id of ['revision','revision-id'])$(id).value='';$('revision-version').value=1;status('项目已切换');};
function activateTab(name,button=null){
  if(name==='ingest'&&window.LibraryWorkspace){window.LibraryWorkspace.open('upload',{trigger:button});return;}
  window.WorkspaceDrawers?.close({restoreFocus:false});
  const page=$('tab-'+name);
  if(!page)throw Error(`页面不存在：${name}`);
  document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));
  page.classList.remove('hidden');
  $('title').textContent=titles[name]||name;
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));
  window.WorkspaceContext?.enter(name);
  $('scope').classList.toggle('hidden',!['search','qa','records','sources'].includes(name));
  document.dispatchEvent(new CustomEvent('workspace:page-changed',{detail:{page:name}}));
}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>activateTab(button.dataset.tab,button));
window.KnowledgeTabs={
  open(name,{trigger=true}={}){
    const button=document.querySelector(`[data-tab="${name}"]`);
    // Preserve loaders registered by existing modules (records, ontology canvas,
    // runtime). Direct activation is only for internal pages without a menu button.
    if(button&&trigger){button.click();return;}
    activateTab(name,button);
  },
};
// Project creation is implemented by the inline workbench form.
bind('add-filter',async()=>{const field=$('filter-field').value.trim();if(!field)throw Error('填写 metadata 字段路径');let value=$('filter-value').value;try{value=JSON.parse(value);}catch{}const condition={field,op:$('filter-op').value,value};const previous=json('filters');$('filters').value=JSON.stringify(previous?{and:[previous,condition]}:condition,null,2);});
// Search lives in the linked workspace module (workspace.js); it owns #search, #query and the result rail.
// 知识写入的文件上传/提交已移到 ingest-mode.js（文件队列）+ workbench.js（bind('ingest') 提交队列）；
// 旧对话流的 doc-file.onchange / bind('ingest') 死代码已删除（doc-text 输入框已随对话流移除）。
bind('write-batch',async()=>{const result=await api(endpoint('/records'),json('batch'));status(`已写入 ${result.records.length} 条知识。`);});
bind('load-graph',async()=>{const result=await api(endpoint('/graph'),scope());$('records').innerHTML=`<p>${result.nodes.length} 个实体 · ${result.edges.length} 条关系</p>`+result.edges.map(r=>`<div class="card">${esc(recordName(r.subject_id))} → <strong>${typeHint(r.type)}</strong> → ${esc(recordName(r.object_id))}</div>`).join('');});
bind('revise',async()=>{const result=await api(endpoint('/records/'+encodeURIComponent($('revision-id').value)),{record:json('revision'),expected_version:Number($('revision-version').value)},'PUT');status(`已保存版本 ${result.version}；旧版本仍可按系统时间查询。`);$('revision-version').value=result.version;});
(async()=>{try{
  // 等认证就绪（auth.js）：未登录时这里会挂起，登录成功后才继续，
  // 避免未授权就请求 /api/health、/api/projects 而报 401。
  if(window.__authReady && typeof window.__authReady.then==='function'){await window.__authReady;}
  // workspace.js owns project bootstrap and the first view's data reads.
  }catch(e){status(e.message,true);}})();
