const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const file=require('node:path').join(__dirname,'../../knowledge_service/web/ingest-result-summary.js');
const summary=fs.existsSync(file)?require(file):{};

test('ingestion summary reports saved result facts separately from paragraph and operation counts',()=>{
  const result={records:[],discovery_candidates:3,pending_reviews:0,document:{metadata:{discovery_candidates:[
    {id:'a',kind:'entity'},{id:'b',kind:'relation'},{id:'c',kind:'attribute'}]}}};
  const text=summary.text(result);
  assert.match(text,/开放发现候选 3 条.*实体 1.*关系 1.*属性 1/);assert.match(text,/尚未正式入库/);
});

test('scalar-only legacy counts remain useful without invented kind breakdowns',()=>{
  const text=summary.text({discovery_candidates:118,pending_reviews:0});
  assert.match(text,/开放发现候选 118 条/);assert.doesNotMatch(text,/实体 0|关系 0|属性 0/);
  assert.match(summary.text({replayed:true,records:[]}),/复用已保存结果/);
});

test('unknown kinds and replayed results never become a fabricated zero knowledge total',()=>{
  assert.match(summary.text({records:[{id:'legacy'}]}),/种类|类型/);
  assert.doesNotMatch(summary.text({records:[{id:'legacy'}]}),/知识 0 条/);
  assert.equal(summary.hasKnowledge({replayed:true,records:[]}),true);
  assert.equal(summary.hasKnowledge({records:[],document:{metadata:{discovery_candidates:[{id:'legacy-candidate'}]}}}),true);
});
