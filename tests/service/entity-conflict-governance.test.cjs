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
  assert.throws(()=>governance.duplicateConfirmationPayload([group,{...group,key:'overlap'}],new Set(['group','overlap'])),/重叠/);
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
    source_contexts:[{document_id:'doc',document_version_id:'doc-v1',source_document_version_id:'file-v1',document_title:'来源文档',valid_from:'2025-01-01T00:00:00Z',valid_until:null,quote:'原文',assertion_id:'support'}]},
    {id:'b',text:'甲'}]};
  const context={current:'p',endpoint:s=>'/p'+s,document:{createElement:element},
    window:{DisplayTime:require('../../knowledge_service/web/display-time.js'),Auth:{isAdmin:()=>true},SourceReader:{open:args=>opened.push(args)},KnowledgeChanges:{notify:async detail=>notices.push(detail)}},
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
  await source.listeners.click();
  assert.equal(opened[0].versionId,'file-v1');
  assert.equal(opened[0].fallbackEvidence.documentVersionId,'doc-v1');
  assert.ok(nodes.some(n=>String(n.textContent).includes('来源有效期 2025年01月01日 08:00:00')));
  const ack=nodes.find(n=>n.id==='duplicate-merge-ack'),choose=nodes.findLast(n=>n.type==='checkbox'&&n.className==='duplicate-merge-choice');
  choose.checked=true;choose.listeners.change();ack.checked=true;ack.listeners.change();
  await nodes.find(n=>n.textContent==='确认合并所选组').listeners.click();
  assert.equal(notices.length,1);assert.equal(notices[0].preserveGovernance,true);
  assert.equal(reloads,0);
});

test('document scope survives async mount and batch preview keeps per-group errors and conflicts visible',async()=>{
  function element(tag) {
    return {tag,children:[],listeners:{},append(...nodes){this.children.push(...nodes);},
      replaceChildren(...nodes){this.children=nodes;},setAttribute(){},
      addEventListener(name,handler){this.listeners[name]=handler;}};
  }
  const calls=[];
  const group=(key,memberIds)=>({key,membership:key,name:key,type:'urn:test#Platform',members:memberIds.map(id=>({id,text:'平台规则',version:1,
    metadata:{aliases:['平台']},source_contexts: id==='id-2' ? [] : [{document_id:'doc',document_title:'冻结规则',quote:'平台和职责'}],
    relation_context:id==='id-1' ? {total:6,examples:[{id:'r',type:'关联',subject_label:'平台乙',object_label:'审批规范'}]} : {total:0,examples:[]}}))});
  const groups=[group('甲组',['id-1','id-2']),group('乙组',['id-2','id-3']),group('失败组',['id-4','id-5'])];
  const ready=group=>({...group,status:'ready',preview_fingerprint:'f'.repeat(64),keep_id:group.members[0].id,
    affected_records:{relations:4,attributes:2},attribute_conflicts:[{predicate:'平台职责',values:[
      {value:'甲',valid_from:'2025-01-01',valid_until:'2025-04-01'},
      {value:'乙',valid_from:'2025-02-01',valid_until:group.key==='甲组'?'2025-03-01':null}]}],fact_deduplication_total:1});
  let finishReviews;
  const reviews=new Promise(resolve=>{finishReviews=resolve;});
  const deferred=new Map();
  const context={current:'p',endpoint:s=>'/p'+s,document:{createElement:element},
    labelOf:type=>type==='urn:test#Platform' ? '平台' : type,window:{DisplayTime:require('../../knowledge_service/web/display-time.js'),Auth:{isAdmin:()=>true}},
    api:async(url,body)=>{
      calls.push({url,body:JSON.parse(JSON.stringify(body??null))});
      if(url.includes('/sources?')) return {documents:[{id:'search-doc',version_id:'search-v1',metadata:{title:'关键词文档'}}]};
      if(url.endsWith('/resolution-reviews'))return reviews;
      if(url.endsWith('/scan'))return {groups:groups.filter(group=>!deferred.has(group.key)),next_cursor:null};
      if(url.endsWith('/deferred/defer')) {
        const group=groups.find(item=>item.key===body.key);
        const saved={id:group.key,name:group.name,type:group.type,member_count:group.members.length};
        deferred.set(saved.id,saved);
        return saved;
      }
      if(url.endsWith('/deferred/restore')) {
        const restored=deferred.delete(body.id);
        return {restored,group:groups.find(group=>group.key===body.id)};
      }
      if(url.includes('/deferred?'))return {groups:[...deferred.values()],next_cursor:null};
      if(url.endsWith('/preview-batch'))return {results:[ready(groups[0]),ready(groups[1]),{key:'失败组',status:'failed',error:'版本变化'}]};
      throw Error(url);
    }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../knowledge_service/web/entity-governance.js'),'utf8'),context);
  const ui=context.window.EntityGovernance,host=element('div');
  const children=(node=host)=>[node,...node.children.flatMap(children)];
  const byId=id=>children().find(node=>node.id===id);
  const byText=text=>children().find(node=>node.tag==='button'&&node.textContent===text)
    || children().find(node=>node.textContent===text);
  const byClass=name=>children().filter(node=>node.className===name);
  assert.equal(typeof ui.setDocumentScope,'function');
  ui.setDocumentScope({id:'doc',version_id:'ver',title:'打开时选中的文档'});
  const mount=ui.mount(host);
  ui.setDocumentScope({id:'doc',version_id:'ver',title:'运行中的文档'});
  finishReviews({reviews:[]});await mount;
  assert.match(byClass('duplicate-document-selection')[0].textContent,/运行中的文档/);
  await byText('扫描已有重复实体').listeners.click();
  assert.deepEqual(calls.at(-1).body,{cursor:null,limit:20,document_id:'doc',document_version_id:'ver',document_mode:'within'});
  const scope=byId('duplicate-scan-scope');scope.value='related';scope.listeners.change();
  await byText('扫描已有重复实体').listeners.click();
  assert.equal(calls.at(-1).body.document_mode,'related');
  for(const node of byClass('duplicate-preview-choice')){node.checked=true;node.listeners.change();}
  await byId('duplicate-preview-batch').listeners.click();
  assert.deepEqual(calls.at(-1),{url:'/p/duplicates/preview-batch',body:{keys:['甲组','乙组','失败组']}});
  assert.ok(children().some(node=>String(node.textContent).includes('版本变化')));
  assert.equal(calls.some(call=>call.url.endsWith('/confirm')),false);
  assert.equal(byClass('duplicate-member').length,0);
  for(const detail of byClass('duplicate-preview-detail')){detail.open=true;detail.listeners.toggle();}
  byId('duplicate-diff-only').checked=true;byId('duplicate-diff-only').listeners.change();
  assert.ok(byText('未提供来源证据'));
  assert.ok(byText('当前无关系记录'));
  assert.ok(byText('平台乙 — 关联 → 审批规范'));
  assert.ok(children().some(node=>String(node.textContent).includes('共同重叠区间 2025年02月01日')));
  assert.ok(byText('重叠区间边界待核对原文。'));
  assert.ok(children().some(node=>String(node.textContent).includes('id-2 · 修订 1')));
  assert.equal(byText('别名'),undefined);
  const [first,second]=byClass('duplicate-merge-choice');first.checked=true;first.listeners.change();
  assert.equal(second.disabled,true);
  assert.ok(children().some(node=>String(node.textContent).includes('逐组统计')));
  assert.equal(byId('duplicate-merge-ack').checked,false);
  await byText('暂不合并').listeners.click();
  const showsGroup=()=>byClass('entity-review-card duplicate-candidate').some(node=>
    children(node).some(child=>child.textContent==='甲组'));
  assert.equal(showsGroup(),false);
  await byText('扫描已有重复实体').listeners.click();
  assert.equal(showsGroup(),false);
  await ui.mount(host);
  const savedList=byClass('duplicate-deferred')[0];
  savedList.open=true;
  await savedList.listeners.toggle();
  assert.ok(byText('恢复复核'));
  await byText('恢复复核').listeners.click();
  assert.ok(showsGroup());
  assert.equal(byText('恢复复核'),undefined);
  byId('duplicate-document-query').value='原文 关键词';
  await byClass('duplicate-document-search')[0].listeners.submit({preventDefault(){}});
  assert.deepEqual(calls.at(-1),{url:'/p/sources?summary_only=true&state=all&limit=20&query=%E5%8E%9F%E6%96%87%20%E5%85%B3%E9%94%AE%E8%AF%8D',body:{}});
  byText('关键词文档').listeners.click();
  assert.match(byClass('duplicate-document-selection')[0].textContent,/关键词文档/);
  ui.setDocumentScope(null);
  await byText('扫描已有重复实体').listeners.click();
  assert.deepEqual(calls.at(-1).body,{cursor:null,limit:20});

  ui.setDocumentScope({id:'doc',version_id:'ver',title:'前一项目文档'});
  const previousApi=context.api;
  let finishScan;
  context.api=(url,payload)=>url.endsWith('/scan')
    ? new Promise(resolve=>{finishScan=resolve;}) : previousApi(url,payload);
  const oldScan=byText('扫描已有重复实体').listeners.click();
  context.current='next-project';
  await ui.mount(host);
  assert.equal(byId('duplicate-scan-scope').value,'all');
  finishScan({groups:[group('旧项目返回的候选',['stale-1','stale-2'])],next_cursor:'stale-cursor'});
  await oldScan;
  assert.equal(byText('旧项目返回的候选'),undefined);
  assert.equal(byText('继续扫描').hidden,true);
});
