const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../../knowledge_service/web/knowledge-changes.js');

test('a committed change invalidates graph immediately and refreshes both owners',async()=>{
  const calls=[];
  const changes=create({project:()=> 'p',invalidateGraph:()=>calls.push('invalidate'),
    refreshLedger:async()=>calls.push('ledger'),refreshGraph:async()=>calls.push('graph')});
  assert.equal(await changes.notify({projectId:'p'}),false);
  assert.deepEqual(calls,['invalidate','ledger','graph']);
});

test('a newer commit during refresh revokes old graph work and receives a final refresh',async()=>{
  let finish,invalidations=0,graphs=0,ledgers=0;
  const changes=create({project:()=> 'p',invalidateGraph:()=>invalidations++,
    refreshLedger:async()=>{ledgers++;},refreshGraph:()=>++graphs===1?new Promise(resolve=>finish=resolve):true});
  const first=changes.notify({projectId:'p'});await Promise.resolve();
  const second=changes.notify({projectId:'p'});
  assert.equal(invalidations,2);finish(true);
  assert.equal(await first,false);assert.equal(await second,false);
  assert.equal(graphs,2);assert.equal(ledgers,2);
});

test('refresh failure stays separate from committed save and a later refresh can retry',async()=>{
  let fail=true;
  const changes=create({project:()=> 'p',invalidateGraph(){},refreshLedger:async()=>true,
    refreshGraph:async()=>{if(fail)throw Error('unavailable');return true;}});
  assert.equal(await changes.notify({projectId:'p'}),true);
  fail=false;assert.equal(await changes.notify({projectId:'p'}),false);
});

test('a late mutation from another project never refreshes current views',async()=>{
  let current='p',count=0,finish;
  const changes=create({project:()=>current,invalidateGraph:()=>count++,refreshLedger:async()=>true,
    refreshGraph:()=>new Promise(resolve=>finish=resolve)});
  const pending=changes.notify({projectId:'p'});current='q';
  assert.equal(await changes.notify({projectId:'p'}),false);
  finish(true);await pending;assert.equal(count,1);
});
