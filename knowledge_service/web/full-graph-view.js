(function(){
  'use strict';
  function install(){
    const stage=document.getElementById('graph-stage');if(!stage||!window.FullGraph)return;
    const modes=document.createElement('div');modes.className='full-graph-mode';modes.innerHTML='<div class="full-graph-controls" role="group" aria-label="图谱工作方式"><button type="button" data-fg-mode="local" aria-pressed="true">局部探索</button><button type="button" data-fg-mode="full" aria-pressed="false">全量大图</button></div>';
    (stage.querySelector('.graph-stage-heading')||stage).append(modes);
    const section=document.createElement('section');section.hidden=true;section.className='full-graph-section';
    section.innerHTML='<div class="full-graph-controls"><div class="full-graph-batches" role="group" aria-label="渐进加载"><button type="button" data-fg="load">加载首批关系</button><button type="button" data-fg="next" disabled>下一批</button><button type="button" data-fg="auto" disabled>自动继续</button><button type="button" data-fg="pause" disabled>暂停</button><button type="button" data-fg="cancel" disabled>取消加载</button><button type="button" data-fg="restart" disabled>重新开始</button></div><button type="button" data-fg="reset">适应画布</button><div class="full-graph-locate"><input type="search" data-fg="query" aria-label="在已加载图中定位实体或关系" placeholder="名称或实体 / 关系 ID"><button type="button" data-fg="locate">定位</button></div><details class="full-graph-options"><summary>显示设置</summary><label class="check"><input type="checkbox" data-fg="edges" checked>显示关系线</label><p>名称随缩放显示；标签数量不限制实体加载。</p></details></div><p class="full-graph-progress" aria-live="polite">尚未加载。先显示一批关系和实体，再按需继续；自动继续可补齐全部关系与孤立实体。</p><div class="full-graph-host"></div><p class="fg-scope-note">每批最多 500 条 · 暂停后仍可平移、缩放和查看详情 · 点击实体或关系查看来源、版本与有效期 · 双击空白适应画布</p>';
    stage.append(section);
    const host=section.querySelector('.full-graph-host'),status=section.querySelector('.full-graph-progress');
    const control=action=>section.querySelector('[data-fg="'+action+'"]');
    control('pause').textContent='本批后暂停';
    let renderer=null,loader=null,mode='local',fixed=null,loadedProject=null,run=0,lastProgress=null;
    const project=()=>document.getElementById('project')?.value||'';
    const scope=()=>({...((window.WorkspaceContext?.read(project(),'search'))||(typeof window.scope==='function'?window.scope():{})),...(window.wb?.ontologyScope?.()||{})});
    const counts=value=>' · 实体 '+value.entities.toLocaleString()+' / '+value.manifest.entity_count.toLocaleString()+' · 关系 '+value.relations.toLocaleString()+' / '+value.manifest.relation_count.toLocaleString();
    const message=(text,error=false)=>{
      if(lastProgress&&!text.includes(' · 实体 '))text+=(lastProgress.stage==='complete'?' · 已核对完整清单':' · 当前为局部图')+counts(lastProgress);
      status.textContent=text;status.classList.toggle('error',error);
    };
    const select=record=>window.openKnowledgeRecord?.({id:record.id,kind:record.kind||'entity',projectId:loadedProject,scope:fixed});
    function controls(){
      const state=loader?.state||'idle',active=['loading','paused'].includes(state),busy=state==='loading'||!!loader?.automatic;
      control('load').disabled=active||state==='complete';
      control('next').disabled=!active||busy;control('auto').disabled=!active||busy;
      control('pause').disabled=!busy||!!loader?.pauseRequested;control('cancel').disabled=!active;
      control('restart').disabled=!loader?.manifest;
    }
    const stop=()=>{run++;loader?.cancel();controls();};
    function switchMode(next){
      mode=next;stage.dataset.graphMode=mode;section.hidden=mode!=='full';
      if(mode==='full'&&window.WorkspaceDrawers?.active?.dialog?.id==='graph-settings-drawer')window.WorkspaceDrawers.close({restoreFocus:false});
      for(const element of stage.querySelectorAll('[data-graph-local],.graph-toolbar,.graph-display-switch,#graph-settings,#graph-local-type-controls,#graph-pan-hint,#graph-summary,#graph-canvas,#graph-type-buttons,#graph-timeline'))element.hidden=mode==='full';
      const pathTools=document.getElementById('graph-path-tools');
      if(pathTools){pathTools.hidden=mode==='full';document.querySelector('.search-graph-workspace .inspector-rail')?.classList.toggle('is-path-open',mode==='local'&&pathTools.open);}
      for(const button of modes.querySelectorAll('[data-fg-mode]'))button.setAttribute('aria-pressed',String(button.dataset.fgMode===mode));
      if(mode==='local'){if(loader&&loader.state!=='complete'){stop();message('加载已取消，当前图不完整；可重新开始。');}window.wb?.chart?.resize();}else renderer?.resize();
    }
    modes.addEventListener('click',event=>{const button=event.target.closest('[data-fg-mode]');if(button)switchMode(button.dataset.fgMode);});
    function progress(value,epoch){
      if(epoch!==run)return;controls();lastProgress=value;
      const label=value.stage==='complete'?'全量加载完成':value.stage==='cancelled'?'已取消，当前图不完整':value.stage==='error'?'加载中断，当前图不完整':value.pause_requested?'当前批完成后暂停':value.stage==='paused'&&!value.automatic?'已暂停，当前为局部图':value.phase==='entities'?'正在补齐实体（含孤立实体）':'正在加载关系与实体';
      message(label+counts(value)+(value.stage==='paused'&&!value.automatic?' · 可点下一批或自动继续。':''));
    }
    async function start(){
      stop();lastProgress=null;const epoch=run,selected=project();if(!selected){message('请先选择项目。',true);return;}
      try{
        if(!renderer){renderer=new window.FullGraph.Renderer(host,{onSelect:select,onError:text=>message(text,true)});loader=new window.FullGraph.SessionLoader(renderer);}
        loader.onProgress=value=>progress(value,epoch);
        renderer.showEdges=control('edges').checked!==false;
        fixed=scope();loadedProject=selected;message('正在核对图谱范围与完整清单…');
        const pending=loader.begin(selected,fixed);controls();await pending;
      }catch(error){if(error.name!=='AbortError'&&epoch===run)message(error.message+' 当前图不完整，请重新开始。',true);}
      finally{if(epoch===run)controls();}
    }
    async function advance(automatic=false){
      if(!loader)return;const epoch=run;
      try{const pending=automatic?loader.resume():loader.next();controls();await pending;}
      catch(error){if(error.name!=='AbortError'&&epoch===run)message(error.message+' 当前图不完整，请重新开始。',true);}
      finally{if(epoch===run)controls();}
    }
    function pause(){loader?.pause();controls();}
    function cancel(){stop();message('加载已取消，当前图不完整；重新开始可核对新的完整清单。');}
    function locate(){
      if(!renderer){message('请先加载首批图谱。',true);return;}
      const query=control('query').value.trim();if(!query)return;const data=renderer.dataset;
      const match=(data.nodeIndex.has(query)?query:null)||data.records.find(row=>row.text===query)?.id||data.records.find(row=>String(row.text).includes(query))?.id;
      if(match){renderer.locate(match);message('已定位实体；可在详情中查看编辑、来源和历史。');return;}
      const edge=data.edges.find(row=>row.id===query);
      if(edge){renderer.locate(edge.subject_id);select(edge);message('已定位关系与起点，可在知识详情中维护。');return;}
      message('当前已加载图中没有匹配项；可继续加载或使用全库检索。',true);
    }
    section.addEventListener('click',event=>{
      const action=event.target.closest('[data-fg]')?.dataset.fg;
      if(action==='load'||action==='restart')start();if(action==='next')advance();if(action==='auto')advance(true);
      if(action==='pause')pause();if(action==='cancel')cancel();if(action==='reset')renderer?.reset();if(action==='locate')locate();
    });
    control('query').addEventListener('keydown',event=>{if(event.key==='Enter')locate();});
    control('edges').addEventListener('change',event=>{if(renderer){renderer.showEdges=event.target.checked;renderer.requestDraw();}});
    const invalidate=()=>{
      stop();if(renderer)renderer.setDataset(new window.FullGraph.GraphDataset());
      if(loader)loader.manifest=null;fixed=null;loadedProject=null;lastProgress=null;controls();message('项目或范围已改变，请重新开始加载图谱。');
    };
    const projectControl=document.getElementById('project');
    if(projectControl){
      const priorChange=projectControl.onchange;
      projectControl.onchange=function(event){invalidate();return priorChange?.call(this,event);};
    }
    document.addEventListener('workspace:scope-changed',event=>{if(!event.detail?.page||event.detail.page==='search')invalidate();});
    document.addEventListener('version-context:changed',invalidate);
    window.addEventListener('beforeunload',()=>loader?.destroy(),{once:true});
    window.FullGraphView={switchMode,load:start,restart:start,next:()=>advance(),resume:()=>advance(true),pause,cancel,get renderer(){return renderer;},get loader(){return loader;},get mode(){return mode;}};
    controls();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
