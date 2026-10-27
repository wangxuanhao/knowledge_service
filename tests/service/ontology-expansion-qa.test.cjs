const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const web=path.resolve(__dirname,'../../knowledge_service/web');
const source=fs.readFileSync(path.join(web,'workbench.js'),'utf8');
const {sanitizeEvidenceSummary}=require(path.join(web,'provenance-drawer.js'));

// Exercise the production rendering/history functions with only the DOM surface they use.
function declaration(name){
  const found=source.match(new RegExp('  function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?\\n  \\}'));
  assert.ok(found,'production '+name+' declaration exists');return found[0];
}
function fixture(){
  class Element {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.className='';this.hidden=false;this._text='';this.parentElement=null;}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
    set innerHTML(value){this.textContent=value;}
    get childElementCount(){return this.children.length;}
    append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
    prepend(...nodes){for(const node of nodes)node.parentElement=this;this.children.unshift(...nodes);}
    replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
    setAttribute(key,value){this.attributes[key]=String(value);if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);}
    getAttribute(key){return this.attributes[key]??null;}
    matches(selector){
      if(selector.startsWith('.'))return this.className.split(/\s+/).includes(selector.slice(1));
      const attr=selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/);
      if(attr){const value=this.dataset[attr[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())];return value!==undefined&&(attr[2]===undefined||value===attr[2]);}
      return this.tagName===selector.toUpperCase();
    }
    querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    focus(){this.focused=true;}
  }
  const document={createElement:tag=>new Element(tag),querySelector:()=>null};
  const transcript=new Element('ol'),hero=new Element(),opened=[],selected=[],tabs=[];
  const context={document,window:{},current:'project-frozen',transcript,hero,sessionManager:null,qaHistory:[],evidenceSerial:0,
    provenance:{sanitizeEvidenceSummary,open:value=>opened.push(value),decorateAnswer:(node,text)=>{node.textContent=text;}},
    labelOf:value=>value||'',esc:value=>String(value??''),atEnd(){},platformGroupLabel:group=>group.platform,
    showTab:tab=>tabs.push(tab),status(){},$c:()=>null,addScopeNote(){},markDraft(){},markUnresolvedProvenance(){}};
  context.window.selectEntityDetail=async id=>selected.push(id);
  context.window.suppressNextAutoGraph=()=>tabs.push('suppress');
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(web,'qa-presentation.js'),'utf8'),context);
  const helperStart=source.indexOf('  const evidenceSummary='),helperEnd=source.indexOf('  function markDraft(',helperStart);
  assert.ok(helperStart>=0&&helperEnd>helperStart);
  vm.runInContext(source.slice(helperStart,helperEnd)+'\n'+['frozenCitation','addTurn','loadHistory','addEvidence'].map(declaration).join('\n')+
    '\nthis.render=addEvidence;this.replay=loadHistory;',context);
  return {context,turn:new Element('li'),transcript,opened,selected,tabs};
}
const ledger={matched:true,entity_matches:[{id:'meituan',text:'美团',type_label:'互联网平台'}],
  terms:['urn:Platform'],literal_terms:[],subclasses:[],type_hits:7,used_hits:2};
const row=(citation,channel='milvus',extra={})=>({citation,kind:'chunk',text:'原文 '+citation,
  retrieval_channel:channel,provenance_ref:'frozen-'+citation,version_id:'version-'+citation,
  validity_status:'effective',...extra});
const citations=container=>container.querySelectorAll('.qa-citation').filter(node=>/^\[[ERFU]\d+\]$/.test(node.textContent)).map(node=>node.textContent);

test('enabled QA exposes supplemental evidence independently while direct evidence stays collapsed',()=>{
  const f=fixture();f.context.render(f.turn,{answer_id:'answer-frozen',ontology_expansion:ledger,
    evidence:[row('E1'),row('E2','ontology_entity_type')],uncertain_references:[row('U1','ontology_type',{validity_status:'unknown'})]});
  const panel=f.turn.querySelector('.qa-evidence-panel'),supplement=f.turn.querySelector('.qa-ontology-supplement');
  assert.ok(supplement,'enabled expansion has its own evidence region');
  assert.equal(panel.hidden,true);assert.equal(supplement.hidden,false);assert.equal(supplement.parentElement,panel.parentElement);
  assert.equal(panel.dataset.expansionGroup,'direct');assert.equal(supplement.dataset.expansionGroup,'supplemental');
  assert.deepEqual(citations(panel),['[E1]']);assert.deepEqual(citations(supplement),['[E2]','[U1]']);
  assert.match(supplement.textContent,/本体扩展补充/);assert.match(supplement.textContent,/美团 → 互联网平台/);
  assert.match(supplement.textContent,/检索补入 2 条/);assert.match(supplement.textContent,/1 条回答依据.*1 条独立参考/);
  assert.match(f.turn.querySelector('.qa-evidence-toggle').textContent,/直接命中 · 1 条回答依据/);
  assert.doesNotMatch(f.turn.querySelector('.qa-evidence-toggle').textContent,/独立参考/);
  f.turn.querySelector('.qa-evidence-toggle').onclick();assert.equal(panel.hidden,false);assert.equal(supplement.hidden,false);
});

test('expansion ledger retains a precise zero state and separates retrieved count from offered evidence',()=>{
  for(const expansion of [{matched:false,has_hierarchy:false,type_hits:0,used_hits:0},ledger]){
    const f=fixture();f.context.render(f.turn,{answer_id:'empty-answer',ontology_expansion:expansion,evidence:[]});
    const supplement=f.turn.querySelector('.qa-ontology-supplement');assert.ok(supplement,'an empty ledger remains visible');
    assert.match(supplement.textContent,/0 条回答依据.*0 条独立参考/);
    assert.ok(supplement.querySelector('.ontology-result-empty'));
    if(expansion.used_hits)assert.match(supplement.textContent,/检索补入 2 条.*本回答.*没有可展示的扩展依据.*参考/);
    else assert.match(supplement.textContent,/未匹配.*实体.*类名/);
  }
});

test('legacy unmarked history and disabled QA retain the original compact evidence DOM',()=>{
  const f=fixture();f.context.render(f.turn,{answer_id:'old-answer',evidence:[row('E4')],historical_references:[row('R3')]});
  assert.equal(f.turn.querySelector('.qa-ontology-supplement'),null);
  assert.equal(f.turn.querySelector('[data-expansion-group]'),null);
  assert.deepEqual(citations(f.turn),['[E4]','[R3]']);assert.equal(f.turn.querySelector('.qa-evidence-panel').hidden,true);
  const empty=fixture();empty.context.render(empty.turn,{evidence:[]});assert.equal(empty.turn.children.length,0);
});

test('explicit class markers work without a ledger, preserving reference kinds, frozen citations and source controls',async()=>{
  const f=fixture(),sources=[{title:'版本一',quote:'当时原文',document_version_id:'doc-v1',version:1},
    {title:'版本二',quote:'另一个冻结片段',document_version_id:'doc-v2',version:2}];
  f.context.render(f.turn,{answer_id:'answer-frozen',evidence:[row('E7','ontology_type',{kind:'entity',id:'entity-frozen',sources})],
    historical_references:[row('R4','ontology_type',{validity_status:'expired',sources})],
    future_references:[row('F2','ontology_entity_type',{validity_status:'not_yet_effective',sources})],
    uncertain_references:[row('U9','ontology_type',{validity_status:'unknown',usage:'qualified_quote',sources})]});
  const supplement=f.turn.querySelector('.qa-ontology-supplement');assert.ok(supplement);
  assert.deepEqual(citations(supplement),['[E7]','[R4]','[F2]','[U9]']);
  assert.match(supplement.textContent,/历史参考/);assert.match(supplement.textContent,/尚未生效参考/);assert.match(supplement.textContent,/有效性待核对/);
  assert.match(supplement.textContent,/仅用于文档说明/);assert.match(supplement.textContent,/类型扩展/);
  for(const citation of ['[E7]','[R4]','[F2]','[U9]'])supplement.querySelectorAll('.qa-citation').find(node=>node.textContent===citation).onclick();
  assert.deepEqual(f.opened.map(value=>[value.projectId,value.answerId,value.citation]),[
    ['project-frozen','answer-frozen','E7'],['project-frozen','answer-frozen','R4'],['project-frozen','answer-frozen','F2'],['project-frozen','answer-frozen','U9']]);
  const sourceMore=supplement.querySelector('.qa-source-more');assert.ok(sourceMore);
  const sourceItems=supplement.querySelector('.qa-source-list').querySelectorAll('.qa-source-item');assert.equal(sourceItems[1].hidden,true);
  sourceMore.onclick();assert.equal(sourceItems[1].hidden,false);assert.equal(sourceItems[0].hidden,false);
  const graph=supplement.querySelector('[data-evidence-node]');assert.equal(typeof graph.onclick,'function');await graph.onclick();
  assert.deepEqual(f.selected,['entity-frozen']);assert.deepEqual(f.tabs,['suppress','search']);
});

test('direct and supplemental evidence have independent more controls and stable original citation ordering',()=>{
  const f=fixture();const evidence=Array.from({length:8},(_,i)=>row('E'+(i+1),i%2?'ontology_entity_type':'milvus'));
  f.context.render(f.turn,{answer_id:'more-answer',ontology_expansion:ledger,evidence});
  const direct=f.turn.querySelector('.qa-evidence-panel'),supplement=f.turn.querySelector('.qa-ontology-supplement');
  assert.ok(supplement);assert.deepEqual(citations(direct),['[E1]','[E3]','[E5]','[E7]']);assert.deepEqual(citations(supplement),['[E2]','[E4]','[E6]','[E8]']);
  const directRows=direct.querySelectorAll('.qa-evidence-item'),extraRows=supplement.querySelectorAll('.qa-evidence-item');
  assert.equal(directRows[3].hidden,true);assert.equal(extraRows[3].hidden,true);assert.equal(extraRows[0].hidden,false);
  supplement.querySelector('.qa-evidence-more').onclick();assert.equal(extraRows[3].hidden,false);assert.equal(directRows[3].hidden,true);
  direct.querySelector('.qa-evidence-more').onclick();assert.equal(directRows[3].hidden,false);
});

test('duplicate citations do not leak into both sections while different validity citations remain offered',()=>{
  const f=fixture();f.context.render(f.turn,{answer_id:'duplicate-answer',ontology_expansion:ledger,
    evidence:[row('E1'),row('E1','ontology_type'),row('E2','ontology_type')],
    uncertain_references:[row('U1','ontology_type',{version_id:'version-E1',validity_status:'unknown'})]});
  const supplement=f.turn.querySelector('.qa-ontology-supplement');assert.ok(supplement);
  assert.deepEqual(citations(f.turn.querySelector('.qa-evidence-panel')),['[E1]']);assert.deepEqual(citations(supplement),['[E2]','[U1]']);
});

test('production history replay restores expansion ledger, separate references and frozen answer identity',()=>{
  const f=fixture();const item={kind:'assistant',text:'历史回答 [E5] [U4]',answer_id:'historical-answer',ontology_expansion:ledger,
    evidence:[row('E3'),row('E5','ontology_entity_type')],uncertain_references:[row('U4','ontology_type',{validity_status:'unknown'})]};
  f.context.replay(JSON.parse(JSON.stringify([item])));
  const supplement=f.transcript.querySelector('.qa-ontology-supplement');assert.ok(supplement,'history uses the same expansion rendering');
  assert.deepEqual(citations(supplement),['[E5]','[U4]']);assert.match(supplement.textContent,/美团 → 互联网平台/);
  supplement.querySelectorAll('.qa-citation').find(node=>node.textContent==='[U4]').onclick();assert.equal(f.opened[0].answerId,'historical-answer');
  f.context.replay([{...item,ontology_expansion:undefined,evidence:[row('E3')],uncertain_references:[row('U4')]}]);
  assert.equal(f.transcript.querySelector('.qa-ontology-supplement'),null,'unmarked historical rows are never inferred as expansion');
});
