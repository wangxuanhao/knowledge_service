const test=require('node:test');
const assert=require('node:assert/strict');
const catalog=require('../../knowledge_service/web/ontology-model-catalog.js');
test('SPARQL uses explicit publication and never inherits hidden page filters',()=>{
  const body=catalog.sparqlRequest({ontologyId:'publication-1'},'SELECT ?s WHERE { ?s ?p ?o } LIMIT 20');
  assert.equal(body.ontology_id,'publication-1');
  assert.equal(body.filters,undefined);
  assert.equal(body.known_at,undefined);
});
test('SPARQL rejects empty input before a request is submitted',()=>{
  assert.throws(()=>catalog.sparqlRequest({},'   '),/查询/);
});
