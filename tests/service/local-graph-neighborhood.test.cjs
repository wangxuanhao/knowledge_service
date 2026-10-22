const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {createStore}=require('../../knowledge_service/web/workspace-context.js');
const GraphTypeFilter=require('../../knowledge_service/web/graph-types.js');
const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
function workspace({reply,seed='a'}={}){
  const store=createStore();store.edit('project','search',{known_at:'2025-01-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}});store.apply('project','search');
  const elements=new Map(Object.entries({'graph-node':{value:seed},'type-scope':{value:''},'predicate-scope':{value:''},'graph-direction':{value:'backward'},'graph-max-nodes':{value:'250'},'graph-max-edges':{value:'1000'},'graph-summary':{textContent:''}}));
  const requests=[],rendered=[];
  const context={window:{WorkspaceContext:{read:store.read,ticket:store.ticket,accepts:store.accepts}},current:'project',GraphTypeFilter,graphRequests:GraphTypeFilter.createGraphRequestGate(),
    get:id=>elements.get(id),scope:()=>({known_at:'draft-must-not-leak'}),ontologyScope:()=>({ontology_scope:'all'}),kgTime:()=>()=>{},
    scopedRead:async()=>{throw Error('legacy scopedRead merges unapplied hidden controls');},
    api:async(url,body)=>{requests.push({route:url.replace('/api/projects/project',''),body});return reply?await reply():{records:[{id:'a',kind:'entity',type:'Person'},{id:'b',kind:'entity',type:'Person'},{id:'r',kind:'relation',type:'knows',subject_id:'a',object_id:'b'}],nodes:[],edges:[],truncated:true,continuation:{reason:'budget_exhausted'}};},
    renderGraph:result=>rendered.push(result),renderDiscoveryHint:async()=>{},console};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  const graphScope='),source.indexOf('  window.clearGraphWorkspace=')),context);
  return {context,store,requests,rendered,elements,draw:context.window.drawGraph};
}
test('local graph sends a seed and explicit budgets with the applied search Scope',async()=>{
  const subject=workspace();await subject.draw('a',2);
  assert.equal(subject.requests[0].route,'/graph-neighborhood');
  const body=subject.requests[0].body;
  assert.deepEqual(Array.from(body.seeds),['a']);
  assert.equal(body.hops,2);assert.equal(body.direction,'backward');
  assert.equal(body.max_nodes,250);assert.equal(body.max_edges,1000);
  assert.equal(body.known_at,'2025-01-01');assert.equal(body.filters.value,'杭州');
  assert.equal(Object.hasOwn(body,'entity_type'),false);assert.equal(Object.hasOwn(body,'predicate'),false);
  assert.equal(subject.rendered[0].nodes.length,2);assert.equal(subject.rendered[0].edges[0].id,'r');
  assert.equal(subject.rendered[0].neighborhood.truncated,true);
});

test('local graph accepts zero and user-entered hops above three without clamping',async()=>{
  for(const hops of [0,4,10]){const subject=workspace();await subject.draw('a',hops);assert.equal(subject.requests[0].body.hops,hops);}
});

test('invalid hop input is rejected before any neighborhood request',async()=>{
  for(const hops of [-1,11,2.5,'',NaN]){const subject=workspace();await assert.rejects(()=>subject.draw('a',hops),/0.*10.*整数/);assert.equal(subject.requests.length,0);}
});
test('local graph without a chosen entity explains how to begin without requesting a full graph',async()=>{
  const subject=workspace({seed:''});await subject.draw();
  assert.equal(subject.requests.length,0);
  assert.match(subject.elements.get('graph-summary').textContent,/选择实体|选中实体/);
});
test('page-owned ontology release scope is retained beside applied content filters',async()=>{
  const subject=workspace();subject.context.ontologyScope=()=>({ontology_scope:'ids',ontology_ids:['release-old']});
  await subject.draw('a',1);
  assert.equal(subject.requests[0].body.ontology_scope,'ids');
  assert.deepEqual(Array.from(subject.requests[0].body.ontology_ids),['release-old']);
  assert.equal(subject.requests[0].body.filters.value,'杭州');
});
test('applied scope A B A changes invalidate an in-flight neighborhood',async()=>{
  let finish;const pending=new Promise(resolve=>finish=resolve),subject=workspace({reply:()=>pending});
  const draw=subject.draw('a',1),initial=subject.store.read('project','search');
  subject.store.edit('project','search',{...initial,known_at:'2025-02-01'});subject.store.apply('project','search');
  subject.store.edit('project','search',initial);subject.store.apply('project','search');
  finish({records:[{id:'a',kind:'entity'}],nodes:[],edges:[]});await draw;
  assert.equal(subject.rendered.length,0);
});
test('clearing the selected seed invalidates a pending neighborhood response',async()=>{
  let finish;const pending=new Promise(resolve=>finish=resolve),subject=workspace({reply:()=>pending});
  const draw=subject.draw('a',1);subject.elements.get('graph-node').value='';await subject.draw();
  finish({records:[{id:'a',kind:'entity'}],nodes:[],edges:[]});await draw;
  assert.equal(subject.rendered.length,0);
});
test('budget status identifies truncation and offers a fresh bounded request',()=>{
  const subject=workspace();
  assert.equal(typeof subject.context.graphBudgetSummary,'function');
  const summary=subject.context.graphBudgetSummary({neighborhood:{seeds:['a'],hops:2,direction:'both',max_nodes:500,max_edges:1000,truncated:true}});
  assert.match(summary,/500/);assert.match(summary,/1000/);assert.match(summary,/截断/);assert.match(summary,/重新请求|重新加载/);
  assert.doesNotMatch(summary,/游标|断点续|完整图谱|全量加载完成/);
});
test('attribute nodes have a separate visible budget and truncated attribute state',()=>{
  const subject=workspace();
  const summary=subject.context.graphBudgetSummary({neighborhood:{hops:1,direction:'both',max_nodes:500,max_edges:1000,truncated:false},
    attribute_budget:200,attribute_count:200,attributes_truncated:true,attribute_candidates_loaded:false});
  assert.match(summary,/属性.*200/);assert.match(summary,/属性.*截断/);assert.match(summary,/冲突候选/);
});
test('unloaded attribute provenance count is shown as unknown rather than zero support',()=>{
  const {attributeDetailsHtml}=require('../../knowledge_service/web/evidence-inspector.js');
  const html=attributeDetailsHtml([{label:'额度',values:[{value:7,datatype:'integer',status:'accepted',accepted_support_count_known:false}]}]);
  assert.doesNotMatch(html,/0 条支撑/);assert.match(html,/未计数|尚未读取/);
});
test('local type choices include actual loaded legacy record types beside ontology terms',()=>{
  assert.ok(source.includes('function updateGraphTypeChoices('));
  const selects=new Map(['type-scope','predicate-scope'].map(id=>[id,{options:[{value:''}],appendChild(row){this.options.push(row);}}]));
  const context={get:id=>selects.get(id),labelOf:value=>value,document:{createElement:()=>({})}};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function updateGraphTypeChoices('),source.indexOf('  function renderGraph(')),context);
  context.updateGraphTypeChoices({nodes:[{kind:'entity',type:'LegacyThing'}],edges:[{kind:'relation',type:'legacyRelation'}]});
  assert.equal(selects.get('type-scope').options[1].value,'LegacyThing');
  assert.equal(selects.get('predicate-scope').options[1].value,'legacyRelation');
});
test('changing a canvas type filter keeps the chosen seed available for redraw and expand',async()=>{
  assert.ok(source.includes('async function refreshGraphCanvasTypes('));
  const row={id:'a',kind:'entity'},node={value:'a'},calls=[];
  const context={current:'p',get:id=>id==='graph-node'?node:{value:'1'},ui:{pinned:new Map([['a',row]])},wb:{nodes:new Map([['a',row]])},
    inspect:record=>{node.value=record.id;},status:()=>{},window:{clearGraphWorkspace:()=>{node.value='';},drawGraph:async(id,hops)=>calls.push({id,hops})}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  async function refreshGraphCanvasTypes('),source.indexOf("  for(const id of ['type-scope','predicate-scope'])get(id).addEventListener",source.indexOf('  async function refreshGraphCanvasTypes('))),context);
  await context.refreshGraphCanvasTypes();
  assert.equal(node.value,'a');assert.equal(calls[0].id,'a');
});
