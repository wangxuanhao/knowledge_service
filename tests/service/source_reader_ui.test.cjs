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
