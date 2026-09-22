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

// ---------------------------------------------------------------------------
// 审核模型：派生状态必须与后端 _materialize_candidates 的依赖方向一致
// 本体类型 → 实体 → 关系/属性，且"手动排除"与"连带跳过"严格分离
// ---------------------------------------------------------------------------
const { createReviewModel, PAGE_SIZE } = require('../../knowledge_service/web/ontology-details.js');

function snapModel(overrides = {}) {
  const draft = {
    id: 'd1', status: 'draft',
    mappings: {
      entity_types: { 主播: 'urn:ns:主播', 'MCN机构': 'urn:ns:MCN' },
      relation_types: { 签约: 'urn:ns:签约' },
      attributes: { 粉丝量: 'urn:ns:粉丝量' },
    },
    candidate_snapshot: [
      { id: 'e1', kind: 'entity', text: '张三', proposed_type: '主播', document_version_id: 'dv1' },
      { id: 'e2', kind: 'entity', text: '快驴', proposed_type: 'MCN机构', document_version_id: 'dv1' },
      { id: 'r1', kind: 'relation', proposed_type: '签约', subject_id: 'e1', object_id: 'e2', document_version_id: 'dv1' },
      { id: 'a1', kind: 'attribute', proposed_type: '粉丝量', entity_id: 'e1', value: '12万', document_version_id: 'dv1' },
    ],
    ...overrides,
  };
  return createReviewModel(draft);
}

test('审核顺序：关类型连带实体与关系，重开后派生项自动复活', () => {
  const M = snapModel();
  assert.equal(M.stats().e.on, 2);
  assert.equal(M.stats().r.on, 1);
  // 关掉 MCN机构 → e2 及以它为端点的 r1 连带跳过
  M.termOn.set('MCN机构', false);
  assert.equal(M.entityState(M.entities[1]).state, 'derived');
  assert.equal(M.relationState(M.relations[0]).state, 'derived');
  assert.equal(M.stats().e.skip, 1);
  assert.equal(M.stats().r.skip, 1);
  // 重开 → 自动复活，手动排除集合未被污染
  M.termOn.set('MCN机构', true);
  assert.equal(M.stats().e.on, 2);
  assert.equal(M.stats().r.on, 1);
  assert.equal(M.manual.size, 0);
});

test('手动取消实体：属性和关系连带跳过，但只有手动态写入排除', () => {
  const M = snapModel();
  M.manual.add('e1');
  assert.equal(M.entityState(M.entities[0]).state, 'manual');
  assert.equal(M.attributeState(M.attributes[0]).state, 'derived');
  assert.equal(M.relationState(M.relations[0]).state, 'derived');
  const st = M.stats();
  assert.equal(st.e.skip, 1);
  assert.equal(st.a.skip, 1);
  assert.equal(st.r.skip, 1);
  // 存量的 excluded_candidate_ids 反序列化后仍是手动排除
  const M2 = snapModel({ excluded_candidate_ids: ['e2'] });
  assert.equal(M2.entityState(M2.entities[1]).state, 'manual');
});

test('跨片段同名唯一才兜底，歧义不猜', () => {
  // 关系端点 id 指向不存在的实体，但同文档仅有一个同名实体 → 兜底命中
  const uniq = snapModel();
  uniq.relations[0].subject_id = 'missing';
  uniq.relations[0].subject = '张三';
  const M1 = createReviewModel({
    id: 'd2', status: 'draft',
    mappings: uniq.mappings ? { entity_types: { 主播: 'u:1', 'MCN机构': 'u:2' }, relation_types: { 签约: 'u:3' }, attributes: {} } : {},
    candidate_snapshot: [
      { id: 'e1', kind: 'entity', text: '张三', proposed_type: '主播', document_version_id: 'dv1' },
      { id: 'e2', kind: 'entity', text: '快驴', proposed_type: 'MCN机构', document_version_id: 'dv1' },
      { id: 'r1', kind: 'relation', proposed_type: '签约', subject_id: 'missing', object_id: 'e2', subject: '张三', object: '快驴', document_version_id: 'dv1' },
    ],
  });
  assert.equal(M1.relationState(M1.relations[0]).state, 'on');
  // 同名出现两次 → 歧义，判为端点缺失（不猜）
  const M2 = createReviewModel({
    id: 'd3', status: 'draft',
    mappings: { entity_types: { 主播: 'u:1', 'MCN机构': 'u:2' }, relation_types: { 签约: 'u:3' }, attributes: {} },
    candidate_snapshot: [
      { id: 'e1', kind: 'entity', text: '张三', proposed_type: '主播', document_version_id: 'dv1' },
      { id: 'e9', kind: 'entity', text: '张三', proposed_type: '主播', document_version_id: 'dv1' },
      { id: 'e2', kind: 'entity', text: '快驴', proposed_type: 'MCN机构', document_version_id: 'dv1' },
      { id: 'r1', kind: 'relation', proposed_type: '签约', subject_id: 'missing', object_id: 'e2', subject: '张三', object: '快驴', document_version_id: 'dv1' },
    ],
  });
  assert.equal(M2.relationState(M2.relations[0]).state, 'derived');
});

test('分页粒度固定为 100（用户拍板）', () => {
  assert.equal(PAGE_SIZE, 100);
});
