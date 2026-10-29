const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function load({model=true}={}){
  const context={console,Map,Set,Math,Array,Error,Float32Array,Uint8Array,Uint32Array,Int32Array,setTimeout,performance:{now:()=>0},cancelAnimationFrame(){}};
  context.window=context;vm.createContext(context);
  const files=['graph-palette.js','graph-appearance.js',...(model?['graph-exploration.js']:[]),'full-graph.js'];
  for(const file of files)vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web',file),'utf8'),context);
  return {...context.FullGraph,exploration:context.GraphExploration,appearance:context.GraphAppearance};
}

function scene(count=3){
  const api=load(),dataset=new api.GraphDataset(),calls=[];
  dataset.addNodes(Array.from({length:count},(_,i)=>({id:'n'+i,text:'Name '+i,type:'Type'+i%2})),new Float32Array(Array.from({length:count*2},(_,i)=>i%2?0:(i/2)*.4-.4)));
  if(count>1)dataset.addEdges([{id:'r',subject_id:'n0',object_id:'n1'}]);
  const labelContext={setTransform(){},clearRect(){calls.push({kind:'clear'});},save(){},restore(){},beginPath(){},moveTo(x,y){calls.push({kind:'move',x,y});},lineTo(x,y){calls.push({kind:'line',x,y});},arc(x,y,r){calls.push({kind:'arc',x,y,r});},stroke(){calls.push({kind:'stroke',color:this.strokeStyle,width:this.lineWidth});},fill(){calls.push({kind:'fill',color:this.fillStyle});},measureText:text=>({width:text.length*6}),fillText(text,x,y){calls.push({kind:'text',text,x,y});}};
  let requested=0;
  const renderer=Object.assign(Object.create(api.Renderer.prototype),{dataset,camera:new api.Camera(),selected:0,selectedEdge:-1,showEdges:true,showLabels:true,dpr:1,width:800,height:600,canvas:{width:800,height:600,style:{opacity:''}},labelContext,durations:[],requestDraw(){requested++;},sync(){this.typeProjection?.sync();},abort:{abort(){}},observer:{disconnect(){}},host:{},lost:true});
  return {api,dataset,renderer,calls,requested:()=>requested};
}

test('dataset neighbor indexing is lazy and reuses its canonical arrays and ID map',()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset(),edge={id:'r',subject_id:'a',object_id:'b'};
  dataset.addNodes([{id:'a'},{id:'b'}]);dataset.addEdges([edge]);
  assert.equal(dataset._neighborIndex,undefined,'ordinary appends must not construct adjacency storage');
  assert.equal(typeof dataset.getNeighborIndex,'function');
  const index=dataset.getNeighborIndex();
  assert.equal(index.source,dataset);assert.equal(index.nodeIndex,dataset.nodeIndex);
  assert.equal(index._nodes,dataset.records);assert.equal(index._edges,dataset.edges);
  assert.equal(dataset.getNeighborIndex(),index);assert.equal(index.page('a').items[0].edge,edge);
});

test('an activated neighbor index consumes appends without reading old edge records',()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset();
  dataset.addNodes([{id:'a'},{id:'b'}]);dataset.addEdges([{id:'old',subject_id:'a',object_id:'b'}]);
  assert.equal(typeof dataset.getNeighborIndex,'function');const index=dataset.getNeighborIndex(),version=index.version;
  let oldEdges=0;
  // Keep the same array object: the model owns its append-only source identity.
  Object.defineProperty(dataset.edges,0,{get(){oldEdges++;throw new Error('old edge revisited');},configurable:true});
  dataset.addNodes([{id:'c'}]);dataset.addEdges([{id:'new',subject_id:'b',object_id:'c'}]);
  assert.ok(index.version>version,'append hooks keep an already opened index current');
  assert.equal(index._nodesSeen,3);assert.equal(index._edgesSeen,2);assert.equal(oldEdges,0);
  assert.equal(index.page('c',{direction:'backward'}).items[0].edge.id,'new');
});

test('type projections create independent neighbor indexes with projection positions',()=>{
  const {GraphDataset,TypeProjection}=load(),dataset=new GraphDataset();
  dataset.addNodes([{id:'a',type:'A'},{id:'hidden',type:'B'},{id:'b',type:'A'}]);
  dataset.addEdges([{id:'ab',subject_id:'a',object_id:'b'},{id:'ah',subject_id:'a',object_id:'hidden'}]);
  const projection=new TypeProjection(dataset,'A');projection.sync();
  assert.equal(typeof projection.dataset.getNeighborIndex,'function');
  const index=projection.dataset.getNeighborIndex();
  assert.equal(index.nodeIndex,projection.dataset.nodeIndex);assert.notEqual(index.nodeIndex,dataset.nodeIndex);
  assert.equal(index.nodeIndex.get('b'),1);assert.deepEqual(Array.from(index.focus('a',{hops:2}).nodeIds),['a','b']);
  assert.equal(dataset._neighborIndex,undefined,'opening a projection never builds the full session index');
  dataset.addNodes([{id:'c',type:'A'}]);dataset.addEdges([{id:'bc',subject_id:'b',object_id:'c'}]);projection.sync();
  assert.equal(index._nodesSeen,3);assert.equal(index._edgesSeen,2);
});

test('async neighbor preparation yields between bounded node chunks without activating a partial index',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset(),chunks=[];
  dataset.addNodes(Array.from({length:9001},(_,i)=>({id:'n'+i})));
  assert.equal(dataset._neighborIndex,undefined);assert.equal(typeof dataset.getNeighborIndexAsync,'function');let reads=0;
  dataset.records=new Proxy(dataset.records,{get(target,key,receiver){if(/^\d+$/.test(String(key)))reads++;return Reflect.get(target,key,receiver);}});
  const index=await dataset.getNeighborIndexAsync({yieldControl(){
    assert.equal(dataset._neighborIndex,undefined,'append hooks cannot see an unfinished deferred index');
    assert.ok(reads<=4000,'each chunk consumes at most four thousand nodes');chunks.push(reads);reads=0;return Promise.resolve();
  }});
  assert.ok(chunks.length>=2);assert.ok(chunks.every(count=>count>0));assert.ok(reads<=4000);
  assert.equal(index.complete,true);assert.equal(index._nodesSeen,9001);assert.equal(dataset.getNeighborIndex(),index);
  assert.equal(index._nodes,dataset.records);assert.equal(index.nodeIndex,dataset.nodeIndex);
});

test('concurrent async neighbor requests share one preparation promise and completed index',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset();dataset.addNodes(Array.from({length:5001},(_,i)=>({id:'n'+i})));
  assert.equal(typeof dataset.getNeighborIndexAsync,'function');let yields=0;
  const first=dataset.getNeighborIndexAsync({yieldControl:()=>{yields++;return Promise.resolve();}}),second=dataset.getNeighborIndexAsync({yieldControl:()=>assert.fail('a concurrent caller must reuse the pending preparation')});
  assert.equal(second,first);const [a,b]=await Promise.all([first,second]);assert.equal(a,b);assert.equal(yields,1);
  assert.equal(await dataset.getNeighborIndexAsync({yieldControl:()=>assert.fail('a hot index needs no yielding')}),a);
});

test('async neighbor preparation consumes at most four thousand edges between yields',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset(),chunks=[];dataset.addNodes([{id:'a'},{id:'b'}]);
  dataset.addEdges(Array.from({length:9001},(_,i)=>({id:'r'+i,subject_id:'a',object_id:'b'})));let reads=0;
  dataset.edges=new Proxy(dataset.edges,{get(target,key,receiver){if(/^\d+$/.test(String(key)))reads++;return Reflect.get(target,key,receiver);}});
  const index=await dataset.getNeighborIndexAsync({yieldControl(){chunks.push(reads);assert.ok(reads<=4000);assert.equal(dataset._neighborIndex,undefined);reads=0;return Promise.resolve();}});
  assert.deepEqual(chunks,[4000,4000]);assert.equal(reads,1001);assert.equal(index._edgesSeen,9001);assert.equal(index.complete,true);
});

test('appends while async preparation yields stay lazy and are included before completion',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset();dataset.addNodes(Array.from({length:9001},(_,i)=>({id:'n'+i})));
  assert.equal(typeof dataset.getNeighborIndexAsync,'function');let appended=false;
  const edge={id:'late-edge',subject_id:'n0',object_id:'late'};
  const index=await dataset.getNeighborIndexAsync({yieldControl(){
    if(!appended){
      appended=true;assert.equal(dataset._neighborIndex,undefined);
      dataset.addNodes([{id:'late'}]);dataset.addEdges([edge]);
      assert.equal(dataset._neighborIndex,undefined,'normal append hooks must leave pending preparation deferred');
    }
    return Promise.resolve();
  }});
  assert.equal(appended,true);assert.equal(index.complete,true);assert.equal(index._nodesSeen,9002);assert.equal(index._edgesSeen,1);
  assert.equal(index.page('late',{direction:'backward'}).items[0].edge,edge);
});

test('async neighbor preparation retries after a rejected yield and the hot path reads no records',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset();dataset.addNodes(Array.from({length:5001},(_,i)=>({id:'n'+i})));
  assert.equal(typeof dataset.getNeighborIndexAsync,'function');
  await assert.rejects(dataset.getNeighborIndexAsync({yieldControl:()=>Promise.reject(new Error('yield interrupted'))}),/yield interrupted/);
  assert.equal(dataset._neighborIndex,undefined);
  const index=await dataset.getNeighborIndexAsync({yieldControl:()=>Promise.resolve()});
  dataset.records=new Proxy(dataset.records,{get(target,key,receiver){if(/^\d+$/.test(String(key)))assert.fail('a completed index must not be rebuilt');return Reflect.get(target,key,receiver);}});
  assert.equal(await dataset.getNeighborIndexAsync({yieldControl:()=>assert.fail('a completed index must not yield')}),index);
});

test('default async index preparation returns control to the timer queue',async()=>{
  const {GraphDataset}=load(),dataset=new GraphDataset();dataset.addNodes(Array.from({length:5001},(_,i)=>({id:'n'+i})));
  assert.equal(typeof dataset.getNeighborIndexAsync,'function');let timerRan=false;
  setTimeout(()=>{timerRan=true;},0);const index=await dataset.getNeighborIndexAsync();
  assert.equal(timerRan,true);assert.equal(index.complete,true);
});

test('renderer remains loadable without the optional model until neighbor indexing is requested',()=>{
  const {GraphDataset}=load({model:false}),dataset=new GraphDataset();
  dataset.addNodes([{id:'a'}]);assert.equal(dataset.nodeCount,1);
  assert.equal(typeof dataset.getNeighborIndex,'function');assert.throws(()=>dataset.getNeighborIndex(),/GraphExploration|探索/);
});

test('focus geometry and the selected ring stay visible with labels disabled and use the type palette',()=>{
  const {renderer,dataset,calls,api}=scene(),positions=Array.from(dataset.positions),camera={...renderer.camera};
  assert.equal(typeof renderer.setExplorationFocus,'function');
  renderer.showLabels=false;renderer.setExplorationFocus(dataset.getNeighborIndex().focus('n0'));renderer.drawLabels();
  assert.equal(renderer.canvas.style.opacity,'0.18');assert.equal(calls.filter(c=>c.kind==='line').length,1);
  assert.equal(calls.filter(c=>c.kind==='fill').length,2);assert.equal(calls.filter(c=>c.kind==='arc').length,3,'two nodes and the selected ring');
  assert.equal(calls.filter(c=>c.kind==='text').length,0);
  const expected='rgb('+Array.from(api.appearance.rgbFor(dataset.records[0])).join(',')+')';
  assert.equal(calls.find(c=>c.kind==='fill').color,expected);
  assert.deepEqual(Array.from(dataset.positions),positions);assert.deepEqual({...renderer.camera},camera);
  calls.length=0;renderer.showEdges=false;renderer.drawLabels();assert.equal(calls.filter(c=>c.kind==='line').length,0);assert.equal(calls.filter(c=>c.kind==='fill').length,2);
});

test('focus prepares only supplied edge records and each frame visits bounded focus geometry and labels',()=>{
  const {renderer,dataset,calls}=scene(1200),focus={nodeIds:new Set(Array.from({length:300},(_,i)=>'n'+i)),edgeIds:new Set(['r']),edgeRecords:[dataset.edges[0]]};
  assert.equal(typeof renderer.setExplorationFocus,'function');
  dataset.edges=new Proxy(dataset.edges,{get(target,key,receiver){if(/^\d+$/.test(String(key))||key===Symbol.iterator||['find','findIndex','filter','forEach','map'].includes(key))throw new Error('focus must not scan the full edge collection');return Reflect.get(target,key,receiver);}});
  renderer.setExplorationFocus(focus);
  let nodeReads=0,lookups=0;
  dataset.records=new Proxy(dataset.records,{get(target,key,receiver){if(/^\d+$/.test(String(key))){nodeReads++;assert.ok(Number(key)<200,'only bounded focus records may be drawn');}return Reflect.get(target,key,receiver);}});
  dataset.nodeIndex.get=()=>{lookups++;throw new Error('resolve IDs once when the focus changes');};
  renderer.camera.worldToScreen=(x,y)=>[100+x*.1,100+y*.1];renderer.camera.scale=10;renderer.drawLabels();
  assert.equal(calls.filter(c=>c.kind==='fill').length,200);assert.ok(calls.filter(c=>c.kind==='text').length<=20);assert.ok(nodeReads<=220);assert.equal(lookups,0);
});

test('focus overlay caps supplied edge geometry at four hundred records',()=>{
  const {renderer,dataset,calls}=scene();
  dataset.addEdges(Array.from({length:600},(_,i)=>({id:'extra'+i,subject_id:'n0',object_id:'n1'})));
  renderer.setExplorationFocus({nodeIds:new Set(['n0','n1']),edgeIds:new Set(dataset.edges.map(edge=>edge.id)),edgeRecords:dataset.edges});
  renderer.showLabels=false;renderer.drawLabels();assert.equal(calls.filter(call=>call.kind==='line').length,400);
});

test('focus is drawn by the complete-view cache finish path without new GL work',()=>{
  const {renderer,dataset,calls}=scene();assert.equal(typeof renderer.setExplorationFocus,'function');
  renderer.showLabels=false;renderer.lost=false;renderer.gl={viewport(){},clearColor(){},clear(){}};renderer.drawViewCache=()=>true;
  renderer.setExplorationFocus(dataset.getNeighborIndex().focus('n0'));renderer.draw();
  assert.equal(calls.filter(c=>c.kind==='fill').length,2);assert.equal(renderer.drawnNodes,3);assert.equal(renderer.drawnEdges,1);
});

test('focus clears on dataset type and lifecycle replacement and restores the canvas opacity',()=>{
  for(const change of ['clear','dataset','type','destroy']){
    const {renderer,dataset,api,requested}=scene();assert.equal(typeof renderer.setExplorationFocus,'function');
    renderer.canvas.style.opacity='0.9';renderer.setExplorationFocus(dataset.getNeighborIndex().focus('n0'));
    if(change==='clear')renderer.setExplorationFocus(null);
    if(change==='dataset')renderer.setDataset(new api.GraphDataset());
    if(change==='type')renderer.setTypeFilter('Type0');
    if(change==='destroy')renderer.destroy();
    assert.equal(renderer.explorationFocus,null,change);assert.equal(renderer.canvas.style.opacity,'0.9',change);
    const before=requested();renderer.setExplorationFocus(null);assert.equal(requested(),before,'clearing an inactive focus schedules no extra frame');
  }
});

test('type focus resolves projection coordinates and cannot render hidden type endpoints',()=>{
  const {renderer,dataset,calls}=scene();renderer.setTypeFilter('Type0');
  assert.equal(typeof renderer.setExplorationFocus,'function');
  const display=renderer.displayDataset;display.positions=new Float32Array([-.7,.3,.8,-.2]);
  renderer.setExplorationFocus({nodeIds:new Set(['n2','n1']),edgeIds:new Set(['r']),edgeRecords:[dataset.edges[0]]});renderer.showLabels=false;renderer.drawLabels();
  const center=calls.find(c=>c.kind==='arc'),expected=renderer.camera.worldToScreen(.8,-.2,renderer.width,renderer.height);
  assert.ok(Math.abs(center.x-expected[0])<.001&&Math.abs(center.y-expected[1])<.001);
  assert.equal(calls.filter(c=>c.kind==='fill').length,1);assert.equal(calls.filter(c=>c.kind==='line').length,0);
});
