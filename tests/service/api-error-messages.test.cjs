const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const declaration=fs.readFileSync('knowledge_service/web/app.js','utf8').match(/async function api\([^\n]+\)\{[\s\S]*?\n\}/)[0];
test('structured upload errors expose the actionable message instead of raw JSON',async()=>{
  const context={fetch:async()=>({ok:false,status:400,text:async()=>JSON.stringify({detail:{code:'invalid_package',message:'此 ZIP 包含 3 份资料，请拆分后上传。'}})})};
  vm.runInNewContext(declaration+';this.request=api;',context);
  await assert.rejects(context.request('/preview'),error=>{
    assert.equal(error.message,'此 ZIP 包含 3 份资料，请拆分后上传。');
    assert.equal(error.code,'invalid_package');assert.equal(error.status,400);return true;
  });
});
