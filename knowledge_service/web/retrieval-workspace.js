/* Presentation only: retain original controls, event ownership and graph sessions. */
(()=>{
  'use strict';
  function install(){
    const get=id=>document.getElementById(id),page=get('tab-search');
    const header=get('graph-workspace-header'),stage=get('graph-stage');
    if(!page||!header||!stage||header.dataset.presentation==='unified')return;
    header.dataset.presentation='unified';
    const heading=document.createElement('div');heading.className='retrieval-page-heading';
    const title=header.querySelector(':scope > h2');
    if(title)heading.append(title);
    const project=document.createElement('span');project.className='retrieval-project-name';heading.append(project);
    const searchOptions=header.querySelector('.search-options');
    if(searchOptions){
      header.querySelector('.searchbar')?.append(searchOptions);
      const summary=searchOptions.querySelector('summary');
      if(summary){
        summary.setAttribute('aria-label','检索设置');
        const label=document.createElement('span');label.className='retrieval-settings-label';
        for(const node of [...summary.childNodes])label.append(node);
        summary.append(label);
      }
    }
    const syncProject=()=>{project.textContent=get('project')?.selectedOptions?.[0]?.textContent||'';};
    get('project')?.addEventListener('change',syncProject);syncProject();
    header.prepend(heading);
    const modes=stage.querySelector('.full-graph-mode');
    if(modes)stage.querySelector('.graph-stage-heading').prepend(modes);
    get('graph-heading')?.classList.add('sr-only');

    // Moving the real buttons preserves direct local handlers and section-level
    // delegated full-graph handlers. The latter must remain inside its section.
    function camera(parent,buttons,local){
      if(!parent||!buttons.length)return;
      const tools=document.createElement('div');tools.className='retrieval-camera-tools';
      tools.setAttribute('role','group');tools.setAttribute('aria-label','图谱视图');
      if(local)tools.dataset.graphLocal='';
      for(const button of buttons)tools.append(button);
      parent.append(tools);
    }
    camera(stage,[get('graph-zoom-out'),get('graph-zoom-in'),get('graph-reset-view')].filter(Boolean),true);
    const full=stage.querySelector('.full-graph-section');
    camera(full,[...(full?.querySelectorAll('.full-graph-view-tools button')||[])],false);

    // Local type controls and timeline are created lazily, and can be removed
    // and rebuilt by the graph owner. Group the actual nodes after each mount.
    const localFooter=document.createElement('div');localFooter.className='retrieval-footer-tools';
    localFooter.dataset.graphLocal='';localFooter.hidden=stage.dataset.graphMode==='full';stage.append(localFooter);
    const fullFooter=document.createElement('div');fullFooter.className='retrieval-footer-tools';
    if(full){
      for(const node of [full.querySelector('.full-graph-types'),full.querySelector('.full-graph-footer')])if(node)fullFooter.append(node);
      full.append(fullFooter);
    }
    function mountLocalFooter(){
      for(const id of ['graph-type-buttons','graph-timeline']){
        const node=get(id);if(node&&node.parentElement!==localFooter)localFooter.append(node);
      }
    }
    mountLocalFooter();new MutationObserver(mountLocalFooter).observe(stage,{childList:true});
    for(const node of [get('graph-summary'),full?.querySelector('.full-graph-progress')]){
      if(!node)continue;
      const describe=()=>{node.title=node.textContent;};describe();
      new MutationObserver(describe).observe(node,{childList:true,characterData:true,subtree:true});
    }

    // The full settings use the same modal drawer manager as local settings.
    // Keep its dialog inside the section so FullGraphView.control() still finds
    // the authoritative checkboxes when loading/restarting the renderer.
    const options=full?.querySelector('.full-graph-options');
    const trigger=options?.querySelector('summary'),content=options?.querySelector('.full-graph-settings-panel');
    let fullSettings=null;
    if(trigger&&content&&window.WorkspaceDrawers){
      trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-controls','full-graph-settings-drawer');
      trigger.addEventListener('click',event=>{
        event.preventDefault();
        if(!fullSettings){
          fullSettings=WorkspaceDrawers.create({id:'full-graph-settings-drawer',title:'图谱设置',
            description:'全量大图 · 显示设置不改变已应用的资料范围和加载数量。',content});
          full.append(fullSettings.dialog);
        }
        fullSettings.open({trigger});
      });
    }
    function modeChanged(){
      const local=stage.dataset.graphMode!=='full';
      const localTools=stage.querySelector(':scope > .retrieval-camera-tools');
      if(localTools)localTools.hidden=!local;
      if(local&&fullSettings?.dialog.open)fullSettings.close({restoreFocus:false});
      positionNeighbors();
    }
    function positionNeighbors(){
      const canvas=stage.dataset.graphMode==='full'?full?.querySelector('.full-graph-host'):get('graph-canvas');
      if(!canvas||canvas.hidden)return;
      const box=canvas.getBoundingClientRect(),outer=stage.getBoundingClientRect();
      if(!box.height)return;
      for(const [name,value] of [['--retrieval-canvas-top',Math.max(0,Math.round(box.top-outer.top+8))+'px'],
        ['--retrieval-canvas-room',Math.max(0,Math.floor(box.height-16))+'px']]){
        if(stage.style.getPropertyValue(name)!==value)stage.style.setProperty(name,value);
      }
    }
    const neighborLayout=new ResizeObserver(positionNeighbors);
    for(const node of [stage,get('graph-canvas'),full?.querySelector('.full-graph-host')])if(node)neighborLayout.observe(node);
    new MutationObserver(modeChanged).observe(stage,{attributes:true,attributeFilter:['data-graph-mode']});
    modeChanged();

    // Decorate controls without rebuilding them; their listeners/disabled states
    // remain owned by their original modules. Dynamic status text stays intact.
    const icons=window.WorkspaceIcons;
    if(icons){
      const decorate=(node,name)=>{if(node)icons.decorate(node,name);};
      for(const [id,name] of [['search','search'],['graph-settings','settings'],['graph-results-toggle','panel'],
        ['graph-create-entity','plus'],['graph-path-toggle','route'],['graph-expand','expand'],['draw-graph','refresh'],
        ['graph-reset-view','maximize'],['graph-zoom-in','zoom-in'],['graph-zoom-out','zoom-out']])decorate(get(id),name);
      for(const [action,name] of [['locate','locate'],['reset','maximize'],['zoom-in','zoom-in'],['zoom-out','zoom-out']])
        decorate(full?.querySelector('[data-fg="'+action+'"]'),name);
      decorate(trigger,'settings');
      decorate(searchOptions?.querySelector('summary'),'sliders');
    }
    document.addEventListener('workspace:page-changed',()=>{
      if(page.classList.contains('hidden'))fullSettings?.close({restoreFocus:false});
    });
    window.GraphWorkspaceLayout?.resize();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
