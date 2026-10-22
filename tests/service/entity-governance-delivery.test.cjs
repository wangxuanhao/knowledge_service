const test = require('node:test');
const assert = require('node:assert/strict');
const governance = require('../../knowledge_service/web/entity-governance.js');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('each maxCount conflict requires an explicit existing winner without defaults', () => {
  const selection = governance.createWinnerSelection([
    {record_ids:['a','b']}, {record_ids:['c','d']},
  ]);
  assert.throws(() => selection.winners(), /逐组/);
  assert.throws(() => selection.choose(0, 'outside'), /候选/);
  selection.choose(0, 'b');
  assert.throws(() => selection.winners(), /逐组/);
  selection.choose(1, 'c');
  assert.deepEqual(selection.winners(), ['b','c']);
});

test('preparing a merge returns confirmed winners with the same preview fingerprint and entity revisions', async () => {
  const nodes=[];
  const element=tag=>{const node={tag,children:[],listeners:{},append(...items){this.children.push(...items);},
    setAttribute(){},addEventListener(name,handler){this.listeners[name]=handler;},showModal(){},close(){},remove(){}};
    nodes.push(node);return node;};
  const data={keep:{text:'keep'},drop:{text:'drop'},expected_versions:{keep:2,drop:3},
    attribute_conflicts:[],affected_records:{relations:1,attributes:2},preview_fingerprint:'f'.repeat(64)};
  const context={window:{},current:'p',endpoint:suffix=>'/p'+suffix,
    document:{createElement:element,body:{append(){}}},api:async url=>url.endsWith('/preview')?data:{version:1}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/entity-governance.js'),'utf8'),context);
  const pending=context.window.EntityGovernance.prepareMerge('keep','drop');
  for(let i=0;i<20;i++)await Promise.resolve();
  const ack=nodes.find(node=>node.type==='checkbox');ack.checked=true;ack.listeners.change();
  nodes.find(node=>node.textContent==='确认使用这些选择').listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(await pending)),{
    attribute_winners:[],preview_fingerprint:'f'.repeat(64),expected_versions:{keep:2,drop:3},
  });
});

test('resolution decisions keep real review revisions and the confirmed preview fingerprint', () => {
  const review = {decision_version: 4};
  assert.deepEqual(governance.reviewDecisionPayload(review,'merged','同一对象',{'a':2,'b':3},
    {attribute_winners:['attr-b'],preview_fingerprint:'confirmed-preview'}), {
    decision:'merged',reason:'同一对象',expected_version:4,expected_entity_versions:{a:2,b:3},attribute_winners:['attr-b'],
    preview_fingerprint:'confirmed-preview',
  });
  assert.deepEqual(governance.reviewDecisionPayload(review,'separate','两个人'), {
    decision:'separate',reason:'两个人',expected_version:4,
  });
  assert.throws(() => governance.reviewDecisionPayload(review,'rejected',''), /原因/);
  assert.throws(() => governance.reviewDecisionPayload(review,'merged','同一对象',{}, {attribute_winners:[]}), /预览/);
});
