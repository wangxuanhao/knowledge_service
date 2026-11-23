const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');

async function openReprocessing(env){
  const pending=env.tab('处理记录').click();
  env.requests.at(-1).resolve({runs:[]});await pending;
  const panel=env.get('source-meta').querySelector('.source-reprocess');
  assert.equal(panel.hidden,true);
  await env.get('source-meta').querySelector('.source-run-actions').querySelectorAll('button').find(button=>button.textContent==='重新处理').click();
  assert.equal(panel.hidden,false);
  env.requests.length=0;
  return panel;
}

test('saved source offers three explicit reprocessing modes and explains the OCR rebuild boundary',async()=>{
  const env=setup(),destinations=[];env.context.LibraryWorkspace.openBatch=options=>destinations.push(options);await settle();await env.rows()[0].click();
  assert.equal(env.get('source-meta').querySelector('.source-run-tools').hidden,true);
  const panel=await openReprocessing(env);
  const modes=panel.querySelector('select').querySelectorAll('option');
  assert.deepEqual(modes.map(option=>option.value),['documents','discovery','ontology']);
  assert.match(panel.textContent,/仅文档检索/);assert.match(panel.textContent,/开放发现/);assert.match(panel.textContent,/使用项目本体/);
  assert.match(panel.textContent,/OCR.*重新处理.*切片.*索引/);
  const control=panel.querySelector('select');control.value='discovery';await control.dispatch('change');
  assert.match(panel.querySelector('#source-reprocess-mode-note').textContent,/待核对/);
  control.value='documents';await control.dispatch('change');
  const start=panel.querySelector('button');const pending=start.click();await settle();
  const request=env.requests.at(-1);assert.match(request.url,/\/projects\/project-one\/documents\/one\/reprocess$/);
  assert.equal(request.body.extraction_mode,'documents');assert.ok(request.body.idempotency_key);
  request.resolve({batch:{id:'batch-new'},item:{id:'item-new',document_generation:1}});await pending;
  assert.match(panel.textContent,/已创建.*处理任务/);
  const view=panel.querySelectorAll('button').find(button=>button.textContent==='查看处理任务');await view.click();
  assert.deepEqual(destinations.map(({projectId,batchId,itemId})=>[projectId,batchId,itemId]),[['project-one','batch-new','item-new']]);
});

test('legacy sources explain reupload and viewers do not get a reprocessing write control',async()=>{
  const legacy=documentRow('legacy');delete legacy.metadata.source_document_version_id;
  const env=setup({documents:[legacy]});await settle();await env.rows()[0].click();
  const panel=await openReprocessing(env);
  assert.match(panel.textContent,/重新上传/);assert.equal(panel.querySelector('button'),null);
  const viewer=setup({writer:false});await settle();await viewer.rows()[0].click();
  assert.equal(viewer.get('source-meta').querySelector('.source-reprocess'),null);
});

test('reprocessing duplicate clicks share a request and late results cannot repaint another document',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const panel=await openReprocessing(env);
  const start=panel.querySelector('button'),pending=start.click();await settle();await start.click();
  assert.equal(env.requests.length,1);
  await env.rows()[1].click();const current=env.get('source-meta').textContent;
  env.requests[0].resolve({batch:{id:'old-batch'},item:{id:'old-item',document_generation:1}});await pending;
  assert.equal(env.get('source-meta').textContent,current);
});
