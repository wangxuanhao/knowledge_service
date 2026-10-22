/* Searchable ontology catalog and cards. All edits go through OntologyModel. */
(() => {
  const KINDS={class:'实体类',relation:'关系',attribute:'属性'};
  const label = term => term.label_zh || term.label || term.name || term.id;
  const summaryIndexes=new WeakMap();
  function summaryIndex(summary) {
    if(!summary||typeof summary!=='object')return {entries:[],byId:new Map(),classes:new Map(),attributes:new Map(),relations:new Map(),cards:new Map()};
    if(summaryIndexes.has(summary))return summaryIndexes.get(summary);
    const entries=[['classes','class'],['relations','relation'],['attributes','attribute']]
      .flatMap(([key,kind])=>(summary[key]||[]).map(term=>({...term,kind})));
    const index={entries,byId:new Map(entries.map(item=>[item.id,item])),classes:new Map((summary.classes||[]).map(item=>[item.id,item])),attributes:new Map(),relations:new Map(),cards:new Map()};
    const add=(map,id,item)=>{if(!map.has(id))map.set(id,[]);map.get(id).push(item);};
    for(const attr of summary.attributes||[])if(attr.active!==false)for(const id of attr.domain||[])add(index.attributes,id,attr);
    for(const relation of summary.relations||[])for(const id of new Set([...(relation.domain||[]),...(relation.range||[])]))add(index.relations,id,relation);
    summaryIndexes.set(summary,index);return index;
  }
  function catalogEntries(summary, options={}) {
    const query=(options.query||'').trim().toLocaleLowerCase();
    return summaryIndex(summary).entries
      .filter(term=>(!options.kind||options.kind===term.kind)&&(!options.activeOnly||term.active!==false)
        &&(!query||[term.id,label(term),term.description||''].join(' ').toLocaleLowerCase().includes(query)));
  }
  function typeCard(term, summary) {
    const index=summaryIndex(summary),item=index.byId.get(term.id)||term;
    if(index.cards.has(item.id))return index.cards.get(item.id);
    const names=ids=>(ids||[]).map(id=>label(index.byId.get(id)||{id})).join('、');
    const ancestorIds=new Set(),byId=index.classes;
    const visit=id=>{if(id===item.id||ancestorIds.has(id))return;ancestorIds.add(id);for(const p of byId.get(id)?.parents||[])visit(p);};
    for(const parent of item.parents||[])visit(parent);
    const property=attr=>({...attr,datatype:(attr.range||[]).map(value=>String(value).split(/[#/]/).pop().replace(/^xsd:/,'')).join(' | ')||'未声明',
      required:attr.required===true||Number(attr.min_count??attr.minCount??0)>0});
    const attributes=[...new Map([...ancestorIds].flatMap(id=>index.attributes.get(id)||[]).map(attr=>[attr.id,attr])).values()];
    const card={...item, parents:[...(item.parents||[])],
      attributes:(index.attributes.get(item.id)||[]).map(property),
      inheritedAttributes:attributes.filter(attr=>!(attr.domain||[]).includes(item.id))
        .map(attr=>({...property(attr),inheritedFrom:(attr.domain||[]).filter(id=>ancestorIds.has(id))})),
      relations:index.relations.get(item.id)||[],
      endpointText:`${names(item.domain)||'未指定主体'} → ${names(item.range)||'未指定客体'}`};
    index.cards.set(item.id,card);return card;
  }
  // A secondary parent gets an addressable reference. Path checks also expose malformed cycles safely.
  function hierarchyRows(summary,options={}) {
    const classes=(summary?.classes||[]).filter(c=>c.active!==false),byId=new Map(classes.map(c=>[c.id,c]));
    const children=new Map(classes.map(c=>[c.id,[]])),roots=[];
    for(const c of classes){const parents=[...new Set(c.parents||[])].filter(id=>byId.has(id));
      if(!parents.length)roots.push(c.id);for(const id of parents)children.get(id).push(c.id);}
    const sort=ids=>ids.sort((a,b)=>String(label(byId.get(a))).localeCompare(String(label(byId.get(b))),'zh-CN'));
    sort(roots);children.forEach(sort);
    const coverage=new Set(),cover=id=>{if(coverage.has(id))return;coverage.add(id);for(const child of children.get(id)||[])cover(child);};
    roots.forEach(cover);for(const c of classes)if(!coverage.has(c.id)){roots.push(c.id);cover(c.id);}
    const seen=new Set(),rows=[],collapsed=options.collapsed||new Set();
    const descendants=id=>{const found=new Set([id]),walk=next=>{for(const child of children.get(next)||[])if(!found.has(child)){found.add(child);walk(child);}};walk(id);return found.size-1;};
    const walk=(id,depth,parentId,path)=>{const term=byId.get(id),cycle=path.has(id),reference=seen.has(id),kids=children.get(id)||[];
      const expanded=!collapsed.has(id)&&!reference&&!cycle;seen.add(id);
      rows.push({...term,id,depth,parentId,reference,cycle,expanded,childCount:kids.length,descendantCount:descendants(id),
        externalParents:(term.parents||[]).filter(parent=>!byId.has(parent))});
      if(expanded){const next=new Set(path);next.add(id);for(const child of kids)walk(child,depth+1,id,next);}};
    for(const id of roots)walk(id,0,null,new Set());return rows;
  }
  function sourceWriteAllowed(opened,current) {
    return !current.preview&&!opened.preview&&['project','draftId','revision','ontologyId'].every(key=>opened[key]===current[key]);
  }
  function sparqlRequest(snapshot,query) {
    if(typeof query!=='string'||!query.trim()||query.length>10000)throw new Error('请填写不超过 10000 字符的 SPARQL 查询。');
    return {query:query.trim(),ontology_id:snapshot.ontologyId||null,include_unknown:true};
  }
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  let side=null,cards=null,ctx=null,list=null,search=null,kind=null,active=null,summaryLine=null,drafts=null,work=null,changes=null,draftError=null;
  let projectKey='';
  const project=()=>typeof current==='undefined'?'':current;
  const button=(text,action,cls='om-btn ghost')=>{const node=el('button',cls,text);node.type='button';node.addEventListener('click',action);return node;};
  const run=action=>Promise.resolve().then(action).catch(error=>ctx.status(error.message,true));
  function renderEntries(state) {
    if(!list||!cards)return;
    const entries=catalogEntries(state.summary,{query:search.value,kind:kind.value,activeOnly:active.checked});
    summaryLine.textContent=state.loading?'正在读取本体与草案…':`${entries.length} 个术语 · ${state.preview?'历史只读':state.showingDraft?'草案未发布':'已发布'}`;
    list.replaceChildren();cards.replaceChildren();
    for(const entry of entries){
      const pick=button(label(entry),()=>ctx.select(entry.id),'om-catalog-item'+(state.selected===entry.id?' is-selected':''));
      pick.append(el('small',null,`${KINDS[entry.kind]}${entry.active===false?' · 已停用':''}`));
      pick.title=entry.id;pick.setAttribute('aria-pressed',String(state.selected===entry.id));list.append(pick);
      if(state.view!=='cards')continue;
      const model=typeCard(entry,state.summary),card=el('article','om-type-card om-type-'+entry.kind+(state.selected===entry.id?' is-selected':''));
      const title=button(label(entry),()=>ctx.select(entry.id),'om-type-card-title');
      card.append(el('small','om-type-kind',`${KINDS[entry.kind]}${entry.active===false?' · 已停用':''}`),title,el('code','om-type-iri',entry.id));
      if(entry.description)card.append(el('p','om-type-description',entry.description));
      if(entry.kind==='class'){
        const parents=el('div','om-type-members');
        if(!model.parents.length)parents.append(el('small',null,'顶层类 · 未声明父类'));
        for(const id of model.parents)parents.append(button('↑ '+label(summaryIndex(state.summary).byId.get(id)||{id}),()=>ctx.select(id),'om-type-link'));
        card.append(parents);
        const members=el('div','om-type-members');
        for(const attr of model.attributes)members.append(button(`${label(attr)}${attr.required?' *':''} · ${attr.datatype}`,()=>ctx.select(attr.id),'om-type-link'));
        if(model.inheritedAttributes.length)members.append(el('small',null,`继承 ${model.inheritedAttributes.length} 项属性 · ${model.parents.length} 个父类`));
        for(const rel of model.relations)members.append(button('关系 · '+label(rel),()=>ctx.select(rel.id),'om-type-link'));
        if(!model.attributes.length&&!model.relations.length)members.append(el('small',null,'尚未声明属性或业务关系'));
        card.append(members);
      }else card.append(el('p','om-type-endpoints',model.endpointText));
      const actions=el('div','om-type-card-actions');
      actions.append(button('检查定义',()=>ctx.select(entry.id)));
      if(!state.preview&&entry.kind==='class'){
        actions.append(button('＋ 子类',()=>ctx.openCreate({kind:'class',parent:entry.id})),
          button('＋ 属性',()=>ctx.openCreate({kind:'attribute',domain:entry.id})),
          button('＋ 关系',()=>ctx.openCreate({kind:'relation',domain:entry.id})));
      }
      card.append(actions);cards.append(card);
    }
    if(!entries.length){list.append(el('p','om-catalog-empty',state.loading?'正在读取本体与草案…':'没有匹配的术语。'));if(state.view==='cards')cards.append(el('p','om-catalog-empty',state.loading?'正在读取本体…':'没有匹配的术语，可调整搜索或插入新术语。'));}
  }
  function renderDrafts(state) {
    if(!drafts)return;
    drafts.replaceChildren();
    const placeholder=el('option',null,'选择待处理草案');placeholder.value='';drafts.append(placeholder);
    for(const draft of (state.draftChoices||[]).filter(item=>item.status==='pending')){
      const option=el('option',null,`${draft.title||'未命名草案'} · r${draft.revision||'?'} · ${draft.id.slice(0,8)}`);
      option.value=draft.id;drafts.append(option);
    }
    drafts.value=state.draft?.id||'';
    drafts.title=state.draftLoadError||'';
    if(draftError)draftError.textContent=state.draftLoadError||'';
  }
  async function showSource() {
    const p=project(),state=ctx.S;
    const snapshot=()=>({project:project(),draftId:ctx.S.showingDraft?ctx.S.draft?.id:null,
      revision:ctx.S.showingDraft?ctx.S.draft?.revision:null,ontologyId:ctx.S.ontologyId,preview:!!ctx.S.preview});
    const opened=snapshot();
    try{
      const url=state.preview?`/ontology?ontology_id=${encodeURIComponent(state.preview.id)}`
        :state.showingDraft&&state.draft?`/ontology-drafts/${encodeURIComponent(state.draft.id)}`:'/ontology';
      const data=await api(endpoint(url),undefined,'GET');if(p!==project())return;
      const dialog=el('dialog','om-source-dialog');
      dialog.append(el('h3',null,'RDF / Turtle 原始定义'),el('p',null,'保留原始图与尚未图形化的 RDF 表达。历史版本只读；编辑会将差异加入草案，审核发布后生效。'),el('pre',null,data.turtle||'（空本体）'));
      if(!opened.preview){
        const editor=el('textarea','om-source-editor');editor.value=data.turtle||'';editor.rows=18;editor.hidden=true;
        editor.setAttribute('aria-label','编辑 Turtle 原始定义');
        const feedback=el('p');feedback.setAttribute('role','status');
        const save=button('把源码差异加入草案',async()=>{
          save.disabled=true;
          try{
            if(!sourceWriteAllowed(opened,snapshot())||(opened.draftId&&data.revision!==opened.revision))
              throw new Error('项目、草案或修订已变化，请关闭后重新读取源码。');
            await ctx.sendCommand({action:'diff_turtle',edited_turtle:editor.value,reason:'源码编辑：编译差异进入草案审核'});
            feedback.textContent='源码差异已加入草案，仍需校验、逐项审核和发布。';
          }catch(error){feedback.textContent=error.message;save.disabled=false;}
        });save.hidden=true;
        const edit=button('编辑 Turtle',()=>{editor.hidden=false;save.hidden=false;edit.hidden=true;editor.focus();});
        dialog.append(edit,editor,save,feedback);
      }
      const download=button('导出 Turtle',()=>{
        const blob=new Blob([data.turtle||''],{type:'text/turtle;charset=utf-8'}),url=URL.createObjectURL(blob),link=el('a');
        link.href=url;link.download=`ontology-${data.id||state.draft?.id||'draft'}.ttl`;link.click();URL.revokeObjectURL(url);
      });
      const queryArea=el('details','om-source-query');queryArea.append(el('summary',null,'高级查询 · SPARQL（只读）'),el('p',null,'查询当前项目的已发布本体与知识；历史本体按本窗口所选发布读取，未发布草案源码不参与。不会继承其他页面隐藏的 Metadata 条件。'));
      const query=el('textarea');query.rows=4;query.value='SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20';query.setAttribute('aria-label','SPARQL 查询');
      const queryResult=el('pre');queryResult.setAttribute('role','status');
      const queryButton=button('运行只读查询',async()=>{queryButton.disabled=true;try{if(p!==project())throw new Error('项目已变化，请重新打开查询窗口。');const body=sparqlRequest({ontologyId:opened.preview?state.preview.id:opened.ontologyId},query.value);const result=await api(endpoint('/sparql'),body);if(p!==project())return;queryResult.textContent=JSON.stringify(result,null,2);}catch(error){queryResult.textContent=error.message;}finally{queryButton.disabled=false;}});
      queryArea.append(query,queryButton,queryResult);
      const close=button('关闭',()=>{dialog.close();dialog.remove();});dialog.append(download,queryArea,close);document.body.append(dialog);dialog.showModal();
      dialog.addEventListener('cancel',()=>dialog.remove());
    }catch(error){ctx.status('源码读取失败：'+error.message,true);}
  }
  function mount(sideHost,cardHost,controller) {
    side=sideHost;cards=cardHost;ctx=controller;if(!side||!cards)return;
    projectKey=project();side.replaceChildren();
    side.append(el('h3','om-catalog-title','本体目录'));
    search=el('input','om-catalog-search');search.type='search';search.placeholder='搜索名称、说明或 IRI';search.setAttribute('aria-label','搜索本体术语');
    kind=el('select','om-catalog-kind');kind.setAttribute('aria-label','术语种类');
    for(const [value,text] of [['','全部术语'],...Object.entries(KINDS)]){const option=el('option',null,text);option.value=value;kind.append(option);}
    active=el('input');active.type='checkbox';const activeLabel=el('label','om-catalog-active');activeLabel.append(active,el('span',null,'只看有效术语'));
    summaryLine=el('p','om-catalog-count');list=el('div','om-catalog-list');
    drafts=el('select','om-catalog-drafts');drafts.setAttribute('aria-label','选择草案');drafts.addEventListener('change',()=>{if(drafts.value)void run(()=>ctx.selectDraft(drafts.value));});
    const advanced=el('details','om-catalog-governance');
    advanced.append(el('summary',null,'高级'),button('RDF 源码 / 导出',()=>void showSource()));
    side.append(search,kind,activeLabel,summaryLine,list,advanced);
    work=document.getElementById?.('om-work-changes-host');
    if(work){
      work.replaceChildren();const management=el('details','om-draft-management');management.append(el('summary',null,'草案管理'));
      draftError=el('p','om-draft-error');draftError.setAttribute('role','status');
      const tools=el('div','om-catalog-tools');
      const newDraft=button('另开一轮修改',()=>void run(()=>ctx.createDraft()));newDraft.dataset.ontologyWrite='true';
      const closeDraft=button('关闭当前草案',()=>void run(()=>ctx.closeDraft()));closeDraft.dataset.ontologyWrite='true';
      tools.append(newDraft,closeDraft,button('重读草案列表',()=>void run(()=>ctx.refresh())));
      management.append(drafts,draftError,tools);changes=el('div','om-work-operations');work.append(management,changes);
    }
    for(const control of [search,kind,active])control.addEventListener(control===search?'input':'change',()=>renderEntries(ctx.S));
    render(ctx.S);
  }
  function render(state) {
    if(!side||!ctx)return;
    if(projectKey!==project()){projectKey=project();search.value='';kind.value='';active.checked=false;}
    side.querySelectorAll('[data-ontology-write]').forEach(node=>node.disabled=!!state.preview);
    work?.querySelectorAll('[data-ontology-write]').forEach(node=>node.disabled=!!state.preview);
    if(changes){changes.replaceChildren();const draft=state.draft?.status==='pending'?state.draft:null;
      changes.append(el('p',null,draft?`本轮修改 ${draft.id} · r${draft.revision??'?'} · ${draft.operations?.length??0} 项变更`:'尚未明确选择工作草案。'));
      for(const operation of draft?.operations||[]){const row=el('details','om-work-operation');
        row.append(el('summary',null,`${operation.action} · ${operation.target_iri||operation.target||operation.id} · ${operation.status||'未发布'}`),el('pre',null,JSON.stringify(operation,null,2)));changes.append(row);}
    }
    drafts.disabled=!!state.preview;renderEntries(state);renderDrafts(state);
  }
  const apiObject={catalogEntries,typeCard,hierarchyRows,sourceWriteAllowed,sparqlRequest,mount,render};
  if(typeof module!=='undefined'&&module.exports)module.exports=apiObject;
  if(typeof window!=='undefined')window.OntologyCatalog=apiObject;
})();
