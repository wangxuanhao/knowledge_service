const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function setup(){
  class Element{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.events={};this.attributes={};this.hidden=false;this.value='';this.files=[];this._text='';this.className='';
      this.classList={contains:name=>this.className.split(/\s+/).includes(name),add:name=>{if(!this.classList.contains(name))this.className+=' '+name;},remove:name=>{this.className=this.className.split(/\s+/).filter(x=>x!==name).join(' ');},toggle:(name,on)=>{this.classList[on?'add':'remove'](name);}};}
    append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
    prepend(...nodes){this.append(...nodes);this.children=[...nodes,...this.children.filter(n=>!nodes.includes(n))];}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null;}
    replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this._text='';this.append(...nodes);}
    set textContent(value){this.replaceChildren();this._text=String(value);}get textContent(){return this._text+this.children.map(n=>n.textContent).join(' ');}
    setAttribute(name,value){this.attributes[name]=String(value);}getAttribute(name){return this.attributes[name];}
    addEventListener(name,fn){(this.events[name]??=[]).push(fn);}dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event);}
    matches(selector){return selector.startsWith('.')?this.classList.contains(selector.slice(1)):selector.startsWith('#')?this.id===selector.slice(1):this.tagName===selector.toUpperCase();}
    closest(selector){return this.matches(selector)?this:this.parentNode?.closest(selector)||null;}
    querySelectorAll(selector){return descendants(this).filter(n=>selector==='[data-upload-step]'?n.dataset.uploadStep!==undefined:selector.includes(' > ')?n.matches(selector.split(' > ').at(-1)):n.matches(selector));}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    get firstElementChild(){return this.children[0];}focus(){this.focused=true;}
  }
  const descendants=node=>node.children.flatMap(n=>[n,...descendants(n)]);
  const document={createElement:tag=>new Element(tag),getElementById:id=>descendants(document.body).find(n=>n.id===id)||null,
    querySelector:selector=>document.body.querySelector(selector),querySelectorAll:selector=>selector==='input[name="document-input-mode"]'?[]:document.body.querySelectorAll(selector),addEventListener(){}};
  document.body=new Element('body');document.documentElement=new Element('html');
  const make=(tag,id,css,parent=document.body)=>{const n=new Element(tag);n.id=id;n.className=css||'';parent.append(n);return n;};
  const host=make('section','tab-ingest');make('h2',null,null,host);
  const panel=make('div',null,'document-upload-panel',host),workspace=make('div',null,'document-upload-workspace',panel),main=make('div',null,'document-upload-main',workspace);
  const zone=make('section',null,'document-upload-zone',main),drop=make('label','doc-dropzone','document-dropzone',zone);make('input','doc-file',null,drop);
  for(const id of ['doc-upload-formats','doc-upload-extensions','doc-upload-limits','doc-upload-package-help','preview-source','preview-chunks'])make('span',id,null,zone);
  const list=make('section',null,'document-queue',main);
  for(const id of ['doc-upload-summary','doc-file-list','doc-batch-help','doc-submit-results','doc-retry-files','doc-clear-files'])make('div',id,null,list);
  const options=make('section',null,'document-options',workspace),metaLabel=make('label',null,null,options);make('input','doc-meta',null,metaLabel);make('p','doc-meta-help',null,options);
  make('div','ingest-mode-slot',null,panel);make('details',null,'parse-settings',panel);const actions=make('div',null,'document-actions',panel);make('button','ingest',null,actions);
  for(const id of ['doc-text','doc-title','doc-published-at','doc-time-mode','doc-valid-from','doc-valid-until','doc-effective-help'])make('input',id,null,options);
  const mode=make('select','extraction-mode',null,options);mode.value='documents';mode.selectedOptions=[{textContent:'仅文档检索'}];
  make('section','ingest-batches',null,host);make('div',null,'ingest-results-bar',host);const structured=make('details',null,'structured-import',host);make('summary',null,null,structured);make('textarea','batch',null,structured);
  const sources=make('section','tab-sources'),sourcePanel=make('div',null,'panel',sources),controls=make('div',null,'row',sourcePanel);make('button','upload-source',null,controls);
  make('button','open-ingest-results');make('div','chunk-preview');make('div','status');const project=make('select','project');project.value='p';project.selectedOptions=[{textContent:'测试项目'}];
  const context={document,current:'p',Auth:{isAdmin:()=>true},Event:class{constructor(type){this.type=type;}},IngestBatches:{loadCapabilities(){},visibilityChanged(){}},IngestOntology:{refresh(){}},
    WorkspaceDrawers:{create(options){const dialog=make('dialog',options.id),body=make('div',null,null,dialog);make('h2',options.id+'-title',null,dialog);body.append(options.content);return {dialog,body,open(){dialog.open=true;},close(){dialog.open=false;options.onClose();}};}}};
  context.window=context;vm.createContext(context);vm.runInContext(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),context);vm.runInContext(fs.readFileSync('knowledge_service/web/library-workspace.js','utf8'),context);
  const get=id=>document.getElementById(id),visible=node=>!node.hidden&&!node.classList.contains('hidden')&&(!node.parentNode||visible(node.parentNode));
  const file=name=>({name,size:10,lastModified:1});
  return {context,get,visible,file,steps:()=>document.body.querySelectorAll('[data-upload-step]'),go(index){this.steps()[index].onclick();}};
}

test('only step one shows upload controls; later steps show a compact selection and return without clearing appendable files',()=>{
  const env=setup(),queue=env.context.DocumentUploadQueue,first=env.file('first.pdf');
  env.context.LibraryWorkspace.open('upload');queue.add([first,env.file('second.docx')]);
  assert.equal(env.visible(env.get('doc-dropzone')),true);assert.equal(env.visible(env.get('doc-file-list')),true);
  for(const step of [1,2]){
    env.go(step);
    assert.equal(env.visible(env.get('doc-dropzone')),false,'the full dropzone belongs only to step one');
    assert.equal(env.visible(env.get('doc-file-list')),false,'later steps do not repeat the full queue');
    const compact=env.get('library-upload-selection');assert.ok(compact);assert.equal(env.visible(compact),true);
    assert.match(compact.textContent,/2 份资料/);assert.match(compact.textContent,/first\.pdf.*second\.docx/);
    const edit=compact.querySelectorAll('button').find(n=>n.textContent==='返回修改文件');assert.ok(edit);edit.onclick();
    assert.equal(env.context.LibraryWorkspace.snapshot().step,0);assert.equal(env.visible(env.get('doc-dropzone')),true);
    assert.equal(queue.items()[0].file,first,'returning retains the original File objects');assert.equal(queue.items().length,2);
  }
  queue.add([env.file('third.webp')]);env.go(1);
  assert.match(env.get('library-upload-selection').textContent,/3 份资料/);
  assert.match(env.get('library-upload-selection').textContent,/third\.webp/);
  env.context.LibraryWorkspace.close();env.context.LibraryWorkspace.open();
  assert.equal(env.context.LibraryWorkspace.snapshot().step,1);assert.equal(queue.items().length,3);
  assert.equal(env.visible(env.get('doc-dropzone')),false);
});

test('later steps bound a long file list and queue changes refresh their compact summary',()=>{
  const env=setup(),queue=env.context.DocumentUploadQueue;env.context.LibraryWorkspace.open('upload');
  queue.add(Array.from({length:8},(_,index)=>env.file('doc-'+index+'.pdf')));env.go(1);
  const compact=env.get('library-upload-selection');assert.ok(compact);assert.match(compact.textContent,/8 份资料/);
  assert.match(compact.textContent,/doc-0\.pdf/);assert.doesNotMatch(compact.textContent,/doc-7\.pdf/);assert.match(compact.textContent,/另 5 份/);
  queue.remove(queue.items()[0].file);assert.match(compact.textContent,/7 份资料/);assert.doesNotMatch(compact.textContent,/doc-0\.pdf/);
});
