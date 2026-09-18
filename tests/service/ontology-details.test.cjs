const { test } = require('node:test');
const assert = require('node:assert/strict');

const { shortIri } = require('../../knowledge_service/web/ontology-details.js');
const { renderTechnicalDetails } = require('../../knowledge_service/web/ontology-details.js');

test('shortIri presents terminal IRI segments while preserving the raw value', () => {
  assert.deepEqual(shortIri('urn:test:%E4%BF%9D%E6%8A%A4%E5%AF%B9%E8%B1%A1'), {
    label: '保护对象',
    malformed: false,
    raw: 'urn:test:%E4%BF%9D%E6%8A%A4%E5%AF%B9%E8%B1%A1',
  });
  assert.equal(shortIri('https://example.test/ns#Merchant').label, 'Merchant');
  assert.equal(shortIri('https://example.test/ns/Order').label, 'Order');
  assert.equal(shortIri('urn:test:a%2Fb').label, 'a/b');
  assert.deepEqual(shortIri('urn:test:%E8%B1%A'), {
    label: '%E8%B1%A',
    malformed: true,
    raw: 'urn:test:%E8%B1%A',
  });
  assert.equal(shortIri('urn:test:').label, 'urn:test:');
  assert.equal(shortIri('https://example.test/ns/').label, 'https://example.test/ns/');
  assert.equal(shortIri('https://example.test/ns#').label, 'https://example.test/ns#');
});

test('technical details explain the three raw formats in readable Chinese', () => {
  const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;');
  const html=renderTechnicalDetails({id:'draft-1',turtle:'<原始 Turtle>',diff:{classes:{added:[{label:'保护对象'}]}},mappings:{entity_types:{保护对象:'urn:test:%E4%BF%9D%E6%8A%A4%E5%AF%B9%E8%B1%A1'}},schema_summary:{classes:[{id:'urn:test:item',name:'item',label_zh:'保护对象',description:'受规则保护的对象',parents:[]}],relations:[],attributes:[]}},esc);
  for(const text of ['审核草案并查看技术详情','版本差异','本体定义','名称与技术标识（可编辑）','保护对象','查看原始数据','查看 Turtle 原始源码','查看原始映射数据','不是乱码'])assert.ok(html.includes(text),text);
  assert.ok(html.includes('&lt;原始 Turtle>'));
  assert.ok(html.includes('aria-describedby="diff-help-draft-1"'));
});

test('relations render as a directed entity-to-entity flow', () => {
  const html=renderTechnicalDetails({id:'draft-2',schema_summary:{classes:[{id:'urn:test:Content',name:'Content',label_zh:'内容'},{id:'urn:test:Object',name:'Object',label_zh:'保护对象'}],relations:[{name:'protects',label_zh:'保护',domain:['urn:test:Content'],range:['urn:test:Object'],description:'保护关系'}],attributes:[]}},String);
  assert.ok(html.includes('class="ontology-relation-card"'));
  assert.ok(html.includes('class="ontology-relation-flow"'));
  assert.ok(html.includes('内容</b><i>→</i><strong>保护</strong><i>→</i><b>保护对象'));
  assert.ok(html.includes('方向：起点实体类型 → 关系 → 终点实体类型'));
});
