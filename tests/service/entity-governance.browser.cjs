/* Real DOM workflow for duplicate governance and the unified conflict drawer. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const out=path.resolve(__dirname,'../../.benchmarks/entity-attributes');
let browser;
before(async()=>{
  fs.mkdirSync(out,{recursive:true});
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
});
after(async()=>browser?.close());
async function fixture(width=1440){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.setContent('<!doctype html><html lang="zh-CN"><body><select id="project"><option value="p">示例项目</option></select><h1 id="title">知识台账</h1><div id="scope"></div><main><section id="governance"></section></main><section id="tab-jobs" class="hidden"></section><section id="tab-runtime" class="hidden"></section><div id="tasks"></div></body></html>');
  await page.addStyleTag({path:path.join(web,'theme.css')});
  await page.addStyleTag({path:path.join(web,'entity-governance.css')});
  await page.addStyleTag({path:path.join(web,'entity-attribute-panel.css')});
  await page.addStyleTag({content:'*{box-sizing:border-box}body{margin:16px;font:14px "Microsoft YaHei",sans-serif;color:#193e32;background:#f4f7f3}main{max-width:1050px;margin:auto;padding:16px;background:#fff}button,input,textarea{font:inherit}button{padding:7px 10px;cursor:pointer}textarea{max-width:100%}.hidden{display:none}pre{white-space:pre-wrap;overflow-wrap:anywhere}dialog{max-width:calc(100vw - 24px)}'});
  await page.evaluate(()=>{
    window.current='p';window.$=id=>document.getElementById(id);
    window.endpoint=suffix=>'/api/projects/'+current+suffix;
    window.esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;');
    window.scope=()=>({});window.bind=()=>{};window.showTab=()=>{};window.status=()=>{};
    window.writable=true;window.Auth={isAdmin:()=>writable,user:()=>({id:'reviewer'})};window.calls=[];window.sourceOpens=[];window.notifications=[];
    window.KnowledgeChanges={notify:async value=>{
      notifications.push(value);
      if(window.holdTableRefresh)return new Promise(resolve=>{window.finishTableRefresh=resolve;});
    }};
    window.SourceReader={open:value=>sourceOpens.push(value)};
    window.sourceFailure=false;window.sourceAttempts=0;
    const source={document_title:'平台规则',document_id:'doc',document_version_id:'doc-v1',assertion_id:'s1',decision_version:2,quote:'冻结证据正文',valid_from:'2025-01-01',valid_until:null};
    const member=id=>({record_id:id,version:2,version_id:id+'-v2',value_preview:'平台职责 '+id,datatype:'string',assertion_versions:[{id:'support-'+id,version:2}]});
    window.conflictSet={id:'set',entity_id:'e',predicate:'平台职责',version:3,state:'open',owner_available:true,members:[member('a'),member('b')]};
    const group={key:'group',name:'美团平台',type:'平台',member_count:2,members:[{id:'e',text:'美团平台',version:1,version_id:'e-v1',document_count:1,support_count:1,source_contexts:[{...source,source_document_version_id:'frozen-file-v1'}]},{id:'duplicate',text:'美团平台',version:1,version_id:'duplicate-v1',document_count:1,support_count:1,source_contexts:[{...source,document_id:'doc2',document_title:'补充规则'}]}]};
    const second={...group,key:'second',name:'待重新读取的候选'};
    const oversized={...group,key:'large',name:'商品分享权限',member_count:24,members:[],blocked:true};
    const incompatible={...group,key:'mixed',name:'商品分享功能',members:group.members.map((row,index)=>({...row,
      valid_from:index?null:'2026-06-02T16:00:00Z', metadata:{applicability:{platforms:{mode:'specific',values:[index?'抖音电商':'京东']}}}}))};
    const seven={...group,key:'seven',name:'未成年人',member_count:7,members:Array.from({length:7},(_,index)=>{
      const id='person-'+(index+1);
      return {...group.members[0],id,text:'未成年人',version_id:id+'-v1',
        source_contexts:[{...source,document_title:'规则 '+(index+1),document_id:'doc-'+index}]};
    })};
    const preview={...group,preview_fingerprint:'a'.repeat(64),keep_id:'e',affected_records:{relations:4,attributes:2},attribute_conflicts:[{predicate:'平台职责',values:[{value:'职责甲'},{value:'职责乙'}]}]};
    const deferred=new Map();
    const scanned=items=>({groups:items.filter(item=>!deferred.has(item.key))
      .map(item=>({...item,membership:item.key})),next_cursor:null});
    window.api=async(url,body)=>{
      calls.push({url,body});
      if(url.endsWith('/resolution-reviews'))return {reviews:[]};
      if(url.includes('/sources?'))return {documents:[{id:'doc',version_id:'doc-v1',metadata:{title:'平台规则'}}]};
      if(window.partitionScenario&&url.endsWith('/duplicates/scan')){
        const receipt=window.partitionReceipt;
        const members=receipt?seven.members.filter(member=>receipt.targets.some(target=>target.keep_id===member.id)):seven.members;
        return scanned([{...seven,members,member_count:members.length,review_receipt:receipt}]);
      }
      if(body?.key==='seven'&&url.endsWith('/duplicates/members'))return {key:'seven',members:seven.members,next_after_id:null};
      if(url.includes('/duplicates/partition/')){
        if(body.key==='mixed'&&Object.entries(body.assignments).some(([id,keep])=>id!==keep))
          throw Error('所选实体的业务有效期、适用范围不同');
        const targets=Object.keys(body.assignments).filter(id=>body.assignments[id]===id).map(keep_id=>({
          keep_id,drop_ids:Object.keys(body.assignments).filter(id=>id!==keep_id&&body.assignments[id]===keep_id),
          affected_records:{relations:3,attributes:1},attribute_conflicts:[]}));
        if(url.endsWith('/preview'))return {preview_fingerprint:'c'.repeat(64),targets};
        if(window.holdPartition)await new Promise(resolve=>{window.releasePartition=resolve;});
        window.partitionReceipt={status:'completed',assigned_count:Object.keys(body.assignments).length,
          kept_count:targets.length,merged_count:Object.keys(body.assignments).length-targets.length,targets};
        return {...window.partitionReceipt,records:[]};
      }
      if(url.endsWith('/duplicates/scan'))return scanned(window.reviewMode?[incompatible,oversized]:body.document_mode==='related'?[group,second]:[group]);
      if(url.includes('/duplicates/deferred?'))return {groups:[...deferred.values()],next_cursor:null};
      if(url.endsWith('/duplicates/deferred/defer')){
        const item=[group,second,incompatible,oversized,seven].find(item=>item.key===body.key);
        const saved={id:item.key,name:item.name,type:item.type,member_count:item.member_count??item.members.length};
        deferred.set(saved.id,saved);return saved;
      }
      if(url.endsWith('/duplicates/deferred/restore')){
        const item=[group,second,incompatible,oversized,seven].find(item=>item.key===body.id);
        return {restored:deferred.delete(body.id),group:scanned([item]).groups[0]};
      }
      if(url.endsWith('/duplicates/preview'))return preview;
      if(url.endsWith('/duplicates/preview-batch'))return {results:window.reviewMode?
        [{key:'mixed',status:'review_required',error:'组内有成员的业务有效期、适用范围不同，需逐对复核。'}]:
        [{...preview,status:'ready'},{key:'second',status:'failed',error:'候选实体修订已变化'}]};
      if(url.endsWith('/duplicates/members'))return {key:body.key,
        members:body.key==='mixed'?incompatible.members:body.after_id?[group.members[1]]:[group.members[0]],
        next_after_id:body.key==='mixed'||body.after_id?null:group.members[0].id};
      if(url.endsWith('/merge/preview'))return {keep:group.members[0],drop:group.members[1],
        expected_versions:body.expected_versions,preview_fingerprint:'b'.repeat(64),
        affected_records:{relations:1,attributes:0},attribute_conflicts:[]};
      if(url.endsWith('/merge'))return {records:[]};
      if(url.endsWith('/duplicates/confirm')){
        if(window.holdConfirmation)return new Promise((_,reject)=>{window.rejectConfirmation=reject;});
        return {results:[{key:'group',name:'美团平台',status:'merged',records:[]}]};
      }
      if(url.endsWith('/attribute-conflicts/set/read'))return conflictSet;
      if(url.endsWith('/resolve'))return {id:'set',state:'resolved',records:[]};
      if(url.includes('/members/')&&url.endsWith('/read'))return {id:'a',version_id:'a-v2',value:'完整段落\n全部值与冻结原文分开保存。'};
      if(url.endsWith('/sources')){
        sourceAttempts++;
        if(sourceFailure){sourceFailure=false;throw Error('模拟连接中断');}
        return {sources:[{...source,assertion_id:body.cursor?'s2':'s1',document_title:body.cursor?'第二页证据':'第一页证据'}],next_cursor:body.cursor?null:'page-2'};
      }
      throw Error('unexpected '+url);
    };
  });
  await page.addScriptTag({path:path.join(web,'display-time.js')});
  await page.addScriptTag({path:path.join(web,'ingest-mode.js')});
  await page.addScriptTag({path:path.join(web,'entity-governance.js')});
  await page.addScriptTag({path:path.join(web,'workspace-drawers.js')});
  await page.addScriptTag({path:path.join(web,'task-review.js')});
  await page.evaluate(()=>EntityGovernance.mount(document.getElementById('governance'),{reload:async()=>{}}));
  return page;
}
for(const width of [1440,390])test(`duplicate scan requires preview and explicit confirmation at ${width}px`,async()=>{
  const page=await fixture(width);
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/scan'))),false);
  const scanner=page.locator('.duplicate-scanner'),confirm=scanner.locator('#duplicate-confirm');
  const scan=scanner.getByRole('button',{name:'扫描已有重复实体',exact:true});
  const scope=await scanner.getByLabel('扫描范围').boundingBox();
  const action=await scan.boundingBox();
  const section=await scanner.boundingBox();
  assert.ok(section.x+section.width-action.x-action.width<24,'scan action is aligned to the right edge');
  if(width===1440){
    assert.equal(action.y,scope.y);
    assert.ok(action.x>=scope.x+scope.width);
  }else{
    assert.ok(action.y>=scope.y+scope.height);
  }
  assert.equal(await confirm.isDisabled(),true);
  await scan.click();
  await scanner.getByRole('status').filter({hasText:'扫描完成，已找到 1 组疑似重复实体。'}).waitFor({timeout:3000});
  const queue=page.locator('.entity-resolution-queue');
  assert.equal(await queue.getByRole('heading',{name:'入库消歧复核 · 0 条待处理'}).count(),1);
  await page.locator('.entity-review-card').waitFor();
  assert.equal(await page.locator('.duplicate-merge-choice').isDisabled(),true);
  await scanner.getByRole('button',{name:'预览本组影响'}).click();
  await scanner.getByText('核对身份、来源和合并影响',{exact:true}).waitFor();
  assert.equal(await scanner.getByRole('button',{name:'复核组内成员'}).isVisible(),true);
  await scanner.getByText('合并后继续使用「美团平台」 · 影响 4 条关系、2 条属性 · 1 组冲突').waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'核对身份、来源和合并影响');
  await page.locator('.duplicate-source-contexts details summary').first().click();
  await scanner.getByRole('button',{name:'查看冻结原文'}).first().click();
  assert.deepEqual(await page.evaluate(()=>sourceOpens[0]),
    {projectId:'p',versionId:'frozen-file-v1',assertionId:'s1',focusText:'冻结证据正文',
      fallbackEvidence:{documentTitle:'平台规则',documentVersionId:'doc-v1',quote:'冻结证据正文',quoteTruncated:undefined}});
  const legacySource=page.locator('.duplicate-source-contexts details').filter({hasText:'补充规则'});
  await legacySource.locator('summary').click();
  assert.match(await legacySource.innerText(),/没有关联冻结文件版本/);
  assert.equal(await legacySource.getByRole('button',{name:'查看冻结原文'}).count(),0);
  await page.locator('.duplicate-merge-choice').check();
  assert.equal(await confirm.isDisabled(),true);
  await scanner.locator('#duplicate-merge-ack').check();
  await page.evaluate(()=>{holdTableRefresh=true;});
  await confirm.click();await scanner.getByText('已融合：美团平台',{exact:true}).waitFor();
  const receipt=scanner.locator('.duplicate-merge-status');
  assert.match(await receipt.innerText(),/合并完成.*用时/);
  assert.equal(await scanner.getByRole('button',{name:'扫描已有重复实体',exact:true}).isEnabled(),true);
  await page.evaluate(()=>finishTableRefresh(false));
  const payload=await page.evaluate(()=>calls.find(call=>call.url.endsWith('/confirm')).body);
  assert.deepEqual(payload,{groups:[{key:'group',preview_fingerprint:'a'.repeat(64)}]});
  assert.equal(await page.evaluate(()=>notifications[0].preserveGovernance),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(out,`duplicate-confirm-${width}.png`),fullPage:true});await page.close();
});
test('scoped batch previews show partial failures to read-only reviewers on narrow screens',async()=>{
  const page=await fixture(390);
  await page.evaluate(()=>{writable=false;});
  const scanner=page.locator('.duplicate-scanner');
  await scanner.locator('#duplicate-scan-scope').selectOption('related');
  const scan=scanner.getByRole('button',{name:'扫描已有重复实体',exact:true});
  assert.equal(await scan.isDisabled(),true);
  await scanner.getByRole('searchbox').fill('平台');
  await scanner.getByRole('button',{name:'查找文档'}).click();
  await scanner.getByRole('button',{name:/平台规则/}).click();
  await scan.click();
  await scanner.getByRole('button',{name:'勾选前 20 组待预览'}).click();
  await scanner.getByRole('button',{name:'批量预览（2/20）'}).click();
  await scanner.getByText('候选实体修订已变化',{exact:true}).waitFor();
  await scanner.getByText('核对身份、来源和合并影响',{exact:true}).click();
  await scanner.getByText('补充规则',{exact:true}).waitFor();
  await scanner.getByRole('button',{name:'暂不合并',exact:true}).first().click();
  const deferredHeading=scanner.getByText('暂不合并清单',{exact:true});
  await scanner.getByText('「美团平台」已移入暂不合并清单。',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'暂不合并清单');
  await scan.click();
  await scanner.getByText('扫描完成，已找到 1 组疑似重复实体。',{exact:true}).waitFor();
  await page.evaluate(()=>EntityGovernance.mount(document.getElementById('governance')));
  await deferredHeading.click();
  await scanner.getByRole('button',{name:'恢复复核'}).click();
  await scanner.getByRole('button',{name:'预览本组影响'}).waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'预览本组影响');
  assert.equal(await scanner.getByRole('button',{name:'确认合并所选组'}).count(),0);
  const calls=await page.evaluate(()=>window.calls);
  assert.deepEqual(calls.find(call=>call.url.endsWith('/scan')).body,
    {cursor:null,limit:20,document_id:'doc',document_version_id:'doc-v1',document_mode:'related'});
  assert.deepEqual(calls.find(call=>call.url.endsWith('/preview-batch')).body,{keys:['group','second']});
  assert.equal(calls.some(call=>call.url.endsWith('/confirm')),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(out,'duplicate-scoped-preview-390.png'),fullPage:true});
  await page.close();
});
test('blocked and incompatible groups can be reviewed, paged and merged as an explicit pair',async()=>{
  const page=await fixture(390);
  page.setDefaultTimeout(6000);
  await page.evaluate(()=>{window.reviewMode=true;});
  const scanner=page.locator('.duplicate-scanner');
  await scanner.getByRole('button',{name:'扫描已有重复实体',exact:true}).click();
  await scanner.getByRole('button',{name:'勾选前 20 组待预览'}).click();
  await scanner.getByRole('button',{name:'批量预览（1/20）'}).click();
  const mixed=scanner.locator('.duplicate-candidate').filter({hasText:'商品分享功能'});
  await mixed.getByText('需要人工复核',{exact:true}).waitFor();
  assert.equal(await mixed.getByRole('button',{name:'预览本组影响'}).count(),0);
  await mixed.getByRole('button',{name:'复核组内成员'}).click();
  const review=page.getByRole('dialog',{name:'复核组内成员'});
  assert.equal(await review.evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(255, 255, 255)');
  const matrix=review.getByRole('table',{name:'成员差异对比'});
  await matrix.getByRole('columnheader',{name:'平台',exact:true}).waitFor();
  assert.ok(await matrix.getByText('京东',{exact:true}).isVisible());
  assert.ok(await matrix.getByText('抖音电商',{exact:true}).isVisible());
  await matrix.getByRole('button',{name:'查看 01 的身份与来源'}).click();
  await review.getByText('来源证据',{exact:true}).first().waitFor();
  await matrix.getByRole('button',{name:'查看 01 的身份与来源'}).click();
  await review.getByRole('checkbox',{name:'保留实体 01',exact:true}).check();
  await review.getByRole('button',{name:'02 归入 01',exact:true}).click();
  await review.getByRole('button',{name:'预览分组方案'}).click();
  await review.getByRole('status').filter({hasText:'所选实体的业务有效期、适用范围不同'}).waitFor();
  assert.equal(await review.getByRole('button',{name:'确认分组方案'}).count(),0);
  await review.getByRole('checkbox',{name:'保留实体 02',exact:true}).check();
  await review.getByRole('button',{name:'预览分组方案'}).click();
  await review.getByRole('checkbox',{name:'已核对归并影响'}).check();
  await review.getByRole('button',{name:'确认分组方案'}).click();
  await review.getByText('分组复核已完成',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/merge'))),false);
  await review.getByRole('button',{name:'关闭复核'}).click();

  const large=scanner.locator('.duplicate-candidate').filter({hasText:'商品分享权限'});
  await large.getByRole('button',{name:'暂不合并',exact:true}).click();
  await scanner.getByText('「商品分享权限」已移入暂不合并清单。',{exact:true}).waitFor();
  await scanner.getByText('暂不合并清单',{exact:true}).click();
  await scanner.getByRole('button',{name:'恢复复核'}).click();
  await large.getByRole('button',{name:'复核组内成员'}).click();
  await review.getByText('平台规则',{exact:true}).click();
  await review.getByRole('button',{name:'查看冻结原文'}).click();
  assert.equal(await page.evaluate(()=>sourceOpens.at(-1).versionId),'frozen-file-v1');
  await review.getByRole('button',{name:'设为保留'}).click();
  await review.getByRole('button',{name:'下一页成员'}).click();
  await review.getByText('补充规则',{exact:true}).waitFor();
  await review.getByRole('button',{name:'设为并入'}).click();
  await review.getByRole('button',{name:'预览所选两条'}).click();
  await page.getByText('确认实体融合',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/merge'))),false);
  await page.getByRole('checkbox',{name:'已核对影响与来源；将重复实体停用，关系转到保留实体。'}).check();
  await page.getByRole('button',{name:'确认合并这两条'}).click();
  await scanner.getByText(/已合并所选两条/).waitFor();
  const merge=await page.evaluate(()=>calls.find(call=>call.url.endsWith('/merge')).body);
  assert.deepEqual(merge,{keep_id:'e',drop_id:'duplicate',expected_versions:{e:1,duplicate:1},
    attribute_winners:[],preview_fingerprint:'b'.repeat(64)});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.close();
});
for(const width of [1440,390])test(`seven candidates are allocated to two retained records at ${width}px`,async()=>{
  const page=await fixture(width);
  page.setDefaultTimeout(5000);
  await page.evaluate(()=>{window.partitionScenario=true;window.holdPartition=true;});
  const scanner=page.locator('.duplicate-scanner');
  const scan=scanner.getByRole('button',{name:'扫描已有重复实体',exact:true});
  await scan.click();
  await scanner.getByRole('button',{name:'复核组内成员'}).click();
  const review=page.getByRole('dialog',{name:'复核组内成员'});
  const preview=review.getByRole('button',{name:'预览分组方案',exact:true});
  const confirm=review.getByRole('button',{name:'确认分组方案',exact:true});
  const keep=number=>review.getByRole('checkbox',{name:'保留实体 '+number,exact:true});
  const target=(number,id)=>review.getByRole('button',{name:number+' 归入 '+id,exact:true});
  await keep('01').waitFor();
  assert.ok(await preview.isDisabled());
  await keep('01').check();await keep('02').check();
  await keep('03').check();await keep('04').check();
  await review.getByRole('combobox',{name:'05 的归并目标'}).selectOption('person-4');
  await keep('04').uncheck();await keep('03').uncheck();
  assert.equal(await target('05','01').getAttribute('aria-pressed'),'false');
  const destinations=['1','2','1','2','2'];
  for(const [index,id] of destinations.entries())await target('0'+(index+3),'0'+id).click();
  assert.equal(await target('07','02').getAttribute('aria-pressed'),'true');
  const meter=review.getByRole('progressbar',{name:'分组安排进度'});
  assert.equal(await meter.evaluate(node=>Number(node.value??node.getAttribute('aria-valuenow'))),7);
  await preview.click();
  const ack=review.getByRole('checkbox',{name:'已核对归并影响',exact:true});
  assert.ok(await confirm.isDisabled());
  await ack.check();
  await review.getByRole('button',{name:'编辑分组'}).click();
  await target('07','01').click();
  await preview.click();
  assert.ok(await confirm.isDisabled(),'changing a target requires a new sign-off');
  await ack.check();
  const payload=await page.evaluate(()=>calls.filter(call=>call.url.endsWith('/partition/preview')).at(-1).body);
  assert.deepEqual(payload.assignments,{'person-1':'person-1','person-2':'person-2','person-3':'person-1',
    'person-4':'person-2','person-5':'person-1','person-6':'person-2','person-7':'person-1'});
  assert.deepEqual(Object.values(payload.expected_versions),Array(7).fill(1));
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/partition/confirm'))),false);
  assert.ok(await review.evaluate(node=>node.scrollWidth<=node.clientWidth+1),'the dialog fits horizontally');
  await confirm.click();
  await page.waitForFunction(()=>typeof window.releasePartition==='function');
  assert.ok(await confirm.isDisabled());
  assert.ok(await review.getByRole('button',{name:'关闭复核'}).isDisabled());
  await page.evaluate(()=>releasePartition());
  await review.getByText('分组复核已完成',{exact:true}).first().waitFor();
  assert.deepEqual(await page.evaluate(()=>calls.find(call=>call.url.endsWith('/partition/confirm')).body),
    {...payload,preview_fingerprint:'c'.repeat(64)});
  await review.getByRole('button',{name:'关闭复核'}).click();
  const card=scanner.locator('.duplicate-candidate').filter({hasText:'未成年人'});
  await card.getByText(/复核已完成/).first().waitFor();
  await scan.click();
  await card.getByText(/复核已完成/).first().waitFor();
  assert.equal(await scanner.locator('.duplicate-merge-choice:enabled:visible').count(),0);
  assert.equal(await page.evaluate(()=>calls.filter(call=>call.url.endsWith('/partition/confirm')).length),1);
  await page.close();
});
test('confirmation holds the current scope and invalidates selected previews when delivery is unknown',async()=>{
  const page=await fixture();
  await page.evaluate(()=>{window.holdConfirmation=true;EntityGovernance.setDocumentScope({id:'doc',version_id:'doc-v1',title:'平台规则'});});
  const scanner=page.locator('.duplicate-scanner');
  await scanner.getByRole('button',{name:'扫描已有重复实体',exact:true}).click();
  await scanner.getByRole('button',{name:'预览本组影响'}).click();
  await scanner.locator('.duplicate-merge-choice').check();
  await scanner.locator('#duplicate-merge-ack').check();
  await scanner.getByRole('button',{name:'确认合并所选组'}).click();
  const receipt=scanner.locator('.duplicate-merge-status');
  await receipt.getByText('正在合并 1 组',{exact:false}).waitFor();
  assert.match(await receipt.innerText(),/已用时/);
  assert.equal(await scanner.getByRole('button',{name:'正在合并…'}).isDisabled(),true);
  assert.equal(await scanner.locator('#duplicate-scan-scope').isDisabled(),true);
  assert.equal(await scanner.getByRole('searchbox').isDisabled(),true);
  await page.evaluate(()=>EntityGovernance.setDocumentScope({id:'doc',version_id:'doc-v1',title:'更新的标题'}));
  assert.equal(await scanner.locator('.duplicate-document-selection').innerText(),'平台规则 · 版本 doc-v1');
  await page.evaluate(()=>window.rejectConfirmation(Error('模拟连接中断')));
  await receipt.getByText(/无法确认提交结果/).waitFor();
  assert.equal(await scanner.locator('#duplicate-scan-scope').isDisabled(),false);
  assert.equal(await scanner.locator('#duplicate-merge-ack').isChecked(),false);
  assert.equal(await scanner.locator('.duplicate-merge-choice').isDisabled(),true);
  assert.equal(await scanner.locator('.duplicate-document-selection').innerText(),'更新的标题 · 版本 doc-v1');
  await page.close();
});
test('conflict evidence retries and pages before explicit member decisions',async()=>{
  const page=await fixture(390);
  await page.evaluate(()=>{sourceFailure=true;return openAttributeConflictReview({id:'set'},{projectId:'p'});});
  const drawer=page.locator('.attribute-conflict-review');
  await drawer.getByRole('button',{name:'查看完整值及原文来源'}).first().click();
  await drawer.getByRole('button',{name:'重试来源'}).click();
  await drawer.getByText('第一页证据',{exact:true}).waitFor({timeout:3000});
  await drawer.getByRole('button',{name:'更多来源'}).click();
  await drawer.getByText('第二页证据',{exact:true}).waitFor({timeout:3000});
  assert.equal(await page.evaluate(()=>sourceAttempts),3);
  await drawer.getByRole('button',{name:'定位冻结原文'}).first().click();
  assert.equal(await page.evaluate(()=>sourceOpens[0].versionId),'doc-v1');
  await drawer.getByPlaceholder('必填：逐值核对与生效区间的审核理由').fill('核对来源后保留甲值');
  await drawer.getByRole('button',{name:'提交集合审核'}).click();
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/resolve'))),false);
  await drawer.locator('fieldset').nth(0).getByLabel('接受此值').check();
  await drawer.locator('fieldset').nth(1).getByLabel('拒绝此值').check();
  await drawer.getByRole('button',{name:'提交集合审核'}).click();
  await drawer.waitFor({state:'detached'});
  const payload=await page.evaluate(()=>calls.find(call=>call.url.endsWith('/resolve')).body);
  assert.equal(payload.expected_version,3);assert.equal(payload.expected_members.length,2);
  assert.deepEqual(payload.decisions,[{attribute_id:'a',decision:'accept'},{attribute_id:'b',decision:'reject'}]);
  await page.close();
});
