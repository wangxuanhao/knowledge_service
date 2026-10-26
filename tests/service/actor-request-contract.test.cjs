const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const web=path.resolve(__dirname,'../../knowledge_service/web');
test('manual ontology review leaves the audit actor to the server',async()=>{
  const source=fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8');
  const code=source.slice(source.indexOf('  async function openManualReview()'),source.indexOf('  async function validateAndReview()'));
  const nodes=[],payloads=[];
  function el(tag,className,textContent){
    const node={tag,className,textContent,children:[],listeners:{},value:'',append(...children){this.children.push(...children);},
      setAttribute(){},addEventListener(name,fn){this.listeners[name]=fn;},showModal(){},close(){}};
    nodes.push(node);return node;
  }
  const draft={id:'draft',revision:2,submitted_at:'now',base_ontology_id:'ontology',operations:[]};
  const model={S:{draft,summary:{classes:[]}},actorName:()=> 'obsolete-client-actor',refresh:async()=>{}};
  const context={current:'project',el,model:()=>model,status(){},labelOfIri:iri=>iri,
    document:{querySelector:()=>null,getElementById:()=>null,body:el('body')},
    endpoint:suffix=>'/api/projects/project'+suffix,
    api:async(url,payload)=>{
      if(url.endsWith('/review-plan'))return {validation_fingerprint:'fingerprint',items:[{
        action:'create_term',target_iri:'urn:A',operation_id:'operation',operation_fingerprint:'operation-fingerprint',approvable:true}]};
      if(url.endsWith('/decisions'))payloads.push(payload);
      return draft;
    }};
  vm.runInNewContext(code,context);
  await context.openManualReview();
  nodes.find(node=>node.textContent==='收下').listeners.click();
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(payloads.length,1);
  assert.equal(Object.hasOwn(payloads[0],'actor'),false);
  assert.equal(payloads[0].expected_revision,2);
  assert.equal(payloads[0].decisions[0].action,'approve');
});

test('ontology panel cache version includes the canonical actor contract',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.ok(html.includes('/assets/ontology-model-panel.js?v=draft-workspace-20261023-2'));
});
