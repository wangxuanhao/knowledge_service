const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
const params=request=>new URL(request.url,'http://test').searchParams;
const response=(extra={})=>({documents:[documentRow('hit')],total:1,counts:{all:1,ready:1,failed:0,processing:0},...extra});
async function search(ui,term){ui.get('source-search-input').value=term;ui.get('source-search-form').dispatch('submit');await settle();}
async function mode(ui,value){ui.get('source-search-mode').value=value;ui.get('source-search-mode').dispatch('change');await settle();}
const steps=ui=>JSON.parse(JSON.stringify(ui.sourceRequests.at(-1).body.search_steps||[]));

test('explicit refinement preserves results until submission and sends the complete prefix',async()=>{
  const ui=setup({sourceRead:()=>Promise.resolve(response())});await settle();
  assert.ok(ui.get('source-search-refine'),'explicit refine action exists');assert.equal(ui.get('source-search-refine').disabled,true);
  await search(ui,'退款');await ui.get('source-search-refine').click();assert.equal(ui.get('source-search-input').value,'');assert.equal(ui.rows().length,1);
  const count=ui.sourceRequests.length;await mode(ui,'semantic');assert.equal(ui.sourceRequests.length,count);assert.equal(ui.rows().length,1);
  await search(ui,'多久到账');assert.deepEqual(steps(ui),[{query:'退款',retrieval_mode:'keyword'}]);assert.equal(params(ui.sourceRequests.at(-1)).get('retrieval_mode'),'semantic');
  assert.match(ui.get('source-search-steps').textContent,/退款/);assert.match(ui.get('source-search-steps').textContent,/多久到账/);
  await ui.get('source-search-back').click();await settle();assert.deepEqual(steps(ui),[]);assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'退款');assert.equal(ui.get('source-search-mode').value,'keyword');
  await ui.get('source-search-steps').querySelector('button').click();await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);
});

test('deletion replays remaining conditions, semantic bounds survive keyword and refresh/paging',async()=>{
  const ui=setup({sourceRead:()=>Promise.resolve(response({bounded_by_semantic:true,complete:false,next_cursor:'next'}))});await settle();
  await search(ui,'first');assert.ok(ui.get('source-search-refine'));await ui.get('source-search-refine').click();await mode(ui,'semantic');await search(ui,'second');
  await ui.get('source-search-refine').click();await mode(ui,'keyword');await search(ui,'third');
  assert.match(ui.document.querySelector('.source-summary').textContent,/召回范围内/);assert.match(ui.document.querySelector('.source-summary').textContent,/不完整/);assert.doesNotMatch(ui.document.querySelector('.source-summary').textContent,/当前范围共/);
  await ui.get('source-load-more').click();assert.equal(params(ui.sourceRequests.at(-1)).get('cursor'),'next');assert.equal(steps(ui).length,2);
  await ui.changeScope();await ui.refresh.click();assert.equal(steps(ui).length,2);
  const remove=ui.get('source-search-steps').querySelectorAll('button')[0];assert.equal(remove.getAttribute('aria-label'),'删除第 1 步：first');await remove.click();await settle();assert.deepEqual(steps(ui),[{query:'second',retrieval_mode:'semantic'}]);
  await ui.get('source-search-clear').click();assert.deepEqual(steps(ui),[]);assert.equal(ui.get('source-search-mode').value,'keyword');
});

test('loading, errors, empty results and the eight step limit disable further refinement',async()=>{
  let fail=false,empty=false;const ui=setup({sourceRead:()=>fail?Promise.reject(Error('offline')):Promise.resolve(response(empty?{documents:[],total:0}:{}))});await settle();
  await search(ui,'one');assert.ok(ui.get('source-search-refine'));
  for(let i=2;i<=8;i++){await ui.get('source-search-refine').click();await search(ui,'step'+i);}
  assert.equal(ui.get('source-search-refine').disabled,true);assert.match(ui.get('source-search-steps').textContent,/最多8步/);assert.equal(steps(ui).length,7);
  await ui.get('source-search-back').click();assert.equal(ui.get('source-search-refine').disabled,false);
  fail=true;await search(ui,'bad');assert.equal(ui.get('source-search-refine').disabled,true);assert.equal(steps(ui).length,6);
  fail=false;empty=true;await ui.refresh.click();assert.equal(ui.get('source-search-refine').disabled,true);
});

test('pending refinement can be cancelled and IME defers mode submission without widening an empty draft',async()=>{
  const ui=setup({sourceRead:()=>Promise.resolve(response())});await settle();await search(ui,'first');
  await ui.get('source-search-refine').click();await mode(ui,'semantic');await ui.get('source-search-back').click();
  assert.equal(ui.get('source-search-input').value,'first');assert.equal(ui.get('source-search-mode').value,'keyword');assert.deepEqual(steps(ui),[]);
  await ui.get('source-search-refine').click();const input=ui.get('source-search-input');await input.dispatch('compositionstart');const before=ui.sourceRequests.length;
  await mode(ui,'semantic');await input.dispatch('compositionend');await settle();assert.equal(ui.sourceRequests.length,before);
  input.value='second';await input.dispatch('compositionstart');await mode(ui,'keyword');await ui.get('source-search-form').dispatch('submit');assert.equal(ui.sourceRequests.length,before);
  await input.dispatch('compositionend');await settle();assert.deepEqual(steps(ui),[{query:'first',retrieval_mode:'keyword'}]);assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'second');
  await ui.get('source-search-refine').click();await mode(ui,'semantic');await ui.get('source-search-clear').click();assert.equal(ui.get('source-search-mode').value,'semantic');assert.deepEqual(steps(ui),[]);
});

test('prefix changes invalidate cached opens and stale responses with the same final query',async()=>{
  const pending=[],ui=setup({sourceRead:(url,body)=>new Promise((resolve,reject)=>pending.push({url,body,resolve,reject}))});
  pending[0].resolve(response());await settle();await search(ui,'first');pending.at(-1).resolve(response());await settle();
  await ui.get('source-search-refine').click();await search(ui,'last');const chained=pending.at(-1);assert.equal(ui.get('source-search-refine').disabled,true);
  ui.get('source-search-steps').querySelector('button').click();const unchained=pending.at(-1);assert.equal(params(unchained).get('query'),'last');assert.equal(unchained.body.search_steps,undefined);
  chained.resolve(response({documents:[documentRow('stale')]}));await settle();assert.equal(ui.rows().length,0);
  unchained.resolve(response({documents:[documentRow('current')]}));await settle();assert.match(ui.rows()[0].textContent,/current/);
  const count=pending.length;await ui.context.SourcesView.open();assert.equal(pending.length,count);
  await ui.get('source-search-refine').click();await search(ui,'failure');pending.at(-1).reject(Error('offline'));await settle();assert.equal(ui.get('source-search-refine').disabled,true);assert.match(ui.get('source-search-steps').textContent,/failure/);
});
