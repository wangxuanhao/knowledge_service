/* Explicit candidate intake. Reading and previewing never prepare a draft. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.OntologyIntake=api.createUI(root);
})(typeof window==='object'?window:globalThis,()=>{
  const clone=value=>JSON.parse(JSON.stringify(value));
  const same=(a,b)=>['project','draftId','revision','ontologyId','preview'].every(key=>(a[key]??null)===(b[key]??null));
  function createController({getContext,request,uid,canWrite=()=>true,getActor=()=> 'system:auth-disabled',storage=null,requireDurable=false,onChange=()=>{},onSaved=async()=>{}}){
    const state={open:false,scope:{},items:[],terms:[],selected:[],mappings:{},target:'',targetRevision:null,total:0,offset:0,
      ontologyId:null,preview:null,receipt:null,pending:null,busy:false,error:null};
    let generation=0,previewContext=null;
    const pendingByProject=new Map();
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
      const path='/candidates?offset='+state.offset+'&limit=100'+(state.scope.documentId?'&document_id='+encodeURIComponent(state.scope.documentId):'');
      const data=await request(path,undefined,'GET');
      if(serial!==generation||getContext().project!==project||!state.open)return;
      const beforeSelection=JSON.stringify(state.selected),beforeOntology=state.ontologyId;
      let rows=data.items||[];
      if(state.scope.candidateIds?.length)rows=rows.filter(item=>state.scope.candidateIds.includes(item.id)
        ||(item.kind==='entity'&&(item.required_by||[]).some(id=>state.scope.candidateIds.includes(id))));
      state.items=append?[...state.items,...rows]:rows;state.total=data.total??rows.length;
      state.offset+=(data.items||[]).length;state.terms=data.published_terms||[];state.ontologyId=data.expected_ontology_id??null;
      const pinned=state.scope.candidateIds||[];
      state.selected=[...new Set([...state.selected,...rows.filter(item=>pinned.includes(item.id)).map(item=>item.id)])];
      if(append&&(beforeSelection!==JSON.stringify(state.selected)||beforeOntology!==state.ontologyId))invalidate();
    }
    function body(){
      if(!state.selected.length)throw Error('请先选择要处理的候选。');
      if(!state.target)throw Error('请明确选择目标草案、另建草案或仅确认复用。');
      if(state.target==='reuse'&&state.selected.some(id=>!state.mappings[id]))throw Error('请为每个选中候选明确确认正式术语映射。');
      const existing=!['new','reuse'].includes(state.target);
      return {candidate_ids:[...state.selected].sort(),expected_ontology_id:state.ontologyId,
        target_draft_id:existing?state.target:null,expected_revision:existing?state.targetRevision:null,
        create_new_draft:state.target==='new',name:'本轮结构补充',confirmed_mappings:{...state.mappings},infer_hierarchy:false};
    }
    async function work(action){state.busy=true;state.error=null;changed();try{return await action();}
      catch(error){state.error=error.message||String(error);throw error;}finally{state.busy=false;changed();}}
    const controller={state,
      hasPending:project=>!!readPending(project),
      async open(scope={}){
        const context=getContext();if(!context.project)throw Error('请先选择项目。');if(context.preview)throw Error('历史版本只读，请先回到最新。');
        if(scope.projectId&&scope.projectId!==context.project)throw Error('项目已变化，请在原项目重新打开。');
        const pending=readPending(context.project);
        if(pending){state.pending=pending;state.scope={...pending.scope};state.open=true;state.error=null;state.ontologyId=pending.body.expected_ontology_id;changed();return state;}
        generation++;Object.assign(state,{open:true,scope:{...scope},items:[],terms:[],selected:[],mappings:{},
          target:context.draftId||'',targetRevision:context.revision??null,total:0,offset:0,preview:null,receipt:null,pending:null,error:null});
        previewContext=null;await work(()=>list());return state;
      },
      close(){state.open=false;generation++;changed();},
      async more(){editable();await work(()=>list(true));},
      select(id,on){editable();if(!state.items.some(item=>item.id===id))throw Error('候选不在当前已读范围。');
        state.selected=on?[...new Set([...state.selected,id])]:state.selected.filter(value=>value!==id);if(!on)delete state.mappings[id];invalidate();changed();},
      mapping(id,iri){editable();if(iri&&!state.terms.some(term=>term.iri===iri))throw Error('只能明确映射到当前正式发布的术语。');
        if(iri)state.mappings[id]=iri;else delete state.mappings[id];invalidate();changed();},
      target(id,revision){editable();state.target=id||'';state.targetRevision=revision??(getContext().draftId===id?getContext().revision:null);invalidate();changed();},
      async preview(){editable();const snapshot=getContext();if(snapshot.preview)throw Error('历史版本只读。');
        if(snapshot.ontologyId!==state.ontologyId)throw Error('正式发布已变化，请重新读取候选。');const payload=body(),serial=generation;
        return work(async()=>{const result=await request('/selection-preview',payload,'POST');assertScope(snapshot);
          if(serial!==generation||!state.open)throw Error('抽屉已关闭，请重新预览。');state.preview=result;previewContext=snapshot;return result;});},
      async apply(kind){
        if(state.busy)throw Error('正在处理，请稍候。');
        if(!canWrite())throw Error('当前账号没有确认写入权限，请联系管理员。');
        const path=kind==='reuse'?'/confirm-reuse':'/add-selection';
        const replaying=!!state.pending;
        if(state.pending){if(state.pending.project!==getContext().project||state.pending.actor!==getActor()||getContext().preview)throw Error('项目、登录身份已变化或正在历史只读预览。');
          if(state.pending.path!==path)throw Error('请重试原来的确认动作。');}
        else{
          if(!state.preview||!previewContext)throw Error('请先预览选中的候选。');assertScope(previewContext);
          if(kind==='reuse'?!state.preview.can_confirm_reuse:!state.preview.can_add)throw Error('预览仍有阻断项，请先解决。');
          const payload={...body(),preview_fingerprint:state.preview.preview_fingerprint,source_fingerprint:state.preview.source_fingerprint,
            idempotency_key:uid(),selected_suggestion_fingerprints:null};
          const scope=Object.fromEntries(['projectId','documentId','batchId','candidateIds'].filter(key=>state.scope[key]!==undefined).map(key=>[key,state.scope[key]]));
          const pending={version:1,project:getContext().project,actor:getActor(),path,body:clone(payload),scope,workspace:{...getContext()}};
          remember(pending);state.pending=pending;
        }
        const pending=state.pending;
        return work(async()=>{
          try{
            const result=await request(pending.path,clone(pending.body),'POST');
            if(getContext().project!==pending.project||getActor()!==pending.actor)throw Error('项目或登录身份已变化；请返回原项目和账号，用同一请求核对结果。');
            forget(pending);state.pending=null;state.receipt=result.receipt||null;state.preview=null;previewContext=null;
            try{await onSaved(result,pending);}catch(error){state.error='操作已保存，但页面刷新失败：'+(error.message||error)+'。请重新读取，避免重复确认。';}
            return result;
          }catch(error){
            // Definite rejections are safe to revise. Network/timeout outcomes keep the exact request for replay.
            if(error.status>=400&&error.status<500&&!(replaying&&[401,403,429].includes(error.status))){forget(pending);state.pending=null;state.preview=null;previewContext=null;}
            throw error;
          }
        });
      },
    };return controller;
  }
  function receiptText(receipt){
    if(!receipt)return '尚未返回回执。';
    const facts=receipt.facts||{},n=value=>value===undefined?'待核对':String(value);
    const index=receipt.index||{},status={not_requested:'未请求索引',queued:'索引排队中',pending:'索引待处理',running:'索引同步中',completed:'索引同步完成',succeeded:'索引同步完成',failed:'索引同步失败'};
    if(receipt.structure)return `已加入草案 ${receipt.draft_id} · r${receipt.revision}；新增结构操作 ${n(receipt.structure.added)} 项，复用候选绑定 ${n(receipt.structure.reused)} 项，关联知识 ${n(facts.selected)} 条，待审核发布。`;
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
    let drawer=null,host=null,drafts=[],controller,openSerial=0;
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
    const run=action=>Promise.resolve().then(action).catch(error=>{if(!controller.state.error)controller.state.error=error.message;render();});
    let storage;try{storage=root.sessionStorage;}catch{storage=null;}
    controller=createController({getContext:context,request,storage,requireDurable:true,getActor:()=>root.Auth?.user?.()?.id||root.Auth?.user?.()?.username||'system:auth-disabled',canWrite:()=>Boolean(root.Auth?.isAdmin?.()),uid:()=>root.OntologyModel.uid(),onChange:()=>render(),onSaved:async(result,pending)=>{
      const M=root.OntologyModel;if(result.draft&&same(pending.workspace,context()))M.S.draft=result.draft;
      await M.refresh();doc.dispatchEvent(new CustomEvent('ontology-intake:saved',{detail:{projectId:context().project,receipt:result.receipt}}));
    }});
    function ensure(){if(drawer)return;
      host=node('div','ontology-intake');drawer=root.WorkspaceDrawers.create({id:'ontology-intake-drawer',title:'待完善结构',
        description:'先核对候选和证据，再只读预览；只有明确确认才加入工作草案或写入知识。',content:host,onClose:()=>{++openSerial;controller.close();}});
      drawer.dialog.classList.add('ontology-intake-drawer');
    }
    function render(){if(!host)return;const s=controller.state;host.replaceChildren();
      const current=context(),locked=s.busy||!!s.pending,scope=s.scope;
      host.append(node('p','oi-context',`当前项目 ${current.project||'—'} · 正式发布 ${s.ontologyId||'尚未发布'}`));
      if(scope.documentId||scope.batchId)host.append(node('p','oi-scope',`来源${scope.documentId?'文档 '+scope.documentId:''}${scope.batchId?' · 处理批次 '+scope.batchId:''}`));
      if(s.error){const e=node('p','oi-error',s.error);e.setAttribute('role','alert');host.append(e);}
      if(s.pending){host.append(node('p','oi-warning',`上次确认结果未知。原请求选中 ${s.pending.body.candidate_ids.length} 条候选，目标 ${s.pending.body.target_draft_id||(s.pending.path==='/confirm-reuse'?'仅复用正式术语':'确认时新建草案')}，正式基线 ${s.pending.body.expected_ontology_id||'空基线'}。已恢复原请求身份；只有点击下方按钮才重试核对。`));
        const retry=button('重试原请求并核对回执',()=>controller.apply(s.pending.path==='/confirm-reuse'?'reuse':'add'));retry.disabled=s.busy;host.append(retry);return;}
      if(s.receipt){const receipt=node('section','oi-receipt');receipt.setAttribute('role','status');receipt.append(node('h3',null,'处理回执'),node('p',null,receiptText(s.receipt)));
        for(const row of s.receipt.facts?.per_batch||[])receipt.append(node('p','subtle',JSON.stringify(row)));
        if(s.receipt.draft_id)receipt.append(button('查看这份工作草案',async()=>{await root.OntologyModel.selectDraft(s.receipt.draft_id);drawer.close();}));
        receipt.append(button('重新读取待处理候选',()=>controller.open(scope)));host.append(receipt);return;}
      const title=node('div','oi-section-heading');title.append(node('h3',null,'01 选择候选'),node('span',null,`已选 ${s.selected.length} · 已读 ${s.offset} / ${s.total}`));host.append(title);
      const list=node('div','oi-candidate-list');
      for(const item of s.items){const card=node('article','oi-candidate'+(s.selected.includes(item.id)?' is-selected':''));
        const label=node('label','oi-candidate-select'),input=node('input');input.type='checkbox';input.checked=s.selected.includes(item.id);input.disabled=locked;
        input.onchange=()=>run(()=>controller.select(item.id,input.checked));label.append(input,node('strong',null,describeCandidate(item)),node('small',null,kindLabels[item.kind]||'候选'));card.append(label,node('code','oi-candidate-id',item.id));
        if(item.materialized)card.append(node('p','oi-dependency-note','已写入正式知识 · 本次可明确选择为依赖并复用，仍需确认准确正式术语映射。'));
        if(item.dependency_candidate){
          const labels=(item.required_by||[]).map(id=>{const fact=s.items.find(row=>row.id===id);return fact?describeCandidate(fact):id;});
          card.append(node('p','subtle','以下待处理知识依赖此实体：'+labels.join('；')+'。勾选才会进入本次确认范围。'));
        }
        card.append(node('p','oi-source',`${item.document_title||item.document_id||'来源文档缺失'} · 修订 ${item.document_version_id||'缺失'}`));
        if(item.document_id)card.append(button('返回来源文档',async()=>{const project=current.project,id=item.document_id;drawer.close();
          if(context().project!==project)return;await root.KnowledgeNavigation.open('sources',{projectId:project});
          if(context().project===project)await root.selectSource(id);}));
        if(item.evidence){const evidence=node('details','oi-evidence');evidence.append(node('summary',null,'来源证据'),node('pre',null,typeof item.evidence==='string'?item.evidence:JSON.stringify(item.evidence,null,2)));card.append(evidence);}
        const links=item.linked_drafts||[];card.append(node('p','subtle',links.length?'关联草案：'+links.map(link=>`${link.draft_id}（${link.status}）`).join('、'):'未加入本轮修改'));
        for(const link of links.filter(link=>link.status==='draft_created'))card.append(button('查看关联草案 '+link.draft_id,async()=>{
          const project=current.project;await root.OntologyModel.selectDraft(link.draft_id);if(context().project===project)drawer.close();}));
        if(s.selected.includes(item.id)){
          const mapping=node('select','oi-mapping');mapping.setAttribute('aria-label','确认正式术语映射：'+(item.text||item.id));
          const blank=node('option',null,'选择准确正式术语（需核对后确认）');blank.value='';mapping.append(blank);
          for(const term of s.terms){const option=node('option',null,`${term.label||term.iri} · ${kindLabels[term.kind]||term.kind} · ${term.iri}`);option.value=term.iri;mapping.append(option);}
          mapping.value=s.mappings[item.id]||'';mapping.disabled=locked;mapping.onchange=()=>run(()=>controller.mapping(item.id,mapping.value));card.append(mapping);
          if(item.suggested_mapping)card.append(node('p','subtle','建议复用：'+(item.suggested_mapping.label||item.suggested_mapping.target_iri)+'；请选择上方正式身份明确确认。'));
        }list.append(card);
      }host.append(list);
      if(!s.items.length){host.append(node('p','subtle',s.busy?'正在读取候选…':'当前范围没有可处理的发现候选。结构待定统计还可能包含旧知识，请核对其类型或手动补充定义。'));
        if(!s.busy)host.append(button('回到建模页新增定义',()=>{drawer.close();const menu=doc.getElementById('om-definition-menu');if(menu){menu.open=true;menu.querySelector('summary')?.focus();}}));}
      if(s.offset<s.total){const more=button('读取下一页候选',()=>controller.more());more.disabled=locked;host.append(more);}
      const target=node('section','oi-target');target.append(node('h3',null,'02 明确处理目标'));
      const select=node('select');select.setAttribute('aria-label','候选处理目标');
      for(const [value,text] of [['','请选择目标'],['new','基于当前正式发布另建一轮修改'],...drafts.map(d=>[d.id,`${d.title||'工作草案'} · ${d.id} · r${d.revision}`]),['reuse','仅复用正式术语，确认写入知识']]){const o=node('option',null,text);o.value=value;select.append(o);}
      select.value=s.target;select.disabled=locked;select.onchange=()=>run(()=>controller.target(select.value,drafts.find(d=>d.id===select.value)?.revision));target.append(select);
      target.append(node('p','subtle',s.target==='new'?'预览不会创建草案；确认加入时才建立本轮修改。':s.target==='reuse'?'每个候选必须明确映射到正式术语。只处理所选事实范围，不建立草案或本体版本。':'沿用明确选择的草案；原有手工修改和已加入批次保留。'));
      const preview=button(s.busy?'正在处理…':'预览选中内容（只读）',()=>controller.preview());preview.disabled=locked||!s.selected.length||!s.target;target.append(preview);host.append(target);
      if(s.preview){const p=s.preview,box=node('section','oi-preview');box.append(node('h3',null,'03 核对预览并确认'),node('p',null,`目标 ${p.target?.draft_id||(s.target==='reuse'?'仅确认事实，不建草案':'确认加入时新建草案')} · 修订 ${p.target?.revision??'—'} · 基线 ${p.target?.base_ontology_id||'空基线'} · 关联知识 ${(p.associated_fact_scope||[]).length} 条`),node('p',null,`结构建议 ${(p.suggestions||[]).length} 项 · 需补结构候选 ${(p.new||[]).length} / 正式术语复用 ${(p.reused||[]).length} / 冲突 ${(p.conflicts||[]).length}`));
        const names=new Map(s.terms.map(term=>[term.iri,term.label||term.iri]));
        for(const binding of [...p.new||[],...p.reused||[]]){const item=s.items.find(row=>row.id===binding.candidate_id);if(item?.proposed_type)names.set(binding.target_iri,item.proposed_type);}
        for(const suggestion of p.suggestions||[]){const d=node('details','oi-diff'),technical=node('details','oi-technical');
          technical.append(node('summary',null,'技术详情（原始操作与 RDF 值）'),node('pre',null,JSON.stringify(suggestion,null,2)));
          d.append(node('summary',null,changeTitle(suggestion,names)),node('pre','oi-change-values','修改前\n'+changeValue(suggestion.before,names)+'\n修改后\n'+changeValue(suggestion.after,names)),technical);box.append(d);}
        const facts=p.associated_fact_scope||[],items=[...s.items,...facts];
        const outcomes=new Map((p.fact_outcomes||[]).map(item=>[item.candidate_id,item]));
        const outcomeLabels={add:'预计新增',reuse:'预计复用',skip:'预计跳过',pending:'等待结构获批',blocked:'暂不能处理'};
        const factSection=node('section','oi-fact-preview');factSection.append(node('h4',null,'逐条关联知识预期结果'));
        for(const fact of facts){
          const outcome=outcomes.get(fact.id),row=node('article','oi-fact-row');
          row.append(node('strong',null,describeCandidate(fact)),node('code','oi-candidate-id',fact.id),
            node('p',outcome?.outcome==='skip'||outcome?.outcome==='blocked'?'oi-error':'oi-fact-outcome',
              outcome?`${outcomeLabels[outcome.outcome]||'未判定'} · ${outcome.message||outcome.reason_code||'未提供判定原因'}`:'服务尚未提供此事实的预期结果，请重新预览。'));
          if(outcome?.reason_code)row.append(node('small','subtle','原因代码：'+outcome.reason_code));
          factSection.append(row);
        }box.append(factSection);
        const completeFacts=facts.length===s.selected.length&&s.selected.every(id=>facts.some(item=>item.id===id)&&outcomeLabels[outcomes.get(id)?.outcome]);
        if(!completeFacts)box.append(node('p','oi-error','逐条事实范围或预期结果不完整，暂不能确认。请重新读取并预览。'));
        for(const key of ['conflicts','warnings','missing_dependencies'])for(const message of p[key]||[])box.append(node('p',key==='warnings'?'oi-warning':'oi-error',diagnosticText(message,items)));
        const reuse=s.target==='reuse',apply=button(reuse?'确认写入所选知识':'加入本轮修改',()=>controller.apply(reuse?'reuse':'add'));apply.classList.add('primary');apply.disabled=locked||!completeFacts||!(reuse?p.can_confirm_reuse:p.can_add);box.append(apply);host.append(box);
      }
    }
    return {createController,receiptText,state:controller.state,
      async open(scope={}){ensure();drawer.open({trigger:scope.trigger});const serial=++openSerial,openedProject=context().project;
        host.replaceChildren(node('p','oi-context','正在读取当前正式发布、工作草案与候选…'));
        try{if(scope.projectId&&scope.projectId!==context().project)throw Error('项目已变化，请重新打开。');
          if(controller.hasPending(openedProject)){await controller.open(scope);return;}
          await bounded(root.OntologyModel.refresh(),'当前本体读取超时，请重试；没有保存任何结构修改。');
          if(serial!==openSerial||openedProject!==context().project)return;
          const project=context().project;const result=await bounded(api('/api/projects/'+encodeURIComponent(project)+'/ontology-drafts',undefined,'GET'),'草案列表读取超时，请重试。');
          if(serial!==openSerial||project!==context().project)return;drafts=(result.items||[]).filter(d=>d.status==='pending');await controller.open(scope);
        }catch(error){if(serial===openSerial&&openedProject===context().project){host.replaceChildren(node('p','oi-error',error.message),button('重新读取',()=>this.open(scope)));}}},
      close:()=>drawer?.close(),projectChanged(){drawer?.close({restoreFocus:false});controller.close();},
    };
  }
  return {createController,createUI,receiptText,describeCandidate,diagnosticText};
});
