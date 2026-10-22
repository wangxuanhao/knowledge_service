const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const calls = [], window = {};
  let revision = 1;
  const summary = {classes: [], relations: [], attributes: []};
  const draft = () => ({id: 'draft', status: 'pending', revision, ontology: summary});
  const context = {window, current: 'project', console,
    document: {getElementById: () => null, addEventListener() {}},
    endpoint: suffix => '/project' + suffix,
    api: async (url, body, method = 'POST') => {
      calls.push({url, body, method});
      if (url.endsWith('/commands')) {revision++; return draft();}
      if (url.endsWith('/ontology-drafts')) return {items: [draft()]};
      if (url.includes('/ontology-drafts/draft')) return draft();
      if (url.endsWith('/ontology')) return {id: 'published', summary};
      return {};
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model.js'), 'utf8'), context);
  window.OntologyModel.S.draft = draft();
  return {model: window.OntologyModel, calls};
}

test('manual relation creation sends identity, labels and both endpoints as one draft command', async () => {
  const f = fixture();
  await f.model.createTerm({kind: 'relation', label: 'belongsTo', label_zh: '属于 / 门店', domain: 'urn:A', range: 'urn:B'});
  const writes = f.calls.filter(call => call.method === 'POST');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/project/ontology-drafts/draft/commands');
  const body = JSON.parse(JSON.stringify(writes[0].body));
  assert.equal(body.expected_revision, 1);
  assert.equal(body.command.action, 'batch');
  const iri = 'urn:knowledge:ontology:project:属于-%2F-门店';
  assert.deepEqual(body.command.commands, [
    {action: 'create_term', target_iri: iri, kind: 'relation'},
    {action: 'add_annotation', target_iri: iri, predicate: 'http://www.w3.org/2000/01/rdf-schema#label', value: 'belongsTo'},
    {action: 'add_annotation', target_iri: iri, predicate: 'http://www.w3.org/2000/01/rdf-schema#label', value: '属于 / 门店', language: 'zh'},
    {action: 'add_domain', target_iri: iri, value: 'urn:A'},
    {action: 'add_range', target_iri: iri, value: 'urn:B'},
  ]);
});

test('manual classes retain their parent and attributes retain domain and datatype', async () => {
  for (const [spec, actions] of [
    [{kind: 'class', label: 'Child', parent: 'urn:Parent'}, ['create_term', 'add_annotation', 'add_parent']],
    [{kind: 'attribute', label: 'Name', domain: 'urn:Owner', datatype: 'http://www.w3.org/2001/XMLSchema#string'}, ['create_term', 'add_annotation', 'add_domain', 'set_datatype']],
  ]) {
    const f = fixture(); await f.model.createTerm(spec);
    assert.deepEqual(Array.from(f.calls.find(call => call.url.endsWith('/commands')).body.command.commands, command => command.action), actions);
  }
});

test('historical term creation cannot write a draft', async () => {
  const f = fixture(); f.model.S.preview = {id: 'history'};
  await assert.rejects(f.model.createTerm({kind: 'class', label: 'Child'}), /历史.*只读/);
  assert.equal(f.calls.length, 0);
});

test('the retired direct-save editor and its write handlers are absent', () => {
  const web = path.join(__dirname, '../../knowledge_service/web');
  assert.doesNotMatch(fs.readFileSync(path.join(web, 'index.html'), 'utf8'), /id="tab-ontology"|id="save-ontology"/);
  assert.doesNotMatch(fs.readFileSync(path.join(web, 'index.html'), 'utf8'), /ontology-details\.js/);
  for (const file of ['app.js', 'workspace.js', 'ontology-model-panel.js', 'ontology-model-canvas.js'])
    assert.doesNotMatch(fs.readFileSync(path.join(web, file), 'utf8'), /endpoint\('\/ontology\/(?:terms|term|term-retire)'\)|bind\('save-ontology'/);
});
