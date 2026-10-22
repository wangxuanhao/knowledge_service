const test=require('node:test');
const assert=require('node:assert/strict');
const {createController}=require('../../knowledge_service/web/entity-mindmap.js');
function harness(reply){
  let project='p',generation=0;const calls=[],prepared=[],rendered=[];
  const controller=createController({getProject:()=>project,getScope:()=>({known_at:'2025-01-01',filters:{field:'metadata.city',op:'eq',value:'杭州'},ontology_scope:'ids',ontology_ids:['release']}),
    ticket:()=>generation,accepts:ticket=>ticket===generation,api:async(url,body)=>{calls.push({url,body});return reply?await reply(url,body):url.endsWith('/records/page')?{records:[{id:'deep',kind:'entity',text:'深页实体'}]}:{tree:{id:'deep',text:'深页实体',children:[]}};},prepare:row=>prepared.push(row),render:result=>rendered.push(result)});
  return {controller,calls,prepared,rendered,switchScope:()=>generation++,switchProject:()=>project='q'};
}
test('detail-to-mindmap uses exact entity ID and applied search scope independently of picker page',async()=>{
  const h=harness();await h.controller.open({id:'deep'},4);
  assert.deepEqual(h.calls[0].body.record_ids,['deep']);assert.equal(h.prepared[0].id,'deep');
  assert.equal(h.calls[1].body.root_id,'deep');assert.equal(h.calls[1].body.depth,4);
  assert.equal(h.calls[1].body.filters.value,'杭州');assert.deepEqual(h.calls[1].body.ontology_ids,['release']);assert.equal(h.rendered.length,1);
});
test('out-of-scope root is explained and never sent to mindmap',async()=>{
  const h=harness(()=>({records:[]}));await assert.rejects(()=>h.controller.open({id:'outside'},3),/当前.*范围/);assert.equal(h.calls.length,1);assert.equal(h.prepared.length,0);
});
test('late exact entity response cannot navigate after project or scope changes',async()=>{
  for(const change of ['switchProject','switchScope']){let finish;const h=harness(()=>new Promise(resolve=>finish=resolve));const pending=h.controller.open({id:'deep'},3);h[change]();finish({records:[{id:'deep',kind:'entity'}]});await pending;assert.equal(h.prepared.length,0);assert.equal(h.rendered.length,0);}
});
test('new root and invalidate discard stale mindmap responses',async()=>{
  let finish;const h=harness(()=>new Promise(resolve=>finish=resolve));const pending=h.controller.draw('deep',3);h.controller.invalidate();finish({tree:{id:'deep'}});await pending;assert.equal(h.rendered.length,0);
});
test('depth must be an integer from zero through five',async()=>{
  for(const depth of [-1,6,1.5,'']){const h=harness();await assert.rejects(()=>h.controller.draw('deep',depth),/0.*5/);assert.equal(h.calls.length,0);}
});
