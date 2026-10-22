const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createStore}=require('../../knowledge_service/web/workspace-context.js');
const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
function harness(reply){
  const store=createStore();store.edit('p','search',{known_at:'2025-01-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}});store.apply('p','search');
  const selected={id:'selected',kind:'entity',text:'之前选择',type:'Thing'};
  const elements=new Map(Object.entries({'graph-node':{value:'selected'},'graph-entity-choice':{value:'selected',options:[]},'mindmap-root':{value:'selected'},'graph-entity-filter':{value:''},'graph-choice-status':{textContent:''},'graph-choice-next':{disabled:false},'tab-search':{classList:{contains:()=>false}}}));
  const requests=[],inspected=[];
  const context={window:{WorkspaceContext:{read:store.read,ticket:store.ticket,accepts:store.accepts}},current:'p',recordOpenEpoch:0,ontologyScope:()=>({ontology_scope:'all',ontology_ids:null}),ui:{options:[selected],pinned:new Map(),picker:{request:0,cursor:null,query:'',page:0}},
    wb:{nodes:new Map()},status:()=>{},get:id=>elements.get(id),scope:()=>({known_at:'draft'}),endpoint:route=>'/api/projects/p'+route,choices:()=>{},kgTime:()=>()=>{},
    inspect:row=>inspected.push(row),api:async(url,body)=>{requests.push({url,body});return reply?await reply(url,body):{records:[{id:'deep-149999',kind:'entity',text:'深页实体'}],next_cursor:'next-page',total:150000};}};
  vm.createContext(context);
  const direct=source.slice(source.indexOf('  async function selectEntityDetail('),source.indexOf('  async function options('));
  vm.runInContext(direct,context);
  return {context,store,elements,requests,inspected};
}
test('search entity absent from loaded choices opens by a scoped exact ID lookup',async()=>{
  const subject=harness((url,body)=>url.endsWith('/graph-neighborhood')?{records:[{id:'deep-149999',kind:'entity',text:'深页实体',class_parents:[]}]}:{records:[{id:'deep-149999',kind:'entity',text:'深页实体'}]});
  const choice=subject.elements.get('graph-entity-choice');Object.defineProperty(choice,'value',{get:()=>'',set:()=>{}});
  await subject.context.window.selectEntityDetail('deep-149999');
  const request=subject.requests[0];
  assert.ok(request.url.endsWith('/records/page'));
  assert.equal(request.body.known_at,'2025-01-01');
  assert.deepEqual(JSON.parse(JSON.stringify(request.body.filters)),{field:'metadata.city',op:'eq',value:'杭州'});
  assert.deepEqual(Array.from(request.body.record_ids),['deep-149999']);
  assert.equal(subject.inspected[0].id,'deep-149999');
});

test('an already rendered entity opens its existing detail without loading picker choices',async()=>{
  const subject=harness();
  const row={id:'rendered',kind:'entity',text:'已渲染实体'};
  subject.context.wb.nodes.set(row.id,row);
  const choice=subject.elements.get('graph-entity-choice');Object.defineProperty(choice,'value',{get:()=>'',set:()=>{}});
  await subject.context.window.selectEntityDetail(row.id);
  assert.equal(subject.requests.length,0);
  assert.equal(subject.inspected[0],row);
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
