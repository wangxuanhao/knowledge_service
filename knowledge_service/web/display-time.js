/* Format calendar dates and API timestamps for the workbench. */
(function(root){
  const formatter=new Intl.DateTimeFormat('zh-CN',{
    timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'
  });
  const isoDate=/^(\d{4})-(\d{2})-(\d{2})$/;
  const isoInstant=/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/i;

  function format(value,fallback='—'){
    if(typeof value==='string'){
      value=value.trim();
      const day=value.match(isoDate);
      const datePart=day||value.slice(0,10).match(isoDate);
      if(!datePart||(!day&&!isoInstant.test(value)))return fallback;
      // Check the calendar date before Date can roll invalid days into the next month.
      const calendarDate=new Date(datePart[0]);
      if(!Number.isFinite(calendarDate.getTime())||calendarDate.toISOString().slice(0,10)!==datePart[0])return fallback;
      if(day)return `${day[1]}年${day[2]}月${day[3]}日`;
    }else if(typeof value!=='number'){
      return fallback;
    }
    const date=new Date(value);
    if(!Number.isFinite(date.getTime()))return fallback;
    const parts=Object.fromEntries(formatter.formatToParts(date).map(({type,value})=>[type,value]));
    return `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}:${parts.second}`;
  }

  function formatLogLine(line){
    return String(line).replace(/^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\]/,
      (_,value)=>'['+format(value,value)+']');
  }

  const api={format,formatLogLine};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.DisplayTime=api;
})(typeof window==='object'?window:globalThis);
