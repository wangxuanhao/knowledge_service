const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
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
  assert.match(env.body.textContent,/latest-run/);assert.doesNotMatch(env.body.textContent,/obsolete/);
  const history=env.tab('修订记录').click(),pending=env.requests.at(-1);await env.tab('原文').click();pending.resolve({versions:[{id:'obsolete-history'}]});await history;
  assert.equal(env.body.textContent,'原文 one');
  const first=env.tab('处理记录').click(),firstRequest=env.requests.at(-1),second=env.tab('处理记录').click(),secondRequest=env.requests.at(-1);
  secondRequest.resolve({runs:[{id:'newest'}]});await second;firstRequest.resolve({runs:[{id:'older'}]});await first;assert.match(env.body.textContent,/newest/);
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
  const pending=env.tab('修订记录').click();env.requests.at(-1).resolve({versions:[{id:'revision'}]});await pending;assert.match(env.body.textContent,/revision/);
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
