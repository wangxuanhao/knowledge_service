const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const source=fs.readFileSync('knowledge_service/web/workbench.js','utf8');
const previewSource=source.slice(source.indexOf('const chunkPreviewState='),source.indexOf("bind('ingest',async()=>{"));
const bindSource=fs.readFileSync('knowledge_service/web/app.js','utf8').match(/^function bind\([^\n]+/m)[0];
const result=text=>({total:1,chunks:[{text,start_char:0,end_char:text.length}]});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

function fixture(respond=(_file,index)=>result('正文 '+index)){
  class Element{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.events={};this.attributes={};this.value='';this.hidden=false;this.isConnected=true;
      this.classList={add(){},remove(){},toggle(){}};}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.text='';this.children=items;}
    setAttribute(key,value){this.attributes[key]=value;}
    removeAttribute(key){delete this.attributes[key];}
    getAttribute(key){return this.attributes[key];}
    addEventListener(name,handler){this.events[name]=handler;}
    querySelectorAll(){return this.children.filter(item=>item.tagName==='button');}
    focus(){document.activeElement=this;}
    get textContent(){return this.text||this.children.map(item=>item.textContent||'').join(' ');}
    set textContent(value){this.text=value;this.children=[];}
  }
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  get('chunk-preview').hidden=true;
  const document={body:new Element(),documentElement:new Element(),createElement:tag=>new Element(tag),
    getElementById:get,querySelector:()=>null,addEventListener(){}};
  const files=[{name:'first.txt'},{name:'second.txt'}];
  const documents=files.map(file=>({title:file.name,file,metadata:{}}));
  const calls=[],messages=[];
  class FormData{constructor(){this.values=new Map();}append(key,value){this.values.set(key,value);}get(key){return this.values.get(key);}}
  const window={DocumentUploadQueue:{generation:1},LibraryWorkspace:{ticket:()=>({}),accepts:()=>true}};
  const context={document,window,FormData,current:'project',$:get,
    controlsProject:()=>context.current,readParseSettings:()=>({chunk_size:900,chunk_overlap:50}),
    readDocumentBatch:async()=>documents,documentUploadOptions:doc=>({metadata:doc.metadata}),
    status:(...args)=>messages.push(args),
    api:async(url,form)=>{const file=form.get('file');calls.push({url,file,options:JSON.parse(form.get('options'))});return respond(file,files.indexOf(file));}};
  vm.runInNewContext(bindSource+'\n'+previewSource,context);
  return {get,context,window,files,calls,messages,preview:()=>get('preview-chunks').onclick(),
    async select(index){const picker=get('chunk-preview-file');picker.value=String(index);assert.equal(typeof picker.events.change,'function','file picker must switch the preview');await picker.events.change();},
    close(){get('chunk-preview-close').events.click();}};
}

test('two uploaded files can each be previewed and switching back reuses the matching result',async()=>{
  const env=fixture();await env.preview();
  assert.deepEqual(env.get('chunk-preview-file').children.map(option=>option.textContent),['first.txt','second.txt']);
  assert.equal(env.calls.length,1);
  assert.match(env.get('chunk-preview-content').textContent,/正文 0/);
  await env.select(1);
  assert.equal(env.calls[1].file,env.files[1]);
  assert.equal(env.calls[1].options.chunk_size,900);
  assert.match(env.get('chunk-preview-summary').textContent,/second.txt/);
  assert.match(env.get('chunk-preview-content').textContent,/正文 1/);
  await env.select(0);
  assert.equal(env.calls.length,2);
  assert.match(env.get('chunk-preview-content').textContent,/正文 0/);
});

test('a slow file response cannot overwrite the file selected afterwards',async()=>{
  const slow=deferred(),env=fixture((_file,index)=>index===1?slow.promise:result('第一份'));
  await env.preview();const loading=env.select(1);await env.select(0);
  slow.resolve(result('第二份'));await loading;
  assert.match(env.get('chunk-preview-content').textContent,/第一份/);
  assert.doesNotMatch(env.get('chunk-preview-content').textContent,/第二份/);
});

test('a failed file can be retried without losing previews for the other files',async()=>{
  let attempts=0;
  const env=fixture((_file,index)=>{if(index===1&&attempts++===0)throw Error('无法解析第二份');return result('正文 '+index);});
  await env.preview();await env.select(1);
  assert.match(env.get('chunk-preview-content').textContent,/无法解析第二份/);
  assert.equal(env.get('chunk-preview-list').children.length,0);
  await env.select(0);await env.select(1);
  assert.match(env.get('chunk-preview-content').textContent,/正文 1/);
});

test('the second file remains available when the first file cannot be parsed',async()=>{
  const env=fixture((_file,index)=>{if(index===0)throw Error('首份解析失败');return result('第二份正常');});
  await env.preview();
  assert.equal(env.get('chunk-preview').hidden,false);
  assert.match(env.get('chunk-preview-content').textContent,/首份解析失败/);
  await env.select(1);
  assert.match(env.get('chunk-preview-content').textContent,/第二份正常/);
});

test('closing the drawer discards a pending file response',async()=>{
  const slow=deferred(),env=fixture((_file,index)=>index===1?slow.promise:result('第一份'));
  await env.preview();const loading=env.select(1);env.close();
  slow.resolve(result('已关闭的第二份'));await loading;
  assert.equal(env.get('chunk-preview').hidden,true);
  assert.doesNotMatch(env.get('chunk-preview-content').textContent,/已关闭的第二份/);
});

test('closing the first loading preview allows a new session before the old response finishes',async()=>{
  const slow=deferred();let attempts=0;
  const env=fixture(()=>attempts++===0?slow.promise:result('新的预览'));
  const opening=env.preview();
  await new Promise(setImmediate);
  try{
    assert.equal(env.get('chunk-preview').hidden,false);
    env.close();
    assert.equal(env.get('preview-chunks').disabled,false,'closing must allow another preview immediately');
    await env.preview();
    assert.match(env.get('chunk-preview-content').textContent,/新的预览/);
  }finally{slow.resolve(result('过期的首份预览'));await opening;}
  assert.doesNotMatch(env.get('chunk-preview-content').textContent,/过期的首份预览/);
});

test('starting another preview reads the current files and settings again',async()=>{
  const env=fixture();await env.preview();env.close();
  env.context.readParseSettings=()=>({chunk_size:1200,chunk_overlap:100});
  await env.preview();
  assert.equal(env.calls.length,2);
  assert.equal(env.calls[1].options.chunk_size,1200);
});

for(const invalidate of ['project','queue','workspace']){
  test(`a pending response is ignored after changing the ${invalidate}`,async()=>{
    const slow=deferred(),env=fixture((_file,index)=>index===1?slow.promise:result('第一份'));
    await env.preview();const loading=env.select(1);
    if(invalidate==='project')env.context.current='another-project';
    if(invalidate==='queue')env.window.DocumentUploadQueue.generation++;
    if(invalidate==='workspace')env.window.LibraryWorkspace.accepts=()=>false;
    slow.resolve(result('过期结果'));await loading;
    assert.doesNotMatch(env.get('chunk-preview-content').textContent,/过期结果/);
  });
}
