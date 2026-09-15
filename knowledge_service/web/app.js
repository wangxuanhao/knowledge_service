const $ = id => document.getElementById(id);
let current = '';
const titles = {projects:'项目管理',search:'检索与问答',ingest:'知识写入',records:'知识与历史',ontology:'本体管理',discovery:'本体发现',dashboard:'项目总览',graph:'交互图谱',sources:'原文数据源',mindmap:'实体脑图',jobs:'后台任务',evaluation:'快照与评测'};
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
function status(message, error=false){
  clearTimeout(statusTimer);
  const element=$('status');
  element.textContent=message;element.classList.toggle('error',error);
  // Keep errors and ongoing progress visible; each new notice owns its timer.
  if(message&&!error&&!/^(处理中|正在|读取中|生成中)/.test(message)){
    statusTimer=setTimeout(()=>{element.textContent='';element.classList.remove('error');},4000);
  }
}
async function api(path,body,method='POST'){
  const response=await fetch(path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const text=await response.text();let result;
  try{result=text?JSON.parse(text):{};}catch{throw Error(response.ok?'服务返回了无法解析的数据':`服务请求失败（HTTP ${response.status}）：${text.slice(0,180)||'无错误详情'}`);}
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));return result;
}
function endpoint(suffix){if(!current)throw Error('请先选择或创建项目');return '/api/projects/'+encodeURIComponent(current)+suffix;}
function scope(){return {known_at:iso('known-at'),filters:json('filters'),include_unknown:true};}
function bind(id,fn){$(id).onclick=async()=>{const button=$(id);button.disabled=true;status('处理中…');try{await fn();if($('status').textContent==='处理中…')status('完成');}catch(error){status(error.message,true);}finally{button.disabled=false;}};}
function card(row,actions=false){
  const rowType=row.type_label||labelOf(row.type);
  const typeDisplay=row.kind==='relation'
    ? `${esc(recordName(row.subject_id))} —[${esc(rowType)}]→ ${esc(recordName(row.object_id))}`
    : esc(rowType);
  return `<article class="card"><h3>${esc(row.text?.slice(0,110)||row.id)}</h3><div class="subtle">${esc(row.kind)} · ${typeDisplay} · 版本 ${esc(row.version)} ${row.score!==undefined?'· 匹配分 '+row.score.toFixed(3):''}</div><p>${esc(row.text)}</p><div class="subtle">有效期 ${esc(row.valid_from||'未知')} → ${esc(row.valid_until||'未知')}<br>记录时间 ${esc(row.recorded_at)}<br>来源 ${esc(row.source_id||'手工 / 原始记录')} · 本体版本 ${esc(row.ontology_id||'—')}</div><details><summary>Metadata 与属性</summary><pre>${esc(JSON.stringify({metadata:row.metadata,properties:row.properties},null,2))}</pre></details>${actions?`<div class="row"><button data-history="${esc(row.id)}">查看历史</button><button data-edit="${esc(row.id)}" class="secondary">编辑</button></div>`:''}</article>`;
}
async function projects(){const result=await api('/api/projects',undefined,'GET');$('project').innerHTML='<option value="">选择项目</option>'+result.projects.map(p=>`<option value="${esc(p.id)}" data-ontology-mode="${esc(p.metadata?.ontology_mode||'ontology')}">${esc(p.name)}</option>`).join('');if(current)$('project').value=current;}
$('project').onchange=()=>{current=$('project').value;for(const id of ['hits','records','history','answer','ontology-result','ontology-summary','search-summary'])$(id).textContent='';for(const id of ['turtle','revision','revision-id'])$(id).value='';$('revision-version').value=1;$('ontology-versions').innerHTML='';status('项目已切换');};
$('project').addEventListener('change',()=>{loadOntologyTerms().catch(()=>{});});
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));$('tab-'+button.dataset.tab).classList.remove('hidden');$('title').textContent=titles[button.dataset.tab];document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));$('scope').classList.toggle('hidden',!['search','graph','records','sources'].includes(button.dataset.tab));});
// Project creation is implemented by the inline workbench form.
bind('add-filter',async()=>{const field=$('filter-field').value.trim();if(!field)throw Error('填写 metadata 字段路径');let value=$('filter-value').value;try{value=JSON.parse(value);}catch{}const condition={field,op:$('filter-op').value,value};const previous=json('filters');$('filters').value=JSON.stringify(previous?{and:[previous,condition]}:condition,null,2);});
async function search(answer=false){const body={...scope(),query:$('query').value,k:10};if(answer)body.generate=$('generate').checked;const result=await api(endpoint(answer?'/qa':'/search'),body);$('hits').innerHTML=result.hits.map(r=>card(r)).join('')||'<p class="subtle">当前条件没有匹配知识。</p>';$('answer').textContent=result.answer||'';$('search-summary').textContent=`筛选后 ${result.candidate_count} 条候选 · 返回 ${result.hits.length} 条 · ${result.semantic?'语义向量检索':'演示字符匹配（非语义模型）'}`;}
bind('search',()=>search());bind('qa',()=>search(true));$('query').onkeydown=e=>{if(e.key==='Enter')$('search').click();};
$('doc-file').onchange=async()=>{const file=$('doc-file').files[0];if(file){$('doc-title').value=file.name;$('doc-text').value=await file.text();}};
bind('ingest',async()=>{const mode=document.getElementById('extraction-mode').value,result=await api(endpoint('/documents'),{title:$('doc-title').value,text:$('doc-text').value,metadata:readDocumentMetadata(),extract:mode!=='documents',extraction_mode:mode});status(`文档 ${result.document.id} 已处理，写入 ${result.records.length} 条知识。`);});
bind('write-batch',async()=>{const result=await api(endpoint('/records'),json('batch'));status(`已写入 ${result.records.length} 条知识。`);});
bind('load-records',async()=>{const result=await api(endpoint('/records/query'),scope());$('records').innerHTML=`<p class="subtle">共 ${result.total} 条，显示前 ${result.records.length} 条。更多记录可通过接口分页。</p>`+result.records.map(r=>card(r,true)).join('');$('records').querySelectorAll('[data-history]').forEach(b=>b.onclick=async()=>{try{$('history').textContent=JSON.stringify(await api(endpoint('/records/'+encodeURIComponent(b.dataset.history)+'/history'),undefined,'GET'),null,2);}catch(e){status(e.message,true);}});$('records').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const row=result.records.find(r=>r.id===b.dataset.edit);const fields=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until'];$('revision').value=JSON.stringify(Object.fromEntries(fields.filter(f=>row[f]!==undefined).map(f=>[f,row[f]])),null,2);$('revision-id').value=row.id;$('revision-version').value=row.version;});});
bind('load-graph',async()=>{const result=await api(endpoint('/graph'),scope());$('records').innerHTML=`<p>${result.nodes.length} 个实体 · ${result.edges.length} 条关系</p>`+result.edges.map(r=>`<div class="card">${esc(recordName(r.subject_id))} → <strong>${typeHint(r.type)}</strong> → ${esc(recordName(r.object_id))}</div>`).join('');});
bind('revise',async()=>{const result=await api(endpoint('/records/'+encodeURIComponent($('revision-id').value)),{record:json('revision'),expected_version:Number($('revision-version').value)},'PUT');status(`已保存版本 ${result.version}；旧版本仍可按系统时间查询。`);$('revision-version').value=result.version;});
async function loadOntology(id=''){const p=current;let result;try{result=await api(endpoint('/ontology')+(id?'?ontology_id='+encodeURIComponent(id):''),undefined,'GET');}catch(error){if(p!==current)return null;$('turtle').value='';$('ontology-summary').textContent='尚未发布本体；可先持续开放发现并累计候选，再到「本体发现」发布版本。';return null;}if(p!==current)return result;cacheOntologyTerms(result.summary);$('turtle').value=result.turtle;$('ontology-summary').textContent=`${result.summary.classes.length} 类 · ${result.summary.relations.length} 关系 · ${result.summary.attributes.length} 属性`;return result;}
bind('load-ontology',async()=>{const p=current;const result=await api(endpoint('/ontologies'),undefined,'GET');if(p!==current)return;$('ontology-versions').innerHTML=[...result.versions].reverse().map(v=>`<option value="${esc(v.id)}">${esc(v.created_at)} · ${esc(v.id)}</option>`).join('');await loadOntology();});
$('ontology-versions').onchange=async()=>{try{await loadOntology($('ontology-versions').value);}catch(e){status(e.message,true);}};
bind('save-ontology',async()=>{const result=await api(endpoint('/ontologies'),{turtle:$('turtle').value});status(`本体新版本 ${result.id} 已保存，下次抽取直接生效。`);});
bind('validate',async()=>{const id=$('ontology-versions').value;$('ontology-result').textContent=JSON.stringify(await api(endpoint('/ontology/validate')+(id?'?ontology_id='+encodeURIComponent(id):''),scope()),null,2);});
bind('run-sparql',async()=>{$('ontology-result').textContent=JSON.stringify(await api(endpoint('/sparql'),{...scope(),query:$('sparql').value,ontology_id:$('ontology-versions').value||null}),null,2);});
(async()=>{try{const health=await api('/api/health',undefined,'GET');$('health').textContent=`服务在线 · ${health.semantic?'语义模型已配置':'演示模式'} · Semantica ${health.semantica_version||'未安装'}`;await projects();await loadOntologyTerms().catch(()=>{});}catch(e){status(e.message,true);}})();
