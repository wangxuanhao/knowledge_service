const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
const params=request=>new URL(request.url,'http://test').searchParams;
async function search(ui,value){ui.get('source-search-input').value=value;ui.get('source-search-form').dispatch('submit');await settle();}

test('search submits trimmed draft, scopes counts and preserves state/scope/refresh',async()=>{
  const ui=setup();await settle();
  const input=ui.get('source-search-input');assert.ok(input,'labeled search input exists');
  assert.equal(ui.document.querySelector('label[for="source-search-input"]').textContent,'搜索资料');
  input.value='two';await input.dispatch('input');assert.equal(ui.rows().length,2,'typing does not apply search');
  await search(ui,'  two  ');assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'two');assert.equal(ui.rows().length,1);assert.equal(ui.get('all-count').textContent,'1');
  await ui.filter('ready').click();await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'two');assert.equal(ui.rows().length,0);
  await ui.changeScope();await ui.refresh.click();await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'two');assert.equal(params(ui.sourceRequests.at(-1)).get('state'),'ready');
  assert.match(ui.document.querySelector('.source-empty').textContent,/搜索/);
  await ui.get('source-empty-clear-search').click();await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);assert.equal(params(ui.sourceRequests.at(-1)).get('state'),'ready');
});

test('composition Enter and submit do not apply a draft; normal Enter submits',async()=>{
  const ui=setup();await settle();const input=ui.get('source-search-input');assert.ok(input);
  const count=ui.sourceRequests.length;input.value='two';await input.dispatch('compositionstart');await input.dispatch('keydown',{key:'Enter',isComposing:true});await ui.get('source-search-form').dispatch('submit');await settle();assert.equal(ui.sourceRequests.length,count);
  await input.dispatch('compositionend');await input.dispatch('keydown',{key:'Enter'});await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'two');
  await ui.get('source-search-clear').click();await settle();assert.equal(input.value,'');assert.equal(ui.rows().length,2);
});

test('query identity protects pagination and cached opens against stale responses',async()=>{
  const pending=[],ui=setup({sourceRead:(url,body)=>new Promise(resolve=>pending.push({url,body,resolve}))});
  pending[0].resolve({documents:[documentRow('old')],next_cursor:'old-cursor',total:2});await settle();
  await search(ui,'first');assert.equal(params(pending.at(-1)).get('cursor'),null);
  const first=pending.at(-1);await search(ui,'second');const second=pending.at(-1);
  first.resolve({documents:[documentRow('stale')]});await settle();assert.equal(ui.rows().length,0);
  second.resolve({documents:[documentRow('second')],total:2,next_cursor:'second-cursor',counts:{all:2,ready:2,processing:0,failed:0}});await settle();
  const before=pending.length;await ui.context.SourcesView.open();assert.equal(pending.length,before);
  ui.get('source-search-input').value='unsubmitted';const more=ui.get('source-load-more').click();assert.equal(params(pending.at(-1)).get('query'),'second');assert.equal(params(pending.at(-1)).get('cursor'),'second-cursor');
  const append=pending.at(-1);await search(ui,'third');append.resolve({documents:[documentRow('wrong-page')]});await more;await settle();assert.equal(ui.rows().length,0);
  pending.at(-1).resolve({documents:[documentRow('third')]});await settle();assert.match(ui.rows()[0].textContent,/third/);
});

test('project switch and citation selection clear applied and draft search',async()=>{
  const ui=setup();await settle();await search(ui,'two');await ui.context.selectSource('one');
  assert.equal(ui.get('source-search-input').value,'');assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);assert.match(ui.title.textContent,/one/);
  await search(ui,'two');ui.get('source-search-input').value='draft';ui.context.current='project-two';await ui.project.dispatch('change');await settle();
  assert.equal(ui.get('source-search-input').value,'');assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);assert.equal(ui.rows().length,2);
});

test('citation outside the loaded page uses an unsearched point lookup',async()=>{
  const ui=setup({sourceRead:url=>{
    const query=params({url}),id=query.get('document_id');
    return Promise.resolve({documents:id?[documentRow(id)]:query.get('query')?[documentRow('two')]:[documentRow('one')],total:80});
  }});await settle();await search(ui,'two');await ui.context.selectSource('outside');
  const lookup=params(ui.sourceRequests.at(-1));assert.equal(lookup.get('document_id'),'outside');assert.equal(lookup.get('query'),null);assert.equal(lookup.get('state'),'all');assert.match(ui.title.textContent,/outside/);
});

test('failed search keeps its applied term for refresh and clears stale counts',async()=>{
  let fail=false;const ui=setup({sourceRead:url=>fail?Promise.reject(Error('search offline')):Promise.resolve({documents:[documentRow('one')],counts:{all:1,ready:1,failed:0,processing:0}})});await settle();fail=true;
  await search(ui,'failed-term');assert.equal(ui.rows().length,0);assert.equal(ui.get('all-count').textContent,'0');assert.match(ui.document.querySelector('.source-empty').textContent,/search offline/);
  fail=false;await ui.refresh.click();await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'failed-term');assert.equal(ui.rows().length,1);
});
