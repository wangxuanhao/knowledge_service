const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const web=path.resolve(__dirname,'../../knowledge_service/web');
test('manual ontology review leaves the audit actor to the server',async()=>{
  const review=require('../../knowledge_service/web/ontology-review-workspace.js');
  const draft={id:'draft',revision:2,status:'pending',submitted_at:'now',base_ontology_id:'ontology'};
  const payloads=[];
  const controller=review.createController({getContext:()=>({projectId:'project',draftId:'draft',revision:2}),
    request:async(route,payload,method)=>{
      if(method==='POST'){payloads.push(payload);return {draft};}
      if(route.endsWith('/review-plan'))return {revision:2,validation_fingerprint:'fingerprint',items:[{
        action:'create_term',target_iri:'urn:A',operation_id:'operation',approvable:true}]};
      if(route.includes('/stage-availability'))return {stages:{review:{allowed:true}}};
      return draft;
    }});
  await controller.load();await controller.decide('approve');
  assert.equal(payloads.length,1);
  assert.equal(Object.hasOwn(payloads[0],'actor'),false);
  assert.equal(payloads[0].expected_revision,2);
  assert.equal(payloads[0].action,'approve');
});

test('ontology panel cache version includes the canonical actor contract',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.ok(html.includes('/assets/ontology-model-panel.js?v=attribute-editing-20261028-1'));
});
