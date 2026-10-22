const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const file=path.join(__dirname,'../../knowledge_service/web/capacity-diagnostics.js');

test('capacity diagnostics exposes tested pure aggregation helpers',()=>{
  assert.ok(fs.existsSync(file),'diagnostic module exists');
  const audit=require(file);
  assert.equal(audit.enabled('?capacity_audit=1'),true);
  assert.equal(audit.enabled('?capacity_audit=0'),false);
  assert.equal(audit.enabled('?other=capacity_audit=1'),false);
});

test('ordinary URLs do not inspect or modify the product DOM',()=>{
  const context={window:{location:{search:''}},URLSearchParams,module:{exports:{}}};
  Object.defineProperty(context,'document',{get(){throw Error('ordinary URL touched DOM');}});
  vm.runInNewContext(fs.readFileSync(file,'utf8'),context);
});

test('bounded series reports all observed counts and retained nearest-rank percentiles',()=>{
  const {createSeries,summarize}=require(file),series=createSeries(3);
  [1,2,3,4,5,NaN,-1].forEach(value=>series.add(value));
  assert.deepEqual(summarize(series),{count:5,retained:3,retention:'most recent samples',min_ms:1,max_ms:5,mean_ms:3,p50_ms:4,p95_ms:5,p99_ms:5});
  assert.equal(series.values.length,3);
  assert.equal(summarize(createSeries(2)).p95_ms,null);
});

test('dataset audit counts unique IDs and dangling relations independently of renderer counters',()=>{
  const {auditDataset}=require(file);
  const dataset={records:[{id:'a'},{id:'a'},{id:'b'}],edges:[{id:'x',subject_id:'a',object_id:'b'},{id:'x',subject_id:'a',object_id:'absent'}]};
  assert.deepEqual(auditDataset(dataset),{entities:3,relations:2,unique_entities:2,unique_relations:1,duplicate_entity_ids:1,duplicate_relation_ids:1,dangling_relations:1});
});

test('resource metadata strips queries, ignores foreign origins and never contains response content',()=>{
  const {safeResource}=require(file);
  const row={name:'http://127.0.0.1:8100/api/projects/a/graph-sessions?token=secret',duration:17.25,transferSize:200,encodedBodySize:100,decodedBodySize:500,responseStatus:200,initiatorType:'fetch'};
  assert.deepEqual(safeResource(row,'http://127.0.0.1:8100'),{path:'/api/projects/a/graph-sessions',duration_ms:17.25,transfer_bytes:200,encoded_bytes:100,decoded_bytes:500,status:200,initiator:'fetch'});
  assert.equal(safeResource({...row,name:'https://external.example/test?token=secret'},'http://127.0.0.1:8100'),null);
});

test('method observation preserves promise identity, return values and product errors',async()=>{
  const {observeMethod}=require(file),calls=[];
  const promise=Promise.resolve(7),target={load(){return promise;},plain(){return 9;},fail(){throw Error('product error');}};
  observeMethod(target,'load',{before(){calls.push('before');},after(value){calls.push(value);}});
  assert.equal(target.load(),promise);
  await promise;
  assert.deepEqual(calls,['before',7]);
  observeMethod(target,'plain',{after(){throw Error('instrumentation error');}});
  assert.equal(target.plain(),9);
  observeMethod(target,'fail',{error(error){calls.push(error.message);}});
  assert.throws(()=>target.fail(),/product error/);
  assert.equal(calls.at(-1),'product error');
});
