/* Task navigation retains the actual legacy tab buttons and their registered loaders. */
(()=>{
  const nav=document.querySelector('aside nav');if(!nav)return;
  const buttons=new Map([...nav.querySelectorAll('button[data-tab]')].map(button=>[button.dataset.tab,button]));
  nav.replaceChildren();
  const groups=[['使用知识',['qa','search']],['管理资料',['sources','records','ontology-model']],['运行与质量',['runtime','quality']]];
  for(const [label,names] of groups){
    const group=document.createElement('section');group.className='nav-group';
    const title=document.createElement('h2');title.textContent=label;group.append(title);
    for(const name of names){const button=buttons.get(name);if(button)group.append(button);}
    nav.append(group);
  }
  const management=document.createElement('section');management.className='nav-group nav-management';management.dataset.layer='system';
  const title=document.createElement('h2');title.textContent='管理';management.append(title);
  for(const name of ['projects','useradmin']){const button=buttons.get(name);if(button)management.append(button);}
  nav.after(management);
  // Internal destinations stay registered so their original loaders and deep links remain usable.
  const internal=document.createElement('div');internal.hidden=true;
  for(const name of ['ingest','mindmap'])if(buttons.get(name))internal.append(buttons.get(name));
  nav.append(internal);
  const syncRoles=()=>{
    const viewer=document.body.classList.contains('is-viewer');
    document.querySelectorAll('aside .nav-group').forEach(group=>{const items=[...group.querySelectorAll('button')];group.hidden=viewer&&items.length>0&&items.every(button=>button.classList.contains('admin-only'));});
  };
  syncRoles();new MutationObserver(syncRoles).observe(document.body,{attributes:true,attributeFilter:['class']});
  buttons.get('useradmin')?.addEventListener('click',()=>window.UserAdmin?.open());
  buttons.get('projects')?.addEventListener('click',()=>window.ProjectsView?.refresh());
  buttons.get('quality')?.addEventListener('click',()=>window.RuntimeView?.show('snapshots',{quality:true}));
})();
