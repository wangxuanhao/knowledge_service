const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(options = {}) {
  const calls = [], notices = [];
  const rootClasses=new Set();
  const root = { innerHTML: '', querySelectorAll: selector => selector==='[data-om-view]'?(options.viewButtons||[]):[], classList:{toggle(name,on){if(on)rootClasses.add(name);else rootClasses.delete(name);}} };
  const sidebar = { innerHTML: '', querySelectorAll: () => [] };
  const summary = id => ({ classes: [{ id, label: id }, ...(options.twoClasses ? [{ id: 'other', label: 'Other' }] : [])], relations: [], attributes: [] });
  const window = options.catalog ? {OntologyCatalog:require('../../knowledge_service/web/ontology-model-catalog.js')} : {};
  if(options.canvas)window.OntologyCanvas=options.canvas;
  const api = async (url, body, method = 'POST') => {
    calls.push({ url, method, body });
    if (options.manualDraft && method === 'POST' && url === '/p/ontology-drafts') return {id:'manual',status:'pending',revision:1};
    if (url === '/p/ontology-drafts/manual') return {id:'manual',status:'pending',revision:1,ontology:summary('manual')};
    if (options.waitForDraftList && url.endsWith('/ontology-drafts') && method === 'GET') await options.waitForDraftList;
    if (options.waitForGeneration && url.endsWith('/ontology-discovery/drafts')) await options.waitForGeneration;
    if (options.waitForFinalization && url.endsWith('/finalize')) await options.waitForFinalization;
    if (options.waitForReadiness && url.includes('/publish-readiness')) await options.waitForReadiness;
    if (options.waitForCommands && url.endsWith('/commands')) {await options.waitForCommands;return {id:'new',status:'pending',revision:3};}
    if (url.includes('/publish-readiness')) return url.includes('view=editing')
      ? options.editingReadiness || {ready:false,validation_checked:false,requires_full_validation:false}
      : options.fullReadiness || {ready:true,validation_checked:true};
    if (options.waitForParents && url.endsWith('/discover-parents')) await options.waitForParents;
    url = url.replace('?compact=true', '').replace('?view=canvas', '');
    if (url === '/p/ontology-drafts/manual') return {id:'manual',status:'pending',revision:1,ontology:summary('manual')};
    if (url.endsWith('/discover-parents')) return { added_count: 0, draft: { id: 'new', status: 'pending', revision: 1 } };
    if (options.waitForOntology && url === '/p/ontology') await options.waitForOntology;
    if (url === '/p/ontology-discovery/drafts') return options.mappingOnly
      ? { result_kind: 'mapping_only', run: { id: 'mapped', status: 'ready_to_finalize', result_kind: 'mapping_only' } }
      : { id: 'new', unified_draft_id: 'new', result_kind: 'draft' };
    if (url === '/p/ontology-discovery/runs/mapped/finalize') return { materialized_count: 3, skipped_candidates: [] };
    if (url === '/p/ontology') return { id: 'published', summary: summary('published') };
    if (url === '/p/ontology-discovery') return options.discovery || { published: true, latest_run: options.mappingOnly
      ? { id: 'mapped', status: 'ready_to_finalize', result_kind: 'mapping_only' }
      : { unified_draft_id: 'new', status: 'draft_created' } };
    if (url === '/p/ontology-drafts' && options.draftListError) throw Error('草案列表读取失败');
    if (url === '/p/ontology-drafts') return { items: options.olderPending
      ? [{ id: 'old', status: 'pending' }, { id: 'new', status: 'pending' }]
      : [{ id: 'new', status: 'pending' }] };
    if (url === '/p/ontology-drafts/new') return { id: 'new', status: options.terminalDetail||'pending', ontology: summary('new') };
    if (url === '/p/ontology-drafts/old') return { id: 'old', status: 'accepted', ontology: summary('old') };
    return {};
  };
  const context = {
    window, current: 'p', api, endpoint: suffix => '/p' + suffix,
    document: { getElementById: id => options.withRoot && id === 'tab-ontology-model' ? root
      : options.withSidebar && id === 'om-side' ? sidebar : id==='om-node-display'?options.displayControl||null
        :id==='om-canvas-host'?options.canvasHost||null:null, addEventListener() {} },
    status: (message, error) => notices.push({ message, error }), console,
    ...(options.gateTimers || {}),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model.js'), 'utf8'), context);
  return { model: window.OntologyModel, calls, notices, root, sidebar, rootClasses,
    switchProject: value => { context.current = value; } };
}

test('multiple pending drafts require an explicit workspace selection before any write',async()=>{
  const f=fixture({olderPending:true});await f.model.refresh();
  assert.equal(f.model.S.draft,null);
  await assert.rejects(f.model.ensureDraft(),/多份.*选择/);
  assert.ok(!f.calls.some(call=>call.method==='POST'));
});

test('initial loading paints the shell before starting the graph renderer in its visible host',async()=>{
  let complete;const pending=new Promise(resolve=>complete=resolve),host={hidden:true},mounts=[];
  const canvas={render(){},mount(target){mounts.push({target,hidden:target?.hidden});}};
  const f=fixture({withRoot:true,canvas,canvasHost:host,waitForOntology:pending});
  const refresh=f.model.refresh();
  assert.ok(f.root.innerHTML.includes('om-canvas-host'),'loading shell is available immediately');
  assert.equal(mounts.length,0,'network loading must not synchronously initialize the graph renderer');
  complete();await refresh;
  assert.equal(mounts.length,1);assert.equal(mounts[0].target,host);assert.equal(mounts[0].hidden,false);
});
test('a failed draft list read cannot prepare an accidental new draft',async()=>{
  const f=fixture({draftListError:true});
  await assert.rejects(f.model.ensureDraft(),/草案列表读取失败/);
  assert.ok(!f.calls.some(call=>call.method==='POST'));
});
test('a completed command cannot replace a different draft selected during the write',async()=>{
  let release;const f=fixture({waitForCommands:new Promise(done=>{release=done;})});
  f.model.S.draft={id:'new',status:'pending',revision:2};const writing=f.model.sendCommand({action:'add_parent'});
  for(let i=0;i<10;i++)await Promise.resolve();f.model.S.draft={id:'other',status:'pending',revision:4};release();
  await assert.rejects(writing,/已保存|目标.*变化/);assert.equal(f.model.S.draft.id,'other');
});
for(const status of ['accepted','rejected'])test(`a fetched ${status} draft cannot remain the editable unpublished work projection`,async()=>{
  const f=fixture({terminalDetail:status});f.model.S.draft={id:'new',status:'pending',revision:2};await f.model.refresh();
  assert.equal(f.model.S.draft,null);assert.equal(f.model.S.showingDraft,false);assert.equal(f.model.S.summary.classes[0].id,'published');
  assert.equal(f.calls.filter(call=>call.url==='/p/ontology'&&call.method==='GET').length,2,'terminal transition requires a fresh formal release read');
});

test('the type view compiles connected property cards without changing inheritance or business endpoints', () => {
  const f=fixture({catalog:true});
  f.model.S.summary={classes:[{id:'p',label:'Parent'},{id:'c',label:'Child',parents:['p']}],
    attributes:[{id:'field',label:'名称',domain:['c'],range:['xsd:string'],required:true}],
    relations:[{id:'relation',label:'关联',domain:['c'],range:['p']}]};
  const before=JSON.stringify(f.model.S.summary),graph=f.model.compileGraph();
  assert.equal(f.model.S.view,'types');
  assert.equal(graph.nodes.find(node=>node.id==='c').attributes[0].datatype,'string');
  assert.equal(graph.edges.find(edge=>edge.kind==='inherit').source,'p');
  assert.equal(graph.edges.find(edge=>edge.kind==='relation').source,'c');
  assert.equal(graph.edges.find(edge=>edge.kind==='relation').termIri,'relation');
  assert.equal(JSON.stringify(f.model.S.summary),before);
});

test('selecting a definition opens a dismissible inspector without changing the chosen term', () => {
  const f=fixture({withRoot:true});
  f.model.S.summary={classes:[{id:'a'}],relations:[],attributes:[]};
  f.model.select('a');
  assert.ok(f.rootClasses.has('om-inspector-open'));
  f.model.setInspector(false);
  assert.equal(f.rootClasses.has('om-inspector-open'),false);
  assert.equal(f.model.S.selected,'a');
  assert.equal(f.calls.length,0);
});

test('a rebuilt shell reflects the retained layout and compact display choice', () => {
  const buttons=['types','hierarchy','cards'].map(view=>({dataset:{omView:view},on:false,
    addEventListener(){},classList:{toggle(_name,value){buttons.find(button=>button.dataset.omView===view).on=value;}}}));
  const display={value:'cards',addEventListener(){}};
  const f=fixture({withRoot:true,viewButtons:buttons,displayControl:display});
  f.model.S.view='hierarchy';f.model.S.nodeDisplay='compact';f.model.render();
  assert.equal(buttons.find(button=>button.on)?.dataset.omView,'hierarchy');
  assert.equal(display.value,'compact');
  assert.equal(display.disabled,false);
  f.model.S.view='cards';f.model.render();assert.equal(display.disabled,true);
});

test('history previews reject draft preparation and commands before any request', async () => {
  const f = fixture();
  f.model.S.preview = { id: 'historic', version: 1 };
  f.model.S.draft = { id: 'pending', status: 'pending', revision: 2 };
  await assert.rejects(f.model.ensureDraft(), /历史.*只读/);
  await assert.rejects(f.model.sendCommand({ action: 'retire_term', target_iri: 'urn:test:A' }), /历史.*只读/);
  assert.equal(f.calls.length, 0);
});




test('incremental candidate action is visibly disabled while viewing history', async () => {
  const f = fixture({ withRoot: true, withSidebar: true, discovery: {
    published: true, candidate_status_counts: { pending: 3 },
  } });
  await f.model.refresh();
  f.model.S.preview = { id: 'historic', version: 1 };
  f.model.select(null);
  assert.match(f.sidebar.innerHTML, /id="om-generate-draft-inline"[^>]*disabled/);
});

test('empty-history candidate generation controls are visibly disabled too', async () => {
  const f = fixture({ withRoot: true, withSidebar: true });
  await f.model.refresh();
  f.model.S.preview = { id: 'empty-history', version: 1 };
  f.model.S.summary = null;
  f.model.S.discovery = { published: false, candidate_count: 2 };
  f.model.select(null);
  assert.match(f.sidebar.innerHTML, /id="om-generate-draft"[^>]*disabled/);
  assert.doesNotMatch(f.sidebar.innerHTML, /id="om-infer-hierarchy"/);
  assert.match(f.sidebar.innerHTML, /发现父类/);
});

test('explicit draft creation selects the created draft without replacing another pending draft', async () => {
  const f = fixture({manualDraft:true});
  f.model.S.draft={id:'new',status:'pending'};
  await f.model.createDraft();
  assert.equal(f.model.S.draft.id,'manual');
  assert.equal(f.model.S.showingDraft,true);
  assert.equal(f.calls.find(call=>call.method==='POST'&&call.url==='/p/ontology-drafts').body.base_ontology_id,'published');
});

test('ensuring a draft cannot write its state into a project selected during the request', async () => {
  let release;
  const waitForDraftList = new Promise(resolve => { release = resolve; });
  const f = fixture({ waitForDraftList });
  const ensuring = f.model.ensureDraft();
  f.switchProject('next'); release();
  await assert.rejects(ensuring, /项目.*切换/);
  assert.equal(f.model.S.draft, null);
  assert.equal(f.calls.length, 1);
});

test('parent discovery remains available and repeatable without new candidates', async () => {
  const f = fixture({ withRoot: true, twoClasses: true, discovery: { published: true,
    candidate_status_counts: { pending: 0, included_in_draft: 110 } } });
  await f.model.refresh();
  assert.match(f.root.innerHTML, /id="om-discover-parents"/);
  await f.model.discoverParents();
  await f.model.discoverParents();
  assert.equal(f.calls.filter(c => c.url.endsWith('/discover-parents')).length, 2);
  assert.ok(!f.calls.some(c => c.url.endsWith('/ontology-discovery/drafts')));
  assert.match(f.notices.at(-1).message, /没有.*新增/);
});

test('parent discovery ignores double clicks while one model request is running', async () => {
  let release;
  const waitForParents = new Promise(resolve => { release = resolve; });
  const f = fixture({ twoClasses: true, waitForParents });
  await f.model.refresh();
  const first = f.model.discoverParents();
  const second = f.model.discoverParents();
  for (let i = 0; i < 20; i++) await Promise.resolve();
  try { assert.equal(f.calls.filter(c => c.url.endsWith('/discover-parents')).length, 1); }
  finally { release(); await Promise.all([first, second]); }
  assert.equal(f.model.S.discoveringParents, false);
});



test('new candidates exclude candidates already included in the pending draft', async () => {
  const f = fixture({ withRoot: true, withSidebar: true, discovery: {
    published: true, unpublished_candidate_count: 340,
    candidate_status_counts: { pending: 230, included_in_draft: 110 },
  } });
  await f.model.refresh();
  assert.match(f.sidebar.innerHTML, /又抽出 230 个新候选/);
  assert.doesNotMatch(f.sidebar.innerHTML, /又抽出 340/);
});


test('canvas refresh finishes while slow readiness remains independently pending', async () => {
  let release;
  const waitForReadiness = new Promise(resolve => { release = resolve; });
  const f = fixture({ waitForReadiness });
  const refreshing = f.model.refresh();
  try {
    for (let i = 0; i < 30; i++) await Promise.resolve();
    assert.equal(f.model.S.loading, false);
    assert.equal(f.model.S.gatesLoading, true);
    assert.equal(f.model.S.graph.nodes[0].id, 'new');
    assert.ok(f.calls.some(c => c.url.includes('/publish-readiness')));
    assert.equal(await Promise.race([refreshing.then(()=>true),new Promise(resolve=>setImmediate(()=>resolve(false)))]),true,
      'a committed edit must not keep its saving status pending on publication readiness');
  } finally { release(); await refreshing;for(let i=0;i<30;i++)await Promise.resolve(); }
  assert.equal(f.model.S.gatesLoading, false);
});

test('preparing an existing draft does not wait for publication projections before allowing its write',async()=>{
  let release;const f=fixture({waitForReadiness:new Promise(resolve=>release=resolve)});
  const preparing=f.model.ensureDraft();
  try{
    assert.equal(await Promise.race([preparing.then(()=>true),new Promise(resolve=>setImmediate(()=>resolve(false)))]),true);
  }finally{release();await preparing;}
});

test('editing readiness never triggers full validation unless the server requests it',async()=>{
  const f=fixture();f.model.S.draft={id:'new',status:'pending',revision:1};
  await f.model.refreshDraftState();
  assert.ok(f.calls.some(call=>call.url.endsWith('/publish-readiness?view=editing')));
  assert.ok(!f.calls.some(call=>call.url.endsWith('/publish-readiness')));
  const eligible=fixture({editingReadiness:{ready:false,requires_full_validation:true}});
  eligible.model.S.draft={id:'new',status:'pending',revision:1};
  await eligible.model.refreshDraftState();
  assert.equal(eligible.calls.filter(call=>call.url.endsWith('/publish-readiness')).length,1);
  assert.equal(eligible.model.S.readiness.validation_checked,true);
});

test('a stalled gate read exits loading, preserves canvas and exposes a retryable error',async()=>{
  let release;const timers=new Set();const f=fixture({waitForReadiness:new Promise(resolve=>release=resolve),gateTimers:{
    setTimeout(fn){timers.add(fn);return fn;},clearTimeout(fn){timers.delete(fn);}}});
  const refreshing=f.model.refresh();
  try{
    for(let i=0;i<30;i++)await Promise.resolve();
    assert.equal(f.model.S.graph.nodes[0].id,'new');
    assert.ok(timers.size>0,'gate requests must have a bounded wait');
    for(const timer of [...timers])timer();
    for(let i=0;i<30;i++)await Promise.resolve();
    assert.equal(f.model.S.gatesLoading,false);
    assert.match(f.model.S.gateLoadError,/重试|未能|超时/);
    assert.notEqual(f.model.S.readiness?.ready,true);
  }finally{release();await refreshing;for(let i=0;i<30;i++)await Promise.resolve();}
});

test('opens the modeling shell before slow network reads finish', async () => {
  let release;
  const waitForOntology = new Promise(resolve => { release = resolve; });
  const f = fixture({ withRoot: true, waitForOntology });
  const refreshing = f.model.refresh();
  try { assert.match(f.root.innerHTML, /om-canvas-host/); }
  finally { release(); await refreshing; }
});

test('refresh replaces a cached accepted draft with the current pending draft', async () => {
  const f = fixture();
  f.model.S.draft = { id: 'old', status: 'accepted' };
  await f.model.refresh();
  assert.equal(f.model.S.draft.id, 'new');
  assert.equal(f.model.S.graph.nodes[0].id, 'new');
});

test('first entry keeps formal structure visible until multiple pending drafts are explicitly selected', async () => {
  const f = fixture({ olderPending: true });
  await f.model.refresh();
  assert.equal(f.model.S.draft, null);
  assert.equal(f.model.S.showingDraft,false);
  assert.equal(f.model.S.summary.classes[0].id,'published');
});
