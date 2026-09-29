/* Project-scoped task summaries and explicit ontology relation review. */
(() => {
  const states={queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断'};
  const kinds={ingest:'文档解析',neo4j_sync:'Neo4j 同步',legacy_import:'旧项目导入',ontology_publish:'本体发布',semantic_index:'语义索引'};
  const beijingTime=value=>{
    if(!value)return '—';
    const date=new Date(value);
    if(Number.isNaN(date.getTime()))return String(value);
    const parts=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).formatToParts(date);
    const part=type=>parts.find(item=>item.type===type)?.value||'';
    return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`;
  };
  const displayLog=line=>String(line).replace(/\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\]/g,(_,value)=>`[${beijingTime(value)}]`);
  const safeTerm=type=>typeof term==='function'?term(type):String(type||'').split(/[\/#]/).pop();
  const safeLabel=type=>typeof labelOf==='function'?labelOf(type):safeTerm(type);
  const ontoName=t=>t.label_zh||((t.label&&t.label!==t.name)?t.label:'')||t.description||t.name||'';
  const typeHint=type=>{const primary=safeLabel(type),local=safeTerm(type);return primary===local?esc(primary):`${esc(primary)} <span class="subtle">${esc(local)}</span>`;};
  const panel=document.createElement('div');panel.className='panel';
  panel.innerHTML='<div class="row"><h2>知识审核</h2><button id="refresh-reviews" class="secondary">刷新审核</button><button id="review-ontology" class="secondary">本体工作台 ↗</button></div><p class="subtle">这里审核已有正式本体下的具体事实：批准后写入正式图谱。开放候选与证据异常在本体工作台处理；本体异常在单独队列确认例外或标记整改。</p><nav class="review-view-tabs" aria-label="知识审核视图"><button id="review-view-facts" type="button" aria-pressed="true">待审核事实</button><button id="review-view-exceptions" type="button" class="secondary" aria-pressed="false">约束异常</button><button id="review-view-history" type="button" class="secondary" aria-pressed="false">已处理</button></nav><label id="review-kind-wrap">事实类型<select id="review-kind"><option value="">全部事实</option><option value="entity">实体映射</option><option value="relation">关系事实</option><option value="attribute">属性事实</option></select></label><div id="relation-reviews"></div>';
  const reviewPage=document.createElement('section');reviewPage.id='tab-reviews';reviewPage.className='tab hidden';reviewPage.append(panel);
  document.querySelector('main').append(reviewPage);
  const reviewNav=document.createElement('button');reviewNav.dataset.tab='reviews';reviewNav.textContent='知识审核';
  document.querySelector('[data-tab="ontology-workbench"]').after(reviewNav);
  reviewNav.onclick=()=>{
    document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));reviewPage.classList.remove('hidden');
    document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===reviewNav));
    $('title').textContent='知识审核';$('scope').classList.add('hidden');reviews();
  };
  $('review-ontology').onclick=()=>window.OntologyWorkbench?.open('discover');
  $('review-kind').onchange=()=>reviews();
  const attributeOption=document.createElement('label');attributeOption.className='check';
  attributeOption.innerHTML='<input type="checkbox" id="parse-attributes">抽取实体业务属性（选填，每片额外调用一次 LLM，属性值全部待审核）';
  const constraintOption=document.createElement('label');
  constraintOption.innerHTML='关系两端约束<select id="parse-relation-constraints"><option value="review" selected>审核 · 冲突进入候选（推荐）</option><option value="advisory">提示 · 允许入图并记录警告</option><option value="strict">严格 · 任一冲突整份失败</option><option value="off">关闭 · 不检查类型组合</option></select><small>domain/range 始终用于引导模型；此项决定抽取后的处理方式。结构化 API 写入仍默认严格。</small>';
  document.querySelector('.parse-settings').append(constraintOption);
  document.querySelector('.parse-settings').append(attributeOption);
  $('extraction-mode').onchange?.();
  const parseSettings=readParseSettings;
  readParseSettings=()=>({...parseSettings(),relation_constraint_mode:$('parse-relation-constraints').value,
    ...($('extraction-mode').value!=='documents'&&$('parse-attributes').checked?{extract_attributes:true}:{})});
  let reviewView='facts';
  let generation=0;
  async function reviews(){
    const p=current,serial=++generation,host=$('relation-reviews');
    if(!p){host.textContent='请先选择项目';return;}
    host.textContent='正在读取审核清单…';
    try{
      const [data,ontology,health]=await Promise.all([api('/api/projects/'+encodeURIComponent(p)+'/reviews',undefined,'GET'),api('/api/projects/'+encodeURIComponent(p)+'/ontology',undefined,'GET').catch(()=>null),api('/api/health',undefined,'GET')]);
      if(p!==current||serial!==generation)return;
      if(!health.capabilities?.includes('typed_reviews')){host.textContent='当前仍是旧版后端。请先重启 8100 服务，再刷新本页，启用实体、关系和属性审核。';return;}
      const filter=$('review-kind').value,all=data.reviews||[];
      const isPending=row=>['pending','contradicting'].includes(row.status);
      const factQueue=all.filter(row=>isPending(row)&&row.kind!=='validation');
      const exceptionQueue=all.filter(row=>isPending(row)&&row.kind==='validation');
      const done=all.filter(row=>!isPending(row));
      const items=(reviewView==='facts'?factQueue:reviewView==='exceptions'?exceptionQueue:done)
        .filter(row=>reviewView!=='facts'||!filter||(row.kind||'relation')===filter);
      const pending=reviewView==='history'?[]:items;
      const labels={validation:'本体异常',entity:'实体映射',relation:'关系事实',attribute:'属性事实'};
      $('review-view-facts').textContent=`待审核事实 ${factQueue.length}`;$('review-view-exceptions').textContent=`约束异常 ${exceptionQueue.length}`;$('review-view-history').textContent=`已处理 ${done.length}`;$('review-kind-wrap').hidden=reviewView!=='facts';
      if(!ontology){host.innerHTML='<p class="subtle">本项目尚未发布本体。可先持续开放发现并累计候选，到「本体工作台」生成、审核和发布本体版本；发布后再用该本体受控重解析，正式实体与关系才会进入图谱。</p><button id="reviews-goto-discovery">前往本体工作台 ↗</button>';host.querySelector('#reviews-goto-discovery').onclick=()=>window.OntologyWorkbench?.open('discover');return;}
      if(reviewView==='history'){
        host.innerHTML=`<p>已处理 ${done.length} 条 · 当前本体 ${esc(ontology.id)}</p>`+(done.length?done.map(r=>`<p>${esc(labels[r.kind||'relation'])} · ${esc(r.document_title)} · ${esc(r.path_label||safeLabel(r.proposed_type)||safeLabel(r.predicate)||'')} · ${r.kind==='validation'?(r.resolution==='accepted_exception'?'已确认例外':'需整改'):(r.status==='approved'?'已批准':'已拒绝')} · ${esc(r.target_type||'')} · ${esc(r.note)} · ${esc(r.reviewed_at)}</p>`).join(''):'<p class="subtle">暂无已处理记录。</p>');return;
      }
      host.innerHTML=`<p>${reviewView==='facts'?'待审核事实':'待处理约束异常'} ${pending.length} 条 · 当前本体 ${esc(ontology.id)}</p>`+pending.map((r,i)=>{
        const kind=r.kind||'relation',terms=ontology.summary[{entity:'classes',relation:'relations',attribute:'attributes'}[kind]]||[];
        const heading=kind==='validation'?`${esc(r.text||r.record_id||'图谱记录')} · ${esc(r.path_label||safeLabel(r.path)||'图级约束')}`:kind==='entity'?`${esc(r.text)} · ${typeHint(r.proposed_type)}`:kind==='attribute'?`${esc(r.subject)} · ${typeHint(r.proposed_type)} = ${esc(JSON.stringify(r.value))}`:`${esc(r.subject)} → ${typeHint(r.predicate)} → ${esc(r.object)}`;
        const issues=(r.constraint_issues||[]).map(x=>`${x.endpoint==='subject_id'?'主语':'宾语'}：实际 ${safeLabel(x.actual_type)}，期望 ${x.expected_types.map(safeLabel).join(' / ')}`).join('；');
        const proposed=r.proposed_type||r.predicate||'';
        const changeKind={entity:'class',relation:'relation',attribute:'attribute'}[kind],domainType=r.entity_type||r.subject_type||'',rangeType=r.object_type||'';
        const optionsFor=selected=>(ontology.summary.classes||[]).map(x=>`<option value="${esc(x.id)}" ${x.id===selected?'selected':''}>${esc(x.label)}</option>`).join('');
        const classOptions=optionsFor('');
        const changeState=r.ontology_change?`<p class="ontology-revalidation">本体草案已批准 · ${esc(r.ontology_change.status)} · ${esc(r.ontology_change.issues?.join('；')||'可继续映射审核')}</p>`:'';
        const validationDetails=kind==='validation'?`<div class="constraint-exception"><b>限制：${esc(r.constraint||'SHACLConstraint')}</b><span>字段 / 关系：${esc(r.path_label||r.path||'图级约束')}</span><span>级别：${esc(r.severity||'Violation')}</span><span>实际值：${esc(r.actual_value??'缺失')}</span><p>${esc(r.message)}</p></div>`:'';
        const mapping=kind==='validation'?'':r.status==='contradicting'?`<p class="subtle">冲突属性已锁定：${esc(safeLabel(r.conflict_predicate||r.target_type))}</p>`:`<label>映射到当前本体${labels[kind]}<select class="review-target"><option value="">请选择，不自动匹配</option>${terms.map(t=>`<option value="${esc(t.id)}" ${t.id===r.target_type||t.id===r.proposed_type||t.id===r.ontology_change?.target_type?'selected':''}>${esc(ontoName(t))} · ${esc(t.name)}</option>`).join('')}</select></label>`;
        const actions=kind==='validation'?'<button data-action="approve">确认例外</button><button data-action="reject" class="secondary">标记需整改</button>':r.status==='contradicting'&&r.conflict?.code==='attribute_max_count_one'?'<button data-action="reject" class="secondary">保留旧值/拒绝候选</button><button data-action="approve_replace">接受新值</button>':`<button data-action="approve" ${r.blocked?'disabled':''}>批准并写入</button><button data-action="reject" class="secondary">拒绝</button><button data-action="propose" class="secondary">申请本体变更</button>`;
        const existingOptions=terms.map(t=>`<option value="${esc(t.id)}" ${t.id===r.proposed_type||t.id===r.ontology_change?.target_type?'selected':''}>${esc(ontoName(t))} · ${esc(t.name)}</option>`).join('');
        const changeEditor=kind==='validation'?'':`<details class="ontology-change-editor"><summary>本体变更草案</summary><div class="change-fields"><label>操作<select class="change-operation"><option value="add">新增定义</option><option value="update">调整现有定义</option></select></label><label class="change-existing-label" hidden>要调整的现有术语<select class="change-existing"><option value="">请选择现有术语</option>${existingOptions}</select></label><label>技术标识 IRI<input class="change-uri-preview" value="保存时由后端根据名称生成" readonly></label><label>显示名称<input class="change-label" value="${esc(proposed)}"></label><label>中文名称<input class="change-label-zh" maxlength="200"></label><label>定义说明<input class="change-description" placeholder="该术语表达什么"></label>${kind==='entity'?`<label>父类<select class="change-parent"><option value="">不指定</option>${classOptions}</select></label>`:`<label>定义域<select class="change-domain"><option value="">不指定</option>${optionsFor(domainType)}</select></label><label>值域<select class="change-range"><option value="">不指定</option>${kind==='attribute'?'<option value="http://www.w3.org/2001/XMLSchema#string">字符串</option><option value="http://www.w3.org/2001/XMLSchema#integer">整数</option><option value="http://www.w3.org/2001/XMLSchema#decimal">小数</option><option value="http://www.w3.org/2001/XMLSchema#boolean">布尔值</option>':optionsFor(rangeType)}</select></label>`}<label class="change-rationale">变更理由<input maxlength="2000" placeholder="为什么现有本体无法表达这条知识"></label><button data-submit-proposal>提交草案</button></div></details>`;
        return `<article class="review-item ${kind==='validation'?'validation-review':''}" data-review="${i}"><small>${labels[kind]}</small><h3>${heading}</h3><p>${esc(r.document_title)} · ${esc(r.reason)}</p>${validationDetails}${issues?`<p class="constraint-conflict">${esc(issues)}</p>`:''}${r.conflict?.code==='attribute_max_count_one'?`<p class="constraint-conflict">该属性为单值字段，候选值与当前正式属性冲突。${r.status==='contradicting'?'请选择保留旧值或接受新值。':'普通批准只会登记冲突，不会覆盖或写入候选值。'}</p>`:''}${changeState}<small>抽取本体 ${esc(r.ontology_id)} · 原文字符 ${r.start_char}–${r.end_char}</small>${r.blocked?'<p class="error">关联实体尚未入图或已删除，请先审核实体；拒绝实体不会自动拒绝这些依赖候选。</p>':''}<details><summary>查看原文证据</summary><pre>${esc(r.attribute_evidence||r.evidence)}</pre></details>${kind==='attribute'?`<details><summary>当前正式属性值</summary><pre>${esc(JSON.stringify(r.current_values||[],null,2))}</pre></details>`:''}${mapping}<label>审核理由（必填）<input class="review-note" maxlength="2000" placeholder="填写确认例外、整改或知识审核理由"></label><div class="review-actions">${actions}</div>${changeEditor}</article>`;
      }).join('');
      if(!pending.length)host.insertAdjacentHTML('beforeend','<p class="subtle">当前视图没有待处理事项。</p>');
      host.querySelectorAll('[data-review]').forEach(article=>{
        const r=pending[Number(article.dataset.review)];
        const operation=article.querySelector('.change-operation'),existingLabel=article.querySelector('.change-existing-label');
        if(operation&&existingLabel){
          const existing=article.querySelector('.change-existing'),preview=article.querySelector('.change-uri-preview');
          const syncChangeTarget=()=>{const updating=operation.value==='update';existingLabel.hidden=!updating;preview.value=updating?(existing.value||'请选择现有术语'):'保存时由后端根据名称生成';};
          operation.onchange=syncChangeTarget;existing.onchange=syncChangeTarget;syncChangeTarget();
        }
        if(r.source_changed){
          article.querySelector('h3').insertAdjacentHTML('afterend','<p class="error">原文已变更。下方保留抽取时片段，不能直接批准，请重新提取。</p>');
          const approve=article.querySelector('[data-action="approve"], [data-action="approve_replace"]');if(approve)approve.disabled=true;
        }
      });
      host.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{
        const article=button.closest('[data-review]'),r=pending[Number(article.dataset.review)];
        if(button.dataset.action==='propose'){
          const editor=article.querySelector('.ontology-change-editor');editor.open=true;editor.scrollIntoView?.({behavior:'smooth',block:'nearest'});return;
        }
        const note=article.querySelector('.review-note').value.trim(),target=article.querySelector('.review-target')?.value||(r.status==='contradicting'?(r.conflict_predicate||r.target_type):'');
        if(!note){status('请填写审核理由',true);return;}
        if(['approve','approve_replace'].includes(button.dataset.action)&&r.kind!=='validation'&&!target){status('请选择要映射的本体定义',true);return;}
        if(p!==current)return;
        article.querySelectorAll('button').forEach(b=>b.disabled=true);
        try{
          await api('/api/projects/'+encodeURIComponent(p)+'/reviews/'+encodeURIComponent(r.document_id)+'/'+encodeURIComponent(r.id),{action:button.dataset.action,target_type:target,note,expected_version:r.document_version,expected_ontology_id:ontology.id,expected_entity_version:r.entity_version||null,expected_attribute_versions:Object.fromEntries((r.conflict?.current_values||[]).map(value=>[value.record_id,value.version]))});
          if(p===current){status(r.kind==='validation'?(button.dataset.action==='approve'?'已确认本体例外；知识保持不变。':'已标记需整改；知识保持不变。'):(button.dataset.action==='approve_replace'?'已接受新值并取代旧属性。':button.dataset.action==='approve'?'知识已写入；重新检索可查看，Neo4j 需重新同步。':'已拒绝，原图未改动。'));await reviews();}
        }catch(error){status(error.message,true);article.querySelectorAll('button').forEach(b=>b.disabled=['approve','approve_replace'].includes(b.dataset.action)&&(r.blocked||r.source_changed));}
      });
      host.querySelectorAll('[data-submit-proposal]').forEach(button=>button.onclick=async()=>{
        const article=button.closest('[data-review]'),r=pending[Number(article.dataset.review)],editor=button.closest('.ontology-change-editor');
        const kind={entity:'class',relation:'relation',attribute:'attribute'}[r.kind||'relation'];
        const operation=editor.querySelector('.change-operation').value;
        const body={document_id:r.document_id,candidate_id:r.id,operation,
          kind,uri:operation==='update'?editor.querySelector('.change-existing').value:'',label:editor.querySelector('.change-label').value.trim(),label_zh:editor.querySelector('.change-label-zh').value.trim(),description:editor.querySelector('.change-description').value.trim(),
          rationale:editor.querySelector('.change-rationale input').value.trim(),expected_ontology_id:ontology.id,
          expected_document_version:r.document_version,parent:editor.querySelector('.change-parent')?.value||'',
          domain:editor.querySelector('.change-domain')?.value||'',range:editor.querySelector('.change-range')?.value||''};
        if((operation==='update'&&!body.uri)||!body.label||!body.rationale){status(operation==='update'?'请选择要调整的现有术语，并填写显示名称和变更理由':'请填写显示名称和变更理由',true);return;}
        button.disabled=true;
        try{await api('/api/projects/'+encodeURIComponent(p)+'/ontology-change-proposals',body);status('本体变更申请已提交；请到本体工作台审批并生成新版本。');await reviews();}
        catch(error){status(error.message,true);button.disabled=false;}
      });
    }catch(error){if(p===current&&serial===generation)host.textContent='审核清单读取失败：'+error.message;}
  }
  $('refresh-reviews').onclick=reviews;
  function setReviewView(view){reviewView=view;[['facts','review-view-facts'],['exceptions','review-view-exceptions'],['history','review-view-history']].forEach(([value,id])=>$(id).setAttribute?.('aria-pressed',String(value===view)));reviews();}
  $('review-view-facts').onclick=()=>setReviewView('facts');
  $('review-view-exceptions').onclick=()=>setReviewView('exceptions');
  $('review-view-history').onclick=()=>setReviewView('history');
  async function refresh(){await jobList();}
  document.querySelector('[data-tab="jobs"]').addEventListener('click',()=>refresh().catch(e=>status(e.message,true)));
  const previousProjectChange=$('project').onchange;
  $('project').onchange=function(...args){previousProjectChange?.apply(this,args);generation++;if(!reviewPage.classList.contains('hidden'))reviews();if(!$('tab-jobs').classList.contains('hidden'))refresh().catch(e=>status(e.message,true));};
  let jobsGeneration=0;
  jobList=async()=>{
    const serial=++jobsGeneration,p=current;
    const [result,scoped]=await Promise.all([api('/api/jobs',undefined,'GET'),p?api('/api/projects/'+encodeURIComponent(p)+'/jobs',undefined,'GET'):Promise.resolve({jobs:[]})]);
    if(serial!==jobsGeneration||p!==current)return result.jobs;
    const open=new Set([...$('tasks').querySelectorAll('details[open]')].map(d=>d.dataset.key));
    // Preserve each open log panel's scroll position across the full re-render; follow the
    // newest line only when the reader was already at the bottom.
    const scrollState={};
    $('tasks').querySelectorAll('details[data-key]').forEach(d=>{const pre=d.querySelector('pre');if(pre)scrollState[d.dataset.key]={top:pre.scrollTop,atBottom:pre.scrollHeight-pre.scrollTop-pre.clientHeight<8};});
    const jobs=scoped.jobs;
    $('tasks').innerHTML=jobs.map(j=>{
      const logs=j.logs||[],last={};
      for(const line of logs){
        if(/仍在等待|处理失败/.test(line))continue;
        const group=/实体抽取/.test(line)?'实体抽取':/关系抽取/.test(line)?'关系抽取':/待审核|等待审核/.test(line)?'关系审核':/LLM|抽取模块/.test(line)?'模型配置':/片段 \d/.test(line)?'切片处理':/融合|消歧/.test(line)?'消歧融合':/校验/.test(line)?'本体校验':/向量化/.test(line)?'向量化':/落库|提交/.test(line)?'保存':/切片/.test(line)?'切片准备':'任务';
        last[group]=line;
      }
      const title=j.result?.document?.metadata?.title||logs.find(l=>l.includes('文档开始 · '))?.split('文档开始 · ')[1]?.split(' · ')[0]||kinds[j.kind]||j.kind;
      const key=j.id+'-raw';
      return `<article class="card task-summary"><div class="row"><h3>${esc(title)}</h3><b>${esc(states[j.status]||j.status)}</b></div><small>${esc(kinds[j.kind]||j.kind)} · ${esc(j.id)} · ${esc(beijingTime(j.created_at))}（北京时间） · 已运行 ${j.elapsed_seconds??'—'} 秒</small><p>${esc(j.failed_stage||j.stage||'等待前序任务完成')}</p>${j.error?`<p class="error">${esc(j.error)}</p>`:''}${j.result?.pending_reviews?`<p>本次产生 ${j.result.pending_reviews} 条知识候选。<button data-open-review class="secondary">前往知识审核 ↗</button></p>`:''}<progress max="100" value="${j.progress||0}"></progress><small>阶段进度 ${j.progress||0}%（非模型处理比例）</small><ul>${Object.entries(last).map(([group,line])=>`<li><b>${group}</b> · ${esc(displayLog(line))}</li>`).join('')}</ul>${j.heartbeat_at?`<small>最近心跳 ${esc(beijingTime(j.heartbeat_at))}（北京时间），仅表示线程仍在等待，不代表实际推进。</small>`:''}<details data-key="${key}" ${open.has(key)?'open':''}><summary>完整日志与执行位置（${logs.length} 条，含重复等待）</summary><pre>${esc(logs.map(displayLog).join('\n'))}${j.worker_stack?'\n\n线程位置\n'+esc(j.worker_stack.join('\n')):''}${j.failure_stack?'\n\n失败位置\n'+esc(j.failure_stack.join('\n')):''}</pre></details></article>`;
    }).join('')||'<p>当前项目暂无任务。</p>';
    $('tasks').querySelectorAll('details[data-key]').forEach(d=>{const pre=d.querySelector('pre'),state=scrollState[d.dataset.key];if(pre&&state)pre.scrollTop=state.atBottom?pre.scrollHeight:state.top;});
    $('tasks').querySelectorAll('[data-open-review]').forEach(button=>button.onclick=()=>reviewNav.click());
    return result.jobs;
  };
  bind('load-jobs',refresh);
})();
