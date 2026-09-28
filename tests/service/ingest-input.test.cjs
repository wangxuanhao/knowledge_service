const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');

function form(){
  const fields={};
  const ids=['doc-file','doc-text','doc-title','doc-meta','doc-published-at','project','ingest',
    'doc-file-list','doc-batch-help','doc-upload-summary','doc-clear-files','doc-retry-files','doc-dropzone'];
  for(const id of ids)fields[id]={value:'',files:[],hidden:false,disabled:false,required:false,textContent:'',
    focus(){},addEventListener(){},classList:{toggle(){},add(){},remove(){}},closest(){return {classList:{toggle(){}}};},
    replaceChildren(){},append(){}};
  const modes=[{value:'file',checked:false,addEventListener(){}},{value:'text',checked:true,addEventListener(){}}];
  const window={};
  const document={getElementById:id=>fields[id],querySelectorAll:()=>modes,createElement:()=>({className:'',textContent:'',dataset:{},append(){},addEventListener(){}})};
  vm.runInNewContext(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),{window,document,JSON,Date,Error});
  return {fields,modes,queue:window.DocumentUploadQueue,read:window.readDocumentInput,
    metadata:window.readDocumentMetadata,batch:window.readDocumentBatch};
}

const file=(name,size=10,lastModified=1)=>({name,size,lastModified});

test('pasted text keeps title and metadata validation',async()=>{
  const {fields:f,read}=form();
  await assert.rejects(read(),/项目/);
  f.project.value='project';await assert.rejects(read(),/标题/);
  f['doc-title'].value='title';await assert.rejects(read(),/正文/);
  f['doc-text'].value='content';assert.equal(await read(),'content');
  f['doc-meta'].value='[]';await assert.rejects(read(),/JSON 对象/);
  f['doc-meta'].value='{broken';await assert.rejects(read(),/格式/);
});

test('file queue accepts supported formats without browser decoding',async()=>{
  const {fields:f,modes,queue,batch}=form();
  modes[0].checked=true;modes[1].checked=false;f.project.value='p';
  const selected=[file('a.txt'),file('b.md'),file('c.pdf'),file('d.docx'),file('e.html'),file('f.htm')];
  queue.add(selected);
  const rows=await batch();
  assert.equal(rows.length,6);
  assert.equal(JSON.stringify(rows.map(row=>row.file.name)),JSON.stringify(selected.map(item=>item.name)));
  assert.equal(rows[2].text,undefined);
  assert.equal(rows[2].title,'c.pdf');
});

test('file queue enforces type, per-file, count, total and duplicate limits',()=>{
  const {queue}=form();
  assert.throws(()=>queue.add([file('deck.pptx')]),/支持/);
  assert.throws(()=>queue.add([file('large.pdf',25_000_001)]),/25 MB/);
  assert.throws(()=>queue.add(Array.from({length:21},(_,i)=>file(i+'.txt'))),/20/);
  assert.throws(()=>queue.add([file('a.pdf',24_000_000),file('b.pdf',24_000_000),file('c.pdf',24_000_000),file('d.pdf',24_000_000),file('e.pdf',24_000_000)]),/100 MB/);
  queue.add([file('same.pdf',10,7),file('same.pdf',10,7)]);
  assert.equal(queue.items().length,1);
});

test('successful files are skipped while failed files remain retryable',()=>{
  const {queue}=form();
  const a=file('a.pdf'),b=file('b.docx');queue.add([a,b]);
  queue.setStatus(a,'queued','job-a');queue.setStatus(b,'failed','bad');
  assert.equal(JSON.stringify(queue.eligible().map(item=>item.file.name)),JSON.stringify(['b.docx']));
  queue.remove(b);
  assert.equal(JSON.stringify(queue.items().map(item=>item.file.name)),JSON.stringify(['a.pdf']));
  queue.clear();assert.equal(queue.items().length,0);
});

test('publication time remains optional metadata rather than validity',()=>{
  const {fields:f,metadata}=form();
  f['doc-meta'].value='{"平台":"测试"}';
  f['doc-published-at'].value='2026-09-01T08:30';
  const result=metadata();
  assert.equal(result.published_at,new Date('2026-09-01T08:30').toISOString());
  assert.equal(result['平台'],'测试');
  assert.equal(result.valid_from,undefined);
});
