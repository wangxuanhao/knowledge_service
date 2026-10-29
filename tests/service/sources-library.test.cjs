const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
test('a versioned document defaults to rich fixed content with an explicit saved-text toggle and download',async()=>{
  const env=setup({rendered:async()=>({ok:true,text:async()=>'<h2 data-block-id="heading">排版正文</h2><p>图片前</p><table><tbody><tr><td>金额</td></tr></tbody></table><p>图片后</p>'})});
  await settle();await env.rows()[0].click();
  assert.equal(env.body.querySelector('h2')?.dataset.blockId,'heading');assert.ok(env.body.querySelector('table'));
  assert.ok(env.body.classList.contains('source-rich-content'));
  assert.match(env.fetches[0].url,/project-one\/source-documents\/version-one\/rendered$/);
  assert.equal(env.fetches.length,1,'library rendering needs no extra version metadata or chunk read');
  const controls=env.get('source-meta').querySelector('.source-reading-controls');
  const plain=controls.querySelectorAll('button').find(button=>button.textContent==='已保存正文');await plain.click();
  assert.equal(env.body.textContent,'原文 one');assert.equal(env.body.classList.contains('source-rich-content'),false);
  const rich=controls.querySelectorAll('button').find(button=>button.textContent==='图文阅读');await rich.click();assert.ok(env.body.querySelector('h2'));
  assert.match(env.get('source-meta').querySelector('.source-downloads').querySelector('a').href,/version-one\/source$/);
});

test('missing fixed rendering clearly falls back to saved text and legacy documents avoid the rich endpoint',async()=>{
  for(const response of [{ok:false,status:404},{ok:true,text:async()=>'   '}]){
    const env=setup({rendered:async()=>response});await settle();await env.rows()[0].click();
    assert.equal(env.body.textContent,'原文 one');assert.match(env.get('source-meta').textContent,/图文原文暂不可用.*已保存正文/);
  }
  const old=documentRow('legacy');delete old.metadata.source_document_version_id;
  const legacy=setup({documents:[old]});await settle();await legacy.rows()[0].click();assert.equal(legacy.body.textContent,'原文 legacy');assert.equal(legacy.fetches.length,0);
});

test('legacy saved text is explained even when original filename metadata is present',async()=>{
  const doc=documentRow('legacy');delete doc.metadata.source_document_version_id;
  const env=setup({documents:[doc]});await settle();await env.rows()[0].click();
  assert.equal(env.body.textContent,'原文 legacy');assert.match(env.get('source-meta').textContent,/旧资料仅保留已保存正文，未关联固定来源版本/);
  assert.match(env.get('source-meta').querySelector('.source-meta__head').textContent,/legacy.txt/);
  assert.equal(env.fetches.length,0);
});

test('closing, switching tabs and changing project abort rich reads and late responses never repaint',async()=>{
  for(const action of ['close','tab','project']){
    let finish;const env=setup({rendered:async()=>new Promise(resolve=>{finish=resolve;})});await settle();const open=env.rows()[0].click();await settle();
    assert.equal(env.fetches.length,1);const signal=env.fetches[0].signal;
    if(action==='close')env.drawers[0].close();
    if(action==='tab'){const load=env.tab('修订记录').click();env.requests.at(-1).resolve({versions:[]});await load;}
    if(action==='project'){env.context.current='other';await env.project.dispatch('change');await settle();}
    assert.equal(signal.aborted,true,action);const expected=env.body.textContent;
    finish({ok:true,text:async()=>'<p>过期资料</p>'});await open;assert.equal(env.body.textContent,expected,action);
  }
});
test('library loads a list without selecting a document and moves original details to one drawer',async()=>{
  const env=setup();await settle();
  assert.equal(env.rows().length,2);
  assert.equal(env.title.textContent,'','initial load must not open the first document');
  assert.equal(env.body.textContent,'');
  assert.equal(env.drawers.length,1);
  assert.equal(env.drawers[0].dialog.open,false);
  assert.equal(env.drawers[0].options.content,env.article,'preserve the existing article and handlers');
  assert.equal(env.drawers[0].options.title,env.title,'preserve source-title identity');
  assert.ok(!env.article.parentNode.parentNode?.classList.contains('source-layout'));
  assert.ok(env.rows().every(row=>row.getAttribute('aria-current')!=='true'));
});

test('missing file metadata is explained without presenting a record ID as a filename',async()=>{
  const id='66c306e60c224984866f0448ae5f6154',env=setup({documents:[{id,text:'已保存的正文',metadata:{title:'业务规则',status:'ready'}}]});
  await settle();await env.rows()[0].click();
  const head=env.document.querySelector('.source-meta__head');
  assert.doesNotMatch(head.textContent,/未知类型|未知大小|未知解析器/);
  assert.doesNotMatch(head.textContent,new RegExp(id));
  assert.match(head.textContent,/业务规则/);
  assert.match(env.get('source-meta').textContent,/未记录原始文件信息/);
});

test('processing records summarize execution and keep the exact raw payload lazy',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const pending=env.tab('处理记录').click();
  const run={id:'run-2',attempt:2,status:'failed',active_stage:'extraction',created_at:'2026-10-22T03:00:00Z',
    counts:{chunks_total:10,chunks_failed:2,records_accepted:8,assertions_pending:3},
    readiness:{document_ready:true,keyword_ready:true,semantic_ready:false,graph_ready:false},
    failure:{code:'timeout',message:'服务连接超时',retryable:true},debug:{large:'RAW_PROCESSING_SENTINEL'.repeat(1000)}};
  env.requests.at(-1).resolve({runs:[run]});await pending;
  assert.match(env.body.textContent,/处理记录|执行记录/);assert.match(env.body.textContent,/服务连接超时/);
  assert.match(env.body.textContent,/切片总数/);assert.match(env.body.textContent,/知识抽取/);
  assert.doesNotMatch(env.body.textContent,/RAW_PROCESSING_SENTINEL/);
  const raw=env.body.querySelector('.source-raw-data');assert.equal(raw.open,false);
  raw.open=true;await raw.dispatch('toggle');
  assert.deepEqual(JSON.parse(raw.querySelector('pre').textContent),run);
});

test('processing record candidate action keeps its exact ingest run and document context',async()=>{
  const env=setup(),opened=[];env.context.OntologyIntake={open:async args=>opened.push(args)};
  await settle();await env.rows()[0].click();const reading=env.tab('处理记录').click();
  env.requests.at(-1).resolve({runs:[{id:'specific-run',status:'completed',counts:{assertions_pending:3}}]});await reading;
  const action=env.body.querySelectorAll('button').find(button=>button.textContent==='核对本次处理的候选');
  assert.ok(action);await action.click();assert.equal(opened[0].runId,'specific-run');assert.equal(opened[0].documentId,'one');
});

test('later confirmed source facts do not leave a discovery run looking like the current graph is unavailable',async()=>{
  const env=setup({acceptedAssertions:[
    {document_id:'one',kind:'entity',status:'accepted',canonical_record_id:'entity-one'},
    {document_id:'one',kind:'entity',status:'accepted',canonical_record_id:'entity-one'},
    {document_id:'one',kind:'relation',status:'accepted',canonical_record_id:'relation-one'},
    {document_id:'one',kind:'attribute',status:'pending',canonical_record_id:'pending'},
    {document_id:'one',kind:'entity',status:'accepted',canonical_record_id:null},
    {document_id:'two',kind:'entity',status:'accepted',canonical_record_id:'other-document'},
  ]});
  await settle();await env.rows()[0].click();const reading=env.tab('处理记录').click();
  const run={id:'discovery-run',status:'completed',counts:{records_accepted:1,assertions_pending:0},
    readiness:{document_ready:true,candidate_ready:true,graph_ready:false}};
  env.requests.at(-1).resolve({runs:[run]});await reading;
  assert.match(env.body.textContent,/已确认入图事实：2 条/);
  assert.match(env.body.querySelector('.source-detail-intro').textContent,/当次|各次/);
  assert.match(env.body.querySelector('.source-processing-run').textContent,/当次入图：未直接入图/);
  assert.doesNotMatch(env.body.textContent,/正式图谱：尚未就绪/);
  assert.equal(run.readiness.graph_ready,false,'later confirmation must not rewrite the historical run');
  const raw=env.body.querySelector('.source-raw-data');raw.open=true;await raw.dispatch('toggle');
  assert.deepEqual(JSON.parse(raw.querySelector('pre').textContent),run);
});

test('an ontology publication alone cannot stand in for confirmed facts from this source',async()=>{
  const env=setup();await settle();await env.rows()[0].click();const reading=env.tab('处理记录').click();
  env.requests.at(-1).resolve({runs:[{id:'discovery-run',status:'completed',readiness:{graph_ready:false}}]});await reading;
  assert.match(env.body.querySelector('.source-graph-status').textContent,/尚无已确认入图事实/);
  assert.match(env.body.textContent,/本体版本发布.*事实入图/);
});

test('failed current fact reads leave historical processing results visible without reporting an empty graph',async()=>{
  const env=setup({acceptedReadError:Error('offline')});await settle();await env.rows()[0].click();
  const reading=env.tab('处理记录').click();env.requests.at(-1).resolve({runs:[{id:'history-run',status:'completed'}]});await reading;
  assert.equal(env.body.querySelector('.source-processing-run').dataset.runId,'history-run');
  assert.match(env.body.querySelector('.source-graph-status').textContent,/暂未核验/);
  assert.doesNotMatch(env.body.textContent,/尚无已确认入图事实/);
});

test('late fact confirmation cannot replace a newer tab and is reread on the next visit',async()=>{
  const env=setup(),original=env.context.api,confirmations=[];
  env.context.api=(url,...args)=>url.includes('status=accepted')?new Promise(resolve=>confirmations.push({url,resolve})):original(url,...args);
  await settle();await env.rows()[0].click();const first=env.tab('处理记录').click();
  env.requests.at(-1).resolve({runs:[{id:'run',status:'completed'}]});await settle();
  assert.match(confirmations[0].url,/document_id=one&status=accepted/);
  await env.tab('原文').click();confirmations[0].resolve({assertions:[]});await first;
  assert.equal(env.body.textContent,'原文 one');
  const second=env.tab('处理记录').click();env.requests.at(-1).resolve({runs:[]});
  confirmations[1].resolve({assertions:[{document_id:'one',kind:'entity',status:'accepted',canonical_record_id:'later-fact'}]});await second;
  assert.match(env.body.querySelector('.source-graph-status').textContent,/已确认入图事实：1 条/);
  assert.match(env.body.textContent,/暂无独立处理记录/);
});

test('record history distinguishes status updates from text changes without repeating full snapshots',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const fullText='FULL_TEXT_SENTINEL'.repeat(1000),pending=env.tab('修订记录').click();
  const versions=[
    {id:'one',version:1,version_id:'v1',recorded_at:'2026-10-22T03:00:00Z',text:fullText,metadata:{title:'业务规则',status:'processing'}},
    {id:'one',version:2,version_id:'v2',recorded_at:'2026-10-22T03:01:00Z',text:fullText,metadata:{title:'业务规则',status:'ready'}},
    {id:'one',version:3,version_id:'v3',recorded_at:'2026-10-22T03:02:00Z',text:fullText+'新规则',metadata:{title:'业务规则',status:'ready'}}];
  env.requests.at(-1).resolve({versions});await pending;
  const revisions=env.body.querySelectorAll('.source-revision');
  assert.equal(revisions.length,3);assert.equal(revisions[0].dataset.versionId,'v3');
  assert.match(revisions[0].textContent,/正文.*变化|正文.*更新/);
  assert.match(revisions[1].textContent,/处理状态/);assert.match(revisions[1].textContent,/正文未变化/);
  assert.match(revisions[2].textContent,/首次保存/);assert.doesNotMatch(env.body.textContent,/FULL_TEXT_SENTINEL/);
  const raw=revisions[0].querySelector('.source-raw-data');raw.open=true;await raw.dispatch('toggle');
  assert.deepEqual(JSON.parse(raw.querySelector('pre').textContent),versions[2]);
});

test('only explicit row selection opens details; closing, refreshing and filtering never reopen them',async()=>{
  const env=setup();await settle();let row=env.rows()[1];await row.click();
  const drawer=env.drawers[0];assert.equal(drawer.dialog.open,true);assert.equal(env.title.textContent,'资料 two');assert.equal(env.body.textContent,'原文 two');
  assert.deepEqual(env.document.querySelector('.source-detail-tabs').querySelectorAll('button').slice(0,4).map(button=>button.textContent),['原文','提取知识','处理记录','修订记录']);
  drawer.close();assert.equal(env.document.activeElement,row);
  await env.refresh.click();assert.equal(drawer.dialog.open,false);assert.equal(env.title.textContent,'');
  row=env.rows()[0];await row.click();await env.filter('processing').click();
  assert.equal(drawer.dialog.open,false);assert.equal(env.rows().length,0);assert.equal(env.body.textContent,'');
  assert.match(env.document.querySelector('.source-empty').textContent,/当前筛选无结果/);
  await env.filter('all').click();assert.equal(env.rows().length,2);assert.equal(drawer.dialog.open,false);
});

test('detail responses respect the latest document, tab and request even when reads finish out of order',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const assertions=env.tab('提取知识').click(),old=env.requests.at(-1),runs=env.tab('处理记录').click(),latest=env.requests.at(-1);
  latest.resolve({runs:[{id:'latest-run'}]});await runs;old.resolve({assertions:[{kind:'old-assertion',payload:{text:'obsolete'}}]});await assertions;
  assert.equal(env.body.querySelector('.source-processing-run').dataset.runId,'latest-run');assert.doesNotMatch(env.body.textContent,/obsolete/);
  const history=env.tab('修订记录').click(),pending=env.requests.at(-1);await env.tab('原文').click();pending.resolve({versions:[{id:'obsolete-history'}]});await history;
  assert.equal(env.body.textContent,'原文 one');
  const first=env.tab('处理记录').click(),firstRequest=env.requests.at(-1),second=env.tab('处理记录').click(),secondRequest=env.requests.at(-1);
  secondRequest.resolve({runs:[{id:'newest'}]});await second;firstRequest.resolve({runs:[{id:'older'}]});await first;assert.equal(env.body.querySelector('.source-processing-run').dataset.runId,'newest');
  const oldDocument=env.tab('修订记录').click(),oldDocumentRequest=env.requests.at(-1);await env.rows()[1].click();oldDocumentRequest.resolve({versions:[{id:'wrong-document'}]});await oldDocument;
  assert.equal(env.body.textContent,'原文 two');
});

test('closing details invalidates successes and failures, including after reopening the same document',async()=>{
  for(const reject of [false,true]){
    const env=setup();await settle();await env.rows()[0].click();const pending=env.tab('处理记录').click(),request=env.requests.at(-1);
    env.drawers[0].close();await env.rows()[0].click();const expected=env.body.textContent;
    if(reject)request.reject(Error('obsolete-error'));else request.resolve({runs:[{id:'obsolete-success'}]});await pending;
    assert.equal(env.body.textContent,expected);
  }
});

test('switching to original text suppresses a stale tab failure while current failures remain readable',async()=>{
  const env=setup();await settle();await env.rows()[0].click();
  const old=env.tab('处理记录').click(),oldRequest=env.requests.at(-1);await env.tab('原文').click();oldRequest.reject(Error('obsolete-failure'));await old;
  assert.equal(env.body.textContent,'原文 one');
  const current=env.tab('处理记录').click();env.requests.at(-1).reject(Error('current-failure'));await current;
  assert.equal(env.body.textContent,'读取失败：current-failure');
});

test('project and scope changes close the drawer and discard pending detail responses',async()=>{
  for(const change of ['project','scope']){
    const env=setup();await settle();await env.rows()[0].click();const pending=env.tab('提取知识').click(),request=env.requests.at(-1);
    if(change==='scope')await env.changeScope();else {env.context.current='project-two';await env.project.dispatch('change');await settle();}
    assert.equal(env.drawers[0].dialog.open,false,change);const expected=env.body.textContent;request.reject(Error('stale-'+change));await pending;
    assert.equal(env.body.textContent,expected,change);assert.doesNotMatch(env.body.textContent,/stale-/);
  }
});

test('empty reload resets every status count and clears previous detail content',async()=>{
  const env=setup();await settle();await env.rows()[0].click();env.setDocuments([]);await env.refresh.click();
  for(const name of ['ready','processing','failed','all'])assert.equal(String(env.get(name+'-count').textContent),'0',name);
  assert.equal(env.drawers[0].dialog.open,false);assert.equal(env.body.textContent,'');assert.equal(env.rows().length,0);
});

test('viewer can read all tabs without upload or modeling write entry points',async()=>{
  const env=setup({writer:false});await settle();assert.equal(env.upload.classList.contains('admin-only'),true);await env.upload.click();assert.equal(env.navigation.length,0);
  await env.rows()[0].click();assert.equal(env.tab('完善本体结构'),undefined);
  const pending=env.tab('修订记录').click();env.requests.at(-1).resolve({versions:[{id:'revision'}]});await pending;assert.equal(env.body.querySelector('.source-revision').dataset.versionId,'revision');
});

test('structure intake receives the exact source and closes its old drawer first',async()=>{
  const env=setup(),opened=[];env.context.OntologyIntake={open:async args=>opened.push({args,drawerOpen:env.drawers[0].dialog.open})};
  await settle();await env.rows()[0].click();
  const action=env.tab('完善本体结构');assert.ok(action);
  await action.click();assert.equal(opened.length,1);assert.equal(opened[0].drawerOpen,false);
  assert.equal(opened[0].args.projectId,'project-one');assert.equal(opened[0].args.documentId,'one');
  assert.equal(env.navigation.at(-1),'ontology-model');
  await env.rows()[0].click();const old=env.tab('完善本体结构');
  env.context.current='project-two';await env.project.dispatch('change');await settle();
  await old.click();assert.equal(opened.length,1,'an old source button cannot open intake in another project');
});

test('an admin role arriving after script startup can use the CSS-controlled upload entry',async()=>{
  const env=setup({writer:false});await settle();
  assert.equal(env.upload.classList.contains('admin-only'),true);
  env.setWriter(true);env.document.body.classList.add('is-admin');
  assert.equal(env.upload.hidden,false,'startup auth must not leave an inline hidden attribute after the admin role arrives');
  await env.upload.click();assert.equal(env.navigation.at(-1),'upload');
});

test('reader and formal record links close the source drawer before opening their existing overlays',async()=>{
  const env=setup();await settle();await env.rows()[0].click();assert.equal(env.drawers[0]?.dialog.open,true);const reader=env.get('source-meta').querySelectorAll('button').find(button=>button.textContent==='在原文中打开');await reader.click();
  assert.equal(env.external[0].drawerOpen,false);assert.equal(env.external[0].args.projectId,'project-one');assert.equal(env.external[0].args.versionId,'version-one');
  await env.rows()[0].click();const pending=env.tab('提取知识').click();env.requests.at(-1).resolve({assertions:[{kind:'entity',status:'accepted',payload:{name:'员工'},canonical_record_id:'record-one'}]});await pending;
  const open=env.body.querySelectorAll('button').find(button=>button.textContent==='查看正式知识');await open.click();
  assert.equal(env.external[1].drawerOpen,false);assert.equal(env.external[1].args.id,'record-one');assert.equal(env.external[1].args.projectId,'project-one');assert.equal(env.navigation.at(-1),'records');
});

test('an explicit source deep link opens its row even when the current status filter hides it',async()=>{
  const env=setup();await settle();await env.filter('failed').click();await env.context.selectSource('one');
  assert.equal(env.drawers[0].dialog.open,true);assert.equal(env.title.textContent,'资料 one');assert.equal(env.filter('all').classList.contains('active'),true);
  await assert.rejects(env.context.selectSource('missing'),/当前范围没有这条原文/);assert.equal(env.drawers[0].dialog.open,false);
});

test('a pending citation cannot open another project after a newer list has loaded',async()=>{
  const env=setup();await settle();const reads=[];
  env.context.api=url=>new Promise(resolve=>reads.push({url,resolve}));
  const oldRefresh=env.refresh.click();
  const citation=assert.rejects(env.context.selectSource('one'),/资料范围已更新/);
  env.context.current='project-two';await env.project.dispatch('change');
  reads[1].resolve({documents:[documentRow('new-project')]});await settle();reads[0].resolve({documents:[documentRow('one')]});await Promise.all([citation,oldRefresh]);
  assert.equal(env.drawers[0].dialog.open,false);assert.equal(env.rows().length,1);assert.match(env.rows()[0].textContent,/new-project/);
});

test('a failed refresh leaves a retry state with no previous detail or status counts',async()=>{
  const env=setup();await settle();await env.rows()[0].click();env.context.api=async()=>{throw Error('offline');};await env.refresh.click();
  assert.equal(env.drawers[0].dialog.open,false);assert.equal(env.body.textContent,'');assert.equal(env.rows().length,0);assert.equal(env.refresh.disabled,false);
  assert.match(env.document.querySelector('.source-empty').textContent,/暂时无法读取原文.*offline/);
  for(const name of ['ready','processing','failed','all'])assert.equal(String(env.get(name+'-count').textContent),'0');
});
