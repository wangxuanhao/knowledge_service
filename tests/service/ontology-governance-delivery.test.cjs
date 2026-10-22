const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element(tag) {
  return { tag, children: [], listeners: {}, value: '', checked: false, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    append(...items) { this.children.push(...items); }, appendChild(item) { this.children.push(item); },
    replaceChildren(...items) { this.children = items; }, setAttribute() {},
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
  assert.ok(all(actions).some(node=>node.textContent==='关联知识'));
  assert.ok(all(actions).some(node=>node.textContent==='run-1 · 明确选中 3 条'));
});
