const {test}=require('node:test');
const assert=require('node:assert/strict');
const {fixture}=require('./fixtures/ledger-ui.cjs');

test('ledger startup and project changes never access ontology editor nodes absent from the actual HTML',()=>{
  let f;
  assert.doesNotThrow(()=>{f=fixture({strictOntologyDom:true});});
  for(const id of ['load-ontology','tab-ontology','ontology-summary','turtle','ontology-versions','save-ontology'])
    assert.equal(f.htmlIds.has(id),false,`${id} must stay absent from the delivered shell`);
  for(const handler of f.get('project').events.change||[])handler();
  assert.deepEqual(f.absentAccesses,[]);
  assert.equal(f.events['ontology-version-governance:open'],undefined);
});
test('ledger loads server pages and exact totals while searching and changing kind reset the cursor',async()=>{
  const {get,calls,created}=fixture();await get('load-records').onclick();
  assert.equal(calls.some(call=>call.url.includes('/records/query')),false);
  assert.match(get('record-count').textContent,/150001/);
  await get('records-next').onclick();assert.equal(calls.filter(call=>call.url.endsWith('/records/page')&&call.body.limit===50).at(-1).body.cursor,'next-page');
  await get('records-prev').onclick();assert.equal(calls.filter(call=>call.url.endsWith('/records/page')&&call.body.limit===50).at(-1).body.cursor,null);
  get('record-search').value='远端';await get('record-search-submit').onclick();
  const searched=calls.filter(call=>call.url.endsWith('/records/page')&&call.body.limit===50).at(-1).body;
  assert.equal(searched.query,'远端');assert.equal(searched.cursor,null);assert.equal(searched.filters.value,'杭州');
  const tab=created.find(node=>node.dataset.ledgerView==='attribute');await tab.onclick();
  assert.equal(calls.filter(call=>call.body?.limit===50).at(-1).body.kinds[0],'attribute');
});
test('knowledge review and structured creation have stable toolbar entries; writes are admin-only',()=>{
  const {created,context}=fixture();
  const review=created.find(node=>node.id==='ledger-open-reviews');assert.ok(review);review.onclick();assert.equal(context.reviewOpened,true);
  for(const kind of ['entity','relation','attribute']){
    const entry=created.find(node=>node.dataset.createRecord===kind);assert.ok(entry);assert.match(entry.className,/admin-only/);
  }
});
test('ledger reads only current-page assertion lineage instead of requesting every project assertion',async()=>{
  const {calls,get}=fixture();await get('load-records').onclick();
  assert.equal(calls.some(call=>call.url.endsWith('/assertions')),false);
  assert.ok(calls.some(call=>call.url.endsWith('/assertions/batch')&&call.body.record_ids[0]==='first'));
  assert.equal(calls.some(call=>/duplicate-groups|structure-pending|reclassify/.test(call.url)),false);
});
for(const kind of ['entity','relation']){
  test('ledger '+kind+' type cell renders the shared label and subtle identifier as HTML',async()=>{
    const type='https://test/Merchant';
    const {get,created}=fixture({terms:[{id:type,name:'Merchant',label_zh:'商户'}],
      rows:[{id:'first',kind,text:'示例',type,version:1,metadata:{},ontology_id:'published'}]});
    await get('load-records').onclick();
    if(kind==='relation')await created.find(node=>node.dataset.ledgerView==='relation').onclick();
    const hint=get('tbody').children.at(-1).children[1].children[1];
    assert.equal(hint.innerHTML,'商户 <span class="subtle">Merchant</span>');
    assert.doesNotMatch(hint.textContent,/<span/);
  });
}
test('ledger renders escaped type labels and identifiers without creating executable markup',async()=>{
  const type='https://test/Merchant<img src=x onerror=alert(1)>';
  const {get}=fixture({terms:[{id:type,name:'Merchant',label_zh:'<script>alert(2)</script>'}],
    rows:[{id:'first',kind:'entity',text:'示例',type,version:1,metadata:{},ontology_id:'published'}]});
  await get('load-records').onclick();
  const hint=get('tbody').children.at(-1).children[1].children[1];
  assert.match(hint.innerHTML,/&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
  assert.match(hint.innerHTML,/Merchant&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(hint.innerHTML,/<(?:script|img)\b/);
});
test('ledger attribute labels keep shared formatting while attribute values remain escaped',async()=>{
  const type='https://test/amount';
  const {get,created}=fixture({terms:[{id:type,name:'amount',label_zh:'金额'}],
    rows:[{id:'first',kind:'attribute',text:'金额',type,value:'<svg onload=alert(1)>',
      subject_id:'merchant',version:1,metadata:{},ontology_id:'published'}]});
  await get('load-records').onclick();
  await created.find(node=>node.dataset.ledgerView==='attribute').onclick();
  const excerpt=get('tbody').children.at(-1).children[0].children[0];
  assert.equal(excerpt.innerHTML,'<b>金额 <span class="subtle">amount</span></b> = &lt;svg onload=alert(1)&gt;');
  assert.doesNotMatch(excerpt.innerHTML,/<svg\b/);
});
