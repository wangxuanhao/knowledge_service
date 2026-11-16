const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createSession,progressLabel,applyFields,sourceLabel}=require('../../knowledge_service/web/document-preparation.js');

test('upload and configuration never start parsing; only explicit parse does',async()=>{
  const calls=[];
  const session=createSession({project:'p',request:async(path,body,method)=>{
    calls.push({path,body,method});
    if(path.endsWith('/preparations'))return {id:'s',items:[]};
    if(path.endsWith('/files'))return {id:'a',status:'staged',options:{}};
    return {id:'s',items:[{id:'a',status:'queued'}]};
  }});
  await session.upload(new Blob(['sample']), 'a.md');
  assert.equal(calls.some(c=>c.path.endsWith('/parse')),false);
  await session.configure('a',{title:'美团资料'});
  assert.equal(calls.some(c=>c.path.endsWith('/parse')),false);
  await session.parse(['a']);
  assert.equal(calls.filter(c=>c.path.endsWith('/parse')).length,1);
});

test('late upload response cannot enter a cleared project draft',async()=>{
  let finish;
  const session=createSession({project:'p',request:async path=>path.endsWith('/preparations')?{id:'s',items:[]}:new Promise(r=>finish=r)});
  const pending=session.upload(new Blob(['a']),'a.md');
  await new Promise(r=>setImmediate(r));session.dispose();finish({id:'a',status:'staged'});
  await pending;assert.equal(session.snapshot().items.length,0);
});

test('bulk fill preserves existing platform; explicit overwrite touches only enabled fields',()=>{
  const original={title:'A',applicability:{platforms:{mode:'specific',values:['美团']},businesses:{mode:'unknown',values:[]}}};
  const fields={applicability:{platforms:{mode:'specific',values:['抖音']},businesses:{mode:'specific',values:['电商']}}};
  const filled=applyFields(original,fields,'fill');
  assert.deepEqual(filled.applicability.platforms.values,['美团']);
  assert.deepEqual(filled.applicability.businesses.values,['电商']);
  assert.equal(filled.title,'A');
  const replaced=applyFields(original,{applicability:{platforms:fields.applicability.platforms}},'overwrite');
  assert.deepEqual(replaced.applicability.platforms.values,['抖音']);
  assert.equal(replaced.applicability.businesses.mode,'unknown');
  assert.deepEqual(original.applicability.platforms.values,['美团']);
});

test('unknown parsing work never appears as a fabricated percent',()=>{
  assert.match(progressLabel({status:'running',stage:'ocr',progress:null}),/文字识别/);
  assert.doesNotMatch(progressLabel({status:'running',stage:'ocr',progress:null}),/%/);
  assert.match(progressLabel({status:'running',progress:{completed:8,total:19,unit:'页'}}),/8.*19/);
});

test('closing view does not cancel tasks, while dispose rejects stale results',async()=>{
  const calls=[];const session=createSession({project:'p',request:async(path)=>{calls.push(path);return {id:'s',items:[]};}});
  await session.ensure();session.dispose();assert.equal(calls.length,1);
  assert.equal(calls.some(p=>p.endsWith('/cancel')),false);
});

test('bulk and cancellation follow their own API contracts without parse-only fields',async()=>{
  const calls=[];const session=createSession({project:'p',request:async(path,body)=>{
    calls.push({path,body});return {id:'s',items:[{id:'a',metadata_version:2}]};
  }});
  await session.ensure();await session.bulk(['a'],{title:'B'},'fill');await session.cancel(['a']);
  assert.deepEqual(calls.at(-1).body,{item_ids:['a']});
  assert.deepEqual(calls.at(-2).body,{item_ids:['a'],versions:{a:2},fields:{title:'B'},mode:'fill_missing'});
});

test('publication settings persist independently of file metadata and parsing',async()=>{
  const calls=[];const session=createSession({project:'p',request:async(path,body,method)=>{
    calls.push({path,body,method});return {id:'s',items:[],settings_version:body?.expected_version===0?1:0,publication_settings:body?.settings||{}};
  }});
  await session.ensure();await session.configureSettings({extraction_mode:'documents'});
  assert.equal(calls.at(-1).method,'PATCH');
  assert.deepEqual(calls.at(-1).body,{settings:{extraction_mode:'documents'},expected_version:0});
  assert.equal(session.snapshot().publication_settings.extraction_mode,'documents');
  await session.publish([],{});
  assert.equal(calls.at(-1).body.settings_version,1);
  assert.equal(calls.some(c=>c.path.endsWith('/parse')),false);
});

test('slice source labels show real pages and media times only when available',()=>{
  assert.match(sourceLabel({source_spans:[{payload:{page_no:3}}]}),/3.*页/);
  assert.match(sourceLabel({metadata:{start_ms:1000,end_ms:8500}}),/00:01.*00:08/);
  assert.equal(sourceLabel({metadata:{}}),'');
});

test('editing preserves the version seen when opening the form despite later polling',async()=>{
  let latest=1;const calls=[];
  const session=createSession({project:'p',request:async(path,body)=>{
    calls.push({path,body});return {id:'s',settings_version:latest,items:[{id:'a',metadata_version:latest,options:{}}]};
  }});
  await session.ensure();latest=2;await session.restore();
  await session.configure('a',{title:'old editor'},1);
  assert.equal(calls.at(-1).body.expected_version,1);
  await session.bulk(['a'],{title:'old bulk'},'overwrite',{a:1});
  assert.deepEqual(calls.at(-1).body.versions,{a:1});
  await session.configureSettings({extraction_mode:'documents'},1);
  assert.equal(calls.at(-1).body.expected_version,1);
});

test('independent copies require an explicit publication choice',async()=>{
  const calls=[];const session=createSession({project:'p',request:async(path,body)=>{calls.push(body);return {id:'s',items:[]};}});
  await session.ensure();await session.publish(['a'],{},['a']);
  assert.deepEqual(calls.at(-1).independent_item_ids,['a']);
});
