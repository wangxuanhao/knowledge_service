const {test}=require('node:test');
const assert=require('node:assert/strict');
const {findPath}=require('../../knowledge_service/web/graph-paths.js');
const graph={nodes:['a','b','c','isolated'].map(id=>({id,kind:'entity'})),edges:[
  {id:'ab',kind:'relation',subject_id:'a',object_id:'b'},
  {id:'bc',kind:'relation',subject_id:'b',object_id:'c'},
  {id:'ca',kind:'relation',subject_id:'c',object_id:'a'},
]};
test('directed shortest path retains actual relationships without changing graph',()=>{
  const before=JSON.stringify(graph),result=findPath(graph,'a','c',{maxHops:3});
  assert.deepEqual(result.nodes,['a','b','c']);assert.deepEqual(result.steps.map(s=>s.edge.id),['ab','bc']);
  assert.equal(result.steps[0].reverse,false);assert.equal(JSON.stringify(graph),before);
});
test('hop budget and missing nodes do not imply global disconnection',()=>{
  assert.equal(findPath(graph,'a','c',{maxHops:1}),null);
  assert.equal(findPath(graph,'a','isolated'),null);assert.equal(findPath(graph,'missing','a'),null);
  assert.deepEqual(findPath(graph,'a','a').nodes,['a']);
});
test('undirected search records reverse traversal, never reverses original fact',()=>{
  const result=findPath(graph,'b','a',{directed:false,maxHops:1});
  assert.deepEqual(result.nodes,['b','a']);assert.equal(result.steps[0].reverse,true);
  assert.equal(result.steps[0].edge.subject_id,'a');
});
test('virtual projections and dangling edges cannot form a fact path',()=>{
  const projected={nodes:[...graph.nodes,{id:'v',kind:'attribute_value',virtual:true}],edges:[
    {id:'av',kind:'attribute_edge',subject_id:'a',object_id:'v'},
    {id:'vc',kind:'relation',subject_id:'v',object_id:'c'},
    {id:'ghost',subject_id:'a',object_id:'ghost'},
    {id:'ghost2',subject_id:'ghost',object_id:'c'},
  ]};
  assert.equal(findPath(projected,'a','c'),null);assert.equal(findPath(projected,'v','c'),null);
});
test('parallel edges produce one stable shortest route carrying its evidence identity',()=>{
  const result=findPath({...graph,edges:[{...graph.edges[0],id:'preferred'},...graph.edges]},'a','b');
  assert.equal(result.steps[0].edge.id,'preferred');
  assert.equal(findPath(graph,'a','c',{maxHops:0}),null);
});
