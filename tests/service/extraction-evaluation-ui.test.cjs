const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workbench.js'),'utf8');

function evaluation(body,{respond=async()=>({})}={}){
  const element=(value='')=>({value,textContent:'',innerHTML:'',attributes:{},
    setAttribute(name,value){this.attributes[name]=value;},
    removeAttribute(name){delete this.attributes[name];},
    replaceChildren(){this.innerHTML='';}});
  const elements={'gold-json':element(typeof body==='string'?body:JSON.stringify(body)),
    'gold-json-error':element(),'evaluation-result':element(),'run-evaluation':element()};
  const requests=[];
  const context={
    $:id=>elements[id],
    scope:()=>({applicability:{platforms:{mode:'specific',values:['京东']}},
      ontology_scope:'ids',ontology_ids:['other'],include_unknown:true}),
    current:'project', wb:{epoch:1}, window:{DisplayTime:require('../../knowledge_service/web/display-time.js')},
    renderEvalResult:()=>'<p>评测结果</p>', esc:value=>value,
    api:async (url,payload)=>{requests.push({url,payload:JSON.parse(JSON.stringify(payload))});return respond();},
  };
  const read=source.slice(source.indexOf('function controlsProject('),source.indexOf('function discoveryHintHost('));
  const run=source.slice(source.indexOf('const evaluationInput='),source.indexOf("bind('export-project'"));
  vm.runInNewContext(read+'\n'+run,context);
  return {requests,elements,run:elements['run-evaluation'].onclick};
}

test('quality evaluation forwards the scope in the gold JSON',async()=>{
  const body={entities:['商户','平台'],relations:[['商户','onboards','平台']],
    applicability:{platforms:{mode:'specific',values:['美团']}},
    ontology_scope:'ids',ontology_ids:['release'],known_at:'2026-11-18',valid_at:'2026-11-17',
    include_unknown:false,filters:{field:'metadata.city',op:'eq',value:'上海'}};
  const page=evaluation(body);
  await page.run();
  assert.deepEqual(page.requests,[{url:'/api/projects/project/evaluate',payload:body}]);
  const result=page.elements['evaluation-result'].innerHTML;
  assert.match(result,/平台[： ]美团/);
  assert.match(result,/release/);
  assert.match(result,/<p>评测结果<\/p>/);
});

test('editing the gold input discards late scores and invalidates displayed scores',async()=>{
  let finish;
  const page=evaluation({entities:['原答案'],relations:[]},{
    respond:()=>new Promise(resolve=>{finish=resolve;})});
  const input=page.elements['gold-json'],result=page.elements['evaluation-result'];
  const request=page.run();
  input.value='{"entities":["新答案"],"relations":[]}';
  input.oninput?.();
  finish({});
  await request;
  assert.equal(result.innerHTML,'');
  const next=page.run();
  finish({});
  await next;
  assert.match(result.innerHTML,/评测结果/);
  input.value='{"entities":["又一份答案"],"relations":[]}';
  input.oninput?.();
  assert.equal(result.innerHTML,'');
});

test('invalid JSON is reported at the labelled input without making a request',async()=>{
  const page=evaluation('{');
  await page.run();
  assert.match(page.elements['gold-json-error'].textContent,/JSON 格式错误/);
  assert.equal(page.elements['gold-json'].attributes['aria-invalid'],'true');
  assert.deepEqual(page.requests,[]);
});

test('a gold set without a scope uses the evaluation endpoint defaults',async()=>{
  const body={entities:['商户'],relations:[]};
  const page=evaluation(body);
  await page.run();
  assert.deepEqual(page.requests,[{url:'/api/projects/project/evaluate',payload:body}]);
});
