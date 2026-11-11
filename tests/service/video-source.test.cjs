const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const read=file=>fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web',file),'utf8');

test('video hydration uses authenticated owned blobs and deferred seek',()=>{
  const content=read('source-content.js');
  assert.match(content,/function hydrateVideos\(/);
  assert.match(content,/scope\.loadBlob\(url/);
  assert.match(content,/video\.controls\s*=\s*true/);
  assert.match(content,/function seekVideo\(/);
  assert.match(content,/loadedmetadata/);
  assert.match(content,/video\.removeAttribute\('src'\)/);
});

test('reader provides ASR segments and routes their timestamps back to video reading',()=>{
  const reader=read('source-reader.js');
  assert.match(reader,/\/asr/);
  assert.match(reader,/segment\.start_ms/);
  assert.match(reader,/view\.seekVideo/);
  assert.match(reader,/mp4\|webm/);
});

test('video session authenticates bytes, seeks after metadata and releases its blob on disposal',async()=>{
  const {createSession}=require('../../knowledge_service/web/source-content.js');
  const previous=globalThis.location;globalThis.location={origin:'https://reader.test'};
  const revoked=[],reads=[],events=new Map();let pauses=0,loads=0;
  const attributes=new Map([['src','/api/projects/p/source-documents/v/assets/clip']]);
  const video={tagName:'VIDEO',dataset:{assetId:'clip'},readyState:0,duration:12,currentTime:0,getAttributeNames:()=>[...attributes.keys()],
    removeAttribute:name=>attributes.delete(name),getAttribute:name=>attributes.get(name),after:()=>{},
    addEventListener:(name,fn)=>{const handlers=events.get(name)||[];handlers.push(fn);events.set(name,handlers);},
    removeEventListener:(name,fn)=>events.set(name,(events.get(name)||[]).filter(item=>item!==fn)),
    pause:()=>pauses++,load:()=>loads++,scrollIntoView:()=>{}};
  const host={ownerDocument:{createElement:tag=>tag==='template'?{content:{querySelectorAll:selector=>['*','img,video'].includes(selector)?[video]:[],cloneNode:()=>({})}}:{setAttribute:()=>{}}},
    querySelectorAll:selector=>selector.startsWith('video')?[video]:[],classList:{add:()=>{},remove:()=>{}},replaceChildren:()=>{}};
  const session=createSession({host,projectId:'p',versionId:'v',fetcher:async(url,options)=>{
    reads.push({url,options});return {ok:true,text:async()=>'<video controls src="/api/projects/p/source-documents/v/assets/clip"></video>',blob:async()=>new Blob(['video'],{type:'video/mp4'})};
  },urlApi:{createObjectURL:()=> 'blob:video',revokeObjectURL:url=>revoked.push(url)}});
  try{
    await session.render();session.seekVideo('clip',4200);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(reads[0].url,'/api/projects/p/source-documents/v/rendered');
    assert.equal(reads[1].url,'/api/projects/p/source-documents/v/assets/clip');
    assert.equal(reads[1].options.redirect,'error');assert.equal(video.src,'blob:video');
    assert.equal(video.controls,true);assert.equal(video.currentTime,0);
    video.readyState=1;for(const handler of events.get('loadedmetadata')||[])handler();
    assert.equal(video.currentTime,4.2);
    session.dispose();assert.deepEqual(revoked,['blob:video']);assert.equal(pauses,1);assert.equal(loads,1);
    assert.equal(reads[0].options.signal.aborted,true);
  }finally{session.dispose();globalThis.location=previous;}
});
