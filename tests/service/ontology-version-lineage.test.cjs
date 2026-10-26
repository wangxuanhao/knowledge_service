const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const all = node => [node, ...node.children.flatMap(all)];
function fixture({ actions = false, syncActions } = {}) {
  let root;
  const element = tag => ({
    tag, children: [], attrs: {}, listeners: {}, dataset: {}, textContent: '', value: '', style: {}, parent: null,
    classList: { toggle() {}, add() {}, remove() {} },
    get isConnected() { return this === root || !!this.parent?.isConnected; },
    append(...items) {
      for (const item of items) {
        if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item);
        item.parent = this; this.children.push(item);
      }
    },
    replaceChildren(...items) { this.children.forEach(item => { item.parent = null; }); this.children = []; this.append(...items); },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    querySelector(selector) { return all(this).find(node => selector.startsWith('.') ? node.className === selector.slice(1) : node.tag === selector); },
    focus() { this.focusCount = (this.focusCount || 0) + 1; },
  });
  root = element('root');
  const actionHost = element('actions'), footer = element('footer');
  root.append(actionHost, footer);
  const requests = [], calls = [], opened = [], events = {}, messages = [];
  const state = actions ? {
    summary: { classes: [], relations: [], attributes: [] }, ontologyId: 'current-release', preview: null,
    draft: { id: 'active-draft', revision: 2, status: 'pending', operations: [], validation_fingerprint: 'fp' },
    stageAvailability: { stages: { review: { allowed: true } } },
    readiness: { ready: true, items: [] },
  } : {};
  const model = { S: state, status: (message, error) => messages.push({ message, error }),
    openDraftHistory: id => opened.push(id), refresh: async () => {},
    on(name, handler) { events[name] = handler; return () => {}; },
  };
  const context = {
    window: { OntologyModel: model }, current: 'p', console,
    document: { createElement: element, getElementById: id => id === 'tab-ontology-model' ? root : null, body: root },
    endpoint: suffix => '/' + context.current + suffix,
    setTimeout() { return 1; }, clearTimeout() {},
    api: async (url, body, method = 'GET') => {
      calls.push({ url, body, method });
      if (url.endsWith('/ontologies')) return new Promise(resolve => requests.push({ url, resolve }));
      if (url.endsWith('/validate')) return new Promise(resolve => { context.finishValidation = resolve; });
      if (url.endsWith('/decisions/batch-approve')) return { draft: state.draft, batch: {} };
      return method === 'GET' ? {} : state.draft;
    },
  };
  if (syncActions) context.window.OntologyWorkspace = { syncActions: () => syncActions({ actionHost, footer, all }) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model-panel.js'), 'utf8'), context);
  const panel = context.window.OntologyPanel;
  const mount = () => panel.mount(null, actions ? actionHost : null, model);
  mount();
  return { root, actionHost, footer, requests, calls, opened, events, messages, state, model, context, panel, mount,
    find: text => all(root).find(node => node.textContent === text),
    selected: () => all(root).find(node => node.className === 'om-history-item sel'),
  };
}
const releases = [
  { id: 'release-a', version: 3, metadata: { publication: { draft_id: 'draft-a' } } },
  { id: 'release-b', version: 3, metadata: { publication: { draft_id: 'draft-b' } } },
];
async function open(f, id, versions = releases) {
  const pending = f.panel.openHistory(id);
  f.requests.at(-1).resolve({ versions });
  await pending;
}

test('version links select the exact ontology ID even when publications share a display version', async () => {
  const f = fixture(); await open(f, 'release-a');
  assert.ok(all(f.selected()).some(node => node.textContent === 'release-a'));
  assert.ok(!all(f.selected()).some(node => node.textContent === 'release-b'));
  assert.ok(f.find('来源草案'));
  assert.ok(f.find('draft-a'));
  assert.ok(f.find('查看来源草案'));
});

test('source draft navigation uses the recorded ID and preserves the active editing state', async () => {
  const f = fixture(), draft = { id: 'working-draft', revision: 7 };
  f.state.draft = draft; f.state.selected = 'urn:selected'; f.state.preview = { id: 'preview-id' };
  await open(f, 'release-b');
  assert.ok(f.find('查看来源草案'));
  await f.find('查看来源草案').listeners.click();
  assert.deepEqual(f.opened, ['draft-b']);
  assert.equal(f.state.draft, draft);
  assert.equal(f.state.selected, 'urn:selected');
  assert.equal(f.state.preview.id, 'preview-id');
  assert.ok(f.calls.every(call => call.method === 'GET'));
  assert.equal(all(f.root).find(node => node.className === 'om-history').attrs.hidden, '');
});

test('legacy publications state that no source draft was recorded', async () => {
  const f = fixture(); await open(f, 'legacy', [{ id: 'legacy', version: 1 }]);
  assert.ok(f.find('未记录来源草案'));
  assert.equal(f.find('查看来源草案'), undefined);
});

test('an unavailable exact publication never silently selects a different version', async () => {
  const f = fixture(); await open(f, 'missing-release');
  assert.equal(f.selected(), undefined);
  assert.ok(all(f.root).some(node => String(node.textContent).includes('未找到这条已发布本体版本')));
  assert.equal(f.find('查看来源草案'), undefined);
});

test('retained source controls cannot route after selection, project change, closing or reopening', async () => {
  const f = fixture(); await open(f, 'release-b');
  let stale = f.find('查看来源草案');
  assert.ok(stale);
  const other = all(f.root).find(node => node.className === 'om-history-item');
  other.children[0].listeners.click();
  await stale.listeners.click();
  assert.deepEqual(f.opened, []);
  stale = f.find('查看来源草案');
  f.context.current = 'q'; await stale.listeners.click();
  assert.deepEqual(f.opened, []);
  f.context.current = 'p'; f.panel.closeHistory(); await stale.listeners.click();
  assert.deepEqual(f.opened, []);
  await open(f, 'release-a'); await stale.listeners.click();
  assert.deepEqual(f.opened, []);
});

test('the latest pending exact-ID navigation wins and the no-argument entry preserves selection', async () => {
  const f = fixture();
  const first = f.panel.openHistory('release-b');
  const second = f.panel.openHistory('release-a');
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve({ versions: releases }); await Promise.all([first, second]);
  assert.ok(all(f.selected()).some(node => node.textContent === 'release-a'));
  await open(f, undefined);
  assert.ok(all(f.selected()).some(node => node.textContent === 'release-a'));
});

test('a newer list selection is not overwritten by an older pending exact-ID open', async () => {
  const f = fixture(); await open(f, 'release-a');
  const pending = f.panel.openHistory('release-a');
  const newerSelection = all(f.root).find(node => node.className === 'om-history-item');
  newerSelection.children[0].listeners.click();
  f.requests.at(-1).resolve({ versions: releases }); await pending;
  assert.ok(all(f.selected()).some(node => node.textContent === 'release-b'));
});

test('closing a pending history open prevents delayed detail reads and focus', async () => {
  const f = fixture(), pending = f.panel.openHistory('release-a');
  const overlay = all(f.root).find(node => node.className === 'om-history');
  f.panel.closeHistory(); f.requests[0].resolve({ versions: releases }); await pending;
  assert.equal(overlay.attrs.hidden, '');
  assert.equal(f.calls.filter(call => call.url.includes('/knowledge-context')).length, 0);
  assert.ok(all(overlay).every(node => !node.focusCount));
});

test('project A to B to A cannot apply an obsolete requested publication', async () => {
  const f = fixture(), first = f.panel.openHistory('obsolete-a');
  f.context.current = 'q'; f.mount(); const second = f.panel.openHistory('q-release');
  f.context.current = 'p'; f.mount(); const third = f.panel.openHistory('release-a');
  f.requests[2].resolve({ versions: releases }); await third;
  f.requests[1].resolve({ versions: [{ id: 'q-release', version: 9 }] }); await second;
  f.requests[0].resolve({ versions: [{ id: 'obsolete-a', version: 1 }] }); await first;
  assert.ok(all(f.selected()).some(node => node.textContent === 'release-a'));
  assert.equal(f.find('q-release'), undefined);
  assert.equal(f.find('obsolete-a'), undefined);
});

test('workspace receives the actual controls after mount, background gates and direct busy renders', async () => {
  const observed = [];
  const f = fixture({ actions: true, syncActions({ actionHost, footer, all }) {
    const controls = all(actionHost).find(node => node.className === 'om-ab-actions');
    assert.ok(controls, 'syncActions runs only after fresh controls exist');
    observed.push(controls); footer.replaceChildren(controls);
  } });
  assert.equal(observed.length, 1);
  const input = all(f.footer).find(node => node.className === 'om-pub-note');
  input.value = '保留的发布说明'; input.listeners.input();
  f.events['stage:changed']();
  assert.equal(observed.length, 2);
  assert.equal(all(f.footer).find(node => node.className === 'om-pub-note').value, '保留的发布说明');
  assert.notEqual(observed[0], observed[1]);
  assert.equal(f.footer.children[0], observed[1]);
  const review = f.find('校验并审核'), pending = review.listeners.click();
  assert.ok(observed.length >= 3, 'beginAction renders directly and must replace the footer controls');
  assert.equal(all(f.footer).find(node => node.tag === 'button' && node.textContent === '正在校验草案…').disabled, true);
  assert.equal(f.actionHost.attrs['aria-busy'], 'true');
  f.context.finishValidation(f.state.draft); await pending;
  assert.equal(f.actionHost.attrs['aria-busy'], 'false');
  assert.ok(f.find('校验并审核'));
  assert.equal(all(f.root).filter(node => node.className === 'om-ab-actions').length, 1);
  assert.equal(f.calls.filter(call => call.url.endsWith('/validate')).length, 1);
  f.requests[0].resolve({ versions: [] }); await Promise.resolve(); await Promise.resolve();
});

test('empty action rendering also notifies the optional workspace hook', () => {
  let count = 0;
  const f = fixture({ actions: true, syncActions() { count++; } });
  f.state.summary = null; f.panel.render(f.state);
  assert.equal(count, 2);
});
