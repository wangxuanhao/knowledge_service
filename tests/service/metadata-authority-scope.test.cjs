const {test}=require('node:test');
const assert=require('node:assert/strict');
const fields=require('../../knowledge_service/web/scope-fields.js');

test('current metadata AND/OR reaches authority eligibility without rewriting conditions',()=>{
  for(const mode of ['keyword','semantic','hybrid']){
    const request={retrieval_mode:mode,filters:{or:[{field:'metadata.region.city',op:'eq',value:'北京'},
      {and:[{field:'id',op:'eq',value:'a'},{field:'metadata.amount',op:'gt',value:100}]}]}};
    const before=JSON.stringify(request);
    assert.equal(fields.requireRetrievalScope(request),request);
    assert.equal(JSON.stringify(request),before);
  }
});

test('current retrieval and QA expose business metadata only on content pages',()=>{
  for(const page of ['search','qa'])assert.equal(fields.usesPostgresFilters({page,purpose:'retrieval',retrievalMode:'hybrid'}),true);
  for(const page of ['projects','users','storage'])assert.equal(fields.usesPostgresFilters({page,purpose:'retrieval'}),false);
});

test('unsupported NOT and malformed metadata remain rejected even in historical keyword',()=>{
  const body={known_at:'2025-01-01',retrieval_mode:'keyword',filters:{not:{field:'metadata.region',op:'eq',value:'北京'}}};
  assert.throws(()=>fields.requireRetrievalScope(body),/NOT/);
  assert.throws(()=>fields.requireRetrievalScope({filters:{field:'metadata.region',op:'exists',value:'false'}}),/过滤器/);
});
