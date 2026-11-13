const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const out=path.resolve(__dirname,'../../.benchmarks/entity-attributes');
let browser;
before(async()=>{fs.mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});});
after(async()=>browser?.close());
for(const width of [1440,390])test(`class identity settings retain failed edits and stay within ${width}px`,async()=>{
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.setContent('<!doctype html><html lang="zh-CN"><body><select id="project"><option value="p">示例项目</option></select><main class="ontology-model"><h2>本体建模层 · 平台</h2><div id="identity"></div></main></body></html>');
  await page.addStyleTag({path:path.join(web,'ontology-model-panel.css')});
  await page.addStyleTag({content:'*{box-sizing:border-box}body{font:14px "Microsoft YaHei",sans-serif;color:#193e32;background:#f4f7f3;margin:16px}main{max-width:540px;background:#fff;padding:18px;margin:16px auto;border:1px solid #dce8e2}label{display:flex;flex-direction:column;gap:5px;margin:12px 0}select,input,textarea{max-width:100%;font:inherit}button{max-width:100%;white-space:normal}p{line-height:1.7;overflow-wrap:anywhere}'});
  await page.addScriptTag({path:path.join(web,'ontology-model-identity.js')});
  await page.evaluate(()=>{
    const cls={id:'https://example.org/Platform',active:true,identity_policy:{declared:true,mode:'scoped',scope:'',keys:[]},
      identity_annotations:[{predicate:'https://knowledge.local/schema/identityPolicy',value:'scoped'}]};
    window.calls=[];window.fail=true;window.Auth={isAdmin:()=>true};
    window.model={S:{ontologyId:'onto',draft:{id:'draft',status:'pending',revision:1,base_ontology_id:'onto'},showingDraft:true},
      status:()=>{},sendCommand:async command=>{calls.push(command);if(fail){fail=false;throw Error('模拟保存失败，输入保留');}}};
    window.render=()=>document.getElementById('identity').replaceChildren(OntologyIdentity.classSection(cls,{model,project:()=>document.getElementById('project').value,
      title:text=>{const h=document.createElement('h3');h.textContent=text;return h;}}));
    render();
  });
  await page.locator('.om-identity-policy summary').click();
  await page.getByLabel('实体身份策略',{exact:true}).selectOption('reference');
  await page.getByLabel('身份范围',{exact:true}).fill('平台治理');
  await page.getByLabel('身份键',{exact:true}).fill('platformCode\n统一社会信用代码');
  await page.getByRole('button',{name:'保存身份策略到草案'}).click();
  await page.getByText('模拟保存失败，输入保留',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('实体身份策略',{exact:true}).inputValue(),'reference');
  assert.equal(await page.getByLabel('身份键',{exact:true}).inputValue(),'platformCode\n统一社会信用代码');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(out,`identity-settings-${width}.png`),fullPage:true});
  await page.getByRole('button',{name:'保存身份策略到草案'}).click();
  const payload=await page.evaluate(()=>calls.at(-1));assert.equal(payload.action,'batch');
  assert.equal(payload.commands[0].action,'remove_annotation');
  assert.ok(payload.commands.some(command=>command.predicate.endsWith('/identityPolicy')&&command.value==='reference'));
  await page.evaluate(()=>{model.S.preview={id:'old'};render();});
  assert.equal(await page.getByRole('button',{name:'保存身份策略到草案'}).count(),0);
  assert.match(await page.locator('#identity').innerText(),/历史版本 · 只读/);
  await page.close();
});
