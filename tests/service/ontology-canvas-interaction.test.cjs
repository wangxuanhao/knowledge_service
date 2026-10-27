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
function fixture(view = 'graph', initialGraph, additions = {}) {
  const selected = [], commands = [], host = element('host');
  const frames = new Map(); let frameId = 0;
  const window = { cytoscape: options => {
    const { wheelSensitivity, ...headlessOptions } = options;
    const cy = cytoscape({ ...headlessOptions, container: undefined, headless: true, styleEnabled: !!additions.styleEnabled });
    if (additions.measuredViewport) {
      // Headless Cytoscape has no DOM size. Supply only that measurement;
      // layouts, card bounds, animation, pan and zoom remain the real engine.
      const container = cy.container.bind(cy), destroy = cy.destroy.bind(cy), pan = cy.pan.bind(cy), layout = cy.layout.bind(cy);
      cy.container = () => options.container;
      cy.width = () => options.container.clientWidth;
      cy.height = () => options.container.clientHeight;
      // Cytoscape's plain-object check uses the caller's prototype. Bridge
      // VM-created pan objects back to this realm without changing values.
      cy.pan = (...args) => pan(...args.map(arg => arg && typeof arg === 'object' ? { ...arg } : arg));
      cy.layout = options => layout({ ...options, ...(options.positions && {
        positions: Object.fromEntries(Object.entries(options.positions).map(([id, position]) => [id, { ...position }])),
      }) });
      cy.destroy = () => { cy.container = container; return destroy(); };
    }
    return cy;
  },
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); } };
  const flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/ontology-model-canvas.js'), 'utf8'), {
    window, document: { createElement: element, createElementNS: (_, tag) => element(tag) }, setTimeout, console, ...additions.globals,
  });
  const canvas = window.OntologyCanvas;
  canvas.mount(host, { S: {}, select: id => selected.push(id), sendCommand: cmd => commands.push(cmd), status() {}, ...additions.ctx });
  let graph = initialGraph || { nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], edges: [{ id: 'ab', source: 'a', target: 'b', kind: 'inherit' }] };
  const render = () => canvas.render({ graph, view, selected: 'a' });
  render();
  const all = node => [node, ...node.children.flatMap(all)];
  return { canvas, cy: canvas.__cy(), selected, commands, host, graph, render, flush, frames,
    button: text => all(host).find(x => x.tag === 'button' && x.textContent === text),
    bands: () => all(host).filter(x => x.tag === 'rect'),
  };
}

test('two-click relation creation delegates both endpoints to the canonical atomic command helper', async t => {
  const calls = [];
  const f = fixture('graph', undefined, { globals: { api: async (url, body) => calls.push({url, body}), endpoint: suffix => '/p' + suffix },
    ctx: { S: { ontologyId: 'current-release' }, createTerm: async spec => calls.push(JSON.parse(JSON.stringify(spec))) } });
  t.after(() => f.canvas.destroy());
  f.button('新增关联关系').listeners.click();
  f.cy.$id('a').emit('tapstart').emit('tap'); f.cy.$id('b').emit('tapstart').emit('tap');
  const all = node => [node, ...node.children.flatMap(all)];
  all(f.host).find(x => x.tag === 'input').value = '属于';
  await f.button('确认建关系').listeners.click();
  for (let i=0;i<10;i++) await Promise.resolve();
  assert.deepEqual(calls, [{kind:'relation', label:'属于', label_zh:'属于', domains:['a'], ranges:['b']}]);
});

test('canvas retains contextual connections with one generic creation menu elsewhere',t=>{
  const f=fixture();t.after(()=>f.canvas.destroy());
  assert.equal(f.button('＋ 新建类'),undefined);
  assert.ok(f.button('编辑继承（连线）'));
  assert.ok(f.button('新增关联关系'));
});

test('focus traces only direct connections and clears when the selection is removed',t=>{
  const graph={nodes:['a','b','c','d'].map(id=>({id,label:id})),edges:[
    {id:'ab',source:'a',target:'b',kind:'inherit'},
    {id:'bc',source:'b',target:'c',kind:'relation',termIri:'urn:rel'},
  ]};
  const f=fixture('graph',graph,{styleEnabled:true,measuredViewport:true});t.after(()=>f.canvas.destroy());
  f.canvas.focus('a');
  assert.ok(f.cy.$id('a').hasClass('is-focus'));
  assert.ok(f.cy.$id('ab').hasClass('is-traced'));
  assert.ok(f.cy.$id('b').hasClass('is-neighbor'));
  assert.ok(f.cy.$id('c').hasClass('is-muted'));
  assert.ok(Number(f.cy.$id('ab').style('z-index'))>Number(f.cy.$id('c').style('z-index')),
    'the active inheritance line must stay visible above unrelated cards');
  f.canvas.focus('urn:rel');
  assert.ok(f.cy.$id('bc').hasClass('is-traced'));
  assert.ok(f.cy.$id('a').hasClass('is-muted'));
  f.canvas.render({graph,view:'graph',selected:null});
  assert.equal(f.cy.elements('.is-muted, .is-focus, .is-traced, .is-neighbor').length,0);
});

test('rapid focus changes center the latest node and fit cancels focus without moving cards',async t=>{
  const f=fixture('graph',undefined,{styleEnabled:true,measuredViewport:true});t.after(()=>f.canvas.destroy());
  f.cy.$id('a').position({x:-800,y:300});f.cy.$id('b').position({x:900,y:700});
  f.canvas.focus('a');f.canvas.focus('b');
  await new Promise(resolve=>setTimeout(resolve,380));
  const point=f.cy.$id('b').renderedPosition(),stage=f.host.children[0];
  assert.ok(Math.abs(point.x-stage.clientWidth/2)<2);
  assert.ok(Math.abs(point.y-stage.clientHeight/2)<60);
  const before=f.cy.nodes().map(n=>({...n.position()}));
  f.button('适应画布').listeners.click();
  assert.equal(f.cy.elements('.is-muted').length,0);
  assert.deepEqual(f.cy.nodes().map(n=>({...n.position()})),before);
});

test('focusing distant relationship endpoints fits both anchors below the normal wheel zoom limit',async t=>{
  const graph={nodes:[{id:'a'},{id:'b'}],edges:[{id:'ab',source:'a',target:'b',kind:'relation',termIri:'r'}]};
  const f=fixture('graph',graph,{styleEnabled:true,measuredViewport:true});t.after(()=>f.canvas.destroy());
  f.cy.$id('a').position({x:10000,y:400});f.cy.$id('b').position({x:20000,y:400});
  f.canvas.focus('r');await new Promise(resolve=>setTimeout(resolve,340));
  assert.ok(f.cy.zoom()<.25);
  for(const node of f.cy.nodes()){
    const box=node.renderedBoundingBox();
    assert.ok(box.x1>=0&&box.x2<=950,'both declared endpoints remain visible');
  }
});

test('one inheritance edge cannot collapse many independent classes into an unreadable strip',t=>{
  const graph={nodes:Array.from({length:42},(_,i)=>({id:`n${i}`,label:`类型 ${i}`})),
    edges:[{id:'inherit',source:'n0',target:'n1',kind:'inherit'}]};
  const f=fixture('graph',graph,{styleEnabled:true,measuredViewport:true});t.after(()=>f.canvas.destroy());
  assert.ok(f.cy.zoom()>.35,'a partially modeled ontology retains readable card sizes');
  assert.ok(new Set(f.cy.nodes().map(node=>node.position('y'))).size>2,'independent roots wrap into rows');
});

test('an inspector covering almost the entire graph keeps focus centered for when it closes',async t=>{
  const measuredElement=tag=>({...element(tag),getBoundingClientRect(){return {left:0,right:950,width:950};}});
  const f=fixture('graph',undefined,{styleEnabled:true,measuredViewport:true,globals:{document:{
    createElement:measuredElement,createElementNS:(_,tag)=>measuredElement(tag),
    getElementById:()=>({getBoundingClientRect:()=>({left:20,width:930})}),
  }}});t.after(()=>f.canvas.destroy());
  f.canvas.focus('a');await new Promise(resolve=>setTimeout(resolve,340));
  assert.ok(Math.abs(f.cy.$id('a').renderedPosition('x')-475)<2);
});

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

function assertWholeGraphVisible(f) {
  const stage = f.host.children[0];
  for (const node of f.cy.elements()) {
    const bb = node.renderedBoundingBox();
    assert.ok(bb.x1 >= 0 && bb.y1 >= 0 && bb.x2 <= stage.clientWidth && bb.y2 <= stage.clientHeight,
      `${node.id()} must be visible after switching view: ${JSON.stringify(bb)}`);
  }
}

test('switching layouts cancels an in-flight class focus and shows every card and edge', async t => {
  const graph = {
    nodes: Array.from({ length: 12 }, (_, i) => ({ id: `class-${i}`, label: `Class ${i}` })),
    edges: Array.from({ length: 9 }, (_, i) => ({ id: `edge-${i}`, source: `class-${i}`, target: `class-${i + 3}`, kind: 'inherit' })),
  };
  const f = fixture('graph', graph, { styleEnabled: true, measuredViewport: true });
  t.after(() => f.canvas.destroy());
  const nodeIds = f.cy.nodes().map(node => node.id()), edgeIds = f.cy.edges().map(edge => edge.id());
  for (const next of ['hierarchy', 'graph']) {
    f.canvas.focus('class-0');
    f.canvas.setView(next);
    await new Promise(resolve => setTimeout(resolve, 420));
    assertWholeGraphVisible(f);
    assert.deepEqual(f.cy.nodes().map(node => node.id()), nodeIds);
    assert.deepEqual(f.cy.edges().map(edge => edge.id()), edgeIds);
  }
});

test('returning from term cards fits the graph after its hidden container becomes measurable', t => {
  const graph = { nodes: Array.from({ length: 12 }, (_, i) => ({ id: `class-${i}` })), edges: [] };
  const f = fixture('types', graph, { styleEnabled: true, measuredViewport: true });
  t.after(() => f.canvas.destroy());
  const positions = f.cy.nodes().map(node => ({ ...node.position() }));
  f.cy.zoom(1.1); f.cy.center(f.cy.nodes().first());
  const stage = f.host.children[0];
  f.canvas.render({ graph, view: 'cards' });
  stage.clientWidth = 0; stage.clientHeight = 0;
  f.canvas.setView('types');
  f.canvas.render({ graph, view: 'types' });
  stage.clientWidth = 950; stage.clientHeight = 740;
  f.flush();
  assertWholeGraphVisible(f);
  assert.deepEqual(f.cy.nodes().map(node => ({ ...node.position() })), positions,
    'returning to the same graph retains its node positions');
});

test('the first graph layout waits for a visible viewport after loading data in term cards',t=>{
  const graph={nodes:Array.from({length:12},(_,i)=>({id:`class-${i}`,label:`Class ${i}`})),edges:[]};
  const f=fixture('cards',graph,{styleEnabled:true,measuredViewport:true});
  t.after(()=>f.canvas.destroy());
  const stage=f.host.children[0];stage.clientWidth=0;stage.clientHeight=0;
  f.canvas.setView('types');f.canvas.render({graph,view:'types'});
  stage.clientWidth=950;stage.clientHeight=740;f.flush();
  const nodes=f.cy.nodes();
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i].boundingBox(),b=nodes[j].boundingBox();
    assert.ok(Math.min(a.x2,b.x2)-Math.max(a.x1,b.x1)<=0||Math.min(a.y2,b.y2)-Math.max(a.y1,b.y1)<=0,
      'first visible type cards must not overlap');
  }
  assertWholeGraphVisible(f);
});

test('explicit fitting includes a deep hierarchy below the normal minimum zoom', t => {
  const graph = {
    nodes: Array.from({ length: 30 }, (_, i) => ({ id: `class-${i}` })),
    edges: Array.from({ length: 29 }, (_, i) => ({ id: `edge-${i}`, source: `class-${i}`, target: `class-${i + 1}`, kind: 'inherit' })),
  };
  const f = fixture('graph', graph, { styleEnabled: true, measuredViewport: true });
  t.after(() => f.canvas.destroy());
  f.canvas.setView('hierarchy');
  assertWholeGraphVisible(f);
  assert.ok(f.cy.zoom() < 0.25, 'overview may zoom out far enough to show the complete hierarchy');
  f.button('适应画布').listeners.click();
  assertWholeGraphVisible(f);
  const viewport = { zoom: f.cy.zoom(), pan: { ...f.cy.pan() } };
  f.canvas.setDisplay('compact');
  assert.equal(f.cy.zoom(), viewport.zoom);
  assert.deepEqual({ ...f.cy.pan() }, viewport.pan);
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

test('unchanged graph renders do not invalidate Cytoscape data and card images',t=>{
  const f=fixture();t.after(()=>f.canvas.destroy());
  let updates=0;f.cy.on('data',()=>updates++);
  f.render();f.render();
  assert.equal(updates,0,'unchanged nodes and edges must not trigger renderer updates');
  f.graph.nodes[0].label='Updated';f.render();
  assert.equal(f.cy.$id('a').data('label'),'Updated');
  assert.ok(updates>0,'a real definition change still reaches the canvas');
});

test('term-card rendering postpones hidden graph updates until the canvas is shown',t=>{
  const f=fixture();t.after(()=>f.canvas.destroy());
  let updates=0;f.cy.on('data',()=>updates++);
  f.graph.nodes[0].label='Updated while hidden';
  f.canvas.render({graph:f.graph,view:'cards'});
  assert.equal(updates,0,'the hidden canvas does not rebuild nodes for the term list');
  f.canvas.setView('types');f.canvas.render({graph:f.graph,view:'types'});f.flush();
  assert.equal(f.cy.$id('a').data('label'),'Updated while hidden');
});

test('drag cannot select a node or advance the two-click relation workflow', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  const a = f.cy.$id('a');
  a.emit('tapstart'); a.emit('drag'); a.emit('tap');
  assert.deepEqual(f.selected, []);
  a.emit('tapstart'); a.emit('tap');
  assert.deepEqual(f.selected, ['a'], 'the next intentional click selects normally');
  f.button('新增关联关系').listeners.click();
  a.emit('tapstart'); a.emit('drag'); a.emit('tap');
  f.cy.$id('b').emit('tapstart').emit('tap');
  const confirm = f.host.children[0].children.find(x => x.className === 'om-canvas-confirm');
  assert.equal(confirm.hidden, true, 'B must be the first endpoint after dragging A');
  assert.deepEqual(f.commands, []);
});

test('remounting a new project host clears gesture and link state', t => {
  const f = fixture('hierarchy'); t.after(() => f.canvas.destroy());
  f.button('新增关联关系').listeners.click();
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
  f.button('新增关联关系').listeners.click();
  f.cy.$id('a').emit('tapstart').emit('tap');
  f.cy.$id('b').emit('tapstart').emit('tap');
  const confirm = stage.children.find(x => x.className === 'om-canvas-confirm');
  assert.equal(confirm.hidden, false);
  assert.equal(confirm.style.bottom, '112px');
});

test('connected class cards encode safe property rows and switch independently of hierarchy layout', t => {
  const f=fixture('hierarchy',{nodes:[{id:'a',label:'订单 <&>',attributes:[{label:'金额 <&>',datatype:'decimal',required:true}],
    inheritedAttributes:[{id:'name',label:'名称'}],parents:['root']}],edges:[]});
  t.after(()=>f.canvas.destroy());
  const node=f.cy.$id('a'),data=node.data();
  assert.ok(data.cardImage,'connected nodes carry an SVG class card');
  const svg=decodeURIComponent(data.cardImage.split(',')[1]);
  assert.match(svg,/金额 &lt;&amp;&gt;/);
  assert.match(svg,/decimal/);
  assert.match(svg,/继承 1 项属性/);
  assert.doesNotMatch(svg,/<script|订单 <&>/);
  const position={...node.position()};f.cy.zoom(1.2);f.cy.pan({x:24,y:35});
  f.canvas.setDisplay('compact');
  assert.equal(node.data('display'),'compact');
  assert.deepEqual({...node.position()},position,'display changes retain the hierarchy placement');
  assert.equal(f.cy.zoom(),1.2);
  assert.deepEqual({...f.cy.pan()},{x:24,y:35});
  f.canvas.setDisplay('cards');assert.equal(node.data('display'),'cards');
  assert.deepEqual(f.commands,[],'node presentation never emits semantic commands');
});

test('changing graph node display preserves coordinates without adding hierarchy bands', t => {
  const f = fixture(); t.after(() => f.canvas.destroy());
  const positions = f.cy.nodes().map(node => ({ ...node.position() }));
  f.canvas.setDisplay('compact');
  f.canvas.setDisplay('cards');
  assert.deepEqual(f.cy.nodes().map(node => ({ ...node.position() })), positions);
  assert.equal(f.bands().length, 0, 'depth bands belong only to the hierarchy layout');
});

test('business arrows use domain to range while inheritance points to the declared parent', t => {
  const f=fixture('graph',{nodes:[{id:'parent'},{id:'child'}],edges:[
    {id:'inherit',source:'parent',target:'child',kind:'inherit'},
    {id:'business',source:'child',target:'parent',kind:'relation',termIri:'urn:relationship'}]});
  t.after(()=>f.canvas.destroy());
  assert.equal(f.cy.$id('inherit').data('source'),'parent','layout keeps existing inheritance direction');
  assert.equal(f.cy.$id('inherit').data('sourceArrow'),'triangle');
  assert.equal(f.cy.$id('inherit').data('targetArrow'),'none');
  assert.equal(f.cy.$id('business').data('targetArrow'),'triangle');
  assert.equal(f.cy.$id('business').data('termIri'),'urn:relationship');
  f.cy.$id('business').emit('tap');assert.deepEqual(f.selected,['urn:relationship']);
});

test('Cytoscape applies card sizes and arrow shapes to the rendered elements', t => {
  const f=fixture('graph',{nodes:[{id:'p',label:'Parent'},{id:'c',label:'Child'}],edges:[
    {id:'inherit',source:'p',target:'c',kind:'inherit'},
    {id:'business',source:'c',target:'p',kind:'relation'}]}, {styleEnabled:true});
  t.after(()=>f.canvas.destroy());
  assert.equal(f.cy.$id('p').style('width'),'196px');
  assert.equal(f.cy.$id('p').style('label'),'');
  assert.equal(f.cy.$id('inherit').style('source-arrow-shape'),'triangle');
  assert.equal(f.cy.$id('inherit').style('source-arrow-fill'),'hollow');
  assert.equal(f.cy.$id('business').style('target-arrow-shape'),'triangle');
  assert.equal(f.cy.$id('business').style('line-style'),'solid');
  f.canvas.setDisplay('compact');
  assert.equal(f.cy.$id('p').style('width'),'62px');
  assert.equal(f.cy.$id('p').style('shape'),'ellipse');
});
