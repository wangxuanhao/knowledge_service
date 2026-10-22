const {test}=require('node:test');
const assert=require('node:assert/strict');
const {shouldPoll}=require('../../knowledge_service/web/search-index-config.js');
test('project vector status polls only during an open visible build',()=>{
  assert.equal(shouldPoll({open:true,hidden:false,state:'building'}),true);
  for(const state of ['not_built','ready','failed','requires_rebuild'])assert.equal(shouldPoll({open:true,hidden:false,state}),false);
  assert.equal(shouldPoll({open:false,hidden:false,state:'building'}),false);
  assert.equal(shouldPoll({open:true,hidden:true,state:'building'}),false);
});
