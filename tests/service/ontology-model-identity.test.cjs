const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const target=path.resolve(__dirname,'../../knowledge_service/web/ontology-model-identity.js');
const identity=fs.existsSync(target)?require(target):{};
const NS='https://knowledge.local/schema/';
const cls={id:'http://ex/A',identity_policy:{mode:'scoped',declared:true,scope:'old',keys:['code']},identity_annotations:[
  {predicate:NS+'identityPolicy',value:'scoped',datatype:'http://www.w3.org/2001/XMLSchema#string'},
  {predicate:NS+'identityScope',value:'old',language:'zh'},
  {predicate:NS+'identityKey',value:'code'},
]};

test('identity settings remove exact reserved annotations before adding the final state',()=>{
  assert.equal(typeof identity.policyCommands,'function');
  const before=JSON.stringify(cls),commands=identity.policyCommands(cls,{mode:'reference',scope:' new ',keys:'code\nregistry'});
  assert.equal(commands.length,7);
  assert.deepEqual(commands.slice(0,3),cls.identity_annotations.map(spec=>({action:'remove_annotation',target_iri:cls.id,...spec})));
  assert.deepEqual(commands.slice(3).map(c=>c.value),['reference','new','code','registry']);
  assert.equal(commands.at(-1).value,'registry');
  assert.equal(JSON.stringify(cls),before);
});

test('unchanged settings preserve raw qualifiers and unrelated annotations without commands',()=>{
  assert.equal(typeof identity.policyCommands,'function');
  assert.deepEqual(identity.policyCommands(cls,{mode:'scoped',scope:'old',keys:'code'}),[]);
  const term={...cls,identity_annotations:[...cls.identity_annotations,{predicate:'http://ex/note',value:'保留'}]};
  assert.ok(identity.policyCommands(term,{mode:'reference',scope:'',keys:''}).every(c=>c.predicate.startsWith(NS)));
});

test('IRI identity annotation removals use the draft command value_type wire field',()=>{
  const iriKey={predicate:NS+'identityKey',value:'http://ex/code',type:'iri'};
  const term={id:cls.id,identity_policy:{mode:'scoped',declared:true,scope:'',keys:[iriKey.value]},
    identity_annotations:[{predicate:NS+'identityPolicy',value:'scoped'},iriKey]};
  const commands=identity.policyCommands(term,{mode:'reference',scope:'',keys:''});
  const removal=commands.find(command=>command.predicate===iriKey.predicate);
  assert.deepEqual(removal,{action:'remove_annotation',target_iri:cls.id,
    predicate:iriKey.predicate,value:iriKey.value,value_type:'iri'});
  assert.equal(Object.hasOwn(removal,'type'),false);
});

test('missing annotation projection is read-only, invalid mode fails, and legacy stays conservative',()=>{
  assert.equal(typeof identity.policyCommands,'function');
  assert.throws(()=>identity.policyCommands({id:cls.id},{mode:'reference'}),/完整读取/);
  assert.throws(()=>identity.policyCommands(cls,{mode:'auto'}),/身份策略/);
  assert.deepEqual(identity.policyState({}),{mode:'',scope:'',keys:[]});
  assert.deepEqual(identity.policyCommands({id:cls.id,identity_annotations:[]},{mode:'',scope:'',keys:''}),[]);
});

test('identity semantics are detected on either side and excluded from label-only publication',()=>{
  assert.equal(typeof identity.isIdentityOperation,'function');
  for(const name of ['identityPolicy','identityScope','identityKey'])for(const side of ['before','after']){
    assert.equal(identity.isIdentityOperation({action:'remove_annotation',[side]:{predicate:NS+name}}),true);
  }
  assert.equal(identity.isIdentityOperation({action:'add_annotation',after:{predicate:'http://ex/note'}}),false);
  const panel=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8');
  assert.match(panel,/OntologyIdentity\??\.classSection/);
  assert.match(panel,/!window\.OntologyIdentity\??\.isIdentityOperation\(o\)/);
  const html=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/index.html'),'utf8');
  assert.match(html,/ontology-model-identity\.js\?v=identity-policy-20261113-3/);
});

function fixture(overrides={}){
  const nodes=[];
  const projectControl={listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
  const document={body:{classList:{contains:()=>true}},getElementById:id=>id==='project'?projectControl:null,createElement(tag){
    const node={tag,children:[],listeners:{},isConnected:true,value:'',disabled:false,
      append(...items){this.children.push(...items);},setAttribute(name,value){this[name]=value;},
      addEventListener(name,fn){this.listeners[name]=fn;}};nodes.push(node);return node;}};
  let project='p';const calls=[],model={S:{ontologyId:'v1',showingDraft:true,draft:{id:'d',revision:1,status:'pending'}},
    async sendCommand(command){calls.push(command);},status(){}};
  Object.assign(model.S,overrides);
  const window={Auth:{isAdmin:()=>!overrides.viewer}};
  const vm=require('node:vm');vm.runInNewContext(fs.readFileSync(target,'utf8'),{window,document});
  const options={model,project:()=>project,title:text=>({textContent:text})};
  const mount=(term=cls)=>window.OntologyIdentity.classSection(term,options);
  return {nodes,calls,model,mount,project(value){project=value;projectControl.listeners.change?.();}};
}

test('history and viewers see policy but no editing controls',()=>{
  assert.equal(typeof identity.classSection,'function');
  for(const setting of [{preview:{id:'history'}},{viewer:true}]){
    const f=fixture(setting);f.mount();assert.equal(f.nodes.some(n=>n.tag==='select'),false);
  }
});

test('save is explicit, retries retain inputs, and stale revision is guarded',async()=>{
  assert.equal(typeof identity.classSection,'function');
  const f=fixture();f.mount();
  const mode=f.nodes.find(n=>n.tag==='select'),save=f.nodes.find(n=>n.textContent==='保存身份策略到草案');
  mode.value='reference';mode.listeners.change();assert.equal(f.calls.length,0);
  f.model.sendCommand=async command=>{f.calls.push(command);throw Error('temporary');};
  await save.listeners.click();assert.equal(f.calls.length,1);assert.equal(mode.value,'reference');assert.equal(save.disabled,false);
  f.mount();assert.equal(f.nodes.filter(n=>n.tag==='select').at(-1).value,'reference');
  f.model.S.draft.revision++;
  await save.listeners.click();assert.equal(f.calls.length,1);
});

test('pending writes prevent duplicate submissions and keep controls disabled across rerenders',async()=>{
  const f=fixture();f.mount();let complete;
  const mode=f.nodes.find(n=>n.tag==='select'),save=f.nodes.find(n=>n.textContent==='保存身份策略到草案');
  mode.value='reference';mode.listeners.change();
  f.model.sendCommand=async command=>{f.calls.push(command);await new Promise(resolve=>complete=resolve);};
  const pending=save.listeners.click();await save.listeners.click();
  assert.equal(f.calls.length,1);assert.equal(mode.disabled,true);
  f.mount();assert.equal(f.nodes.filter(n=>n.tag==='select').at(-1).disabled,true);
  complete();await pending;
  assert.equal(f.nodes.filter(n=>n.textContent==='保存身份策略到草案').at(-1).disabled,true);
});

test('stale ontology, draft, role, history, and project A B A callbacks cannot write',async()=>{
  for(const mutate of [f=>f.model.S.ontologyId='v2',f=>f.model.S.draft.id='other',
    f=>f.model.S.preview={id:'old'},f=>f.model.S.draft.status='accepted',
    f=>{f.project('b');f.project('p');}]){
    const f=fixture();f.mount();const mode=f.nodes.find(n=>n.tag==='select');
    mode.value='reference';mode.listeners.change();mutate(f);
    await f.nodes.find(n=>n.textContent==='保存身份策略到草案').listeners.click();assert.equal(f.calls.length,0);
  }
});

test('legacy conservative policy and missing raw metadata have explicit read states',()=>{
  const f=fixture();f.mount({id:'http://ex/A'});
  assert.equal(f.nodes.some(n=>n.textContent?.includes('未配置（保持现有规则）')),true);
  assert.equal(f.nodes.some(n=>n.tag==='select'),false);
  assert.equal(f.nodes.some(n=>n.textContent==='重新读取身份设置'),true);
  const missing=fixture();missing.mount({id:'http://ex/A',identity_annotations:[]});
  assert.equal(missing.nodes.find(n=>n.tag==='select').value,'');
});

test('identity choices explain business behavior in plain labels',()=>{
  const f=fixture();f.mount();
  assert.deepEqual(f.nodes.filter(node=>node.tag==='option').map(node=>node.textContent),[
    '未配置（保持现有规则）','按业务区间区分（事件等）','跨文档统一身份（长期对象）']);
});

function actualModelFixture({draftBase='published',draftClass=cls}={}){
  const vm=require('node:vm'),web=path.resolve(__dirname,'../../knowledge_service/web');
  const disconnect=node=>{node.isConnected=false;for(const child of node.children||[])disconnect(child);};
  const element=tag=>({tag,children:[],listeners:{},isConnected:true,value:'',
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children.forEach(disconnect);this.children=nodes;},
    setAttribute(name,value){this[name]=value;},addEventListener(name,fn){this.listeners[name]=fn;}});
  const host=element('host'),projectControl=element('select'),notices=[],requests=[];
  const summary={classes:[cls],relations:[],attributes:[]};
  let draft=null,failCommands=true;
  const context={window:{Auth:{isAdmin:()=>true}},current:'p',console,
    document:{createElement:element,getElementById:id=>id==='project'?projectControl:null,addEventListener(){}},
    endpoint:suffix=>'/p'+suffix,status:(message,error)=>notices.push({message,error}),
    api:async(url,body,method='POST')=>{
      requests.push({url,body,method});const route=url.split('?')[0];
      if(route==='/p/ontology')return {id:'published',summary};
      if(route==='/p/ontology-drafts'&&method==='GET')return {items:draft?[draft]:[]};
      if(route==='/p/ontology-drafts'&&method==='POST')return draft={id:'prepared',revision:1,status:'pending',
        base_ontology_id:draftBase,ontology:{...summary,classes:[draftClass]}};
      if(route==='/p/ontology-drafts/prepared/commands'){
        if(failCommands)throw Error('commands failed after draft preparation');
        return draft={...draft,revision:2};
      }
      if(route==='/p/ontology-drafts/prepared')return draft;
      if(route.endsWith('/stage-availability'))return {stages:{review:{allowed:true}}};
      if(route.endsWith('/review-plan'))return {revision:draft?.revision,items:[]};
      if(route.endsWith('/publish-readiness'))return {revision:draft?.revision,ready:false,items:[]};
      return {};
    }};
  for(const file of ['ontology-model.js','ontology-model-identity.js'])vm.runInNewContext(fs.readFileSync(path.join(web,file),'utf8'),context);
  const model=context.window.OntologyModel;
  Object.assign(model.S,{summary,ontologyId:'published',selected:cls.id});
  const options={model,project:()=>context.current,title:text=>({textContent:text})};
  const repaint=()=>{const term=(model.S.summary?.classes||[]).find(c=>c.id===cls.id);if(term)host.replaceChildren(context.window.OntologyIdentity.classSection(term,options));};
  for(const event of ['draft:changed','graph:changed','stage:changed','select'])model.on(event,repaint);
  const all=node=>[node,...(node.children||[]).flatMap(all)],find=text=>all(host).find(node=>node.textContent===text);
  repaint();return {model,notices,requests,host,find,all:()=>all(host),repaint,
    allowCommands(){failCommands=false;}};
}

test('actual draft preparation failure preserves the published edit and offers visible draft recovery',async()=>{
  const f=actualModelFixture(),mode=f.all().find(node=>node.tag==='select');
  mode.value='reference';mode.listeners.change();
  await f.find('保存身份策略到草案').listeners.click();
  assert.equal(f.model.S.draft.id,'prepared');assert.equal(f.model.S.showingDraft,false);
  assert.ok(f.notices.some(item=>item.error&&item.message.includes('commands failed')),'detached form failure must be visible globally');
  assert.ok(f.all().some(node=>node.textContent?.includes('commands failed')),'current inspector must show the failure');
  const recovery=f.find('打开草案继续编辑');assert.ok(recovery,'a prepared draft must be explicitly reopenable');
  await recovery.listeners.click();for(let count=0;count<20;count++)await Promise.resolve();
  assert.equal(f.model.S.showingDraft,true);assert.equal(f.all().find(node=>node.tag==='select').value,'reference');
  f.allowCommands();await f.find('保存身份策略到草案').listeners.click();
  const writes=f.requests.filter(item=>item.url.includes('/commands'));
  assert.equal(writes.length,2);assert.equal(writes[1].body.command.commands.find(command=>
    command.action==='add_annotation'&&command.predicate===NS+'identityPolicy').value,'reference');
});

test('draft preparation never transfers dirty policy edits across a different base or policy baseline',async()=>{
  for(const settings of [{draftBase:'foreign-base'},{draftClass:{...cls,identity_policy:{...cls.identity_policy,scope:'changed'}}},
    {draftClass:{...cls,identity_annotations:[...cls.identity_annotations,{predicate:NS+'identityKey',value:'foreign'}]}}]){
    const f=actualModelFixture(settings),mode=f.all().find(node=>node.tag==='select');
    mode.value='reference';mode.listeners.change();await f.find('保存身份策略到草案').listeners.click();
    assert.equal(f.requests.filter(item=>item.url.includes('/commands')).length,0);
    assert.equal(f.find('打开草案继续编辑'),undefined);
  }
});

test('prepared-draft recovery remains read-only in history and stale recovery callbacks cannot write',async()=>{
  const f=actualModelFixture(),mode=f.all().find(node=>node.tag==='select');
  mode.value='reference';mode.listeners.change();await f.find('保存身份策略到草案').listeners.click();
  const recovery=f.find('打开草案继续编辑');assert.ok(recovery);
  f.model.S.preview={id:'history'};f.repaint();
  assert.equal(f.all().some(node=>node.tag==='select'||node.textContent==='打开草案继续编辑'),false);
  const before=f.requests.length;await recovery.listeners.click();assert.equal(f.requests.length,before);
});
