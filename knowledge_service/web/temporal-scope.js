/* UI modes resolve to the existing API time axes only when a draft is applied. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.TemporalScope=api;})(typeof window==='object'?window:globalThis,function(){
  const localPattern=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/;
  function localTime(value){
    if(!value)return '';
    const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';
    return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,23);
  }
  function utc(value,label){
    const invalid=()=>{throw Error(label+'无效，请选择有效日期后应用筛选；原条件已保留');};
    if(value==null||value==='')invalid();
    const date=new Date(value);if(!Number.isFinite(date.getTime()))invalid();
    // Date can roll February 30 into March. Local form values must round-trip.
    if(typeof value==='string'&&!/[zZ]|[+-]\d{2}:?\d{2}$/.test(value)){
      const match=value.match(localPattern);
      if(!match)invalid();
      const expected=[...match.slice(1,6).map(Number),Number(match[6]||0),Number((match[7]||'').padEnd(3,'0'))];
      const actual=[date.getFullYear(),date.getMonth()+1,date.getDate(),date.getHours(),date.getMinutes(),date.getSeconds(),date.getMilliseconds()];
      if(actual.some((part,index)=>part!==expected[index]))invalid();
    }
    return date.toISOString();
  }
  function localKey(value){
    const match=typeof value==='string'?value.match(localPattern):null;
    return match?match.slice(1,6).join(':')+':'+(match[6]||'00')+'.'+(match[7]||'').padEnd(3,'0'):null;
  }
  const sameLocal=(value,previous)=>{const key=localKey(value);return key!==null&&key===localKey(previous);};
  function capture({mode='all',validAt='',knownAt='',includeUnknown=true}={},clock=()=>new Date(),applied={}){
    if(!['all','current','at'].includes(mode))throw Error('请选择业务时间范围；原条件已保留');
    const previous=restore(applied);
    // Compare local components before Date parsing: the repeated DST hour must
    // retain its original occurrence, as well as the backend's microseconds.
    const valid_at=mode==='all'?null:mode==='at'&&sameLocal(validAt,previous.validAt)?applied.valid_at:utc(mode==='current'?clock():validAt,'业务时点');
    const known_at=knownAt?(sameLocal(knownAt,previous.knownAt)?applied.known_at:utc(knownAt,'系统历史时点')):null;
    return {valid_at,known_at,include_unknown:mode==='all'||includeUnknown!==false};
  }
  function restore(value={}){
    return {mode:value.valid_at?'at':'all',validAt:localTime(value.valid_at),knownAt:localTime(value.known_at),includeUnknown:!value.valid_at||value.include_unknown!==false};
  }
  function describe(value={}){
    const business=value.valid_at?'业务时点（固定）：'+value.valid_at+' · '+(value.include_unknown===false?'不含生效时间未知':'含生效时间未知'):'全部业务时间（含生效时间未知）';
    return business+' · '+(value.known_at?'系统修订时点：'+value.known_at:'当前系统版本');
  }
  function provenance(value={}){
    const metadata=value.metadata||{};
    if(typeof metadata.temporal_source_text==='string'&&metadata.temporal_source_text.trim()&&
      Number.isFinite(metadata.temporal_confidence)&&metadata.temporal_confidence>=.5){
      const boundaries=['valid_from','valid_until'];
      const extracted=boundaries.map(key=>provenanceTime(metadata[key]));
      if(extracted.some(Boolean)&&boundaries.every((key,index)=>
        extracted[index]===provenanceTime(value[key])&&(!metadata[key]||extracted[index]))&&
        !(extracted[0]&&extracted[1]&&extracted[0]>=extracted[1]))return '原文抽取';
    }
    return {explicit:'明确维护',published_at:'发布时间推定',extracted:'原文抽取',source_inherited:'来源继承',unknown:'未知'}[metadata.effective_time_source]||'未知';
  }
  function provenanceTime(value){
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:T.+(?:Z|[+-]\d{2}:?\d{2}))?$/.test(value))return null;
    const date=new Date(value);if(!Number.isFinite(date.getTime()))return null;
    if(value.length===10&&date.toISOString().slice(0,10)!==value)return null;
    const fraction=(value.match(/\.(\d+)/)?.[1]||'').padEnd(6,'0').slice(0,6);
    return date.toISOString().replace(/\.\d{3}Z$/,'.'+fraction+'Z');
  }
  const interval=(value={},format=String)=>(value.valid_from?format(value.valid_from):'未知')+' → '+(value.valid_until?format(value.valid_until):'未设结束时间');
  return {capture,restore,describe,provenance,interval};
});
