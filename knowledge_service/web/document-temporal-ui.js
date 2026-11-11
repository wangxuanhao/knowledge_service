/* Shared, inert date and evidence summary for previews and saved documents. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.DocumentTemporalUI=api;
})(typeof window==='object'?window:globalThis,()=>{
  const labels={published_at:'原文发布时间',valid_from:'业务生效',valid_until:'业务失效',revised_at:'原文修订时间'};
  const statuses={manual:'手工填写',extracted:'正文识别',unknown:'来源未知',review:'待核验'};
  const effectiveSources={explicit:'手工填写',extracted:'正文识别'};
  const warnings={
    reversed_interval:'生效与失效时间顺序冲突，请核验原文。',
    expired_status_without_date:'原文标记了失效状态，但未识别到失效日期。',
    title_status_conflict:'标题中的失效日期与原文状态冲突，请核验。',
    invalid_supplied_revised_at:'原文修订时间格式无效，请核验。',
    invalid_legacy_revised_at:'原文修订时间格式无效，请核验。',
  };
  const kinds={header:'文首日期字段',first_effective:'首次生效',latest_effective:'最近生效',
    self_effective:'本文生效条款',self_expiry:'本文失效条款',self_revision:'本文修订条款',
    validity_range:'本文有效期',valid_through:'有效截止日（含当日）',title_expiry:'标题失效时间'};
  function text(value){
    if(value===null||value===undefined)return '';
    try{
      return typeof value==='object'||typeof value==='function'?JSON.stringify(value)||'无法显示的数据':String(value);
    }catch{return '无法显示的数据';}
  }
  const escape=value=>text(value).replace(/[&<>"']/g,char=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;',
  }[char]));
  const formatter=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',
    day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  function dateLabel(value){
    if(value===null||value===undefined||value==='')return '';
    if(typeof value!=='string'&&typeof value!=='number')return text(value);
    const date=new Date(value);
    return Number.isFinite(date.getTime())?formatter.format(date):text(value);
  }
  function position(candidate){
    const start=candidate?.start_char,end=candidate?.end_char;
    return Number.isInteger(start)&&Number.isInteger(end)&&start>=0&&end>=start?
      '解析正文位置 '+start+'–'+end:'';
  }
  function evidence(candidate){
    const parts=[candidate.raw,position(candidate)];
    if(candidate.kind)parts.push(typeof candidate.kind==='string'&&Object.hasOwn(kinds,candidate.kind)?kinds[candidate.kind]:candidate.kind);
    if(candidate.value)parts.push('识别值 '+dateLabel(candidate.value));
    if(candidate.timezone)parts.push(candidate.timezone);
    return parts.filter(Boolean).map(escape).join(' · ');
  }
  function warningLabel(value){
    if(typeof value!=='string')return text(value);
    if(Object.hasOwn(warnings,value))return warnings[value];
    const field=value.replace(/_candidate_limit$/,'');
    return Object.hasOwn(labels,field)&&value.endsWith('_candidate_limit')?
      labels[field]+'候选日期过多，请核验。':value;
  }
  function render(input){
    const record=input?.document_time||input||{},metadata=record.metadata||{};
    const temporal=record.document_temporal||metadata.document_temporal||{},fields=temporal.fields||{};
    const effectiveSource=record.effective_time_source||metadata.effective_time_source;
    const rows=[],details=[];
    for(const [name,label] of Object.entries(labels)){
      const field=fields[name]||{};
      const metadataDate=['published_at','revised_at'].includes(name)&&Object.hasOwn(metadata,name);
      const value=Object.hasOwn(record,name)?record[name]:metadataDate?metadata[name]:field.value;
      const candidates=Array.isArray(field.candidates)?field.candidates.filter(candidate=>candidate&&typeof candidate==='object'):[];
      if(name==='revised_at'&&!value&&!candidates.length&&!field.supplied_value&&(!field.status||field.status==='unknown'))continue;
      let status=typeof field.status==='string'&&Object.hasOwn(statuses,field.status)?statuses[field.status]:'来源未知';
      if(name==='valid_from'&&effectiveSource==='published_at')status='发布时间推定';
      else if(name==='valid_from'&&!field.status&&typeof effectiveSource==='string'&&Object.hasOwn(effectiveSources,effectiveSource))status=effectiveSources[effectiveSource];
      const display=dateLabel(value)||(name==='valid_until'?'未设结束时间':'未知');
      rows.push('<p><strong>'+label+'</strong> · '+escape(display)+'（'+escape(status)+'）</p>');
      for(const candidate of candidates)details.push('<p><strong>'+label+'</strong> · '+evidence(candidate)+'</p>');
      if(field.supplied_value)details.push('<p><strong>'+label+'</strong> · 待核验输入 '+escape(field.supplied_value)+'</p>');
    }
    const sourceStatus=temporal.source_status;
    if(sourceStatus){
      const candidate=typeof sourceStatus==='object'?sourceStatus:{raw:sourceStatus};
      rows.push('<p>原文状态 · '+escape(candidate.raw)+'</p>');
      details.push('<p>原文状态 · '+evidence(candidate)+'</p>');
    }
    for(const warning of Array.isArray(temporal.warnings)?temporal.warnings:[]){
      rows.push('<p>待核验 · '+escape(warningLabel(warning))+'</p>');
    }
    const basis=details.length?'<details class="source-technical"><summary>识别依据</summary>'+details.join('')+'</details>':'';
    return '<div class="source-meta__mapping"><h4>文档时间 · Asia/Shanghai</h4>'+rows.join('')+basis+'</div>';
  }
  return {render};
});
