const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');

const {frozenSourceDocument,attributeDetailsHtml}=require(path.resolve(
  __dirname,'../../knowledge_service/web/evidence-inspector.js'));

test('changed provenance source assets use fresh cache versions',()=>{
  const html=require('node:fs').readFileSync(path.resolve(
    __dirname,'../../knowledge_service/web/index.html'),'utf8');
  // 这两个文件改过就必须带**新的** ?v=，否则浏览器一直用缓存的旧 JS。
  // 断言"带版本号、且不是那次改动之前的旧版本" —— 换新版本时要在这里同步（别让断言跟着自动走）。
  assert.match(html,/\/assets\/provenance-drawer\.js\?v=[^"']+/);
  assert.match(html,/\/assets\/evidence-inspector\.js\?v=[^"']+/);
  assert.ok(!html.includes('/assets/evidence-inspector.js?v=candidate-evidence-1'),
    'evidence-inspector.js 改过文案后必须换新的 ?v=（B1：撤销入口指向「知识台账 → 可撤销的操作」）');
});

test('frozen source viewer distinguishes complete historical versions from legacy segments',()=>{
  const complete=frozenSourceDocument({
    title:'合同',version:3,version_id:'doc-v3',source_content:'full_version',
    excerpt_before:'前文',highlight:'定位内容',excerpt_after:'后文'});
  assert.equal(complete.source_content,'full_version');
  assert.equal(complete.reason,'回答生成时冻结的历史原文定位');
  assert.equal(complete.highlight,'定位内容');

  const legacy=frozenSourceDocument({
    title:'旧来源',version:1,source_content:'segments_only',highlight:'旧片段'});
  assert.equal(legacy.source_content,'segments_only');
});

test('candidate source projection accepts immutable full text and absolute highlight',()=>{
  const projected=frozenSourceDocument({
    title:'固定历史文档',version:4,version_id:'doc-v4',source_content:'full_version',
    reason:'候选证据 · 历史切片内恢复定位',full_text:'012345恢复证据abcdef',
    start_char:6,end_char:10,
  });
  assert.equal(projected.title,'固定历史文档');
  assert.equal(projected.version,4);
  assert.equal(projected.reason,'候选证据 · 历史切片内恢复定位');
  assert.equal(projected.before,'012345');
  assert.equal(projected.highlight,'恢复证据');
  assert.equal(projected.after,'abcdef');
});

test('formal attributes render every value with support and an explicit escaped conflict marker',()=>{
  const html=attributeDetailsHtml([{predicate:'https://test/age',label:'年龄 <script>',values:[
    {record_id:'age-1',value:20,datatype:'http://www.w3.org/2001/XMLSchema#integer',
      valid_from:'2025-01-01',valid_until:null,accepted_support_count:2,status:'accepted'},
    {record_id:'age-2',value:21,datatype:'http://www.w3.org/2001/XMLSchema#integer',
      valid_from:null,valid_until:null,accepted_support_count:1,status:'accepted'},
    {record_id:null,candidate_id:'candidate-1',value:'<script>alert(1)</script>',
      datatype:'http://www.w3.org/2001/XMLSchema#string',valid_from:null,valid_until:null,
      accepted_support_count:0,status:'contradicting',conflict:{reason:'来源冲突'}},
  ]}]);
  assert.ok(html.includes('aria-label="正式属性"'));
  assert.equal((html.match(/<tr>/g)||[]).length,4);
  assert.ok(html.includes('20'));
  assert.ok(html.includes('21'));
  assert.ok(html.includes('2 条支撑'));
  assert.ok(html.includes('冲突候选'));
  assert.ok(html.includes('来源冲突'));
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert.ok(html.includes('https://test/age'));
  assert.equal(html.includes('<script>'),false);
});
