const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');

const {frozenSourceDocument}=require(path.resolve(
  __dirname,'../../knowledge_service/web/evidence-inspector.js'));

test('changed provenance source assets use fresh cache versions',()=>{
  const html=require('node:fs').readFileSync(path.resolve(
    __dirname,'../../knowledge_service/web/index.html'),'utf8');
  assert.ok(html.includes('/assets/provenance-drawer.js?v=3'));
  assert.ok(html.includes('/assets/evidence-inspector.js?v=provenance-2'));
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
