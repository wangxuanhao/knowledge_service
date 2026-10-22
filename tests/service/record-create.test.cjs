const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/record-create.js');
const create=fs.existsSync(file)?require(file):{};
const xsd='http://www.w3.org/2001/XMLSchema#';
const publication={id:'published-7',summary:{classes:[{id:'Employee',label_zh:'员工'}],relations:[
  {id:'worksAt',domain:['Employee','Contractor'],range:['Company','Team']},{id:'relatedTo',domain:[],range:[]}],
  attributes:[{id:'age',domain:['Employee'],range:[xsd+'integer']},{id:'active',range:[xsd+'boolean']},
    {id:'salary',range:[xsd+'decimal']}]}};

test('creation catalog preserves multiple or unconstrained endpoints and requires a published identity',()=>{
  assert.equal(typeof create.catalog,'function');
  const catalog=create.catalog(publication);
  assert.deepEqual(catalog.relation[0].domain,['Employee','Contractor']);
  assert.deepEqual(catalog.relation[1].range,[]);
  assert.throws(()=>create.catalog({summary:publication.summary}),/发布/);
  assert.throws(()=>create.catalog({id:'draft',summary:{}}),/目录/);
});
test('structured records bind the actual publication and preserve metadata and business time',()=>{
  const record=create.buildRecord('entity',{text:' 张三 ',type:'Employee',metadata:'{"region":"华东"}',
    valid_from:'2026-01-01',valid_until:'2027-01-01'},publication);
  assert.equal(record.text,'张三');assert.equal(record.ontology_id,'published-7');
  assert.deepEqual(record.metadata,{region:'华东'});assert.equal(record.id,undefined);
  assert.throws(()=>create.buildRecord('entity',{text:'张三',type:'invented'},publication),/本体/);
  assert.throws(()=>create.buildRecord('relation',{text:'任职',type:'worksAt',subject_id:'e1'},publication),/终点/);
  assert.throws(()=>create.buildRecord('entity',{text:'张三',type:'Employee',valid_from:'2027-01-01',valid_until:'2026-01-01'},publication),/晚于/);
});
test('attribute creation follows declared primitive datatype without changing unsupported ranges',()=>{
  const fields={type:'active',subject_id:'e1',mode:'boolean',value:'false'};
  const record=create.buildRecord('attribute',fields,publication);
  assert.equal(record.value,false);assert.equal(record.datatype,xsd+'boolean');assert.match(record.text,/false/);
  assert.deepEqual(create.attributeModes(publication.summary.attributes[0]),['integer']);
  assert.deepEqual(create.attributeModes(publication.summary.attributes[2]),[]);
  assert.throws(()=>create.buildRecord('attribute',{...fields,type:'age'},publication),/数据类型/);
  assert.throws(()=>create.buildRecord('attribute',{...fields,type:'salary',mode:'double',value:'1.5'},publication),/数据类型/);
});
test('entity lookup is bounded server pagination and ignores responses after project or scope changes',async()=>{
  let project='p',generation=0,finish;const calls=[];
  const session=create.createSession({project:'p',request:async(url,body)=>{calls.push({url,body});
    if(body?.query==='delayed')return new Promise(resolve=>finish=resolve);
    return {records:[{id:'far-100001',kind:'entity',text:'远端实体'}],next_cursor:'cursor-2',total:150000};},
    accepts:()=>project==='p'&&generation===0,canWrite:()=>true,scope:{filters:{field:'metadata.city',op:'eq',value:'杭州'}}});
  const first=await session.entities('远端',null);
  assert.equal(first.total,150000);assert.equal(calls[0].url,'/api/projects/p/records/page');
  assert.equal(calls[0].body.limit,50);assert.deepEqual(calls[0].body.kinds,['entity']);
  await session.entities('远端','cursor-2');assert.equal(calls[1].body.cursor,'cursor-2');
  const pending=session.entities('delayed');generation++;finish({records:[{id:'stale'}]});
  await assert.rejects(pending,/范围|项目/);project='q';
  await assert.rejects(session.entities('name'),/范围|项目/);
});
test('submit rechecks current publication and role before using the formal records write',async()=>{
  let published=publication,writer=true;const calls=[];
  const session=create.createSession({project:'p',accepts:()=>true,canWrite:()=>writer,
    request:async(url,body,method)=>{calls.push({url,body,method});return body?{records:[{id:'new',...body.records[0]}]}:published;}});
  await session.loadPublication();
  const record=create.buildRecord('entity',{text:'李四',type:'Employee'},publication);
  const result=await session.submit(record);assert.equal(result.records[0].id,'new');
  assert.deepEqual(calls.at(-1).body,{records:[record]});assert.equal(calls.at(-1).url,'/api/projects/p/records');
  published={...publication,id:'published-8'};
  await assert.rejects(session.submit(record),/本体.*更新/);
  writer=false;await assert.rejects(session.submit(record),/管理员/);
});

test('exact entity ID is a single bounded lookup independent of corpus text matches',async()=>{
  const calls=[],filters={field:'metadata.city',op:'eq',value:'杭州'};
  const session=create.createSession({project:'p',accepts:()=>true,scope:{filters},request:async(url,body)=>{
    calls.push({url,body});return {records:[{id:'zzz',kind:'entity'}]};}});
  assert.equal((await session.entity('zzz')).id,'zzz');
  assert.equal(calls.length,1);assert.deepEqual(calls[0].body.record_ids,['zzz']);
  assert.deepEqual(calls[0].body.filters,filters);assert.equal(calls[0].body.query,undefined);
});
test('cursor pager searches the whole server scope and makes next/previous requests without retaining the corpus',async()=>{
  const calls=[];const pager=create.createPager(async body=>{calls.push(body);return body.cursor
    ?{records:[{id:'last'}],next_cursor:null,total:150001}
    :{records:[{id:'first'}],next_cursor:'page-two',total:150001};});
  await pager.load({kinds:['entity'],query:'远端',filters:{field:'x',op:'eq',value:1}});
  await pager.next();assert.equal(pager.snapshot().page,1);assert.equal(pager.snapshot().records[0].id,'last');
  await pager.previous();assert.equal(calls.at(-1).cursor,null);assert.equal(pager.snapshot().total,150001);
  assert.equal(calls[0].query,'远端');assert.equal(calls[0].limit,50);
  await pager.load({kinds:['attribute'],query:''});assert.equal(pager.snapshot().page,0);
});
test('cursor pager rejects stale requests even when an old scope is selected again',async()=>{
  const pending=[];const pager=create.createPager(body=>new Promise(resolve=>pending.push({body,resolve})));
  const a=pager.load({kinds:['entity'],query:'a'}),b=pager.load({kinds:['entity'],query:'b'});
  pending[1].resolve({records:[{id:'current'}],next_cursor:null,total:1});await b;
  pending[0].resolve({records:[{id:'stale'}],next_cursor:null,total:1});assert.equal(await a,null);
  assert.equal(pager.snapshot().records[0].id,'current');
  const old=pager.next();await old;pager.invalidate();assert.equal(pager.snapshot().records.length,0);
});
