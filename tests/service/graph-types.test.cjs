const {test}=require('node:test');
const assert=require('node:assert/strict');
const {selectType,entityNodes,relationEdges,isAttributeNode}=require('../../knowledge_service/web/graph-types.js');
test('type buttons select matching entities, not neighbours or the inverse category',()=>{
  const graph={nodes:[{id:'p',type:'a/Platform'},{id:'l',type:'Law'},{id:'p2',type:'b/Platform'}],edges:[{id:'e',subject_id:'p',object_id:'l'}]};
  assert.deepEqual(selectType(graph,'a/Platform').nodes.map(n=>n.id),['p']);
  assert.equal(selectType(graph,'a/Platform').edges.length,0);
  assert.deepEqual(selectType(graph,'Law').nodes.map(n=>n.id),['l']);
  assert.deepEqual(selectType(graph,'b/Platform').nodes.map(n=>n.id),['p2']);
  assert.equal(selectType(graph,'').nodes.length,3);
  assert.equal(graph.nodes.length,3);
});

test('attribute value nodes stay out of entity types, counts, and relation filtering',()=>{
  const graph={nodes:[
    {id:'p',kind:'entity',type:'Person'},
    {id:'v',kind:'attribute_value',type:'KS_ATTRIBUTE_VALUE',virtual:true,subject_id:'p'},
  ],edges:[
    {id:'property',kind:'attribute_edge',type:'KS_ATTRIBUTE',subject_id:'p',object_id:'v'},
  ]};
  assert.deepEqual(entityNodes(graph).map(node=>node.id),['p']);
  assert.deepEqual(relationEdges(graph),[]);
  assert.equal(isAttributeNode(graph.nodes[1]),true);
  assert.deepEqual(selectType(graph,'Person').nodes.map(node=>node.id),['p']);
  assert.deepEqual(selectType(graph,'Person').edges,[]);
  assert.equal(selectType(graph,'').nodes.length,2);
});
