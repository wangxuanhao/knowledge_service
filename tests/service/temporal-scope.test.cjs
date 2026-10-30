const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const file=path.join(web,'temporal-scope.js');
const temporal=fs.existsSync(file)?require(file):{};
const workspace=require(path.join(web,'workspace-context.js'));
const fields=require(path.join(web,'scope-fields.js'));
const read=name=>fs.readFileSync(path.join(web,name),'utf8');
const plain=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
const settle=()=>new Promise(setImmediate);
function declaration(source,name){
  const match=source.match(new RegExp('  (?:async )?function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?\\n  \\}'));
  assert.ok(match,'production function '+name+' exists');return match[0];
}

test('all business time stays inclusive and returns only API temporal fields',()=>{
  assert.equal(typeof temporal.capture,'function','shared temporal capture must exist');
  assert.deepEqual(temporal.capture({mode:'all',validAt:'bad date',includeUnknown:false}),
    {valid_at:null,known_at:null,include_unknown:true});
});

test('current business time freezes and validates the clock at apply',()=>{
  assert.equal(typeof temporal.capture,'function');
  let time='2026-10-28T01:02:03.456Z';
  const scope=temporal.capture({mode:'current',includeUnknown:false},()=>new Date(time));
  time='2026-10-29T01:02:03.456Z';
  assert.deepEqual(scope,{valid_at:'2026-10-28T01:02:03.456Z',known_at:null,include_unknown:false});
  for(const value of ['now',null,new Date('bad date')])assert.throws(()=>temporal.capture({mode:'current'},()=>value),/时间|时点/);
});

test('specified local dates convert to UTC deterministically and reject normalized invalid dates',()=>{
  assert.equal(typeof temporal.capture,'function');
  const script="const t=require(process.argv[1]);process.stdout.write(JSON.stringify(t.capture({mode:'at',validAt:'2026-10-28T09:02:03.456',knownAt:'2024-01-01T08:30',includeUnknown:false})))";
  const scope=JSON.parse(execFileSync(process.execPath,['-e',script,file],{env:{...process.env,TZ:'Asia/Shanghai'}}));
  assert.deepEqual(scope,{valid_at:'2026-10-28T01:02:03.456Z',known_at:'2024-01-01T00:30:00.000Z',include_unknown:false});
  for(const value of ['', 'not a date','2026-02-30T12:00'])assert.throws(()=>temporal.capture({mode:'at',validAt:value}),/业务|日期/);
  assert.throws(()=>temporal.capture({mode:'all',knownAt:'bad date'}),/系统/);
});

test('restoring a frozen current point never silently refreshes it',()=>{
  assert.equal(typeof temporal.restore,'function');
  const scope={valid_at:'2026-10-28T01:02:03.456Z',known_at:'2024-01-01T00:30:00.000Z',include_unknown:false};
  const draft=temporal.restore(scope);
  assert.equal(draft.mode,'at');assert.equal(draft.includeUnknown,false);
  assert.deepEqual(temporal.capture(draft),scope);
  assert.equal(temporal.restore({}).mode,'all');
  assert.match(temporal.describe(scope),/2026-10-28T01:02:03.456Z/);
  assert.match(temporal.describe(scope),/不含生效时间未知/);
  assert.match(temporal.describe({}),/全部业务时间.*当前系统版本/);
});

test('unchanged restored inputs retain backend microseconds on both independent axes',()=>{
  const applied={valid_at:'2026-10-28T01:02:03.456789Z',known_at:'2024-01-01T00:30:00.987654Z',include_unknown:false};
  const draft=temporal.restore(applied);
  assert.deepEqual(temporal.capture(draft,()=>new Date(),applied),applied);
  draft.validAt='2024-02-01T10:00';
  const edited=temporal.capture(draft,()=>new Date(),applied);
  assert.equal(edited.valid_at,new Date(draft.validAt).toISOString());assert.equal(edited.known_at,applied.known_at);
});

test('unchanged local form values retain the later DST-fold instant on both axes before UTC parsing',()=>{
  const script=`const t=require(process.argv[1]);
    const original={valid_at:'2026-11-01T06:30:00.123456Z',known_at:'2026-11-01T06:30:00.123456Z',include_unknown:false};
    const draft=t.restore(original),unchanged=t.capture(draft,()=>new Date(),original);
    const zero={...original,valid_at:'2026-11-01T06:30:00.000456Z',known_at:'2026-11-01T06:30:00.000456Z'};
    const trimmed={...t.restore(zero),validAt:'2026-11-01T01:30',knownAt:'2026-11-01T01:30:00'};
    const normalized=t.capture(trimmed,()=>new Date(),zero);
    const edited=t.capture({...trimmed,validAt:'2026-11-01T01:45'},()=>new Date(),zero);
    process.stdout.write(JSON.stringify({original,draft,unchanged,zero,normalized,edited}));`;
  const result=JSON.parse(execFileSync(process.execPath,['-e',script,file],{env:{...process.env,TZ:'America/New_York'}}));
  assert.equal(result.draft.validAt,'2026-11-01T01:30:00.123','fixture restores the later fold as ambiguous local time');
  assert.deepEqual(result.unchanged,result.original);
  assert.deepEqual(result.normalized,result.zero,'browser normalization must retain original microseconds and fold');
  assert.equal(result.edited.valid_at,'2026-11-01T05:45:00.000Z','changed local value follows normal browser Date interpretation');
  assert.equal(result.edited.known_at,result.zero.known_at,'editing one axis leaves the other exact instant unchanged');
});

function editor(){
  let page='search',bridge;
  const store=workspace.createStore(),controls=new Map();
  const get=id=>{if(!controls.has(id))controls.set(id,{id,value:'',checked:false,hidden:false,disabled:false,open:false,children:[],
    listeners:{},classList:{toggle(){}},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},focus(){},prepend(){},replaceChildren(){this.children=[];},append(node){this.children.push(node);}});return controls.get(id);};
  const context={store,get page(){return page;},isContentPage:workspace.isContentPage,read:p=>store.read(p,page),register:value=>{bridge=value;value.restore();}};
  const environment={window:{WorkspaceContext:context,TemporalScope:temporal,ScopeFields:fields,KnowledgeTabs:{open:value=>{page=value;bridge.restore();}},
    ApplicabilityUI:{readQueryScope:()=>({applicability:{platforms:{mode:'specific',values:['平台甲']}}})}},
    document:{getElementById:get,createElement:()=>({setAttribute(){}}),addEventListener(){},dispatchEvent(){}},
    current:'p',CustomEvent:class{},showRuntimeView(){},Date:class extends Date{constructor(...args){super(...(args.length?args:['2026-10-28T01:02:03.456Z']));}},JSON,Error};
  vm.runInNewContext(read('workspace-context-ui.js'),environment);
  return {get,store,context,apply:()=>bridge.apply(),restore:()=>bridge.restore(),reset:()=>bridge.reset(),page:value=>{page=value;bridge.restore();}};
}

test('shared editor preserves combined scope on invalid dates, restores, copies and resets all pages',()=>{
  const e=editor(),original={valid_at:'2024-02-01T00:00:00.000Z',known_at:'2025-01-01T00:00:00.000Z',include_unknown:false,
    ontology_scope:'ids',ontology_ids:['ontology'],kinds:['entity'],filters:{field:'metadata.region',op:'eq',value:'华东'},
    applicability:{platforms:{mode:'specific',values:['平台甲']}}};
  e.store.edit('p','search',original);e.store.apply('p','search');const initial=e.store.read('p','search');e.restore();
  assert.equal(e.get('scope-time-mode').value,'at');assert.equal(e.get('scope-include-unknown').checked,false);
  e.get('scope-valid-at').value='';assert.throws(e.apply,/业务|日期/);
  assert.deepEqual(e.store.read('p','search'),initial);
  e.get('scope-valid-at').value='invalid';assert.throws(e.apply,/业务|日期/);
  const before=e.store.read('p','search');e.restore();e.apply();assert.deepEqual(e.store.read('p','search'),before);
  e.get('scope-time-mode').value='current';e.get('scope-include-unknown').checked=false;e.apply();
  const current=e.store.read('p','search');assert.equal(current.valid_at,'2026-10-28T01:02:03.456Z');assert.equal(current.include_unknown,false);
  assert.deepEqual(current.filters,original.filters);assert.deepEqual(current.ontology_ids,['ontology']);assert.deepEqual(current.kinds,['entity']);
  e.get('scope-to-qa').onclick();assert.deepEqual(e.store.read('p','qa'),current);
  for(const page of ['sources','qa','search','records']){e.store.copy('p','qa',page);e.page(page);assert.equal(e.get('scope-time-mode').value,'at');assert.equal(e.get('scope-include-unknown').checked,false);}
  e.reset();assert.deepEqual(e.store.read('p','records'),{known_at:null,valid_at:null,filters:null,include_unknown:true});
  e.get('scope-time-mode').value='all';e.get('scope-include-unknown').checked=false;e.apply();assert.equal(e.store.read('p','records').include_unknown,true);
});

test('editor cancels time drafts and shows only active date/unknown controls, preserving exact restored timestamps',()=>{
  const e=editor(),applied={valid_at:'2026-10-28T01:02:03.456789Z',known_at:'2024-01-01T00:30:00.987654Z',include_unknown:false};
  e.store.edit('p','search',applied);e.store.apply('p','search');e.restore();e.apply();
  assert.equal(e.store.read('p','search').valid_at,applied.valid_at);assert.equal(e.store.read('p','search').known_at,applied.known_at);
  assert.equal(e.get('scope-valid-at-field').hidden,false);assert.equal(e.get('scope-include-unknown').disabled,false);
  e.get('scope-time-mode').value='current';e.get('scope-time-mode').listeners.change();
  assert.equal(e.get('scope-valid-at-field').hidden,true);assert.equal(e.get('scope-include-unknown').disabled,false);
  e.get('scope-close').onclick();assert.equal(e.get('scope-time-mode').value,'at');assert.equal(e.store.read('p','search').valid_at,applied.valid_at);
  e.get('scope-time-mode').value='all';e.get('scope-time-mode').listeners.change();
  assert.equal(e.get('scope-include-unknown').disabled,true);assert.equal(e.get('scope-include-unknown').checked,true);
});

function submissionFixture(stage){
  let resolve,applied={valid_at:'2024-01-01T00:30:00.000Z',known_at:'2025-01-01T00:00:00.000Z',include_unknown:false,
    filters:{field:'metadata.region',op:'eq',value:'华东'},applicability:{platforms:{mode:'specific',values:['平台甲']}},ontology_ids:['o'],kinds:['chunk']};
  const gate=new Promise(done=>resolve=done),bodies=[],notes=[],saved=[];
  const controls={generate:{checked:false},'qa-mode':{value:'keyword'},'qa-include-references':{checked:false}};
  const env={busy:false,preparing:false,current:'p',serial:0,input:{value:'问题'},send:{disabled:false},summary:{textContent:''},progress:{textContent:''},qaHistory:[],
    reads:0,scope:()=>{env.reads++;return applied;},$c:id=>controls[id],window:{ScopeFields:fields,TemporalScope:temporal},
    sessionManager:{loading:false,state:{conflict:false},ensureActive:async()=>{if(stage==='ensure')await gate;},save:async value=>{saved.push(plain(value));if(stage==='save')await gate;}},
    addScopeNote:(_turn,value)=>notes.push(plain(value)),addTurn:()=>({}),persist(){},stream:(body,_turn,_index,scope)=>bodies.push({body:plain(body),scope:plain(scope)}),Date,JSON};
  vm.createContext(env);vm.runInContext(declaration(read('workbench.js'),'submit')+';this.submit=submit;',env);
  return {env,resolve,bodies,notes,saved,initial:plain(applied),mutate(){applied.valid_at='2030-01-01T00:00:00.000Z';applied.include_unknown=true;applied.filters.value='华南';applied.applicability.platforms.values.push('平台乙');controls['qa-mode'].value='hybrid';}};
}

for(const stage of ['ensure','save'])test('QA captures one deep scope before pending '+stage+' and keeps body, history and note identical',async()=>{
  const f=submissionFixture(stage),pending=f.env.submit();await settle();f.mutate();f.resolve();await pending;
  assert.equal(f.env.reads,1);assert.deepEqual(f.notes,[f.initial]);assert.deepEqual(f.saved[0][0].scope,f.initial);
  assert.deepEqual(plain(f.env.qaHistory[0].scope),f.initial);assert.deepEqual(f.bodies[0].scope,f.initial);
  assert.deepEqual(Object.fromEntries(Object.keys(f.initial).map(key=>[key,f.bodies[0].body[key]])),f.initial);
  assert.equal(f.bodies[0].body.retrieval_mode,'keyword');assert.equal(f.bodies[0].body.include_references,false);
});

test('historical semantic and hybrid submission fail explicitly without changing applied scope; keyword keeps both axes',async()=>{
  for(const mode of ['semantic','hybrid']){
    const f=submissionFixture();f.env.$c('qa-mode').value=mode;await f.env.submit();
    assert.equal(f.bodies.length,0);assert.match(f.env.summary.textContent,/历史时点仅支持关键词/);assert.deepEqual(plain(f.env.scope()),f.initial);
  }
  const f=submissionFixture();await f.env.submit();assert.equal(f.bodies[0].body.valid_at,f.initial.valid_at);assert.equal(f.bodies[0].body.known_at,f.initial.known_at);
});

test('failed QA stream retry reuses its captured body and full scope after filter edits',async()=>{
  const body={valid_at:'2024-01-01T00:00:00.000Z',known_at:null,include_unknown:false,query:'问题',retrieval_mode:'keyword'},scope={valid_at:body.valid_at,known_at:null,include_unknown:false,filters:null};
  let replay;const sent=[],records=[],turn={dataset:{},replaceChildren(){},append(){}};
  const env={busy:false,preparing:false,sessionManager:null,current:'p',serial:0,scope:()=>({valid_at:'2030-01-01'}),qaHistory:records,summary:{textContent:''},progress:{textContent:''},live:{textContent:''},send:{disabled:false},
    window:{ScopeFields:fields,RequestDeadline:require(path.join(web,'request-deadline.js'))},provenance:{close(){}},AbortController,
    document:{createElement:()=>({dataset:{}})},markDraft(){},persist(){},addFailure:(_target,_message,callback)=>{replay=callback;},endpoint:x=>x,
    fetch:async(_url,init)=>{sent.push(JSON.parse(init.body));throw Error('offline');},Error};
  vm.createContext(env);vm.runInContext(declaration(read('workbench.js'),'stream')+';this.stream=stream;',env);
  await env.stream(body,turn,0,scope);assert.deepEqual(plain(records[0].scope),scope);await replay();
  assert.deepEqual(sent,[body,body]);assert.deepEqual(plain(records[0].scope),scope);
});

test('loaded old user turns keep their original business/system time and unknown choice',()=>{
  const original={valid_at:'2020-01-01T00:00:00.000Z',known_at:'2021-01-01T00:00:00.000Z',include_unknown:false},messages=[{kind:'user',text:'旧问题',scope:original}],notes=[];
  const env={qaHistory:[],sessionManager:{},transcript:{replaceChildren(){}},hero:{},addTurn:()=>({}),addScopeNote:(_turn,value)=>notes.push(plain(value)),atEnd(){},scope:()=>{throw Error('must not reread current controls');}};
  vm.createContext(env);vm.runInContext(declaration(read('workbench.js'),'loadHistory')+';this.load=loadHistory;',env);env.load(messages);
  assert.deepEqual(notes,[original]);assert.deepEqual(messages[0].scope,original);
});

test('temporal provenance labels use explicit metadata and never infer inheritance from source ids or extraction timestamps',()=>{
  assert.equal(typeof temporal.provenance,'function');
  assert.equal(temporal.provenance({metadata:{effective_time_source:'explicit'}}),'明确维护');
  assert.equal(temporal.provenance({metadata:{effective_time_source:'published_at'}}),'发布时间推定');
  assert.equal(temporal.provenance({metadata:{effective_time_source:'extracted'}}),'原文抽取');
  assert.equal(temporal.provenance({metadata:{effective_time_source:'source_inherited'}}),'来源继承');
  for(const row of [{source_id:'d'}, {metadata:{extracted_at:'2020-01-01'}}, {valid_from:'2020-01-01'}, {metadata:{effective_time_source:'unrecognized'}}])assert.equal(temporal.provenance(row),'未知');
  assert.equal(temporal.interval({}),'未知 → 未设结束时间');
});

test('actual extracted time provenance requires matching current boundaries and source text with confidence',()=>{
  const row={valid_from:'2020-01-01T00:00:00.000000Z',valid_until:'2022-01-01T00:00:00.000000Z',metadata:{
    valid_from:'2020-01-01',valid_until:'2022-01-01',temporal_confidence:.9,temporal_source_text:'自2020年起至2022年之前'}};
  assert.equal(temporal.provenance(row),'原文抽取');
  assert.equal(temporal.provenance({...row,valid_from:'2021-01-01'}),'未知');
  assert.equal(temporal.provenance({...row,metadata:{...row.metadata,temporal_confidence:.4}}),'未知');
  assert.equal(temporal.provenance({...row,metadata:{...row.metadata,temporal_source_text:''}}),'未知');
  assert.equal(temporal.provenance({...row,metadata:{...row.metadata,valid_from:'每年年初'}}),'未知');
  assert.equal(temporal.provenance({...row,metadata:{...row.metadata,valid_until:'2019-01-01'}}),'未知');
});

test('shared controls replace the duplicate QA date and time helper loads before all consumers with new versions',()=>{
  const html=read('index.html');assert.equal(html.includes('id="qa-as-of"'),false,'QA duplicate business date is removed');
  for(const id of ['scope-time-mode','scope-valid-at-field','scope-include-unknown','scope-time-help','scope-time-applied'])assert.ok(html.includes('id="'+id+'"'),id);
  for(const file of ['workspace-context-ui.js','workbench.js','evidence-inspector.js'])assert.ok(html.includes('/assets/'+file+'?v=unified-temporal-20261028-1'),file);
  assert.ok(html.includes('/assets/temporal-scope.js?v=unified-temporal-20261028-2'),'temporal helper DST preservation version');
  assert.ok(html.includes('/assets/workspace.js?v=graph-exploration-20261028-1'),'workspace includes concurrent graph updates');
  assert.ok(html.includes('/assets/workspace-context.css?v=unified-temporal-20261028-2'),'responsive scope styles version');
  for(const consumer of ['workbench.js','workspace-context-ui.js','workspace.js','evidence-inspector.js'])assert.ok(html.indexOf('/assets/temporal-scope.js?')<html.indexOf('/assets/'+consumer+'?'));
  assert.match(read('workspace.js'),/系统修订时间/);assert.doesNotMatch(read('workspace.js'),/知识时间/);
  assert.match(read('evidence-inspector.js'),/未设结束时间|TemporalScope|temporal\.interval/);
});
