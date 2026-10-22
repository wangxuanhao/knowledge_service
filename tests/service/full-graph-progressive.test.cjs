const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function harness({badCount=false,noRelations=false,empty=false}={}){
  const calls=[],progress=[],renders=[];
  const nodes=[...(empty?[]:['a','b','c','isolated']).map(id=>({id,kind:'entity',text:id,version_id:'v-'+id}))];
  const relations=[{id:'r1',kind:'relation',subject_id:'a',object_id:'b'}, {id:'r2',kind:'relation',subject_id:'b',object_id:'c'}];
  const manifest={id:'session',entity_count:badCount?5:nodes.length,relation_count:noRelations||empty?0:2,watermark:1,layout_version:'stable-id-v1'};
  const context={window:{},AbortController,DOMException,console,performance,requestAnimationFrame:fn=>setImmediate(fn),
    fetch:async(url,options)=>{
      const body=JSON.parse(options.body);calls.push({url,body});
      let payload;
      if(!url.endsWith('/page'))payload=manifest;
      else if(body.kind==='connected'){
        const index=body.cursor?1:0;
        payload={session_id:'session',kind:'connected',records:[relations[index]],entities:index?[nodes[1],nodes[2]]:[nodes[0],nodes[1]],next_cursor:index?null:'relation-cursor',watermark:1,layout_version:'stable-id-v1'};
      }else payload={session_id:'session',kind:'entities',records:nodes,next_cursor:null,watermark:1,layout_version:'stable-id-v1'};
      return {ok:true,json:async()=>payload};
    }};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  const renderer={setDataset(data){this.dataset=data;},sync(){renders.push([this.dataset.nodeCount,this.dataset.edgeCount]);}};
  const loader=new context.window.FullGraph.SessionLoader(renderer,{onProgress:value=>progress.push(value),batchSize:400});
  return {loader,renderer,calls,progress,renders,context};
}

test('begin renders a connected first batch then pauses before fetching the corpus',async()=>{
  const {loader,calls,renders,progress}=harness();await loader.begin('p',{});
  assert.equal(calls.length,2);
  assert.equal(calls[1].body.kind,'connected');
  assert.ok(calls[1].body.limit<=500);
  assert.deepEqual(renders.at(-1),[2,1]);
  assert.equal(loader.state,'paused');
  assert.equal(progress.at(-1).stage,'paused');
  assert.equal(progress.some(value=>value.stage==='complete'),false);
});

test('manual next retains cursors and auto resume sweeps isolated entities before exact completion',async()=>{
  const {loader,calls,renders,progress}=harness();await loader.begin('p',{});await loader.next();
  assert.equal(calls[2].body.cursor,'relation-cursor');
  assert.equal(calls[2].body.kind,'connected');
  assert.deepEqual(renders.at(-1),[3,2]);
  assert.equal(loader.state,'paused');
  await loader.resume();
  assert.equal(calls[3].body.kind,'entities');
  assert.deepEqual(renders.at(-1),[4,2]);
  assert.equal(loader.dataset.records.filter(row=>row.id==='b').length,1);
  assert.equal(loader.state,'complete');
  assert.equal(progress.at(-1).stage,'complete');
});

test('pause preserves the session and cursor while an in-flight automatic batch finishes',async()=>{
  const {loader,calls,context,progress}=harness();await loader.begin('p',{});
  const original=context.fetch;let finish;
  context.fetch=(...args)=>new Promise(resolve=>{finish=async()=>resolve(await original(...args));});
  const automatic=loader.resume();await Promise.resolve();loader.pause();assert.equal(progress.at(-1).pause_requested,true);await finish();await automatic;
  assert.equal(loader.state,'paused');assert.equal(calls.length,3);assert.equal(loader.dataset.edgeCount,2);
  context.fetch=original;await loader.resume();assert.equal(loader.state,'complete');assert.equal(calls.length,4);
});

test('cancel discards a late page before it can change the rendered partial graph',async()=>{
  const {loader,context}=harness();await loader.begin('p',{});
  const original=context.fetch;let finish;
  context.fetch=(...args)=>new Promise(resolve=>{finish=async()=>resolve(await original(...args));});
  const pending=loader.next();await Promise.resolve();loader.cancel();await finish();await pending;
  assert.equal(loader.dataset.edgeCount,1);assert.equal(loader.state,'cancelled');
});

test('count mismatch never reports complete and compatibility load still performs the full sweep',async()=>{
  const bad=harness({badCount:true});
  await assert.rejects(()=>bad.loader.load('p',{}),/计数/);
  assert.equal(bad.progress.some(value=>value.stage==='complete'),false);
  const good=harness();await good.loader.load('p',{});assert.equal(good.loader.state,'complete');
});

for(const empty of [false,true])test('entity-only '+(empty?'empty':'isolated')+' graph completes without relation requests or stale automatic state',async()=>{
  const {loader,calls}=harness({noRelations:true,empty});await loader.load('p',{});
  assert.equal(calls.length,2);assert.equal(calls[1].body.kind,'entities');
  assert.equal(loader.state,'complete');assert.equal(loader.automatic,false);assert.equal(loader.automaticRun,null);
});

test('a mismatched snapshot page is rejected before any records reach the dataset',async()=>{
  const {loader,context}=harness();await loader.begin('p',{});
  const original=context.fetch;context.fetch=async(...args)=>{const response=await original(...args),payload=await response.json();payload.watermark=99;return {ok:true,json:async()=>payload};};
  await assert.rejects(()=>loader.next(),/会话/);assert.equal(loader.state,'error');assert.equal(loader.dataset.edgeCount,1);
});

// Exercise the renderer's real cache/draw methods through a small WebGL blend
// model. The representative pixel is on a line, away from entity points.
function drawingHarness(){
  const context={window:{},performance,console};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  const {Renderer,GraphDataset,Camera}=context.window.FullGraph;
  const gl={LINES:1,POINTS:2,TRIANGLE_STRIP:3,SRC_ALPHA:4,ONE_MINUS_SRC_ALPHA:5,ONE:6,FRAMEBUFFER:7,COLOR_BUFFER_BIT:8,
    bound:null,pixels:[0,0,0,0],cached:[0,0,0,0],color:[0,0,0,0],draws:[],uploads:[],sizes:[],
    bindFramebuffer(type,value){this.bound=value;},viewport(){},clearColor(...v){this.clearValue=v;},
    clear(){if(this.bound)this.cached=[...this.clearValue];else this.pixels=[...this.clearValue];},
    useProgram(){},enableVertexAttribArray(){},bindBuffer(type,value){this.buffer=value;},bufferData(type,value){this.uploads.push({buffer:this.buffer,length:value.length});},vertexAttribPointer(){},uniform2f(){},uniform1i(){},uniform1f(location,value){this.sizes.push(value);},activeTexture(){},bindTexture(){},generateMipmap(){},
    uniform4f(location,...v){this.color=v;},blendFunc(s,d){this.blend=[s,d,s,d];},blendFuncSeparate(...v){this.blend=v;},
    drawArrays(mode,first,count){this.draws.push({mode,first,count});if(!count||mode===this.POINTS)return;
      const src=mode===this.LINES?this.color:this.cached,dst=this.bound?this.cached:this.pixels;
      const factor=(f)=>f===this.ONE?1:f===this.SRC_ALPHA?src[3]:1-src[3];
      const result=src.map((v,i)=>Math.min(1,v*factor(this.blend[i===3?2:0])+dst[i]*factor(this.blend[i===3?3:1])));
      if(this.bound)this.cached=result;else this.pixels=result;
    }};
  const data=new GraphDataset();data.addNodes([{id:'a'},{id:'b'}]);data.addEdges([{id:'r1',subject_id:'a',object_id:'b'}]);
  const renderer=Object.assign(Object.create(Renderer.prototype),{gl,dataset:data,locations:{},cacheLocations:{},cacheFramebuffer:{},cacheSize:2048,
    program:{},cacheProgram:{},nodeBuffer:'nodes',edgeBuffer:'edges',width:800,height:600,canvas:{width:800,height:600},camera:new Camera(),showEdges:true,dpr:1,selected:-1,durations:[],drawLabels(){},requestDraw(){}});
  return {gl,renderer};
}

test('cached sparse relationship pixels darken the background instead of becoming white',()=>{
  const {gl,renderer}=drawingHarness();renderer.buildEdgeCache();renderer.draw();
  assert.ok(gl.cached[3]>.3,'a sparse relationship must retain useful alpha in the cache');
  assert.ok(gl.pixels[1]<.979-.1,'a visible relationship must darken green by at least 0.1');
  assert.ok(gl.pixels.every((value,i)=>i===3||value<[.967,.979,.971][i]));
  renderer.showEdges=false;renderer.draw();assert.deepEqual(gl.pixels,[.967,.979,.971,1]);
});

test('progressive cache appends new relations within an opacity bucket',()=>{
  const {gl,renderer}=drawingHarness();renderer.buildEdgeCache();
  renderer.dataset.addEdges([{id:'r2',subject_id:'b',object_id:'a'}]);renderer.buildEdgeCache();
  assert.deepEqual(gl.draws.filter(value=>value.mode===gl.LINES).map(value=>[value.first,value.count]),[[0,2],[2,2]]);
});

test('isolated entity sweep keeps the completed relationship layer without reuploading it',()=>{
  const {gl,renderer}=drawingHarness();renderer.sync();const drawCount=gl.draws.length;
  renderer.sync();assert.equal(gl.uploads.length,2);assert.equal(gl.draws.length,drawCount);
  renderer.dataset.addNodes([{id:'isolated'}]);renderer.sync();
  assert.equal(gl.uploads.length,3);assert.equal(gl.uploads.at(-1).buffer,'nodes');
  assert.equal(gl.draws.length,drawCount);
});

test('small partial graphs use readable five-pixel nodes while dense normal graphs retain two-pixel nodes',()=>{
  const {gl,renderer}=drawingHarness();renderer.draw();assert.equal(gl.sizes.at(-1),5);
  renderer.dataset.addNodes(Array.from({length:800},(_,i)=>({id:'extra'+i})));renderer.draw();
  assert.equal(gl.sizes.at(-1),2);
});
