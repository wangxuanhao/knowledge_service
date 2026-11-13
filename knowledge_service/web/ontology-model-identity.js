/* Class identity settings use exact, atomic draft annotations and reviewed releases. */
(() => {
  const NS='https://knowledge.local/schema/';
  const predicates=new Set(['identityPolicy','identityScope','identityKey'].map(name=>NS+name));
  const modes=[['','未配置（保持现有规则）'],['scoped','按业务区间区分（事件等）'],['reference','跨文档统一身份（长期对象）']];
  const isIdentityOperation=operation=>['add_annotation','remove_annotation'].includes(operation.action)&&
    ['before','after'].some(side=>predicates.has(operation[side]?.predicate));
  function policyState(cls) {
    const policy=cls.identity_policy||{};
    return {mode:policy.declared?policy.mode:'',scope:policy.scope||'',keys:[...(policy.keys||[])]};
  }
  function normalized(values) {
    const mode=String(values.mode||'');
    if(!modes.some(([key])=>key===mode))throw Error('请选择有效的实体身份策略。');
    const keys=[...new Set((Array.isArray(values.keys)?values.keys:String(values.keys||'').split(/\r?\n/))
      .map(key=>String(key).trim()).filter(Boolean))].sort();
    return {mode,scope:String(values.scope||'').trim(),keys};
  }
  const signature=values=>JSON.stringify(normalized(values));
  function policyCommands(cls,values) {
    if(!Array.isArray(cls.identity_annotations))throw Error('身份注解尚未完整读取，请刷新后重试。');
    const desired=normalized(values);
    if(signature(desired)===signature(policyState(cls)))return [];
    const commands=[],push=(action,spec)=>commands.push({action,target_iri:cls.id,...Object.fromEntries(
      ['predicate','value','type','language','datatype'].filter(key=>spec[key]!==undefined&&spec[key]!==null)
        .map(key=>[key==='type'?'value_type':key,spec[key]]))});
    // Remove first: every intermediate graph remains valid even for single-valued fields.
    cls.identity_annotations.filter(a=>predicates.has(a.predicate)).forEach(a=>push('remove_annotation',a));
    if(desired.mode)push('add_annotation',{predicate:NS+'identityPolicy',value:desired.mode});
    if(desired.scope)push('add_annotation',{predicate:NS+'identityScope',value:desired.scope});
    desired.keys.forEach(value=>push('add_annotation',{predicate:NS+'identityKey',value}));
    if(commands.length>100)throw Error('身份设置超过单次草案命令上限，请减少身份键后重试。');
    return commands;
  }
  const caches=new Map();let epoch=0,projectControl=null;
  const baseline=cls=>JSON.stringify([policyState(cls),cls.identity_annotations]);
  const cacheKey=(cls,options)=>{const st=options.model.S;return JSON.stringify([
    options.project(),cls.id,st.ontologyId,st.draft?.id,st.preview?.id]);};
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;
    if(text!==undefined)node.textContent=String(text);return node;};
  const field=(text,input)=>{const node=el('label','om-attribute-field');node.append(el('span',null,text),input);return node;};
  const canWrite=(cls,options)=>{
    const st=options.model.S;
    return !!options.project()&&!!window.Auth?.isAdmin?.()&&!st.preview&&!st.loading&&cls.active!==false&&
      (!st.draft||(st.draft.status==='pending'&&st.showingDraft===true));
  };
  const ticket=options=>{const st=options.model.S;return JSON.stringify([epoch,options.project(),st.ontologyId,
    st.draft?.id,st.draft?.revision,st.draft?.status,!!st.showingDraft,st.preview?.id]);};
  function preparedCurrent(cls,options,prepared) {
    const st=options.model.S,draft=st.draft;
    const term=(draft?.ontology?.classes||[]).find(item=>item.id===cls.id);
    return !!prepared&&prepared.epoch===epoch&&prepared.project===options.project()&&
      !!window.Auth?.isAdmin?.()&&!st.preview&&!st.loading&&cls.active!==false&&
      st.ontologyId===prepared.base&&draft?.id===prepared.id&&draft.revision===prepared.revision&&
      draft.status==='pending'&&draft.base_ontology_id===prepared.base&&!!term&&
      baseline(term)===prepared.stamp&&baseline(cls)===prepared.stamp;
  }
  function classSection(cls,options) {
    const control=typeof document.getElementById==='function'?document.getElementById('project'):null;
    if(control&&projectControl!==control){projectControl=control;control.addEventListener('change',()=>{epoch++;});}
    const st=options.model.S,policy=policyState(cls),box=el('section','om-detail-sec om-identity-policy');
    box.append(options.title('实体身份策略','由本体版本固定的识别规则。修改写入草案，经人工审核发布后用于后续识别。'));
    const source=st.preview?'历史版本 · 只读':st.showingDraft?'工作草案 · 待审核发布':'正式版本';
    const mode=modes.find(([id])=>id===policy.mode)?.[1]||'策略无法识别';
    box.append(el('p','om-detail-note',source+'：'+mode),el('p','om-detail-note',
      policy.mode==='reference'?'同类型、身份范围与身份键相容时，可识别跨资料的同一实体；来源时间与适用条件保留在各条知识中。':
        '默认按来源时间和适用条件区分实体；已登记的相容身份键可提供更强的识别依据。'));
    if(policy.scope)box.append(el('p','om-detail-note','身份范围：'+policy.scope));
    if(policy.keys.length)box.append(el('p','om-detail-note','身份键：'+policy.keys.join('、')));
    const key=cacheKey(cls,options),stamp=baseline(cls),cached=caches.get(key);
    if(!canWrite(cls,options)){
      if(cached?.stamp===stamp&&preparedCurrent(cls,options,cached.prepared)){
        const state=cached,opened=ticket(options),feedback=el('p','om-attribute-message');
        feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
        const recover=el('button','om-btn secondary admin-only','打开草案继续编辑');recover.type='button';
        const sync=()=>{feedback.textContent=state.error;recover.disabled=state.busy||
          !preparedCurrent(cls,options,state.prepared);};
        state.update=sync;
        recover.addEventListener('click',async()=>{
          if(state.busy)return;
          try{
            if(ticket(options)!==opened||!preparedCurrent(cls,options,state.prepared))
              throw Error('项目、草案或版本已变化，请重新核对身份策略。');
            state.busy=true;state.error='正在打开草案，身份设置输入已保留…';sync();
            const prepared=state.prepared;
            await options.model.selectDraft(prepared.id);
            const st=options.model.S,term=(st.summary?.classes||[]).find(item=>item.id===cls.id);
            if(epoch!==prepared.epoch||options.project()!==prepared.project||st.preview||
                st.draft?.id!==prepared.id||st.draft.base_ontology_id!==prepared.base||
                st.ontologyId!==prepared.base||!term||baseline(term)!==prepared.stamp)
              throw Error('草案定义已变化，保留的输入未应用，请重新核对。');
            state.prepared={...prepared,revision:st.draft.revision};state.error='输入已保留，可重试保存身份策略。';
            options.model.select(cls.id);
          }catch(error){state.error=error.message||'打开草案失败，请重试。';
            if(options.project()===state.prepared.project&&epoch===state.prepared.epoch)options.model.status(state.error,true);}
          finally{state.busy=false;state.update();}
        });
        box.append(feedback,recover);sync();
      }
      return box;
    }
    if(!Array.isArray(cls.identity_annotations)){
      box.append(el('p','om-attribute-message','身份注解尚未完整读取，暂不能编辑。'));
      const retry=el('button','om-btn secondary','重新读取身份设置');retry.type='button';
      retry.addEventListener('click',async()=>{try{await options.model.refresh();}catch(error){options.model.status(error.message,true);}});
      box.append(retry);return box;
    }
    const previous=caches.get(key);
    if(!previous||previous.stamp!==stamp)caches.set(key,{stamp,values:{...policy,keys:policy.keys.join('\n')},busy:false,error:'',applied:signature(policy)});
    const state=caches.get(key),opened=ticket(options),editor=el('details','om-attribute-editor admin-only');
    editor.append(el('summary',null,'设置身份策略'));
    const body=el('div','om-attribute-editor-body'),modeInput=el('select','om-new-input');
    modeInput.setAttribute('aria-label','实体身份策略');
    for(const [value,text] of modes){const option=el('option',null,text);option.value=value;modeInput.append(option);}
    modeInput.value=state.values.mode;
    const scope=el('input','om-new-input');scope.type='text';scope.value=state.values.scope;scope.setAttribute('aria-label','身份范围');
    scope.placeholder='选填，例如：组织登记目录';
    const keys=el('textarea','om-new-input');keys.rows=3;keys.value=state.values.keys;keys.setAttribute('aria-label','身份键');
    keys.placeholder='选填，每行一个，例如：统一社会信用代码';
    const feedback=el('p','om-attribute-message');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
    const save=el('button','om-btn primary','保存身份策略到草案');save.type='button';
    const sync=()=>{
      feedback.textContent=state.error;
      save.disabled=state.busy||signature(state.values)===state.applied||!canWrite(cls,options);
      modeInput.disabled=scope.disabled=keys.disabled=state.busy;
      save.setAttribute('aria-busy',String(state.busy));
    };
    for(const [input,key,event] of [[modeInput,'mode','change'],[scope,'scope','input'],[keys,'keys','input']])
      input.addEventListener(event,()=>{state.values[key]=input.value;state.error='';sync();});
    state.update=sync;
    save.addEventListener('click',async()=>{
      if(state.busy)return;
      const origin={project:options.project(),epoch,base:options.model.S.ontologyId,
        draftId:options.model.S.draft?.id,revision:options.model.S.draft?.revision};
      try{
        if(!canWrite(cls,options)||ticket(options)!==opened||editor.isConnected===false||
            (options.model.S.selected&&options.model.S.selected!==cls.id))
          throw Error('项目、草案或版本已变化，请重新打开身份策略设置。');
        const commands=policyCommands(cls,state.values);if(!commands.length)return;
        state.busy=true;state.error='正在写入草案…';sync();
        if(typeof options.model.ensureDraft==='function'){
          const draft=await options.model.ensureDraft(),current=options.model.S;
          if(epoch!==origin.epoch||options.project()!==origin.project||current.preview||
              current.ontologyId!==origin.base||current.draft?.id!==draft.id||
              current.draft.revision!==draft.revision||draft.status!=='pending')
            throw Error('项目、草案或版本已变化，保留的输入未应用，请重新核对。');
          if(origin.draftId){
            if(draft.id!==origin.draftId||draft.revision!==origin.revision||ticket(options)!==opened)
              throw Error('草案修订已变化，保留的输入未应用，请重新核对。');
          }else{
            const prepared={project:origin.project,epoch:origin.epoch,base:origin.base,
              id:draft.id,revision:draft.revision,stamp:state.stamp};
            if(!preparedCurrent(cls,options,prepared))
              throw Error('草案基线或身份定义已变化，保留的输入未应用，请重新核对。');
            state.prepared=prepared;caches.set(cacheKey(cls,options),state);
            options.model.select(cls.id);
          }
        }
        await options.model.sendCommand({action:'batch',commands});
        state.applied=signature(state.values);state.error='身份策略已写入草案，需人工审核发布后生效。';
        if(options.project()===JSON.parse(opened)[1])options.model.status(state.error);
      }catch(error){state.error=error.message||'保存失败，请重试。';
        if(options.project()===origin.project&&epoch===origin.epoch)options.model.status(state.error,true);}
      finally{state.busy=false;state.update();}
    });
    body.append(field('识别方式',modeInput),el('p','om-detail-note','跨文档统一身份适合机构、产品等长期对象；按业务区间区分适合合同、事件等随具体时间和适用条件变化的对象。'),
      field('身份范围（可选）',scope),field('身份键（可选，每行一个）',keys),
      el('p','om-detail-note','身份键名称需与实体登记的键一致。保存只加入草案，审核发布后用于后续识别；现有数据保持原状。'),save,feedback);
    editor.append(body);box.append(editor);sync();return box;
  }
  const api={policyState,policyCommands,isIdentityOperation,classSection};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.OntologyIdentity=api;
})();
