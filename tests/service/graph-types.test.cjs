const {test}=require('node:test');
const assert=require('node:assert/strict');
const {selectType,entityNodes,relationEdges,isAttributeNode,createGraphRequestGate}=require('../../knowledge_service/web/graph-types.js');
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

function deferred(){
  let resolve;
  const promise=new Promise(done=>{resolve=done;});
  return {promise,resolve};
}

test('newer graph request wins when responses resolve out of order',async()=>{
  const gate=createGraphRequestGate();
  const scope={known_at:null,include_unknown:true,filters:{and:[{field:'city',op:'eq',value:'北京'}]}};
  let rendered='initial';
  const draw=async(node,pending)=>{
    const ticket=gate.begin('project',scope,node);
    const result=await pending.promise;
    if(gate.isCurrent(ticket,'project',scope,node))rendered=result;
  };
  const slow=deferred(),fast=deferred();
  const first=draw('A',slow),second=draw('B',fast);
  fast.resolve('B');await second;
  slow.resolve('A');await first;
  assert.equal(rendered,'B');
});

test('invalidating graph requests keeps cleared state after a pending response',async()=>{
  const gate=createGraphRequestGate();
  const scope={filters:null,known_at:null,include_unknown:true};
  const pending=deferred();let rendered='graph';
  const ticket=gate.begin('project',scope,'A');
  const request=pending.promise.then(result=>{
    if(gate.isCurrent(ticket,'project',scope,'A'))rendered=result;
  });
  gate.invalidate();rendered='empty hint';
  pending.resolve('stale graph');await request;
  assert.equal(rendered,'empty hint');
});

test('invalidated graph load releases busy without letting its stale owner release the next load',async()=>{
  const gate=createGraphRequestGate();
  const scope={filters:null,known_at:null,include_unknown:true};
  const firstResult=deferred(),secondResult=deferred();
  const started=[];let rendered='empty hint';
  const autoLoad=async(project,pending)=>{
    if(gate.isBusy())return false;
    started.push(project);
    const ticket=gate.begin(project,scope,null);
    try{
      const result=await pending.promise;
      if(gate.isCurrent(ticket,project,scope,null))rendered=result;
    }finally{gate.release(ticket);}
    return true;
  };

  const first=autoLoad('A',firstResult);
  gate.invalidate();
  const second=autoLoad('B',secondResult);
  assert.deepEqual(started,['A','B']);
  assert.equal(gate.isBusy(),true);

  firstResult.resolve('stale A');await first;
  assert.equal(rendered,'empty hint');
  assert.equal(gate.isBusy(),true);

  secondResult.resolve('current B');await second;
  assert.equal(rendered,'current B');
  assert.equal(gate.isBusy(),false);
});

test('workspace gates graph rendering and invalidates requests when clearing',()=>{
  const workspace=require('node:fs').readFileSync(
    require('node:path').resolve(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  assert.ok(workspace.includes('graphRequests.begin('));
  assert.ok(workspace.includes('graphRequests.isCurrent('));
  assert.ok(workspace.includes('graphRequests.release(ticket)'));
  assert.ok(workspace.includes('!graphRequests.isBusy()'));
  assert.ok(!workspace.includes('ui.busy'));
  const invalidations=workspace.match(/graphRequests\.invalidate\(\)/g)||[];
  assert.equal(invalidations.length,1);
  const resetStart=workspace.indexOf('graphRequests.invalidate()');
  const clear=workspace.slice(resetStart,workspace.indexOf("act('search'",resetStart));
  assert.ok(clear.includes('graphRequests.invalidate()'));
  assert.ok(clear.includes('ui.graph=null'));
  assert.ok(clear.includes('wb.nodes.clear()'));
});
