const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const sourcePath = path.join(__dirname, '../../knowledge_service/web/ontology-draft-history.js');
const all = node => [node, ...node.children.flatMap(all)];
const visibleText = node => [node.textContent, ...node.children
  .filter(child => node.tag !== 'details' || node.open || child.tag === 'summary')
  .map(visibleText)].join(' ');
const textOf = node => all(node).map(item => item.textContent).join(' ');
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const element = tag => ({tag, children: [], attrs: {}, dataset: {}, listeners: {}, parent: null,
    textContent: '', hidden: false, open: false,
    get isConnected() { return this.tag === 'root' || !!this.parent?.isConnected; },
    append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } },
    replaceChildren(...items) { this.children.forEach(item => item.parent = null); this.children = []; this.append(...items); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    click() { return this.listeners.click?.({preventDefault() {}}); },
  });
  const root = element('root'), host = element('div'); root.append(host);
  const requests = [], calls = [];
  const state = {draft: {id: 'working', revision: 7}, selected: 'urn:Current', showingDraft: true,
    preview: null, summary: {classes: [{id: 'urn:Customer', label_zh: '当前的新名称'}]}};
  const before = JSON.stringify(state);
  Object.freeze(state.draft); Object.freeze(state);
  const model = {S: state, helpers: {label: item => item.label_zh || item.label || item.name || item.id}};
  const versions = [], opened = [];
  const context = {window: {}, current: 'p', console, document: {createElement: element},
    endpoint: suffix => `/api/projects/${context.current}${suffix}`,
    api: (url, body, method) => { calls.push({url, body, method}); return new Promise((resolve, reject) => requests.push({url, resolve, reject})); },
  };
  if (fs.existsSync(sourcePath)) vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), context);
  const history = context.window.OntologyDraftHistory;
  assert.ok(history, 'the read-only history module must be registered');
  history.mount(host, {model, onVersion: id => versions.push(id)});
  return {history, context, root, host, requests, calls, versions, opened, state, before, model, element,
    button: name => all(host).find(node => node.tag === 'button' && node.textContent.includes(name))};
}
const draft = (id = 'archived', extra = {}) => ({id, title: '客户定义调整', revision: 3,
  status: 'accepted', operations: [], decisions: [], source_context: {}, ...extra});

test('mount is lazy and direct read-only details never replace or modify the working draft', async () => {
  const f = fixture(); assert.equal(f.calls.length, 0);
  const showing = f.history.show('pending / draft');
  assert.equal(f.calls[0].url, '/api/projects/p/ontology-drafts/pending%20%2F%20draft');
  f.requests[0].resolve(draft('pending / draft', {status: 'pending', ontology: {classes: [{id: 'urn:Customer', label_zh: '旧客户'}]},
    operations: [{id: 'operation-opaque', action: 'create_term', target_iri: 'urn:Customer', after: {kind: 'class'}}]}));
  await showing;
  assert.match(visibleText(f.host), /待处理/); assert.match(visibleText(f.host), /只读/);
  assert.match(visibleText(f.host), /旧客户/); assert.doesNotMatch(visibleText(f.host), /当前的新名称|operation-opaque|urn:Customer/);
  assert.ok(!all(f.host).some(node => ['input', 'textarea', 'select'].includes(node.tag)));
  assert.ok(!all(f.host).some(node => node.tag === 'button' && /审核|发布|收下|驳回|恢复/.test(node.textContent)));
  assert.equal(JSON.stringify(f.state), f.before);
  assert.ok(f.calls.every(call => call.method === 'GET' && call.body === undefined));
});

test('directory reads all statuses only on demand and opens a selected immutable draft', async () => {
  const f = fixture(), opening = f.history.showList();
  assert.equal(f.calls[0].url, '/api/projects/p/ontology-drafts');
  f.requests[0].resolve({items: [draft('a', {title: '已采用草案'}), draft('published', {title: '已发布草案', published_ontology_id: 'publication-id'}),
    draft('r', {title: '已关闭草案', status: 'rejected'}), draft('p', {title: '仍待处理', status: 'pending'})]});
  await opening;
  assert.match(visibleText(f.host), /已收下/); assert.match(visibleText(f.host), /已驳回/); assert.match(visibleText(f.host), /待处理/);
  assert.ok(all(f.host).some(node => node.className === 'om-dh-status' && node.textContent === '已发布'));
  f.button('已关闭草案').click();
  assert.equal(f.calls.at(-1).url, '/api/projects/p/ontology-drafts/r');
  f.requests.at(-1).resolve(draft('r', {status: 'rejected'})); await flush();
  assert.match(visibleText(f.host), /已驳回/);
  const calls = f.calls.length; await f.history.showList(); assert.equal(f.calls.length, calls, 'a completed directory is cached');
});

test('base and result links resolve labels yet navigate by distinct exact publication IDs', async () => {
  const f = fixture(), showing = f.history.show('a');
  f.requests[0].resolve(draft('a', {base_ontology_id: 'base-id', published_ontology_id: 'result-id'})); await showing;
  assert.ok(all(f.host).some(node => node.className === 'om-dh-status' && node.textContent === '已发布'));
  assert.equal(f.calls.at(-1).url, '/api/projects/p/ontologies');
  f.requests.at(-1).resolve({versions: [{id: 'base-id', metadata: {version: 2}}, {id: 'result-id', metadata: {version: 2}}]}); await flush();
  f.button('基线版本').click(); f.button('产出版本').click();
  assert.deepEqual(f.versions, ['base-id', 'result-id']);
  assert.match(f.button('基线版本').textContent, /v2/); assert.match(f.button('产出版本').textContent, /v2/);
  assert.doesNotMatch(visibleText(f.host), /base-id|result-id/);
  assert.equal(JSON.stringify(f.state), f.before);
});

test('A to B to A draft reads reject superseded responses even when IDs repeat', async () => {
  const f = fixture(), first = f.history.show('a'), second = f.history.show('b'), third = f.history.show('a');
  f.requests[2].resolve(draft('a', {title: '最新 A'})); await third;
  f.requests[1].resolve(draft('b', {title: '过时 B'})); await second;
  f.requests[0].resolve(draft('a', {title: '过时 A'})); await first;
  assert.match(visibleText(f.host), /最新 A/); assert.doesNotMatch(visibleText(f.host), /过时/);
});

test('project and host remount invalidate reads and version callbacks from old content', async () => {
  const f = fixture(), old = f.history.show('a');
  f.context.current = 'q'; const host = f.element('div'); f.root.replaceChildren(host);
  f.history.mount(host, {model: f.model, onVersion: id => f.versions.push(id)});
  const latest = f.history.show('a');
  assert.equal(f.calls[1].url, '/api/projects/q/ontology-drafts/a');
  f.requests[1].resolve(draft('a', {title: '新项目草案'})); await latest;
  f.requests[0].resolve(draft('a', {title: '旧项目草案'})); await old;
  assert.match(visibleText(host), /新项目草案/); assert.doesNotMatch(visibleText(host), /旧项目草案/);
});

test('suspend preserves completed views and reset invalidates pending reads and caches', async () => {
  const f = fixture(), opening = f.history.showList();
  f.requests[0].resolve({items: [draft()]}); await opening;
  const saved = textOf(f.host); f.history.suspend(); assert.equal(textOf(f.host), saved);
  const detail = f.history.show('a'); f.history.reset();
  f.requests[1].resolve(draft('a', {title: '已经关闭的旧响应'})); await detail;
  assert.equal(textOf(f.host).trim(), '');
  const reload = f.history.showList(); assert.equal(f.calls.length, 3);
  f.requests[2].resolve({items: []}); await reload; assert.match(visibleText(f.host), /没有.*草案/);
});

test('failed detail and directory reads expose a working GET retry instead of an empty success state', async () => {
  const f = fixture(), detail = f.history.show('a');
  f.requests[0].reject(new Error('offline')); await detail;
  assert.match(visibleText(f.host), /读取失败/); f.button('重试').click();
  f.requests[1].resolve(draft('a')); await flush(); assert.match(visibleText(f.host), /客户定义调整/);
  const directory = f.history.showList(); f.requests[2].reject(new Error('offline')); await directory;
  assert.match(visibleText(f.host), /读取失败/); assert.doesNotMatch(visibleText(f.host), /没有.*草案/);
  f.button('重试').click(); f.requests[3].resolve({items: [draft()]}); await flush();
  assert.match(visibleText(f.host), /客户定义调整/);
});

test('final decisions match operation fingerprints and source context stays a saved snapshot', async () => {
  const f = fixture(), showing = f.history.show('a');
  f.requests[0].resolve(draft('a', {source_kind: 'discovery',
    operations: [{id: 'o1', fingerprint: 'f1', action: 'add_annotation', target_iri: 'urn:Customer', after: {predicate: 'http://www.w3.org/2000/01/rdf-schema#label', value: '历史客户', language: 'zh'}},
      {id: 'o2', fingerprint: 'f2', action: 'add_parent', target_iri: 'urn:Customer', after: {value: 'urn:Party'}},
      {id: 'o3', fingerprint: 'f3', action: 'retire_term', target_iri: 'urn:Old'}],
    decisions: [{id: 'old', operation_id: 'o1', operation_fingerprint: 'f1', action: 'reject', reason: '旧理由'},
      {id: 'new', supersedes_decision_id: 'old', operation_id: 'o1', operation_fingerprint: 'f1', action: 'approve', reason: '保留历史称呼'},
      {id: 'd2', operation_id: 'o2', operation_fingerprint: 'f2', action: 'reject', reason: '父类依据不足'},
      {id: 'stale', operation_id: 'o3', operation_fingerprint: 'old-fingerprint', action: 'approve'}],
    source_context: {documents: [{document_id: 'secret-doc-id', document_title: '<img onerror="bad">旧政策', expected_document_version: 4, document_version: 9, version: 10, expected_document_version_id: 'opaque-document-version', quote: '当时保存的句子'},
      {document_title: '另一份旧资料', document_version: 6, version: 8}, {document_title: '遗留版本资料', version: 7}],
      intake_bindings: [{discovery_run_id: 'intake-opaque-run', selected_candidate_ids: ['candidate-id']}]} }));
  await showing;
  assert.match(visibleText(f.host), /收下 1.*不收 1.*待决定 1/);
  const groups = all(f.host).filter(node => node.className === 'om-dh-group');
  assert.equal(groups.length, 2); assert.ok(groups.every(node => node.tag === 'details' && node.open === false && node.children[0].tag === 'summary'));
  assert.match(visibleText(f.host), /历史客户/); assert.doesNotMatch(visibleText(f.host), /保留历史称呼|父类依据不足/);
  groups[0].open = true;
  assert.match(visibleText(f.host), /保留历史称呼/); assert.match(visibleText(f.host), /父类依据不足/);
  assert.doesNotMatch(visibleText(f.host), /旧理由|old-fingerprint|opaque-run|secret-doc-id|candidate-id|opaque-document-version/);
  assert.match(visibleText(f.host), /纳入批次 1/); assert.match(visibleText(f.host), /纳入批次技术明细/);
  assert.doesNotMatch(visibleText(f.host), /来源运行|抽取任务/);
  for (const version of [4, 6, 7]) assert.match(visibleText(f.host), new RegExp(`保存的文档版本：${version}`));
  assert.doesNotMatch(visibleText(f.host), /保存的文档版本：(?:8|9|10)/);
  assert.match(visibleText(f.host), /当时保存的句子/); assert.match(visibleText(f.host), /旧政策/);
  assert.ok(!all(f.host).some(node => node.tag === 'img'));
  assert.ok(f.calls.every(call => /\/ontology-drafts(?:\/[^/]+)?$/.test(call.url)));
  assert.equal(JSON.stringify(f.state), f.before);
});

test('a missing history identity or mismatched detail is shown honestly and remains retryable', async () => {
  const f = fixture(); await f.history.show(null); assert.equal(f.calls.length, 0);
  assert.match(visibleText(f.host), /未提供.*草案/);
  const opening = f.history.show('wanted'); f.requests[0].resolve(draft('wrong')); await opening;
  assert.match(visibleText(f.host), /读取失败/); assert.ok(f.button('重试'));
});

test('an identical mount preserves completed content while a detached or closed target rejects pending paints', async () => {
  const f = fixture(), opening = f.history.show('a'); f.requests[0].resolve(draft('a')); await opening;
  const savedRoot = f.host.children[0]; f.history.mount(f.host, {model: f.model});
  assert.equal(f.host.children[0], savedRoot); assert.equal(f.calls.length, 1);
  const pending = f.history.show('b'); f.history.suspend();
  const loading = textOf(f.host); f.requests[1].resolve(draft('b', {title: '关闭后的响应'})); await pending;
  assert.equal(textOf(f.host), loading);
  const detached = f.history.show('c'); f.root.replaceChildren();
  f.requests[2].reject(new Error('late error')); await detached;
  assert.doesNotMatch(textOf(f.host), /读取失败/);
});

test('a project change rejects pending data and stale version callbacks even without a remount', async () => {
  const f = fixture(), opening = f.history.show('a');
  f.requests[0].resolve(draft('a', {base_ontology_id: 'base'})); await opening;
  const oldLink = f.button('基线版本'), before = textOf(f.host);
  f.context.current = 'q'; f.requests[1].resolve({versions: [{id: 'base', metadata: {version: 8}}]}); await flush();
  oldLink.click(); assert.deepEqual(f.versions, []); assert.equal(textOf(f.host), before);
  const latest = f.history.showList(); f.requests[2].resolve({items: []}); await latest;
  assert.match(visibleText(f.host), /没有.*草案/);
});

test('version names can retry independently without rereading the draft or losing exact links', async () => {
  const f = fixture(), opening = f.history.show('a');
  f.requests[0].resolve(draft('a', {base_ontology_id: 'base'})); await opening;
  f.requests[1].reject(new Error('offline')); await flush();
  assert.match(visibleText(f.host), /版本名称读取失败/);
  f.button('基线版本').click(); assert.deepEqual(f.versions, ['base']);
  f.history.suspend(); // Closing and reopening a completed view preserves its retry controls.
  f.button('重试版本名称').click(); f.requests[2].resolve({versions: [{id: 'base', metadata: {version: 5}}]}); await flush();
  assert.match(f.button('基线版本').textContent, /v5/); assert.equal(f.calls.filter(call => call.url.endsWith('/ontology-drafts/a')).length, 1);
});

test('the directory can explicitly refresh a cached pending entry after its status changes', async () => {
  const f = fixture(), opening = f.history.showList();
  f.requests[0].resolve({items: [draft('a', {status: 'pending'})]}); await opening;
  assert.ok(f.button('刷新目录'), 'saved directory data needs an explicit refresh control');
  f.button('刷新目录').click(); assert.equal(f.calls.length, 2);
  f.requests[1].resolve({items: [draft('a', {status: 'accepted'})]}); await flush();
  assert.match(visibleText(f.host), /已收下/);
});

test('saved Chinese names stay preferred when later annotation operations contain English labels', async () => {
  const f = fixture(), opening = f.history.show('a');
  f.requests[0].resolve(draft('a', {ontology: {classes: [{id: 'urn:Customer', label_zh: '历史中文名', label: 'Customer'}]},
    operations: [{id: 'o', action: 'add_annotation', target_iri: 'urn:Customer', after: {predicate: 'rdfs:label', language: 'en', value: 'English annotation'}}]}));
  await opening;
  assert.ok(all(f.host).some(node => node.tag === 'summary' && node.textContent === '历史中文名'));
});
