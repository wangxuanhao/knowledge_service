/* Static browser regression: actual HTML/CSS and QA fit hook, no app, API or models.
 * Run with Node's test runner and playwright on NODE_PATH; uses a separate browser.
 */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../knowledge_service/web');
let html=fs.readFileSync(path.join(web,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
html=html.replace(/<link\b[^>]*>/gi,tag=>{
  const href=tag.match(/href="\/assets\/([^"?]+\.css)/);
  return href?'<style>'+fs.readFileSync(path.join(web,href[1]),'utf8')+'</style>':'';
});
const source=fs.readFileSync(path.join(web,'workbench.js'),'utf8');
const fitStart=source.indexOf('  // Keep the composer inside the viewport');
const fitEnd=source.indexOf('  fit();',fitStart)+'  fit();'.length;
assert.ok(fitStart>=0&&fitEnd>fitStart,'the actual QA fit hook must be loaded');
const fitSource=source.slice(fitStart,fitEnd);
let browser;
before(async()=>{
  const candidates=[process.env.KG_BROWSER_EXECUTABLE,
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome','/usr/bin/chromium'].filter(Boolean);
  const executablePath=candidates.find(file=>fs.existsSync(file));
  browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
});
after(async()=>{await browser?.close();});

async function fixture(){
  const page=await browser.newPage({viewport:{width:390,height:844}});
  await page.route('**/*',route=>route.abort());
  await page.setContent(html,{waitUntil:'load'});
  await page.addScriptTag({content:fs.readFileSync(path.join(web,'menu-hierarchy.js'),'utf8')});
  await page.evaluate(()=>{
    document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-qa'));
    document.getElementById('tab-qa').prepend(document.getElementById('scope'));
    document.getElementById('cb-details').open=false;
    document.getElementById('qa-hero').hidden=true;
    const select=document.getElementById('qa-mode');select.hidden=true;
    select.insertAdjacentHTML('afterend','<div class="ks-select"><button class="ks-select-trigger" type="button">关键词</button></div>');
    document.getElementById('qa-progress').textContent='回答完成';
    document.getElementById('qa-transcript').innerHTML=Array.from({length:2},()=>
      '<li class="qa-turn qa-turn-user"><p class="qa-bubble">退款</p></li>'+
      '<li class="qa-turn qa-turn-assistant"><div class="qa-answer">退款需要原始凭证。</div>'+
      '<button class="qa-evidence-toggle">查看 5 条回答依据</button><div class="qa-evidence-panel">'+
      '<p>固定来源和证据说明</p>'.repeat(36)+'</div></li>').join('');
    window.$c=id=>document.getElementById(id);
  });
  await page.addScriptTag({content:fitSource});
  await settle(page);
  return page;
}
async function settle(page){
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function geometry(page){
  return page.evaluate(()=>{
    const page=document.getElementById('qa-page'),composer=document.getElementById('qa-composer');
    const scroll=document.getElementById('qa-scroll'),rect=composer.getBoundingClientRect();
    return {top:rect.top,bottom:rect.bottom,innerHeight:window.innerHeight,
      pageTop:page.getBoundingClientRect().top,fit:page.style.getPropertyValue('--qa-fit'),
      minHeight:getComputedStyle(page).minHeight,
      scrollsInternally:scroll.scrollHeight>scroll.clientHeight,
      overflow:document.documentElement.scrollWidth-window.innerWidth};
  });
}
function assertReachable(box){
  assert.equal(box.overflow,0,'the narrow page must not overflow horizontally');
  assert.ok(box.scrollsInternally,'evidence must scroll inside the transcript');
  assert.ok(box.top>=0&&box.bottom<=box.innerHeight+1,JSON.stringify(box));
}

test('expanded evidence keeps the mobile composer in the viewport',async()=>{
  const page=await fixture();
  try{assertReachable(await geometry(page));}finally{await page.close();}
});

test('late sidebar content recomputes QA space without a viewport resize',async()=>{
  const page=await fixture();
  try{
    const before=await geometry(page);
    await page.evaluate(()=>{
      const status=document.createElement('p');status.textContent='管理员会话已载入';
      document.getElementById('session').append(status);
    });
    await settle(page);
    const after=await geometry(page);
    assert.ok(after.pageTop>before.pageTop,'the asynchronous shell content must move QA down');
    assert.notEqual(after.fit,before.fit,'QA must recalculate the remaining viewport space');
    assertReachable(after);
  }finally{await page.close();}
});
