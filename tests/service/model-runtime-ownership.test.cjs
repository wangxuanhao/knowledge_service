const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=name=>fs.readFileSync(path.join(__dirname,'../../knowledge_service/web',name),'utf8');
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});return {promise,resolve,reject};};
function element(tag='div'){
  const node={tagName:tag,children:[],listeners:{},dataset:{},value:'',className:'',innerHTML:'',textContent:'',hidden:false,
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},
    insertBefore(node){this.children.unshift(node);},setAttribute(name,value){this[name]=value;},
    addEventListener(name,handler){(this.listeners[name]??=[]).push(handler);},
    click(){this.onclick?.();for(const handler of this.listeners.click||[])handler();},
    querySelectorAll(){return [];},querySelector(){return null;},closest(){return {classList:{toggle(){}}};}};
  node.classList={contains:name=>node.className.split(' ').includes(name),
    add:name=>{if(!node.classList.contains(name))node.className+=' '+name;},
    remove:name=>{node.className=node.className.split(' ').filter(value=>value!==name).join(' ');},
    toggle(name,on){node.classList[on?'add':'remove'](name);}};
  return node;
}
const descendants=node=>[node,...node.children.filter(child=>typeof child==='object').flatMap(descendants)];

function modelFixture(read){
  const calls=[],events={},project=element(),root=element();root.className='hidden';
  const context={window:{},current:'p',console,status(){},
    endpoint:suffix=>'/'+context.current+suffix,
    api:async(url,body,method='GET')=>{calls.push(url);return read?.(url,body,method)??
      (url.endsWith('/ontology')?{id:'release',summary:{classes:[],relations:[],attributes:[]}}:
        url.endsWith('/ontology-drafts')?{items:[{id:'selected',status:'pending',revision:1}]}:
        url.includes('/ontology-drafts/selected?')?{id:'selected',status:'pending',revision:1,ontology:{classes:[]}}:{});},
    document:{getElementById:id=>id==='project'?project:null,querySelector:()=>null,
      addEventListener(name,handler){events[name]=handler;}}};
  vm.runInNewContext(source('ontology-model.js'),context);
  return {context,calls,project,root,events,model:context.window.OntologyModel};
}

test('model refresh reads the draft directory exactly once even with a selected draft',async()=>{
  const f=modelFixture();f.model.S.draft={id:'selected',status:'pending',revision:1};
  await f.model.refresh();
  assert.equal(f.calls.filter(url=>url.endsWith('/ontology-drafts')).length,1);
  assert.equal(f.model.S.draftChoices[0].id,'selected');
});

test('a superseded directory failure cannot replace a newer refresh and explicit refresh retries',async()=>{
  const old=deferred();let reads=0;
  const f=modelFixture(url=>{if(url.endsWith('/ontology-drafts'))return ++reads===1?old.promise:{items:[]};});
  const first=f.model.refresh();await settle();const second=f.model.refresh();await second;
  old.reject(Error('obsolete failure'));await first;
  assert.equal(f.model.S.draftLoadError,null);
  await f.model.refresh();assert.equal(reads,3);
});

test('hidden project changes clear model state without mounting the catalog',()=>{
  const f=modelFixture();f.events.DOMContentLoaded();let renders=0;
  f.context.document.getElementById=id=>id==='project'?f.project:id==='tab-ontology-model'?f.root:null;
  f.context.window.OntologyCatalog={mount(){renders++;},render(){renders++;}};
  for(const handler of f.project.listeners.change)handler();
  assert.equal(renders,0);assert.equal(f.root.innerHTML,'');assert.equal(f.calls.length,0);
});

test('catalog render consumes model choices and errors; explicit reload belongs to the model',async()=>{
  const side=element(),cards=element(),work=element(),calls=[];let refreshes=0;
  const context={window:{},current:'p',document:{createElement:element,getElementById:id=>id==='om-work-changes-host'?work:null},
    api:async url=>{calls.push(url);return {items:[]};},endpoint:value=>value};
  vm.runInNewContext(source('ontology-model-catalog.js'),context);
  const state={view:'types',summary:{classes:[]},draftChoices:[{id:'draft',title:'My draft',status:'pending',revision:2}],draftLoadError:'重试草案列表'};
  const controller={S:state,refresh:async()=>{refreshes++;},status(){}};
  context.window.OntologyCatalog.mount(side,cards,controller);context.window.OntologyCatalog.render(state);await settle();
  assert.deepEqual(calls,[]);
  const picker=descendants(work).find(node=>node.className==='om-catalog-drafts');
  assert.equal(picker.children[1].value,'draft');
  assert.equal(descendants(work).find(node=>node.className==='om-draft-error').textContent,state.draftLoadError);
  descendants(work).find(node=>node.textContent==='重读草案列表').click();await settle();assert.equal(refreshes,1);
});

function runtimeFixture(){
  const tab=element(),page=element(),loads=[];page.querySelectorAll=()=>[];
  const nodes=new Map([['tab-runtime',page],['title',element()]]);
  for(const id of ['tab-dashboard','tab-jobs','tab-evaluation'])nodes.set(id,element());
  const context={window:{},document:{body:element(),querySelector:()=>tab,getElementById:id=>nodes.get(id)},status(){}};
  vm.createContext(context);vm.runInContext(source('runtime-view.js'),context);
  context.window.RuntimeView.on('overview',()=>loads.push('overview'));context.window.RuntimeView.on('jobs',()=>loads.push('jobs'));
  context.document.querySelector=()=>tab;
  const workbench=source('workbench.js');vm.runInContext(workbench.slice(workbench.indexOf('function showTab('),workbench.indexOf('function controlsProject(')),context);
  return {context,loads};
}
test('runtime navigation selects the target before tab activation loads it once',()=>{
  const f=runtimeFixture();f.context.showRuntimeView('jobs');assert.deepEqual(f.loads,['jobs']);
});
test('the upload task progress link uses the single runtime loader',()=>{
  const f=runtimeFixture(),workbench=source('workbench.js');f.context.link=element();f.context.jobList=()=>{f.loads.push('extra');return Promise.resolve();};
  const start=workbench.indexOf('link.onclick=()=>{showRuntimeView(');
  vm.runInContext(workbench.slice(start,workbench.indexOf('\n',start)),f.context);f.context.link.click();
  assert.deepEqual(f.loads,['jobs']);
});

function jobsFixture({visible=false,watching=false}={}){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  get('tab-jobs').className=visible?'':'hidden';get('tab-runtime').className=visible?'':'hidden';
  const calls=[],context={window:{RuntimeView:{on(){}}},current:'p',wb:{jobs:new Map(watching?[['watched',{}]]:[])},$:get,
    bind(){},esc:String,status(){},showTab(){},document:{createElement:element,querySelector:()=>element()},
    api:async url=>{calls.push(url);return {jobs:[{id:url==='/api/jobs'?'watched':'scoped',status:'running',logs:[]}]};}};
  vm.createContext(context);vm.runInContext('let jobList;',context);vm.runInContext(source('task-review.js'),context);
  return {context,calls,get,run:()=>vm.runInContext('jobList()',context)};
}
for(const visible of [false,true])for(const watching of [false,true])test(`jobs reads only its consumers: visible=${visible}, watcher=${watching}`,async()=>{
  const f=jobsFixture({visible,watching}),jobs=await f.run();
  assert.equal(f.calls.includes('/api/jobs'),watching);
  assert.equal(f.calls.includes('/api/projects/p/jobs'),visible);
  if(watching)assert.equal(jobs[0].id,'watched','global watcher completion semantics must survive project filtering');
});

function hintFixture(){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  for(const id of ['tab-search','tab-graph','tab-mindmap'])get(id).className='hidden';
  get('project').selectedOptions=[{dataset:{ontologyMode:'discovery'}}];
  for(const id of ['graph-canvas','mindmap-canvas'])get(id).parentNode={insertBefore(node){nodes.set(node.id,node);}};
  const calls=[],gates=[],context={current:'p',$:id=>id.endsWith('-discovery-hint')?nodes.get(id)||null:get(id),window:{},wb:{epoch:0},endpoint:suffix=>'/'+context.current+suffix,
    document:{getElementById:get,createElement(){const node=element();node.querySelector=()=>element();return node;}},
    api:url=>{calls.push(url);const gate=deferred();gates.push(gate);return gate.promise;}};
  vm.createContext(context);const code=source('workbench.js');
  vm.runInContext(code.slice(code.indexOf('function discoveryHintHost('),code.indexOf("for(const tab of ['graph'")),context);
  return {context,get,calls,gates};
}
test('discovery hints skip hidden pages and use the compact summary on the visible graph',async()=>{
  const f=hintFixture(),hidden=f.context.renderDiscoveryHint(),hiddenReads=f.calls.length;
  f.gates.forEach(gate=>gate.resolve({}));await hidden;assert.equal(hiddenReads,0);
  f.get('tab-search').className='';const loading=f.context.renderDiscoveryHint();
  assert.equal(f.calls[0],'/p/ontology-discovery');f.gates[0].resolve({unpublished_candidate_count:2});await loading;
  assert.match(f.get('graph-canvas-discovery-hint').innerHTML,/<b>2<\/b>/);
});
test('discovery hints reject old success and failure responses for the same project',async()=>{
  for(const fails of [false,true]){
    const f=hintFixture();f.get('tab-search').className='';const old=f.context.renderDiscoveryHint(),fresh=f.context.renderDiscoveryHint();
    f.gates[1].resolve({unpublished_candidate_count:2});await fresh;
    if(fails)f.gates[0].reject(Error('old'));else f.gates[0].resolve({unpublished_candidate_count:7});await old;
    assert.equal(f.get('graph-canvas-discovery-hint').hidden,false);assert.match(f.get('graph-canvas-discovery-hint').innerHTML,/<b>2<\/b>/);
  }
});

function vocabularyFixture(){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  get('project').value='p';get('tab-ingest').className='hidden';
  const calls=[],gates=[],context={window:{},document:{getElementById:get,querySelectorAll:()=>[],querySelector:()=>null,createElement:element},
    api:url=>{calls.push(url);const gate=deferred();gates.push(gate);return gate.promise;},endpoint:suffix=>'/'+get('project').value+suffix};
  vm.runInNewContext(source('ingest-mode.js'),context);return {context,get,calls,gates};
}
test('upload vocabulary waits for upload entry and ignores a superseded project read',async()=>{
  const f=vocabularyFixture();for(const handler of f.get('project').listeners.change)handler();assert.equal(f.calls.length,0);
  f.get('tab-ingest').className='';const old=f.context.window.loadApplicabilityVocabulary();f.get('project').value='q';
  const fresh=f.context.window.loadApplicabilityVocabulary();f.gates[1].resolve({platforms:['new']});await fresh;
  f.gates[0].resolve({platforms:['old']});await old;
  assert.equal(f.get('doc-platform-options').children[0].value,'new');
});

test('batch and structured library views do not consume upload vocabulary',async()=>{
  for(const mode of ['batches','structured']){
    const f=vocabularyFixture();f.get('tab-ingest').className='';
    f.context.window.LibraryWorkspace={snapshot:()=>({active:true,mode})};
    const reading=f.context.window.loadApplicabilityVocabulary(),count=f.calls.length;
    f.gates.forEach(gate=>gate.resolve({}));await reading;assert.equal(count,0,mode);
  }
});
