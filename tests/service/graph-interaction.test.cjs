const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createSession,fitViewport}=require('../../knowledge_service/web/graph-interaction.js');
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
