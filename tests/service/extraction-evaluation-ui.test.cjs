const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workbench.js'),'utf8');

function evaluation(body){
  const input=JSON.stringify(body);
  const elements={'gold-json':{value:input},'gold-json-error':{},'evaluation-result':{}};
  const requests=[],actions={};
  const context={
    $:id=>elements[id],
    scope:()=>({applicability:{platforms:{mode:'specific',values:['京东']}},
      ontology_scope:'ids',ontology_ids:['other'],include_unknown:true}),
    current:'project', wb:{epoch:1}, window:{},
    bind:(id,handler)=>{actions[id]=handler;}, renderEvalResult:()=>'<p>评测结果</p>',
    api:async (url,payload)=>{requests.push({url,payload:JSON.parse(JSON.stringify(payload))});return {};},
  };
  const read=source.slice(source.indexOf('function controlsProject('),source.indexOf('function discoveryHintHost('));
  const run=source.slice(source.indexOf("bind('run-evaluation'"),source.indexOf("bind('export-project'"));
  vm.runInNewContext(read+'\n'+run,context);
  return {requests,elements,run:actions['run-evaluation']};
}

test('quality evaluation forwards the scope in the gold JSON',async()=>{
  const body={entities:['商户','平台'],relations:[['商户','onboards','平台']],
    applicability:{platforms:{mode:'specific',values:['美团']}},
    ontology_scope:'ids',ontology_ids:['release'],known_at:'2026-11-18',valid_at:'2026-11-17',
    include_unknown:false,filters:{field:'metadata.city',op:'eq',value:'上海'}};
  const page=evaluation(body);
  await page.run();
  assert.deepEqual(page.requests,[{url:'/api/projects/project/evaluate',payload:body}]);
  assert.equal(page.elements['evaluation-result'].innerHTML,'<p>评测结果</p>');
});

test('a gold set without a scope uses the evaluation endpoint defaults',async()=>{
  const body={entities:['商户'],relations:[]};
  const page=evaluation(body);
  await page.run();
  assert.deepEqual(page.requests,[{url:'/api/projects/project/evaluate',payload:body}]);
});
