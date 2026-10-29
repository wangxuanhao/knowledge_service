const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const RDFS='http://www.w3.org/2000/01/rdf-schema#';
const XSD='http://www.w3.org/2001/XMLSchema#';

function fixture(preview=false){
  const element=tag=>({tag,children:[],listeners:{},attributes:{},dataset:{},value:'',isConnected:true,
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
    setAttribute(k,v){this.attributes[k]=String(v);},addEventListener(k,fn){const previous=this.listeners[k];
      this.listeners[k]=previous?(...args)=>{previous(...args);return fn(...args);}:fn;},
    focus(){},classList:{add(){},toggle(){}}});
  const summary={classes:[{id:'parent',label_zh:'基础对象'},{id:'child',label_zh:'商品',parents:['parent']},{id:'other',label_zh:'订单'}],relations:[],attributes:[
    {id:'name',label_zh:'名称',domain:['parent'],range:[XSD+'string']},
    {id:'stock',label_zh:'库存',label:'stock',description:'当前库存',domain:['child','other'],range:[XSD+'integer'],annotations:[
      {predicate:RDFS+'label',value:'库存',language:'zh-CN'},
      {predicate:RDFS+'label',value:'Stock',language:'en'},
      {predicate:RDFS+'comment',value:'当前库存',language:'zh'},
      {predicate:RDFS+'comment',value:'Current stock',language:'en'},
    ]},
    {id:'code',label_zh:'编码',domain:[],range:[XSD+'string']},
    {id:'old',label_zh:'旧字段',active:false,domain:[]},
  ]};
  const writes=[],creates=[],statuses=[];
  const model={S:{summary,preview:preview?{id:'history'}:null,draft:{id:'draft',revision:1},ontologyId:'published'},
    async sendCommand(cmd){writes.push(JSON.parse(JSON.stringify(cmd)));},
    openCreate(value){creates.push(value);},select(){},status(...args){statuses.push(args);}};
  const window={OntologyModel:model};
  const context={window,document:{createElement:element,getElementById(){return null;}},console,current:'p',setTimeout,clearTimeout,
    api:async()=>({}),endpoint:x=>x};
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-catalog.js'),'utf8'),context);
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-attributes.js'),'utf8'),context);
  const api=window.OntologyAttributes,all=n=>[n,...n.children.flatMap(all)],text=n=>[n.textContent||'',...n.children.map(text)].join(' ');
  const options={model,project:()=> 'p',title:(name)=>{const n=element('h5');n.textContent=name;return n;}};
  return {api,model,summary,writes,creates,statuses,options,all,text,context,element,
    find:(node,name)=>all(node).find(n=>n.tag==='button'&&n.textContent===name),
    json:value=>JSON.parse(JSON.stringify(value))};
}

test('editing a canonical Chinese name and description preserves English annotations and the IRI',()=>{
  const f=fixture(),term=f.summary.attributes[1];
  const commands=f.json(f.api.metadataCommands(term,{name:'可用库存',description:'扣除锁定后的库存'}));
  assert.deepEqual(commands,[
    {action:'remove_annotation',target_iri:'stock',predicate:RDFS+'label',value:'库存',language:'zh-CN'},
    {action:'add_annotation',target_iri:'stock',predicate:RDFS+'label',value:'可用库存',language:'zh-CN'},
    {action:'remove_annotation',target_iri:'stock',predicate:RDFS+'comment',value:'当前库存',language:'zh'},
    {action:'add_annotation',target_iri:'stock',predicate:RDFS+'comment',value:'扣除锁定后的库存',language:'zh'},
  ]);
  assert.equal(term.label_zh,'库存');assert.equal(term.annotations.length,4);
});

test('unchanged metadata is a no-op; blank name is rejected and clearing description removes its literal',()=>{
  const f=fixture(),term=f.summary.attributes[1];
  assert.equal(f.api.metadataCommands(term,{name:'库存',description:'当前库存'}).length,0);
  assert.throws(()=>f.api.metadataCommands(term,{name:' ',description:''}),/名称/);
  assert.deepEqual(f.json(f.api.metadataCommands(term,{name:'库存',description:''})),[
    {action:'remove_annotation',target_iri:'stock',predicate:RDFS+'comment',value:'当前库存',language:'zh'},
  ]);
});

test('renaming the displayed Chinese locale retains other Chinese translations',()=>{
  const f=fixture(),term=f.summary.attributes[1];
  term.annotations.push({predicate:RDFS+'label',value:'庫存',language:'zh-Hant'});
  const commands=f.json(f.api.metadataCommands(term,{name:'可用库存',description:term.description}));
  assert.equal(commands.length,2);assert.ok(commands.every(c=>c.language==='zh-CN'));
});

test('association edits only the selected type ownership and retains unknown or retired current fields',()=>{
  const f=fixture();
  assert.deepEqual(f.json(f.api.associationCommands('child',['stock','legacy'],['code','legacy'])),[
    {action:'remove_domain',target_iri:'stock',value:'child'},
    {action:'add_domain',target_iri:'code',value:'child'},
  ]);
});

test('a type lists direct and inherited attributes with parent names, and creates within that type',()=>{
  const f=fixture(),node=f.api.classSection(f.summary.classes[1],f.summary,f.options);
  assert.match(f.text(node),/库存/);assert.match(f.text(node),/继承属性.*名称.*基础对象/);
  f.find(node,'添加属性').listeners.click();
  assert.deepEqual(f.json(f.creates),[{kind:'attribute',domain:'child'}]);
});

test('linking existing properties is searchable, preselected and submitted as one difference batch',async()=>{
  const f=fixture(),node=f.api.classSection(f.summary.classes[1],f.summary,f.options);
  const inputs=f.all(node).filter(n=>n.type==='checkbox');
  const stock=inputs.find(n=>n.value==='stock'),code=inputs.find(n=>n.value==='code');
  assert.ok(stock.checked);assert.equal(inputs.find(n=>n.value==='old'),undefined);
  assert.ok(!inputs.find(n=>n.value==='name'),'inherited attributes need no redundant ownership');
  const search=f.all(node).find(n=>n.type==='search');search.value='编码';search.listeners.input();
  assert.ok(f.all(node).find(n=>n.dataset.attributeId==='stock').hidden);
  stock.checked=false;stock.listeners.change();code.checked=true;code.listeners.change();
  assert.equal(f.writes.length,0);await f.find(node,'应用关联').listeners.click();
  assert.deepEqual(f.writes,[{action:'batch',commands:[
    {action:'remove_domain',target_iri:'stock',value:'child'},
    {action:'add_domain',target_iri:'code',value:'child'},
  ]}]);
});

test('a rejected write retains selections and metadata inputs after a panel rerender',async()=>{
  const f=fixture();f.model.sendCommand=async()=>{throw Error('模拟冲突');};
  let node=f.api.metadataEditor(f.summary.attributes[1],f.options);
  const name=f.all(node).find(n=>n.attributes['aria-label']==='属性名称');name.value='待保存库存';name.listeners.input();
  await f.find(node,'保存名称与说明').listeners.click();
  node=f.api.metadataEditor(f.summary.attributes[1],f.options);
  assert.equal(f.all(node).find(n=>n.attributes['aria-label']==='属性名称').value,'待保存库存');
  assert.match(f.text(node),/模拟冲突/);
});

test('a failure after draft preparation unlocks the currently rendered metadata editor',async()=>{
  const f=fixture();let replacement;
  f.model.sendCommand=async()=>{
    f.model.S.draft.revision++;
    replacement=f.api.metadataEditor(f.summary.attributes[1],f.options);
    throw Error('草案准备后写入失败');
  };
  const node=f.api.metadataEditor(f.summary.attributes[1],f.options);
  const name=f.all(node).find(n=>n.attributes['aria-label']==='属性名称');name.value='待重试库存';name.listeners.input();
  await f.find(node,'保存名称与说明').listeners.click();
  assert.equal(f.find(replacement,'保存名称与说明').disabled,false);
  assert.match(f.text(replacement),/草案准备后写入失败/);
});

test('historical attributes are readable and expose no write controls',()=>{
  const f=fixture(true),node=f.api.classSection(f.summary.classes[1],f.summary,f.options);
  assert.match(f.text(node),/库存/);assert.match(f.text(node),/基础对象/);
  assert.equal(f.find(node,'添加属性'),undefined);assert.equal(f.find(node,'应用关联'),undefined);
  assert.equal(f.api.metadataEditor(f.summary.attributes[1],f.options),null);
});

test('stale attribute editors cannot submit into a different draft revision',async()=>{
  const f=fixture(),node=f.api.metadataEditor(f.summary.attributes[1],f.options);
  const name=f.all(node).find(n=>n.attributes['aria-label']==='属性名称');name.value='新库存';name.listeners.input();
  f.model.S.draft.revision=2;
  await f.find(node,'保存名称与说明').listeners.click();
  assert.equal(f.writes.length,0);assert.match(f.text(node),/变化|重新/);
});

test('creating an attribute from a type includes preselected ownership, datatype and description',async()=>{
  const f=fixture(),host=f.element('host'),created=[];
  f.model.createTerm=async spec=>{created.push(f.json(spec));};
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8'),f.context);
  f.context.window.OntologyPanel.mount(host,null,f.model);
  f.context.window.OntologyPanel.openCreate({kind:'attribute',domain:'child'});
  f.context.window.OntologyPanel.render(f.model.S);
  const inputs=f.all(host),name=inputs.find(n=>n.placeholder?.includes('中文名'));
  const datatype=inputs.find(n=>n.attributes['aria-label']==='属性数据类型');
  assert.ok(datatype,'datatype is selected during creation');assert.equal(datatype.value,XSD+'string');
  name.value='发布日期';name.listeners.input();
  datatype.value=XSD+'date';datatype.listeners.change?.();
  const description=inputs.find(n=>n.attributes['aria-label']==='新属性说明');
  description.value='业务公布日期';description.listeners.input();
  await f.find(host,'新建并写入草案').listeners.click();
  assert.equal(created[0].domain,'child');assert.equal(created[0].datatype,XSD+'date');
  assert.equal(created[0].description,'业务公布日期');
});

test('existing datatypes outside the short list remain visible and can be explicitly cleared',async()=>{
  const f=fixture(),host=f.element('host');
  f.summary.attributes[1].range=[XSD+'double'];f.model.S.selected='stock';
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8'),f.context);
  f.context.window.OntologyPanel.mount(host,null,f.model);
  const select=f.all(host).find(n=>n.tag==='select'&&n.className==='om-ce-select');
  assert.equal(select.value,XSD+'double');
  select.value='';select.listeners.change();
  const apply=f.find(host,'应用类型');assert.equal(apply.disabled,false);
  await apply.listeners.click();
  assert.deepEqual(f.writes,[{action:'set_datatype',target_iri:'stock',datatype:null}]);
});

test('an attribute creation form cannot submit after its project changes',async()=>{
  const f=fixture(),host=f.element('host'),created=[];
  f.model.createTerm=async spec=>created.push(spec);
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8'),f.context);
  f.context.window.OntologyPanel.mount(host,null,f.model);
  f.context.window.OntologyPanel.openCreate({kind:'attribute',domain:'child'});
  f.context.window.OntologyPanel.render(f.model.S);
  const name=f.all(host).find(n=>n.placeholder?.includes('中文名'));name.value='编号';name.listeners.input();
  f.context.current='other-project';
  await f.find(host,'新建并写入草案').listeners.click();
  assert.equal(created.length,0);assert.match(f.statuses.at(-1)[0],/变化|重新/);
});

test('a creation form stays locked across draft preparation and unlocks with its inputs on failure',async()=>{
  const f=fixture(),host=f.element('host');let fail;
  const pending=new Promise((resolve,reject)=>{fail=reject;});
  f.model.createTerm=()=>pending;
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8'),f.context);
  const panel=f.context.window.OntologyPanel;panel.mount(host,null,f.model);
  panel.openCreate({kind:'attribute',domain:'child'});panel.render(f.model.S);
  const name=f.all(host).find(n=>n.placeholder?.includes('中文名'));name.value='编号';name.listeners.input();
  const submitted=f.find(host,'新建并写入草案').listeners.click();
  panel.render(f.model.S);
  assert.equal(f.find(host,'新建并写入草案').disabled,true);
  fail(Error('保存失败'));await submitted;
  assert.equal(f.find(host,'新建并写入草案').disabled,false);
  assert.equal(f.all(host).find(n=>n.placeholder?.includes('中文名')).value,'编号');
});

test('successful creation without an owner clears the rerendered locked form',async()=>{
  const f=fixture(),host=f.element('host');let panel;
  f.model.createTerm=async()=>{panel.render(f.model.S);};
  vm.runInNewContext(fs.readFileSync(path.join(web,'ontology-model-panel.js'),'utf8'),f.context);
  panel=f.context.window.OntologyPanel;panel.mount(host,null,f.model);
  panel.openCreate({kind:'attribute'});panel.render(f.model.S);
  const name=f.all(host).find(n=>n.placeholder?.includes('中文名'));name.value='规则编号';name.listeners.input();
  await f.find(host,'新建并写入草案').listeners.click();
  assert.equal(f.find(host,'新建并写入草案'),undefined);
  assert.match(f.text(host),/选择一个定义/);
});
