const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element(tag) {
  return { tag, children: [], listeners: {}, value: '', checked: false, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    append(...items) { this.children.push(...items); }, appendChild(item) { this.children.push(item); },
    replaceChildren(...items) { this.children = items; }, setAttribute(name,value) { this[name]=value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
  };
}
const all = node => [node, ...node.children.flatMap(all)];
function panelFixture(preview = false, active = false) {
  const host = element('host'), commands = [], calls = [];
  const S = { selected: 'urn:test:A', preview: preview ? { id: 'old' } : null,
    summary: { classes: [{ id: 'urn:test:A', label: '类A', active, parents: [] }], relations: [], attributes: [] } };
  const window = { OntologyModel: { S, status() {}, sendCommand: async cmd => commands.push(cmd) } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model-panel.js'), 'utf8'), {
    window, document: { createElement: element }, current: 'p', endpoint: suffix => '/p' + suffix,
    api: async url => { calls.push(url); return url.endsWith('/ontologies')
      ? { versions: [{ id: 'source', version: 2 }] } : { record_count: 3 }; }, console,
  });
  window.OntologyPanel.mount(host, null, window.OntologyModel);
  return { host, S, commands, calls, model: window.OntologyModel, panel: window.OntologyPanel, find: text => all(host).find(x => x.textContent === text) };
}
async function flush() { for (let i = 0; i < 15; i++) await Promise.resolve(); }

test('manual creation uses the shared draft command helper with the selected parent', async () => {
  const f = panelFixture(false, true), created = [];
  f.S.selected = null;
  f.model.createTerm = async spec => created.push(JSON.parse(JSON.stringify(spec)));
  f.panel.openCreate({kind: 'class', parent: 'urn:test:A'});
  const inputs = all(f.host).filter(node => node.tag === 'input');
  inputs[0].value = '优惠券'; inputs[0].listeners.input();
  await f.find('新建并写入草案').listeners.click();
  assert.equal(created.length, 1);
  assert.equal(created[0].parent, 'urn:test:A');
  assert.equal(created[0].label_zh, '优惠券');
  assert.ok(f.calls.every(url => !url.includes('/ontology/terms')));
});

test('background gate completion and retry preserve the unfinished next term', async () => {
  const host = element('host'), window = {};
  let release;
  const context = {
    window, current: 'p', endpoint: suffix => '/p' + suffix, console,
    document: { getElementById: () => null, createElement: element, addEventListener() {} },
    api: async url => url.includes('publish-readiness') ? await new Promise(resolve => { release = resolve; })
      : { stages: { review: { allowed: true } } },
  };
  for (const file of ['ontology-model.js', 'ontology-model-panel.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web', file), 'utf8'), context);
  }
  const M = window.OntologyModel;
  M.S.summary = { classes: [], relations: [], attributes: [] };
  M.S.draft = { id: 'draft', status: 'pending', revision: 1 };
  window.OntologyPanel.mount(host, null, M);
  window.OntologyPanel.openCreate({kind:'class'});
  const name = all(host).find(node => node.tag === 'input');
  assert.ok(name);
  for (const readiness of [null, { ready: false, requires_full_validation: false }]) {
    const pending = M.refreshDraftState();
    name.value = '优惠券';
    release(readiness);
    await pending;
    assert.equal(all(host).find(node => node.tag === 'input'), name, 'gates must not replace the form');
    assert.equal(name.value, '优惠券');
  }
});

test('restoring a term requires an explicit source release and selected definition fields', async () => {
  const f = panelFixture(); await flush();
  const source = all(f.host).find(x => x.className === 'om-restore-source');
  assert.ok(source, 'a source release selector is required');
  const reason = all(f.host).find(x => x.tag === 'textarea');
  reason.value = '恢复业务定义'; reason.listeners.input();
  assert.equal(f.find('确认恢复').disabled, true);
  source.value = 'source'; source.listeners.change();
  const field = all(f.host).find(x => x.value === 'parents' && x.type === 'checkbox');
  field.checked = true; field.listeners.change();
  assert.equal(f.find('确认恢复').disabled, false);
  await f.find('确认恢复').listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(f.commands[0])), {
    action: 'restore_term', target_iri: 'urn:test:A', reason: '恢复业务定义',
    source_ontology_id: 'source', selected_fields: ['parents'],
  });
});

test('active draft definitions are visibly unpublished and cannot claim extraction availability',async()=>{
  const f=panelFixture(false,true);f.S.showingDraft=true;f.S.draft={id:'draft',revision:4,status:'pending'};f.panel.render(f.S);
  assert.ok(f.find('草案中启用'));
  assert.ok(f.find('未发布'));
  assert.ok(!f.find('生效'));
  assert.ok(!all(f.host).some(x=>String(x.textContent).includes('可被检索/抽取使用')));
});

test('history inspector preserves definitions while omitting all write controls', async () => {
  const f = panelFixture(true, true); await flush();
  assert.ok(f.find('类A'));
  assert.ok(!all(f.host).some(x => ['input', 'textarea', 'select'].includes(x.tag)));
  assert.ok(!f.find('确认加入停用变更'));
  f.S.selected = null; f.panel.render(f.S);
  assert.ok(!f.find('新建并写入草案'));
});

test('an unpublished empty project renders its action bar and routes pending candidates through selected intake',()=>{
  const actions=element('actions'),opened=[],legacy=[];
  const state={summary:null,draft:null,discovery:null,preview:null};
  const window={OntologyModel:{S:state,status(){},openIntake:()=>opened.push('intake'),generateDraftFromDiscovery:()=>legacy.push('legacy')}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),{
    window,document:{createElement:element},current:'p',console,
  });
  assert.doesNotThrow(()=>window.OntologyPanel.mount(null,actions,window.OntologyModel));
  state.discovery={published:false,candidate_count:3,candidate_status_counts:{pending:3},entity_types:['客户']};
  window.OntologyPanel.render(state);
  const pending=all(actions).find(node=>node.textContent==='选择候选并预览');assert.ok(pending);
  pending.listeners.click();assert.deepEqual(opened,['intake']);assert.deepEqual(legacy,[]);
  state.preview={id:'history'};window.OntologyPanel.render(state);
  assert.equal(all(actions).find(node=>node.textContent==='选择候选并预览').disabled,true);
});

test('normal draft action bar reports its exact linked knowledge scope',()=>{
  const actions=element('actions');
  const state={summary:{classes:[],relations:[],attributes:[]},draft:{id:'draft',status:'pending',revision:2,operations:[{action:'create_term'}],associated_knowledge:{selected:3,per_batch:[{run_id:'run-1',selected:3}]}},preview:null};
  const window={OntologyModel:{S:state,status(){}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),{
    window,document:{createElement:element},current:'p',console,
  });
  window.OntologyPanel.mount(null,actions,window.OntologyModel);
  assert.ok(all(actions).some(node=>node.textContent==='关联候选事实'));
  assert.ok(all(actions).some(node=>String(node.textContent).includes('明确选中 3 条')));
});

function actionFixture(options={}){
  const actions=element('actions'),calls=[],messages=[],timers=[];
  const state={summary:{classes:[{id:'urn:Customer',label_zh:'客户'}],relations:[],attributes:[]},ontologyId:'release',
    draft:{id:'draft',status:'pending',revision:2,base_ontology_id:'release',operations:[{action:'create_term',target_iri:'urn:Customer',after:{kind:'class'}}],
      associated_knowledge:{selected:3,per_batch:[{run_id:'source-run',candidate_ids:['a','b','c'],selected:3}]},...options.draft},
    stageAvailability:{stages:{review:{allowed:true}}},readiness:options.readiness||{ready:true,approval_counts:{approved:1},items:[]},preview:null};
  Object.assign(state,options.state||{});
  const model={S:state,status:(message,error)=>messages.push({message,error}),uid:()=> 'publish-key',refresh:async()=>{}};
  const context={window:{OntologyModel:model},current:'p',console,CustomEvent:function(type,init){this.type=type;this.detail=init.detail;},
    document:{createElement:element,dispatchEvent(){}},endpoint:suffix=>'/api/projects/'+context.current+suffix,
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
    api:async(url,body,method='GET')=>{calls.push({url,body,method});if(url.endsWith('/ontologies'))return {versions:[]};
      if(options.request)return options.request(url,body,method);
      return url.endsWith('/decisions/batch-approve')?{draft:state.draft,batch:{approved:1}}:state.draft;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),context);
  context.window.OntologyPanel.mount(null,actions,model);
  return {actions,state,calls,messages,timers,context,panel:context.window.OntologyPanel,find:text=>all(actions).find(node=>node.textContent===text)};
}

test('submitted review resumes server eligible bulk approval without validation or resubmission',async()=>{
  const f=actionFixture({draft:{submitted_at:'now',validation_fingerprint:'frozen'},state:{gatesLoading:true,reviewGateLoading:false,
    reviewPlan:{revision:2,items:Array.from({length:188},()=>({auto_approvable:true}))}}});
  const button=f.find('批量批准（188 项变更）');assert.ok(button);assert.equal(button.disabled,false);
  await button.listeners.click();
  assert.ok(!f.calls.some(c=>/\/(validate|submit)$/.test(c.url)));
  const call=f.calls.find(c=>c.url.endsWith('/decisions/batch-approve'));
  assert.equal(call.body.expected_revision,2);assert.equal(call.body.validation_fingerprint,'frozen');
});

test('draft footer distinguishes definitions and facts and exposes every server blocker with the real next step',()=>{
  const f=actionFixture({readiness:{ready:false,approval_counts:{approved:0},items:[
    {code:'not_submitted',severity:'error',message:'这份请求还没有提交过审核'},
    {code:'decision_pending',severity:'error',message:'188 条变更尚未形成最终决定'},
  ]}});
  for(const label of ['实体类型','关系类型','属性','关联候选事实'])assert.ok(f.find(label),label);
  assert.ok(!f.find('结构变更'),'low-level operations must not be presented as definition counts');
  assert.ok(all(f.actions).some(node=>String(node.textContent).includes('进入候选核对页查看')));
  for(const message of ['这份请求还没有提交过审核','188 条变更尚未形成最终决定'])assert.ok(f.find(message),message);
  assert.ok(all(f.actions).some(node=>String(node.textContent).includes('下一步：')&&String(node.textContent).includes('校验并审核')));
  assert.ok(all(f.actions).some(node=>String(node.textContent).includes('按定义审核')));
});

test('validation shows immediate local progress, rejects duplicate clicks and reports a long request',async()=>{
  let finish;const f=actionFixture({request:async url=>url.endsWith('/validate')?new Promise(resolve=>finish=resolve):url.endsWith('/decisions/batch-approve')?{draft:f.state.draft,batch:{approved:1}}:f.state.draft});
  const initial=f.find('校验并审核'),pending=initial.listeners.click();
  const loading=all(f.actions).find(node=>node.tag==='button'&&node.textContent==='正在校验草案…');assert.ok(loading);assert.equal(loading.disabled,true);assert.equal(f.actions['aria-busy'],'true');
  await initial.listeners.click();assert.equal(f.calls.filter(call=>call.url.endsWith('/validate')).length,1);
  f.timers[0]();assert.ok(all(f.actions).some(node=>String(node.textContent).includes('仍在处理')));
  finish(f.state.draft);await pending;assert.equal(f.actions['aria-busy'],'false');assert.ok(f.find('校验并审核'));
});

test('validation never submits or mutates another project after its pending request completes',async()=>{
  let finish;const f=actionFixture({request:async url=>url.endsWith('/validate')?new Promise(resolve=>finish=resolve):{}});
  const pending=f.find('校验并审核').listeners.click();f.context.current='q';f.state.draft={id:'q-draft',revision:5,status:'pending'};f.panel.render(f.state);
  finish({id:'draft',revision:2,validation_fingerprint:'fp'});await pending;
  assert.equal(f.state.draft.id,'q-draft');assert.ok(!f.calls.some(call=>/\/(submit|decisions\/batch-approve)$/.test(call.url)));
});

test('publication locks locally before its first read and preserves the fingerprint and idempotency payload',async()=>{
  let finish;const f=actionFixture({draft:{validation_fingerprint:'fp',submitted_at:'now'},request:async(url,body,method)=>{
    if(url.endsWith('/publish-readiness'))return new Promise(resolve=>finish=resolve);
    if(url.endsWith('/publish'))return {id:'new-release',metadata:{version:2}};
    return f.state.draft;
  }});
  const input=all(f.actions).find(node=>node.className==='om-pub-note');input.value='补充客户定义';input.listeners.input();
  const publish=all(f.actions).find(node=>node.tag==='button'&&/^发布/.test(node.textContent)),pending=publish.listeners.click();
  const loading=all(f.actions).find(node=>node.tag==='button'&&node.textContent==='正在确认发布门禁…');assert.ok(loading);assert.equal(loading.disabled,true);
  await publish.listeners.click();assert.equal(f.calls.filter(call=>call.url.endsWith('/publish-readiness')).length,1);
  finish({ready:true,revision:2,validation_fingerprint:'fp'});await pending;
  const write=f.calls.find(call=>call.url.endsWith('/publish'));assert.equal(write.body.expected_revision,2);assert.equal(write.body.validation_fingerprint,'fp');
  assert.equal(write.body.idempotency_key,'publish-key');assert.equal(write.body.note,'补充客户定义');assert.equal(f.actions['aria-busy'],'false');
});

test('finishing validation clears local busy state even when refresh returns an empty model',async()=>{
  const f=actionFixture();f.context.window.OntologyModel.refresh=async()=>{f.state.summary=null;};
  await f.find('校验并审核').listeners.click();assert.equal(f.actions['aria-busy'],'false');
});

test('content validation errors keep their error treatment while workflow conditions provide next steps',()=>{
  const f=actionFixture({readiness:{ready:false,items:[{code:'datatype_mismatch',severity:'error',message:'金额字段需要整数',remediation:'修复金额定义后重新校验'}]}});
  assert.ok(f.find('金额字段需要整数'));assert.ok(f.find('下一步：修复金额定义后重新校验'));
  assert.ok(all(f.actions).some(node=>node.className==='om-ab-blocker is-error'));
  assert.equal(all(f.actions).find(node=>node.tag==='button'&&/^发布/.test(node.textContent)).disabled,true);
});

test('an incomplete linked-fact summary remains unknown instead of claiming zero facts',()=>{
  const f=actionFixture({draft:{associated_knowledge:{per_batch:[]}}});
  const chip=all(f.actions).find(node=>node.className==='om-ab-chip'&&node.children.some(child=>child.textContent==='关联候选事实'));
  assert.equal(chip.children[0].textContent,'—');
  assert.ok(all(f.actions).some(node=>String(node.textContent).includes('候选事实数量尚未读取')));
});

test('publication warnings remain explicit and frozen while a guarded request is running',async()=>{
  let finish;const f=actionFixture({readiness:{ready:true,approval_counts:{approved:1},items:[{code:'warning-1',severity:'warning',message:'需要人工确认的警告'}]},
    request:async url=>url.endsWith('/publish-readiness')?new Promise(resolve=>finish=resolve):f.state.draft});
  const input=all(f.actions).find(node=>node.className==='om-pub-note');input.value='发布前已核对';input.listeners.input();
  const publish=all(f.actions).find(node=>node.tag==='button'&&/^发布/.test(node.textContent));assert.equal(publish.disabled,true);
  const tick=all(f.actions).find(node=>node.type==='checkbox');assert.equal(tick.checked,false);tick.checked=true;tick.listeners.change();assert.equal(publish.disabled,false);
  const pending=publish.listeners.click();assert.equal(all(f.actions).find(node=>node.type==='checkbox').disabled,true);
  finish({ready:false,items:[{code:'validation_changed',severity:'error',message:'需要重新校验'}]});await pending;
  assert.ok(!f.calls.some(call=>call.url.endsWith('/publish')));assert.equal(f.actions['aria-busy'],'false');
});
