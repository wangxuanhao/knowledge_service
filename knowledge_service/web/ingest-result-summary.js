/* Counts from the saved task result, never from a different document/run read. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.IngestResultSummary=api;})(typeof window==='object'?window:globalThis,()=>{
  const kinds=['entity','relation','attribute'];
  const unique=items=>[...new Map(items.map((item,index)=>[item.id||'row-'+index,item])).values()];
  function details(rows){return `实体 ${rows.filter(item=>item.kind==='entity').length} · 关系 ${rows.filter(item=>item.kind==='relation').length} · 属性 ${rows.filter(item=>item.kind==='attribute').length}`;}
  function text(result){
    if(!result)return '';
    const parts=[],metadata=result.document?.metadata||{};
    if(result.replayed)parts.push('本次复用已保存结果，未重复写入');
    else if(Array.isArray(result.records)){const rows=unique(result.records.filter(item=>kinds.includes(item.kind))),unknown=result.records.filter(item=>!kinds.includes(item.kind)&&!['document','chunk'].includes(item.kind)).length;
      if(unknown){if(rows.length)parts.push(`已知种类知识 ${rows.length} 条（${details(rows)}）`);parts.push(`${unknown} 条写入结果未记录知识种类，完整知识数量待核对`);}
      else parts.push(`本次写入知识 ${rows.length} 条（${details(rows)}）`);}
    let pending=false;
    for(const [key,countKey,label] of [['discovery_candidates','discovery_candidates','开放发现候选'],['review_candidates','pending_reviews','待审核候选']]){
      const rows=Array.isArray(metadata[key])?unique(metadata[key]):null,scalar=result[countKey];
      if(rows){const facts=rows.filter(item=>kinds.includes(item.kind)),other=rows.filter(item=>['exception','validation'].includes(item.kind)).length,unknown=rows.length-facts.length-other;
        if(facts.length||unknown){parts.push(unknown?`${label} ${facts.length+unknown} 条（${unknown} 条种类未记录；已知 ${details(facts)}）`:`${label} ${facts.length} 条（${details(facts)}）`);pending=true;}
        if(other)parts.push(`其他审核事项 ${other} 项`);
        if(Number.isInteger(scalar)&&scalar!==rows.length&&scalar!==facts.length)parts.push(`${label}总数记录为 ${scalar}，与保存的明细不一致，请核对处理记录`);
      }else if(Number.isInteger(scalar)&&scalar>0){parts.push(`${label} ${scalar} 条（未记录种类明细）`);pending=true;}
    }
    if(pending)parts.push('候选在抽取时尚未正式入库，当前状态以知识台账或草案为准');
    return parts.join('；')||'此任务未记录可核对的结果数量，请查看处理记录';
  }
  const hasCandidates=result=>!!result&&(result.discovery_candidates>0||result.pending_reviews>0||['discovery_candidates','review_candidates'].some(key=>Array.isArray(result.document?.metadata?.[key])&&result.document.metadata[key].length>0));
  const hasKnowledge=result=>hasCandidates(result)||!!result?.replayed||!!result?.records?.some(item=>!['document','chunk'].includes(item.kind));
  return {text,hasCandidates,hasKnowledge};
});
