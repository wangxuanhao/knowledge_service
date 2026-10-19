const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const cytoscape = require('../../knowledge_service/web/vendor/cytoscape.min.js');

function element(tag) {
  return { tag, children: [], attributes: {}, listeners: {}, style: {}, clientWidth: 950, clientHeight: 740,
    classList: { toggle() {}, contains() { return false; } },
    append(...items) { this.children.push(...items); }, appendChild(item) { this.children.push(item); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    addEventListener(key, fn) { this.listeners[key] = fn; },
  };
}
function fixture(view = 'graph', initialGraph) {
  const selected = [], commands = [], host = element('host');
  const frames = new Map(); let frameId = 0;
  const window = { cytoscape: options => {
    const { wheelSensitivity, ...headlessOptions } = options;
    return cytoscape({ ...headlessOptions, container: undefined, headless: true, styleEnabled: false });
  },
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); } };
  const flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model-canvas.js'), 'utf8'), {
    window, document: { createElement: element, createElementNS: (_, tag) => element(tag) }, setTimeout, console,
  });
  const canvas = window.OntologyCanvas;
  canvas.mount(host, { S: {}, select: id => selected.push(id), sendCommand: cmd => commands.push(cmd), status() {} });
  let graph = initialGraph || { nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], edges: [{ id: 'ab', source: 'a', target: 'b', kind: 'inherit' }] };
  const render = () => canvas.render({ graph, view, selected: 'a' });
  render();
  const all = node => [node, ...node.children.flatMap(all)];
  return { canvas, cy: canvas.__cy(), selected, commands, host, graph, render, flush, frames,
    button: text => all(host).find(x => x.tag === 'button' && x.textContent === text),
    bands: () => all(host).filter(x => x.tag === 'rect'),
  };
}

test('incremental additions and repeated view selection preserve dragged positions and viewport', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  f.cy.$id('a').position({ x: 421, y: 319 });
  f.cy.zoom(1.3); f.cy.pan({ x: 22, y: 35 });
  f.graph.nodes.push({ id: 'c', label: 'C' });
  f.graph.edges.push({ id: 'bc', source: 'b', target: 'c', kind: 'inherit' });
  f.render(); f.canvas.setView('graph');
  assert.deepEqual({ ...f.cy.$id('a').position() }, { x: 421, y: 319 });
  assert.equal(f.cy.zoom(), 1.3);
  assert.deepEqual({ ...f.cy.pan() }, { x: 22, y: 35 });
  assert.notDeepEqual(f.cy.$id('c').position(), { x: 0, y: 0 });
});

test('first asynchronous graph load lays out classes after an empty loading render', t => {
  const f = fixture('graph', { nodes: [], edges: [] });
  t.after(() => f.canvas.destroy());
  for (let i = 0; i < 8; i++) f.graph.nodes.push({ id: `class-${i}`, label: `Class ${i}` });
  f.render();
  const rows = new Set(f.cy.nodes().map(n => n.position('y')));
  assert.ok(rows.size > 1, 'initial data must use the full canvas instead of a thin horizontal strip');
  const before = f.cy.nodes().map(n => ({ ...n.position() }));
  f.render();
  assert.deepEqual(f.cy.nodes().map(n => ({ ...n.position() })), before, 'repeated renders preserve the layout');
});

test('drag cannot select a node or advance the two-click relation workflow', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  const a = f.cy.$id('a');
  a.emit('tapstart'); a.emit('drag'); a.emit('tap');
  assert.deepEqual(f.selected, []);
  a.emit('tapstart'); a.emit('tap');
  assert.deepEqual(f.selected, ['a'], 'the next intentional click selects normally');
  f.button('连关系').listeners.click();
  a.emit('tapstart'); a.emit('drag'); a.emit('tap');
  f.cy.$id('b').emit('tapstart').emit('tap');
  const confirm = f.host.children[0].children.find(x => x.className === 'om-canvas-confirm');
  assert.equal(confirm.hidden, true, 'B must be the first endpoint after dragging A');
  assert.deepEqual(f.commands, []);
});

test('remounting a new project host clears gesture and link state', t => {
  const f = fixture('hierarchy'); t.after(() => f.canvas.destroy());
  f.button('连关系').listeners.click();
  f.cy.$id('a').emit('tapstart').emit('tap');
  f.cy.$id('b').emit('tapstart').emit('drag');
  const host = element('new-project');
  f.canvas.mount(host);
  f.canvas.render({ graph: { nodes: [{ id: 'a', label: 'new A' }], edges: [] } });
  assert.notEqual(f.canvas.__cy(), f.cy);
  assert.equal(f.canvas.__cy().nodes().length, 1);
  f.canvas.__cy().$id('a').emit('tapstart').emit('tap');
  assert.deepEqual(f.selected, ['a']);
  assert.deepEqual(f.commands, []);
});

test('fit keeps coordinates while explicit relayout restores automatic placement', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  f.cy.$id('a').position({ x: 421, y: 319 });
  f.button('适应画布').listeners.click();
  assert.deepEqual({ ...f.cy.$id('a').position() }, { x: 421, y: 319 });
  assert.ok(f.button('重新布局'), 'relayout is discoverable separately from fit');
  f.button('重新布局').listeners.click();
  assert.notDeepEqual({ ...f.cy.$id('a').position() }, { x: 421, y: 319 });
  assert.deepEqual(f.commands, []);
});

test('hierarchy bands track node motion and incremental nodes', t => {
  const f = fixture('hierarchy'); t.after(() => f.canvas.destroy());
  const before = f.bands()[0].attributes.x;
  f.cy.$id('a').position({ x: 500, y: 220 });
  f.flush();
  assert.notEqual(f.bands()[0].attributes.x, before);
  f.graph.nodes.push({ id: 'c', label: 'C' });
  f.graph.edges.push({ id: 'bc', source: 'b', target: 'c', kind: 'inherit' });
  f.render();
  assert.equal(f.bands().length, 3);
  assert.deepEqual({ ...f.cy.$id('a').position() }, { x: 500, y: 220 });
});

test('hierarchy gesture bursts update only the moved band once per frame', t => {
  const graph = { nodes: Array.from({ length: 160 }, (_, i) => ({ id: `n${i}`, label: `N${i}` })), edges: [] };
  const f = fixture('hierarchy', graph); t.after(() => f.canvas.destroy());
  let writes = 0, lookups = 0;
  const all = node => [node, ...node.children.flatMap(all)];
  for (const el of all(f.host)) {
    const set = el.setAttribute.bind(el);
    el.setAttribute = (...args) => { writes++; set(...args); };
  }
  const node = f.cy.$id('n0'), get = f.cy.getElementById.bind(f.cy);
  f.cy.getElementById = (...args) => { lookups++; return get(...args); };
  const before = f.bands()[0].attributes.x;
  for (let i = 1; i <= 120; i++) node.position({ x: i + 500, y: i });
  assert.equal(writes, 0, 'pointer events must not synchronously rewrite SVG');
  assert.equal(f.frames.size, 1);
  f.flush();
  assert.ok(writes <= 10, `one changed band requires bounded writes, got ${writes}`);
  assert.ok(lookups <= 4, `only band members need querying, got ${lookups}`);
  assert.notEqual(f.bands()[0].attributes.x, before);
  writes = 0; lookups = 0;
  for (let i = 0; i < 120; i++) f.cy.pan({ x: i, y: i });
  f.flush();
  assert.equal(lookups, 0, 'viewport changes use cached model geometry');
  assert.equal(writes, 1, 'all bands move with one SVG transform');
});

test('destroy cancels pending band updates before mounting another project', () => {
  const f = fixture('hierarchy');
  f.cy.$id('a').position({ x: 400, y: 200 });
  assert.equal(f.frames.size, 1);
  f.canvas.destroy();
  assert.equal(f.frames.size, 0);
  f.flush();
});

test('hierarchy label size remains readable after group zoom', t => {
  const f = fixture('hierarchy'); t.after(() => f.canvas.destroy());
  f.cy.zoom(0.25); f.flush();
  const all = node => [node, ...node.children.flatMap(all)];
  const label = all(f.host).find(el => el.attributes.class === 'om-canvas-band-label');
  assert.equal(parseFloat(label.style.fontSize) * f.cy.zoom(), 8);
});

test('link confirmation clears a wrapped toolbar', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  const stage = f.host.children[0];
  stage.children.find(x => x.className === 'om-canvas-bar').offsetHeight = 90;
  f.button('连关系').listeners.click();
  f.cy.$id('a').emit('tapstart').emit('tap');
  f.cy.$id('b').emit('tapstart').emit('tap');
  const confirm = stage.children.find(x => x.className === 'om-canvas-confirm');
  assert.equal(confirm.hidden, false);
  assert.equal(confirm.style.bottom, '112px');
});
