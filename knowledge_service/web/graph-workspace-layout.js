/* Page composition only. Existing modules continue to own queries and graph state. */
(()=>{
  'use strict';
  const get=id=>document.getElementById(id);
  let page,surface,header,panel,results,inspector,tabs,status,resultsToggle;
  let frame=0,layoutPromise=Promise.resolve(),resolveLayout=null,activePanel=null,returnFocus=null;
  const active=()=>!!page&&!page.classList.contains('hidden');

  function placeScope(scope){
    if(!header||!active()||window.WorkspaceContext?.page!=='search')return false;
    if(scope.parentElement!==header)header.append(scope);
    resize();return true;
  }

  function resize(){
    if(!surface||frame)return layoutPromise;
    layoutPromise=new Promise(resolve=>{resolveLayout=resolve;});
    frame=requestAnimationFrame(()=>{
      frame=0;
      if(active()){
        const fullscreen=surface.classList.contains('is-graph-fullscreen-workspace');
        const top=fullscreen?0:surface.getBoundingClientRect().top;
        const height=Math.max(240,Math.floor((window.visualViewport?.height||innerHeight)-top-(fullscreen?0:12)));
        const value=height+'px';
        if(surface.style.getPropertyValue('--graph-workspace-height')!==value)surface.style.setProperty('--graph-workspace-height',value);
      }
      // The second frame follows both renderers' existing ResizeObservers.
      const done=resolveLayout;resolveLayout=null;
      requestAnimationFrame(()=>done?.());
    });
    return layoutPromise;
  }

  function settled(){return resize();}

  function showPanel(kind){
    if(!panel)return;
    if(!activePanel)returnFocus=document.activeElement;
    const hiding=kind==='results'?inspector:results;
    const moveFocus=hiding.contains(document.activeElement);
    activePanel=kind;panel.hidden=false;surface.dataset.panel=kind;
    results.hidden=kind!=='results';inspector.hidden=kind!=='detail';
    for(const tab of tabs){
      const selected=tab.dataset.graphPanel===kind;
      tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
    }
    if(moveFocus)tabs.find(tab=>tab.dataset.graphPanel===kind).focus({preventScroll:true});
    resultsToggle.setAttribute('aria-expanded','true');
    resize();
  }

  function closePanel(){
    if(!panel)return;
    activePanel=null;panel.hidden=true;surface.dataset.panel='closed';
    resultsToggle.setAttribute('aria-expanded','false');
    if(panel.contains(document.activeElement)){
      const target=returnFocus?.isConnected&&!panel.contains(returnFocus)?returnFocus:resultsToggle;
      target.focus({preventScroll:true});
    }
    resize();
  }

  function setState(kind,detail){
    if(!surface)return;
    surface.dataset.state=String(kind||'idle');
    const message=typeof detail==='string'?detail:detail?.message||'';
    status.textContent=message;status.hidden=!message;resize();
  }

  function install(){
    page=get('tab-search');surface=page?.querySelector('.search-graph-workspace');
    const query=page?.querySelector('.retrieval-query-panel');
    results=surface?.querySelector('.search-results-pane');inspector=surface?.querySelector('.inspector-rail');
    if(!surface||!query||!results||!inspector)return;
    surface.id='graph-workspace-surface';surface.dataset.panel='closed';
    header=document.createElement('header');header.id='graph-workspace-header';
    const title=document.createElement('h2');title.textContent='检索与图谱';
    header.append(title,query);page.prepend(header);
    placeScope(get('scope'));

    panel=document.createElement('aside');panel.id='graph-workspace-panel';panel.hidden=true;
    panel.setAttribute('aria-label','检索结果与知识详情');
    const heading=document.createElement('div');heading.className='graph-panel-heading';
    const tablist=document.createElement('div');tablist.className='graph-panel-tabs';tablist.setAttribute('role','tablist');tablist.setAttribute('aria-label','工作区面板');
    tabs=['results','detail'].map((kind,index)=>{
      const tab=document.createElement('button');tab.type='button';tab.id='graph-panel-tab-'+kind;
      tab.dataset.graphPanel=kind;tab.textContent=index?'知识详情':'检索结果';tab.setAttribute('role','tab');
      tab.setAttribute('aria-controls',index?'graph-panel-detail':'graph-panel-results');tab.setAttribute('aria-selected',String(!index));tab.tabIndex=index?-1:0;
      tab.addEventListener('click',()=>showPanel(kind));tablist.append(tab);return tab;
    });
    tablist.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();const index=event.key==='Home'?0:event.key==='End'?1:activePanel==='results'?1:0;
      showPanel(tabs[index].dataset.graphPanel);tabs[index].focus();
    });
    // Move the original close control so its cancellation/path listeners survive.
    const close=get('detail-drawer-close');close.setAttribute('aria-label','收起结果与详情');
    close.addEventListener('click',closePanel);heading.append(tablist,close);
    for(const [node,kind] of [[results,'results'],[inspector,'detail']]){
      node.id='graph-panel-'+kind;node.setAttribute('role','tabpanel');node.setAttribute('aria-labelledby','graph-panel-tab-'+kind);node.tabIndex=0;
    }
    panel.append(heading,results,inspector);surface.append(panel);
    results.hidden=false;inspector.hidden=true;

    resultsToggle=document.createElement('button');resultsToggle.type='button';resultsToggle.id='graph-results-toggle';resultsToggle.className='secondary';
    resultsToggle.textContent='检索结果';resultsToggle.setAttribute('aria-controls',panel.id);resultsToggle.setAttribute('aria-expanded','false');
    resultsToggle.addEventListener('click',()=>activePanel==='results'?closePanel():showPanel('results'));
    const actions=get('graph-stage').querySelector('.graph-presentation-actions')||get('graph-stage').querySelector('.graph-stage-heading');
    actions.prepend(resultsToggle);
    status=document.createElement('p');status.id='graph-workspace-state';status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.hidden=true;
    header.after(status);

    // Observe content, never the visibility attributes changed by showPanel().
    // Nested evidence updates do not reopen an explicitly closed inspector.
    new MutationObserver(()=>{
      if(active()&&(get('hits').childElementCount||get('search-summary').textContent.trim()))showPanel('results');
    }).observe(get('hits'),{childList:true});
    new MutationObserver(()=>{
      if(active()&&get('search-summary').textContent.trim())showPanel('results');
    }).observe(get('search-summary'),{childList:true,characterData:true,subtree:true});
    new MutationObserver(()=>{
      if(active()&&get('graph-detail').hasChildNodes())showPanel('detail');
    }).observe(get('graph-detail'),{childList:true});
    let pathOpen=false;
    new MutationObserver(()=>{
      const next=inspector.classList.contains('is-path-open');
      if(next&&!pathOpen&&active())showPanel('detail');pathOpen=next;
    }).observe(inspector,{attributes:true,attributeFilter:['class']});

    document.addEventListener('workspace:page-changed',()=>{if(active())placeScope(get('scope'));resize();});
    get('project').addEventListener('change',closePanel);
    document.addEventListener('workspace:scope-changed',resize);
    document.addEventListener('fullscreenchange',resize);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)resize();});
    window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);
    // Watch only finite layout boundaries; equal-size observations make no writes.
    const observer=new ResizeObserver(resize);
    for(const node of [header,page.parentElement,get('status'),document.querySelector('body>aside')])if(node)observer.observe(node);
    new MutationObserver(resize).observe(surface,{attributes:true,attributeFilter:['class']});
    resize();
  }

  window.GraphWorkspaceLayout=Object.freeze({placeScope,openResults:()=>showPanel('results'),openDetail:()=>showPanel('detail'),closePanel,setState,resize,settled,afterLayout:settled,
    snapshot:()=>Object.freeze({panel:activePanel||'closed',state:surface?.dataset.state||'idle',active:active()})});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
