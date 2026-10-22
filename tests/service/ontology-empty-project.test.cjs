const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../../knowledge_service/web');
const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const apiSource = app.slice(app.indexOf('async function api('), app.indexOf('function endpoint('));

function fixture(options = {}) {
  const calls = [], window = {};
  const context = {
    window, current: 'p', endpoint: suffix => '/p' + suffix, console,
    document: {getElementById: () => null, addEventListener() {}},
    fetch: async (url, init) => {
      const body = init.body === undefined ? undefined : JSON.parse(init.body);
      calls.push({url, method: init.method, body});
      let status = 200, result = {};
      if (url === '/p/ontology' && init.method === 'GET') {
        if (options.waitForOntology) await options.waitForOntology;
        if (options.networkError) throw Error('offline');
        status = options.status ?? 404;
        result = options.result ?? {detail: 'No published ontology', code: 'ontology_not_published'};
      } else if (url === '/p/ontology-drafts' && init.method === 'GET') result = {items: []};
      else if (url === '/p/ontology-drafts' && init.method === 'POST') result = {id: 'empty', status: 'pending', revision: 1, base_ontology_id: body.base_ontology_id};
      return {status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(result)};
    },
  };
  vm.createContext(context);
  vm.runInContext(apiSource, context);
  vm.runInContext(fs.readFileSync(path.join(web, 'ontology-model.js'), 'utf8'), context);
  return {model: window.OntologyModel, api: context.api, calls, switchProject: p => {context.current = p;}};
}

test('first manual edit prepares an explicit empty draft only for an unpublished ontology', async () => {
  const f = fixture();
  const draft = await f.model.ensureDraft();
  assert.equal(draft.id, 'empty');
  const writes = f.calls.filter(call => call.method === 'POST');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/p/ontology-drafts');
  assert.equal(writes[0].body.base_ontology_id, null);
});

test('published manual edits retain the exact publication precondition', async () => {
  const f = fixture({status: 200, result: {id: 'published-release'}});
  await f.model.ensureDraft();
  assert.equal(f.calls.find(call => call.method === 'POST').body.base_ontology_id, 'published-release');
});

for (const [status, code] of [[401, 'ontology_not_published'], [403, 'ontology_not_published'],
  [404, undefined], [404, 'project_not_found'], [500, 'ontology_not_published']]) {
  test(`HTTP ${status} / ${code || 'no code'} cannot create an empty draft`, async () => {
    const f = fixture({status, result: {detail: 'read failed', ...(code ? {code} : {})}});
    await assert.rejects(f.model.ensureDraft(), /read failed/);
    assert.ok(!f.calls.some(call => call.method === 'POST'));
  });
}

test('network failures cannot create an empty draft', async () => {
  const f = fixture({networkError: true});
  await assert.rejects(f.model.ensureDraft(), /无法连接/);
  assert.ok(!f.calls.some(call => call.method === 'POST'));
});

test('a malformed successful ontology response cannot become an empty baseline', async () => {
  const f = fixture({status: 200, result: {}});
  await assert.rejects(f.model.ensureDraft(), /本体|ontology/i);
  assert.ok(!f.calls.some(call => call.method === 'POST'));
});

for (const history of [false, true]) {
  test(`unpublished response cannot write after ${history ? 'entering history' : 'switching project'}`, async () => {
    let release;
    const f = fixture({waitForOntology: new Promise(resolve => {release = resolve;})});
    const ensuring = f.model.ensureDraft();
    for (let i = 0; i < 20; i++) await Promise.resolve();
    assert.ok(f.calls.some(call => call.url === '/p/ontology'));
    if (history) f.model.S.preview = {id: 'historic'};
    else f.switchProject('next');
    release();
    await assert.rejects(ensuring, history ? /历史.*只读/ : /项目.*切换/);
    assert.ok(!f.calls.some(call => call.method === 'POST'));
  });
}

test('API errors retain only safe HTTP status and string code metadata', async () => {
  const f = fixture({result: {detail: 'read failed', code: 'ontology_not_published', secret: 'private fixture'}});
  await assert.rejects(f.api('/p/ontology', undefined, 'GET'), error => {
    assert.equal(error.status, 404);
    assert.equal(error.code, 'ontology_not_published');
    assert.equal(error.message, 'read failed');
    assert.deepEqual(Object.keys(error).sort(), ['code', 'status']);
    return true;
  });
});

test('API errors never copy an object-valued error code', async () => {
  const f = fixture({result: {detail: 'read failed', code: {private: true}}});
  await assert.rejects(f.api('/p/ontology', undefined, 'GET'), error => {
    assert.equal(error.status, 404);
    assert.equal(error.code, undefined);
    return true;
  });
});
