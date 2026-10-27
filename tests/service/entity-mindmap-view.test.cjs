const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.join(__dirname,'../../knowledge_service/web/entity-mindmap-view.js');
const view=fs.existsSync(file)?require(file):{};

test('mindmap keeps original facts and distinguishes repeated entity occurrences',()=>{
  assert.equal(typeof view.prepareTree,'function');
  const fact={id:'a',text:'甲',type:'Merchant',children:[
    {id:'b',text:'乙',predicate:'follows',children:[{id:'a',text:'甲',children:[]}]},
    {id:'a',text:'甲',predicate:'refers',children:[]},
  ]};
  const original=JSON.stringify(fact);
  const tree=view.prepareTree(fact);
  assert.equal(JSON.stringify(fact),original);
  assert.equal(tree.entityId,'a');
  assert.equal(tree.children[1].entityId,'a');
  assert.notEqual(tree.id,tree.children[1].id);
  assert.equal(tree.children[0].predicate,'follows');
});

test('a small tree stays compact and a broad tree reserves legible node spacing',()=>{
  assert.equal(typeof view.layout,'function');
  const small=view.prepareTree({id:'root',text:'根',children:[{id:'a',text:'甲'},{id:'b',text:'乙'}]});
  const compact=view.layout(small,1200,680);
  assert.ok(compact.width<=320,'two levels must not stretch across the canvas');
  assert.ok(compact.height<400,'two branches must not fill the viewport height');
  const broad=view.prepareTree({id:'root',children:Array.from({length:20},(_,i)=>({id:String(i),text:'分支'}))});
  assert.ok(view.layout(broad,1200,680).height>=20*110,'cards must not overlap in broad trees');
});

test('collapsing a branch changes the visible count without losing descendants',()=>{
  const tree=view.prepareTree({id:'root',children:[{id:'a',children:[{id:'b'},{id:'c'}]}]});
  tree.children[0].collapsed=false;
  assert.equal(view.layout(tree,1000,600).visible,4);
  tree.children[0].collapsed=true;
  assert.equal(view.layout(tree,1000,600).visible,2);
  assert.equal(tree.children[0].children.length,2);
});

test('long multilingual names wrap within cards and tooltip text is escaped',()=>{
  assert.equal(typeof view.wrapName,'function');
  const text=view.wrapName('这是一个非常长的中文实体名称以及 additional English text');
  assert.equal(text.split('\n').length,2);
  assert.ok(text.endsWith('…'));
  assert.ok(view.wrapName('共同侵权的责任认定与承担').split('\n').every(line=>line.length>=4),'avoid orphan characters in short second lines');
  assert.equal(view.escapeHtml('<img src=x onerror="x">'),'&lt;img src=x onerror=&quot;x&quot;&gt;');
});
