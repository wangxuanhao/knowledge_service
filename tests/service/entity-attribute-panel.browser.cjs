/* Shared property reader against a real browser; no database or model calls. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const out=path.resolve(__dirname,'../../.benchmarks/entity-attributes');
let browser;
before(async()=>{
  fs.mkdirSync(out,{recursive:true});
  const executablePath=[process.env.KG_BROWSER_EXECUTABLE,'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','/usr/bin/chromium'].filter(Boolean).find(file=>fs.existsSync(file));
  browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
});
after(async()=>browser?.close());
async function fixture(viewport={width:1440,height:1000}){
  const page=await browser.newPage({viewport});
  await page.setContent('<!doctype html><html lang="zh-CN"><body><select id="project" aria-label="项目"><option value="p">示例项目</option><option value="q">其他项目</option></select><main><section class="network"><h2>实体关系网络</h2><div id="network"></div></section><aside><h3>美团平台</h3><div id="properties"></div></aside></main></body></html>');
  await page.addStyleTag({path:path.join(web,'entity-attribute-panel.css')});
  await page.addStyleTag({content:'*{box-sizing:border-box}body{margin:0;background:#f4f7f3;color:#193e32;font-family:"Microsoft YaHei",sans-serif;font-size:13px}#project{margin:14px}main{display:grid;grid-template-columns:minmax(0,1fr) 370px;margin:8px 20px;border:1px solid #dce8e2;background:#fff;height:calc(100dvh - 72px);min-height:0}.network{padding:24px;min-width:0}h2{font-size:19px}#network{height:620px}aside{border-left:1px solid #dce8e2;padding:18px;min-width:0;overflow:auto}button{cursor:pointer;border:1px solid #b8cebd;border-radius:4px;background:#276248;color:#fff}button.secondary{background:#fff;color:#315943}button:disabled{opacity:.5;cursor:default}@media(max-width:680px){main{display:block;margin:0 8px;height:auto}.network{padding:12px}#network{height:260px}aside{border-top:1px solid #dce8e2;border-left:0;padding:14px}}'});
  await page.addScriptTag({path:path.join(web,'vendor/echarts.min.js')});
  await page.addScriptTag({path:path.join(web,'entity-attribute-panel.js')});
  await page.evaluate(()=>{
    window.calls=[];window.sourceOpens=[];window.active=true;window.Auth={isAdmin:()=>true};
    window.SourceReader={open:value=>sourceOpens.push(value)};
    window.chart=echarts.init(document.getElementById('network'));
    chart.setOption({animation:false,series:[{type:'graph',layout:'none',roam:true,data:[{id:'e',name:'美团平台',x:250,y:180,symbolSize:60},{id:'org',name:'运营主体',x:90,y:90,symbolSize:45},{id:'law',name:'平台规则',x:410,y:90,symbolSize:45},{id:'user',name:'平台用户',x:250,y:340,symbolSize:45}],links:[{source:'e',target:'org'},{source:'e',target:'law'},{source:'e',target:'user'},{source:'user',target:'law'}],label:{show:true},edgeSymbol:['none','arrow'],lineStyle:{color:'#9bb7a5'},itemStyle:{color:'#588c71'}}]});
    const value=(id,extra={})=>({record_id:id,version:2,version_id:id+'-v2',value_preview:'平台应当建立并完善规则体系。\n包括信息公示、用户服务和争议处理。<script>不能执行</script>',value_truncated:true,value_length:850,datatype:'http://www.w3.org/2001/XMLSchema#string',document_count:2,accepted_support_count:3,status:'accepted',valid_from:'2025-01-01',valid_until:null,...extra});
    window.api=async(url,body)=>{
      calls.push({url,body});
      if(url.endsWith('/sources'))return {record_id:'a',version_id:'a-v2',sources:[{id:body.cursor?'s2':'s1',document_title:body.cursor?'补充规则':'平台治理规则',document_version_id:'frozen-document-v3',chunk_id:'chunk-7',start_char:30,end_char:90,quote:'这是已冻结的历史原文证据。\n原文引文与属性值分别保存。'}],next_cursor:body.cursor?null:'sources-next'};
      if(url.endsWith('/a'))return {id:'a',version:2,version_id:'a-v2',value:'完整职责说明\n\n'+('保留段落和全部原始文本。\n'.repeat(50))+'尾部可完整阅读'};
      return {entity:{id:'e',text:'美团平台',version:4,version_id:'e-v4'},groups:[{predicate:'https://example.org/职责',label:'平台职责',total_values:2,values:[body.cursor?value('b',{value_preview:'历史期间职责',value_truncated:false,value:'历史期间职责',valid_from:'2023-01-01',valid_until:'2024-01-01'}):value('a')]}],property_count:1,value_count:2,watermark:8,next_cursor:body.cursor?null:'values-next'};
    };
    window.panel=EntityAttributePanel.mount(document.getElementById('properties'),{entity:{id:'e',version:4,version_id:'e-v4'},projectId:'p',scope:{filters:{platform:'美团'}},accepts:()=>active});
  });
  await page.locator('.attribute-fact').waitFor();return page;
}
test('grouped values, paragraphs and paged frozen sources leave the relation network unchanged',async()=>{
  const page=await fixture();
  const before=await page.evaluate(()=>JSON.stringify(chart.getOption().series[0].data));
  const beforeRect=await page.locator('#network').boundingBox();
  await page.getByRole('button',{name:/展开全文/}).click();
  await page.locator('.attribute-fact-value.is-expanded').waitFor();
  assert.match(await page.locator('.attribute-fact-value.is-expanded').innerText(),/尾部可完整阅读/);
  assert.ok((await page.locator('.attribute-fact-value.is-expanded').boundingBox()).height<=386,'full paragraphs stay inside a 24rem scroll area');
  assert.deepEqual(await page.locator('#network').boundingBox(),beforeRect,'expansion preserves the graph canvas rectangle');
  assert.equal(await page.locator('#properties script').count(),0);
  await page.getByRole('button',{name:'查看来源',exact:true}).click();
  await page.getByRole('button',{name:/更多来源/}).click();
  assert.equal(await page.locator('.attribute-source').count(),2);
  await page.getByRole('button',{name:'在原文中打开 ↗'}).first().click();
  assert.equal(await page.evaluate(()=>sourceOpens[0].versionId),'frozen-document-v3');
  await page.getByRole('button',{name:/继续加载属性值/}).click();
  assert.equal(await page.locator('.attribute-fact').count(),2);
  assert.equal(await page.evaluate(()=>JSON.stringify(chart.getOption().series[0].data)),before);
  assert.equal(await page.evaluate(()=>chart.getOption().series[0].links.length),4);
  assert.equal(await page.evaluate(()=>calls.filter(call=>call.url.endsWith('/sources'))[0].body.expected_version_id),'a-v2');
  await page.screenshot({path:path.join(out,'network-property-reader.png'),fullPage:true});await page.close();
});
test('narrow screens keep property controls and evidence inside the viewport',async()=>{
  const page=await fixture({width:390,height:844});
  await page.getByRole('button',{name:'查看来源',exact:true}).click();
  const overflow=await page.evaluate(()=>[...document.querySelectorAll('#properties button,.attribute-fact-value,.attribute-source')].filter(el=>{const r=el.getBoundingClientRect();return r.left<0||r.right>innerWidth+1;}).map(el=>el.className));
  assert.deepEqual(overflow,[]);await page.screenshot({path:path.join(out,'mobile-property-reader.png'),fullPage:true});await page.close();
});
test('late property responses cannot populate a stale project and historical conflicts stay read only',async()=>{
  const page=await fixture();
  await page.evaluate(()=>{
    window.panel.dispose();window.finish=null;
    window.stalePanel=EntityAttributePanel.mount(document.getElementById('properties'),{entity:{id:'e'},projectId:'p',scope:{},request:()=>new Promise(resolve=>finish=resolve)});
    document.getElementById('project').value='q';finish({entity:{id:'e'},groups:[{predicate:'secret',values:[{record_id:'secret',value_preview:'过时项目内容'}]}]});
  });
  await page.waitForTimeout(20);assert.doesNotMatch(await page.locator('#properties').innerText(),/过时项目内容/);
  await page.evaluate(()=>{
    document.getElementById('project').value='p';
    EntityAttributePanel.open({entity:{id:'e',text:'历史实体'},projectId:'p',scope:{known_at:'2025-01-01'},request:async()=>({entity:{id:'e'},groups:[],property_count:0,value_count:0,conflict_sets:[{id:'set',predicate:'职责',members:[{value_preview:'甲'},{value_preview:'乙'}]}]})});
  });
  await page.locator('.attribute-conflict-set').waitFor();assert.equal(await page.getByRole('button',{name:'处理冲突'}).isDisabled(),true);
  assert.match(await page.locator('.entity-attribute-dialog').innerText(),/历史知识范围 · 只读/);
  await page.keyboard.press('Escape');assert.equal(await page.locator('.entity-attribute-dialog').isVisible(),false);await page.close();
});
test('conflict values use dedicated full and frozen-source reads and hand off to unified review',async()=>{
  const page=await fixture();
  await page.evaluate(()=>{
    window.conflictCalls=[];window.reviewed=[];
    window.openAttributeConflictReview=(set,options)=>{reviewed.push({id:set.id,projectId:options.projectId});options.onResolved();};
    const request=async(url,body)=>{
      conflictCalls.push({url,body});
      if(url.endsWith('/read'))return {id:'disputed',version:2,version_id:'disputed-v2',value:'争议值完整正文\n保留两方证据'};
      if(url.endsWith('/sources'))return {record_id:'disputed',version_id:'disputed-v2',sources:[{id:'s',document_title:'争议来源',document_version_id:'dispute-doc-v1',quote:'历史引文'}]};
      return {entity:{id:'e'},groups:[],property_count:0,value_count:0,conflict_sets:[{id:'set',predicate:'平台职责',members:[{record_id:'disputed',version_id:'disputed-v2',value_preview:'争议预览',value_truncated:true,document_count:1}]}]};
    };
    window.conflictRequest=request;EntityAttributePanel.open({entity:{id:'e',text:'争议实体'},projectId:'p',scope:{},request});
  });
  const dialog=page.locator('.entity-attribute-dialog');await dialog.locator('.attribute-conflict-set').waitFor();
  await dialog.getByRole('button',{name:'展开全文',exact:true}).click();assert.match(await dialog.innerText(),/争议值完整正文/);
  await dialog.getByRole('button',{name:'查看来源',exact:true}).click();await dialog.locator('.attribute-source').waitFor();
  assert.equal(await page.evaluate(()=>conflictCalls[1].url),'/api/projects/p/attribute-conflicts/set/members/disputed/read');
  assert.equal(await page.evaluate(()=>conflictCalls[2].body.expected_version_id),'disputed-v2');
  await dialog.getByRole('button',{name:'处理冲突',exact:true}).click();assert.deepEqual(await page.evaluate(()=>reviewed),[{id:'set',projectId:'p'}]);
  await page.evaluate(()=>{
    document.querySelector('.entity-attribute-dialog').close();
    EntityAttributePanel.open({entity:{id:'e',text:'重新打开'},projectId:'p',scope:{},request:(...args)=>new Promise(resolve=>setTimeout(()=>conflictRequest(...args).then(resolve),40))});
  });
  await dialog.locator('.attribute-conflict-set').waitFor({timeout:1500});
  await page.close();
});
test('owner refresh and independent conflict retry preserve the network and replace the displayed snapshot',async()=>{
  const page=await fixture();const before=await page.locator('#network').boundingBox();
  await page.evaluate(()=>{
    panel.dispose();window.ownerRevision=4;window.ownerUpdates=[];window.conflictFail=true;window.reviewCalls=[];
    const set=id=>({id,predicate:'平台职责',version:1,members:[]});
    const request=async(url,body)=>{
      reviewCalls.push({url,body});
      if(url.endsWith('/attribute-conflicts')){if(conflictFail){conflictFail=false;throw Error('冲突分页网络失败');}return {entity_id:'e',conflict_sets:[set('051')],next_cursor:null};}
      return {entity:{id:'e',text:ownerRevision===5?'更新的平台':'美团平台',version:ownerRevision,version_id:'e-v'+ownerRevision},groups:[{predicate:'职责',label:'职责',total_values:1,values:[{record_id:ownerRevision===5?'fresh':'original',version_id:'fact-v2',value_preview:ownerRevision===5?'更新后的属性值':'原属性值',datatype:'string',status:'accepted'}]}],property_count:1,value_count:1,watermark:ownerRevision,conflict_sets:Array.from({length:50},(_,i)=>set(String(i+1).padStart(3,'0'))),conflict_next_cursor:'050'};
    };
    window.panel=EntityAttributePanel.mount(document.getElementById('properties'),{entity:{id:'e',version:4,version_id:'e-v4'},projectId:'p',scope:{},request,onEntityChange:owner=>{ownerUpdates.push(owner);if(window.deferOwner)return new Promise(resolve=>window.finishOwner=resolve);}});
  });
  await page.locator('[data-conflict-set="050"]').first().waitFor();
  await page.getByRole('button',{name:'继续加载冲突集合 · 每页 20 组',exact:true}).click();
  await page.getByRole('button',{name:'重试冲突分页',exact:true}).click();
  await page.locator('[data-conflict-set="051"]').first().waitFor();assert.equal(await page.locator('.attribute-fact').count(),1);
  await page.evaluate(()=>ownerRevision=5);await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.locator('[data-attribute-record="fresh"]').waitFor();assert.match(await page.locator('.attribute-owner-revision').innerText(),/更新的平台.*r5/);
  assert.equal(await page.locator('[data-attribute-record="original"]').count(),0);assert.equal(await page.locator('[data-conflict-set="051"]').count(),0);
  assert.equal(await page.evaluate(()=>ownerUpdates[0].version_id),'e-v5');assert.equal(await page.evaluate(()=>reviewCalls.filter(call=>call.url.endsWith('/attribute-conflicts')).length),2);
  await page.evaluate(()=>{window.deferOwner=true;ownerRevision=6;});await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.waitForFunction(()=>typeof window.finishOwner==='function');
  await page.evaluate(()=>ownerRevision=7);await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.evaluate(()=>{window.deferOwner=false;finishOwner();});
  await page.waitForFunction(()=>ownerUpdates.at(-1).version_id==='e-v7',{},{timeout:1500});
  assert.deepEqual(await page.locator('#network').boundingBox(),before);await page.close();
});
test('upload helper displays published attribute count and paragraph guidance without enabling extraction',async()=>{
  const page=await browser.newPage();
  await page.setContent('<select id="project"><option value="p">示例项目</option></select><section id="tab-ingest"><select id="extraction-mode"><option value="ontology">使用项目本体</option><option value="documents">仅文档</option></select><p id="mode-help"></p><label><input id="parse-attributes" type="checkbox">抽取属性</label><p id="parse-attributes-help"></p></section>');
  await page.evaluate(()=>{window.api=async url=>url.endsWith('/ontologies')?{versions:[{id:'published',version:3,summary:{attributes:[{id:'amount'},{id:'description'}]}}]}:{open_draft_count:0};});
  await page.addScriptTag({path:path.join(web,'ingest-ontology.js')});
  await page.evaluate(()=>IngestOntology.mount(document.getElementById('extraction-mode'),document.getElementById('mode-help')));
  await page.waitForFunction(()=>document.getElementById('parse-attributes-help').textContent.includes('2 项属性'));
  assert.match(await page.locator('#parse-attributes-help').innerText(),/段落.*默认关闭.*知识审核/);assert.equal(await page.locator('#parse-attributes').isChecked(),false);
  await page.evaluate(async()=>{document.getElementById('extraction-mode').value='documents';await IngestOntology.refresh();});
  assert.match(await page.locator('#parse-attributes-help').innerText(),/仅文档.*不抽取/);assert.equal(await page.locator('#parse-attributes').isChecked(),false);await page.close();
});
