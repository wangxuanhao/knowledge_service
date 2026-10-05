/* Frozen answer evidence. This module loads before workbench history hydration. */
(() => {
  'use strict';
  const LEGACY_NOTE='该历史回答生成于溯源记录启用前';
  const validCitation=value=>typeof value==='string'&&/^E[1-9][0-9]*$/.test(value);

  function tokenizeCitations(text,allowed){
    if(typeof text!=='string'||!text)return [];
    const offered=allowed==null?null:new Set(allowed),segments=[];
    let start=0;
    for(const match of text.matchAll(/\[(E[1-9][0-9]*)\]/g)){
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
    for(const key of ['text_preview','kind','provenance_ref']){
      if(own(key)&&typeof item[key]==='string')result[key]=key==='text_preview'?item[key].slice(0,240):item[key];
    }
    if(own('version')&&Number.isSafeInteger(item.version)&&item.version>0)result.version=item.version;
    return result;
  }

  const pure={tokenizeCitations,sanitizeEvidenceSummary,LEGACY_NOTE};
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
    source_occurrence:['document_id','version_id','version','title','source_content','start_char','end_char','excerpt_before','highlight','excerpt_after'],
    document_version:['document_id','version_id','version','title','source_content'],
    ingest_run:['run_id','attempt','status_at_capture','created_at','updated_at_at_capture']
  };
  const fieldLabels={answer_id:'答案标识',citation:'引用',mode:'生成模式',run_id:'运行标识',record_id:'记录标识',
    version:'版本',kind:'类型',text_preview:'内容',valid_from:'业务生效',valid_until:'业务失效',recorded_at:'记录时间',
    terminal_reason:'链路终点',assertion_id:'断言标识',status_at_capture:'生成答案时的状态',quote:'证据原文',
    start_char:'起始位置',end_char:'结束位置',event_id:'事件标识',from_status:'此前状态',to_status:'决定状态',
    actor:'操作人',reason:'原因',created_at:'创建时间',version_id:'版本标识',document_id:'文档标识',title:'标题',
    source_content:'原文完整性',excerpt_before:'前文',highlight:'定位原文',excerpt_after:'后文',
    attempt:'尝试次数',updated_at_at_capture:'当时更新时间'};
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
  const tabs=element('div',undefined,'provenance-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','溯源视图');
  const chain=element('div',undefined,'provenance-chain');chain.id='provenance-chain';
  const rawPanel=element('div');rawPanel.id='provenance-api';
  const raw=element('pre');raw.id='provenance-raw';raw.tabIndex=0;rawPanel.appendChild(raw);
  const panels=[chain,rawPanel],tabButtons=[];
  function selectTab(index){
    tabButtons.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});
  }
  // 标签名故意写成一整句：用户原话"证据溯源看不懂，E1 E2分别是干什么的"。
  // "证据链 / API 数据"两个词既没说清这里是什么，也没说清该看哪一个。
  ['这条引用从哪来','原始接口数据（开发排查用）'].forEach((label,index)=>{
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
  /* 说明块挂在标签页上方的常驻容器里。以前它是 chain 的第一个子节点，而 open()/render()
     会 replaceChildren() 清空 chain —— 于是"读取中/读取失败"时说明一起被清掉，
     用户正好在看"看不懂"的空抽屉。说明必须独立于数据生命周期。 */
  const guideHost=element('div');guideHost.id='provenance-guide';
  selectTab(0);root.append(header,status,guideHost,tabs,chain,rawPanel);document.body.appendChild(root);
  function ensureGuide(){if(!guideHost.children.length)guideHost.appendChild(explainBlock());}
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
  function onProjectChange(){close(false);chain.replaceChildren();raw.textContent='';}
  function currentProject(){
    return typeof current==='undefined'?null:current;
  }
  function isActive(ticket,context){
    const project=currentProject();
    return ticket===serial&&!root.hidden&&active?.projectId===context.projectId&&
      active?.answerId===context.answerId&&active?.citation===context.citation&&
      (project===null||project===context.projectId);
  }
  // 「这些块是干什么的」：抽屉里原本只有一堆卡片，用户反馈看不懂为什么要展示这些。
  // 这里给一份名词表 + 一句话说明，默认折叠，不挡住真正要看的那条链。
  const nodePurpose={
    answer:'你正在看的这条回答本身：它引用了哪几条证据、每条是"被引用"还是"只提供过"。',
    retrieval:'生成这条答案的那一次检索：范围、时间点与检索模式。',
    assertion:'写入知识时保存的那次判断（当时的状态与它引用到的原文）。',
    record_version:'这条知识在那一刻的版本快照：名称、类型、生效区间。',
    chunk_version:'断言引用到的原文切片（分块）。',
    source_occurrence:'切片在那份文档里的精确位置与前后文——「往上找原文」就是从这里找。',
    document_version:'当时那份文档的版本（不是今天的文档）。',
    ingest_run:'这份文档是哪一次导入任务处理的。',
    review_event:'谁在什么时候批准或拒绝了这条知识（人工审核痕迹）。'
  };
  /* 阅读指南：默认展开。以前这段是折叠的，等于没写——用户反馈就是"证据溯源看不懂，
     E1 E2分别是干什么的，下面的内容是什么，为什么要展示这些，给llm提示的内容又是什么"。
     所以先正面回答这四个问题，名词表再折一层（要用的人点开）。 */
  function explainBlock(){
    const box=element('details',undefined,'provenance-explain');
    box.open=true;
    box.appendChild(element('summary','这一页怎么看？E1 / E2 是什么？'));
    const qa=element('dl');
    const rows=[
      ['E1 / E2 是什么','答案正文里的引用编号。正文写成 [E1]、[E2]，每个编号对应下面的一张卡；点正文里的编号就跳到这里。'],
      ['下面的内容是','生成这条答案时冻结下来的一条条历史记录：哪份文档、命中哪一段原文、原文在文档里的位置、当时的置信度。'],
      ['为什么要展示','用来核对答案有没有依据：卡片里的原文能和源文档对上，才叫有据可依；对不上就是没依据，要重新检索或补资料。也是审核与追责的凭据。'],
      ['「给 LLM 的提示」是什么','服务端实际拼给模型的提示词片段。模型只看到证据正文，看不到这一页的历史、审核与版本痕迹——所以这些历史是给你和审核人看的，不是模型的推理过程。'],
      ['怎么读某一张卡','按箭头从上往下：这条回答 → 那次检索 → 当时写入的判断 → 原文切片 → 这份文档的版本。'],
    ];
    rows.forEach(([term,copy])=>qa.append(element('dt',term),element('dd',copy)));
    box.appendChild(qa);
    const glossary=element('details',undefined,'provenance-explain__glossary');
    glossary.appendChild(element('summary','名词表：每类卡片分别是什么'));
    const list=element('dl');
    for(const [type,purpose] of Object.entries(nodePurpose)){
      list.append(element('dt',nodeLabels[type]),element('dd',purpose));
    }
    glossary.appendChild(list);
    glossary.appendChild(element('p','没出现的那类卡片＝这条链上确实没有那条记录（例如手工录入的对象没有「原文定位」）。缺少该有的记录时会显示上方的完整性警告。'));
    box.appendChild(glossary);
    return box;
  }
  function render(data){
    ensureGuide();chain.replaceChildren();raw.textContent=JSON.stringify(data,null,2);
    const nodes=list(data.nodes).filter(node=>node&&Object.hasOwn(detailFields,node.type));
    const edges=list(data.edges),warnings=list(data.integrity?.warnings);
    const names=new Map(nodes.map(node=>[node.ref,scalar(node.label)||nodeLabels[node.type]]));
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
      card.appendChild(element('p',[scalar(node.status),scalar(node.occurred_at)].filter(Boolean).join(' · '),'provenance-node-meta'));
      const details=node.details&&typeof node.details==='object'?node.details:{};
      const fields=element('dl');
      for(const key of detailFields[node.type]){
        const value=scalar(details[key]);if(!value)continue;
        fields.append(element('dt',fieldLabels[key]),element('dd',value));
      }
      card.appendChild(fields);
      if(node.type==='source_occurrence'){
        const excerpt=Object.fromEntries(detailFields.source_occurrence.map(key=>[key,details[key]]).filter(([,value])=>scalar(value)!==''));
        card.appendChild(button('查看历史原文',()=>{
          if(typeof window.openFrozenSourceEvidence==='function')window.openFrozenSourceEvidence(excerpt);
          else status.textContent='原文查看器尚未就绪，请稍后重试。';
        }));
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
    else if(warnings.length||data.integrity?.complete===false)setState('partial','证据链存在历史缺口，请查看下方说明。');
    else setState('ready','已显示生成该回答时保存的证据链。');
  }
  async function open(options={}){
    const {projectId,answerId,citation}=options;
    if(typeof projectId!=='string'||!projectId||typeof answerId!=='string'||!answerId||!validCitation(citation))return;
    cancelRequest();const ticket=serial,context={projectId,answerId,citation};active=context;
    if(options.trigger)restoreFocus=options.trigger;
    else if(!root.contains(document.activeElement))restoreFocus=document.activeElement;
    syncLayout();
    root.hidden=false;title.textContent='证据溯源 · ['+citation+']';
    ensureGuide();chain.replaceChildren();raw.textContent='';selectTab(0);setState('loading','正在读取历史证据链…');closeButton.focus();
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
    container.replaceChildren();
    for(const segment of tokenizeCitations(text,offered)){
      if(segment.type==='citation'&&context.answerId&&context.projectId){
        const citationButton=button(segment.text,()=>open({...context,citation:segment.citation,trigger:citationButton}));
        citationButton.className='secondary provenance-citation';citationButton.setAttribute('aria-label','查看引用 '+segment.citation+' 的证据溯源');
        container.appendChild(citationButton);
      }else container.appendChild(document.createTextNode(segment.text));
    }
  }
  function renderLegacyNote(container){container.appendChild(element('p',LEGACY_NOTE,'provenance-legacy-note'));}
  ensureGuide();
  window.ProvenanceDrawer={...pure,open,close,render,decorateAnswer,renderLegacyNote,onProjectChange};
})();
