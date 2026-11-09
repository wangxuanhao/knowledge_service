const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function node(tag='div'){
  return {tag,children:[],dataset:{},textContent:'',innerHTML:'',disabled:false,open:false,style:{},
    classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(){},after(){},
    append(...items){this.children.push(...items);},appendChild(item){this.children.push(item);},
    replaceChildren(...items){this.children=items;},insertAdjacentHTML(){},showModal(){this.open=true;},close(){this.open=false;}};
}

function inspector({defer=false}={}){
  const elements=new Map(),selectors=new Map(),events=new Map(),calls=[];
  const host=node();elements.set('graph-detail',host);
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  host.querySelector=selector=>{if(!selectors.has(selector))selectors.set(selector,node());return selectors.get(selector);};
  host.querySelectorAll=selector=>selector.includes('data-action="edit"')?
    ['edit','add-relation','add-attribute'].map(action=>host.querySelector('[data-action="'+action+'"]')).concat(host.querySelector('.evidence-danger').children):[];
  let live={known_at:null,filters:{field:'metadata.city',op:'eq',value:'当前'}},revision=1,finish;
  const context={window:{TemporalScope:require('../../knowledge_service/web/temporal-scope.js'),Auth:{isAdmin:()=>true},
      WorkspaceContext:{ticket:()=>({revision}),accepts:ticket=>ticket.revision===revision}},
    document:{createElement:tag=>node(tag),body:node(),addEventListener:(name,fn)=>events.set(name,fn)},
    $:get,current:'p',scope:()=>live,endpoint:suffix=>'/api/projects/p'+suffix,
    esc:String,term:String,labelOf:String,status(){},editRecord(){},historyFor(){},
    RecordVersionContext:{revision:()=>1,build:()=>({}),summary:()=>''},
    api:async(url,body)=>{calls.push({url,body});if(url.endsWith('/ontologies'))return {versions:[]};
      if(defer)return new Promise(resolve=>finish=resolve);return {documents:[],message:'冻结来源'};},console};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/evidence-inspector.js'),'utf8'),context);
  const row={id:'entity',kind:'entity',type:'Thing',text:'历史实体',version:1,version_id:'entity-v1'};
  return {context,row,calls,host,get,selectors,events,render:scope=>context.window.renderEvidenceInspector(row,[],{scope}),
    changeOwner(value=live){live=value;revision++;},finish:value=>finish(value)};
}

test('inspector posts the frozen record scope and disables historical write actions',async()=>{
  const h=inspector(),fixed={known_at:'2025-01-01T00:00:00Z',ontology_scope:'ids',ontology_ids:['old'],applicability:{platforms:{mode:'specific',values:['历史平台']}}};
  await h.render(fixed);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.find(call=>call.url.endsWith('/evidence')).body)),fixed);
  for(const action of ['edit','add-relation','add-attribute'])assert.equal(h.host.querySelector('[data-action="'+action+'"]').disabled,true);
  assert.equal(h.host.querySelector('.evidence-danger').children[0].disabled,true);
});

test('frozen evidence remains invalidated after the live applied scope changes A B A',async()=>{
  const h=inspector({defer:true}),pending=h.render({known_at:'2025-01-01T00:00:00Z'});
  h.changeOwner();h.finish({documents:[],message:'过时来源'});await pending;
  assert.equal(h.get('inspector-source-list').innerHTML,'');
});

test('historical inspector write controls are read only even with current live controls',async()=>{
  const h=inspector();await h.render({known_at:'2025-01-01T00:00:00Z'});
  assert.equal(h.host.querySelector('[data-action="edit"]').disabled,true);
  assert.equal(h.host.querySelector('.evidence-danger').children[0].disabled,true);
});

test('display-only type and predicate filters never leak into evidence Scope requests',async()=>{
  const h=inspector();await h.render({known_at:'2025-01-01T00:00:00Z',entity_type:'Thing',predicate:'knows'});
  const body=h.calls.find(call=>call.url.endsWith('/evidence')).body;
  assert.equal(body.known_at,'2025-01-01T00:00:00Z');assert.equal(Object.hasOwn(body,'entity_type'),false);assert.equal(Object.hasOwn(body,'predicate'),false);
});

test('source canonical navigation preserves the applied source scope before changing pages',async()=>{
  const body=node(),calls=[];let live={known_at:'2025-01-01T00:00:00Z',filters:{field:'metadata.city',op:'eq',value:'来源城市'}};
  const fixed=JSON.parse(JSON.stringify(live));
  const context={body,document:{createElement:tag=>node(tag)},scope:()=>live,
    window:{WorkspaceDrawers:{close(){}},KnowledgeTabs:{open(){live={known_at:null,filters:null};}},openKnowledgeRecord:async value=>calls.push(value)}};
  vm.createContext(context);const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/sources-view.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('  function renderAssertions('),source.indexOf('  function load({')),context);
  context.renderAssertions([{kind:'relation',status:'accepted',canonical_record_id:'exact-edge',payload:{text:'关系'}}],'p');
  await body.children[0].children.find(child=>child.tag==='button').onclick();
  assert.equal(calls[0].id,'exact-edge');assert.deepEqual(JSON.parse(JSON.stringify(calls[0].scope)),fixed);
});

function selection({defer=false}={}){
  const calls=[],inspected=[],elements=new Map();let revision=1,finish;
  const get=id=>{if(!elements.has(id))elements.set(id,{...node(),value:'',querySelector(){return null;}});return elements.get(id);};
  const scope={known_at:'2025-01-01T00:00:00Z',ontology_scope:'ids',ontology_ids:['old']};
  const rich={id:'entity',kind:'entity',version_id:'v1',text:'完整详情',class_parents:[{id:'Parent'}],attributes:[{label:'属性',values:[{value:'完整值'}]}]};
  const context={window:{WorkspaceContext:{ticket:()=>({revision}),accepts:ticket=>ticket.revision===revision},
      renderEvidenceInspector:(row,options,context)=>inspected.push({row,context})},
    AbortController,current:'p',recordOpenEpoch:0,recordReadAbort:null,detailSelectionEpoch:0,
    ui:{options:[],pinned:new Map()},wb:{nodes:new Map()},GraphTypeFilter:{isAttributeNode:()=>false},GraphWorkbench:{selection(){}},
    get,choices(){},graphScope:()=>scope,effectiveGraphScope:()=>scope,
    sameRecordVersion:(a,b)=>!b.version_id||a.version_id===b.version_id,
    invalidatePendingDetail(){context.recordReadAbort?.abort();context.recordOpenEpoch++;},
    beginPendingDetail:()=>1,settlePendingDetail(){},
    api:async(url,body)=>{calls.push({url,body});if(defer)return new Promise(resolve=>finish=resolve);return {records:[rich]};},console};
  vm.createContext(context);const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('  function inspect(row'),source.indexOf('  // 图谱悬停提示')),context);
  return {calls,inspected,rich,context,read:mode=>context.window.inspectKnowledgeRecord({id:'entity',kind:'entity',version_id:'v1'},{scope,qualify:mode}),changeOwner(){revision++;},finish:value=>finish(value)};
}

test('compact canvas entity inspection reads one bounded qualified detail without navigation',async()=>{
  const h=selection();await h.read('entity');
  assert.equal(h.calls.length,1);assert.ok(h.calls[0].url.endsWith('/graph-neighborhood'));
  assert.equal(h.calls[0].body.hops,0);assert.equal(h.calls[0].body.max_nodes,1);
  assert.equal(h.inspected[0].row.attributes[0].values[0].value,'完整值');
});

test('dropdown selection uses exact record inspection without a neighborhood redraw',async()=>{
  const h=selection();await h.read('record');
  assert.equal(h.calls.length,1);assert.ok(h.calls[0].url.endsWith('/records/page'));
  assert.equal(h.calls[0].body.record_ids[0],'entity');assert.equal(h.inspected.length,1);
});

test('already enriched entity inspection reuses its qualified detail',async()=>{
  const h=selection();await h.context.window.inspectKnowledgeRecord({...h.rich,class_ontology_id:'old'},{qualify:'entity'});
  assert.equal(h.calls.length,0);assert.equal(h.inspected[0].row.attributes[0].values[0].value,'完整值');
});

test('a stale qualified inspection cannot replace the inspector after owner revision changes',async()=>{
  const h=selection({defer:true}),pending=h.read('entity');
  assert.equal(h.calls.length,1,'inspection starts its qualified authority request');
  h.changeOwner();h.finish({records:[h.rich]});await pending;assert.equal(h.inspected.length,0);
});

function searchHits(){
  const calls=[],buttons=[],host={html:''};let live={known_at:null,filters:null,entity_type:'Person',predicate:'knows'};
  Object.defineProperty(host,'innerHTML',{set(value){this.html=value;buttons.length=0;
    for(const match of value.matchAll(/<button\b([^>]*data-graph-view[^>]*)>/g)){
      const button={dataset:{},closest:()=>({querySelectorAll:()=>buttons})};for(const attr of match[1].matchAll(/data-([\w-]+)="([^"]*)"/g))button.dataset[attr[1].replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=attr[2];buttons.push(button);
    }},get(){return this.html;}});
  host.querySelectorAll=selector=>selector==='[data-graph-view]'?buttons:[];
  const context={document:{activeElement:null},window:{openKnowledgeRecord:async value=>calls.push(value)},get:()=>host,scope:()=>live,graphScope:()=>live,
    ontologyScope:()=>({ontology_scope:'all',ontology_ids:null}),readGraphHops:()=>1,localActionLabel:()=> '展开1跳邻域',esc:String,labelOf:String,platformGroupLabel:()=>'',status(){},historyFor(){}};
  vm.createContext(context);const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('  function renderHits('),source.indexOf('  // 检索和问答共用')),context);
  return {calls,buttons,render:(result,scope)=>context.renderHits(result,scope),setLive:value=>live=value};
}

test('search navigation forwards hit revision and originating authority scope with current display restrictions',async()=>{
  const h=searchHits(),fixed={known_at:'2025-01-01T00:00:00Z',filters:{field:'metadata.city',op:'eq',value:'origin'},ontology_scope:'all',ontology_ids:null};
  h.render({hits:[{id:'hit',kind:'entity',text:'命中',version:1,version_id:'hit-v1'}]},fixed);
  h.setLive({known_at:null,filters:null,entity_type:'Person',predicate:'knows'});await h.buttons[0].onclick();
  assert.equal(h.calls[0].version_id,'hit-v1');assert.equal(h.calls[0].version,1);
  assert.equal(h.calls[0].view,'local');assert.equal(h.calls[0].hops,1);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].scope)),{...fixed,entity_type:'Person',predicate:'knows'});
});

test('search navigation preserves a supplied numeric revision when no version ID was returned',async()=>{
  const h=searchHits();h.render({hits:[{id:'hit',kind:'relation',text:'命中关系',version:3}]},{known_at:null});await h.buttons[0].onclick();
  assert.equal(h.calls[0].version,3);assert.equal(h.calls[0].version_id,undefined);
});

test('a visible full-projection record reuses the same authority scope despite its transient type chip',()=>{
  const row={id:'a',kind:'entity',version_id:'v1'},data={records:[row],nodeIndex:new Map([['a',0]]),edges:[]};
  const context={renderer:{displayDataset:data},loadedProject:'p',fixed:{known_at:null,ontology_scope:'all',entity_type:null,predicate:null}};
  vm.createContext(context);const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph-view.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('    function hasRecord('),source.indexOf('    async function focusRecord(')),context);
  assert.equal(context.hasRecord(row,{projectId:'p',scope:{known_at:null,ontology_scope:'all',entity_type:'TypeA',predicate:null}}),true);
});

test('an explicit record excluded by the active full projection never switches or rebuilds the canvas',async()=>{
  const row={id:'b',kind:'entity',type:'TypeB',text:'B',version_id:'v1'},draws=[];
  const view={mode:'full',filters:{entity_type:'TypeA',predicate:null},hasRecord:()=>false,switchMode(mode){this.mode=mode;}};
  const context={window:{FullGraphView:view,GraphOverview:{explicit(){}},WorkspaceContext:{ticket:()=>({}),accepts:()=>true},
      GraphWorkspaceLayout:{settled:async()=>{}},suppressNextAutoGraph(){},drawGraph:async()=>{draws.push('draw');return {nodes:[row],edges:[]};}},
    current:'p',recordReadAbort:null,graphReadAbort:null,recordOpenEpoch:0,localBranches:null,AbortController,
    wb:{nodes:new Map()},GraphTypeFilter:{isAttributeNode:()=>false,isAttributeEdge:()=>false},graphRequests:{invalidate(){}},
    graphScope:()=>({known_at:null,ontology_scope:'all',entity_type:null,predicate:null}),effectiveGraphScope:()=>({}),
    api:async()=>({records:[row]}),get:()=>({classList:{contains:()=>false}}),inspect(){context.recordOpenEpoch++;},sameRecordVersion:()=>true,GraphWorkbench:{focusRecord(){}}};
  vm.createContext(context);const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('  window.openKnowledgeRecord='),source.indexOf('  const sameRecordVersion=')),context);
  await assert.rejects(()=>context.window.openKnowledgeRecord({id:'b',kind:'entity'}),/筛选/);
  assert.equal(view.mode,'full');assert.equal(draws.length,0);
});
