const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const source=fs.readFileSync('knowledge_service/web/workbench.js','utf8');
const previewSource=source.slice(source.indexOf('const chunkPreviewState='),source.indexOf("bind('ingest',async()=>{"));
const optionsSource=source.slice(source.indexOf('function documentUploadOptions(document)'),source.indexOf('const chunkPreviewState='));
const bindSource=fs.readFileSync('knowledge_service/web/app.js','utf8').match(/^function bind\([^\n]+/m)[0];
const result=text=>({total:1,chunks:[{text,start_char:0,end_char:text.length}]});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

function fixture(respond=(_file,index)=>result('正文 '+index),input='file'){
  class Element{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.events={};this.attributes={};this.dataset={};this.value='';this.hidden=false;this.isConnected=true;
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
  const documents=files.map(file=>({title:file.name,metadata:{},document_time_mode:'auto',
    ...(input==='file'?{file}:{text:'正文'})}));
  const calls=[],messages=[];
  class FormData{constructor(){this.values=new Map();}append(key,value){this.values.set(key,value);}get(key){return this.values.get(key);}}
  const window={DocumentUploadQueue:{generation:1},LibraryWorkspace:{ticket:()=>({}),accepts:()=>true}};
  const context={document,window,FormData,current:'project',$:get,
    controlsProject:()=>context.current,readParseSettings:()=>({chunk_size:900,chunk_overlap:50}),
    readDocumentBatch:async()=>documents,
    status:(...args)=>messages.push(args),
    api:async(url,payload)=>{
      const file=payload instanceof FormData?payload.get('file'):null;
      const options=file?JSON.parse(payload.get('options')):payload;
      const index=file?files.indexOf(file):documents.findIndex(doc=>doc.title===options.title);
      const packagePath=file?payload.get('package_path'):undefined;
      const previewView=file?payload.get('preview_view'):undefined;
      calls.push({url,file,options,packagePath,previewView});return respond(file,index,packagePath);
    }};
  vm.runInNewContext(bindSource+'\n'+optionsSource+'\n'+previewSource,context);
  return {get,context,window,files,calls,messages,preview:()=>get('preview-chunks').onclick(),
    previewSource:()=>get('preview-source').onclick(),
    async select(index){const picker=get('chunk-preview-file');picker.value=String(index);assert.equal(typeof picker.events.change,'function','file picker must switch the preview');await picker.events.change();},
    async selectDocument(path){const picker=get('chunk-preview-document');picker.value=path;await picker.events.change();},
    close(){get('chunk-preview-close').events.click();}};
}

test('chunk preview stays on chunks even when a legacy response includes article images',async()=>{
  const env=fixture(()=>({...result('分片结果'),source_preview:{html:'完整原文',images:[],assets:{}}}));
  await env.preview();
  assert.equal(env.calls[0].previewView,'chunks');
  assert.match(env.get('chunk-preview-content').textContent,/分片结果/);
  assert.ok(env.get('chunk-preview-list').children.every(button=>button.textContent!=='图文正文'));
  assert.equal(env.get('chunk-preview-list').hidden,false);
});

test('the separate source preview does not require chunk settings and restores focus to its own button',async()=>{
  const env=fixture(()=>({source_preview:{html:'完整原文',images:[],assets:{}},title:'原文标题'}));
  env.context.readParseSettings=()=>{throw Error('invalid chunk settings');};
  env.window.SourceContent={createSession:({host})=>({mount(html){host.textContent=html;},hydrateImages(){},dispose(){}})};
  await env.previewSource();
  assert.equal(env.calls[0].previewView,'source');
  assert.equal(env.get('chunk-preview-title').textContent,'原文预览');
  assert.equal(env.get('chunk-preview-list').hidden,true);
  assert.match(env.get('chunk-preview-content').textContent,/完整原文/);
  env.close();assert.equal(env.context.document.activeElement,env.get('preview-source'));
});

test('source preview hides the chunk navigation while the article is still loading',async()=>{
  const slow=deferred(),env=fixture(()=>slow.promise);
  const opening=env.previewSource();await new Promise(setImmediate);
  try{assert.equal(env.get('chunk-preview-list').hidden,true);}
  finally{env.close();slow.resolve({source_preview:{html:'原文',images:[],assets:{}}});await opening;}
});

for(const input of ['file','text'])test(input+' preview forwards the automatic document time mode through the normal route',async()=>{
  const env=fixture(undefined,input);await env.preview();
  assert.equal(env.calls[0].options.document_time_mode,'auto');
  assert.match(env.calls[0].url,input==='file'?/documents\/upload\/preview$/:/documents\/preview$/);
});

const packageResult=path=>({...result('包内正文 '+path),title:'标题 '+path,
  package:{filename:'rules.zip',selected_path:path,document_count:2,image_count:3,
    documents:[{path:'platform/a.md',title:'规则 A'},{path:'platform/b.md',title:'规则 B'}]}});

test('ZIP preview exposes member documents and switches the requested article with matching cache',async()=>{
  const env=fixture((_file,index,path)=>index===0?packageResult(path||'platform/a.md'):result('普通文件'));
  env.files[0].name='rules.zip';
  await env.preview();
  assert.equal(env.get('chunk-preview-package').hidden,false);
  assert.deepEqual(env.get('chunk-preview-document').children.map(option=>option.value),['platform/a.md','platform/b.md']);
  assert.match(env.get('chunk-preview-package-summary').textContent,/2.*3/);
  await env.selectDocument('platform/b.md');
  assert.equal(env.calls[1].packagePath,'platform/b.md');
  assert.match(env.get('chunk-preview-content').textContent,/包内正文 platform\/b.md/);
  await env.selectDocument('platform/a.md');
  assert.equal(env.calls.length,2);
  assert.match(env.get('chunk-preview-content').textContent,/包内正文 platform\/a.md/);
  await env.select(1);
  assert.equal(env.get('chunk-preview-package').hidden,true);
  assert.match(env.get('chunk-preview-content').textContent,/普通文件/);
});

test('a pending ZIP member cannot overwrite the member or upload file selected afterwards',async()=>{
  const slow=deferred();
  const env=fixture((_file,index,path)=>index===1?result('普通文件'):
    path==='platform/b.md'?slow.promise:packageResult('platform/a.md'));
  await env.preview();
  const pending=env.selectDocument('platform/b.md');
  await env.select(1);
  slow.resolve(packageResult('platform/b.md'));await pending;
  assert.equal(env.get('chunk-preview-package').hidden,true);
  assert.match(env.get('chunk-preview-content').textContent,/普通文件/);
});

test('retrying a failed ZIP member retries that member instead of showing the previous cached article',async()=>{
  let attempts=0;
  const env=fixture((_file,_index,path)=>{
    if(path==='platform/b.md'&&attempts++===0)throw Error('第二份暂时失败');
    return packageResult(path||'platform/a.md');
  });
  await env.preview();await env.selectDocument('platform/b.md');
  assert.match(env.get('chunk-preview-content').textContent,/第二份暂时失败/);
  const retry=env.get('chunk-preview-content').children.find(child=>child.textContent==='重试此文件');
  await retry.events.click();
  assert.equal(env.calls.at(-1).packagePath,'platform/b.md');
  assert.equal(env.calls.length,3);
  assert.match(env.get('chunk-preview-content').textContent,/包内正文 platform\/b.md/);
});

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

test('each file preview displays the returned document time and parsed evidence',async()=>{
  const env=fixture((_file,index)=>({...result('正文 '+index),document_time:{
    published_at:'2026-09-24T16:00:00Z',valid_from:null,valid_until:null,
    document_temporal:{fields:{published_at:{status:'extracted',candidates:[{
      raw:'发布时间'+index,start_char:index,end_char:index+5}]}},warnings:[]}}}));
  env.window.DocumentTemporalUI=require('../../knowledge_service/web/document-temporal-ui.js');
  await env.preview();
  assert.match(env.get('chunk-preview-summary').children.at(-1).innerHTML,/原文发布时间/);
  assert.match(env.get('chunk-preview-summary').children.at(-1).innerHTML,/发布时间0/);
  await env.select(1);
  assert.match(env.get('chunk-preview-summary').children.at(-1).innerHTML,/发布时间1/);
  await env.select(0);
  assert.match(env.get('chunk-preview-summary').children.at(-1).innerHTML,/发布时间0/);
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
