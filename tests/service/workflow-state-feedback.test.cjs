const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {fixture:ledger}=require('./fixtures/ledger-ui.cjs');
const read=name=>fs.readFileSync('knowledge_service/web/'+name,'utf8');
const workbench=read('workbench.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};

test('ledger only offers supported kinds and initializes/switches/resets to the actual query kind',async()=>{
  const env=ledger(),{context,get,created,calls}=env;
  assert.doesNotMatch(created.find(node=>node.className==='record-library-tools').innerHTML,/<option value="">/);
  assert.equal(get('record-kind').value,'entity');
  get('record-kind').value='relation';await get('record-kind').onchange();
  assert.deepEqual(Array.from(calls.findLast(call=>call.url.endsWith('/records/page')&&call.body.limit===50).body.kinds),['relation']);
  get('record-kind').value='';get('record-kind').onchange();
  assert.equal(get('record-kind').value,'relation');
  get('tab-records').className='hidden';context.current='other';
  for(const change of get('project').events.change)change();
  assert.equal(get('record-kind').value,'relation');
});

function snapshotFixture(){
  const nodes=new Map(),calls=[],messages=[],reads=[],cards=[];
  let restoreResponse=()=>({recovery_snapshot_name:'恢复点'});
  class Element{
    constructor(){this.value='';this.children=[];this.dataset={};this.style={};this.className='';this.hidden=false;this.textContent='';this.classList={add(){},remove(){},contains:name=>this.className.split(' ').includes(name)};}
    set textContent(value){this._text=value;this.html='';this.children=[];}
    get textContent(){return this._text||'';}
    set innerHTML(value){this.html=value;this.children=[];if(this===get('snapshot-cards')){
      cards.length=0;for(const [,id] of value.matchAll(/data-snapshot-restore="([^"]+)"/g)){
        const card=new Element(),actions=new Element(),button=new Element();button.dataset.snapshotRestore=id;
        button.closest=selector=>selector==='.eval-snapshot-card'?card:actions;
        card.querySelector=()=>card.children.find(child=>child.className==='eval-restore-confirm')||null;
        cards.push({card,actions,button});
      }
    }}
    get innerHTML(){return this.html||'';}
    querySelector(selector){return this.controls?.[selector]??(this.controls??={})[selector]??((this.controls??={})[selector]=new Element());}
    querySelectorAll(){return this===get('snapshot-cards')?cards.map(item=>item.button):[];}
    appendChild(child){this.children.push(child);}scrollIntoView(){}remove(){this.removed=true;}
  }
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  get('project').onchange=()=>{context.current=get('project').value;};get('project').value='A';
  get('tab-runtime').className='hidden';get('tab-evaluation').className='hidden';
  const document={createElement:()=>new Element(),getElementById:get,querySelector:()=>null,dispatchEvent(){}};
  const context=vm.createContext({window:{RuntimeView:{current:'overview',on(){}}},document,$:get,current:'A',scope:()=>({}),CustomEvent:class{},
    endpoint:suffix=>'/api/projects/'+context.current+suffix,status:(...args)=>messages.push(args),bind:(id,fn)=>{get(id).onclick=fn;},
    esc:value=>String(value),api:async(url,body,method)=>{calls.push({url,body,method});if(method==='GET'){const result=deferred();reads.push(result);return result.promise;}return restoreResponse();}});
  vm.runInContext(workbench.slice(0,workbench.indexOf('let dashboardRequest='))+workbench.slice(workbench.indexOf('async function snapshots('),workbench.indexOf("bind('run-evaluation'")),context);
  return {context,get,calls,messages,reads,cards,load:()=>context.snapshots(),respondRestore(fn){restoreResponse=fn;},
    switch(project,visible=false){get('tab-runtime').className=visible?'':'hidden';get('tab-evaluation').className=visible?'':'hidden';context.window.RuntimeView.current=visible?'snapshots':'overview';get('project').value=project;get('project').onchange();},
    resolve(index,name){reads[index].resolve({snapshots:[{id:name,name,created_at:'2026-11-01T12:00:00Z'}]});}};
}

test('project switch clears snapshot cards immediately and does not read a hidden backup view',async()=>{
  const env=snapshotFixture();const loading=env.load();env.resolve(0,'old-A');await loading;
  env.switch('B');assert.equal(env.get('snapshot-cards').textContent,'');
  assert.equal(env.get('snapshot-cards').innerHTML,'');assert.equal(env.calls.length,1);
  env.switch('C',true);assert.equal(env.calls.length,2);assert.equal(env.calls[1].url,'/api/projects/C/snapshots');
  env.resolve(1,'C');await settle();
});

test('snapshot reads discard old A after A to B to A and earlier reads of the same project',async()=>{
  const env=snapshotFixture(),old=env.load();env.switch('B');env.switch('A');
  const fresh=env.load();env.resolve(1,'fresh-A');await fresh;env.resolve(0,'old-A');await old;
  assert.match(env.get('snapshot-cards').innerHTML,/fresh-A/);assert.doesNotMatch(env.get('snapshot-cards').innerHTML,/old-A/);
  const earlier=env.load(),latest=env.load();env.resolve(3,'latest');await latest;env.resolve(2,'earlier');await earlier;
  assert.match(env.get('snapshot-cards').innerHTML,/latest/);assert.doesNotMatch(env.get('snapshot-cards').innerHTML,/earlier/);
});

test('stale restore handler refuses to open confirmation after a project round trip',async()=>{
  const env=snapshotFixture(),loading=env.load();env.resolve(0,'saved');await loading;
  const item=env.cards[0];env.switch('B');env.switch('A');await item.button.onclick();
  assert.equal(item.card.children.length,0);assert.equal(env.calls.filter(call=>call.body).length,0);
});

test('restore confirmation checks its original generation again before any POST',async()=>{
  const env=snapshotFixture(),loading=env.load();env.resolve(0,'saved');await loading;
  const item=env.cards[0];await item.button.onclick();const warning=item.card.children[0];
  env.switch('B');env.switch('A');const restoring=warning.querySelector('.eval-confirm-yes').onclick();await settle();
  const writes=env.calls.filter(call=>call.body).length;
  if(env.reads[1])env.resolve(1,'cleanup');await restoring;
  assert.equal(writes,0);
});

test('explicit current restore posts to its captured project and then refreshes',async()=>{
  const env=snapshotFixture(),loading=env.load();env.resolve(0,'saved');await loading;
  const item=env.cards[0];await item.button.onclick();
  const restoring=item.card.children[0].querySelector('.eval-confirm-yes').onclick();await settle();
  assert.equal(env.calls[1].url,'/api/projects/A/snapshots/saved/restore');assert.deepEqual(Object.keys(env.calls[1].body),[]);
  env.resolve(1,'after-restore');await restoring;assert.match(env.messages[0][0],/已恢复/);
});

test('successful restore reports a failed list refresh without suggesting that restoration failed',async()=>{
  const env=snapshotFixture(),loading=env.load();env.resolve(0,'saved');await loading;
  const item=env.cards[0];await item.button.onclick();
  const restoring=item.card.children[0].querySelector('.eval-confirm-yes').onclick();await settle();
  env.reads[1].reject(Error('网络暂不可用'));await restoring;
  assert.match(env.messages.at(-1)[0],/已恢复.*列表刷新失败.*网络暂不可用/);assert.equal(env.messages.at(-1)[1],true);
  assert.equal(env.calls.filter(call=>call.body).length,1);
  const confirmation=item.card.children[0];
  assert.match(confirmation.textContent,/已恢复.*刷新/);
  assert.doesNotMatch(confirmation.innerHTML,/<button/);assert.doesNotMatch(confirmation.textContent,/正在恢复/);
});

test('a same-project list refresh during restoration does not discard its successful receipt',async()=>{
  const env=snapshotFixture(),gate=deferred(),loading=env.load();env.resolve(0,'saved');await loading;
  env.respondRestore(()=>gate.promise);const item=env.cards[0];item.button.onclick();
  const restoring=item.card.children[0].querySelector('.eval-confirm-yes').onclick();
  const refreshed=env.load();env.resolve(1,'intermediate-list');await refreshed;
  gate.resolve({recovery_snapshot_name:'原项目恢复点'});await settle();
  const receipt=env.messages.find(([text])=>/已恢复到/.test(text));
  if(env.reads[2])env.resolve(2,'after-restoration');await restoring;
  assert.ok(receipt,'completed same-project restoration must still report its receipt');assert.match(receipt[0],/原项目恢复点/);
  assert.match(env.get('snapshot-cards').innerHTML,/after-restoration/);assert.equal(env.calls.filter(call=>call.body).length,1);
});

test('a same-project list refresh during a failed restore still reports its failure',async()=>{
  const env=snapshotFixture(),gate=deferred(),loading=env.load();env.resolve(0,'saved');await loading;
  env.respondRestore(()=>gate.promise);const item=env.cards[0];item.button.onclick();
  const restoring=item.card.children[0].querySelector('.eval-confirm-yes').onclick();
  const refreshed=env.load();env.resolve(1,'fresh-list');await refreshed;
  gate.reject(Error('恢复失败：请求未完成'));await restoring;
  assert.match(env.messages.at(-1)?.[0]||'',/恢复失败：请求未完成/);assert.equal(env.messages.at(-1)[1],true);
});

test('refreshing the list before restore confirmation still rejects the outdated confirmation',async()=>{
  const env=snapshotFixture(),loading=env.load();env.resolve(0,'saved');await loading;
  const item=env.cards[0];item.button.onclick();const confirm=item.card.children[0].querySelector('.eval-confirm-yes');
  const refreshed=env.load();await confirm.onclick();env.resolve(1,'fresh-list');await refreshed;
  assert.equal(env.calls.filter(call=>call.body).length,0);
});

test('a completed restore never updates a different project generation after an A to B to A round trip',async()=>{
  const env=snapshotFixture(),gate=deferred(),loading=env.load();env.resolve(0,'saved');await loading;
  env.respondRestore(()=>gate.promise);const item=env.cards[0];item.button.onclick();
  const restoring=item.card.children[0].querySelector('.eval-confirm-yes').onclick();env.switch('B');env.switch('A');
  gate.resolve({recovery_snapshot_name:'stale-receipt'});await restoring;
  assert.equal(env.messages.length,0);assert.equal(env.reads.length,1);
});

function projectsFixture(){
  const nodes=new Map(),overlays=[],calls=[],messages=[];let activeElement=null;
  class Element{
    constructor(){this.children=[];this.dataset={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.className='';this.classList={add(){},remove(){}};}
    appendChild(child){this.children.push(child);overlays.push(child);}setAttribute(key,value){this[key]=value;}
    removeAttribute(key){delete this[key];}addEventListener(){}remove(){}focus(){activeElement=this;}select(){}
    querySelector(selector){return this.fields?.get(selector)||null;}
    querySelectorAll(selector){return selector==='[data-rename]'?[rename]:[];}
    set innerHTML(value){this.html=value;this.fields=new Map();for(const [,id] of value.matchAll(/id="([^"]+)"/g))this.fields.set('#'+id,new Element());
      for(const name of ['projects-modal-close','projects-modal-feedback'])if(value.includes(name))this.fields.set('.'+name,new Element());}
    get innerHTML(){return this.html||'';}
  }
  const rename=new Element();rename.dataset.rename='A';
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const document={body:new Element(),createElement:()=>new Element(),addEventListener(){},removeEventListener(){}};
  let fail=false;
  const context=vm.createContext({window:{Auth:{whenReady:async()=>{}},WorkspaceContext:{projectPayload:name=>({name})}},document,$:get,current:'A',
    requestAnimationFrame:fn=>fn(),setTimeout:fn=>fn(),esc:value=>String(value),projects:async()=>{},status:(...args)=>messages.push(args),
    api:async(url,body,method)=>{calls.push({url,body,method});if(fail)throw Error('服务暂不可用，请重试');return {projects:[{id:'A',name:'原名称'}]};}});
  vm.runInContext(read('projects-view.js'),context);
  return {context,get,overlays,calls,messages,rename,fail(){fail=true;},active:()=>activeElement};
}

for(const mode of ['create','settings'])test(mode+' validation and network errors remain inside the dialog with fields intact',async()=>{
  const env=projectsFixture();if(mode==='create')env.context.window.ProjectsView.create();else{await env.context.window.ProjectsView.refresh();env.rename.onclick();}
  const modal=env.overlays[0],field=modal.querySelector(mode==='create'?'#create-name':'#projects-rename-input'),submit=modal.querySelector(mode==='create'?'#create-project':'#projects-rename-confirm');
  field.value='   ';env.get('elsewhere').focus();await submit.onclick();
  const feedback=modal.querySelector('.projects-modal-feedback');assert.ok(feedback,'dialog needs a local feedback region');
  assert.equal(feedback.textContent,'请输入项目名称');assert.equal(feedback.role,'alert');assert.equal(env.active(),field);assert.equal(field['aria-invalid'],'true');
  field.value='保留的项目名称';env.fail();await submit.onclick();
  assert.equal(feedback.textContent,'服务暂不可用，请重试');assert.equal(feedback.role,'alert');assert.equal(field.value,'保留的项目名称');assert.equal(submit.disabled,false);
});

for(const admin of [false,true])test('QA index failure gives '+(admin?'admins a build entry':'viewers a repair instruction'),()=>{
  const nodes=new Map(),calls=[],get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:true,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}});return nodes.get(id);};
  const element=()=>({dataset:{},setAttribute(){},append(){},focus(){}});
  const context=vm.createContext({$c:get,window:{Auth:{isAdmin:()=>admin},ProjectVectorIndex:{open:id=>calls.push(id)}},document:{createElement:element},busy:false,atEnd(){},current:'A'});
  const helper=workbench.match(/  function showIndexNotice\([^\n]*\)\{[\s\S]*?\n  \}/)?.[0]||'';
  vm.runInContext(helper+'\n'+workbench.slice(workbench.indexOf('  function addFailure('),workbench.indexOf('  async function stream(')),context);
  context.addFailure({...element(),dataset:{}},'索引维度不匹配',()=>{});
  assert.equal(get('qa-notice').hidden,false);assert.equal(get('qa-build').hidden,!admin);
  if(!admin)assert.match(get('qa-notice-text').textContent,/需要项目管理员修复索引.*修复.*重试/);
  assert.equal(calls.length,0);
});

for(const admin of [false,true])test('QA degraded notice and explicit build action respect the current role '+admin,async()=>{
  const nodes=new Map(),calls=[],get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}});return nodes.get(id);};
  const context=vm.createContext({$c:get,window:{Auth:{isAdmin:()=>admin},ProjectVectorIndex:{open:id=>calls.push(id)}},current:'A'});
  const helper=workbench.match(/  function showIndexNotice\([^\n]*\)\{[\s\S]*?\n  \}/)?.[0]||'';
  vm.runInContext(helper+'\n'+workbench.slice(workbench.indexOf('  function showRetrievalNotice('),workbench.indexOf('  if(window.QaConversations)')),context);
  for(const payload of [{degraded:true},{retrieval_errors:{semantic:'语义索引尚未就绪'}}]){
    context.showRetrievalNotice(payload);assert.equal(get('qa-build').hidden,!admin);
    if(!admin)assert.match(get('qa-notice-text').textContent,/需要项目管理员修复索引.*修复.*重试/);
  }
  assert.equal(calls.length,0);await get('qa-build').listeners.click();assert.equal(calls.length,admin?1:0);
});
