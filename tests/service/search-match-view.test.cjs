const {test}=require('node:test');
const assert=require('node:assert/strict');
const view=require('../../knowledge_service/web/search-match-view.js');
test('keyword explains field locations while semantic is never presented as a literal hit',()=>{
  const keyword=view.describe({mode:'keyword',fields:['title','body'],channels:['keyword']});
  assert.deepEqual(keyword.fields,['标题命中','正文命中']);
  assert.equal(keyword.highlight,true);
  const semantic=view.describe({mode:'semantic',fields:['title'],channels:['semantic']});
  assert.equal(semantic.label,'语义相关');
  assert.equal(semantic.highlight,false);
  assert.deepEqual(semantic.fields,['标题含查询文字']);
  assert.match(semantic.note,/不能.*标题.*正文/);
});
test('fusion labels come from the returned channels rather than the selected mode',()=>{
  assert.equal(view.describe({mode:'hybrid',channels:['semantic'],fields:[]}).label,'仅语义召回');
  assert.equal(view.describe({mode:'hybrid',channels:['keyword','semantic'],fields:[]}).label,'关键词＋语义召回');
  assert.equal(view.describe({mode:'hybrid',strategy:'keyword_rerank',channels:['keyword','semantic']}).label,'关键词候选 · 语义排序');
});
