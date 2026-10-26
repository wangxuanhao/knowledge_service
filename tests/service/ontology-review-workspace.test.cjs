const test=require('node:test');
const assert=require('node:assert/strict');
const review=require('../../knowledge_service/web/ontology-review-workspace.js');
const item=(id,target,extra={})=>({operation_id:id,target_iri:target,action:'add_annotation',approvable:true,auto_approvable:true,...extra});
function fixture(extra={}){
  let context={projectId:'p',draftId:'d',revision:2,preview:false},writes=[];
  let draft={id:'d',revision:2,status:'pending',submitted_at:'now',base_ontology_id:'base',validation_fingerprint:'fp',
    ontology:{classes:[{id:'urn:A',label_zh:'甲'}],relations:[{id:'urn:B',label:'乙'}]},operations:[]};
  let plan={revision:2,validation_fingerprint:'fp',warnings:[],items:[item('a','urn:A'),item('b','urn:A'),item('c','urn:B')]};
  const controller=review.createController({getContext:()=>context,onSaved:fresh=>{draft=fresh;context={...context,revision:fresh.revision};},
    request:async(route,body,method)=>{
      if(extra.request){const value=await extra.request(route,body,method);if(value!==undefined)return value;}
      if(method==='POST'){
        writes.push({route,body});draft={...draft,revision:draft.revision+1};
        plan={...plan,revision:draft.revision,items:plan.items.map(row=>row.target_iri===body.target_iri?{...row,decision:{action:body.action}}:row)};
        return {draft};
      }
      return route.endsWith('/review-plan')?plan:route.includes('/stage-availability')?{stages:{review:{allowed:true}}}:draft;
    }});
  return {controller,writes,setContext:c=>context=c,setPlan:p=>plan=p,getPlan:()=>plan};
}
test('188 operations group into 60 exact definitions with readable identity',()=>{
  const items=Array.from({length:188},(_,i)=>item('op'+i,'urn:type:'+i%60));
  const groups=review.groupDefinitions({ontology:{classes:[{id:'urn:type:0',label:'客户'}]}},{items});
  assert.equal(groups.length,60);assert.equal(groups.reduce((n,g)=>n+g.items.length,0),188);
  assert.equal(groups[0].label,'客户');assert.equal(groups[0].kind,'实体类型');
});

test('a prior request to edit is still pending until an approve or reject decision',()=>{
  const groups=review.groupDefinitions({},{items:[item('a','urn:A',{decision:{action:'request_changes',reason:'补充定义'}})]});
  assert.equal(groups[0].pending.length,1);assert.equal(groups[0].approved,0);assert.equal(groups[0].rejected,0);
});
test('definition decision is one server-owned command and moves to next pending definition',async()=>{
  const f=fixture();await f.controller.load();assert.equal(f.writes.length,0);
  await f.controller.decide('approve',{next:true});
  assert.equal(f.writes.length,1);assert.ok(f.writes[0].route.endsWith('/decisions/by-definition'));
  assert.deepEqual(f.writes[0].body,{expected_revision:2,expected_ontology_id:'base',validation_fingerprint:'fp',acknowledged_warning_codes:[],target_iri:'urn:A',action:'approve',reason:''});
  assert.equal(f.controller.state.activeIri,'urn:B');assert.equal(f.controller.state.draft.revision,3);
});
test('reason, warning and blocked safeguards apply before a definition decision',async()=>{
  const f=fixture();f.setPlan({...f.getPlan(),warnings:[{code:'warn',message:'确认影响'}],items:[item('a','urn:A',{requires_reason:true})]});
  await f.controller.load();
  await assert.rejects(f.controller.decide('approve'),/理由/);
  await assert.rejects(f.controller.decide('approve',{reason:'已核对'}),/警告/);
  assert.equal(f.writes.length,0);
  await f.controller.decide('approve',{reason:'已核对',acknowledged:['warn']});assert.equal(f.writes.length,1);
  const blocked=fixture();blocked.setPlan({...blocked.getPlan(),items:[item('a','urn:A',{approvable:false})]});await blocked.controller.load();
  await assert.rejects(blocked.controller.decide('approve'),/阻断/);assert.equal(blocked.writes.length,0);
});
test('uncertain write requires a read-only reload before another decision',async()=>{
  const f=fixture({request:async(_route,_body,method)=>{if(method==='POST')throw Error('connection lost');}});
  await f.controller.load();await assert.rejects(f.controller.decide('approve'),/connection lost/);
  assert.equal(f.controller.state.uncertain,true);await assert.rejects(f.controller.decide('approve'),/重新读取/);
  await f.controller.load();assert.equal(f.controller.state.uncertain,false);
});
test('project switches and closing invalidate delayed review reads',async()=>{
  for(const close of [false,true]){
    let release;const waiting=new Promise(done=>release=done);
    const f=fixture({request:async()=>{await waiting;}}),loading=f.controller.load();
    if(close)f.controller.close();else f.setContext({projectId:'other',draftId:'d',revision:2});release();await loading;
    assert.equal(f.controller.state.draft,null);
  }
});
test('bulk command delegates eligibility to server without operation lists',async()=>{
  const f=fixture();await f.controller.load();await f.controller.bulkApprove();
  assert.ok(f.writes[0].route.endsWith('/decisions/batch-approve'));
  assert.equal(f.writes[0].body.decisions,undefined);assert.equal(f.writes[0].body.target_iri,undefined);
});

test('a completed write after a revision change never updates the current draft',async()=>{
  let release;const waiting=new Promise(done=>release=done),f=fixture({request:async(_route,_body,method)=>{if(method==='POST'){await waiting;return {draft:{id:'d',revision:3,status:'pending'}};}}});
  await f.controller.load();const saving=f.controller.decide('approve');f.setContext({projectId:'p',draftId:'d',revision:9});release();await saving;
  assert.equal(f.controller.state.draft.revision,2);
});

test('rejecting a definition sends acknowledged warnings and stops at a terminal draft',async()=>{
  const f=fixture({request:async(_route,_body,method)=>method==='POST'?{draft:{id:'d',revision:3,status:'rejected'}}:undefined});
  f.setPlan({...f.getPlan(),warnings:[{code:'warn',message:'确认影响'}]});await f.controller.load();
  await assert.rejects(f.controller.decide('reject',{reason:'重复定义'}),/警告/);
  await f.controller.decide('reject',{reason:'重复定义',acknowledged:['warn']});
  assert.equal(f.controller.state.draft.status,'rejected');assert.equal(f.controller.state.uncertain,false);assert.equal(f.controller.state.groups.length,0);
});

test('a remotely changed definition cannot replace only the revision while the canvas remains stale',async()=>{
  const f=fixture({request:async(route,_body,method)=>{
    if(method==='GET'&&route==='')return {id:'d',revision:6,status:'pending',submitted_at:'now',ontology:{classes:[{id:'urn:New'}]}};
    if(route==='/review-plan')return {revision:6,validation_fingerprint:'fp',items:[item('new','urn:New')]};
  }});
  await f.controller.load();assert.equal(f.controller.state.draft,null);assert.match(f.controller.state.error,/其他位置|修订/);
});

test('reload confirms a terminal rejection even when terminal review-plan is unavailable',async()=>{
  let committed=false;
  const f=fixture({request:async(route,_body,method)=>{
    if(method==='POST'){committed=true;throw Error('response lost');}
    if(committed&&route==='')return {id:'d',revision:3,status:'rejected'};
    if(committed&&route==='/review-plan')throw Error('terminal draft has no plan');
  }});
  await f.controller.load();await assert.rejects(f.controller.decide('reject',{reason:'重复'}),/response lost/);
  await f.controller.load();assert.equal(f.controller.state.draft.status,'rejected');assert.equal(f.controller.state.uncertain,false);
});

test('a bulk no-op response cannot install a remotely changed definition snapshot',async()=>{
  const f=fixture({request:async(_route,_body,method)=>method==='POST'?{draft:{id:'d',revision:8,status:'pending',ontology:{classes:[{id:'urn:Remote'}]},operations:[]},batch:{approved:0}}:undefined});
  await f.controller.load();await assert.rejects(f.controller.bulkApprove(),/其他位置|定义/);
  assert.equal(f.controller.state.draft.revision,2);assert.equal(f.controller.state.uncertain,true);
});
