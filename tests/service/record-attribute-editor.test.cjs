const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/record-attribute-editor.js');
const editor=fs.existsSync(file)?require(file):{};
const xsd='http://www.w3.org/2001/XMLSchema#';

test('attribute editor exposes typed value parser',()=>{
  assert.equal(typeof editor.parseValue,'function');
});
test('booleans require true or false and preserve false',()=>{
  assert.equal(editor.parseValue('boolean','false').value,false);
  assert.equal(editor.parseValue('boolean','true').datatype,xsd+'boolean');
  for(const invalid of ['yes','0','','False'])assert.throws(()=>editor.parseValue('boolean',invalid),/true.*false/);
});
test('numeric values reject blank, nonfinite, fractional integers and precision loss',()=>{
  assert.deepEqual(editor.parseValue('integer','0'),{value:0,datatype:xsd+'integer'});
  assert.deepEqual(editor.parseValue('double','2.5'),{value:2.5,datatype:xsd+'double'});
  assert.deepEqual(editor.parseValue('double','2.0'),{value:2,datatype:xsd+'integer'});
  for(const invalid of ['',' ','Infinity','NaN','0x10','1e999'])assert.throws(()=>editor.parseValue('double',invalid));
  for(const invalid of ['1.2','9007199254740993'])assert.throws(()=>editor.parseValue('integer',invalid));
});
test('date and JSON lexical modes validate while respecting string-only backend contract',()=>{
  assert.deepEqual(editor.parseValue('date','2024-02-29'),{value:'2024-02-29',datatype:xsd+'string'});
  for(const invalid of ['2023-02-29','2024-13-01','2024-04-31','2024-2-1'])assert.throws(()=>editor.parseValue('date',invalid));
  assert.equal(editor.parseValue('dateTime','2024-02-29T12:30:00Z').datatype,xsd+'string');
  assert.throws(()=>editor.parseValue('dateTime','2023-02-29T12:30:00Z'));
  assert.equal(editor.parseValue('json','{"enabled":false}').value,'{"enabled":false}');
  assert.throws(()=>editor.parseValue('json','{broken}'));
});
test('attribute revision keeps binding, full writable payload, unknown current subject and expected revision',()=>{
  const row={id:'a',kind:'attribute',text:'old',type:'t',subject_id:'not-in-page',ontology_id:'historical',
    value:false,datatype:xsd+'boolean',version:7,version_id:'v7',metadata:{platform:'A'},properties:{a:1},source_id:'d',object_id:null,
    valid_from:'2024-01-01',valid_until:null};
  const before=JSON.stringify(row);
  const record=editor.buildRecord(row,{text:'new',type:'t2',subject_id:'not-in-page',mode:'boolean',value:'false',valid_from:'2024-01-01',valid_until:''});
  assert.deepEqual(record,{id:'a',kind:'attribute',text:'new',type:'t2',subject_id:'not-in-page',ontology_id:'historical',
    value:false,datatype:xsd+'boolean',metadata:{platform:'A'},properties:{a:1},source_id:'d',object_id:null,valid_from:'2024-01-01',valid_until:null});
  assert.equal(row.version,7);assert.equal(JSON.stringify(row),before);
  assert.throws(()=>editor.buildRecord(row,{text:'new',type:'t',subject_id:'',mode:'string',value:'x'}),/所属实体/);
});
test('business validity dates must be real and ordered',()=>{
  const row={id:'a',kind:'attribute',ontology_id:'o'};
  const fields={text:'value',type:'t',subject_id:'e',mode:'string',value:'x',valid_from:'2024-02-29',valid_until:'2024-03-01'};
  assert.equal(editor.buildRecord(row,fields).valid_from,'2024-02-29');
  assert.throws(()=>editor.buildRecord(row,{...fields,valid_from:'2023-02-29'}),/时间/);
  assert.throws(()=>editor.buildRecord(row,{...fields,valid_until:'2024-01-01'}),/失效时间/);
  assert.throws(()=>editor.buildRecord(row,{...fields,valid_until:fields.valid_from}),/失效时间/);
});

const vm=require('node:vm');
function dialogHarness(overrides={}){
  const elements=new Map(),calls=[];
  class Element{
    constructor(id=''){this.id=id;this.value='';this.dataset={};this.classList={add(){},remove(){},toggle(){}};this.open=false;this.disabled=false;}
    set innerHTML(html){
      this.html=html;
      for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){
        const child=new Element(match[2]);elements.set(child.id,child);
        child.value=/\bvalue="([^"]*)"/.exec(match[0])?.[1]||'';
        if(match[1]==='select'){
          const content=html.slice(match.index+match[0].length).split('</select>')[0];
          const options=[...content.matchAll(/<option value="([^"]*)"( selected)?/g)];
          child.value=(options.find(option=>option[2])||options[0])?.[1]||'';
        }
      }
    }
    get innerHTML(){return this.html||'';}
    setAttribute(){} appendChild(child){if(child.id)elements.set(child.id,child);return child;}
    append(){} after(){} addEventListener(){} closest(){return panel;}
    querySelector(selector){return selector==='.record-dialog-heading'?new Element():null;}
    showModal(){this.open=true;} close(){this.open=false;}
  }
  const panel=new Element(),body=new Element();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
  const fixture={ontology:{id:'historical',summary:{attributes:[{id:'t',label_zh:'状态'}]}},records:{records:[{id:'e',text:'Visible entity'}],total:1500},...overrides};
  const context={document:{createElement:()=>new Element(),body},$:get,current:'p',esc:String,labelOf:String,status(){},
    wb:{nodes:new Map(),records:new Map()},RecordAttributeEditor:editor,
    RecordVersionContext:{build:()=>({byId:new Map([['historical',{}]])}),summary:()=>''},
    endpoint:p=>'/p'+p,api:async(url,data,method)=>{
      calls.push({url,data,method});
      if(method==='PUT')return fixture.save?fixture.save(data):{...data.record,version:data.expected_version+1,version_id:'new'};
      if(url.endsWith('/ontologies'))return {versions:[{id:'historical'}]};
      if(url.includes('/ontology?'))return fixture.ontology;
      if(url.includes('/records/query'))return fixture.records;
    },scope:()=>({filters:{platform:'A'},known_at:null}),editRecord:null,historyFor:null,
  };
  context.window=context;vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/record-dialog.js'),'utf8'),context);
  return {context,get,calls,fixture,async open(row={}){
    context.editRecord({id:'a',kind:'attribute',text:'Status',subject_id:'unloaded',type:'t',value:false,datatype:xsd+'boolean',ontology_id:'historical',version:7,version_id:'v7',...row});
    await new Promise(resolve=>setImmediate(resolve));
  }};
}
test('dialog uses bound attribute types and scoped entity choices with visible page limitation',async()=>{
  const harness=dialogHarness();await harness.open();
  assert.ok(harness.get('record-structured-edit').innerHTML.includes('维护实体属性'));
  assert.ok(harness.get('record-structured-edit').innerHTML.includes('最多 1000'));
  assert.equal(harness.get('structured-subject').value,'unloaded');
  assert.equal(harness.get('structured-record-type').value,'t');
  assert.equal(harness.calls.find(call=>call.url.includes('/records/query')).data.filters.platform,'A');
});
test('attribute dialog saves booleans, prevents duplicate in-flight submits and advances expected version',async()=>{
  let resolveSave;const harness=dialogHarness({save:data=>new Promise(resolve=>{resolveSave=()=>resolve({...data.record,version:data.expected_version+1,version_id:'new'});})});
  await harness.open();
  const button=harness.get('save-structured-record');assert.equal(typeof button.onclick,'function');
  const first=button.onclick();const second=button.onclick();
  assert.equal(harness.calls.filter(call=>call.method==='PUT').length,1);
  resolveSave();await Promise.all([first,second]);
  const save=harness.calls.find(call=>call.method==='PUT');
  assert.equal(save.data.record.value,false);assert.equal(save.data.record.subject_id,'unloaded');
  assert.equal(save.data.expected_version,7);assert.equal(save.data.record.ontology_id,'historical');
  assert.equal(harness.get('revision-version').value,8);
});

test('repeated attribute saves use the latest expected version',async()=>{
  const harness=dialogHarness();await harness.open();
  await harness.get('save-structured-record').onclick();
  await harness.get('save-structured-record').onclick();
  assert.deepEqual(harness.calls.filter(call=>call.method==='PUT').map(call=>call.data.expected_version),[7,8]);
});
test('save failure is visible and enables correction without discarding form values',async()=>{
  const harness=dialogHarness({save:async()=>{throw Error('version conflict');}});await harness.open();
  const button=harness.get('save-structured-record');await button.onclick();
  assert.equal(harness.get('record-dialog-message').textContent,'version conflict');
  assert.equal(button.disabled,false);assert.equal(harness.get('structured-value').value,'false');
});
test('malformed typed value never reaches the revision endpoint',async()=>{
  const harness=dialogHarness();await harness.open();
  harness.get('structured-value').value='not boolean';await harness.get('save-structured-record').onclick();
  assert.equal(harness.calls.filter(call=>call.method==='PUT').length,0);
  assert.match(harness.get('record-dialog-message').textContent,/true.*false/);
});
test('scope change after options load blocks a stale form submission',async()=>{
  const harness=dialogHarness();await harness.open();harness.context.scope=()=>({filters:{platform:'B'}});
  await harness.get('save-structured-record').onclick();
  assert.equal(harness.calls.filter(call=>call.method==='PUT').length,0);
  assert.match(harness.get('record-dialog-message').textContent,/范围已变化/);
});
test('wrong bound ontology response blocks type choices and leaves advanced editor available',async()=>{
  const harness=dialogHarness({ontology:{id:'latest',summary:{attributes:[{id:'new'}]}}});await harness.open();
  assert.match(harness.get('record-structured-edit').innerHTML,/归属不一致/);
  assert.equal(harness.get('save-structured-record').onclick,undefined);
  assert.equal(harness.get('record-advanced-edit').hidden,false);
});
test('a response from a previous project cannot replace the active attribute editor',async()=>{
  let release;const harness=dialogHarness({save:data=>new Promise(resolve=>{release=()=>resolve({...data.record,version:8,version_id:'saved'});})});
  await harness.open();const pending=harness.get('save-structured-record').onclick();
  harness.context.current='other';release();await pending;
  assert.equal(harness.get('revision-version').value,7);
  assert.equal(harness.context.wb.records.size,0);
});
