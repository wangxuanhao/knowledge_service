const test=require('node:test');
const assert=require('node:assert/strict');
const {fixture}=require('./fixtures/ledger-ui.cjs');
const {setup,documentRow,settle}=require('./fixtures/sources-ui.cjs');

test('a 50 row ledger uses one bounded lineage batch and keeps all assertion details available',async()=>{
  const rows=Array.from({length:50},(_,i)=>({id:'row-'+i,kind:'entity',text:'Row '+i,version:1,metadata:{discovery_candidate_id:'assertion-'+i}}));
  const env=fixture({rows});
  env.context.api=async(url,body)=>{
    env.calls.push({url,body});
    if(url.endsWith('/records/page'))return {records:rows,total:50,next_cursor:null};
    if(url.endsWith('/ontologies'))return {versions:[]};
    if(url.endsWith('/assertions/batch'))return {assertions_by_record:Object.fromEntries(rows.map((row,i)=>[row.id,[{id:'assertion-'+i,canonical_record_id:row.id,status:'accepted',quote:'Full quote'}]]))};
    throw Error('Unexpected request '+url);
  };
  await env.get('load-records').onclick();
  const batches=env.calls.filter(call=>call.url.endsWith('/assertions/batch'));
  assert.equal(batches.length,1);assert.equal(batches[0].body.record_ids.length,50);
  assert.equal(env.calls.some(call=>call.url.includes('/assertions?')),false);
  const cell=env.get('tbody').children.at(-1).children[4];
  assert.match([cell,...cell.children].map(node=>node.textContent).join(' '),/1/);
});

test('ledger endpoint reference reads are bounded pages and stop after their context is stale',async()=>{
  const rows=Array.from({length:60},(_,i)=>({id:'relation-'+i,kind:'relation',subject_id:'subject-'+i,object_id:'object-'+i,text:'R',version:1,metadata:{}}));
  const env=fixture({rows}),batches=[];let resolve;
  env.context.api=async(url,body)=>{
    env.calls.push({url,body});
    if(url.endsWith('/assertions/batch'))return {assertions_by_record:{}};
    if(url.endsWith('/ontologies'))return {versions:[]};
    if(url.endsWith('/records/page')&&body.record_ids){batches.push(body);await new Promise(done=>resolve=done);return {records:[],next_cursor:null};}
    if(url.endsWith('/records/page'))return {records:rows,total:60,next_cursor:null};
    throw Error('Unexpected request '+url);
  };
  const loading=env.get('load-records').onclick();await settle();
  assert.equal(batches.length,1);assert.equal(batches[0].record_ids.length,100);
  assert.equal(batches[0].limit,100);assert.equal(batches[0].filters,undefined,'endpoint labels keep temporal scope without the row metadata filter');
  env.context.current='other';resolve();await loading;assert.equal(batches.length,1);
});

for(const returnToLedger of [false,true])test('leaving the ledger cancels initial-page follow-ons'+(returnToLedger?' even after returning':''),async()=>{
  const env=fixture(),calls=[];let release;
  env.context.api=async(url,body)=>{
    calls.push({url,body});
    if(url.endsWith('/records/page')&&body.limit===50)return new Promise(resolve=>release=resolve);
    if(url.endsWith('/records/page'))return {records:[],total:0,next_cursor:null};
    if(url.endsWith('/ontologies'))return {versions:[]};
    if(url.endsWith('/assertions/batch'))return {assertions_by_record:{}};
    throw Error('Unexpected request '+url);
  };
  const loading=env.get('load-records').onclick();await settle();assert.equal(calls.length,1);
  env.get('tab-records').className='hidden';
  for(const handler of env.events['workspace:page-changed']||[])handler({detail:{page:'sources'}});
  if(returnToLedger){env.get('tab-records').className='';for(const handler of env.events['workspace:page-changed']||[])handler({detail:{page:'records'}});}
  release({records:[{id:'row',kind:'entity',text:'Row',version:1,metadata:{}}],total:1,next_cursor:null});await loading;
  assert.equal(calls.length,1,'a response from the previous ledger visit must not start ontology, counts or lineage reads');
});

test('leaving the ledger while references load prevents the next reference batch',async()=>{
  const rows=Array.from({length:60},(_,i)=>({id:'r-'+i,kind:'relation',subject_id:'s-'+i,object_id:'o-'+i,text:'R',version:1,metadata:{}}));
  const env=fixture({rows}),batches=[];let release;
  env.context.api=async(url,body)=>{
    if(url.endsWith('/assertions/batch'))return {assertions_by_record:{}};
    if(url.endsWith('/ontologies'))return {versions:[]};
    if(url.endsWith('/records/page')&&body.record_ids){batches.push(body);return new Promise(resolve=>release=resolve);}
    if(url.endsWith('/records/page'))return {records:rows,total:60,next_cursor:null};
    throw Error('Unexpected request '+url);
  };
  const loading=env.get('load-records').onclick();await settle();assert.equal(batches.length,1);
  env.get('tab-records').className='hidden';release({records:[],next_cursor:null});await settle();
  if(batches.length>1){release({records:[],next_cursor:null});await loading;}
  assert.equal(batches.length,1);await loading;
});

test('sources navigation and a citation share one directory read, then fetch only selected full text',async()=>{
  const env=setup();await settle();const requests=[];
  env.context.api=(url,body,method)=>new Promise(resolve=>requests.push({url,body,method,resolve}));
  const loading=env.refresh.click(),opening=env.context.SourcesView.open(),citation=env.context.selectSource('one');await settle();
  assert.equal(requests.length,1);assert.match(requests[0].url,/\/sources\?limit=50&summary_only=true&state=all/);
  requests[0].resolve({documents:[{...documentRow('one'),text:undefined}],total:1,counts:{all:1,ready:1,processing:0,failed:0},next_cursor:null});await settle();
  assert.equal(requests.length,2);assert.match(requests[1].url,/\/records\/one$/);
  assert.equal(requests[1].method,'GET');requests[1].resolve(documentRow('one'));
  await Promise.all([loading,opening,citation]);assert.equal(env.body.textContent,'原文 one');
});

test('source pages preserve global status counts, load more and resolve citations outside the page',async()=>{
  const env=setup();await settle();const reads=[];
  env.context.api=async(url,body,method)=>{
    reads.push({url,body,method});
    if(url.includes('/records/'))return documentRow('remote');
    const query=new URL(url,'http://test').searchParams;
    if(query.get('document_id'))return {documents:[{...documentRow('remote'),text:undefined}],total:1};
    return {documents:[{...documentRow(query.get('cursor')?'two':'one'),text:undefined}],next_cursor:query.get('cursor')?null:'next',total:70,counts:{all:100,ready:70,processing:20,failed:10}};
  };
  await env.refresh.click();assert.equal(env.get('all-count').textContent,'100');assert.equal(env.get('failed-count').textContent,'10');
  const more=env.get('source-load-more');assert.ok(more);await more.click();assert.equal(env.rows().length,2);assert.match(reads.at(-1).url,/cursor=next/);
  await env.filter('ready').click();await settle();assert.match(reads.at(-1).url,/state=ready/);assert.equal(env.rows().length,1);assert.equal(env.get('all-count').textContent,'100');
  await env.context.selectSource('remote');assert.ok(reads.some(item=>item.url.includes('document_id=remote')));assert.equal(env.title.textContent,'资料 remote');assert.equal(env.body.textContent,'原文 remote');
});

test('a selected source full-text response cannot open after project switching',async()=>{
  const env=setup();await settle();const reads=[];
  env.context.api=(url,body,method)=>new Promise(resolve=>reads.push({url,body,method,resolve}));
  const selected=env.rows()[0].click();await settle();assert.equal(reads.length,1);
  env.context.current='next';const changing=env.project.dispatch('change');await settle();
  reads.find(item=>item.url.includes('/sources')).resolve({documents:[],total:0,counts:{all:0,ready:0,failed:0,processing:0}});
  reads[0].resolve(documentRow('one'));await Promise.all([selected,changing]);assert.equal(env.drawers[0].dialog.open,false);assert.equal(env.body.textContent,'');
});

test('the latest source citation owns selection when point lookups complete out of order',async()=>{
  const env=setup();await settle();const reads=[];
  env.context.api=(url,body,method)=>new Promise(resolve=>reads.push({url,body,method,resolve}));
  const first=env.context.selectSource('remote-one');const rejected=assert.rejects(first,/资料范围已更新/);
  await settle();
  const second=env.context.selectSource('remote-two');await settle();assert.equal(reads.length,2);
  reads[1].resolve({documents:[{...documentRow('remote-two'),text:undefined}]});await settle();
  reads.find(item=>item.url.includes('/records/')).resolve(documentRow('remote-two'));await second;
  reads[0].resolve({documents:[{...documentRow('remote-one'),text:undefined}]});await settle();
  reads.find(item=>item.url.endsWith('/records/remote-one'))?.resolve(documentRow('remote-one'));await rejected;
  assert.equal(env.title.textContent,'资料 remote-two');assert.equal(env.drawers[0].dialog.open,true);
});
