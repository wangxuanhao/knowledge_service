const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createController,releaseLabel,resultRelease,attributeExtractionHelp}=require('../../knowledge_service/web/ingest-ontology.js');

const v1={id:'release-1',version:1,created_at:'2026-10-15T00:00:00Z'};
const v3={id:'release-3',version:3,created_at:'2026-10-16T00:00:00Z'};
const controller=(readVersions=async()=>({versions:[v1,v3]}),readDraftState=async()=>({open_draft_count:1}))=>
  createController({readVersions,readDraftState});

test('submission pins the displayed authoritative release, including reused version numbers',async()=>{
  const c=controller(async()=>({versions:[v1,v3,{...v3,id:'annotation',version_reused:true}]}));
  await c.refresh('p','ontology');
  assert.equal(c.state().release.id,'annotation');
  assert.deepEqual(c.settings('p','ontology'),{ontology_id:'annotation'});
  assert.match(releaseLabel(c.state().release),/本体 v3.*标注修订/);
  assert.equal(c.state().openDraftCount,1);
});

test('unpublished ontology and failed reads block controlled submission only',async()=>{
  for(const read of [async()=>({versions:[]}),async()=>{throw Error('network');}]){
    const c=controller(read);await c.refresh('p','ontology');
    assert.throws(()=>c.settings('p','ontology'),/本体/);
    assert.deepEqual(c.settings('p','discovery'),{});
    assert.deepEqual(c.settings('p','documents'),{});
  }
});

test('late project responses cannot replace the current project or unblock stale submission',async()=>{
  let resolveOld;
  const c=controller(p=>p==='old'?new Promise(resolve=>{resolveOld=resolve;}):Promise.resolve({versions:[v3]}));
  const pending=c.refresh('old','ontology');
  assert.throws(()=>c.settings('old','ontology'),/本体/);
  await c.refresh('new','ontology');
  resolveOld({versions:[v1]});await pending;
  assert.equal(c.state().projectId,'new');
  assert.equal(c.state().release.id,v3.id);
  assert.throws(()=>c.settings('old','ontology'),/本体/);
});

test('draft read failure is explicit and does not invent a draft count',async()=>{
  const c=controller(undefined,async()=>{throw Error('forbidden');});
  await c.refresh('p','ontology');
  assert.equal(c.state().openDraftCount,null);
  assert.match(c.state().draftError,/forbidden/);
  assert.deepEqual(c.settings('p','ontology'),{ontology_id:v3.id});
});

test('result uses assertion ownership over the submitted context and never guesses latest',async()=>{
  const read=async()=>({versions:[v1,v3]});
  assert.equal((await resultRelease('p',[{ontology_id:v1.id}],v3,read)).id,v1.id);
  assert.equal((await resultRelease('p',[],v1,read)).id,v1.id);
  assert.equal(await resultRelease('p',[],null,read),null);
  const missing=await resultRelease('p',[{ontology_id:'removed'}],v3,read);
  assert.equal(missing.id,'removed');
  assert.match(releaseLabel(missing),/版本号未知/);
  const mixed=await resultRelease('p',[{ontology_id:v1.id},{ontology_id:v3.id}],v3,read);
  assert.match(releaseLabel(mixed),/多个本体版本/);
});
test('attribute extraction help uses the displayed published definitions including paragraphs and review',()=>{
  assert.equal(typeof attributeExtractionHelp,'function');
  const help=attributeExtractionHelp({mode:'ontology',status:'ready',release:{id:'published',summary:{attributes:[{id:'amount'},{id:'description'},{id:'retired',active:false}]}}});
  assert.match(help,/已发布.*2 项属性/);assert.match(help,/TTL/);assert.match(help,/段落/);assert.match(help,/默认关闭/);assert.match(help,/知识审核.*属性事实/);
  assert.match(attributeExtractionHelp({mode:'documents'}),/仅文档.*不抽取/);
  assert.doesNotMatch(attributeExtractionHelp({mode:'ontology',status:'loading'}),/0 项属性/);
});
test('upload attribute toggle is off by default and its help is not limited to scalar fields',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const html=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/index.html'),'utf8');
  const checkbox=html.match(/<input\b[^>]*id="parse-attributes"[^>]*>/)?.[0]||'';
  assert.ok(checkbox);assert.doesNotMatch(checkbox,/\bchecked\b/);assert.match(checkbox,/aria-describedby="parse-attributes-help"/);
  assert.match(html,/id="parse-attributes-help"/);assert.doesNotMatch(html,/数量、金额、日期、状态等标量字段/);
});
