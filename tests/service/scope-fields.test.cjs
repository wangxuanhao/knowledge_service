const {test}=require('node:test');
const assert=require('node:assert/strict');
const fields=require('../../knowledge_service/web/scope-fields.js');

test('business metadata is discoverable without exposing ingestion plumbing',()=>{
  const facets=['metadata.region.city','metadata.title','metadata.batch_id',
    'metadata.readiness.semantic_ready','metadata.source_document_version_id',
    'metadata.applicability.platforms.values'].map(field=>({field}));
  assert.deepEqual(fields.visibleFacets(facets).map(f=>f.field),['metadata.region.city','metadata.title']);
  assert.equal(fields.visibleFacets(facets,{showSystem:true}).length,5);
});

test('hiding system fields preserves already selected restrictions',()=>{
  const facets=[{field:'metadata.batch_id'},{field:'metadata.region'}];
  assert.deepEqual(fields.visibleFacets(facets,{selected:['metadata.batch_id']}).map(f=>f.field),
    ['metadata.batch_id','metadata.region']);
  assert.equal(fields.label('metadata.region.city'),'region.city');
  assert.equal(fields.label('valid_from'),'生效时间');
});

test('advanced metadata summaries count nested conditions without treating groups as leaves',()=>{
  assert.equal(fields.countConditions(null),0);
  assert.equal(fields.countConditions({and:[{field:'metadata.region',op:'eq',value:'华东'},
    {or:[{field:'metadata.score',op:'gte',value:80},{not:{field:'metadata.blocked',op:'eq',value:true}}]}]}),3);
});

test('online retrieval exposes only fixed public fields and preserves supported AST identity',()=>{
  assert.equal(typeof fields.requireRetrievalScope,'function');
  assert.deepEqual(fields.retrievalFields().map(row=>row.v),['id','kind','type','source_id','valid_from','valid_until']);
  assert.match(fields.retrievalFields().find(row=>row.v==='source_id').l,/片段.*文档/);
  const request={retrieval_mode:'hybrid',filters:{or:[{field:'id',op:'eq',value:'a'},{field:'type',op:'in',value:['Merchant']}]}};
  const before=JSON.stringify(request);
  assert.equal(fields.requireRetrievalScope(request),request);
  assert.equal(JSON.stringify(request),before);
});

test('online metadata preserves whole AND/OR predicates and still rejects unsupported NOT',()=>{
  assert.equal(typeof fields.requireRetrievalScope,'function');
  for(const field of ['metadata.region.city','region.city','metadata.id','ontology_id','metadata.applicability.platforms.values']){
    for(const filters of [{field,op:'eq',value:'x'},
      {and:[{field:'id',op:'eq',value:'a'},{or:[{field:'type',op:'eq',value:'Merchant'},{field,op:'eq',value:'x'}]}]}]){
      const request={retrieval_mode:'keyword',filters},before=JSON.stringify(request);
      assert.equal(fields.requireRetrievalScope(request),request);
      assert.equal(JSON.stringify(request),before);
    }
  }
  assert.throws(()=>fields.requireRetrievalScope({filters:{not:{field:'id',op:'eq',value:'a'}}}),/NOT/);
  assert.throws(()=>fields.requireRetrievalScope({filters:{and:[{field:'id',op:'eq',value:'a'}],field:'id',op:'eq',value:'b'}}),/过滤器/);
});

test('PG metadata capabilities depend on actual use and keep historical keyword filters intact',()=>{
  assert.equal(typeof fields.usesPostgresFilters,'function');
  for(const page of ['sources','records'])assert.equal(fields.usesPostgresFilters({page}),true);
  assert.equal(fields.usesPostgresFilters({page:'search',purpose:'graph'}),true);
  assert.equal(fields.usesPostgresFilters({page:'qa',purpose:'graph'}),true);
  assert.equal(fields.usesPostgresFilters({page:'qa',retrievalMode:'keyword',knownAt:'2025-01-01'}),true);
  assert.equal(fields.usesPostgresFilters({page:'search',retrievalMode:'semantic',knownAt:'2025-01-01'}),false);
  const body={retrieval_mode:'keyword',known_at:'2025-01-01',filters:{not:{field:'metadata.region',op:'eq',value:'华东'}}};
  assert.throws(()=>fields.requireRetrievalScope(body),/NOT/);
  assert.throws(()=>fields.requireRetrievalScope({...body,retrieval_mode:'hybrid'}),/历史/);
});
