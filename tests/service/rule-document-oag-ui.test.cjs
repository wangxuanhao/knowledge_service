const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const web=path.resolve(__dirname,'../../knowledge_service/web');
class Element {
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.dataset={};this.attrs={};this.listeners={};this.hidden=false;this._text='';this.value='';this.checked=false;this.isConnected=true;}
  append(...nodes){for(const node of nodes)this.appendChild(node);}
  appendChild(node){node.parentNode?.removeChild(node);this.children.push(node);node.parentNode=this;return node;}
  removeChild(node){this.children=this.children.filter(child=>child!==node);node.parentNode=null;return node;}
  remove(){this.parentNode?.removeChild(this);}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  set textContent(value){this.children=[];this._text=String(value);}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  set innerHTML(value){this.children=[];this._text=String(value);}
  setAttribute(key,value){this.attrs[key]=String(value);}
  getAttribute(key){return this.attrs[key]??null;}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  dispatch(type,event={}){for(const fn of this.listeners[type]||[])fn({preventDefault(){},...event});if(type==='click')this.onclick?.();}
  focus(){this.ownerDocument.activeElement=this;}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  get childElementCount(){return this.children.length;}
  querySelectorAll(selector){return descendants(this).filter(node=>selector.startsWith('.')?(node.className||'').split(' ').includes(selector.slice(1)):node.tagName===selector.toUpperCase());}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)]);}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
  const doc={createElement(tag){return new Element(tag,this);},createTextNode(text){const node=this.createElement('#text');node.textContent=text;return node;},
    getElementById(id){return descendants(this.body).find(node=>node.id===id)||null;},querySelectorAll(){return [];},querySelector(){return null;},addEventListener(){}};
  doc.body=doc.createElement('body');doc.activeElement=doc.body;
  for(const id of ['qa-transcript','qa-hero','qa-query','qa','qa-progress','qa-summary','qa-live','qa-scroll','qa-composer','qa-clear','generate','qa-include-references']){
    const node=doc.createElement('div');node.id=id;doc.body.append(node);
  }
  const storage=new Map(),requests=[],provenance=[],streams=[],frames=new Map();
  let frameId=0;
  const localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  const fetch=(url,options)=>{
    if(url.endsWith('/provenance')){
      let resolve;const promise=new Promise(done=>resolve=done);provenance.push({url,options,resolve});return promise;
    }
    requests.push(JSON.parse(options.body));
    return Promise.resolve(new Response(new ReadableStream({start(controller){streams.push(controller);}})));
  };
  const window={DisplayTime:require('../../knowledge_service/web/display-time.js'),document:doc,fetch,TemporalScope:require('../../knowledge_service/web/temporal-scope.js'),RequestDeadline:require('../../knowledge_service/web/request-deadline.js'),innerWidth:1200,innerHeight:900,addEventListener(){},
    requestAnimationFrame(callback){frames.set(++frameId,callback);return frameId;},cancelAnimationFrame(id){frames.delete(id);}};
  const context={window,document:doc,localStorage,current:'p',wb:{epoch:0},AbortController,TextDecoder,console,
    fetch,endpoint:s=>'/api/projects/'+context.current+s,scope:()=>({known_at:null}),setTimeout,
    esc:String,labelOf:x=>x,platformGroupLabel:group=>'平台：'+group.platform,showTab(){throw Error('citation must not navigate');},status(){}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(web,'scope-fields.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(web,'provenance-drawer.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(web,'workbench.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('/* Knowledge chat:'),source.lastIndexOf("window.addEventListener('resize',()=>{wb.chart")),context);
  const get=id=>doc.getElementById(id);
  const emit=(event,data)=>streams.at(-1).enqueue(new TextEncoder().encode('event: '+event+'\ndata: '+JSON.stringify(data)+'\n\n'));
  const submit=()=>{get('qa-query').value='question';get('qa-composer').dispatch('submit');};
  const flushFrame=()=>{const callbacks=[...frames.values()];frames.clear();callbacks.forEach(callback=>callback());};
  return {context,window,doc,get,emit,submit,flushFrame,streams,requests,provenance,storage,history:()=>JSON.parse(storage.get('kg_qa_v1_p')||'null')};
}

test('temporal reference sections stay separate, inert and frozen across history',async()=>{
  const env=setup();env.submit();await settle();
  const row=(citation,status)=>({citation,kind:'chunk',text:'<img onerror=evil()>',version:2,
    provenance_ref:'answer:a#'+citation,validity_status:status,validity_basis:'explicit',
    valid_from:'2024-01-01',valid_until:'2025-01-01',usage:status==='unknown'?'qualified_quote':'reference',
    query_platform:'平台甲',source_scope:'specific',metadata:{applicability:{platforms:{mode:'specific',values:['平台甲']}}},
    exclusion_reasons:['boundary <script>'],sources:[{document_id:'d',record_version_id:'v2',version:2,title:'冻结原文',quote:'原文'}]});
  env.emit('evidence',{answer_id:'a',as_of:'2026-01-01',known_at:'2026-10-15',
    evidence:[{...row('E1','effective'),usage:'effective_basis'}],historical_references:[row('R1','expired')],
    future_references:[row('F1','not_yet_effective')],uncertain_references:[row('U1','unknown')],
    coverage_warnings:['平台乙缺少依据 <script>']});await settle();
  const panel=env.get('qa-transcript').querySelector('.qa-evidence-panel');
  assert.equal(panel.querySelectorAll('.qa-evidence-item').length,1);
  assert.equal(panel.querySelectorAll('.qa-reference-section').length,3);
  assert.equal(panel.querySelectorAll('img').length,0);
  assert.match(panel.textContent,/有效性待核对/);assert.match(panel.textContent,/未参与本次回答/);
  assert.match(panel.textContent,/仅用于文档说明/);assert.match(panel.textContent,/2024年01月01日/);
  assert.match(panel.textContent,/平台乙缺少依据 <script>/);
  assert.match(panel.querySelector('.qa-reference-section').textContent,/平台甲/);
  panel.querySelector('.qa-reference-section').querySelector('.provenance-citation').dispatch('click');
  assert.equal(env.provenance[0].url,'/api/projects/p/answers/a/evidence/R1/provenance');
  env.emit('done',{answer:'最终回答 [E1]'});env.streams.at(-1).close();await settle();
  env.window.clearKnowledgeChat();
  assert.equal(env.get('qa-transcript').querySelectorAll('.qa-reference-section').length,3);
  assert.equal(env.history()[1].historical_references[0].validity_status,'expired');
  assert.equal(env.history()[1].historical_references[0].sources[0].record_version_id,'v2');
  assert.equal(env.history()[1].historical_references[0].query_platform,'平台甲');
  assert.equal(env.history()[1].historical_references[0].metadata.applicability.platforms.values[0],'平台甲');
});

test('validated final answer replaces unsafe streamed draft and history',async()=>{
  const env=setup();env.submit();await settle();env.emit('delta',{text:'unsafe draft [R1]'});await settle();env.flushFrame();
  assert.equal(env.get('qa-transcript').querySelector('.qa-answer').textContent,'unsafe draft [R1]');
  env.emit('done',{answer:'没有已确认有效的依据。',status:'evidence_only'});env.streams.at(-1).close();await settle();
  assert.equal(env.get('qa-transcript').querySelector('.qa-answer').textContent,'没有已确认有效的依据。');
  assert.equal(env.history()[1].text,'没有已确认有效的依据。');
});

test('QA separates effective evidence counts from retrieval limits and keeps one source action',async()=>{
  const env=setup();
  vm.runInContext(fs.readFileSync(path.join(web,'qa-presentation.js'),'utf8'),env.context);
  env.submit();await settle();
  const sameTime='2026-11-23T10:05:51Z';
  const metadata={applicability:{platforms:{mode:'specific',values:['美团']}}};
  env.emit('evidence',{answer_id:'a',as_of:sameTime,known_at:sameTime,
    evidence:[{citation:'E1',kind:'chunk',text:'# 规则\n\n完整命中片段。',query_platform:'美团',
      validity_status:'effective',provenance_ref:'answer:a#E1',metadata,
      sources:[{title:'规则.md',version:2,quote:'完整命中片段。'},
        {title:'补充资料.md',version:1,quote:'另一处适用例外。'}]}],
    platform_groups:[{source_scope:'unknown',evidence_count:0},
      {source_scope:'specific',platform:'美团',evidence_count:2},
      {source_scope:'specific',platform:'淘宝',evidence_count:0}],
    coverage_warnings:['参考检索先在数据库匹配关键词，再有界读取最多 501 条候选。',
      '上下文仅补充已检索到的相邻片段，条件或例外可能不完整。',
      'None：未检索到该时点确认有效的依据，不能据此断言不存在规则。']});await settle();
  const panel=env.get('qa-transcript').querySelector('.qa-evidence-panel');
  const article=panel.querySelector('.qa-evidence-item');
  assert.equal(article.querySelectorAll('.provenance-citation').length,1);
  const inspection=article.querySelector('.qa-evidence-item-details');
  assert.doesNotMatch(inspection.textContent,/完整命中片段/);
  assert.match(inspection.textContent,/另一处适用例外/);
  const otherSource=inspection.querySelector('.qa-source-item');
  assert.equal(otherSource.hidden,true);
  inspection.querySelector('.qa-source-more').dispatch('click');
  assert.equal(otherSource.hidden,false);
  const detail=panel.querySelector('.qa-evidence-details');
  assert.equal(detail.children[0].textContent,'本次检索范围');
  assert.match(detail.querySelector('.qa-time-context').textContent,/规则判断时点/);
  assert.doesNotMatch(detail.querySelector('.qa-time-context').textContent,/知识记录截止/);
  const groups=detail.querySelector('.qa-reasoning-block');
  assert.match(groups.textContent,/美团.*2 条有效依据/);
  assert.match(groups.textContent,/淘宝.*未找到确认有效的依据/);
  assert.doesNotMatch(groups.textContent,/undefined|范围待确认/);
  const limits=detail.querySelector('.qa-retrieval-limits');
  assert.equal(limits.tagName,'DETAILS');
  assert.ok(!limits.open);
  assert.match(limits.textContent,/501 条候选/);
});

test('QA submits the shared historical business time and explicit reference visibility',async()=>{
  const env=setup(),scope={valid_at:'2024-01-01T00:30:00.000Z',known_at:null,include_unknown:false};
  env.context.scope=()=>scope;env.get('qa-include-references').checked=false;
  env.submit();await settle();assert.equal(env.requests[0].valid_at,scope.valid_at);
  assert.equal(env.requests[0].include_references,false);assert.equal(env.requests[0].known_at,null);assert.equal(env.requests[0].include_unknown,false);
  assert.deepEqual(env.history()[0].scope,scope);
});

test('QA independently requests ontology expansion and retains its result in history',async()=>{
  const env=setup(),toggle=env.doc.createElement('input');
  toggle.id='qa-ontology-expansion';toggle.checked=true;env.doc.body.append(toggle);
  env.submit();await settle();
  assert.equal(env.requests[0].ontology_expansion,true);
  const expansion={matched:false,has_hierarchy:true,type_hits:0};
  env.emit('evidence',{answer_id:'expanded',evidence:[],ontology_expansion:expansion});
  env.emit('done',{answer:'文档说明'});env.streams.at(-1).close();await settle();
  assert.deepEqual(env.history()[1].ontology_expansion,expansion);
});

test('reference-only response remains visible and never invents a source opener',async()=>{
  const env=setup();env.submit();await settle();
  env.emit('evidence',{answer_id:'missing',evidence:[],historical_references:[{citation:'R1',text:'旧条款',source_id:'latest-doc',validity_status:'expired'}]});
  await settle();const panel=env.get('qa-transcript').querySelector('.qa-evidence-panel');
  assert.ok(panel);assert.equal(panel.querySelectorAll('.qa-evidence-item').length,0);
  assert.equal(panel.querySelectorAll('.provenance-citation').length,0);
  assert.match(panel.textContent,/冻结来源不可用/);assert.equal(env.provenance.length,0);
});

test('U citations remain offered after finalization and history preserves only allowed temporal metadata',async()=>{
  const env=setup();env.submit();await settle();
  env.emit('evidence',{answer_id:'u',evidence:[],uncertain_references:[{citation:'U1',text:'待核对原文',usage:'qualified_quote',
    validity_status:'unknown',provenance_ref:'answer:u#U1',metadata:{published_at:'2024-01-01',effective_time_source:'published_at',secret:'private'}}]});
  env.emit('done',{answer:'有效性待核对：原文写明……[U1]'});env.streams.at(-1).close();await settle();
  const citation=env.get('qa-transcript').querySelector('.qa-answer').querySelector('.provenance-citation');
  assert.ok(citation);citation.dispatch('click');assert.match(env.provenance[0].url,/\/U1\/provenance$/);
  assert.deepEqual(env.history()[1].uncertain_references[0].metadata,{published_at:'2024-01-01',effective_time_source:'published_at'});
});

test('changed QA assets and accessible temporal controls have current cache versions',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.match(html,/<script\b[^>]*\bsrc="\/assets\/workbench\.js\?v=[\w.-]+"/);
  assert.ok(html.includes('/assets/workbench.css?v=split-previews-20261110-1'));
  assert.ok(html.includes('/assets/qa-presentation.js?v=qa-source-and-scope-20261123-1'));
  assert.ok(html.includes('/assets/provenance-drawer.js?v=community-evidence-layout-20261125-2'));
  assert.ok(html.includes('/assets/qa-page.css?v=retrieval-match-20261123-1'));
  assert.match(html,/id="qa-include-references"[^>]*checked/);
  assert.doesNotMatch(html,/id="qa-as-of"/);assert.match(html,/id="scope-valid-at"[^>]*type="datetime-local"/);
});

test('streaming and failed history visibly mark the answer as an unvalidated draft',async()=>{
  const env=setup();env.submit();await settle();env.emit('delta',{text:'草稿内容'});await settle();env.flushFrame();
  assert.match(env.get('qa-transcript').querySelector('.qa-draft-status').textContent,/生成中，尚未完成依据校验/);
  env.emit('error',{detail:'校验失败'});env.streams.at(-1).close();await settle();
  assert.match(env.get('qa-transcript').querySelector('.qa-draft-status').textContent,/未完成的草稿/);
  env.window.clearKnowledgeChat();assert.match(env.get('qa-transcript').querySelector('.qa-draft-status').textContent,/未完成的草稿/);
  const done=setup();done.submit();await settle();done.emit('done',{answer:'最终回答'});done.streams.at(-1).close();await settle();
  assert.equal(done.get('qa-transcript').querySelector('.qa-draft-status'),null);
});

test('zero effective platform evidence describes the gap and governance dates survive history',async()=>{
  const env=setup();env.submit();await settle();
  const row={citation:'U1',text:'原文待核对',validity_status:'unknown',validity_basis:'missing_valid_from',metadata:{effective_time_source:'published_at'},
    source_eligibility:[{document_version_id:'original-v1',governance_version_id:'governed-v3',validity_status:'expired',valid_from:'2020-01-01',valid_until:'2025-01-01',effective_time_source:'explicit',secret:'private'}]};
  env.emit('evidence',{answer_id:'g',evidence:[],platform_groups:[{platform:'平台甲',source_scope:'specific',evidence_rows:[]}],uncertain_references:[row]});
  env.emit('done',{answer:'原文说明'});env.streams.at(-1).close();await settle();
  const panel=env.get('qa-transcript').querySelector('.qa-evidence-panel');
  assert.match(panel.textContent,/未找到确认有效的依据/);
  assert.match(panel.textContent,/有效期核对：版本 governed-v3/);assert.match(panel.textContent,/失效（不含）：2025年01月01日/);
  assert.match(panel.textContent,/未维护生效时间/);assert.match(panel.textContent,/发布日期推定/);
  const governance=env.history()[1].uncertain_references[0].source_eligibility[0];
  assert.equal(governance.document_version_id,'original-v1');assert.equal(governance.governance_version_id,'governed-v3');assert.equal(governance.secret,undefined);
  env.window.clearKnowledgeChat();assert.match(env.get('qa-transcript').textContent,/governed-v3/);
});

test('unknown source validity explains the missing dates in business language',async()=>{
  const env=setup();env.submit();await settle();
  env.emit('evidence',{answer_id:'undated',evidence:[],uncertain_references:[{
    citation:'U1',kind:'chunk',text:'未成年人模式每日22时至次日6时限制直播。',
    usage:'qualified_quote',validity_status:'unknown',validity_basis:'source_validity',
    exclusion_reasons:['source_validity','unknown'],
    source_eligibility:[{document_version_id:'original-v1',governance_version_id:'governed-v3',
      validity_status:'unknown',valid_from:null,valid_until:null}]
  }]});await settle();
  const panel=env.get('qa-transcript').querySelector('.qa-evidence-panel');
  const reason=panel.querySelector('.qa-reference-exclusions').textContent;
  assert.match(reason,/来源文档/);
  assert.match(reason,/未维护生效时间/);
  assert.match(reason,/可用于文档说明/);
  assert.doesNotMatch(reason,/source_validity|unknown/);
});

for(const [key,citation,status] of [['historical_references','R1','expired'],['future_references','F1','not_yet_effective']]){
  test(status+' document explanation keeps its usage after history reload',async()=>{
    const env=setup();env.submit();await settle();
    env.emit('evidence',{answer_id:'document',evidence:[],[key]:[{
      citation,kind:'chunk',text:'冻结文档说明',usage:'reference',validity_status:status,
      document_quote_allowed:true,exclusion_reasons:[status]
    }]});
    env.emit('done',{answer:'按文档说明 ['+citation+']'});env.streams.at(-1).close();await settle();
    assert.equal(env.history()[1][key][0].document_quote_allowed,true);
    env.window.clearKnowledgeChat();
    const item=env.get('qa-transcript').querySelector('.qa-reference-item');
    assert.match(item.querySelector('.qa-reference-usage').textContent,/仅用于文档说明/);
    assert.match(item.querySelector('.qa-reference-exclusions').textContent,/可用于文档说明/);
  });
}

