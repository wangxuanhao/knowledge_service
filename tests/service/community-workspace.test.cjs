const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');

const web = join(__dirname, '../../knowledge_service/web');

test('社区页签、社区索引和社区问答使用同一工作台模块', () => {
  const html = readFileSync(join(web, 'index.html'), 'utf8');
  assert.match(html, /class="retrieval-kinds"[\s\S]*?data-retrieval-kind="knowledge"[\s\S]*?data-retrieval-kind="neighborhood"[\s\S]*?data-retrieval-kind="community"[\s\S]*?<input id="query"/);
  assert.doesNotMatch(html, /id="(?:community-from-query|community-browse-form)"/);
  assert.match(html, /data-runtime-view="communities"/);
  assert.match(html, /data-community-view="questions"/);
  assert.match(html, /\/assets\/community-workspace\.js\?v=/);
});

test('社区范围严格匹配已应用范围，无法映射时保留显式提示', () => {
  const {communityScope} = require(join(web, 'community-workspace.js'));
  assert.deepEqual(communityScope({}), {kind:'project'});
  assert.deepEqual(communityScope({applicability:{
    platforms:{mode:'specific',values:['京东']},businesses:{mode:'unknown',values:[]},audiences:{mode:'unknown',values:[]},
  }}),
    {kind:'platform',platform:'京东'});
  assert.equal(communityScope({valid_at:'2026-05-27'}), null);
  assert.equal(communityScope({filters:{field:'metadata.team',op:'eq',value:'ops'}}), null);
  assert.equal(communityScope({applicability:{platforms:{mode:'specific',values:['京东','美团']}}}), null);
  assert.equal(communityScope({applicability:{audiences:{mode:'specific',values:['商户']}}}), null);
  assert.equal(communityScope({applicability:{platforms:{mode:'all',values:[]}}}), null);
  assert.equal(communityScope({ontology_ids:['release-1']}), null);
  assert.equal(communityScope({ontology_scope:'unknown'}), null);
});

test('自动问答无需社区选择，有报告向量时优先语义', () => {
  const {questionMode} = require(join(web, 'community-workspace.js'));
  assert.equal(questionMode('auto', 0, true), 'semantic');
  assert.equal(questionMode('auto', 0, false), 'keyword');
  assert.equal(questionMode('auto', 2, true), 'keyword');
  assert.equal(questionMode('global', 2, true), 'global');
});
