/* Enhance original navigation controls without replacing registered listeners. */
(()=>{
  const aside=document.querySelector('body>aside'),icons=window.WorkspaceIcons;
  if(!aside||!icons||document.getElementById('workspace-nav-collapse'))return;
  const storageKey='knowledge-service.navigation-collapsed',desktop=window.matchMedia('(min-width: 741px)');
  let preferred=false;
  try{preferred=window.localStorage.getItem(storageKey)==='true';}catch{/* Storage can be disabled by browser policy. */}
  const button=(id,label,icon)=>{
    const node=document.createElement('button');node.id=id;node.type='button';node.className='workspace-rail-control';
    node.setAttribute('aria-label',label);node.setAttribute('title',label);icons.decorate(node,icon);return node;
  };
  const toggle=button('workspace-nav-collapse','收起导航','collapse');
  toggle.setAttribute('aria-controls','workspace-navigation');aside.prepend(toggle);
  for(const item of aside.querySelectorAll('button[data-tab]')){
    const text=item.textContent.trim(),label=document.createElement('span');label.className='workspace-nav-label';label.textContent=text;
    item.replaceChildren(label);item.setAttribute('aria-label',text);item.setAttribute('title',text);
    icons.decorate(item,icons.tabIcons[item.dataset.tab]||'panel');
  }
  function syncCurrent(){
    for(const item of aside.querySelectorAll('button[data-tab]')){
      if(item.classList.contains('active'))item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');
    }
  }
  syncCurrent();new MutationObserver(syncCurrent).observe(aside,{attributes:true,subtree:true,attributeFilter:['class']});
  const project=document.getElementById('project'),projectLabel=project?.parentElement;
  let projectToggle=null;
  if(projectLabel){
    projectLabel.id=projectLabel.id||'workspace-project-picker';projectLabel.classList.add('workspace-project-picker');
    projectToggle=button('workspace-project-toggle','切换知识项目','project');
    projectToggle.setAttribute('aria-controls',projectLabel.id);projectToggle.setAttribute('aria-expanded','false');aside.prepend(projectToggle);
    projectToggle.addEventListener('click',()=>{
      const open=projectLabel.classList.toggle('workspace-project-open');projectToggle.setAttribute('aria-expanded',String(open));if(open)project.focus();
    });
  }
  function closeProject(restoreFocus=false){
    if(!projectLabel?.classList.contains('workspace-project-open'))return;
    projectLabel.classList.remove('workspace-project-open');projectToggle.setAttribute('aria-expanded','false');if(restoreFocus)projectToggle.focus();
  }
  function sync(){
    const compact=preferred&&desktop.matches;document.body.classList.toggle('workspace-nav-compact',compact);
    toggle.setAttribute('aria-expanded',String(!compact));
    const label=compact?'展开导航':'收起导航';toggle.setAttribute('aria-label',label);toggle.setAttribute('title',label);
    if(!compact)closeProject();
  }
  toggle.addEventListener('click',()=>{
    preferred=!preferred;try{window.localStorage.setItem(storageKey,String(preferred));}catch{/* Interaction does not depend on persistence. */}sync();
  });
  document.addEventListener('click',event=>{if(!projectLabel?.contains(event.target)&&!projectToggle?.contains(event.target))closeProject();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeProject(true);});
  desktop.addEventListener('change',sync);
  // Authentication renders this original button asynchronously; retain its menu handlers.
  const session=document.getElementById('session');
  function labelSession(){const who=session?.querySelector('.session-who');if(who){const text=who.textContent.trim().replace(/\s+/g,' ');who.setAttribute('aria-label',text+' · 账户菜单');who.setAttribute('title',text+' · 账户菜单');}}
  if(session){labelSession();new MutationObserver(labelSession).observe(session,{childList:true,subtree:true});}
  sync();
})();
