/* Explicit candidate intake. Reading and previewing never prepare a draft. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.OntologyIntake=api.createUI(root);
})(typeof window==='object'?window:globalThis,()=>{
  const clone=value=>JSON.parse(JSON.stringify(value));
  const same=(a,b)=>['project','draftId','revision','ontologyId','preview'].every(key=>(a[key]??null)===(b[key]??null));
  const termKind=item=>({entity:'class',relation:'relation',attribute:'attribute'})[item?.kind];
  const awaitingDraft=item=>!item?.materialized&&!!item?.linked_drafts?.some(link=>link.status==='draft_created');
  function previewSummary(preview){
    const facts=[...new Map((preview.associated_fact_scope||[]).map(item=>[item.id,item])).values()];
    const definitions=new Map((preview.suggestions||[]).filter(item=>item.action==='create_term').map(item=>[item.target_iri,item.after?.kind||item.kind]));
    const count=(rows,kind)=>rows.filter(item=>item===kind).length;
    return {selected:facts.length,entities:count(facts.map(item=>item.kind),'entity'),relations:count(facts.map(item=>item.kind),'relation'),attributes:count(facts.map(item=>item.kind),'attribute'),
      newClasses:count([...definitions.values()],'class'),newRelations:count([...definitions.values()],'relation'),newAttributes:count([...definitions.values()],'attribute'),
      definitions:definitions.size,operations:(preview.suggestions||[]).length};
  }
  function createController({getContext,request,uid,canWrite=()=>true,getActor=()=> 'system:auth-disabled',storage=null,requireDurable=false,onChange=()=>{},onSaved=async()=>{}}){
    const state={open:false,scope:{},items:[],terms:[],selected:[],mappings:{},target:'',targetRevision:null,total:0,offset:0,
      ontologyId:null,preview:null,receipt:null,receiptSummary:null,pending:null,busy:false,activity:null,error:null,filters:{status:'pending',document:'',kind:'',type:''},detailId:null,scopeNote:null};
    let generation=0,previewContext=null,sessionReady=false;
    const pendingByProject=new Map();
    const sessions=new Map();
    const sessionKey=scope=>JSON.stringify([getActor(),getContext().project,scope.documentId||'',scope.batchId||'',scope.runId||'',...(scope.candidateIds||[]).slice().sort()]);
    const recoveryKey=(project,actor=getActor())=>'knowledge:ontology-intake:pending:v1:'+encodeURIComponent(actor)+':'+encodeURIComponent(project);
    function readPending(project){
      const key=recoveryKey(project);if(pendingByProject.has(key))return pendingByProject.get(key);
      const saved=storage?.getItem(key);if(!saved)return null;
      let record;try{record=JSON.parse(saved);}catch{throw Error('原请求恢复记录无法读取，请先核对原草案或知识结果，避免重复确认。');}
      if(record.version!==1||record.project!==project||record.actor!==getActor()||!['/add-selection','/confirm-reuse'].includes(record.path)
        ||!Array.isArray(record.body?.candidate_ids)||!record.body.candidate_ids.length||!record.body.idempotency_key||!record.body.preview_fingerprint||!record.body.source_fingerprint)
        throw Error('原请求恢复记录不完整，请核对原草案或知识结果后再处理。');
      pendingByProject.set(key,record);return record;
    }
    function remember(record){
      const key=recoveryKey(record.project,record.actor);
      if(requireDurable&&!storage)throw Error('无法保存请求恢复记录，请允许浏览器会话存储后重试。');
      try{storage?.setItem(key,JSON.stringify(record));}catch{throw Error('请求恢复记录保存失败，尚未发送确认；请检查浏览器会话存储后重试。');}
      pendingByProject.set(key,record);
    }
    function forget(record){const key=recoveryKey(record.project,record.actor);pendingByProject.delete(key);try{storage?.removeItem(key);}catch{/* A retained receipt can be safely replayed with the original identity. */}}
    const changed=()=>onChange(state);
    const editable=()=>{if(state.busy)throw Error('正在处理，请稍候。');if(state.pending)throw Error('上次请求结果尚未确认，请先重试原请求。');};
    const invalidate=()=>{state.preview=null;state.receipt=null;previewContext=null;state.error=null;};
    function assertScope(snapshot){
      const now=getContext();
      if(now.preview)throw Error('历史版本只读，请回到当前工作区。');
      if(!same(snapshot,now))throw Error('项目、工作草案、修订或正式发布已变化，请重新读取并预览。');
    }
    async function list(append=false){
      const serial=generation,project=getContext().project;
      let path='/candidates?offset='+state.offset+'&limit=100';
      for(const [key,param] of [['documentId','document_id'],['batchId','batch_id'],['runId','run_id']])if(state.scope[key])path+='&'+param+'='+encodeURIComponent(state.scope[key]);
      const data=await request(path,undefined,'GET');
      if(serial!==generation||getContext().project!==project||!state.open)return;
      if((state.scope.batchId||state.scope.runId)&&(typeof data.scope_note!=='string'||!data.scope_note.trim()))throw Error('当前服务无法核对这个处理范围，暂未展示候选。请更新服务后重试。');
      const beforeSelection=JSON.stringify(state.selected),beforeOntology=state.ontologyId;
      let rows=data.items||[];
      if(state.scope.candidateIds?.length)rows=rows.filter(item=>state.scope.candidateIds.includes(item.id)
        ||(item.kind==='entity'&&(item.required_by||[]).some(id=>state.scope.candidateIds.includes(id))));
      state.items=append?[...state.items,...rows]:rows;state.total=data.total??rows.length;
      state.offset+=(data.items||[]).length;state.terms=data.published_terms||[];state.ontologyId=data.expected_ontology_id??null;
      state.scopeNote=data.scope_note||null;
      const pinned=state.scope.candidateIds||[];
      const selectable=new Set(state.items.filter(item=>!awaitingDraft(item)).map(item=>item.id));
      state.selected=[...new Set([...state.selected,...rows.filter(item=>pinned.includes(item.id)).map(item=>item.id)])].filter(id=>selectable.has(id));
      state.mappings=Object.fromEntries(Object.entries(state.mappings).filter(([id])=>state.selected.includes(id)));
      if(append&&(beforeSelection!==JSON.stringify(state.selected)||beforeOntology!==state.ontologyId))invalidate();
    }
    function body(){
      if(!state.selected.length)throw Error('请先选择要处理的候选。');
      if(state.items.some(item=>state.selected.includes(item.id)&&awaitingDraft(item)))throw Error('候选已加入草案，请查看关联草案继续审核，无需重复加入。');
      if(!state.target)throw Error('请明确选择目标草案、另建草案或仅确认复用。');
      if(state.target==='reuse'&&state.selected.some(id=>!state.mappings[id]))throw Error('请为每个选中候选明确确认正式术语映射。');
      const existing=!['new','reuse'].includes(state.target);
      return {candidate_ids:[...state.selected].sort(),expected_ontology_id:state.ontologyId,
        target_draft_id:existing?state.target:null,expected_revision:existing?state.targetRevision:null,
        create_new_draft:state.target==='new',name:'本轮结构补充',confirmed_mappings:{...state.mappings},infer_hierarchy:false};
    }
    async function work(action,activity='loading'){const serial=generation;state.busy=true;state.activity=activity;state.error=null;changed();try{return await action();}
      catch(error){if(serial===generation)state.error=error.message||String(error);throw error;}finally{if(serial===generation){state.busy=false;state.activity=null;changed();}}}
    const controller={state,
      hasPending:project=>!!readPending(project),
      async open(scope={}){
        const context=getContext();if(!context.project)throw Error('请先选择项目。');if(context.preview)throw Error('历史版本只读，请先回到最新。');
        if(scope.projectId&&scope.projectId!==context.project)throw Error('项目已变化，请在原项目重新打开。');
        sessionReady=false;const pending=readPending(context.project);
        if(pending){state.pending=pending;state.scope={...pending.scope};state.open=true;state.error=null;state.ontologyId=pending.body.expected_ontology_id;changed();return state;}
        const saved=sessions.get(sessionKey(scope)),serial=++generation;
        Object.assign(state,{open:true,scope:{...scope},items:[],terms:[],selected:[],mappings:{},
          target:context.draftId||'',targetRevision:context.revision??null,total:0,offset:0,preview:null,receipt:null,receiptSummary:null,pending:null,error:null});
        state.filters=saved?.filters||{status:'pending',document:'',kind:'',type:''};state.detailId=saved?.detailId||null;state.scopeNote=null;
        previewContext=null;await work(async()=>{
          await list();
          while(saved&&serial===generation&&state.open&&state.offset<Math.min(saved.offset,state.total)&&state.offset<10000){
            const before=state.offset;await list(true);if(state.offset<=before)break;
          }
          if(saved&&serial===generation&&state.open){
            const ids=new Set(state.items.filter(item=>!awaitingDraft(item)).map(item=>item.id));state.selected=saved.selected.filter(id=>ids.has(id));
            state.mappings=Object.fromEntries(Object.entries(saved.mappings).filter(([id,iri])=>state.selected.includes(id)&&state.terms.some(term=>term.iri===iri&&term.kind===termKind(state.items.find(item=>item.id===id)))));
            state.target=saved.target;state.targetRevision=saved.target===context.draftId?context.revision:saved.targetRevision;
          }
        });if(serial===generation&&state.open)sessionReady=true;return state;
      },
      close(){if(sessionReady&&state.open&&!state.pending&&!state.receipt)sessions.set(sessionKey(state.scope),clone({selected:state.selected,mappings:state.mappings,target:state.target,targetRevision:state.targetRevision,offset:state.offset,filters:state.filters,detailId:state.detailId}));sessionReady=false;state.open=false;state.busy=false;state.activity=null;generation++;invalidate();changed();},
      back(){editable();invalidate();changed();},
      async more(){editable();await work(()=>list(true));},
      select(id,on){return controller.selectMany([id],on);},
      selectMany(ids,on){editable();const loaded=new Set(state.items.map(item=>item.id));
        if(ids.some(id=>!loaded.has(id)))throw Error('候选不在当前已读范围。');
        if(on&&state.items.some(item=>ids.includes(item.id)&&awaitingDraft(item)))throw Error('候选已加入草案，请查看关联草案继续审核，无需重复加入。');
        const next=on?[...new Set([...state.selected,...ids])]:state.selected.filter(id=>!ids.includes(id));
        if(next.length>10000)throw Error('一次最多处理 10000 条候选，请缩小范围。');
        state.selected=next;if(!on)for(const id of ids)delete state.mappings[id];invalidate();changed();},
      useSuggestedMappings(ids=state.selected){editable();
        for(const item of state.items){if(!ids.includes(item.id)||!state.selected.includes(item.id)||state.mappings[item.id])continue;
          const iri=item.suggested_mapping?.target_iri;
          if(iri&&state.terms.filter(term=>term.iri===iri&&term.kind===termKind(item)).length===1)state.mappings[item.id]=iri;
        }invalidate();changed();},
      mapping(id,iri){editable();if(iri&&!state.terms.some(term=>term.iri===iri))throw Error('只能明确映射到当前正式发布的术语。');
        if(iri&&!state.terms.some(term=>term.iri===iri&&term.kind===termKind(state.items.find(item=>item.id===id))))throw Error('候选与正式术语的种类不匹配，请选择对应类型。');
        if(iri)state.mappings[id]=iri;else delete state.mappings[id];invalidate();changed();},
      target(id,revision){editable();state.target=id||'';state.targetRevision=revision??(getContext().draftId===id?getContext().revision:null);invalidate();changed();},
      async preview(){editable();const snapshot=getContext();if(snapshot.preview)throw Error('历史版本只读。');
        if(snapshot.ontologyId!==state.ontologyId)throw Error('正式发布已变化，请重新读取候选。');const payload=body(),serial=generation;
        return work(async()=>{const result=await request('/selection-preview',payload,'POST');assertScope(snapshot);
          if(serial!==generation||!state.open)throw Error('抽屉已关闭，请重新预览。');state.preview=result;previewContext=snapshot;return result;},'preview');},
      async apply(kind){
        if(!state.open)throw Error('核对工作区已关闭，请重新打开并预览。');
        if(state.busy)throw Error('正在处理，请稍候。');
        if(!canWrite())throw Error('当前账号没有确认写入权限，请联系管理员。');
        if(!state.pending&&readPending(getContext().project)){state.pending=readPending(getContext().project);changed();throw Error('上次请求结果尚未确认，请重试原请求核对。');}
        const path=kind==='reuse'?'/confirm-reuse':'/add-selection';
        const replaying=!!state.pending;
        if(state.pending){if(state.pending.project!==getContext().project||state.pending.actor!==getActor()||getContext().preview)throw Error('项目、登录身份已变化或正在历史只读预览。');
          if(state.pending.path!==path)throw Error('请重试原来的确认动作。');}
        else{
          if(!state.preview||!previewContext)throw Error('请先预览选中的候选。');assertScope(previewContext);
          if(kind==='reuse'?!state.preview.can_confirm_reuse:!state.preview.can_add)throw Error('预览仍有阻断项，请先解决。');
          const payload={...body(),preview_fingerprint:state.preview.preview_fingerprint,source_fingerprint:state.preview.source_fingerprint,
            idempotency_key:uid(),selected_suggestion_fingerprints:null};
          const scope=Object.fromEntries(['projectId','documentId','documentTitle','batchId','runId','candidateIds'].filter(key=>state.scope[key]!==undefined).map(key=>[key,state.scope[key]]));
          const pending={version:1,project:getContext().project,actor:getActor(),path,body:clone(payload),scope,workspace:{...getContext()},summary:previewSummary(state.preview)};
          remember(pending);state.pending=pending;
        }
        const pending=state.pending;
        return work(async()=>{
          try{
            const result=await request(pending.path,clone(pending.body),'POST');
            if(getContext().project!==pending.project||getActor()!==pending.actor)throw Error('项目或登录身份已变化；请返回原项目和账号，用同一请求核对结果。');
            forget(pending);sessions.delete(sessionKey(pending.scope));
            if(state.pending!==pending)return result;
            state.pending=null;state.receipt=result.receipt||null;state.receiptSummary=pending.summary||null;state.preview=null;previewContext=null;const savedGeneration=generation;
            state.activity='refresh';changed();
            try{await onSaved(result,pending);}catch(error){if(savedGeneration===generation&&getContext().project===pending.project&&getActor()===pending.actor)state.error='操作已保存，但页面刷新失败：'+(error.message||error)+'。请重新读取，避免重复确认。';}
            return result;
          }catch(error){
            // Definite rejections are safe to revise. Network/timeout outcomes keep the exact request for replay.
            if(error.status>=400&&error.status<500&&!(replaying&&[401,403,429].includes(error.status))){forget(pending);if(state.pending===pending){state.pending=null;state.preview=null;previewContext=null;}}
            throw error;
          }
        },'apply');
      },
    };return controller;
  }
  function receiptText(receipt){
    if(!receipt)return '尚未返回回执。';
    const facts=receipt.facts||{},n=value=>value===undefined?'待核对':String(value);
    const index=receipt.index||{},status={not_requested:'未请求索引',queued:'索引排队中',pending:'索引待处理',running:'索引同步中',completed:'索引同步完成',succeeded:'索引同步完成',failed:'索引同步失败'};
    if(receipt.structure)return `已加入工作草案 · 修订 ${receipt.revision}；关联候选知识 ${n(facts.selected)} 条，待审核发布，尚未正式入库。`;
    return `知识新增 ${n(facts.added)} / 复用 ${n(facts.reused)} / 跳过 ${n(facts.skipped)}；${status[index.status]||index.status||'索引状态待核对'}。`;
  }
  function describeCandidate(item){
    if(!item)return '未知候选';
    if(item.kind==='relation')return `${item.subject||item.subject_id||'?'} → ${item.proposed_type||'关系'} → ${item.object||item.object_id||'?'}`;
    if(item.kind==='attribute')return `${item.subject||item.entity_id||'?'} · ${item.proposed_type||'属性'} = ${JSON.stringify(item.value)??'未提供值'}`;
    return `${item.text||item.id} · ${item.proposed_type||'类型待核对'}`;
  }
  function diagnosticText(message,items){
    if(typeof message==='string')return message;
    const byId=new Map(items.map(item=>[item.id,item]));
    const label=id=>byId.has(id)?`${describeCandidate(byId.get(id))}（${id}）`:`${id||'缺失身份'}（未在当前已读范围，请读取下一页或回来源核对）`;
    return [message.message||message.reason||message.code||'需核对',message.candidate_id?'候选：'+label(message.candidate_id):'',message.required_by?'被以下事实依赖：'+label(message.required_by):''].filter(Boolean).join(' · ');
  }
  const kindLabels={entity:'实体',class:'实体类',relation:'关系',attribute:'属性'};
  const valueLabels={'http://www.w3.org/2000/01/rdf-schema#label':'名称','http://www.w3.org/2000/01/rdf-schema#comment':'说明',
    'http://www.w3.org/2001/XMLSchema#string':'文本','http://www.w3.org/2001/XMLSchema#integer':'整数',
    'http://www.w3.org/2001/XMLSchema#decimal':'小数','http://www.w3.org/2001/XMLSchema#double':'浮点数',
    'http://www.w3.org/2001/XMLSchema#boolean':'布尔值','http://www.w3.org/2001/XMLSchema#date':'日期','http://www.w3.org/2001/XMLSchema#dateTime':'日期时间'};
  function changeValue(value,names){
    if(value===undefined)return '未提供';if(value===null)return '空值（null）';
    if(Array.isArray(value))return value.length?value.map(item=>changeValue(item,names)).join('、'):'空列表（[]）';
    if(typeof value==='object'){const fields={kind:'定义种类',predicate:'说明项',value:'值',language:'语言',datatype:'数据类型',type:'值种类'};
      return Object.entries(value).map(([key,item])=>(fields[key]||key)+'：'+(key==='kind'?(kindLabels[item]||changeValue(item,names)):changeValue(item,names))).join('\n')||'空对象（{}）';}
    return names.get(value)||valueLabels[value]||String(value);
  }
  function changeTitle(suggestion,names){
    const labels={create_term:'新增定义',add_annotation:'名称说明',remove_annotation:'移除名称说明',add_domain:'所属类',remove_domain:'移除所属类',
      add_range:'关系目标类',remove_range:'移除关系目标类',set_datatype:'数据类型',add_parent:'新增上级类',remove_parent:'移除上级类',retire_term:'停用定义',restore_term:'恢复定义'};
    const comment=(suggestion.after||suggestion.before)?.predicate==='http://www.w3.org/2000/01/rdf-schema#comment';
    const action=comment&&suggestion.action==='add_annotation'?'定义说明':labels[suggestion.action]||'结构变更（详见技术详情）';
    return action+' · '+(names.get(suggestion.target_iri)||suggestion.target_iri||'未提供目标');
  }
  function createUI(root){
    const doc=root.document,node=(tag,css,text)=>{const e=doc.createElement(tag);if(css)e.className=css;if(text!==undefined)e.textContent=String(text);return e;};
    const button=(text,fn)=>{const b=node('button','om-btn',text);b.type='button';b.onclick=()=>run(fn);return b;};
    let drawer=null,host=null,drafts=[],controller,openSerial=0,preparing=false;
    const context=()=>{const state=root.OntologyModel?.S||{};return {project:typeof current==='undefined'?null:current,draftId:state.draft?.status==='pending'?state.draft.id:null,
      revision:state.draft?.status==='pending'?state.draft.revision:null,ontologyId:state.ontologyId??null,preview:!!state.preview};};
    async function bounded(promise,message){let timer;
      try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(message)),25000);})]);}
      finally{clearTimeout(timer);}
    }
    async function request(path,body,method){
      const project=context().project;if(!project)throw Error('请先选择项目。');
      return bounded(api('/api/projects/'+encodeURIComponent(project)+'/ontology-discovery'+path,body,method),
        method==='GET'||path==='/selection-preview'?'读取或预览超时，请重试；尚未确认保存。':'请求超时；确认动作可能已完成，请用原请求重试核对。');
    }
    const run=action=>{const serial=openSerial;return Promise.resolve().then(action).catch(error=>{if(serial!==openSerial||!controller.state.open)return;if(!controller.state.error)controller.state.error=error.message;render();});};
    let storage;try{storage=root.sessionStorage;}catch{storage=null;}
    controller=createController({getContext:context,request,storage,requireDurable:true,getActor:()=>root.Auth?.user?.()?.id||root.Auth?.user?.()?.username||'system:auth-disabled',canWrite:()=>Boolean(root.Auth?.isAdmin?.()),uid:()=>root.OntologyModel.uid(),onChange:()=>render(),onSaved:async(result,pending)=>{
      const M=root.OntologyModel;if(result.draft&&same(pending.workspace,context()))M.S.draft=result.draft;
      await bounded(M.refresh(),'保存已完成，但页面更新超时。请重新读取草案核对，避免重复确认。');doc.dispatchEvent(new CustomEvent('ontology-intake:saved',{detail:{projectId:pending.project,receipt:result.receipt}}));
    }});
    function ensure(){if(drawer)return;
      host=node('div','ontology-intake');drawer=root.WorkspaceDrawers.create({id:'ontology-intake-drawer',title:'候选核对与入库',dismissOnBackdrop:false,
        description:'选择来源范围，批量核对类型和证据，再预览确认。',content:host,onClose:()=>{preparing=false;++openSerial;controller.close();}});
      drawer.dialog.classList.add('ontology-intake-drawer');
    }
    const sourceName=item=>item.document_title&&item.document_title!==item.document_id?item.document_title:'未命名资料';
    const visibleItems=s=>s.items.filter(item=>((s.filters.status||'pending')==='all'||awaitingDraft(item)===((s.filters.status||'pending')==='draft'))&&(!s.filters.document||item.document_id===s.filters.document)&&(!s.filters.kind||item.kind===s.filters.kind)&&(!s.filters.type||item.proposed_type===s.filters.type));
    const compatible=(s,item)=>s.terms.filter(term=>term.iri===item.suggested_mapping?.target_iri&&term.kind===termKind(item)).length===1;
    function renderGoals(s,locked){
      const target=node('section','oi-target');target.append(node('h3',null,'01 这些候选怎样入库？'));
      const choices=node('div','oi-goals');
      for(const [value,title,description] of [
        ['reuse','已有类型可用，直接确认知识','使用已经发布的类型和字段。核对映射并预览后，直接写入所选知识，不修改本体。'],
        ['structure','需要补充定义，先加入草案','新增或完善类型、关系和字段。确认后先进入草案，审核发布后才处理关联知识。']]){
        const label=node('label','oi-goal'+((value==='reuse')===(s.target==='reuse')?' is-active':'')),radio=node('input');radio.type='radio';radio.name='oi-goal';radio.value=value;
        radio.checked=(value==='reuse')===(s.target==='reuse');radio.disabled=locked||(value==='reuse'&&!s.ontologyId);
        radio.onchange=()=>run(()=>controller.target(value==='reuse'?'reuse':context().draftId||'new'));
        const text=node('span');text.append(node('strong',null,title),node('small',null,description));label.append(radio,text);choices.append(label);
      }target.append(choices);
      if(!s.ontologyId)target.append(node('p','subtle','当前尚未发布本体，请先把需要的定义加入草案；发布后才能确认正式知识。'));
      if(s.target!=='reuse'){
        const label=node('label','oi-draft-choice','保存到哪份草案？'),select=node('select');select.setAttribute('aria-label','候选处理目标');
        for(const [value,text] of [['','选择草案或新建草案'],...drafts.map(d=>[d.id,`${d.title||'工作草案'} · 修订 ${d.revision}`]),['new','新建草案（确认加入时才创建）']]){const o=node('option',null,text);o.value=value;select.append(o);}
        select.value=s.target;select.disabled=locked;select.onchange=()=>run(()=>controller.target(select.value,drafts.find(d=>d.id===select.value)?.revision));label.append(select);target.append(label);
        target.append(node('p','subtle',s.target==='new'?'只读预览不会创建草案；确认加入后仍需审核发布。':'所选候选将追加到这份草案，保留其中已有修改；审核发布后才处理关联知识。'));
      }else target.append(node('p','subtle','每条候选都要对应准确的已发布类型。可以批量采用已有建议，未匹配的候选仍需核对。'));
      host.append(target);
    }
    function renderFilters(s,locked){
      const filters=node('div','oi-filters');
      const documents=[...new Map(s.items.map(item=>[item.document_id,sourceName(item)])).entries()].filter(([id])=>id);
      const types=[...new Set(s.items.filter(item=>!s.filters.kind||item.kind===s.filters.kind).map(item=>item.proposed_type).filter(Boolean))];
      for(const [key,title,options] of [['status','处理状态',[['pending','待核对'],['draft','已加入草案 · 待发布'],['all','全部处理状态']]],['document','来源资料',documents],['kind','候选种类',[['entity','实体'],['relation','关系'],['attribute','属性']]],['type','建议类型',types.map(type=>[type,type])]]){
        const label=node('label',null,title),select=node('select');select.id='oi-filter-'+key;select.setAttribute('aria-label',title+'筛选');
        for(const [value,text] of key==='status'?options:[['','全部'+title],...options]){const option=node('option',null,text);option.value=value;select.append(option);}
        select.value=s.filters[key];select.disabled=locked;select.onchange=()=>{s.filters[key]=select.value;if(key==='kind')s.filters.type='';render();doc.getElementById(select.id)?.focus();};label.append(select);filters.append(label);
      }host.append(filters,node('p','oi-filter-note','筛选和批量选择仅作用于已读取的候选。继续读取下一页后，可再次选择新增内容。'));
    }
    function renderDetail(item,s,locked){
      const detail=node('section','oi-detail');detail.setAttribute('aria-label','候选详情');detail.append(node('h4',null,'候选详情'));
      if(!item){detail.append(node('p','subtle','选择列表中的候选查看证据与类型映射。'));return detail;}
      detail.append(node('strong','oi-detail-name',describeCandidate(item)),node('p','oi-source','来源：'+sourceName(item)));
      if(item.materialized)detail.append(node('p','oi-dependency-note','已写入正式知识 · 本次可明确选择为依赖并复用，仍需确认准确正式术语映射。'));
      if(item.dependency_candidate){const labels=(item.required_by||[]).map(id=>s.items.find(row=>row.id===id)).filter(Boolean).map(describeCandidate);
        detail.append(node('p','subtle',`关联依赖：${labels.slice(0,3).join('；')||'所选关系或属性'}${labels.length>3?' 等 '+labels.length+' 条':''}。选择此实体后才会进入本次确认范围。`));}
      const links=item.linked_drafts||[];detail.append(node('p','subtle',links.length?'已关联 '+links.length+' 份草案；加入草案不等于已写入知识。':'待核对，尚未加入草案。'));
      if(awaitingDraft(item))detail.append(node('p','oi-dependency-note','已加入草案，发布前不能重复加入。请查看关联草案继续审核。'));
      for(const link of links.filter(link=>link.status==='draft_created'))detail.append(button('查看关联草案',async()=>{const project=context().project;await root.OntologyModel.selectDraft(link.draft_id);if(context().project===project)drawer.close();}));
      if(item.evidence){const evidence=node('details','oi-evidence');evidence.open=true;evidence.append(node('summary',null,'来源证据'),node('pre',null,typeof item.evidence==='string'?item.evidence:JSON.stringify(item.evidence,null,2)));detail.append(evidence);}
      if(s.selected.includes(item.id)){
        const label=node('label','oi-mapping-label','确认已有类型（可选；直接入库时必填）'),mapping=node('select');mapping.setAttribute('aria-label','确认正式术语映射：'+(item.text||item.id));
        const blank=node('option',null,'选择对应的已发布类型');blank.value='';mapping.append(blank);
        for(const term of s.terms.filter(term=>term.kind===termKind(item))){const option=node('option',null,term.label||term.iri);option.value=term.iri;mapping.append(option);}
        mapping.value=s.mappings[item.id]||'';mapping.disabled=locked;mapping.onchange=()=>run(()=>controller.mapping(item.id,mapping.value));label.append(mapping);detail.append(label);
        if(item.suggested_mapping)detail.append(node('p','subtle','建议对应：'+(item.suggested_mapping.label||item.suggested_mapping.target_iri)+'。批量采用或手动选择后才计入确认。'));
      }
      if(item.document_id)detail.append(button('返回来源文档',async()=>{const project=context().project;drawer.close();await root.KnowledgeNavigation.open('sources',{projectId:project});if(context().project===project)await root.selectSource(item.document_id);}));
      const technical=node('details','oi-technical');technical.append(node('summary',null,'技术信息'),node('code','oi-candidate-id','候选：'+item.id),node('code','oi-candidate-id','来源修订：'+(item.document_version_id||'未记录')));
      if(links.length)technical.append(node('pre',null,JSON.stringify(links,null,2)));detail.append(technical);return detail;
    }
    function renderPreview(s,locked){const p=s.preview,stats=previewSummary(p),box=node('section','oi-preview');box.append(node('h3',null,'03 核对预览并确认'),node('p',null,`${s.target==='reuse'?'确认后写入所选知识':s.target==='new'?'确认后新建草案，等待审核发布':'确认后加入所选草案，等待审核发布'} · 关联知识 ${(p.associated_fact_scope||[]).length} 条`),node('p','oi-preview-definition-summary',`新增定义：实体类型 ${stats.newClasses} 个 · 关系类型 ${stats.newRelations} 个 · 属性 ${stats.newAttributes} 个`),node('p','oi-preview-fact-summary',`候选知识 ${stats.selected} 条：实体 ${stats.entities} 条 · 关系 ${stats.relations} 条 · 属性 ${stats.attributes} 条`),node('p','subtle','定义是类型与字段的规则；候选知识是从原文抽出的实体、关系或属性陈述，均不是段落数。'));
        const names=new Map(s.terms.map(term=>[term.iri,term.label||term.iri]));
        for(const binding of [...p.new||[],...p.reused||[]]){const item=s.items.find(row=>row.id===binding.candidate_id);if(item?.proposed_type)names.set(binding.target_iri,item.proposed_type);}
        const detail=node('details','oi-preview-details');detail.open=false;detail.append(node('summary',null,`查看定义修改明细（${new Set((p.suggestions||[]).map(item=>item.target_iri)).size} 个定义）`),node('p','subtle',`${stats.operations} 项底层操作：创建、名称、说明和约束分别计数；不是新增知识条数。`));
        for(const suggestion of p.suggestions||[]){const d=node('details','oi-diff'),technical=node('details','oi-technical');
          technical.append(node('summary',null,'技术详情（原始操作与 RDF 值）'),node('pre',null,JSON.stringify(suggestion,null,2)));
          d.append(node('summary',null,changeTitle(suggestion,names)),node('pre','oi-change-values','修改前\n'+changeValue(suggestion.before,names)+'\n修改后\n'+changeValue(suggestion.after,names)),technical);detail.append(d);}box.append(detail);
        const facts=p.associated_fact_scope||[],items=[...s.items,...facts];
        const outcomes=new Map((p.fact_outcomes||[]).map(item=>[item.candidate_id,item]));
        const outcomeLabels={add:'预计新增',reuse:'预计复用',skip:'预计跳过',pending:'等待结构获批',blocked:'暂不能处理'};
        const counts={};for(const outcome of outcomes.values())counts[outcome.outcome]=(counts[outcome.outcome]||0)+1;
        box.append(node('p','oi-outcome-summary',Object.entries(counts).map(([key,count])=>(outcomeLabels[key]||'待核对')+' '+count+' 条').join(' · ')));
        const factSection=node('details','oi-fact-preview');factSection.open=facts.length<=10;factSection.append(node('summary',null,'逐条关联知识预期结果'));
        for(const fact of facts){
          const outcome=outcomes.get(fact.id),row=node('article','oi-fact-row');
          row.append(node('strong',null,describeCandidate(fact)),
            node('p',outcome?.outcome==='skip'||outcome?.outcome==='blocked'?'oi-error':'oi-fact-outcome',
              outcome?`${outcomeLabels[outcome.outcome]||'未判定'} · ${outcome.message||outcome.reason_code||'未提供判定原因'}`:'服务尚未提供此事实的预期结果，请重新预览。'));
          if(outcome?.reason_code)row.append(node('small','subtle','原因代码：'+outcome.reason_code));
          factSection.append(row);
        }box.append(factSection);
        const completeFacts=facts.length===s.selected.length&&s.selected.every(id=>facts.some(item=>item.id===id)&&outcomeLabels[outcomes.get(id)?.outcome]);
        if(!completeFacts)box.append(node('p','oi-error','逐条事实范围或预期结果不完整，暂不能确认。请重新读取并预览。'));
        for(const key of ['conflicts','warnings','missing_dependencies'])for(const message of p[key]||[])box.append(node('p',key==='warnings'?'oi-warning':'oi-error',diagnosticText(message,items)));
        const reuse=s.target==='reuse',apply=button(reuse?'确认写入所选知识':'确认加入草案，等待发布',()=>controller.apply(reuse?'reuse':'add'));apply.classList.add('primary');apply.disabled=locked||!completeFacts||!(reuse?p.can_confirm_reuse:p.can_add);const footer=node('div','oi-confirmbar'),back=button('返回调整选择',()=>controller.back());back.disabled=locked;footer.append(back,node('span',null,reuse?'确认后写入所选知识':'本次只加入草案，知识仍待审核发布'),apply);box.append(footer);host.append(box);
      }
    function progressCard(title,description){
      const box=node('section','oi-progress');box.setAttribute('role','status');box.setAttribute('aria-live','polite');
      const spinner=node('span','oi-spinner');spinner.setAttribute('aria-hidden','true');box.append(spinner,node('h3',null,title),node('p',null,description));return box;
    }
    function render(){if(!host||preparing)return;const s=controller.state;
      const oldScroll=host.querySelector?.('.oi-list-pane')?.scrollTop||0,focused=doc.activeElement?.id;
      host.replaceChildren();
      const current=context(),locked=s.busy||!!s.pending,scope=s.scope;
      const scoped=!!(scope.documentId||scope.batchId||scope.runId||scope.candidateIds?.length);drawer.dialog.classList.toggle?.('oi-workspace',!scoped);
      const resultMode=!!s.receipt||s.busy&&['apply','refresh'].includes(s.activity);drawer.dialog.classList.toggle?.('oi-result-dialog',resultMode);host.setAttribute('aria-busy',String(s.busy));
      host.append(node('p','oi-context',`${scope.runId?'本次处理':scope.batchId?'本批次':scope.documentId?'本文档':scope.candidateIds?.length?'所选候选':'当前项目'}${scope.documentTitle?' · '+scope.documentTitle:''} · ${s.ontologyId?'已有正式本体':'本体尚未发布'}`));
      if(!resultMode)host.append(node('p','oi-scope',scope.runId?'仅核对此次处理对应的当前候选；旧结果已被重跑替换时不会混入新结果。':scope.documentId?'显示这份资料当前的候选；资料处理完成后，仍需核对候选才能成为正式知识。':scope.batchId?'只显示本批次成功处理且来源身份可核对的候选。':'当前项目的候选池。可按来源、种类或建议类型筛选后批量核对。'));
      if(s.scopeNote)host.append(node('p','oi-warning',s.scopeNote));
      if(s.error){const e=node('p','oi-error',s.error);e.setAttribute('role','alert');host.append(e);}
      if(s.busy&&['preview','apply','refresh'].includes(s.activity)){
        const reuse=s.target==='reuse'||s.pending?.path==='/confirm-reuse';
        host.append(progressCard(s.activity==='preview'?'正在核对所选候选':s.activity==='refresh'?'已保存，正在同步页面':reuse?'正在确认知识入库':'正在加入工作草案',
          s.activity==='preview'?`已选 ${s.selected.length} 条候选，正在校对类型、来源和处理结果。`:s.activity==='refresh'?'服务已返回保存结果，正在更新草案与数量。':`正在提交 ${s.pending?.body.candidate_ids.length??s.selected.length} 条候选，请稍候，重复提交已停用。`));return;
      }
      if(s.pending){host.append(node('p','oi-warning',`上次确认结果未知。原请求选中 ${s.pending.body.candidate_ids.length} 条候选，目标 ${s.pending.body.target_draft_id||(s.pending.path==='/confirm-reuse'?'仅复用正式术语':'确认时新建草案')}，正式基线 ${s.pending.body.expected_ontology_id||'空基线'}。已恢复原请求身份；只有点击下方按钮才重试核对。`));
        const retry=button('重试原请求并核对回执',()=>controller.apply(s.pending.path==='/confirm-reuse'?'reuse':'add'));retry.disabled=s.busy;host.append(retry);return;}
      if(s.receipt){const receipt=node('section','oi-receipt');receipt.setAttribute('role','status');
        const heading=node('h3'),icon=node('span','oi-result-icon','✓');icon.setAttribute('aria-hidden','true');heading.append(icon,node('span',null,s.receipt.structure?'已加入草案，等待审核发布':'知识确认已完成'));receipt.append(heading,node('p',null,receiptText(s.receipt)));
        const stats=s.receiptSummary;
        if(stats&&stats.selected===s.receipt.facts?.selected)receipt.append(node('p','oi-receipt-counts',`实体 ${stats.entities} 条 · 关系 ${stats.relations} 条 · 属性 ${stats.attributes} 条`));
        if(s.receipt.structure)receipt.append(node('p','oi-next-step','下一步：查看草案 → 校验并审核 → 填写发布说明 → 发布。'));
        for(const [index,row] of (s.receipt.facts?.per_batch||[]).entries())receipt.append(node('p','subtle',`来源处理 ${index+1} · 新增 ${row.added??'待核对'} / 复用 ${row.reused??'待核对'} / 跳过 ${row.skipped??'待核对'}`));
        const technical=node('details','oi-technical');technical.append(node('summary',null,'查看技术回执'));technical.ontoggle=()=>{if(technical.open&&technical.children.length===1)technical.append(node('pre',null,JSON.stringify(s.receipt,null,2)));};receipt.append(technical);
        if(s.receipt.draft_id){const view=button('查看这份工作草案',async()=>{await root.OntologyModel.selectDraft(s.receipt.draft_id);drawer.close();});view.classList.add('primary');receipt.append(view);}
        receipt.append(button('重新读取待处理候选',()=>controller.open(scope)));host.append(receipt);return;}
      if(s.preview){renderPreview(s,locked);return;}
      renderGoals(s,locked);
      const visible=visibleItems(s),title=node('div','oi-section-heading');title.append(node('h3',null,'02 批量选择候选'),node('span',null,`当前列表 ${visible.length} 条 · 全部已选 ${s.selected.length} 条 · 已读 ${s.offset} / ${s.total}`));host.append(title);renderFilters(s,locked);
      const selectable=visible.filter(item=>!awaitingDraft(item));
      const tools=node('div','oi-bulk-tools'),selectAll=button(`选择当前列表（${selectable.length} 条）`,()=>controller.selectMany(selectable.map(item=>item.id),true)),clear=button('清空全部选择',()=>controller.selectMany([...s.selected],false));
      selectAll.disabled=locked||!selectable.length;clear.disabled=locked||!s.selected.length;tools.append(selectAll,clear);
      const suggestions=s.items.filter(item=>s.selected.includes(item.id)&&!s.mappings[item.id]&&compatible(s,item));
      const suggested=button(`批量采用已有类型建议（${suggestions.length} 条）`,()=>controller.useSuggestedMappings());suggested.disabled=locked||!suggestions.length;tools.append(suggested);
      const dependencies=s.items.filter(item=>item.kind==='entity'&&!awaitingDraft(item)&&!s.selected.includes(item.id)&&(item.required_by||[]).some(id=>s.selected.includes(id)));
      if(dependencies.length){const supplement=button(`补选关联实体（${dependencies.length} 条）`,()=>controller.selectMany(dependencies.map(item=>item.id),true));supplement.disabled=locked;tools.append(supplement);}
      host.append(tools);
      const layout=node('div','oi-work-layout'),list=node('div','oi-list-pane');list.setAttribute('aria-label','候选列表');
      const groups=new Map();for(const item of visible){const key=item.document_id||'';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(item);}
      const active=visible.find(item=>item.id===s.detailId)||visible[0];if(active)s.detailId=active.id;
      for(const group of groups.values()){
        const heading=node('div','oi-source-heading');heading.append(node('strong',null,sourceName(group[0])),node('small',null,group.length+' 条'));
        const candidates=group.filter(item=>!awaitingDraft(item)),allSelected=candidates.length>0&&candidates.every(item=>s.selected.includes(item.id)),pick=button(allSelected?'取消本组选择':'选择本组',()=>controller.selectMany(candidates.map(item=>item.id),!allSelected));pick.disabled=locked||!candidates.length;heading.append(pick);list.append(heading);
        for(const item of group){const row=node('article','oi-candidate'+(s.selected.includes(item.id)?' is-selected':'')),label=node('label','oi-candidate-select'),input=node('input');input.type='checkbox';input.checked=s.selected.includes(item.id);input.disabled=locked||awaitingDraft(item);input.id='oi-pick-'+s.items.indexOf(item);input.setAttribute('aria-label','选择候选：'+describeCandidate(item));
          input.onchange=()=>run(()=>controller.select(item.id,input.checked));label.append(input,node('span','sr-only','选择候选'));row.append(label);
          const open=button(describeCandidate(item),()=>{s.detailId=item.id;render();});open.className='oi-open-detail';open.id='oi-detail-'+s.items.indexOf(item);open.setAttribute('aria-pressed',String(s.detailId===item.id));row.append(open,node('small','oi-kind',kindLabels[item.kind]||'候选'));
          row.append(node('small','oi-row-status',item.materialized?'已入库 · 依赖复用':awaitingDraft(item)?'已加入草案 · 待发布':s.mappings[item.id]?'已确认类型':'待核对'));list.append(row);
        }
      }
      if(!visible.length&&s.items.length)list.append(node('p','subtle',(s.filters.status||'pending')==='pending'?'当前筛选没有待核对候选。已加入草案的内容可切换到「已加入草案 · 待发布」查看。':'当前筛选没有匹配候选，可调整上方筛选。'));
      layout.append(list,renderDetail(active,s,locked));host.append(layout);list.scrollTop=oldScroll;
      if(!s.items.length){host.append(node('p','subtle',s.busy?'正在读取候选…':'当前范围没有可处理的发现候选。结构待定统计还可能包含旧知识，请核对其类型或手动补充定义。'));
        if(!s.busy)host.append(button('回到建模页新增定义',()=>{drawer.close();const menu=doc.getElementById('om-definition-menu');if(menu){menu.open=true;menu.querySelector('summary')?.focus();}}));}
      if(s.offset<s.total){const more=button('读取下一页候选',()=>controller.more());more.disabled=locked;host.append(more);}
      const actions=node('div','oi-actions');actions.append(node('strong',null,`已选 ${s.selected.length} 条`));
      const missing=s.selected.filter(id=>!s.mappings[id]).length;actions.append(node('span',null,s.target==='reuse'?`已确认类型 ${s.selected.length-missing} 条 · 尚待映射 ${missing} 条`:'预览后加入草案，审核发布后才处理关联知识'));
      const preview=button(s.busy?'正在处理…':'预览选中内容（只读）',async()=>{await controller.preview();host.querySelector?.('.oi-preview')?.scrollIntoView?.({block:'start',behavior:'smooth'});});preview.classList.add('primary');preview.disabled=locked||!s.selected.length||!s.target;actions.append(preview);host.append(actions);

      if(focused)doc.getElementById(focused)?.focus({preventScroll:true});
    }
    return {createController,receiptText,state:controller.state,
      async open(scope={}){ensure();drawer.open({trigger:scope.trigger});const serial=++openSerial,openedProject=context().project;preparing=true;
        drawer.dialog.classList.toggle?.('oi-result-dialog',false);host.replaceChildren(progressCard('正在读取候选核对工作区','正在读取当前本体、工作草案和候选知识。'));
        try{if(scope.projectId&&scope.projectId!==context().project)throw Error('项目已变化，请重新打开。');
          if(controller.hasPending(openedProject)){await controller.open(scope);return;}
          await bounded(root.OntologyModel.refresh(),'当前本体读取超时，请重试；没有保存任何结构修改。');
          if(serial!==openSerial||openedProject!==context().project)return;
          const project=context().project;const result=await bounded(api('/api/projects/'+encodeURIComponent(project)+'/ontology-drafts',undefined,'GET'),'草案列表读取超时，请重试。');
          if(serial!==openSerial||project!==context().project)return;drafts=(result.items||[]).filter(d=>d.status==='pending');await controller.open(scope);
          if(serial===openSerial&&!['','new','reuse'].includes(controller.state.target)){
            const target=drafts.find(d=>d.id===controller.state.target);controller.target(target?.id||'',target?.revision);
          }
        }catch(error){if(serial===openSerial&&openedProject===context().project){host.replaceChildren(node('p','oi-error',error.message),button('重新读取',()=>this.open(scope)));}}
        finally{if(serial===openSerial){preparing=false;if(controller.state.open&&!controller.state.error)render();}}},
      close:()=>drawer?.close(),projectChanged(){drawer?.close({restoreFocus:false});controller.close();},
    };
  }
  return {createController,createUI,receiptText,describeCandidate,diagnosticText,previewSummary};
});
