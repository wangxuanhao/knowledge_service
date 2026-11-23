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
test('shared scores distinguish a binary hit, cosine similarity and reciprocal-rank fusion',()=>{
  assert.deepEqual(view.rankBadges({mode:'keyword',score:18.3}),['关键词匹配 1']);
  assert.deepEqual(view.rankBadges({mode:'semantic',score:.83}),['语义相似度 0.83']);
  assert.deepEqual(view.rankBadges({mode:'hybrid',channels:['keyword','semantic'],score:.032786885}),
    ['关键词匹配 1','融合分 0.032787']);
  assert.deepEqual(view.rankBadges({mode:'hybrid',strategy:'keyword_rerank',channels:['keyword','semantic'],score:.83}),
    ['关键词匹配 1','语义重排 0.83']);
  assert.deepEqual(view.rankBadges({mode:'semantic',rrf_score:.02,score:.02}),['融合分 0.02']);
  assert.deepEqual(view.rankBadges({mode:'keyword',supplemental:true}),[]);
  assert.deepEqual(view.rankBadges({mode:'semantic',score:NaN}),[]);
});
test('record explanations separate observed locations from fields used to rank a candidate',()=>{
  assert.deepEqual(view.recordMatch({kind:'entity',id:'merchant-1',text:'商户',metadata:{aliases:['门店']}},'门店',
    {mode:'keyword'}).fields,['aliases']);
  assert.deepEqual(view.hitLabels({mode:'keyword',fields:['title'],indexed_fields:['body','title','filename']}),
    ['关键词匹配 1','标题命中']);
  assert.deepEqual(view.hitLabels({mode:'semantic',score:.83,fields:[],indexed_fields:['body','title','filename']}),
    ['语义相似度 0.83','参与检索：正文片段、标题、文件名']);
});
