const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/qa-presentation.js');
function api(){const context={window:{}};vm.runInNewContext(fs.readFileSync(file,'utf8'),context);return context.window.QaPresentation;}
test('answer formatting recognizes headings, lists, emphasis and citations without creating HTML or links',()=>{
  const p=api();
  const blocks=p.blocks('# 价格说明\n\n**团购价：**销售价 [U1]\n- 外卖价\n<script>alert(1)</script> [坏链接](javascript:alert(1))');
  assert.equal(blocks[0].tag,'h3');
  assert.ok(blocks.some(b=>b.tag==='li'));
  const tokens=p.inline('**团购价：**销售价 [U1] <img src=x onerror=alert(1)>');
  assert.ok(tokens.some(t=>t.type==='strong'&&t.text==='团购价：'));
  assert.ok(tokens.some(t=>t.type==='citation'&&t.text==='[U1]'));
  assert.ok(tokens.some(t=>t.type==='text'&&t.text.includes('<img')));
  assert.equal(p.inline('**没有闭合')[0].type,'text');
  assert.ok(!tokens.some(t=>t.type==='html'||t.type==='link'));
});
test('generation status explains an enabled but skipped model and reports numeric milliseconds',()=>{
  const p=api();
  assert.match(p.status({generation:{requested:true,called:false,skip_reason:'no_eligible_evidence'},timings_ms:{total:877,retrieval:605}}),/未调用模型.*有效/);
  assert.match(p.status({generation:{called:true},timings_ms:{total:1500,llm:700}}),/模型回答.*1.50 s/);
  assert.match(p.status({generation:{requested:false,called:false,skip_reason:'disabled'}}),/仅展示依据/);
  assert.match(p.status({mode:'evidence_only',generation:{called:true}}),/模型已调用.*仅展示依据/);
  assert.match(p.status({mode:'llm',generation:{called:true},answer_purpose:'document_explanation',answer_label:'按文档说明，有效性待核对'}),/模型回答.*按文档说明.*待核对/);
});
