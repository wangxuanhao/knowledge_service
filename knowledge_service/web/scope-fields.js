/* Field presentation and online retrieval capability; applied scopes stay intact. */
(() => {
  const technical=new Set(['active_stage','batch_id','batch_item_id','chunk_id','chunk_overlap',
    'chunk_size','chunk_strategy','confidence','discovery_candidate_id','discovery_candidate_ids',
    'discovery_candidates','discovery_draft_id','document_generation','effective_time_source',
    'end_char','error_code','evidence','extracted_at','extraction_mode','file_sha256',
    'heading_path','heading_paths','indexed_at','public_error','review_candidates','run_id',
    'source_document_version_id','source_kind','source_version_id','start_char','status','uploaded_at']);
  const local=field=>String(field||'').replace(/^metadata\./,'');
  const isSystemField=field=>technical.has(local(field).split('.')[0])||local(field).startsWith('readiness.');
  function visibleFacets(facets,{showSystem=false,selected=[]}={}){
    const keep=new Set(selected);
    return facets.filter(f=>keep.has(f.field)||(!local(f.field).startsWith('applicability.')&&
      (showSystem||!isSystemField(f.field))));
  }
  const label=field=>({valid_from:'生效时间',valid_until:'失效时间',title:'文档标题'}[local(field)]||local(field));
  const fixedFields=[['id','记录 ID'],['kind','记录种类'],['type','知识类型'],
    ['source_id','原文片段所属文档 ID'],['valid_from','生效时间'],['valid_until','失效时间']];
  const retrievalFields=()=>fixedFields.map(([v,l])=>({v,l}));
  function usesPostgresFilters({page,purpose,retrievalMode,knownAt}={}){
    return page==='sources'||page==='records'||
      ((page==='search'||page==='qa')&&(!knownAt||retrievalMode==='keyword'));
  }
  function requireRetrievalScope(request={}){
    if(request.known_at&&request.retrieval_mode!=='keyword'){
      throw Error('历史时点仅支持关键词检索。原条件已保留，请切换关键词方式或明确清除历史时点。');
    }
    const invalid=()=>{throw Error('过滤器结构或运算符不受当前在线检索支持。原条件已保留，请修正后再检索。');};
    function visit(ast,depth=0){
      if(depth>16||!ast||Array.isArray(ast)||typeof ast!=='object')invalid();
      const keys=Object.keys(ast),group=keys.find(key=>['and','or','not'].includes(key));
      if(group){
        if(keys.length!==1)invalid();
        if(group==='not')throw Error('当前过滤器不支持 NOT。原条件已保留；请使用已支持的 AND、OR 或叶子条件。');
        else{if(!Array.isArray(ast[group])||!ast[group].length||ast[group].length>100)invalid();for(const child of ast[group])visit(child,depth+1);}
        return;
      }
      if(keys.length!==3||!keys.includes('field')||!keys.includes('op')||!keys.includes('value')||typeof ast.field!=='string')invalid();
      if([...ast.field].length>256||!/^(?!\p{Nd})[\p{L}\p{N}_][\p{L}\p{N}_-]*(?:\.(?!\p{Nd})[\p{L}\p{N}_][\p{L}\p{N}_-]*)*$/u.test(ast.field))invalid();
      if(!['eq','ne','in','gt','gte','lt','lte','exists','contains'].includes(ast.op))invalid();
      if(ast.op==='in'&&!Array.isArray(ast.value))invalid();
      if(ast.op==='exists'&&typeof ast.value!=='boolean')invalid();
      if(['gt','gte','lt','lte'].includes(ast.op)&&!['number','string'].includes(typeof ast.value))invalid();
      try{JSON.stringify(ast.value,(_key,value)=>{if(['undefined','function','symbol','bigint'].includes(typeof value)||(typeof value==='number'&&!Number.isFinite(value)))invalid();return value;});}catch(_error){invalid();}
    }
    if(request.filters!=null)visit(request.filters);
    return request;
  }
  function countConditions(filter){
    if(!filter||typeof filter!=='object')return 0;
    if(typeof filter.field==='string')return 1;
    return ['and','or'].reduce((count,key)=>count+(Array.isArray(filter[key])?
      filter[key].reduce((sum,item)=>sum+countConditions(item),0):0),0)+countConditions(filter.not);
  }
  const api={visibleFacets,isSystemField,label,countConditions,retrievalFields,usesPostgresFilters,requireRetrievalScope};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.ScopeFields=api;
})();
