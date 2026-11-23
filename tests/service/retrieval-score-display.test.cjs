const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function render(row,mode='hybrid',extra={},query=''){
  const hits={innerHTML:'',querySelectorAll:()=>[]};
  const context={window:{SourceMatchView:require('../../knowledge_service/web/search-match-view.js')},
    get:()=>hits,scope:()=>({}),labelOf:value=>value,esc:value=>String(value??'')};
  const source=fs.readFileSync('knowledge_service/web/workspace.js','utf8');
  const declaration=source.match(/  function renderHits\([^\n]*\)\{[\s\S]*?\n  \}/)[0];
  vm.runInNewContext(declaration+';renderHits(result,{},query);',{...context,query,result:{hits:[{kind:'chunk',id:'c',text:'测试内容',...row}],active_mode:mode,...extra}});
  return hits.innerHTML;
}
test('missing and invalid scores do not invent a zero score',()=>{
  for(const score of [undefined,null,'',NaN,Infinity,'0.3',false]){
    const html=render({score});
    assert.doesNotMatch(html,/原文 · (?:0|NaN|Infinity)/);
    assert.doesNotMatch(html,/融合分|关键词匹配|语义相似度/);
  }
});
test('real scores retain zero, sign, binary keywords and RRF precision',()=>{
  assert.match(render({score:0},'semantic'),/语义相似度 0/);
  assert.match(render({score:-.2},'semantic'),/语义相似度 -0.2/);
  assert.match(render({keyword_score:18.23456},'keyword'),/关键词匹配 1/);
  const a=render({score:.032786885}),b=render({score:.032522475});
  assert.match(a,/融合分 0.032787/);assert.match(b,/融合分 0.032522/);
});
test('chunk score shows the verified field and parent title independently of the excerpt',()=>{
  const html=render({search_source_title:'退款规则',retrieval_match:{mode:'keyword',fields:['title'],
    indexed_fields:['body','title','filename']}},'keyword');
  assert.match(html,/<b>退款规则<\/b>/);
  assert.match(html,/标题命中/);
  assert.match(html,/测试内容/);
});
test('a textual hit late in a ranked chunk is visible in the preview',()=>{
  const html=render({text:'前文'.repeat(180)+'退款时限以协议为准',
    retrieval_match:{mode:'keyword',fields:['body']}},'keyword',{},'退款时限');
  assert.match(html,/<mark>退款时限<\/mark>/);
});
test('provenance context never inherits a retrieval score',()=>{
  const html=render({},'hybrid',{hits:[],source_chunks:[{text:'来源',score:.3}],context_chunks:[{text:'前后文',score:.3}]});
  assert.doesNotMatch(html,/融合分|· 0.3/);
});
