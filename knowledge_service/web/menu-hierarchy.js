/* Group peer task buttons into discoverable navigation sections without changing tab behavior. */
(()=>{
  const nav=document.querySelector('aside nav');if(!nav)return;
  const groups=[
    ['项目',['projects','dashboard']],
    ['探索与展示',['search','qa','mindmap','sources']],
    ['建模与治理',['ingest','candidate-mindmap','discovery','ontology','reviews','records']],
    ['运行与质量',['jobs','evaluation']],
  ];
  const buttons=new Map([...nav.querySelectorAll('button[data-tab]')].map(button=>[button.dataset.tab,button]));
  nav.replaceChildren();
  for(const [label,tabs] of groups){
    const available=tabs.map(tab=>buttons.get(tab)).filter(Boolean).filter(button=>!button.classList.contains('hidden'));if(!available.length)continue;
    const group=document.createElement('details');group.className='nav-group';group.open=available.some(button=>button.classList.contains('active'));
    const summary=document.createElement('summary');summary.textContent=label;group.append(summary,...available);nav.append(group);
    available.forEach(button=>button.addEventListener('click',()=>{group.open=true;document.querySelectorAll('.nav-group').forEach(other=>{if(other!==group)other.open=false;});}));
  }
  const resolve=document.getElementById('parse-resolve');
  if(resolve?.parentElement)resolve.parentElement.lastChild.textContent='同名／别名实体消歧（稳定类型 IRI 和有效期需兼容）';
  const governance=document.querySelector('.governance-note');
  if(governance)governance.textContent='Semantica EntityMerger · 稳定类型 IRI 与业务区间兼容时可跨本体版本融合，来源和版本轨迹完整保留。';
})();
