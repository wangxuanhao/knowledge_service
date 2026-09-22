const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');

const {frozenSourceDocument,attributeDetailsHtml}=require(path.resolve(
  __dirname,'../../knowledge_service/web/evidence-inspector.js'));

test('changed provenance source assets use fresh cache versions',()=>{
  const html=require('node:fs').readFileSync(path.resolve(
    __dirname,'../../knowledge_service/web/index.html'),'utf8');
  assert.ok(html.includes('/assets/provenance-drawer.js?v=3'));
  assert.ok(html.includes('/assets/evidence-inspector.js?v=attribute-facts-1'));
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
