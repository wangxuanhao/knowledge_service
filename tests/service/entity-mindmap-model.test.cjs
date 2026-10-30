const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.join(__dirname,'../../knowledge_service/web/entity-mindmap-model.js');
const model=fs.existsSync(file)?require(file):{};

function fixture(){
  return {id:'root',entityId:'entity-a',name:'根实体',type:'Merchant',collapsed:false,children:[
    {id:'left',entityId:'entity-b',name:'左分支',type:'Rule',predicate:'follows',collapsed:true,children:[
      {id:'hidden',entityId:'entity-a',name:'隐藏的商户',type:'Merchant',predicate:'references',collapsed:false,children:[]},
    ]},
    {id:'right',entityId:'entity-a',name:'右分支',type:'Rule',predicate:'mentions',collapsed:false,children:[]},
  ]};
}

test('index retains distinct occurrence IDs, hidden descendants and actual tree edges',()=>{
  assert.equal(typeof model.indexTree,'function');
  const tree=fixture(),before=JSON.stringify(tree),index=model.indexTree(tree);
  assert.ok(index.nodes instanceof Map);
  assert.ok(index.parents instanceof Map);
  assert.deepEqual([...index.nodes.keys()],['root','left','hidden','right']);
  assert.equal(index.nodes.get('hidden').entityId,index.nodes.get('root').entityId);
  assert.equal(index.parents.get('hidden'),'left');
  assert.equal(index.parents.has('root'),false);
  assert.deepEqual(index.edges,[
    {id:'left',parentId:'root',childId:'left',predicate:'follows'},
    {id:'hidden',parentId:'left',childId:'hidden',predicate:'references'},
    {id:'right',parentId:'root',childId:'right',predicate:'mentions'},
  ]);
  assert.equal(JSON.stringify(tree),before);
});

test('search matches names, translated types and entity IDs across collapsed branches',()=>{
  assert.equal(typeof model.search,'function');
  const index=model.indexTree(fixture()),labelOf=value=>({Merchant:'商户',Rule:'业务规则'}[value]||value);
  assert.deepEqual(model.search(index,'隐藏',labelOf).map(node=>node.id),['hidden']);
  assert.deepEqual(model.search(index,'商户',labelOf).map(node=>node.id),['root','hidden']);
  assert.deepEqual(model.search(index,' ENTITY-A ',labelOf).map(node=>node.id),['root','hidden','right']);
  assert.deepEqual(model.search(index,'rule',labelOf).map(node=>node.id),['left','right']);
  assert.deepEqual(model.search(index,'  ',labelOf),[]);
});

test('reveal returns root-to-parent ancestors without changing collapse state',()=>{
  assert.equal(typeof model.reveal,'function');
  const tree=fixture(),before=JSON.stringify(tree),index=model.indexTree(tree);
  assert.deepEqual(model.reveal(index,'hidden'),['root','left']);
  assert.deepEqual(model.reveal(index,'root'),[]);
  assert.deepEqual(model.reveal(index,'missing'),[]);
  assert.equal(JSON.stringify(tree),before);
});

test('tree paths include only real edges, support reverse traversal and reject stale endpoints',()=>{
  assert.equal(typeof model.pathBetween,'function');
  const tree=fixture(),before=JSON.stringify(tree),index=model.indexTree(tree);
  assert.deepEqual(model.pathBetween(index,'hidden','right'),{nodeIds:['hidden','left','root','right'],edgeIds:['hidden','left','right']});
  assert.deepEqual(model.pathBetween(index,'right','hidden'),{nodeIds:['right','root','left','hidden'],edgeIds:['right','left','hidden']});
  assert.deepEqual(model.pathBetween(index,'hidden','left'),{nodeIds:['hidden','left'],edgeIds:['hidden']});
  assert.deepEqual(model.pathBetween(index,'left','left'),{nodeIds:['left'],edgeIds:[]});
  assert.equal(model.pathBetween(index,'missing','right'),null);
  assert.equal(model.pathBetween(index,'left','missing'),null);
  assert.equal(JSON.stringify(tree),before);
});

test('canonical tree deeply clones and expands every descendant',()=>{
  assert.equal(typeof model.canonicalTree,'function');
  const tree=fixture(),before=JSON.stringify(tree),canonical=model.canonicalTree(tree);
  assert.notEqual(canonical,tree);
  assert.notEqual(canonical.children,tree.children);
  assert.notEqual(canonical.children[0].children[0],tree.children[0].children[0]);
  assert.equal(canonical.children[0].collapsed,false);
  canonical.children[0].children[0].name='Changed copy';
  assert.equal(JSON.stringify(tree),before);
  assert.deepEqual([...model.indexTree(null).nodes],[]);
  assert.equal(model.canonicalTree(null),null);
});
