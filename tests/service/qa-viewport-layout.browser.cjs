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

async function fixture(viewport={width:390,height:844}){
  const page=await browser.newPage({viewport});
  await page.route('**/*',route=>route.abort());
  await page.setContent(html,{waitUntil:'load'});
  await page.addScriptTag({content:fs.readFileSync(path.join(web,'menu-hierarchy.js'),'utf8')});
  await page.evaluate(()=>{
    document.documentElement.classList.remove('auth-pending');
    document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-qa'));
    document.getElementById('tab-qa').prepend(document.getElementById('scope'));
    document.getElementById('cb-details').open=false;
    document.getElementById('qa-hero').hidden=true;
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
    const main=document.querySelector('body>main');
    return {top:rect.top,bottom:rect.bottom,innerHeight:window.innerHeight,
      pageTop:page.getBoundingClientRect().top,fit:page.style.getPropertyValue('--qa-fit'),
      minHeight:getComputedStyle(page).minHeight,
      outerScrollRange:main.scrollHeight-main.clientHeight,outerScrollTop:main.scrollTop,
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
      document.getElementById('project').closest('label').append(status);
    });
    await settle(page);
    const after=await geometry(page);
    assert.ok(after.pageTop>before.pageTop,'the asynchronous shell content must move QA down');
    assert.notEqual(after.fit,before.fit,'QA must recalculate the remaining viewport space');
    assertReachable(after);
  }finally{await page.close();}
});

for(const viewport of [{width:1440,height:900},{width:1440,height:600},{width:768,height:850}]){
  test(`QA at ${viewport.width}x${viewport.height} scrolls only the conversation above the composer`,async()=>{
    const page=await fixture(viewport);
    try{
      const before=await geometry(page);
      const screenshots=path.resolve(__dirname,'../../.benchmarks/qa-scroll');
      fs.mkdirSync(screenshots,{recursive:true});
      await page.screenshot({path:path.join(screenshots,`${viewport.width}x${viewport.height}-${before.outerScrollRange>1?'before':'after'}.png`)});
      assertReachable(before);
      assert.ok(before.outerScrollRange<=1,'the workspace must not scroll below the input: '+JSON.stringify(before));
      await page.locator('#qa-settings>summary').click();
      await page.locator('.qa-temporal-options summary').click();
      await settle(page);
      assertReachable(await geometry(page));
      await page.locator('#qa-settings>summary').click();
      await page.evaluate(()=>{
        const status=document.getElementById('status');
        status.textContent='检索范围已更新。\n请核对本次问题所使用的资料与平台范围。';
      });
      await settle(page);
      const expanded=await geometry(page);
      assertReachable(expanded);
      assert.ok(expanded.outerScrollRange<=1,'scope/status changes must not create another workspace scrollbar');
      await page.locator('#qa-scroll').hover();
      await page.mouse.wheel(0,2000);
      await settle(page);
      const after=await geometry(page);
      assert.equal(after.outerScrollTop,0,'scrolling a conversation must not move the outer workspace');
      assert.equal(after.top,expanded.top,'the composer must remain in the same viewport position');
      assertReachable(after);
    }finally{await page.close();}
  });
}
