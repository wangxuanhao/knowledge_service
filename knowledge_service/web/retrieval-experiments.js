/* Fixed-question retrieval and QA experiments, using the shared authenticated API. */
(()=>{
  'use strict';
  const VARIANTS = {
    basic: ['基础 Local / OAG', '本体词项扩展、实体锚点、来源与有界图关系'],
    document_only: ['仅文档', '只检索原文切片，跳过实体、关系、本体辅助与图邻域'],
    no_graph: ['去图关系', '保留实体和原文检索，跳过关系邻域与路径'],
    no_ontology: ['去本体辅助', '跳过查询扩展和类型标签解析，仍核对治理资格'],
    combined: ['去图关系＋本体辅助', '同时跳过图邻域、路径、查询扩展与类型标签解析'],
  };
  const STATES = {queued:'已排队', running:'运行中', completed:'已完成', failed:'执行失败', cancelled:'已取消', invalidated:'输入已变化'};
  function parseQuestions(text){
    const raw=String(text||'').trim();
    let questions;
    if(raw.startsWith('[')){
      try{questions=JSON.parse(raw);}catch{throw Error('问题 JSON 无法解析，请检查引号和逗号。');}
    }else questions=raw.split(/\r?\n/).map(query=>query.trim()).filter(Boolean).map(query=>({query}));
    if(!Array.isArray(questions)||questions.length<1||questions.length>100)throw Error('请输入 1 到 100 道问题。');
    if(questions.some(q=>!q||typeof q.query!=='string'||!q.query.trim()))throw Error('每道问题需要非空 query。');
    return questions;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={parseQuestions,VARIANTS};
  if(typeof document==='undefined')return;
  const page=document.getElementById('tab-evaluation');
  if(!page)return;
  const extraction=page.querySelector('.extraction-evaluation');
  const tabs=document.createElement('div');tabs.className='rex-tabs';tabs.setAttribute('role','tablist');
  tabs.innerHTML='<button type="button" role="tab" aria-selected="true" data-rex-tab="extraction">抽取质量评测</button><button type="button" role="tab" aria-selected="false" data-rex-tab="retrieval">检索与问答消融</button>';
  if(extraction)page.insertBefore(tabs,extraction);else page.append(tabs);
  const panel=document.createElement('div');panel.id='retrieval-experiments';panel.className='rex-panel hidden';
  panel.innerHTML=`
    <header class="rex-heading"><div><span class="rex-eyebrow">RETRIEVAL LAB</span><h2>检索与问答消融</h2><p>用同一题集、范围与知识版本，比较实际执行的检索策略。</p></div><button type="button" class="secondary" data-rex-refresh>刷新运行记录</button></header>
    <div class="rex-notice">语义与混合检索只使用 Milvus。索引未就绪时会记录失败。模型未配置时仅保存实际检索证据，不计答案得分。</div>
    <div class="rex-layout"><form class="rex-config">
      <h3>固定实验输入</h3><label>实验名称<input name="name" maxlength="200" placeholder="例如：退款规则 · 第一轮"></label>
      <label>固定题集<textarea name="questions" rows="7" placeholder="每行一个问题；也可填写带 expected_record_ids、expected_answer 的 JSON 数组"></textarea></label>
      <details><summary>如何提供标准依据与答案</summary><pre>[{"id":"q1","query":"退款需要哪些材料？","expected_record_ids":["实际记录 ID"],"expected_answer":"人工标准答案"}]</pre><p>依据指标按记录 ID 精确比对；答案指标仅对真实模型回答做文本精确比对。未提供标准项显示「未评分」。</p></details>
      <div class="rex-fields"><label>检索模式<select name="retrieval_mode"><option value="hybrid">混合 · Milvus RRF</option><option value="semantic">语义 · Milvus 向量</option><option value="keyword">关键词</option></select></label><label>实体预算<input name="k_entities" type="number" min="0" max="50" value="5"></label><label>原文预算<input name="k_chunks" type="number" min="1" max="50" value="5"></label><label>关系跳数<input name="hops" type="number" min="0" max="3" value="2"></label></div>
      <details><summary>本实验自己的 Metadata / 时间范围</summary><p>默认读取当前项目。此处只影响本次实验，不沿用其他页面的筛选条件。</p><textarea name="scope" rows="5" aria-label="实验范围 JSON">{}</textarea></details>
      <fieldset class="rex-variants"><legend>比较变体</legend>${Object.entries(VARIANTS).map(([key,[label,description]])=>`<label><input type="checkbox" name="variants" value="${key}" checked><span><b>${label}</b><small>${description}</small></span></label>`).join('')}</fieldset>
      <label class="rex-inline"><input type="checkbox" name="generate">调用已配置模型生成回答</label>
      <button type="submit">创建并运行固定实验</button>
    </form><aside class="rex-history"><h3>运行记录</h3><div data-rex-runs class="subtle">选择项目后刷新记录。</div></aside></div>
    <div data-rex-status role="status" aria-live="polite"></div><section data-rex-detail class="rex-detail"></section>`;
  page.append(panel);
  const form=panel.querySelector('form'),runsHost=panel.querySelector('[data-rex-runs]'),detail=panel.querySelector('[data-rex-detail]'),statusHost=panel.querySelector('[data-rex-status]');
  let requestSerial=0,selectedRun=null,pollTimer=null,activeProject=null;
  const drafts=new Map();
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const project=()=>typeof current!=='undefined'?current:document.getElementById('project')?.value;
  const url=(p,suffix='')=>'/api/projects/'+encodeURIComponent(p)+'/retrieval-experiments'+suffix;
  const note=(text,error=false)=>{statusHost.textContent=text;statusHost.className=error?'rex-error':'rex-message';};
  const score=value=>value===null||value===undefined?'未评分':(Number(value)*100).toFixed(1)+'%';
  function stopPolling(){clearTimeout(pollTimer);pollTimer=null;}
  function remember(){if(activeProject)drafts.set(activeProject,new FormData(form));}
  function restoreDraft(p){const values=drafts.get(p);if(!values){form.reset();return;}for(const input of form.elements){if(!input.name)continue;if(input.type==='checkbox')input.checked=values.getAll(input.name).includes(input.value);else if(values.has(input.name))input.value=values.get(input.name);}}
  async function listRuns(){
    const p=project();if(!p){runsHost.textContent='请先选择项目。';return;}
    const serial=requestSerial;
    const result=await api(url(p),undefined,'GET');
    if(p!==project()||serial!==requestSerial)return;
    runsHost.innerHTML=result.runs?.length?result.runs.map(run=>`<button type="button" class="rex-run" data-run="${escape(run.id)}"><b>${escape(run.name)}</b><span>${escape(STATES[run.status]||run.status)} · ${run.completed_items??0}/${run.total_items??'—'}</span><small>${escape(new Date(run.created_at).toLocaleString('zh-CN'))}</small></button>`).join(''):'尚无实验。创建后会保留固定输入、逐题证据和实际策略。';
    for(const button of runsHost.querySelectorAll('[data-run]'))button.onclick=()=>loadRun(button.dataset.run).catch(error=>note(error.message,true));
  }
  async function action(name){
    const p=project(),id=selectedRun;
    if(!p||!id)return;
    if(name==='export'){
      const result=await api(url(p,'/'+encodeURIComponent(id)+'/export'),undefined,'GET');
      if(p!==project()||id!==selectedRun)return;
      const link=document.createElement('a'),blobUrl=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
      link.href=blobUrl;link.download='retrieval-experiment-'+id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(blobUrl),1000);return;
    }
    await api(url(p,'/'+encodeURIComponent(id)+'/'+name),{});
    if(p===project()){await loadRun(id);await listRuns();}
  }
  function renderRun(run){
    const valid=run.status!=='invalidated',manifest=run.manifest;
    detail.innerHTML=`<div class="rex-result-heading"><div><h3>${escape(run.name)}</h3><span class="rex-state">${escape(STATES[run.status]||run.status)}</span> <span>${run.completed_items??0}/${run.total_items??'—'} 项</span></div><div class="rex-actions">${['queued','running'].includes(run.status)?'<button type="button" data-action="cancel" class="secondary">取消实验</button>':''}${['failed','cancelled'].includes(run.status)?'<button type="button" data-action="retry" class="secondary">重试未完成项</button>':''}${valid?'<button type="button" data-action="export" class="secondary">导出 JSON</button>':''}</div></div>${run.error?`<p class="rex-error">${escape(run.error)}</p>`:''}`;
    for(const button of detail.querySelectorAll('[data-action]'))button.onclick=()=>action(button.dataset.action).catch(error=>note(error.message,true));
    if(!valid)return;
    const summaries=Object.entries(run.summary||{});
    if(summaries.length){const table=document.createElement('div');table.className='rex-table-wrap';table.innerHTML=`<table class="rex-table"><thead><tr><th>实际变体</th><th>完成问题</th><th>依据召回</th><th>答案精确匹配</th><th>平均耗时</th></tr></thead><tbody>${summaries.map(([key,row])=>`<tr><td>${escape(VARIANTS[key]?.[0]||key)}</td><td>${row.completed_questions}</td><td>${score(row.mean_evidence_recall)}<small>评分 ${row.evidence_scored_questions} 题</small></td><td>${score(row.answer_exact_match)}<small>评分 ${row.answer_scored_questions} 题</small></td><td>${row.mean_elapsed_ms===null?'—':Math.round(row.mean_elapsed_ms)+' ms'}</td></tr>`).join('')}</tbody></table>`;detail.append(table);}
    for(const receipt of run.results||[]){
      const box=document.createElement('details');box.className='rex-receipt';
      const question=manifest?.questions?.find(q=>q.id===receipt.question_id);
      box.innerHTML=`<summary><span>${escape(VARIANTS[receipt.variant]?.[0]||receipt.variant)} · ${escape(question?.query||receipt.question_id)}</span><small>${receipt.status==='completed'?'已执行':'失败'} · ${Math.round(receipt.elapsed_ms)} ms</small></summary><p class="subtle">省略步骤：${escape(receipt.omitted_stages.join('、')||'无')}。治理资格始终核对。</p>${receipt.error?`<p class="rex-error">${escape(receipt.error)}</p>`:''}${receipt.generation_skip_reason?`<p class="subtle">模型：${receipt.generation_skip_reason==='model_unavailable'?'未配置，未调用模型，答案不评分':'未请求生成'}</p>`:''}`;
      const result=receipt.result;
      if(result){
        const answer=document.createElement('pre');answer.className='rex-answer';answer.textContent=result.answer||'未生成回答';box.append(answer);
        const strategy=document.createElement('p');strategy.className='subtle';strategy.textContent='实际模式：'+(result.active_modes||[result.requested_mode]).filter(Boolean).join(' / ')+'；后端：'+(result._backend||'见检索收据')+'；路径：'+(result.reasoning_paths?.length||0);box.append(strategy);
        const evidence=document.createElement('div');evidence.className='rex-evidence';
        for(const row of result.evidence||[]){
          const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=`[${row.citation||'依据'}] ${row.text?.slice(0,80)||row.id} · r${row.version||'?'}`;
          button.onclick=async()=>{try{const p=project();if(!p)return;if(result.answer_id&&row.citation){const trace=await api('/api/projects/'+encodeURIComponent(p)+'/answers/'+encodeURIComponent(result.answer_id)+'/evidence/'+encodeURIComponent(row.citation)+'/provenance',undefined,'GET');if(p!==project())return;const traceBox=document.createElement('pre');traceBox.className='rex-trace';traceBox.textContent=JSON.stringify(trace,null,2);box.append(traceBox);}else if(row.sources?.[0]&&window.openFrozenSourceEvidence)window.openFrozenSourceEvidence(row.sources[0]);}catch(error){note(error.message,true);}};
          evidence.append(button);
        }
        box.append(evidence);
      }
      detail.append(box);
    }
    if(manifest){const fixed=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='固定输入与运行配置';pre.className='rex-manifest';pre.textContent=JSON.stringify(manifest,null,2);fixed.append(summary,pre);detail.append(fixed);}
  }
  async function loadRun(id){
    stopPolling();const p=project();if(!p)return;
    const serial=++requestSerial;selectedRun=id;
    const run=await api(url(p,'/'+encodeURIComponent(id)),undefined,'GET');
    if(p!==project()||serial!==requestSerial)return;
    renderRun(run);
    if(['queued','running'].includes(run.status)&&!panel.classList.contains('hidden'))pollTimer=setTimeout(()=>loadRun(id).catch(error=>note(error.message,true)),2500);
  }
  form.onsubmit=async event=>{
    event.preventDefault();const p=project();if(!p){note('请先选择项目。',true);return;}
    const button=form.querySelector('[type="submit"]');button.disabled=true;
    try{
      const values=new FormData(form),questions=parseQuestions(values.get('questions'));
      let ownScope;try{ownScope=JSON.parse(values.get('scope')||'{}');}catch{throw Error('实验范围必须是有效 JSON。');}
      const variants=values.getAll('variants');if(!variants.length)throw Error('请至少选择一个比较变体。');
      note('正在固定输入并提交持久任务…');remember();
      const result=await api(url(p),{name:values.get('name')||'检索与问答消融',questions,variants,scope:ownScope,
        retrieval_mode:values.get('retrieval_mode'),k_entities:Number(values.get('k_entities')),k_chunks:Number(values.get('k_chunks')),hops:Number(values.get('hops')),generate:values.has('generate')});
      if(p!==project())return;
      note('实验已提交，可随时离开；运行记录会保留。');await loadRun(result.id);await listRuns();
    }catch(error){note(error.message,true);}finally{button.disabled=false;}
  };
  panel.querySelector('[data-rex-refresh]').onclick=()=>listRuns().catch(error=>note(error.message,true));
  for(const button of tabs.querySelectorAll('[data-rex-tab]'))button.onclick=()=>{
    const retrieval=button.dataset.rexTab==='retrieval';panel.classList.toggle('hidden',!retrieval);extraction?.classList.toggle('hidden',retrieval);
    for(const tab of tabs.querySelectorAll('[role="tab"]'))tab.setAttribute('aria-selected',String(tab===button));
    if(retrieval){activeProject=project();listRuns().catch(error=>note(error.message,true));if(selectedRun)loadRun(selectedRun).catch(error=>note(error.message,true));}else stopPolling();
  };
  document.getElementById('project')?.addEventListener('change',()=>{remember();activeProject=project();restoreDraft(activeProject);requestSerial++;stopPolling();selectedRun=null;detail.replaceChildren();runsHost.textContent='项目已切换，请刷新运行记录。';note('');});
  window.RetrievalExperimentsView={refresh:listRuns,open(){tabs.querySelector('[data-rex-tab="retrieval"]').click();}};
})();
