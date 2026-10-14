const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../../knowledge_service/web/ingest-batches.js');

test('upload pool bounds concurrency and retains individual failures',async()=>{
  let active=0,peak=0;
  const result=await ui.pool([0,1,2,3,4,5,6],3,async i=>{
    peak=Math.max(peak,++active);
    await new Promise(resolve=>setTimeout(resolve,5));active--;
    if(i===3)throw Error('failed');return i;
  });
  assert.equal(peak,3);assert.equal(result.filter(x=>x.status==='fulfilled').length,6);
  assert.equal(result[3].status,'rejected');
});
test('retry labels distinguish whole document and slice resume',()=>{
  assert.equal(ui.retryLabels.resume_checkpoint,'从断点继续');
  assert.equal(ui.retryLabels.retry_failed_chunks,'只重试失败切片');
  assert.equal(ui.retryLabels.restart_document,'整份重新处理');
});
test('failure CSV quotes formulas, separators and newlines',()=>{
  const csv=ui.failureCsv([{original_name:'=SUM(1,2)',public_error:'a\nb',error_code:'llm_timeout'}]);
  assert.match(csv,/"'=SUM\(1,2\)"/);assert.match(csv,/"a\nb"/);
});
