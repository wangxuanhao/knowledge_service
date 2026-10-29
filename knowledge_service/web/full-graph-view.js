(function(){
  'use strict';
  function install(){
    const stage=document.getElementById('graph-stage');if(!stage||!window.FullGraph)return;
    const modes=document.createElement('div');modes.className='full-graph-mode';modes.innerHTML='<div class="full-graph-controls" role="group" aria-label="图谱工作方式"><button type="button" data-fg-mode="local" aria-pressed="true">局部探索</button><button type="button" data-fg-mode="full" aria-pressed="false">全量大图</button></div>';
    (stage.querySelector('.graph-stage-heading')||stage).append(modes);
    const section=document.createElement('section');section.hidden=true;section.className='full-graph-section';
    section.innerHTML='<div class="full-graph-controls full-graph-toolbar"><div class="full-graph-view-tools" role="group" aria-label="图谱视图"><button type="button" data-fg="zoom-out" aria-label="缩小图谱" title="缩小图谱">−</button><button type="button" data-fg="zoom-in" aria-label="放大图谱" title="放大图谱">＋</button><button type="button" data-fg="reset">适应画布</button></div><div class="full-graph-locate"><input type="search" data-fg="query" aria-label="在已加载图中定位实体或关系" placeholder="名称或实体 / 关系 ID"><button type="button" data-fg="locate">定位</button></div><details class="full-graph-options"><summary class="graph-tool-button">图谱设置</summary><div class="full-graph-settings-panel"><strong>画面显示</strong><label class="check"><input type="checkbox" data-fg="edges" checked>显示关系线</label><label class="check"><input type="checkbox" data-fg="labels" checked>显示实体名称</label><p>按实体类型着色，名称随缩放显示。显示设置不改变加载数量。</p></div></details></div><p class="full-graph-progress" aria-live="polite">尚未加载。先显示一批关系和实体，再按需继续；自动继续可补齐全部关系与孤立实体。</p><div class="full-graph-host"></div><div class="full-graph-types" aria-label="全量图实体类型"><div class="full-graph-type-heading"><strong>实体类型</strong><span class="full-graph-type-summary" aria-live="polite"></span></div><p class="full-graph-type-empty"></p><div class="full-graph-type-row"><button type="button" data-fg="all-types" data-fg-types-all aria-pressed="true" disabled>全部类型 · 0</button><div class="full-graph-type-controls" role="group" aria-label="筛选已加载实体类型"></div></div><div class="full-graph-type-pagination"><button type="button" data-fg="type-prev" aria-label="上一页实体类型" disabled>上一页</button><span class="full-graph-type-page"></span><button type="button" data-fg="type-next" aria-label="下一页实体类型" disabled>下一页</button></div></div><div class="full-graph-footer"><div class="full-graph-controls full-graph-batches" role="group" aria-label="渐进加载"><button type="button" data-fg="load">加载首批关系</button><button type="button" data-fg="next" disabled>下一批</button><button type="button" data-fg="auto" disabled>自动继续</button><button type="button" data-fg="pause" disabled>暂停</button><button type="button" data-fg="cancel" disabled>取消加载</button><button type="button" data-fg="restart" disabled>重新开始</button></div></div><p class="fg-scope-note">按类型着色 · 名称随缩放显示 · 每批最多 500 条 · 暂停后仍可平移、缩放和查看详情 · 双击空白适应画布</p>';
    stage.append(section);
    const scopeBanner=document.getElementById('graph-version-scope');if(scopeBanner)modes.before(scopeBanner);
    const host=section.querySelector('.full-graph-host'),status=section.querySelector('.full-graph-progress');
    const control=action=>section.querySelector('[data-fg="'+action+'"]');
    control('pause').textContent='本批后暂停';
    let renderer=null,loader=null,mode='local',fixed=null,loadedProject=null,run=0,lastProgress=null;
    const typeList=section.querySelector('.full-graph-type-controls'),typeButtons=new Map(),typePageSize=40;
    let typePage=0;
    const typeLabel=type=>type?String((typeof labelOf==='function'?labelOf(type):null)||type.split(/[\/#]/).pop()||type):'未知类型';
    function renderTypes(data=renderer?.dataset,selected=renderer?.typeFilter??null){
      const types=data?.types,total=types?.size||0,loaded=data?.records.length||0;
      typePage=Math.min(typePage,Math.max(0,Math.ceil(total/typePageSize)-1));
      const visible=[],offset=typePage*typePageSize;
      if(types){let index=0;for(const entry of types){if(index++<offset)continue;visible.push(entry);if(visible.length===typePageSize)break;}}
      const present=new Set(visible.map(([type])=>type));
      for(const [type,button] of typeButtons)if(!present.has(type)){button.remove();typeButtons.delete(type);}
      for(const [type,bucket] of visible){
        let button=typeButtons.get(type);
        if(!button){button=document.createElement('button');button.type='button';button.className='full-graph-type-button';button.dataset.fgType=type;button.setAttribute('title',type||'未知类型（缺少类型 ID）');typeList.append(button);typeButtons.set(type,button);}
        const count=bucket.nodes.length;
        button.textContent=typeLabel(type)+' · '+count.toLocaleString();button.dataset.typeCount=String(count);
        button.style.setProperty('--fg-type-color',window.GraphAppearance?.colorFor({type})||'#0e7c7b');
        button.setAttribute('aria-pressed',String(selected===type));
      }
      const all=control('all-types');all.textContent='全部类型 · '+loaded.toLocaleString();all.disabled=!loaded;all.setAttribute('aria-pressed',String(selected===null));
      section.querySelector('.full-graph-type-heading').hidden=!total;section.querySelector('.full-graph-type-row').hidden=!total;
      section.querySelector('.full-graph-type-summary').textContent=total?(lastProgress?.stage==='complete'?'完整清单':'当前已加载')+' · '+total.toLocaleString()+' 类 · '+loaded.toLocaleString()+' 个实体'+(selected===null?'':' · 筛选：'+typeLabel(selected)):'';
      const empty=section.querySelector('.full-graph-type-empty');empty.hidden=!!total;empty.textContent=lastProgress?.stage==='complete'?'当前范围没有实体。':'加载首批后显示实体类型，数量随批次更新。';
      section.querySelector('.full-graph-type-pagination').hidden=total<=typePageSize;
      section.querySelector('.full-graph-type-page').textContent=total?'第 '+(typePage+1)+' / '+Math.ceil(total/typePageSize)+' 页':'';
      control('type-prev').disabled=typePage===0;control('type-next').disabled=(typePage+1)*typePageSize>=total;
    }
    typeList.addEventListener('click',event=>{
      const button=event.target.closest('[data-fg-type]');if(!button||!renderer)return;
      renderer.setTypeFilter(button.dataset.fgType);renderTypes();bindExplorer();
    });
    const project=()=>document.getElementById('project')?.value||'';
    const scope=()=>({...((window.WorkspaceContext?.read(project(),'search'))||(typeof window.scope==='function'?window.scope():{})),...(window.wb?.ontologyScope?.()||{})});
    const counts=value=>' · 实体 '+value.entities.toLocaleString()+' / '+value.manifest.entity_count.toLocaleString()+' · 关系 '+value.relations.toLocaleString()+' / '+value.manifest.relation_count.toLocaleString();
    const message=(text,error=false)=>{
      if(lastProgress&&!text.includes(' · 实体 '))text+=(lastProgress.stage==='complete'?' · 已核对完整清单':' · 当前为局部图')+counts(lastProgress);
      status.textContent=text;status.title=text;status.classList.toggle('error',error);
    };
    const select=record=>{bindExplorer(record);return window.openKnowledgeRecord?.({id:record.id,kind:record.kind||'entity',projectId:loadedProject,scope:fixed});};
    function bindExplorer(selected){
      if(mode!=='full'||!window.GraphExplorer)return;
      const data=renderer?.displayDataset;
      const empty=()=>({nodeIds:new Set(),edgeIds:new Set(),edgeRecords:[],truncated:false});
      GraphExplorer.setContext({mode:'full',key:JSON.stringify([loadedProject||project(),fixed||scope()]),
        revision:JSON.stringify([data?.nodeCount||0,data?.edgeCount||0,renderer?.typeFilter]),
        selected:selected===undefined?(data?.records[renderer?.selected]||null):selected,
        adapter:{neighbors:async(id,options)=>data?(await data.getNeighborIndexAsync()).page(id,options):{items:[],count:0,nextCursor:null},
          focus:async(id,options)=>data?(await data.getNeighborIndexAsync()).focus(id,options):empty(),
          setFocus:value=>renderer?.setExplorationFocus(value),select:row=>renderer?.locate(row.id),canCollapse:()=>false,
          expand:async id=>{
            const row=data?.records[data.nodeIndex.get(id)];if(!row)throw Error('当前显示图中没有该实体，请重新选择。');
            // Canvas-only type filters must not silently reject the full graph's
            // selected anchor when handing it to the scoped neighborhood query.
            for(const key of ['type-scope','predicate-scope']){const input=document.getElementById(key);if(input)input.value='';}
            switchMode('local');
            const input=document.getElementById('graph-node');if(input)input.value=id;
            const result=await window.drawGraph(id,document.getElementById('graph-hops')?.value||1);
            if(result){await window.selectEntityDetail?.(id);GraphExplorer.open();}
          }
        }});
    }
    function controls(){
      const state=loader?.state||'idle',active=['loading','paused'].includes(state),busy=state==='loading'||!!loader?.automatic;
      control('load').disabled=active||state==='complete';
      control('next').disabled=!active||busy;control('auto').disabled=!active||busy;
      control('pause').disabled=!busy||!!loader?.pauseRequested;control('cancel').disabled=!active;
      control('restart').disabled=!loader?.manifest;
    }
    const stop=()=>{run++;loader?.cancel();controls();};
    function switchMode(next){
      if(next==='full')window.suspendLocalGraphExplorer?.();
      mode=next;stage.dataset.graphMode=mode;section.hidden=mode!=='full';
      if(mode==='full'&&window.WorkspaceDrawers?.active?.dialog?.id==='graph-settings-drawer')window.WorkspaceDrawers.close({restoreFocus:false});
      for(const element of stage.querySelectorAll('[data-graph-local],.graph-toolbar,.graph-display-switch,#graph-settings,#graph-local-type-controls,#graph-pan-hint,#graph-summary,#graph-canvas,#graph-type-buttons,#graph-timeline'))element.hidden=mode==='full';
      const pathTools=document.getElementById('graph-path-tools');
      if(pathTools){pathTools.hidden=mode==='full';document.querySelector('.search-graph-workspace .inspector-rail')?.classList.toggle('is-path-open',mode==='local'&&pathTools.open);}
      for(const button of modes.querySelectorAll('[data-fg-mode]'))button.setAttribute('aria-pressed',String(button.dataset.fgMode===mode));
      if(mode==='local'){if(loader&&loader.state!=='complete'){stop();message('加载已取消，当前图不完整；可重新开始。');}if(window.GraphWorkbench?.reveal)window.GraphWorkbench.reveal();else window.wb?.chart?.resize();window.refreshLocalGraphExplorer?.();}else{renderer?.resize();bindExplorer();}
    }
    modes.addEventListener('click',event=>{const button=event.target.closest('[data-fg-mode]');if(button)switchMode(button.dataset.fgMode);});
    function progress(value,epoch){
      if(epoch!==run)return;renderer?.setSessionManifest(value.manifest);controls();lastProgress=value;renderTypes();
      const label=value.stage==='complete'?'全量加载完成':value.stage==='cancelled'?'已取消，当前图不完整':value.stage==='error'?'加载中断，当前图不完整':value.pause_requested?'当前批完成后暂停':value.stage==='paused'&&!value.automatic?'已暂停，当前为局部图':value.phase==='entities'?'正在补齐实体（含孤立实体）':'正在加载关系与实体';
      message(label+counts(value)+(value.stage==='paused'&&!value.automatic?' · 可点下一批或自动继续。':''));
      const explorer=window.GraphExplorer?.snapshot();if(explorer?.open||explorer?.focused)bindExplorer();
    }
    async function start(){
      stop();lastProgress=null;typePage=0;renderTypes(null,null);const epoch=run,selected=project();if(!selected){message('请先选择项目。',true);return;}
      try{
        if(!renderer){renderer=new window.FullGraph.Renderer(host,{onSelect:select,onError:text=>message(text,true)});loader=new window.FullGraph.SessionLoader(renderer);}
        // begin() cancels the previous session synchronously before clearing its records.
        let acceptProgress=false;loader.onProgress=value=>{if(acceptProgress)progress(value,epoch);};
        renderer.showEdges=control('edges').checked!==false;
        renderer.showLabels=control('labels')?.checked!==false;
        fixed=scope();loadedProject=selected;message('正在核对图谱范围与完整清单…');
        const pending=loader.begin(selected,fixed);acceptProgress=true;controls();bindExplorer();await pending;bindExplorer();
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
      const query=control('query').value.trim();if(!query)return;const data=renderer.displayDataset;
      const match=(data.nodeIndex.has(query)?query:null)||data.records.find(row=>row.text===query)?.id||data.records.find(row=>String(row.text).includes(query))?.id;
      if(match&&renderer.locate(match)!==false){message('已定位实体；可在详情中查看编辑、来源和历史。');return;}
      const edge=data.edges.find(row=>row.id===query);
      if(edge&&renderer.locate(edge.subject_id)!==false){select(edge);message('已定位关系与起点，可在知识详情中维护。');return;}
      message('当前显示图中没有可定位的匹配项；可切换全部类型、继续加载或使用全库检索。',true);
    }
    section.addEventListener('click',event=>{
      const action=event.target.closest('[data-fg]')?.dataset.fg;
      if(action==='load'||action==='restart')start();if(action==='next')advance();if(action==='auto')advance(true);
      if(action==='pause')pause();if(action==='cancel')cancel();if(action==='reset')renderer?.reset();if(action==='locate')locate();
      if(action==='zoom-in')renderer?.zoom(1.25);if(action==='zoom-out')renderer?.zoom(1/1.25);
      if(action==='all-types'){renderer?.setTypeFilter(null);renderTypes();bindExplorer();}
      if(action==='type-prev'&&typePage>0){typePage--;renderTypes();}if(action==='type-next'&&!control('type-next').disabled){typePage++;renderTypes();}
    });
    control('query').addEventListener('keydown',event=>{if(event.key==='Enter')locate();});
    control('edges').addEventListener('change',event=>{if(renderer){renderer.showEdges=event.target.checked;renderer.requestDraw();}});
    control('labels')?.addEventListener('change',event=>{if(renderer){renderer.showLabels=event.target.checked;renderer.requestDraw();}});
    const invalidate=()=>{
      stop();if(renderer)renderer.setDataset(new window.FullGraph.GraphDataset());
      if(loader)loader.manifest=null;fixed=null;loadedProject=null;lastProgress=null;typePage=0;renderTypes(null,null);controls();message('项目或范围已改变，请重新开始加载图谱。');
      bindExplorer();
    };
    const projectControl=document.getElementById('project');
    if(projectControl){
      const priorChange=projectControl.onchange;
      projectControl.onchange=function(event){invalidate();const result=priorChange?.call(this,event);bindExplorer();return result;};
    }
    document.addEventListener('workspace:scope-changed',event=>{if(!event.detail?.page||event.detail.page==='search')invalidate();});
    document.addEventListener('version-context:changed',invalidate);
    window.addEventListener('beforeunload',()=>loader?.destroy(),{once:true});
    window.FullGraphView={switchMode,load:start,restart:start,next:()=>advance(),resume:()=>advance(true),pause,cancel,get renderer(){return renderer;},get loader(){return loader;},get mode(){return mode;}};
    controls();renderTypes(null,null);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
