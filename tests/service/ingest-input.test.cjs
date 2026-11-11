const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');

function form(){
  const fields={};
  const ids=['doc-file','doc-text','doc-title','doc-meta','doc-published-at','doc-time-mode',
    'doc-valid-from','doc-valid-until','doc-effective-help','project','ingest',
    'doc-file-list','doc-batch-help','doc-upload-summary','doc-clear-files','doc-retry-files','doc-dropzone',
    'doc-upload-formats','doc-upload-extensions','doc-upload-package-help','doc-upload-limits'];
  for(const id of ids)fields[id]={value:'',files:[],hidden:false,disabled:false,required:false,textContent:'',events:{},
    focus(){},addEventListener(name,handler){this.events[name]=handler;},classList:{toggle(){},add(){},remove(){}},closest(){return {classList:{toggle(){}}};},
    replaceChildren(){},append(){}};
  const modes=[{value:'file',checked:false,addEventListener(){}},{value:'text',checked:true,addEventListener(){}}];
  const window={};
  const document={getElementById:id=>fields[id],querySelector:()=>null,querySelectorAll:()=>modes,createElement:()=>({className:'',textContent:'',dataset:{},append(){},addEventListener(){}})};
  vm.runInNewContext(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),{window,document,JSON,Date,Error});
  return {fields,modes,window,queue:window.DocumentUploadQueue,read:window.readDocumentInput,
    metadata:window.readDocumentMetadata,batch:window.readDocumentBatch,
    options:window.readDocumentUploadOptions,describe:window.ApplicabilityUI.describe};
}

const file=(name,size=10,lastModified=1)=>({name,size,lastModified});

for(const invalidate of [false,true])test('ZIP drag waits for capabilities and '+(invalidate?'ignores a cleared queue':'retains the dropped files'),async()=>{
  const {fields,window,queue}=form();let finish;
  window.IngestBatches={loadCapabilities:()=>new Promise(resolve=>{finish=resolve;})};
  const transfer={files:[file('rules.zip')]};
  const pending=fields['doc-dropzone'].events.drop({preventDefault(){},dataTransfer:transfer});
  assert.equal(queue.items().length,0);
  transfer.files=[];
  if(invalidate)queue.clear();
  queue.setCapabilities({max_file_bytes:25_000_000,max_batch_items:1000,max_batch_bytes:1_000_000_000,
    supported_extensions:['.md','.zip'],markdown_package:true});
  assert.equal(typeof finish,'function','drop must await server capabilities');finish();await pending;
  assert.equal(queue.items().length,invalidate?0:1);
  if(!invalidate)assert.equal(queue.items()[0].file.name,'rules.zip');
});

test('visible format guidance follows server ZIP capability with matching picker acceptance',()=>{
  const {fields,queue}=form();
  const capabilities={max_file_bytes:25_000_000,max_batch_items:1000,max_batch_bytes:1_000_000_000,
    supported_extensions:['.txt','.md','.pdf','.docx','.html','.htm','.zip'],markdown_package:true};
  queue.setCapabilities(capabilities);
  assert.match(fields['doc-upload-formats'].textContent,/ZIP/);
  assert.match(fields['doc-upload-extensions'].textContent,/\.zip/);
  assert.match(fields['doc-file'].accept,/\.zip/);
  assert.equal(fields['doc-upload-package-help'].hidden,false);
  assert.match(fields['doc-upload-package-help'].textContent,/包内.*预览/);
  queue.add([file('rules.zip')]);
  queue.clear();
  queue.setCapabilities({...capabilities,supported_extensions:['.txt','.md'],markdown_package:false});
  assert.doesNotMatch(fields['doc-upload-formats'].textContent,/ZIP/);
  assert.doesNotMatch(fields['doc-file'].accept,/\.zip/);
  assert.equal(fields['doc-upload-package-help'].hidden,true);
});

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

test('document dates default to per-file automatic detection',async()=>{
  const {fields:f,modes,queue,batch,options}=form();
  assert.equal(options().document_time_mode,'auto');
  modes[0].checked=true;modes[1].checked=false;f.project.value='p';
  queue.add([file('first.md'),file('second.md')]);
  const rows=await batch();
  assert.equal(rows.length,2);
  assert.ok(rows.every(row=>row.document_time_mode==='auto'));
  assert.ok(rows.every(row=>row.valid_from===undefined&&row.valid_until===undefined));
});

test('manual time mode remains an explicit upload choice',()=>{
  const {fields:f,options}=form();
  f['doc-time-mode'].value='manual';
  f['doc-published-at'].value='2026-09-01T08:30';
  const value=options();
  assert.equal(value.document_time_mode,'manual');
  assert.equal(value.published_at,new Date('2026-09-01T08:30').toISOString());
});

test('automatic publication-only summaries preserve unknown effective time',()=>{
  const {describe}=form();
  const publication='2026-09-01T00:00:00Z';
  const label=describe({document_time_mode:'auto',published_at:publication,valid_from:null,valid_until:null});
  assert.doesNotMatch(label,/生效：2026-09-01/);
  assert.match(label,/未知|待识别|待核验/);
  assert.doesNotMatch(label,/持续有效/);
});

test('automatic publication is independent of an earlier expiry while manual inference validates it',()=>{
  const {fields:f,options}=form();
  f['doc-published-at'].value='2026-10-01T08:30';
  f['doc-valid-until'].value='2026-09-01T08:30';
  assert.doesNotThrow(options);
  f['doc-time-mode'].value='manual';
  assert.throws(options,/失效时间必须晚于生效时间/);
});
