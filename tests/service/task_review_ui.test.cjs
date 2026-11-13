const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function fixture(reviewRows=[],options={}){
  const nodes=new Map(),calls=[],created=[],requests=[];
  function element(){const buttons=new Map();const el={innerHTML:'',textContent:'',value:'',className:'',dataset:{},onclick:null,events:{},children:[],
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(){},
    isConnected:true,showModal(){this.open=true;},close(){this.open=false;},remove(){this.isConnected=false;},after(){},prepend(){throw Error('review must not be prepended to jobs');},
    insertAdjacentHTML(){},querySelector(){return element();},querySelectorAll(selector){
      if(selector!=='[data-open-discovery]')return [];
      return [...this.innerHTML.matchAll(/<button[^>]*data-open-discovery="([^"]*)"[^>]*>([^<]*)<\/button>/g)].map(([,index,text])=>{
        if(!buttons.has(index)){const button=element();button.dataset.openDiscovery=index;button.textContent=text;buttons.set(index,button);}return buttons.get(index);
      });},
    click(){return this.onclick?.();},addEventListener(name,fn){this.events[name]=fn;}};
    el.classList={contains:name=>el.className.split(' ').includes(name),add:name=>el.className+=' '+name,
      remove:name=>el.className=el.className.split(' ').filter(n=>n!==name).join(' '),toggle(){}};
    return el;}
  const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const logs=['[2026-09-10T10:00:01Z] 文档开始 · 文件.md · 4 字符','[2026-09-10T10:00:02Z] 实体抽取返回 · 2 个','[2026-09-10T10:00:03Z] 实体抽取返回 · 3 个',
    '关系抽取返回 · 1 条','仍在等待当前步骤（非完成进度）'];
  const job={id:'job-a',project_id:'a',kind:'ingest',status:'running',logs,stage:'关系抽取',progress:40,
    created_at:'2026-09-10T10:00:00Z',heartbeat_at:'2026-09-10T10:00:04Z',...options.job};
  // window 是浏览器里一定存在的东西：各模块在**加载时**就会向 window.RuntimeView 注册
  // 「项目与运行」的分区加载函数，沙箱少了它这些模块会直接抛 ReferenceError。
  let actor={id:'admin-a',role:'admin'},writer=true;const opened=[],intake=[];
  const context=vm.createContext({window:{IngestResultSummary:require('../../knowledge_service/web/ingest-result-summary.js'),Auth:{user:()=>actor,isAdmin:()=>writer},
    KnowledgeNavigation:{open:async(target,args)=>{opened.push({target,...args});if(options.navigate)await options.navigate(context);}},OntologyIntake:{open:async args=>intake.push(args)}},current:'a',$:get,readParseSettings:()=>({chunk_size:1800}),showTab(){},
    document:{body:element(),createElement(tag){const e=element();e.tagName=tag;created.push(e);return e;},querySelector:get,querySelectorAll:()=>[]},
    esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    status(){},jobList(){throw Error('old list must not be used');},bind:(id,fn)=>get(id).onclick=fn,
    api:async (url,body)=>{calls.push(url);requests.push({url,body});
      if(url.endsWith('/attribute-conflicts'))return {conflict_sets:options.conflictSet?[options.conflictSet]:[],next_cursor:null};
      if(options.conflictRead&&url.endsWith('/attribute-conflicts/set/read'))return options.conflictRead(body);
      if(options.conflictSet&&url.endsWith('/attribute-conflicts/set/read'))return options.conflictSet;
      if(options.conflictSet&&url.endsWith('/attribute-conflicts/set/resolve'))return {id:'set',state:'resolved',records:[]};
      if(options.conflictSources&&url.endsWith('/sources'))return options.conflictSources(body);
      if(options.conflictSet&&url.includes('/attribute-conflicts/set/members/')){
        const id=url.split('/members/')[1].split('/')[0],member=options.conflictSet.members.find(m=>m.record_id===id);
        return url.endsWith('/sources')?{record_id:id,version_id:member.version_id,sources:[],next_cursor:null}:{id,version_id:member.version_id,value:member.value_preview};
      }
      if(url.endsWith('/reviews'))return {reviews:reviewRows};
      if(url.endsWith('/ontology-change-proposals'))return {proposals:[]};
      if(url==='/api/health')return {capabilities:['typed_reviews']};
      if(url.endsWith('/ontology')){if(options.noOntology)throw Error('本项目尚未发布本体。开放本体发现模式需先在本体发现中生成并发布草案。');return {id:'ont',summary:{relations:[],classes:[],attributes:[]}};}
      if(url==='/api/jobs')return {jobs:[job,{...job,id:'job-b',project_id:'b'}]};
      if(url==='/api/projects/a/jobs')return {jobs:[job]};
      if(url==='/api/projects/b/jobs')return {jobs:[]};
      throw Error('Unexpected URL: '+url);}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/task-review.js'),'utf8'),context);
  return {context,get,calls,requests,created,opened,intake,setActor:value=>{actor=value;},setWriter:value=>{writer=value;}};
}

test('unified conflict review reloads exact set and submits no winner until all values are reviewed',async()=>{
  const set={id:'set',entity_id:'owner',predicate:'count',state:'open',version:4,members:[
    {record_id:'a',version:2,version_id:'a2',value_preview:'1',assertion_versions:[{id:'s1',version:3}]},
    {record_id:'b',version:5,version_id:'b5',value_preview:'2',assertion_versions:[{id:'s2',version:6}]}]};
  const f=fixture([],{conflictSet:set});f.context.window.EntityGovernance=require('../../knowledge_service/web/entity-governance.js');
  let resolved=0;await f.context.window.openAttributeConflictReview({id:'set'},{projectId:'a',onResolved:()=>{resolved++;}});
  assert.ok(f.calls.includes('/api/projects/a/attribute-conflicts/set/read'));
  const radios=f.created.filter(node=>node.type==='radio');assert.equal(radios.length,4);assert.ok(radios.every(node=>!node.checked));
  const reason=f.created.find(node=>node.tagName==='textarea');reason.value='已核对';
  const submit=f.created.find(node=>node.textContent==='提交集合审核');await submit.onclick();
  assert.equal(f.requests.filter(r=>r.url.endsWith('/resolve')).length,0);
  radios[0].onchange();radios[3].onchange();await submit.onclick();
  const posted=f.requests.find(r=>r.url.endsWith('/resolve')).body;
  assert.equal(posted.expected_version,4);assert.deepEqual(posted.expected_members,set.members);assert.equal(resolved,1);
  assert.deepEqual(JSON.parse(JSON.stringify(posted.decisions)),[{attribute_id:'a',decision:'accept'},{attribute_id:'b',decision:'reject'}]);
  assert.equal(f.created.find(node=>node.tagName==='dialog').isConnected,false);
});

test('viewer conflict reader keeps evidence available without decision controls',async()=>{
  const f=fixture([],{conflictSet:{id:'set',state:'open',version:1,members:[{record_id:'a',version:1,version_id:'v',value_preview:'1'}]}});
  f.setWriter(false);await f.context.window.openAttributeConflictReview({id:'set'},{projectId:'a'});
  assert.ok(f.created.some(node=>node.textContent==='查看完整值及原文来源'));
  assert.ok(!f.created.some(node=>node.textContent==='提交集合审核'));
});

test('conflict sources paginate and retry the failed cursor without duplicating evidence',async()=>{
  let failed=false;
  const set={id:'set',state:'open',version:1,members:[{record_id:'a',version:1,version_id:'a1',value_preview:'1'}]};
  const f=fixture([],{conflictSet:set,conflictSources:body=>{
    if(body.cursor&&!failed){failed=true;throw Error('分页网络失败');}
    return {record_id:'a',version_id:'a1',sources:[{document_title:body.cursor?'第二页来源':'第一页来源'}],next_cursor:body.cursor?null:'next-page'};
  }});
  await f.context.window.openAttributeConflictReview(set,{projectId:'a'});
  await f.created.find(node=>node.textContent==='查看完整值及原文来源').onclick();
  const more=f.created.find(node=>node.textContent==='更多来源');await more.click();
  assert.equal(f.requests.filter(request=>request.url.endsWith('/sources')).length,2);
  assert.equal(more.textContent,'重试来源');await more.click();
  assert.deepEqual(f.requests.filter(request=>request.url.endsWith('/sources')).map(request=>request.body.cursor),[null,'next-page','next-page']);
  assert.equal(f.created.filter(node=>node.textContent==='第一页来源').length,1);
  assert.equal(f.created.filter(node=>node.textContent==='第二页来源').length,1);assert.equal(more.hidden,true);
});

test('conflict source initial failure is retryable and stale drawers cannot request another page',async()=>{
  let failed=false;
  const set={id:'set',state:'open',version:1,members:[{record_id:'a',version:1,version_id:'a1',value_preview:'1'}]};
  const f=fixture([],{conflictSet:set,conflictSources:()=>{
    if(!failed){failed=true;throw Error('初次网络失败');}
    return {record_id:'a',version_id:'a1',sources:[{document_title:'重试成功'}],next_cursor:'next-page'};
  }});
  await f.context.window.openAttributeConflictReview(set,{projectId:'a'});
  await f.created.find(node=>node.textContent==='查看完整值及原文来源').onclick();
  const retry=f.created.find(node=>node.textContent==='重试来源');await retry.click();
  assert.equal(f.requests.filter(request=>request.url.endsWith('/sources')).length,2);
  assert.equal(f.created.filter(node=>node.textContent==='重试成功').length,1);
  f.context.current='b';f.get('project').onchange();await retry.click();
  assert.equal(f.requests.filter(request=>request.url.endsWith('/sources')).length,2);
});

test('knowledge-review attribute queue opens the same conflict-set drawer',async()=>{
  const f=fixture([],{conflictSet:{id:'set',state:'open',predicate:'count',version:1,members:[]}});
  await f.get('refresh-reviews').onclick();
  const entry=f.created.find(node=>node.textContent==='处理属性冲突集合');assert.ok(entry);
  await entry.onclick();assert.ok(f.calls.includes('/api/projects/a/attribute-conflicts/set/read'));
});

test('pending conflict drawer cannot revive after A to B to A project changes',async()=>{
  let release;const pending=new Promise(resolve=>{release=resolve;});
  const f=fixture([],{conflictRead:()=>pending});
  const opening=f.context.window.openAttributeConflictReview({id:'set'},{projectId:'a'});
  f.context.current='b';f.get('project').onchange();f.context.current='a';f.get('project').onchange();
  release({id:'set',state:'open',version:1,members:[]});await opening;
  assert.ok(!f.created.some(node=>node.textContent==='提交集合审核'));
});

test('scoped conflict summary opens complete four-member set and submits all explicit decisions',async()=>{
  const complete={id:'set',entity_id:'owner',predicate:'count',state:'open',version:1,members:
    ['a','b','c','d'].map((id,index)=>({record_id:id,version:2,version_id:id+'2',value_preview:index+1,
      applicability:{platforms:{mode:'specific',values:[index<2?'美团':'淘宝']}},assertion_versions:[{id:'s'+id,version:3}]}))};
  const original={valid_at:'2026-01-01T00:00:00Z',applicability:{platforms:{mode:'specific',values:['美团']}}};
  const f=fixture([],{conflictSet:complete,conflictRead:body=>body.applicability?{...complete,members:complete.members.slice(0,2)}:complete});
  f.context.window.EntityGovernance=require('../../knowledge_service/web/entity-governance.js');
  await f.context.window.openAttributeConflictReview({id:'set',members:complete.members.slice(0,2)},{projectId:'a',scope:original});
  const radios=f.created.filter(n=>n.type==='radio');assert.equal(radios.length,8);
  assert.ok(f.created.some(n=>n.textContent==='完整冲突集合，包含不同时间/平台范围'));
  await f.created.filter(n=>n.textContent==='查看完整值及原文来源')[2].onclick();
  const detail=f.requests.find(r=>r.url.endsWith('/members/c/read')).body;
  assert.equal(detail.applicability,undefined);assert.equal(detail.valid_at,undefined);
  const sources=f.requests.find(r=>r.url.endsWith('/members/c/sources')).body;assert.equal(sources.applicability,undefined);
  f.created.find(n=>n.tagName==='textarea').value='分别核对两个平台';
  for(const index of [0,3,4,7])radios[index].onchange();
  await f.created.find(n=>n.textContent==='提交集合审核').onclick();
  const posted=f.requests.find(r=>r.url.endsWith('/resolve')).body;
  assert.equal(posted.expected_members.length,4);assert.equal(posted.decisions.length,4);
});

test('withdrawn owner conflict keeps evidence and rejection but disables acceptance',async()=>{
  const f=fixture([],{conflictSet:{id:'set',owner_available:false,state:'open',version:1,
    members:[{record_id:'a',version:1,version_id:'a1',value_preview:'1'}]}});
  await f.context.window.openAttributeConflictReview({id:'set'},{projectId:'a'});
  const radios=f.created.filter(n=>n.type==='radio');
  assert.equal(radios[0].disabled,true);assert.equal(radios[1].disabled,false);
  assert.ok(f.created.some(n=>n.textContent==='实体来源已撤回，仅可拒绝冲突值'));
});

test('refresh uses new project-scoped summary and preserves full logs collapsed',async()=>{
  const {get,calls}=fixture();
  await get('load-jobs').onclick();
  const html=get('tasks').innerHTML,summary=html.split('<details')[0];
  assert.match(html,/job-a/);assert.doesNotMatch(html,/job-b/);
  assert.match(summary,/实体抽取返回 · 3 个/);
  assert.doesNotMatch(summary,/实体抽取返回 · 2 个|仍在等待当前步骤/);
  assert.match(html,/实体抽取返回 · 2 个/);
  assert.match(html,/2026-09-10 18:00:00/);
  assert.match(html,/2026-09-10 18:00:04/);
  assert.match(html,/\[2026-09-10 18:00:01\] 文档开始/);
  assert.doesNotMatch(html,/2026-09-10T10:00:0[0-4]Z/);
  assert.doesNotMatch(html,/<details[^>]* open/);
  assert.ok(!calls.includes('/api/projects/a/reviews'));
});

test('programmatic project switch does not leave previous jobs or load hidden reviews',async()=>{
  const {context,get,calls}=fixture();
  await get('load-jobs').onclick();
  context.current='b';get('project').onchange();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(!calls.includes('/api/projects/b/reviews'));
  assert.match(get('tasks').innerHTML,/当前项目暂无任务/);
});

test('durable task cards offer state-appropriate controls without reviving legacy closures',async()=>{
  for(const [state,action,label] of [['running','cancel','取消任务'],['failed','retry','重试任务'],['cancelled','resume','重新排队']]){
    const {get}=fixture([],{job:{status:state,payload:{text:'private source'}}});
    await get('load-jobs').onclick();
    assert.match(get('tasks').innerHTML,new RegExp('data-job-action="'+action+'"'));
    assert.match(get('tasks').innerHTML,new RegExp(label));
    assert.doesNotMatch(get('tasks').innerHTML,/private source/);
  }
  const {get}=fixture();
  await get('load-jobs').onclick();
  assert.doesNotMatch(get('tasks').innerHTML,/data-job-action/);
});

test('completed discovery task surfaces saved candidate counts without opening full logs',async()=>{
  const {get}=fixture([],{job:{status:'completed',result:{records:[],pending_reviews:0,discovery_candidates:118}}});await get('load-jobs').onclick();
  const summary=get('tasks').innerHTML.split('<details')[0];assert.match(summary,/开放发现候选 118 条/);assert.match(summary,/尚未正式入库/);
});

test('completed discovery tasks open their saved run and document without writing or using the job id',async()=>{
  for(const result of [
    {run_id:'saved-run',document:{id:'saved-doc',metadata:{title:'Saved',run_id:'saved-run',extraction_mode:'discovery'}}},
    {document:{id:'saved-doc',metadata:{run_id:'saved-run',extraction_mode:'discovery',readiness:{candidate_ready:true,graph_ready:false}}}},
  ]){
    const f=fixture([],{job:{status:'completed',result:{discovery_candidates:3,...result}}});await f.get('load-jobs').onclick();
    const button=f.get('tasks').querySelectorAll('[data-open-discovery]')[0];assert.ok(button);assert.match(button.textContent,/核对本次处理的候选/);
    const reads=f.calls.length;await button.click();
    assert.equal(f.opened[0].target,'ontology-model');assert.equal(f.opened[0].projectId,'a');
    assert.equal(f.intake[0].runId,'saved-run');assert.equal(f.intake[0].documentId,'saved-doc');
    assert.equal(f.calls.length,reads,'follow-up never submits or creates a task');
  }
});

test('discovery tasks missing durable identities explicitly offer project-wide current candidates',async()=>{
  const f=fixture([],{job:{status:'completed',logs:['文档开始 · old-log.md · 4 字符'],result:{discovery_candidates:3}}});await f.get('load-jobs').onclick();
  const button=f.get('tasks').querySelectorAll('[data-open-discovery]')[0];assert.ok(button);assert.equal(button.textContent,'核对项目当前发现候选');
  assert.match(f.get('tasks').innerHTML,/显示当前项目全部发现候选，不按本次任务筛选/);
  await button.click();assert.equal(f.intake[0].projectId,'a');assert.equal(f.intake[0].runId,undefined);assert.equal(f.intake[0].documentId,undefined);
});

test('saved discovery document scope is labelled current when the task did not save a run',async()=>{
  const f=fixture([],{job:{status:'completed',result:{discovery_candidates:2,document:{id:'saved-doc',metadata:{readiness:{candidate_ready:true,graph_ready:false}}}}}});
  await f.get('load-jobs').onclick();const button=f.get('tasks').querySelectorAll('[data-open-discovery]')[0];
  assert.equal(button.textContent,'核对这份资料当前发现候选');await button.click();
  assert.equal(f.intake[0].documentId,'saved-doc');assert.equal(f.intake[0].runId,undefined);
});

test('task discovery entry is absent for viewers, unfinished jobs or results without candidates',async()=>{
  for(const [writer,status,count] of [[false,'completed',3],[true,'running',3],[true,'completed',0]]){
    const f=fixture([],{job:{status,result:{discovery_candidates:count}}});f.setWriter(writer);await f.get('load-jobs').onclick();
    assert.equal(f.get('tasks').querySelectorAll('[data-open-discovery]').length,0);assert.equal(f.intake.length,0);
  }
});

test('task discovery scope rejects conflicting newer document metadata',async()=>{
  const f=fixture([],{job:{status:'completed',result:{run_id:'old-run',discovery_candidates:2,document:{id:'doc',metadata:{run_id:'new-run',extraction_mode:'ontology'}}}}});
  await f.get('load-jobs').onclick();await f.get('tasks').querySelectorAll('[data-open-discovery]')[0].click();
  assert.equal(f.intake[0].runId,'old-run');assert.equal(f.intake[0].documentId,undefined);
});

test('stale task discovery buttons cannot follow a different project, actor or read-only role',async()=>{
  for(const change of ['project','roundtrip','actor','role','during-navigation']){
    const f=fixture([],{navigate:change==='during-navigation'?context=>{context.current='b';}:null,job:{status:'completed',result:{run_id:'saved-run',discovery_candidates:3}}});
    await f.get('load-jobs').onclick();const button=f.get('tasks').querySelectorAll('[data-open-discovery]')[0];assert.ok(button);
    if(change==='project')f.context.current='b';
    if(change==='roundtrip'){f.context.current='b';f.get('project').onchange();f.context.current='a';f.get('project').onchange();}
    if(change==='actor')f.setActor({id:'admin-b',role:'admin'});
    if(change==='role')f.setWriter(false);
    await button.click();assert.equal(f.intake.length,0,change);
    if(change!=='during-navigation')assert.equal(f.opened.length,0,change);
  }
});

test('review menu is independent and does not own ingestion settings',async()=>{
  const {context,get,calls,created}=fixture();
  const page=created.find(e=>e.id==='tab-reviews'),nav=created.find(e=>e.dataset.tab==='reviews');
  assert.ok(get('main').children.includes(page));
  assert.match(page.children[0].innerHTML,/实体映射.*关系事实.*属性事实/);
  nav.click();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('/api/projects/a/reviews'));
  assert.ok(!calls.includes('/api/jobs'));
  assert.equal(get('title').textContent,'知识审核');
  assert.ok(!calls.includes('/api/projects/a/ontology-change-proposals'));
  assert.deepEqual(context.readParseSettings(),{chunk_size:1800});
});

test('visible review page requests the new project exactly once on project change',async()=>{
  const {context,get,calls,created}=fixture();
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  context.current='b';
  get('project').onchange();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.filter(url=>url==='/api/projects/b/reviews').length,1);
});

test('missing ontology shows guidance instead of Not found',async()=>{
  const {get,created}=fixture([],{noOntology:true});
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/尚未发布本体/);
  assert.match(html,/前往本体建模层/);
  assert.doesNotMatch(html,/Not found:/);
});

test('old backend is clearly blocked before decisions are offered',async()=>{
  const {context,get,created}=fixture(),api=context.api;
  context.api=async url=>url==='/api/health'?{capabilities:[]}:api(url);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(get('relation-reviews').textContent,/旧版后端.*重启/);
});

test('SHACL exception review names the failed constraint without requiring ontology mapping',async()=>{
  const candidate={id:'validation-1',kind:'validation',status:'pending',record_id:'entity-1',text:'规则文档',
    reason:'本体约束异常',constraint:'MinCountConstraintComponent',path:'https://example.org/title',path_label:'title',
    message:'Less than 1 values',severity:'Violation',document_title:'原文.md',ontology_id:'ont',
    start_char:0,end_char:10,evidence:'规则正文',document_id:'doc',document_version:2};
  const {get,created}=fixture([candidate]);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  get('review-view-exceptions').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/本体异常/);
  assert.match(html,/MinCountConstraintComponent/);
  assert.match(html,/title/);
  assert.match(html,/确认例外/);
  assert.doesNotMatch(html,/映射到当前本体/);
});

test('single-value attribute conflict offers keep-old and accept-new actions',async()=>{
  const candidate={id:'attribute-1',kind:'attribute',status:'contradicting',subject:'甲',
    proposed_type:'https://test/count',target_type:'https://test/count',
    conflict_predicate:'https://test/count',value:2,
    reason:'属性候选',document_title:'原文.md',ontology_id:'ont',start_char:0,end_char:4,
    evidence:'甲数量2',document_id:'doc',document_version:3,entity_version:1,
    current_values:[{record_id:'old-count',value:1,datatype:'http://www.w3.org/2001/XMLSchema#integer',version:1}],
    conflict:{code:'attribute_max_count_one',current_values:[{record_id:'old-count',value:1,version:1}]}};
  const {get,created}=fixture([candidate]);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/保留旧值\/拒绝候选/);
  assert.match(html,/data-action="approve_replace"[^>]*>接受新值/);
  assert.match(html,/当前正式属性值/);
  assert.match(html,/冲突属性已锁定.*count/);
  assert.doesNotMatch(html,/select class="review-target"/);
  assert.doesNotMatch(html,/批准并写入/);
});

test('pending single-value conflict must be registered before replacement',async()=>{
  const candidate={id:'attribute-1',kind:'attribute',status:'pending',subject:'甲',
    proposed_type:'https://test/count',value:2,reason:'属性候选',document_title:'原文.md',
    ontology_id:'ont',start_char:0,end_char:4,evidence:'甲数量2',document_id:'doc',
    document_version:2,entity_version:1,current_values:[{record_id:'old-count',value:1,version:1}],
    conflict:{code:'attribute_max_count_one',current_values:[{record_id:'old-count',value:1,version:1}]}};
  const {get,created}=fixture([candidate]);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/普通批准只会登记冲突/);
  assert.match(html,/批准并写入/);
  assert.doesNotMatch(html,/data-action="approve_replace"/);
});
