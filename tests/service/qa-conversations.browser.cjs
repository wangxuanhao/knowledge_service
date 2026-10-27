/* Full workbench browser integration with a bounded HTTP fixture, no live models. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const clone=value=>JSON.parse(JSON.stringify(value));

async function fixture(){
  const conversations=new Map(),requests=[],errors=[],failures={save:0};let serial=0;
  const project={id:'p',name:'会话验收项目',counts:{documents:1,entities:0,relations:0,chunks:1},metadata:{ontology_mode:'discovery'}};
  const server=http.createServer(async(req,res)=>{
    const url=new URL(req.url,'http://127.0.0.1');
    const json=(value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value));};
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    let body={};try{body=JSON.parse(Buffer.concat(chunks).toString()||'{}');}catch{return json({detail:'Invalid fixture JSON'},400);}
    const summary=conversation=>{const {messages,...rest}=conversation;return {...rest,message_count:messages.length};};
    const match=url.pathname.match(/^\/api\/projects\/p\/qa\/conversations(?:\/([^/]+))?$/);
    if(match){
      const id=match[1],conversation=conversations.get(id);
      if(!id&&req.method==='GET'){
        const rows=[...conversations.values()].filter(row=>row.title.includes(url.searchParams.get('q')||'')).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
        const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||50);
        return json({conversations:rows.slice(offset,offset+limit).map(summary),total:rows.length,has_more:rows.length>offset+limit});
      }
      if(!id&&req.method==='POST'){
        const now=new Date(Date.now()+ ++serial).toISOString();
        const row={id:'conversation-'+serial,title:body.title||'新会话',title_auto:!body.title,version:1,created_at:now,updated_at:now,messages:[],message_count:0};
        conversations.set(row.id,row);return json({conversation:row},201);
      }
      if(!conversation)return json({detail:'会话不存在'},404);
      if(req.method==='GET')return json({conversation});
      const expected=req.method==='DELETE'?Number(url.searchParams.get('expected_version')):body.expected_version;
      if(expected!==conversation.version)return json({detail:'会话版本冲突'},409);
      if(req.method==='DELETE'){conversations.delete(id);res.writeHead(204);return res.end();}
      if(req.method==='PATCH'){conversation.title=body.title;conversation.title_auto=false;}
      if(req.method==='PUT'){
        if(failures.save>0){failures.save--;return json({detail:'暂时无法保存'},503);}
        conversation.messages=clone(body.messages);conversation.message_count=body.messages.length;
        if(conversation.title_auto)conversation.title=body.messages.find(row=>row.kind==='user')?.text.slice(0,80)||'新会话';
      }
      conversation.version++;conversation.updated_at=new Date(Date.now()+ ++serial).toISOString();
      return json({conversation});
    }
    if(url.pathname==='/api/projects/p/search'){
      const matched=body.query!=='未匹配词',enabled=body.ontology_expansion;
      const direct={id:'meituan',version_id:'meituan-v1',kind:'entity',text:'美团直接命中',type:'互联网平台',score:1,metadata:{}};
      const supplement={id:'related',version_id:'related-v1',kind:'entity',text:'美团知识产权维权平台',type:'知识产权维权平台',retrieval_channel:'ontology_entity_type',metadata:{}};
      return json({hits:matched?[direct,...(enabled?[supplement]:[])]:[],source_chunks:[],context_chunks:[],
        requested_mode:body.retrieval_mode,active_mode:body.retrieval_mode,_backend:'milvus',degraded:false,complete:true,
        candidate_count:matched?8:0,channel_quotas:{entity:5,chunk:5,relation:5},
        ...(enabled?{ontology_expansion:{matched,has_hierarchy:true,type_hits:matched?8:0,used_hits:matched?1:0,
          terms:matched?['urn:互联网平台']:[],literal_terms:[],entity_terms:matched?['urn:互联网平台']:[],subclasses:matched?['urn:知识产权维权平台']:[],
          entity_matches:matched?[{id:'meituan',text:'美团',type:'urn:互联网平台',type_label:'互联网平台'}]:[]}}:{})});
    }
    if(url.pathname==='/api/projects/p/qa/stream'){
      requests.push(body);const id='answer-'+ ++serial;
      const evidence={citation:'E1',kind:'chunk',text:'冻结原文：'+body.query,version:1,version_id:'v1',
        validity_status:'effective',answer_eligible:true,provenance_ref:'answer:'+id+'#E1',sources:[]};
      const entity=body.query.includes('美团'),matched=entity||body.query.includes('互联网平台');
      const expansion=body.ontology_expansion?{matched,has_hierarchy:true,type_hits:matched?7:0,used_hits:matched?1:0,
        terms:matched?['urn:test:互联网平台']:[],subclasses:matched?['urn:test:知识产权维权平台']:[],
        ...(entity?{entity_matches:[{id:'meituan',text:'美团',matched_name:'美团',match_kind:'name',type:'urn:test:互联网平台',type_label:'互联网平台'}],entity_terms:['urn:test:互联网平台'],literal_terms:[],used_hits:1}:{})}:undefined;
      const supplementary={...evidence,citation:'E2',text:'类型补充：知识产权维权平台',retrieval_channel:entity?'ontology_entity_type':'ontology_type',provenance_ref:'answer:'+id+'#E2'};
      const events=[['evidence',{answer_id:id,evidence:[evidence,...(body.ontology_expansion&&matched?[supplementary]:[])],ontology_expansion:expansion,
        requested_mode:body.retrieval_mode,active_modes:[body.retrieval_mode],degraded:false,_backend:'milvus'}],
        ['done',{answer_id:id,answer:'关于'+body.query+'的文档回答 [E1]',mode:'evidence_only',
          generation:{requested:false,called:false},timings_ms:{total:8}}]];
      res.writeHead(200,{'Content-Type':'text/event-stream; charset=utf-8'});
      return res.end(events.map(([event,data])=>'event: '+event+'\ndata: '+JSON.stringify(data)+'\n\n').join(''));
    }
    if(url.pathname.startsWith('/api/')){
      const name=url.pathname.slice(5);let value={};
      if(name==='auth/policy')value={auth_enabled:false};
      else if(name==='projects')value={projects:[project]};
      else if(name==='projects/p')value=project;
      else if(name==='ingest/capabilities')value={concurrency_choices:['auto',1,2],max_file_bytes:10000000,max_batch_bytes:20000000,max_batch_items:20,supported_extensions:['.txt','.md','.pdf','.docx','.zip']};
      else if(name.endsWith('/records/page'))value={records:[],total:0,next_cursor:null};
      else if(name.endsWith('/metadata/facets'))value={fields:[],records:1};
      else if(name.endsWith('/graph-neighborhood'))value={records:[],truncated:false};
      else if(name.endsWith('/sources'))value={documents:[]};
      else if(name.endsWith('/ontologies'))value={versions:[]};
      else if(name.endsWith('/ontology'))return json({detail:'尚未发布本体'},404);
      else if(name.endsWith('/ingest-batches'))value={batches:[]};
      else if(name.endsWith('/jobs'))value={jobs:[]};
      else if(name.endsWith('/snapshots'))value={snapshots:[]};
      else if(name.endsWith('/applicability-values'))value={platforms:[],businesses:[],audiences:[]};
      else if(name.endsWith('/ontology-drafts'))value={drafts:[]};
      else if(name.endsWith('/ontology-discovery'))value={unpublished_candidate_count:0};
      return json(value);
    }
    const file=path.resolve(web,url.pathname==='/'?'index.html':url.pathname.replace(/^\/(assets|vendor)\//,(_,prefix)=>prefix==='vendor'?'vendor/':'').replace(/^\//,''));
    if(!file.startsWith(web+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'};
    res.writeHead(200,{'Content-Type':(types[path.extname(file)]||'application/octet-stream')+'; charset=utf-8'});res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const executablePath='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  const browser=await chromium.launch({headless:true,...(fs.existsSync(executablePath)?{executablePath}:{})});
  async function page(){
    const context=await browser.newContext({viewport:{width:1440,height:950}}),page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:'+server.address().port,{waitUntil:'load'});
    await page.waitForSelector('#project option[value="p"]',{state:'attached'});
    await page.selectOption('#project','p');await page.click('[data-tab="qa"]');
    await page.waitForFunction(()=>!document.getElementById('qa').disabled);
    return page;
  }
  return {page,conversations,requests,errors,failures,close:async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));}};
}

test('server conversations create, switch, rename, restore, delete and stay inside the mobile viewport',async()=>{
  const env=await fixture();
  try{
    const page=await env.page();
    async function ask(query,mode){
      await page.locator('#qa-settings>summary').click();
      assert.equal(await page.locator('#qa-mode').isVisible(),true,'retrieval mode remains accessible inside settings');
      await page.selectOption('#qa-mode',{label:mode});
      await page.check('#qa-ontology-expansion');
      await page.locator('#qa-settings>summary').click();
      await page.fill('#qa-query',query);await page.click('#qa');
      await page.waitForSelector('.qa-turn-assistant[data-state="done"]');
      await page.waitForFunction(()=>document.querySelector('.qa-session-select[aria-current="true"] small')?.textContent.includes('2 条消息'));
    }
    await ask('美团','语义向量');
    assert.match(await page.locator('.qa-answer-status').innerText(),/美团 → 互联网平台.*实际补入 1 条/);
    assert.equal(await page.locator('.qa-ontology-supplement').isVisible(),true);
    assert.equal(await page.locator('.qa-evidence-panel').isVisible(),false);
    assert.match(await page.locator('.qa-ontology-supplement').innerText(),/本体扩展补充.*类型补充/s);
    assert.equal(env.requests[0].ontology_expansion,true);
    assert.equal(env.requests[0].retrieval_mode,'semantic');
    const first=[...env.conversations.values()].find(row=>row.title==='美团');assert.equal(first.messages.length,2);
    await page.click('#qa-new-chat');await page.waitForFunction(()=>!document.querySelector('.qa-turn'));
    await ask('互联网平台','混合检索');
    assert.match(await page.locator('.qa-answer-status').innerText(),/互联网平台.*1 个子类.*7 条类型/);
    assert.equal(env.conversations.size,2);
    await page.locator('.qa-session-item.is-active summary').click();
    await page.locator('.qa-session-item.is-active').getByRole('button',{name:'重命名',exact:true}).click();
    await page.fill('#qa-session-name','平台规则');await page.locator('#qa-session-name').press('Enter');
    await page.waitForFunction(()=>document.getElementById('qa-active-session-title').textContent==='平台规则');
    const other=await env.page();
    assert.match(await other.locator('.qa-answer').innerText(),/互联网平台/);
    assert.match(await other.locator('.qa-answer-status').innerText(),/7 条类型/);
    await other.getByRole('button',{name:'美团',exact:true}).click();
    await other.waitForFunction(()=>document.querySelector('.qa-answer')?.textContent.includes('关于美团'));
    assert.equal(await other.locator('.qa-answer .provenance-citation').count(),1);
    assert.match(await other.locator('.qa-ontology-supplement').innerText(),/类型补充/);
    await other.setViewportSize({width:390,height:844});
    await other.locator('.qa-ontology-supplement').scrollIntoViewIfNeeded();
    const mobileSupplement=await other.locator('#qa-composer').evaluate(node=>({bottom:node.getBoundingClientRect().bottom,height:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth}));
    assert.ok(mobileSupplement.bottom<=mobileSupplement.height+1,JSON.stringify(mobileSupplement));assert.equal(mobileSupplement.overflow,0);
    const sectionShots=path.resolve(__dirname,'../../.benchmarks/ontology-sections');fs.mkdirSync(sectionShots,{recursive:true});
    await other.screenshot({path:path.join(sectionShots,'qa-mobile.png')});
    await other.setViewportSize({width:1440,height:950});
    await other.locator('.qa-session-item.is-active summary').click();
    await other.locator('.qa-session-item.is-active').getByRole('button',{name:'删除',exact:true}).click();
    await other.click('#qa-session-dialog-cancel');assert.equal(env.conversations.size,2);
    await other.locator('.qa-session-item.is-active summary').click();
    await other.locator('.qa-session-item.is-active').getByRole('button',{name:'删除',exact:true}).click();
    await other.click('#qa-session-dialog-confirm');
    await other.waitForFunction(()=>document.querySelectorAll('.qa-session-item').length===1);
    assert.equal(env.conversations.has(first.id),false);
    assert.match(await other.locator('.qa-answer').innerText(),/互联网平台/);
    await other.click('#qa-clear');await other.click('#qa-session-dialog-cancel');
    assert.equal(await other.locator('.qa-answer').count(),1);
    env.failures.save=1;
    await other.click('#qa-clear');await other.click('#qa-session-dialog-confirm');
    await other.waitForFunction(()=>!document.querySelector('.qa-turn'));
    await other.getByRole('button',{name:'重试保存',exact:true}).click();
    await other.waitForFunction(()=>document.querySelector('.qa-session-select[aria-current="true"] small')?.textContent.includes('0 条消息'));
    await other.fill('#qa-query','清空后新问题');await other.click('#qa');
    await other.waitForSelector('.qa-turn-assistant[data-state="done"]');
    await other.waitForFunction(()=>document.querySelector('.qa-session-select[aria-current="true"] small')?.textContent.includes('2 条消息'));
    const remaining=[...env.conversations.values()][0];assert.equal(remaining.messages.length,2);assert.equal(remaining.messages[0].text,'清空后新问题');
    await other.click('#qa-clear');await other.click('#qa-session-dialog-confirm');
    await other.waitForFunction(()=>!document.querySelector('.qa-turn'));
    await other.setViewportSize({width:390,height:844});
    await other.waitForFunction(()=>document.getElementById('qa-workspace').dataset.historyOpen==='false');
    const appMenu=other.getByRole('button',{name:'工作区菜单',exact:true});
    assert.equal(await other.locator('body>aside nav').isVisible(),false);
    await appMenu.click();assert.equal(await other.locator('body>aside nav').isVisible(),true);
    assert.equal(await other.getByRole('button',{name:'资料库',exact:true}).isVisible(),true);
    await appMenu.click();assert.equal(await other.locator('body>aside nav').isVisible(),false);
    assert.ok(await other.locator('#qa-page').evaluate(node=>node.getBoundingClientRect().top<100),'mobile chat should use a compact project header');
    await other.click('#qa-sessions-toggle');
    assert.equal(await other.locator('#qa-sessions-backdrop').isVisible(),true);
    await other.click('#qa-sessions-close');
    const geometry=await other.locator('#qa-composer').evaluate(node=>({bottom:node.getBoundingClientRect().bottom,height:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth,outer:document.querySelector('body>main').scrollHeight-document.querySelector('body>main').clientHeight}));
    assert.ok(geometry.bottom<=geometry.height+1,JSON.stringify(geometry));assert.equal(geometry.overflow,0);assert.ok(geometry.outer<=1,JSON.stringify(geometry));
    assert.ok(await other.evaluate(()=>document.documentElement.scrollHeight-innerHeight<=1),'the mobile document must not scroll the project header out of view');
    assert.ok(await other.locator('body>aside').evaluate(node=>node.getBoundingClientRect().top>=0),'the project selector must stay visible after using the history drawer');
    assert.ok(await other.locator('body>main').evaluate(node=>node.getBoundingClientRect().bottom<=innerHeight+1),'the mobile workspace must occupy only the space below its header');
    const screenshots=path.resolve(__dirname,'../../.benchmarks/qa-conversations');fs.mkdirSync(screenshots,{recursive:true});
    await page.screenshot({path:path.join(screenshots,'desktop.png')});await page.screenshot({path:path.join(sectionShots,'qa-desktop.png')});await other.screenshot({path:path.join(screenshots,'mobile.png')});
    assert.deepEqual(env.errors,[]);
  }finally{await env.close();}
});

test('legacy history uploads only after ownership confirmation and restores its citations in another browser',async()=>{
  const env=await fixture();
  const legacy=[{kind:'user',text:'旧问题',scope:{}},{kind:'assistant',text:'旧回答 [E1]',scope:{},answer_id:'legacy-answer',evidence:[{citation:'E1',kind:'chunk',text_preview:'旧原文',provenance_ref:'answer:legacy-answer#E1'}]}];
  try{
    const page=await env.page();
    await page.evaluate(value=>localStorage.setItem('kg_qa_v1_p',JSON.stringify(value)),legacy);
    await page.reload({waitUntil:'load'});await page.waitForSelector('#project option[value="p"]',{state:'attached'});
    await page.selectOption('#project','p');await page.click('[data-tab="qa"]');
    await page.click('#qa-session-import');await page.click('#qa-session-dialog-cancel');assert.equal(env.conversations.size,0);
    await page.click('#qa-session-import');assert.match(await page.locator('#qa-session-dialog-description').innerText(),/当前账号/);
    await page.click('#qa-session-dialog-confirm');await page.waitForSelector('.qa-answer .provenance-citation');
    assert.equal(env.conversations.size,1);assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('kg_qa_v1_p'))[0].text),'旧问题');
    const second=await env.page();assert.match(await second.locator('.qa-answer').innerText(),/旧回答/);assert.equal(await second.locator('.qa-answer .provenance-citation').count(),1);
    assert.equal([...env.conversations.values()][0].messages[1].evidence[0].text_preview,'旧原文');assert.deepEqual(env.errors,[]);
  }finally{await env.close();}
});


test('search exposes direct and supplemental regions only after enabling expansion, including an unmatched empty state',async()=>{
  const env=await fixture();
  try{
    const page=await env.page();await page.click('[data-tab="search"]');
    await page.fill('#query','美团');await page.click('#search');await page.waitForSelector('.hit-entry');
    assert.equal(await page.locator('#hits [data-expansion-group]').count(),0);
    if(await page.locator('#ontology-expansion-toggle').count())await page.click('#ontology-expansion-toggle');
    else await page.check('#ontology-expansion');
    await page.click('#search');
    const direct=page.locator('#hits [data-expansion-group="direct"]'),extra=page.locator('#hits [data-expansion-group="supplemental"]');
    await extra.waitFor();assert.match(await direct.innerText(),/美团直接命中/);assert.doesNotMatch(await direct.innerText(),/维权平台/);
    assert.match(await extra.innerText(),/本体扩展补充.*美团知识产权维权平台/s);assert.doesNotMatch(await extra.innerText(),/美团直接命中/);
    const screenshots=path.resolve(__dirname,'../../.benchmarks/ontology-sections');fs.mkdirSync(screenshots,{recursive:true});
    await page.screenshot({path:path.join(screenshots,'search-desktop.png')});
    await page.setViewportSize({width:390,height:844});await extra.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.screenshot({path:path.join(screenshots,'search-mobile.png')});
    await page.fill('#query','未匹配词');await page.click('#search');
    await page.waitForFunction(()=>document.querySelector('#hits [data-expansion-group="supplemental"]')?.textContent.includes('未匹配'));
    assert.equal(await page.locator('#hits [data-expansion-group="supplemental"] .hit-entry').count(),0);
    assert.deepEqual(env.errors,[]);
  }finally{await env.close();}
});
