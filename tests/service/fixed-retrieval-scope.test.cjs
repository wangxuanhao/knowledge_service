const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const fields=require('../../knowledge_service/web/scope-fields.js');
const read=name=>fs.readFileSync('knowledge_service/web/'+name,'utf8');
function declaration(source,name){
  const match=source.match(new RegExp('  (?:async )?function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?\\n  \\}'));
  assert.ok(match,'production function '+name+' exists');return match[0];
}
const unsupported={or:[{field:'id',op:'eq',value:'a'},{not:{field:'metadata.region',op:'eq',value:'华东'}}]};

test('search rejects incompatible applied conditions before fetching or clearing visible results',async()=>{
  const controls=new Map(),get=id=>{if(!controls.has(id))controls.set(id,{value:'',textContent:'old results'});return controls.get(id);};
  get('search-mode').value='hybrid';get('query').value='退款';
  const applied={filters:unsupported},before=JSON.stringify(applied);let requests=0;
  const environment={current:'p',ui:{request:0},get,scope:()=>applied,performance:{now:()=>1},window:{ScopeFields:fields,WorkspaceContext:{ticket:()=>({})}},kgTime:()=>()=>{},api:async()=>{requests++;throw Error('request reached server');},endpoint:value=>value,Boolean,Error};
  vm.createContext(environment);vm.runInContext(declaration(read('workspace.js'),'runSearch')+';this.run=runSearch;',environment);
  await assert.rejects(environment.run(),/NOT/);
  assert.equal(requests,0);assert.equal(get('hits').textContent,'old results');
  assert.match(get('search-summary').textContent,/保留/);assert.equal(JSON.stringify(applied),before);
});

test('QA stream validates replay bodies before reserving turns or making requests',async()=>{
  let requests=0;const summary={textContent:''},body={retrieval_mode:'hybrid',filters:unsupported},before=JSON.stringify(body);
  const environment={busy:false,preparing:false,sessionManager:null,current:'p',serial:0,summary,progress:{textContent:''},live:{textContent:''},send:{disabled:false},window:{ScopeFields:fields},qaHistory:[],provenance:{close(){}},AbortController,document:{createElement:()=>({dataset:{}})},markDraft(){},persist(){},addFailure(){},endpoint:value=>value,fetch:async()=>{requests++;throw Error('request reached server');},Error};
  vm.createContext(environment);vm.runInContext(declaration(read('workbench.js'),'stream')+';this.replay=stream;',environment);
  await environment.replay(body,{dataset:{state:'error'},replaceChildren(){},append(){}});
  assert.equal(requests,0);assert.equal(environment.qaHistory.length,0);
  assert.match(summary.textContent,/NOT/);assert.match(summary.textContent,/保留/);
  assert.equal(JSON.stringify(body),before);
});

function submissionFixture(save){
  const controls={'qa-as-of':{value:''},generate:{checked:false},'qa-mode':{value:'hybrid'}};
  const environment={busy:false,preparing:false,current:'p',serial:0,input:{value:'问题 A'},send:{disabled:false},summary:{textContent:''},progress:{textContent:''},qaHistory:[],scope:()=>({}),$c:id=>controls[id],window:{ScopeFields:fields},sessionManager:{loading:false,state:{conflict:false},ensureActive:async()=>{},save},addScopeNote(){},addTurn(){return {};},persist(){},stream(){environment.streams++;},streams:0,Date};
  vm.createContext(environment);vm.runInContext(declaration(read('workbench.js'),'submit')+';this.submit=submit;',environment);return environment;
}
test('an unsaved question remains in history and never starts a stream on authentication failure',async()=>{
  const environment=submissionFixture(async()=>{throw Object.assign(new Error('登录已失效'),{status:401});});
  await environment.submit();assert.equal(environment.streams,0);assert.equal(environment.qaHistory[0].text,'问题 A');assert.match(environment.summary.textContent,/未保存/);assert.equal(environment.preparing,false);
});
test('an older failed turn cannot retry while a new question is waiting for its save',async()=>{
  const environment={busy:false,preparing:true,qaHistory:[],sessionManager:{loading:false,state:{conflict:false}}};
  vm.createContext(environment);vm.runInContext(declaration(read('workbench.js'),'stream')+';this.retry=stream;',environment);
  await environment.retry({});assert.equal(environment.preparing,true);
});
test('a late save from an old selection cannot release or overwrite a newer pending submission',async()=>{
  const pending=[];const environment=submissionFixture(()=>new Promise((resolve,reject)=>pending.push({resolve,reject})));
  const first=environment.submit();await new Promise(setImmediate);assert.equal(environment.streams,0);
  // The conversation-change hook cancels A and activates B before its submission.
  environment.serial++;environment.preparing=false;environment.qaHistory=[];environment.input.value='问题 B';
  const second=environment.submit();await new Promise(setImmediate);
  pending[0].reject(new Error('旧会话保存失败'));await first;
  assert.equal(environment.preparing,true);assert.equal(environment.send.disabled,true);assert.equal(environment.summary.textContent,'');assert.equal(environment.streams,0);
  pending[1].resolve();await second;assert.equal(environment.preparing,false);assert.equal(environment.streams,1);assert.equal(environment.qaHistory[0].text,'问题 B');
});

test('opened content filter rail discovers business metadata for current retrieval and graph',async()=>{
  const source=read('workspace.js');
  for(const sample of [{page:'search',purpose:'retrieval',mode:'hybrid',expected:1},
    {page:'qa',purpose:'retrieval',mode:'keyword',expected:1},
    {page:'search',purpose:'graph',mode:'hybrid',expected:1},
    {page:'sources',purpose:'retrieval',mode:'hybrid',expected:1},
    {page:'records',purpose:'retrieval',mode:'hybrid',expected:1},
    {page:'qa',purpose:'retrieval',mode:'keyword',knownAt:'2025-01-01',expected:1}]){
    const values={'scope-purpose':sample.purpose,'search-mode':sample.mode,'qa-mode':sample.mode,'scope-known-at':sample.knownAt||''};
    let requests=0;const original={filters:unsupported,known_at:sample.knownAt||null},before=JSON.stringify(original);
    const environment={current:'p',get:id=>({value:values[id]||''}),details:{open:true},cb:{facets:[],conditions:[]},scope:()=>original,window:{ScopeFields:fields,WorkspaceContext:{page:sample.page,isContentPage:()=>true}},endpoint:value=>value,api:async()=>{requests++;return {fields:[{field:'metadata.region'}],records:1};},renderFacetChoices(){},renderCB(){},JSON};
    vm.createContext(environment);vm.runInContext(declaration(source,'usesPostgresFields')+'\n'+declaration(source,'discoverMetadata')+';this.discover=discoverMetadata;',environment);
    await environment.discover();assert.equal(requests,sample.expected,JSON.stringify(sample));assert.equal(JSON.stringify(original),before);
  }
});

test('UI keeps explicit graph purpose and bumps each changed runtime resource',()=>{
  const html=read('index.html');
  assert.ok(html.includes('id="scope-purpose"'),'scope purpose selector exists');assert.ok(html.includes('<option value="graph">图谱'),'PG graph purpose exists');
  assert.ok(html.includes('id="scope-compatibility"'),'scope incompatibility remains visible');
  assert.ok(html.includes('#scope [hidden]{display:none!important}'),'page-owned filter controls stay hidden despite existing grid/flex CSS');
  for(const file of ['scope-fields.js','workspace-context-ui.js'])assert.ok(html.includes('/assets/'+file+'?v=authority-retrieval-20261021-2'),file+' version');
  assert.ok(html.includes('/assets/workspace.js?v=graph-visual-ontology-sections-20261027-2'),'workspace.js version');
  assert.ok(html.includes('/assets/workbench.js?v=ontology-sections-20261027-2'),'workbench.js version');
});

test('applying the shared editor preserves dedicated ontology/kind scope and original metadata AST',()=>{
  const applied={ontology_scope:'ids',ontology_ids:['o1'],kinds:['entity'],filters:unsupported,applicability:{platforms:{mode:'specific',values:['旧平台']}}},before=JSON.stringify(applied);
  const controls={'cb-raw-json':JSON.stringify(unsupported),'filters':JSON.stringify(unsupported)};
  const environment={current:'p',context:{read:()=>applied},get:id=>({value:controls[id]||'',focus(){}}),window:{},JSON,Date,Error};
  vm.createContext(environment);vm.runInContext(declaration(read('workspace-context-ui.js'),'capture')+';this.captureScope=capture;',environment);
  const captured=environment.captureScope();
  assert.equal(captured.ontology_scope,'ids');assert.equal(JSON.stringify(captured.ontology_ids),'["o1"]');assert.equal(JSON.stringify(captured.kinds),'["entity"]');
  assert.equal(JSON.stringify(captured.filters),JSON.stringify(unsupported));assert.equal(JSON.stringify(applied),before);
  assert.equal(captured.applicability,undefined,'cleared applicability inputs do not inherit the old restriction');
});

test('switching to fixed field options retains an already selected PG condition without offering other metadata',()=>{
  const environment={window:{ScopeFields:fields},visibleFacets:()=>[],fieldLabel:fields.label};
  vm.createContext(environment);vm.runInContext(declaration(read('workspace.js'),'fieldOptions')+';this.options=fieldOptions;',environment);
  const options=environment.options('metadata.region');
  assert.equal(options.length,7);assert.equal(options.at(-1).v,'metadata.region');assert.match(options.at(-1).l,/保留/);
  assert.equal(environment.options().length,6);
});
