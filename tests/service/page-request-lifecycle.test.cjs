const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {fixture}=require('./fixtures/ledger-ui.cjs');
const web=name=>fs.readFileSync(path.join(__dirname,'../../knowledge_service/web',name),'utf8');
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

test('project cards stay idle at startup and still load on project page entry',async()=>{
  const env=fixture(),{context,get}=env;
  get('tab-projects').className='hidden';context.window.Auth={whenReady:()=>Promise.resolve()};
  context.api=async url=>{env.calls.push({url});return {projects:[]};};
  vm.runInContext(web('projects-view.js'),context);await settle();assert.equal(env.calls.length,0);
  get('tab-projects').className='';await context.window.ProjectsView.refresh();
  assert.equal(env.calls.length,1);assert.match(get('projects-root').innerHTML,/项目管理/);
});

test('explicit project management refresh waits for authentication without an eager startup read',async()=>{
  const env=fixture(),{context}=env,auth=deferred();
  context.window.Auth={whenReady:()=>auth.promise};
  context.api=async url=>{env.calls.push({url});return {projects:[]};};
  vm.runInContext(web('projects-view.js'),context);
  const loading=context.window.ProjectsView.refresh();await settle();
  assert.equal(env.calls.length,0);
  auth.resolve();await loading;assert.equal(env.calls.length,1);
});

for(const id of ['apply-scope','reset-scope'])test('ledger '+id+' loads once through its scope event',async()=>{
  const env=fixture(),{get,events,calls}=env;
  get(id).onclick=()=>{for(const handler of events['workspace:scope-changed'])handler({detail:{page:'records'}});};
  await get(id).click();await settle();
  assert.equal(calls.filter(call=>call.url.endsWith('/records/page')&&call.body.limit===50).length,1);
});

test('project settings select the target runtime section before activating its loaders',async()=>{
  const source=web('projects-view.js'),nodes=new Map(),loads=[];let active='overview';
  const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',focus(){},select(){},dispatchEvent(){}});return nodes.get(id);};
  const runtime={show(view,options={}){active=view;if(options.load!==false)loads.push(view);}};
  const context=vm.createContext({projectsCache:[{id:'p',name:'Project'}],projects:async()=>{},$:get,Event:class{},
    openModal:()=>({overlay:{querySelector:selector=>get(selector)},close(){}}),openDelete(){},esc:value=>value,
    window:{RuntimeView:runtime,KnowledgeTabs:{open(){runtime.show(active);}}}});
  vm.runInContext(source.slice(source.indexOf('  function openRename('),source.indexOf('  function openDelete(')),context);
  context.openRename('p');await get('#project-backup').onclick();assert.deepEqual(loads,['snapshots']);
});

function batches({realRuntime=false}={}){
  const env=fixture(),{context,get,events}=env,calls=[],timers=new Map(),library={active:false,mode:'upload'};
  const prototype=Object.getPrototypeOf(get('project'));
  prototype.prepend=function(...children){this.children.unshift(...children);};
  prototype.remove=function(){this.removed=true;};
  context.document.getElementById=get;
  get('project').value='p';get('tab-ingest').className='hidden';get('tab-runtime').className='hidden';get('tab-jobs').className='hidden';
  context.window.LibraryWorkspace={snapshot:()=>library};
  context.window.RuntimeView={current:'overview',on(name,fn){this[name]=fn;}};
  if(realRuntime){
    for(const id of ['body','tab-runtime','tab-dashboard','tab-jobs','tab-evaluation']){
      const node=get(id);node.classList.toggle=(name,on)=>{
        const values=new Set(node.className.split(' ').filter(Boolean));if(on)values.add(name);else values.delete(name);node.className=[...values].join(' ');
      };
    }
    context.document.body=get('body');const originalQuery=context.document.querySelector;
    context.document.querySelector=selector=>selector==='[data-tab="runtime"]'?get('runtime-nav'):originalQuery(selector);
    vm.runInContext(web('runtime-view.js'),context);
  }
  context.window.DocumentUploadQueue={generation:0,setCapabilities(){},setStatus(){}};
  let next=0;context.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;};context.clearTimeout=id=>timers.delete(id);
  context.AbortController=AbortController;
  const batch={id:'b',status:'running',total_items:1,requested_concurrency:'auto',effective_concurrency:1,counts:{running:1}};
  let respond=async url=>url==='/api/ingest/capabilities'?{concurrency_choices:['auto',1]}:
    url.endsWith('?limit=20')?{items:[batch]}:url.includes('/items?')?{items:[],next_cursor:null}:batch;
  context.fetch=async(url,options)=>{calls.push({url,options});const data=await respond(url,options);return {ok:true,json:async()=>data};};
  vm.runInContext(web('ingest-batches.js'),context);
  const api=context.window.IngestBatches;
  context.window.LibraryWorkspace.open=()=>{drawer();void api.loadRecent();};
  const visibility=()=>api.visibilityChanged?.();
  function drawer(active=true,mode='batches'){library.active=active;library.mode=mode;get('tab-ingest').className=active?'':'hidden';visibility();}
  function jobs(active=true){get('tab-runtime').className=active?'':'hidden';get('tab-jobs').className=active?'':'hidden';context.window.RuntimeView.current=active?'jobs':'overview';visibility();}
  function change(project){get('project').value=project;for(const handler of get('project').events.change||[])handler();}
  function page(name){for(const handler of events['workspace:page-changed']||[])handler({detail:{page:name}});}
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const details=()=>descendants(get('ingest-batches')).filter(node=>node.dataset.batchId==='b');
  return {...env,api,calls,timers,batch,drawer,jobs,change,page,details,descendants,respond(fn){respond=fn;}};
}

test('hidden batch module readiness, refresh and project changes issue no reads',async()=>{
  const env=batches();await env.api.ready;await env.api.loadRecent();env.change('q');await settle();
  assert.equal(env.calls.length,0);assert.equal(env.timers.size,0);
});

test('real runtime section changes invalidate a prior jobs visit before reopening',async()=>{
  const env=batches({realRuntime:true}),old=deferred(),fresh=deferred();let reads=0;
  env.respond(()=>++reads===1?old.promise:fresh.promise);
  env.context.window.RuntimeView.show('jobs');
  env.context.window.RuntimeView.show('overview');const aborted=env.calls[0].options.signal.aborted;
  env.context.window.RuntimeView.show('jobs');const reopenedReads=env.calls.length;
  fresh.resolve({items:[{...env.batch,status:'completed'}]});await settle();
  old.resolve({items:[env.batch]});await settle();
  assert.equal(aborted,true,'leaving the jobs section must abort its pending list');
  assert.equal(reopenedReads,2,'reopening jobs must read a fresh list');
  const labels=env.descendants(env.get('ingest-batch-tasks')).map(node=>node.textContent).join(' ');
  assert.match(labels,/完成/);assert.doesNotMatch(labels,/处理中/);
});

for(const surface of ['jobs','drawer'])test(surface+' batch summaries do not fan out to details or items',async()=>{
  const env=batches();env[surface]();await env.api.loadRecent();await settle();
  assert.deepEqual(env.calls.map(call=>call.url),['/api/projects/p/ingest-batches?limit=20']);
  assert.equal(env.timers.size,0);assert.equal(env.details().length,0);
  const host=env.get(surface==='jobs'?'ingest-batch-tasks':'ingest-batches');
  assert.ok(env.descendants(host).some(node=>node.textContent==='查看批次详情'));
});

test('concurrent visible batch list reads share one request and failed reads can retry',async()=>{
  const env=batches(),gate=deferred();env.drawer();env.respond(()=>gate.promise);
  const first=env.api.loadRecent(),second=env.api.loadRecent();assert.equal(env.calls.length,1);
  gate.resolve({items:[]});await Promise.all([first,second]);
  env.respond(async()=>{throw Error('temporary');});await env.api.loadRecent();
  env.respond(async()=>({items:[]}));await env.api.loadRecent();assert.equal(env.calls.length,3);
});

test('selecting a summary in an open drawer reads details without reloading the list',async()=>{
  const env=batches();env.drawer();await env.api.loadRecent();
  const view=env.descendants(env.get('ingest-batches')).find(node=>node.textContent==='查看批次详情');
  await view.onclick();await settle();
  assert.equal(env.calls.filter(call=>call.url.endsWith('?limit=20')).length,1);
  assert.equal(env.calls.filter(call=>call.url.includes('/items?')).length,1);
});

test('only explicit visible detail loading starts its current items page and polling',async()=>{
  const env=batches();env.drawer();const detail=env.api.monitor('p',env.batch);await detail.refresh();await settle();
  assert.equal(env.calls.filter(call=>call.url.endsWith('/b')).length,1);
  assert.equal(env.calls.filter(call=>call.url.includes('/items?')).length,1);assert.equal(env.timers.size,1);
  env.drawer(false);assert.equal(env.timers.size,0);
  env.drawer();await settle();assert.equal(env.calls.length,4);assert.equal(env.timers.size,1);
});

for(const state of ['paused','needs_attention','interrupted','completed','completed_with_errors','cancelled'])test(state+' detail stops automatic polling',async()=>{
  const env=batches();env.batch.status=state;env.drawer();await env.api.monitor('p',env.batch).refresh();await settle();
  assert.equal(env.calls.length,2);assert.equal(env.timers.size,0);
});

for(const boundary of ['close','leave','project'])test(boundary+' invalidates an in-flight detail before its items follow-on',async()=>{
  const env=batches(),old=deferred();env.drawer();env.respond(()=>old.promise);
  const loading=env.api.monitor('p',env.batch).refresh();assert.equal(env.calls.length,1);
  if(boundary==='project'){env.change('q');env.change('p');}
  else if(boundary==='close')env.drawer(false);
  else{env.get('tab-ingest').className='hidden';env.page('records');}
  assert.equal(env.calls[0].options.signal?.aborted,true);
  old.resolve({...env.batch,status:'completed'});await loading;await settle();
  assert.equal(env.calls.filter(call=>!call.url.endsWith('?limit=20')).length,1);assert.equal(env.timers.size,0);
  assert.equal(env.details()[0]?.dataset.status,undefined);
});

test('a list response from a closed and reopened drawer cannot populate the new visit',async()=>{
  const env=batches(),old=deferred();env.drawer();env.respond(()=>old.promise);
  const before=env.api.loadRecent();env.drawer(false);env.drawer();
  env.respond(async()=>({items:[]}));await env.api.loadRecent();
  old.resolve({items:[env.batch]});await before;
  assert.equal(env.calls[0].options.signal?.aborted,true);
  assert.equal(env.descendants(env.get('ingest-batches')).some(node=>node.textContent==='查看批次详情'),false);
});

test('reopened detail accepts the new read and rejects the previous visit response',async()=>{
  const env=batches(),old=deferred(),latest=deferred();env.drawer();env.respond(()=>old.promise);
  const first=env.api.monitor('p',env.batch).refresh();env.drawer(false);
  env.respond(url=>url.includes('/items?')?Promise.resolve({items:[]}):latest.promise);env.drawer();
  latest.resolve({...env.batch,status:'paused'});await settle();
  old.resolve({...env.batch,status:'running'});await first;await settle();
  assert.equal(env.calls.filter(call=>call.url.includes('/items?')).length,1);
  assert.equal(env.details()[0].dataset.status,'paused');assert.equal(env.timers.size,0);
});

test('upload submission obtains capabilities on use and starts server work without hidden monitoring',async()=>{
  const env=batches(),parts=[];
  env.context.FormData=class{append(...args){parts.push(args);}};
  env.context.crypto={randomUUID:()=> 'upload-key'};
  env.respond(async url=>url==='/api/ingest/capabilities'?{concurrency_choices:['auto']}:
    url.endsWith('/files')?{id:'item'}:{...env.batch,settings:{extraction_mode:'documents'}});
  await env.api.submit('p',[{file:{name:'file.txt'},title:'File',metadata:{}}],{extraction_mode:'documents'});
  assert.deepEqual(env.calls.map(call=>call.url),['/api/ingest/capabilities','/api/projects/p/ingest-batches','/api/projects/p/ingest-batches/b/files','/api/projects/p/ingest-batches/b/start']);
  assert.equal(parts.length,2);assert.equal(env.timers.size,0);
});
