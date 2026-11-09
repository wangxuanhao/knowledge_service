const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createStore}=require('../../knowledge_service/web/workspace-context.js');
const GraphTypeFilter=require('../../knowledge_service/web/graph-types.js');
const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
function harness(reply){
  const store=createStore();store.edit('p','search',{known_at:'2025-01-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}});store.apply('p','search');
  const selected={id:'selected',kind:'entity',text:'之前选择',type:'Thing'};
  const elements=new Map(Object.entries({'graph-node':{value:'selected'},'graph-entity-choice':{value:'selected',options:[]},'mindmap-root':{value:'selected'},'graph-entity-filter':{value:''},'graph-choice-status':{textContent:''},'graph-choice-next':{disabled:false},'type-scope':{value:''},'predicate-scope':{value:''},'tab-search':{classList:{contains:()=>false}}}));
  const requests=[],inspected=[],focused=[];
  const context={window:{WorkspaceContext:{read:store.read,ticket:store.ticket,accepts:store.accepts}},current:'p',recordOpenEpoch:0,ontologyScope:()=>({ontology_scope:'all',ontology_ids:null}),ui:{options:[selected],pinned:new Map(),picker:{request:0,cursor:null,query:'',page:0}},
    GraphTypeFilter,graphRequests:GraphTypeFilter.createGraphRequestGate(),AbortController,recordReadAbort:null,graphReadAbort:null,localBranches:null,localBranchScope:'',localSelectedType:'',localScopeOverride:null,localScopeOwner:'',localScopeCanvas:null,
    GraphWorkbench:{focusRecord:row=>focused.push(row)},renderGraph:result=>{context.ui.graph=result;context.wb.nodes=new Map(result.nodes.map(row=>[row.id,row]));},renderDiscoveryHint:async()=>{},
    wb:{nodes:new Map()},status:()=>{},get:id=>elements.get(id),scope:()=>({known_at:'draft'}),endpoint:route=>'/api/projects/p'+route,choices:()=>{},kgTime:()=>()=>{},
    inspect:row=>inspected.push(row),api:async(url,body)=>{requests.push({url,body});return reply?await reply(url,body):{records:[{id:'deep-149999',kind:'entity',text:'深页实体'}],next_cursor:'next-page',total:150000};}};
  vm.createContext(context);
  const direct=source.slice(source.indexOf('  async function selectEntityDetail('),source.indexOf('  async function options('));
  vm.runInContext(source.slice(source.indexOf('  const graphScope='),source.indexOf('  window.GraphWorkspaceContext='))+direct+
    source.slice(source.indexOf('  window.drawGraph=async('),source.indexOf('  window.clearGraphWorkspace=')),context);
  return {context,store,elements,requests,inspected,focused};
}
test('search entity absent from loaded choices opens by a scoped exact ID lookup',async()=>{
  const row={id:'deep-149999',kind:'entity',text:'深页实体',version_id:'deep-v1',attributes:[],class_parents:[],class_ontology_id:null};
  const subject=harness(()=>({records:[row]}));
  const choice=subject.elements.get('graph-entity-choice');Object.defineProperty(choice,'value',{get:()=>'',set:()=>{}});
  await subject.context.window.selectEntityDetail('deep-149999');
  const request=subject.requests[0];
  assert.ok(request.url.endsWith('/records/page'));
  assert.equal(request.body.known_at,'2025-01-01');
  assert.deepEqual(JSON.parse(JSON.stringify(request.body.filters)),{field:'metadata.city',op:'eq',value:'杭州'});
  assert.deepEqual(Array.from(request.body.record_ids),['deep-149999']);
  assert.equal(request.body.limit,1);assert.equal(request.body.include_total,false);
  assert.equal(Object.hasOwn(request.body,'query'),false,'exact navigation must not load a picker prefix');
  assert.equal(subject.requests.length,2);const graph=subject.requests[1];assert.ok(graph.url.endsWith('/graph-neighborhood'));
  assert.deepEqual(Array.from(graph.body.seeds),[row.id]);assert.equal(graph.body.hops,0);assert.equal(graph.body.max_nodes,1);
  assert.equal(graph.body.known_at,request.body.known_at);assert.deepEqual(JSON.parse(JSON.stringify(graph.body.filters)),JSON.parse(JSON.stringify(request.body.filters)));
  assert.equal(subject.inspected[0].id,'deep-149999');
  assert.equal(subject.focused[0].id,row.id);
});

test('an already rendered entity qualifies its version and focuses without rebuilding or loading picker choices',async()=>{
  const row={id:'rendered',kind:'entity',text:'已渲染实体',version_id:'rendered-v1',attributes:[],class_parents:[],class_ontology_id:null};
  const subject=harness(()=>({records:[row]}));
  subject.context.wb.nodes.set(row.id,row);
  const graph=subject.context.ui.graph={nodes:[row],edges:[]},nodes=subject.context.wb.nodes;
  const choice=subject.elements.get('graph-entity-choice');Object.defineProperty(choice,'value',{get:()=>'',set:()=>{}});
  await subject.context.window.selectEntityDetail(row.id);
  assert.equal(subject.requests.length,1);assert.ok(subject.requests[0].url.endsWith('/records/page'));
  assert.deepEqual(Array.from(subject.requests[0].body.record_ids),[row.id]);assert.equal(subject.requests[0].body.limit,1);
  assert.equal(Object.hasOwn(subject.requests[0].body,'query'),false);
  assert.equal(subject.context.ui.graph,graph);assert.equal(subject.context.wb.nodes,nodes);
  assert.deepEqual(JSON.parse(JSON.stringify(subject.inspected[0])),row);assert.equal(subject.focused[0],subject.inspected[0]);
});

test('explicit local navigation bypasses a cached full target and reads requested expansion budgets',async()=>{
  const row={id:'cached',kind:'entity',text:'全局已缓存',version_id:'cached-v1'};
  const neighbor={id:'neighbor',kind:'entity',text:'邻居',version_id:'neighbor-v1'};
  const subject=harness((url)=>({records:url.endsWith('/records/page')?[row]:[row,neighbor]}));
  for(const [id,value] of [['graph-hops','3'],['graph-direction','backward'],['graph-max-nodes','1000'],['graph-max-edges','2000']])subject.elements.set(id,{value});
  subject.context.readInspectorRecord=async row=>row;
  let focused=false;subject.context.window.FullGraphView={mode:'full',filters:{},hasRecord:()=>true,focusRecord:()=>{focused=true;},switchMode:()=>{}};
  await subject.context.window.openKnowledgeRecord({id:row.id,kind:'entity',view:'local',hops:3});
  assert.equal(focused,false);
  assert.equal(subject.requests.length,2);
  const read=subject.requests[1];assert.ok(read.url.endsWith('/graph-neighborhood'));
  assert.equal(read.body.hops,3);assert.equal(read.body.direction,'backward');
  assert.equal(read.body.max_nodes,1000);assert.equal(read.body.max_edges,2000);
  assert.deepEqual(Array.from(read.body.seeds),[row.id]);
  assert.equal(subject.context.ui.graph.nodes.length,2);
});

test('explicit local relation expands both endpoints while retaining its exact edge',async()=>{
  const a={id:'a',kind:'entity',version_id:'a-v1'},b={id:'b',kind:'entity',version_id:'b-v1'};
  const edge={id:'edge',kind:'relation',type:'knows',subject_id:'a',object_id:'b',version_id:'edge-v2'};
  const subject=harness(url=>({records:url.endsWith('/records/page')?[edge]:[a,b]}));
  subject.elements.set('graph-hops',{value:'2'});
  await subject.context.window.openKnowledgeRecord({id:edge.id,kind:'relation',view:'local',hops:2,version_id:'edge-v2'});
  const read=subject.requests[1];assert.equal(read.body.hops,2);
  assert.deepEqual(Array.from(read.body.seeds),['a','b']);assert.equal(read.body.max_nodes,500);
  assert.equal(subject.context.ui.graph.edges[0].version_id,'edge-v2');
});

test('invalid explicit local hops reject before reading or replacing a valid graph',async()=>{
  const subject=harness();const graph=subject.context.ui.graph={nodes:[],edges:[]};
  await assert.rejects(subject.context.window.openKnowledgeRecord({id:'selected',kind:'entity',view:'local',hops:1.5}),/0–10/);
  assert.equal(subject.requests.length,0);assert.equal(subject.context.ui.graph,graph);
});

test('exact relation takes one reserved budget slot without adding an over-budget parallel edge',async()=>{
  const a={id:'a',kind:'entity',version_id:'a-v1'},b={id:'b',kind:'entity',version_id:'b-v1'};
  const exact={id:'exact',kind:'relation',type:'knows',subject_id:'a',object_id:'b',version_id:'exact-v2'};
  const earlier={...exact,id:'earlier',version_id:'earlier-v1'};
  const subject=harness(url=>({records:url.endsWith('/records/page')?[exact]:[a,b,earlier],truncated:false,attribute_budget:200,attribute_count:0}));
  subject.elements.set('graph-hops',{value:'3'});subject.elements.set('graph-max-edges',{value:'1'});
  await subject.context.window.openKnowledgeRecord({id:exact.id,kind:'relation',view:'local',hops:3,version_id:'exact-v2'});
  assert.deepEqual(Array.from(subject.context.ui.graph.edges,row=>row.id),['exact']);
  assert.equal(subject.context.ui.graph.truncated,true);
  assert.equal(subject.context.ui.graph.neighborhood.truncated,true);
  assert.equal(subject.context.ui.graph.attribute_budget,200);
  assert.equal(subject.requests[1].body.hops,3);assert.equal(subject.requests[1].body.max_edges,1);
});
test('entity choices use bounded query pages and preserve the chosen graph seed',async()=>{
  const subject=harness();
  assert.equal(typeof subject.context.loadEntityChoices,'function');
  await subject.context.loadEntityChoices('深页');
  const first=subject.requests[0];assert.ok(first.url.endsWith('/records/page'));
  assert.equal(first.body.limit,50);assert.equal(first.body.query,'深页');assert.deepEqual(Array.from(first.body.kinds),['entity']);
  assert.equal(first.body.known_at,'2025-01-01');
  await subject.context.loadEntityChoices('深页','next-page');
  assert.equal(subject.requests[1].body.cursor,'next-page');
  assert.equal(subject.elements.get('graph-node').value,'selected');
  assert.equal(subject.context.ui.pinned.get('selected').id,'selected');
  assert.match(subject.elements.get('graph-choice-status').textContent,/150,000|150000/);
});
test('scope generation changes discard pending entity pages without replacing selected choices',async()=>{
  let finish;const subject=harness(()=>new Promise(resolve=>finish=resolve));
  assert.equal(typeof subject.context.loadEntityChoices,'function');
  const pending=subject.context.loadEntityChoices('query');subject.store.reset('p','search');
  finish({records:[{id:'stale',kind:'entity'}],total:1,next_cursor:null});await pending;
  assert.equal(subject.context.ui.options[0].id,'selected');
});
test('picker and exact lookup retain the page-owned ontology release scope',async()=>{
  const subject=harness();subject.context.ontologyScope=()=>({ontology_scope:'ids',ontology_ids:['old-release']});
  await subject.context.loadEntityChoices('深页');
  assert.equal(subject.requests[0].body.ontology_scope,'ids');
  assert.deepEqual(Array.from(subject.requests[0].body.ontology_ids),['old-release']);
});

test('graph and mindmap pages keep independent queries, results and back cursors',async()=>{
  const subject=harness((url,body)=>({records:[{id:body.query+(body.cursor||'1'),kind:'entity'}],next_cursor:body.cursor?null:'page2',total:75}));
  await subject.context.loadEntityChoices('graph');
  await subject.context.loadEntityChoices('graph','page2');
  await subject.context.loadEntityChoices('brain',null,'mindmap');
  assert.equal(subject.context.ui.options[0].id,'graphpage2');
  assert.equal(subject.context.ui.mindmapPicker.options[0].id,'brain1');
  assert.equal(subject.context.ui.picker.page,2);
  await subject.context.loadEntityChoices('graph',null);
  assert.equal(subject.context.ui.picker.page,1);
  assert.equal(subject.context.ui.mindmapPicker.query,'brain');
});

test('server alias matches remain visible in candidate choices',()=>{
  const graph={value:'',innerHTML:''},mind={value:'',innerHTML:''};
  const context={get:id=>id==='graph-entity-choice'?graph:id==='mindmap-root'?mind:{value:'旧别名'},
    ui:{options:[{id:'alias-hit',kind:'entity',text:'正式名称',type:'Thing',metadata:{aliases:['旧别名']}}],mindmapPicker:{options:[]},pinned:new Map()},
    selectedEntityId:'',pendingEntityId:'',labelOf:value=>value,term:value=>value,esc:value=>String(value)};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  function choices(){'),source.indexOf("  for(const target of ['graph','mindmap'])")),context);
  context.choices();assert.match(graph.innerHTML,/alias-hit/);
});
