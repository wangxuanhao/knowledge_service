const {test}=require('node:test');
const assert=require('node:assert/strict');
const {shouldPoll}=require('../../knowledge_service/web/search-index-config.js');
test('index status follows automatic work only while visible',()=>{
  assert.equal(shouldPoll({open:true,hidden:false,state:'building'}),true);
  for(const state of ['not_built','failed','requires_rebuild','leased'])assert.equal(shouldPoll({open:true,hidden:false,state}),true);
  assert.equal(shouldPoll({open:true,hidden:false,state:'ready'}),false);
  assert.equal(shouldPoll({open:false,hidden:false,state:'building'}),false);
  assert.equal(shouldPoll({open:true,hidden:true,state:'building'}),false);
});
