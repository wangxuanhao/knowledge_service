const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const web=name=>fs.readFileSync(path.join(__dirname,'../../knowledge_service/web',name),'utf8');
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

function element(tag='div'){
  const node={tagName:tag,children:[],listeners:{},dataset:{},className:'',value:'',hidden:false,innerHTML:'',textContent:'',
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},
    insertBefore(item){this.children.unshift(item);},setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
    addEventListener(name,handler){(this.listeners[name]??=[]).push(handler);},
    querySelectorAll(){return [];},querySelector(){return null;}};
  node.classList={contains:name=>node.className.split(' ').includes(name),add:name=>{node.className+=' '+name;},
    remove:name=>{node.className=node.className.split(' ').filter(item=>item!==name).join(' ');},
    toggle(name,on){this[on?'add':'remove'](name);}};
  return node;
}

test('the initial HTML hides the shell and authentication starts before graph vendors',()=>{
  const html=web('index.html');
  assert.match(html,/<html[^>]*class="[^"]*auth-pending/);
  assert.match(html,/html\.auth-pending\s+body\s*\{[^}]*visibility\s*:\s*hidden/);
  assert.ok(html.indexOf('/assets/auth.js')<html.indexOf('/vendor/echarts.min.js'));
});

test('authentication probes before DOM ready and releases only after verified policy/session',async()=>{
  for(const disabled of [false,true]){
    const policy=deferred(),classes=new Set(['auth-pending']),requests=[],listeners={};
    const document={readyState:'loading',documentElement:{classList:{remove:name=>classes.delete(name)}},body:null,
      addEventListener(name,handler){(listeners[name]??=[]).push(handler);},getElementById:()=>null,querySelectorAll:()=>[]};
    const context={document,Headers,URL,console,localStorage:{getItem:()=>null,removeItem(){}},window:{console,
      location:{pathname:'/',search:'',href:'http://test/',origin:'http://test',replace(){}},
      fetch:async url=>{requests.push(url);await policy.promise;return new Response(JSON.stringify({auth_enabled:!disabled}));}}};
    vm.runInNewContext(web('auth.js'),context);
    assert.deepEqual(requests,['/api/auth/policy'],'vendor parsing must not postpone the policy request');
    let released=false;context.window.Auth.whenReady().then(()=>released=true);
    await settle();assert.equal(released,false);assert.ok(classes.has('auth-pending'));
    policy.resolve();await settle();assert.equal(released,false,'DOM and authorization must both be ready');
    document.body={classList:{add(){},toggle(){}}};document.readyState='interactive';
    for(const handler of listeners.DOMContentLoaded||[])handler();await settle();
    assert.equal(released,disabled);assert.equal(classes.has('auth-pending'),!disabled);
  }
});

test('hidden reclassification does no startup/project work and visible requests are shared per project',async()=>{
  const root=element(),project=element(),nav=element(),listeners={},calls=[],gate=deferred();root.className='hidden';
  const context={window:{},current:'old',console,setTimeout(){},document:{readyState:'complete',body:element(),createElement:element,
    getElementById:id=>id==='project'?project:id==='tab-ontology-model'?root:null,
    querySelector:()=>nav,addEventListener(name,handler){(listeners[name]??=[]).push(handler);}},
    endpoint:suffix=>'/api/projects/'+context.current+suffix,
    api:async url=>{calls.push(url);await gate.promise;return {stale_records:0,groups:[]};}};
  vm.runInNewContext(web('ontology-model-reclassify.js'),context);
  await context.window.OntologyReclassify.refresh();assert.equal(calls.length,0);
  for(const handler of project.listeners.change||[])handler();assert.equal(calls.length,0);
  root.className='';const first=context.window.OntologyReclassify.refresh(),second=context.window.OntologyReclassify.refresh();
  assert.equal(calls.length,1);
  context.current='new';for(const handler of project.listeners.change||[])handler();
  assert.equal(calls.length,2,'the next project must not be blocked by the old project request');
  gate.resolve();await Promise.all([first,second]);await settle();
});

test('simultaneous ingestion version refreshes share one read and a project change stays independent',async()=>{
  const {createController}=require('../../knowledge_service/web/ingest-ontology.js');
  const gate=deferred(),calls=[];
  const controller=createController({readVersions:async project=>{calls.push(project);await gate.promise;return {versions:[{id:project,version:1}]};},readDraftState:async()=>({open_draft_count:0})});
  const first=controller.refresh('old','ontology'),again=controller.refresh('old','ontology'),next=controller.refresh('new','ontology');
  assert.deepEqual(calls,['old','new']);gate.resolve();await Promise.all([first,again,next]);
  assert.equal(controller.state().projectId,'new');assert.equal(controller.state().release.id,'new');
});

test('the hidden ingest panel does not read versions on mount or window focus',async()=>{
  const page=element(),mode=element(),after={after(){}},calls=[],listeners={};page.className='hidden';mode.value='ontology';
  const project=element();project.value='p';
  const context={window:{addEventListener(name,handler){listeners[name]=handler;}},document:{createElement:element,
    getElementById:id=>id==='project'?project:id==='tab-ingest'?page:null,querySelector:()=>null,addEventListener(){}},
    api:async url=>{calls.push(url);return {versions:[],open_draft_count:0};}};
  vm.runInNewContext(web('ingest-ontology.js'),context);context.window.IngestOntology.mount(mode,after);
  await context.window.IngestOntology.refresh();await listeners.focus();assert.equal(calls.length,0);
  page.className='';await context.window.IngestOntology.refresh();assert.equal(calls.length,2);
});

for(const resolveBeforeReopen of [false,true])test('a hidden upload publication invalidates its earlier version read'+(resolveBeforeReopen?' before reopening':''),async()=>{
  const page=element(),mode=element(),project=element(),listeners={},versions=[];mode.value='ontology';project.value='p';
  const context={window:{addEventListener(){}},document:{createElement:element,
    getElementById:id=>id==='project'?project:id==='tab-ingest'?page:null,querySelector:()=>null,
    addEventListener(name,handler){listeners[name]=handler;}},api:url=>{
      if(url.endsWith('/ontologies')){const gate=deferred();versions.push(gate);return gate.promise;}
      return Promise.resolve({open_draft_count:0});
    }};
  vm.runInNewContext(web('ingest-ontology.js'),context);const ingest=context.window.IngestOntology;
  ingest.mount(mode,{after(){}});const before=ingest.refresh();assert.equal(versions.length,1);
  page.className='hidden';listeners['ontology-model:published']({detail:{projectId:'p',versionId:'new'}});
  assert.equal(versions.length,1,'publication must not start hidden upload reads');
  if(resolveBeforeReopen){versions[0].resolve({versions:[{id:'old',version:1}]});await before;assert.equal(ingest.state().release,null);}
  page.className='';const after=ingest.refresh();assert.equal(versions.length,2,'reopening must start a read after publication');
  const shared=ingest.refresh();assert.equal(versions.length,2,'postpublication reads still share one request');
  versions[1].resolve({versions:[{id:'new',version:2}]});await Promise.all([after,shared]);
  if(!resolveBeforeReopen){versions[0].resolve({versions:[{id:'old',version:1}]});await before;}
  assert.equal(ingest.state().release.id,'new');assert.equal(ingest.settings('p','ontology').ontology_id,'new');
});

test('a hidden ontology card view builds only sidebar entries',()=>{
  const side=element(),cards=element(),created=[];
  const context={current:'p',document:{createElement(tag){const node=element(tag);created.push(node);return node;}},
    api:async()=>({items:[]}),endpoint:suffix=>suffix,window:{}};
  vm.runInNewContext(web('ontology-model-catalog.js'),context);
  const controller={S:{view:'types',summary:{classes:[{id:'a'}],relations:[],attributes:[]}},status(){}};
  context.window.OntologyCatalog.mount(side,cards,controller);
  assert.equal(created.filter(node=>node.className.includes('om-type-card')).length,0);
  controller.S.view='cards';context.window.OntologyCatalog.render(controller.S);
  assert.equal(cards.children.length,1);
});

test('shared ontology terms reads do not duplicate requests or let an old project clear new labels',async()=>{
  const source=web('app.js'),end=source.indexOf('const recordName='),gate=deferred(),calls=[];
  const context={document:{getElementById:()=>null},api:async url=>{calls.push(url);if(url.includes('/old/')){await gate.promise;throw Error('old failure');}return {id:'release',summary:{classes:[{id:'type',label:'New label'}]}};},
    endpoint:suffix=>'/api/projects/'+vm.runInContext('current',context)+suffix};
  vm.createContext(context);vm.runInContext(source.slice(0,end),context);vm.runInContext("current='old'",context);
  const old=vm.runInContext('loadOntologyTerms()',context),again=vm.runInContext('loadOntologyTerms()',context);
  assert.equal(calls.length,1);vm.runInContext("current='new'",context);await vm.runInContext('loadOntologyTerms()',context);
  gate.resolve();await Promise.all([old,again]);assert.equal(vm.runInContext("labelOf('type')",context),'New label');
});

test('project selection clears stale graph state and completes current project loading',async()=>{
  const source=web('workspace.js'),nodes=new Map(),calls=[],gate=deferred();
  const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const project=get('project');project.value='new';project.selectedOptions=[{textContent:'New project'}];
  const context={get,current:'old',ontologyLoadGeneration:0,select:element(),
    ui:{request:2,options:['old'],mindmapPicker:{options:['old'],request:3},mindmapRootId:'old',ontology:{id:'old'}},
    cb:{facets:['old'],conditions:['old'],custom:true},window:{clearGraphWorkspace(){calls.push('clearGraph');}},
    sessionStorage:{setItem(key,value){calls.push([key,value]);}},
    choices(){calls.push('choices');},resetTimeline(){calls.push('resetTimeline');},renderCB(){calls.push('renderCB');},
    updateChip(){calls.push('updateChip');},status(message){throw Error(message);},
    loadOntologyTerms(){calls.push('loadOntology');return gate.promise;},
    options:async()=>calls.push('options'),discoverMetadata:async()=>calls.push('metadata'),
    autoLoadGraph(){calls.push('autoLoadGraph');}};
  project.onchange=()=>{calls.push('priorChange');context.current=project.value;};
  for(const id of ['filters','query','graph-node','type-scope','predicate-scope','graph-entity-filter'])get(id).value='old';
  vm.runInNewContext(source.slice(source.indexOf('  // Ignore out-of-order project responses'),
    source.indexOf('  // 打开「检索与交互图谱」且尚未渲染时')),context);
  assert.doesNotThrow(()=>project.onchange(),'project selection must reach loading without removed editor helpers');
  assert.equal(context.current,'new');assert.equal(project.title,'New project');
  assert.equal(context.ui.request,3);assert.equal(context.ui.mindmapPicker.request,4);
  assert.equal(context.ui.options.length,0);assert.equal(context.ui.mindmapPicker.options.length,0);
  assert.equal(context.ui.ontology,null);assert.equal(context.cb.facets.length,0);assert.equal(context.cb.conditions.length,0);
  for(const id of ['filters','query','graph-node','type-scope','predicate-scope','graph-entity-filter'])assert.equal(get(id).value,'');
  assert.deepEqual(calls,['priorChange','choices','resetTimeline','clearGraph','renderCB','updateChip',
    ['knowledge-project','new'],'loadOntology']);
  const ontology={id:'new-release'};gate.resolve(ontology);await settle();
  assert.equal(context.ui.ontology,ontology);assert.deepEqual(calls.slice(-3),['options','metadata','autoLoadGraph']);
});

test('returning to a project rejects its superseded ontology read',async()=>{
  const source=web('app.js'),gates=[];
  const context={document:{getElementById:()=>null},api:()=>{const gate=deferred();gates.push(gate);return gate.promise;},endpoint:suffix=>suffix};
  vm.createContext(context);vm.runInContext(source.slice(0,source.indexOf('const recordName=')),context);
  vm.runInContext("current='a'",context);const old=vm.runInContext('loadOntologyTerms()',context);
  vm.runInContext("current='b'",context);const middle=vm.runInContext('loadOntologyTerms()',context);
  vm.runInContext("current='a'",context);const latest=vm.runInContext('loadOntologyTerms()',context);
  gates[2].resolve({summary:{classes:[{id:'type',label:'Latest'}]}});await latest;
  gates[0].resolve({summary:{classes:[{id:'type',label:'Obsolete'}]}});gates[1].resolve({summary:{}});await Promise.all([old,middle]);
  assert.equal(vm.runInContext("labelOf('type')",context),'Latest');
});

test('a publication refresh starts a new ontology read and rejects the earlier response',async()=>{
  const source=web('app.js'),workspace=web('workspace.js'),gates=[],listeners={};
  const context={document:{getElementById:()=>null,addEventListener(name,handler){listeners[name]=handler;}},
    api:()=>{const gate=deferred();gates.push(gate);return gate.promise;},endpoint:suffix=>suffix,
    window:{clearGraphWorkspace(){},drawGraph:async()=>{}},ui:{request:0,options:[],ontology:null},ontologyLoadGeneration:0,
    options:async()=>{},discoverMetadata:async()=>{},resetTimeline(){},status(){},get:()=>({classList:{contains:()=>true}})};
  vm.createContext(context);vm.runInContext(source.slice(0,source.indexOf('const recordName=')),context);
  vm.runInContext("current='p'",context);
  vm.runInContext(workspace.slice(workspace.indexOf("  document.addEventListener('ontology-model:published'"),workspace.indexOf('  // Dashboard loads on entry')),context);
  const before=vm.runInContext('loadOntologyTerms()',context);
  const publishing=listeners['ontology-model:published']({detail:{projectId:'p',versionId:'new'}});
  assert.equal(gates.length,2,'publication must not reuse a read started before the write');
  const shared=vm.runInContext('loadOntologyTerms()',context);assert.equal(gates.length,2,'reads begun after publication still share the fresh request');
  gates[1].resolve({id:'new',summary:{classes:[{id:'type',label:'New label'}]}});await Promise.all([publishing,shared]);
  gates[0].resolve({id:'old',summary:{classes:[{id:'type',label:'Old label'}]}});await before;
  assert.equal(context.ui.ontology.id,'new');assert.equal(vm.runInContext("labelOf('type')",context),'New label');
});

for(const trigger of ['direct','event'])test('reclassification '+trigger+' publication invalidates an older plan request',async()=>{
  const root=element(),project=element(),listeners={},gates=[];
  const context={window:{},current:'p',console,setTimeout(){},document:{readyState:'complete',body:element(),createElement:element,
    getElementById:id=>id==='project'?project:id==='tab-ontology-model'?root:null,querySelector:()=>element(),
    addEventListener(name,handler){listeners[name]=handler;}},endpoint:suffix=>suffix,
    api:()=>{const gate=deferred();gates.push(gate);return gate.promise;}};
  vm.runInNewContext(web('ontology-model-reclassify.js'),context);
  const controller=context.window.OntologyReclassify,before=controller.refresh();assert.equal(gates.length,1);
  const publishing=trigger==='direct'?controller.afterPublish():listeners['ontology-model:published']({detail:{projectId:'p',versionId:'new'}});
  assert.equal(gates.length,2,'a published ontology needs a plan read after the write');
  const shared=controller.refresh();assert.equal(gates.length,2);
  gates[1].resolve({current_ontology_id:'new',stale_records:0,groups:[]});await Promise.all([publishing,shared]);
  gates[0].resolve({current_ontology_id:'old',stale_records:77,groups:[]});await before;
  assert.equal(root.children.find(node=>node.id==='om-rc-strip').hidden,true,'the obsolete plan must not return after the new one');
});

test('tab activation announces view changes including internal navigation without a button click',()=>{
  const source=web('app.js'),records=element(),sources=element(),events=[],nodes=new Map([['tab-records',records],['tab-sources',sources],['scope',element()],['title',element()]]);
  const context={window:{},$:id=>nodes.get(id),titles:{sources:'资料库'},
    CustomEvent:class{constructor(type,{detail}){this.type=type;this.detail=detail;}},
    document:{querySelectorAll:selector=>selector==='.tab'?[records,sources]:[],dispatchEvent:event=>events.push(event)}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function activateTab('),source.indexOf("document.querySelectorAll('[data-tab]').forEach(button=>button.onclick")),context);
  context.activateTab('sources');assert.equal(records.classList.contains('hidden'),true);assert.equal(sources.classList.contains('hidden'),false);
  assert.equal(events[0]?.type,'workspace:page-changed');assert.equal(events[0]?.detail.page,'sources');
});

test('ontology scope options reuse the loaded DTO and query entity choices only in the open graph settings',async()=>{
  const source=web('workspace.js'),calls=[],nodes=new Map();
  for(const id of ['tab-search','graph-entity-filter','type-scope','predicate-scope'])nodes.set(id,element());
  const context={current:'p',window:{WorkspaceContext:{read:()=>({}),ticket:()=>null}},get:id=>nodes.get(id),scope:()=>({}),
    graphSettingsDrawer:null,ui:{options:[],ontology:{summary:{classes:[{id:'Thing'}],relations:[{id:'knows'}]}}},
    loadEntityChoices:async()=>{calls.push('choices');return {records:[]};},api:async()=>{calls.push('ontology');return {};},
    endpoint:value=>value,esc:value=>value,term:value=>value,labelOf:value=>value};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  async function options('),source.indexOf('  function inspect(')),context);
  await context.options();assert.deepEqual(calls,[]);assert.match(nodes.get('type-scope').innerHTML,/Thing/);
  context.graphSettingsDrawer={dialog:{open:true}};await context.options();assert.deepEqual(calls,['choices']);
  nodes.get('tab-search').className='hidden';await context.options();assert.deepEqual(calls,['choices']);
});
