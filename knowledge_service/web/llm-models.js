/* Platform-wide OpenAI-compatible profiles. Credentials only live in the password field until submission. */
(function(){
'use strict';
const destination='llm-models',endpoint='/api/llm-models';
let ready=false,host=null,list=null,summary=null,message=null,count=null,busy=false,serial=0,dismissDialog=null;
const gate=Promise.resolve(window.__authReady).then(()=>{ready=true;syncNavigation();});
function canAccess(){return ready&&!!window.Auth&&(window.Auth.isSuperAdmin()||(!window.Auth.user()&&window.Auth.isAdmin()));}
function syncNavigation(){const button=document.querySelector('[data-tab="llm-models"]');if(button)button.hidden=!canAccess();}
function node(tag,className='',text){const element=document.createElement(tag);element.className=className;if(text!==undefined)element.textContent=text;return element;}
function button(text,action,secondary=true){const element=node('button',secondary?'secondary':'',text);element.type='button';element.onclick=action;return element;}
function flash(text,tone=''){if(!message)return;message.textContent=text;message.className='llm-flash'+(tone?' is-'+tone:'');message.setAttribute('role',tone==='error'?'alert':'status');}
function setBusy(value){busy=value;host?.setAttribute('aria-busy',String(value));host?.querySelectorAll('button').forEach(item=>{item.disabled=value||item.dataset?.active==='true';});}
async function request(path,method='GET',payload){
 if(!canAccess())throw Error('仅超级管理员可管理模型');
 let response;try{response=await window.fetch(path,{method,...(payload?{headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});}catch{throw Error('无法连接知识服务，请检查网络后重试。');}
 let data;try{data=await response.json();}catch{throw Error('服务返回了无法解析的数据，请重试。');}
 if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'模型操作失败（HTTP '+response.status+'）');
 return data;
}
async function open(){
 await gate;if(!canAccess())return;
 dismissDialog?.();window.KnowledgeTabs?.open(destination,{trigger:false});window.WorkspaceContext?.enter(destination);
 host=document.getElementById('tab-'+destination);if(!host)return;
 const page=node('section','llm-page'),head=node('header','llm-head'),intro=node('div');intro.append(node('h2','','模型管理'),node('p','llm-sub','保存 OpenAI 兼容模型配置，选择平台统一使用的默认模型。'));
 head.append(intro,button('新增模型',()=>edit(),false));summary=node('section','llm-active');summary.setAttribute('aria-label','当前默认模型');
 message=node('p','llm-flash');message.setAttribute('aria-live','polite');
 const listHead=node('div','llm-list-head');count=node('span','llm-sub');listHead.append(node('h3','','已保存模型'),count,button('刷新',()=>load()));
 list=node('div','llm-list');page.append(head,summary,message,listHead,list,node('p','llm-note','默认模型适用于平台的抽取、问答与本体建议。切换后，新操作使用新配置，进行中的任务继续使用启动时的模型。'));host.replaceChildren(page);
 await load();
}
async function load(){
 const ticket=++serial;setBusy(true);count.textContent='正在读取…';list.replaceChildren(node('p','llm-empty','正在读取模型配置…'));
 try{const data=await request(endpoint);if(ticket!==serial||!canAccess())return;render(data);}
 catch(error){if(ticket===serial){summary.replaceChildren(node('p','','无法确认当前默认模型'));count.textContent='读取失败';list.replaceChildren(node('p','llm-empty','暂时无法读取模型清单，请点击刷新重试。'));flash(error.message,'error');}}
 finally{if(ticket===serial)setBusy(false);}
}
function render(data){
 summary.replaceChildren(node('span','llm-eyebrow','当前默认模型'));
 const active=data.active;
 if(active){summary.append(node('h3','',active.id==='environment'?'环境配置':active.name||active.model),node('p','',[(active.id==='environment'?'环境兼容配置':'已保存配置'),active.model,active.base_url].filter(Boolean).join(' · ')));}
 else summary.append(node('h3','','尚未配置默认模型'),node('p','','新增并设为默认后，平台即可使用该模型。'));
 if(!active&&data.environment_available)summary.append(node('p','','检测到环境配置，刷新后可查看生效状态。'));
 list.replaceChildren();const items=data.items||[];count.textContent=items.length+' 个配置';
 if(!items.length)list.append(node('p','llm-empty','还没有保存的模型。点击「新增模型」添加第一套配置。'));
 for(const item of items){
  const row=node('article','llm-profile'),identity=node('div','llm-identity'),title=node('div','llm-profile-title');title.append(node('h4','',item.name));
  if(item.is_active)title.append(node('span','llm-badge','默认'));
  identity.append(title,node('p','llm-model-name',item.model),node('p','llm-url',item.base_url),node('small','llm-key-state',item.has_api_key?'API key 已设置':'API key 未设置'));
  const actions=node('div','llm-actions');const activate=button(item.is_active?'当前默认':'设为默认',()=>run(item,'activate'));
  // dataset is a DOMStringMap in browsers; assign properties rather than markup from profile values.
  activate.dataset.active=String(!!item.is_active);activate.disabled=!!item.is_active;
  actions.append(button('编辑',()=>edit(item)),button('测试连接',()=>run(item,'test')),activate);row.append(identity,actions);list.append(row);
 }
}
async function run(item,action){
 if(busy||!canAccess())return;setBusy(true);flash(action==='test'?'正在测试连接…':'正在设置默认模型…');
 try{const result=await request(endpoint+'/'+encodeURIComponent(item.id)+'/'+action,'POST');if(action==='test')flash((result.ok?'连接成功：':'连接失败：')+(result.detail||'测试完成'),result.ok?'success':'error');else{await load();flash('默认模型已切换为「'+item.name+'」。','success');}}
 catch(error){flash(error.message,'error');}finally{setBusy(false);}
}
function edit(item=null){
 if(busy||!canAccess())return;dismissDialog?.();
 const previous=document.activeElement,dialog=node('dialog','llm-dialog');dialog.setAttribute('aria-labelledby','llm-dialog-title');dialog.setAttribute('aria-describedby','llm-dialog-help');
 const title=node('h2','',item?'编辑模型':'新增模型');title.id='llm-dialog-title';const help=node('p','llm-sub','填写 OpenAI 兼容接口配置。保存后可测试连接，再设为默认。');help.id='llm-dialog-help';
 const error=node('p','llm-flash is-error');error.setAttribute('role','alert');const form=node('form','llm-form');const fields={};
 for(const [name,label,type,placeholder] of [['name','配置名称','text','例如：生产模型'],['base_url','Base URL','url','https://api.example.com/v1'],['model','模型名','text','例如：gpt-4o-mini'],['api_key','API key','password',item?'留空保留已保存密钥':'输入 API key']]){
  const wrap=node('label','llm-field',label),input=node('input');input.name=name;input.type=type;input.placeholder=placeholder;input.autocomplete=name==='api_key'?'new-password':'off';input.required=name!=='api_key'||!item;input.value=name==='api_key'?'':item?.[name]||'';fields[name]=input;wrap.append(input);form.append(wrap);
 }
 const keyHelp=node('small','llm-sub',item?'API key 留空保留旧值；输入新值将替换旧密钥。':'API key 仅用于模型请求，保存后不会回显。');keyHelp.id='llm-key-help';fields.api_key.setAttribute('aria-describedby',keyHelp.id);form.append(keyHelp);
 const actions=node('div','llm-dialog-actions'),cancel=button('取消',()=>dialog.close()),save=button('保存配置',null,false);save.type='submit';actions.append(cancel,save);form.append(actions);dialog.append(title,help,error,form);document.body.append(dialog);
 let saving=false;const cleanup=()=>{fields.api_key.value='';dialog.remove();if(dismissDialog===dismiss)dismissDialog=null;previous?.focus();};const dismiss=()=>dialog.close();dismissDialog=dismiss;
 dialog.addEventListener('close',cleanup);dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
 form.onsubmit=async event=>{
  event.preventDefault();if(saving||!canAccess())return;error.textContent='';
  const payload={name:fields.name.value.trim(),base_url:fields.base_url.value.trim(),model:fields.model.value.trim(),api_key:fields.api_key.value};
  if(!payload.name||!payload.base_url||!payload.model||(!item&&!payload.api_key.trim())){error.textContent='请填写配置名称、Base URL、模型名和 API key。';return;}
  try{const url=new URL(payload.base_url);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw Error();}catch{error.textContent='Base URL 请输入不含账号、密钥参数或片段的 HTTP / HTTPS 地址。';return;}
  saving=true;save.disabled=true;error.textContent='正在保存…';
  const pending=request(endpoint+(item?'/'+encodeURIComponent(item.id):''),item?'PATCH':'POST',payload);payload.api_key='';fields.api_key.value='';
  try{await pending;dismiss();await load();flash(item?'模型配置已保存。':'模型配置已创建，可测试连接或设为默认。','success');}
  catch(failure){if(dialog.open)error.textContent=failure.message;}
  finally{saving=false;save.disabled=false;}
 };
 dialog.showModal();fields.name.focus();
}
document.addEventListener('workspace:page-changed',event=>{if(event.detail?.page!==destination)dismissDialog?.();});
window.LLMModels={open,canAccess};
})();
