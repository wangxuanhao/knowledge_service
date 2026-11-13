const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/entity-attribute-panel.js');
const panel=fs.existsSync(file)?require(file):{};
const value=(id,extra={})=>({record_id:id,version:2,version_id:id+'-v2',value_preview:'第一行\n第二行',datatype:'http://www.w3.org/2001/XMLSchema#string',status:'accepted',document_count:2,accepted_support_count:3,...extra});
const page=(values,extra={})=>({entity:{id:'e',version:4,version_id:'e-v4'},groups:[{predicate:'p',label:'职责',total_values:2,values}],property_count:1,value_count:2,next_cursor:null,watermark:8,...extra});
function harness(request,extra={}){
  const calls=[],states=[];let active=true;
  const controller=panel.createController({projectId:'p',entity:{id:'e',version:4,version_id:'e-v4'},scope:{known_at:'2025-01-01',filters:{city:'上海'},entity_type:'class',predicate:'relation'},
    request:async(url,body)=>{calls.push({url,body});return request(url,body);},accepts:()=>active,onChange:state=>states.push(state),...extra});
  return {controller,calls,states,stale:()=>active=false};
}
test('renders grouped concrete values with escaped bounded previews and independent counts',()=>{
  assert.equal(typeof panel.renderHtml,'function');
  const html=panel.renderHtml({...page([value('a',{value_preview:'<img src=x>\n'+ '长'.repeat(500),value_truncated:true,value_length:510})]),loading:false,values:{},sources:{}});
  assert.match(html,/职责/);assert.match(html,/2 份文档/);assert.match(html,/3 条支撑/);
  assert.match(html,/展开全文/);assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/<img/);
  assert.ok(!html.includes('长'.repeat(401)));assert.match(html,/查看来源/);assert.match(html,/时间待核/);
});
test('pages scoped attributes without losing values or inheriting relation predicates',async()=>{
  assert.equal(typeof panel.createController,'function');
  const h=harness((url,body)=>body.cursor?page([value('b')]):page([value('a')],{next_cursor:'next'}));
  await h.controller.load();await h.controller.loadMore();
  assert.equal(h.calls[0].body.limit,20);assert.equal(h.calls[0].body.entity_type,undefined);assert.equal(h.calls[0].body.predicate,undefined);
  assert.equal(h.calls[0].body.known_at,'2025-01-01');assert.equal(h.calls[1].body.cursor,'next');
  assert.deepEqual(h.controller.snapshot().groups[0].values.map(v=>v.record_id),['a','b']);
});
test('selecting a property reloads a bounded page with its explicit predicate',async()=>{
  const h=harness(()=>page([value('a')]));await h.controller.load();await h.controller.selectPredicate('p');
  assert.equal(h.calls[1].body.predicate,'p');assert.equal(h.calls[1].body.limit,20);
});
test('a deep retrieval hit uses one exact scoped preview request then can return to normal paging',async()=>{
  const h=harness((url,body)=>page([value(body.focus_record_id||'a')]));
  assert.equal(typeof h.controller.focus,'function');
  await h.controller.focus('deep-fact','p');assert.equal(h.calls[0].body.focus_record_id,'deep-fact');assert.equal(h.calls.length,1);
  assert.equal(h.controller.snapshot().groups[0].values[0].record_id,'deep-fact');
  await h.controller.selectPredicate(null);assert.equal(h.calls[1].body.focus_record_id,undefined);
});

test('focused retrieval pins fail closed for changed or missing fact revisions before rendering values',async()=>{
  for(const actual of [value('a',{version_id:'a-v3',version:3}),value('a',{version_id:undefined}),null]){
    const h=harness(()=>page(actual?[actual]:[]));
    await h.controller.focus('a','p',{version_id:'a-v2',version:2});
    const state=h.controller.snapshot();assert.match(state.error,/检索命中.*版本/);assert.deepEqual(state.groups,[]);
  }
  for(const expected of [{},null]){
    const missing=harness(()=>page([value('a')]));await missing.controller.focus('a','p',expected);
    assert.match(missing.controller.snapshot().error,/检索命中.*版本/);assert.deepEqual(missing.controller.snapshot().groups,[]);
  }
});

test('focused retrieval accepts an exact version ID or numeric fallback and keeps historical scope',async()=>{
  const h=harness(()=>page([value('a')]));await h.controller.focus('a','p',{version_id:'a-v2',version:2});
  assert.equal(h.controller.snapshot().error,'');assert.equal(h.calls[0].body.known_at,'2025-01-01');
  const numeric=harness(()=>page([value('a',{version_id:undefined})]));await numeric.controller.focus('a','p',{version:2});
  assert.equal(numeric.controller.snapshot().error,'');assert.equal(numeric.controller.snapshot().groups[0].values[0].version,2);
  const mismatch=harness(()=>page([value('a',{version:3})]));await mismatch.controller.focus('a','p',{version:2});
  assert.match(mismatch.controller.snapshot().error,/检索命中.*版本/);
});

test('only explicit refresh accepts a changed focused fact and clearly labels the new snapshot',async()=>{
  const h=harness(()=>page([value('a',{version:3,version_id:'a-v3',value_preview:'更新后的值'})]));
  await h.controller.focus('a','p',{version_id:'a-v2'});assert.deepEqual(h.controller.snapshot().groups,[]);
  await h.controller.load();assert.deepEqual(h.controller.snapshot().groups,[]);
  await h.controller.refresh();const state=h.controller.snapshot();assert.equal(state.error,'');
  assert.equal(state.groups[0].values[0].version_id,'a-v3');const html=panel.renderHtml(state);
  assert.match(html,/已刷新为当前范围的最新属性值/);assert.doesNotMatch(html,/已定位检索命中的属性值/);
  await h.controller.selectPredicate(null);assert.doesNotMatch(panel.renderHtml(h.controller.snapshot()),/可能与原检索命中不同/);
});
test('late responses and replaced entity revisions cannot populate the active panel',async()=>{
  let finish;const h=harness(()=>new Promise(resolve=>finish=resolve));const pending=h.controller.load();h.stale();finish(page([value('a')]));await pending;
  assert.equal(h.controller.snapshot().groups.length,0);
  const changed=harness(()=>page([value('a')],{entity:{id:'e',version:5,version_id:'new'}}));await changed.controller.load();
  assert.match(changed.controller.snapshot().error,/版本.*变化/);assert.equal(changed.controller.snapshot().groups.length,0);
});
test('explicit refresh replaces a revised owner atomically and later pages use that revision',async()=>{
  let revision=4,finishOldValue;
  const h=harness((url,body)=>{
    if(url.endsWith('/a'))return new Promise(resolve=>finishOldValue=resolve);
    const fresh=revision===5;
    return page([value(body.cursor?'c':fresh?'b':'a',{value_truncated:!fresh})],{entity:{id:'e',version:revision,version_id:'e-v'+revision},next_cursor:body.cursor?null:'more',watermark:revision});
  });
  await h.controller.load();const oldFull=h.controller.toggleValue('a');revision=5;
  assert.equal(typeof h.controller.refresh,'function');await h.controller.refresh();
  assert.equal(h.controller.snapshot().entity.version_id,'e-v5');assert.equal(h.controller.snapshot().groups[0].values[0].record_id,'b');
  finishOldValue({id:'a',version_id:'a-v2',value:'旧正文'});await oldFull;assert.deepEqual(h.controller.snapshot().values,{});
  await h.controller.loadMore();assert.deepEqual(h.controller.snapshot().groups[0].values.map(item=>item.record_id),['b','c']);
});
test('refresh recovers an initial owner revision error and rejects late refreshes including A B A',async()=>{
  const h=harness(()=>page([value('fresh')],{entity:{id:'e',version:5,version_id:'e-v5'}}));
  await h.controller.load();assert.match(h.controller.snapshot().error,/版本.*变化/);await h.controller.refresh();assert.equal(h.controller.snapshot().error,'');
  const pending=[];const race=harness(()=>new Promise(resolve=>pending.push(resolve)));
  const a=race.controller.load(),b=race.controller.refresh(),againA=race.controller.refresh();
  pending[2](page([value('new-a')]));await againA;
  pending[1](page([value('late-b')],{entity:{id:'e',version:5,version_id:'e-v5'}}));pending[0](page([value('late-a')]));await Promise.all([a,b]);
  assert.deepEqual(race.controller.snapshot().groups[0].values.map(item=>item.record_id),['new-a']);
});
test('conflict collection pages independently beyond fifty sets without duplicating values',async()=>{
  const set=id=>({id,predicate:'p',version:1,members:[]});let fail=true;
  const h=harness((url,body)=>{
    if(!url.endsWith('/attribute-conflicts'))return page([value('a')],{conflict_sets:Array.from({length:50},(_,i)=>set(String(i+1).padStart(3,'0'))),conflict_next_cursor:'050',next_cursor:'values-next'});
    if(fail){fail=false;throw Error('冲突分页暂不可用');}
    return {entity_id:'e',conflict_sets:[set('051'),set('052')],next_cursor:null};
  });
  await h.controller.load();assert.equal(typeof h.controller.loadMoreConflicts,'function');
  await h.controller.loadMoreConflicts();assert.match(h.controller.snapshot().conflict_error,/冲突分页暂不可用/);
  await h.controller.loadMoreConflicts();const state=h.controller.snapshot();
  assert.equal(state.conflict_sets.length,52);assert.equal(state.groups[0].values.length,1);assert.equal(state.next_cursor,'values-next');
  assert.equal(h.calls[1].body.entity_id,'e');assert.equal(h.calls[1].body.limit,20);assert.equal(h.calls[1].body.cursor,'050');assert.equal(h.calls[1].body.known_at,'2025-01-01');
  assert.match(panel.renderHtml({...state,conflict_next_cursor:'052'}),/还有未加载的冲突集合/);
});
test('a refresh invalidates a pending conflict page and attribute paging preserves loaded conflicts',async()=>{
  let finishConflict;
  const h=harness((url,body)=>url.endsWith('/attribute-conflicts')?new Promise(resolve=>finishConflict=resolve):page([value(body.cursor?'b':'a')],{conflict_sets:[{id:'first',predicate:'p',members:[]}],conflict_next_cursor:'first',next_cursor:'values-next'}));
  await h.controller.load();const old=h.controller.loadMoreConflicts();await h.controller.refresh();
  finishConflict({entity_id:'e',conflict_sets:[{id:'late',members:[]}],next_cursor:null});await old;
  assert.deepEqual(h.controller.snapshot().conflict_sets.map(set=>set.id),['first']);
  const more=h.controller.loadMoreConflicts();finishConflict({entity_id:'e',conflict_sets:[{id:'second',members:[]}],next_cursor:null});await more;await h.controller.loadMore();
  assert.deepEqual(h.controller.snapshot().conflict_sets.map(set=>set.id),['first','second']);
});
test('long values load on demand and reject a different fact revision',async()=>{
  let mismatch=false;const h=harness(url=>url.endsWith('/a')?{id:'a',version:2,version_id:mismatch?'new':'a-v2',value:'全文\n'+ '段落'.repeat(300)}:page([value('a',{value_truncated:true})]));
  await h.controller.load();assert.equal(h.calls.length,1);await h.controller.toggleValue('a');
  assert.match(h.controller.snapshot().values.a.text,/全文\n/);assert.equal(h.controller.snapshot().values.a.expanded,true);
  await h.controller.toggleValue('a');assert.equal(h.controller.snapshot().values.a.expanded,false);assert.equal(h.calls.length,2);
  mismatch=true;const other=harness(url=>url.endsWith('/a')?{id:'a',version:9,version_id:'new',value:'wrong'}:page([value('a',{value_truncated:true})]));
  await other.controller.load();await other.controller.toggleValue('a');assert.match(other.controller.snapshot().values.a.error,/版本.*变化/);
});
test('sources page per value with immutable document versions and preserve retryable failures',async()=>{
  let fail=true;const h=harness((url,body)=>{
    if(!url.endsWith('/sources'))return page([value('a')]);
    if(fail){fail=false;throw Error('网络中断');}
    return {record_id:'a',version_id:'a-v2',sources:[{id:body.cursor?'s2':'s1',document_title:'历史文档',document_version_id:'doc-v1',quote:'原文'}],next_cursor:body.cursor?null:'s-next'};
  });
  await h.controller.load();await h.controller.toggleSources('a');assert.match(h.controller.snapshot().sources.a.error,/网络中断/);
  await h.controller.loadSources('a');await h.controller.loadSources('a',true);
  const state=h.controller.snapshot().sources.a;assert.deepEqual(state.items.map(s=>s.id),['s1','s2']);assert.equal(state.items[0].document_version_id,'doc-v1');
  assert.equal(h.calls.at(-1).body.limit,10);assert.equal(h.calls.at(-1).body.cursor,'s-next');
});
test('a failed later source page can retry its cursor and source revisions cannot silently advance',async()=>{
  let attempts=0;const h=harness((url,body)=>{
    if(!url.endsWith('/sources'))return page([value('a')]);
    if(body.cursor&&attempts++===0)throw Error('分页失败');
    return {record_id:'a',version_id:'a-v2',sources:[{id:body.cursor?'s2':'s1'}],next_cursor:body.cursor?null:'next'};
  });
  await h.controller.load();await h.controller.loadSources('a');await h.controller.loadSources('a',true);
  assert.equal(h.controller.snapshot().sources.a.retryMore,true);
  await h.controller.loadSources('a',h.controller.snapshot().sources.a.retryMore);
  assert.deepEqual(h.controller.snapshot().sources.a.items.map(item=>item.id),['s1','s2']);
  const stale=harness(url=>url.endsWith('/sources')?{record_id:'a',version_id:'new',sources:[{id:'unsafe'}]}:page([value('a')]));
  await stale.controller.load();await stale.controller.loadSources('a');assert.match(stale.controller.snapshot().sources.a.error,/版本.*变化/);assert.equal(stale.controller.snapshot().sources.a.items.length,0);
});
test('past intervals and several accepted values receive distinct labels',()=>{
  const html=panel.renderHtml({...page([value('past',{valid_from:'2020-01-01',valid_until:'2021-01-01'}),value('current')]),valid_at:'2025-01-01',values:{},sources:{}});
  assert.match(html,/历史区间值/);assert.match(html,/多个已接受值/);assert.doesNotMatch(html,/多值 \/ 历史区间/);
});
test('summary is bounded to three concrete values and marks unresolved conflict',()=>{
  assert.equal(typeof panel.summaryHtml,'function');
  const html=panel.summaryHtml(page(Array.from({length:6},(_,i)=>value('a'+i,{value_preview:'v'+i})),{conflict_count:1}));
  assert.match(html,/v0/);assert.match(html,/v2/);assert.doesNotMatch(html,/v3/);assert.match(html,/存在冲突/);
});
test('repeated hover for the same entity and frozen scope shares one bounded request',async()=>{
  let calls=0;const request=async()=>{calls++;return page([value('a')]);};
  const options={projectId:'hover-test',scope:{known_at:'2025-01-01'},request};
  await Promise.all([panel.hoverHtml({id:'e',version_id:'e-v4'},options),panel.hoverHtml({id:'e',version_id:'e-v4'},options)]);
  assert.equal(calls,1);
  await panel.hoverHtml({id:'e',version_id:'e-v4'},{...options,scope:{known_at:'2024-01-01'}});assert.equal(calls,2);
});
test('isolated conflict sets remain separate from accepted values and expose one shared review action',async()=>{
  const conflictSet={id:'set-1',predicate:'p',version:3,state:'open',members:[value('x',{value_preview:'争议甲',support_count:4,applicability:{platforms:['平台甲']}}),value('y',{value_preview:'争议乙'})]};
  const h=harness(()=>page([],{groups:[],conflict_sets:[conflictSet]}));await h.controller.load();
  const state=h.controller.snapshot();assert.equal(state.conflict_sets[0].id,'set-1');
  const html=panel.renderHtml({...state,canReview:true});assert.match(html,/未解决冲突/);assert.match(html,/争议甲/);assert.match(html,/争议乙/);assert.match(html,/data-conflict-set="set-1"/);
  assert.doesNotMatch(html,/当前范围没有已接受的属性值/);assert.match(panel.summaryHtml({...state}),/存在冲突/);
  assert.match(html,/string/);assert.match(html,/4 条支撑/);assert.match(html,/平台甲/);
});
test('quarantined conflict members read full values and frozen sources only through dedicated routes',async()=>{
  const h=harness(url=>{
    if(url.endsWith('/members/x/read'))return {id:'x',version:2,version_id:'x-v2',value:'争议全文\n保留原值'};
    if(url.endsWith('/members/x/sources'))return {record_id:'x',version_id:'x-v2',sources:[{id:'s',document_title:'冲突来源',document_version_id:'frozen-v1',quote:'争议原文'}]};
    return page([],{groups:[],conflict_sets:[{id:'set',predicate:'p',members:[value('x',{value_preview:'争议预览',value_truncated:true})]}]});
  });
  await h.controller.load();await h.controller.toggleValue('x');assert.equal(h.calls.length,2);
  assert.match(h.calls[1].url,/attribute-conflicts\/set\/members\/x\/read$/);assert.equal(h.calls[1].body.expected_version_id,'x-v2');
  await h.controller.toggleSources('x');assert.match(h.calls[2].url,/attribute-conflicts\/set\/members\/x\/sources$/);
  const html=panel.renderHtml(h.controller.snapshot());assert.match(html,/争议全文/);assert.match(html,/冲突来源/);assert.doesNotMatch(html,/已接受/);
});
test('frozen sources label superseded decisions and withdrawn documents without hiding the quote',()=>{
  const state={...page([value('a')]),values:{},sources:{a:{expanded:true,items:[{document_title:'旧来源',document_version_id:'old-doc-v1',quote:'原始引文',status:'superseded',decision_version:8,source_active:false,applicability:{platforms:['平台甲']}}]}}};
  const html=panel.renderHtml(state);assert.match(html,/已取代/);assert.match(html,/来源已撤回/);assert.match(html,/原始引文/);assert.match(html,/平台甲/);
});
test('create and edit accept multiline strings without trimming their original value',()=>{
  const editor=require('../../knowledge_service/web/record-attribute-editor.js');
  const input='  第一段\n\n第二段 <script>\n';assert.equal(editor.parseValue('string',input).value,input);
  const create=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/record-create.js'),'utf8');
  const edit=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/record-dialog.js'),'utf8');
  assert.match(create,/value=field\(form,'属性值','textarea'\)/);assert.match(edit,/<textarea id="structured-value"/);
});
test('entity inspector and graph surfaces use the shared reader without attribute drawing',()=>{
  const read=name=>fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/'+name),'utf8');
  assert.match(read('evidence-inspector.js'),/EntityAttributePanel\.mount\(/);
  assert.match(read('full-graph-view.js'),/EntityAttributePanel\?\.hoverHtml/);
  assert.match(read('entity-mindmap-view.js'),/onInspect\(/);
  assert.match(read('workspace.js'),/EntityAttributePanel\?\.hoverHtml/);
});
