const test=require('node:test');
const assert=require('node:assert/strict');
const {createController}=require('../../knowledge_service/web/graph-explorer.js');

function scene(overrides={}){
  const calls=[],focus=[],a={id:'a',kind:'entity',text:'甲'},b={id:'b',kind:'entity',text:'乙'};
  const adapter={neighbors:(id,options)=>{calls.push({id,...options});return {items:[{node:b,edge:{id:'r',type:'关联'},direction:'out'}],nextCursor:options.cursor?null:'p2',count:2};},
    focus:(id,options)=>({nodeIds:new Set([id,b.id]),edgeIds:new Set(['r']),...options}),setFocus:value=>focus.push(value),
    select:row=>calls.push({selected:row.id}),expand:async()=>{},collapse:()=>{},canCollapse:()=>true,...overrides};
  const controller=createController();controller.setContext({mode:'local',key:'project/scope',revision:1,adapter,selected:a});
  return {controller,adapter,calls,focus,a,b};
}

test('closed neighbor tool does not query or scan a graph',()=>{
  const {controller,calls}=scene();assert.equal(controller.snapshot().open,false);assert.equal(calls.length,0);
});
test('neighbors are paged and direction change returns to the first page',async()=>{
  const {controller,calls}=scene();await controller.open();assert.equal(calls.length,1);
  await controller.next();assert.equal(calls.at(-1).cursor,'p2');await controller.previous();assert.equal(calls.at(-1).cursor,null);
  await controller.setDirection('backward');assert.equal(calls.at(-1).direction,'backward');assert.equal(calls.at(-1).cursor,null);
});
test('static focus is bounded and closing the panel preserves its state',async()=>{
  const {controller,focus}=scene();await controller.toggleFocus();
  const result=focus.at(-1);assert.equal(result.hops,1);assert.ok(result.maxNodes<=200);assert.ok(result.maxEdges<=400);assert.ok(result.maxScannedEdges<=2000);
  await controller.open();controller.close();assert.equal(controller.snapshot().focused,true);assert.equal(focus.at(-1),result);
  await controller.setHops(2);assert.equal(focus.at(-1).hops,2);await controller.toggleFocus();assert.equal(focus.at(-1),null);
});
test('selection of a relation or virtual attribute disables entity exploration',async()=>{
  const {controller,calls}=scene();controller.select({id:'r',kind:'relation'});await controller.open();assert.equal(calls.length,0);assert.equal(controller.snapshot().selected,null);
  controller.select({id:'v',kind:'attribute_value',virtual:true});await controller.toggleFocus();assert.equal(controller.snapshot().focused,false);
});
test('a late neighbor page cannot replace a newer selection',async()=>{
  let finish;const {controller,b}=scene({neighbors:()=>new Promise(resolve=>finish=resolve)});
  const loading=controller.open();controller.close();controller.select(b);finish({items:[{node:{id:'obsolete'}}],count:1});await loading;
  assert.equal(controller.snapshot().selected.id,'b');assert.equal(controller.snapshot().page.items.length,0);
});
test('scope and mode changes restore the old adapter and discard focus',async()=>{
  const {controller,focus,adapter,a}=scene();await controller.toggleFocus();
  controller.setContext({mode:'full',key:'other/scope',revision:1,adapter,selected:a});
  assert.equal(focus.at(-1),null);assert.equal(controller.snapshot().focused,false);assert.equal(controller.snapshot().mode,'full');
});
test('append refreshes an open page and recalculates focus while keeping selection',async()=>{
  const {controller,calls,focus,adapter,a}=scene();await controller.open();await controller.toggleFocus();
  controller.setContext({mode:'local',key:'project/scope',revision:2,adapter,selected:a});await controller.idle();
  assert.equal(controller.snapshot().selected.id,'a');assert.ok(calls.length>=2);assert.ok(focus.at(-1).nodeIds.has('a'));
});
test('failed expansion leaves exploration available with an actionable error',async()=>{
  const {controller}=scene({expand:async()=>{throw Error('读取邻域失败');}});await controller.expand();
  assert.match(controller.snapshot().error,/读取邻域失败/);assert.equal(controller.snapshot().busy,false);assert.equal(controller.snapshot().selected.id,'a');
});
test('collapse is available during a pending expansion and invalidates its UI completion',async()=>{
  let finish,collapsed=0;const {controller}=scene({expand:()=>new Promise(resolve=>finish=resolve),collapse:()=>collapsed++,canCollapse:()=>false});
  const pending=controller.expand();assert.equal(controller.snapshot().canCollapse,true);controller.collapse();finish();await pending;
  assert.equal(collapsed,1);assert.equal(controller.snapshot().busy,false);
});
test('selecting a listed neighbor uses the existing adapter selection action',async()=>{
  const {controller,calls,b}=scene();await controller.open();controller.locate(b);assert.equal(calls.at(-1).selected,b.id);
});
test('focus clipping is exposed and an older asynchronous focus cannot override new controls',async()=>{
  const pending=[];const {controller,focus}=scene({focus:(id,options)=>new Promise(resolve=>pending.push(()=>resolve({nodeIds:new Set([id]),edgeIds:new Set(),truncated:options.hops===2,...options})))});
  const first=controller.toggleFocus(),second=controller.setHops(2);pending[1]();await second;pending[0]();await first;
  assert.equal(focus.filter(Boolean).length,1);assert.equal(controller.snapshot().focusInfo.truncated,true);
});
test('an older focus failure does not clear a newer successful focus',async()=>{
  const pending=[];const {controller}=scene({focus:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});
  const first=controller.toggleFocus(),second=controller.setHops(2);pending[1].resolve({nodeIds:new Set(['a']),edgeIds:new Set()});await second;
  pending[0].reject(Error('过期错误'));await first;assert.equal(controller.snapshot().focused,true);assert.equal(controller.snapshot().error,'');
});
test('async neighbor preparation exposes loading and closing clears it immediately',async()=>{
  let finish;const {controller}=scene({neighbors:()=>new Promise(resolve=>finish=resolve)});
  const pending=controller.open();assert.equal(controller.snapshot().loading,true);controller.close();assert.equal(controller.snapshot().loading,false);
  finish({items:[],count:0});await pending;assert.equal(controller.snapshot().loading,false);
});
test('clearing project data preserves the active graph mode while explicit local reset changes it',()=>{
  const {controller,adapter,a}=scene();controller.setContext({mode:'full',key:'p',revision:1,adapter,selected:a});controller.clear();
  assert.equal(controller.snapshot().mode,'full');assert.equal(controller.snapshot().selected,null);
  controller.clear({mode:'local'});assert.equal(controller.snapshot().mode,'local');
});
