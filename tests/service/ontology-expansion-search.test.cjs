const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const web=path.resolve(__dirname,'../../knowledge_service/web');
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function harness({helpers=true}={}){
  const buttons=new Map(),graphSelections=[],sourceSelections=[];
  const hits={innerHTML:'',querySelectorAll(selector){
    const attribute=selector.slice(1,-1),key=attribute==='data-graph-node'?'graphNode':'sourceRecord';
    const elements=[...this.innerHTML.matchAll(new RegExp(attribute+'="([^"]+)"','g'))].map(match=>({dataset:{[key]:match[1]}}));
    buttons.set(attribute,elements);return elements;
  }};
  const context={window:{},get:()=>hits,labelOf:value=>value,esc:escape,
    selectEntityDetail:async id=>graphSelections.push(id),historyFor:row=>sourceSelections.push(row.id),status:()=>{}};
  vm.createContext(context);
  if(helpers)vm.runInContext(fs.readFileSync(path.join(web,'qa-presentation.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(web,'workspace.js'),'utf8');
  const declaration=source.match(/  function renderHits\(result\)\{[\s\S]*?\n  \}/)?.[0];
  assert.ok(declaration,'search renderer remains independently testable');
  vm.runInContext(declaration+';this.render=renderHits;',context);
  return {render:context.render,hits,buttons,graphSelections,sourceSelections};
}
function region(html,group){
  const start=html.indexOf('data-expansion-group="'+group+'"');
  assert.notEqual(start,-1,'visible '+group+' result region');
  const next=html.indexOf('data-expansion-group="',start+1);
  const context=html.indexOf('ontology-result-context',start+1);
  return html.slice(start,Math.min(...[next,context,html.length].filter(index=>index>=0)));
}
const row=(kind,id,channel='milvus')=>({kind,id,text:id,type:'平台',source_id:kind==='chunk'?'document-'+id:undefined,
  subject_id:kind==='relation'?'subject-'+id:undefined,retrieval_channel:channel,score:0.75});
const ledger={matched:true,entity_matches:[{id:'meituan',text:'美团',type_label:'互联网平台',match_kind:'name'}],
  literal_terms:[],subclasses:['urn:DeliveryPlatform'],type_hits:5,used_hits:2};

test('expansion off retains the existing three channel columns and provenance labels',()=>{
  const subject=harness();
  subject.render({hits:['entity','chunk','relation'].map(kind=>row(kind,'direct-'+kind)),
    source_chunks:[row('chunk','source')],context_chunks:[row('chunk','neighbor')],
    channel_quotas:{entity:2,chunk:4,relation:3}});
  const html=subject.hits.innerHTML;
  assert.doesNotMatch(html,/data-expansion-group|ontology-result-section/);
  assert.match(html,/匹配实体 <small>1 \/ 2/);
  assert.match(html,/原文片段 <small>3 \/ 4/);
  assert.match(html,/关系链路 <small>1 \/ 3/);
  assert.match(html,/命中知识的来源原文/);
  assert.match(html,/命中条款的前后文（不参与排名）/);
});

test('enabled search separates entity and literal class supplements with actual counts and stable channel order',()=>{
  const subject=harness();
  subject.render({hits:[row('entity','direct-entity'),row('entity','expanded-entity','ontology_entity_type'),
    row('chunk','direct-chunk'),row('chunk','expanded-chunk','ontology_type'),
    row('relation','direct-relation'),row('relation','expanded-relation','ontology_type')],ontology_expansion:ledger});
  const html=subject.hits.innerHTML,direct=region(html,'direct'),supplemental=region(html,'supplemental');
  assert.match(direct,/直接命中 <small>3 条/);
  assert.match(supplemental,/本体扩展补充 <small>3 条/);
  assert.doesNotMatch(direct,/expanded-/);
  assert.doesNotMatch(supplemental,/direct-/);
  for(const kind of ['entity','chunk','relation'])assert.match(supplemental,new RegExp('expanded-'+kind));
  assert.ok(direct.indexOf('direct-entity')<direct.indexOf('direct-chunk'));
  assert.ok(direct.indexOf('direct-chunk')<direct.indexOf('direct-relation'));
  assert.match(supplemental,/美团 → 互联网平台/);
  assert.match(supplemental,/5 条类型候选/);
  assert.doesNotMatch(html,/<details\b/,'both search result regions are visible by default');
  assert.equal((supplemental.match(/类型扩展/g)||[]).length,3);
});

test('enabled zero supplements keep a visible diagnostic for unmatched names, empty candidates and final ranking',()=>{
  for(const [expansion,explanation] of [
    [{matched:false,has_hierarchy:false,type_hits:0,used_hits:0},/未匹配.*实体.*类名/],
    [{...ledger,type_hits:0,used_hits:0},/当前范围.*类型.*内容/],
    [{...ledger,type_hits:5,used_hits:0},/已找到类型候选，本次未补入新结果/],
  ]){
    const subject=harness();subject.render({hits:[row('entity','direct')],ontology_expansion:expansion});
    const html=region(subject.hits.innerHTML,'supplemental');
    assert.match(html,/本体扩展补充 <small>0 条/);
    assert.match(html,/ontology-result-empty/);
    assert.match(html,explanation);
    assert.doesNotMatch(html,/候选可能|未进入最终排名/);
    assert.doesNotMatch(html,/<details\b/);
  }
});

test('legacy explicit markers still split safely without presentation helpers or a ledger',()=>{
  const subject=harness({helpers:false});
  subject.render({hits:[row('entity','legacy-direct'),row('entity','legacy-entity','ontology_entity_type'),
    row('chunk','legacy-class','ontology_type')]});
  assert.match(region(subject.hits.innerHTML,'direct'),/legacy-direct/);
  const supplemental=region(subject.hits.innerHTML,'supplemental');
  assert.match(supplemental,/本体扩展补充 <small>2 条/);
  assert.match(supplemental,/legacy-entity/);
  assert.match(supplemental,/legacy-class/);
});

test('legacy marked supplements remain visible without inventing unmatched diagnostics',()=>{
  const subject=harness();
  subject.render({hits:[row('entity','legacy-entity','ontology_entity_type')]});
  const supplemental=region(subject.hits.innerHTML,'supplemental');
  assert.match(supplemental,/本体扩展补充 <small>1 条/);
  assert.doesNotMatch(supplemental,/未匹配/);
});

test('duplicate candidates and shared source context render once outside ranked result totals',()=>{
  const subject=harness(),direct=row('entity','canonical'),extra=row('relation','supplement','ontology_entity_type');
  const source=row('chunk','shared-source'),neighbor=row('chunk','shared-neighbor'),ranked=row('chunk','ranked');
  subject.render({hits:[direct,{...direct,retrieval_channel:'ontology_type'},extra,{...extra},ranked],
    ontology_expansion:ledger,source_chunks:[source,{...source},ranked],context_chunks:[source,neighbor,{...neighbor}]});
  const html=subject.hits.innerHTML;
  assert.match(region(html,'direct'),/直接命中 <small>2 条/);
  assert.match(region(html,'supplemental'),/本体扩展补充 <small>1 条/);
  assert.equal((html.match(/<article\b/g)||[]).length,5);
  const context=html.slice(html.indexOf('ontology-result-context'));
  assert.match(context,/来源与上下文/);
  assert.match(context,/不参与排名/);
  assert.match(context,/shared-source/);assert.match(context,/shared-neighbor/);
  assert.doesNotMatch(context,/canonical|supplement|>ranked</);
  assert.equal((context.match(/<article\b/g)||[]).length,2);
});

test('separate result and context cards preserve graph/source actions and escape diagnostic content',async()=>{
  const subject=harness();
  subject.render({hits:[row('entity','direct'),row('relation','expanded','ontology_type')],
    source_chunks:[row('chunk','source')],context_chunks:[row('chunk','neighbor')],
    ontology_expansion:{...ledger,entity_matches:[{text:'<img src=x>',type_label:'<script>alert(1)</script>'}]}});
  for(const button of subject.buttons.get('data-graph-node'))await button.onclick();
  for(const button of subject.buttons.get('data-source-record'))button.onclick();
  assert.deepEqual(subject.graphSelections,['direct','subject-expanded']);
  assert.deepEqual(subject.sourceSelections,['document-source','document-neighbor']);
  assert.match(subject.hits.innerHTML,/&lt;img src=x&gt; → &lt;script&gt;/);
  assert.doesNotMatch(subject.hits.innerHTML,/<img|<script/);
});


test('enabled result sections omit empty channels so the supplemental module is not buried under empty panels',()=>{
  const subject=harness();subject.render({hits:[row('entity','direct'),row('entity','extra','ontology_type')],ontology_expansion:ledger});
  const html=subject.hits.innerHTML;
  assert.doesNotMatch(region(html,'direct'),/原文片段|关系链路|暂无命中/);
  assert.doesNotMatch(region(html,'supplemental'),/原文片段|关系链路|暂无命中/);
});
