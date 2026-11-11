const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/source-content.js');
const content=()=>{assert.ok(fs.existsSync(file),'shared source content module exists');return require(file);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('asset URL accepts only the exact same-origin project and frozen version endpoint',()=>{
  const {validatedAssetUrl}=content(),base='https://reader.test';
  const valid='/api/projects/p%2Fa/source-documents/v%3Fx/assets/image-1';
  assert.equal(validatedAssetUrl(valid,'p/a','v?x',base),valid);
  assert.equal(validatedAssetUrl(base+valid,'p/a','v?x',base),valid);
  for(const url of ['https://outside.test'+valid,'//outside.test'+valid,'data:image/png;base64,AA',
    'javascript:alert(1)',valid+'?download=1',valid+'#x',valid+'/ocr',valid+'%2Focr',valid+'%5Cother',
    '/api/projects/other/source-documents/v%3Fx/assets/image-1',
    '/api/projects/p%2Fa/source-documents/other/assets/image-1',valid+'/../other',
    'https://user:pass@reader.test'+valid])assert.equal(validatedAssetUrl(url,'p/a','v?x',base),null,url);
});

test('disposing an image scope aborts queued work and revokes all owned blob URLs',async()=>{
  const {createResourceScope}=content(),revoked=[],pending=deferred(),signals=[];
  const scope=createResourceScope({fetcher:async(url,{signal})=>{signals.push(signal);return pending.promise;},urlApi:{createObjectURL:()=> 'blob:owned',revokeObjectURL:url=>revoked.push(url)}});
  const task=scope.loadBlob('/asset',()=>assert.fail('late image must never be assigned'));
  await settle();scope.dispose();assert.equal(signals[0].aborted,true);
  pending.resolve({ok:true,blob:async()=>({})});await task;
  assert.equal(scope.active(),false);assert.deepEqual(revoked,[],'stale bytes do not create a URL');
  assert.equal(scope.objectUrl({}),null);
});

test('invalidation during URL creation releases a late blob without assigning it',async()=>{
  const {createResourceScope}=content(),revoked=[];let current=true;
  const scope=createResourceScope({isCurrent:()=>current,fetcher:async()=>({ok:true,blob:async()=>({})}),urlApi:{createObjectURL:()=>{current=false;return 'blob:late';},revokeObjectURL:url=>revoked.push(url)}});
  await scope.loadBlob('/asset',()=>assert.fail('late URL cannot reach an image'));
  assert.deepEqual(revoked,['blob:late']);scope.dispose();assert.deepEqual(revoked,['blob:late']);
});

test('image fetching is bounded and disposing drains queued jobs without starting them',async()=>{
  const {createResourceScope}=content(),reads=[];
  const scope=createResourceScope({maxConcurrent:2,fetcher:async(url)=>{const wait=deferred();reads.push({url,...wait});return wait.promise;},urlApi:{createObjectURL:()=> 'blob:image',revokeObjectURL:()=>{}}});
  const jobs=Array.from({length:7},(_,i)=>scope.loadBlob('/asset/'+i,()=>{}));
  await settle();assert.equal(reads.length,2);
  reads[0].resolve({ok:true,blob:async()=>({})});await settle();assert.equal(reads.length,3);
  scope.dispose();for(const read of reads.slice(1))read.resolve({ok:true,blob:async()=>({})});await Promise.all(jobs);
  assert.equal(reads.length,3);
});

test('a shared owner tracks downloads and releases each URL once, including repeated disposal',async()=>{
  const {createResourceScope}=content(),revoked=[];let next=0;
  const scope=createResourceScope({urlApi:{createObjectURL:()=> 'blob:'+ ++next,revokeObjectURL:url=>revoked.push(url)}});
  const one=scope.objectUrl({}),two=scope.objectUrl({});scope.release(one);scope.release(one);scope.dispose();scope.dispose();
  assert.deepEqual(revoked,[one,two]);
});

test('binary and ZIP-backed Markdown downloads retain distinct source/package names',()=>{
  const {downloadOptions}=content();
  assert.deepEqual(downloadOptions({original_name:'guide.md',format:'markdown',mime_type:'application/zip'}),[
    {suffix:'source',label:'下载当前 Markdown',filename:'guide.md'},
    {suffix:'package',label:'下载原始 ZIP 知识包',filename:'guide.zip'}]);
  assert.deepEqual(downloadOptions({original_name:'guide.pdf',format:'pdf',mime_type:'application/pdf'}),[
    {suffix:'source',label:'下载固定版本原文件',filename:'guide.pdf'}]);
  assert.equal(downloadOptions({source_file:'rule.md',source_format:'md'})[0].filename,'rule.md');
});

test('ZIP primary provenance owns source names and MIME while the complete package keeps its ZIP name',()=>{
  const {downloadOptions,isPdfSource}=content();
  const packaged={original_name:'资料包.zip',format:'zip',mime_type:'application/zip',parser_metadata:{provenance:{package:{
    entry_path:'规则/规则原件.pdf',filename:'规则原件.pdf',mime_type:'application/pdf',sha256:'a'.repeat(64)}}}};
  assert.equal(isPdfSource(packaged),true);
  assert.deepEqual(downloadOptions(packaged),[
    {suffix:'source',label:'下载当前资料原件',filename:'规则原件.pdf'},
    {suffix:'package',label:'下载原始 ZIP 知识包',filename:'资料包.zip'}]);
  const image={...packaged,parser_metadata:{provenance:{package:{entry_path:'assets/截图.png',filename:'截图.png',mime_type:'image/png'}}}};
  assert.equal(downloadOptions(image)[0].filename,'截图.png');
});

test('raw source view distinguishes primary binary bytes from a readable image-page JSON manifest',()=>{
  const {sourceInfo,downloadOptions}=content();
  assert.equal(typeof sourceInfo,'function');
  const packaged=(filename,mime_type)=>({original_name:'pages.zip',format:'zip',mime_type:'application/zip',parser_metadata:{provenance:{package:{entry_path:filename,filename,mime_type}}}});
  for(const [filename,mime_type] of [['文档.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],['截图.webp','image/webp'],['讲解.webm','video/webm'],['规则.pdf','application/pdf']]){
    const info=sourceInfo(packaged(filename,mime_type));assert.equal(info.binary,true,filename);
    assert.equal(info.filename,filename);assert.match(info.rawExplanation,/解析后的正文/);
  }
  const pages=packaged('manifest.json','application/json'),info=sourceInfo(pages);
  assert.equal(info.binary,false);assert.equal(info.isPageManifest,true);
  assert.match(info.rawExplanation,/图片页序清单.*JSON/);assert.doesNotMatch(info.rawExplanation,/Markdown/);
  assert.deepEqual(downloadOptions(pages),[
    {suffix:'source',label:'下载图片页序清单',filename:'manifest.json'},
    {suffix:'package',label:'下载原始 ZIP 知识包',filename:'pages.zip'}]);
});

test('entrypoint loads the shared content renderer before both reader entry points',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/index.html'),'utf8');
  assert.match(html,/source-content\.js\?v=[\w.-]+/);
  assert.ok(html.indexOf('source-content.js')<html.indexOf('source-reader.js'));
  assert.ok(html.indexOf('source-content.js')<html.indexOf('sources-view.js'));
});
