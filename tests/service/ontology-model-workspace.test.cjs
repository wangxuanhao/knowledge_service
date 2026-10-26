const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const target=path.resolve(__dirname,'../../knowledge_service/web/ontology-model-workspace.js');
const workspace=fs.existsSync(target)?require(target):{};

test('workspace is demand-opened and close/reopen preserves the selected tab without editing draft state',()=>{
  assert.equal(typeof workspace.createController,'function','workspace controller is missing');
  const draft={id:'working',revision:3},context={project:'p',draft},before=JSON.stringify(draft);
  const control=workspace.createController({getContext:()=>context});
  assert.equal(control.state.open,false);
  control.open('sources');control.toggleExpanded();control.close();
  assert.equal(control.state.open,false);assert.equal(control.state.tab,'sources');
  control.open();assert.equal(control.state.tab,'sources');assert.equal(control.state.open,true);
  control.openDraft('archived');assert.equal(control.state.tab,'archive');assert.equal(control.state.archiveId,'archived');
  assert.equal(context.draft,draft);assert.equal(JSON.stringify(draft),before);
});

test('workspace project changes invalidate archive identity and expansion even when returning to the original project',()=>{
  assert.equal(typeof workspace.createController,'function');
  const context={project:'a'},control=workspace.createController({getContext:()=>context});
  control.openDraft('old');control.toggleExpanded();
  context.project='b';control.sync();
  assert.equal(control.state.open,false);assert.equal(control.state.archiveId,null);assert.equal(control.state.expanded,false);
  context.project='a';control.sync();control.open();
  assert.equal(control.state.archiveId,null);assert.equal(control.state.tab,'definitions');
});

test('workspace separates unique definitions, low-level operations and candidate facts without changing inputs',()=>{
  assert.equal(typeof workspace.draftSummary,'function');
  const state={draft:{id:'draft',operations:[
    {target_iri:'urn:A',action:'create_term',after:{kind:'class'}},
    {target_iri:'urn:A',action:'add_annotation'},
    {target_iri:'urn:R',action:'create_term',after:{kind:'relation'}},
    {target_iri:'urn:P',action:'create_term',after:{kind:'attribute'}},
  ],associated_knowledge:{selected:118},source_context:{intake_bindings:[
    {discovery_run_id:'r1',selected_candidate_ids:['a','b']},
    {discovery_run_id:'r2',selected_candidate_ids:['b','c']},
  ]}},readiness:{items:[{severity:'error',code:'not_submitted'},{severity:'info',code:'validation_not_evaluated'}]}};
  const before=JSON.stringify(state),counts=workspace.draftSummary(state);
  assert.equal(counts.definitions,3);assert.equal(counts.operations,4);assert.equal(counts.facts,118);assert.equal(counts.pending,1);
  assert.equal(counts.kinds.class,1);assert.equal(counts.kinds.relation,1);assert.equal(counts.kinds.attribute,1);
  assert.equal(JSON.stringify(state),before);
  assert.equal(workspace.draftSummary({draft:{source_context:state.draft.source_context}}).facts,3);
  assert.equal(workspace.draftSummary({draft:{}}).facts,null,'absent count is unknown, not a fabricated zero');
});

test('source records use explicit task identity and frozen source references rather than guessing from run names',()=>{
  assert.equal(typeof workspace.sourceRecords,'function');
  const draft={source_context:{documents:[{document_id:'d',expected_document_version:2,version:99,title:'原始资料'}],intake_bindings:[
    {discovery_run_id:'ingest-looking-run',selected_candidate_ids:['c']},
    {discovery_run_id:'other-run',task_id:'actual-task',selected_candidate_ids:['x','y']},
  ]}};
  const before=JSON.stringify(draft),records=workspace.sourceRecords(draft);
  assert.equal(records.documents[0].id,'d');assert.equal(records.documents[0].version,2);
  assert.equal(records.runs[0].id,'ingest-looking-run');assert.equal(records.runs[0].taskId,null);
  assert.equal(records.runs[1].taskId,'actual-task');assert.equal(records.runs[1].selected,2);
  assert.equal(JSON.stringify(draft),before);
});

test('work-draft summaries and names never inherit a historical canvas type or label',()=>{
  const historical={classes:[{id:'same',label_zh:'历史实体名称'}],relations:[],attributes:[]};
  const draft={ontology:{classes:[],relations:[{id:'same',label_zh:'当前关系名称'}],attributes:[]},operations:[
    {id:'create',target_iri:'same',action:'create_term',after:{kind:'relation'}},
    {id:'name',target_iri:'same',action:'add_annotation',after:{predicate:'http://www.w3.org/2000/01/rdf-schema#label',value:'当前关系名称',language:'zh'}},
  ]};
  const counts=workspace.draftSummary({draft,summary:historical,preview:{id:'old'}});
  assert.equal(counts.kinds.relation,1);assert.equal(counts.kinds.class,0);
  const catalog=require('../../knowledge_service/web/ontology-model-catalog.js');
  const groups=catalog.draftDefinitionGroups(draft,historical);
  assert.equal(groups[0].kind,'relation');assert.equal(groups[0].label,'当前关系名称');
});

test('moving governance controls preserves the actual note input and original action handlers across refreshes',()=>{
  assert.equal(typeof workspace.relocateActions,'function');
  const make=()=>({children:[],append(node){node.parent?.remove(node);this.children.push(node);node.parent=this;},
    remove(node){this.children=this.children.filter(item=>item!==node);},replaceChildren(...nodes){for(const old of this.children)old.parent=null;this.children=[];for(const node of nodes)this.append(node);}});
  let submitted=0;const source=make(),footer=make();
  const first={input:{value:'保留发布说明'},click(){submitted++;}};source.append(first);source.querySelector=()=>source.children[0]||null;
  workspace.relocateActions(source,footer);
  assert.equal(footer.children[0],first);assert.equal(footer.children[0].input.value,'保留发布说明');footer.children[0].click();assert.equal(submitted,1);
  workspace.relocateActions(source,footer);assert.equal(footer.children[0],first,'an unchanged source must not erase moved controls');
  const second={input:{value:'保留发布说明'},click(){submitted+=10;}};source.append(second);
  workspace.relocateActions(source,footer);assert.equal(footer.children.length,1);assert.equal(footer.children[0],second);
  footer.children[0].click();assert.equal(submitted,11);
});

test('a refreshed action section without controls clears stale live controls',()=>{
  const target={children:[],replaceChildren(...nodes){this.children=nodes;}};
  let controls={id:'old'},bar={id:'bar-1'};
  const source={get firstElementChild(){return bar;},querySelector:()=>controls};
  workspace.relocateActions(source,target);assert.equal(target.children[0].id,'old');
  controls=null;workspace.relocateActions(source,target);assert.equal(target.children[0].id,'old');
  bar={id:'replacement-without-actions'};workspace.relocateActions(source,target);assert.equal(target.children.length,0);
});

test('the fixed footer takes only the live operation row and leaves warnings in the scrolling section',()=>{
  const warning={id:'restore-acknowledgement'},row={id:'operation-row'},actions={children:[warning,row]};
  const bar={id:'action-bar'},footer={children:[],replaceChildren(...nodes){
    this.children=nodes;actions.children=actions.children.filter(item=>!nodes.includes(item));
  }};
  const source={firstElementChild:bar,querySelector(selector){
    if(selector==='.om-ab-ops')return actions.children.includes(row)?row:null;
    if(selector==='.om-ab-actions')return actions;
    return null;
  }};
  workspace.relocateActions(source,footer);
  assert.equal(footer.children[0],row);
  assert.deepEqual(actions.children,[warning]);
  workspace.relocateActions(source,footer);
  assert.equal(footer.children[0],row,'subsequent synchronization must not move the remaining warning wrapper');
  assert.deepEqual(actions.children,[warning]);
});

test('a historical restore response cannot replace the working draft after project A B A navigation',async()=>{
  let complete;const pending=new Promise(resolve=>complete=resolve),events={},projectEvents={};
  const project={addEventListener(name,fn){projectEvents[name]=fn;}},requests=[];
  const context={window:{},current:'a',console,wb:{clearVersionContext(){}},
    document:{getElementById:id=>id==='project'?project:null,querySelector:()=>null,addEventListener(name,fn){events[name]=fn;}},
    endpoint:suffix=>'/api/projects/'+context.current+suffix,
    api:async(url,body)=>{requests.push({url,body});return pending;}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/ontology-model.js'),'utf8'),context);
  events.DOMContentLoaded();const restore=context.window.OntologyModel.forkFrom('historical-publication');
  context.current='b';projectEvents.change();context.current='a';projectEvents.change();
  complete({id:'restore-created-in-a',status:'pending',revision:1});
  await assert.rejects(restore,/项目|原项目/);
  assert.equal(context.window.OntologyModel.S.draft,null);
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/projects/a/ontology-drafts');
});
