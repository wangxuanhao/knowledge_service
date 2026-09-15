/* Project-scoped task summaries and explicit ontology relation review. */
(() => {
  const states={queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断'};
  const kinds={ingest:'文档解析',neo4j_sync:'Neo4j 同步',legacy_import:'旧项目导入'};
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
  panel.innerHTML='<div class="row"><h2>知识审核</h2><button id="refresh-reviews" class="secondary">刷新审核</button><button id="review-ontology" class="secondary">管理本体定义 ↗</button></div><p class="subtle">实体、关系和属性候选在批准前不会入图；本体异常不会阻塞整份文档，知识先保存并在这里说明具体限制，可确认例外或标记整改。</p><section class="ontology-change-queue"><h3>本体变更草案</h3><div id="ontology-change-proposals"></div></section><label>候选类型<select id="review-kind"><option value="">全部类型</option><option value="validation">本体异常</option><option value="entity">实体类型</option><option value="relation">关系</option><option value="attribute">实体属性</option></select></label><div id="relation-reviews"></div>';
  const reviewPage=document.createElement('section');reviewPage.id='tab-reviews';reviewPage.className='tab hidden';reviewPage.append(panel);
  document.querySelector('main').append(reviewPage);
  const reviewNav=document.createElement('button');reviewNav.dataset.tab='reviews';reviewNav.textContent='知识审核';
  document.querySelector('[data-tab="ontology"]').after(reviewNav);
  reviewNav.onclick=()=>{
    document.querySelectorAll('.tab').forEach(t=>t.classList.add('hidden'));reviewPage.classList.remove('hidden');
    document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===reviewNav));
    $('title').textContent='知识审核';$('scope').classList.add('hidden');reviews();
  };
  $('review-ontology').onclick=()=>showTab('ontology');
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
  let generation=0;
  async function reviews(){
    const p=current,serial=++generation,host=$('relation-reviews');
    if(!p){host.textContent='请先选择项目';return;}
    host.textContent='正在读取审核清单…';
    try{
      const [data,ontology,health,changeData]=await Promise.all([api('/api/projects/'+encodeURIComponent(p)+'/reviews',undefined,'GET'),api('/api/projects/'+encodeURIComponent(p)+'/ontology',undefined,'GET').catch(()=>null),api('/api/health',undefined,'GET'),api('/api/projects/'+encodeURIComponent(p)+'/ontology-change-proposals',undefined,'GET')]);
      if(p!==current||serial!==generation)return;
      if(!health.capabilities?.includes('typed_reviews')){host.textContent='当前仍是旧版后端。请先重启 8100 服务，再刷新本页，启用实体、关系和属性审核。';return;}
      const filter=$('review-kind').value,items=data.reviews.filter(r=>!filter||(r.kind||'relation')===filter);
      const pending=items.filter(r=>r.status==='pending'),done=items.filter(r=>r.status!=='pending');
      const labels={validation:'本体异常',entity:'实体类型',relation:'关系',attribute:'实体属性'};
      const changes=changeData.proposals||[],pendingChanges=changes.filter(x=>x.status==='pending');
      $('ontology-change-proposals').innerHTML=pendingChanges.length?pendingChanges.map((draft,i)=>`<article class="ontology-change-card" data-change="${i}"><div><small>${draft.operation==='add'?'新增':'调整'} · ${{class:'实体类',relation:'关系',attribute:'属性'}[draft.kind]}</small><h4>${esc(draft.label)}</h4><code>${esc(draft.uri)}</code></div><div class="change-impact"><b>${draft.impact.risk==='high'?'高影响变更':'受控变更'}</b><span>现有知识 ${draft.impact.record_count} · 约束引用 ${draft.impact.constraint_count} · 关联候选 ${draft.impact.linked_candidates}</span></div><label>审批意见<input class="change-note" maxlength="2000" placeholder="说明批准或拒绝原因"></label><div>${draft.impact.risk==='high'?'<label class="check"><input class="change-confirm-impact" type="checkbox">确认影响</label>':''}<button data-change-action="approve">批准本体版本</button><button data-change-action="reject" class="secondary">拒绝</button></div></article>`).join(''):`<p class="subtle">暂无待批准草案 · 已处理 ${changes.length} 条</p>`;
      if(!ontology){host.innerHTML='<p class="subtle">本项目尚未发布本体。可先持续开放发现并累计候选，到「本体发现」生成、审核和发布本体版本；发布后再用该本体受控重解析，正式实体与关系才会进入图谱。</p><button id="reviews-goto-discovery">前往本体发现 ↗</button>';host.querySelector('#reviews-goto-discovery').onclick=()=>showTab('discovery');return;}
      host.innerHTML=`<p>待审核 ${pending.length} 条 · 已处理 ${done.length} 条 · 当前本体 ${esc(ontology.id)}</p>`+pending.map((r,i)=>{
        const kind=r.kind||'relation',terms=ontology.summary[{entity:'classes',relation:'relations',attribute:'attributes'}[kind]]||[];
        const heading=kind==='validation'?`${esc(r.text||r.record_id||'图谱记录')} · ${esc(r.path_label||safeLabel(r.path)||'图级约束')}`:kind==='entity'?`${esc(r.text)} · ${typeHint(r.proposed_type)}`:kind==='attribute'?`${esc(r.subject)} · ${typeHint(r.proposed_type)} = ${esc(JSON.stringify(r.value))}`:`${esc(r.subject)} → ${typeHint(r.predicate)} → ${esc(r.object)}`;
        const issues=(r.constraint_issues||[]).map(x=>`${x.endpoint==='subject_id'?'主语':'宾语'}：实际 ${safeLabel(x.actual_type)}，期望 ${x.expected_types.map(safeLabel).join(' / ')}`).join('；');
        const proposed=r.proposed_type||r.predicate||'',base=(ontology.summary.classes?.[0]?.id||'urn:knowledge:Term').replace(/[^\/#]+$/,'');
        const changeKind={entity:'class',relation:'relation',attribute:'attribute'}[kind],domainType=r.entity_type||r.subject_type||'',rangeType=r.object_type||'';
        const optionsFor=selected=>(ontology.summary.classes||[]).map(x=>`<option value="${esc(x.id)}" ${x.id===selected?'selected':''}>${esc(x.label)}</option>`).join('');
        const classOptions=optionsFor('');
        const changeState=r.ontology_change?`<p class="ontology-revalidation">本体草案已批准 · ${esc(r.ontology_change.status)} · ${esc(r.ontology_change.issues?.join('；')||'可继续映射审核')}</p>`:'';
        const validationDetails=kind==='validation'?`<div class="constraint-exception"><b>限制：${esc(r.constraint||'SHACLConstraint')}</b><span>字段 / 关系：${esc(r.path_label||r.path||'图级约束')}</span><span>级别：${esc(r.severity||'Violation')}</span><span>实际值：${esc(r.actual_value??'缺失')}</span><p>${esc(r.message)}</p></div>`:'';
        const mapping=kind==='validation'?'':`<label>映射到当前本体${labels[kind]}<select class="review-target"><option value="">请选择，不自动匹配</option>${terms.map(t=>`<option value="${esc(t.id)}" ${t.id===r.proposed_type||t.id===r.ontology_change?.target_type?'selected':''}>${esc(ontoName(t))} · ${esc(t.name)}</option>`).join('')}</select></label>`;
        const actions=kind==='validation'?'<button data-action="approve">确认例外</button><button data-action="reject" class="secondary">标记需整改</button>':`<button data-action="approve" ${r.blocked?'disabled':''}>批准并写入</button><button data-action="reject" class="secondary">拒绝</button><button data-action="propose" class="secondary">申请本体变更</button>`;
        const changeEditor=kind==='validation'?'':`<details class="ontology-change-editor"><summary>本体变更草案</summary><div class="change-fields"><label>操作<select class="change-operation"><option value="add">新增定义</option><option value="update">调整现有定义</option></select></label><label>完整 IRI<input class="change-uri" value="${esc(base+proposed)}"></label><label>显示名称<input class="change-label" value="${esc(proposed)}"></label><label>中文名称<input class="change-label-zh" maxlength="200"></label><label>定义说明<input class="change-description" placeholder="该术语表达什么"></label>${kind==='entity'?`<label>父类<select class="change-parent"><option value="">不指定</option>${classOptions}</select></label>`:`<label>定义域<select class="change-domain"><option value="">不指定</option>${optionsFor(domainType)}</select></label><label>值域<select class="change-range"><option value="">不指定</option>${kind==='attribute'?'<option value="http://www.w3.org/2001/XMLSchema#string">字符串</option><option value="http://www.w3.org/2001/XMLSchema#integer">整数</option><option value="http://www.w3.org/2001/XMLSchema#decimal">小数</option><option value="http://www.w3.org/2001/XMLSchema#boolean">布尔值</option>':optionsFor(rangeType)}</select></label>`}<label class="change-rationale">变更理由<input maxlength="2000" placeholder="为什么现有本体无法表达这条知识"></label><button data-submit-proposal>提交草案</button></div></details>`;
        return `<article class="review-item ${kind==='validation'?'validation-review':''}" data-review="${i}"><small>${labels[kind]}</small><h3>${heading}</h3><p>${esc(r.document_title)} · ${esc(r.reason)}</p>${validationDetails}${issues?`<p class="constraint-conflict">${esc(issues)}</p>`:''}${changeState}<small>抽取本体 ${esc(r.ontology_id)} · 原文字符 ${r.start_char}–${r.end_char}</small>${r.blocked?'<p class="error">关联实体尚未入图或已删除，请先审核实体；拒绝实体不会自动拒绝这些依赖候选。</p>':''}<details><summary>查看原文证据</summary><pre>${esc(r.attribute_evidence||r.evidence)}</pre></details>${kind==='attribute'?`<details><summary>实体当前属性（v${esc(r.entity_version||'—')}）</summary><pre>${esc(JSON.stringify(r.current_properties||{},null,2))}</pre></details>`:''}${mapping}<label>审核理由（必填）<input class="review-note" maxlength="2000" placeholder="填写确认例外、整改或知识审核理由"></label><div class="review-actions">${actions}</div>${changeEditor}</article>`;
      }).join('')+(done.length?`<details><summary>查看已审核记录</summary>${done.map(r=>`<p>${esc(labels[r.kind||'relation'])} · ${esc(r.document_title)} · ${esc(r.path_label||safeLabel(r.proposed_type)||safeLabel(r.predicate)||'')} · ${r.kind==='validation'?(r.resolution==='accepted_exception'?'已确认例外':'需整改'):(r.status==='approved'?'已批准':'已拒绝')} · ${esc(r.target_type||'')} · ${esc(r.note)} · ${esc(r.reviewed_at)}</p>`).join('')}</details>`:'');
      host.querySelectorAll('[data-review]').forEach(article=>{
        const r=pending[Number(article.dataset.review)];
        if(r.source_changed){
          article.querySelector('h3').insertAdjacentHTML('afterend','<p class="error">原文已变更。下方保留抽取时片段，不能直接批准，请重新提取。</p>');
          article.querySelector('[data-action="approve"]').disabled=true;
        }
      });
      host.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{
        const article=button.closest('[data-review]'),r=pending[Number(article.dataset.review)];
        if(button.dataset.action==='propose'){
          const editor=article.querySelector('.ontology-change-editor');editor.open=true;editor.scrollIntoView?.({behavior:'smooth',block:'nearest'});return;
        }
        const note=article.querySelector('.review-note').value.trim(),target=article.querySelector('.review-target')?.value||'';
        if(!note){status('请填写审核理由',true);return;}
        if(button.dataset.action==='approve'&&r.kind!=='validation'&&!target){status('请选择要映射的本体定义',true);return;}
        if(p!==current)return;
        article.querySelectorAll('button').forEach(b=>b.disabled=true);
        try{
          await api('/api/projects/'+encodeURIComponent(p)+'/reviews/'+encodeURIComponent(r.document_id)+'/'+encodeURIComponent(r.id),{action:button.dataset.action,target_type:target,note,expected_version:r.document_version,expected_ontology_id:ontology.id,expected_entity_version:r.entity_version||null});
          if(p===current){status(r.kind==='validation'?(button.dataset.action==='approve'?'已确认本体例外；知识保持不变。':'已标记需整改；知识保持不变。'):(button.dataset.action==='approve'?'知识已写入；重新检索可查看，Neo4j 需重新同步。':'已拒绝，原图未改动。'));await reviews();}
        }catch(error){status(error.message,true);article.querySelectorAll('button').forEach(b=>b.disabled=b.dataset.action==='approve'&&(r.blocked||r.source_changed));}
      });
      host.querySelectorAll('[data-submit-proposal]').forEach(button=>button.onclick=async()=>{
        const article=button.closest('[data-review]'),r=pending[Number(article.dataset.review)],editor=button.closest('.ontology-change-editor');
        const kind={entity:'class',relation:'relation',attribute:'attribute'}[r.kind||'relation'];
        const body={document_id:r.document_id,candidate_id:r.id,operation:editor.querySelector('.change-operation').value,
          kind,uri:editor.querySelector('.change-uri').value.trim(),label:editor.querySelector('.change-label').value.trim(),label_zh:editor.querySelector('.change-label-zh').value.trim(),description:editor.querySelector('.change-description').value.trim(),
          rationale:editor.querySelector('.change-rationale input').value.trim(),expected_ontology_id:ontology.id,
          expected_document_version:r.document_version,parent:editor.querySelector('.change-parent')?.value||'',
          domain:editor.querySelector('.change-domain')?.value||'',range:editor.querySelector('.change-range')?.value||''};
        if(!body.uri||!body.label||!body.rationale){status('本体 IRI、显示名称和变更理由均为必填',true);return;}
        button.disabled=true;
        try{await api('/api/projects/'+encodeURIComponent(p)+'/ontology-change-proposals',body);status('本体变更草案已提交，需单独批准后才会生成新本体版本。');await reviews();}
        catch(error){status(error.message,true);button.disabled=false;}
      });
      $('ontology-change-proposals').querySelectorAll('[data-change-action]').forEach(button=>button.onclick=async()=>{
        const card=button.closest('[data-change]'),draft=pendingChanges[Number(card.dataset.change)],note=card.querySelector('.change-note').value.trim();
        if(!note){status('请填写本体变更审批意见',true);return;}
        card.querySelectorAll('button').forEach(x=>x.disabled=true);
        try{await api('/api/projects/'+encodeURIComponent(p)+'/ontology-change-proposals/'+encodeURIComponent(draft.id)+'/decision',{
          action:button.dataset.changeAction,note,expected_revision:draft.revision,expected_ontology_id:ontology.id,
          confirm_impact:card.querySelector('.change-confirm-impact')?.checked||false});
          status(button.dataset.changeAction==='approve'?'新本体版本已生成；关联候选已重新校验，请继续知识审核。':'本体变更草案已拒绝，现有本体未改变。');await reviews();}
        catch(error){status(error.message,true);card.querySelectorAll('button').forEach(x=>x.disabled=false);}
      });
    }catch(error){if(p===current&&serial===generation)host.textContent='审核清单读取失败：'+error.message;}
  }
  $('refresh-reviews').onclick=reviews;
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
