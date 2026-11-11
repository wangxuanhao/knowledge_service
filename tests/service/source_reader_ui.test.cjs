const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../../knowledge_service/web/source-reader.js');
test('OCR overlay normalizes bounded boxes, excludes invalid values',()=>{
  assert.deepEqual(ui.boxStyle({x:.1,y:.2,width:.3,height:.4}),{left:'10%',top:'20%',width:'30%',height:'40%'});
  assert.equal(ui.boxStyle({x:-1,y:0,width:.2,height:.2}),null);
  assert.equal(ui.boxStyle({x:0,y:0,width:NaN,height:.2}),null);
  assert.equal(ui.boxStyle({x:.9,y:0,width:.2,height:.2}),null);
});
test('source reader links escape project/version/asset identifiers',()=>{
  assert.equal(ui.sourceUrl('p/a','v?x','assets/a b'),'/api/projects/p%2Fa/source-documents/v%3Fx/assets/a b');
});
test('table spans refer to data rows without including the header',()=>{
  assert.deepEqual(ui.tableRowRange({table_start_row:3,table_end_row:7},10),[3,7]);
  assert.equal(ui.tableRowRange({table_start_row:-1,table_end_row:7},10),null);
  assert.equal(ui.tableRowRange({table_start_row:3,table_end_row:11},10),null);
});

test('stored code-point offsets select the entity after an emoji in DOM UTF-16',()=>{
  const text='开头😀《电子商务法》';
  const range=ui.characterRange(text,3,10);
  assert.deepEqual(range,[4,11]);
  assert.equal(text.slice(...range),'《电子商务法》');
  assert.equal(ui.characterRange(text,-1,3),null);
  assert.equal(ui.characterRange(text,0,11),null);
});

test('OCR coverage remains partial with actionable failed tile regions',()=>{
  const run={status:'partial',processing_details:{tiles_total:3,tiles_completed:2,tiles:[
    {index:0,status:'completed',region:{x:0,y:0,width:1,height:.35}},
    {index:1,status:'failed',region:{x:0,y:.3,width:1,height:.4},public_error:'中段失败'},
    {index:2,status:'completed',region:{x:0,y:.65,width:1,height:.35}}]}};
  const progress=ui.ocrProgress(run);
  assert.equal(progress.label,'部分完成');assert.equal(progress.completed,2);assert.equal(progress.total,3);
  assert.equal(progress.incomplete,true);assert.equal(progress.failed.length,1);
  assert.equal(progress.failed[0].index,1);
  assert.deepEqual(progress.failed[0].region,{x:0,y:.3,width:1,height:.4});
  assert.equal(ui.ocrProgress({...run,status:'completed'}).incomplete,true,'incomplete coverage never becomes complete success');
  assert.equal(ui.ocrProgress({status:'completed'}).incomplete,false);
  assert.equal(ui.ocrProgress({...run,status:'ignored'}).incomplete,false,'an explicit ignore preserves its own acknowledgement state');
});

test('persisted OCR tiles convert EXIF-oriented original pixel bounds into exact focus regions',()=>{
  const progress=ui.ocrProgress({status:'partial',processing_details:{width:1080,height:20000,tiles_total:2,tiles_completed:1,
    tiles:[{id:'tile-000001',ordinal:0,status:'completed',region:[0,0,1080,2200]},
      {id:'tile-000002',ordinal:1,status:'failed',region:[0,2000,1080,4400],error:{code:'ocr_failed',message:'中段失败'}}]}});
  assert.deepEqual(progress.failed[0].region,{x:0,y:.1,width:1,height:.12});
  assert.equal(progress.failed[0].index,1);assert.equal(progress.failed[0].message,'中段失败');
  assert.equal(ui.ocrProgress({status:'processing',processing_details:{tiles_total:2,tiles_completed:0}}).label,'识别中');
});

test('PDF evidence selects the frozen asset page and preserves explicit page locations',()=>{
  const doc={assets:[{id:'page-image',provenance:{page_no:4}}]};
  assert.equal(ui.pdfPage(doc,{asset_id:'page-image',boxes:[{x:0,y:.8,width:1,height:.1}]}),4);
  assert.equal(ui.pdfPage(doc,{spans:[{payload:{page_no:2}}]}),2);
  assert.equal(ui.pdfPage(doc,{page_number:3,asset_id:'page-image'}),3);
  assert.equal(ui.pdfPage(doc,{page_number:-1}),1);
});

test('PDF evidence resolves ordinary Docling picture locations and clipped native spans before broad page lists',()=>{
  const doc={assets:[{id:'picture',provenance:{locations:[{page_no:4,bbox:{l:0,t:100,r:200,b:300}}]}}],
    blocks:[{block_id:'native-late',payload:{source_locations:[{page_no:3,bbox:{l:0,t:0,r:100,b:50}}]}}]};
  assert.equal(ui.pdfPage(doc,{asset_id:'picture'}),4,'ordinary PDF pictures retain their nested page');
  assert.equal(ui.pdfPage(doc,{page_numbers:[1,3],spans:[{block_id:'native-late'}]}),3,'clipped native block beats the broader multi-page chunk');
  assert.equal(ui.pdfPage(doc,{page_numbers:[1,3,5],spans:[{block_id:'native-late',payload:{source_locations:[{page_no:5}]}}]}),5,'clipped span source locations beat its original block');
  assert.equal(ui.pdfPage(doc,{page_numbers:[1,4],spans:[{asset_id:'picture'}]}),4,'matched image location beats the broader page list');
  assert.equal(ui.pdfPage(doc,{page_numbers:[6]}),6,'locate page numbers remain a fallback when no span location exists');
});

for(const outcome of ['success','failure','stale'])test('OCR retry '+outcome+' reports recognition separately from document reprocessing',async()=>{
  const source=require('node:fs').readFileSync('knowledge_service/web/source-reader.js','utf8');
  const start=source.indexOf("const retry=make('button',progress.incomplete?");
  const handler=source.slice(start,source.indexOf("const ignore=make('button'",start));
  const messages=[],views=[],context={progress:{incomplete:true},projectId:'p',versionId:'v',asset:{id:'image'},sourceUrl:ui.sourceUrl,
    make:()=>({}),accepts:()=>outcome!=='stale',status:(...args)=>messages.push(args),show:async view=>views.push(view),
    api:async()=>{if(outcome==='failure')throw Error('重试失败');}};
  require('node:vm').runInNewContext(handler+';globalThis.retry=retry;',context);
  await context.retry.onclick();
  if(outcome==='success'){
    assert.deepEqual(messages,[['识别结果已更新；如需更新检索或图谱，请在资料详情重新处理']]);
    assert.deepEqual(views,['parsed']);
  }else if(outcome==='failure'){
    assert.deepEqual(messages,[['重试失败',true]]);assert.deepEqual(views,[]);assert.equal(context.retry.disabled,false);
  }else{assert.deepEqual(messages,[]);assert.deepEqual(views,[]);}
});
