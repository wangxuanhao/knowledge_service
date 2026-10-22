/* The existing condition builder edits a draft; requests only read the store. */
(()=>{
  const context=window.WorkspaceContext,store=context.store,get=id=>document.getElementById(id);
  get('ingest-back-sources').onclick=()=>window.KnowledgeTabs.open('sources');
  get('ingest-open-jobs').onclick=()=>showRuntimeView('jobs');
  const panel=get('scope'),details=get('cb-details');let trigger=null;
  const inputs=['query-platforms','query-businesses','query-audiences'];
  const localTime=value=>{if(!value)return '';const date=new Date(value);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);};
  function restore(){
    const page=context.page,allowed=context.isContentPage(page),host=get('tab-'+page);
    panel.classList.toggle('hidden',!allowed);details.open=false;
    const purpose=get('scope-purpose-controls');if(purpose)purpose.hidden=page!=='search';
    if(allowed&&host)host.prepend(panel);
    const value=context.read(current),app=value.applicability||{};
    inputs.forEach((id,i)=>{const dimension=app[['platforms','businesses','audiences'][i]];get(id).value=(dimension?.values||[]).join('，');});
    get('known-at').value=value.known_at||'';get('scope-known-at').value=localTime(value.known_at);get('scope-valid-at').value=localTime(value.valid_at);
    get('filters').value=value.filters?JSON.stringify(value.filters,null,2):'';
    get('cb-raw-json').value=get('filters').value;
    window.ScopeEditor?.restore(value);
    renderChips();
  }
  function capture(){
    const raw=get('cb-raw-json').value.trim(),canonical=get('filters').value.trim();
    const filters=raw||canonical;
    // Parse before changing the applied entry. A failed draft keeps the old scope.
    if(filters){try{JSON.parse(filters);}catch{get('cb-raw-json').focus();throw Error('更多字段 JSON 格式不正确，请修正后应用');}}
    const time=get('scope-valid-at').value;
    const applied=context.read(current),preserved={};
    for(const key of ['ontology_scope','ontology_ids','kinds'])if(applied[key]!==undefined)preserved[key]=applied[key];
    return {...preserved,known_at:get('scope-known-at').value?new Date(get('scope-known-at').value).toISOString():null,valid_at:time?new Date(time).toISOString():null,
      filters:filters?JSON.parse(filters):null,include_unknown:true,...(window.ApplicabilityUI?.readQueryScope?.()||{})};
  }
  function changed(){
    details.open=false;renderChips();
    document.dispatchEvent(new CustomEvent('workspace:scope-changed',{detail:{projectId:current,page:context.page,scope:context.read(current)}}));
  }
  function apply(){store.edit(current,context.page,capture());store.apply(current,context.page);changed();}
  function reset(){store.reset(current,context.page);restore();changed();}
  function renderChips(){
    const host=get('applied-scope-chips');if(!host)return;host.replaceChildren();
    const value=context.read(current),parts=[];
    for(const [key,label] of [['platforms','平台'],['businesses','业务'],['audiences','对象']]){const values=value.applicability?.[key]?.values;if(values?.length)parts.push({label:label+'：'+values.join('、'),key});}
    if(value.valid_at)parts.push({label:'业务时点：'+value.valid_at.slice(0,16).replace('T',' '),key:'valid_at'});
    if(value.known_at)parts.push({label:'系统时点：'+value.known_at,key:'known_at'});
    if(value.filters)parts.push({label:'更多字段 '+window.ScopeFields.countConditions(value.filters)+' 项',key:'filters'});
    for(const item of parts){const button=document.createElement('button');button.type='button';button.className='applied-scope-chip secondary';button.textContent=item.label+' ×';button.onclick=()=>{if(['platforms','businesses','audiences'].includes(item.key)){delete value.applicability[item.key];}else value[item.key]=null;store.edit(current,context.page,value);store.apply(current,context.page);restore();changed();};host.append(button);}
    get('scope-toggle').textContent='筛选'+(parts.length?' · '+parts.length:'');
    get('scope-toggle').setAttribute('aria-expanded',String(details.open));
  }
  get('scope-toggle').onclick=()=>{if(details.open){details.open=false;restore();}else{restore();details.open=true;trigger=get('scope-toggle');get('query-platforms').focus();}renderChips();};
  get('scope-close').onclick=()=>{restore();trigger?.focus();};
  details.addEventListener('toggle',()=>get('scope-toggle').setAttribute('aria-expanded',String(details.open)));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&details.open){restore();trigger?.focus();}});
  get('project').addEventListener('change',restore);
  get('scope-to-qa').onclick=()=>{store.copy(current,context.page,'qa');window.KnowledgeTabs.open('qa');};
  context.register({restore,apply,reset});
})();
