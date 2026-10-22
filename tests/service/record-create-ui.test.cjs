const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function fixture(options={}){
  const nodes=new Map(),calls=[],events=[],listeners={};let writer=true;
  class Element{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.className='';this.open=false;this.disabled=false;this.textContent='';this._value='';this.classList={add(){},remove(){}};}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    set value(value){this._value=value;}get value(){return this._value||(this.tagName==='select'?this.children[0]?.value||'':'');}
    append(...children){this.children.push(...children);}replaceChildren(...children){this.children=[...children];}
    setAttribute(name,value){this[name]=value;}addEventListener(name,fn){(this.events??={})[name]=fn;}
    showModal(){this.open=true;}close(){this.open=false;}remove(){this.removed=true;}click(){return this.onclick?.();}
  }
  const body=new Element('body'),project=new Element('select');project.id='project';
  const publication={id:'pub-2',summary:{classes:[{id:'Employee',label_zh:'员工'}],relations:[{id:'knows',domain:[],range:[]}],
    attributes:[{id:'enabled',label_zh:'启用',range:['http://www.w3.org/2001/XMLSchema#boolean']}]}};
  const context={current:'p',document:{body,createElement:tag=>new Element(tag),getElementById:id=>nodes.get(id),
    addEventListener(name,fn){listeners[name]=fn;},dispatchEvent(event){events.push(event);}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},Auth:{isAdmin:()=>writer},
    RecordAttributeEditor:require('../../knowledge_service/web/record-attribute-editor.js'),scope:()=>({filters:{field:'city',op:'eq',value:'上海'}}),
    status(){},showTab(name){context.openedTab=name;},api:async(url,body,method)=>{
      calls.push({url,body,method});if(url.endsWith('/ontology')){if(options.noOntology)throw Error('本体未发布');return publication;}
      if(url.endsWith('/records/page'))return body.cursor?{records:[{id:'entity-150001',kind:'entity',text:'尾部实体'}],total:150001,next_cursor:null}
        :{records:[{id:'entity-1',kind:'entity',text:'首个实体'}],total:150001,next_cursor:'next-50'};
      if(url.endsWith('/records'))return options.submit?options.submit(body):{records:[{...body.records[0],id:'created',version:1}]};
      throw Error('unexpected '+url);
    }};
  context.window=context;vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/record-create.js'),'utf8'),context);
  return {context,nodes,calls,events,publication,setWriter:value=>writer=value};
}
function find(node,predicate){if(predicate(node))return node;for(const child of node.children||[]){const result=find(child,predicate);if(result)return result;}}
test('entity form submits metadata and latest publication through the formal API and prevents repeated writes',async()=>{
  let finish;const harness=fixture({submit:body=>new Promise(resolve=>{finish=()=>resolve({records:[{...body.records[0],id:'saved'}]});})});
  await harness.context.RecordCreate.open('entity');
  const get=id=>harness.nodes.get(id),form=get('record-create-dialog').children.find(child=>child.tagName==='form');
  get('create-record-text').value='新员工';get('create-record-metadata').value='{"city":"上海"}';
  const first=form.onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
  await form.onsubmit({preventDefault(){}});assert.equal(harness.calls.filter(call=>call.url.endsWith('/records')).length,1);
  finish();await first;
  const write=harness.calls.find(call=>call.url.endsWith('/records'));assert.equal(write.body.records[0].type,'Employee');
  assert.equal(write.body.records[0].ontology_id,'pub-2');assert.equal(write.body.records[0].metadata.city,'上海');
  assert.equal(harness.events[0].type,'records:created');assert.equal(get('save-created-record').disabled,true);
});
test('relationship form reaches a late server page and creates the selected independent relationship',async()=>{
  const harness=fixture();await harness.context.RecordCreate.open('relation',{subject:{id:'subject',text:'起点'}});
  const dialog=harness.nodes.get('record-create-dialog'),pickers=[];
  function collect(node){if(node.className==='record-entity-picker')pickers.push(node);for(const child of node.children||[])collect(child);}collect(dialog);
  const target=pickers[1],tools=find(target,node=>node.className==='record-entity-tools');tools.children[0].value='实体';
  await tools.children[1].click();await new Promise(resolve=>setImmediate(resolve));
  const pages=find(target,node=>node.className==='record-entity-pages');await pages.children[1].click();await new Promise(resolve=>setImmediate(resolve));
  await find(target,node=>node.className==='record-entity-results').children[0].click();
  harness.nodes.get('create-record-text').value='起点认识尾部';
  await dialog.children.find(child=>child.tagName==='form').onsubmit({preventDefault(){}});
  const write=harness.calls.find(call=>call.url.endsWith('/records'));
  assert.equal(write.body.records[0].subject_id,'subject');assert.equal(write.body.records[0].object_id,'entity-150001');
  assert.ok(harness.calls.some(call=>call.body?.cursor==='next-50'));
});
test('attribute form preserves false as a native boolean with the declared datatype',async()=>{
  const harness=fixture();await harness.context.RecordCreate.open('attribute',{subject:{id:'subject',text:'员工'}});
  harness.nodes.get('create-record-value').value='false';
  await harness.nodes.get('record-create-dialog').children.find(child=>child.tagName==='form').onsubmit({preventDefault(){}});
  const record=harness.calls.find(call=>call.url.endsWith('/records')).body.records[0];
  assert.equal(record.kind,'attribute');assert.equal(record.value,false);assert.match(record.datatype,/#boolean$/);
});
test('viewer and unpublished ontology never expose an executable create form',async()=>{
  const viewer=fixture();viewer.setWriter(false);await viewer.context.RecordCreate.open('entity');
  assert.equal(viewer.nodes.has('record-create-dialog'),false);assert.equal(viewer.calls.length,0);
  const empty=fixture({noOntology:true});await empty.context.RecordCreate.open('entity');
  assert.equal(empty.nodes.has('save-created-record'),false);
  const link=find(empty.nodes.get('record-create-dialog'),node=>node.textContent==='前往本体建模层');link.click();assert.equal(empty.context.openedTab,'ontology-model');
});
