/* Shared display semantics: record revisions rN, immutable ontology releases vN. */
(() => {
  function build(versions=[]){
    const currentId=versions.at(-1)?.id??null;
    const list=versions.map(item=>{
      const peers=versions.filter(other=>other.version===item.version);
      const id=String(item.id);let width=Math.min(8,id.length);
      while(width<id.length&&peers.some(other=>other.id!==item.id&&String(other.id).slice(0,width)===id.slice(0,width)))width++;
      return {...item,isCurrent:item.id===currentId,versionCollision:peers.length>1,displayId:id.slice(0,width)};
    });
    return {list,currentId,byId:new Map(list.map(item=>[item.id,item]))};
  }
  function releaseLabel(release){
    if(!release||!Number.isInteger(release.version)||release.version<1)return '未知本体';
    return `本体 v${release.version}（${release.isCurrent?'当前':'历史'}）`
      +(release.version_reused?' · 标注修订':'')
      +(release.versionCollision?` · ${release.displayId}`:'');
  }
  function ontology(row,index){
    if(!row?.ontology_id)return '未知本体';
    if(!index)return '本体归属读取失败';
    return releaseLabel(index.byId.get(row.ontology_id));
  }
  const revision=row=>`修订 r${Number.isInteger(row?.version)&&row.version>0?row.version:'未知'}`;
  const summary=(row,index)=>`${revision(row)} · 依据${ontology(row,index)}`;
  const api={build,releaseLabel,ontology,revision,summary};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.RecordVersionContext=api;
})();
