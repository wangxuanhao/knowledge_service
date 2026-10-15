const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/record-version-context.js');
const context=fs.existsSync(file)?require(file):{};

test('knowledge revisions and authoritative ontology versions have separate labels',()=>{
  assert.equal(typeof context.build,'function','shared version formatter is available');
  const index=context.build([{id:'old',version:7},{id:'latest',version:12}]);
  assert.equal(context.summary({version:3,ontology_id:'old'},index),'修订 r3 · 依据本体 v7（历史）');
  assert.equal(context.summary({version:9,ontology_id:'latest'},index),'修订 r9 · 依据本体 v12（当前）');
});
test('annotation releases keep the structure number and distinguish immutable IDs',()=>{
  assert.equal(typeof context.build,'function');
  const index=context.build([{id:'same-prefix-original',version:2},
    {id:'same-prefix-annotation',version:2,version_reused:true}]);
  const first=context.ontology({ontology_id:'same-prefix-original'},index);
  const second=context.ontology({ontology_id:'same-prefix-annotation'},index);
  assert.match(first,/v2（历史）/);assert.match(second,/v2（当前）.*标注修订/);
  assert.notEqual(index.list[0].displayId,index.list[1].displayId);
});
test('unreadable and unknown bindings never get a guessed version',()=>{
  assert.equal(typeof context.build,'function');
  assert.equal(context.ontology({ontology_id:'missing'},context.build([])),'未知本体');
  assert.equal(context.ontology({ontology_id:'missing'},null),'本体归属读取失败');
  assert.equal(context.ontology({},context.build([{id:'current',version:99}])),'未知本体');
});
