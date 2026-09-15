const {test}=require('node:test');
const assert=require('node:assert/strict');
const {selectType}=require('../../knowledge_service/web/graph-types.js');
test('type buttons select matching entities, not neighbours or the inverse category',()=>{
  const graph={nodes:[{id:'p',type:'a/Platform'},{id:'l',type:'Law'},{id:'p2',type:'b/Platform'}],edges:[{id:'e',subject_id:'p',object_id:'l'}]};
  assert.deepEqual(selectType(graph,'a/Platform').nodes.map(n=>n.id),['p']);
  assert.equal(selectType(graph,'a/Platform').edges.length,0);
  assert.deepEqual(selectType(graph,'Law').nodes.map(n=>n.id),['l']);
  assert.deepEqual(selectType(graph,'b/Platform').nodes.map(n=>n.id),['p2']);
  assert.equal(selectType(graph,'').nodes.length,3);
  assert.equal(graph.nodes.length,3);
});
