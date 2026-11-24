const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

class FakeWorker {
  static instances=[];
  constructor(){this.listeners=new Map();this.terminated=false;FakeWorker.instances.push(this);}
  addEventListener(type,fn){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(fn);}
  removeEventListener(type,fn){this.listeners.get(type)?.delete(fn);}
  postMessage(data){this.sent=data;}
  terminate(){this.terminated=true;}
  emit(type,data={}){for(const fn of [...this.listeners.get(type)||[]])fn(data);}
  listenerCount(){return [...this.listeners.values()].reduce((n,set)=>n+set.size,0);}
}
function loader(){
  FakeWorker.instances=[];
  const context={window:{},Worker:FakeWorker,AbortController,DOMException,console};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  return new context.window.FullGraph.SessionLoader({});
}
async function outcome(promise){return Promise.race([promise.then(value=>({value}),error=>({error})),new Promise(resolve=>setTimeout(()=>resolve({pending:true}),40))]);}

for(const type of ['error','messageerror'])test('layout rejects and cleans up after worker '+type,async()=>{
  const subject=loader(),controller=new AbortController();
  const pending=subject.layout([{id:'a'}],controller.signal),worker=FakeWorker.instances[0];
  worker.emit(type,{message:'worker failed'});
  const result=await outcome(pending);
  assert.ok(result.error,'worker failure must reject instead of leaving layout pending');
  assert.match(result.error.message,/worker failed|布局/);
  assert.equal(worker.listenerCount(),0);
  assert.equal(worker.terminated,true);
  assert.equal(subject.worker,null);
});

test('layout cancellation rejects once and removes all worker listeners',async()=>{
  const subject=loader(),controller=new AbortController();
  const pending=subject.layout([{id:'a'}],controller.signal),worker=FakeWorker.instances[0];
  controller.abort();const result=await outcome(pending);
  assert.equal(result.error?.name,'AbortError');
  assert.equal(worker.listenerCount(),0);
  assert.equal(worker.terminated,true);
  assert.equal(subject.worker,null);
});

test('worker layout ignores an unrelated response and releases listeners after success',async()=>{
  const subject=loader(),controller=new AbortController();
  const pending=subject.layout([{id:'a'}],controller.signal),worker=FakeWorker.instances[0];
  worker.emit('message',{data:{id:999,positions:new Float32Array([9,9])}});
  worker.emit('message',{data:{id:worker.sent.id,positions:new Float32Array([.1,.2])}});
  const result=await outcome(pending);
  assert.equal(result.value[0],new Float32Array([.1])[0]);
  assert.equal(worker.listenerCount(),0);
  controller.abort();assert.equal(worker.terminated,false);
});

test('worker batches return the same circular positions as the main-thread fallback',()=>{
  const web=path.join(__dirname,'../../knowledge_service/web');
  let response;
  const context=vm.createContext({postMessage:(message,transfer)=>{response={message,transfer};}});
  context.self=context;
  context.importScripts=url=>vm.runInContext(fs.readFileSync(path.join(web,url.split('?')[0]),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(web,'full-graph-worker.js'),'utf8'),context);
  const ids=Array.from({length:128},(_,index)=>'实体-'+index);
  context.onmessage({data:{id:7,ids}});
  assert.equal(response.message.id,7);
  assert.equal(response.transfer[0],response.message.positions.buffer);
  const {GraphDataset}=vm.runInNewContext(fs.readFileSync(path.join(web,'full-graph.js'),'utf8')+'\nFullGraph');
  const data=new GraphDataset();
  data.addNodes(ids.map(id=>({id})));
  assert.deepEqual(Array.from(response.message.positions),Array.from(data.positions));
  for(let i=0;i<response.message.positions.length;i+=2)assert.ok(Math.hypot(response.message.positions[i],response.message.positions[i+1])<=.951);
});
