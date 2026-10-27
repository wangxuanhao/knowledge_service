const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function fixture(selected='person', parents=[], settings={}){
  function element(tag){return {tag,children:[],listeners:{},attributes:{},style:{},dataset:{},value:'',
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
    setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(key){delete this.attributes[key];},
    addEventListener(key,fn){this.listeners[key]=fn;},classList:{add(){},toggle(){}},
    getBoundingClientRect(){return {left:0,bottom:20,top:0,width:200,height:100};}};}
  const detail=element('host'),writes=[];
  const summary={classes:[{id:'person',label_zh:'人员',parents},{id:'staff',label_zh:'员工'},{id:'org',label_zh:'组织'}],
    relations:[{id:'works',label_zh:'任职于',domain:['person'],range:['org']}],attributes:[]};
  const model={S:{summary,selected},helpers:{label:t=>t.label_zh||t.id,shortIri:x=>x},
    async sendCommand(cmd){writes.push(JSON.parse(JSON.stringify(cmd)));},status(){},on(){return ()=>{};}};
  const window={OntologyModel:model,innerWidth:1600,innerHeight:1000};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),{
    window,document:{createElement:element,getElementById(){return null;}},console,current:'p',
    api:async()=>settings.usage||({declared:{domain:[],range:[]},inferred:{domain:[],range:[]},missing:{}}),endpoint:x=>x,setTimeout,clearTimeout,
  });
  window.OntologyPanel.mount(detail,null,model);
  const all=node=>[node,...node.children.flatMap(all)],has=(node,cls)=>(node.className||'').split(' ').includes(cls);
  return {detail,model,writes,all,find:cls=>all(detail).filter(node=>has(node,cls))};
}

test('detail explanations live beside the heading and open on focus with Escape dismissal',()=>{
  const f=fixture();const button=f.find('om-help-button')[0];
  assert.ok(button,'headings expose a question-mark help control');
  const popup=f.all(f.detail).find(node=>node.attributes.role==='tooltip');
  assert.ok(popup.hidden);button.listeners.focus();assert.equal(popup.hidden,false);
  button.listeners.keydown({key:'Escape',preventDefault(){},stopPropagation(){}});assert.equal(popup.hidden,true);
});

test('lifecycle controls stay collapsed until deliberately opened',()=>{
  const f=fixture();
  assert.equal(f.find('om-retire')[0].tag,'details');
});

test('endpoint selection applies only the intended difference as one draft command',async()=>{
  const f=fixture('works'),picker=f.find('om-type-picker')[0];
  assert.ok(picker,'endpoints are edited with a searchable multiselect');
  const fields=f.all(picker).filter(node=>node.tag==='input'&&node.type==='checkbox');
  const person=fields.find(node=>node.value==='person'),staff=fields.find(node=>node.value==='staff');
  assert.ok(person.checked);staff.checked=true;staff.listeners.change();person.checked=false;person.listeners.change();
  assert.equal(f.writes.length,0);
  const apply=f.all(picker).find(node=>node.tag==='button'&&node.textContent==='应用选择');
  await apply.listeners.click();
  assert.deepEqual(f.writes,[{action:'batch',commands:[
    {action:'remove_domain',target_iri:'works',value:'person'},
    {action:'add_domain',target_iri:'works',value:'staff'},
  ]}]);
  assert.ok(apply.disabled);
});

test('the inheritance heading exposes every existing parent and follows applied changes',async()=>{
  const f=fixture('person',['staff','org','legacy']),preview=f.find('om-parent-summary')[0];
  assert.ok(preview,'existing parents are visible before opening the editor');
  const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
  assert.match(text(preview),/员工/);assert.match(text(preview),/组织/);assert.match(text(preview),/legacy/);
  const picker=f.find('om-type-picker')[0],inputs=f.all(picker).filter(node=>node.type==='checkbox');
  assert.equal(inputs.find(node=>node.value==='person'),undefined);
  assert.ok(inputs.find(node=>node.value==='legacy').checked);
  const staff=inputs.find(node=>node.value==='staff');staff.checked=false;staff.listeners.change();
  assert.match(text(preview),/员工/,'the current-parent preview stays authoritative before applying');
  await f.all(picker).find(node=>node.textContent==='应用选择').listeners.click();
  assert.doesNotMatch(text(preview),/员工/);
  assert.match(text(preview),/legacy/);
  assert.deepEqual(f.writes,[{action:'batch',commands:[{action:'remove_parent',target_iri:'person',value:'staff'}]}]);
});

test('an empty inheritance heading says that no parent is declared',()=>{
  const f=fixture(),preview=f.find('om-parent-summary')[0];
  assert.ok(preview);assert.equal(preview.children[0].textContent,'暂无父类');
});

const content=node=>[node.textContent||'',...node.children.map(content)].join(' ');
const settle=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
const recommendation=()=>({used:4,source:'draft',declared:{domain:[],range:[]},
  inferred:{domain:[{iri:'person',count:4}],range:[{iri:'org',count:4}]},missing:{domain:true,range:true},
  samples:Array.from({length:3},(_,i)=>({subject:'美团侵权投诉须知',subject_type:'平台规则',object:'法律依据 '+(i+1),object_type:'法律法规'}))});

test('the relationship heading displays both current sides and updates only the applied side',async()=>{
  const f=fixture('works'),preview=f.find('om-endpoint-summary')[0];
  assert.ok(preview);assert.match(content(preview),/主体.*人员/);assert.match(content(preview),/客体.*组织/);
  const picker=f.find('om-type-picker')[0],inputs=f.all(picker).filter(node=>node.type==='checkbox');
  const person=inputs.find(node=>node.value==='person'),staff=inputs.find(node=>node.value==='staff');
  staff.checked=true;staff.listeners.change();person.checked=false;person.listeners.change();
  assert.match(content(preview),/主体.*人员/);
  await f.all(picker).find(node=>node.textContent==='应用选择').listeners.click();
  assert.match(content(preview),/主体.*员工/);assert.match(content(preview),/客体.*组织/);
  assert.doesNotMatch(content(preview),/人员/);
});

test('recommendation evidence keeps server counts and uses collapsed examples rather than long paragraphs',async()=>{
  const f=fixture('works',[],{usage:recommendation()});await settle();
  assert.ok(f.find('om-usage-head')[0]);assert.match(content(f.find('om-usage-head')[0]),/4.*条知识/);
  const sides=f.find('om-usage-side');assert.equal(sides.length,2);
  assert.match(content(sides[0]),/主体.*人员.*4 次/);assert.match(content(sides[1]),/客体.*组织.*4 次/);
  const evidence=f.find('om-usage-samples')[0];assert.equal(evidence.tag,'details');assert.ok(!evidence.open);
  assert.equal(f.find('om-usage-sample').length,3);
  assert.match(content(evidence),/美团侵权投诉须知/);assert.match(content(evidence),/法律依据 3/);
  assert.equal(f.writes.length,0,'viewing recommendations never changes the ontology');
});

test('complete endpoint declarations show a compact status and omit the recommendation write action',async()=>{
  const usage=recommendation();usage.missing={domain:false,range:false};usage.declared={domain:['person'],range:['org']};
  const f=fixture('works',[],{usage});await settle();
  assert.match(content(f.find('om-usage-status')[0]),/两端已声明/);
  assert.equal(f.find('om-usage-apply').length,0);
});

test('absent usage and incomplete inferred endpoints never enable a recommendation write',async()=>{
  const empty=fixture('works',[],{usage:{used:0,declared:{domain:[],range:[]},inferred:{domain:[],range:[]},missing:{domain:true,range:true}}});
  await settle();assert.match(content(empty.find('om-usage-empty')[0]),/暂无使用记录/);
  assert.equal(empty.find('om-usage-side').length,0);assert.equal(empty.find('om-usage-apply').length,0);
  const usage=recommendation();usage.inferred.range=[];
  const partial=fixture('works',[],{usage});await settle();
  assert.equal(partial.find('om-usage-apply')[0].disabled,true);
});
