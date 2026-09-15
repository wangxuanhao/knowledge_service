const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'), fs=require('node:fs');
function form(){
  const fields={};
  for(const id of ['doc-file','doc-text','doc-title','doc-meta','doc-published-at','project','ingest'])fields[id]={value:'',files:[],focus(){},closest(){return {classList:{toggle(){}}};}};
  const modes=[{value:'file',checked:false,addEventListener(){}},{value:'text',checked:true,addEventListener(){}}];
  const window={};
  vm.runInNewContext(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),{window,document:{getElementById:id=>fields[id],querySelectorAll:()=>modes},TextDecoder});
  return {fields,modes,read:window.readDocumentInput,metadata:window.readDocumentMetadata,batch:window.readDocumentBatch};
}
test('required fields fail before submission and metadata remains optional',async()=>{
  const {fields:f,read}=form();
  await assert.rejects(read(),/项目/);
  f.project.value='project';await assert.rejects(read(),/标题/);
  f['doc-title'].value='title';await assert.rejects(read(),/正文/);
  f['doc-text'].value='content';assert.equal(await read(),'content');
  f['doc-meta'].value='[]';await assert.rejects(read(),/JSON 对象/);
  f['doc-meta'].value='{broken';await assert.rejects(read(),/格式/);
  f['doc-meta'].value='{}';assert.equal(await read(),'content');
});
test('publication time is optional metadata, not a fact validity interval',()=>{
  const {fields:f,metadata}=form();
  assert.equal(JSON.stringify(metadata()),'{}');
  f['doc-meta'].value='{"平台":"测试"}';
  f['doc-published-at'].value='2026-09-01T08:30';
  const result=metadata();
  assert.equal(result.published_at,new Date('2026-09-01T08:30').toISOString());
  assert.equal(result['平台'],'测试');
  assert.equal(result.valid_from,undefined);
  assert.equal(result.uploaded_at,undefined);
  for(const name of ['app.js','workbench.js']){
    const source=fs.readFileSync('knowledge_service/web/'+name,'utf8');
    assert.doesNotMatch(source,/doc-from|doc-until/);
    assert.match(source,/metadata:(readDocumentMetadata\(\)|document.metadata)/);
  }
});
test('multiple UTF-8 files remain independent documents with source names',async()=>{
  const {fields:f,modes,batch}=form();
  modes[0].checked=true;modes[1].checked=false;
  f.project.value='p';f['doc-title'].value='first';
  f['doc-meta'].value='{"平台":"测试"}';
  const file=(name,text)=>({name,arrayBuffer:async()=>new TextEncoder().encode(text).buffer});
  f['doc-file'].files=[file('甲.txt','第一份正文'),file('乙.md','# 第二份正文')];
  const rows=await batch();
  assert.equal(rows.length,2);
  assert.equal(rows[0].title,'甲.txt');assert.equal(rows[1].title,'乙.md');
  assert.equal(rows[0].text,'第一份正文');assert.equal(rows[1].text,'# 第二份正文');
  assert.equal(rows[1].metadata.source_file,'乙.md');assert.equal(rows[1].metadata['平台'],'测试');
  f['doc-file'].files[1]=file('空.md','  ');
  await assert.rejects(batch(),/空.md.*正文不能为空/);
  f['doc-file'].files[1]={name:'坏.txt',arrayBuffer:async()=>new Uint8Array([255]).buffer};
  await assert.rejects(batch(),/坏.txt.*UTF-8/);
});
test('swapping the selected file refreshes an auto-filled title but keeps a custom one',()=>{
  const {fields:f,modes}=form();
  modes[0].checked=true;modes[1].checked=false;
  const file=name=>({name,arrayBuffer:async()=>new TextEncoder().encode('正文').buffer});
  f['doc-file'].files=[file('旧.md')];f['doc-file'].onchange();
  assert.equal(f['doc-title'].value,'旧.md');
  f['doc-file'].files=[file('新.md')];f['doc-file'].onchange();
  assert.equal(f['doc-title'].value,'新.md');
  f['doc-title'].value='自定义标题';
  f['doc-file'].files=[file('另一个.md')];f['doc-file'].onchange();
  assert.equal(f['doc-title'].value,'自定义标题');
});
