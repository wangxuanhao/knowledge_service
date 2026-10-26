const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const data=require('../../knowledge_service/web/ontology-draft-data.js');

test('review transport restores identical evidence including duplicate and unresolved references',()=>{
  const refs=['candidate:a',{ref:'document-version:doc:v1'},'candidate:a','unknown:kept'];
  const payload={id:'draft',revision:4,evidence_sets:{shared:refs},operations:[
    {id:'one',fingerprint:'fp1',evidence_set_id:'shared',evidence_count:4},
    {id:'two',fingerprint:'fp2',evidence_set_id:'shared',evidence_count:4}]};
  const before=JSON.stringify(payload),decoded=data.decode(payload);
  assert.deepEqual(decoded.operations[0],{id:'one',fingerprint:'fp1',evidence:refs});
  assert.equal(decoded.operations[0].evidence,decoded.operations[1].evidence);
  assert.equal(JSON.stringify(payload),before);assert.equal(decoded.evidence_sets,undefined);
});

test('invalid compact evidence never silently becomes an empty audit source',()=>{
  assert.throws(()=>data.decode({id:'draft',evidence_sets:{},operations:[{evidence_set_id:'missing',evidence_count:2}]}),/证据|evidence/);
  assert.throws(()=>data.decode({id:'draft',evidence_sets:{one:['a']},operations:[{evidence_set_id:'one',evidence_count:2}]}),/证据|evidence/);
});

test('enveloped decisions decode the draft while retaining the exact server receipt',()=>{
  const receipt={decided:188,operation_ids:['one']},payload={definition:receipt,draft:{id:'d',revision:8,
    evidence_sets:{empty:[]},operations:[{id:'one',evidence_set_id:'empty',evidence_count:0}]}};
  const decoded=data.decode(payload);assert.equal(decoded.definition,receipt);
  assert.deepEqual(decoded.draft.operations[0].evidence,[]);assert.equal(decoded.draft.revision,8);
  const legacy={id:'d',draft:{id:'d'},operations:[{evidence:['old']}]};assert.equal(data.decode(legacy),legacy);
});

test('browser adapter requests the compact view and leaves optimistic guards unchanged',async()=>{
  const calls=[],context={window:{},api:async(...args)=>{calls.push(args);return {draft:{id:'d',evidence_sets:{},operations:[]},batch:{approved:188}};}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/ontology-draft-data.js'),'utf8'),context);
  const body={expected_revision:4,validation_fingerprint:'fp',acknowledged_warning_codes:[]};
  const result=await context.window.OntologyDraftData.request('/p/ontology-drafts/d/decisions/batch-approve',body,'POST');
  assert.equal(calls[0][0],'/p/ontology-drafts/d/decisions/batch-approve?view=review');
  assert.equal(calls[0][1],body);assert.equal(calls[0][2],'POST');assert.equal(result.batch.approved,188);
  await context.window.OntologyDraftData.request('/p/ontology-drafts/d?view=canvas',undefined,'GET');
  assert.equal(calls[1][0],'/p/ontology-drafts/d?view=canvas');
});
