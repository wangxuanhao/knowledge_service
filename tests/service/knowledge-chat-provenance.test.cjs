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
  for(const id of ['qa-transcript','qa-hero','qa-query','qa','qa-progress','qa-summary','qa-live','qa-scroll','qa-composer','qa-clear','generate']){
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
    esc:String,labelOf:x=>x,showTab(){throw Error('citation must not navigate');},status(){}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(web,'provenance-drawer.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(web,'workbench.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('/* Knowledge chat:'),source.lastIndexOf("window.addEventListener('resize',()=>{wb.chart")),context);
  const get=id=>doc.getElementById(id);
  const emit=(event,data)=>streams.at(-1).enqueue(new TextEncoder().encode('event: '+event+'\ndata: '+JSON.stringify(data)+'\n\n'));
  const submit=()=>{get('qa-query').value='question';get('qa-composer').dispatch('submit');};
  return {context,window,doc,get,emit,submit,streams,requests,provenance,storage,history:()=>JSON.parse(storage.get('kg_qa_v1_p')||'null')};
}
const evidence=id=>({answer_id:id,retrieval_run_id:'run-'+id,channels:{chunk:1},evidence:[{
  citation:'E1',kind:'chunk',text:'x'.repeat(500),version:2,provenance_ref:'answer:'+id+'#E1',
  metadata:{secret:1},properties:{private:true},embedding:[1],document:'full'}]});

test('running evidence opens once; safe done decorations and bounded history restore',async()=>{
  const env=setup();env.submit();env.emit('evidence',evidence('a'));await settle();
  const turn=env.get('qa-transcript').querySelector('.qa-turn-assistant');
  const citation=turn.querySelector('.provenance-citation');
  assert.ok(citation,'running evidence must offer a citation button');
  assert.equal(turn.dataset.state,'streaming');citation.dispatch('click');
  assert.equal(env.provenance.length,1);
  assert.equal(env.provenance[0].url,'/api/projects/p/answers/a/evidence/E1/provenance');
  const literal='<img onerror=evil()>[E1] [E99]';env.emit('delta',{text:literal});await settle();
  const answer=turn.querySelector('.qa-answer');assert.equal(answer.textContent,literal);assert.equal(answer.querySelectorAll('button').length,0);
  env.emit('done',{});env.streams.at(-1).close();await settle();
  assert.equal(answer.textContent,literal);assert.equal(answer.querySelectorAll('button').length,1);
  // C1：结构待定的单列数据（structure_pending）要跟着历史一起存/取 ——
  // 不然切一次标签回来，那几条"不算证据的知识"就从证据面板上消失了（用户会以为系统把它们丢了）。
  // 这条流没有结构待定项，所以是 null；有值时的渲染由 test_structure_pending_ui.py 覆盖。
  assert.deepEqual(env.history(),[{kind:'user',text:'question'},{kind:'assistant',text:literal,answer_id:'a',retrieval_run_id:'run-a',
    evidence:[{citation:'E1',text_preview:'x'.repeat(240),kind:'chunk',version:2,provenance_ref:'answer:a#E1'}],
    structure_pending:null}]);
  env.window.clearKnowledgeChat();
  assert.equal(env.get('qa-transcript').querySelector('.qa-answer').querySelectorAll('button').length,1);
  assert.ok(env.get('qa-transcript').querySelector('.qa-evidence-panel').querySelector('.provenance-citation'));
  assert.equal(env.provenance[0].options.signal.aborted,true);
});

test('retry replaces failed assistant history and new identifiers without duplicating question',async()=>{
  const env=setup();env.submit();env.emit('evidence',evidence('old'));env.emit('error',{detail:'failed'});await settle();
  assert.equal(env.history()[1].error,'failed');
  env.get('qa-transcript').querySelector('.qa-retry').dispatch('click');
  env.emit('evidence',evidence('new'));env.emit('delta',{text:'ok [E1]'});await settle();
  assert.equal(env.history()[1].error,'failed','pending retry must not overwrite the last terminal history');
  assert.equal(env.history()[1].answer_id,'old');
  env.emit('done',{});env.streams.at(-1).close();await settle();
  assert.equal(env.requests.length,2);assert.deepEqual(env.requests[0],env.requests[1]);
  assert.equal(env.history().length,2);assert.equal(env.history()[1].answer_id,'new');assert.equal(env.history()[1].retrieval_run_id,'run-new');
  assert.equal(env.history()[1].error,undefined);assert.equal(env.get('qa-transcript').children.length,2);
});

test('interrupted streams restore only the submitted question, never a blank completed answer',async()=>{
  for(const phase of ['submit','evidence','partial']){
    const env=setup();env.submit();
    if(phase!=='submit')env.emit('evidence',evidence('pending'));
    if(phase==='partial')env.emit('delta',{text:'unfinished answer [E1]'});
    await settle();
    assert.deepEqual(env.history(),[{kind:'user',text:'question'}],phase+' must not persist a pending assistant');
    env.window.clearKnowledgeChat();
    assert.equal(env.get('qa-transcript').querySelectorAll('.qa-turn-user').length,1);
    assert.equal(env.get('qa-transcript').querySelectorAll('.qa-turn-assistant').length,0);
    env.streams.at(-1).close();await settle();
    assert.deepEqual(env.history(),[{kind:'user',text:'question'}],'interrupted reader must not persist a terminal state');
  }
});

test('legacy text stays inert and clearing/project change cancels requests without stale persistence',async()=>{
  const env=setup();env.storage.set('kg_qa_v1_p',JSON.stringify([{kind:'assistant',text:'legacy [E1]'}]));env.window.clearKnowledgeChat();
  assert.equal(env.get('qa-transcript').querySelector('.qa-answer').querySelectorAll('button').length,0);
  assert.ok(env.get('qa-transcript').textContent.includes(env.window.ProvenanceDrawer.LEGACY_NOTE));
  env.window.clearKnowledgeChat({clearPersist:true});env.submit();env.emit('evidence',evidence('a'));await settle();
  env.get('qa-transcript').querySelector('.provenance-citation').dispatch('click');
  env.context.current='other';env.context.wb.epoch++;env.window.clearKnowledgeChat();
  assert.equal(env.provenance[0].options.signal.aborted,true);
  env.provenance[0].resolve({ok:true,json:async()=>({subject:{answer_id:'a',citation:'E1'},nodes:[],integrity:{complete:true}})});
  env.streams.at(-1).close();await settle();
  assert.equal(env.get('provenance-drawer').hidden,true);assert.equal(env.get('qa-transcript').children.length,0);
  assert.equal(env.storage.has('kg_qa_v1_other'),false);
});
