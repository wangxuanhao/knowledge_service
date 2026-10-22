const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/auth.js'), 'utf8');

function fixture({health = {auth_enabled: false}, healthStatus = 200, healthFailure = false,
  invalidJson = false, healthGate = null, user = null} = {}) {
  const listeners = new Map(), classes = new Set(), requests = [], redirects = [];
  let resolveRedirect;
  const redirected = new Promise(resolve => { resolveRedirect = resolve; });
  const values = new Map(user ? [['kg_token', 'verified-test-session']] : []);
  const classList = {
    add: name => classes.add(name),
    remove: name => classes.delete(name),
    contains: name => classes.has(name),
    toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
  };
  const document = {
    readyState: 'loading', body: {classList}, getElementById: () => null,
    querySelectorAll: () => [], addEventListener: (name, handler) => listeners.set(name, handler),
  };
  const window = {
    console,
    location: {pathname: '/', search: '', href: 'http://auth.test/', origin: 'http://auth.test',
      replace: url => { redirects.push(url); resolveRedirect(); }},
    async fetch(url) {
      requests.push(url);
      if (url === '/api/auth/policy') {
        if (healthGate) await healthGate;
        if (healthFailure) throw Error('health connection failed');
        return new Response(invalidJson ? 'invalid json' : JSON.stringify(health), {status: healthStatus});
      }
      assert.equal(url, '/api/auth/me');
      return new Response(JSON.stringify({user}), {status: user ? 200 : 401});
    },
  };
  const localStorage = {getItem: key => values.get(key) || null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
  vm.runInNewContext(source, {window, document, localStorage, Headers, URL, console}, {filename: 'auth.js'});
  return {auth: window.Auth, classes, requests, redirects,
    async start() {
      listeners.get('DOMContentLoaded')();
      await Promise.race([window.Auth.whenReady(), redirected]);
    }};
}

test('explicit auth-disabled health permits business writes without inventing an account or superadmin', async () => {
  const f = fixture();
  assert.equal(f.auth.isAdmin(), false, 'unknown policy must not grant business writes');
  await f.start();
  assert.equal(f.auth.isAdmin(), true);
  assert.equal(f.auth.user(), null);
  assert.equal(f.auth.isSuperAdmin(), false);
  assert.equal(f.classes.has('is-admin'), true);
  assert.deepEqual(f.requests, ['/api/auth/policy']);
  assert.deepEqual(f.redirects, []);
});

test('an unresolved health request never grants business writes', async () => {
  let resolveHealth;
  const gate = new Promise(resolve => { resolveHealth = resolve; });
  const f = fixture({health: {auth_enabled: true}, healthGate: gate});
  const ready = f.start();
  assert.equal(f.auth.isAdmin(), false);
  assert.equal(f.auth.user(), null);
  resolveHealth();
  await ready;
  assert.equal(f.auth.isAdmin(), false);
  assert.equal(f.auth.isSuperAdmin(), false);
});

for (const [name, options] of [
  ['missing auth_enabled', {health: {status: 'ok'}}],
  ['nonboolean auth_enabled', {health: {auth_enabled: 'false'}}],
  ['failed HTTP health with an auth-disabled body', {healthStatus: 503}],
  ['unreachable health', {healthFailure: true}],
  ['malformed health JSON', {invalidJson: true}],
]) {
  test(name + ' never grants business writes', async () => {
    const f = fixture(options);
    await f.start();
    assert.equal(f.auth.isAdmin(), false);
    assert.equal(f.auth.user(), null);
    assert.equal(f.auth.isSuperAdmin(), false);
    assert.equal(f.classes.has('is-admin'), false);
  });
}

test('enabled authentication keeps an authenticated viewer read-only', async () => {
  const f = fixture({health: {auth_enabled: true}, user: {id: 'viewer', username: 'viewer', role: 'viewer'}});
  await f.start();
  assert.equal(f.auth.user().id, 'viewer');
  assert.equal(f.auth.isAdmin(), false);
  assert.equal(f.auth.isSuperAdmin(), false);
  assert.equal(f.classes.has('is-viewer'), true);
});

for (const role of ['admin', 'superadmin']) {
  test('enabled authentication preserves the verified ' + role + ' role', async () => {
    const f = fixture({health: {auth_enabled: true}, user: {id: role, username: role, role}});
    await f.start();
    assert.equal(f.auth.user().id, role);
    assert.equal(f.auth.isAdmin(), true);
    assert.equal(f.auth.isSuperAdmin(), role === 'superadmin');
  });
}
