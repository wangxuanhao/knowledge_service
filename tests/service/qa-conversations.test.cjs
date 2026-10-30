const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/qa-conversations.js');
const lib=fs.existsSync(file)?require(file):{};
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function storage(){const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key),values};}
function server(){
  const rows=new Map();let sequence=0;const writes=[];
  const key=(project,id)=>project+'/'+id;
  function seed(project,id,messages=[],title=id){const item={id,title,version:1,created_at:'2026-10-20T10:00:00Z',updated_at:'2026-10-20T10:00:00Z',message_count:messages.length,messages:clone(messages)};rows.set(key(project,id),item);return item;}
  const api={
    async list(project,{q='',offset=0,limit=50}={}){const all=[...rows.entries()].filter(([k])=>k.startsWith(project+'/')).map(([,v])=>v).filter(v=>v.title.includes(q));return {conversations:clone(all.slice(offset,offset+limit)),total:all.length,has_more:offset+limit<all.length};},
    async create(project,title){return {conversation:clone(seed(project,'new-'+(++sequence),[],title||'新会话'))};},
    async get(project,id){const row=rows.get(key(project,id));if(!row)throw Object.assign(new Error('不存在'),{status:404});return {conversation:clone(row)};},
    async save(project,id,body){writes.push(clone({project,id,...body}));const row=rows.get(key(project,id));if(body.expected_version!==row.version)throw Object.assign(new Error('冲突'),{status:409});Object.assign(row,{messages:clone(body.messages),message_count:body.messages.length,version:row.version+1});return {conversation:clone(row)};},
    async rename(project,id,body){const row=rows.get(key(project,id));if(body.expected_version!==row.version)throw Object.assign(new Error('冲突'),{status:409});Object.assign(row,{title:body.title,version:row.version+1});return {conversation:clone(row)};},
    async remove(project,id,body){const row=rows.get(key(project,id));if(body.expected_version!==row.version)throw Object.assign(new Error('冲突'),{status:409});rows.delete(key(project,id));}
  };return {api,rows,writes,seed};
}
function controller(api,options={}){assert.equal(typeof lib.createController,'function','controller API must exist');return lib.createController({api,getActor:()=> 'alice',storage:storage(),...options});}
test('client encodes project, conversation IDs and literal title searches, preserving HTTP errors',async()=>{
  assert.equal(typeof lib.createClient,'function','HTTP client API must exist');
  const calls=[];const fetch=async(url,init={})=>{calls.push({url,init});return {ok:true,status:init.method==='DELETE'?204:200,json:async()=>({conversations:[],conversation:{id:'x'}})};};
  const api=lib.createClient({fetch});await api.list('项目/a',{q:'[a] & b',offset:50});await api.get('项目/a','id/b');await api.save('项目/a','id/b',{expected_version:2,messages:[]});await api.rename('项目/a','id/b',{expected_version:3,title:'x'});await api.remove('项目/a','id/b',{expected_version:4});
  assert.equal(calls[0].url,'/api/projects/%E9%A1%B9%E7%9B%AE%2Fa/qa/conversations?limit=50&offset=50&q=%5Ba%5D%20%26%20b');
  assert.equal(calls[1].url,'/api/projects/%E9%A1%B9%E7%9B%AE%2Fa/qa/conversations/id%2Fb');
  assert.equal(calls[2].init.method,'PUT');assert.deepEqual(JSON.parse(calls[2].init.body),{expected_version:2,messages:[]});assert.equal(calls[3].init.method,'PATCH');
  assert.equal(calls[4].url,'/api/projects/%E9%A1%B9%E7%9B%AE%2Fa/qa/conversations/id%2Fb?expected_version=4');
  const broken=lib.createClient({fetch:async()=>({ok:false,status:422,json:async()=>({detail:'消息数量超过限制'})})});
  await assert.rejects(broken.save('p','x',{messages:[]}),error=>error.status===422&&/超过限制/.test(error.message));
});
test('project loading waits for authentication and empty projects do not issue requests',async()=>{
  const ready=deferred();let calls=0;const s=server();const original=s.api.list;s.api.list=async(...args)=>{calls++;return original(...args);};
  const c=controller(s.api,{authReady:ready.promise});const loading=c.setProject('p');await tick();assert.equal(calls,0);ready.resolve();await loading;assert.equal(calls,1);await c.setProject('');assert.equal(calls,1);assert.equal(c.activeId,null);
});
test('two clients restore server conversations without local transcript storage',async()=>{
  const s=server();const frozen={kind:'assistant',text:'回答',references:[{snapshot_id:'frozen-1',quote:'当时原文'}]};s.seed('p','a',[{kind:'user',text:'问题'},frozen]);
  const first=controller(s.api);await first.setProject('p');await first.save([{kind:'user',text:'新问题'},frozen]);
  let restored;const second=controller(s.api,{onActivate:messages=>{restored=messages;}});await second.setProject('p');assert.equal(second.activeId,'a');assert.deepEqual(restored,[{kind:'user',text:'新问题'},frozen]);
});
test('concurrent ensureActive calls share one new empty conversation',async()=>{
  const s=server();const pending=deferred();let creates=0;s.api.create=async()=>{creates++;return pending.promise;};const c=controller(s.api);await c.setProject('p');const a=c.ensureActive(),b=c.ensureActive();await tick();assert.equal(creates,1);pending.resolve({conversation:s.seed('p','fresh')});assert.equal((await a).id,'fresh');assert.equal((await b).id,'fresh');assert.equal(c.activeId,'fresh');
});
test('saving snapshots is serial and each queued write uses the preceding server version',async()=>{
  const s=server();s.seed('p','a');const gate=deferred();const original=s.api.save;let saves=0;s.api.save=async(...args)=>{if(++saves===1)await gate.promise;return original(...args);};
  const c=controller(s.api);await c.setProject('p');const first=[{kind:'user',text:'one',references:[{quote:'frozen'}]}];const p1=c.save(first);first[0].references[0].quote='mutated';const p2=c.save([{kind:'user',text:'two'}]);await tick();assert.equal(saves,1);gate.resolve();await Promise.all([p1,p2]);
  assert.deepEqual(s.writes.map(w=>w.expected_version),[1,2]);assert.equal(s.writes[0].messages[0].references[0].quote,'frozen');assert.equal(s.rows.get('p/a').messages[0].text,'two');
});
test('409 is reported, preserves local content and prevents queued overwrites until explicit reload',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'server'}]);const errors=[];let activations=0,last;
  const c=controller(s.api,{onError:message=>errors.push(message),onActivate:()=>activations++,onState:state=>{last=state;}});await c.setProject('p');s.rows.get('p/a').version=7;
  const one=c.save([{kind:'user',text:'unsaved'}]),two=c.save([{kind:'user',text:'also unsaved'}]);await assert.rejects(one,e=>e.status===409);await assert.rejects(two,e=>e.status===409);assert.equal(s.writes.length,1);assert.equal(activations,1);assert.equal(last.conflict,true);assert.ok(errors.some(e=>/冲突|其他设备/.test(e)));
  await c.reloadActive();assert.equal(last.conflict,false);await c.save([{kind:'user',text:'after reload'}]);assert.equal(s.writes.at(-1).expected_version,7);
});
test('validation failures reject without truncating message arrays',async()=>{
  const s=server();s.seed('p','a');let received;s.api.save=async(p,id,body)=>{received=body.messages;throw Object.assign(new Error('消息数量超过限制'),{status:422});};const c=controller(s.api);await c.setProject('p');const messages=Array.from({length:501},(_,i)=>({kind:'user',text:String(i)}));await assert.rejects(c.save(messages),/超过限制/);assert.equal(received.length,501);
});
test('late switch responses cannot activate an earlier selection',async()=>{
  const s=server();s.seed('p','a');s.seed('p','b');const gate=deferred(),original=s.api.get;s.api.get=(p,id)=>id==='b'?gate.promise:original(p,id);const activated=[];const c=controller(s.api,{onActivate:(messages,row)=>activated.push(row?.id)});await c.setProject('p');const b=c.switchTo('b');await c.switchTo('a');gate.resolve({conversation:clone(s.rows.get('p/b'))});await b;assert.equal(c.activeId,'a');assert.equal(activated.at(-1),'a');
});
test('project and actor changes isolate late reads and local active pointers',async()=>{
  const s=server();s.seed('p','a');s.seed('q','b');const gate=deferred(),original=s.api.get;let actor='alice';s.api.get=(p,id)=>p==='p'?gate.promise:original(p,id);const store=storage(),activated=[];const c=controller(s.api,{getActor:()=>actor,storage:store,onActivate:(m,row)=>activated.push(row?.id)});const old=c.setProject('p');await tick();actor='bob';await c.setProject('q');gate.resolve({conversation:clone(s.rows.get('p/a'))});await old;assert.equal(c.activeId,'b');assert.equal(activated.at(-1),'b');assert.ok([...store.values.keys()].every(key=>!key.includes('kg_qa_v1_')));assert.ok([...store.values.values()].every(value=>value==='b'));
});
test('late saves update their own record without replacing a newly selected transcript',async()=>{
  const s=server();s.seed('p','a');s.seed('p','b',[{kind:'user',text:'B'}]);const gate=deferred(),original=s.api.save;s.api.save=async(...args)=>{await gate.promise;return original(...args);};const activated=[];const c=controller(s.api,{onActivate:(m,row)=>activated.push(row?.id)});await c.setProject('p');const save=c.save([{kind:'user',text:'A'}]);await c.switchTo('b');gate.resolve();await save;assert.equal(c.activeId,'b');assert.equal(activated.at(-1),'b');assert.equal(s.rows.get('p/a').messages[0].text,'A');
});
test('delete waits for pending writes and tombstones reject delayed old saves',async()=>{
  const s=server();s.seed('p','a');const gate=deferred(),original=s.api.save;s.api.save=async(...args)=>{await gate.promise;return original(...args);};const c=controller(s.api);await c.setProject('p');const save=c.save([{kind:'user',text:'A'}]);await tick();const deletion=c.remove('a');await assert.rejects(c.save([{kind:'user',text:'late'}]),/删除/);assert.ok(s.rows.has('p/a'));gate.resolve();await save;await deletion;assert.equal(s.rows.has('p/a'),false);assert.equal(c.activeId,null);
});
test('new conversation reuses an empty active conversation and changes cancel generation first',async()=>{
  const s=server();s.seed('p','a');const events=[];const c=controller(s.api,{getMessages:()=>[],onBeforeChange:()=>events.push('before'),onActivate:()=>events.push('activate')});await c.setProject('p');events.length=0;await c.newConversation();assert.equal(c.activeId,'a');assert.equal(s.rows.size,1);assert.deepEqual(events,['before','activate']);
});
test('search ignores late old queries and paginates the current literal query',async()=>{
  const s=server();const old=deferred();s.api.list=async(p,{q,offset})=>q==='old'?old.promise:{conversations:[{id:q+offset,title:q,version:1,message_count:0}],total:2,has_more:offset===0};const c=controller(s.api);await c.setProject('p');const first=c.search('old');await c.search('[new]');old.resolve({conversations:[{id:'stale',title:'old'}],total:1,has_more:false});await first;assert.equal(c.state.conversations[0].title,'[new]');await c.loadMore();assert.equal(c.state.conversations.length,2);
});
test('active pointers are isolated by account and project and contain no message bodies',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'private transcript'}]);s.seed('p','b');const store=storage();let actor='alice';const c=controller(s.api,{storage:store,getActor:()=>actor});await c.setProject('p');await c.switchTo('b');actor='bob';await c.setProject('p');assert.equal(c.activeId,'a');actor='alice';await c.setProject('p');assert.equal(c.activeId,'b');assert.ok([...store.values.entries()].every(([key,value])=>!key.includes('private')&&!value.includes('private')));assert.equal(store.values.size,2);
});
test('legacy import requires explicit confirmation and copies frozen references without deleting local data',async()=>{
  const s=server(),store=storage();const legacy=[{kind:'user',text:'old'},{kind:'assistant',text:'answer',payload:{sources:[{snapshot_id:'fixed',quote:'old text'}]}}];store.setItem('kg_qa_v1_p',JSON.stringify(legacy));const c=controller(s.api,{storage:store});await c.setProject('p');assert.equal(c.state.legacyAvailable,true);assert.equal(s.rows.size,0);await assert.rejects(c.importLegacy(false),/确认/);assert.equal(s.rows.size,0);await c.importLegacy(true);assert.deepEqual(s.rows.get('p/'+c.activeId).messages,legacy);assert.equal(store.getItem('kg_qa_v1_p'),JSON.stringify(legacy));
});
function dom(){
  class Element {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.listeners={};this.hidden=false;this.disabled=false;this.value='';this.open=false;this._text='';this.className='';}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(child=>child.textContent||'').join('');}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this._text='';this.children=children;}
    setAttribute(key,value){this.attributes[key]=String(value);}
    getAttribute(key){return this.attributes[key]||null;}
    addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
    removeEventListener(name,callback){this.listeners[name]=(this.listeners[name]||[]).filter(item=>item!==callback);}
    dispatch(name,properties={}){const event={preventDefault(){this.defaultPrevented=true;},target:this,...properties};for(const callback of [...this.listeners[name]||[]])callback(event);return event;}
    focus(){document.activeElement=this;}
    select(){}
    showModal(){this.open=true;}
    close(){this.open=false;this.dispatch('close');}
    setCustomValidity(message){this.validation=message;}
    reportValidity(){return !this.validation;}
    querySelector(selector){return selector==='form'?this.form:null;}
  }
  const ids=['qa-workspace','qa-session-list','qa-session-status','qa-session-new','qa-new-chat','qa-sessions-toggle','qa-sessions-close','qa-sessions-backdrop','qa-session-search','qa-session-more','qa-session-import','qa-active-session-title','qa-session-dialog','qa-session-dialog-title','qa-session-dialog-description','qa-session-rename-label','qa-session-name','qa-session-dialog-cancel','qa-session-dialog-confirm'];
  const elements=Object.fromEntries(ids.map(id=>[id,new Element(id==='qa-session-dialog'?'dialog':id==='qa-session-list'?'ul':'div')]));
  const document={getElementById:id=>elements[id]||null,createElement:tag=>new Element(tag),activeElement:null};elements['qa-session-dialog'].form=new Element('form');return {document,elements};
}
function mounted(s,options={}){assert.equal(typeof lib.mount,'function','browser mount API must exist');const {document,elements}=dom();const c=lib.mount({api:s.api,document,storage:storage(),getActor:()=> 'alice',getMessages:()=>[],viewportWidth:1200,...options});return {c,elements,document};}
test('mount renders untrusted titles as text, active semantics and desktop/mobile history controls',async()=>{
  const s=server();s.seed('p','a',[],'<img src=x onerror=alert(1)>');const {c,elements}=mounted(s);await c.setProject('p');const item=elements['qa-session-list'].children[0],button=item.children[0];assert.equal(item.className,'qa-session-item is-active');assert.equal(button.getAttribute('aria-current'),'true');assert.match(button.textContent,/<img src=x/);assert.equal(button.children[0].tagName,'SPAN');assert.equal(button.children[0].children.length,0);assert.equal(elements['qa-workspace'].dataset.historyOpen,'true');elements['qa-sessions-close'].dispatch('click');assert.equal(elements['qa-workspace'].dataset.historyOpen,'false');assert.equal(elements['qa-sessions-toggle'].getAttribute('aria-expanded'),'false');
  const mobile=mounted(s,{viewportWidth:600});assert.equal(mobile.elements['qa-workspace'].dataset.historyOpen,'false');mobile.elements['qa-sessions-toggle'].dispatch('click');assert.equal(mobile.elements['qa-sessions-backdrop'].hidden,false);
});
test('cancel and Escape in the native delete dialog never mutate server data and return focus',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'keep'}]);const {c,elements,document}=mounted(s);await c.setProject('p');let item=elements['qa-session-list'].children[0],actions=item.children[1],remove=actions.children[2];remove.focus();remove.dispatch('click');assert.equal(elements['qa-session-dialog'].open,true);assert.match(elements['qa-session-dialog-description'].textContent,/来源文档/);elements['qa-session-dialog-cancel'].dispatch('click');await tick();assert.equal(s.rows.has('p/a'),true);assert.equal(document.activeElement,remove);remove.dispatch('click');elements['qa-session-dialog'].dispatch('cancel');await tick();assert.equal(s.rows.has('p/a'),true);assert.equal(elements['qa-session-dialog'].open,false);
});
test('rename rejects whitespace and submits trimmed text on Enter without form navigation',async()=>{
  const s=server();s.seed('p','a');const {c,elements}=mounted(s);await c.setProject('p');const rename=elements['qa-session-list'].children[0].children[1].children[1];rename.dispatch('click');elements['qa-session-name'].value='   ';elements['qa-session-dialog-confirm'].dispatch('click');assert.equal(elements['qa-session-dialog'].open,true);assert.equal(s.rows.get('p/a').title,'a');elements['qa-session-name'].value='  safe <title>  ';const event=elements['qa-session-name'].dispatch('keydown',{key:'Enter'});assert.equal(event.defaultPrevented,true);await tick();assert.equal(s.rows.get('p/a').title,'safe <title>');assert.equal(elements['qa-session-dialog'].open,false);
});
test('clear and legacy import both require native dialog confirmation',async()=>{
  const s=server(),store=storage();s.seed('p','a',[{kind:'user',text:'keep'}]);store.setItem('kg_qa_v1_p',JSON.stringify([{kind:'user',text:'old'}]));let current=[{kind:'user',text:'keep'}];const {c,elements}=mounted(s,{storage:store,getMessages:()=>current,onActivate:messages=>{current=messages;}});await c.setProject('p');const cancel=c.requestClear();elements['qa-session-dialog-cancel'].dispatch('click');await cancel;assert.equal(s.rows.get('p/a').messages.length,1);const clear=c.requestClear();elements['qa-session-dialog-confirm'].dispatch('click');await clear;assert.deepEqual(current,[]);assert.deepEqual(s.rows.get('p/a').messages,[]);elements['qa-session-import'].dispatch('click');assert.match(elements['qa-session-dialog-description'].textContent,/当前账号/);assert.equal(s.rows.size,1);elements['qa-session-dialog-cancel'].dispatch('click');await tick();assert.equal(s.rows.size,1);elements['qa-session-import'].dispatch('click');elements['qa-session-dialog-confirm'].dispatch('click');await tick();assert.equal(s.rows.size,2);
});
test('conflict renders an explicit reload action while preserving the active local transcript',async()=>{
  const s=server();s.seed('p','a');const {c,elements}=mounted(s);await c.setProject('p');s.rows.get('p/a').version=9;await assert.rejects(c.save([{kind:'user',text:'unsaved'}]));const reload=elements['qa-session-status'].children.find(child=>child.tagName==='BUTTON');assert.ok(reload);assert.match(reload.textContent,/重新加载/);reload.dispatch('click');assert.match(elements['qa-session-dialog-description'].textContent,/未保存/);elements['qa-session-dialog-cancel'].dispatch('click');await tick();assert.equal(c.state.conflict,true);reload.dispatch('click');elements['qa-session-dialog-confirm'].dispatch('click');await tick();assert.equal(c.state.conflict,false);
});
test('mount is optional when the conversation workspace is absent',()=>{
  assert.equal(typeof lib.mount,'function','browser mount API must exist');assert.equal(lib.mount({document:{getElementById:()=>null}}),null);
});
test('delete sends the final queued version and preserves local content on another-device conflict',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'local'}]);let removedVersion;const remove=s.api.remove;s.api.remove=async(p,id,body)=>{removedVersion=body.expected_version;return remove(p,id,body);};let messages;const c=controller(s.api,{onActivate:value=>{messages=value;}});await c.setProject('p');await c.save([{kind:'user',text:'latest local'}]);s.rows.get('p/a').version=9;await assert.rejects(c.remove('a'),error=>error.status===409);assert.equal(removedVersion,2);assert.equal(s.rows.has('p/a'),true);assert.equal(c.activeId,'a');assert.equal(c.state.conflict,true);assert.equal(messages[0].text,'latest local');
});
test('network or 401 save failures retain dirty messages across selections and offer an explicit retry',async()=>{
  const s=server();s.seed('p','a');s.seed('p','b');const save=s.api.save;let unavailable=true;s.api.save=async(...args)=>{if(unavailable)throw Object.assign(new Error('登录已失效'),{status:401});return save(...args);};let restored;const c=controller(s.api,{onActivate:messages=>{restored=messages;}});await c.setProject('p');await assert.rejects(c.save([{kind:'user',text:'unsaved local',frozen:{quote:'kept'}}]),e=>e.status===401);assert.equal(c.state.dirty,true);assert.equal(c.state.canRetry,true);await c.switchTo('b');await c.switchTo('a');assert.equal(restored[0].text,'unsaved local');assert.equal(c.state.dirty,true);unavailable=false;await c.retrySave();assert.equal(s.rows.get('p/a').messages[0].text,'unsaved local');assert.equal(c.state.dirty,false);
});
test('conflicted local content survives selecting the same conversation until explicit reload',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'server'}]);let restored;const c=controller(s.api,{onActivate:messages=>{restored=messages;}});await c.setProject('p');s.rows.get('p/a').version=9;await assert.rejects(c.save([{kind:'user',text:'local dirty'}]));await c.switchTo('a');assert.equal(restored[0].text,'local dirty');assert.equal(c.state.conflict,true);await c.reloadActive();assert.equal(restored[0].text,'server');assert.equal(c.state.dirty,false);
});
test('mount exposes retry after a failed save and first ensureActive does not cancel the submitting turn',async()=>{
  const s=server();let cancellations=0;const {c,elements}=mounted(s,{onBeforeChange:()=>cancellations++});await c.setProject('p');const before=cancellations;await c.ensureActive();assert.equal(cancellations,before);const save=s.api.save;s.api.save=async()=>{throw new Error('网络断开');};await assert.rejects(c.save([{kind:'user',text:'retry me'}]));const retry=elements['qa-session-status'].children.find(child=>child.tagName==='BUTTON');assert.ok(retry);assert.match(retry.textContent,/重试/);s.api.save=save;retry.dispatch('click');await tick();assert.equal(c.state.dirty,false);
});
test('pointer identities cannot collide when account and project names contain separators',()=>{
  assert.notEqual(lib.pointerKey('a_b','c'),lib.pointerKey('a','b_c'));
});
test('dirty messages survive project round trips and failed selection loads restore the previous view',async()=>{
  const s=server();s.seed('p','a');s.seed('p','b');s.seed('q','c');const save=s.api.save;s.api.save=async()=>{throw new Error('offline');};let restored;const c=controller(s.api,{onActivate:messages=>{restored=messages;}});await c.setProject('p');await assert.rejects(c.save([{kind:'user',text:'local draft'}]));await c.setProject('q');await c.setProject('p');assert.equal(restored[0].text,'local draft');s.api.get=async()=>{throw new Error('load offline');};await assert.rejects(c.switchTo('b'),/load offline/);assert.equal(c.activeId,'a');assert.equal(restored[0].text,'local draft');s.api.save=save;await c.retrySave();assert.equal(c.state.dirty,false);
});
test('changing project cancels an open confirmation before it can mutate another scope',async()=>{
  const s=server();s.seed('p','a');s.seed('q','b');const {c,elements}=mounted(s);await c.setProject('p');elements['qa-session-list'].children[0].children[1].children[2].dispatch('click');assert.equal(elements['qa-session-dialog'].open,true);await c.setProject('q');assert.equal(elements['qa-session-dialog'].open,false);elements['qa-session-dialog-confirm'].dispatch('click');await tick();assert.equal(s.rows.has('p/a'),true);assert.equal(s.rows.has('q/b'),true);
});
test('script exposes the same browser-global entry point without requiring Node globals',()=>{
  const vm=require('node:vm'),context={window:{}};vm.runInNewContext(fs.readFileSync(file,'utf8'),context);assert.equal(typeof context.window.QaConversations.mount,'function');assert.equal(typeof context.window.QaConversations.createController,'function');
});
test('background saves do not erase a focused search draft before the debounce runs',async()=>{
  const s=server();s.seed('p','a');const {c,elements}=mounted(s);await c.setProject('p');const input=elements['qa-session-search'];input.focus();input.value='still typing';await c.save([{kind:'user',text:'background save'}]);assert.equal(input.value,'still typing');await c.setProject('q');assert.equal(input.value,'');
});
test('an actor change immediately hides the prior account state and refuses active conversation reuse',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'alice private'}]);let actor='alice';const c=controller(s.api,{getActor:()=>actor});await c.setProject('p');actor='bob';assert.equal(c.state.conversation,null);assert.deepEqual(c.state.conversations,[]);assert.equal(c.activeId,null);await assert.rejects(c.ensureActive(),/账号/);await assert.rejects(c.newConversation(),/账号/);
});
test('crossing the desktop breakpoint updates drawer defaults but keyboard height changes keep it open',()=>{
  const s=server(),view=new EventTarget();view.innerWidth=1200;view.innerHeight=900;const {elements}=mounted(s,{window:view,viewportWidth:undefined});const workspace=elements['qa-workspace'],toggle=elements['qa-sessions-toggle'],backdrop=elements['qa-sessions-backdrop'];assert.equal(workspace.dataset.historyOpen,'true');view.innerWidth=700;view.dispatchEvent(new Event('resize'));assert.equal(workspace.dataset.historyOpen,'false');assert.equal(toggle.getAttribute('aria-expanded'),'false');assert.equal(backdrop.hidden,true);toggle.dispatch('click');assert.equal(workspace.dataset.historyOpen,'true');assert.equal(backdrop.hidden,false);view.innerHeight=400;view.dispatchEvent(new Event('resize'));assert.equal(workspace.dataset.historyOpen,'true');view.innerWidth=1400;view.dispatchEvent(new Event('resize'));assert.equal(workspace.dataset.historyOpen,'true');assert.equal(toggle.getAttribute('aria-expanded'),'true');assert.equal(backdrop.hidden,true);
});

test('failed clear retries synchronize the displayed history before the next question can restore old messages',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'old question'},{kind:'assistant',text:'old answer'}]);
  let history=[],displayed=[],offline=true;
  const original=s.api.save;s.api.save=async(...args)=>{if(offline)throw new Error('offline');return original(...args);};
  const c=controller(s.api,{getMessages:()=>history,onBeforeChange:()=>{displayed=[];},onActivate:messages=>{history=messages;displayed=clone(messages);}});
  await c.setProject('p');await assert.rejects(c.clearActive(),/offline/);
  assert.deepEqual(displayed,[]);assert.equal(c.state.dirty,true);
  offline=false;await c.retrySave();
  assert.deepEqual(history,[],'retry success must commit the clear to the root history');
  history.push({kind:'user',text:'next question'});await c.save(history);
  assert.deepEqual(s.rows.get('p/a').messages,[{kind:'user',text:'next question'}]);
});

test('a late clear retry never replaces a newly selected conversation',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'A'}]);s.seed('p','b',[{kind:'user',text:'B'}]);
  let history=[],offline=true;const gate=deferred(),original=s.api.save;
  s.api.save=async(...args)=>{if(offline)throw new Error('offline');await gate.promise;return original(...args);};
  const c=controller(s.api,{onActivate:messages=>{history=messages;}});await c.setProject('p');await assert.rejects(c.clearActive());offline=false;
  const retry=c.retrySave();await c.switchTo('b');gate.resolve();await retry;
  assert.equal(c.activeId,'b');assert.deepEqual(history,[{kind:'user',text:'B'}]);
});

async function authenticatedConversation(){
  const vm=require('node:vm'),s=server();s.seed('p','a');const tokens=storage();tokens.setItem('kg_token','old-session');
  const redirects=[],requests=[],listeners=new Map(),classList={add(){},remove(){},toggle(){}};
  const user={id:'alice',username:'alice',role:'viewer'};let expired=false,authenticatedUser=user,unauthorizedGate=null,conversationGate=null,identityGate=null,bodyGate=null;
  const document={readyState:'loading',documentElement:{classList},body:{classList},getElementById:()=>null,querySelectorAll:()=>[],addEventListener:(event,fn)=>listeners.set(event,fn)};
  const window={location:{pathname:'/',search:'',href:'http://qa.test/',origin:'http://qa.test',replace:url=>redirects.push(url)},console,
    async fetch(url,init={}){
      requests.push({url,init});
      const json=(value,status=200)=>{const response=new Response(JSON.stringify(value),{status});if(bodyGate&&url.includes('/qa/conversations')){const read=response.json.bind(response);response.json=async()=>{await bodyGate;return read();};}return response;};
      if(url==='/api/auth/policy')return json({auth_enabled:true});
      if(url==='/api/auth/me'){if(identityGate)await identityGate;return expired?json({detail:'登录失效'},401):json({user:authenticatedUser});}
      if(expired){if(unauthorizedGate)await unauthorizedGate;return json({detail:'登录失效'},401);}
      if(url.startsWith('/api/projects/p/qa/conversations')){
        if(conversationGate)await conversationGate;
        if(init.method==='POST')return json(await s.api.create('p'));
        if(init.method==='PUT')return json(await s.api.save('p','a',JSON.parse(init.body)));
        if(url.includes('/conversations/a'))return json(await s.api.get('p','a'));
        return json(await s.api.list('p'));
      }
      return json({detail:'登录失效'},401);
    }};
  window.RequestDeadline=require('../../knowledge_service/web/request-deadline.js');
  const context={window,document,localStorage:tokens,Headers,URL,Response,console};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/auth.js'),'utf8'),context);
  listeners.get('DOMContentLoaded')();await window.__authReady;
  vm.runInNewContext(fs.readFileSync(file,'utf8'),context);
  const c=window.QaConversations.createController({storage:tokens});await c.setProject('p');
  return {c,s,window,tokens,redirects,requests,setExpired:value=>{expired=value;},setUser:value=>{authenticatedUser=value;},setUnauthorizedGate:value=>{unauthorizedGate=value;},setConversationGate:value=>{conversationGate=value;},setIdentityGate:value=>{identityGate=value;},setBodyGate:value=>{bodyGate=value;}};
}

test('real auth wrapper keeps401 conversation drafts in memory and same-account login can retry',async()=>{
  const f=await authenticatedConversation();f.setExpired(true);
  await assert.rejects(f.c.save([{kind:'user',text:'unsaved answer'}]),error=>error.status===401);
  assert.deepEqual(f.redirects,[],'the real auth wrapper must not navigate away from an unsaved conversation');
  assert.equal(f.c.state.dirty,true);assert.equal(f.c.state.loginRequired,true);assert.equal(f.tokens.getItem('kg_token'),null);
  f.setExpired(false);f.tokens.setItem('kg_token','new-session');await f.c.retrySave();
  assert.equal(f.c.state.dirty,false);assert.equal(f.s.rows.get('p/a').messages[0].text,'unsaved answer');
  const calls=f.requests.filter(row=>row.url==='/api/auth/me');assert.equal(calls.length,2,'retry must verify the newly logged-in account');
  assert.equal(f.requests.at(-1).init.headers.get('Authorization'),'Bearer new-session');
  assert.ok([...f.tokens.values.values()].every(value=>!value.includes('unsaved answer')));
});

test('login as a different account cannot save the previous account draft',async()=>{
  const f=await authenticatedConversation();f.setExpired(true);await assert.rejects(f.c.save([{kind:'user',text:'alice private'}]));
  f.setExpired(false);f.setUser({id:'bob',username:'bob',role:'viewer'});f.tokens.setItem('kg_token','bob-session');
  await assert.rejects(f.c.retrySave(),error=>error.status===401&&/原账号/.test(error.message));
  assert.equal(f.c.state.dirty,true);assert.equal(f.s.rows.get('p/a').messages.length,0);assert.deepEqual(f.redirects,[]);
});

test('ordinary API401 still follows the normal login redirect',async()=>{
  const f=await authenticatedConversation();await f.window.fetch('/api/unrelated');
  assert.equal(f.redirects.length,1);assert.equal(f.tokens.getItem('kg_token'),null);
});

test('manual-login fetch only accepts exact same-origin personal conversation endpoints',async()=>{
  const f=await authenticatedConversation();
  for(const url of ['https://other.test/api/projects/p/qa/conversations','/api/projects/p/qa/stream','/api/projects/p/qa/conversations/a/extra']){
    await assert.rejects(f.window.Auth.fetchConversation(url),/仅用于个人/);
  }
  assert.equal(f.requests.some(row=>String(row.url).includes('other.test')),false);
});

test('expiry renders login in a new tab and retains retry even when initial history loading fails',async()=>{
  const s=server();s.api.list=async()=>{throw Object.assign(new Error('登录失效'),{status:401});};
  const {c,elements}=mounted(s);await assert.rejects(c.setProject('p'));
  assert.equal(c.state.loginRequired,true);const login=elements['qa-session-status'].children.find(child=>child.tagName==='A');
  assert.ok(login);assert.equal(login.href,'/login');assert.equal(login.target,'_blank');assert.match(login.rel,/noopener/);
  const retry=elements['qa-session-status'].children.find(child=>child.tagName==='BUTTON');assert.equal(retry.textContent,'重新加载会话');
  s.api.list=async()=>({conversations:[],total:0,has_more:false});retry.dispatch('click');await tick();assert.equal(c.state.loginRequired,false);
});

test('a failed clear keeps the local empty draft even before saving is retried',async()=>{
  const s=server();s.seed('p','a',[{kind:'user',text:'old question'}]);let history=[];
  const original=s.api.save;s.api.save=async()=>{throw new Error('offline');};
  const c=controller(s.api,{onActivate:messages=>{history=messages;}});await c.setProject('p');await assert.rejects(c.clearActive());
  assert.deepEqual(history,[]);assert.deepEqual(c.state.conversation.messages,[]);s.api.save=original;
  history.push({kind:'user',text:'new question'});await c.save(history);
  assert.deepEqual(s.rows.get('p/a').messages,[{kind:'user',text:'new question'}]);
});

test('late expiry does not remove a newer login token from another tab',async()=>{
  const f=await authenticatedConversation(),gate=deferred();f.setExpired(true);f.setUnauthorizedGate(gate.promise);
  const saving=f.c.save([{kind:'user',text:'pending answer'}]);await tick();
  f.tokens.setItem('kg_token','newer-session');gate.resolve();await assert.rejects(saving,error=>error.status===401);
  assert.equal(f.tokens.getItem('kg_token'),'newer-session');assert.deepEqual(f.redirects,[]);
  f.setExpired(false);await f.c.retrySave();assert.equal(f.s.rows.get('p/a').messages[0].text,'pending answer');
});

test('changing account token without401 cannot create a conversation for the previous account',async()=>{
  const f=await authenticatedConversation();await f.c.save([{kind:'user',text:'Alice existing conversation'}]);
  f.setUser({id:'bob',username:'bob',role:'viewer'});f.tokens.setItem('kg_token','bob-session');
  await assert.rejects(f.c.newConversation(),error=>error.status===401&&/原账号/.test(error.message));
  assert.equal(f.requests.filter(row=>row.init.method==='POST').length,0);
  assert.equal(f.s.rows.size,1);assert.deepEqual(f.redirects,[]);
});

test('a changed same-account token is verified once and cached for further conversation calls',async()=>{
  const f=await authenticatedConversation();f.tokens.setItem('kg_token','alice-session-2');
  await f.c.save([{kind:'user',text:'same account'}]);await f.c.save([{kind:'user',text:'next question'}]);
  assert.equal(f.requests.filter(row=>row.url==='/api/auth/me').length,2);
  assert.equal(f.requests.at(-1).init.headers.get('Authorization'),'Bearer alice-session-2');
});

test('token changes during a conversation response reject the stale result and preserve its draft',async()=>{
  const f=await authenticatedConversation(),gate=deferred();f.setConversationGate(gate.promise);
  const saving=f.c.save([{kind:'user',text:'alice local'}]);await tick();
  f.tokens.setItem('kg_token','bob-session');f.setUser({id:'bob',username:'bob',role:'viewer'});gate.resolve();
  await assert.rejects(saving,error=>error.status===401&&/登录状态已变化/.test(error.message));
  assert.equal(f.c.state.dirty,true);assert.equal(f.c.state.conversation.messages[0].text,'alice local');
  assert.equal(f.requests.findLast(row=>row.init.method==='PUT').init.headers.get('Authorization'),'Bearer old-session');
  await assert.rejects(f.c.retrySave(),error=>error.status===401&&/原账号/.test(error.message));
  assert.equal(f.requests.filter(row=>row.init.method==='PUT').length,1);
});

test('token changes while identity is being checked cannot dispatch a conversation mutation',async()=>{
  const f=await authenticatedConversation(),gate=deferred();f.tokens.setItem('kg_token','alice-session-2');f.setIdentityGate(gate.promise);
  const saving=f.c.save([{kind:'user',text:'alice private'}]);await tick();
  f.tokens.setItem('kg_token','bob-session');gate.resolve();
  await assert.rejects(saving,error=>error.status===401&&/登录状态已变化/.test(error.message));
  assert.equal(f.requests.filter(row=>row.init.method==='PUT').length,0);assert.equal(f.c.state.dirty,true);
});

test('token changes while a conversation JSON body loads cannot commit the old response',async()=>{
  const f=await authenticatedConversation(),gate=deferred();f.setBodyGate(gate.promise);
  const saving=f.c.save([{kind:'user',text:'body still loading'}]);await tick();
  f.tokens.setItem('kg_token','bob-session');gate.resolve();
  await assert.rejects(saving,error=>error.status===401&&/登录状态已变化/.test(error.message));
  assert.equal(f.c.state.dirty,true);
});
