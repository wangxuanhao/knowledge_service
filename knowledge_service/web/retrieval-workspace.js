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
    if(searchOptions)heading.append(searchOptions);
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
    }
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
    }
    document.addEventListener('workspace:page-changed',()=>{
      if(page.classList.contains('hidden'))fullSettings?.close({restoreFocus:false});
    });
    window.GraphWorkspaceLayout?.resize();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
