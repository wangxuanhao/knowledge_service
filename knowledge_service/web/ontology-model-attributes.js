/* Type-owned property editing. Writes remain governed atomic draft commands. */
(() => {
  const RDFS='http://www.w3.org/2000/01/rdf-schema#',XSD='http://www.w3.org/2001/XMLSchema#';
  const label=term=>term.label_zh||term.label||term.name||term.id;
  const DATATYPES=[['string','文本'],['integer','整数'],['decimal','小数'],['boolean','是／否'],
    ['date','日期'],['dateTime','日期时间'],['anyURI','链接']];
  const local=value=>String(value||'').split(/[#/]/).pop().replace(/^xsd:/,'');
  const datatypeLabel=term=>(term.range||[]).map(value=>{
    const code=local(value);return (DATATYPES.find(([key])=>key===code)?.[1]||code)+' · '+code;
  }).join(' / ')||'未限定数据类型';
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;
    if(text!==undefined)node.textContent=String(text);return node;};
  const button=(text,action,cls='om-btn secondary')=>{const node=el('button',cls,text);node.type='button';
    node.addEventListener('click',action);return node;};
  const field=(text,input)=>{const node=el('label','om-attribute-field');node.append(el('span',null,text),input);return node;};
  const caches=new Map();let cacheProject='';
  function editState(options,key,baseline,initial) {
    const project=options.project();if(project!==cacheProject){cacheProject=project;caches.clear();}
    const stamp=JSON.stringify(baseline),cacheKey=project+':'+key;
    if(caches.get(cacheKey)?.stamp!==stamp)caches.set(cacheKey,{stamp,...initial,error:'',busy:false});
    return caches.get(cacheKey);
  }
  function snapshot(options) {
    const state=options.model.S;return JSON.stringify([options.project(),!!state.preview,state.ontologyId,
      state.draft?.id,state.draft?.revision]);
  }
  function assertWrite(options,opened,node) {
    if(options.model.S.preview||snapshot(options)!==opened||node.isConnected===false)
      throw Error('项目、草案或版本已变化，请重新打开属性编辑。');
  }
  const literalSpec=annotation=>Object.fromEntries(['predicate','value','language','datatype']
    .filter(key=>annotation[key]!==undefined&&annotation[key]!==null).map(key=>[key,annotation[key]]));
  const languageRank=annotation=>{const lang=(annotation.language||'').toLowerCase();
    return lang.startsWith('zh')?0:!lang?1:lang.startsWith('en')?2:3;};
  function metadataCommands(term,values) {
    const name=String(values.name||'').trim(),description=String(values.description||'').trim();
    if(!name)throw Error('请填写属性名称。');
    if(name===label(term)&&description===(term.description||''))return [];
    if(!Array.isArray(term.annotations))throw Error('属性注解尚未完整读取，请刷新后重新编辑。');
    const commands=[],annotations=term.annotations;
    const push=(action,spec)=>commands.push({action,target_iri:term.id,...literalSpec(spec)});
    if(name!==label(term)){
      const current=annotations.find(a=>a.predicate===RDFS+'label'&&a.value===term.label_zh
        &&(a.language||'').toLowerCase().startsWith('zh'));
      const language=current?.language||'zh';
      const old=annotations.filter(a=>a.predicate===RDFS+'label'&&(a.language||'').toLowerCase()===language.toLowerCase());
      old.forEach(a=>push('remove_annotation',a));
      push('add_annotation',{predicate:RDFS+'label',value:name,language});
    }
    if(description!==(term.description||'')){
      const comments=annotations.filter(a=>a.predicate===RDFS+'comment')
        .sort((a,b)=>languageRank(a)-languageRank(b)||String(a.language||'').localeCompare(b.language||'')||a.value.localeCompare(b.value));
      const selected=comments.find(a=>a.value===term.description)||comments[0];
      const sameLanguage=comments.filter(a=>(a.language||'')===(selected?.language||''));
      sameLanguage.forEach(a=>push('remove_annotation',a));
      if(description)push('add_annotation',{...selected,predicate:RDFS+'comment',value:description});
    }
    return commands;
  }
  function associationCommands(typeId,before,after) {
    const previous=new Set(before),selected=new Set(after);
    return [...previous].filter(id=>!selected.has(id)).map(id=>({action:'remove_domain',target_iri:id,value:typeId}))
      .concat([...selected].filter(id=>!previous.has(id)).map(id=>({action:'add_domain',target_iri:id,value:typeId})));
  }
  function datatypeSelect(value=XSD+'string') {
    const select=el('select','om-new-input');select.setAttribute('aria-label','属性数据类型');
    const entries=[['','暂不限定'],...DATATYPES.map(([code,name])=>[XSD+code,`${name} · ${code}`])];
    if(value&&!entries.some(([id])=>id===value))entries.push([value,local(value)+'（当前定义）']);
    for(const [id,text] of entries){const option=el('option',null,text);option.value=id;select.append(option);}
    select.value=value;return select;
  }
  function metadataEditor(term,options) {
    if(options.model.S.preview||term.active===false)return null;
    const state=editState(options,'metadata:'+term.id,[term.label_zh,term.label,term.description,term.annotations],
      {name:label(term),description:term.description||''});
    const opened=snapshot(options),box=el('details','om-attribute-editor admin-only');
    box.append(el('summary',null,'编辑名称与说明'));
    const body=el('div','om-attribute-editor-body');
    const name=el('input','om-new-input');name.type='text';name.value=state.name;name.setAttribute('aria-label','属性名称');
    const description=el('textarea','om-new-input');description.rows=3;description.value=state.description;
    description.setAttribute('aria-label','属性说明');description.placeholder='选填：说明这个属性的业务含义';
    const feedback=el('p','om-attribute-message',state.error);feedback.setAttribute('role','status');
    const sync=()=>{feedback.textContent=state.error;save.disabled=state.busy||!name.value.trim()||
      (name.value.trim()===label(term)&&description.value.trim()===(term.description||''));
      name.disabled=description.disabled=state.busy;};
    const save=button('保存名称与说明',async()=>{
      if(state.busy)return;
      try{
        assertWrite(options,opened,box);const commands=metadataCommands(term,state);if(!commands.length)return;
        state.busy=true;state.error='正在写入草案…';sync();
        await options.model.sendCommand({action:'batch',commands});
        state.error='已写入草案，审核发布后生效。';
        if(snapshot(options)===opened)feedback.textContent=state.error;
        options.model.status(state.error);
      }catch(error){state.error=error.message;feedback.textContent=state.error;}
      finally{state.busy=false;state.update();}
    },'om-btn primary');
    name.addEventListener('input',()=>{state.name=name.value;sync();});
    description.addEventListener('input',()=>{state.description=description.value;sync();});
    state.update=sync;
    body.append(field('名称',name),field('说明',description),save,feedback);box.append(body);sync();return box;
  }
  function associationPicker(cls,summary,card,options) {
    const direct=card.attributes.map(a=>a.id),inherited=new Set(card.inheritedAttributes.map(a=>a.id));
    const state=editState(options,'association:'+cls.id,direct,{selected:new Set(direct)});
    const selected=state.selected;let applied=[...direct];
    const opened=snapshot(options),picker=el('details','om-type-picker om-attribute-picker admin-only');
    picker.append(el('summary',null,'关联已有属性'));
    const search=el('input','om-type-search');search.type='search';search.placeholder='搜索属性名称';search.setAttribute('aria-label','搜索已有属性');
    const list=el('div','om-type-options'),rows=[];
    const candidates=(summary.attributes||[]).filter(a=>!inherited.has(a.id)&&(a.active!==false||direct.includes(a.id)));
    const sync=()=>{feedback.textContent=state.error;apply.disabled=state.busy||!associationCommands(cls.id,applied,[...selected]).length;
      for(const {input} of rows)input.disabled=state.busy;};
    for(const attr of candidates){
      const row=el('label','om-type-option');row.dataset.attributeId=attr.id;
      const input=el('input');input.type='checkbox';input.value=attr.id;input.checked=selected.has(attr.id);
      input.addEventListener('change',()=>{input.checked?selected.add(attr.id):selected.delete(attr.id);sync();});
      const copy=el('span');copy.append(el('span',null,label(attr)+(attr.active===false?'（已停用）':'')),
        el('small','om-attribute-datatype',datatypeLabel(attr)));
      row.append(input,copy);list.append(row);rows.push({row,input,attr});
    }
    const empty=el('p','om-detail-inline-empty','没有可关联的属性，可先添加新属性。');empty.hidden=!!rows.length;
    search.addEventListener('input',()=>{const query=search.value.trim().toLocaleLowerCase();let visible=0;
      for(const {row,attr} of rows){row.hidden=![label(attr),attr.id,attr.description||''].join(' ').toLocaleLowerCase().includes(query);if(!row.hidden)visible++;}
      empty.hidden=!!visible;empty.textContent=query?'没有匹配的属性。':'没有可关联的属性，可先添加新属性。';});
    const feedback=el('p','om-attribute-message',state.error);feedback.setAttribute('role','status');
    const apply=button('应用关联',async()=>{
      if(state.busy)return;
      try{
        assertWrite(options,opened,picker);const commands=associationCommands(cls.id,applied,[...selected]);if(!commands.length)return;
        state.busy=true;state.error='正在写入草案…';sync();
        await options.model.sendCommand({action:'batch',commands});applied=[...selected];
        state.error='关联已写入草案，审核发布后生效。';options.model.status(state.error);
      }catch(error){state.error=error.message;feedback.textContent=state.error;}
      finally{state.busy=false;state.update();}
    },'om-btn primary');
    const actions=el('div','om-type-actions');actions.append(apply);
    state.update=sync;
    picker.append(search,list,empty,actions,feedback);sync();return picker;
  }
  function classSection(cls,summary,options) {
    const model=options.model,card=window.OntologyCatalog?.typeCard(cls,summary)||{
      attributes:(summary.attributes||[]).filter(a=>(a.domain||[]).includes(cls.id)&&a.active!==false),inheritedAttributes:[]};
    const box=el('section','om-detail-sec om-attributes');
    box.append(options.title(`属性（${card.attributes.length}）`,'定义此类型可填写的字段。关联选择只改变当前类型的归属；编辑共享属性会影响所有适用类型。'));
    const list=el('ul','om-attribute-list');
    for(const attr of card.attributes){const row=el('li','om-attribute-item');
      row.append(button(label(attr),()=>model.select(attr.id),'om-detail-link'),el('small','om-attribute-datatype',datatypeLabel(attr)));
      list.append(row);}
    if(card.attributes.length)box.append(list);else box.append(el('p','om-detail-inline-empty','还没有直属属性'));
    if(!model.S.preview&&cls.active!==false){
      const actions=el('div','om-attribute-actions admin-only');
      actions.append(button('添加属性',()=>model.openCreate({kind:'attribute',domain:cls.id}),'om-btn primary'));
      box.append(actions,associationPicker(cls,summary,card,options));
    }
    if(card.inheritedAttributes.length){
      const inherited=el('div','om-inherited-attributes');
      inherited.append(options.title(`继承属性（${card.inheritedAttributes.length}）`,'来自上级类型，在父类解除归属后才会取消继承。点击名称编辑共享属性定义。'));
      const list=el('ul','om-attribute-list');
      for(const attr of card.inheritedAttributes){const row=el('li','om-attribute-item');
        row.append(button(label(attr),()=>model.select(attr.id),'om-detail-link'),el('small','om-attribute-datatype',datatypeLabel(attr)));
        const source=el('span','om-attribute-source');source.append(el('span',null,'来自 '));
        for(const id of attr.inheritedFrom||[]){const parent=(summary.classes||[]).find(c=>c.id===id)||{id};
          source.append(button(label(parent),()=>model.select(id),'om-attribute-parent'));}
        row.append(source);list.append(row);}
      inherited.append(list);box.append(inherited);
    }
    return box;
  }
  window.OntologyAttributes={metadataCommands,associationCommands,classSection,metadataEditor,datatypeSelect};
})();
