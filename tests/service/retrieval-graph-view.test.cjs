const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Element {
  constructor(){this.hidden=false;this.disabled=false;this.dataset={};this.value='';this.listeners={};this.attributes={};this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  setAttribute(name,value){this.attributes[name]=value;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  querySelector(selector){return this.children?.get(selector)||null;}
  querySelectorAll(selector){return this.groups?.get(selector)||[];}
  prepend(element){this.prepended=element;}
  append(element){this.appended=element;}
}
function harness(){
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
  for(const selector of ['.full-graph-host','.full-graph-progress',...['load','next','auto','pause','restart','cancel','reset','query','locate','edges'].map(action=>'[data-fg="'+action+'"]')])parts.set(selector,new Element());
  section.children=parts;
  let created=0,cancelled=0,resizeCount=0,resetCount=0,projectChanges=0,drawerCloses=0,loaded=[],actions=[];
  const dataset={nodeIndex:new Map([['e1',0]]),records:[{id:'e1',text:'申请'}],edges:[{id:'r1',kind:'relation',subject_id:'e1',object_id:'e2'}]};
  const located=[],selected=[];
  class Renderer {constructor(host,options){this.dataset=dataset;this.options=options;}resize(){resizeCount++;}reset(){resetCount++;}locate(id){located.push(id);}setDataset(value){this.dataset=value;}requestDraw(){}}
  class Loader {
    constructor(renderer){this.renderer=renderer;this.state='idle';}
    cancel(){cancelled++;this.state='cancelled';}
    notify(){this.onProgress?.({stage:this.state,phase:'connected',manifest:this.manifest,entities:2,relations:1,automatic:this.automatic});}
    async begin(project,scope){loaded.push({project,scope});this.manifest={entity_count:150001,relation_count:300000};this.state='paused';this.notify();}
    async next(){actions.push('next');this.notify();}
    async resume(){actions.push('auto');this.automatic=true;this.state='loading';this.notify();}
    pause(){actions.push('pause');this.automatic=false;this.state='paused';this.notify();}
    destroy(){}
  }
  const project=Object.assign(new Element(),{value:'p'}),elements=new Map([['graph-stage',stage],['project',project],['graph-path-tools',pathTools]]);
  project.onchange=()=>{projectChanges++;};
  const document={readyState:'complete',getElementById:id=>elements.get(id),createElement:()=>created++===0?modes:section,querySelector:()=>rail,addEventListener(){}};
  const window={FullGraph:{Renderer,SessionLoader:Loader,GraphDataset:class{}},WorkspaceDrawers:{active:{dialog:{id:'graph-settings-drawer'}},close(){drawerCloses++;}},WorkspaceContext:{read:()=>({known_at:'2026-10-01',filters:{field:'metadata.city',op:'eq',value:'杭州'}})},wb:{ontologyScope:()=>({ontology_scope:'ids',ontology_ids:['v3']})},openKnowledgeRecord:record=>selected.push(record),addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/full-graph-view.js'),'utf8'),{document,window});
  return {view:window.FullGraphView,project,stage,heading,section,locals,pathTools,rail,buttons,parts,loaded,actions,located,selected,get cancelled(){return cancelled;},get resizeCount(){return resizeCount;},get resetCount(){return resetCount;},get projectChanges(){return projectChanges;},get drawerCloses(){return drawerCloses;}};
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
  assert.equal(subject.parts.get('[data-fg="next"]').disabled,false);
  await subject.view.next();await subject.view.resume();subject.view.pause();
  assert.deepEqual(subject.actions,['next','auto','pause']);
});

test('programmatic project onchange invalidates the partial graph and preserves the prior project handler',async()=>{
  const subject=harness();await subject.view.load();const previous=subject.view.renderer.dataset;
  subject.project.value='other';subject.project.onchange();
  assert.equal(subject.projectChanges,1);assert.equal(subject.view.loader.state,'cancelled');
  assert.notEqual(subject.view.renderer.dataset,previous);assert.equal(subject.view.loader.manifest,null);
  assert.match(subject.parts.get('.full-graph-progress').textContent,/项目或范围已改变/);
});
