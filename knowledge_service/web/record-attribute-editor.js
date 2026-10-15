/* Attribute values use the same four primitive datatypes as RecordWrite. */
(function(root,factory){
  const editor=factory();
  if(typeof module==='object'&&module.exports)module.exports=editor;
  else root.RecordAttributeEditor=editor;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const xsd='http://www.w3.org/2001/XMLSchema#';
  const writable=['id','kind','text','type','metadata','properties','source_id','subject_id','object_id','ontology_id','valid_from','valid_until','value','datatype'];
  function calendarDate(text){
    const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if(!match)return false;
    const [year,month,day]=match.slice(1).map(Number);
    const date=new Date(0);date.setUTCFullYear(year,month-1,day);date.setUTCHours(0,0,0,0);
    return date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day;
  }
  function timestamp(text){
    if(calendarDate(text))return Date.parse(text);
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(text)||!calendarDate(text.slice(0,10)))return NaN;
    return Date.parse(text);
  }
  function parseValue(mode,input){
    const text=String(input),trimmed=text.trim();let value=text,datatype='string';
    if(mode==='boolean'){
      if(!['true','false'].includes(trimmed))throw Error('布尔值只能是 true 或 false');
      value=trimmed==='true';datatype='boolean';
    }else if(mode==='integer'||mode==='double'){
      if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed))throw Error('请输入有效数值');
      value=Number(trimmed);
      if(!Number.isFinite(value))throw Error('数值必须有限');
      if((mode==='integer'&&!Number.isInteger(value))||(Number.isInteger(value)&&!Number.isSafeInteger(value)))throw Error('整数必须是安全范围内的整数');
      // JSON has one number type: 2.0 travels as 2 and is an integer server-side.
      datatype=Number.isInteger(value)?'integer':'double';
    }else if(mode==='date'){
      if(!calendarDate(trimmed))throw Error('日期必须是有效的 YYYY-MM-DD');value=trimmed;
    }else if(mode==='dateTime'){
      if(!trimmed.includes('T')||!Number.isFinite(timestamp(trimmed)))throw Error('日期时间必须包含有效日期、时间和时区');value=trimmed;
    }else if(mode==='json'){
      try{JSON.parse(text);}catch{throw Error('请输入有效 JSON（保存为字符串）');}
    }else if(mode!=='string')throw Error('不支持的数据类型，请使用高级 JSON 检查原始记录');
    return {value,datatype:xsd+datatype};
  }
  function buildRecord(row,fields){
    if(!fields.subject_id)throw Error('请选择所属实体');
    if(!fields.type)throw Error('请选择属性类型');
    if(!fields.text?.trim())throw Error('请填写属性显示文本');
    const record=Object.fromEntries(writable.filter(key=>row[key]!==undefined).map(key=>[key,row[key]]));
    Object.assign(record,{text:fields.text.trim(),type:fields.type,subject_id:fields.subject_id},parseValue(fields.mode,fields.value));
    for(const key of ['valid_from','valid_until']){
      const value=fields[key]?.trim()||null;
      if(value&&!Number.isFinite(timestamp(value)))throw Error('业务时间必须是有效日期或带时区的 ISO 日期时间');
      record[key]=value;
    }
    if(record.valid_from&&record.valid_until&&timestamp(record.valid_until)<=timestamp(record.valid_from))throw Error('业务失效时间必须晚于生效时间');
    return record;
  }
  return {parseValue,buildRecord};
});
