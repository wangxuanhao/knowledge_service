const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function fixture(reviewRows=[],options={}){
  const nodes=new Map(),calls=[],created=[];
  function element(){const el={innerHTML:'',textContent:'',value:'',className:'',dataset:{},onclick:null,events:{},children:[],
    append(child){this.children.push(child);},after(){},prepend(){throw Error('review must not be prepended to jobs');},
    insertAdjacentHTML(){},querySelector(){return element();},querySelectorAll(){return [];},
    click(){return this.onclick?.();},addEventListener(name,fn){this.events[name]=fn;}};
    el.classList={contains:name=>el.className.split(' ').includes(name),add:name=>el.className+=' '+name,
      remove:name=>el.className=el.className.split(' ').filter(n=>n!==name).join(' '),toggle(){}};
    return el;}
  const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const logs=['[2026-09-10T10:00:01Z] 文档开始 · 文件.md · 4 字符','[2026-09-10T10:00:02Z] 实体抽取返回 · 2 个','[2026-09-10T10:00:03Z] 实体抽取返回 · 3 个',
    '关系抽取返回 · 1 条','仍在等待当前步骤（非完成进度）'];
  const job={id:'job-a',project_id:'a',kind:'ingest',status:'running',logs,stage:'关系抽取',progress:40,
    created_at:'2026-09-10T10:00:00Z',heartbeat_at:'2026-09-10T10:00:04Z'};
  const context=vm.createContext({current:'a',$:get,readParseSettings:()=>({chunk_size:1800}),showTab(){},
    document:{createElement(){const e=element();created.push(e);return e;},querySelector:get,querySelectorAll:()=>[]},
    esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    status(){},jobList(){throw Error('old list must not be used');},bind:(id,fn)=>get(id).onclick=fn,
    api:async url=>{calls.push(url);if(url.endsWith('/reviews'))return {reviews:reviewRows};
      if(url.endsWith('/ontology-change-proposals'))return {proposals:[]};
      if(url==='/api/health')return {capabilities:['typed_reviews']};
      if(url.endsWith('/ontology')){if(options.noOntology)throw Error('本项目尚未发布本体。开放本体发现模式需先在本体发现中生成并发布草案。');return {id:'ont',summary:{relations:[],classes:[],attributes:[]}};}
      if(url==='/api/jobs')return {jobs:[job,{...job,id:'job-b',project_id:'b'}]};
      if(url==='/api/projects/a/jobs')return {jobs:[job]};
      if(url==='/api/projects/b/jobs')return {jobs:[]};
      throw Error('Unexpected URL: '+url);}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/task-review.js'),'utf8'),context);
  return {context,get,calls,created};
}

test('refresh uses new project-scoped summary and preserves full logs collapsed',async()=>{
  const {get,calls}=fixture();
  await get('load-jobs').onclick();
  const html=get('tasks').innerHTML,summary=html.split('<details')[0];
  assert.match(html,/job-a/);assert.doesNotMatch(html,/job-b/);
  assert.match(summary,/实体抽取返回 · 3 个/);
  assert.doesNotMatch(summary,/实体抽取返回 · 2 个|仍在等待当前步骤/);
  assert.match(html,/实体抽取返回 · 2 个/);
  assert.match(html,/2026-09-10 18:00:00/);
  assert.match(html,/2026-09-10 18:00:04/);
  assert.match(html,/\[2026-09-10 18:00:01\] 文档开始/);
  assert.doesNotMatch(html,/2026-09-10T10:00:0[0-4]Z/);
  assert.doesNotMatch(html,/<details[^>]* open/);
  assert.ok(!calls.includes('/api/projects/a/reviews'));
});

test('programmatic project switch does not leave previous jobs or load hidden reviews',async()=>{
  const {context,get,calls}=fixture();
  await get('load-jobs').onclick();
  context.current='b';get('project').onchange();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(!calls.includes('/api/projects/b/reviews'));
  assert.match(get('tasks').innerHTML,/当前项目暂无任务/);
});

test('review menu is independent and attribute extraction is explicitly opt-in',async()=>{
  const {context,get,calls,created}=fixture();
  const page=created.find(e=>e.id==='tab-reviews'),nav=created.find(e=>e.dataset.tab==='reviews');
  assert.ok(get('main').children.includes(page));
  assert.match(page.children[0].innerHTML,/实体类型.*关系.*实体属性/);
  nav.click();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('/api/projects/a/reviews'));
  assert.ok(!calls.includes('/api/jobs'));
  assert.equal(get('title').textContent,'知识审核');
  get('extraction-mode').value='ontology';get('parse-attributes').checked=true;
  assert.equal(context.readParseSettings().extract_attributes,true);
  get('parse-relation-constraints').value='review';
  assert.equal(context.readParseSettings().relation_constraint_mode,'review');
  assert.ok(calls.includes('/api/projects/a/ontology-change-proposals'));
  get('extraction-mode').value='documents';
  assert.equal(context.readParseSettings().extract_attributes,undefined);
  get('extraction-mode').value='discovery';get('parse-attributes').checked=true;
  assert.equal(context.readParseSettings().extract_attributes,true);
});

test('missing ontology shows guidance instead of Not found',async()=>{
  const {get,created}=fixture([],{noOntology:true});
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/尚未发布本体/);
  assert.match(html,/前往本体发现/);
  assert.doesNotMatch(html,/Not found:/);
});

test('old backend is clearly blocked before decisions are offered',async()=>{
  const {context,get,created}=fixture(),api=context.api;
  context.api=async url=>url==='/api/health'?{capabilities:[]}:api(url);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(get('relation-reviews').textContent,/旧版后端.*重启/);
});

test('SHACL exception review names the failed constraint without requiring ontology mapping',async()=>{
  const candidate={id:'validation-1',kind:'validation',status:'pending',record_id:'entity-1',text:'规则文档',
    reason:'本体约束异常',constraint:'MinCountConstraintComponent',path:'https://example.org/title',path_label:'title',
    message:'Less than 1 values',severity:'Violation',document_title:'原文.md',ontology_id:'ont',
    start_char:0,end_char:10,evidence:'规则正文',document_id:'doc',document_version:2};
  const {get,created}=fixture([candidate]);
  created.find(e=>e.dataset.tab==='reviews').click();
  await new Promise(resolve=>setImmediate(resolve));
  const html=get('relation-reviews').innerHTML;
  assert.match(html,/本体异常/);
  assert.match(html,/MinCountConstraintComponent/);
  assert.match(html,/title/);
  assert.match(html,/确认例外/);
  assert.doesNotMatch(html,/映射到当前本体/);
});
