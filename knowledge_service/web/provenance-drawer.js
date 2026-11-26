/* Frozen answer evidence. This module loads before workbench history hydration. */
(() => {
  'use strict';
  const validCitation=value=>typeof value==='string'&&/^[ERFU][1-9][0-9]*$/.test(value);
  const matchModes=['keyword','semantic','hybrid'];
  const matchFields=['title','filename','body','identifier','aliases','entity_name','relation_text',
    'attribute_value','owner_name','property_name','record_text'];

  function sanitizeRetrievalMatch(match){
    if(!match||typeof match!=='object'||Array.isArray(match)||!matchModes.includes(match.mode))return null;
    const result={mode:match.mode};
    if([null,'fusion','keyword_rerank'].includes(match.strategy))result.strategy=match.strategy;
    for(const [key,allowed] of [['channels',['keyword','semantic']],['fields',matchFields],['indexed_fields',matchFields]]){
      if(Array.isArray(match[key]))result[key]=[...new Set(match[key].filter(value=>allowed.includes(value)))];
    }
    for(const key of ['score','rrf_score'])if(Number.isFinite(match[key]))result[key]=match[key];
    if(typeof match.supplemental==='boolean')result.supplemental=match.supplemental;
    return result;
  }

  function tokenizeCitations(text,allowed){
    if(typeof text!=='string'||!text)return [];
    const offered=allowed==null?null:new Set(allowed),segments=[];
    let start=0;
    for(const match of text.matchAll(/\[([ERFU][1-9][0-9]*)\]/g)){
      if(offered&&!offered.has(match[1]))continue;
      if(match.index>start)segments.push({type:'text',text:text.slice(start,match.index)});
      segments.push({type:'citation',text:match[0],citation:match[1]});
      start=match.index+match[0].length;
    }
    if(start<text.length)segments.push({type:'text',text:text.slice(start)});
    return segments;
  }

  function sanitizeEvidenceSummary(item){
    const result={};
    if(!item||typeof item!=='object'||Array.isArray(item))return result;
    const own=key=>Object.prototype.hasOwnProperty.call(item,key);
    if(own('citation')&&validCitation(item.citation))result.citation=item.citation;
    for(const key of ['text_preview','kind','provenance_ref','source_status','source_reason','retrieval_channel','ranker_version']){
      if(own(key)&&typeof item[key]==='string')result[key]=key==='text_preview'?item[key].slice(0,240):item[key];
    }
    const match=own('retrieval_match')&&sanitizeRetrievalMatch(item.retrieval_match);
    if(match)result.retrieval_match=match;
    if(own('version')&&Number.isSafeInteger(item.version)&&item.version>0)result.version=item.version;
    for(const key of ['version_id','valid_from','valid_until','validity_status','validity_basis','usage','query_platform','source_scope']){
      if(own(key)&&typeof item[key]==='string')result[key]=item[key].slice(0,500);
    }
    if(typeof item.answer_eligible==='boolean')result.answer_eligible=item.answer_eligible;
    if(typeof item.document_quote_allowed==='boolean')result.document_quote_allowed=item.document_quote_allowed;
    if(Array.isArray(item.exclusion_reasons))result.exclusion_reasons=item.exclusion_reasons.filter(value=>typeof value==='string').slice(0,20).map(value=>value.slice(0,500));
    if(Array.isArray(item.source_eligibility))result.source_eligibility=item.source_eligibility.slice(0,20).flatMap(source=>{
      if(!source||typeof source!=='object')return [];
      const clean={};
      for(const key of ['document_version_id','governance_version_id','validity_status','valid_from','valid_until','effective_time_source']){
        if(typeof source[key]==='string')clean[key]=source[key].slice(0,500);
        else if(source[key]===null)clean[key]=null;
      }
      return Object.keys(clean).length?[clean]:[];
    });
    if(item.metadata&&typeof item.metadata==='object'){
      const metadata={};
      for(const key of ['published_at','effective_time_source'])if(typeof item.metadata[key]==='string')metadata[key]=item.metadata[key].slice(0,500);
      if(item.metadata.applicability&&typeof item.metadata.applicability==='object'){
        const applicability={};
        for(const key of ['platforms','businesses','audiences']){
          const dimension=item.metadata.applicability[key];
          if(!dimension||typeof dimension!=='object'||!['all','specific','unknown'].includes(dimension.mode))continue;
          applicability[key]={mode:dimension.mode,values:Array.isArray(dimension.values)?dimension.values.filter(value=>typeof value==='string').slice(0,30).map(value=>value.slice(0,200)):[]};
        }
        if(Object.keys(applicability).length)metadata.applicability=applicability;
      }
      if(Object.keys(metadata).length)result.metadata=metadata;
    }
    if(own('sources')&&Array.isArray(item.sources))result.sources=item.sources.slice(0,20).flatMap(source=>{
      if(!source||typeof source!=='object'||Array.isArray(source))return [];
      const clean={};
      for(const key of ['document_id','document_version_id','record_version_id','title','quote','channel','valid_from','valid_until']){
        if(typeof source[key]==='string')clean[key]=key==='quote'?source[key].slice(0,240):source[key];
      }
      if(Number.isSafeInteger(source.version)&&source.version>0)clean.version=source.version;
      return Object.keys(clean).length?[clean]:[];
    });
    return result;
  }

  const pure={tokenizeCitations,sanitizeEvidenceSummary};
  if(typeof module!=='undefined'&&module.exports)module.exports=pure;
  if(typeof window==='undefined'||typeof document==='undefined')return;

  const nodeLabels={answer:'答案',retrieval:'检索',record_version:'记录版本',assertion:'断言',
    review_event:'审核决定',chunk_version:'原文片段',source_occurrence:'原文定位',
    document_version:'历史文档',ingest_run:'摄取运行'};
  const detailFields={
    answer:['answer_id','citation','mode'],retrieval:['run_id'],
    record_version:['record_id','version','kind','text_preview','valid_from','valid_until','recorded_at','terminal_reason'],
    assertion:['assertion_id','kind','status_at_capture','quote','start_char','end_char'],
    review_event:['event_id','from_status','to_status','actor','reason','created_at'],
    chunk_version:['record_id','version_id','text_preview','start_char','end_char'],
    source_occurrence:['document_id','version_id','version','title','source_content','valid_from','valid_until','effective_time_source','start_char','end_char','excerpt_before','highlight','excerpt_after'],
    document_version:['document_id','version_id','version','title','source_content','valid_from','valid_until','effective_time_source'],
    ingest_run:['run_id','attempt','status_at_capture','created_at','updated_at_at_capture']
  };
  const fieldLabels={answer_id:'答案标识',citation:'引用',mode:'生成模式',run_id:'运行标识',record_id:'记录标识',
    version:'版本',kind:'类型',text_preview:'内容',valid_from:'业务生效',valid_until:'业务失效',recorded_at:'记录时间',
    terminal_reason:'链路终点',assertion_id:'断言标识',status_at_capture:'生成答案时的状态',quote:'证据原文',
    start_char:'起始位置',end_char:'结束位置',event_id:'事件标识',from_status:'此前状态',to_status:'决定状态',
    actor:'操作人',reason:'原因',created_at:'创建时间',version_id:'版本标识',document_id:'文档标识',title:'标题',
    source_content:'原文完整性',effective_time_source:'有效时间来源',excerpt_before:'前文',highlight:'定位原文',excerpt_after:'后文',
    attempt:'尝试次数',updated_at_at_capture:'当时更新时间'};
  const timestampFields=new Set(['valid_from','valid_until','recorded_at','created_at','updated_at_at_capture']);
  const scalar=value=>typeof value==='string'||typeof value==='number'||typeof value==='boolean'?String(value):'';
  const list=value=>Array.isArray(value)?value:[];
  const element=(tag,text,className)=>{
    const result=document.createElement(tag);
    if(text!==undefined)result.textContent=text;
    if(className)result.className=className;
    return result;
  };
  const button=(text,handler)=>{
    const result=element('button',text,'secondary');result.type='button';
    result.addEventListener('click',handler);return result;
  };
  const root=element('section',undefined,'provenance-drawer');root.id='provenance-drawer';
  root.hidden=true;root.setAttribute('role','dialog');root.setAttribute('aria-modal','false');
  root.setAttribute('aria-labelledby','provenance-drawer-title');root.tabIndex=-1;
  const header=element('header'),title=element('h2','证据溯源');title.id='provenance-drawer-title';
  const closeButton=button('关闭 ×',()=>close());closeButton.setAttribute('aria-label','关闭证据溯源');
  header.append(title,closeButton);
  const status=element('p',undefined,'provenance-status');status.id='provenance-status';
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const guideHost=element('p',undefined,'provenance-guide');guideHost.id='provenance-guide';
  const overview=element('div',undefined,'provenance-overview');overview.id='provenance-overview';
  const audit=element('details',undefined,'provenance-audit');audit.id='provenance-audit';
  audit.open=false;audit.hidden=true;audit.append(element('summary','溯源详情'));
  const auditBody=element('div',undefined,'provenance-audit-body');
  const tabs=element('div',undefined,'provenance-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','溯源视图');
  const chain=element('div',undefined,'provenance-chain');chain.id='provenance-chain';
  const rawPanel=element('div');rawPanel.id='provenance-api';
  const raw=element('pre');raw.id='provenance-raw';raw.tabIndex=0;rawPanel.appendChild(raw);
  const panels=[chain,rawPanel],tabButtons=[];
  function selectTab(index){
    tabButtons.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});
  }
  ['证据链','原始接口数据'].forEach((label,index)=>{
    const tab=button(label,()=>selectTab(index));tab.id='provenance-tab-'+index;
    tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panels[index].id);
    panels[index].setAttribute('role','tabpanel');panels[index].setAttribute('aria-labelledby',tab.id);
    panels[index].tabIndex=0;
    tab.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?1:1-index;
      selectTab(next);tabButtons[next].focus();
    });
    tabButtons.push(tab);tabs.appendChild(tab);
  });
  selectTab(0);auditBody.append(tabs,chain,rawPanel);audit.append(auditBody);
  root.append(header,guideHost,status,overview,audit);document.body.appendChild(root);
  const narrowScreen=typeof window.matchMedia==='function'?window.matchMedia('(max-width: 760px)'):null;
  function syncLayout(){
    let target=document.body;
    if(narrowScreen?narrowScreen.matches:window.innerWidth<=760){
      const scroll=document.getElementById('qa-scroll'),transcript=document.getElementById('qa-transcript');
      if(scroll){
        let mount=document.getElementById('qa-provenance-mount');
        if(!mount){
          mount=element('div');mount.id='qa-provenance-mount';
          if(transcript?.parentNode===scroll)transcript.after(mount);
          else scroll.appendChild(mount);
        }
        target=mount;
      }
    }
    if(root.parentNode!==target){
      // Moving the same node preserves the open request and tab state. Reparenting
      // can blur a focused descendant, so restore it without jumping the scroll.
      const focused=root.contains(document.activeElement)?document.activeElement:null;
      target.appendChild(root);
      if(focused&&!root.hidden)focused.focus({preventScroll:true});
    }
  }
  narrowScreen?.addEventListener('change',syncLayout);
  window.addEventListener?.('resize',syncLayout);
  syncLayout();
  let serial=0,controller=null,active=null,restoreFocus=null;
  function setState(state,message){root.dataset.state=state;status.textContent=message;root.setAttribute('aria-busy',String(state==='loading'));}
  function cancelRequest(){serial++;controller?.abort();controller=null;}
  function close(restore=true){
    cancelRequest();active=null;root.hidden=true;
    if(restore&&restoreFocus?.isConnected)restoreFocus.focus();restoreFocus=null;
  }
  root.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
  function onProjectChange(){close(false);clearContent();}
  function currentProject(){
    return typeof current==='undefined'?null:current;
  }
  function isActive(ticket,context){
    const project=currentProject();
    return ticket===serial&&!root.hidden&&active?.projectId===context.projectId&&
      active?.answerId===context.answerId&&active?.citation===context.citation&&
      (project===null||project===context.projectId);
  }
  function clearContent(){
    overview.replaceChildren();chain.replaceChildren();raw.textContent='';
    audit.open=false;audit.hidden=true;selectTab(0);
  }
  function frozenSourceButton(details){
    const excerpt=Object.fromEntries(detailFields.source_occurrence.map(key=>[key,details[key]]).filter(([,value])=>scalar(value)!==''));
    const open = button('查看历史原文',()=>{
      if(typeof window.openFrozenSourceEvidence==='function')window.openFrozenSourceEvidence(excerpt);
      else status.textContent='原文查看器尚未就绪，请稍后重试。';
    });
    open.className += ' button-inline-action';
    return open;
  }
  function sourceCard(details){
    const card=element('article',undefined,'provenance-source-card');
    card.append(element('h3',scalar(details.title)||'来源文档'));
    const version=scalar(details.version);
    const from=window.DisplayTime.format(details.valid_from,'');
    const until=window.DisplayTime.format(details.valid_until,'');
    const metadata=[version&&'版本 '+version,from&&'生效 '+from,until&&'失效 '+until].filter(Boolean);
    if(metadata.length)card.append(element('p',metadata.join(' · '),'provenance-source-meta'));
    const clip=scalar(details.highlight);
    if(clip)card.append(element('blockquote',clip));
    else card.append(element('p','这条引用没有保存可预览的原文片段。','provenance-source-meta'));
    if(clip||details.source_content==='full_version')card.append(frozenSourceButton(details));
    return card;
  }
  function renderOverview(nodes){
    const occurrences=nodes.filter(node=>node.type==='source_occurrence'&&node.details).map(node=>node.details);
    const first=occurrences.findIndex(detail=>scalar(detail.highlight));
    if(first>0)occurrences.unshift(...occurrences.splice(first,1));
    if(occurrences.length){
      overview.append(sourceCard(occurrences[0]));
      if(occurrences.length>1){
        const more=element('details',undefined,'provenance-more-sources');
        const sources=element('div',undefined,'provenance-more-source-list');
        for(const details of occurrences.slice(1))sources.append(sourceCard(details));
        more.append(element('summary','其他原文片段 · '+(occurrences.length-1)),sources);
        overview.append(more);
      }
      return;
    }
    for(const [type,field] of [['chunk_version','text_preview'],['assertion','quote'],['record_version','text_preview']]){
      const match=nodes.find(node=>node.type===type&&scalar(node.details?.[field]));
      if(!match)continue;
      const card=element('article',undefined,'provenance-source-card');
      card.append(element('h3',type==='record_version'?'引用的知识记录':'已保存的引用片段'),
        element('blockquote',scalar(match.details[field])));
      overview.append(card);return;
    }
    if(nodes.length)overview.append(element('p','这条引用没有保存可预览的片段。','provenance-source-meta'));
  }
  function render(data){
    clearContent();raw.textContent=JSON.stringify(data,null,2);
    const nodes=list(data.nodes).filter(node=>node&&Object.hasOwn(detailFields,node.type));
    const edges=list(data.edges),warnings=list(data.integrity?.warnings);
    const names=new Map(nodes.map(node=>[node.ref,scalar(node.label)||nodeLabels[node.type]]));
    renderOverview(nodes);audit.hidden=false;
    if(warnings.length){
      const warningList=element('ul',undefined,'provenance-warnings');
      for(const warning of warnings){
        if(!warning||typeof warning!=='object')continue;
        const item=element('li',scalar(warning.message));
        item.appendChild(element('small',[scalar(warning.code),scalar(warning.node_ref)].filter(Boolean).join(' · ')));
        warningList.appendChild(item);
      }
      chain.appendChild(warningList);
    }
    for(const node of nodes){
      const card=element('article',undefined,'provenance-node');
      card.appendChild(element('small',nodeLabels[node.type]));
      card.appendChild(element('h3',scalar(node.label)||nodeLabels[node.type]));
      card.appendChild(element('p',[scalar(node.status),window.DisplayTime.format(node.occurred_at,"")].filter(Boolean).join(' · '),'provenance-node-meta'));
      const details=node.details&&typeof node.details==='object'?node.details:{};
      const fields=element('dl');
      for(const key of detailFields[node.type]){
        const value=timestampFields.has(key)?window.DisplayTime.format(details[key],''):scalar(details[key]);if(!value)continue;
        const timeLabels={explicit:'明确维护',valid_from:'规范生效时间',valid_until:'规范失效时间',source_validity:'来源有效期',missing_valid_from:'未维护生效时间',published_at:'发布日期推定',unknown:'待核对'};
        fields.append(element('dt',fieldLabels[key]),element('dd',key==='effective_time_source'?(timeLabels[value]||value):value));
      }
      card.appendChild(fields);
      if(node.type==='source_occurrence'){
        card.appendChild(frozenSourceButton(details));
      }
      if(node.type==='record_version'&&typeof details.record_id==='string'){
        card.appendChild(button('版本历史',()=>{
          if(typeof window.openRecordHistory==='function')window.openRecordHistory(details.record_id);
          else status.textContent='历史查看器尚未就绪，请稍后重试。';
        }));
      }
      const branches=edges.filter(edge=>edge&&edge.source_ref===node.ref);
      if(branches.length){
        const links=element('ul',undefined,'provenance-branches');
        for(const edge of branches)links.appendChild(element('li',scalar(edge.relation)+' → '+(names.get(edge.target_ref)||scalar(edge.target_ref))));
        card.appendChild(links);
      }
      chain.appendChild(card);
    }
    if(!nodes.length)setState('empty','没有可展示的溯源节点。');
    else if(warnings.length||data.integrity?.complete===false)setState('partial','部分历史记录缺失，详见溯源详情。');
    else setState('ready','');
  }
  async function open(options={}){
    const {projectId,answerId,citation}=options;
    if(typeof projectId!=='string'||!projectId||typeof answerId!=='string'||!answerId||!validCitation(citation))return;
    cancelRequest();const ticket=serial,context={projectId,answerId,citation};active=context;
    if(options.trigger)restoreFocus=options.trigger;
    else if(!root.contains(document.activeElement))restoreFocus=document.activeElement;
    syncLayout();
    root.hidden=false;title.textContent='证据溯源 · ['+citation+']';
    guideHost.textContent='['+citation+'] 是这条回答的引用编号。';
    clearContent();root.scrollTop=0;setState('loading','正在读取引用…');closeButton.focus();
    controller=new AbortController();const signal=controller.signal;
    const url='/api/projects/'+encodeURIComponent(projectId)+'/answers/'+encodeURIComponent(answerId)+
      '/evidence/'+encodeURIComponent(citation)+'/provenance';
    try{
      const response=await window.fetch(url,{method:'GET',signal});
      if(!isActive(ticket,context))return;
      if(!response.ok){
        setState(response.status===404?'not-found':'failed',response.status===404?'未找到这条历史回答或证据引用。':'历史证据读取失败，请重新打开重试。');return;
      }
      const data=await response.json();
      if(!isActive(ticket,context))return;
      if(data?.subject?.answer_id!==answerId||data?.subject?.citation!==citation){setState('failed','返回的证据与当前回答不匹配。');return;}
      render(data);
    }catch(error){
      if(!isActive(ticket,context))return;
      setState(error?.name==='AbortError'?'cancelled':'failed',error?.name==='AbortError'?'已取消读取。':'历史证据读取失败，请重新打开重试。');
    }
  }
  function decorateAnswer(container,text,context={}){
    const summaries=list(context.evidence).map(sanitizeEvidenceSummary);
    const offered=new Set(summaries.map(item=>item.citation).filter(Boolean));
    if(window.QaPresentation){
      window.QaPresentation.render(container,text,token=>{
        const citation=token.slice(1,-1);
        if(!offered.has(citation)||!context.answerId||!context.projectId)return null;
        const link=button(token,()=>open({...context,citation,trigger:link}));
        link.className='secondary provenance-citation';link.setAttribute('aria-label','查看引用 '+citation+' 的证据溯源');
        return link;
      });
      return;
    }
    container.replaceChildren();
    for(const segment of tokenizeCitations(text,offered)){
      if(segment.type==='citation'&&context.answerId&&context.projectId){
        const citationButton=button(segment.text,()=>open({...context,citation:segment.citation,trigger:citationButton}));
        citationButton.className='secondary provenance-citation';citationButton.setAttribute('aria-label','查看引用 '+segment.citation+' 的证据溯源');
        container.appendChild(citationButton);
      }else container.appendChild(document.createTextNode(segment.text));
    }
  }
  window.ProvenanceDrawer={...pure,open,close,render,decorateAnswer,onProjectChange};
})();
