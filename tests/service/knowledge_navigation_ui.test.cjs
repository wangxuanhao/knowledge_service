const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(
  path.join(__dirname,'../../knowledge_service/web/knowledge-navigation.js'),
  'utf8',
);
const appSource=fs.readFileSync(
  path.join(__dirname,'../../knowledge_service/web/app.js'),
  'utf8',
);

function fixture({projectId='project-b',visible=null,runtimeCurrent='overview'}={}){
  const calls=[];
  const refreshes={records:0,reviews:0,'ontology-model':0,jobs:0,overview:0};
  const state={top:visible==='jobs'||visible==='runtime'?'runtime':visible,runtime:visible==='jobs'?'jobs':runtimeCurrent};
  const shown=name=>name==='runtime'?state.top==='runtime':name==='jobs'?state.top==='runtime'&&state.runtime==='jobs':state.top===name;
  const pages=new Map(['records','reviews','ontology-model','runtime','jobs'].map(name=>[name,{
    classList:{contains:value=>value==='hidden'&&!shown(name)},
  }]));
  const project={
    value:'project-a',
    options:[{value:''},{value:'project-a'},{value:'project-b'}],
    dispatchEvent(event){
      calls.push(['change',event.type,this.value]);
      context.current=this.value;
      // Mirror the real listeners: records refreshes; ontology-model only clears;
      // the currently visible runtime subpanel refreshes itself.
      if(state.top==='records')refreshes.records++;
      if(state.top==='reviews')refreshes.reviews++;
      if(state.top==='runtime')refreshes[state.runtime]++;
    },
  };
  const context=vm.createContext({
    window:{
      KnowledgeTabs:{open(name,options){
        calls.push(['tab',name,context.current,options]);
        state.top=name;
        if(options?.trigger===false)return;
        if(name==='records')refreshes.records++;
        if(name==='ontology-model')refreshes['ontology-model']++;
        if(name==='runtime')refreshes[runtimeCurrent]++;
      }},
      TaskReview:{open(){calls.push(['reviews',context.current]);state.top='reviews';refreshes.reviews++;}},
      SourcesView:{open(){calls.push(['sources',context.current]);}},
      OntologyModel:{refresh(){calls.push(['ontology-refresh',context.current]);refreshes['ontology-model']++;}},
      RuntimeView:{get current(){return state.runtime;},show(view,options){
        calls.push(['runtime-view',view,context.current,options]);state.runtime=view;
        if(options?.load!==false)refreshes[view]++;
      }},
    },
    document:{getElementById(id){return id==='project'?project:pages.get(id.replace(/^tab-/,''))||null;}},
    Event:class Event{constructor(type){this.type=type;}},
    setTimeout,
    current:'project-a',
  });
  vm.runInContext(source,context);
  return {context,project,calls,projectId,refreshes};
}

test('records switches to the task project before opening and refreshes through the real tab',async()=>{
  const {context,calls,projectId}=fixture();
  await context.window.KnowledgeNavigation.open('records',{projectId});
  assert.deepEqual(calls,[
    ['change','change','project-b'],
    ['tab','records','project-b',undefined],
  ]);
});

test('same-project navigation does not dispatch a redundant project change',async()=>{
  const {context,calls}=fixture();
  await context.window.KnowledgeNavigation.open('ontology-model',{projectId:'project-a'});
  assert.deepEqual(calls,[['tab','ontology-model','project-a',undefined]]);
});

test('special destinations use their public loaders after selecting the task project',async()=>{
  for(const [target,expected] of [
    ['reviews',[['reviews','project-b']]],
    ['sources',[['sources','project-b']]],
    ['jobs',[
      ['runtime-view','jobs','project-a',{load:false}],
      ['change','change','project-b'],
    ]],
  ]){
    const {context,calls}=fixture();
    await context.window.KnowledgeNavigation.open(target,{projectId:'project-b'});
    const relevant=target==='jobs'?calls.slice(1):calls.slice(1);
    assert.deepEqual(JSON.parse(JSON.stringify(relevant)),expected,target);
  }
});

test('an already visible records or ontology page refreshes only from project change',async()=>{
  for(const target of ['records','ontology-model']){
    const {context,calls,refreshes}=fixture({visible:target});
    await context.window.KnowledgeNavigation.open(target,{projectId:'project-b'});
    assert.equal(refreshes[target],1,target);
    if(target==='records')assert.equal(calls.filter(call=>call[0]==='tab').length,0,target);
    else assert.equal(calls.filter(call=>call[0]==='ontology-refresh').length,1,target);
  }
});

test('an already visible review page reuses its project-change refresh',async()=>{
  const {context,calls,refreshes}=fixture({visible:'reviews'});
  await context.window.KnowledgeNavigation.open('reviews',{projectId:'project-b'});
  assert.equal(refreshes.reviews,1);
  assert.equal(calls.filter(call=>call[0]==='reviews').length,0);
});

test('jobs loads exactly once whether or not it is already the active runtime section',async()=>{
  const already=fixture({visible:'jobs',runtimeCurrent:'jobs'});
  await already.context.window.KnowledgeNavigation.open('jobs',{projectId:'project-b'});
  assert.equal(already.refreshes.jobs,1);
  assert.equal(already.calls.filter(call=>call[0]==='runtime-view'&&call[3]?.load!==false).length,0);

  const elsewhere=fixture({visible:'runtime',runtimeCurrent:'overview'});
  await elsewhere.context.window.KnowledgeNavigation.open('jobs',{projectId:'project-b'});
  assert.equal(elsewhere.refreshes.overview,0,'must not load the previous runtime section');
  assert.equal(elsewhere.refreshes.jobs,1);
  assert.deepEqual(JSON.parse(JSON.stringify(elsewhere.calls)),[
    ['tab','runtime','project-a',{trigger:false}],
    ['runtime-view','jobs','project-a',{load:false}],
    ['change','change','project-b'],
  ]);

  const sameProject=fixture({visible:'runtime',runtimeCurrent:'overview'});
  await sameProject.context.window.KnowledgeNavigation.open('jobs',{projectId:'project-a'});
  assert.equal(sameProject.refreshes.overview,0);
  assert.equal(sameProject.refreshes.jobs,1);
});

test('a deleted project is rejected without falling back to the current project',async()=>{
  const {context,calls}=fixture();
  await assert.rejects(
    context.window.KnowledgeNavigation.open('records',{projectId:'deleted-project'}),
    /该任务所属项目已不存在/,
  );
  assert.deepEqual(calls,[]);
});

test('unknown destinations are rejected explicitly',async()=>{
  const {context,calls}=fixture();
  await assert.rejects(
    context.window.KnowledgeNavigation.open('not-a-page',{projectId:'project-a'}),
    /未知的导航目标/,
  );
  assert.deepEqual(calls,[]);
});

test('KnowledgeTabs clicks real menu buttons once and directly opens internal pages',()=>{
  const classes=initial=>{
    const values=new Set(initial.split(' ').filter(Boolean));
    return {add:value=>values.add(value),remove:value=>values.delete(value),
      toggle(value,force){force?values.add(value):values.delete(value);},
      contains:value=>values.has(value)};
  };
  let recordClicks=0;
  const recordButton={dataset:{tab:'records'},classList:classes(''),click(){recordClicks++;this.onclick();}};
  const buttons=[recordButton];
  const nodes={
    'tab-records':{classList:classes('hidden')},
    'tab-sources':{classList:classes('hidden')},
    title:{textContent:''},
    scope:{classList:classes('hidden')},
  };
  const context=vm.createContext({window:{},titles:{records:'知识台账',sources:'原文数据源'},
    $:id=>nodes[id],document:{
      querySelector(selector){return selector==='[data-tab="records"]'?recordButton:null;},
      querySelectorAll(selector){return selector==='[data-tab]'?buttons:Object.values(nodes).filter(node=>node.classList);},
    }});
  const block=appSource.match(/function activateTab[\s\S]*?window\.KnowledgeTabs=\{[\s\S]*?\n\};/);
  assert.ok(block,'app.js must expose the tab navigation block');
  vm.runInContext(block[0],context);

  context.window.KnowledgeTabs.open('records');
  assert.equal(recordClicks,1);
  assert.equal(nodes['tab-records'].classList.contains('hidden'),false);

  context.window.KnowledgeTabs.open('sources');
  assert.equal(recordClicks,1);
  assert.equal(nodes['tab-sources'].classList.contains('hidden'),false);

  context.window.KnowledgeTabs.open('records',{trigger:false});
  assert.equal(recordClicks,1,'silent activation must not call existing loaders');
});
