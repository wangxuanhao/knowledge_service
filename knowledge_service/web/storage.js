// Optional graph storage controls; credentials are only read on the server.
(() => {
  const panel = document.createElement('section');
  panel.className = 'panel neo4j-panel';
  panel.innerHTML = '<h2>Neo4j 图谱副本</h2><p class="subtle">本地存储继续保留。按项目手动同步完整图谱与版本；检索仍走本地。连接凭据通过服务环境变量配置。</p><div class="row"><button data-storage="check">检查连接</button><button data-storage="status" class="secondary">当前项目同步状态</button><button data-storage="sync">同步当前项目</button></div><pre aria-live="polite"></pre>';
  document.getElementById('tab-dashboard').appendChild(panel);
  const output = panel.querySelector('pre');
  let generation = 0;
  const visible=()=>!document.getElementById('tab-dashboard').classList.contains('hidden');
  output.textContent='进入项目总览后自动检查 Neo4j 连接与项目同步状态。';
  async function inspectStorage(){
    const projectId=current, token=++generation;
    output.textContent='正在检查 Neo4j 连接'+(projectId?'及当前项目同步状态…':'…');
    const requests=[api('/api/storage/neo4j/check',{})];
    if(projectId)requests.push(api('/api/projects/'+encodeURIComponent(projectId)+'/storage/neo4j',undefined,'GET'));
    const [connection,sync]=await Promise.allSettled(requests);
    if(token!==generation||projectId!==current)return;
    const connectionText=connection.status==='fulfilled'?describe(connection.value):'连接检查失败：'+connection.reason.message;
    const syncText=!projectId?'请在左侧选择项目后查看同步状态。':sync.status==='fulfilled'?describe(sync.value):'同步状态检查失败：'+sync.reason.message;
    output.textContent=connectionText+'\n\n'+syncText;
  }
  document.querySelector('[data-tab="dashboard"]').addEventListener('click',inspectStorage);
  document.getElementById('project').addEventListener('change', () => { generation++;output.textContent = '项目已切换，进入总览后自动核验。';if(visible())inspectStorage(); });
  function describe(result){
    const v=result.verification;
    if(!v)return result.connected?'Neo4j 连接正常 · 数据库：'+result.database:JSON.stringify(result,null,2);
    const labels={matched:'已核验：本地与 Neo4j 一致',mismatch:'数据不一致：需要重新同步',unavailable:'无法核验远端，不代表已同步'};
    let message=labels[v.state]+'\n项目：'+document.getElementById('project').selectedOptions[0].textContent+'\n数据库：'+result.database;
    if(v.local)message+='\n\n项目 / 本地 → Neo4j\n实体：'+v.local.entities+' → '+v.remote.entities+'\n关系：'+v.local.relations+' → '+v.remote.relations+'\n记录版本：'+v.local.versions+' → '+v.remote.versions+'\n本体：'+v.local.ontologies+' → '+v.remote.ontologies;
    if(v.checked_at)message+='\n核验时间：'+new Date(v.checked_at).toLocaleString();
    if(result.last_sync)message+='\n上次同步：'+new Date(result.last_sync.synced_at).toLocaleString();
    return message;
  }
  panel.querySelectorAll('button').forEach(button => {
    button.onclick = async () => {
      const projectId = current;
      const token=++generation;
      button.disabled = true;
      try {
        let result;
        if (button.dataset.storage === 'check') {
          result = await api('/api/storage/neo4j/check', {});
        } else if (button.dataset.storage === 'status') {
          result = await api(endpoint('/storage/neo4j'), undefined, 'GET');
        } else {
          result = await api(endpoint('/storage/neo4j/sync'), {});
          watch(result);
          status('Neo4j 同步任务已提交；在后台任务中查看结果。本地数据保持不变。');
          for(let i=0;i<180;i++){
            if(current!==projectId||token!==generation)return;
            output.textContent='同步中… '+(result.progress||0)+'%\n任务：'+result.id+'\n远端写入后会核验数据，提交任务不代表同步成功。';
            if(!['queued','running'].includes(result.status))break;
            await new Promise(resolve=>setTimeout(resolve,1500));
            result=await api('/api/jobs/'+encodeURIComponent(result.id),undefined,'GET');
          }
          if(current!==projectId||token!==generation)return;
          if(result.status!=='completed'){
            output.textContent=['queued','running'].includes(result.status)?'任务仍在执行，请稍后检查同步状态。':'同步未成功：'+result.status+'\n'+(result.error||'请查看后台任务');return;
          }
          result=await api(endpoint('/storage/neo4j'),undefined,'GET');
          status(result.in_sync?'Neo4j 同步完成，远端数据核验通过。':'同步结束，但远端未通过核验。',!result.in_sync);
        }
        if (current === projectId&&token===generation) output.textContent = describe(result);
      } catch (error) {
        if (current === projectId&&token===generation) output.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  });
  if(visible())inspectStorage();
})();
