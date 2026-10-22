/* Formal record creation and bounded record selection. PostgreSQL remains authoritative. */
(function(root,factory){
  const api=factory(root,typeof module==='object'&&module.exports?require('./record-attribute-editor.js'):root.RecordAttributeEditor);
  if(typeof module==='object'&&module.exports)module.exports=api;else root.RecordCreate=api;
})(typeof window==='object'?window:globalThis,(root,attributeEditor)=>{
  const xsd='http://www.w3.org/2001/XMLSchema#',literal='http://www.w3.org/2000/01/rdf-schema#Literal';
  const modes=['string','boolean','integer','double'];
  const keys={entity:'classes',relation:'relations',attribute:'attributes'};
  const clone=value=>JSON.parse(JSON.stringify(value));
  function catalog(publication){
    if(!publication?.id)throw Error('尚未读取到已发布本体，请先到本体建模层发布');
    const result={};
    for(const [kind,key] of Object.entries(keys)){
      if(!Array.isArray(publication.summary?.[key]))throw Error('已发布本体缺少完整类型目录，请刷新或到本体建模层检查');
      result[kind]=publication.summary[key].filter(item=>item.active!==false).map(item=>({...item,
        domain:[...(item.domain||[])],range:[...(item.range||[])]}));
    }
    return result;
  }
  function attributeModes(term){
    const ranges=term?.range||[];
    return !ranges.length||ranges.includes(literal)?[...modes]:modes.filter(mode=>ranges.includes(xsd+mode));
  }
  function objectValue(value,label){
    let result=value||{};
    if(typeof result==='string'){try{result=result.trim()?JSON.parse(result):{};}catch{throw Error(label+'必须是有效 JSON 对象');}}
    if(!result||typeof result!=='object'||Array.isArray(result))throw Error(label+'必须是 JSON 对象');
    return clone(result);
  }
  function businessTime(value){
    value=String(value||'').trim();if(!value)return null;
    // Reuse the attribute editor's strict calendar/timezone validation.
    attributeEditor.parseValue(value.includes('T')?'dateTime':'date',value);
    return value;
  }
  function buildRecord(kind,fields,publication){
    const terms=catalog(publication)[kind];if(!terms)throw Error('不支持的知识类别');
    const term=terms.find(item=>item.id===fields.type);
    if(!term)throw Error('请选择当前已发布本体中的类型；缺少类型请先到本体建模层建模');
    const record={kind,text:String(fields.text||'').trim(),type:term.id,ontology_id:publication.id,
      metadata:objectValue(fields.metadata,'元数据'),properties:{},
      valid_from:businessTime(fields.valid_from),valid_until:businessTime(fields.valid_until)};
    if(record.valid_from&&record.valid_until&&Date.parse(record.valid_until)<=Date.parse(record.valid_from))throw Error('业务失效时间必须晚于生效时间');
    if(kind!=='entity'){
      if(!String(fields.subject_id||'').trim())throw Error('请选择所属或起点实体');
      record.subject_id=fields.subject_id.trim();
    }
    if(kind==='relation'){
      if(!String(fields.object_id||'').trim())throw Error('请选择终点实体');record.object_id=fields.object_id.trim();
    }
    if(kind==='attribute'){
      if(!attributeModes(term).includes(fields.mode))throw Error('所选属性数据类型无法由当前写入服务表达，请到本体建模层检查 range');
      const parsed=attributeEditor.parseValue(fields.mode,fields.value??'');
      if(term.range?.length&&!term.range.includes(literal)&&!term.range.includes(parsed.datatype))throw Error('属性值的原生数据类型与本体 range 不一致');
      Object.assign(record,parsed);
      if(!record.text)record.text=(term.label_zh||term.label||term.name||term.id)+' = '+String(parsed.value);
    }
    if(!record.text)throw Error(kind==='entity'?'请填写实体名称':'请填写关系显示文本');
    return record;
  }
  function createSession({project,request,accepts=()=>true,canWrite=()=>false,scope={}}){
    const base='/api/projects/'+encodeURIComponent(project),fixedScope=clone(scope);let publication=null;
    function assertCurrent(){if(!project||!accepts())throw Error('项目或查询范围已变化，请重新打开表单');}
    async function read(suffix,body){assertCurrent();const result=await request(base+suffix,body,body===undefined?'GET':'POST');assertCurrent();return result;}
    return {
      assertCurrent,
      async loadPublication(){const result=await read('/ontology');catalog(result);publication=result;return result;},
      entities(query='',cursor=null){return read('/records/page',{...fixedScope,ontology_scope:'all',ontology_ids:null,
        kinds:['entity'],query:String(query).trim(),cursor,limit:50});},
      async entity(id){
        id=String(id||'').trim();if(!id)throw Error('请输入实体 ID');
        // The scope-aware page query is authoritative; a direct ID never bypasses scope.
        const page=await read('/records/page',{...fixedScope,ontology_scope:'all',ontology_ids:null,
          kinds:['entity'],record_ids:[id],limit:1});
        const record=(page.records||[]).find(item=>item.id===id);
        if(record)return record;
        throw Error('当前范围内找不到这个实体 ID，请检查项目、业务时间和筛选条件');
      },
      async submit(record){
        assertCurrent();if(!canWrite())throw Error('只有管理员可以创建知识记录');
        if(!publication||record.ontology_id!==publication.id)throw Error('请选择已读取的本体发布');
        const latest=await read('/ontology');
        if(latest.id!==publication.id)throw Error('本体已更新，请重新打开表单使用最新发布');
        if(!canWrite())throw Error('只有管理员可以创建知识记录');
        return read('/records',{records:[record]});
      },
    };
  }
  function createPager(request){
    let serial=0,criteria={},cursors=[null],state={records:[],total:0,next_cursor:null,page:0};
    async function fetchPage(index){
      const ticket=++serial,body={...clone(criteria),cursor:cursors[index],limit:50};
      const result=await request(body);if(ticket!==serial)return null;
      state={records:result.records||[],total:result.total,next_cursor:result.next_cursor||null,page:index};
      return {...state};
    }
    return {
      load(next){criteria=clone(next);cursors=[null];state={records:[],total:0,next_cursor:null,page:0};return fetchPage(0);},
      next(){if(!state.next_cursor)return Promise.resolve(null);cursors[state.page+1]=state.next_cursor;return fetchPage(state.page+1);},
      previous(){return state.page?fetchPage(state.page-1):Promise.resolve(null);},
      invalidate(){serial++;cursors=[null];state={records:[],total:0,next_cursor:null,page:0};},
      snapshot(){return {...state,records:[...state.records]};},
    };
  }
  const label=term=>term.label_zh||term.label||term.name||term.id;
  function element(tag,className,text){const node=root.document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
  function field(host,text,tag='input'){const wrap=element('label','record-create-field',text),input=element(tag);wrap.append(input);host.append(wrap);return input;}
  function button(text,fn,className='secondary'){const node=element('button',className,text);node.type='button';node.onclick=fn;return node;}
  /* Shared with the record editor: no preloaded prefix, and exact IDs are checked in the same scope. */
  function entityPicker(host,{session,title,selected=null,onChange=()=>{}}){
    const box=element('section','record-entity-picker'),heading=element('b',null,title);
    const chosen=element('p','record-entity-selected'),tools=element('div','record-entity-tools');
    const query=element('input');query.placeholder='按名称、别名或 ID 查询全库';query.setAttribute('aria-label',title+'搜索');
    const list=element('div','record-entity-results'),note=element('small'),pages=element('div','record-entity-pages');
    let value=selected,serial=0,cursors=[null],index=0,nextCursor=null;
    function paint(){chosen.textContent=value?`${value.text||value.id} · ${value.id}`:'尚未选择';onChange(value);}
    async function search(nextIndex=0){
      const ticket=++serial;note.textContent='正在查询当前范围的全部实体…';
      try{const result=await session.entities(query.value,cursors[nextIndex]);if(ticket!==serial)return;
        index=nextIndex;nextCursor=result.next_cursor;list.replaceChildren();
        for(const row of result.records||[])list.append(button(row.text+' · '+row.id,()=>{value=row;paint();}));
        note.textContent=`匹配 ${result.total??'未知'} 条 · 第 ${index+1} 页 · 每页最多 50 条`;
        if(!result.records?.length)list.append(element('p','subtle','没有匹配实体；可调整搜索或直接输入准确 ID 定位。'));
        prev.disabled=index===0;next.disabled=!nextCursor;
      }catch(error){if(ticket===serial){list.replaceChildren();note.textContent=error.message;prev.disabled=true;next.disabled=true;}}
    }
    const find=button('查询',()=>{cursors=[null];void search(0);});
    query.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();find.click();}};
    const prev=button('上一页',()=>void search(index-1)),next=button('下一页',()=>{cursors[index+1]=nextCursor;void search(index+1);});
    prev.disabled=true;next.disabled=true;pages.append(prev,next);
    query.oninput=()=>{serial++;cursors=[null];index=0;nextCursor=null;prev.disabled=true;next.disabled=true;note.textContent='条件已修改，点击“查询”搜索当前完整范围。';};
    const direct=element('div','record-entity-direct'),id=element('input');id.placeholder='准确实体 ID（按当前范围校验）';id.setAttribute('aria-label',title+'准确 ID');
    direct.append(id,button('按 ID 定位',async()=>{const ticket=++serial;try{const row=await session.entity(id.value);if(ticket===serial){value=row;paint();note.textContent='已定位当前范围内的实体';}}catch(error){if(ticket===serial)note.textContent=error.message;}}));
    tools.append(query,find);box.append(heading,chosen,tools,list,note,pages,direct);host.append(box);paint();
    return {get value(){return value;},invalidate(){serial++;},search:()=>{cursors=[null];return search(0);}};
  }
  let opened=null,openSerial=0;
  async function open(kind='entity',{subject=null}={}){
    if(!root.document)return;
    const writer=()=>!!root.Auth?.isAdmin?.();
    if(!writer()){if(typeof status==='function')status('只有管理员可以创建知识记录',true);return;}
    const project=typeof current==='string'?current:'';
    if(!project){if(typeof status==='function')status('请先选择项目',true);return;}
    root.WorkspaceDrawers?.close({restoreFocus:false});
    const trigger=root.document.activeElement;
    opened?.close();const serial=++openSerial,context=root.WorkspaceContext,page=context?.page||'records';
    const currentScope=()=>context?context.read(project,page):(typeof scope==='function'?scope():{});
    const ticket=context?.ticket(project,page),stamp=JSON.stringify(currentScope());
    const accepts=()=>serial===openSerial&&current===project&&JSON.stringify(currentScope())===stamp&&(!ticket||context.accepts(ticket,project,page));
    const session=createSession({project,scope:currentScope(),accepts,canWrite:writer,request:(...args)=>api(...args)});
    const dialog=element('dialog','record-create-dialog');dialog.id='record-create-dialog';dialog.setAttribute('aria-labelledby','record-create-title');
    const heading=element('div','record-create-heading'),title=element('h2',null,'新增'+({entity:'实体',relation:'关系',attribute:'属性'}[kind]||'知识'));title.id='record-create-title';
    const pickers=[];let submitted=false;
    function close(options={}){openSerial++;pickers.forEach(item=>item.invalidate());dialog.close();dialog.remove();if(opened?.dialog===dialog)opened=null;if(options.restoreFocus!==false&&trigger?.isConnected)trigger.focus?.();}
    heading.append(title,button('关闭 ×',close));
    const help=element('p','subtle','正在读取当前已发布本体…'),message=element('p','record-create-message');message.setAttribute('role','status');
    const form=element('form','record-create-form');dialog.append(heading,help,message,form);root.document.body.append(dialog);
    opened={dialog,close};dialog.addEventListener('cancel',event=>{event.preventDefault();close();});dialog.showModal();
    function showError(error){if(accepts()){message.textContent=error.message;message.classList.add('error');}}
    try{
      const publication=await session.loadPublication();if(!accepts())return;
      const terms=catalog(publication)[kind]||[];
      const modeling=()=>{close();if(typeof showTab==='function')showTab('ontology-model');};
      if(!terms.length){help.textContent='当前发布还没有可用的'+({entity:'类',relation:'关系',attribute:'属性'}[kind])+'定义，请先建模并发布。';form.append(button('前往本体建模层',modeling));return;}
      help.textContent=`依据当前已发布本体 ${publication.id} 创建正式记录；类型与端点约束由服务端校验。`;
      const type=field(form,'本体类型','select');type.id='create-record-type';
      for(const term of terms){const option=element('option',null,label(term));option.value=term.id;type.append(option);}
      const constraint=element('p','record-create-constraints');form.append(constraint);
      const text=field(form,kind==='entity'?'实体名称':kind==='relation'?'关系显示文本':'属性显示文本（可留空自动生成）');text.id='create-record-text';text.maxLength=1000000;
      if(kind!=='attribute')text.required=true;
      if(kind!=='entity'){
        const endpoints=element('div','record-create-endpoints');form.append(endpoints);
        pickers.push(entityPicker(endpoints,{session,title:kind==='relation'?'起点实体':'所属实体',selected:subject}));
        if(kind==='relation')pickers.push(entityPicker(endpoints,{session,title:'终点实体'}));
      }
      let mode,value;
      if(kind==='attribute'){
        mode=field(form,'数据类型','select');mode.id='create-record-datatype';value=field(form,'属性值');value.id='create-record-value';
      }
      const time=element('div','record-create-times');form.append(time);
      const from=field(time,'业务生效时间（可留空）'),until=field(time,'业务失效时间（可留空）');
      from.placeholder=until.placeholder='YYYY-MM-DD 或带时区 ISO 时间';from.id='create-valid-from';until.id='create-valid-until';
      const more=element('details','record-create-advanced');more.append(element('summary',null,'更多：元数据与高级 JSON'));
      const metadata=field(more,'元数据 JSON','textarea');metadata.value='{}';metadata.rows=3;metadata.id='create-record-metadata';
      const json=field(more,'完整记录 JSON（可选；填写后使用其完整内容）','textarea');json.rows=8;json.id='create-record-json';
      more.append(element('small','subtle','保留当前发布的 ontology_id；记录身份、端点、有效期和元数据以服务端校验为准。'));
      form.append(more);
      const actions=element('div','record-create-actions'),save=element('button','admin-only','创建正式记录');save.type='submit';save.id='save-created-record';
      actions.append(save,button('前往本体建模层',modeling));form.append(actions);
      function setInput(){if(!mode)return;value.type=mode.value==='boolean'?'text':mode.value==='integer'||mode.value==='double'?'number':'text';value.step=mode.value==='integer'?'1':'any';value.placeholder=mode.value==='boolean'?'true 或 false':'';}
      function setType(){const term=terms.find(item=>item.id===type.value),names=ids=>ids.map(id=>label(publication.summary.classes.find(item=>item.id===id)||{id})).join(' / ');
        constraint.textContent=kind==='entity'?'类型只取自当前已发布的可用类。':`允许主体：${term.domain.length?names(term.domain):'未限定'}${kind==='relation'?`；允许客体：${term.range.length?names(term.range):'未限定'}`:`；值域：${term.range.join(' / ')||'未限定（原生标量）'}`}`;
        if(mode){const available=attributeModes(term);mode.replaceChildren();for(const name of available){const option=element('option',null,{string:'字符串',boolean:'布尔值',integer:'整数',double:'浮点数'}[name]);option.value=name;mode.append(option);}save.disabled=!available.length;
          if(!available.length)constraint.textContent+='。当前写入服务只支持字符串、布尔值、整数和浮点数，无法表达该值域；请到本体建模层检查。';setInput();}
      }
      type.onchange=setType;if(mode)mode.onchange=setInput;setType();
      form.onsubmit=async event=>{
        event.preventDefault();if(save.disabled||submitted)return;save.disabled=true;message.textContent='正在校验并写入…';message.classList.remove('error');
        try{
          session.assertCurrent();let record;
          if(json.value.trim()){
            try{record=JSON.parse(json.value);}catch{throw Error('完整记录 JSON 格式不正确');}
            if(!record||Array.isArray(record)||record.kind!==kind||record.ontology_id!==publication.id)throw Error('高级 JSON 必须是同一知识类别并携带当前发布 ontology_id');
          }else record=buildRecord(kind,{text:text.value,type:type.value,metadata:metadata.value,
            subject_id:pickers[0]?.value?.id,object_id:pickers[1]?.value?.id,mode:mode?.value,value:value?.value,
            valid_from:from.value,valid_until:until.value},publication);
          const result=await session.submit(record);if(!accepts())return;
          submitted=true;message.textContent='已创建正式记录，旧记录与修订历史保留。';
          root.document.dispatchEvent(new CustomEvent('records:created',{detail:{projectId:project,records:result.records||[]}}));
          actions.append(button('在台账查看',()=>{const row=result.records?.[0];close();if(row)root.focusLedgerRecord?.(row);}));
          if(typeof status==='function')status('已创建'+({entity:'实体',relation:'关系',attribute:'属性'}[kind]));
        }catch(error){showError(error);}
        finally{if(accepts()&&!submitted)save.disabled=kind==='attribute'&&!attributeModes(terms.find(item=>item.id===type.value)).length;}
      };
    }catch(error){showError(error);help.textContent='未能读取可靠的当前发布；创建前需要先发布本体。';form.replaceChildren(button('前往本体建模层',()=>{close();if(typeof showTab==='function')showTab('ontology-model');}));}
  }
  if(root.document){
    root.document.getElementById('project')?.addEventListener('change',()=>opened?.close());
    root.document.addEventListener('workspace:scope-changed',()=>opened?.close());
  }
  return {catalog,attributeModes,buildRecord,createSession,createPager,entityPicker,open,close:options=>opened?.close(options)};
});
