const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const catalog = require('../../knowledge_service/web/ontology-model-catalog.js');
const summary = { classes:[{id:'urn:A',label_zh:'订单',parents:['urn:P','urn:Q']},{id:'urn:old',label:'历史类',active:false}],
  relations:[{id:'urn:r',label:'关联',domain:[],range:[]}], attributes:[{id:'urn:a',label:'金额',domain:['urn:A'],range:['xsd:decimal']}] };
test('catalog searches every kind and retains incomplete or retired definitions', () => {
  assert.equal(catalog.catalogEntries(summary).length, 4);
  assert.equal(catalog.catalogEntries(summary,{kind:'relation'})[0].id,'urn:r');
  assert.equal(catalog.catalogEntries(summary,{query:'金额'})[0].kind,'attribute');
  assert.equal(catalog.catalogEntries(summary,{query:'urn:old'})[0].active,false);
  assert.equal(catalog.catalogEntries(summary,{activeOnly:true}).length,3);
});
test('type cards expose all parents and independently addressable properties and relations', () => {
  const model = catalog.typeCard(summary.classes[0],summary);
  assert.deepEqual(model.parents,['urn:P','urn:Q']);
  assert.equal(model.attributes[0].id,'urn:a');
  assert.equal(catalog.typeCard(summary.relations[0],summary).endpointText,'未指定主体 → 未指定客体');
});
test('attribute cards show applicable classes and value types, including unassigned definitions',()=>{
  const unassigned={id:'unassigned',label:'来源链接'};
  const data={...summary,attributes:[...summary.attributes,unassigned]};
  assert.equal(catalog.typeCard(summary.attributes[0],data).endpointText,'订单 · decimal');
  assert.equal(catalog.typeCard(unassigned,data).endpointText,'未指定适用类型 · 未限定值类型');
});
test('RDF editing rejects stale project, draft revision or read-only snapshot', () => {
  const opened={project:'p',draftId:'draft',revision:2,ontologyId:'release'};
  assert.equal(catalog.sourceWriteAllowed(opened,{...opened}),true);
  assert.equal(catalog.sourceWriteAllowed(opened,{...opened,revision:3}),false);
  assert.equal(catalog.sourceWriteAllowed(opened,{...opened,preview:true}),false);
  assert.equal(catalog.sourceWriteAllowed(opened,{...opened,project:'other'}),false);
});

test('Turtle import and editing follow write permissions while viewers can read and export', async () => {
  const nodes=[],writes=[];
  let canWrite=false;
  const element=tag=>{
    const node={tag,children:[],events:{},hidden:false,value:'',
      append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},
      addEventListener(name,handler){this.events[name]=handler;},setAttribute(){},querySelectorAll(){return [];},showModal(){}};
    nodes.push(node);
    return node;
  };
  const context={window:{Auth:{isAdmin:()=>canWrite}},current:'p',document:{createElement:element,body:element('body')},
    endpoint:route=>route,api:async()=>({turtle:'@prefix ex: <urn:ex:> .',id:'release'})};
  vm.runInNewContext(readFileSync(require.resolve('../../knowledge_service/web/ontology-model-catalog.js'),'utf8'),context);
  const controller={S:{},status(message){assert.fail(message);},sendCommand:async command=>writes.push(command)};
  const catalog=context.window.OntologyCatalog;
  catalog.mount(element('aside'),element('main'),controller);
  const find=(label)=>nodes.find(node=>node.textContent===label);
  assert.equal(find('导入 Turtle 本体').hidden,true);
  controller.S.ontologyId='release';
  catalog.render(controller.S);
  find('RDF 源码 / 导出').events.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(find('导出 Turtle').hidden,false);
  assert.equal(find('编辑 Turtle'),undefined);

  canWrite=true;
  controller.S.ontologyId=null;
  catalog.render(controller.S);
  assert.equal(find('导入 Turtle 本体').hidden,false);
  find('导入 Turtle 本体').events.click();
  await new Promise(resolve=>setImmediate(resolve));
  const save=find('加入草案');
  const file=nodes.find(node=>node.type==='file');
  const dialog=nodes.findLast(node=>node.tag==='dialog');
  assert.equal(save.disabled,true);
  assert.equal(dialog.children.some(node=>node.tag==='pre'||node.tag==='textarea'),false);
  file.files=[{name:'rules.ttl',text:async()=>'<urn:class> a <http://www.w3.org/2002/07/owl#Class> .'}];
  await file.onchange();
  assert.equal(save.disabled,false);
  assert.deepEqual(writes,[]);
  assert.equal(dialog.children.some(node=>node.tag==='pre'||node.tag==='textarea'),false);
  canWrite=false;
  await save.events.click();
  assert.deepEqual(writes,[]);
  canWrite=true;
  await save.events.click();
  assert.equal(writes[0].action,'import_turtle');
  assert.match(writes[0].turtle,/urn:class/);
});

test('class card summaries preserve direct datatypes, declared required flags and inherited properties', () => {
  const data={classes:[{id:'parent',label:'父类'},{id:'child',parents:['parent']}],relations:[],attributes:[
    {id:'name',label:'名称',domain:['parent'],range:['http://www.w3.org/2001/XMLSchema#string'],required:true},
    {id:'amount',label:'金额',domain:['child'],range:['xsd:decimal'],min_count:1},
    {id:'retired',domain:['child'],active:false},
  ]};
  const before=JSON.stringify(data),card=catalog.typeCard(data.classes[1],data);
  assert.equal(card.attributes[0].datatype,'decimal');
  assert.equal(card.attributes[0].required,true);
  assert.equal(card.attributes.length,1,'retired fields remain in the term catalog, outside active card rows');
  assert.equal(card.inheritedAttributes[0].id,'name');
  assert.deepEqual(card.inheritedAttributes[0].inheritedFrom,['parent']);
  assert.equal(JSON.stringify(data),before,'card compilation must not mutate RDF definitions');
});

test('hierarchy navigation collapses branches and preserves multiple parents as references', () => {
  const data={classes:[{id:'p',label:'P'},{id:'q',label:'Q'}, {id:'c',label:'C',parents:['p','q']},
    {id:'g',label:'G',parents:['c']} ]};
  const rows=catalog.hierarchyRows(data);
  assert.equal(rows.filter(row=>row.id==='c').length,2);
  assert.equal(rows.find(row=>row.id==='c'&&row.reference).parentId,'q');
  assert.equal(rows.find(row=>row.id==='p').descendantCount,2);
  const collapsed=catalog.hierarchyRows(data,{collapsed:new Set(['p'])});
  assert.equal(collapsed.find(row=>row.id==='p').expanded,false);
  assert.equal(collapsed.find(row=>row.id==='c').parentId,'q','the other parent remains navigable');
  assert.ok(collapsed.some(row=>row.id==='g'));
});

test('hierarchy navigation safely exposes cyclic components and missing parent references', () => {
  const rows=catalog.hierarchyRows({classes:[{id:'a',parents:['b','external']},{id:'b',parents:['a']} ]});
  assert.ok(rows.some(row=>row.id==='a'&&row.externalParents.includes('external')));
  assert.ok(rows.some(row=>row.cycle));
  assert.ok(rows.length<=3);
});
