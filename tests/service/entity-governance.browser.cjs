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
  await page.addStyleTag({path:path.join(web,'entity-governance.css')});
  await page.addStyleTag({path:path.join(web,'entity-attribute-panel.css')});
  await page.addStyleTag({content:'*{box-sizing:border-box}body{margin:16px;font:14px "Microsoft YaHei",sans-serif;color:#193e32;background:#f4f7f3}main{max-width:1050px;margin:auto;padding:16px;background:#fff}button,input,textarea{font:inherit}button{padding:7px 10px;cursor:pointer}textarea{max-width:100%}.hidden{display:none}pre{white-space:pre-wrap;overflow-wrap:anywhere}dialog{max-width:calc(100vw - 24px)}'});
  await page.evaluate(()=>{
    window.current='p';window.$=id=>document.getElementById(id);
    window.endpoint=suffix=>'/api/projects/'+current+suffix;
    window.esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;');
    window.scope=()=>({});window.bind=()=>{};window.showTab=()=>{};window.status=()=>{};
    window.Auth={isAdmin:()=>true,user:()=>({id:'reviewer'})};window.calls=[];window.sourceOpens=[];window.notifications=[];
    window.KnowledgeChanges={notify:async value=>notifications.push(value)};
    window.SourceReader={open:value=>sourceOpens.push(value)};
    window.sourceFailure=false;window.sourceAttempts=0;
    const source={document_title:'平台规则',document_id:'doc',document_version_id:'doc-v1',assertion_id:'s1',decision_version:2,quote:'冻结证据正文',valid_from:'2025-01-01',valid_until:null};
    const member=id=>({record_id:id,version:2,version_id:id+'-v2',value_preview:'平台职责 '+id,datatype:'string',assertion_versions:[{id:'support-'+id,version:2}]});
    window.conflictSet={id:'set',entity_id:'e',predicate:'平台职责',version:3,state:'open',owner_available:true,members:[member('a'),member('b')]};
    const group={key:'group',name:'美团平台',type:'平台',member_count:2,members:[{id:'e',text:'美团平台',version:1,version_id:'e-v1',document_count:1,support_count:1,source_contexts:[source]},{id:'duplicate',text:'美团平台',version:1,version_id:'duplicate-v1',document_count:1,support_count:1,source_contexts:[{...source,document_id:'doc2',document_title:'补充规则'}]}]};
    window.api=async(url,body)=>{
      calls.push({url,body});
      if(url.endsWith('/resolution-reviews'))return {reviews:[]};
      if(url.endsWith('/duplicates/scan'))return {groups:[group],next_cursor:null};
      if(url.endsWith('/duplicates/preview'))return {...group,preview_fingerprint:'a'.repeat(64),keep_id:'e',affected_records:{relations:4,attributes:2},attribute_conflicts:[{predicate:'平台职责',values:[{value:'职责甲'},{value:'职责乙'}]}]};
      if(url.endsWith('/duplicates/confirm'))return {results:[{key:'group',name:'美团平台',status:'merged',records:[]}]};
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
  await page.addScriptTag({path:path.join(web,'entity-governance.js')});
  await page.addScriptTag({path:path.join(web,'task-review.js')});
  await page.evaluate(()=>EntityGovernance.mount(document.getElementById('governance'),{reload:async()=>{}}));
  return page;
}
for(const width of [1440,390])test(`duplicate scan requires preview and explicit confirmation at ${width}px`,async()=>{
  const page=await fixture(width);
  assert.equal(await page.evaluate(()=>calls.some(call=>call.url.endsWith('/scan'))),false);
  const scanner=page.locator('.duplicate-scanner'),confirm=scanner.getByRole('button',{name:'确认选中的已预览组'});
  assert.equal(await confirm.isDisabled(),true);
  await scanner.getByRole('button',{name:'扫描已有重复实体',exact:true}).click();
  await page.locator('.entity-review-card').waitFor();
  assert.equal(await page.locator('.entity-review-card input[type=checkbox]').isDisabled(),true);
  await scanner.getByRole('button',{name:'预览本组影响'}).click();
  await scanner.getByText('保留 e · 影响 4 条关系、2 条属性 · 1 组冲突').waitFor();
  await page.locator('.duplicate-source-contexts details summary').first().click();
  await scanner.getByRole('button',{name:'查看冻结原文'}).first().click();
  assert.equal(await page.evaluate(()=>sourceOpens[0].versionId),'doc-v1');
  await page.locator('.entity-review-card input[type=checkbox]').check();
  assert.equal(await confirm.isDisabled(),true);
  await scanner.locator(':scope > label input[type=checkbox]').check();
  await confirm.click();await scanner.getByText('已融合：美团平台',{exact:true}).waitFor();
  const payload=await page.evaluate(()=>calls.find(call=>call.url.endsWith('/confirm')).body);
  assert.deepEqual(payload,{groups:[{key:'group',preview_fingerprint:'a'.repeat(64)}]});
  assert.equal(await page.evaluate(()=>notifications[0].preserveGovernance),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(out,`duplicate-confirm-${width}.png`),fullPage:true});await page.close();
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
