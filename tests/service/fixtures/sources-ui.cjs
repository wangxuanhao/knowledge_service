const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

// Small DOM boundary for the browser module, paired with the real drawer manager
// and deferred API reads to exercise focus, node movement and request lifetimes.
class Element {
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.dataset={};this.attributes={};this.listeners={};this.className='';this._text='';this.hidden=false;this.open=false;this.scrollTop=0;
    this.classList={contains:name=>this.className.split(/\s+/).includes(name),add:(...names)=>names.forEach(name=>{if(!this.classList.contains(name))this.className+=' '+name;}),remove:(...names)=>{this.className=this.className.split(/\s+/).filter(name=>!names.includes(name)).join(' ');},toggle:(name,force)=>{const add=force??!this.classList.contains(name);this.classList[add?'add':'remove'](name);return add;}};}
  append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}}
  before(node){const parent=this.parentNode,index=parent.children.indexOf(this);node.remove();node.parentNode=parent;parent.children.splice(index,0,node);}
  after(node){const parent=this.parentNode,index=parent.children.indexOf(this);node.remove();node.parentNode=parent;parent.children.splice(index+1,0,node);}
  replaceChildren(...nodes){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...nodes);}
  set textContent(value){this.replaceChildren();this._text=String(value);}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  set innerHTML(html){this.replaceChildren();const stack=[this];for(const token of String(html).match(/<[^>]+>|[^<]+/g)||[]){if(token.startsWith('</')){stack.pop();continue;}if(token.startsWith('<')){const tag=token.match(/^<(\w+)/)?.[1];if(!tag)continue;const node=this.ownerDocument.createElement(tag);for(const match of token.matchAll(/([\w-]+)="([^"]*)"/g))node.setAttribute(match[1],match[2]);stack.at(-1).append(node);if(!['input','br','hr'].includes(tag))stack.push(node);}else stack.at(-1)._text+=token;}}
  setAttribute(name,value){this.attributes[name]=String(value);if(name==='class')this.className=String(value);if(name==='id')this.id=String(value);if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,char)=>char.toUpperCase())]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  dispatch(name,event={}){const pending=[];for(const fn of this.listeners[name]||[])pending.push(fn({target:this,preventDefault(){},...event}));if(this['on'+name])pending.push(this['on'+name]({target:this,preventDefault(){},...event}));return Promise.all(pending);}
  click(){return this.dispatch('click');}
  focus(){this.ownerDocument.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;for(const fn of this.listeners.close||[])fn();}
  get isConnected(){return this===this.ownerDocument.body||Boolean(this.parentNode?.isConnected);}
  querySelectorAll(selector){return descendants(this).filter(node=>selector.startsWith('.')?node.classList.contains(selector.slice(1)):selector.startsWith('#')?node.id===selector.slice(1):selector.startsWith('[')?node.getAttribute(selector.slice(1,-1).split('=')[0])===(selector.match(/="([^"]*)"/)?.[1]??node.getAttribute(selector.slice(1,-1))):node.tagName===selector.toUpperCase());}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const documentRow=(id,status='completed')=>({id,text:'原文 '+id,derived_count:3,metadata:{title:'资料 '+id,source_file:id+'.txt',source_format:'txt',source_size_bytes:120,status,source_document_version_id:'version-'+id}});

function setup({documents=[documentRow('one'),documentRow('two','failed')],writer=true}={}){
  const document={createElement(tag){return new Element(tag,this);},getElementById(id){return descendants(this.body).find(node=>node.id===id)||null;},querySelector(selector){return this.body.querySelector(selector);},querySelectorAll(selector){return this.body.querySelectorAll(selector);},addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);},listeners:{},async dispatch(name,event){await Promise.all((this.listeners[name]||[]).map(fn=>fn(event)));}};
  document.body=document.createElement('body');document.activeElement=document.body;
  const make=(tag,id,parent=document.body)=>{const node=document.createElement(tag);node.id=id;parent.append(node);return node;};
  const page=make('section','tab-sources'),panel=make('div','sources-panel',page),layout=make('div','sources-layout',panel);layout.className='source-layout';
  const list=make('div','source-list',layout),article=make('article','source-article',layout),title=make('h3','source-title',article),body=make('pre','source-body',article);
  const refresh=make('button','load-sources',panel),upload=make('button','upload-source',panel),project=make('select','project');
  const requests=[],drawers=[],navigation=[],external=[];let scopeRevision=0,scopeValue={filters:null};
  const WorkspaceDrawers=require('../../../knowledge_service/web/workspace-drawers.js').createManager(document),createDrawer=WorkspaceDrawers.create;
  WorkspaceDrawers.create=options=>{const drawer=createDrawer(options);drawer.options=options;drawers.push(drawer);return drawer;};
  const context={document,current:'project-one',console,WorkspaceDrawers,Auth:{isAdmin:()=>writer},WorkspaceContext:{ticket:(project,page)=>({project,page,revision:scopeRevision}),accepts:(ticket,project,page)=>ticket.project===project&&ticket.page===page&&ticket.revision===scopeRevision},
    $:id=>document.getElementById(id),esc:value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),scope:()=>scopeValue,endpoint:suffix=>'/api/projects/'+context.current+suffix,
    KnowledgeTabs:{open:name=>navigation.push(name)},LibraryWorkspace:{open:name=>navigation.push(name)},SourceReader:{open:args=>external.push({type:'reader',args,drawerOpen:Boolean(WorkspaceDrawers.active)})},openKnowledgeRecord:async args=>external.push({type:'record',args,drawerOpen:Boolean(WorkspaceDrawers.active)}),
    api:(url,body,method)=>{
      if(url.includes('/sources')){
        const query=new URL(url,'http://test').searchParams,state=query.get('state')||'all',id=query.get('document_id');
        const status=doc=>doc.metadata?.status==='failed'?'failed':doc.metadata?.status==='processing'?'processing':'ready';
        const counts={all:documents.length,ready:0,failed:0,processing:0};documents.forEach(doc=>counts[status(doc)]++);
        const found=documents.filter(doc=>(!id||doc.id===id)&&(state==='all'||status(doc)===state));
        return Promise.resolve({documents:found.map(doc=>({...doc,text:undefined})),total:found.length,counts});
      }
      if(/\/records\/[^/?]+(?:\?.*)?$/.test(url))return Promise.resolve(documents.find(doc=>url.split('?')[0].endsWith('/'+doc.id))||{});
      return new Promise((resolve,reject)=>requests.push({url,body,method,resolve,reject}));}};
  context.window=context;vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../../knowledge_service/web/sources-view.js'),'utf8'),context);
  return {context,document,page,list,article,title,body,refresh,upload,project,drawers,requests,navigation,external,documents,get:id=>document.getElementById(id),rows:()=>list.querySelectorAll('.source-item'),tab:name=>document.querySelector('.source-detail-tabs').querySelectorAll('button').find(button=>button.textContent===name),filter:name=>document.querySelectorAll('.source-filter-btn').find(button=>button.dataset.filter===name),setDocuments:rows=>{documents=rows;},setWriter:value=>{writer=value;},changeScope:async()=>{scopeRevision++;scopeValue={filters:{status:'changed'}};await document.dispatch('workspace:scope-changed',{detail:{page:'sources'}});await settle();}};
}


module.exports={setup,documentRow,settle};
