const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.join(__dirname,'../../knowledge_service/web/ontology-intake.js');
const intake=fs.existsSync(file)?require(file):{};
function fixture(options={}){
  assert.equal(typeof intake.createController,'function','intake exposes a testable selection controller');
  const context={project:'p',draftId:'draft',revision:2,ontologyId:'release',preview:false},calls=[];
  let writes=0;
  const controller=intake.createController({getContext:()=>({...context}),uid:()=> 'request-1',canWrite:options.canWrite,storage:options.storage,getActor:options.getActor,requireDurable:options.requireDurable,
    request:async(path,body,method)=>{calls.push({path,body,method});
      if(path.startsWith('/candidates'))return {items:options.pages?(path.includes('offset=0')?[{id:'a',kind:'entity',proposed_type:'客户'}]:[{id:'b',kind:'entity'}]):[{id:'a',kind:'entity',proposed_type:'客户'},{id:'b',kind:'entity'}],total:2,expected_ontology_id:'release',published_terms:[{iri:'urn:Customer',label:'客户',kind:'class'}]};
      if(path==='/selection-preview')return {candidate_ids:body.candidate_ids,preview_fingerprint:'preview',source_fingerprint:'source',can_add:true,can_confirm_reuse:true,suggestions:[],associated_fact_scope:body.candidate_ids};
      writes++;if(options.failFirst&&writes===1)throw Error('无法连接');
      if(options.secondStatus&&writes===2){const error=Error('登录状态已过期');error.status=options.secondStatus;throw error;}
      return {draft:{id:'draft',revision:3},receipt:{facts:{selected:body.candidate_ids.length,added:1,reused:0,skipped:0}}};
    }});
  return {controller,context,calls};
}
test('inventory and preview never prepare or mutate a draft',async()=>{
  const {controller:c,calls}=fixture();await c.open();c.select('a',true);c.target('draft');await c.preview();
  assert.deepEqual(calls.map(x=>x.path),['/candidates?offset=0&limit=100','/selection-preview']);
  assert.equal(calls[1].body.target_draft_id,'draft');assert.equal(calls[1].body.expected_revision,2);
  assert.deepEqual(calls[1].body.candidate_ids,['a']);
});
test('candidate selection change invalidates an old preview',async()=>{
  const {controller:c,calls}=fixture();await c.open();c.select('a',true);c.target('draft');await c.preview();c.select('b',true);
  await assert.rejects(c.apply('add'),/预览/);assert.equal(calls.length,2);
});
test('project and revision changes cannot submit a stale preview',async()=>{
  for(const key of ['project','revision','ontologyId','preview']){
    const {controller:c,context,calls}=fixture();await c.open();c.select('a',true);c.target('draft');await c.preview();
    context[key]=key==='preview'?true:'changed';await assert.rejects(c.apply('add'),/变化|只读/);assert.equal(calls.length,2);
  }
});
test('unknown write outcomes retry the identical request identity and body',async()=>{
  const {controller:c,calls}=fixture({failFirst:true});await c.open();c.select('a',true);c.target('draft');await c.preview();
  await assert.rejects(c.apply('add'),/无法连接/);await c.apply('add');
  const writes=calls.filter(x=>x.path==='/add-selection');assert.equal(writes.length,2);assert.deepEqual(writes[0].body,writes[1].body);
  assert.equal(writes[1].body.idempotency_key,'request-1');
});
test('reuse requires an explicit mapping to a published term for every selected candidate',async()=>{
  const {controller:c,calls}=fixture();await c.open();c.select('a',true);c.target('reuse');
  assert.throws(()=>c.mapping('a','urn:DraftOnly'),/正式/);
  await assert.rejects(c.preview(),/映射/);c.mapping('a','urn:Customer');await c.preview();await c.apply('reuse');
  const body=calls.at(-1).body;assert.equal(calls.at(-1).path,'/confirm-reuse');
  assert.equal(body.target_draft_id,null);assert.equal(body.create_new_draft,false);assert.deepEqual(body.confirmed_mappings,{a:'urn:Customer'});
});
test('a scoped opening filters actual candidate identities and preserves source context',async()=>{
  const {controller:c}=fixture();await c.open({documentId:'doc',batchId:'batch',candidateIds:['b']});
  assert.deepEqual(c.state.items.map(x=>x.id),['b']);assert.equal(c.state.scope.documentId,'doc');assert.equal(c.state.scope.batchId,'batch');
});
test('an unknown result remains recoverable after visiting another project',async()=>{
  const {controller:c,context,calls}=fixture({failFirst:true});await c.open();c.select('a',true);c.target('draft');await c.preview();
  await assert.rejects(c.apply('add'));c.close();context.project='other';await c.open();c.close();context.project='p';await c.open();
  assert.equal(c.state.pending.body.idempotency_key,'request-1');await c.apply('add');
  assert.deepEqual(calls.filter(x=>x.path==='/add-selection').map(x=>x.body.idempotency_key),['request-1','request-1']);
});

test('receipt reports actual independent fact and index outcomes',()=>{
  assert.match(intake.receiptText({facts:{selected:5,added:2,reused:1,skipped:2},index:{status:'queued'}}),/新增 2 \/ 复用 1 \/ 跳过 2.*排队/);
  assert.match(intake.receiptText({facts:{},index:{}}),/待核对/);
});

function uiFixture(options={}){
  const vm=require('node:vm'),rootNode=element('body'),drawers=[],calls=[];
  function element(tag){return {tag,children:[],value:'',classList:{add(){}},setAttribute(){},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},focus(){}};}
  const state={ontologyId:'release',draft:{id:'draft',status:'pending',revision:2}};
  const candidates=options.candidates||[{id:'a',text:'张三',kind:'entity',proposed_type:'客户',document_id:'doc',document_version_id:'version',evidence:'原文证据'}];
  const window={document:{createElement:element,body:rootNode,getElementById:()=>null},Auth:{isAdmin:()=>true},
    OntologyModel:{S:state,uid:()=> 'ui-key',refresh:options.refresh||(async()=>{})},WorkspaceDrawers:{create(config){const drawer={dialog:element('dialog'),open(){this.isOpen=true;},close(){this.isOpen=false;config.onClose();},...config};drawers.push(drawer);return drawer;}}};
  const context={window,current:'p',setTimeout,clearTimeout,console,api:async(url,body,method)=>{calls.push({url,body,method});
    if(url.endsWith('/ontology-drafts'))return {items:[{id:'draft',status:'pending',revision:2,title:'手工草案'}]};
    if(url.includes('/candidates'))return {items:candidates,total:candidates.length,expected_ontology_id:'release',published_terms:[]};
    if(url.endsWith('/selection-preview'))return {can_add:true,suggestions:[],associated_fact_scope:candidates.filter(item=>body.candidate_ids.includes(item.id)),fact_outcomes:candidates.map(item=>({candidate_id:item.id,outcome:'pending',message:'依赖本轮结构审核通过'})),preview_fingerprint:'fp',source_fingerprint:'src',...options.previewResponse};
    return {};
  }};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),context);
  const all=node=>[node,...node.children.flatMap(all)];
  return {ui:window.OntologyIntake,drawers,calls,all,context,find:text=>all(drawers[0].content).find(item=>item.textContent===text)};
}
test('drawer previews real selected candidate identities and explicit target without draft creation',async()=>{
  const f=uiFixture();await f.ui.open({projectId:'p',documentId:'doc'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();
  await f.find('预览选中内容（只读）').onclick();
  const preview=f.calls.find(call=>call.url.endsWith('/selection-preview'));
  assert.deepEqual(JSON.parse(JSON.stringify(preview.body.candidate_ids)),['a']);assert.equal(preview.body.target_draft_id,'draft');
  assert.ok(f.find('加入本轮修改'));assert.ok(!f.calls.some(call=>call.method==='POST'&&call.url.endsWith('/ontology-drafts')));
});
test('closing during preparation cannot reopen or populate a stale drawer',async()=>{
  let resolve;const f=uiFixture({refresh:()=>new Promise(done=>{resolve=done;})});const opening=f.ui.open({projectId:'p'});
  f.drawers[0].close();resolve();await opening;
  assert.equal(f.ui.state.open,false);assert.equal(f.calls.length,0);
});
test('a revoked write role prevents confirmation after a valid preview',async()=>{
  let allowed=true;const {controller:c,calls}=fixture({canWrite:()=>allowed});await c.open();c.select('a',true);c.target('draft');await c.preview();
  allowed=false;await assert.rejects(c.apply('add'),/权限|管理员/);assert.ok(!calls.some(x=>x.path==='/add-selection'));
});
test('reload restores the exact unresolved request for its project and actor without automatically writing',async()=>{
  const values=new Map(),storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const first=fixture({failFirst:true,storage,getActor:()=> 'user-1'});await first.controller.open();first.controller.select('a',true);first.controller.target('draft');await first.controller.preview();
  await assert.rejects(first.controller.apply('add'));
  const next=fixture({storage,getActor:()=> 'user-1'});await next.controller.open();
  assert.equal(next.calls.length,0,'restoring a request must not apply or rebuild it');
  assert.equal(next.controller.state.pending.body.idempotency_key,'request-1');
  const other=fixture({storage,getActor:()=> 'user-2'});await other.controller.open();assert.equal(other.controller.state.pending,null);assert.equal(values.size,1,'another actor cannot clear the owner recovery record');
  await next.controller.apply('add');assert.equal(values.size,0);
});
test('loading another page invalidates a preview when pinned selections expand',async()=>{
  const {controller:c,calls}=fixture({pages:true});await c.open({candidateIds:['a','b']});c.target('draft');await c.preview();await c.more();
  assert.equal(c.state.preview,null);assert.deepEqual(c.state.selected,['a','b']);await assert.rejects(c.apply('add'),/预览/);
  assert.ok(!calls.some(call=>call.path==='/add-selection'));
});
test('dependency diagnostics map exact source IDs to readable fact descriptions',()=>{
  assert.equal(typeof intake.diagnosticText,'function');
  const items=[{id:'entity-a',kind:'entity',text:'张三',proposed_type:'客户'},{id:'rel',kind:'relation',subject:'张三',object:'订单甲',proposed_type:'拥有'}];
  const text=intake.diagnosticText({candidate_id:'entity-a',required_by:'rel',message:'请选择同批实体'},items);
  assert.match(text,/张三.*客户/);assert.match(text,/张三.*拥有.*订单甲/);assert.match(text,/请选择同批实体/);
});
test('fact preview shows the actual selected value with its server-predicted outcome and reason',async()=>{
  const f=uiFixture({candidates:[{id:'a',kind:'entity',text:'张三',proposed_type:'客户'},
    {id:'amount',kind:'attribute',subject:'张三',entity_id:'a',proposed_type:'金额',value:42}],
    previewResponse:{fact_outcomes:[{candidate_id:'a',outcome:'pending',message:'需要结构操作获批'},
      {candidate_id:'amount',outcome:'skip',reason_code:'datatype_mismatch',message:'属性值未通过数据类型校验'}]}});
  await f.ui.open({projectId:'p'});
  for(const checkbox of f.all(f.drawers[0].content).filter(item=>item.type==='checkbox')){checkbox.checked=true;await checkbox.onchange();}
  await f.find('预览选中内容（只读）').onclick();
  assert.ok(f.find('逐条关联知识预期结果'));
  assert.ok(f.find('预计跳过 · 属性值未通过数据类型校验'));
  assert.ok(f.all(f.drawers[0].content).some(item=>item.textContent==='张三 · 金额 = 42'));
});

test('structure preview names candidate kinds and operations in Chinese while retaining exact before and after details',async()=>{
  const f=uiFixture({candidates:[{id:'a',kind:'entity',text:'张三',proposed_type:'客户'},
    {id:'owns',kind:'relation',subject:'张三',object:'订单甲',proposed_type:'拥有'},
    {id:'amount',kind:'attribute',subject:'张三',proposed_type:'金额',value:42}],previewResponse:{
      new:[{candidate_id:'a',target_iri:'urn:Customer'},{candidate_id:'amount',target_iri:'urn:Amount'}],
      reused:[{candidate_id:'owns',target_iri:'urn:Owns'}],suggestions:[
        {action:'create_term',target_iri:'urn:Customer',before:null,after:{kind:'class'}},
        {action:'add_annotation',target_iri:'urn:Customer',before:null,after:{predicate:'http://www.w3.org/2000/01/rdf-schema#label',value:'客户',language:'zh',datatype:null}},
        {action:'add_domain',target_iri:'urn:Owns',before:null,after:{value:'urn:Customer'}},
        {action:'set_datatype',target_iri:'urn:Amount',before:{datatype:null},after:{datatype:'http://www.w3.org/2001/XMLSchema#integer'}},
        {action:'future_operation',target_iri:'urn:Unknown',after:{unfamiliar:'exact-value'}}]}});
  await f.ui.open({projectId:'p'});
  for(const kind of ['实体','关系','属性'])assert.ok(f.find(kind),kind+' has a readable candidate badge');
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();
  await f.find('预览选中内容（只读）').onclick();
  for(const heading of ['新增定义 · 客户','名称说明 · 客户','所属类 · 拥有','数据类型 · 金额'])assert.ok(f.find(heading),heading);
  assert.ok(f.all(f.drawers[0].content).some(item=>item.textContent==='修改前\n数据类型：空值（null）\n修改后\n数据类型：整数'));
  assert.ok(f.all(f.drawers[0].content).some(item=>item.textContent==='修改前\n未提供\n修改后\nunfamiliar：exact-value'));
  const raw=f.all(f.drawers[0].content).filter(item=>item.tag==='pre').map(item=>item.textContent||'').join('\n');
  assert.match(raw,/urn:Customer/);assert.match(raw,/future_operation/);assert.match(raw,/http:\/\/www.w3.org\/2001\/XMLSchema#integer/);
});
test('a response missing per-fact outcomes cannot enable confirmation',async()=>{
  const f=uiFixture({previewResponse:{fact_outcomes:[]}});await f.ui.open({projectId:'p'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();await f.find('预览选中内容（只读）').onclick();
  assert.equal(f.find('加入本轮修改').disabled,true);
});
test('recovery persistence failure prevents a write before it can become uncertain',async()=>{
  const {controller:c,calls}=fixture({requireDurable:true,storage:{getItem:()=>null,setItem(){throw Error('quota');}}});
  await c.open();c.select('a',true);c.target('draft');await c.preview();await assert.rejects(c.apply('add'),/恢复记录保存失败/);
  assert.equal(c.state.pending,null);assert.ok(!calls.some(call=>call.path==='/add-selection'));
});
test('an expired login during unknown-result replay cannot discard its recovery identity',async()=>{
  const {controller:c}=fixture({failFirst:true,secondStatus:401});await c.open();c.select('a',true);c.target('draft');await c.preview();
  await assert.rejects(c.apply('add'));await assert.rejects(c.apply('add'));
  assert.equal(c.state.pending.body.idempotency_key,'request-1');
});
test('an exact pending-fact scope retains its materialized owner as an explicit unchecked reuse dependency',async()=>{
  const f=uiFixture({candidates:[{id:'a',kind:'entity',text:'张三',proposed_type:'客户',materialized:true,dependency_candidate:true,required_by:['amount']},
    {id:'amount',kind:'attribute',subject:'张三',entity_id:'a',proposed_type:'金额',value:42}]});
  await f.ui.open({projectId:'p',candidateIds:['amount']});
  assert.deepEqual(JSON.parse(JSON.stringify(f.ui.state.items.map(item=>item.id))),['a','amount']);
  assert.deepEqual(JSON.parse(JSON.stringify(f.ui.state.selected)),['amount']);
  assert.ok(f.find('已写入正式知识 · 本次可明确选择为依赖并复用，仍需确认准确正式术语映射。'));
  assert.ok(f.all(f.drawers[0].content).some(item=>String(item.textContent||'').includes('张三 · 金额 = 42')));
});
