/* Real ECharts interaction regression; no application, database or models.
 * Run with playwright on NODE_PATH: node --test tests/service/entity-mindmap-view.browser.cjs
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
const workspace=fs.readFileSync(path.join(web,'workspace.js'),'utf8');
const bindings=workspace.match(/  get\('mindmap-zoom-in'\)\.onclick[^\n]+\n[^\n]+\n[^\n]+/);
assert.ok(bindings,'load the actual workspace view-button bindings');
let browser;
before(async()=>{
  const executablePath=[process.env.KG_BROWSER_EXECUTABLE,
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome','/usr/bin/chromium'].filter(Boolean).find(file=>fs.existsSync(file));
  browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
});
after(async()=>{await browser?.close();});

async function settle(page){
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function fixture(viewport={width:1440,height:1000}){
  const page=await browser.newPage({viewport});
  await page.route('**/*',route=>route.abort());
  await page.setContent(html,{waitUntil:'load'});
  await page.evaluate(()=>{
    document.documentElement.classList.remove('auth-pending');
    document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-mindmap'));
  });
  await page.addScriptTag({path:path.join(web,'vendor/echarts.min.js')});
  await page.addScriptTag({path:path.join(web,'entity-mindmap-model.js')});
  await page.addScriptTag({path:path.join(web,'entity-mindmap-export.js')});
  await page.addScriptTag({path:path.join(web,'entity-mindmap-view.js')});
  await page.addScriptTag({path:path.join(web,'entity-mindmap-explorer.js')});
  await page.evaluate(()=>{
    const get=id=>document.getElementById(id),host=get('mindmap-canvas');
    window.chart=echarts.init(host);
    window.mindmapView=EntityMindmapView.create({chart,host,
      summary:get('mindmap-summary'),empty:get('mindmap-empty'),zoomLabel:get('mindmap-zoom-level'),
      buttons:{in:get('mindmap-zoom-in'),out:get('mindmap-zoom-out'),reset:get('mindmap-reset-view')}});
    EntityMindmapExplorer.mount({view:mindmapView,root:get('tab-mindmap'),host});
    mindmapView.render({tree:{id:'a',text:'根实体',children:[
      {id:'b',text:'关联实体',children:[{id:'c',text:'下级实体'}]},
      {id:'d',text:'另一分支'}]}});
  });
  await page.addScriptTag({content:"const get=id=>document.getElementById(id);const mindmapView=window.mindmapView;\n"+bindings[0]});
  await settle(page);
  await page.waitForFunction(()=>chart.getZr().animation.isFinished());
  return page;
}
async function metrics(page){
  return page.evaluate(()=>{
    const series=chart.getModel().getSeriesByIndex(0),data=series.getData();
    const a=data.getItemGraphicEl(1).transformCoordToGlobal(0,0);
    const b=data.getItemGraphicEl(2).transformCoordToGlobal(0,0);
    const cards=[1,2].map(index=>{
      let symbol;
      data.getItemGraphicEl(index).traverse(element=>{if(element.getTextContent?.())symbol=element;});
      const text=symbol.getTextContent();
      const screenBox=element=>{
        const box=element.getBoundingRect().clone();box.applyTransform(element.getComputedTransform());
        return {width:box.width,height:box.height};
      };
      const fonts=text.childrenRef().filter(element=>element.type==='tspan').map(element=>{
        const transform=element.getComputedTransform();
        return Number(element.style.font.match(/([\d.]+)px/)[1])*(transform?Math.hypot(transform[0],transform[1]):1);
      });
      return {card:screenBox(symbol),text:screenBox(text),fonts};
    });
    return {zoom:series.coordinateSystem.getZoom(),label:document.getElementById('mindmap-zoom-level').textContent,
      separation:Math.hypot(b[0]-a[0],b[1]-a[1]),root:a,cards};
  });
}

test('mindmap plus and minus buttons scale the rendered tree and reset restores it',async()=>{
  const page=await fixture();
  try{
    const initial=await metrics(page);
    await page.getByRole('button',{name:'放大脑图'}).click();
    await settle(page);
    const enlarged=await metrics(page);
    assert.ok(enlarged.zoom>initial.zoom,JSON.stringify({initial,enlarged}));
    assert.ok(enlarged.separation>initial.separation*1.1,
      'rendered nodes must move apart when zooming in: '+JSON.stringify({initial,enlarged}));
    assert.equal(enlarged.label,'120%');
    await page.getByRole('button',{name:'缩小脑图'}).click();
    await settle(page);
    const reduced=await metrics(page);
    assert.ok(Math.abs(reduced.zoom-initial.zoom)<1e-6);
    assert.ok(Math.abs(reduced.separation-initial.separation)<1);
    await page.getByRole('button',{name:'放大脑图'}).click();
    await page.getByRole('button',{name:'复位视图'}).click();
    await settle(page);
    assert.equal((await metrics(page)).zoom,1);
    assert.equal((await metrics(page)).label,'100%');
  }finally{await page.close();}
});

test('card text scales with its card for buttons and wheel gestures',async()=>{
  const page=await fixture();
  try{
    const initial=await metrics(page);
    const screenshots=path.resolve(__dirname,'../../.benchmarks/mindmap-zoom');
    fs.mkdirSync(screenshots,{recursive:true});
    await page.locator('.mindmap-stage').screenshot({path:path.join(screenshots,'100-percent.png')});
    const assertScaled=current=>{
      for(let i=0;i<initial.cards.length;i++){
        const cardRatio=current.cards[i].card.width/initial.cards[i].card.width;
        const textRatio=current.cards[i].text.height/initial.cards[i].text.height;
        assert.ok(Math.abs(textRatio-cardRatio)<.02,
          'text and card must share the displayed scale: '+JSON.stringify({zoom:current.zoom,cardRatio,textRatio}));
        assert.ok(Math.abs(cardRatio-current.zoom)<.02,'the complete card must follow the displayed zoom');
        initial.cards[i].fonts.forEach((font,j)=>assert.ok(
          Math.abs(current.cards[i].fonts[j]/font-cardRatio)<.02,'rendered glyphs must scale with the card'));
      }
    };
    for(let i=0;i<4;i++)await page.getByRole('button',{name:'缩小脑图'}).click();
    await settle(page);
    assertScaled(await metrics(page));
    await page.locator('.mindmap-stage').screenshot({path:path.join(screenshots,'48-percent.png')});
    await page.getByRole('button',{name:'复位视图'}).click();
    await settle(page);
    assertScaled(await metrics(page));
    await page.locator('#mindmap-canvas').hover();
    await page.mouse.wheel(0,-120);
    await page.waitForFunction(()=>chart.getModel().getSeriesByIndex(0).coordinateSystem.getZoom()>1);
    await settle(page);
    assertScaled(await metrics(page));
  }finally{await page.close();}
});

test('toolbar zoom respects limits and continues from native wheel zoom',async()=>{
  const page=await fixture();
  try{
    const initial=await metrics(page);
    for(let i=0;i<5;i++)await page.getByRole('button',{name:'放大脑图'}).click();
    await settle(page);
    const maximum=await metrics(page);
    assert.equal(maximum.zoom,2);
    assert.equal(maximum.label,'200%');
    assert.ok(Math.abs(maximum.separation/initial.separation-2)<.01);
    for(let i=0;i<13;i++)await page.getByRole('button',{name:'缩小脑图'}).click();
    await settle(page);
    const minimum=await metrics(page);
    assert.equal(minimum.zoom,.25);
    assert.equal(minimum.label,'25%');
    assert.ok(Math.abs(minimum.separation/initial.separation-.25)<.01);
    await page.getByRole('button',{name:'复位视图'}).click();
    await page.locator('#mindmap-canvas').hover();
    await page.mouse.wheel(0,-120);
    await page.waitForFunction(()=>chart.getModel().getSeriesByIndex(0).coordinateSystem.getZoom()>1);
    const wheel=await metrics(page);
    assert.equal(wheel.label,Math.round(wheel.zoom*100)+'%');
    await page.getByRole('button',{name:'放大脑图'}).click();
    await settle(page);
    const next=await metrics(page);
    assert.ok(Math.abs(next.zoom/wheel.zoom-1.2)<1e-6);
    assert.ok(Math.abs(next.separation/wheel.separation-1.2)<.01);
  }finally{await page.close();}
});

test('dragging and toggling branches preserve the zoomed card and text sizes',async()=>{
  const page=await fixture();
  try{
    await page.getByRole('button',{name:'缩小脑图'}).click();
    await settle(page);
    await page.waitForFunction(()=>chart.getZr().animation.isFinished());
    const before=await metrics(page);
    const point=await page.evaluate(()=>{
      const data=chart.getModel().getSeriesByIndex(0).getData();
      const a=data.getItemGraphicEl(1).transformCoordToGlobal(0,0),b=data.getItemGraphicEl(2).transformCoordToGlobal(0,0);
      const rect=document.getElementById('mindmap-canvas').getBoundingClientRect();
      return {x:rect.left+(a[0]+b[0])/2,y:rect.top+(a[1]+b[1])/2};
    });
    await page.mouse.move(point.x,point.y);await page.mouse.down();
    await page.mouse.move(point.x+60,point.y+30,{steps:5});await page.mouse.up();
    await settle(page);
    const panned=await metrics(page);
    assert.ok(Math.hypot(panned.root[0]-before.root[0],panned.root[1]-before.root[1])>20,'dragging must actually pan the tree');
    assert.equal(panned.zoom,before.zoom);
    for(const count of [3,4]){
      const branch=await page.evaluate(()=>{
        const point=chart.getModel().getSeriesByIndex(0).getData().getItemGraphicEl(2).transformCoordToGlobal(0,0);
        const rect=document.getElementById('mindmap-canvas').getBoundingClientRect();
        return {x:rect.left+point[0],y:rect.top+point[1]};
      });
      await page.mouse.click(branch.x,branch.y);
      await page.getByRole('button',{name:'放大脑图'}).hover();
      await page.waitForFunction(expected=>document.getElementById('mindmap-summary').textContent.includes(`已显示 ${expected} / 4`),count);
      await page.waitForFunction(()=>chart.getZr().animation.isFinished());
      const after=await metrics(page);
      assert.equal(after.zoom,before.zoom);
      after.cards.forEach((card,i)=>{
        assert.ok(Math.abs(card.card.width-before.cards[i].card.width)<1,JSON.stringify({i,before,after}));
        assert.ok(Math.abs(card.text.height-before.cards[i].text.height)<1);
        assert.deepEqual(card.fonts,before.cards[i].fonts);
      });
    }
  }finally{await page.close();}
});

test('loaded-node search reveals and centers a folded descendant without changing facts',async()=>{
  const page=await fixture();
  try{
    assert.equal(await page.getByRole('combobox',{name:'搜索本图节点'}).count(),1,'loaded-node finder must be available');
    await page.evaluate(()=>mindmapView.render({tree:{id:'root',text:'起点',children:[
      {id:'branch',text:'分支',children:[{id:'folded',text:'折叠层',children:[{id:'target',text:'隐藏目标'}]}]},
      {id:'else',text:'无关分支'}]}}));
    const query=page.getByRole('combobox',{name:'搜索本图节点'});
    await query.fill('隐藏目标');
    await page.locator('#mindmap-search-results button').click();
    await settle(page);
    const focused=await page.evaluate(()=>{
      const data=chart.getModel().getSeriesByIndex(0).getData();let point=null;
      data.each(index=>{const raw=data.getRawDataItem(index);if(raw?.entityId==='target')point=data.getItemGraphicEl(index)?.transformCoordToGlobal(0,0);});
      return {point,width:chart.getWidth(),height:chart.getHeight(),summary:document.getElementById('mindmap-summary').textContent};
    });
    assert.ok(focused.point,'the hidden occurrence must be rendered');
    assert.ok(Math.hypot(focused.point[0]-focused.width/2,focused.point[1]-focused.height/2)<3);
    assert.match(focused.summary,/5 \/ 5/);
  }finally{await page.close();}
});

test('route controls highlight only the loaded occurrence path and can clear it',async()=>{
  const page=await fixture();
  try{
    await page.evaluate(()=>mindmapView.render({tree:{id:'a',text:'甲节点',children:[
      {id:'b',text:'乙节点',predicate:'属于',children:[{id:'c',text:'丙节点',predicate:'关联'}]},
      {id:'d',text:'丁节点',predicate:'引用'}]}}));
    await page.locator('#mindmap-trace-panel summary').click();
    await page.getByLabel('路径起点').selectOption('mindmap-root.0.0');
    await page.getByLabel('路径终点').selectOption('mindmap-root.1');
    await page.getByRole('button',{name:'追踪路径',exact:true}).click();
    const detail=await page.locator('#mindmap-trace-path').innerText();
    for(const fact of ['丙节点','乙节点','甲节点','丁节点','属于','关联','引用'])assert.ok(detail.includes(fact),detail);
    assert.match(await page.locator('#mindmap-trace-status').innerText(),/不表示关系方向/);
    const marked=await page.evaluate(()=>{
      const result=[];chart.getModel().getSeriesByIndex(0).getData().each(index=>{
        const row=chart.getModel().getSeriesByIndex(0).getData().getRawDataItem(index);
        if(row?.lineStyle?.color==='#bd742b')result.push(row.id);
      });return result;
    });
    assert.deepEqual(marked.sort(),['mindmap-root.0','mindmap-root.0.0','mindmap-root.1']);
    await page.getByRole('button',{name:'清除路径',exact:true}).click();
    assert.equal(await page.locator('#mindmap-trace-path').innerText(),'');
    assert.equal(await page.evaluate(()=>mindmapView.snapshot().tree.children.length),2);
  }finally{await page.close();}
});

test('overview navigation pans the actual camera and clear invalidates exploration controls',async()=>{
  const page=await fixture();
  try{
    await page.waitForFunction(()=>document.querySelector('#mindmap-overview-map rect'));
    await page.locator('#mindmap-overview-map').scrollIntoViewIfNeeded();
    const before=await metrics(page),box=await page.locator('#mindmap-overview-map').boundingBox();
    await page.mouse.click(box.x+box.width*.8,box.y+box.height*.75);
    await settle(page);
    const after=await metrics(page);
    assert.ok(Math.hypot(after.root[0]-before.root[0],after.root[1]-before.root[1])>10);
    assert.equal(after.zoom,before.zoom);
    await page.getByRole('button',{name:'小地图',exact:true}).click();
    assert.equal(await page.locator('#mindmap-overview').isVisible(),false);
    await page.evaluate(()=>mindmapView.clear());
    assert.equal(await page.getByRole('combobox',{name:'搜索本图节点'}).isDisabled(),true);
    assert.equal(await page.getByRole('button',{name:'导出 SVG',exact:true}).isDisabled(),true);
    assert.equal(await page.locator('#mindmap-trace-path').innerText(),'');
    assert.equal(await page.locator('#mindmap-overview').isVisible(),false);
  }finally{await page.close();}
});

test('SVG and PNG downloads contain the full loaded tree independently of the viewport',async()=>{
  const page=await fixture();
  try{
    await page.evaluate(()=>{
      mindmapView.render({truncated:true,tree:{id:'a',text:'导出根节点',children:[
        {id:'b',text:'中间节点',predicate:'连接',children:[{id:'c',text:'折叠节点',children:[{id:'d',text:'隐藏导出目标'}]}]},
        {id:'e',text:'另一分支'}]}});
      mindmapView.pan(900,600);
    });
    let [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'导出 SVG',exact:true}).click()]);
    const svg=fs.readFileSync(await download.path(),'utf8');
    assert.equal((svg.match(/data-node-id=/g)||[]).length,5);
    assert.ok(svg.includes('隐藏导出目标'));
    assert.ok(svg.includes('部分结果'));
    assert.doesNotMatch(svg,/<script|mindmap-overview|mindmap-search-results|#bd742b/);
    const output=path.resolve(__dirname,'../../.benchmarks/mindmap-explorer');fs.mkdirSync(output,{recursive:true});
    fs.writeFileSync(path.join(output,'canonical-export.svg'),svg);
    [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'导出 PNG',exact:true}).click()]);
    const png=fs.readFileSync(await download.path());
    assert.equal(png.subarray(1,4).toString(),'PNG');
    assert.ok(png.readUInt32BE(16)>300);assert.ok(png.readUInt32BE(20)>300);
    assert.ok(png.length>5000);
    fs.writeFileSync(path.join(output,'canonical-export.png'),png);
    await page.evaluate(()=>mindmapView.clear());
    assert.equal(await page.locator('#mindmap-export-status').innerText(),'');
  }finally{await page.close();}
});

test('a pending PNG export is invalidated when regeneration starts before the HTTP response',async()=>{
  const page=await fixture(),downloads=[];page.on('download',download=>downloads.push(download));
  try{
    await page.evaluate(()=>{
      const original=HTMLCanvasElement.prototype.toBlob;
      HTMLCanvasElement.prototype.toBlob=function(callback,...args){
        return original.call(this,blob=>{window.releaseMindmapPng=()=>callback(blob);},...args);
      };
    });
    await page.getByRole('button',{name:'导出 PNG',exact:true}).click();
    await page.waitForFunction(()=>typeof releaseMindmapPng==='function');
    const requests=workspace.match(/  window\.openEntityMindmap=row=>\{[^\n]+\n  act\('draw-mindmap',[^\n]+/);
    assert.ok(requests,'use the actual workspace request-start bindings');
    await page.addScriptTag({content:`{
      const mindmapView=window.mindmapView,get=id=>document.getElementById(id),select={value:'new-root'},status=()=>{};
      const mindmap={draw:()=>{window.mindmapRequestStarted=true;return new Promise(resolve=>window.finishMindmapRequest=resolve);},open:()=>Promise.resolve()};
      const act=(id,handler)=>get(id).onclick=handler;
      ${requests[0]}
    }`});
    await page.getByRole('button',{name:'生成脑图',exact:true}).click();
    assert.equal(await page.evaluate(()=>mindmapRequestStarted),true);
    await page.evaluate(()=>releaseMindmapPng());
    await settle(page);
    assert.equal(downloads.length,0);
    assert.equal(await page.locator('#mindmap-export-status').innerText(),'');
    assert.equal(await page.getByRole('button',{name:'导出 PNG',exact:true}).isDisabled(),true);
  }finally{await page.close();}
});

test('canonical SVG keeps wide Latin names types and predicates inside their cards',async()=>{
  const page=await fixture();
  try{
    const errors=await page.evaluate(async()=>{
      const result=EntityMindmapExport.buildSvg({id:'root',entityId:'a',name:'W'.repeat(31),type:'M'.repeat(42),children:[
        {id:'child',entityId:'b',name:'超长中文名称和英文 Mixed WWW '+ 'W'.repeat(20),type:'实体类型',predicate:'W'.repeat(42),children:[]}]});
      const frame=document.createElement('iframe');frame.style.cssText='position:absolute;left:-10000px;width:1200px;height:1000px';
      const loaded=new Promise(resolve=>frame.onload=resolve);frame.srcdoc=result.svg;document.body.append(frame);await loaded;
      await frame.contentDocument.fonts.ready;
      const violations=[];
      frame.contentDocument.querySelectorAll('[data-node-id]').forEach(group=>{
        const card=group.querySelector('rect').getBBox();
        group.querySelectorAll('tspan').forEach(span=>{
          const box=span.getBBox();
          if(box.x<card.x+15||box.x+box.width>card.x+card.width-15||box.y<card.y||box.y+box.height>card.y+card.height)
            violations.push({text:span.textContent,card:{x:card.x,y:card.y,width:card.width,height:card.height},box:{x:box.x,y:box.y,width:box.width,height:box.height}});
        });
      });frame.remove();return violations;
    });
    assert.deepEqual(errors,[]);
  }finally{await page.close();}
});

test('remounting explorer controls clears previous path decorations',async()=>{
  const page=await fixture();
  try{
    await page.locator('#mindmap-trace-panel summary').click();
    await page.getByLabel('路径起点').selectOption('mindmap-root.0.0');
    await page.getByLabel('路径终点').selectOption('mindmap-root.1');
    await page.getByRole('button',{name:'追踪路径',exact:true}).click();
    const remaining=await page.evaluate(()=>{
      EntityMindmapExplorer.mount({view:mindmapView,root:document.getElementById('tab-mindmap'),host:document.getElementById('mindmap-canvas')});
      const data=chart.getModel().getSeriesByIndex(0).getData(),marked=[];
      data.each(index=>{const row=data.getRawDataItem(index);if(row?.lineStyle?.color==='#bd742b')marked.push(row.id);});
      return marked;
    });
    assert.deepEqual(remaining,[]);
    assert.equal(await page.locator('#mindmap-trace-clear').isDisabled(),true);
  }finally{await page.close();}
});

for(const viewport of [{width:1440,height:900},{width:1920,height:1080},{width:390,height:844}]){
  test(`explorer controls remain reachable at ${viewport.width}x${viewport.height}`,async()=>{
    const page=await fixture(viewport);
    try{
      await page.locator('#mindmap-trace-panel summary').click();
      const query=page.getByRole('combobox',{name:'搜索本图节点'});await query.fill('实体');
      await settle(page);
      const dimensions=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth}));
      assert.ok(dimensions.document<=dimensions.width,JSON.stringify(dimensions));
      const button=page.getByRole('button',{name:'导出 SVG',exact:true});await button.scrollIntoViewIfNeeded();
      const box=await button.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=viewport.width+1);
      await query.fill('');
      const output=path.resolve(__dirname,'../../.benchmarks/mindmap-explorer');fs.mkdirSync(output,{recursive:true});
      await page.locator('#tab-mindmap').screenshot({path:path.join(output,`${viewport.width}x${viewport.height}.png`)});
    }finally{await page.close();}
  });
}
