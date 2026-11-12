const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');
const params=request=>new URL(request.url,'http://test').searchParams;
async function mode(ui,value='semantic'){const select=ui.get('source-search-mode');assert.ok(select,'search mode select exists');select.value=value;select.dispatch('change');await settle();}
async function search(ui,value){ui.get('source-search-input').value=value;ui.get('source-search-form').dispatch('submit');await settle();}
const response=(id,extra={})=>({documents:[documentRow(id)],total:1,counts:{all:1,ready:1,processing:0,failed:0},...extra});

test('mode switch applies current draft, invalidates cached and slow responses, binds pagination',async()=>{
  const pending=[],ui=setup({sourceRead:url=>new Promise(resolve=>pending.push({url,resolve}))});
  pending[0].resolve(response('directory'));await settle();
  await search(ui,'old');const old=pending.at(-1);
  ui.get('source-search-input').value='  current  ';await mode(ui);const semantic=pending.at(-1);
  assert.equal(params(semantic).get('retrieval_mode'),'semantic');assert.equal(params(semantic).get('query'),'current');assert.equal(params(semantic).get('cursor'),null);
  old.resolve(response('stale'));await settle();assert.equal(ui.rows().length,0);
  semantic.resolve(response('semantic',{search_mode:'semantic',next_cursor:'semantic-page',total:2}));await settle();
  const before=pending.length;await ui.context.SourcesView.open();assert.equal(pending.length,before);
  ui.get('source-search-input').value='unsubmitted';const more=ui.get('source-load-more').click();const append=pending.at(-1);
  assert.equal(params(append).get('retrieval_mode'),'semantic');assert.equal(params(append).get('query'),'current');assert.equal(params(append).get('cursor'),'semantic-page');
  ui.get('source-search-input').value='current';await mode(ui,'keyword');const keyword=pending.at(-1);assert.equal(params(keyword).get('cursor'),null);
  append.resolve(response('stale-page'));await more;await settle();assert.equal(ui.rows().length,0);
  keyword.resolve(response('keyword'));await settle();assert.match(ui.rows()[0].textContent,/keyword/);
});

test('semantic recall shows bounded corpus counts and plain text snippet',async()=>{
  const doc=documentRow('hit');doc.match={score:0.9,chunk_id:'chunk',chunk_version_id:'version',text:'<img src=x onerror=alert(1)> & 正文'};
  const ui=setup({sourceRead:url=>Promise.resolve(response('hit',{documents:[doc],search_mode:'semantic',result_limit:100,matched_chunk_count:3,total_is_exact:false,complete:false}))});await settle();
  ui.get('source-search-input').value='concept';await mode(ui);
  const summary=ui.document.querySelector('.source-summary').textContent;
  assert.match(summary,/向量正文召回/);assert.match(summary,/本次召回\s*1\s*份资料/);assert.match(summary,/最多\s*100\s*片段/);assert.match(summary,/不完整/);assert.doesNotMatch(summary,/当前范围共/);
  const snippet=ui.rows()[0].querySelector('.source-item__match');assert.ok(snippet);assert.equal(snippet.textContent,doc.match.text);assert.equal(snippet.querySelector('img'),null);
  assert.match(ui.document.querySelector('.source-filter-bar').getAttribute('aria-label'),/本次召回/);
});

test('empty query and clear retain mode but show ordinary directory; citations and projects reset keyword',async()=>{
  const ui=setup();await settle();await mode(ui);
  assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);assert.match(ui.document.querySelector('.source-summary').textContent,/当前范围共/);
  await search(ui,'two');await ui.get('source-search-clear').click();await settle();assert.equal(ui.get('source-search-mode').value,'semantic');assert.equal(params(ui.sourceRequests.at(-1)).get('query'),null);
  await search(ui,'two');await ui.context.selectSource('one');assert.equal(ui.get('source-search-mode').value,'keyword');assert.equal(ui.get('source-search-input').value,'');assert.notEqual(params(ui.sourceRequests.at(-1)).get('retrieval_mode'),'semantic');
  await mode(ui);ui.context.current='project-two';await ui.project.dispatch('change');await settle();assert.equal(ui.get('source-search-mode').value,'keyword');assert.equal(ui.get('source-search-input').value,'');
});

test('semantic failure stays explicit, keeps mode on refresh and never falls back',async()=>{
  const ui=setup({sourceRead:url=>params({url}).get('retrieval_mode')==='semantic'&&params({url}).get('query')?Promise.reject(Error('Milvus unavailable')):Promise.resolve(response('one'))});await settle();
  ui.get('source-search-input').value='concept';await mode(ui);assert.equal(ui.rows().length,0);assert.match(ui.document.querySelector('.source-empty').textContent,/Milvus unavailable/);assert.match(ui.document.querySelector('.source-summary').textContent,/向量正文.*失败/);
  const before=ui.sourceRequests.length;await ui.refresh.click();await settle();assert.equal(ui.sourceRequests.length,before+1);assert.equal(params(ui.sourceRequests.at(-1)).get('retrieval_mode'),'semantic');
});

test('semantic empty recall describes this recall only, and mode changes defer during IME',async()=>{
  const ui=setup({sourceRead:()=>Promise.resolve({documents:[],total:0,counts:{all:0,ready:0,processing:0,failed:0},search_mode:'semantic',complete:true})});await settle();
  const input=ui.get('source-search-input');input.value='正文';await input.dispatch('compositionstart');const before=ui.sourceRequests.length;await mode(ui);assert.equal(ui.sourceRequests.length,before);
  await input.dispatch('compositionend');await settle();assert.equal(params(ui.sourceRequests.at(-1)).get('retrieval_mode'),'semantic');assert.equal(params(ui.sourceRequests.at(-1)).get('query'),'正文');
  assert.match(ui.document.querySelector('.source-empty').textContent,/本次.*召回/);assert.doesNotMatch(ui.document.querySelector('.source-empty').textContent,/全项目没有/);
});
