const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/qa-presentation.js');
function api(){const context={window:{}};vm.runInNewContext(fs.readFileSync(file,'utf8'),context);return context.window.QaPresentation;}
test('answer formatting recognizes headings, lists, emphasis and citations without creating HTML or links',()=>{
  const p=api();
  const blocks=p.blocks('# 价格说明\n\n**团购价：**销售价 [U1]\n- 外卖价\n<script>alert(1)</script> [坏链接](javascript:alert(1))');
  assert.equal(blocks[0].tag,'h3');
  assert.ok(blocks.some(b=>b.tag==='li'));
  const tokens=p.inline('**团购价：**销售价 [U1] <img src=x onerror=alert(1)>');
  assert.ok(tokens.some(t=>t.type==='strong'&&t.text==='团购价：'));
  assert.ok(tokens.some(t=>t.type==='citation'&&t.text==='[U1]'));
  assert.ok(tokens.some(t=>t.type==='text'&&t.text.includes('<img')));
  assert.equal(p.inline('**没有闭合')[0].type,'text');
  assert.ok(!tokens.some(t=>t.type==='html'||t.type==='link'));
});
test('generation status explains an enabled but skipped model and reports numeric milliseconds',()=>{
  const p=api();
  assert.match(p.status({generation:{requested:true,called:false,skip_reason:'no_eligible_evidence'},timings_ms:{total:877,retrieval:605}}),/未调用模型.*有效/);
  assert.match(p.status({generation:{called:true},timings_ms:{total:1500,llm:700}}),/模型回答.*1.50 s/);
  assert.match(p.status({generation:{requested:false,called:false,skip_reason:'disabled'}}),/仅展示依据/);
  assert.match(p.status({mode:'evidence_only',generation:{called:true}}),/模型已调用.*仅展示依据/);
  assert.match(p.status({mode:'llm',generation:{called:true},answer_purpose:'document_explanation',answer_label:'按文档说明，有效性待核对'}),/模型回答.*按文档说明.*待核对/);
});

test('answer status distinguishes class expansion from an unmatched entity or class name',()=>{
  const p=api();
  assert.match(p.status({ontology_expansion:{matched:false,has_hierarchy:true,type_hits:0}}),/本体扩展.*未匹配.*实体.*类名/);
  const expanded=p.status({ontology_expansion:{matched:true,has_hierarchy:true,
    terms:['urn:knowledge:互联网平台'],subclasses:['urn:knowledge:知识产权维权平台'],type_hits:7}});
  assert.match(expanded,/互联网平台/);
  assert.match(expanded,/1.*子类/);
  assert.match(expanded,/7.*类型/);
  assert.doesNotMatch(expanded,/urn:knowledge/);
  assert.match(p.status({ontology_expansion:{matched:false,has_hierarchy:false,type_hits:0}}),/暂无.*父子层级/);
});

test('entity expansion status shows entity-to-type paths and actual supplemental results',()=>{
  const expanded=api().status({ontology_expansion:{matched:true,has_hierarchy:false,
    terms:['urn:knowledge:InternetPlatform','urn:knowledge:InternetEnterprise'],literal_terms:[],
    entity_terms:['urn:knowledge:InternetPlatform','urn:knowledge:InternetEnterprise'],
    entity_matches:[{id:'meituan',text:'美团',matched_name:'美团',match_kind:'name',
      type:'urn:knowledge:InternetPlatform',type_label:'互联网平台'},
      {id:'meituan',text:'美团',matched_name:'美团',match_kind:'name',
        type:'urn:knowledge:InternetEnterprise',type_label:'互联网企业'}],
    subclasses:['urn:knowledge:DeliveryPlatform'],type_hits:9,used_hits:2}});
  assert.match(expanded,/美团 → 互联网平台／互联网企业/);
  assert.match(expanded,/1 个子类/);
  assert.match(expanded,/9 条类型候选/);
  assert.match(expanded,/实际补入 2 条/);
  assert.match(expanded,/相关类型内容/);
  assert.doesNotMatch(expanded,/urn:|https?:|InternetPlatform/);
});

test('alias expansion and literal classes remain explicit without inventing a used count for legacy ledgers',()=>{
  const p=api(),expanded=p.status({ontology_expansion:{matched:true,
    terms:['urn:知识:Parent','urn:知识:平台'],literal_terms:['urn:知识:Parent'],matched_terms:['父类'],
    entity_matches:[{id:'meituan',text:'美团',matched_name:'团团',match_kind:'alias',type_label:'平台'}],
    subclasses:[],type_hits:3,used_hits:0}});
  assert.match(expanded,/团团.*别名.*美团.*→ 平台/);
  assert.match(expanded,/类名：父类/);
  assert.match(expanded,/实际补入 0 条/);
  const named=p.status({ontology_expansion:{matched:true,entity_matches:[
    {text:'美团/大众点评',type_label:'互联网平台/互联网企业'}]}});
  assert.match(named,/美团\/大众点评 → 互联网平台\/互联网企业/);
  const legacy=p.status({ontology_expansion:{matched:true,terms:['https://example.org/%E5%B9%B3%E5%8F%B0'],type_hits:3}});
  assert.match(legacy,/平台.*3 条类型候选/);
  assert.doesNotMatch(legacy,/实际补入|https?:/);
});

test('malformed expansion diagnostics are ignored safely and flat ontologies still report unmatched names',()=>{
  const p=api();
  for(const expansion of [[],{matched:true,terms:{},subclasses:'bad',entity_matches:[null,3,{},
    {text:{bad:true},type_label:[]}],type_hits:-3,used_hits:1.5},
    {matched:false,terms:null,subclasses:{},entity_matches:'bad',type_hits:'9'}]){
    let text;assert.doesNotThrow(()=>{text=p.status({ontology_expansion:expansion});});
    assert.doesNotMatch(text,/\[object Object\]|NaN|undefined|1\.5 条|-3 条/);
  }
  const flat=p.status({ontology_expansion:{matched:false,has_hierarchy:false}});
  assert.match(flat,/未匹配.*实体.*类名/);
  assert.doesNotMatch(flat,/无法扩展|不能扩展|无子类可扩展/);
});

test('search summary uses the same entity-to-type explanation as QA',()=>{
  const context={window:{}};vm.createContext(context);
  vm.runInContext(fs.readFileSync(file,'utf8'),context);
  const source=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  const declaration=source.match(/  function expansionSummary\(result\)\{[\s\S]*?\n  \}/)?.[0];
  assert.ok(declaration);vm.runInContext(declaration+';this.searchSummary=expansionSummary;',context);
  const expansion={matched:true,entity_matches:[{text:'美团',type_label:'互联网平台',match_kind:'name'}],
    terms:['urn:Platform'],literal_terms:[],subclasses:[],type_hits:6,used_hits:1};
  const summary=context.searchSummary({ontology_expansion:expansion});
  assert.match(summary,/美团 → 互联网平台.*6 条类型候选.*实际补入 1 条/);
  assert.doesNotMatch(summary,/urn:/);
  assert.equal(context.searchSummary({}),'');
});

test('search marks supplemental type hits while direct query hits retain their existing labels',()=>{
  const source=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  const declaration=source.match(/  function renderHits\(result\)\{[\s\S]*?\n  \}/)?.[0];
  assert.ok(declaration);
  const hits={innerHTML:'',querySelectorAll:()=>[]};
  const context={window:{},get:()=>hits,labelOf:value=>value,esc:value=>String(value)};
  vm.runInNewContext(declaration+';this.render=renderHits;',context);
  const rows=['entity','relation','chunk'].flatMap(kind=>[
    {kind,id:'direct-'+kind,subject_id:'root',text:'direct-'+kind,type:'平台',retrieval_channel:'milvus'},
    {kind,id:'extra-'+kind,subject_id:'root',text:'extra-'+kind,type:'平台',retrieval_channel:'ontology_entity_type'}]);
  context.render({hits:rows});
  const entries=hits.innerHTML.match(/<article\b[\s\S]*?<\/article>/g);
  assert.equal(entries.length,6);
  for(const kind of ['entity','relation','chunk']){
    assert.doesNotMatch(entries.find(html=>html.includes('direct-'+kind)),/类型扩展/);
    assert.match(entries.find(html=>html.includes('extra-'+kind)),/类型扩展/);
  }
});


test('expansion partitions require explicit channels and keep stable direct and supplement order',()=>{
  const p=api(),direct={id:'a',version_id:'a-v1'},entity={id:'b',version_id:'b-v1',retrieval_channel:'ontology_entity_type'},type={id:'c',version_id:'c-v1',retrieval_channel:'ontology_type'};
  assert.equal(p.isExpansionRow(entity),true);assert.equal(p.isExpansionRow(type),true);
  assert.equal(p.isExpansionRow({backend_ranks:{ontology:1}}),false);
  assert.equal(p.isExpansionRow(null),false);
  const result=p.partitionExpansion([entity,direct,type]);
  assert.deepEqual(Array.from(result.direct),[direct]);assert.deepEqual(Array.from(result.supplemental),[entity,type]);
});
test('partitioning deduplicates search versions, favors direct matches and retains distinct frozen citation IDs',()=>{
  const p=api(),extra={id:'a',version_id:'same',retrieval_channel:'ontology_type'},direct={id:'a',version_id:'same'};
  const result=p.partitionExpansion([extra,{id:'b',version_id:'b-v1'},direct,{...extra}]);
  assert.deepEqual(Array.from(result.direct).map(row=>row.id),['b','a']);assert.equal(result.supplemental.length,0);
  const citations=p.partitionExpansion([{id:'x',version_id:'v',citation:'E1'}, {id:'x',version_id:'v',citation:'U1',retrieval_channel:'ontology_type'}]);
  assert.equal(citations.direct[0].citation,'E1');assert.equal(citations.supplemental[0].citation,'U1');
  assert.equal(p.partitionExpansion(null).direct.length,0);
});
