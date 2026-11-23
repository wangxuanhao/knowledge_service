const test = require('node:test');
const assert = require('node:assert/strict');

function fixture() {
  const { createSession } = require('../../knowledge_service/web/graph-neighborhood-search.js');
  let context = { projectId: 'p', scope: { known_at: '2026-11-01' } };
  const requests = [];
  const session = createSession({
    getContext: () => context,
    request: (projectId, body, signal) => new Promise((resolve, reject) => {
      requests.push({ projectId, body, signal, resolve, reject });
    }),
  });
  const last = () => requests.at(-1);
  const seed = { id: 'a', label: '申请' };
  const match = { id: 'b', label: '审批', hops: 1, path: { start: 'a', end: 'b', steps: [{ from: 'a', to: 'b', relation_id: 'ab' }] } };
  const records = [{ kind: 'entity', id: 'a' }, { kind: 'entity', id: 'b' }, { kind: 'relation', id: 'ab', subject_id: 'a', object_id: 'b' }];
  const reply = { matches: [match], total: 1, records };
  const start = async () => {
    const pending = session.start({ seed, direction: 'backward', hops: 2, relationshipTypes: ['reviews'] });
    last().resolve(reply);
    await pending;
  };
  return { session, last, seed, match, records, reply, start, changeContext: value => { context = value; } };
}

test('refinement sends all prior conditions and rolls back to the saved result without a request', async () => {
  const { session, last, start, reply } = fixture();
  await start();
  const type = { field: 'entity_type', value: '流程' };
  const platform = { field: 'source_platform', value: '服务平台' };
  let pending = session.refine([type]);
  last().resolve(reply);
  await pending;
  pending = session.refine([type, platform]);
  const latest = last();
  assert.deepEqual(latest.body, {
    known_at: '2026-11-01', seeds: ['a'], direction: 'backward', hops: 2,
    relationship_types: ['reviews'], filter_steps: [type, platform],
  });
  latest.resolve({ matches: [], total: 0, partial: true, records: [] });
  await pending;
  assert.equal(session.snapshot().stage.result.partial, true);
  session.back();
  assert.equal(last(), latest);
  assert.deepEqual(session.snapshot().stage.criteria.filters, [type]);
  assert.equal(session.snapshot().stage.result, reply);
});

test('failed filter leaves the last stage intact and can be retried', async () => {
  const { session, last, start, reply } = fixture();
  await start();
  const filters = [{ field: 'attribute_value', value: '0' }];
  const pending = session.refine(filters);
  last().reject(new Error('暂不可用'));
  await pending;
  assert.equal(session.snapshot().stage.result, reply);
  assert.deepEqual(session.snapshot().stage.criteria.filters, []);
  assert.match(session.snapshot().error, /暂不可用/);
  const retry = session.retry();
  assert.deepEqual(last().body.filter_steps, filters);
  last().resolve(reply);
  await retry;
  assert.deepEqual(session.snapshot().stage.criteria.filters, filters);
});

test('continuing from a result preserves the path records and changes the seed', async () => {
  const { session, last, start, records } = fixture();
  await start();
  const pending = session.expand('b');
  assert.deepEqual(last().body.seeds, ['b']);
  last().resolve({ matches: [{ id: 'c', path: { start: 'b', end: 'c', steps: [{ from: 'b', to: 'c', relation_id: 'bc' }] } }], records: [] });
  await pending;
  const stage = session.snapshot().stage;
  assert.deepEqual(stage.trail.nodes, ['a', 'b']);
  assert.deepEqual(stage.trail.edges, ['ab']);
  assert.deepEqual(stage.trail.records, records);
});

test('a late response cannot replace new results or survive a scope or project change', async () => {
  const { session, last, changeContext, seed, reply } = fixture();
  const old = session.start({ seed, direction: 'both', hops: 2, relationshipTypes: [] });
  const oldRequest = last();
  const fresh = session.start({ seed, direction: 'forward', hops: 0, relationshipTypes: [] });
  assert.equal(oldRequest.signal.aborted, true);
  last().resolve(reply);
  await fresh;
  oldRequest.resolve({ matches: [] });
  await old;
  assert.equal(session.snapshot().stage.result, reply);

  const stale = session.refine([{ field: 'entity_name', value: '审批' }]);
  changeContext({ projectId: 'different', scope: {} });
  last().resolve(reply);
  await stale;
  assert.equal(session.snapshot().stage, null);
  assert.equal(session.snapshot().pending, false);
});

test('an exact seed keeps its originating scope and rejects mismatched versions', async () => {
  const { session, last, start, seed, records, reply } = fixture();
  await start();
  const frozen = { known_at: '2026-05-01', ontology_scope: 'ids', ontology_ids: ['ontology-v1'] };
  const criteria = { seed: { ...seed, version_id: 'a1' }, direction: 'forward', hops: 2, relationshipTypes: [] };
  let pending = session.start(criteria, { scope: frozen });
  assert.equal(last().body.known_at, frozen.known_at);
  assert.deepEqual(last().body.ontology_ids, frozen.ontology_ids);
  last().resolve({ ...reply, records: [{ ...records[0], version_id: 'a2' }, ...records.slice(1)] });
  assert.equal(await pending, false);
  assert.equal(session.snapshot().stage.result, reply);
  assert.equal(session.snapshot().errorCode, 'seed_version_changed');

  const pinnedReply = { ...reply, records: [{ ...records[0], version_id: 'a1' }, ...records.slice(1)] };
  pending = session.start(criteria, { scope: frozen });
  last().resolve(pinnedReply);
  assert.equal(await pending, true);
  assert.deepEqual(session.snapshot().stage.scope, frozen);

  pending = session.refine([{ field: 'entity_name', value: '审批' }]);
  assert.equal(last().body.known_at, frozen.known_at);
  last().resolve(pinnedReply);
  assert.equal(await pending, true);
});
