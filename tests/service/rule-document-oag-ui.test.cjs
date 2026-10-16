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
  for(const id of ['qa-transcript','qa-hero','qa-query','qa','qa-progress','qa-summary','qa-live','qa-scroll','qa-composer','qa-clear','generate','qa-as-of','qa-include-references']){
    const node=doc.createElement('div');node.id=id;doc.body.append(node);
  }
  const storage=new Map(),requests=[],provenance=[],streams=[];
  const localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  const fetch=(url,options)=>{
    if(url.endsWith('/provenance')){
      let resolve;const promise=new Promise(done=>resolve=done);provenance.push({url,options,resolve});return promise;
    }
    requests.push(JSON.parse(options.body));
    return Promise.resolve(new Response(new ReadableStream({start(controller){streams.push(controller);}})));
  };
  const window={document:doc,fetch,innerWidth:1200,innerHeight:900,addEventListener(){}};
  const context={window,document:doc,localStorage,current:'p',wb:{epoch:0},AbortController,TextDecoder,console,
    fetch,endpoint:s=>'/api/projects/'+context.current+s,scope:()=>({known_at:null}),setTimeout,
    esc:String,labelOf:x=>x,platformGroupLabel:group=>'平台：'+group.platform,showTab(){throw Error('citation must not navigate');},status(){}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(web,'provenance-drawer.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(web,'workbench.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('/* Knowledge chat:'),source.lastIndexOf("window.addEventListener('resize',()=>{wb.chart")),context);
  const get=id=>doc.getElementById(id);
  const emit=(event,data)=>streams.at(-1).enqueue(new TextEncoder().encode('event: '+event+'\ndata: '+JSON.stringify(data)+'\n\n'));
  const submit=()=>{get('qa-query').value='question';get('qa-composer').dispatch('submit');};
  return {context,window,doc,get,emit,submit,streams,requests,provenance,storage,history:()=>JSON.parse(storage.get('kg_qa_v1_p')||'null')};
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
  assert.match(panel.textContent,/仅用于文档说明/);assert.match(panel.textContent,/2024-01-01/);
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
  const env=setup();env.submit();await settle();env.emit('delta',{text:'unsafe draft [R1]'});
  env.emit('done',{answer:'没有已确认有效的依据。',status:'evidence_only'});env.streams.at(-1).close();await settle();
  assert.equal(env.get('qa-transcript').querySelector('.qa-answer').textContent,'没有已确认有效的依据。');
  assert.equal(env.history()[1].text,'没有已确认有效的依据。');
});

test('QA controls submit a historical business time and explicit reference visibility',async()=>{
  const env=setup();env.get('qa-as-of').value='2024-01-01T08:30';env.get('qa-include-references').checked=false;
  env.submit();await settle();assert.equal(env.requests[0].valid_at,new Date('2024-01-01T08:30').toISOString());
  assert.equal(env.requests[0].include_references,false);assert.equal(env.requests[0].known_at,null);
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
  assert.ok(html.includes('/assets/workbench.js?v=ontology-version-1'));
  for(const asset of ['workbench.css','provenance-drawer.js'])assert.ok(html.includes('/assets/'+asset+'?v=rule-oag-2'));
  assert.match(html,/id="qa-include-references"[^>]*checked/);
  assert.match(html,/id="qa-as-of"[^>]*type="datetime-local"/);
});

test('streaming and failed history visibly mark the answer as an unvalidated draft',async()=>{
  const env=setup();env.submit();await settle();env.emit('delta',{text:'草稿内容'});await settle();
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
  assert.match(panel.textContent,/未检索到本次时点确认有效的依据/);
  assert.match(panel.textContent,/有效期核对：版本 governed-v3/);assert.match(panel.textContent,/失效（不含）：2025-01-01/);
  assert.match(panel.textContent,/未维护生效时间/);assert.match(panel.textContent,/发布日期推定/);
  const governance=env.history()[1].uncertain_references[0].source_eligibility[0];
  assert.equal(governance.document_version_id,'original-v1');assert.equal(governance.governance_version_id,'governed-v3');assert.equal(governance.secret,undefined);
  env.window.clearKnowledgeChat();assert.match(env.get('qa-transcript').textContent,/governed-v3/);
});

