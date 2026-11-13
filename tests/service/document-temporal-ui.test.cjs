const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('upload option forwarding retains the selected time mode',()=>{
  const source=fs.readFileSync('knowledge_service/web/workbench.js','utf8');
  const start=source.indexOf('function documentUploadOptions(document)');
  const end=source.indexOf('const chunkPreviewState=',start);
  const context={};vm.runInNewContext(source.slice(start,end),context);
  assert.equal(context.documentUploadOptions({metadata:{},document_time_mode:'auto'}).document_time_mode,'auto');
  assert.equal(context.documentUploadOptions({metadata:{},document_time_mode:'manual'}).document_time_mode,'manual');
});

test('document time summary keeps publication separate from unknown validity',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const html=render({published_at:'2026-09-24T16:00:00Z',valid_from:null,valid_until:null,
    document_temporal:{fields:{published_at:{status:'extracted'},valid_from:{status:'unknown'},valid_until:{status:'unknown'}},warnings:[]}});
  assert.match(html,/原文发布时间/);
  assert.match(html,/业务生效/);
  assert.match(html,/未知/);
  assert.match(html,/未设结束时间/);
  assert.doesNotMatch(html,/持续有效/);
});

test('saved time evidence and review state render as inert text',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const html=render({valid_from:null,valid_until:null,metadata:{document_temporal:{fields:{
    valid_from:{status:'review',candidates:[{raw:'<img src=x onerror=alert(1)>',start_char:1,end_char:2}]},
  },warnings:['reversed_interval']}}});
  assert.match(html,/待核验/);
  assert.match(html,/识别依据/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img/);
});

test('legacy inferred validity and saved revision are labelled with their actual provenance',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const html=render({valid_from:'2026-09-24T16:00:00Z',valid_until:null,metadata:{
    published_at:'2026-09-24T16:00:00Z',revised_at:'2026-10-01T16:00:00Z',effective_time_source:'published_at'}});
  assert.match(html,/发布时间推定/);
  assert.match(html,/原文修订时间/);
  assert.match(html,/2026-09-25/);
  assert.match(html,/Asia\/Shanghai/);
  assert.doesNotMatch(html,/持续有效/);
});

test('arbitrary legacy metadata does not set business dates or break the saved-source summary',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const html=render({metadata:{valid_from:'2040-01-01T00:00:00Z',document_temporal:{fields:{
    published_at:{candidates:[null,'legacy text',{raw:'发布日期',kind:'header'}]},
  }}}});
  assert.doesNotMatch(html,/2040-01-01/);
  assert.match(html,/文首日期字段/);
  assert.match(html,/业务生效/);
});

test('structured legacy date and evidence values are escaped without coercing objects',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const structured={toString:null,label:'<script>saved JSON</script>'};
  const html=render({effective_time_source:structured,metadata:{published_at:structured,document_temporal:{fields:{
    published_at:{status:structured,candidates:[{raw:structured,value:structured,kind:structured,timezone:structured}]},
    valid_from:{status:structured,value:structured},
  },source_status:{raw:structured},warnings:[structured]}}});
  assert.match(html,/&quot;toString&quot;:null/);
  assert.match(html,/&lt;script&gt;saved JSON&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script|1970-01-01/);
});

test('evidence exposes parsed text offsets and translated warnings without source links',()=>{
  const {render}=require('../../knowledge_service/web/document-temporal-ui.js');
  const html=render({document_temporal:{fields:{valid_until:{status:'review',candidates:[{
    raw:'2026年10月1日起废止',start_char:10,end_char:24,kind:'expiry',timezone:'Asia/Shanghai',value:'2026-09-30T16:00:00Z'}]}},
    source_status:{raw:'已失效<script>',start_char:0,end_char:3},
    warnings:['expired_status_without_date','title_status_conflict','invalid_supplied_revised_at','<other>']}});
  assert.match(html,/解析正文位置 10–24/);
  assert.match(html,/失效状态.*日期|状态.*失效.*日期/);
  assert.match(html,/标题.*状态|状态.*标题/);
  assert.match(html,/修订时间/);
  assert.match(html,/已失效&lt;script&gt;/);
  assert.match(html,/&lt;other&gt;/);
  assert.doesNotMatch(html,/<details[^>]*\bopen\b|<script|href=/);
});

test('time helper is versioned and loaded before preview and saved-source consumers',()=>{
  const html=fs.readFileSync('knowledge_service/web/index.html','utf8');
  assert.match(html,/id="doc-time-mode"/);
  assert.match(html,/<option value="auto" selected>自动识别每份文档时间/);
  assert.match(html,/手工填写（兼容发布时间推定）/);
  assert.match(html,/id="doc-upload-package-help"/);
  assert.match(fs.readFileSync('knowledge_service/web/ingest-mode.js','utf8'),/ZIP[^\n]*相对目录/);
  const helper=html.indexOf('/assets/document-temporal-ui.js?v=document-time-20261110-1');
  assert.ok(helper>=0);
  for(const file of ['ingest-mode','workbench','sources-view','ingest-batches','library-workspace','ingest-results']){
    const version=['ingest-mode','workbench'].includes(file)?'unified-media-20261111-1':file==='library-workspace'?'upload-steps-20261111-1':file==='sources-view'?'search-content-20261113-1':'document-time-20261110-1';
    const marker='/assets/'+file+'.js?v='+version;
    assert.ok(html.includes(marker),marker);
    if(['workbench','sources-view','ingest-results'].includes(file))assert.ok(helper<html.indexOf(marker));
  }
});

test('expanded preview date evidence remains inside a bounded scrolling overview',()=>{
  const css=fs.readFileSync('knowledge_service/web/workbench.css','utf8');
  assert.match(css,/\.chunk-preview-overview\{[^}]*max-height:45dvh[^}]*overflow:auto/);
  assert.match(css,/\.chunk-preview-summary>div\{[^}]*min-width:0[^}]*flex-basis:100%[^}]*overflow-wrap:anywhere/);
  const html=fs.readFileSync('knowledge_service/web/index.html','utf8');
  assert.ok(html.includes('/assets/workbench.css?v=split-previews-20261110-1'));
});

test('reopened source details render persisted dates and evidence alongside the frozen content',async()=>{
  const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
  const doc=documentRow('dated');doc.valid_from=null;doc.valid_until=null;
  doc.metadata.published_at='2026-09-24T16:00:00Z';
  doc.metadata.document_temporal={fields:{published_at:{status:'extracted',candidates:[{
    raw:'发布时间：2026年9月25日',start_char:0,end_char:17}]}},warnings:[]};
  const env=setup({documents:[doc]});env.context.DocumentTemporalUI=require('../../knowledge_service/web/document-temporal-ui.js');
  await settle();await env.rows()[0].click();
  const publication=env.get('source-meta').querySelectorAll('p').find(node=>node.querySelector('strong')?.textContent==='原文发布时间');
  assert.match(publication.textContent,/2026-09-25/);
  assert.match(env.get('source-meta').textContent,/解析正文位置 0–17/);
  assert.ok(env.body.querySelector('p'),'frozen rich content remains mounted');
  env.drawers[0].close();await env.rows()[0].click();
  assert.match(env.get('source-meta').textContent,/识别依据/);
});

test('structured legacy publication metadata still allows the saved source to open',async()=>{
  const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
  const doc=documentRow('legacy-date');doc.metadata.published_at={toString:null,label:'legacy'};
  const env=setup({documents:[doc]});env.context.DocumentTemporalUI=require('../../knowledge_service/web/document-temporal-ui.js');
  await settle();await env.rows()[0].click();
  assert.match(env.get('source-meta').textContent,/原文发布时间/);
  assert.ok(env.body.querySelector('p'));
});
