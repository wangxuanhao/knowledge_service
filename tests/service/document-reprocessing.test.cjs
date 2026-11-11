const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');

test('saved source offers three explicit reprocessing modes and explains the OCR rebuild boundary',async()=>{
  const env=setup(),destinations=[];env.context.KnowledgeNavigation={open:(page,options)=>destinations.push({page,options})};await settle();await env.rows()[0].click();
  const panel=env.get('source-meta').querySelector('.source-reprocess');assert.ok(panel);
  assert.match(panel.textContent,/重新处理.*构建图谱/);
  const modes=panel.querySelector('select').querySelectorAll('option');
  assert.deepEqual(modes.map(option=>option.value),['documents','discovery','ontology']);
  assert.match(panel.textContent,/仅文档检索/);assert.match(panel.textContent,/开放发现/);assert.match(panel.textContent,/使用项目本体/);
  assert.match(panel.textContent,/OCR.*重新处理.*切片.*索引/);
  assert.match(panel.textContent,/开放发现.*不会直接.*图谱/);
  assert.match(panel.textContent,/模式切换.*不会直接清空图谱/);
  const start=panel.querySelector('button');const pending=start.click();await settle();
  const request=env.requests.at(-1);assert.match(request.url,/\/projects\/project-one\/documents\/one\/reprocess$/);
  assert.equal(request.body.extraction_mode,'documents');assert.ok(request.body.idempotency_key);
  request.resolve({batch:{id:'batch-new'},item:{id:'item-new',document_generation:1}});await pending;
  assert.match(panel.textContent,/已创建.*处理任务/);assert.match(panel.textContent,/索引.*同步/);
  const view=panel.querySelectorAll('button').find(button=>button.textContent==='查看处理任务');await view.click();
  assert.equal(destinations[0]?.page,'jobs');assert.equal(destinations[0]?.options.projectId,'project-one');
});

test('legacy sources explain reupload and viewers do not get a reprocessing write control',async()=>{
  const legacy=documentRow('legacy');delete legacy.metadata.source_document_version_id;
  const env=setup({documents:[legacy]});await settle();await env.rows()[0].click();
  const panel=env.get('source-meta').querySelector('.source-reprocess');assert.ok(panel);
  assert.match(panel.textContent,/重新上传/);assert.equal(panel.querySelector('button'),null);
  const viewer=setup({writer:false});await settle();await viewer.rows()[0].click();
  assert.equal(viewer.get('source-meta').querySelector('.source-reprocess'),null);
});

test('reprocessing duplicate clicks share a request and late results cannot repaint another document',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const panel=env.get('source-meta').querySelector('.source-reprocess');assert.ok(panel);
  const start=panel.querySelector('button'),pending=start.click();await settle();await start.click();
  assert.equal(env.requests.length,1);
  await env.rows()[1].click();const current=env.get('source-meta').textContent;
  env.requests[0].resolve({batch:{id:'old-batch'},item:{id:'old-item',document_generation:1}});await pending;
  assert.equal(env.get('source-meta').textContent,current);
});
