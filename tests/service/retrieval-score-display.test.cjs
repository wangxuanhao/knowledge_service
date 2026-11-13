const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function render(row,mode='hybrid',extra={}){
  const hits={innerHTML:'',querySelectorAll:()=>[]};
  const context={window:{},get:()=>hits,scope:()=>({}),labelOf:value=>value,esc:value=>String(value??'')};
  const source=fs.readFileSync('knowledge_service/web/workspace.js','utf8');
  const declaration=source.match(/  function renderHits\([^\n]*\)\{[\s\S]*?\n  \}/)[0];
  vm.runInNewContext(declaration+';renderHits(result);',{...context,result:{hits:[{kind:'chunk',id:'c',text:'测试内容',...row}],active_mode:mode,...extra}});
  return hits.innerHTML;
}
test('missing and invalid scores do not invent a zero score',()=>{
  for(const score of [undefined,null,'',NaN,Infinity,'0.3',false]){
    const html=render({score});
    assert.doesNotMatch(html,/原文 · (?:0|NaN|Infinity)/);
    assert.doesNotMatch(html,/融合分|关键词分|语义分/);
  }
});
test('real scores retain zero, sign, BM25 scale and RRF precision',()=>{
  assert.match(render({score:0},'semantic'),/语义分 0/);
  assert.match(render({score:-.2},'semantic'),/语义分 -0.2/);
  assert.match(render({keyword_score:18.23456},'keyword'),/关键词分 18.235/);
  const a=render({score:.032786885}),b=render({score:.032522475});
  assert.match(a,/融合分 0.032787/);assert.match(b,/融合分 0.032522/);
});
test('provenance context never inherits a retrieval score',()=>{
  const html=render({},'hybrid',{hits:[],source_chunks:[{text:'来源',score:.3}],context_chunks:[{text:'前后文',score:.3}]});
  assert.doesNotMatch(html,/融合分|· 0.3/);
});
