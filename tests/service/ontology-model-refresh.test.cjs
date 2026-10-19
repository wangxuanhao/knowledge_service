const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(options = {}) {
  const calls = [], notices = [];
  const root = { innerHTML: '', querySelectorAll: () => [] };
  const sidebar = { innerHTML: '', querySelectorAll: () => [] };
  const summary = id => ({ classes: [{ id, label: id }, ...(options.twoClasses ? [{ id: 'other', label: 'Other' }] : [])], relations: [], attributes: [] });
  const window = {};
  const api = async (url, body, method = 'POST') => {
    calls.push({ url, method });
    if (options.waitForDraftList && url.endsWith('/ontology-drafts') && method === 'GET') await options.waitForDraftList;
    if (options.waitForGeneration && url.endsWith('/ontology-discovery/drafts')) await options.waitForGeneration;
    if (options.waitForReadiness && url.endsWith('/publish-readiness')) await options.waitForReadiness;
    if (options.waitForParents && url.endsWith('/discover-parents')) await options.waitForParents;
    url = url.replace('?compact=true', '').replace('?view=canvas', '');
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
    if (url === '/p/ontology-drafts') return { items: options.olderPending
      ? [{ id: 'old', status: 'pending' }, { id: 'new', status: 'pending' }]
      : [{ id: 'new', status: 'pending' }] };
    if (url === '/p/ontology-drafts/new') return { id: 'new', status: 'pending', ontology: summary('new') };
    if (url === '/p/ontology-drafts/old') return { id: 'old', status: 'accepted', ontology: summary('old') };
    return {};
  };
  const context = {
    window, current: 'p', api, endpoint: suffix => '/p' + suffix,
    document: { getElementById: id => options.withRoot && id === 'tab-ontology-model' ? root
      : options.withSidebar && id === 'om-side' ? sidebar : null, addEventListener() {} },
    status: (message, error) => notices.push({ message, error }), console,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model.js'), 'utf8'), context);
  return { model: window.OntologyModel, calls, notices, root, sidebar,
    switchProject: value => { context.current = value; } };
}

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

test('a generation response for the previous project cannot select its draft in the next project', async () => {
  let release;
  const waitForGeneration = new Promise(resolve => { release = resolve; });
  const f = fixture({ waitForGeneration });
  const generating = f.model.generateDraftFromDiscovery();
  f.switchProject('next'); release(); await generating;
  assert.equal(f.model.S.draft, null);
  assert.equal(f.calls.length, 1);
});

test('candidates already in a draft are shown as awaiting review without another generation action', async () => {
  const f = fixture({ withRoot: true, withSidebar: true, discovery: {
    published: true, unpublished_candidate_count: 110,
    candidate_status_counts: { pending: 0, included_in_draft: 110 },
    latest_run: { unified_draft_id: 'new', status: 'draft_created' },
  } });
  await f.model.refresh();
  assert.match(f.sidebar.innerHTML, /110.*已.*草案/);
  assert.doesNotMatch(f.sidebar.innerHTML, /又抽出|id="om-generate-draft-inline"/);
  await f.model.generateDraftFromDiscovery();
  assert.equal(f.calls.filter(c => c.url.endsWith('/ontology-discovery/drafts')).length, 0);
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

test('generation is single flight including programmatic double invocation', async () => {
  let release;
  const waitForGeneration = new Promise(resolve => { release = resolve; });
  const f = fixture({ waitForGeneration });
  const first = f.model.generateDraftFromDiscovery();
  const second = f.model.generateDraftFromDiscovery();
  try { assert.equal(f.calls.filter(c => c.url.endsWith('/ontology-discovery/drafts')).length, 1); }
  finally { release(); await Promise.all([first, second]); }
});

test('the graph becomes visible before slow publication readiness completes', async () => {
  let release;
  const waitForReadiness = new Promise(resolve => { release = resolve; });
  const f = fixture({ waitForReadiness });
  const refreshing = f.model.refresh();
  try {
    for (let i = 0; i < 30; i++) await Promise.resolve();
    assert.equal(f.model.S.loading, false);
    assert.equal(f.model.S.gatesLoading, true);
    assert.equal(f.model.S.graph.nodes[0].id, 'new');
    assert.ok(f.calls.some(c => c.url.endsWith('/publish-readiness')));
  } finally { release(); await refreshing; }
  assert.equal(f.model.S.gatesLoading, false);
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

test('first entry selects the latest discovery draft among multiple pending drafts', async () => {
  const f = fixture({ olderPending: true });
  await f.model.refresh();
  assert.equal(f.model.S.draft.id, 'new');
});

test('generation opens the returned draft instead of refreshing an older cached draft', async () => {
  const f = fixture();
  f.model.S.draft = { id: 'old', status: 'pending' };
  await f.model.generateDraftFromDiscovery(false);
  assert.equal(f.model.S.draft.id, 'new');
  assert.equal(f.model.S.generating, false);
  assert.match(f.notices.at(-1).message, /草案.*审核/);
  assert.ok(!f.calls.some(call => call.url.endsWith('/publish')), 'generation must not publish automatically');
});

test('existing types require explicit confirmation before candidate finalization', async () => {
  const f = fixture({ mappingOnly: true });
  await f.model.generateDraftFromDiscovery(false);
  assert.match(f.notices.at(-1).message, /确认写入/);
  assert.ok(!f.calls.some(call => call.url.endsWith('/finalize')));
  await f.model.finalizeDiscoveryRun();
  assert.equal(f.calls.filter(call => call.url.endsWith('/finalize')).length, 1);
  assert.match(f.notices.at(-1).message, /3.*检索/);
  assert.ok(!f.calls.some(call => call.url.endsWith('/publish')));
});

test('finalization cannot write a pending vocabulary draft', async () => {
  const f = fixture();
  f.model.S.discovery = { latest_run: { id: 'new', status: 'draft_created', result_kind: 'draft' } };
  await f.model.finalizeDiscoveryRun();
  assert.ok(!f.calls.some(call => call.url.endsWith('/finalize')));
});
