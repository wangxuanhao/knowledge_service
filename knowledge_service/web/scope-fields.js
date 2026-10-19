/* Presentation policy only: every query still uses the existing scope contract. */
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
  function countConditions(filter){
    if(!filter||typeof filter!=='object')return 0;
    if(typeof filter.field==='string')return 1;
    return ['and','or'].reduce((count,key)=>count+(Array.isArray(filter[key])?
      filter[key].reduce((sum,item)=>sum+countConditions(item),0):0),0)+countConditions(filter.not);
  }
  const api={visibleFacets,isSystemField,label,countConditions};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.ScopeFields=api;
})();
