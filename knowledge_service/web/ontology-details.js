(function(root){
  'use strict';
  // ============================================================
  // 本体发现草案：技术详情（只读展示） + 三段式审核（可交互）
  //
  // 审核顺序严格对齐后端发布物化的依赖方向
  //（services/ontology_discovery.py::_materialize_candidates）：
  //   ① 本体类型(类/关系/属性) → ② 实体候选 → ③ 关系/属性候选
  // 两类状态必须分开：
  //   - 手动排除：用户自己的决定，写进 excluded_*，可随时勾回；
  //   - 派生跳过：上游被关/端点缺失导致"发布时会跳过"，只是预览态，
  //               不写进排除名单，上游恢复即自动复活。
  // 前端只预览"排除/端点"两类后果；本体 Turtle 校验仍由后端发布时执行，
  // 发布结果的 skipped_candidates 是最终裁决。
  // ============================================================
  const PAGE_SIZE = 100; // 组内分页粒度（用户拍板：100）

  function shortIri(value){const iri=String(value??''),delimiter=Math.max(iri.lastIndexOf('#'),iri.lastIndexOf('/'),iri.lastIndexOf(':')),terminal=iri.slice(delimiter+1);if(!terminal)return {label:iri,malformed:false,raw:iri};try{return {label:decodeURIComponent(terminal),malformed:false,raw:iri};}catch{return {label:terminal,malformed:true,raw:iri};}}
  function nameOf(item){return item?.label_zh||item?.label||item?.name||'未命名术语';}
  function indexTerms(summary={}){const index=new Map();for(const kind of ['classes','relations','attributes'])for(const item of summary[kind]||[]){index.set(item.id,nameOf(item));index.set(item.name,nameOf(item));}return index;}
  function refs(values,index){return values?.length?values.map(value=>index.get(value)||shortIri(value).label).join('、'):'未指定';}
  function json(value){return JSON.stringify(value||{},null,2);}
  function copyButton(kind,label,key=''){return `<button type="button" class="ontology-copy" data-copy-kind="${kind}"${key?` data-copy-key="${key}"`:''} aria-label="复制${label}">复制</button>`;}

  // ------------------------------------------------------------
  // 审核状态模型：从 draft 快照一次性建索引，之后所有推导都是 O(1)/O(n)
  // ------------------------------------------------------------
  function createReviewModel(draft){
    const snap=draft.candidate_snapshot||[];
    const mappings=draft.review_base_mappings||draft.mappings||{};
    // 类型开关：source 名（候选的 proposed_type）-> 是否保留
    const termGroups=[
      ['cls','entity_types','实体类'],
      ['rel','relation_types','关系类型'],
      ['attr','attributes','属性类型'],
    ];
    const terms=[];const termOn=new Map();const termKindBySource=new Map();
    for(const [gk,mk,label] of termGroups){
      for(const source of Object.keys(mappings[mk]||{})){
        terms.push({group:gk,source,label});
        termOn.set(source,true);
        termKindBySource.set(source,gk);
      }
    }
    (draft.excluded_terms||[]).forEach(s=>termOn.set(s,false));
    const termLabels={...(draft.term_labels||{})};

    const entities=[],relations=[],attributes=[];
    for(const c of snap){
      if(c.kind==='entity')entities.push(c);
      else if(c.kind==='relation')relations.push(c);
      else if(c.kind==='attribute')attributes.push(c);
    }
    // 实体索引：物化时按文档分组解析端点（by_document[document_key][candidate_id]）
    const entByDoc=new Map();const entById=new Map();
    const docKey=c=>(c.document_version_id||c.document_id||'');
    for(const e of entities){
      entById.set(e.id,e);
      const k=docKey(e);
      if(!entByDoc.has(k))entByDoc.set(k,new Map());
      entByDoc.get(k).set(e.id,e);
    }
    // 跨片段 id 对不上时的文本兜底（与脑图 _candidate_mindmap 一致）：
    // 同文档 + 同名唯一才可兜底，歧义不猜
    const textIndex=new Map();
    for(const e of entities){
      const key=docKey(e)+'::'+String(e.text||'').trim().toLowerCase();
      if(!textIndex.has(key))textIndex.set(key,[]);
      textIndex.get(key).push(e);
    }
    function resolveEndpoint(rel,idKey,textFallback){
      const local=entByDoc.get(docKey(rel));
      let e=local&&local.get(rel[idKey]);
      if(!e){
        const hits=textIndex.get(docKey(rel)+'::'+String(rel[textFallback]||'').trim().toLowerCase())||[];
        if(hits.length===1)e=hits[0];
      }
      return e||null;
    }
    for(const r of relations){r._subject=resolveEndpoint(r,'subject_id','subject');r._object=resolveEndpoint(r,'object_id','object');}
    for(const a of attributes){a._entity=entById.get(a.entity_id)||null;}

    // 手动排除集合（用户决定）
    const manual=new Set(draft.excluded_candidate_ids||[]);

    // ---------- 派生状态（纯函数，与后端 _materialize_candidates 裁决一致） ----------
    // 每个非 on 状态都带 code：行内显示具体原因（reason），统计条按 code 聚合（量大也不刷屏）
    const entityState=e=>{
      if(!termOn.get(e.proposed_type))return {state:'derived',code:'term_excluded',reason:`类型「${e.proposed_type}」已在第①步排除`};
      if(manual.has(e.id))return {state:'manual',code:'manual',reason:'你手动取消，勾选即可恢复'};
      if(!String(e.text||'').trim())return {state:'derived',code:'empty_name',reason:'实体名称为空，无法物化'};
      return {state:'on'};
    };
    const endpointState=(e,fallbackName)=>{
      if(!e)return {state:'derived',code:'unresolved_endpoint',reason:`端点「${fallbackName}」在候选中解析不到（跨片段/跨文档无法对应）`};
      const s=entityState(e);
      return s.state==='on'?{state:'on'}:{state:'derived',code:'endpoint_missing',reason:`端点「${e.text}」未入图：${s.reason}`};
    };
    const relationState=r=>{
      if(!termOn.get(r.proposed_type))return {state:'derived',code:'term_excluded',reason:`关系类型「${r.proposed_type}」已在第①步排除`};
      const s=endpointState(r._subject,r.subject);if(s.state!=='on')return s;
      const o=endpointState(r._object,r.object);if(o.state!=='on')return o;
      if(manual.has(r.id))return {state:'manual',code:'manual',reason:'你手动取消，勾选即可恢复'};
      return {state:'on'};
    };
    const attributeState=a=>{
      if(!termOn.get(a.proposed_type))return {state:'derived',code:'term_excluded',reason:`属性类型「${a.proposed_type}」已在第①步排除`};
      if(!a._entity||entityState(a._entity).state!=='on')return {state:'derived',code:'owner_missing',reason:`所属实体「${a._entity?a._entity.text:'?'}」未入图`};
      if(manual.has(a.id))return {state:'manual',code:'manual',reason:'你手动取消，勾选即可恢复'};
      return {state:'on'};
    };
    // 全量统计（在内存数组上算，不依赖 DOM —— 大数据量下仍精确）
    // 原因按 code 聚合：几千条候选也只显示"类型已排除 ×150"这类汇总，不会逐条刷屏
    function stats(){
      const count=arr=>arr.reduce((m,x)=>{m[x.state==='on'?'on':'skip']++;if(x.state!=='on'){const c=x.code||'other';m.reasons[c]=(m.reasons[c]||0)+1;}return m;},{on:0,skip:0,reasons:{}});
      return {e:count(entities.map(entityState)),r:count(relations.map(relationState)),a:count(attributes.map(attributeState))};
    }
    return {terms,termOn,termLabels,termKindBySource,entities,relations,attributes,
      manual,entityState,relationState,attributeState,stats,mappings};
  }

  // ------------------------------------------------------------
  // 只读技术详情（保留旧契约：版本差异 / 本体定义 / Turtle / IRI 映射）
  // ------------------------------------------------------------
  const GROUPS=[['entity_types','实体类'],['relation_types','关系'],['attributes','属性']];
  function readableOntology(draft,esc){const summary=draft.schema_summary||draft.summary||{},index=indexTerms(summary),cards=[];for(const item of summary.classes||[])cards.push(`<article class="ontology-class-card"><span>实体类</span><b>${esc(nameOf(item))}</b><p>${esc(item.description||'暂无定义，请在发布前补充')}</p><small>父类：${esc(refs(item.parents,index))}</small></article>`);for(const item of summary.relations||[]){const source=refs(item.domain,index),target=refs(item.range,index);cards.push(`<article class="ontology-relation-card"><span>关系类型</span><div class="ontology-relation-flow" aria-label="${esc(source)} 通过 ${esc(nameOf(item))} 指向 ${esc(target)}"><b>${esc(source)}</b><i>→</i><strong>${esc(nameOf(item))}</strong><i>→</i><b>${esc(target)}</b></div><p>${esc(item.description||'暂无定义，请在发布前补充')}</p><small>方向：起点实体类型 → 关系 → 终点实体类型</small></article>`);}for(const item of summary.attributes||[])cards.push(`<article class="ontology-attribute-card"><span>属性</span><b>${esc(nameOf(item))}</b><p>${esc(item.description||'暂无定义，请在发布前补充')}</p><small>适用实体：${esc(refs(item.domain,index))}<br>数据类型：${esc(refs(item.range,index))}</small></article>`);return cards.join('')||'<p class="subtle">草案中没有可展示的本体术语</p>';}
  function readableDiff(diff={},esc){const labels={added:'新增',retained:'保留',removed:'删除'},items=[];for(const [group,title] of [['classes','实体类'],['relations','关系'],['attributes','属性']])for(const state of ['added','retained','removed']){const rows=diff[group]?.[state]||[];if(rows.length)items.push(`<div><b>${title} · ${labels[state]}</b><p>${rows.map(row=>esc(row.label||row.name||shortIri(row.id||row).label)).join('、')}</p></div>`);}return items.join('')||'<p class="subtle">没有可展示的版本变化。</p>';}
  function readableMappings(draft,esc){const mappings=draft.review_base_mappings||draft.mappings||{},excluded=new Set(draft.excluded_terms||[]),rows=[];for(const [key,title] of GROUPS)for(const [name,iri] of Object.entries(mappings[key]||{})){const shown=shortIri(iri);rows.push(`<div class="ontology-mapping-row" data-term-row><label><input type="checkbox" data-term-include data-term-source="${esc(name)}" ${excluded.has(name)?'':'checked'}>保留${esc(title)}</label><input data-term-label data-term-source="${esc(name)}" value="${esc(draft.term_labels?.[name]||name)}" aria-label="${esc(title)}中文名称"><i>→</i><code>${esc(shown.label)}</code>${shown.malformed?'<em class="ontology-encoding-warning">编码格式不完整</em>':''}<details><summary>查看完整技术标识</summary><code>${esc(iri)}</code>${copyButton('iri','完整 IRI',esc(`${key}:${name}`))}</details></div>`);}return rows.join('')||'<p class="subtle">没有名称映射。</p>';}
  function candidateReviewHtml(draft,esc){
    // 已发布草案不显示审核；draft 状态的审核改走 bindReviewPanel 三段式，这里仅留作兼容
    if(draft.status!=='draft')return '';
    const excluded=new Set(draft.excluded_candidate_ids||[]);
    const rows=(draft.candidate_snapshot||[]).map(item=>`<label class="candidate-review-row"><input type="checkbox" data-candidate-include value="${esc(item.id)}" ${excluded.has(item.id)?'':'checked'}><span>${esc(item.kind==='entity'?'实体':item.kind==='relation'?'关系':'属性')}</span><b>${esc(item.text||`${item.subject||'?'} → ${item.proposed_type||'?'} → ${item.object||'?'}`)}</b><small>${esc(item.proposed_type||'未分类')} · 置信度 ${Math.round(Number(item.confidence||0)*100)}%</small></label>`).join('');
    return `<div class="ontology-detail-region candidate-review"><h5>逐项审核候选知识（旧视图）</h5><div class="candidate-review-list">${rows||'<p class="subtle">没有候选知识。</p>'}</div></div>`;
  }
  function technicalSections(draft,esc){
    const suffix=String(draft.id||'draft').replace(/[^\w-]/g,'');
    return `<div class="ontology-detail-region" aria-describedby="diff-help-${suffix}"><h5>版本差异</h5><p id="diff-help-${suffix}">展示本草案相对上一版新增、保留或删除的实体类、关系和属性。第一版草案会把全部术语显示为新增。</p><div class="ontology-readable-grid">${readableDiff(draft.diff,esc)}</div><details><summary>查看原始数据</summary>${copyButton('diff','原始差异数据')}<pre>${esc(json(draft.diff))}</pre></details></div><div class="ontology-detail-region" aria-describedby="turtle-help-${suffix}"><h5>本体定义</h5><p id="turtle-help-${suffix}">本体定义规定系统认识哪些事物、它们可以建立什么关系，以及可使用哪些属性。系统以 Turtle 标准格式保存。</p><div class="ontology-readable-grid">${readableOntology(draft,esc)}</div><details><summary>查看 Turtle 原始源码</summary>${copyButton('turtle','Turtle 原始源码')}<pre>${esc(draft.turtle||'')}</pre></details></div><div class="ontology-detail-region" aria-describedby="mapping-help-${suffix}"><h5>名称与技术标识（可编辑）</h5><p id="mapping-help-${suffix}">三段式审核里的类型开关与改名等价于这里的勾选；%E4%... 是中文的 URI 编码，不是乱码。</p><details><summary>展开 IRI 原始映射</summary><div class="ontology-mappings">${readableMappings(draft,esc)}</div><details><summary>查看原始映射数据</summary>${copyButton('mappings','原始映射数据')}<pre>${esc(json(draft.mappings))}</pre></details></details></div><div class="ontology-copy-status" role="status" aria-live="polite"></div>`;
  }
  function renderTechnicalDetails(draft={},esc=value=>String(value)){
    return `<details class="ontology-technical-details"><summary>审核草案并查看技术详情</summary>${technicalSections(draft,esc)}</details>`;
  }
  function bindCopyButtons(host,draft={},clipboard=root.navigator?.clipboard){const status=host.querySelector('.ontology-copy-status');host.querySelectorAll('[data-copy-kind]').forEach(button=>button.onclick=async()=>{const kind=button.dataset.copyKind,key=button.dataset.copyKey;let value=kind==='turtle'?(draft.turtle||''):kind==='diff'?json(draft.diff):kind==='mappings'?json(draft.mappings):'';if(kind==='iri'){const split=key.indexOf(':'),group=key.slice(0,split),name=key.slice(split+1);value=draft.mappings?.[group]?.[name]||'';}try{if(!clipboard?.writeText)throw Error('clipboard unavailable');await clipboard.writeText(value);button.textContent='已复制';if(status)status.textContent='已复制';root.setTimeout(()=>button.textContent='复制',2000);}catch{if(status)status.textContent='复制失败，请手动选择内容';}});}

  // ------------------------------------------------------------
  // 三段式审核面板（大数据量策略）：
  //  - 关闭的分组不渲染行（不是隐藏，是不进 DOM），首屏只有组标题；
  //  - 组内每页 100 条，"加载更多"追加；
  //  - 类型开关只局部刷新受影响组标题 + 已展开组 + 统计条，不整页重建；
  //  - 统计在内存数组上算，精确到全量。
  // ------------------------------------------------------------
  const KIND_LABEL={cls:['实体类','kind-cls'],rel:['关系类型','kind-rel'],attr:['属性类型','kind-attr']};
  // 统计条的原因分类标签（按 code 聚合显示，避免大数据量下一屏全是"×1"）
  const REASON_LABEL={
    term_excluded:'类型已排除',
    manual:'手动取消',
    endpoint_missing:'端点实体未入图（连带）',
    unresolved_endpoint:'端点无法解析（跨片段/跨文档）',
    owner_missing:'所属实体未入图（连带）',
    empty_name:'名称为空',
  };
  function escHtml(v){return String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
  const esc=escHtml;

  function reviewPanelHtml(draft){
    if(draft.status!=='draft')return '';
    return `<div class="odr-panel" data-odr="${esc(draft.id)}">
      <p class="odr-flow">审核顺序与发布处理一致：<b>① 定本体骨架</b>（关一个类型会连带影响一片）→ <b>② 审实体</b>（取消实体，挂它的关系/属性跟着跳过）→ <b>③ 审关系与属性</b>。灰色带原因的是被上游<b>连带跳过</b>的预览（重新打开上游即恢复，不占你的排除名单）；橙色描边是你<b>手动取消</b>的。</p>
      <section class="odr-step">
        <div class="odr-step-head"><span class="odr-no">1</span><h5>本体骨架：类型去留与改名</h5><small>关闭类型只影响这批候选，历史图谱不变；停用已发布类型请去「本体管理」</small></div>
        <div data-odr-terms></div>
      </section>
      <section class="odr-step">
        <div class="odr-step-head"><span class="odr-no">2</span><h5>实体候选（按类型分组）</h5>
          <span class="odr-tools"><input data-odr-search placeholder="搜索实体名/文档…"><button type="button" data-odr-enttoggle>全选/全不选（仅可选项）</button></span></div>
        <div data-odr-entities></div>
      </section>
      <section class="odr-step">
        <div class="odr-step-head"><span class="odr-no">3</span><h5>关系与属性候选</h5><small>端点实体或类型没了，关系自动跳过并注明原因</small></div>
        <div class="odr-ra"><div data-odr-relations></div><div data-odr-attributes></div></div>
      </section>
      <footer class="odr-foot">
        <div class="odr-stats" data-odr-stats></div>
        <button type="button" class="odr-save" data-odr-save>保存审核修改</button>
      </footer>
    </div>`;
  }

  // 绑定：host = 草案 article；onSave(payload) 由调用方发 PUT；label 改名随保存一起提交
  function bindReviewPanel(host,draft,onSave){
    const panel=host.querySelector('[data-odr]');
    if(!panel)return;
    const M=createReviewModel(draft);
    // 渲染分页状态：每组展开后显示条数；关闭即清空 DOM
    const shown=new Map(); // groupKey -> 已渲染条数
    let searchQ='';

    // ---------- ① 类型 ----------
    function renderTerms(){
      panel.querySelector('[data-odr-terms]').innerHTML=M.terms.map(t=>{
        const on=M.termOn.get(t.source);
        const [kl,kc]=KIND_LABEL[t.group];
        return `<div class="odr-term ${on?'':'off'}" data-term-row="${esc(t.source)}">
          <span class="odr-kind ${kc}">${kl}</span>
          <input class="odr-term-name" data-term-name="${esc(t.source)}" value="${esc(M.termLabels[t.source]||t.source)}" ${on?'':'disabled'}>
          <label class="odr-switch"><input type="checkbox" data-term-toggle="${esc(t.source)}" ${on?'checked':''}><i></i></label>
        </div>`;
      }).join('');
    }

    // ---------- ② 实体分组 ----------
    // 组标题始终渲染（汇总状态）；组内行只在展开且需要时渲染
    function entityGroups(){
      const map=new Map();
      for(const e of M.entities){
        if(!map.has(e.proposed_type))map.set(e.proposed_type,[]);
        map.get(e.proposed_type).push(e);
      }
      return map;
    }
    function groupSummary(source,list){
      const on=M.termOn.get(source);
      if(!on){
        // 计算被波及的关系/属性数
        const ids=new Set(list.map(e=>e.id));
        const hitR=M.relations.filter(r=>(ids.has(r._subject?.id)||ids.has(r._object?.id))).length;
        const hitA=M.attributes.filter(a=>ids.has(a._entity?.id)).length;
        return {line:`<span class="odr-g-impact">整组跳过 · 波及关系 ${hitR} / 属性 ${hitA}</span>`,disabled:true};
      }
      const onN=list.filter(e=>M.entityState(e).state==='on').length;
      return {line:`<span class="odr-g-count">${onN}/${list.length} 入图</span>`,disabled:false};
    }
    function entityRow(e){
      const s=M.entityState(e);
      const dis=s.state==='derived'?'disabled':'';
      const chk=s.state==='on'?'checked':'';
      const cls=s.state==='manual'?'manual-off':s.state==='derived'?'derived':'';
      return `<label class="odr-crow ${cls}">
        <input type="checkbox" data-ent="${esc(e.id)}" ${dis} ${chk}>
        <span><b>${esc(e.text)}</b><small>${esc(e.document_title||e.document_id||'')} · 置信度 ${Math.round(Number(e.confidence||0)*100)}%</small>
        ${s.reason?`<em class="odr-reason">${esc(s.reason)}</em>`:''}</span></label>`;
    }
    // 组上下文：key -> {items,row}，供"展开时按需填充"使用
    const groupCtx=new Map();
    function groupBodyHtml(key){
      const c=groupCtx.get(key);if(!c)return '';
      const n=shown.get(key)||0;
      const rows=c.items.slice(0,n).map(c.row).join('');
      const more=c.items.length>n?`<button type="button" class="odr-more" data-more="${esc(key)}">加载更多（已显示 ${n}/${c.items.length}，每页 ${PAGE_SIZE}）</button>`:'';
      return rows+more;
    }
    function fillGroup(g,key){const body=g.querySelector('.odr-gbody');if(body)body.innerHTML=groupBodyHtml(key);}
    function renderEntities(){
      const host2=panel.querySelector('[data-odr-entities]');
      const q=searchQ.trim().toLocaleLowerCase();
      const html=[...entityGroups()].map(([source,list0])=>{
        const list=q?list0.filter(e=>String(e.text).toLocaleLowerCase().includes(q)||String(e.document_title||'').toLocaleLowerCase().includes(q)):list0;
        if(!list.length)return '';
        const sum=groupSummary(source,list);
        const key='ent:'+source;
        if(sum.disabled){
          // 类型关闭：组内行一律不进 DOM（连分页按钮都不生成）
          groupCtx.delete(key);
          return `<details class="odr-group disabled" data-group="${esc(key)}"><summary>
            <b>${esc(source)}</b><span class="odr-g-count">${list.length} 个实体</span>${sum.line}</summary>
            <div class="odr-gbody"><p class="odr-g-skip">类型已在第①步关闭，这 ${list.length} 个实体发布时全部跳过；重新打开类型即恢复，无需重新勾选。</p></div></details>`;
        }
        groupCtx.set(key,{items:list,row:entityRow});
        const open=shown.has(key);
        return `<details class="odr-group" ${open?'open':''} data-group="${esc(key)}"><summary>
            <b>${esc(source)}</b>${sum.line}</summary>
          <div class="odr-gbody">${open?groupBodyHtml(key):''}</div></details>`;
      }).join('');
      host2.innerHTML=html||'<p class="subtle">没有实体候选。</p>';
    }

    // ---------- ③ 关系/属性（同样折叠 + 100 分页，折叠即释放 DOM） ----------
    function raSection(containerSel,items,stateFn,textFn){
      const host2=panel.querySelector(containerSel);
      const isRel=containerSel==='[data-odr-relations]';
      const groups=new Map();
      items.forEach(x=>{const k=x.proposed_type||'未分类';if(!groups.has(k))groups.set(k,[]);groups.get(k).push(x);});
      host2.innerHTML=[...groups].map(([type,list])=>{
        const key=(isRel?'rel:':'attr:')+type;
        const row=x=>{
          const s=stateFn(x);const dis=s.state==='derived'?'disabled':'';const chk=s.state==='on'?'checked':'';
          const cls=s.state==='manual'?'manual-off':s.state==='derived'?'derived':'';
          return `<label class="odr-crow ${cls}"><input type="checkbox" data-${isRel?'rel':'attr'}="${esc(x.id)}" ${dis} ${chk}>
            <span><b>${textFn(x)}</b><em class="odr-tag">${esc(type)}</em>${s.reason?`<em class="odr-reason">${esc(s.reason)}</em>`:''}</span></label>`;
        };
        groupCtx.set(key,{items:list,row});
        const open=shown.has(key);
        const onN=list.filter(x=>stateFn(x).state==='on').length;
        return `<details class="odr-group" ${open?'open':''} data-group="${esc(key)}"><summary><b>${esc(type)}</b><span class="odr-g-count">${onN}/${list.length} 入图</span></summary><div class="odr-gbody">${open?groupBodyHtml(key):''}</div></details>`;
      }).join('')||'<p class="subtle">无候选</p>';
    }
    function renderRA(){
      raSection('[data-odr-relations]',M.relations,M.relationState,
        r=>`${r.subject||r._subject?.text||'?'} → ${r.proposed_type} → ${r.object||r._object?.text||'?'}`);
      raSection('[data-odr-attributes]',M.attributes,M.attributeState,
        a=>`${a._entity?.text||'?'} · ${a.proposed_type} = ${a.value??''}`);
    }

    // ---------- 统计条 ----------
    function renderStats(){
      const st=M.stats();
      // 按 code 聚合成"标签 ×数量"，与具体候选名无关（几千条也只是一行汇总）
      const chips=obj=>Object.entries(obj.reasons)
        .sort((a,b)=>b[1]-a[1])
        .map(([code,n])=>`<span class="odr-chip">${esc(REASON_LABEL[code]||code)} ×${n}</span>`).join('');
      panel.querySelector('[data-odr-stats]').innerHTML=
        `<b class="ok">预计入图：${st.e.on} 实体 · ${st.r.on} 关系 · ${st.a.on} 属性</b>
         <span class="skip">将跳过：${st.e.skip} 实体 / ${st.r.skip} 关系 / ${st.a.skip} 属性</span>
         <span class="odr-chips">${chips(st.e)}${chips(st.r)}${chips(st.a)}</span>`;
    }
    function refreshType(source){
      // 局部刷新：只重画受影响的实体组 + RA 区 + 统计；未展开的组本来就零行，成本极低
      renderEntities();renderRA();renderStats();renderTerms();
    }

    // ---------- 事件（统一委托） ----------
    panel.addEventListener('change',e=>{
      const t=e.target;
      if(t.dataset.termToggle){
        M.termOn.set(t.dataset.termToggle,t.checked);
        // 类型变化使所有相关分页缓存失效（关类型本就零渲染，重开回到前 100）
        for(const k of[...shown.keys()])shown.delete(k);
        refreshType(t.dataset.termToggle);
      }else if(t.dataset.ent){
        t.checked?M.manual.delete(t.dataset.ent):M.manual.add(t.dataset.ent);
        renderRA();renderStats();
      }else if(t.dataset.rel){
        t.checked?M.manual.delete(t.dataset.rel):M.manual.add(t.dataset.rel);renderStats();
      }else if(t.dataset.attr){
        t.checked?M.manual.delete(t.dataset.attr):M.manual.add(t.dataset.attr);renderStats();
      }
    });
    panel.addEventListener('input',e=>{
      if(e.target.dataset.termName)M.termLabels[e.target.dataset.termName]=e.target.value;
      if(e.target.dataset.odrSearch!==undefined)searchQ=e.target.value;
    });
    panel.querySelector('[data-odr-search]').addEventListener('input',()=>{
      // 搜索时临时展开命中组的前 100；清空后回到折叠态
      const q=searchQ.trim();
      shown.clear();
      if(q)for(const [source] of entityGroups())shown.set('ent:'+source,PAGE_SIZE);
      renderEntities();
    });
    panel.addEventListener('click',e=>{
      const more=e.target.closest('[data-more]');
      if(more){
        // 只重填这一组，不重建整个区（保留其它组的展开状态与滚动位置）
        const key=more.dataset.more;
        const g=more.closest('details.odr-group');
        shown.set(key,(shown.get(key)||0)+PAGE_SIZE);
        fillGroup(g,key);
      }
    });
    // 折叠 = 真正释放 DOM：关闭清空内容，展开才按需填充（toggle 不冒泡，用捕获阶段）
    panel.addEventListener('toggle',e=>{
      const g=e.target;
      if(!g||g.tagName!=='DETAILS'||!g.dataset||!g.dataset.group)return;
      if(g.classList.contains('disabled'))return;
      const key=g.dataset.group;
      if(!g.open){
        shown.delete(key);
        const body=g.querySelector('.odr-gbody');
        if(body)body.innerHTML='';
      }else if(!shown.has(key)){
        shown.set(key,PAGE_SIZE);
        fillGroup(g,key);
      }
    },true);
    panel.querySelector('[data-odr-enttoggle]').addEventListener('click',()=>{
      // 仅作用于当前"可入图(on)"或"手动取消(manual)"的实体；派生跳过不动
      const hasOn=M.entities.some(x=>M.entityState(x).state==='on');
      M.entities.forEach(x=>{const s=M.entityState(x);
        if(s.state==='on'&&hasOn)M.manual.add(x.id);
        else if(s.state==='manual')M.manual.delete(x.id);});
      renderEntities();renderRA();renderStats();
    });
    panel.querySelector('[data-odr-save]').addEventListener('click',async()=>{
      const btn=panel.querySelector('[data-odr-save]');btn.disabled=true;
      try{
        const payload={
          excluded_candidate_ids:[...M.manual],
          excluded_terms:[...M.termOn].filter(([,on])=>!on).map(([s])=>s),
          term_labels:Object.fromEntries(Object.entries(M.termLabels).filter(([s,v])=>v&&v.trim()&&v.trim()!==s)),
        };
        await onSave(payload);
      }finally{btn.disabled=false;}
    });

    // 初始：组全折叠（零行 DOM），只画标题/类型/统计
    shown.clear();
    renderTerms();renderEntities();renderRA();renderStats();
    return M;
  }

  const api={shortIri,nameOf,indexTerms,refs,createReviewModel,PAGE_SIZE,
    reviewPanelHtml,bindReviewPanel,
    renderTechnicalDetails,readableDiff,readableOntology,readableMappings,
    candidateReviewHtml,bindCopyButtons};
  root.OntologyDetails=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
