const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function setup(){
  const ids=['doc-file','doc-text','doc-title','doc-meta','doc-published-at','doc-valid-from','doc-valid-until',
    'doc-platforms','doc-businesses','doc-audiences','doc-platforms-mode','doc-businesses-mode','doc-audiences-mode',
    'doc-effective-help','query-platforms','query-businesses','query-audiences','project','ingest',
    'doc-file-list','doc-batch-help','doc-upload-summary','doc-clear-files','doc-retry-files','doc-dropzone'];
  const fields=Object.fromEntries(ids.map(id=>[id,{id,value:'',files:[],hidden:false,disabled:false,required:false,
    textContent:'',listeners:{},focus(){},addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);},
    dispatch(type){for(const fn of this.listeners[type]||[])fn({target:this});},
    classList:{toggle(){},add(){},remove(){}},closest(){return {classList:{toggle(){}}};},replaceChildren(){},append(){}}]));
  for(const id of ['doc-platforms-mode','doc-businesses-mode','doc-audiences-mode'])fields[id].value='unknown';
  const modes=[{value:'file',checked:false,addEventListener(){}},{value:'text',checked:true,addEventListener(){}}];
  const window={};
  const document={getElementById:id=>fields[id]||null,querySelectorAll:()=>modes,
    querySelector:()=>null,createElement:()=>({className:'',textContent:'',dataset:{},append(){},addEventListener(){}})};
  vm.runInNewContext(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),
    {window,document,JSON,Date,Error,Number,String,Array,Object,Set});
  return {fields,window,read:window.readDocumentUploadOptions,batch:window.readDocumentBatch};
}

test('upload applicability and dates are top-level options, not arbitrary metadata',()=>{
  const {fields:f,read}=setup();
  f['doc-meta'].value='{"region":"华东"}';
  f['doc-platforms-mode'].value='specific';
  f['doc-businesses-mode'].value='specific';
  f['doc-audiences-mode'].value='specific';
  f['doc-platforms'].value=' 美团, 淘宝，美团 ';
  f['doc-businesses'].value='外卖, 到店';
  f['doc-audiences'].value='商户';
  f['doc-published-at'].value='2026-09-01T08:30';
  f['doc-valid-until'].value='2026-10-01T08:30';
  const value=read();
  assert.deepEqual(JSON.parse(JSON.stringify(value.applicability)),{
    platforms:{mode:'specific',values:['美团','淘宝']},
    businesses:{mode:'specific',values:['外卖','到店']},
    audiences:{mode:'specific',values:['商户']},
  });
  assert.equal(value.published_at,new Date('2026-09-01T08:30').toISOString());
  assert.equal(value.valid_from,undefined);
  assert.equal(value.valid_until,new Date('2026-10-01T08:30').toISOString());
  assert.deepEqual(JSON.parse(JSON.stringify(value.metadata)),{
    region:'华东',applicability:JSON.parse(JSON.stringify(value.applicability))});
  assert.match(f['doc-effective-help'].textContent,/沿用发布时间/);
  assert.match(f['doc-effective-help'].textContent,/失效/);
});

test('explicit effective time wins and expiry must be later than effective time',()=>{
  const {fields:f,read}=setup();
  f['doc-published-at'].value='2026-09-01T08:30';
  f['doc-valid-from'].value='2026-09-02T08:30';
  f['doc-valid-until'].value='2026-09-02T08:30';
  assert.throws(read,/失效时间必须晚于生效时间/);
  f['doc-valid-until'].value='';
  const value=read();
  assert.equal(value.valid_from,new Date('2026-09-02T08:30').toISOString());
  assert.match(f['doc-effective-help'].textContent,/显式生效时间/);
  assert.match(f['doc-effective-help'].textContent,/持续有效/);
});

test('text and batch documents share cloned top-level applicability options',async()=>{
  const {fields:f,batch}=setup();
  f.project.value='p';f['doc-title'].value='规则';f['doc-text'].value='正文';
  f['doc-platforms-mode'].value='specific';
  f['doc-platforms'].value='京东';f['doc-published-at'].value='2026-09-01T08:30';
  const [document]=await batch();
  assert.deepEqual(JSON.parse(JSON.stringify(document.applicability)),{
    platforms:{mode:'specific',values:['京东']},
    businesses:{mode:'unknown',values:[]},audiences:{mode:'unknown',values:[]}});
  assert.equal(document.published_at,new Date('2026-09-01T08:30').toISOString());
  assert.deepEqual(JSON.parse(JSON.stringify(document.metadata.applicability)),
    JSON.parse(JSON.stringify(document.applicability)));
});

test('blank dimensions stay unknown and all mode never leaks typed values',()=>{
  const {fields:f,read}=setup();
  f['doc-platforms-mode'].value='all';
  f['doc-platforms'].value='美团';
  const value=read();
  assert.deepEqual(JSON.parse(JSON.stringify(value.applicability)),{
    platforms:{mode:'all',values:[]},businesses:{mode:'unknown',values:[]},audiences:{mode:'unknown',values:[]},
  });
  assert.deepEqual(JSON.parse(JSON.stringify(value.metadata.applicability)),
    JSON.parse(JSON.stringify(value.applicability)));
});

test('query platform scope is compact, optional, and preserves the existing scope payload',()=>{
  const {fields:f,window}=setup();
  assert.deepEqual(JSON.parse(JSON.stringify(window.ApplicabilityUI.readQueryScope())),{});
  f['query-platforms'].value='美团, 淘宝，美团';
  assert.deepEqual(JSON.parse(JSON.stringify(window.ApplicabilityUI.readQueryScope())),{
    applicability:{platforms:{mode:'specific',values:['美团','淘宝']},
      businesses:{mode:'unknown',values:[]},audiences:{mode:'unknown',values:[]}},
  });
  const source=fs.readFileSync('knowledge_service/web/workbench.js','utf8');
  assert.match(source,/readQueryScope\?\.\(\)/);
  assert.match(source,/\.\.\.scope\(\),valid_at:asOf\?new Date\(asOf\)\.toISOString\(\):\(scope\(\)\.valid_at\|\|null\)/);
  const workspace=fs.readFileSync('knowledge_service/web/workspace.js','utf8');
  assert.match(workspace,/\.\.\.scope\(\),query/);
  assert.match(workspace,/ApplicabilityUI\?\.describe\?\.\(r\)/);
  assert.match(workspace,/context_chunks/);
  assert.match(workspace,/platform_groups/);
  assert.match(source,/按平台检索结果/);
  assert.match(source,/未检索到本次时点确认有效的依据/);
  assert.match(source,/!rows\.length&&!pendingCount&&!platformGroups\.length/);
});

test('unified metadata applicability sends platform, business and audience together',()=>{
  const {fields:f,window}=setup();
  f['query-platforms'].value='美团';
  f['query-businesses'].value='外卖';
  f['query-audiences'].value='商户';
  assert.deepEqual(JSON.parse(JSON.stringify(window.ApplicabilityUI.readQueryScope())),{
    applicability:{platforms:{mode:'specific',values:['美团']},
      businesses:{mode:'specific',values:['外卖']},audiences:{mode:'specific',values:['商户']}},
  });
  f['query-platforms'].value='';
  assert.deepEqual(JSON.parse(JSON.stringify(window.ApplicabilityUI.readQueryScope())),{
    applicability:{platforms:{mode:'unknown',values:[]},
      businesses:{mode:'specific',values:['外卖']},audiences:{mode:'specific',values:['商户']}},
  });
});

test('HTML exposes three-state upload fields, project vocabularies, time semantics, and grouped labels',()=>{
  const html=fs.readFileSync('knowledge_service/web/index.html','utf8');
  for(const id of ['doc-platforms','doc-businesses','doc-audiences','doc-platforms-mode','doc-businesses-mode','doc-audiences-mode',
    'doc-platform-options','doc-business-options','doc-audience-options','doc-published-at','doc-valid-from','doc-valid-until','doc-effective-help','query-platforms']){
    assert.match(html,new RegExp(`id=["']${id}["']`));
  }
  assert.match(html,/指定范围/);assert.match(html,/全部适用/);assert.match(html,/暂不确定/);
  assert.match(html,/左闭右开|\[生效[^<]*失效\)/);
  assert.match(html,/本批文件共用[^<]*(?:范围|时间)|范围与时间由本批文件共用/);
  const ingest=fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8');
  assert.match(ingest,/applicability-values/);
  assert.match(ingest,/metadata\.applicability/);
  const workbench=fs.readFileSync('knowledge_service/web/workbench.js','utf8');
  const workspace=fs.readFileSync('knowledge_service/web/workspace.js','utf8');
  for(const source of [workbench,workspace]){
    assert.match(source,/通用规则/);assert.match(source,/范围待确认/);
  }
  assert.match(workbench,/不参与确定性答案/);
});

test('evidence formatter surfaces applicability and validity returned by the service',()=>{
  const {window}=setup();
  const text=window.ApplicabilityUI.describe({
    applicability:{platforms:{mode:'specific',values:['美团']},businesses:{mode:'specific',values:['外卖']},audiences:{mode:'specific',values:['商户']}},
    valid_from:'2026-09-01T00:00:00Z',valid_until:null,
  });
  assert.match(text,/平台：美团/);
  assert.match(text,/业务：外卖/);
  assert.match(text,/对象：商户/);
  assert.match(text,/持续有效/);
  assert.match(fs.readFileSync('knowledge_service/web/workbench.js','utf8'),/ApplicabilityUI\?\.describe\?\.\(row\)/);
});

test('unknown effective time is not described as ongoing validity',()=>{
  const {fields:f,window}=setup();
  const text=window.ApplicabilityUI.describe({valid_from:null,valid_until:null});
  assert.doesNotMatch(text,/持续有效/);
  assert.match(f['doc-effective-help'].textContent,/尚未提供发布时间或生效时间/);
  assert.match(f['doc-effective-help'].textContent,/未设失效时间/);
  assert.doesNotMatch(f['doc-effective-help'].textContent,/持续有效/);
});
