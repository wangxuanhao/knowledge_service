const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Element {
  constructor(){this.hidden=false;this.disabled=false;this.dataset={};this.value='';this.textContent='';this.listeners={};this.attributes={};this.childNodes=[];this.style={setProperty(name,value){this[name]=value;}};this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  addEventListener(type,fn){this.listeners[type]=fn;}
  matches(selector){return selector==='[data-fg-type]'?Object.hasOwn(this.dataset,'fgType'):selector.startsWith('.')?this.className?.split(' ').includes(selector.slice(1)):false;}
  closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector)||null;}
  querySelector(selector){return this.children?.get(selector)||this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){return this.groups?.get(selector)||this.childNodes.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
  prepend(element){this.prepended=element;}
  append(element){element.remove();this.childNodes.push(element);element.parentElement=this;this.appended=element;}
  remove(){if(this.parentElement){const children=this.parentElement.childNodes;children.splice(children.indexOf(this),1);this.parentElement=null;}}
}
function harness({labels={}}={}){
  const stage=new Element(),heading=new Element(),rail=new Element(),pathTools=new Element();pathTools.open=true;rail.classes.add('is-path-open');
  stage.children=new Map([['.graph-stage-heading',heading]]);
  const localIds=['graph-toolbar','graph-settings','graph-local-type-controls','graph-display-switch','graph-pan-hint','graph-summary','graph-canvas','graph-type-buttons','graph-timeline'];
  const locals=localIds.map(id=>Object.assign(new Element(),{id}));
  locals[1].open=true;
  // Resolve the production selector so omission of a local control remains observable.
  stage.querySelectorAll=selector=>locals.filter(element=>selector.includes('#'+element.id)||selector.includes('.'+element.id)||selector.includes('[data-graph-local]')&&['graph-toolbar','graph-settings'].includes(element.id));
  const modes=new Element(),buttons=['local','full'].map(mode=>Object.assign(new Element(),{dataset:{fgMode:mode}}));
  modes.groups=new Map([['[data-fg-mode]',buttons]]);
  const section=new Element(),parts=new Map();
  for(const selector of ['.full-graph-host','.full-graph-progress','.full-graph-type-controls','.full-graph-type-summary','.full-graph-type-empty','.full-graph-type-heading','.full-graph-type-row','.full-graph-type-page','.full-graph-type-pagination',...['load','next','auto','pause','restart','cancel','reset','zoom-in','zoom-out','query','locate','edges','all-types','type-prev','type-next'].map(action=>'[data-fg="'+action+'"]')])parts.set(selector,new Element());
  section.children=parts;
  let created=0,cancelled=0,resizeCount=0,resetCount=0,projectChanges=0,drawerCloses=0,loaded=[],actions=[];
  class GraphDataset {
    constructor(){this.nodeIndex=new Map();this.records=[];this.edges=[];this.types=new Map();}
    add(records){for(const record of records){if(this.nodeIndex.has(record.id))continue;const index=this.records.length;this.nodeIndex.set(record.id,index);this.records.push(record);const type=String(record.type||'');if(!this.types.has(type))this.types.set(type,{nodes:[],edges:[]});this.types.get(type).nodes.push(index);}}
  }
  const initialData=()=>{const data=new GraphDataset();data.add([{id:'e1',text:'申请',type:'https://a.test/Type'},{id:'e2',text:'审批',type:'https://b.test/Type'}]);data.edges.push({id:'r1',kind:'relation',subject_id:'e1',object_id:'e2'});return data;};
  const located=[],selected=[],filters=[],colors=[];
  class Renderer {
    constructor(host,options){this.setDataset(initialData());this.options=options;this.camera={scale:1};}
    resize(){resizeCount++;}reset(){resetCount++;}zoom(factor){this.camera.scale*=factor;}
    locate(id){if(!this.displayDataset.nodeIndex.has(id))return false;located.push(id);return true;}
    setDataset(value){this.dataset=value;this.displayDataset=value;this.typeFilter=null;this.sessionManifest=null;}
    setTypeFilter(type){filters.push(type);this.typeFilter=type;this.refresh();}
    refresh(){if(this.typeFilter===null){this.displayDataset=this.dataset;return;}const data=new GraphDataset();data.add(this.dataset.records.filter(record=>String(record.type||'')===this.typeFilter));data.edges=this.dataset.edges.filter(edge=>data.nodeIndex.has(edge.subject_id)&&data.nodeIndex.has(edge.object_id));this.displayDataset=data;}
    setSessionManifest(value){this.sessionManifest=value;}requestDraw(){}
  }
  class Loader {
    constructor(renderer){this.renderer=renderer;this.state='idle';}
    cancel(){cancelled++;this.state='cancelled';this.notify();}
    notify(){if(!this.manifest)return;this.renderer.refresh();this.onProgress?.({stage:this.state,phase:'connected',manifest:this.manifest,entities:this.renderer.dataset.records.length,relations:this.renderer.dataset.edges.length,automatic:this.automatic});}
    async begin(project,scope){this.cancel();this.manifest=null;this.renderer.setDataset(new GraphDataset());this.state='loading';loaded.push({project,scope});await (this.manifestRequest?.()||Promise.resolve());this.renderer.setDataset(initialData());this.manifest={entity_count:150001,relation_count:300000};this.state='paused';this.notify();}
    async next(){actions.push('next');this.notify();}
    async resume(){actions.push('auto');this.automatic=true;this.state='loading';this.notify();}
    pause(){actions.push('pause');this.automatic=false;this.state='paused';this.notify();}
    destroy(){}
  }
  const project=Object.assign(new Element(),{value:'p'}),elements=new Map([['graph-stage',stage],['project',project],['graph-path-tools',pathTools]]);
  project.onchange=()=>{projectChanges++;};
  const events=new Map();
  const document={readyState:'complete',getElementById:id=>elements.get(id),createElement:()=>created++===0?modes:created===2?section:new Element(),querySelector:()=>rail,addEventListener:(name,fn)=>events.set(name,fn)};
  const window={FullGraph:{Renderer,SessionLoader:Loader,GraphDataset},GraphAppearance:{colorFor:record=>{colors.push(record.type);return record.type==='https://a.test/Type'?'#123456':'#654321';}},WorkspaceDrawers:{active:{dialog:{id:'graph-settings-drawer'}},close(){drawerCloses++;}},WorkspaceContext:{read:()=>({known_at:'2026-10-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}})},wb:{ontologyScope:()=>({ontology_scope:'ids',ontology_ids:['v3']})},openKnowledgeRecord:record=>selected.push(record),addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph-view.js'),'utf8'),{document,window,labelOf:type=>labels[type]});
  const click=action=>section.listeners.click({target:{closest:selector=>selector==='[data-fg]'?{dataset:{fg:action}}:null}});
  const typeButtons=()=>parts.get('.full-graph-type-controls').querySelectorAll('[data-fg-type]');
  const filter=type=>parts.get('.full-graph-type-controls').listeners.click({target:typeButtons().find(button=>button.dataset.fgType===type)});
  return {view:window.FullGraphView,project,stage,heading,section,locals,pathTools,rail,buttons,parts,loaded,actions,located,selected,filters,colors,events,click,typeButtons,filter,get cancelled(){return cancelled;},get resizeCount(){return resizeCount;},get resetCount(){return resetCount;},get projectChanges(){return projectChanges;},get drawerCloses(){return drawerCloses;}};
}

test('full graph mode hides every local filter, budget and history control without clearing local settings',()=>{
  const subject=harness();
  subject.view.switchMode('full');
  assert.equal(subject.stage.dataset.graphMode,'full');
  assert.ok(subject.locals.every(element=>element.hidden),'local-only controls must not appear to filter the full graph');
  assert.equal(subject.pathTools.hidden,true);
  assert.equal(subject.rail.classes.has('is-path-open'),false);
  assert.equal(subject.section.hidden,false);
  assert.equal(subject.drawerCloses,1,'full mode closes the active graph settings drawer');
  assert.equal(subject.buttons[1].attributes['aria-pressed'],'true');
  subject.view.switchMode('local');
  assert.ok(subject.locals.every(element=>!element.hidden));
  assert.equal(subject.locals[1].open,true,'switching mode preserves disclosure and local selections');
  assert.equal(subject.pathTools.hidden,false);
  assert.equal(subject.rail.classes.has('is-path-open'),true);
  assert.equal(subject.section.hidden,true);
});

test('full loading and selection retain the page-owned metadata, temporal and ontology scope',async()=>{
  const subject=harness();subject.view.switchMode('full');await subject.view.load();
  assert.equal(subject.loaded[0].project,'p');
  assert.equal(subject.loaded[0].scope.known_at,'2026-10-01');
  assert.equal(subject.loaded[0].scope.filters.value,'杭州');
  assert.deepEqual(Array.from(subject.loaded[0].scope.ontology_ids),['v3']);
  subject.view.renderer.options.onSelect({id:'r1',kind:'relation'});
  assert.equal(subject.selected[0].projectId,'p');
  assert.equal(subject.selected[0].scope,subject.loaded[0].scope);
  subject.parts.get('[data-fg="query"]').value='r1';
  subject.parts.get('[data-fg="query"]').listeners.keydown({key:'Enter'});
  assert.deepEqual(subject.located,['e1']);
  assert.equal(subject.selected.at(-1).id,'r1');
  assert.match(subject.parts.get('.full-graph-progress').textContent,/实体.*150,001/,'locating a record retains partial counts');
  subject.view.switchMode('local');assert.ok(subject.cancelled>0);
});

test('full mode waits for explicit first batch and exposes partial counts with resume controls',async()=>{
  const subject=harness();subject.view.switchMode('full');assert.equal(subject.loaded.length,0);
  assert.match(subject.section.innerHTML,/data-fg="next"/);assert.match(subject.section.innerHTML,/data-fg="auto"/);
  assert.match(subject.section.innerHTML,/data-fg="pause"/);assert.match(subject.section.innerHTML,/data-fg="restart"/);
  await subject.view.load();
  assert.match(subject.parts.get('.full-graph-progress').textContent,/已暂停/);
  assert.match(subject.parts.get('.full-graph-progress').textContent,/2 \/ 150,001/);
  assert.match(subject.parts.get('.full-graph-progress').textContent,/1 \/ 300,000/);
  assert.equal(subject.view.renderer.sessionManifest,subject.view.loader.manifest,'the renderer receives total session counts before choosing its display layout');
  assert.equal(subject.parts.get('[data-fg="next"]').disabled,false);
  await subject.view.next();await subject.view.resume();subject.view.pause();
  assert.deepEqual(subject.actions,['next','auto','pause']);
});

test('full graph progress forwards the current small-session totals without changing loading scope',async()=>{
  const subject=harness();await subject.view.load();
  const manifest={id:'small-session',entity_count:73,relation_count:45};subject.view.loader.manifest=manifest;subject.view.loader.notify();
  assert.equal(subject.view.renderer.sessionManifest,manifest,'small-layout eligibility uses the manifest total rather than the loaded batch size');
  assert.match(subject.parts.get('.full-graph-progress').textContent,/2 \/ 73/);
  assert.match(subject.parts.get('.full-graph-progress').textContent,/1 \/ 45/);
  assert.equal(subject.loaded.length,1,'display metadata does not create a second graph request');
});

test('full graph zoom buttons change only the camera without loading or replacing records',async()=>{
  const subject=harness();await subject.view.load();
  const renderer=subject.view.renderer,records=renderer.dataset;
  const click=action=>subject.section.listeners.click({target:{closest:()=>({dataset:{fg:action}})}});
  click('zoom-in');assert.ok(renderer.camera.scale>1,'plus must zoom the existing graph');
  click('zoom-out');assert.ok(Math.abs(renderer.camera.scale-1)<1e-6,'minus reverses the zoom step');
  assert.equal(renderer.dataset,records);
  assert.equal(subject.loaded.length,1);
  assert.equal(subject.view.loader.state,'paused');
});

test('programmatic project onchange invalidates the partial graph and preserves the prior project handler',async()=>{
  const subject=harness();await subject.view.load();const previous=subject.view.renderer.dataset;
  subject.project.value='other';subject.project.onchange();
  assert.equal(subject.projectChanges,1);assert.equal(subject.view.loader.state,'cancelled');
  assert.notEqual(subject.view.renderer.dataset,previous);assert.equal(subject.view.loader.manifest,null);
  assert.equal(subject.view.renderer.sessionManifest,null,'a new dataset discards the previous session display metadata');
  assert.match(subject.parts.get('.full-graph-progress').textContent,/项目或范围已改变/);
});

test('full graph owns loaded type counts with canonical labels, colors and stable controls across batches',async()=>{
  const a='https://a.test/Type',b='https://b.test/Type';
  const subject=harness({labels:{[a]:'同名类型',[b]:'同名类型'}});await subject.view.load();
  assert.ok(subject.section.innerHTML.indexOf('full-graph-host')<subject.section.innerHTML.indexOf('full-graph-types'));
  assert.ok(subject.section.innerHTML.indexOf('full-graph-types')<subject.section.innerHTML.indexOf('full-graph-footer'),'types remain below the canvas and above batch controls');
  const initial=subject.typeButtons();assert.equal(initial.length,2);
  assert.deepEqual(initial.map(button=>button.dataset.fgType),[a,b]);
  assert.ok(initial.every(button=>button.textContent.includes('同名类型')));
  assert.equal(initial[0].attributes.title,a);assert.equal(initial[1].attributes.title,b);
  assert.equal(initial[0].dataset.typeCount,'1');assert.equal(initial[0].style['--fg-type-color'],'#123456');
  assert.equal(initial[1].style['--fg-type-color'],'#654321');assert.ok(subject.colors.includes(a)&&subject.colors.includes(b));
  assert.match(subject.parts.get('.full-graph-type-summary').textContent,/已加载/);
  assert.doesNotMatch(subject.parts.get('.full-graph-type-summary').textContent,/完整清单/);
  subject.filter(a);assert.equal(subject.view.renderer.typeFilter,a);assert.deepEqual(subject.filters,[a]);
  assert.equal(initial[0].attributes['aria-pressed'],'true');assert.equal(initial[1].attributes['aria-pressed'],'false');
  subject.view.renderer.dataset.add([{id:'e1',type:a},{id:'e3',text:'新申请',type:a},{id:'e4',text:'未知实体'}]);subject.view.loader.notify();
  const later=subject.typeButtons();assert.equal(later[0],initial[0],'batch counts preserve the existing focused category node');
  assert.equal(later[0].dataset.typeCount,'2');assert.equal(later[0].attributes['aria-pressed'],'true');
  assert.equal(later[2].dataset.fgType,'');assert.match(later[2].textContent,/未知类型/);
  assert.equal(subject.parts.get('[data-fg="all-types"]').textContent,'全部类型 · 4');
  assert.equal(subject.loaded.length,1);assert.deepEqual(subject.actions,[],'display filtering never advances the loading session');
  subject.click('all-types');assert.equal(subject.view.renderer.typeFilter,null);assert.deepEqual(subject.filters,[a,null]);
  assert.equal(subject.parts.get('[data-fg="all-types"]').attributes['aria-pressed'],'true');
  subject.filter('');assert.equal(subject.view.renderer.typeFilter,'','unknown type must remain distinct from all types');
  subject.view.loader.state='complete';subject.view.loader.notify();assert.match(subject.parts.get('.full-graph-type-summary').textContent,/完整清单/);
});

test('full graph type paging keeps controls bounded and retains selection across new categories',async()=>{
  const subject=harness();await subject.view.load();
  subject.view.renderer.dataset.add(Array.from({length:95},(_,index)=>({id:'many-'+index,text:'实体'+index,type:'urn:many:'+index})));subject.view.loader.notify();
  assert.equal(subject.typeButtons().length,40);assert.equal(subject.parts.get('[data-fg="type-prev"]').disabled,true);
  const first=subject.typeButtons()[0];subject.filter(first.dataset.fgType);subject.click('type-next');
  assert.equal(subject.typeButtons().length,40);assert.equal(subject.parts.get('[data-fg="type-prev"]').disabled,false);
  assert.equal(subject.view.renderer.typeFilter,first.dataset.fgType);
  subject.click('type-next');assert.equal(subject.typeButtons().length,17);assert.equal(subject.parts.get('[data-fg="type-next"]').disabled,true);
  subject.view.renderer.dataset.add([{id:'new-type',type:'urn:many:new'}]);subject.view.loader.notify();
  assert.equal(subject.typeButtons().length,18);assert.equal(subject.view.renderer.typeFilter,first.dataset.fgType);
  subject.click('type-prev');subject.click('type-prev');assert.equal(subject.typeButtons()[0].attributes['aria-pressed'],'true');
  assert.equal(subject.loaded.length,1);assert.deepEqual(subject.actions,[]);
});

test('the first full-graph type page reads at most its forty visible categories on each batch',async()=>{
  const subject=harness();await subject.view.load();
  subject.view.renderer.dataset.add(Array.from({length:200},(_,index)=>({id:'type-'+index,type:'urn:type:'+index})));
  const types=subject.view.renderer.dataset.types,entries=types.entries.bind(types);let inspected=0;
  types.entries=function*(){for(const entry of entries()){inspected++;yield entry;}};types[Symbol.iterator]=types.entries;
  subject.view.loader.notify();assert.equal(subject.typeButtons().length,40);assert.ok(inspected<=40,'a bounded first page must not copy every loaded type');
  inspected=0;subject.view.loader.notify();assert.ok(inspected<=40,'subsequent batches retain the bounded read cost');
});

test('restart keeps old categories and totals cleared while the new manifest is pending',async()=>{
  const subject=harness();await subject.view.load();subject.filter('https://a.test/Type');let release;
  subject.view.loader.manifestRequest=()=>new Promise(resolve=>{release=resolve;});
  const pending=subject.view.restart();
  try{
    assert.equal(subject.view.renderer.dataset.records.length,0);assert.equal(subject.view.renderer.sessionManifest,null);
    assert.equal(subject.typeButtons().length,0,'the old begin/cancel progress must not repopulate cleared categories');
    assert.equal(subject.parts.get('.full-graph-type-summary').textContent,'');
    assert.equal(subject.parts.get('[data-fg="all-types"]').textContent,'全部类型 · 0');
    assert.equal(subject.parts.get('.full-graph-progress').textContent,'正在核对图谱范围与完整清单…');
  }finally{release();await pending;}
  assert.equal(subject.typeButtons().length,2);assert.equal(subject.view.renderer.typeFilter,null);
});

test('a failed restart manifest cannot restore old categories or append old session totals to its error',async()=>{
  const subject=harness();await subject.view.load();subject.filter('https://b.test/Type');let reject;
  subject.view.loader.manifestRequest=()=>new Promise((resolve,fail)=>{reject=fail;});
  const pending=subject.view.restart();reject(new Error('清单读取失败'));await pending;
  assert.equal(subject.typeButtons().length,0);assert.equal(subject.view.renderer.sessionManifest,null);
  assert.equal(subject.parts.get('.full-graph-type-summary').textContent,'');
  assert.equal(subject.parts.get('[data-fg="all-types"]').textContent,'全部类型 · 0');
  const status=subject.parts.get('.full-graph-progress').textContent;assert.match(status,/清单读取失败/);assert.doesNotMatch(status,/实体 |150,001/);
});

test('full graph locate searches the filtered view and never reports a rejected locate as successful',async()=>{
  const subject=harness();await subject.view.load();subject.filter('https://a.test/Type');
  const query=subject.parts.get('[data-fg="query"]'),status=subject.parts.get('.full-graph-progress');
  for(const value of ['e2','审批','r1']){query.value=value;subject.click('locate');assert.doesNotMatch(status.textContent,/已定位/);}
  assert.deepEqual(subject.located,[]);assert.deepEqual(subject.selected,[]);
  query.value='申请';subject.click('locate');assert.deepEqual(subject.located,['e1']);assert.match(status.textContent,/已定位实体/);
  subject.view.renderer.locate=()=>false;subject.click('locate');assert.doesNotMatch(status.textContent,/已定位/);
  subject.click('all-types');query.value='r1';subject.click('locate');assert.deepEqual(subject.selected,[],'a failed edge endpoint locate must not claim a relation was located');
});

test('full graph pause and cancel retain type selection while restart and scope changes reset it',async()=>{
  const subject=harness();assert.equal(subject.typeButtons().length,0);assert.match(subject.parts.get('.full-graph-type-empty').textContent,/加载首批/);
  assert.equal(subject.parts.get('.full-graph-type-heading').hidden,true);assert.equal(subject.parts.get('.full-graph-type-row').hidden,true,'an empty type panel occupies only its empty-state line');
  await subject.view.load();subject.filter('https://b.test/Type');const button=subject.typeButtons()[1];
  assert.equal(subject.parts.get('.full-graph-type-heading').hidden,false);assert.equal(subject.parts.get('.full-graph-type-row').hidden,false);
  subject.view.pause();subject.view.cancel();assert.equal(subject.view.renderer.typeFilter,'https://b.test/Type');assert.equal(subject.typeButtons()[1],button);
  await subject.view.restart();assert.equal(subject.view.renderer.typeFilter,null);assert.equal(subject.parts.get('[data-fg="all-types"]').attributes['aria-pressed'],'true');
  subject.filter('https://a.test/Type');subject.events.get('workspace:scope-changed')({detail:{page:'search'}});
  assert.equal(subject.view.renderer.typeFilter,null);assert.equal(subject.typeButtons().length,0);assert.equal(subject.parts.get('[data-fg="all-types"]').disabled,true);
  assert.match(subject.parts.get('.full-graph-type-empty').textContent,/加载首批/);
});

test('full mode lets its grid and panel grow around a nonshrinking canvas while keeping type rows bounded',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph.css'),'utf8');
  const declarations=selector=>css.split(selector+'{')[1]?.split('}')[0]||'';
  const workspace=declarations('body main #tab-search .search-graph-workspace:has(>#graph-stage[data-graph-mode="full"])');
  assert.match(workspace,/(?:^|;)height:auto(?:;|$)/,'the full-mode grid must grow beyond the desktop fixed 520px budget');
  assert.match(workspace,/grid-template-rows:auto/,'an open inspector must not restore a fixed first grid row');
  assert.match(workspace,/align-items:stretch/,'the evidence and graph panels must share one row height');
  const stage=declarations('body main #tab-search #graph-stage[data-graph-mode="full"]');
  assert.match(stage,/(?:^|;)height:auto(?:;|$)/);assert.match(stage,/min-height:clamp\(520px,/);
  const canvas=declarations('body main #tab-search #graph-stage[data-graph-mode="full"] .full-graph-host');
  assert.match(canvas,/flex:1 0 auto/,'batch and type controls must not shrink the graph below its usable height');
  assert.match(canvas,/height:clamp\(280px,/,'a responsive explicit canvas height prevents measured canvas pixels from controlling the automatic panel height');
  assert.doesNotMatch(workspace+stage,/overflow:(?:auto|scroll)/,'the page supplies natural scrolling for the expanded panel');
  assert.match(declarations('body main #tab-search .full-graph-type-controls'),/max-height:64px/,'two scrollable type rows leave room for the graph and batch controls');
});
