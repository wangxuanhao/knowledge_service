const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function fixture(selected='a',opts={}) {
  const element=tag=>({tag,children:[],listeners:{},attributes:{},style:{},dataset:{},value:'',append(...n){this.children.push(...n);},replaceChildren(...n){this.children=n;},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},addEventListener(k,f){this.listeners[k]=f;},classList:{add(){},toggle(){}}});
  const detail=element('host'),writes=[],requests=[],messages=[],events={};
  const model={S:{selected,summary:{classes:[{id:'a',label:'源'},{id:'b',label:'目标'},{id:'old',active:false}],relations:[{id:'r'},{id:'s'},{id:'rold',active:false}],attributes:[{id:'x'},{id:'y'}]},draft:{id:'d',revision:1,status:'pending'}},helpers:{label:t=>t.label||t.id,shortIri:x=>x},on(k,f){events[k]=f;return ()=>{};},status(m){messages.push(m);},async ensureDraft(){if(opts.ensure)await opts.ensure(model,events);return model.S.draft;},async sendCommand(c){writes.push(JSON.parse(JSON.stringify(c)));}};
  const context={window:{OntologyModel:model,OntologyReclassify:{open(){messages.push('migration');}}},document:{createElement:element,getElementById(){return null;}},console,current:'p',endpoint:x=>x,setTimeout,clearTimeout,api:async(url,body,method)=>{if(url.includes('type-merge-preview')){requests.push({url,body:JSON.parse(JSON.stringify(body)),method});return opts.preview?opts.preview():{source_iri:'a',target_iri:'b',expected_revision:1,preview_fingerprint:'fp',can_merge:true,operations:[{action:'retire_term',target_iri:'a'}],impact:{record_count:3,linked_relation_count:2},warnings:['请核对候选映射']};}return {record_count:3};}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),context);
  context.window.OntologyPanel.mount(detail,null,model);
  const all=n=>[n,...n.children.flatMap(all)],find=c=>all(detail).find(n=>(n.className||'').split(' ').includes(c));
  return {detail,model,writes,requests,messages,context,events,find,all,render:()=>context.window.OntologyPanel.render()};
}
const content=n=>[n.textContent||'',...n.children.map(content)].join(' ');
const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
function fill(f){const target=f.find('om-merge-target');assert.ok(target,'class details expose a merge target');target.value='b';target.listeners.change();const reason=f.find('om-merge-reason');reason.value='重复类型';reason.listeners.input();}
test('merge requires server preview before submitting guarded class command',async()=>{
 const f=fixture();fill(f);assert.deepEqual(f.find('om-merge-target').children.map(n=>n.value),['','b']);assert.ok(f.find('om-merge-confirm').disabled);
 await f.find('om-merge-preview').listeners.click();assert.equal(f.writes.length,0);assert.deepEqual(f.requests,[{url:'/ontology-drafts/d/type-merge-preview',body:{expected_revision:1,source_iri:'a',target_iri:'b'},method:'POST'}]);
 assert.match(content(f.detail),/3.*正式知识/);assert.match(content(f.detail),/停用.*源/);assert.equal(f.find('om-merge-confirm').disabled,false);
 await f.find('om-merge-confirm').listeners.click();assert.deepEqual(f.writes,[{action:'merge_class',target_iri:'a',replacement_iri:'b',preview_fingerprint:'fp',reason:'重复类型'}]);assert.match(f.messages.join(' '),/审核.*发布/);
});
test('target, reason and revision edits invalidate merge confirmation',async()=>{
 for(const change of ['target','reason','revision']){const f=fixture();fill(f);await f.find('om-merge-preview').listeners.click();if(change==='revision'){f.model.S.draft.revision++;f.render();}else{const node=f.find(change==='target'?'om-merge-target':'om-merge-reason');node.value=change==='target'?'':'新原因';node.listeners[change==='target'?'change':'input']();}await f.find('om-merge-confirm').listeners.click();assert.equal(f.writes.length,0,change);assert.ok(f.find('om-merge-confirm').disabled);}
});
test('late previews cannot confirm after project or selection changes',async()=>{
 for(const change of ['project','selection','revision']){let resolve;const f=fixture('a',{preview:()=>new Promise(r=>{resolve=r;})});fill(f);const pending=f.find('om-merge-preview').listeners.click();await settle();if(change==='project')f.context.current='q';else if(change==='selection')f.model.S.selected='b';else f.model.S.draft.revision++;resolve({source_iri:'a',target_iri:'b',expected_revision:1,preview_fingerprint:'fp',can_merge:true,operations:[],impact:{}});await pending;await f.find('om-merge-confirm').listeners.click();assert.equal(f.writes.length,0,change);}
});
test('preparing a draft may rerender detail without losing the merge form or preview',async()=>{
 const f=fixture('a',{ensure:async(m,events)=>{m.S.draft={id:'d',revision:1,status:'pending'};events['draft:changed']();}});fill(f);await f.find('om-merge-preview').listeners.click();assert.equal(f.find('om-merge-target').value,'b');assert.equal(f.find('om-merge-confirm').disabled,false);
});
test('merge preview rejection and server failure never enable confirmation',async()=>{
 for(const preview of [async()=>({can_merge:false,warnings:['结构冲突']}),async()=>{throw new Error('预览不可用');}]){const f=fixture('a',{preview});fill(f);await f.find('om-merge-preview').listeners.click();assert.ok(f.find('om-merge-confirm').disabled);assert.equal(f.writes.length,0);}
});
test('retirement offers same-kind active replacement and keeps explicit no-migration notice',async()=>{
 for(const [selected,target] of [['a','b'],['r','s'],['x','y']]){const f=fixture(selected);await settle();const replacement=f.find('om-retire-replacement');assert.ok(replacement);assert.deepEqual(replacement.children.map(n=>n.value),['',target]);assert.match(content(f.detail),/历史知识.*保留.*自动迁移/);replacement.value=target;replacement.listeners.change();const reason=f.find('om-retire-reason');reason.value='定义重复';reason.listeners.input();const button=f.all(f.find('om-retire')).find(n=>n.textContent==='确认加入停用变更');await button.listeners.click();assert.deepEqual(f.writes,[{action:'retire_term',target_iri:selected,reason:'定义重复',after:{replacement:{iri:target}}}]);if(selected!=='a')assert.equal(f.find('om-merge-target'),undefined);}
});
test('leaving a type or entering history invalidates a prior preview even when returning',async()=>{
 for(const change of ['selection','history']){const f=fixture();fill(f);await f.find('om-merge-preview').listeners.click();if(change==='selection')f.model.S.selected='r';else f.model.S.preview={id:'old'};f.render();f.model.S.selected='a';f.model.S.preview=null;f.render();assert.ok(f.find('om-merge-confirm').disabled);}
});
test('duplicate preview and confirm clicks do not submit twice',async()=>{
 let resolve;const f=fixture('a',{preview:()=>new Promise(r=>{resolve=r;})});fill(f);const button=f.find('om-merge-preview');const pending=button.listeners.click();await settle();await button.listeners.click();assert.equal(f.requests.length,1);resolve({source_iri:'a',target_iri:'b',expected_revision:1,preview_fingerprint:'fp',can_merge:true,operations:[],impact:{}});await pending;const confirm=f.find('om-merge-confirm');await Promise.all([confirm.listeners.click(),confirm.listeners.click()]);assert.equal(f.writes.length,1);
});
test('an empty project never prepares a draft or requests a merge preview',async()=>{
 const f=fixture();f.context.current='';f.render();fill(f);await f.find('om-merge-preview').listeners.click();assert.equal(f.requests.length,0);assert.ok(f.find('om-merge-preview').disabled);
});
