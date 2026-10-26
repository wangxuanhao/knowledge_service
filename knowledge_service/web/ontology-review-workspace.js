/* A review unit is one exact definition IRI. The server owns operation membership. */
(function(root,factory){
  const exported=factory();
  if(typeof module==='object'&&module.exports)module.exports=exported;
  else root.OntologyReview=exported;
})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  const actions={create_term:'新增定义',add_parent:'添加父类',remove_parent:'移除父类',add_domain:'添加所属类型',
    remove_domain:'移除所属类型',add_range:'添加目标类型',remove_range:'移除目标类型',set_datatype:'修改数据类型',
    retire_term:'停用定义',restore_term:'恢复定义',add_annotation:'添加名称或说明',remove_annotation:'移除名称或说明'};
  const localName=value=>String(value||'').split(/[#/:]/).pop()||String(value||'未命名');
  const finalDecision=item=>['approve','reject'].includes(item.decision?.action);
  function terms(draft){
    const ontology=draft.ontology?.summary||draft.ontology||{};
    return ['classes','relations','attributes'].flatMap((key,i)=>(ontology[key]||[]).map(term=>({...term,reviewKind:['实体类型','关系类型','属性'][i]})));
  }
  function groupDefinitions(draft,plan){
    const catalog=new Map(terms(draft).map(term=>[term.id,term])),groups=new Map();
    for(const item of plan.items||[]){
      const iri=item.target_iri;if(!iri)continue;
      if(!groups.has(iri)){
        const term=catalog.get(iri)||{},creation=(draft.operations||[]).find(op=>op.target_iri===iri&&op.action==='create_term');
        const kind=creation?.after?.kind;
        groups.set(iri,{iri,label:term.label_zh||term.label||term.name||creation?.after?.label||localName(iri),
          kind:term.reviewKind||({class:'实体类型',object_property:'关系类型',datatype_property:'属性'}[kind])||'定义',items:[]});
      }
      groups.get(iri).items.push(item);
    }
    return [...groups.values()].map(group=>{
      const pending=group.items.filter(item=>!finalDecision(item));
      return {...group,pending,blocked:pending.some(item=>item.approvable!==true),
        requiresReason:pending.some(item=>item.requires_reason),
        exception:pending.some(item=>!item.auto_approvable),
        approved:group.items.filter(item=>item.decision?.action==='approve').length,
        rejected:group.items.filter(item=>item.decision?.action==='reject').length};
    });
  }
  function createController({getContext,request,onSaved=()=>{},onChange=()=>{}}){
    const state={draft:null,plan:null,stage:null,groups:[],activeIri:null,filter:'pending',busy:false,loading:false,uncertain:false,error:''};
    let closed=false,epoch=0,stamp=null;
    const copyContext=()=>({...getContext()});
    const same=(a,b)=>!!a&&!!b&&a.projectId===b.projectId&&a.draftId===b.draftId&&a.revision===b.revision&&!b.preview;
    const valid=(context,token)=>!closed&&token===epoch&&same(context,getContext());
    const notify=()=>{if(!closed)onChange(state);};
    const sameOperations=(left,right)=>JSON.stringify([left.ontology,left.operations])===JSON.stringify([right.ontology,right.operations]);
    function terminal(draft){
      state.draft=draft;state.plan=null;state.groups=[];state.activeIri=null;state.stage=null;state.uncertain=false;
      state.error='草案已结束，审核决定已保存。请关闭后查看草案记录。';
    }
    const visible=()=>state.groups.filter(group=>state.filter==='all'||(state.filter==='completed'? !group.pending.length:
      state.filter==='exceptions'?group.pending.length&&group.exception:group.pending.length));
    function select(iri){if(state.busy||state.loading)return;state.activeIri=iri;state.error='';notify();}
    function setFilter(filter){if(state.busy||state.loading)return;state.filter=filter;if(!visible().some(g=>g.iri===state.activeIri))state.activeIri=visible()[0]?.iri||null;notify();}
    function navigate(offset){const list=visible(),index=list.findIndex(g=>g.iri===state.activeIri);if(list.length)select(list[Math.max(0,Math.min(list.length-1,index+offset))].iri);}
    async function load(){
      if(closed||state.busy||getContext().preview)return;
      const context=copyContext(),token=++epoch;
      state.loading=true;state.error='';notify();
      try{
        const draft=await request('',undefined,'GET');
        if(!valid(context,token))return;
        if(draft.id!==context.draftId)throw Error('草案身份不一致，请关闭后重新打开。');
        if(draft.status!=='pending'){
          onSaved(draft);if(!closed&&token===epoch){stamp=copyContext();terminal(draft);}return;
        }
        if(draft.revision!==context.revision&&(!state.uncertain||!state.draft||draft.revision<context.revision||!sameOperations(state.draft,draft)))
          throw Error('草案已在其他位置更新，请关闭审核并刷新草案，核对新修订后再处理。');
        const [plan,stage]=await Promise.all([request('/review-plan',undefined,'GET'),request('/stage-availability',undefined,'GET')]);
        if(!valid(context,token))return;
        if(draft.revision!==plan.revision)throw Error('草案与审核计划修订不一致，请重新读取。');
        if(!draft.submitted_at||!plan.validation_fingerprint)throw Error('草案尚未提交，请返回草案核对。');
        onSaved(draft);
        if(closed||token!==epoch||!same({...context,revision:draft.revision},getContext()))return;
        stamp=copyContext();state.draft=draft;state.plan=plan;state.stage=stage;
        state.groups=groupDefinitions(draft,plan);state.uncertain=false;
        if(!visible().some(group=>group.iri===state.activeIri))state.activeIri=visible()[0]?.iri||null;
      }catch(error){if(valid(context,token)){state.error=error.message||String(error);state.uncertain=true;}}
      finally{if(!closed&&token===epoch){state.loading=false;notify();}}
    }
    async function save(route,body,next){
      if(closed||state.busy||state.loading)return;
      if(state.uncertain)throw Error('上次结果尚未确认，请先重新读取审核结果。');
      if(!same(stamp,getContext()))throw Error('项目、草案或修订已变化，请关闭后重新打开审核。');
      if(state.stage?.stages?.review?.allowed!==true)throw Error('服务端当前不允许审核，请重新读取条件。');
      const context=copyContext(),token=epoch,oldList=visible(),oldIndex=oldList.findIndex(g=>g.iri===state.activeIri);
      state.busy=true;state.error='';notify();
      let saved=false;
      try{
        const result=await request(route,{expected_revision:state.draft.revision,expected_ontology_id:state.draft.base_ontology_id,
          validation_fingerprint:state.plan.validation_fingerprint,acknowledged_warning_codes:[],...body},'POST');
        if(!valid(context,token))return;
        if(!result?.draft||result.draft.id!==context.draftId)throw Error('审核结果不完整，请重新读取确认。');
        if(result.draft.status==='pending'&&!sameOperations(state.draft,result.draft))
          throw Error('草案定义已在其他位置更新，请关闭审核并刷新草案后再处理。');
        onSaved(result.draft);saved=true;
        stamp=copyContext();state.draft=result.draft;
        if(result.draft.status!=='pending'){
          terminal(result.draft);return;
        }
        state.busy=false;await load();
        if(closed||!same(stamp,getContext())||state.uncertain)return;
        if(next){
          const remaining=visible();
          const following=oldList.slice(oldIndex+1).find(old=>remaining.some(g=>g.iri===old.iri));
          state.activeIri=following?.iri||remaining[0]?.iri||null;
        }
      }catch(error){
        if(!closed&&(valid(context,token)||saved&&same(stamp,getContext()))){state.uncertain=true;state.error='审核结果未确认：'+(error.message||error);}
        throw error;
      }finally{if(!closed){state.busy=false;notify();}}
    }
    async function decide(action,{reason='',acknowledged=[],next=false}={}){
      if(!['approve','reject'].includes(action))throw Error('无效审核操作');
      if(state.uncertain)throw Error('请先重新读取审核结果。');
      const group=state.groups.find(g=>g.iri===state.activeIri);if(!group?.pending.length)return;
      if(action==='approve'&&group.blocked)throw Error('此定义有校验阻断，请先修正后再批准。');
      if(!reason.trim()&&(action==='reject'||group.requiresReason))throw Error('请填写处理理由。');
      const codes=[...new Set([...(state.plan.warnings||[]),...group.pending.flatMap(item=>item.warnings||[])].map(w=>w.code).filter(Boolean))];
      if(codes.some(code=>!acknowledged.includes(code)))throw Error('请逐项确认相关警告。');
      await save('/decisions/by-definition',{target_iri:group.iri,action,reason:reason.trim(),acknowledged_warning_codes:acknowledged},next);
    }
    async function bulkApprove(){
      if(!(state.plan?.items||[]).some(item=>!item.decision&&item.auto_approvable===true))return;
      await save('/decisions/batch-approve',{},true);
    }
    return {state,load,select,setFilter,navigate,visible,decide,bulkApprove,close(){closed=true;++epoch;}};
  }

  function open({host,getContext,request,onSaved}){
    const doc=host.ownerDocument,el=(tag,cls,text)=>{const node=doc.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;};
    const dialog=el('dialog','om-definition-review');dialog.setAttribute('aria-labelledby','om-definition-review-title');
    const header=el('header','odr-header'),titleBox=el('div'),title=el('h2',null,'按定义审核');title.id='om-definition-review-title';
    titleBox.append(el('small',null,'草案审核'),title);
    const button=(text,handler,cls='om-btn')=>{const node=el('button',cls,text);node.type='button';node.addEventListener('click',handler);return node;};
    const close=button('关闭',()=>dialog.close());header.append(titleBox,close);
    const summary=el('p','odr-summary'),toolbar=el('div','odr-toolbar'),filter=el('select');filter.setAttribute('aria-label','审核范围');
    for(const [value,text] of [['pending','待审核定义'],['exceptions','需重点核对'],['completed','已完成定义'],['all','全部定义']]){
      const option=el('option',null,text);option.value=value;filter.append(option);
    }
    const feedback=el('p','odr-feedback');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
    const body=el('div','odr-body'),list=el('nav','odr-list'),detail=el('article','odr-detail'),footer=el('footer','odr-footer');
    list.setAttribute('aria-label','定义列表');detail.tabIndex=0;
    const previous=button('上一项',()=>controller.navigate(-1)),following=button('下一项',()=>controller.navigate(1));
    const retry=button('重新读取',()=>{void controller.load();}),bulk=button('批量批准',()=>{void execute(()=>controller.bulkApprove());});
    toolbar.append(filter,bulk,retry);body.append(list,detail);dialog.append(header,summary,toolbar,feedback,body,footer);
    let reasonValue='',acknowledged=new Set(),shownIri=null,shownRevision=null;
    const reason=el('textarea');reason.rows=2;reason.placeholder='说明批准或不批准的原因';reason.setAttribute('aria-label','处理理由');
    reason.addEventListener('input',()=>{reasonValue=reason.value;});
    async function execute(task){try{await task();}catch(error){feedback.textContent=controller.state.uncertain
      ?controller.state.error+' 请先重新读取审核结果。':error.message||String(error);}}
    const approve=button('批准此定义',()=>{void execute(()=>controller.decide('approve',{reason:reasonValue,acknowledged:[...acknowledged]}));},'om-btn primary');
    const approveNext=button('批准并下一项',()=>{void execute(()=>controller.decide('approve',{reason:reasonValue,acknowledged:[...acknowledged],next:true}));},'om-btn primary');
    const reject=button('不批准此定义',()=>{void execute(()=>controller.decide('reject',{reason:reasonValue,acknowledged:[...acknowledged]}));});
    const rejectNext=button('不批准并下一项',()=>{void execute(()=>controller.decide('reject',{reason:reasonValue,acknowledged:[...acknowledged],next:true}));});
    const position=el('span','odr-position'),navigation=el('div','odr-navigation'),decisions=el('div','odr-decisions');
    navigation.append(previous,position,following);decisions.append(reject,rejectNext,approve,approveNext);footer.append(navigation,decisions);
    function display(value,draft){
      if(value==null)return '—';if(Array.isArray(value))return value.map(v=>display(v,draft)).join('、')||'—';
      if(typeof value==='object')return Object.entries(value).filter(([k])=>!['fingerprint','operation_fingerprint'].includes(k)).map(([k,v])=>`${fields[k]||k}：${display(v,draft)}`).join('；')||'—';
      const names={'http://www.w3.org/2000/01/rdf-schema#label':'名称','http://www.w3.org/2000/01/rdf-schema#comment':'说明',
        class:'实体类型',relation:'关系类型',attribute:'属性',object_property:'关系类型',datatype_property:'属性',zh:'中文'};
      const term=terms(draft).find(t=>t.id===value);return term?.label_zh||term?.label||names[value]||String(value);
    }
    const fields={kind:'定义类别',label:'名称',label_zh:'中文名称',iri:'标识',value:'内容',predicate:'字段',datatype:'数据类型',language:'语言',lang:'语言',parent:'父类',domain:'所属类型',range:'目标类型',active:'启用状态',
      affected_records:'受影响记录',affected_assertions:'受影响断言',dependencies:'依赖',dependency_count:'依赖数量',record_count:'记录数量',assertion_count:'断言数量',count:'数量',descendants:'子类',references:'引用',entity_count:'实体数量',relation_count:'关系数量',attribute_count:'属性数量'};
    function render(state){
      const locked=state.busy||state.loading||state.uncertain,groups=controller.visible(),active=state.groups.find(g=>g.iri===state.activeIri);
      const pending=state.groups.filter(g=>g.pending.length).length,total=state.plan?.items.length||0,done=state.plan?.items.filter(finalDecision).length||0;
      const eligible=state.plan?.items.filter(i=>!i.decision&&i.auto_approvable===true).length||0;
      dialog.setAttribute('aria-busy',String(state.busy||state.loading));
      summary.textContent=state.draft?`${state.groups.length} 个定义 · ${total} 项变更；${pending} 个定义待审核，${done}/${total} 项变更已决定。审核保存后仍需发布草案。`:'正在读取草案与审核条件…';
      const gateReason=state.stage?.stages?.review?.allowed===false?state.stage.stages.review.reason||'当前审核条件未满足，请返回草案处理后重新读取。':'';
      feedback.textContent=state.busy?'正在保存审核决定，请稍候…':state.loading?'正在读取最新审核结果…':state.error||gateReason||'按一个类型、关系或属性集中核对；批量批准只处理服务端判定符合条件的变更。';
      feedback.classList.toggle('is-error',!!state.error);filter.value=state.filter;filter.disabled=state.busy||state.loading;
      bulk.textContent=`批量批准（${eligible} 项变更）`;bulk.disabled=locked||!eligible||state.stage?.stages?.review?.allowed!==true;
      retry.disabled=state.busy||state.loading;list.replaceChildren();
      for(const group of groups){
        const row=button('',()=>controller.select(group.iri),'odr-row');row.disabled=state.busy||state.loading;row.setAttribute('aria-current',String(group.iri===state.activeIri));
        row.append(el('small',null,group.kind),el('strong',null,group.label),el('span',null,group.pending.length?`${group.pending.length} 项待审核${group.blocked?' · 有阻断':group.exception?' · 需核对':''}`:`已完成 · 批准 ${group.approved} / 不批准 ${group.rejected}`));list.append(row);
      }
      previous.disabled=locked||groups.findIndex(g=>g.iri===state.activeIri)<=0;following.disabled=locked||groups.findIndex(g=>g.iri===state.activeIri)>=groups.length-1;
      position.textContent=groups.length?`${groups.findIndex(g=>g.iri===state.activeIri)+1} / ${groups.length}`:'0 / 0';
      approve.disabled=approveNext.disabled=locked||!active?.pending.length||active.blocked||state.stage?.stages?.review?.allowed!==true;
      reject.disabled=rejectNext.disabled=locked||!active?.pending.length||state.stage?.stages?.review?.allowed!==true;
      if(shownIri!==state.activeIri||shownRevision!==state.draft?.revision){reasonValue='';acknowledged=new Set();shownIri=state.activeIri;shownRevision=state.draft?.revision;}
      detail.replaceChildren();
      if(!active){detail.append(el('div','odr-empty',state.loading?'正在加载定义…':state.error?'读取失败，点击「重新读取」重试。':'当前范围没有待处理的定义，可切换范围查看已完成内容。'));return;}
      detail.append(el('small','odr-kicker',active.kind),el('h3',null,active.label),el('p','odr-description',`此定义共有 ${active.items.length} 项变更，${active.pending.length} 项待决定；一次审核会处理本定义全部待决定的变更。`));
      const reasonLabels={medium_risk:'中风险变更：请核对定义与适用范围',high_risk:'高风险变更：需要理由与人工确认',
        irreversible_action:'涉及停用等影响较大的操作',rebase_conflict:'与当前基线冲突，请先处理冲突',blocking_error:'校验未通过，请先修正相关问题',
        operation_warning:'此操作有警告，请确认影响',draft_warning_requires_acknowledgement:'草案有警告，需要逐项确认并填写理由',requires_review:'需要人工核对'};
      const riskReasons=[...new Set(active.items.flatMap(i=>i.reason_codes||[]))].filter(code=>code!=='low_risk_no_warning');
      if(riskReasons.length)detail.append(el('p','odr-risk-reasons',riskReasons.map(code=>reasonLabels[code]||code).join('；')+'。'));
      const warnings=new Map();
      for(const warning of [...(state.plan.warnings||[]),...active.items.flatMap(i=>i.warnings||[])]){
        const key=warning.code||warning.message;
        if(!warnings.has(key))warnings.set(key,{code:warning.code,messages:new Set()});
        warnings.get(key).messages.add(warning.message||warning.code);
      }
      for(const warning of warnings.values()){
        const label=el('label','odr-warning');
        if(warning.code){const check=el('input');check.type='checkbox';check.checked=acknowledged.has(warning.code);check.disabled=locked||!active.pending.length;
          check.addEventListener('change',()=>{if(check.checked)acknowledged.add(warning.code);else acknowledged.delete(warning.code);});label.append(check);}
        label.append(el('span',null,[...warning.messages].join('；')));detail.append(label);
      }
      const changes=el('div','odr-changes');
      for(const item of active.items){
        const operation=(state.draft.operations||[]).find(op=>op.id===item.operation_id),change=el('section','odr-change');
        change.append(el('h4',null,actions[item.action]||item.action),el('span','odr-change-state',finalDecision(item)?item.decision.action==='approve'?'已批准':'已不批准':item.approvable?'待审核':'校验阻断'));
        for(const error of item.errors||[])change.append(el('p','odr-error',error.message||error.code));
        if(item.risk==='high')change.append(el('p','odr-error','高风险变更：请核对影响并填写理由。'));
        if(operation){const pair=el('dl','odr-values');for(const [key,label] of [['before','修改前'],['after','修改后']])pair.append(el('dt',null,label),el('dd',null,display(operation[key],state.draft)));change.append(pair);}
        const evidence=operation?.evidence;
        for(const reference of (Array.isArray(evidence)?evidence:evidence?[evidence]:[])){
          const text=typeof reference==='string'?'证据引用：'+reference:reference.quote||reference.text||reference.title||reference.document_id||reference.assertion_id;
          if(text)change.append(el('blockquote','odr-evidence',text));
        }
        const impact=item.impact||operation?.impact||{};
        if(Object.keys(impact).length)change.append(el('p','odr-impact','影响范围：'+display(impact,state.draft)));
        if(item.decision?.reason)change.append(el('p',null,'处理理由：'+item.decision.reason));
        changes.append(change);
      }
      detail.append(changes);
      if(active.pending.length){
        detail.append(el('label','odr-reason-label',active.requiresReason?'处理理由（批准与不批准均必填）':'处理理由（不批准时必填）'));
        reason.value=reasonValue;reason.disabled=locked;detail.append(reason);
      }
      const technical=el('details','odr-technical');technical.append(el('summary',null,'技术详情（标识与原始操作）'),el('p',null,active.iri),el('pre',null,JSON.stringify(active.items.map(item=>({review:item,operation:(state.draft.operations||[]).find(op=>op.id===item.operation_id)})),null,2)));detail.append(technical);
    }
    const controller=createController({getContext:()=>{
      const context=getContext();return {...context,preview:context.preview||!dialog.open||!dialog.isConnected};
    },request,onSaved,onChange:render});
    filter.addEventListener('change',()=>controller.setFilter(filter.value));
    dialog.addEventListener('keydown',event=>{
      const editing=/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)||event.target.isContentEditable;
      if(event.altKey&&!editing&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();controller.navigate(event.key==='ArrowLeft'?-1:1);}
    });
    dialog.addEventListener('close',()=>{controller.close();dialog.remove();},{once:true});
    host.append(dialog);dialog.showModal();void controller.load();return {dialog,controller};
  }
  return {groupDefinitions,createController,open};
});
