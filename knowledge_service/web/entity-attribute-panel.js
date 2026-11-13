/* Shared scoped property reader. It never adds nodes or edges to a graph. */
(function(root,factory){
  const api=factory(root);if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityAttributePanel=api;
})(typeof window==='object'?window:globalThis,root=>{
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone=value=>JSON.parse(JSON.stringify(value));
  const text=value=>typeof value==='string'?value:JSON.stringify(value)??'';
  const preview=value=>Array.from(String(value??'')).slice(0,400).join('');
  const cleanScope=value=>{const {entity_type,predicate,...scope}=clone(value||{});return scope;};
  const interval=value=>!value.valid_from&&!value.valid_until?'时间待核':`${value.valid_from||'起始未知'} → ${value.valid_until||'未设结束时间'}${value.valid_until?'（不含）':''}`;
  const conflict=value=>value.status==='contradicting'||value.conflict_state&&value.conflict_state!=='clear';
  const sameVersion=(expected,actual)=>expected.version_id?expected.version_id===actual.version_id:expected.version===undefined||expected.version===actual.version;
  const matchesFactVersion=(expected,actual)=>Boolean(actual)&&(typeof expected?.version_id==='string'&&expected.version_id?expected.version_id===actual.version_id:Number.isInteger(expected?.version)&&expected.version>0&&expected.version===actual.version);
  const changed=()=>Error('知识版本已变化，请重新加载属性后阅读');
  function createController({projectId,entity,scope={},request,accepts=()=>true,onChange=()=>{}}){
    const base='/api/projects/'+encodeURIComponent(projectId)+'/entities/'+encodeURIComponent(entity.id)+'/attributes',fixed=cleanScope(scope);
    let generation=0,conflictGeneration=0,disposed=false;
    const state={entity:clone(entity),groups:[],conflict_sets:[],conflict_next_cursor:null,conflict_loading:false,conflict_error:'',property_count:0,value_count:0,next_cursor:null,watermark:null,predicate:null,focus_expected_version:null,focus_refreshed:false,loading:false,error:'',values:Object.create(null),sources:Object.create(null)};
    const valid=epoch=>!disposed&&epoch===generation&&accepts();
    const emit=()=>{if(!disposed&&accepts())onChange(clone(state));};
    const fact=id=>state.groups.flatMap(group=>group.values||[]).find(value=>value.record_id===id)||state.conflict_sets.flatMap(set=>(set.members||[]).map(value=>({...value,conflict_set_id:set.id}))).find(value=>value.record_id===id);
    const factUrl=(id,value,sources=false)=>value.conflict_set_id?'/api/projects/'+encodeURIComponent(projectId)+'/attribute-conflicts/'+encodeURIComponent(value.conflict_set_id)+'/members/'+encodeURIComponent(id)+(sources?'/sources':'/read'):base+'/'+encodeURIComponent(id)+(sources?'/sources':'');
    async function load(more=false,acceptRevision=false){
      if(disposed||!accepts()||(more&&(state.loading||!state.next_cursor)))return;
      const epoch=more?generation:++generation,cursor=more?state.next_cursor:null,owner=clone(state.entity);
      state.loading=true;state.error='';
      if(!more){conflictGeneration++;state.groups=[];state.conflict_sets=[];state.conflict_next_cursor=null;state.conflict_loading=false;state.conflict_error='';state.values=Object.create(null);state.sources=Object.create(null);state.next_cursor=null;}
      emit();
      try{
        const result=await request(base,{...fixed,limit:20,cursor,...(state.predicate?{predicate:state.predicate}:{}),...(state.focus_record_id?{focus_record_id:state.focus_record_id}:{})});
        if(!valid(epoch))return;
        if(!result.entity||result.entity.id!==entity.id||!acceptRevision&&!sameVersion(owner,result.entity))throw changed();
        if(more&&state.watermark!==result.watermark)throw changed();
        if(state.focus_record_id&&state.focus_expected_version!==null){
          const focused=(result.groups||[]).flatMap(group=>group.values||[]).find(value=>value.record_id===state.focus_record_id);
          if(!matchesFactVersion(state.focus_expected_version,focused))throw Error('检索命中的属性版本已变化或无法核对，请重新检索，或点击刷新读取当前范围的最新值。');
        }
        for(const incoming of result.groups||[]){
          const group=state.groups.find(item=>item.predicate===incoming.predicate);
          if(!group)state.groups.push(clone(incoming));
          else{const ids=new Set(group.values.map(value=>value.record_id));group.values.push(...incoming.values.filter(value=>!ids.has(value.record_id)));group.total_values=incoming.total_values;}
        }
        Object.assign(state,{entity:result.entity,property_count:result.property_count,value_count:result.value_count,next_cursor:result.next_cursor,watermark:result.watermark});
        if(!more&&state.focus_record_id&&acceptRevision)state.focus_refreshed=true;
        if(!more){state.conflict_sets=result.conflict_sets||[];state.conflict_next_cursor=result.conflict_next_cursor||null;}
      }catch(error){if(valid(epoch))state.error=error.message;}
      finally{if(valid(epoch)){state.loading=false;emit();}}
    }
    async function loadMoreConflicts(){
      if(disposed||!accepts()||state.loading||state.conflict_loading||!state.conflict_next_cursor)return;
      const epoch=generation,ticket=++conflictGeneration,owner=clone(state.entity),cursor=state.conflict_next_cursor;
      const current=()=>valid(epoch)&&ticket===conflictGeneration;
      state.conflict_loading=true;state.conflict_error='';emit();
      try{
        const result=await request('/api/projects/'+encodeURIComponent(projectId)+'/attribute-conflicts',{...fixed,entity_id:entity.id,limit:20,cursor});
        if(!current())return;
        if(result.entity_id&&result.entity_id!==entity.id||result.entity&&!sameVersion(owner,result.entity))throw changed();
        const incoming=result.conflict_sets||[];
        for(const set of incoming){const previous=state.conflict_sets.find(item=>item.id===set.id);if(previous&&previous.version!==set.version)throw changed();}
        const ids=new Set(state.conflict_sets.map(set=>set.id));state.conflict_sets.push(...incoming.filter(set=>!ids.has(set.id)));
        state.conflict_next_cursor=result.next_cursor||null;
      }catch(error){if(current())state.conflict_error=error.message;}
      finally{if(current()){state.conflict_loading=false;emit();}}
    }
    async function toggleValue(id){
      const value=fact(id);if(!value||!accepts()||disposed)return;
      const entry=state.values[id]||(state.values[id]={expanded:false,loading:false,error:''});
      if(entry.loading)return;
      if(entry.expanded){entry.expanded=false;emit();return;}
      if(entry.text!==undefined){entry.expanded=true;emit();return;}
      if(!value.value_truncated){entry.text=text(value.value??value.value_preview);entry.expanded=true;emit();return;}
      const epoch=generation;entry.loading=true;entry.error='';emit();
      try{
        const result=await request(factUrl(id,value),{...fixed,expected_version_id:value.version_id});
        if(!valid(epoch))return;
        if(result.id!==id||!sameVersion(value,result))throw changed();
        entry.text=text(result.value);entry.expanded=true;
      }catch(error){if(valid(epoch))entry.error=error.message;}
      finally{if(valid(epoch)){entry.loading=false;emit();}}
    }
    async function loadSources(id,more=false){
      const value=fact(id);if(!value||!accepts()||disposed)return;
      const entry=state.sources[id]||(state.sources[id]={expanded:true,items:[],next_cursor:null,loading:false,error:''});
      if(entry.loading||more&&!entry.next_cursor)return;
      const epoch=generation;entry.loading=true;entry.expanded=true;entry.error='';entry.retryMore=more;emit();
      try{
        const result=await request(factUrl(id,value,true),{...fixed,expected_version_id:value.version_id,limit:10,cursor:more?entry.next_cursor:null});
        if(!valid(epoch))return;
        if(result.record_id!==id||!sameVersion(value,result))throw changed();
        entry.items=more?[...entry.items,...result.sources]:result.sources;entry.next_cursor=result.next_cursor;
      }catch(error){if(valid(epoch))entry.error=error.message;}
      finally{if(valid(epoch)){entry.loading=false;emit();}}
    }
    return {load:()=>load(),refresh:()=>{state.focus_expected_version=null;return load(false,true);},loadMore:()=>load(true),loadMoreConflicts,selectPredicate:predicate=>{state.focus_record_id=null;state.focus_expected_version=null;state.focus_refreshed=false;state.predicate=predicate||null;return load();},focus:(recordId,predicate,expectedVersion)=>{state.focus_record_id=recordId;state.focus_expected_version=expectedVersion===undefined?null:clone(expectedVersion??{});state.focus_refreshed=false;state.predicate=predicate||null;return load();},toggleValue,loadSources,
      toggleSources(id){const entry=state.sources[id];if(!entry)return loadSources(id);entry.expanded=!entry.expanded;emit();},
      snapshot:()=>clone(state),dispose(){disposed=true;generation++;}};
  }
  function summaryHtml(result){
    const entries=(result.groups||[]).flatMap(group=>(group.values||[]).map(value=>({group,value}))).slice(0,3);
    const warning=result.conflict_count||result.conflict_sets?.length||entries.some(({value})=>conflict(value));
    return `<div class="attribute-hover-summary"><strong>属性 ${Number(result.property_count)||0} 项 · ${Number(result.value_count)||0} 个值</strong>${entries.map(({group,value})=>`<div><span>${escape(group.label||group.predicate)}</span>：${escape(Array.from(text(value.value_preview??value.value)).slice(0,60).join(''))}${value.value_truncated?'…':''}</div>`).join('')}${warning?'<small class="attribute-conflict">存在冲突 · 点击查看详情</small>':''}</div>`;
  }
  function scopeHtml(value){
    const applicability=value.applicability||value.metadata?.applicability;
    if(!applicability)return '';
    return `<span>适用范围：${escape(text(applicability))}</span>`;
  }
  function sourceStateHtml(source){
    const labels={accepted:'已接受',contradicting:'冲突待复核',pending:'待审核',rejected:'已拒绝',superseded:'已取代',withdrawn:'已撤回'};
    const parts=[source.status?(labels[source.status]||source.status):'',source.source_active===false?'来源已撤回（仅供历史复核）':'',Number.isInteger(source.decision_version)?'决定修订 r'+source.decision_version:''].filter(Boolean);
    return parts.length?`<small class="attribute-source-state">${escape(parts.join(' · '))}</small>`:'';
  }
  function sourceListHtml(evidence,id){return evidence.expanded?`<section class="attribute-value-sources" aria-label="这个值的来源">${evidence.loading?'<p role="status">正在读取来源…</p>':''}${evidence.error?`<p class="attribute-panel-error" role="alert">${escape(evidence.error)} <button type="button" class="secondary" data-attribute-action="retry-sources" data-record="${escape(id)}">重试</button></p>`:''}${(evidence.items||[]).map((source,index)=>`<article class="attribute-source"><strong>${escape(source.document_title||'历史来源')}</strong><small>文档版本：${escape(source.document_version_id||'未保存')} · ${escape(interval(source))}</small>${sourceStateHtml(source)}${scopeHtml(source)}<blockquote>${escape(source.quote||'原文引文未保存')}${source.quote_truncated?'…':''}</blockquote><small>${source.chunk_id?'切片 '+escape(source.chunk_id)+' · ':''}${Number.isInteger(source.start_char)?'原文位置 '+source.start_char+'–'+source.end_char:'未保存精确位置'}</small>${source.document_version_id?`<button type="button" class="secondary" data-attribute-action="source" data-record="${escape(id)}" data-source-index="${index}">在原文中打开 ↗</button>`:''}</article>`).join('')}${!evidence.loading&&!evidence.error&&!evidence.items?.length?'<p class="attribute-panel-notice">没有可定位的文档来源；可能由手工维护。</p>':''}${evidence.next_cursor?`<button type="button" class="secondary" data-attribute-action="more-sources" data-record="${escape(id)}" ${evidence.loading?'disabled':''}>更多来源 · 每页 10 条</button>`:''}</section>`:'';}
  function renderHtml(state){
    const groups=state.groups||[];
    const values=state.values||{},sources=state.sources||{};
    return `<div class="entity-attribute-panel" aria-busy="${Boolean(state.loading||state.conflict_loading)}"><header class="attribute-panel-heading"><div><h4>实体属性</h4><p>${Number(state.property_count)||0} 项属性 / ${Number(state.value_count)||0} 个值</p>${state.entity?.version?`<p class="attribute-owner-revision">${escape(state.entity.text||'所属实体')} · 实体修订 r${Number(state.entity.version)}</p>`:''}</div><button type="button" data-attribute-action="refresh" class="secondary" ${state.loading?'disabled':''}>刷新</button></header>${state.readOnly?'<p class="attribute-history-note">历史知识范围 · 只读</p>':''}
      ${groups.length?`<nav class="attribute-group-index" aria-label="按属性筛选"><button type="button" data-attribute-action="predicate" data-predicate="" aria-pressed="${!state.predicate}">全部</button>${groups.map(group=>`<button type="button" data-attribute-action="predicate" data-predicate="${escape(group.predicate)}" aria-pressed="${state.predicate===group.predicate}">${escape(group.label||group.predicate)} <span>${Number(group.total_values)||0}</span></button>`).join('')}</nav>`:''}
      ${state.loading&&!groups.length?'<p class="attribute-panel-notice" role="status">正在读取当前范围的属性…</p>':''}
      ${state.focus_record_id?`<p class="attribute-focus-note">${state.loading?'正在定位属性值…':state.error?'检索命中的属性值未加载；请核对版本或显式刷新。':state.focus_refreshed?'已刷新为当前范围的最新属性值；可能与原检索命中不同。':'已定位检索命中的属性值。'}<button type="button" class="secondary" data-attribute-action="predicate" data-predicate="">查看全部属性</button></p>`:''}
      ${state.error?`<div class="attribute-panel-error" role="alert">${escape(state.error)} <button type="button" data-attribute-action="refresh" class="secondary">重试并刷新</button></div>`:''}
      ${!state.loading&&!state.error&&!groups.length&&!state.conflict_sets?.length?'<p class="attribute-panel-notice">当前范围没有已接受的属性值。</p>':''}
      ${(state.conflict_sets||[]).map(set=>`<section class="attribute-conflict-set" data-conflict-set="${escape(set.id)}"><header><h5>${escape(set.label||set.predicate_label||set.predicate)}</h5><span class="attribute-status">未解决冲突</span></header><p>身份已统一，属性约束待复核；这些值不作为确定事实。</p>${(set.members||[]).map(member=>{
        const id=member.record_id,full=values[id]||{},evidence=sources[id]||{};
        return `<article class="attribute-conflict-member" data-attribute-record="${escape(id)}" tabindex="-1"><div class="attribute-fact-value${full.expanded?' is-expanded':''}">${escape(full.expanded?full.text:preview(member.value_preview??text(member.value)))}${member.value_truncated&&!full.expanded?'…':''}</div><small>${escape(String(member.datatype||'').split(/[#/]/).pop()||'数据类型待核')} · ${escape(interval(member))}${member.document_count!==undefined?' · '+Number(member.document_count)+' 份文档':''}${member.support_count!==undefined?' · '+Number(member.support_count)+' 条支撑':' · 支撑待核'}</small>${scopeHtml(member)}<div class="attribute-fact-actions">${member.value_truncated?`<button type="button" class="secondary" data-attribute-action="value" data-record="${escape(id)}" aria-expanded="${Boolean(full.expanded)}" ${full.loading?'disabled':''}>${full.loading?'正在读取全文…':full.expanded?'收起全文':'展开全文'}</button>`:''}<button type="button" class="secondary" data-attribute-action="sources" data-record="${escape(id)}" aria-expanded="${Boolean(evidence.expanded)}">${evidence.expanded?'收起来源':'查看来源'}</button></div>${full.error?`<p class="attribute-panel-error" role="alert">${escape(full.error)} <button type="button" class="secondary" data-attribute-action="value" data-record="${escape(id)}">重试全文</button></p>`:''}${sourceListHtml(evidence,id)}</article>`;
      }).join('')}<button type="button" data-attribute-action="review-set" data-conflict-set="${escape(set.id)}" ${state.readOnly||!state.canReview?'disabled title="当前范围只读或没有审核权限"':''}>处理冲突</button><p class="attribute-review-note" role="status"></p></section>`).join('')}
      ${state.conflict_error?`<p class="attribute-panel-error" role="alert">${escape(state.conflict_error)} <button type="button" class="secondary" data-attribute-action="more-conflicts">重试冲突分页</button></p>`:''}
      ${state.conflict_next_cursor?`<div class="attribute-conflict-pagination"><p>已加载 ${state.conflict_sets?.length||0} 个冲突集合，还有未加载的冲突集合。</p><button type="button" class="secondary" data-attribute-action="more-conflicts" ${state.conflict_loading?'disabled':''}>${state.conflict_loading?'正在读取更多冲突…':'继续加载冲突集合 · 每页 20 组'}</button></div>`:''}
      ${groups.map(group=>`<section class="attribute-fact-group"><header><h5>${escape(group.label||group.predicate)}</h5><span>${Number(group.total_values)||0} 个值</span></header><small class="attribute-predicate" title="${escape(group.predicate)}">${escape(group.predicate)}</small>${(group.values||[]).map(value=>{
        const id=value.record_id,full=values[id]||{},evidence=sources[id]||{},disputed=conflict(value);
        const body=full.expanded?full.text:text(value.value_preview??value.value);
        const status=disputed?'冲突待复核':value.status==='accepted'?'已接受':value.status==='pending'?'待审核':value.status||'状态未知';
        const history=value.valid_until&&Date.parse(value.valid_until)<=Date.parse(state.valid_at||new Date().toISOString());
        const valueKind=!disputed&&(history?'历史区间值':group.total_values>1?'多个已接受值':'');
        return `<article class="attribute-fact${disputed?' is-conflict':''}" data-attribute-record="${escape(id)}" tabindex="-1"><div class="attribute-fact-state"><span class="attribute-status">${escape(status)}</span><span>${escape(String(value.datatype||'').split(/[#/]/).pop()||'数据类型未知')}</span>${valueKind?'<span>'+valueKind+'</span>':''}</div><div class="attribute-fact-value${full.expanded?' is-expanded':''}">${escape(full.expanded?body:preview(body))}${value.value_truncated&&!full.expanded?'<span class="attribute-ellipsis">…</span>':''}</div><div class="attribute-fact-meta"><span>${escape(interval(value))}</span>${scopeHtml(value)}<span>${Number(value.document_count)||0} 份文档 · ${Number(value.accepted_support_count)||0} 条支撑</span></div><div class="attribute-fact-actions">${value.value_truncated?`<button type="button" class="secondary" data-attribute-action="value" data-record="${escape(id)}" aria-expanded="${Boolean(full.expanded)}" ${full.loading?'disabled':''}>${full.loading?'正在读取全文…':full.expanded?'收起全文':'展开全文'}${!full.expanded&&value.value_length?' · '+Number(value.value_length)+' 字':''}</button>`:''}<button type="button" class="secondary" data-attribute-action="sources" data-record="${escape(id)}" aria-expanded="${Boolean(evidence.expanded)}">${evidence.expanded?'收起来源':'查看来源'}</button>${disputed?`<button type="button" data-attribute-action="review" data-record="${escape(id)}" ${state.readOnly||!state.canReview?'disabled title="当前范围只读或没有审核权限"':''}>处理冲突</button>`:''}</div>${full.error?`<p class="attribute-panel-error" role="alert">${escape(full.error)} <button type="button" class="secondary" data-attribute-action="value" data-record="${escape(id)}">重试全文</button></p>`:''}
        ${sourceListHtml(evidence,id)}</article>`;
      }).join('')}</section>`).join('')}
      ${state.next_cursor?`<button type="button" class="secondary attribute-more" data-attribute-action="more" ${state.loading?'disabled':''}>${state.loading?'正在读取…':'继续加载属性值 · 每页 20 条'}</button>`:''}</div>`;
  }
  const mounts=new WeakMap();let latest=null,drawer=null,drawerTrigger=null;
  function mount(host,{projectId,entity,scope={},request=root.api,accepts=()=>true,onReview,onEntityChange,focusRecordId,predicate,expectedFactVersion}={}){
    mounts.get(host)?.dispose();
    const fixed=cleanScope(scope),scopeTicket=root.WorkspaceContext?.ticket(projectId,root.WorkspaceContext?.page||'search'),page=root.WorkspaceContext?.page||'search';
    const version=JSON.stringify(root.wb?.versionContext||null);
    let observedOwner=clone(entity),ownerRefresh=null,mounted=true;
    const live=()=>mounted&&host.isConnected!==false&&accepts()&&(!root.document?.getElementById('project')?.value||root.document.getElementById('project').value===projectId)&&version===JSON.stringify(root.wb?.versionContext||null)&&(!scopeTicket||root.WorkspaceContext.accepts(scopeTicket,projectId,page));
    function notifyOwner(state){
      if(!live()||!onEntityChange||state.loading||state.error||sameVersion(observedOwner,state.entity)||ownerRefresh)return;
      const owner=clone(state.entity);
      ownerRefresh=Promise.resolve().then(()=>live()?onEntityChange(owner):null).then(()=>{observedOwner=owner;}).catch(()=>{}).finally(()=>{
        ownerRefresh=null;const latestState=controller.snapshot();
        if(!sameVersion(owner,latestState.entity))notifyOwner(latestState);
      });
    }
    const controller=createController({projectId,entity,scope:fixed,request,accepts:live,onChange:state=>{
      const active=host.contains(root.document.activeElement)?root.document.activeElement:null,action=active?.dataset.attributeAction,id=active?.dataset.record;
      host.innerHTML=renderHtml({...state,valid_at:fixed.valid_at,readOnly:Boolean(fixed.known_at),canReview:Boolean(root.Auth?.isAdmin?.())});
      if(action)Array.from(host.querySelectorAll('[data-attribute-action]')).find(button=>button.dataset.attributeAction===action&&button.dataset.record===id)?.focus({preventScroll:true});
      if(focusRecordId&&!state.loading){const target=Array.from(host.querySelectorAll('[data-attribute-record]')).find(node=>node.dataset.attributeRecord===focusRecordId);if(target){target.focus({preventScroll:true});target.scrollIntoView({block:'nearest'});focusRecordId=null;}}
      notifyOwner(state);
    }});
    const handler=async event=>{
      const button=event.target.closest('[data-attribute-action]');if(!button||!host.contains(button)||button.disabled||!live())return;
      const id=button.dataset.record,state=controller.snapshot();
      switch(button.dataset.attributeAction){
        case 'refresh':return controller.refresh();case 'more':return controller.loadMore();case 'more-conflicts':return controller.loadMoreConflicts();case 'predicate':return controller.selectPredicate(button.dataset.predicate);
        case 'value':return controller.toggleValue(id);case 'sources':return controller.toggleSources(id);case 'retry-sources':return controller.loadSources(id,Boolean(state.sources[id]?.retryMore));case 'more-sources':return controller.loadSources(id,true);
        case 'source':{const source=state.sources[id]?.items[Number(button.dataset.sourceIndex)];if(source?.document_version_id)root.SourceReader?.open({projectId,versionId:source.document_version_id,location:source,focusText:source.quote});return;}
        case 'review-set':{
          if(fixed.known_at||!root.Auth?.isAdmin?.())return;
          const set=state.conflict_sets.find(item=>item.id===button.dataset.conflictSet);if(!set)return;
          if(root.openAttributeConflictReview)return root.openAttributeConflictReview(set,{projectId,entity:state.entity,scope:fixed,onResolved:()=>controller.refresh()});
          const note=button.parentElement.querySelector('.attribute-review-note');note.textContent='请到知识台账 → 知识审核 → 属性事实，按此实体和属性查找冲突集合。';return;
        }
        case 'review':if(!fixed.known_at&&root.Auth?.isAdmin?.()){const value=state.groups.flatMap(group=>group.values).find(item=>item.record_id===id);if(onReview)onReview(value,()=>controller.refresh());else root.TaskReview?.open({entityId:entity.id,attributeId:id,conflictSetId:value?.conflict_set_id});}return;
      }
    };
    host.addEventListener('click',handler);
    const result={controller,dispose(){mounted=false;controller.dispose();host.removeEventListener('click',handler);},focus(id,selectedPredicate,expectedVersion){focusRecordId=id;return controller.focus(id,selectedPredicate,expectedVersion);}};
    mounts.set(host,result);latest=result;
    if(focusRecordId)controller.focus(focusRecordId,predicate,expectedFactVersion);else if(predicate)controller.selectPredicate(predicate);else controller.load();return result;
  }
  function open(options){
    if(!root.document)return;
    if(!drawer){
      drawer=root.document.createElement('dialog');drawer.className='entity-attribute-dialog';drawer.setAttribute('aria-labelledby','entity-attribute-drawer-title');drawer.innerHTML='<header class="attribute-drawer-heading"><div><small>实体详情</small><h3 id="entity-attribute-drawer-title"></h3></div><button type="button" class="secondary" aria-label="关闭实体属性">关闭 ×</button></header><div class="attribute-drawer-body"></div>';root.document.body.append(drawer);drawer.querySelector('button').onclick=()=>drawer.close();
      drawer.addEventListener('close',()=>{if(drawer.open)return;mounts.get(drawer.querySelector('.attribute-drawer-body'))?.dispose();if(drawerTrigger?.isConnected)drawerTrigger.focus({preventScroll:true});});
      root.document.addEventListener('keydown',event=>{if(event.key==='Escape'&&drawer.open&&!root.document.querySelector('dialog[open]:modal')){event.preventDefault();drawer.close();}});
      for(const event of ['workspace:scope-changed','workspace:page-changed','graph-workspace:invalidated'])root.document.addEventListener(event,()=>{if(drawer.open)drawer.close();});
      root.document.getElementById('project')?.addEventListener('change',()=>{if(drawer.open)drawer.close();});
    }
    drawer.querySelector('h3').textContent=options.entity.text||options.entity.id;
    if(!drawer.open){drawerTrigger=root.document.activeElement;drawer.show();}return mount(drawer.querySelector('.attribute-drawer-body'),{...options,accepts:()=>drawer.open&&(!options.accepts||options.accepts())});
  }
  const hoverCache=new Map();
  async function hoverHtml(entity,{projectId,scope={},request=root.api}={}){
    const fixed=cleanScope(scope),key=JSON.stringify([projectId,entity.id,entity.version_id||entity.version,fixed]),prior=hoverCache.get(key);
    if(prior&&Date.now()-prior.at<15000)return prior.promise;
    const promise=(async()=>{
    const result=await request('/api/projects/'+encodeURIComponent(projectId)+'/entities/'+encodeURIComponent(entity.id)+'/attributes',{...fixed,limit:3});
    if(!sameVersion(entity,result.entity||{}))return '<small>知识版本已变化，请重新选择</small>';
    return summaryHtml(result);
    })();
    hoverCache.set(key,{at:Date.now(),promise});if(hoverCache.size>64)hoverCache.delete(hoverCache.keys().next().value);
    try{return await promise;}catch(error){hoverCache.delete(key);throw error;}
  }
  return {createController,renderHtml,summaryHtml,mount,open,hoverHtml,focus:(id,predicate,expectedVersion)=>latest?.focus(id,predicate,expectedVersion)};
});
