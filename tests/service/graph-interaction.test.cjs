const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createSession,fitViewport,createPanQueue}=require('../../knowledge_service/web/graph-interaction.js');
test('drag movement suppresses click; simple press remains selectable',()=>{
  let now=0;const session=createSession(()=>now);
  session.press(0,0);session.move(30,0);session.release();assert.equal(session.canClick(),false);
  now=500;assert.equal(session.canClick(),true);
  session.press(5,5);session.release();assert.equal(session.canClick(),true);
});
test('positions stay keyed by identity across filtering and reset between projects',()=>{
  const session=createSession();session.remember('p',[{id:'a',x:30,y:40}]);
  assert.deepEqual(session.position('p','a'),[30,40]);assert.equal(session.position('q','a'),undefined);
  session.remember('p',[{id:'b',x:50,y:60}]);assert.deepEqual(session.position('p','a'),[30,40]);
  session.clear();assert.equal(session.position('p','a'),undefined);
});
test('fit calculates viewport without changing solved node positions',()=>{
  const nodes=[{x:0,y:10},{x:1000,y:510}],before=JSON.stringify(nodes);
  const fit=fitViewport(nodes,600,400);assert.ok(fit.zoom>0&&fit.zoom<1);assert.deepEqual(fit.center,[500,260]);
  assert.equal(JSON.stringify(nodes),before);assert.equal(fitViewport([],500,500),null);
  assert.equal(fitViewport(nodes,0,0),null);
});

test('pan mouse bursts share one frame and preserve their accumulated distance',()=>{
  const frames=new Map(),applied=[];let id=0;
  const queue=createPanQueue({apply:(dx,dy)=>applied.push([dx,dy]),
    schedule:fn=>{frames.set(++id,fn);return id;},cancel:id=>frames.delete(id)});
  for(let i=0;i<120;i++)queue.move(.5,.25);
  assert.equal(frames.size,1);assert.equal(applied.length,0);
  const frame=[...frames.values()][0];frames.clear();frame();
  assert.deepEqual(applied,[[60,30]]);
  queue.move(4,2);queue.flush();
  assert.deepEqual(applied,[[60,30],[4,2]]);assert.equal(frames.size,0);
});

test('discarding pending pan on scope change cannot move the next graph',()=>{
  const frames=new Map(),applied=[];let id=0;
  const queue=createPanQueue({apply:(dx,dy)=>applied.push([dx,dy]),
    schedule:fn=>{frames.set(++id,fn);return id;},cancel:id=>frames.delete(id)});
  queue.move(10,20);const stale=[...frames.values()][0];queue.clear();stale();
  assert.equal(frames.size,0);assert.deepEqual(applied,[]);
  queue.move(2,3);queue.flush();assert.deepEqual(applied,[[2,3]]);
});
