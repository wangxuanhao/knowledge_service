(function(root){
  const fieldNames={title:'标题',filename:'文件名',body:'正文',identifier:'编号',aliases:'别名',
    entity_name:'实体名称',relation_text:'关系文本',attribute_value:'属性值',owner_name:'所属实体名称',
    property_name:'属性名称',record_text:'记录文本'};
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const scoreText=value=>String(Number(value.toPrecision(5)));
  function rankBadges(match){
    const channels=Array.isArray(match.channels)?match.channels:[],badges=[];
    if(channels.includes('keyword')||(match.mode==='keyword'&&!match.supplemental))badges.push('关键词匹配 1');
    const score=finite(match.rrf_score)?match.rrf_score:match.score;
    if(!finite(score)||match.mode==='keyword'&&!finite(match.rrf_score))return badges;
    const label=finite(match.rrf_score)?'融合分':
      match.mode==='semantic'?'语义相似度':match.mode==='hybrid'?
        (match.strategy==='keyword_rerank'?'语义重排':'融合分'):'检索分';
    badges.push(label+' '+scoreText(score));
    return badges;
  }
  function hitLabels(match){
    const name=field=>field==='body'?'正文片段':fieldNames[field];
    const fields=(match.fields||[]).map(name).filter(Boolean);
    const indexed=(match.indexed_fields||[]).map(name).filter(Boolean);
    const location=fields.length?fields.map(field=>field+(match.mode==='keyword'?'命中':'含查询文字')):
      indexed.length?['参与检索：'+indexed.join('、')]:[];
    return [...rankBadges(match),...location];
  }
  function recordMatch(row,query,{mode,strategy}={}){
    const name={entity:'entity_name',relation:'relation_text',chunk:'body',attribute:'attribute_value'}[row.kind]||'record_text';
    const attributes=[[name,row.matched_value??row.text]];
    if(mode==='keyword')attributes.push(['identifier',row.id],['aliases',
      (Array.isArray(row.metadata?.aliases)?row.metadata.aliases:[]).join('\n')]);
    const term=String(query||'').trim().toLowerCase();
    return {mode,strategy,channels:row.match_channels||[],score:row.score,rrf_score:row.rrf_score,
      fields:term?attributes.filter(([,value])=>String(value??'').toLowerCase().includes(term)).map(([field])=>field):[],
      indexed_fields:attributes.map(([field])=>field)};
  }
  function describe(match){
    const channels=match.channels||[],keyword=match.mode==='keyword';
    const label=keyword?'关键词匹配':match.mode==='semantic'?'语义相关':match.strategy==='keyword_rerank'?'关键词候选 · 语义排序':
      channels.includes('keyword')&&channels.includes('semantic')?'关键词＋语义召回':channels.includes('keyword')?'仅关键词召回':channels.includes('semantic')?'仅语义召回':'混合检索（通道未记录）';
    return {label,highlight:keyword,fields:(match.fields||[]).map(field=>(fieldNames[field]||field)+(keyword?'命中':'含查询文字')),
      note:keyword?'黄色只表示实际出现的查询文字。':'标题、文件名和片段共同参与相似度计算，不能据此判断标题或正文各自的贡献；相关片段不代表逐字命中。'};
  }
  function appendText(host,text,term,highlight){
    text=String(text||'');term=String(term||'');
    if(!highlight||!term){host.append(document.createTextNode(text));return;}
    let start=0,index;
    while((index=text.toLowerCase().indexOf(term.toLowerCase(),start))>=0){
      host.append(document.createTextNode(text.slice(start,index)));
      const mark=document.createElement('mark');mark.textContent=text.slice(index,index+term.length);host.append(mark);start=index+term.length;
    }
    host.append(document.createTextNode(text.slice(start)));
  }
  function render(host,match,{detail=false}={}){
    const model=describe(match);host.dataset.matchMode=match.mode;
    const labels=document.createElement('span');labels.className='source-match-labels';
    const scores=rankBadges(match);
    if(match.mode!=='keyword')scores.unshift(model.label);
    for(const text of [...scores,...model.fields]){const label=document.createElement('span');label.textContent=text;labels.append(label);}host.append(labels);
    if(match.text){const snippet=document.createElement('span');snippet.className='source-match-text';
      const prefix=document.createElement('strong');prefix.textContent=model.highlight?'命中上下文：':'相关片段：';snippet.append(prefix);
      appendText(snippet,match.text,match.query,model.highlight);host.append(snippet);}
    if(detail){const note=document.createElement('p');note.className='subtle';note.textContent=model.note;host.append(note);}
  }
  const api={describe,appendText,render,rankBadges,hitLabels,recordMatch};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SourceMatchView=api;
})(typeof window==='undefined'?globalThis:window);
