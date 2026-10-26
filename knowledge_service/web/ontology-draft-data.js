/* Lossless browser transport. Evidence arrays are shared in memory, never copied per operation on the wire. */
(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.OntologyDraftData=value;
})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  function decode(payload){
    if(!payload||typeof payload!=='object')return payload;
    if(!payload.id&&payload.draft)return {...payload,draft:decode(payload.draft)};
    if(!payload.evidence_sets)return payload;
    const result={...payload};delete result.evidence_sets;
    result.operations=(payload.operations||[]).map(operation=>{
      if(!Object.prototype.hasOwnProperty.call(operation,'evidence_set_id'))return operation;
      const evidence=payload.evidence_sets[operation.evidence_set_id];
      if(!Array.isArray(evidence)||evidence.length!==operation.evidence_count)throw Error('草案证据响应不完整，请重新读取。');
      const item={...operation,evidence};delete item.evidence_set_id;delete item.evidence_count;return item;
    });return result;
  }
  function route(path){return /[?&]view=/.test(path)?path:path+(path.includes('?')?'&':'?')+'view=review';}
  async function request(path,body,method){return decode(await api(route(path),body,method));}
  return {decode,request,route};
});
