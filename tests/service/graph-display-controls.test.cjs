const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function setup(checked=false){
  const checkbox=new EventTarget();checkbox.checked=checked;
  const button=new EventTarget();button.attributes={};button.setAttribute=(key,value)=>button.attributes[key]=value;
  const state={textContent:''};button.querySelector=()=>state;
  const document={readyState:'complete',getElementById:id=>id==='ontology-expansion'?checkbox:id==='ontology-expansion-toggle'?button:null};
  const file=path.join(__dirname,'../../knowledge_service/web/graph-display-controls.js');
  if(fs.existsSync(file))vm.runInNewContext(fs.readFileSync(file,'utf8'),{document,Event});
  return {checkbox,button,state};
}

test('ontology button changes the existing retrieval checkbox and emits its native change event',()=>{
  const {checkbox,button}=setup();let changes=0;
  checkbox.addEventListener('change',()=>changes++);
  button.dispatchEvent(new Event('click'));
  assert.equal(checkbox.checked,true);
  assert.equal(button.attributes['aria-pressed'],'true');
  assert.equal(changes,1);
  button.dispatchEvent(new Event('click'));
  assert.equal(checkbox.checked,false);
  assert.equal(changes,2);
});

test('restored and externally changed expansion state stays reflected by the visible control',()=>{
  const {checkbox,button,state}=setup(true);
  assert.equal(button.attributes['aria-pressed'],'true');
  checkbox.checked=false;checkbox.dispatchEvent(new Event('change'));
  assert.equal(button.attributes['aria-pressed'],'false');
  assert.equal(state.textContent,'关');
});
