const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const filename=path.resolve(__dirname,'../../knowledge_service/web/provenance-drawer.js');
function exportsForTest(){
  assert.ok(fs.existsSync(filename),'provenance drawer module exists');
  return require(filename);
}

test('tokenizer keeps literal text and duplicate strict citations without producing HTML',()=>{
  const {tokenizeCitations}=exportsForTest();
  const input='<img onerror=x>[E1] [E01] [E0] [e2] [E-3] [E2.0] [E1]';
  const segments=tokenizeCitations(input);
  assert.deepEqual(segments.filter(s=>s.type==='citation').map(s=>s.citation),['E1','E1']);
  assert.equal(segments.map(s=>s.text).join(''),input);
  assert.ok(segments.every(s=>!Object.hasOwn(s,'html')));
  assert.deepEqual(tokenizeCitations('[E1][E2]',new Set(['E2'])),[
    {type:'text',text:'[E1]'},{type:'citation',text:'[E2]',citation:'E2'}]);
  assert.deepEqual(tokenizeCitations('[E1]',new Set()),[{type:'text',text:'[E1]'}]);
  const huge='E'+'9'.repeat(400);
  assert.equal(tokenizeCitations('['+huge+']')[0].citation,huge);
  assert.deepEqual(tokenizeCitations(null),[]);
});

test('history summary strips arbitrary objects and preserves only typed immutable display fields',()=>{
  const {sanitizeEvidenceSummary}=exportsForTest();
  assert.deepEqual(sanitizeEvidenceSummary({citation:'E1',text_preview:'x'.repeat(400),kind:'entity',version:3,
    provenance_ref:'answer:a#E1',metadata:{secret:'token'},properties:{},embedding:[1],id:'r',text:'full'}),
  {citation:'E1',text_preview:'x'.repeat(240),kind:'entity',version:3,provenance_ref:'answer:a#E1'});
  assert.deepEqual(sanitizeEvidenceSummary({citation:{secret:1},text_preview:[],kind:{},version:'3',provenance_ref:{}}),{});
  assert.deepEqual(sanitizeEvidenceSummary(null),{});
  assert.deepEqual(sanitizeEvidenceSummary({citation:'E01',version:Infinity}),{});
  assert.deepEqual(sanitizeEvidenceSummary({citation:'E1\n'}),{});
});

// A tiny DOM surface exercises the actual event/render code without a browser dependency.
class Element{
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.attrs={};this.dataset={};this.hidden=false;this.listeners={};this._text='';this.isConnected=true;}
  setAttribute(k,v){this.attrs[k]=String(v);if(k==='id')this.id=v;}
  getAttribute(k){return this.attrs[k]??null;}
  appendChild(n){this.children.push(n);n.parentNode=this;return n;}
  append(...nodes){nodes.forEach(n=>this.appendChild(n));}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  set textContent(v){this.children=[];this._text=String(v);}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}
  dispatch(k,event={}){for(const fn of this.listeners[k]||[])fn({target:this,preventDefault(){},...event});if(k==='click')this.onclick?.(event);}
  focus(){this.ownerDocument.activeElement=this;}
  contains(n){return n===this||this.children.some(c=>c.contains?.(n));}
}
function setup(fetch){
  const doc={listeners:{},createElement(tag){return new Element(tag,this);},createTextNode(text){const e=new Element('#text',this);e.textContent=text;return e;},
    getElementById(id){return flatten(this.body).find(n=>n.id===id)||null;},addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}};
  doc.body=doc.createElement('body');doc.activeElement=doc.body;
  const window={document:doc,fetch,AbortController};
  const context={window,document:doc,fetch,AbortController,console,current:'p'};
  assert.ok(fs.existsSync(filename),'provenance drawer module exists');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),context);
  return {drawer:window.ProvenanceDrawer,doc,context,window};
}
function flatten(n){return [n,...n.children.flatMap(flatten)];}
function payload(answer='a') {return {subject:{answer_id:answer,citation:'E1'},nodes:[
  {type:'record_version',ref:'r',label:'<script>literal</script>',details:{record_id:'record',version:2,text_preview:'safe',metadata:'SECRET'}},
  {type:'document_version',ref:'d',label:'doc',details:{title:'doc',version:1,excerpt_before:'before',highlight:'<img>',excerpt_after:'after'}}],
  edges:[{source_ref:'r',target_ref:'d',relation:'sourced-from'}],integrity:{complete:false,warnings:[{code:'missing',node_ref:'d',message:'历史缺口'}]}};}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}

test('drawer is initially hidden, accessible and renders fixed fields, branches, warnings, and raw JSON',async()=>{
  const data=payload();const {drawer,doc,window}=setup(async()=>({ok:true,json:async()=>data}));
  const root=doc.getElementById('provenance-drawer');assert.ok(root.hidden);
  assert.equal(root.getAttribute('aria-labelledby'),'provenance-drawer-title');
  const trigger=doc.createElement('button');trigger.focus();
  await drawer.open({projectId:'p',answerId:'a',citation:'E1',trigger});
  assert.equal(root.hidden,false);
  const nodes=flatten(root),tabs=nodes.filter(n=>n.getAttribute('role')==='tab');
  assert.deepEqual(tabs.map(n=>n.textContent),['证据链','API 数据']);
  assert.equal(doc.getElementById('provenance-status').getAttribute('aria-live'),'polite');
  const chain=doc.getElementById('provenance-chain');
  assert.ok(chain.textContent.includes('<script>literal</script>'));
  assert.ok(!chain.textContent.includes('SECRET'));assert.ok(chain.textContent.includes('历史缺口'));
  assert.ok(chain.textContent.includes('sourced-from'));
  assert.equal(doc.getElementById('provenance-raw').textContent,JSON.stringify(data,null,2));
  assert.equal(root.dataset.state,'partial');
  let excerpt,record;window.openFrozenSourceEvidence=x=>excerpt=x;window.openRecordHistory=x=>record=x;
  nodes.find(n=>n.textContent==='查看历史原文').dispatch('click');assert.equal(excerpt.highlight,'<img>');
  nodes.find(n=>n.textContent==='版本历史').dispatch('click');assert.equal(record,'record');
  tabs[1].dispatch('click');assert.equal(chain.hidden,true);
  root.dispatch('keydown',{key:'Escape'});assert.equal(root.hidden,true);assert.equal(doc.activeElement,trigger);
});

test('GET encodes each identifier, aborts obsolete requests and ignores stale answer/project responses',async()=>{
  const requests=[];const {drawer,doc,context}=setup((url,options)=>{const item={url,options,...deferred()};requests.push(item);return item.promise;});
  context.current='p /';
  const first=drawer.open({projectId:'p /',answerId:'a /',citation:'E1'});
  assert.equal(requests[0].url,'/api/projects/p%20%2F/answers/a%20%2F/evidence/E1/provenance');
  assert.equal(requests[0].options.method,'GET');
  const second=drawer.open({projectId:'p /',answerId:'b',citation:'E1'});
  assert.equal(requests[0].options.signal.aborted,true);
  requests[1].resolve({ok:true,json:async()=>payload('b')});await second;
  requests[0].resolve({ok:true,json:async()=>payload('a /')});await first;
  assert.ok(doc.getElementById('provenance-raw').textContent.includes('"b"'));
  drawer.onProjectChange('other');assert.equal(doc.getElementById('provenance-drawer').hidden,true);
  const third=drawer.open({projectId:'p /',answerId:'c',citation:'E1'});
  context.current='other';requests[2].resolve({ok:true,json:async()=>payload('c')});await third;
  assert.ok(!doc.getElementById('provenance-raw').textContent.includes('"c"'));
});

test('404, cancellation, empty and failed loads have distinct states',async()=>{
  for(const [response,expected] of [[{ok:false,status:404},'not-found'],[{ok:false,status:500},'failed'],
    [{ok:true,json:async()=>({subject:{answer_id:'a',citation:'E1'},nodes:[],edges:[],integrity:{complete:true,warnings:[]}})},'empty']]){
    const {drawer,doc}=setup(async()=>response);await drawer.open({projectId:'p',answerId:'a',citation:'E1'});
    assert.equal(doc.getElementById('provenance-drawer').dataset.state,expected);
  }
  const {drawer,doc}=setup(async()=>{throw Object.assign(new Error('cancelled'),{name:'AbortError'});});
  await drawer.open({projectId:'p',answerId:'a',citation:'E1'});
  assert.equal(doc.getElementById('provenance-drawer').dataset.state,'cancelled');
});

test('decoration only activates offered citations and legacy note remains literal',()=>{
  const {drawer,doc}=setup(async()=>({ok:false,status:404}));const host=doc.createElement('p');
  drawer.decorateAnswer(host,'<img>[E1] [E2] [E1]',{projectId:'p',answerId:'a',evidence:[{citation:'E1'}]});
  assert.equal(host.textContent,'<img>[E1] [E2] [E1]');
  assert.equal(flatten(host).filter(n=>n.tagName==='BUTTON').length,2);
  drawer.renderLegacyNote(host);assert.ok(host.textContent.includes('该历史回答生成于溯源记录启用前'));
});
