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
