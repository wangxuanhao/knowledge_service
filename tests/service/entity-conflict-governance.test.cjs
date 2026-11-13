const test = require('node:test');
const assert = require('node:assert/strict');
const governance = require('../../knowledge_service/web/entity-governance.js');
const fs=require('node:fs');const vm=require('node:vm');

test('duplicate scan groups require an exact reviewed fingerprint and explicit selection', () => {
  assert.equal(typeof governance.duplicateConfirmationPayload, 'function');
  const group={key:'group',preview_fingerprint:'a'.repeat(64),members:[{id:'a'},{id:'b'}]};
  assert.deepEqual(governance.duplicateConfirmationPayload([group],new Set(['group'])),
    {groups:[{key:'group',preview_fingerprint:'a'.repeat(64)}]});
  assert.throws(()=>governance.duplicateConfirmationPayload([{...group,preview_fingerprint:null}],new Set(['group'])),/预览/);
});

test('conflict set decision preserves exact fact and assertion versions without a default winner', () => {
  assert.equal(typeof governance.conflictDecisionPayload, 'function');
  const set={id:'set',version:1,members:[{record_id:'a',version:2,version_id:'v2',assertion_versions:[{id:'s',version:3}]}]};
  assert.throws(()=>governance.conflictDecisionPayload(set,{},'核对'),/逐值/);
  const value=governance.conflictDecisionPayload(set,{a:{decision:'accept'}},' 核对 ');
  assert.equal(value.expected_version,1);
  assert.deepEqual(value.expected_members,set.members);
  assert.deepEqual(value.decisions,[{attribute_id:'a',decision:'accept'}]);
  assert.equal(value.reason,'核对');
});

test('batch merge refreshes data while preserving visible per-group results and retry state',async()=>{
  const nodes=[],notices=[],opened=[];let reloads=0;
  const element=tag=>{const n={tag,children:[],listeners:{},append(...c){this.children.push(...c);},
    replaceChildren(...c){this.children=c;},setAttribute(){},querySelectorAll(){return [];},
    addEventListener(name,handler){this.listeners[name]=handler;}};nodes.push(n);return n;};
  const group={key:'group',name:'甲',type:'Person',members:[{id:'a',version_id:'a1',text:'甲',document_count:1,support_count:1,
    source_contexts:[{document_id:'doc',document_version_id:'doc-v1',document_title:'来源文档',valid_from:'2025-01-01T00:00:00Z',valid_until:null,quote:'原文',assertion_id:'support'}]},
    {id:'b',text:'甲'}]};
  const context={current:'p',endpoint:s=>'/p'+s,document:{createElement:element},
    window:{Auth:{isAdmin:()=>true},SourceReader:{open:args=>opened.push(args)},KnowledgeChanges:{notify:async detail=>notices.push(detail)}},
    api:async url=>url.endsWith('/resolution-reviews')?{reviews:[]}:
      url.endsWith('/scan')?{groups:[group],next_cursor:null}:
      url.endsWith('/preview')?{...group,preview_fingerprint:'a'.repeat(64),keep_id:'a',attribute_conflicts:[],affected_records:{relations:0,attributes:0}}:
      url.endsWith('/confirm')?{results:[{key:'group',name:'甲',status:'merged',records:[]}]}:Promise.reject(Error(url))};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../knowledge_service/web/entity-governance.js'),'utf8'),context);
  await context.window.EntityGovernance.mount(element('div'),{reload:async()=>reloads++});
  assert.equal(notices.length,0);
  await nodes.find(n=>n.tag==='button'&&n.textContent==='扫描已有重复实体').listeners.click();
  await nodes.find(n=>n.textContent==='预览本组影响').listeners.click();
  const source=nodes.find(n=>n.textContent==='查看冻结原文');assert.ok(source);
  source.listeners.click();assert.equal(opened[0].versionId,'doc-v1');
  assert.ok(nodes.some(n=>String(n.textContent).includes('来源有效期 2025-01-01T00:00:00Z')));
  const ack=nodes.find(n=>n.type==='checkbox'),choose=nodes.findLast(n=>n.type==='checkbox');
  choose.checked=true;choose.listeners.change();ack.checked=true;ack.listeners.change();
  await nodes.find(n=>n.textContent==='确认选中的已预览组').listeners.click();
  assert.equal(notices.length,1);assert.equal(notices[0].preserveGovernance,true);
  assert.equal(reloads,0);
});
