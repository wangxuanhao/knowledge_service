const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.join(__dirname,'../../knowledge_service/web/ontology-intake.js');
const intake=fs.existsSync(file)?require(file):{};
function appendCandidates(){
  const source={document_id:'doc',document_version_id:'source-v1'};
  return [
    {...source,id:'bound-owner',kind:'entity',text:'原草案商户',proposed_type:'商户',required_by:['next-relation'],linked_drafts:[{draft_id:'draft',status:'draft_created'}]},
    {...source,id:'new-place',kind:'entity',text:'新增门店',proposed_type:'门店'},
    {...source,id:'next-relation',kind:'relation',text:'原草案商户 → 经营 → 新增门店',proposed_type:'经营',subject_id:'bound-owner',object_id:'new-place'},
  ];
}

test('appending relations reuses only their same-draft source entities in preview and confirmation',async()=>{
  const items=appendCandidates();items.push({...items[0],id:'unrelated-owner',required_by:[]});
  const {controller:c,calls}=fixture({items});await c.open();c.selectMany(['new-place','next-relation'],true);
  await c.preview();
  const body=calls.find(call=>call.path==='/selection-preview').body;
  assert.deepEqual(body.candidate_ids,['bound-owner','new-place','next-relation']);
  assert.equal(body.target_draft_id,'draft');assert.equal(body.expected_revision,2);
  assert.deepEqual(c.state.selected,['new-place','next-relation'],'the explicit selection remains distinct from reused context');
  assert.equal(calls.filter(call=>call.path==='/add-selection').length,0);
  await c.apply('add');assert.deepEqual(calls.at(-1).body.candidate_ids,body.candidate_ids);
});

test('same-draft attribute owners are reused once even when several selected facts depend on them',async()=>{
  const items=appendCandidates();items.push({...items[2],id:'amount',kind:'attribute',entity_id:'bound-owner',proposed_type:'金额',value:9});
  const {controller:c,calls}=fixture({items});await c.open();c.selectMany(['new-place','next-relation','amount'],true);await c.preview();
  assert.deepEqual(calls.at(-1).body.candidate_ids,['amount','bound-owner','new-place','next-relation']);
});

test('same-draft dependencies retain their recorded published mapping without guessing from suggestions',async()=>{
  const items=appendCandidates();items[0].linked_drafts[0].confirmed_mapping={target_iri:'urn:Original',target_kind:'class'};
  items[0].suggested_mapping={target_iri:'urn:Suggested',target_kind:'class'};
  const terms=[{iri:'urn:Original',kind:'class'},{iri:'urn:Suggested',kind:'class'}];
  const {controller:c,calls}=fixture({items,terms});await c.open();c.selectMany(['new-place','next-relation'],true);await c.preview();
  assert.deepEqual(calls.at(-1).body.confirmed_mappings,{'bound-owner':'urn:Original'});
  assert.deepEqual(c.state.mappings,{},'recorded dependency context must not alter explicit user choices');
});

test('refreshing an inherited draft mapping invalidates the older preview',async()=>{
  const initial=appendCandidates();initial[0].linked_drafts[0].confirmed_mapping={target_iri:'urn:Original',target_kind:'class'};
  const refreshed=JSON.parse(JSON.stringify(initial[0]));refreshed.linked_drafts[0].confirmed_mapping.target_iri='urn:Updated';
  const {controller:c}=fixture({readCandidates:route=>({
    items:route.includes('offset=0')?initial:[refreshed],total:4,expected_ontology_id:'release',
    published_terms:[{iri:'urn:Original',kind:'class'},{iri:'urn:Updated',kind:'class'}]})});
  await c.open();c.selectMany(['new-place','next-relation'],true);await c.preview();await c.more();
  assert.equal(c.state.preview,null);
  await assert.rejects(c.apply('add'),/预览/);
});

test('draft dependency reuse follows the explicit destination and never crosses draft or source identity',async()=>{
  for(const mode of ['new','other','reuse','other-link','other-document','other-version','unknown-version']){
    const items=appendCandidates();
    if(mode==='other-link')items[0].linked_drafts.push({draft_id:'other',status:'draft_created'});
    if(mode==='other-document')items[0].document_id='different-doc';
    if(mode==='other-version')items[0].document_version_id='old-v';
    if(mode==='unknown-version')delete items[0].document_version_id;
    const {controller:c,calls}=fixture({items});await c.open();c.selectMany(['new-place','next-relation'],true);
    if(['new','other','reuse'].includes(mode))c.target(mode,5);
    if(mode==='reuse')for(const id of c.state.selected){c.state.mappings[id]='urn:explicit';}
    await c.preview();assert.deepEqual(calls.at(-1).body.candidate_ids,['new-place','next-relation'],mode);
  }
});

test('switching the destination invalidates preview and removes previous draft dependency context',async()=>{
  const {controller:c,calls}=fixture({items:appendCandidates()});await c.open();c.selectMany(['new-place','next-relation'],true);await c.preview();
  assert.ok(calls.at(-1).body.candidate_ids.includes('bound-owner'));
  c.target('new');await assert.rejects(c.apply('add'),/预览/);await c.preview();
  assert.deepEqual(calls.at(-1).body.candidate_ids,['new-place','next-relation']);
  c.target('draft',2);await c.preview();assert.ok(calls.at(-1).body.candidate_ids.includes('bound-owner'));
});

test('the drawer explains same-draft entity reuse and permits its complete fact preview',async()=>{
  const f=uiFixture({candidates:appendCandidates()});await f.ui.open({projectId:'p'});
  await f.find('选择当前列表（2 条）').onclick();
  assert.ok(f.find('将复用所选草案中已有的 1 条关联实体，与本次选择一起核对；不会重复创建实体。'));
  await f.find('预览选中内容（只读）').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(call=>call.url.endsWith('/selection-preview')).body.candidate_ids)),['bound-owner','new-place','next-relation']);
  assert.ok(f.find('本次选择 2 条候选，复用原草案关联实体 1 条，合计核对 3 条。'));
  assert.equal(f.find('确认加入草案，等待发布').disabled,false);
  assert.ok(!f.find('逐条事实范围或预期结果不完整，暂不能确认。请重新读取并预览。'));
});
function fixture(options={}){
  assert.equal(typeof intake.createController,'function','intake exposes a testable selection controller');
  const context={project:'p',draftId:'draft',revision:2,ontologyId:'release',preview:false},calls=[];
  let writes=0;
  const controller=intake.createController({getContext:()=>({...context}),uid:()=> 'request-1',canWrite:options.canWrite,storage:options.storage,getActor:options.getActor,requireDurable:options.requireDurable,
    request:async(path,body,method)=>{calls.push({path,body,method});
      if(path.startsWith('/candidates')&&options.readCandidates)return options.readCandidates(path);
      if(path.startsWith('/candidates'))return {items:options.items||(options.pages?(path.includes('offset=0')?[{id:'a',kind:'entity',proposed_type:'客户'}]:[{id:'b',kind:'entity'}]):[{id:'a',kind:'entity',proposed_type:'客户'},{id:'b',kind:'entity'}]),total:options.items?.length??2,expected_ontology_id:'release',scope_note:/[&?](batch_id|run_id)=/.test(path)?'来源范围已核对':null,published_terms:options.terms||[{iri:'urn:Customer',label:'客户',kind:'class'}]};
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
  function element(tag){return {tag,children:[],value:'',scrollTop:0,scrollCalls:[],classList:{add(){},toggle(){}},setAttribute(name,value){this[name]=value;},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;if(options.clampBodyOnReplace&&this.className==='ontology-intake')for(const drawer of drawers)drawer.body.scrollTop=0;},querySelector(selector){return selector.startsWith('.')?all(this).find(item=>String(item.className||'').split(' ').includes(selector.slice(1)))||null:null;},focus(){this.focused=true;window.document.activeElement=this;},getBoundingClientRect(){return options.rect?.(this,drawers[0])||{top:this.className==='workspace-drawer-body'?120:600};},scrollTo(options){this.scrollTop=options.top;this.scrollCalls.push(options);},scrollIntoView(){this.scrolled=true;for(const drawer of drawers)drawer.dialog.scrollTop=100;}};}
  const state={ontologyId:'release',draft:options.noDraft?null:{id:'draft',status:'pending',revision:2}};
  const candidates=options.candidates||[{id:'a',text:'张三',kind:'entity',proposed_type:'客户',document_id:'doc',document_version_id:'version',evidence:'原文证据'}];
  const values=new Map();
  const window={document:{createElement:element,body:rootNode,getElementById:id=>drawers.flatMap(drawer=>all(drawer.content)).find(item=>item.id===id)||null,dispatchEvent(){}},sessionStorage:{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},Auth:{isAdmin:()=>true},
    matchMedia:()=>({matches:!!options.reducedMotion}),OntologyModel:{S:state,uid:()=> 'ui-key',refresh:options.refresh||(async()=>{})},WorkspaceDrawers:{create(config){const body=element('div');body.className='workspace-drawer-body';const drawer={dialog:element('dialog'),body,open(){this.isOpen=true;},close(){this.isOpen=false;config.onClose();},...config};drawers.push(drawer);return drawer;}}};
  const context={window,current:'p',setTimeout,clearTimeout,console,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},api:async(url,body,method)=>{calls.push({url,body,method});
    if(url.endsWith('/ontology-drafts'))return {items:options.noDraft?[]:options.drafts||[{id:'draft',status:'pending',revision:2,title:'手工草案'}]};
    if(url.includes('/candidates'))return options.readCandidates?options.readCandidates(url):{items:candidates,total:candidates.length,documents:options.documents,expected_ontology_id:options.noOntology?null:'release',published_terms:options.terms||[]};
    if(url.endsWith('/selection-preview')){if(options.previewRead)await options.previewRead();return {can_add:true,suggestions:[],associated_fact_scope:candidates.filter(item=>body.candidate_ids.includes(item.id)),fact_outcomes:candidates.map(item=>({candidate_id:item.id,outcome:'pending',message:'依赖本轮结构审核通过'})),preview_fingerprint:'fp',source_fingerprint:'src',...options.previewResponse};}
    if(url.endsWith('/add-selection')&&options.confirmRead)return options.confirmRead(body);
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
  assert.ok(f.find('确认加入草案，等待发布'));assert.ok(!f.calls.some(call=>call.method==='POST'&&call.url.endsWith('/ontology-drafts')));
});

test('draft destination cards show new versus existing and preview only the explicit destination',async()=>{
  const f=uiFixture();await f.ui.open({projectId:'p'});
  const radio=value=>f.all(f.drawers[0].content).find(item=>item.type==='radio'&&item.name==='oi-draft-destination'&&item.value===value);
  assert.ok(f.find('新建一份草案'));assert.ok(f.find('加入已有草案'));assert.equal(radio('existing').checked,true);
  const existing=f.all(f.drawers[0].content).find(item=>item['aria-label']==='选择已有草案');assert.equal(existing.value,'draft');
  assert.ok(!existing.children.some(item=>item.value==='new'));
  await radio('new').onchange();assert.equal(f.ui.state.target,'new');assert.equal(radio('new').checked,true);
  assert.ok(!f.all(f.drawers[0].content).some(item=>item['aria-label']==='选择已有草案'));
  assert.ok(f.all(f.drawers[0].content).some(item=>/确认加入时才创建/.test(item.textContent||'')));
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();
  await f.find('预览选中内容（只读）').onclick();
  assert.equal(f.calls.find(call=>call.url.endsWith('/selection-preview')).body.create_new_draft,true);
  assert.ok(!f.calls.some(call=>call.url.endsWith('/add-selection')||call.method==='POST'&&call.url.endsWith('/ontology-drafts')));
  await f.find('返回调整选择').onclick();await radio('existing').onchange();assert.equal(f.ui.state.target,'draft');
});

test('existing destination without an active draft stays explicitly unselected across reopening',async()=>{
  const f=uiFixture({noDraft:true});await f.ui.open({projectId:'p'});
  const existing=()=>f.all(f.drawers[0].content).find(item=>item.type==='radio'&&item.name==='oi-draft-destination'&&item.value==='existing');
  assert.ok(existing());await existing().onchange();assert.equal(f.ui.state.target,'');assert.equal(existing().checked,true);
  assert.ok(f.find('当前没有可加入的工作草案，可选择「新建一份草案」。'));
  f.drawers[0].close();await f.ui.open({projectId:'p'});assert.equal(existing().checked,true);assert.equal(f.ui.state.target,'');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
});

test('explicit existing draft survives switching to new, reuse and reopening without selecting another draft',async()=>{
  const f=uiFixture({drafts:[{id:'draft',status:'pending',revision:2},{id:'other',status:'pending',revision:4}]});
  await f.ui.open({projectId:'p'});
  const destination=value=>f.all(f.drawers[0].content).find(item=>item.name==='oi-draft-destination'&&item.value===value);
  let select=f.all(f.drawers[0].content).find(item=>item['aria-label']==='选择已有草案');select.value='other';await select.onchange();
  assert.equal(f.ui.state.target,'other');destination('new').focus();await destination('new').onchange();
  assert.equal(f.context.window.document.activeElement,destination('new'),'repainting the destination preserves keyboard focus on the replacement radio');
  await destination('existing').onchange();
  assert.equal(f.ui.state.target,'other');assert.equal(f.ui.state.targetRevision,4);
  const goal=value=>f.all(f.drawers[0].content).find(item=>item.name==='oi-goal'&&item.value===value);
  await goal('reuse').onchange();await goal('structure').onchange();assert.equal(f.ui.state.target,'other');
  f.drawers[0].close();await f.ui.open({projectId:'p'});assert.equal(f.ui.state.target,'other');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
});

test('changing draft destination retains the candidate-stage body position after a content rebuild',async()=>{
  const f=uiFixture({clampBodyOnReplace:true,drafts:[{id:'draft',status:'pending',revision:2},{id:'other',status:'pending',revision:4}]});await f.ui.open({projectId:'p'});
  const {body,dialog}=f.drawers[0],destination=value=>f.all(f.drawers[0].content).find(item=>item.name==='oi-draft-destination'&&item.value===value);
  body.scrollTop=720;destination('new').focus();await destination('new').onchange();
  assert.equal(body.scrollTop,720,'switching to new must keep the destination cards at the same body position');
  destination('existing').focus();await destination('existing').onchange();assert.equal(body.scrollTop,720);
  const select=f.all(f.drawers[0].content).find(item=>item['aria-label']==='选择已有草案');select.focus();select.value='other';await select.onchange();
  assert.equal(body.scrollTop,720,'choosing an existing draft must not jump back to the candidate list');
  assert.equal(dialog.scrollTop,0,'only the content body owns scrolling');assert.equal(f.ui.state.target,'other');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
});

for(const goalHeight of [660,900])test(`destination expansion reveals the existing field above the sticky actions with a ${goalHeight}px goal`,async()=>{
  const f=uiFixture({rect(node,drawer){
    if(node.className==='workspace-drawer-body')return {top:100,bottom:950,height:850};
    if(node.className==='oi-actions')return {top:860,bottom:950,height:90};
    const offset=drawer?.body.scrollTop||0;
    if(node.id==='oi-processing-goal')return {top:1000-offset,bottom:1000+goalHeight-offset,height:goalHeight};
    if(node.id==='oi-existing-draft')return {top:1000+goalHeight-50-offset,bottom:1000+goalHeight-12-offset,height:38};
    return {top:600,bottom:620,height:20};
  }});await f.ui.open({projectId:'p'});
  const {body,dialog}=f.drawers[0],destination=value=>f.all(f.drawers[0].content).find(item=>item.name==='oi-draft-destination'&&item.value===value);
  body.scrollTop=720;destination('new').focus();await destination('new').onchange();
  destination('existing').focus();await destination('existing').onchange();
  const select=()=>f.all(f.drawers[0].content).find(item=>item['aria-label']==='选择已有草案');
  assert.ok(select().getBoundingClientRect().bottom<=856,'expanded field must be visible above the sticky footer before native selection');
  assert.ok(select().getBoundingClientRect().top>=104,'expanded field must remain below the drawer heading');
  assert.equal(f.context.window.document.activeElement,destination('existing'),'revealing the field retains native radio focus');
  const visibleOffset=body.scrollTop;select().focus();select().value='draft';await select().onchange();
  assert.equal(body.scrollTop,visibleOffset,'an already visible destination does not jump on selection');
  assert.equal(f.context.window.document.activeElement,select());assert.equal(dialog.scrollTop,0);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
});

test('candidate body scroll is not carried into the preview progress or new preview stage',async()=>{
  let finish;const f=uiFixture({previewRead:()=>new Promise(resolve=>finish=resolve)});await f.ui.open({projectId:'p'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();
  const {body,dialog}=f.drawers[0];body.scrollTop=720;const preview=f.find('预览选中内容（只读）').onclick();
  for(let i=0;i<10;i++)await Promise.resolve();
  try{assert.equal(body.scrollTop,0,'new progress feedback must start at the top of the content body');}
  catch(error){finish();await preview;throw error;}
  body.scrollTop=99;finish();await preview;
  assert.equal(body.scrollCalls.at(-1).top,480,'preview navigation starts from the new stage rather than an old selection offset');
  assert.equal(dialog.scrollTop,0);assert.ok(f.find('确认加入草案，等待发布'));
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
  assert.equal(f.find('确认加入草案，等待发布').disabled,true);
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

test('bulk selection is bounded to loaded identities and invalidates preview once selection changes',async()=>{
  const {controller:c,calls}=fixture();await c.open();c.selectMany(['a','b'],true);c.target('draft');await c.preview();
  assert.deepEqual(c.state.selected,['a','b']);
  assert.throws(()=>c.selectMany(['a','unread'],true),/已读|当前/);
  assert.deepEqual(c.state.selected,['a','b'],'invalid bulk input cannot partially change selection');
  c.mapping('a','urn:Customer');c.selectMany(['a'],false);
  assert.deepEqual(c.state.selected,['b']);assert.deepEqual(c.state.mappings,{});
  assert.equal(c.state.preview,null);await assert.rejects(c.apply('add'),/预览/);
  assert.ok(!calls.some(call=>call.path==='/add-selection'));
});

test('bulk suggestions confirm only compatible unique published suggestions and preserve manual mappings',async()=>{
  const items=[{id:'a',kind:'entity',suggested_mapping:{target_iri:'urn:Customer'}},
    {id:'b',kind:'entity',suggested_mapping:{target_iri:'urn:Customer'}},
    {id:'r',kind:'relation',suggested_mapping:{target_iri:'urn:Customer'}},
    {id:'x',kind:'entity',suggested_mapping:{target_iri:'urn:Missing'}},
    {id:'unselected',kind:'entity',suggested_mapping:{target_iri:'urn:Customer'}}];
  const {controller:c,calls}=fixture({items,terms:[{iri:'urn:Customer',kind:'class'}, {iri:'urn:Other',kind:'class'}]});
  await c.open();c.selectMany(['a','b','r','x'],true);c.mapping('a','urn:Other');
  c.useSuggestedMappings(['a','b','r','x','unselected']);
  assert.deepEqual(c.state.mappings,{a:'urn:Other',b:'urn:Customer'});
  assert.throws(()=>c.mapping('r','urn:Customer'),/种类|类型/);
  assert.equal(calls.length,1,'batch mapping never previews or confirms automatically');
});

test('reopening the same source refreshes candidates and restores selection without restoring an old preview',async()=>{
  const {controller:c,calls}=fixture();await c.open({documentId:'doc'});c.selectMany(['a','b'],true);c.mapping('a','urn:Customer');c.target('draft');await c.preview();c.close();
  await c.open({documentId:'other'});assert.deepEqual(c.state.selected,[]);c.close();
  await c.open({documentId:'doc'});assert.deepEqual(c.state.selected,['a','b']);assert.equal(c.state.mappings.a,'urn:Customer');assert.equal(c.state.preview,null);
  assert.equal(calls.filter(call=>call.path.startsWith('/candidates')).length,3);
});

test('candidate inventory request carries actual batch and task filters',async()=>{
  const {controller:c,calls}=fixture();await c.open({documentId:'doc',batchId:'batch/a',runId:'run?1'});
  assert.ok(calls[0].path.includes('&batch_id=batch%2Fa'));assert.ok(calls[0].path.includes('&run_id=run%3F1'));
});

test('goals explain direct knowledge confirmation versus draft publication in business terms',async()=>{
  const f=uiFixture();await f.ui.open({projectId:'p'});
  assert.ok(f.find('已有类型可用，直接确认知识'));
  assert.ok(f.find('需要补充定义，先加入草案'));
  assert.ok(f.all(f.drawers[0].content).some(item=>String(item.textContent||'').includes('审核发布后')));
  assert.equal(f.drawers[0].dismissOnBackdrop,false);
});

test('visible-list batch selection stays explicit and never submits automatically',async()=>{
  const f=uiFixture({candidates:[{id:'a',kind:'entity',text:'张三',document_id:'one',proposed_type:'客户'},
    {id:'b',kind:'entity',text:'李四',document_id:'two',proposed_type:'客户'}]});
  await f.ui.open({projectId:'p'});await f.find('选择当前列表（2 条）').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(f.ui.state.selected)),['a','b']);
  assert.ok(!f.calls.some(call=>call.method==='POST'));
  await f.find('清空全部选择').onclick();assert.equal(f.ui.state.selected.length,0);
});

test('candidates already awaiting draft publication cannot be selected or previewed again',async()=>{
  const linked={id:'b',kind:'entity',linked_drafts:[{draft_id:'draft',status:'draft_created'}]};
  const {controller:c,calls}=fixture({items:[{id:'a',kind:'entity'},linked]});
  await c.open({candidateIds:['a','b']});assert.deepEqual(c.state.selected,['a']);
  c.selectMany(['a'],false);
  assert.throws(()=>c.selectMany(['a','b'],true),/已加入草案/);
  assert.deepEqual(c.state.selected,[],'a rejected batch must not partially select');
  c.state.selected=['b'];c.target('draft');await assert.rejects(c.preview(),/已加入草案/);
  assert.ok(!calls.some(call=>call.method==='POST'));
});

test('reopening candidates drops selections and mappings that another session added to a draft',async()=>{
  const item={id:'a',kind:'entity'};
  const {controller:c}=fixture({items:[item]});await c.open();c.select('a',true);c.mapping('a','urn:Customer');c.close();
  item.linked_drafts=[{draft_id:'draft',status:'draft_created'}];await c.open();
  assert.deepEqual(c.state.selected,[]);assert.deepEqual(c.state.mappings,{});
});

test('draft-linked candidates are hidden by default and read-only when viewing all statuses',async()=>{
  const f=uiFixture({candidates:[{id:'a',kind:'entity',text:'待核对客户',document_id:'one'},
    {id:'b',kind:'entity',text:'已加入客户',document_id:'one',linked_drafts:[{draft_id:'draft',status:'draft_created'}]}]});
  await f.ui.open({projectId:'p'});
  assert.ok(f.find('待核对客户 · 类型待核对'));assert.equal(f.find('已加入客户 · 类型待核对'),undefined);
  const status=f.all(f.drawers[0].content).find(item=>item.id==='oi-filter-status');
  assert.ok(status,'processing status must be filterable');status.value='all';await status.onchange();
  await f.find('选择当前列表（1 条）').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(f.ui.state.selected)),['a']);
  const linkedRow=f.all(f.drawers[0].content).find(item=>item.className==='oi-open-detail'&&item.textContent==='已加入客户 · 类型待核对');
  await linkedRow.onclick();
  const checkboxes=f.all(f.drawers[0].content).filter(item=>item.type==='checkbox');
  assert.equal(checkboxes[1].disabled,true);assert.equal(checkboxes[1].checked,false);
  let opened=null;f.context.window.OntologyModel.selectDraft=async id=>{opened=id;};
  await f.find('查看关联草案').onclick();assert.equal(opened,'draft');assert.equal(f.drawers[0].isOpen,false);
  assert.ok(!f.calls.some(call=>call.method==='POST'));
});

test('closed previews and late page completions cannot unlock a reopened loading session',async()=>{
  let read=0,finishMore,finishOpen;const data={items:[{id:'a',kind:'entity'}],total:2,expected_ontology_id:'release',published_terms:[]};
  const {controller:c}=fixture({readCandidates:()=>++read===1?data:read===2?new Promise(resolve=>finishMore=resolve):new Promise(resolve=>finishOpen=resolve)});
  await c.open();c.select('a',true);c.target('draft');await c.preview();const more=c.more();c.close();
  assert.equal(c.state.preview,null);await assert.rejects(c.apply('add'),/关闭|预览/);
  const reopen=c.open();finishMore(data);await more;assert.equal(c.state.busy,true,'an older read cannot finish the new loading state');
  finishOpen(data);await reopen;assert.deepEqual(c.state.selected,['a']);
});

test('closing during session restoration cannot erase the previous selection',async()=>{
  let count=0,finish;const data={items:[{id:'a',kind:'entity'}],total:1,expected_ontology_id:'release',published_terms:[]};
  const {controller:c}=fixture({readCandidates:()=>++count===2?new Promise(resolve=>finish=resolve):data});
  await c.open({documentId:'doc'});c.select('a',true);c.close();const restoring=c.open({documentId:'doc'});c.close();
  await c.open({documentId:'doc'});finish(data);await restoring;assert.deepEqual(c.state.selected,['a']);
});

test('an older server that ignores task scope cannot populate a widened list',async()=>{
  const {controller:c}=fixture({readCandidates:()=>({items:[{id:'other-task',kind:'entity'}],total:1})});
  await assert.rejects(c.open({runId:'requested-run'}),/范围|服务/);assert.deepEqual(c.state.items,[]);
});

test('an old project rejection cannot clear the active project unknown write identity',async()=>{
  let project='p',rejectP,rejectQ;const writes=[];
  const c=intake.createController({getContext:()=>({project,ontologyId:'release'}),uid:()=> 'key-'+project,
    request:async(path,body)=>{
      if(path.startsWith('/candidates'))return {items:[{id:'a',kind:'entity'}],total:1,expected_ontology_id:'release'};
      if(path==='/selection-preview')return {can_add:true,preview_fingerprint:'fp',source_fingerprint:'src'};
      writes.push(body);return new Promise((_,reject)=>{if(project==='p')rejectP=reject;else rejectQ=reject;});
    }});
  await c.open();c.select('a',true);c.target('new');await c.preview();const p=c.apply('add');const resultP=assert.rejects(p);c.close();
  project='q';await c.open();c.select('a',true);c.target('new');await c.preview();const q=c.apply('add');const resultQ=assert.rejects(q);
  const old=Error('source changed');old.status=409;rejectP(old);await resultP;
  assert.equal(c.state.pending.body.idempotency_key,'key-q');rejectQ(Error('network'));await resultQ;
  assert.equal(c.state.pending.body.idempotency_key,'key-q');await assert.rejects(c.preview(),/原请求|尚未确认/);
  assert.equal(writes.length,2);
});

test('large previews become a bounded confirmation step rather than extending the candidate selection page',async()=>{
  const suggestions=Array.from({length:188},(_,i)=>({action:i%3?'add_annotation':'create_term',target_iri:'urn:Type'+Math.floor(i/3),after:{kind:'class'}}));
  const f=uiFixture({previewResponse:{suggestions}});await f.ui.open({projectId:'p'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();await f.find('预览选中内容（只读）').onclick();
  assert.equal(f.find('01 按资料选择候选'),undefined,'selection page is replaced by the confirmation step');
  assert.ok(f.find('返回调整选择'));assert.ok(f.find('确认加入草案，等待发布'));
  const detail=f.all(f.drawers[0].content).find(item=>item.className==='oi-preview-details');
  assert.ok(detail);assert.equal(detail.open,false);
  await f.find('返回调整选择').onclick();assert.deepEqual(Array.from(f.ui.state.selected),['a']);assert.equal(f.ui.state.preview,null);
});

test('preview requests immediately show accessible loading feedback',async()=>{
  let finish;const f=uiFixture({previewRead:()=>new Promise(resolve=>finish=resolve)});await f.ui.open({projectId:'p'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();const preview=f.find('预览选中内容（只读）').onclick();
  for(let i=0;i<8;i++)await Promise.resolve();assert.ok(f.find('正在核对所选候选'));
  finish();await preview;
});

test('draft confirmation shows immediate progress and a compact unpublished result with factual counts',async()=>{
  let finish;const f=uiFixture({confirmRead:()=>new Promise(resolve=>finish=resolve)});await f.ui.open({projectId:'p'});
  const checkbox=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');checkbox.checked=true;await checkbox.onchange();await f.find('预览选中内容（只读）').onclick();
  const request=f.find('确认加入草案，等待发布').onclick();for(let i=0;i<8;i++)await Promise.resolve();
  assert.ok(f.find('正在加入工作草案'));assert.equal(f.find('确认加入草案，等待发布'),undefined);
  finish({receipt:{draft_id:'draft',revision:3,structure:{added:180},facts:{selected:1}}});await request;
  assert.ok(f.find('已加入草案，等待审核发布'));assert.ok(f.find('实体 1 条 · 关系 0 条 · 属性 0 条'));
  assert.ok(f.all(f.drawers[0].content).some(item=>String(item.textContent||'').includes('尚未正式入库')));
});

test('preview counts definitions, operations and factual candidates independently',()=>{
  const suggestions=Array.from({length:60},(_,i)=>[{action:'create_term',target_iri:'urn:'+i,after:{kind:i<34?'class':'relation'}},
    {action:'add_annotation',target_iri:'urn:'+i},{action:'add_annotation',target_iri:'urn:'+i}]).flat();
  suggestions.push(...Array.from({length:8},(_,i)=>({action:'add_parent',target_iri:'urn:'+i})));
  const associated_fact_scope=Array.from({length:118},(_,i)=>({id:'fact-'+i,kind:i<73?'entity':'relation'}));
  assert.deepEqual(intake.previewSummary({suggestions,associated_fact_scope}),{selected:118,entities:73,relations:45,attributes:0,newClasses:34,newRelations:26,newAttributes:0,definitions:60,operations:188});
});

const documentDirectory=[{document_id:'doc-a',document_title:'资料甲',total:2,pending_count:2,draft_count:0},{document_id:'doc-b',document_title:'资料乙',total:2,pending_count:1,draft_count:1}];
function directoryResponse(items,extra={}){return {items,total:4,documents:documentDirectory,expected_ontology_id:'release',published_terms:[{iri:'urn:Customer',kind:'class'}],...extra};}
test('document reads retain exact task scope, deduplicate global overlap and restore lazy selections',async()=>{
  const a={id:'a',document_id:'doc-a',kind:'entity'},b={id:'b',document_id:'doc-b',kind:'entity'};
  const {controller:c,calls}=fixture({readCandidates:path=>directoryResponse(path.includes('document_id=doc-b')?[b]:path.includes('offset=0')?[a]:[b],{scope_note:'范围已核对'})});
  await c.open({batchId:'batch',runId:'run'});
  assert.deepEqual(c.state.documents,documentDirectory);assert.deepEqual(c.state.expandedDocuments,{});
  await c.toggleDocument('doc-b');assert.equal(c.state.offset,1);assert.equal(c.state.expandedDocuments['doc-b'],true);
  assert.match(calls[1].path,/offset=0&limit=100&document_id=doc-b&batch_id=batch&run_id=run/);
  c.select('b',true);c.mapping('b','urn:Customer');await c.more();assert.deepEqual(c.state.items.map(x=>x.id),['a','b']);
  await c.toggleDocument('doc-b');assert.deepEqual(c.state.selected,['b']);c.close();await c.open({batchId:'batch',runId:'run'});
  assert.deepEqual(c.state.selected,['b']);assert.equal(c.state.mappings.b,'urn:Customer');assert.equal(c.state.expandedDocuments['doc-b'],false);
});
test('lazy-only document pages are replayed on reopen without advancing global pagination',async()=>{
  const a={id:'a',document_id:'doc-a',kind:'entity'},b={id:'b',document_id:'doc-b',kind:'entity'};
  const {controller:c}=fixture({readCandidates:path=>directoryResponse(path.includes('document_id=doc-b')?[b]:[a])});
  await c.open();await c.loadDocument('doc-b');c.select('b',true);c.close();await c.open();
  assert.equal(c.state.offset,1);assert.deepEqual(c.state.selected,['b']);assert.deepEqual(c.state.items.map(x=>x.id),['a','b']);
});
test('pinned candidate entries trim directory and cannot read unrelated documents',async()=>{
  const {controller:c,calls}=fixture({readCandidates:()=>directoryResponse([{id:'a',document_id:'doc-a',kind:'entity'},{id:'b',document_id:'doc-b',kind:'entity'}])});
  await c.open({candidateIds:['a']});assert.deepEqual(c.state.documents.map(x=>x.document_id),['doc-a']);assert.equal(c.state.documents[0].total,1);
  await assert.rejects(c.loadDocument('doc-b'),/范围/);assert.equal(calls.length,1);assert.deepEqual(c.state.selected,['a']);
});
test('late document responses cannot populate a closed or different project session',async()=>{
  for(const change of ['close','project']){let finish;
    const {controller:c,context}=fixture({readCandidates:path=>path.includes('document_id=doc-b')?new Promise(resolve=>finish=resolve):directoryResponse([{id:'a',document_id:'doc-a',kind:'entity'}])});
    await c.open();const loading=c.loadDocument('doc-b');if(change==='close')c.close();else context.project='q';
    finish(directoryResponse([{id:'b',document_id:'doc-b',kind:'entity'}]));await loading;assert.deepEqual(c.state.items.map(x=>x.id),['a']);
  }
});
test('unloaded document headers start collapsed and group selection never includes hidden rows',async()=>{
  const f=uiFixture({candidates:[{id:'a',document_id:'doc-a',kind:'entity',text:'甲'}],documents:documentDirectory});await f.ui.open({projectId:'p'});
  assert.ok(f.find('资料甲'));assert.ok(f.find('资料乙'));assert.equal(f.all(f.drawers[0].content).filter(x=>x.type==='checkbox').length,0);
  await f.find('选择当前列表（1 条）').onclick();assert.deepEqual(Array.from(f.ui.state.selected),['a']);
  await f.find('全部展开').onclick();assert.ok(f.find('取消本资料已加载候选（1 条）'));await f.find('全部收起').onclick();assert.deepEqual(Array.from(f.ui.state.selected),['a']);
});
test('older inventories report only qualified loaded document counts',async()=>{
  const f=uiFixture();await f.ui.open({projectId:'p'});assert.ok(f.all(f.drawers[0].content).some(x=>/已读取 1 份资料.*资料总数待核对/.test(x.textContent||'')));
});

test('single document defaults open and accessible collapse preferences remain isolated by actor and scope',async()=>{
  let actor='first';const {controller:c}=fixture({getActor:()=>actor,readCandidates:()=>directoryResponse([{id:'a',document_id:'doc-a',kind:'entity'}],{documents:documentDirectory.slice(0,1)})});
  await c.open({documentId:'doc-a'});assert.equal(c.state.expandedDocuments['doc-a'],true);await c.toggleDocument('doc-a');
  actor='second';c.close();await c.open({documentId:'doc-a'});assert.equal(c.state.expandedDocuments['doc-a'],true);c.close();
  actor='first';await c.open({documentId:'doc-a'});assert.equal(c.state.expandedDocuments['doc-a'],false);c.close();
  await c.open();assert.equal(c.state.expandedDocuments['doc-a'],true);
  const f=uiFixture();await f.ui.open({projectId:'p'});const toggle=f.all(f.drawers[0].content).find(x=>x.className==='oi-document-toggle');
  assert.equal(toggle['aria-expanded'],'true');assert.equal(toggle['aria-controls'],'oi-document-body-doc');
});
test('group selection is limited to loaded matching candidates and preserves hidden mappings',async()=>{
  const rows=[{id:'a',document_id:'doc-a',kind:'entity',proposed_type:'客户'},{id:'b',document_id:'doc-a',kind:'relation',proposed_type:'拥有'}];
  const f=uiFixture({candidates:rows,documents:documentDirectory});await f.ui.open({projectId:'p'});
  const kind=f.all(f.drawers[0].content).find(x=>x.id==='oi-filter-kind');kind.value='entity';kind.onchange();await f.find('全部展开').onclick();
  await f.find('选择本资料已加载候选（1 条）').onclick();assert.deepEqual(Array.from(f.ui.state.selected),['a']);assert.equal(f.calls.filter(x=>x.method==='POST').length,0);
  await f.find('全部收起').onclick();assert.deepEqual(Array.from(f.ui.state.selected),['a']);
});
test('document failure permits read retry while preserving selections and mappings',async()=>{
  let fail=true;const {controller:c}=fixture({readCandidates:path=>{if(path.includes('document_id=doc-b')){if(fail)throw Error('读取失败');return directoryResponse([{id:'b',document_id:'doc-b',kind:'entity'}]);}return directoryResponse([{id:'a',document_id:'doc-a',kind:'entity'}]);}});
  await c.open();c.select('a',true);c.mapping('a','urn:Customer');await assert.rejects(c.toggleDocument('doc-b'),/读取失败/);
  assert.deepEqual(c.state.selected,['a']);assert.equal(c.state.mappings.a,'urn:Customer');assert.equal(c.state.documentErrors['doc-b'],'读取失败');
  fail=false;await c.loadDocument('doc-b');assert.equal(c.state.documentErrors['doc-b'],undefined);assert.deepEqual(c.state.selected,['a']);
});

test('a fresh project guides a selected candidate to its processing goal before readonly preview',async()=>{
  const f=uiFixture({noDraft:true,noOntology:true});await f.ui.open({projectId:'p'});
  const pick=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');pick.checked=true;await pick.onchange();
  const next=f.find('下一步：选择处理方式');assert.ok(next);assert.equal(next.disabled,false);await next.onclick();
  const goal=f.all(f.drawers[0].content).find(item=>item.id==='oi-processing-goal');assert.equal(goal.focused,true);
  assert.equal(f.drawers[0].dialog.scrollTop,0,'revealing the goal never scrolls the outer dialog or its header');
  assert.equal(f.drawers[0].body.scrollCalls.at(-1).top,480);
  assert.equal(f.calls.filter(item=>item.method==='POST').length,0);
  const structure=f.all(f.drawers[0].content).find(item=>item.type==='radio'&&item.value==='structure');await structure.onchange();
  assert.equal(f.ui.state.target,'new');assert.equal(f.find('预览选中内容（只读）').disabled,false);
});

test('preview navigation scrolls only the drawer body and respects reduced motion',async()=>{
  const f=uiFixture({reducedMotion:true});await f.ui.open({projectId:'p'});
  const pick=f.all(f.drawers[0].content).find(item=>item.type==='checkbox');pick.checked=true;await pick.onchange();
  f.drawers[0].body.scrollTop=25;await f.find('预览选中内容（只读）').onclick();
  assert.equal(f.drawers[0].dialog.scrollTop,0);
  assert.deepEqual(JSON.parse(JSON.stringify(f.drawers[0].body.scrollCalls.at(-1))),{top:480,behavior:'auto'});
});
