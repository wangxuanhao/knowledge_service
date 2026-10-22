const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/workbench.js'),'utf8');
const submitSource=source.slice(source.indexOf("bind('ingest',async()=>{"),source.indexOf('async function snapshots()'));

function fixture(mode,route){
  const nodes=new Map(),element=()=>({textContent:'',append(){}});
  const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  get('extraction-mode').value=mode;
  const document={getElementById:get,createElement:element};
  const payloads=[],file={name:'rule.txt'},documents=[{title:'规则',text:'原文',...(route==='text'?{}:{file})}];
  let submit;
  class FormData{
    constructor(){this.values=new Map();}
    append(key,value){this.values.set(key,value);}
    get(key){return this.values.get(key);}
  }
  const window={document,IngestOntology:{settings:()=>({}),state:()=>({release:{id:'ontology-v1'}})},
    DocumentUploadQueue:{generation:1,setStatus(){}}};
  if(route==='batch')window.IngestBatches={async submit(project,docs,settings){payloads.push(settings);return {id:'batch'};}};
  const context={window,document,FormData,current:'project',wb:{jobs:new Map()},$:get,
    bind(id,handler){assert.equal(id,'ingest');submit=handler;},controlsProject:()=> 'project',
    readParseSettings:()=>({chunk_size:1800,chunk_overlap:200}),documentUploadOptions:()=>({metadata:{}}),
    readDocumentBatch:async()=>documents,status(){},
    api:async(url,payload)=>{payloads.push(payload instanceof FormData?JSON.parse(payload.get('options')):payload);return {id:'job'};}};
  vm.runInNewContext(submitSource,context);
  return {submit,payloads};
}

for(const route of ['batch','upload','text'])for(const mode of ['ontology','discovery','documents']){
  test(`${route} submission sends only the canonical extraction mode: ${mode}`,async()=>{
    const env=fixture(mode,route);
    await env.submit();
    assert.equal(env.payloads.length,1);
    assert.equal(env.payloads[0].extraction_mode,mode);
    assert.equal(Object.hasOwn(env.payloads[0],'extract'),false);
  });
}
