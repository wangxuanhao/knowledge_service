const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const file=path.join(__dirname,'../../knowledge_service/web/graph-exploration.js');
const model=fs.existsSync(file)?require(file):{};
const entity=id=>({id,kind:'entity',name:id});
const relation=(id,from,to)=>({id,subject_id:from,object_id:to,kind:'relation'});
const graph=(ids,edges=[],metadata={})=>({...metadata,nodes:ids.map(entity),edges});
const ids=rows=>rows.map(row=>row.id);

test('UMD exposes the same constructors in browser and CommonJS',()=>{
  assert.equal(typeof model.NeighborIndex,'function');
  assert.equal(typeof model.BranchSession,'function');
  const sandbox={};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),sandbox);
  assert.equal(typeof sandbox.GraphExploration.NeighborIndex,'function');
});

test('neighbors preserve parallel relations, direction and one self-loop',()=>{
  const source=graph(['a','b','c'],[relation('ab','a','b'),relation('ab2','a','b'),relation('ca','c','a'),relation('aa','a','a')]);
  const index=new model.NeighborIndex(source);
  const all=index.page('a');
  assert.equal(all.count,4);
  assert.deepEqual(new Set(all.items.map(item=>item.edge.id)),new Set(['ab','ab2','ca','aa']));
  assert.equal(all.items.filter(item=>item.direction==='self').length,1);
  assert.equal(index.page('a',{direction:'forward'}).count,3);
  assert.equal(index.page('a',{direction:'backward'}).count,2);
  assert.equal(all.items.find(item=>item.edge.id==='ab').node,source.nodes[1]);
  assert.equal(all.items.find(item=>item.edge.id==='ca').direction,'in');
});

test('pages cover every association once and reset old cursors after append',()=>{
  const source=graph(['a','b','c','d'],[relation('ab','a','b'),relation('ac','a','c'),relation('da','d','a')]);
  const index=new model.NeighborIndex(source);
  const first=index.page('a',{limit:2});
  const second=index.page('a',{limit:2,cursor:first.nextCursor});
  assert.equal(first.items.length,2);
  assert.equal(second.items.length,1);
  assert.equal(second.nextCursor,null);
  assert.equal(new Set([...first.items,...second.items].map(item=>item.edge.id)).size,3);
  const previous=index.sync();
  source.nodes.push(entity('e'));
  source.edges.push(relation('ae','a','e'));
  assert.ok(index.sync()>previous);
  const reset=index.page('a',{limit:2,cursor:first.nextCursor});
  assert.equal(reset.resetCursor,true);
  assert.deepEqual(reset.items,index.page('a',{limit:2}).items);
  assert.equal(reset.count,4);
});

test('virtual attributes and dangling rows do not become entity neighbors or anchors',()=>{
  const source=graph(['a','b'],[relation('ab','a','b'),relation('missing','a','unknown')]);
  source.nodes.push({id:'attr',kind:'attribute_value'},{id:'virtual',virtual:true});
  source.edges.push(relation('attr1','a','attr'),{...relation('attr2','a','b'),kind:'attribute_edge'},{...relation('attr3','a','b'),type:'KS_ATTRIBUTE'},relation('virtual','a','virtual'));
  const index=new model.NeighborIndex(source);
  assert.equal(index.page('a').count,1);
  assert.deepEqual(index.page('a').items.map(item=>item.edge.id),['ab']);
  assert.equal(index.page('attr').items.length,0);
  assert.equal(index.focus('attr').nodeIds.size,0);
  assert.equal(index.focus('missing').nodeIds.size,0);
});

test('incremental typed index reuses full graph maps and reads only appended rows',()=>{
  let nodeReads=0,edgeReads=0;
  const records=new Proxy([entity('a'),entity('b')],{get(target,key){if(/^\d+$/.test(String(key)))nodeReads++;return Reflect.get(target,key);}});
  const edges=new Proxy([relation('ab','a','b')],{get(target,key){if(/^\d+$/.test(String(key)))edgeReads++;return Reflect.get(target,key);}});
  const nodeIndex=new Map([['a',0],['b',1]]),source={records,edges,nodeIndex};
  const index=new model.NeighborIndex(source);
  assert.equal(index.nodeIndex,nodeIndex);
  assert.ok(index.headOut instanceof Int32Array);
  assert.ok(index.headIn instanceof Int32Array);
  assert.ok(index.nextOut instanceof Int32Array);
  assert.ok(index.nextIn instanceof Int32Array);
  nodeReads=0;edgeReads=0;
  const version=index.sync();
  assert.equal(index.sync(),version);
  assert.equal(nodeReads+edgeReads,0);
  records.push(entity('c'));nodeIndex.set('c',2);edges.push(relation('bc','b','c'));
  index.sync();
  assert.ok(edgeReads<=2);
  assert.ok(nodeReads<=4);
  assert.equal(index.page('c').items[0].node,records[1]);
  assert.ok(index.headOut.length<Math.max(16,records.length*4));
  assert.ok(index.nextOut.length<Math.max(16,edges.length*4));
});

test('page scanning stays bounded for thousands of invalid associations',()=>{
  const source=graph(['a','b'],Array.from({length:6000},(_,i)=>relation('bad'+i,'a','missing'+i)));
  source.edges.push(relation('ab','a','b'));
  const index=new model.NeighborIndex(source),page=index.page('a',{limit:2});
  assert.equal(page.items.length,1);
  assert.ok(page.scanned<=64);
  assert.equal(page.count,1);
});

test('focus uses direction and two hops inside the supplied projection only',()=>{
  const source=graph(['a','b','c','d'],[relation('ab','a','b'),relation('bc','b','c'),relation('da','d','a'),relation('ch','c','hidden')]);
  const index=new model.NeighborIndex(source);
  assert.deepEqual(index.focus('a',{direction:'forward'}).nodeIds,new Set(['a','b']));
  assert.deepEqual(index.focus('a',{direction:'forward',hops:2}).nodeIds,new Set(['a','b','c']));
  assert.deepEqual(index.focus('a',{direction:'backward',hops:2}).nodeIds,new Set(['a','d']));
  assert.deepEqual(index.focus('a',{direction:'both',hops:2}).edgeIds,new Set(['ab','da','bc']));
});

test('focus enforces scan, node and edge budgets with deterministic endpoint closure',()=>{
  const source=graph(['a',...Array.from({length:3000},(_,i)=>'n'+i)],Array.from({length:3000},(_,i)=>relation('e'+i,'a','n'+i)));
  const index=new model.NeighborIndex(source);
  const focused=index.focus('a',{hops:2,maxNodes:4,maxEdges:2,maxScannedEdges:7});
  assert.equal(focused.truncated,true);
  assert.ok(focused.nodeIds.size<=4);
  assert.ok(focused.edgeIds.size<=2);
  assert.ok(focused.scannedEdges<=7);
  for(const edge of source.edges.filter(edge=>focused.edgeIds.has(edge.id))){assert.ok(focused.nodeIds.has(edge.subject_id));assert.ok(focused.nodeIds.has(edge.object_id));}
  assert.deepEqual(index.focus('a',{hops:2,maxNodes:4,maxEdges:2,maxScannedEdges:7}),focused);
  assert.equal(index.focus('a',{maxNodes:0}).nodeIds.size,0);
});

test('base records and metadata survive branch replacement and collapse without mutation',()=>{
  const base=graph(['a','b'],[relation('ab','a','b')],{total:12,meta:{scope:'original'}});
  const branch=graph(['a','x'],[relation('ax','a','x')]);
  const before=JSON.stringify({base,branch}),session=new model.BranchSession();
  session.reset(base,'a','scope');
  assert.equal(session.expand('a',branch,'scope'),true);
  const expanded=session.graph();
  assert.deepEqual(ids(expanded.nodes),['a','b','x']);
  assert.equal(expanded.nodes[0],base.nodes[0]);
  assert.equal(expanded.nodes[2],branch.nodes[1]);
  assert.equal(expanded.total,12);
  assert.equal(expanded.meta,base.meta);
  assert.equal(session.expand('a',graph(['a','y'],[relation('ay','a','y')]),'scope'),true);
  assert.deepEqual(ids(session.graph().nodes),['a','b','y']);
  assert.equal(session.collapse('a'),true);
  assert.deepEqual(ids(session.graph().nodes),['a','b']);
  assert.equal(JSON.stringify({base,branch}),before);
});

test('shared descendants survive one owner collapse and disappear after the final owner',()=>{
  const session=new model.BranchSession();
  session.reset(graph(['a','b']),'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  session.expand('b',graph(['b','x'],[relation('bx','b','x')]),'s');
  session.expand('x',graph(['x','y'],[relation('xy','x','y')]),'s');
  session.expand('y',graph(['y','x'],[relation('yx','y','x')]),'s');
  session.collapse('a');
  assert.deepEqual(new Set(ids(session.graph().nodes)),new Set(['a','b','x','y']));
  assert.equal(session.has('x'),true);
  session.collapse('b');
  assert.deepEqual(ids(session.graph().nodes),['a','b']);
  assert.equal(session.has('x'),false);
  assert.equal(session.has('y'),false);
  session.expand('a',graph(['a','x'],[relation('ax2','a','x')]),'s');
  assert.deepEqual(new Set(ids(session.graph().nodes)),new Set(['a','b','x']));
});

test('collapse, reset, clear and newer same-anchor requests reject stale tickets',()=>{
  const session=new model.BranchSession(),base=graph(['a']);
  session.reset(base,'a','s');
  const first=session.begin('a','s'),latest=session.begin('a','s');
  assert.equal(session.accept(first,graph(['a','old'])),false);
  assert.equal(session.accept(latest,graph(['a','x'])),true);
  const collapsed=session.begin('a','s');
  session.collapse('a');
  assert.equal(session.accept(collapsed,graph(['a','z'])),false);
  const reset=session.begin('a','s');
  session.reset(base,'a','new');
  assert.equal(session.accept(reset,graph(['a','z'])),false);
  assert.equal(session.begin('a','s'),null);
  const cleared=session.begin('a','new');
  session.clear();
  assert.equal(session.accept(cleared,graph(['a','z'])),false);
  assert.deepEqual(session.graph().nodes,[]);
});

test('failed or detached requests retain accepted branches and cannot resurrect descendants',()=>{
  const session=new model.BranchSession();
  session.reset(graph(['a']),'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  const child=session.begin('x','s'),failure=session.begin('a','s');
  assert.equal(session.accept(failure,null),false);
  assert.equal(session.has('a'),true);
  session.collapse('a');
  assert.equal(session.accept(child,graph(['x','y'])),false);
  assert.equal(session.begin('missing','s'),null);
});

test('branch budgets keep base and accepted shared records while marking clipped results',()=>{
  const session=new model.BranchSession({maxNodes:4,maxEdges:2,maxAttributes:1});
  const base=graph(['a','b'],[relation('ab','a','b')]);
  session.reset(base,'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  const branch=graph(['b','y','z'],[relation('by','b','y'),relation('bz','b','z')]);
  branch.nodes.push({id:'p',kind:'attribute_value'},{id:'q',virtual:true});
  branch.edges.push({...relation('bp','b','p'),kind:'attribute_edge'},{...relation('bq','b','q'),virtual:true});
  assert.equal(session.expand('b',branch,'s'),true);
  const visible=session.graph();
  assert.equal(visible.nodes.filter(node=>!node.virtual&&node.kind!=='attribute_value').length,4);
  assert.equal(visible.nodes.filter(node=>node.virtual||node.kind==='attribute_value').length,1);
  assert.deepEqual(ids(visible.edges).filter(id=>['ab','ax'].includes(id)),['ab','ax']);
  assert.equal(visible.edges.filter(edge=>!edge.virtual&&edge.kind!=='attribute_edge').length,2);
  assert.equal(visible.truncated,true);
  assert.equal(visible.exploration_truncated,true);
  assert.equal(visible.nodes[0],base.nodes[0]);
  const visibleIds=new Set(ids(visible.nodes));
  assert.ok(visible.edges.every(edge=>visibleIds.has(edge.subject_id)&&visibleIds.has(edge.object_id)));
});

test('refresh preserves other branches, base over budget and canonical duplicate records',()=>{
  const session=new model.BranchSession({maxNodes:3,maxEdges:2});
  const base=graph(['a','b']);session.reset(base,'a','s');
  const first=graph(['a','x'],[relation('ax','a','x')]);
  session.expand('a',first,'s');
  session.expand('b',graph(['b','x'],[relation('bx','b','x')]),'s');
  session.expand('a',graph(['a','y'],[relation('ay','a','y')]),'s');
  assert.ok(ids(session.graph().nodes).includes('x'));
  assert.ok(ids(session.graph().edges).includes('bx'));
  const oversized=new model.BranchSession({maxNodes:1,maxEdges:0});
  oversized.reset(base,'a','s');
  assert.deepEqual(oversized.graph().nodes,base.nodes);
  assert.equal(oversized.graph().truncated,true);
  const duplicate=graph(['a','b']);duplicate.nodes[0].version=999;
  oversized.expand('a',duplicate,'s');
  assert.equal(oversized.graph().nodes[0],base.nodes[0]);
});

test('attribute anchors cannot start expansion',()=>{
  const session=new model.BranchSession();
  session.reset({nodes:[entity('a'),{id:'p',kind:'attribute_value'}],edges:[]},'a','s');
  assert.equal(session.begin('p','s'),null);
  assert.equal(session.expand('p',graph(['p','bad']),'s'),false);
});

test('self-loop pages end without an empty continuation and direction changes reset cursors',()=>{
  const index=new model.NeighborIndex(graph(['a','b'],[relation('aa','a','a'),relation('ab','a','b')]));
  const first=index.page('a',{limit:1}),last=index.page('a',{limit:1,cursor:first.nextCursor});
  assert.equal(last.items.length,1);
  assert.equal(last.nextCursor,null);
  const backward=index.page('a',{limit:1,direction:'backward',cursor:first.nextCursor});
  assert.equal(backward.resetCursor,true);
  assert.equal(backward.nextCursor,null);
  assert.equal(backward.items[0].edge.id,'aa');
});

test('conflicting relation endpoints reject replacement atomically',()=>{
  const session=new model.BranchSession();
  session.reset(graph(['a']),'a','s');
  session.expand('a',graph(['a','x'],[relation('same','a','x')]),'s');
  const before=session.graph(),ticket=session.begin('a','s');
  assert.equal(session.accept(ticket,graph(['a','y'],[relation('same','a','y')])),false);
  assert.deepEqual(session.graph(),before);
});

test('page reads only its bounded associations after the index is synchronized',()=>{
  let reads=0;
  const edges=new Proxy(Array.from({length:5000},(_,i)=>relation('e'+i,'a','b')),{get(target,key){if(/^\d+$/.test(String(key)))reads++;return Reflect.get(target,key);}});
  const index=new model.NeighborIndex({...graph(['a','b']),edges});
  reads=0;
  const page=index.page('a',{limit:30});
  assert.equal(page.count,5000);
  assert.equal(page.items.length,30);
  assert.ok(reads<=64);
});

test('branch replacement frees the budget of descendants it detaches',()=>{
  const session=new model.BranchSession({maxNodes:3});
  session.reset(graph(['a']),'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  session.expand('x',graph(['x','y'],[relation('xy','x','y')]),'s');
  assert.equal(session.expand('a',graph(['a','z'],[relation('az','a','z')]),'s'),true);
  assert.deepEqual(ids(session.graph().nodes),['a','z']);
  assert.deepEqual(ids(session.graph().edges),['az']);
  assert.equal(session.graph().truncated,false);
  assert.equal(session.has('x'),false);
});

test('replacement reserves descendants that retain support from another branch',()=>{
  const session=new model.BranchSession({maxNodes:5});
  session.reset(graph(['a','b']),'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  session.expand('b',graph(['b','x'],[relation('bx','b','x')]),'s');
  session.expand('x',graph(['x','y'],[relation('xy','x','y')]),'s');
  session.expand('a',graph(['a','z','extra'],[relation('az','a','z'),relation('ae','a','extra')]),'s');
  assert.deepEqual(new Set(ids(session.graph().nodes)),new Set(['a','b','z','x','y']));
  assert.equal(session.has('x'),true);
  assert.ok(ids(session.graph().edges).includes('xy'));
  assert.equal(session.graph().truncated,true);
});

test('focus exposes bounded canonical edge records matching the retained IDs',()=>{
  const source=graph(['a','b','c'],[relation('ab','a','b'),relation('ab2','a','b'),relation('bc','b','c')]);
  const index=new model.NeighborIndex(source),focused=index.focus('a',{hops:2,maxEdges:2});
  assert.ok(Array.isArray(focused.edgeRecords));
  assert.deepEqual(new Set(ids(focused.edgeRecords)),focused.edgeIds);
  assert.equal(focused.edgeRecords.length,focused.edgeIds.size);
  assert.ok(focused.edgeRecords.length<=2);
  assert.ok(focused.edgeRecords.every(edge=>source.edges.includes(edge)));
  assert.deepEqual(index.focus('missing').edgeRecords,[]);
  assert.deepEqual(index.focus('a',{maxEdges:0}).edgeRecords,[]);
});

test('branch records stay coherent with visible canonical nodes and edges',()=>{
  const base=graph(['a']),branch=graph(['a','x'],[relation('ax','a','x')]);
  base.records=[...base.nodes,...base.edges];
  branch.records=[...branch.nodes,...branch.edges];
  const session=new model.BranchSession();session.reset(base,'a','s');
  session.expand('a',branch,'s');
  const visible=session.graph();
  assert.deepEqual(visible.records,[...visible.nodes,...visible.edges]);
  assert.equal(visible.records.find(row=>row.id==='x'),branch.nodes[1]);
  assert.equal(visible.records.find(row=>row.id==='ax'),branch.edges[0]);
  session.collapse('a');
  assert.deepEqual(session.graph().records,base.records);
  assert.equal(base.records.length,1);
});

test('visible branches merge presentation maps and attribute counts while preserving base scope',()=>{
  const scope={at:'fixed'},neighborhood={seed:'a',hops:1};
  const base=graph(['a'],[],{scope,neighborhood,total:100,attribute_count:1,type_labels:{A:'base A'},class_parents:{A:['Base']},class_ancestors:{A:['BaseRoot']}});
  base.nodes.push({id:'p',kind:'attribute_value'});
  const branch=graph(['a','x'],[relation('ax','a','x')],{scope:{at:'other'},neighborhood:{seed:'x'},total:999,attribute_count:1,type_labels:{A:'branch A',X:'branch X'},class_parents:{X:['Parent']},class_ancestors:{X:['Ancestor']}});
  branch.nodes.push({id:'q',virtual:true});
  const child=graph(['x','y'],[relation('xy','x','y')],{type_labels:{Y:'child Y'},class_parents:{Y:['ChildParent']},class_ancestors:{Y:['ChildAncestor']}});
  const before=JSON.stringify({base,branch,child}),session=new model.BranchSession();
  session.reset(base,'a','s');session.expand('a',branch,'s');session.expand('x',child,'s');
  const visible=session.graph();
  assert.deepEqual(visible.type_labels,{A:'branch A',X:'branch X',Y:'child Y'});
  assert.deepEqual(visible.class_parents,{A:['Base'],X:['Parent'],Y:['ChildParent']});
  assert.deepEqual(visible.class_ancestors,{A:['BaseRoot'],X:['Ancestor'],Y:['ChildAncestor']});
  assert.equal(visible.attribute_count,2);
  assert.equal(visible.total,100);
  assert.equal(visible.scope,scope);
  assert.equal(visible.neighborhood,neighborhood);
  session.collapse('a');
  const collapsed=session.graph();
  assert.deepEqual(collapsed.type_labels,base.type_labels);
  assert.deepEqual(collapsed.class_parents,base.class_parents);
  assert.deepEqual(collapsed.class_ancestors,base.class_ancestors);
  assert.equal(collapsed.attribute_count,1);
  assert.equal(JSON.stringify({base,branch,child}),before);
});

test('branch-only records and presentation fields disappear after collapse without inventing totals',()=>{
  const base=graph(['a']),branch=graph(['a','x'],[],{attribute_count:99,type_labels:{X:'X'},class_parents:{X:['P']},class_ancestors:{X:['A']}});
  branch.records=[...branch.nodes];
  const session=new model.BranchSession();session.reset(base,'a','s');session.expand('a',branch,'s');
  const visible=session.graph();
  assert.deepEqual(visible.records,[...visible.nodes,...visible.edges]);
  assert.equal(visible.attribute_count,0);
  assert.equal(Object.hasOwn(visible,'total'),false);
  session.collapse('a');
  const collapsed=session.graph();
  for(const key of ['records','attribute_count','type_labels','class_parents','class_ancestors','total'])assert.equal(Object.hasOwn(collapsed,key),false,key);
});

test('deferred construction reads no rows and bounded sync waits for all node indexing',()=>{
  let nodeReads=0,edgeReads=0;
  const nodes=new Proxy(['a','b','c','d','e'].map(entity),{get(target,key){if(/^\d+$/.test(String(key)))nodeReads++;return Reflect.get(target,key);}});
  const edges=new Proxy([relation('ae','a','e'),relation('ab','a','b'),relation('cd','c','d')],{get(target,key){if(/^\d+$/.test(String(key)))edgeReads++;return Reflect.get(target,key);}});
  const source={nodes,edges},index=new model.NeighborIndex(source,{defer:true});
  assert.equal(nodeReads+edgeReads,0);
  assert.equal(index.complete,false);
  index.sync({maxNodes:2,maxEdges:1});
  assert.equal(nodeReads,2);
  assert.equal(edgeReads,0);
  assert.equal(index.complete,false);
  nodeReads=0;
  index.sync({maxNodes:2,maxEdges:1});
  assert.equal(nodeReads,2);
  assert.equal(edgeReads,0);
  nodeReads=0;
  index.sync({maxNodes:2,maxEdges:1});
  assert.ok(nodeReads<=4);
  assert.equal(edgeReads,1);
  assert.equal(index.complete,false);
  nodeReads=0;edgeReads=0;
  index.sync({maxNodes:0,maxEdges:1});
  assert.ok(nodeReads<=2);
  assert.equal(edgeReads,1);
  assert.equal(index.complete,false);
  index.sync({maxNodes:0,maxEdges:1});
  assert.equal(index.complete,true);
  const eager=new model.NeighborIndex(source);
  assert.deepEqual(index.page('a').items,eager.page('a').items);
  assert.deepEqual(index.focus('a',{hops:2}),eager.focus('a',{hops:2}));
});

test('appends during deferred preparation remain indexed after bounded completion',()=>{
  const source=graph(['a','b','c'],[relation('ac','a','c')]);
  const index=new model.NeighborIndex(source,{defer:true});
  index.sync({maxNodes:2,maxEdges:1});
  source.nodes.push(entity('d'));source.edges.push(relation('cd','c','d'));
  index.sync({maxNodes:1,maxEdges:1});
  assert.equal(index.complete,false);
  assert.equal(index.version,2);
  let chunks=0;
  while(!index.complete&&chunks++<10)index.sync({maxNodes:1,maxEdges:1});
  assert.equal(index.complete,true);
  assert.equal(index.page('c').count,2);
  assert.deepEqual(index.focus('a',{hops:2,direction:'forward'}).nodeIds,new Set(['a','c','d']));
  source.nodes.push(entity('e'));source.edges.push(relation('de','d','e'));
  assert.equal(index.complete,false);
  index.sync();
  assert.equal(index.complete,true);
  assert.equal(index.page('e').count,1);
});

test('default page and focus complete deferred work without an explicit preparer',()=>{
  const source=graph(['a','b'],[relation('ab','a','b')]);
  const pageIndex=new model.NeighborIndex(source,{defer:true});
  assert.equal(pageIndex.complete,false);
  assert.equal(pageIndex.page('a').count,1);
  assert.equal(pageIndex.complete,true);
  const focusIndex=new model.NeighborIndex(source,{defer:true});
  assert.deepEqual(focusIndex.focus('a').edgeIds,new Set(['ab']));
  assert.equal(focusIndex.complete,true);
});

test('invalidating pending requests retains accepted graph and permits future tickets',()=>{
  const session=new model.BranchSession(),base=graph(['a'],[],{scope:{at:'fixed'}});
  session.reset(base,'a','s');
  session.expand('a',graph(['a','x'],[relation('ax','a','x')]),'s');
  const before=session.graph(),stale=session.begin('a','s'),child=session.begin('x','s');
  session.invalidatePending();
  assert.deepEqual(session.graph(),before);
  assert.equal(session.has('a'),true);
  assert.equal(session.accept(stale,graph(['a','old'])),false);
  assert.equal(session.accept(child,graph(['x','old'])),false);
  assert.equal(session.begin('a','other'),null);
  const fresh=session.begin('x','s');
  assert.equal(session.accept(fresh,graph(['x','y'],[relation('xy','x','y')])),true);
  assert.deepEqual(ids(session.graph().nodes),['a','x','y']);
  assert.equal(session.graph().scope,base.scope);
});
