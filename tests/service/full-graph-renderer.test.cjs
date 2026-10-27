const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function load(withAppearance=false){
  const context={window:{},console,Float32Array,Uint8Array,Map,Set,Math,Array,Error,Uint32Array,performance:{now:()=>0},cancelAnimationFrame(){}};
  vm.createContext(context);
  if(withAppearance)for(const file of ['graph-palette.js','graph-appearance.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web',file),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  return {...context.window.FullGraph,appearance:context.window.GraphAppearance};
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

function assertSeparatedLabels(drawn,width,height){
  for(const row of drawn)assert.ok(row.x>=0&&row.x+row.width<=width&&row.y-12>=0&&row.y+4<=height,'every name fits inside the canvas');
  for(let i=0;i<drawn.length;i++)for(let j=i+1;j<drawn.length;j++){
    const a=drawn[i],b=drawn[j];
    assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+16<=b.y||b.y+16<=a.y,'names must not overlap');
  }
}

test('zoom labels reserve the selected name and reject colliding candidates',()=>{
  const {renderer,drawn}=labelScene(801);renderer.camera.scale=10;renderer.selected=800;
  // Many visible entities occupy the same screen area at close zoom.
  renderer.dataset.positions.fill(0);renderer.drawLabels();
  assert.equal(drawn[0].text,'Label 800');
  assert.equal(drawn.length,1,'the selected name reserves its rectangle before nearby entities');
  assertSeparatedLabels(drawn,renderer.width,renderer.height);
});

test('close zoom samples a bounded set of candidates in a large dataset',()=>{
  const {renderer,drawn}=labelScene(801);let projected=0;
  renderer.camera.scale=10;renderer.dataset={nodeCount:300000,positions:new Float32Array(600000).fill(5),records:[]};
  const project=renderer.camera.worldToScreen.bind(renderer.camera);
  renderer.camera.worldToScreen=(...args)=>{projected++;return project(...args);};renderer.drawLabels();
  assert.equal(drawn.length,0);assert.ok(projected<=12000,'label placement must not scan all entities');
});

test('selected labels remain within narrow canvas boundaries',()=>{
  const {renderer,drawn}=labelScene(1);renderer.width=180;renderer.height=90;renderer.selected=0;
  renderer.dataset.records[0].text='Selected entity with a name that exceeds this narrow canvas';
  renderer.dataset.positions.set([1,1]);renderer.drawLabels();
  assert.equal(drawn.length,1);assertSeparatedLabels(drawn,renderer.width,renderer.height);
});

test('the full renderer label toggle clears names including the selected entity',()=>{
  const {renderer,drawn}=labelScene(10);renderer.selected=9;renderer.showLabels=false;renderer.drawLabels();
  assert.equal(drawn.length,0);
});

test('display zoom uses the canvas center and preserves entity positions',()=>{
  const {renderer}=labelScene(10),before=Array.from(renderer.dataset.positions);let requested=0;
  renderer.requestDraw=()=>requested++;renderer.camera.x=.2;renderer.camera.y=-.1;
  assert.equal(typeof renderer.zoom,'function');renderer.zoom(1.5);
  assert.equal(renderer.camera.scale,1.5);assert.equal(renderer.camera.x,.2);assert.equal(renderer.camera.y,-.1);
  assert.deepEqual(Array.from(renderer.dataset.positions),before);assert.equal(requested,1);
});

// Record WebGL buffer ownership and validate enabled vertex arrays at draw time.
// Browser checks separately exercise the real GLSL pixels and CPU frame time.
function bufferGL(){
  let next=0,bound=null,program=null,copyError=0;
  const buffers=new Map(),attributes=new Map(),enabled=new Set(),uploads=[],draws=[],deleted=[],uniforms=new Map(),copies=[],deletedTextures=[];
  const gl={ARRAY_BUFFER:1,STATIC_DRAW:2,FLOAT:3,UNSIGNED_BYTE:4,LINES:5,POINTS:6,TRIANGLE_STRIP:7,VERTEX_SHADER:8,FRAGMENT_SHADER:9,COMPILE_STATUS:10,LINK_STATUS:11,MAX_TEXTURE_SIZE:12,FRAMEBUFFER:13,FRAMEBUFFER_COMPLETE:14,RENDERER:15,TRIANGLES:16,RGB:6407,RGBA:6408};
  Object.assign(gl,{
    createBuffer:()=>({id:++next}),bindBuffer:(_,buffer)=>{bound=buffer;},bufferData:(_,data)=>{buffers.set(bound,new data.constructor(data));uploads.push({buffer:bound,data:new data.constructor(data)});},deleteBuffer:buffer=>deleted.push(buffer),
    createShader:()=>({}),shaderSource(){},compileShader(){},getShaderParameter:()=>true,attachShader(){},linkProgram(){},deleteShader(){},getProgramParameter:()=>true,createProgram:()=>({id:++next}),useProgram:value=>{program=value;},
    getAttribLocation:(_,name)=>({a_position:0,a_target:1,a_corner:2})[name]??1,getUniformLocation:(_,name)=>name,enableVertexAttribArray:index=>enabled.add(index),disableVertexAttribArray:index=>enabled.delete(index),vertexAttrib3f(){},
    vertexAttribPointer:(index,size,type,normalized,stride,offset)=>attributes.set(index,{buffer:bound,size,type,normalized,stride,offset}),
    drawArrays:(mode,first,count)=>{
      if(count)for(const index of enabled){const attr=attributes.get(index),data=buffers.get(attr?.buffer),bytes=attr?.type===gl.UNSIGNED_BYTE?1:4;
        const stride=attr?.stride||attr?.size*bytes,required=attr?.offset+(first+count-1)*stride+attr?.size*bytes;
        assert.ok(data&&data.byteLength>=required,`attribute ${index} must cover all ${first+count} vertices`);
      }
      draws.push({program,mode,first,count,color:uniforms.get('u_color'),uniforms:new Map(uniforms),attributes:new Map(Array.from(enabled,index=>[index,{...attributes.get(index)}]))});
    },
    getParameter:name=>name===gl.MAX_TEXTURE_SIZE?2048:'recording GL',createTexture:()=>({id:++next}),createFramebuffer:()=>({}),checkFramebufferStatus:()=>gl.FRAMEBUFFER_COMPLETE,
    // Model the real alpha:false / ALPHA_BITS=0 framebuffer copy restriction.
    copyTexImage2D:(target,level,format,x,y,width,height)=>{copies.push({format,width,height});if(format!==gl.RGB)copyError=1282;},getError:()=>{const error=copyError;copyError=0;return error;}
  });
  for(const name of ['enable','blendFunc','blendFuncSeparate','bindTexture','texImage2D','texParameteri','bindFramebuffer','framebufferTexture2D','viewport','clearColor','clear','uniform1i','uniform1f','uniform2f','uniform3f','uniform4f','activeTexture','generateMipmap','deleteTexture','deleteFramebuffer','deleteProgram'])gl[name]=()=>{};
  gl.uniform4f=(location,...values)=>uniforms.set(location,values);
  gl.uniform1f=(location,value)=>uniforms.set(location,value);
  gl.uniform2f=(location,...values)=>uniforms.set(location,values);gl.deleteTexture=texture=>deletedTextures.push(texture);
  return {gl,buffers,uploads,draws,deleted,copies,deletedTextures};
}

function bufferScene(withAppearance=true){
  const {Renderer,GraphDataset,Camera}=load(withAppearance),recording=bufferGL();
  const renderer=Object.assign(Object.create(Renderer.prototype),{gl:recording.gl,dataset:new GraphDataset(),camera:new Camera(),selected:-1,showEdges:true,dpr:1,width:800,height:600,canvas:{width:800,height:600},durations:[],requestDraw(){},drawLabels(){}});
  renderer.setupGL();return {renderer,GraphDataset,...recording};
}

test('canonical type indexes count unique entities and index only within-type relationships',()=>{
  const {GraphDataset}=load(),data=new GraphDataset();
  const rows=[{id:'a',type:'urn:A',type_label:'同名类型'},{id:'b',type:'urn:B',type_label:'同名类型'},
    {id:'c',type:'urn:A'},{id:'unknown'}];
  data.addNodes(rows);data.addNodes(rows);
  const edges=[{id:'aa',subject_id:'a',object_id:'c'},{id:'ab',subject_id:'a',object_id:'b'},
    {id:'bb',subject_id:'b',object_id:'b'}];
  data.addEdges(edges);data.addEdges(edges);
  assert.deepEqual([...data.types.keys()],['urn:A','urn:B','']);
  assert.deepEqual(Array.from(data.types.get('urn:A').nodes),[0,2]);
  assert.deepEqual(Array.from(data.types.get('urn:A').edges),[0]);
  assert.deepEqual(Array.from(data.types.get('urn:B').edges),[2]);
  assert.equal([...data.types.values()].reduce((sum,g)=>sum+g.nodes.length,0),4);
});

test('an active type projection adds only new indexed members and preserves canonical identities and coordinates',()=>{
  const {GraphDataset,TypeProjection}=load(),data=new GraphDataset();
  const a={id:'a',type:'A'},b={id:'b',type:'B'},c={id:'c',type:'A'};
  data.addNodes([a,b],new Float32Array([-.5,.3,.6,-.4]));
  const view=new TypeProjection(data,'A');view.sync();
  assert.equal(view.dataset.records[0],a);assert.deepEqual(Array.from(view.dataset.positions),Array.from(data.positions.slice(0,2)));
  data.addNodes([c,{id:'d',type:'B'}],new Float32Array([.2,.8,.9,-.9]));
  const edge={id:'ac',subject_id:'a',object_id:'c'};data.addEdges([edge,{id:'ab',subject_id:'a',object_id:'b'}]);
  let oldReads=0;data.records=new Proxy(data.records,{get(target,key){if(/^\d+$/.test(String(key))&&Number(key)<2)oldReads++;return Reflect.get(target,key);}});
  view.sync();assert.equal(oldReads,0,'appending must not scan previously indexed entity records');
  assert.deepEqual(Array.from(view.dataset.records,row=>row.id),['a','c']);assert.equal(view.dataset.edges[0],edge);
  assert.deepEqual(Array.from(view.dataset.positions),[-.5,Math.fround(.3),Math.fround(.2),Math.fround(.8)]);
  const positions=view.dataset.positions;view.sync();assert.equal(view.dataset.positions,positions,'unchanged frames must not copy projection buffers');
});

test('rendered type selection filters drawing picking locating and caches while retaining the full loading dataset',()=>{
  const {renderer:r}=bufferScene(),source=r.dataset;
  source.addNodes([{id:'a',type:'A'},{id:'b',type:'B'},{id:'c',type:'A'}],new Float32Array([-.6,0,.6,0,0,.6]));
  source.addEdges([{id:'ac',subject_id:'a',object_id:'c'},{id:'ab',subject_id:'a',object_id:'b'}]);r.sync();r.draw();
  r.onSelect=()=>{};r.setTypeFilter('A');r.draw();
  assert.equal(r.dataset,source);assert.equal(source.nodeCount,3);assert.equal(source.edgeCount,2);
  assert.deepEqual(Array.from(r.displayDataset.records,n=>n.id),['a','c']);
  assert.equal(r.stats().loaded_entities,3);assert.equal(r.stats().drawn_entities,2);assert.equal(r.stats().drawn_relations,1);
  assert.equal(r.cachedDataset,r.displayDataset);assert.equal(r.cachedRelations,1,'a type view cannot reuse the complete relationship layer');
  assert.equal(r.locate('b'),false);assert.equal(r.pick(...r.camera.worldToScreen(.6,0,r.width,r.height)),null);
  assert.equal(r.pick(...r.camera.worldToScreen(-.6,0,r.width,r.height)),source.records[0]);
  source.addNodes([{id:'d',type:'A'},{id:'hidden',type:'B'}]);source.addEdges([{id:'ad',subject_id:'a',object_id:'d'}]);r.sync();r.draw();
  assert.equal(r.displayDataset.nodeCount,3);assert.equal(r.stats().loaded_entities,5);assert.equal(r.stats().drawn_relations,2);
  r.setTypeFilter(null);r.draw();assert.equal(r.displayDataset,source);assert.equal(r.stats().drawn_entities,5);assert.equal(r.cachedRelations,3);
  r.setTypeFilter('missing');r.draw();assert.equal(r.stats().drawn_entities,0);assert.equal(r.stats().drawn_relations,0);assert.equal(r.locate('a'),false);
  r.setDataset(new (load().GraphDataset)());assert.equal(r.typeFilter,null);assert.equal(r.displayDataset.nodeCount,0);
});

test('unknown entity type selection remains separate from all types and bounded labels cannot expose hidden entities',()=>{
  const {renderer:r}=bufferScene(),source=r.dataset,drawn=[];
  source.addNodes([{id:'unknown',text:'未知'},{id:'typed',type:'A',text:'隐藏实体'}],new Float32Array([0,0,.5,.5]));r.sync();r.setTypeFilter('');
  r.labelContext={setTransform(){},clearRect(){},measureText:text=>({width:text.length*12}),fillText:text=>drawn.push(text)};
  r.drawLabels=load().Renderer.prototype.drawLabels;r.drawLabels();
  assert.deepEqual(drawn,['未知']);assert.equal(r.displayDataset.nodeCount,1);r.setTypeFilter(null);assert.equal(r.displayDataset.nodeCount,2);
});

test('switching equal-sized dense types invalidates the previous complete overview and relation textures',()=>{
  const {renderer:r}=bufferScene(),source=r.dataset;
  source.addNodes(Array.from({length:10000},(_,i)=>({id:'typed-'+i,type:i<5000?'A':'B'})));
  source.addEdges([{id:'aa',subject_id:'typed-0',object_id:'typed-1'},{id:'bb',subject_id:'typed-5000',object_id:'typed-5001'}]);
  r.sync();r.draw();r.draw();assert.ok(r.viewSnapshot);const all=r.viewSnapshot;
  r.setTypeFilter('A');assert.equal(r.viewSnapshot,null);r.draw();r.draw();const a=r.viewSnapshot;
  assert.ok(a&&a.dataset!==all.dataset);assert.equal(r.cachedRelations,1);
  r.setTypeFilter('B');assert.equal(r.viewSnapshot,null);r.draw();r.draw();assert.ok(r.viewSnapshot);
  assert.notEqual(r.viewSnapshot.dataset,a.dataset);assert.equal(r.displayDataset.edges[0].id,'bb');
  r.setTypeFilter(null);r.draw();assert.equal(r.displayDataset,source);assert.equal(r.cachedRelations,2);
});

function smallRelationshipScene(){
  const scene=bufferScene(),{renderer}=scene;
  renderer.width=1100;renderer.height=600;renderer.canvas.width=1100;
  renderer.dataset.addNodes(Array.from({length:73},(_,i)=>({id:'entity-'+i,text:'Entity '+i,type:'Type'+i%3})));
  const pairs=[...Array.from({length:24},(_,i)=>[0,i+1]),...Array.from({length:14},(_,i)=>[25,i+26]),...Array.from({length:7},(_,i)=>[40+i,41+i])];
  renderer.dataset.addEdges(pairs.map(([a,b],i)=>({id:'relation-'+i,subject_id:'entity-'+a,object_id:'entity-'+b})));
  renderer.setSessionManifest?.({id:'small-session',entity_count:73,relation_count:45});renderer.sync();return scene;
}

test('known small full graphs show readable nodes and nearby related entities at overview scale',()=>{
  const {renderer,draws}=smallRelationshipScene();renderer.draw();
  const sizes=draws.filter(row=>row.mode===renderer.gl.POINTS).map(row=>row.uniforms.get('u_size'));
  assert.ok(sizes.length&&sizes.every(size=>size>=14),'73-entity overview nodes must be readable circles, not 5px dots');
  const positions=renderer.renderPositions||renderer.dataset.positions,unit=renderer.camera.unit(renderer.width,renderer.height);
  const lengths=renderer.dataset.edges.map(edge=>{const a=renderer.dataset.nodeIndex.get(edge.subject_id)*2,b=renderer.dataset.nodeIndex.get(edge.object_id)*2;return Math.hypot(positions[a]-positions[b],positions[a+1]-positions[b+1])*unit;});
  assert.ok(lengths.reduce((sum,value)=>sum+value,0)/lengths.length<130,'relationship layout must replace the long hash-scattered edges');
  let closest=Infinity;for(let a=0;a<73;a++)for(let b=a+1;b<73;b++)closest=Math.min(closest,Math.hypot(positions[a*2]-positions[b*2],positions[a*2+1]-positions[b*2+1])*unit);
  assert.ok(closest>=20,'overview entities need enough separation to remain individually visible: '+closest);
  const stats=renderer.stats();assert.equal(stats.layout,'small-relations-v1');assert.equal(stats.canonical_layout,'stable-id-v1');assert.equal(stats.drawn_entities,73);assert.equal(stats.drawn_relations,45);
});

test('sparse node and selected ring sprites reserve one physical pixel outside each circular edge',()=>{
  for(const dpr of [1,1.25,2]){
    const {renderer,draws}=smallRelationshipScene();renderer.dpr=dpr;renderer.selected=0;renderer.draw();
    const points=draws.filter(row=>row.mode===renderer.gl.POINTS),normal=points[0],ring=points.at(-1);
    assert.equal(normal.uniforms.get('u_size'),16*dpr+2,'node antialias coverage must fit inside its sprite at DPR '+dpr);
    assert.equal(ring.uniforms.get('u_size'),24*dpr+2,'the selected ring needs the same unclipped physical-pixel margin');
  }
});

test('known small relationships use bounded screen-space stroke geometry and preserve selected edge identity',()=>{
  const {renderer,draws,uploads,buffers}=smallRelationshipScene();renderer.selectedEdge=7;renderer.draw();
  const strokes=draws.filter(row=>row.program===renderer.strokeProgram);
  assert.equal(strokes.length,2,'the relation layer and selected relation use coverage strokes');
  assert.equal(strokes[0].count,45*6);assert.equal(strokes[1].first,7*6);assert.equal(strokes[1].count,6);
  assert.ok(strokes.every(row=>row.mode===renderer.gl.TRIANGLES));
  const edge=renderer.dataset.edges[7],a=renderer.dataset.nodeIndex.get(edge.subject_id)*2,b=renderer.dataset.nodeIndex.get(edge.object_id)*2,positions=renderer.renderPositions;
  assert.deepEqual(Array.from(buffers.get(renderer.strokeBuffer).slice(7*36,7*36+4)),[positions[a],positions[a+1],positions[b],positions[b+1]],'strokes connect the display positions of the exact existing relationship');
  const count=uploads.length;renderer.draw();assert.equal(uploads.length,count,'camera frames reuse relation stroke geometry');
});

test('small relationship coverage buffers recover after context restoration and are disposed',()=>{
  const {renderer,deleted,buffers}=smallRelationshipScene(),old=renderer.strokeBuffer;
  assert.equal(buffers.get(old).byteLength,45*6*6*4,'stroke data is bounded by six vertices per relationship');
  renderer.setupGL();renderer.sync();assert.notEqual(renderer.strokeBuffer,old);assert.equal(buffers.get(renderer.strokeBuffer).byteLength,45*6*6*4);
  const current=renderer.strokeBuffer;renderer.abort={abort(){}};renderer.observer={disconnect(){}};renderer.host={};renderer.pendingFrame=0;renderer.destroy();
  assert.ok(deleted.includes(current),'destroy releases the optional small relationship buffer');
});

test('small relationship layout is deterministic, display-only, and used by picking and locating',()=>{
  const {renderer}=smallRelationshipScene(),other=smallRelationshipScene().renderer,canonical=Array.from(renderer.dataset.positions);
  assert.notEqual(renderer.renderPositions,renderer.dataset.positions,'display layout keeps immutable session positions intact');
  assert.deepEqual(Array.from(renderer.renderPositions),Array.from(other.renderPositions));
  const index=10,x=renderer.renderPositions[index*2],y=renderer.renderPositions[index*2+1];renderer.onSelect=()=>{};
  assert.equal(renderer.pick(...renderer.camera.worldToScreen(x,y,renderer.width,renderer.height)),renderer.dataset.records[index]);
  assert.equal(renderer.locate('entity-10'),true);assert.equal(renderer.camera.x,x);assert.equal(renderer.camera.y,y);
  assert.deepEqual(Array.from(renderer.dataset.positions),canonical);
});

test('small first pages of large or unknown sessions retain the original positions and point path',()=>{
  for(const manifest of [null,{id:'large-session',entity_count:300000,relation_count:1500000}]){
    const {renderer}=bufferScene();renderer.dataset.addNodes(Array.from({length:73},(_,i)=>({id:'entity-'+i})));
    const canonical=renderer.dataset.positions,before=Array.from(canonical);if(manifest)renderer.setSessionManifest?.(manifest);renderer.sync();
    assert.equal(renderer.renderPositions||canonical,canonical);assert.deepEqual(Array.from(canonical),before);
    assert.equal(renderer.strokeProgram,null,'unknown and large sessions do not create the small relation shader');
    assert.equal(renderer.strokeBuffer,null,'unknown and large sessions do not upload the small relation geometry');
  }
});

test('small overview labels use the relationship positions and keep useful names without collisions',()=>{
  const {renderer}=smallRelationshipScene(),drawn=[];
  renderer.labelContext={setTransform(){},clearRect(){},measureText:text=>({width:text.length*6}),fillText(text,x,y){drawn.push({text,x,y,width:text.length*6});}};
  renderer.drawLabels=load().Renderer.prototype.drawLabels;renderer.selected=72;renderer.drawLabels();
  assert.equal(drawn[0].text,'Entity 72');assert.ok(drawn.length>=40,'known small sessions should show most of their names: '+drawn.length);
  assertSeparatedLabels(drawn,renderer.width,renderer.height);
  drawn.length=0;renderer.showLabels=false;renderer.drawLabels();assert.equal(drawn.length,0);
});

test('selected small-graph entity keeps its full law name at overview scale',()=>{
  const {renderer}=smallRelationshipScene(),drawn=[],name='《中华人民共和国电子商务法》';
  renderer.dataset.records[72].text=name;renderer.selected=72;
  renderer.labelContext={setTransform(){},clearRect(){},measureText:text=>({width:text.length*12}),fillText(text){drawn.push(text);}};
  renderer.drawLabels=load().Renderer.prototype.drawLabels;renderer.drawLabels();
  assert.equal(drawn[0],name,'the selected law must remain readable instead of being shortened like nearby labels');
});

test('small display layout refreshes geometry on append and canvas resize without moving canonical positions',()=>{
  const {renderer,buffers,uploads}=smallRelationshipScene(),canonical=Array.from(renderer.dataset.positions),first=renderer.renderPositions;
  renderer.sync();assert.equal(renderer.renderPositions,first,'camera/stable sync never runs layout again');
  renderer.dataset.addEdges([{id:'last-relation',subject_id:'entity-1',object_id:'entity-25'}]);renderer.sync();
  assert.notEqual(renderer.renderPositions,first,'a new relationship updates the small graph topology');
  assert.deepEqual(Array.from(buffers.get(renderer.nodeBuffer)),Array.from(renderer.renderPositions));
  assert.deepEqual(Array.from(buffers.get(renderer.edgeBuffer)),Array.from(renderer.dataset.edgePositions(renderer.renderPositions)));
  const afterAppend=renderer.renderPositions;renderer.width=800;renderer.height=400;renderer.sync();
  assert.notEqual(renderer.renderPositions,afterAppend);assert.deepEqual(Array.from(renderer.dataset.positions),canonical);
  const count=uploads.length;renderer.draw();renderer.draw();assert.equal(uploads.length,count,'camera frames do not regenerate the small display layout');
});

test('small graph topology is independent of node and relationship page order',()=>{
  const {renderer,GraphDataset}=smallRelationshipScene(),other=bufferScene().renderer,data=new GraphDataset();
  data.addNodes([...renderer.dataset.records].reverse());data.addEdges([...renderer.dataset.edges].reverse());other.width=1100;other.height=600;other.setDataset(data);
  other.setSessionManifest({id:'same-scope',entity_count:73,relation_count:45});other.sync();
  for(const [id,a] of renderer.dataset.nodeIndex){const b=data.nodeIndex.get(id);assert.deepEqual(Array.from(renderer.renderPositions.slice(a*2,a*2+2)),Array.from(other.renderPositions.slice(b*2,b*2+2)));}
});

function assertGroupedNodes(renderer,buffers,draws,appearance){
  const points=draws.filter(row=>row.mode===renderer.gl.POINTS),expected=new Map(),seen=new Set();
  renderer.dataset.records.forEach((record,i)=>expected.set(Array.from(renderer.dataset.positions.slice(i*2,i*2+2)).join(','),Array.from(appearance.rgbFor(record),value=>value/255)));
  assert.ok(points.length>0&&points.length<=18,'the palette bounds normal node draw calls');
  assert.equal(points.reduce((sum,row)=>sum+row.count,0),renderer.dataset.nodeCount,'each entity is drawn exactly once');
  for(const draw of points){
    assert.equal(draw.attributes.size,1,'the simple shader only needs positions');
    const attribute=draw.attributes.values().next().value;assert.equal(attribute.buffer,renderer.groupedNodeBuffer);
    const positions=buffers.get(attribute.buffer);
    for(let i=draw.first;i<draw.first+draw.count;i++){
      const key=Array.from(positions.slice(i*2,i*2+2)).join(',');
      assert.ok(expected.has(key)&&!seen.has(key),'grouping preserves each original entity position exactly once');
      assert.deepEqual(draw.color,[...expected.get(key),1]);seen.add(key);
    }
  }
}

test('bounded uniform-color groups preserve canonical colors through append and dataset replacement',()=>{
  const {renderer,GraphDataset,buffers,uploads,draws}=bufferScene(),{appearance}=load(true);
  renderer.dataset.addNodes([{id:'a',type:'Person',type_label:'人'},{id:'b',type:'Company',type_label:'企业'}]);renderer.sync();
  assert.ok(renderer.nodeRanges?.length,'sync prepares bounded color ranges');
  renderer.draw();assertGroupedNodes(renderer,buffers,draws,appearance);draws.length=0;
  assert.deepEqual(Array.from(buffers.get(renderer.nodeBuffer)),Array.from(renderer.dataset.positions),'selection keeps the original record-order position buffer');
  renderer.dataset.addNodes([{id:'c',type:'Person'}]);renderer.sync();
  const before=Array.from(renderer.dataset.positions),uploadCount=uploads.length;renderer.draw();assertGroupedNodes(renderer,buffers,draws,appearance);draws.length=0;
  renderer.draw();assert.equal(uploads.length,uploadCount,'camera draws reuse grouped position uploads');draws.length=0;
  assert.deepEqual(Array.from(renderer.dataset.positions),before);
  const replacement=new GraphDataset();replacement.addNodes([{id:'x',type:'Company'},{id:'y',type:'Person'},{id:'z',type:'Company'}]);renderer.setDataset(replacement);
  assert.deepEqual(Array.from(buffers.get(renderer.nodeBuffer)),Array.from(replacement.positions));
  renderer.draw();assertGroupedNodes(renderer,buffers,draws,appearance);
});

test('dense palette groups stay bounded and selection retains original record indices',()=>{
  const {renderer,buffers,draws}=bufferScene(),{appearance}=load(true),types=['','KS_ATTRIBUTE_VALUE',...Array.from({length:128},(_,i)=>'Type'+i)];
  renderer.dataset.addNodes(Array.from({length:1200},(_,i)=>({id:'node'+i,type:types[i%types.length]})),new Float32Array(Array.from({length:2400},(_,i)=>i/2400)));
  renderer.sync();renderer.draw();assertGroupedNodes(renderer,buffers,draws,appearance);
  assert.ok(draws.filter(row=>row.mode===renderer.gl.POINTS).every(row=>row.program===renderer.program),'dense normal points use the simple program');
  renderer.dataset.records=new Proxy(renderer.dataset.records,{get(target,key,receiver){if(/^\d+$/.test(String(key)))throw new Error('camera frames cannot read per-node styles');return Reflect.get(target,key,receiver);}});
  draws.length=0;renderer.selected=1199;renderer.draw();
  const ring=draws.filter(row=>row.mode===renderer.gl.POINTS).at(-1);
  assert.equal(ring.first,1199);assert.equal(ring.count,1);assert.equal(ring.program,renderer.pointProgram);
  assert.equal(ring.attributes.get(renderer.pointLocations.position).buffer,renderer.nodeBuffer);
});

test('line and cached relation draws do not read beyond grouped entity positions',()=>{
  const {renderer,GraphDataset,draws}=bufferScene();renderer.dataset.addNodes([{id:'a',type:'Person'},{id:'b',type:'Company'}]);
  renderer.dataset.addEdges(Array.from({length:5001},(_,i)=>({id:'r'+i,subject_id:'a',object_id:'b'})));
  renderer.sync();renderer.draw();
  const normal=draws.filter(row=>row.mode===renderer.gl.POINTS);
  assert.equal(normal.reduce((sum,row)=>sum+row.count,0),2,'normal color groups retain every entity');
  assert.ok(normal.every(row=>row.attributes.size===1),'relations and nodes require only a position attribute');
  renderer.showEdges=false;renderer.draw();
  const sparse=new GraphDataset();sparse.addNodes([{id:'a',type:'Person'},{id:'b',type:'Company'}]);
  sparse.addEdges(Array.from({length:3},(_,i)=>({id:'s'+i,subject_id:'a',object_id:'b'})));
  renderer.setDataset(sparse);renderer.showEdges=true;renderer.draw();
});

for(const edgeCount of [3,5001])test('relation picking highlights only its existing edge vertices with '+edgeCount+' relations',()=>{
  const {renderer,GraphDataset,draws}=bufferScene(),selections=[];
  renderer.onSelect=record=>selections.push(record);
  const nodes=[{id:'a',type:'Person'},{id:'b',type:'Person'},{id:'c',type:'Company'},{id:'d',type:'Company'}];
  renderer.dataset.addNodes(nodes,new Float32Array([-.8,0,.8,0,-.8,.6,.8,.6]));
  const relation={id:'picked-relation',subject_id:'a',object_id:'b',type:'knows'};
  renderer.dataset.addEdges(Array.from({length:edgeCount},(_,i)=>i===1?relation:{id:'other-'+i,subject_id:'c',object_id:'d'}));renderer.sync();draws.length=0;
  let edgeReads=0;renderer.dataset.edges=new Proxy(renderer.dataset.edges,{get(target,key,receiver){
    if(['indexOf','find','findIndex'].includes(key))throw new Error('selection must keep the winning edge index during its existing scan');
    if(/^\d+$/.test(String(key)))edgeReads++;return Reflect.get(target,key,receiver);
  }});
  const pick=(x,y)=>renderer.pick(...renderer.camera.worldToScreen(x,y,renderer.width,renderer.height));
  assert.equal(pick(0,0),relation);assert.equal(selections.at(-1),relation);assert.equal(renderer.selected,-1);assert.equal(renderer.selectedEdge,1);
  assert.equal(edgeReads,edgeCount,'relation selection uses its existing single candidate scan');
  renderer.draw();assert.equal(edgeReads,edgeCount,'highlight drawing does not rescan relation records');
  const lines=draws.filter(row=>row.mode===renderer.gl.LINES),overlay=lines.at(-1);
  assert.equal(lines.length,edgeCount>4000?1:2);assert.equal(overlay.first,2);assert.equal(overlay.count,2);
  assert.equal(overlay.attributes.get(renderer.locations.position).buffer,renderer.edgeBuffer);
  assert.equal(overlay.attributes.size,1);
  assert.deepEqual(overlay.color,[207/255,135/255,55/255,1]);
  assert.ok(draws.indexOf(overlay)<draws.findIndex(row=>row.mode===renderer.gl.POINTS),'nodes render above the relation overlay');
  draws.length=0;renderer.showEdges=false;renderer.draw();assert.equal(draws.filter(row=>row.mode===renderer.gl.LINES).length,0);
  renderer.showEdges=true;assert.equal(pick(-.8,0),nodes[0]);assert.equal(renderer.selectedEdge,-1);
  pick(0,0);assert.equal(pick(-1.2,-.8),null);assert.equal(renderer.selectedEdge,-1);
  pick(0,0);renderer.locate('a');assert.equal(renderer.selectedEdge,-1);
  renderer.camera.scale=1;renderer.camera.x=0;renderer.camera.y=0;pick(0,0);
  const replacement=new GraphDataset();replacement.addNodes(nodes);renderer.setDataset(replacement);assert.equal(renderer.selectedEdge,-1);
});

test('context restoration refreshes grouped positions and destroy disposes their buffer',()=>{
  const {renderer,buffers,deleted}=bufferScene();renderer.dataset.addNodes([{id:'a',type:'Person'}]);renderer.sync();
  const before=renderer.groupedNodeBuffer;assert.ok(buffers.get(before) instanceof Float32Array);
  const expected=Array.from(buffers.get(before));renderer.setupGL();renderer.sync();
  assert.notEqual(renderer.groupedNodeBuffer,before);assert.deepEqual(Array.from(buffers.get(renderer.groupedNodeBuffer)),expected);
  const current=renderer.groupedNodeBuffer;renderer.abort={abort(){}};renderer.observer={disconnect(){}};renderer.host={};renderer.pendingFrame=0;
  renderer.destroy();
  assert.ok(deleted.includes(current),'the grouped position buffer belongs to the renderer lifecycle');
});

test('a renderer loaded without appearance helpers still prepares usable color groups',()=>{
  const {renderer,buffers}=bufferScene(false);renderer.dataset.addNodes([{id:'a',type:'Person'}]);renderer.sync();
  assert.ok(buffers.get(renderer.groupedNodeBuffer) instanceof Float32Array);assert.equal(renderer.nodeRanges.length,1);
});

function overviewScene({outlier=false,extent=.8}={}){
  const scene=bufferScene(),{renderer}=scene,positions=new Float32Array(10000);
  for(let i=0;i<5000;i++){positions[i*2]=(i%100)/99*extent*2-extent;positions[i*2+1]=Math.floor(i/100)/49*extent*2-extent;}
  if(outlier)positions[0]=4;
  renderer.dataset.addNodes(Array.from({length:5000},(_,i)=>({id:'overview'+i,type:'Type'+i%3})),positions);
  renderer.dataset.addEdges([{id:'overview-edge',subject_id:'overview0',object_id:'overview4999'}]);renderer.sync();return scene;
}

function warmOverview(scene){scene.renderer.draw();scene.renderer.draw();scene.draws.length=0;}

test('complete overview cache reuses one affine quad with exact dataset counts',()=>{
  const scene=overviewScene(),{renderer,copies,draws}=scene;renderer.draw();assert.equal(copies.length,0,'append frames avoid copying a new bitmap each batch');
  renderer.draw();assert.equal(copies.length,1,'a stable complete overview is copied once');draws.length=0;
  const original=new (load().Camera)();renderer.camera.pan(12,6,800,600);renderer.camera.zoomAt(1.2,400,300,800,600);renderer.draw();
  assert.equal(draws.length,1);assert.equal(draws[0].program,renderer.viewProgram);assert.equal(copies.length,1);
  const scale=draws[0].uniforms.get('u_scale'),offset=draws[0].uniforms.get('u_offset');
  for(const [x,y] of [[-.8,-.8],[.35,.6],[.8,.8]]){
    const [oldX,oldY]=original.worldToScreen(x,y,800,600),[expectedX,expectedY]=renderer.camera.worldToScreen(x,y,800,600);
    const nextX=(((oldX/800*2-1)*scale[0]+offset[0])+1)*400,nextY=(1-((1-oldY/600*2)*scale[1]+offset[1]))*300;
    assert.ok(Math.abs(nextX-expectedX)<1e-5&&Math.abs(nextY-expectedY)<1e-5,'cached pixels follow the camera world mapping');
  }
  const stats=renderer.stats();assert.equal(stats.node_layer,'complete-view-cache');assert.equal(stats.cached_entities,5000);
  assert.equal(stats.drawn_entities,5000);assert.equal(stats.drawn_relations,1);
  renderer.dataset.records=new Proxy(renderer.dataset.records,{get(target,key,receiver){if(/^\d+$/.test(String(key)))throw new Error('overview frames cannot scan records');return Reflect.get(target,key,receiver);}});
  renderer.draw();assert.equal(copies.length,1);
});

test('opaque overview copies use a compatible RGB destination format',()=>{
  const scene=overviewScene(),{renderer,gl,copies}=scene;warmOverview(scene);
  assert.equal(copies[0].format,gl.RGB,'alpha:false framebuffer cannot copy into RGBA');
  assert.equal(renderer.stats().view_cache_error,0);renderer.draw();assert.equal(renderer.stats().node_layer,'complete-view-cache');
});

test('a cropped node, deep zoom, or oversized canvas always keeps exact live groups',()=>{
  for(const kind of ['cropped','deep','size','unsupported']){
    const scene=overviewScene({outlier:kind==='cropped'}),{renderer,gl,draws,copies}=scene;
    if(kind==='deep')renderer.camera.scale=10;
    if(kind==='size')renderer.canvas.width=2049;
    if(kind==='unsupported')gl.copyTexImage2D=undefined;
    renderer.draw();renderer.draw();draws.length=0;renderer.draw();
    assert.equal(copies.length,0,kind+' must not capture an incomplete or unsupported scene');
    assert.ok(draws.some(row=>row.mode===gl.POINTS));assert.equal(renderer.stats().node_layer,'live-color-groups');
  }
});

test('actual short graph canvases can cache the full stable layout and selected ring',()=>{
  for(const height of [280,400])for(const selected of [-1,0]){
    const scene=overviewScene({extent:.95}),{renderer,copies,draws}=scene;renderer.height=height;renderer.canvas.height=height;renderer.selected=selected;
    warmOverview(scene);assert.equal(copies.length,1,'all centers and the selected ring fit at '+height+'px');renderer.draw();
    assert.equal(draws.length,1);assert.equal(draws[0].program,renderer.viewProgram);
  }
});

test('overview texture allocation errors disable only the optional cache',()=>{
  const scene=overviewScene(),{renderer,gl,draws,copies}=scene;gl.getError=()=>1285;warmOverview(scene);renderer.draw();
  assert.equal(copies.length,1);assert.ok(draws.some(row=>row.mode===gl.POINTS));assert.equal(renderer.stats().cached_entities,0);
  assert.equal(renderer.stats().node_layer,'live-color-groups');assert.equal(renderer.stats().view_cache_error,1285);
});

test('overview cache invalidates selection, edges, dimensions, append and replacement',()=>{
  for(const change of ['node','edge','visibility','size','dpr','append','relation','replace','restore']){
    const scene=overviewScene(),{renderer,draws,GraphDataset,copies}=scene;warmOverview(scene);assert.equal(copies.length,1);
    if(change==='node')renderer.selected=5;
    if(change==='edge')renderer.selectedEdge=0;
    if(change==='visibility')renderer.showEdges=false;
    if(change==='size')renderer.canvas.width=801;
    if(change==='dpr')renderer.dpr=2;
    if(change==='append'){renderer.dataset.addNodes([{id:'new',type:'Type1'}],new Float32Array([4,0]));renderer.sync();}
    if(change==='relation'){renderer.dataset.addEdges([{id:'new-edge',subject_id:'overview1',object_id:'overview2'}]);renderer.sync();}
    if(change==='replace'){const d=new GraphDataset();d.addNodes(renderer.dataset.records,renderer.dataset.positions);renderer.setDataset(d);}
    if(change==='restore'){renderer.setupGL();renderer.sync();}
    renderer.draw();assert.ok(draws.some(row=>row.mode===renderer.gl.POINTS),change+' must render live on invalidation');
    assert.equal(renderer.stats().node_layer,'live-color-groups');assert.equal(renderer.stats().cached_entities,0);
  }
});

test('deep zoom falls back live and the complete cached overview can be reused on return',()=>{
  const scene=overviewScene(),{renderer,draws}=scene;warmOverview(scene);renderer.camera.scale=8;renderer.draw();
  assert.ok(draws.some(row=>row.mode===renderer.gl.POINTS));draws.length=0;renderer.camera.scale=1;renderer.draw();
  assert.equal(draws.length,1);assert.equal(draws[0].program,renderer.viewProgram);
});

test('destroy releases the bounded overview texture',()=>{
  const scene=overviewScene(),{renderer,deletedTextures}=scene;warmOverview(scene);const texture=renderer.viewTexture;
  assert.ok(texture);renderer.abort={abort(){}};renderer.observer={disconnect(){}};renderer.host={};renderer.pendingFrame=0;renderer.destroy();
  assert.ok(deletedTextures.includes(texture));
});
