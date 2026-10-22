const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function load(){
  const context={window:{},console,Float32Array,Map,Set,Math,Array,Error,Uint32Array};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  return context.window.FullGraph;
}
test('complete graph buffers include every entity independently of label budget',()=>{
  const {GraphDataset}=load();const data=new GraphDataset();
  const rows=Array.from({length:150000},(_,i)=>({id:'e'+i,text:'实体'+i,type:'Thing',version:1,version_id:'v'+i}));
  data.addNodes(rows);
  assert.equal(data.nodeCount,150000);
  assert.equal(data.nodeIndex.get('e149999'),149999);
  assert.equal(data.positions.length,300000);
  assert.equal(data.records[149999].id,'e149999');
});
test('edges preserve record identity, reject missing endpoints and deduplicate pages',()=>{
  const {GraphDataset}=load();const data=new GraphDataset();
  data.addNodes([{id:'a',text:'甲'},{id:'b',text:'乙'}]);
  const edge={id:'r',subject_id:'a',object_id:'b',type:'knows'};
  data.addEdges([edge,edge]);
  assert.equal(data.edgeCount,1);
  assert.equal(data.edges[0].id,'r');
  assert.throws(()=>data.addEdges([{id:'invalid',subject_id:'a',object_id:'c'}]),/端点/);
});
test('conflicting node revision in a resumed page is rejected',()=>{
  const {GraphDataset}=load();const data=new GraphDataset();
  data.addNodes([{id:'a',version_id:'v1',text:'甲'}]);
  assert.throws(()=>data.addNodes([{id:'a',version_id:'v2',text:'改后'}]),/版本/);
});
test('camera world and screen transformations roundtrip after zoom and pan',()=>{
  const {Camera}=load();const camera=new Camera();camera.pan(20,-10,800,600);camera.zoomAt(2,300,200,800,600);
  const screen=camera.worldToScreen(.25,-.37,800,600);
  const world=camera.screenToWorld(...screen,800,600);
  assert.ok(Math.abs(world[0]-.25)<1e-6&&Math.abs(world[1]+.37)<1e-6);
});

function labelScene(count){
  const {Renderer,GraphDataset,Camera}=load(),dataset=new GraphDataset(),drawn=[];
  const rows=Array.from({length:count},(_,i)=>({id:'n'+i,text:'Label '+i}));
  const positions=new Float32Array(rows.flatMap((_,i)=>[(i%12)*.13-.8,(Math.floor(i/12)%10)*.15-.7]));
  dataset.addNodes(rows,positions);
  const labelContext={setTransform(){},clearRect(){},measureText:text=>({width:text.length*6}),fillText(text,x,y){drawn.push({text,x,y,width:text.length*6});}};
  const renderer=Object.assign(Object.create(Renderer.prototype),{dataset,camera:new Camera(),selected:-1,dpr:1,width:800,height:600,labelContext});
  return {renderer,drawn};
}

test('small first batch labels are visible at normal scale, avoid overlaps and retain selected names',()=>{
  const {renderer,drawn}=labelScene(120);renderer.selected=119;renderer.drawLabels();
  assert.ok(drawn.length>=20,'normal-scale partial graphs need useful names');
  assert.ok(drawn.length<=50,'small-view names have a fixed budget');
  assert.equal(drawn[0].text,'Label 119','selected entity stays labelled even beyond the ordinary label budget');
  for(let i=0;i<drawn.length;i++)for(let j=i+1;j<drawn.length;j++){
    const a=drawn[i],b=drawn[j];
    assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+16<=b.y||b.y+16<=a.y,'names must not overlap');
  }
});

test('dense normal view retains selected labels only and zoomed labels stay bounded',()=>{
  const {renderer,drawn}=labelScene(801);renderer.drawLabels();assert.equal(drawn.length,0);
  renderer.selected=800;renderer.drawLabels();assert.deepEqual(drawn.map(row=>row.text),['Label 800']);
  drawn.length=0;renderer.camera.scale=10;renderer.drawLabels();assert.ok(drawn.length<=70);
  assert.equal(drawn[0].text,'Label 800');
});
