/* Restricted answer formatting: source text never becomes HTML or a URL. */
(() => {
  function inline(text) {
    const parts=[], pattern=/\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([EURF][1-9][0-9]*)\]/g;
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
  const duration=value=>Number(value)>=1000?(Number(value)/1000).toFixed(2)+' s':Math.round(Number(value))+' ms';
  function status(payload={}) {
    const state=payload.generation||{},times=payload.timings_ms||{};
    let text=state.called?(payload.mode==='evidence_only'?'模型已调用 · 仅展示依据':'模型回答'):'仅展示依据';
    if(payload.answer_purpose==='document_explanation')text+=' · '+(payload.answer_label||'按文档说明，有效性待核对');
    if(state.requested&&!state.called)text+=' · 未调用模型：'+(state.skip_reason==='no_eligible_evidence'?'暂无确认有效的依据':'未找到回答依据');
    if(Number.isFinite(times.total))text+=' · 总耗时 '+duration(times.total);
    for(const [key,label] of [['retrieval','检索'],['eligibility','有效性检查'],['freeze','证据保存'],['llm','模型']])
      if(Number.isFinite(times[key]))text+=' · '+label+' '+duration(times[key]);
    return text;
  }
  function annotate(turn,payload) {
    let note=turn.querySelector('.qa-answer-status');
    if(!note){note=document.createElement('p');note.className='qa-answer-status';turn.prepend(note);}
    note.textContent=payload.generation||payload.timings_ms?status(payload):'历史回答';
  }
  window.QaPresentation={inline,blocks,render,status,annotate};
})();
