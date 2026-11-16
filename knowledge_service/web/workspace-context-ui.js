/* The existing condition builder edits a draft; requests only read the store. */
(()=>{
  const context=window.WorkspaceContext,store=context.store,get=id=>document.getElementById(id);
  get('ingest-back-sources').onclick=()=>window.KnowledgeTabs.open('sources');
  get('ingest-open-jobs').onclick=()=>showRuntimeView('jobs');
  const panel=get('scope'),details=get('cb-details');let trigger=null;
  const inputs=['query-platforms','query-businesses','query-audiences'];
  function timeControls(){
    const mode=get('scope-time-mode').value;
    get('scope-valid-at-field').hidden=mode!=='at';get('scope-valid-at').disabled=mode!=='at';
    get('scope-include-unknown').disabled=mode==='all';
    if(mode==='all')get('scope-include-unknown').checked=true;
  }
  function placePanel(page){
    // Each content page owns one inline entry row; search keeps its header slot.
    const tab=get('tab-'+page);
    if(!tab)return;
    const query=selector=>typeof tab.querySelector==='function'?tab.querySelector(selector):null;
    if(page==='sources'){
      const row=query('.source-actions-row');
      if(row){row.prepend(panel);return;}
    }
    if(page==='records'){
      const row=query('.panel > .row');
      if(row){const heading=typeof row.querySelector==='function'?row.querySelector('h2'):null;
        if(heading?.parentNode===row)row.insertBefore(panel,heading.nextSibling);
        else row.prepend(panel);
        return;}
    }
    if(page==='qa'){
      const heading=query('.qa-page-heading');
      if(heading&&typeof heading.querySelector==='function'){
        const actions=heading.querySelector('.qa-page-actions');
        actions?heading.insertBefore(panel,actions):heading.append(panel);return;}
    }
    tab.prepend(panel);
  }
  function restore(){
    const page=context.page,allowed=context.isContentPage(page);
    panel.classList.toggle('hidden',!allowed);details.open=false;
    const purpose=get('scope-purpose-controls');if(purpose)purpose.hidden=page!=='search';
    if(allowed&&!window.GraphWorkspaceLayout?.placeScope(panel))placePanel(page);
    const value=context.read(current),app=value.applicability||{};
    inputs.forEach((id,i)=>{const dimension=app[['platforms','businesses','audiences'][i]];get(id).value=(dimension?.values||[]).join('，');});
    const time=window.TemporalScope.restore(value);
    get('known-at').value=value.known_at||'';get('scope-known-at').value=time.knownAt;get('scope-valid-at').value=time.validAt;
    get('scope-time-mode').value=time.mode;get('scope-include-unknown').checked=time.includeUnknown;timeControls();
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
    const applied=context.read(current),preserved={};
    const time=window.TemporalScope.capture({mode:get('scope-time-mode').value||'all',validAt:get('scope-valid-at').value,
      knownAt:get('scope-known-at').value,includeUnknown:get('scope-include-unknown').checked},()=>new Date(),applied);
    for(const key of ['ontology_scope','ontology_ids','kinds'])if(applied[key]!==undefined)preserved[key]=applied[key];
    return {...preserved,...time,filters:filters?JSON.parse(filters):null,...(window.ApplicabilityUI?.readQueryScope?.()||{})};
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
    if(value.valid_at)parts.push({label:'业务时点（固定）：'+value.valid_at+' · '+(value.include_unknown===false?'不含生效时间未知':'含生效时间未知'),key:'valid_at'});
    if(value.known_at)parts.push({label:'系统修订时点：'+value.known_at,key:'known_at'});
    if(value.filters)parts.push({label:'更多字段 '+window.ScopeFields.countConditions(value.filters)+' 项',key:'filters'});
    for(const item of parts){const button=document.createElement('button');button.type='button';button.className='applied-scope-chip secondary';button.textContent=item.label+' ×';button.onclick=()=>{if(['platforms','businesses','audiences'].includes(item.key)){delete value.applicability[item.key];}else{value[item.key]=null;if(item.key==='valid_at')value.include_unknown=true;}store.edit(current,context.page,value);store.apply(current,context.page);restore();changed();};host.append(button);}
    get('scope-time-applied').textContent='已应用：'+window.TemporalScope.describe(value);
    get('scope-toggle').textContent='筛选'+(parts.length?' · '+parts.length:'');
    get('scope-toggle').setAttribute('aria-expanded',String(details.open));
  }
  function positionPanel(){
    if(!details.open||!get('scope-toggle').getBoundingClientRect)return;
    const anchor=get('scope-toggle').getBoundingClientRect();
    const width=Math.min(320,document.documentElement.clientWidth-24),height=window.innerHeight;
    const left=Math.max(12,Math.min(anchor.left,document.documentElement.clientWidth-width-12));
    const below=anchor.bottom+8;
    const top=height-below>=240?below:Math.max(12,height-Math.min(600,height-24)-12);
    Object.assign(details.style,{position:'fixed',left:left+'px',right:'auto',top:top+'px',
      width:width+'px',maxHeight:Math.max(0,height-top-12)+'px'});
  }
  get('scope-toggle').onclick=()=>{if(details.open){details.open=false;restore();}else{restore();details.open=true;trigger=get('scope-toggle');positionPanel();get('query-platforms').focus();}renderChips();};
  window.addEventListener?.('resize',positionPanel);
  get('scope-close').onclick=()=>{restore();trigger?.focus();};
  details.addEventListener('toggle',()=>get('scope-toggle').setAttribute('aria-expanded',String(details.open)));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&details.open){restore();trigger?.focus();}});
  get('project').addEventListener('change',restore);
  get('scope-time-mode').addEventListener('change',timeControls);
  context.register({restore,apply,reset});
})();
