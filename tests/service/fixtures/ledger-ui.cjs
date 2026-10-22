const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const create=require('../../../knowledge_service/web/record-create.js');
function typeHelpers(terms){
  const source=fs.readFileSync(path.join(__dirname,'../../../knowledge_service/web/app.js'),'utf8');
  // Run the real shared escaping/label helpers before app.js starts binding UI.
  const end=source.indexOf('const recordName=');assert.ok(end>0);
  return vm.runInNewContext(source.slice(0,end)+'\ncacheOntologyTerms({classes:testTerms}); ({esc,typeHint,labelOf});',
    {document:{getElementById:()=>null},testTerms:terms});
}
function fixture(options={}){
  const nodes=new Map(),created=[],calls=[],events={};
  const html=fs.readFileSync(path.join(__dirname,'../../../knowledge_service/web/index.html'),'utf8');
  const htmlIds=new Set(Array.from(html.matchAll(/\bid="([^"]+)"/g),match=>match[1]));
  const retiredIds=new Set(['load-ontology','tab-ontology','ontology-summary','turtle','ontology-versions','save-ontology']);
  const absentAccesses=[];
  const helpers=typeHelpers(options.terms||[]);
  class Element{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.textContent='';this.innerHTML='';this.dataset={};this.className='';this.events={};this.disabled=false;
      this.classList={add:()=>{},remove:()=>{},contains:name=>this.className.split(' ').includes(name),toggle:()=>{}};created.push(this);}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];}before(){}after(){}
    closest(){return get('library-panel');}setAttribute(key,value){this[key]=value;}
    querySelector(selector){return selector==='tbody'?get('tbody'):null;}
    querySelectorAll(){return [];}addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
    click(){const result=this.onclick?.();for(const fn of this.events.click||[])fn();return result;}
    scrollIntoView(){}
  }
  const get=id=>{
    if(options.strictOntologyDom&&retiredIds.has(id)&&!htmlIds.has(id)){absentAccesses.push(id);return null;}
    if(!nodes.has(id)){const node=new Element();node.id=id;}return nodes.get(id);
  };
  const nav=get('nav-records');
  const document={createElement:tag=>new Element(tag),querySelector:selector=>selector==='[data-tab="records"]'?nav:null,
    addEventListener(name,fn){(events[name]??=[]).push(fn);},querySelectorAll:()=>[]};
  const rows=options.rows||[{id:'first',kind:'entity',text:'张三',type:'Employee',version:1,metadata:{},ontology_id:'published'}];
  const context=vm.createContext({window:{RecordCreate:create,Auth:{isAdmin:()=>true},TaskReview:{open(){context.reviewOpened=true;}}},document,
    $:get,current:'p',wb:{records:new Map(),versionContext:{}},RecordVersionContext:require('../../../knowledge_service/web/record-version-context.js'),
    scope:()=>({include_unknown:true,filters:{field:'metadata.city',op:'eq',value:'杭州'}}),
    endpoint:suffix=>'/api/projects/'+context.current+suffix,...helpers,
    status(){},editRecord(){},historyFor(){},setTimeout,clearTimeout,requestAnimationFrame:fn=>fn(),CSS:{escape:value=>value},
    api:async(url,body)=>{calls.push({url,body});
      if(url.endsWith('/records/page')){
        const list=body.cursor?[{...rows[0],id:'last',text:'最后实体'}]:rows;
        return {records:list,total:150001,next_cursor:body.cursor?null:'next-page'};
      }
      if(url.endsWith('/assertions/batch'))return {assertions_by_record:{first:[{id:'a',canonical_record_id:'first',status:'accepted'}]}};
      if(url.endsWith('/ontologies'))return {versions:[]};
      if(url.endsWith('/operations'))return {operations:[]};
      throw Error('Unexpected request '+url);
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../knowledge_service/web/records-view.js'),'utf8'),context);
  return {context,get,calls,created,events,nav,htmlIds,absentAccesses};
}

module.exports={fixture};
