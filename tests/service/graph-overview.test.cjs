const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

// The coordinator is exercised with the real session loader and transport shape.
// Only the renderer (a WebGL surface) is replaced in this non-browser cohort.
function harness({entityCount=4,relationCount=2,failPage=0,mismatch=false}={}){
  const calls=[],states=[],renders=[];
  let current={projectId:'p',scope:{known_at:'2026-01-01T00:00:00Z'}},ready=false,visible=true,pages=0;
  const nodes=Array.from({length:entityCount},(_,i)=>({id:'n'+i,kind:'entity',type:'Thing',version_id:'v'+i}));
  const edges=Array.from({length:relationCount},(_,i)=>({id:'r'+i,kind:'relation',type:'knows',subject_id:'n'+(i%Math.max(1,entityCount)),object_id:'n'+((i+1)%Math.max(1,entityCount)),version_id:'rv'+i}));
  const context={window:{},AbortController,DOMException,console,performance,requestAnimationFrame:fn=>setImmediate(fn),
    fetch:async(url,options)=>{
      const body=JSON.parse(options.body);calls.push({url,body,signal:options.signal});
      const project=decodeURIComponent(url.split('/projects/')[1].split('/')[0]),id='session-'+project;
      const base={session_id:id,watermark:1,layout_version:'stable-id-v1'};
      if(!url.endsWith('/page'))return {ok:true,json:async()=>({id,entity_count:entityCount+(mismatch?1:0),relation_count:relationCount,watermark:1,layout_version:base.layout_version})};
      pages++;if(pages===failPage)return {ok:false,json:async()=>({detail:'PG page unavailable'})};
      const offset=Number(body.cursor||0),all=body.kind==='connected'?edges:nodes,records=all.slice(offset,offset+body.limit);
      const entities=nodes.filter(node=>records.some(edge=>edge.subject_id===node.id||edge.object_id===node.id));
      return {ok:true,json:async()=>({...base,kind:body.kind,records,entities,next_cursor:offset+body.limit<all.length?String(offset+body.limit):null})};
    }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  const overviewPath=path.join(__dirname,'../../knowledge_service/web/graph-overview.js');
  if(fs.existsSync(overviewPath))vm.runInContext(fs.readFileSync(overviewPath,'utf8'),context);
  assert.equal(typeof context.window.GraphOverview?.create,'function','bounded graph overview coordinator is available');
  const renderer={setDataset(data){this.dataset=data;},sync(){renders.push([this.dataset.nodeCount,this.dataset.edgeCount]);}};
  const view={loader:null,async load({projectId,scope,batchSize}){
    this.loader?.cancel();this.loader=new context.window.FullGraph.SessionLoader(renderer,{batchSize});
    await this.loader.begin(projectId,scope);return this.loader;
  },cancel(){this.loader?.cancel();},interrupt(){if(this.loader?.state==='loading'||this.loader?.automatic)this.loader.cancel();},
    hasUsableView(){return Boolean(renderer.dataset?.nodeCount||renderer.dataset?.edgeCount||this.loader?.state==='complete');}};
  const coordinator=context.window.GraphOverview.create({readContext:()=>current,isReady:()=>ready,isVisible:()=>visible,view,setState:(kind,detail)=>states.push({kind,...detail})});
  return {coordinator,calls,states,renderer,renders,view,context,setReady:value=>ready=value,setVisible:value=>visible=value,setContext:value=>current=value};
}

test('default waits for owner readiness and loads one bounded large-project page',async()=>{
  const h=harness({entityCount:1000,relationCount:800});await h.coordinator.ensure();assert.equal(h.calls.length,0);
  h.setReady(true);await h.coordinator.ensure();
  assert.equal(h.calls.length,2);assert.equal(h.calls[1].body.limit,200);
  assert.equal(h.renderer.dataset.edgeCount,200);assert.ok(h.renderer.dataset.nodeCount<=400);
  assert.equal(h.states.at(-1).kind,'partial');assert.equal(h.states.at(-1).totalEntities,1000);
});

test('small manifest completes isolated entities within three total page requests',async()=>{
  const h=harness({entityCount:200,relationCount:400});h.setReady(true);await h.coordinator.ensure();
  assert.equal(h.calls.filter(call=>call.url.endsWith('/page')).length,3);
  assert.ok(h.calls.filter(call=>call.url.endsWith('/page')).every(call=>call.body.limit===200));
  assert.equal(h.view.loader.state,'complete');assert.equal(h.renderer.dataset.nodeCount,200);assert.equal(h.renderer.dataset.edgeCount,400);
  assert.equal(h.states.at(-1).kind,'complete');
});

for(const count of [0,3])test('authoritative '+(count?'isolated':'empty')+' graph is reported without a relation request',async()=>{
  const h=harness({entityCount:count,relationCount:0});h.setReady(true);await h.coordinator.ensure();
  assert.equal(h.calls.length,2);assert.equal(h.calls[1].body.kind,'entities');
  assert.equal(h.states.at(-1).kind,count?'complete':'empty');
  assert.equal(h.states.at(-1).totalEntities,count);
});

test('an authoritative project-empty overview offers existing knowledge preparation destinations',()=>{
  const h=harness();assert.equal(typeof h.context.window.GraphOverview.emptyGuidance,'function');
  const guidance=h.context.window.GraphOverview.emptyGuidance({ontology_scope:'all',known_at:null,filters:null});
  assert.equal(guidance.filtered,false);
  assert.deepEqual(Array.from(guidance.actions,action=>action.id),['sources','jobs','candidates']);
  assert.match(guidance.message,/正式/);assert.match(guidance.message,/资料库/);
});

test('an authoritative filtered-empty overview offers filter adjustment instead of declaring an empty project',()=>{
  const h=harness();assert.equal(typeof h.context.window.GraphOverview.emptyGuidance,'function');
  const guidance=h.context.window.GraphOverview.emptyGuidance({known_at:'2025-01-01T00:00:00Z',filters:{field:'metadata.city',op:'eq',value:'A'}});
  assert.equal(guidance.filtered,true);assert.deepEqual(Array.from(guidance.actions,action=>action.id),['adjust','reset']);
  assert.match(guidance.message,/筛选/);assert.doesNotMatch(guidance.message,/项目没有/);
});

test('reentry preserves explored graph and does not repeat a successful overview',async()=>{
  const h=harness();h.setReady(true);await h.coordinator.ensure();const data=h.renderer.dataset,count=h.calls.length;
  h.setVisible(false);await h.coordinator.ensure();h.setVisible(true);await h.coordinator.ensure();
  assert.equal(h.calls.length,count);assert.equal(h.renderer.dataset,data);
});

test('explicit navigation clears prior complete-overview copy before choosing its canvas',async()=>{
  const h=harness();h.setReady(true);await h.coordinator.ensure();assert.equal(h.states.at(-1).kind,'complete');
  h.coordinator.explicit();assert.equal(h.states.at(-1).kind,'selection');assert.equal(h.states.at(-1).message,'');
});

test('manual cancellation persists for the exact key and a new applied scope may load',async()=>{
  const h=harness({entityCount:1000,relationCount:800});h.setReady(true);await h.coordinator.ensure();h.coordinator.cancel();
  await h.coordinator.ensure();assert.equal(h.calls.length,2);assert.equal(h.states.at(-1).kind,'cancelled');
  h.setContext({projectId:'p',scope:{known_at:'2026-02-01T00:00:00Z',applicability:{platforms:{mode:'specific',values:['A']}}}});
  await h.coordinator.ensure();assert.equal(h.calls.length,4);assert.equal(h.calls[2].body.known_at,'2026-02-01T00:00:00Z');
  assert.equal(h.calls[2].body.applicability.platforms.values[0],'A');
});

test('cancelled A remains cancelled after B and back, while explicit retry reloads A',async()=>{
  const h=harness({entityCount:1000,relationCount:800});h.setReady(true);await h.coordinator.ensure();h.coordinator.cancel();
  h.coordinator.invalidate();h.setContext({projectId:'b',scope:{}});await h.coordinator.ensure();
  h.coordinator.invalidate();h.setContext({projectId:'p',scope:{known_at:'2026-01-01T00:00:00Z'}});await h.coordinator.ensure();
  assert.equal(h.calls.length,4);await h.coordinator.ensure({retry:true});assert.equal(h.calls.length,6);
});

test('pause during the first overview page prevents the small-graph completion loop',async()=>{
  const h=harness();h.setReady(true);const original=h.context.fetch;let release,started;
  const pageStarted=new Promise(resolve=>started=resolve);
  h.context.fetch=async(...args)=>{if(args[0].endsWith('/page')){started();await new Promise(resolve=>release=resolve);}return original(...args);};
  const pending=h.coordinator.ensure();await pageStarted;h.coordinator.pause();h.view.loader.pause();release();await pending;
  assert.equal(h.calls.length,2);assert.equal(h.view.loader.state,'paused');assert.equal(h.states.at(-1).kind,'partial');
});

test('A to B to A creates fresh owned sessions and preserves ontology/history scope',async()=>{
  const h=harness({entityCount:1000,relationCount:800});h.setReady(true);await h.coordinator.ensure();
  for(const projectId of ['b','p']){h.coordinator.invalidate();h.setContext({projectId,scope:{known_at:'2025-01-01T00:00:00Z',valid_at:'2024-01-01T00:00:00Z',ontology_scope:'ids',ontology_ids:['historical']}});await h.coordinator.ensure();}
  assert.equal(h.calls.length,6);assert.equal(h.calls[4].body.ontology_scope,'ids');assert.equal(h.calls[4].body.ontology_ids[0],'historical');
  assert.equal(h.calls[4].body.known_at,'2025-01-01T00:00:00Z');assert.equal(h.calls[4].body.valid_at,'2024-01-01T00:00:00Z');
});

test('a changed published ontology identity permits a new overview even in all-ontology scope',async()=>{
  const h=harness({entityCount:1000,relationCount:800});h.setReady(true);
  h.setContext({projectId:'p',scope:{ontology_scope:'all',ontology_ids:null},ontologyId:'release1'});
  await h.coordinator.ensure();h.coordinator.cancel();
  h.setContext({projectId:'p',scope:{ontology_scope:'all',ontology_ids:null},ontologyId:'release2'});
  await h.coordinator.ensure();assert.equal(h.calls.length,4);
});

test('leaving during a default page revokes its late paint',async()=>{
  const h=harness();h.setReady(true);const original=h.context.fetch;let release,started;
  const pageStarted=new Promise(resolve=>started=resolve);
  h.context.fetch=async(...args)=>{if(args[0].endsWith('/page')){started();await new Promise(resolve=>release=resolve);}return original(...args);};
  const pending=h.coordinator.ensure();await pageStarted;h.setVisible(false);h.coordinator.leave();release();await pending;
  assert.equal(h.renderer.dataset.nodeCount,0);assert.equal(h.states.some(state=>state.kind==='complete'),false);
});

test('a departed explicit navigation lease cannot revive on reentry',()=>{
  const h=harness(),lease=h.coordinator.explicit();assert.equal(lease.accepts(),true);
  h.setVisible(false);h.coordinator.leave();h.setVisible(true);assert.equal(lease.accepts(),false);
});

test('abandoning a pre-paint explicit request releases the default overview on reentry',async()=>{
  const h=harness();h.setReady(true);const original=h.context.fetch;let release,started;
  const pageStarted=new Promise(resolve=>started=resolve);
  h.context.fetch=async(...args)=>{if(args[0].endsWith('/page')){started();await new Promise(resolve=>release=resolve);}return original(...args);};
  const pending=h.coordinator.ensure();await pageStarted;h.coordinator.explicit();
  h.setVisible(false);h.coordinator.leave();release();await pending;h.context.fetch=original;h.setVisible(true);
  await h.coordinator.ensure();
  assert.equal(h.calls.filter(call=>!call.url.endsWith('/page')).length,2,'a fresh overview is owned after the abandoned selection');
  assert.equal(h.view.loader.state,'complete');assert.equal(h.renderer.dataset.nodeCount,4);
});

test('an active pre-paint explicit request still wins while its page remains visible',async()=>{
  const h=harness();h.setReady(true);h.coordinator.explicit();await h.coordinator.ensure();assert.equal(h.calls.length,0);
});

for(const empty of [false,true])test('abandoned selection retains a valid '+(empty?'authoritative empty':'explored')+' view on reentry',async()=>{
  const h=harness(empty?{entityCount:0,relationCount:0}:{});h.setReady(true);await h.coordinator.ensure();
  const data=h.renderer.dataset,count=h.calls.length;h.coordinator.explicit();h.setVisible(false);h.coordinator.leave();h.setVisible(true);await h.coordinator.ensure();
  assert.equal(h.calls.length,count);assert.equal(h.renderer.dataset,data);
});

test('an interrupted page retains loaded counts and does not pretend knowledge is empty',async()=>{
  const h=harness({failPage:2});h.setReady(true);await h.coordinator.ensure();
  assert.equal(h.states.at(-1).kind,'error');assert.equal(h.states.at(-1).loadedRelations,2);assert.equal(h.states.at(-1).totalEntities,4);
  assert.match(h.states.at(-1).message,/PG page unavailable/);
});

test('a count mismatch stays incomplete',async()=>{
  const h=harness({mismatch:true});h.setReady(true);await h.coordinator.ensure();
  assert.equal(h.states.at(-1).kind,'error');assert.equal(h.states.some(state=>state.kind==='complete'),false);
});

test('an explicit record wins an outstanding default page and blocks a replacement default',async()=>{
  const h=harness();h.setReady(true);const original=h.context.fetch;let release,started;
  const pageStarted=new Promise(resolve=>started=resolve);
  h.context.fetch=async(...args)=>{if(args[0].endsWith('/page')){started();await new Promise(resolve=>release=resolve);}return original(...args);};
  const pending=h.coordinator.ensure();await pageStarted;
  const lease=h.coordinator.explicit();assert.equal(lease.accepts(),true);release();await pending;
  assert.equal(h.renderer.dataset.nodeCount,0);const count=h.calls.length;await h.coordinator.ensure();assert.equal(h.calls.length,count);
  h.coordinator.invalidate();assert.equal(lease.accepts(),false);
});

function rendererHarness(){
  const context={window:{},performance,console};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.js'),'utf8'),context);
  const {Renderer,GraphDataset,Camera}=context.window.FullGraph,data=new GraphDataset();
  data.addNodes([{id:'a',kind:'entity',type:'A'},{id:'b',kind:'entity',type:'B'}],new Float32Array([-2,1,2,-1]));
  data.addEdges([{id:'exact',kind:'relation',type:'knows',subject_id:'a',object_id:'b',version_id:'rv2'}]);
  let selected=0;
  const renderer=Object.assign(Object.create(Renderer.prototype),{dataset:data,width:700,height:450,camera:new Camera(),requestDraw(){},onSelect(){selected++;}});
  return {renderer,context,selected:()=>selected};
}

test('nonselecting relation focus selects the exact edge and fits both endpoints',()=>{
  const h=rendererHarness(),r=h.renderer;
  assert.equal(typeof r.focusRecord,'function','renderer exposes camera-only record focus');
  assert.equal(r.focusRecord({id:'exact',kind:'relation',version_id:'rv2'}),true);
  assert.equal(r.selectedEdge,0);assert.equal(h.selected(),0);
  for(const [x,y] of [[-2,1],[2,-1]]){const point=r.camera.worldToScreen(x,y,r.width,r.height);assert.ok(point[0]>=20&&point[0]<=r.width-20);assert.ok(point[1]>=20&&point[1]<=r.height-20);}
  assert.equal(r.focusRecord({id:'exact',kind:'relation',version_id:'wrong'}),false);
});

test('predicate projection leaves authoritative loaded counts intact',()=>{
  const h=rendererHarness(),r=h.renderer;
  assert.equal(typeof r.setPredicateFilter,'function','renderer supports applied predicate restrictions');
  r.clearHover=()=>{};r.setExplorationFocus=()=>{};r.sync=function(){this.typeProjection?.sync();};
  r.setPredicateFilter('different');assert.equal(r.dataset.edgeCount,1);assert.equal(r.displayDataset.edgeCount,0);
  r.setPredicateFilter('knows');assert.equal(r.displayDataset.edgeCount,1);
});
