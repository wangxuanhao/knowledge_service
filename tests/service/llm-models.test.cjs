const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.value='';this.hidden=false;this.disabled=false;this.text='';this.listeners={};this.className='';this.dataset={};}
 set textContent(value){this.text=String(value);this.children=[];} get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
 replaceChildren(...children){this.text='';this.children=[];this.append(...children);}
 setAttribute(name,value){this.attributes[name]=String(value);}
 addEventListener(name,fn){this.listeners[name]=fn;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 focus(){this.focused=true;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.close?.();}
 querySelectorAll(selector){return this.all().filter(x=>selector==='button'?x.tagName==='button':false);}
 all(){return this.children.flatMap(x=>[x,...x.all()]);}
}
function setup(role='superadmin',handler=()=>({items:[],active:null,environment_available:false})){
 const body=new Element('body'),host=new Element('section'),nav=new Element('button');host.id='tab-llm-models';nav.dataset={tab:'llm-models'};body.append(host,nav);
 const calls=[],opened=[];
 const document={body,activeElement:nav,createElement:tag=>new Element(tag),getElementById:id=>body.all().find(x=>x.id===id),querySelector:()=>nav,addEventListener(){}};
 const window={Auth:{isSuperAdmin:()=>role==='superadmin',isAdmin:()=>['superadmin','admin','disabled'].includes(role),user:()=>role==='disabled'?null:{role}},__authReady:Promise.resolve(),KnowledgeTabs:{open:(...args)=>opened.push(args)},WorkspaceContext:{enter(){}},fetch:async(url,options={})=>{calls.push({url,...options});const value=await handler(url,options);return {ok:value.status?value.status<400:true,status:value.status||200,json:async()=>value.body||value};}};
 const context={window,document,URL,console};vm.createContext(context);
 const file=path.resolve(__dirname,'../../knowledge_service/web/llm-models.js');
 if(fs.existsSync(file))vm.runInContext(fs.readFileSync(file,'utf8'),context);
 return {window,host,body,calls,opened,button:text=>body.all().find(x=>x.tagName==='button'&&x.textContent===text),field:name=>body.all().find(x=>x.name===name),dialog:()=>body.all().find(x=>x.tagName==='dialog')};
}
const profile={id:'one',name:'生产模型',base_url:'https://model.example/v1',model:'chat-large',has_api_key:true,is_active:false};

test('only superadmin and confirmed auth-disabled sessions can open model management',async()=>{
 for(const role of ['admin','viewer']){const env=setup(role);assert.ok(env.window.LLMModels,'model management module exists');await env.window.LLMModels.open();assert.equal(env.calls.length,0);assert.equal(env.opened.length,0);}
 for(const role of ['superadmin','disabled']){const env=setup(role);await env.window.LLMModels.open();assert.equal(env.calls[0].url,'/api/llm-models');assert.equal(env.opened[0][0],'llm-models');}
});
test('shows active environment fallback, saved profiles and connection result',async()=>{
 const env=setup('superadmin',(url)=>url.endsWith('/test')?{ok:false,detail:'连接失败，请检查地址'}:{items:[profile],active:{id:'environment',model:'env-model'},environment_available:true});
 await env.window.LLMModels.open();assert.match(env.host.textContent,/环境配置/);assert.match(env.host.textContent,/生产模型/);assert.match(env.host.textContent,/已设置/);
 await env.button('测试连接').onclick();assert.match(env.host.textContent,/连接失败，请检查地址/);assert.equal(env.calls[1].url,'/api/llm-models/one/test');
});
test('create validates fields, posts a masked key once and clears it on close',async()=>{
 const env=setup();await env.window.LLMModels.open();env.button('新增模型').onclick();const dialog=env.dialog(),form=dialog.all().find(x=>x.tagName==='form');
 assert.equal(env.field('api_key').type,'password');await form.onsubmit({preventDefault(){}});assert.equal(env.calls.length,1);
 for(const [name,value] of Object.entries({name:'备用',base_url:'https://other.example/v1',model:'model-b',api_key:'private-key-never-render'}))env.field(name).value=value;
 const key=env.field('api_key');await form.onsubmit({preventDefault(){}});
 assert.equal(env.calls[1].method,'POST');assert.equal(JSON.parse(env.calls[1].body).api_key,'private-key-never-render');assert.equal(key.value,'');assert.equal(env.dialog(),undefined);assert.doesNotMatch(env.body.textContent,/private-key-never-render/);
});
test('edit keeps blank key and activation reloads authoritative default',async()=>{
 let active=null;const env=setup('superadmin',(url)=>{if(url.endsWith('/activate'))active=profile;return {items:[{...profile,is_active:!!active}],active,environment_available:false};});
 await env.window.LLMModels.open();env.button('编辑').onclick();assert.equal(env.field('api_key').value,'');assert.match(env.dialog().textContent,/留空保留/);
 const form=env.dialog().all().find(x=>x.tagName==='form');await form.onsubmit({preventDefault(){}});assert.equal(env.calls[1].method,'PATCH');assert.equal(JSON.parse(env.calls[1].body).api_key,'');
 await env.button('设为默认').onclick();assert.ok(env.calls.some(x=>x.url==='/api/llm-models/one/activate'));assert.match(env.host.textContent,/当前默认/);assert.ok(env.button('当前默认').disabled);
});
test('failed saves show server detail and clear the password; cancel clears it too',async()=>{
 const env=setup('superadmin',(_url,options)=>options.method==='POST'?{status:400,body:{detail:'模型地址不可用'}}:{items:[],active:null,environment_available:false});
 await env.window.LLMModels.open();env.button('新增模型').onclick();for(const [name,value]of Object.entries({name:'新模型',base_url:'https://bad.example/v1',model:'m',api_key:'secret'}))env.field(name).value=value;
 await env.dialog().all().find(x=>x.tagName==='form').onsubmit({preventDefault(){}});assert.match(env.dialog().textContent,/模型地址不可用/);assert.equal(env.field('api_key').value,'');
 const key=env.field('api_key');key.value='another-secret';env.button('取消').onclick();assert.equal(key.value,'');assert.equal(env.dialog(),undefined);
});
test('model page is wired into versioned management navigation',()=>{
 const web=path.resolve(__dirname,'../../knowledge_service/web');const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
 assert.match(html,/<button data-tab="llm-models"[^>]*hidden[^>]*>模型管理<\/button>/);
 assert.match(html,/id="tab-llm-models"/);assert.match(html,/llm-models\.js\?v=platform-models-20261111-1/);assert.match(html,/llm-models\.css\?v=platform-models-20261111-1/);
 assert.match(html,/app\.js\?v=platform-models-20261111-1/);assert.match(html,/menu-hierarchy\.js\?v=platform-models-20261111-1/);
 assert.match(fs.readFileSync(path.join(web,'menu-hierarchy.js'),'utf8'),/\['projects','useradmin','llm-models'\]/);
});
