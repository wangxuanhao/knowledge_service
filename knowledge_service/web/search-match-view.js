(function(root){
  const fieldNames={title:'标题',filename:'文件名',body:'正文',identifier:'资料编号',aliases:'别名'};
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
    for(const text of [model.label,...model.fields]){const label=document.createElement('span');label.textContent=text;labels.append(label);}host.append(labels);
    if(match.text){const snippet=document.createElement('span');snippet.className='source-match-text';
      const prefix=document.createElement('strong');prefix.textContent=model.highlight?'命中上下文：':'相关片段：';snippet.append(prefix);
      appendText(snippet,match.text,match.query,model.highlight);host.append(snippet);}
    if(detail){const note=document.createElement('p');note.className='subtle';note.textContent=model.note;host.append(note);}
  }
  const api={describe,appendText,render};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SourceMatchView=api;
})(typeof window==='undefined'?globalThis:window);
