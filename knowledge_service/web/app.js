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
const titles = {runtime:'项目与运行','ontology-model':'本体建模层',search:'检索与交互图谱',qa:'知识问答',ingest:'知识写入',records:'知识台账',sources:'原文数据源',mindmap:'实体脑图'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = id => $(id).value.trim() ? JSON.parse($(id).value) : null;
const iso = id => $(id).value ? new Date($(id).value).toISOString() : null;
let ontologyTerms = new Map();
function cacheOntologyTerms(summary={}){ontologyTerms.clear();const push=t=>ontologyTerms.set(t.id,t).set(t.name,t);(summary.classes||[]).forEach(push);(summary.relations||[]).forEach(push);(summary.attributes||[]).forEach(push);}
async function loadOntologyTerms(){ const p=current; if(!p){ontologyTerms.clear();return;} try{const r=await api(endpoint('/ontology'),undefined,'GET'); if(p!==current) return;cacheOntologyTerms(r.summary);}catch(e){ontologyTerms.clear();} }
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
  const response=await fetch(path,{method,headers:body===undefined||multipart?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:multipart?body:JSON.stringify(body)});
  const text=await response.text();let result;
  try{result=text?JSON.parse(text):{};}catch{throw Error(response.ok?'服务返回了无法解析的数据':`服务请求失败（HTTP ${response.status}）：${text.slice(0,180)||'无错误详情'}`);}
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));return result;
}
function endpoint(suffix){if(!current)throw Error('请先选择或创建项目');return '/api/projects/'+encodeURIComponent(current)+suffix;}
function scope(){return {known_at:iso('known-at'),filters:json('filters'),include_unknown:true};}
function bind(id,fn){$(id).onclick=async()=>{const button=$(id);button.disabled=true;status('处理中…');try{await fn();if($('status').textContent==='处理中…')status('完成');}catch(error){status(error.message,true);}finally{button.disabled=false;}};}
async function projects(){const result=await api('/api/projects',undefined,'GET');$('project').innerHTML='<option value="">选择项目</option>'+result.projects.map(p=>`<option value="${esc(p.id)}" data-ontology-mode="${esc(p.metadata?.ontology_mode||'ontology')}">${esc(p.name)}</option>`).join('');if(current)$('project').value=current;}
$('project').onchange=()=>{current=$('project').value;for(const id of ['hits','records','history','ontology-result','ontology-summary','search-summary'])$(id).textContent='';window.clearKnowledgeChat?.({notify:true});for(const id of ['turtle','revision','revision-id'])$(id).value='';$('revision-version').value=1;$('ontology-versions').innerHTML='';status('项目已切换');};
$('project').addEventListener('change',()=>{loadOntologyTerms().catch(()=>{});});
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));$('tab-'+button.dataset.tab).classList.remove('hidden');$('title').textContent=titles[button.dataset.tab];document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));$('scope').classList.toggle('hidden',!['search','records','sources','runtime'].includes(button.dataset.tab));});
// Project creation is implemented by the inline workbench form.
bind('add-filter',async()=>{const field=$('filter-field').value.trim();if(!field)throw Error('填写 metadata 字段路径');let value=$('filter-value').value;try{value=JSON.parse(value);}catch{}const condition={field,op:$('filter-op').value,value};const previous=json('filters');$('filters').value=JSON.stringify(previous?{and:[previous,condition]}:condition,null,2);});
// Search lives in the linked workspace module (workspace.js); it owns #search, #query and the result rail.
$('doc-file').onchange=async()=>{const file=$('doc-file').files[0];if(file){$('doc-title').value=file.name;$('doc-text').value=await file.text();}};
bind('ingest',async()=>{const mode=document.getElementById('extraction-mode').value,result=await api(endpoint('/documents'),{title:$('doc-title').value,text:$('doc-text').value,metadata:readDocumentMetadata(),extract:mode!=='documents',extraction_mode:mode});status(`文档 ${result.document.id} 已处理，写入 ${result.records.length} 条知识。`);});
bind('write-batch',async()=>{const result=await api(endpoint('/records'),json('batch'));status(`已写入 ${result.records.length} 条知识。`);});
bind('load-graph',async()=>{const result=await api(endpoint('/graph'),scope());$('records').innerHTML=`<p>${result.nodes.length} 个实体 · ${result.edges.length} 条关系</p>`+result.edges.map(r=>`<div class="card">${esc(recordName(r.subject_id))} → <strong>${typeHint(r.type)}</strong> → ${esc(recordName(r.object_id))}</div>`).join('');});
bind('revise',async()=>{const result=await api(endpoint('/records/'+encodeURIComponent($('revision-id').value)),{record:json('revision'),expected_version:Number($('revision-version').value)},'PUT');status(`已保存版本 ${result.version}；旧版本仍可按系统时间查询。`);$('revision-version').value=result.version;});
async function loadOntology(id=''){const p=current;let result;try{result=await api(endpoint('/ontology')+(id?'?ontology_id='+encodeURIComponent(id):''),undefined,'GET');}catch(error){if(p!==current)return null;$('turtle').value='';$('ontology-summary').textContent='尚未发布本体；可先持续开放发现并累计候选，再到「本体建模层」发布版本。';return null;}if(p!==current)return result;cacheOntologyTerms(result.summary);$('turtle').value=result.turtle;$('ontology-summary').textContent=`${result.summary.classes.length} 类 · ${result.summary.relations.length} 关系 · ${result.summary.attributes.length} 属性`;return result;}
bind('load-ontology',async()=>{const p=current;const result=await api(endpoint('/ontologies'),undefined,'GET');if(p!==current)return;$('ontology-versions').innerHTML=[...result.versions].reverse().map(v=>`<option value="${esc(v.id)}">${esc(v.created_at)} · ${esc(v.id)}</option>`).join('');await loadOntology();});
$('ontology-versions').onchange=async()=>{try{await loadOntology($('ontology-versions').value);}catch(e){status(e.message,true);}};
bind('save-ontology',async()=>{const result=await api(endpoint('/ontologies'),{turtle:$('turtle').value});status(`本体新版本 ${result.id} 已保存，下次抽取直接生效。`);});
bind('validate',async()=>{const id=$('ontology-versions').value;$('ontology-result').textContent=JSON.stringify(await api(endpoint('/ontology/validate')+(id?'?ontology_id='+encodeURIComponent(id):''),scope()),null,2);});
bind('run-sparql',async()=>{$('ontology-result').textContent=JSON.stringify(await api(endpoint('/sparql'),{...scope(),query:$('sparql').value,ontology_id:$('ontology-versions').value||null}),null,2);});
(async()=>{try{
  // 等认证就绪（auth.js）：未登录时这里会挂起，登录成功后才继续，
  // 避免未授权就请求 /api/health、/api/projects 而报 401。
  if(window.__authReady && typeof window.__authReady.then==='function'){await window.__authReady;}
  const health=await api('/api/health',undefined,'GET');$('health').textContent=`服务在线 · ${health.semantic?'语义模型已配置':'演示模式'}`;await projects();await loadOntologyTerms().catch(()=>{});}catch(e){status(e.message,true);}})();
