/* Applied content scope belongs to a project and page, never to hidden controls. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceContext=api;})(typeof window==='object'?window:globalThis,function(){
  const pages=new Set(['sources','qa','search','records']);
  const clone=value=>JSON.parse(JSON.stringify(value));
  const empty=()=>({known_at:null,valid_at:null,filters:null,include_unknown:true});
  function applicability(value){
    const dimensions=['platforms','businesses','audiences'];
    if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).some(key=>!dimensions.includes(key)))throw Error('适用范围必须为维度对象');
    return Object.fromEntries(dimensions.map(name=>{
      const dimension=value[name]??{mode:'unknown',values:[]};
      if(Array.isArray(dimension)||typeof dimension!=='object'||
        !['specific','all','unknown'].includes(dimension.mode)||!Array.isArray(dimension.values)||
        dimension.values.some(item=>typeof item!=='string')||
        (dimension.mode==='specific'?!dimension.values.length:dimension.values.length))throw Error('适用范围维度必须为 mode/values 对象');
      return [name,clone(dimension)];
    }));
  }
  function normalize(value={}){
    const result={...empty(),...clone(value)};
    if(typeof result.filters==='string'){try{result.filters=result.filters.trim()?JSON.parse(result.filters):null;}catch{throw Error('更多字段 JSON 格式不正确，请修正后应用');}}
    if(result.applicability!=null)result.applicability=applicability(result.applicability);
    return result;
  }
  function createStore(storage=null){
    const entries=new Map();let generation=0;
    const storageKey='kg_workspace_scope_v2';
    try{for(const [id,value] of JSON.parse(storage?.getItem(storageKey)||'[]')){const [project,page]=JSON.parse(id);if(project&&pages.has(page)){const applied=normalize(value);entries.set(id,{applied,draft:clone(applied),revision:++generation});}}}catch{}
    const persist=()=>{try{storage?.setItem(storageKey,JSON.stringify([...entries].map(([id,row])=>[id,row.applied])));}catch{}};
    const key=(project,page)=>JSON.stringify([project,page]);
    const entry=(project,page)=>{const id=key(project,page);if(!entries.has(id))entries.set(id,{applied:empty(),draft:empty(),revision:++generation});return entries.get(id);};
    const store={
      read(project,page){return !project||!pages.has(page)?empty():clone(entry(project,page).applied);},
      draft(project,page){return clone(entry(project,page).draft);},
      edit(project,page,value){if(pages.has(page))entry(project,page).draft=clone(value);},
      apply(project,page){if(!project||!pages.has(page))return empty();const row=entry(project,page),next=normalize(row.draft);row.applied=next;row.draft=clone(next);row.revision=++generation;persist();return clone(next);},
      cancel(project,page){const row=entry(project,page);row.draft=clone(row.applied);},
      reset(project,page){if(!pages.has(page))return;store.edit(project,page,empty());return store.apply(project,page);},
      copy(project,from,to){store.edit(project,to,store.read(project,from));return store.apply(project,to);},
      ticket(project,page){return {project,page,revision:entry(project,page).revision};},
      accepts(ticket,project,page){return ticket.project===project&&ticket.page===page&&ticket.revision===entry(project,page).revision;},
      removeProject(project){for(const id of entries.keys())if(JSON.parse(id)[0]===project)entries.delete(id);generation++;persist();},
      clear(){entries.clear();generation++;try{storage?.removeItem(storageKey);}catch{}},
    };return store;
  }
  let session=null;try{if(typeof sessionStorage!=='undefined')session=sessionStorage;}catch{}
  const store=createStore(session);let page='search',bridge=null;
  function projectPayload(name){name=String(name||'').trim();if(!name)throw Error('请输入项目名称');return {name,ontology_mode:'discovery',use_default_ontology:false};}
  function accountPolicy(actor,target){
    const writer=['admin','superadmin'].includes(actor?.role),self=actor?.id===target?.id;
    const edit=writer&&(target?.role!=='superadmin'||actor?.role==='superadmin');
    const protectedTarget=self||target?.role==='superadmin';
    return {edit,role:edit&&!protectedTarget,status:edit&&!protectedTarget,password:edit&&!self,delete:edit&&!protectedTarget};
  }
  function requireRetrievalMode(requested,result={}){
    if(requested==='keyword')return;
    const active=result.active_mode?[result.active_mode]:result.active_modes||[];
    if(result.degraded||active.some(mode=>mode!==requested))throw Error('Milvus 语义检索未按所选模式完成。请到「构建与任务」检查向量连接、模型维度和索引后重试。');
  }
  return {createStore,store,projectPayload,accountPolicy,requireRetrievalMode,isContentPage:name=>pages.has(name),get page(){return page;},
    read(project,name=page){return store.read(project,name);},
    enter(name){page=name;bridge?.restore();},
    register(value){bridge=value;bridge.restore();},
    restore(){bridge?.restore();},
    apply(){return bridge?.apply();},
    reset(){return bridge?.reset();},
    ticket(project,name=page){return store.ticket(project,name);},
    accepts(ticket,project,name=page){return store.accepts(ticket,project,name);},
    clear(){store.clear();bridge?.restore();},
  };
});
