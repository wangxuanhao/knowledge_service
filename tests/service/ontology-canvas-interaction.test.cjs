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
function fixture(view = 'graph') {
  const selected = [], commands = [], host = element('host');
  const window = { cytoscape: options => {
    const { wheelSensitivity, ...headlessOptions } = options;
    return cytoscape({ ...headlessOptions, container: undefined, headless: true, styleEnabled: false });
  },
    addEventListener() {}, removeEventListener() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model-canvas.js'), 'utf8'), {
    window, document: { createElement: element, createElementNS: (_, tag) => element(tag) }, setTimeout, console,
  });
  const canvas = window.OntologyCanvas;
  canvas.mount(host, { S: {}, select: id => selected.push(id), sendCommand: cmd => commands.push(cmd), status() {} });
  let graph = { nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], edges: [{ id: 'ab', source: 'a', target: 'b', kind: 'inherit' }] };
  const render = () => canvas.render({ graph, view, selected: 'a' });
  render();
  const all = node => [node, ...node.children.flatMap(all)];
  return { canvas, cy: canvas.__cy(), selected, commands, host, graph, render,
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
  assert.notEqual(f.bands()[0].attributes.x, before);
  f.graph.nodes.push({ id: 'c', label: 'C' });
  f.graph.edges.push({ id: 'bc', source: 'b', target: 'c', kind: 'inherit' });
  f.render();
  assert.equal(f.bands().length, 3);
  assert.deepEqual({ ...f.cy.$id('a').position() }, { x: 500, y: 220 });
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
