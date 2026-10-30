const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const read=name=>fs.readFileSync('knowledge_service/web/'+name,'utf8');
const never=()=>new Promise(()=>{});
const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
async function outcome(promise){
  return Promise.race([promise.then(value=>({value}),error=>({error})),new Promise(resolve=>setTimeout(()=>resolve({pending:true}),25))]);
}
function fixture(extra={}){
  let now=0,next=0;const timers=new Map();
  const context={AbortController,DOMException,Error,JSON,FormData,Boolean,performance:{now:()=>now},
    setTimeout(fn,ms){const id=++next;timers.set(id,{fn,at:now+ms});return id;},clearTimeout:id=>timers.delete(id),...extra};
  context.window=context;vm.createContext(context);
  if(fs.existsSync('knowledge_service/web/request-deadline.js'))vm.runInContext(read('request-deadline.js'),context);
  return {context,timers,advance(ms){now+=ms;for(const [id,item] of [...timers])if(item.at<=now){timers.delete(id);item.fn();}}};
}
function installApi(context){
  const source=read('app.js');
  vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('\nfunction endpoint('))+';this.api=api;',context);
}
function declaration(source,name){
  const found=source.match(new RegExp('  (?:async )?function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?\\n  \\}'));
  assert.ok(found,'production '+name+' exists');return found[0];
}

test('a hung Enter search exits busy state, reports the deadline and allows another search',async()=>{
  let signal;const controls=new Map(),errors=[];
  const get=id=>{if(!controls.has(id))controls.set(id,{value:'',textContent:'',disabled:false});return controls.get(id);};
  get('query').value='未成年';get('search-mode').value='hybrid';
  const f=fixture({get,current:'p',ui:{request:0},graphExpandState:{},scope:()=>({}),
    ScopeFields:{requireRetrievalScope(){}},endpoint:s=>'/api/projects/p'+s,kgTime:()=>()=>{},status:message=>errors.push(message),
    renderHits(){},expansionSummary:()=>'',updateChip(){},
    fetch:(_url,init)=>{signal=init.signal;return never();}});
  installApi(f.context);
  const source=read('workspace.js');
  vm.runInContext(source.slice(source.indexOf('  function setActionBusy('),source.indexOf('  const ontoName='))+
    declaration(source,'runSearch')+";act('search',runSearch);",f.context);
  const running=get('search').onclick();await flush();
  assert.equal(get('search').disabled,true);
  f.advance(45001);await flush();
  const settled=await outcome(running);
  assert.equal(settled.pending,undefined,'search stays grey forever when its request never settles');
  assert.equal(get('search').disabled,false);
  assert.match(get('search-summary').textContent,/超时/);
  assert.match(errors.at(-1),/超时/);
  assert.equal(signal.aborted,true);
  assert.equal(f.timers.size,0);
  f.context.fetch=async()=>({ok:true,text:async()=>JSON.stringify({hits:[],active_mode:'hybrid',candidate_count:0})});
  await get('search').onclick();assert.equal(get('search').disabled,false);
  assert.match(get('search-summary').textContent,/0 命中/);assert.equal(f.timers.size,0);
});

test('search deadline also covers a response whose headers arrive but body never completes',async()=>{
  let signal;
  const f=fixture({fetch:async(_url,init)=>{signal=init.signal;return {ok:true,text:never};}});
  installApi(f.context);
  const pending=f.context.api('/search',{},'POST',{timeoutMs:100,message:'检索超时'});await flush();
  f.advance(101);await flush();const settled=await outcome(pending);
  assert.equal(settled.pending,undefined,'response body can pin the busy state forever');
  assert.match(settled.error.message,/检索超时/);assert.equal(signal.aborted,true);
});

test('conversation save deadline includes its response body and does not discard the outgoing snapshot',async()=>{
  let init;const f=fixture();vm.runInContext(read('qa-conversations.js'),f.context);
  const client=f.context.QaConversations.createClient({timeoutMs:100,fetch:async(_url,options)=>{init=options;return {ok:true,status:200,json:never};}});
  const messages=[{kind:'user',text:'未成年'}];
  const pending=client.save('p','conversation',{expected_version:1,messages});await flush();
  f.advance(101);await flush();const settled=await outcome(pending);
  assert.equal(settled.pending,undefined,'a stalled save prevents QA from reaching its answer stream forever');
  assert.match(settled.error.message,/超时/);assert.equal(init.signal.aborted,true);
  assert.equal(messages[0].text,'未成年');
});

function streamFixture(fetch){
  const failures=[];
  const node=()=>({dataset:{},replaceChildren(){},append(){},querySelector(){return null;}}),turn=node();
  const f=fixture({fetch,busy:false,preparing:false,sessionManager:null,current:'p',serial:0,controller:null,
    qaHistory:[],ScopeFields:{requireRetrievalScope(){}},summary:{textContent:''},progress:{textContent:''},
    live:{textContent:''},send:{disabled:false},input:{focus(){}},provenance:{close(){}},document:{createElement:node},
    endpoint:s=>'/api/projects/p'+s,addTurn:()=>turn,markDraft(){},persist(){},
    addFailure:(_turn,message)=>failures.push(message),TextDecoder,chat:{splitSseBuffer:()=>({events:[],rest:''})}});
  vm.runInContext(declaration(read('workbench.js'),'stream')+';this.stream=stream;',f.context);
  return {...f,turn,failures};
}

test('QA stream waiting for its first response times out and restores Send with a visible retry error',async()=>{
  let signal;const f=streamFixture((_url,init)=>{signal=init.signal;return never();});
  const pending=f.context.stream({query:'未成年',retrieval_mode:'hybrid'});await flush();
  assert.equal(f.context.send.disabled,true);f.advance(45001);await flush();
  const settled=await outcome(pending);
  assert.equal(settled.pending,undefined,'QA waits forever for response headers');
  assert.equal(f.context.busy,false);assert.equal(f.context.send.disabled,false);
  assert.equal(f.turn.dataset.state,'error');assert.match(f.failures[0],/超时/);
  assert.equal(signal.aborted,true);assert.equal(f.context.qaHistory[0].error,f.failures[0]);
});

test('QA stream that stalls between chunks releases its reader and restores Send',async()=>{
  let signal,reads=0,cancelled=false;
  const f=streamFixture(async(_url,init)=>{signal=init.signal;return {ok:true,body:{getReader:()=>({
    read:()=>++reads===1?Promise.resolve({done:false,value:new TextEncoder().encode(': heartbeat\n\n')}):never(),
    cancel:async()=>{cancelled=true;}
  })}};});
  const pending=f.context.stream({query:'未成年',retrieval_mode:'hybrid'});await flush();
  assert.equal(reads,2);f.advance(60001);await flush();const settled=await outcome(pending);
  assert.equal(settled.pending,undefined,'SSE stalls forever after the response begins');
  assert.equal(f.context.send.disabled,false);assert.match(f.failures[0],/超时/);
  assert.equal(signal.aborted,true);assert.equal(cancelled,true);assert.equal(f.timers.size,0);
});

test('a terminal answer completes even if the transport keeps its connection open',async()=>{
  let reads=0,cancelled=false;
  const f=streamFixture(async()=>({ok:true,body:{getReader:()=>({
    read:()=>++reads===1?Promise.resolve({done:false,value:new TextEncoder().encode('event: done\n\n')}):never(),
    cancel:async()=>{cancelled=true;}
  })}}));
  f.context.chat={splitSseBuffer:()=>({events:[{event:'done',data:{answer_id:'a',answer:'回答内容'}}],rest:''}),
    completeSentences:text=>({announce:text,rest:''})};
  f.context.provenance.decorateAnswer=()=>{};f.context.answerContext=()=>({});
  const pending=f.context.stream({query:'未成年',retrieval_mode:'hybrid'});await flush();
  const settled=await outcome(pending);
  assert.equal(settled.pending,undefined,'a validated done event must not wait for connection EOF');
  assert.equal(f.context.send.disabled,false);assert.equal(f.turn.dataset.state,'done');
  assert.equal(f.context.qaHistory[0].text,'回答内容');assert.equal(cancelled,true);assert.equal(f.timers.size,0);
});

test('cancelling an older request does not cancel a subsequent request or leak deadlines',async()=>{
  const f=fixture(),parent=new AbortController();
  const first=f.context.RequestDeadline.create({signal:parent.signal,timeoutMs:100});
  const pending=first.wait(never());parent.abort(new DOMException('changed project','AbortError'));
  await assert.rejects(pending,error=>error.name==='AbortError');first.close();assert.equal(f.timers.size,0);
  const next=f.context.RequestDeadline.create({timeoutMs:100});
  assert.equal(await next.wait(Promise.resolve('next answer')),'next answer');
  f.advance(1000);assert.equal(next.signal.aborted,false);next.close();assert.equal(f.timers.size,0);
});
