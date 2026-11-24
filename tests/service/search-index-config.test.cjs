const {test}=require('node:test');
const assert=require('node:assert/strict');
const {shouldPoll,indexStatus,createMonitor}=require('../../knowledge_service/web/search-index-config.js');
test('index status follows automatic work only while visible',()=>{
  assert.equal(shouldPoll({open:true,hidden:false,state:'building'}),true);
  for(const state of ['not_built','failed','requires_rebuild','leased'])assert.equal(shouldPoll({open:true,hidden:false,state}),true);
  assert.equal(shouldPoll({open:true,hidden:false,state:'ready'}),false);
  assert.equal(shouldPoll({open:false,hidden:false,state:'building'}),false);
  assert.equal(shouldPoll({open:true,hidden:true,state:'building'}),false);
});

test('vector availability follows the current index watermark and real task progress',()=>{
  const current={automatic:true,state:'ready',applied_seq:50,watermark:50};
  assert.equal(indexStatus(current).label,'向量检索可用');
  assert.equal(indexStatus({...current,watermark:55}).label,'向量索引同步中');
  assert.match(indexStatus({...current,watermark:55}).detail,/5 项变更/);
  assert.equal(indexStatus({...current,state:'building',job:{status:'running',progress:42}}).progress,42);
  assert.equal(indexStatus({...current,state:'building',job:{status:'retry_wait',progress:42}}).progress,null);
  assert.equal(indexStatus({...current,state:'building',job:{status:'retry_wait'}}).label,'向量索引等待重试');
  assert.equal(indexStatus({...current,automatic:false}).label,'向量检索未启用');
});

test('the monitor refreshes from building to ready and ignores a prior project response',async()=>{
  let release,poll;const views=[];
  const monitor=createMonitor({
    read:project=>project==='old'?new Promise(resolve=>{release=resolve;}):Promise.resolve(poll?{state:'ready'}:{state:'building'}),
    render:(result)=>views.push(result?.state||null),
    schedule:callback=>{poll=callback;return callback;},cancel:()=>{},
  });
  const old=monitor.sync('old',true);
  await monitor.sync('current',true);
  assert.equal(views.at(-1),'building');
  await poll();
  assert.equal(views.at(-1),'ready');
  release({state:'failed'});await old;
  assert.equal(views.at(-1),'ready');
  await monitor.sync('current',false);
});
