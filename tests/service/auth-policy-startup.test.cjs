const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/auth.js'), 'utf8');

function boot({policy = {auth_enabled: true}, user = null, policyGate = null, fail = false} = {}) {
  const requests = [], redirects = [], listeners = new Map(), classes = new Set(['auth-pending']);
  let redirected;
  const redirect = new Promise(resolve => { redirected = resolve; });
  const classList = {add: name => classes.add(name), remove: name => classes.delete(name),
    contains: name => classes.has(name), toggle(name, value) { value ? classes.add(name) : classes.delete(name); }};
  const document = {readyState: 'loading', documentElement: {classList}, body: {classList},
    getElementById: () => null, querySelectorAll: () => [],
    addEventListener: (name, handler) => listeners.set(name, handler)};
  const window = {console,
    location: {pathname: '/', search: '', origin: 'http://policy.test', href: 'http://policy.test/',
      replace(url) { redirects.push(url); redirected(); }},
    async fetch(url) {
      requests.push(url);
      if (url === '/api/auth/policy') {
        if (policyGate) await policyGate;
        if (fail) throw Error('policy unavailable');
        return new Response(JSON.stringify(policy));
      }
      assert.equal(url, '/api/auth/me', 'startup must not probe health, runtime, Milvus or OCR');
      return new Response(JSON.stringify({user}), {status: user ? 200 : 401});
    }};
  const localStorage = {getItem: () => user ? 'session' : null, setItem() {}, removeItem() {}};
  vm.runInNewContext(source, {window, document, localStorage, Headers, URL, console}, {filename: 'auth.js'});
  return {window, classes, requests, redirects,
    async finish() {
      listeners.get('DOMContentLoaded')();
      await Promise.race([window.Auth.whenReady(), redirect]);
    }};
}

test('startup policy is the only public probe and disabled authentication releases the cloak', async () => {
  const f = boot({policy: {auth_enabled: false}});
  assert.equal(f.classes.has('auth-pending'), true);
  await f.finish();
  assert.deepEqual(f.requests, ['/api/auth/policy']);
  assert.equal(f.classes.has('auth-pending'), false);
  assert.equal(f.window.Auth.isAdmin(), true);
  assert.deepEqual(f.redirects, []);
});

test('enabled policy keeps unauthenticated business content cloaked during login redirect', async () => {
  const f = boot();
  await f.finish();
  assert.deepEqual(f.requests, ['/api/auth/policy']);
  assert.equal(f.classes.has('auth-pending'), true);
  assert.equal(f.window.Auth.isAdmin(), false);
  assert.equal(f.redirects.length, 1);
});

test('policy failure remains fail closed', async () => {
  const f = boot({fail: true});
  await f.finish();
  assert.deepEqual(f.requests, ['/api/auth/policy']);
  assert.equal(f.classes.has('auth-pending'), true);
  assert.equal(f.window.Auth.isAdmin(), false);
});

test('pending policy cannot release the cloak or grant writes', async () => {
  let release;
  const f = boot({policy: {auth_enabled: false}, policyGate: new Promise(resolve => { release = resolve; })});
  const done = f.finish();
  await new Promise(setImmediate);
  assert.equal(f.classes.has('auth-pending'), true);
  assert.equal(f.window.Auth.isAdmin(), false);
  release();
  await done;
  assert.equal(f.classes.has('auth-pending'), false);
});

test('enabled policy validates the saved session before releasing business content', async () => {
  const f = boot({user: {id: 'viewer', role: 'viewer'}});
  await f.finish();
  assert.deepEqual(f.requests, ['/api/auth/policy', '/api/auth/me']);
  assert.equal(f.classes.has('auth-pending'), false);
  assert.equal(f.window.Auth.isAdmin(), false);
});
