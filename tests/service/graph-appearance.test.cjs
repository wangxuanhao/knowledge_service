const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function appearance(){
  const context={window:{},Map,Set,Uint8Array};vm.createContext(context);
  const web=path.join(__dirname,'../../knowledge_service/web');
  vm.runInContext(fs.readFileSync(path.join(web,'graph-palette.js'),'utf8'),context);
  const file=path.join(web,'graph-appearance.js');
  if(fs.existsSync(file))vm.runInContext(fs.readFileSync(file,'utf8'),context);
  assert.ok(context.window.GraphAppearance,'a shared graph appearance must be available to both renderers');
  return {subject:context.window.GraphAppearance,palette:context.window.GraphPalette};
}

test('canonical entity types retain the same color with translated names and partial/full records',()=>{
  const {subject,palette}=appearance();
  const full={id:'a',type:'urn:Merchant',text:'甲商户'};
  const local={...full,type_label:'商户',class_label:'商户新版'};
  assert.equal(subject.colorFor(local),subject.colorFor(full));
  assert.equal(subject.colorFor(local),palette.colorFor('urn:Merchant'));
  subject.colorFor({type:'urn:Other'});
  assert.equal(subject.colorFor({...local,type_label:'经营主体'}),subject.colorFor(full));
});

test('attribute projections and unknown types remain distinguishable from real entity types',()=>{
  const {subject,palette}=appearance();
  assert.equal(subject.colorFor({kind:'attribute_value',virtual:true,type:'KS_ATTRIBUTE_VALUE'}),palette.attribute);
  assert.equal(subject.colorFor({kind:'entity'}),palette.unknown);
  assert.equal(subject.colorFor({type:'urn:Person',type_label:'属性值'}),palette.colorFor('urn:Person'));
});

test('full renderer RGB colors exactly encode the same local graph type color',()=>{
  const {subject}=appearance();
  for(const type of ['urn:Merchant','Rule','Thing','']){
    const row={type},hex=subject.colorFor(row);
    assert.deepEqual(Array.from(subject.rgbFor(row)),[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)));
  }
});

test('legend category identity cannot collapse equal translated names or real attribute-named types',()=>{
  const {subject}=appearance();
  const a={type:'urn:Merchant',type_label:'共同类型'},b={type:'urn:Rule',type_label:'共同类型'};
  assert.notEqual(subject.categoryKey(a),subject.categoryKey(b));
  assert.equal(subject.categoryKey(a),subject.categoryKey({...a,type_label:'改名'}));
  assert.notEqual(subject.categoryKey({type:'urn:Property',type_label:'属性值'}),subject.categoryKey({kind:'attribute_value'}));
});

test('path names outside the ordinary budget receive priority without unbounded labels or phantom IDs',()=>{
  const {subject}=appearance();
  const nodes=Array.from({length:60},(_,i)=>({id:'n'+i,label:{show:i<50}}));
  const labels=subject.labelsFor(nodes,['n58','n59','missing'],50);
  assert.equal(labels.size,50);
  assert.equal(labels.has('n58'),true);assert.equal(labels.has('n59'),true);
  assert.equal(labels.has('missing'),false);
  assert.equal(nodes[58].label.show,false,'priority must not mutate base appearance used when clearing paths');
});

test('ordinary label candidates cover the graph rather than only the first dense center',()=>{
  const {subject}=appearance();
  const nodes=Array.from({length:200},(_,i)=>({id:'n'+i}));
  const labels=subject.labelsFor(nodes,['n0'],50);
  assert.equal(labels.has('n0'),true);
  assert.ok([...labels].some(id=>Number(id.slice(1))>=150));
  assert.equal(labels.size,50);
});

test('local name collision filtering preserves priority without covering another node',()=>{
  const {subject}=appearance();
  const nodes=[{id:'root',text:'根节点',x:50,y:50},{id:'crowded',text:'邻近节点',x:75,y:50},
    {id:'clear',text:'清晰节点',x:50,y:100},{id:'offscreen',text:'画布外',x:198,y:100}];
  const result=subject.avoidLabelOverlap(nodes,new Set(['root','crowded','clear','offscreen']),{
    project:n=>[n.x,n.y],measure:text=>text.length*12,width:200,height:150,size:()=>[20,20]});
  assert.equal(result.has('root'),false,'a normal name must not cover its neighboring node');
  assert.equal(result.has('crowded'),true);
  assert.equal(result.has('clear'),true);
  assert.equal(result.has('offscreen'),false);
  const overlapping=[{id:'a',text:'第一条名称',x:20,y:50},{id:'b',text:'第二条名称',x:30,y:50}];
  const priority=subject.avoidLabelOverlap(overlapping,new Set(['b','a']),{
    project:n=>[n.x,n.y],measure:()=>60,width:200,height:100,size:()=>[2,2]});
  assert.deepEqual([...priority],['b']);
});

test('real entity types named like projections never receive projection identity or its reserved color',()=>{
  const {subject,palette}=appearance();
  assert.notEqual(subject.colorFor({kind:'entity',type:'属性值'}),palette.attribute);
  assert.notEqual(subject.categoryKey({kind:'entity',type:'KS_ATTRIBUTE_VALUE'}),'attribute:');
  assert.equal(subject.categoryKey({virtual:true,type:'KS_ATTRIBUTE_VALUE'}),'attribute:');
});
