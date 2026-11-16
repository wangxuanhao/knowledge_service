/* Relevance controls are included in search requests, separate from scope. */
(function(root){
  function read(page){
    const strategy=document.getElementById(page+'-hybrid-strategy')?.value||'keyword_rerank';
    const input=document.getElementById(page+'-min-semantic-score');
    const threshold=input&&input.value!==''?Number(input.value):.35;
    if(!Number.isFinite(threshold)||threshold < -1||threshold > 1)throw Error('最低语义相似度需在 -1 到 1 之间');
    return {hybrid_strategy:strategy,min_semantic_score:threshold};
  }
  function install(){
    for(const [page,selector] of [['search','.search-options-panel'],['qa','.qa-settings-panel'],['source','#source-search-form']]){
      const host=document.querySelector(selector);if(!host||document.getElementById(page+'-hybrid-strategy'))continue;
      const panel=document.createElement('div');panel.className='retrieval-relevance-controls';
      const label=document.createElement('label');label.textContent='混合策略';
      const select=document.createElement('select');select.id=page+'-hybrid-strategy';
      for(const [value,text] of [['keyword_rerank','关键词筛选 → 语义排序'],['fusion','关键词＋语义分别召回后融合']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
      }label.append(select);
      const scoreLabel=document.createElement('label');scoreLabel.textContent='最低语义相似度';
      const input=document.createElement('input');input.id=page+'-min-semantic-score';input.type='number';input.min='-1';input.max='1';input.step='.05';input.value='.35';scoreLabel.append(input);
      const hint=document.createElement('small');hint.textContent='仅影响语义召回；数值越高越严格。关键词候选最多 1000 条，余弦相似度不是命中概率。';hint.id=page+'-relevance-help';input.setAttribute('aria-describedby',hint.id);select.setAttribute('aria-describedby',hint.id);
      panel.append(label,scoreLabel,hint);
      if(page==='source'){
        const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='检索设置';details.className='source-relevance-settings';details.append(summary,panel);host.append(details);
        panel.addEventListener('change',()=>document.dispatchEvent(new CustomEvent('sources:relevance-changed')));
      }else host.prepend(panel);
    }
  }
  function write(page,policy){
    const strategy=document.getElementById(page+'-hybrid-strategy'),threshold=document.getElementById(page+'-min-semantic-score');
    if(strategy)strategy.value=policy.hybrid_strategy||'fusion';
    if(threshold)threshold.value=policy.min_semantic_score??.35;
  }
  root.SearchRelevance={read,write};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})(window);
