const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/workspace-context.js');
const context=fs.existsSync(file)?require(file):{};
test('scope is applied explicitly and isolated by project and owning page',()=>{
  assert.equal(typeof context.createStore,'function','page context store is missing');
  const store=context.createStore();
  store.edit('a','qa',{filters:{field:'metadata.city',op:'eq',value:'杭州'}});
  assert.equal(store.read('a','qa').filters,null);
  store.apply('a','qa');
  assert.equal(store.read('a','qa').filters.value,'杭州');
  assert.equal(store.read('a','sources').filters,null);
  assert.equal(store.read('b','qa').filters,null);
  assert.equal(store.read('a','runtime').filters,null);
  assert.equal(store.read('a','ontology-model').filters,null);
});
test('cancelled drafts cannot affect requests, invalid JSON preserves applied scope',()=>{
  const store=context.createStore();
  store.edit('a','search',{filters:{and:[{field:'x',op:'eq',value:1}]}});store.apply('a','search');
  store.edit('a','search',{filters:'{bad'});
  assert.throws(()=>store.apply('a','search'),/JSON/);
  assert.equal(store.read('a','search').filters.and[0].value,1);
  store.cancel('a','search');
  assert.deepEqual(store.draft('a','search'),store.read('a','search'));
});
test('request tickets reject project/page/scope generations including A B A changes',()=>{
  const store=context.createStore();
  const ticket=store.ticket('a','search');
  store.edit('a','search',{applicability:{platforms:{mode:'specific',values:['甲']}}});store.apply('a','search');
  store.reset('a','search');
  assert.equal(store.accepts(ticket,'a','search'),false);
  assert.equal(store.accepts(store.ticket('a','qa'),'b','qa'),false);
  assert.equal(store.accepts(store.ticket('a','qa'),'a','sources'),false);
});
test('explicit transfer copies compatible scope, removal and project deletion erase only owned state',()=>{
  const store=context.createStore();
  store.edit('a','sources',{filters:{field:'metadata.region',op:'eq',value:'华东'},known_at:'2025-01-01'});store.apply('a','sources');
  store.copy('a','sources','qa');
  assert.equal(store.read('a','qa').filters.value,'华东');
  store.reset('a','sources');assert.equal(store.read('a','qa').filters.value,'华东');
  store.removeProject('a');assert.equal(store.read('a','qa').filters,null);
});
test('project creation is empty and account protections preserve own display name only',()=>{
  assert.equal(typeof context.projectPayload,'function');
  assert.deepEqual(context.projectPayload('  新项目  '),{name:'新项目',ontology_mode:'discovery',use_default_ontology:false});
  assert.throws(()=>context.projectPayload(' '),/名称/);
  assert.deepEqual(context.accountPolicy({id:'me',role:'admin'},{id:'me',role:'admin'}),{edit:true,role:false,status:false,password:false,delete:false});
  assert.equal(context.accountPolicy({id:'a',role:'admin'},{id:'s',role:'superadmin'}).edit,false);
  assert.equal(context.accountPolicy({id:'s',role:'superadmin'},{id:'t',role:'superadmin'}).delete,false);
  assert.equal(context.accountPolicy({id:'v',role:'viewer'},{id:'a',role:'admin'}).edit,false);
});
test('semantic and hybrid results require the requested Milvus channel without fallback',()=>{
  assert.equal(typeof context.requireRetrievalMode,'function');
  assert.throws(()=>context.requireRetrievalMode('hybrid',{active_mode:'keyword',degraded:true}),/Milvus/);
  assert.throws(()=>context.requireRetrievalMode('semantic',{active_modes:['keyword']}),/Milvus/);
  assert.doesNotThrow(()=>context.requireRetrievalMode('keyword',{active_mode:'keyword'}));
  assert.doesNotThrow(()=>context.requireRetrievalMode('hybrid',{active_mode:'hybrid'}));
});
test('applied scopes survive a page reload in the login session and logout clears them',()=>{
  const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const first=context.createStore(storage);first.edit('a','qa',{filters:{field:'x',op:'eq',value:1}});first.apply('a','qa');
  const restored=context.createStore(storage);assert.equal(restored.read('a','qa').filters.value,1);
  restored.clear();assert.equal(context.createStore(storage).read('a','qa').filters,null);
});

test('scope store rejects obsolete list dimensions and ignores obsolete session entries',()=>{
  const stored=new Map([['kg_workspace_scope_v1',JSON.stringify([
    [JSON.stringify(['a','search']),{applicability:{platforms:['旧值']}}]])]]);
  const store=context.createStore({getItem:key=>stored.get(key),setItem(){}});
  assert.equal(store.read('a','search').applicability,undefined);
  store.edit('a','search',{applicability:{platforms:['旧值']}});
  assert.throws(()=>store.apply('a','search'),/适用范围/);
  assert.equal(store.read('a','search').applicability,undefined);
});

test('scope UI restores applied state without importing hidden form values',()=>{
  const vm=require('node:vm'),store=context.createStore();
  const applied={field:'metadata.city',op:'eq',value:'杭州'};
  store.edit('a','search',{filters:applied});store.apply('a','search');
  const fields=new Map();
  const get=id=>{
    if(!fields.has(id))fields.set(id,{value:'',open:false,textContent:'',
      classList:{toggle(){}},replaceChildren(){},append(){},prepend(){},
      setAttribute(){},addEventListener(){},focus(){}});
    return fields.get(id);
  };
  get('filters').value=JSON.stringify({field:'metadata.city',op:'eq',value:'旧输入'});
  const bridge={page:'search',store,isContentPage:()=>true,read:project=>store.read(project,'search'),
    register(value){value.restore();}};
  vm.runInNewContext(fs.readFileSync('knowledge_service/web/workspace-context-ui.js','utf8'),{
    window:{WorkspaceContext:bridge,ScopeFields:{countConditions:()=>1}},
    document:{getElementById:get,addEventListener(){},createElement:()=>({})},
    current:'a',showRuntimeView(){},Date,JSON,Object,Array,Error,
  });
  assert.deepEqual(store.read('a','search').filters,applied);
  assert.deepEqual(JSON.parse(get('filters').value),applied);
});
