/* Restricted answer formatting: source text never becomes HTML or a URL. */
(() => {
  function inline(text) {
    const parts=[], pattern=/\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([EURF][1-9][0-9]*|E-[a-z0-9]+)\]/gi;
    let cursor=0, match;
    while((match=pattern.exec(String(text)))) {
      if(match.index>cursor)parts.push({type:'text',text:text.slice(cursor,match.index)});
      parts.push({type:match[1]?'strong':match[2]?'code':'citation',text:match[1]||match[2]||match[0]});
      cursor=pattern.lastIndex;
    }
    if(cursor<text.length)parts.push({type:'text',text:text.slice(cursor)});
    return parts;
  }
  function blocks(text) {
    return String(text||'').split(/\r?\n/).filter(line=>line.trim()).map(line=>{
      const heading=line.match(/^#{1,6}\s+(.+)$/), item=line.match(/^\s*(?:[-*]|\d+[.)、])\s+(.+)$/);
      return {tag:heading?'h3':item?'li':'p',text:heading?.[1]||item?.[1]||line};
    });
  }
  function render(container,text,citation) {
    container.replaceChildren();let list=null;
    for(const block of blocks(text)) {
      const node=document.createElement(block.tag);
      for(const token of inline(block.text)) {
        let part;
        if(token.type==='citation')part=citation?.(token.text);
        if(!part&&['strong','code'].includes(token.type)){part=document.createElement(token.type);part.textContent=token.text;}
        node.append(part||document.createTextNode(token.text));
      }
      if(block.tag==='li') {
        if(!list){list=document.createElement('ul');container.append(list);}
        list.append(node);
      }else{list=null;container.append(node);}
    }
  }
  function sourceTitle(value) {
    const title=String(value||'').trim();
    const markdown=title.match(/^(.*)\.(?:md|markdown)$/i);
    if(!markdown)return title;
    const name=markdown[1];
    const path=name.match(/^业务规则_[^_]+_[^_]+_(.+)$/);
    return path?.[1]||name;
  }
  function evidencePreview(text) {
    const lines=blocks(text),body=lines.filter(line=>line.tag!=='h3');
    const preview=(body.length?body:lines).map(line=>inline(line.text).map(token=>token.text).join(''))
      .join(' ').replace(/\s+/g,' ').trim();
    return text?.length===240&&preview&&!/[。！？.!?…]$/.test(preview)?preview+'…':preview;
  }
  function distinctSourceQuote(sourceQuote, evidenceText) {
    const quote=evidencePreview(String(sourceQuote||'').slice(0,240));
    const comparable=quote.replace(/…$/,'');
    const excerpt=evidencePreview(evidenceText).replace(/…$/,'');
    return comparable&&!excerpt.startsWith(comparable)?quote:'';
  }
  function isEvidenceTranscript(payload) {
    if(payload.mode!=='evidence_only'||payload.error||!payload.answer_id||!Array.isArray(payload.evidence))return false;
    let text=String(payload.text||'').trimStart();
    const label=payload.answer_label&&payload.answer_label+'。';
    if(label&&text.startsWith(label+'\n\n'))text=text.slice(label.length+2);
    const match=text.match(/^\[(E[1-9][0-9]*)\][ \t]+/);
    if(!match)return false;
    const row=payload.evidence.find(item=>item?.citation===match[1]);
    const excerpt=String(row?.text_preview||row?.text||'').trimStart().slice(0,96);
    const original=text.slice(match[0].length).trimStart();
    return !!excerpt&&(original.startsWith(excerpt)||
      original.startsWith(excerpt.replace(/\[([A-Z][0-9]+)\]/g,'［$1］')));
  }
  const duration=value=>Number(value)>=1000?(Number(value)/1000).toFixed(2)+' s':Math.round(Number(value))+' ms';
  function expansionSummary(expansion) {
    if(!expansion||typeof expansion!=='object'||Array.isArray(expansion))return '';
    const strings=value=>Array.isArray(value)?value.filter(item=>typeof item==='string'&&item.trim()).slice(0,100):[];
    const label=value=>{
      if(typeof value!=='string')return '';
      const trimmed=value.trim();
      const text=/^[a-z][a-z0-9+.-]*:/i.test(trimmed)?trimmed.split(/[#/:]/).filter(Boolean).pop()||'':trimmed;
      try{return decodeURIComponent(text);}catch{return text;}
    };
    const groups=new Map(),entityTypes=new Set(strings(expansion.entity_terms));
    for(const match of Array.isArray(expansion.entity_matches)?expansion.entity_matches.slice(0,8):[]){
      if(!match||typeof match!=='object'||Array.isArray(match))continue;
      const name=label(match.text)||label(match.matched_name),type=label(match.type_label)||label(match.type);
      if(!name||!type)continue;
      const alias=match.match_kind==='alias'?label(match.matched_name):'';
      const display=alias&&alias!==name?`${alias}（别名，实体：${name}）`:name;
      const key=(typeof match.id==='string'?match.id:name)+'\u0000'+display;
      if(!groups.has(key))groups.set(key,{display,types:[]});
      if(!groups.get(key).types.includes(type))groups.get(key).types.push(type);
      if(typeof match.type==='string')entityTypes.add(match.type);
    }
    const paths=[...groups.values()].map(group=>group.display+' → '+group.types.join('／'));
    const literalTerms=Array.isArray(expansion.literal_terms)?strings(expansion.literal_terms):strings(expansion.terms).filter(term=>!entityTypes.has(term));
    const matchedNames=strings(expansion.matched_terms);
    const classes=[...new Set((matchedNames.length===literalTerms.length?matchedNames:literalTerms).map(label).filter(Boolean))];
    if(!expansion.matched&&!paths.length&&!classes.length)return '本体扩展：未匹配到可扩展的实体或已发布类名，按原检索执行'
      +(expansion.has_hierarchy===false?'（已发布本体暂无父子层级）':'');
    const details=[];
    if(paths.length)details.push(paths.join('；'));
    if(classes.length)details.push('类名：'+classes.join('、'));
    if(!details.length)details.push('已匹配类型');
    const subclasses=strings(Array.isArray(expansion.subclasses)?expansion.subclasses:expansion.expanded_subclasses);
    details.push(new Set(subclasses).size+' 个子类');
    const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
    if(count(expansion.type_hits)!==null)details.push(expansion.type_hits+' 条类型候选');
    if(count(expansion.used_hits)!==null)details.push('实际补入 '+expansion.used_hits+' 条');
    if(paths.length)details.push('补充相关类型内容');
    return '本体扩展：'+details.join('，');
  }
  function isExpansionRow(row) {
    return !!row&&typeof row==='object'&&!Array.isArray(row)&&
      (row.retrieval_channel==='ontology_entity_type'||row.retrieval_channel==='ontology_type');
  }
  function partitionExpansion(rows) {
    const items=Array.isArray(rows)?rows.filter(row=>row&&typeof row==='object'&&!Array.isArray(row)):[];
    const identity=row=>row.citation?JSON.stringify(['citation',row.citation]):row.version_id?JSON.stringify(['version',row.version_id]):
      row.id?JSON.stringify(['record',row.kind||'',row.id,row.version??null]):row;
    const directKeys=new Set(items.filter(row=>!isExpansionRow(row)).map(identity));
    const direct=[],supplemental=[],seen=new Set();
    for(const row of items){
      const key=identity(row),expanded=isExpansionRow(row);
      if(seen.has(key)||(expanded&&directKeys.has(key)))continue;
      seen.add(key);(expanded?supplemental:direct).push(row);
    }
    return {direct,supplemental};
  }
  function status(payload={},showTimings=true) {
    const state=payload.generation||{},times=payload.timings_ms||{};
    let text=state.called?(payload.mode==='evidence_only'?'模型已调用 · 仅展示依据':'模型回答'):'仅展示依据';
    if(payload.answer_purpose==='document_explanation')text+=' · '+(payload.answer_label||'按文档说明，有效性待核对');
    if(state.requested&&!state.called)text+=' · 未调用模型：'+(state.skip_reason==='no_eligible_evidence'?'暂无确认有效的依据':'未找到回答依据');
    if(showTimings){
      if(Number.isFinite(times.total))text+=' · 总耗时 '+duration(times.total);
      for(const [key,label] of [['retrieval','检索'],['eligibility','有效性检查'],['freeze','证据保存'],['llm','模型']])
        if(Number.isFinite(times[key]))text+=' · '+label+' '+duration(times[key]);
    }
    const expansion=expansionSummary(payload.ontology_expansion);
    if(expansion)text+=' · '+expansion;
    return text;
  }
  function annotate(turn,payload) {
    let note=turn.querySelector('.qa-answer-status');
    if(!note){note=document.createElement('p');note.className='qa-answer-status';turn.prepend(note);}
    note.textContent=payload.generation||payload.timings_ms||payload.ontology_expansion?status(payload,false):'历史回答';
    if(payload.timings_ms)note.title=status(payload);
    const panel=turn.querySelector('.qa-evidence-panel'),answer=turn.querySelector('.qa-answer');
    if(!panel||!answer||!isEvidenceTranscript(payload))return;
    if(panel.hidden)turn.querySelector('.qa-evidence-toggle')?.onclick();
    const full=document.createElement('details');full.className='qa-full-transcript';
    const label=document.createElement('summary');label.textContent='完整命中片段';
    full.append(label,answer);turn.append(full);turn.dataset.result='evidence';
    if(payload.generation?.requested===false)note.textContent='检索结果';
  }
  window.QaPresentation={inline,blocks,render,sourceTitle,evidencePreview,distinctSourceQuote,isEvidenceTranscript,
    expansionSummary,isExpansionRow,partitionExpansion,status,annotate};
})();
