/* Keep type filters compact while preserving their existing selection handlers. */
(()=>{
  'use strict';
  const menus=new WeakMap(),bound=new WeakSet();
  let nextId=0,active=null;

  function close(state,restoreFocus=false){
    if(!state)return;
    if(state.popup.matches(':popover-open'))state.popup.hidePopover();
    state.toggle.setAttribute('aria-expanded','false');
    if(active===state)active=null;
    if(restoreFocus)state.toggle.focus({preventScroll:true});
  }

  function position(state){
    const box=state.toggle.getBoundingClientRect(),viewport=window.visualViewport;
    const width=viewport?.width||innerWidth,height=viewport?.height||innerHeight;
    const left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0;
    const above=box.top-top-12,below=top+height-box.bottom-12;
    const upwards=above>=Math.min(240,below),available=Math.max(96,upwards?above:below);
    const menuWidth=Math.min(460,width-24);
    Object.assign(state.popup.style,{
      width:menuWidth+'px',left:Math.max(left+12,Math.min(box.left,left+width-menuWidth-12))+'px',
      top:upwards?'auto':box.bottom+6+'px',bottom:upwards?innerHeight-box.top+6+'px':'auto',
      maxHeight:Math.min(440,available)+'px'
    });
  }

  function mount(root,{row,list,description,pagination,total,selectedLabel=''}){
    if(!root||!row)return;
    let state=menus.get(root);
    if(!state||state.row!==row){
      const popup=document.createElement('div');popup.className='graph-type-menu';
      popup.id='graph-type-menu-'+(++nextId);popup.setAttribute('popover','auto');
      popup.setAttribute('role','dialog');popup.setAttribute('aria-label','筛选实体类型');
      const heading=document.createElement('header');heading.className='graph-type-menu-heading';
      const title=document.createElement('strong');title.textContent='实体类型';
      const dismiss=document.createElement('button');dismiss.type='button';dismiss.textContent='×';
      dismiss.setAttribute('aria-label','收起类型列表');
      heading.append(title,dismiss);popup.append(heading);
      if(description){description.classList.add('graph-type-menu-description');popup.append(description);}
      if(!list){
        list=document.createElement('div');
        for(const button of [...row.querySelectorAll('button')].slice(1))list.append(button);
      }
      list.classList.add('graph-type-choices');popup.append(list);
      if(pagination)popup.append(pagination);
      const toggle=document.createElement('button');toggle.type='button';toggle.className='secondary graph-type-toggle';
      toggle.dataset.graphTypeToggle='';toggle.setAttribute('popovertarget',popup.id);
      toggle.setAttribute('aria-controls',popup.id);toggle.setAttribute('aria-haspopup','dialog');
      toggle.setAttribute('aria-expanded','false');
      row.classList.add('graph-type-bar');row.append(toggle);root.append(popup);
      state={row,list,popup,toggle};menus.set(root,state);
      dismiss.addEventListener('click',()=>close(state,true));
      toggle.addEventListener('click',()=>position(state));
      popup.addEventListener('beforetoggle',event=>{
        const open=event.newState==='open';toggle.setAttribute('aria-expanded',String(open));
        if(open){active=state;position(state);}else if(active===state)active=null;
      });
      if(!bound.has(root)){
        root.addEventListener('click',event=>{
          const button=event.target.closest('[data-fg-type],[data-type-index]');
          if(button)close(menus.get(root),button.dataset.typeIndex!=='0');
          if(event.target.closest('[data-fg-types-all]'))close(menus.get(root));
        });
        bound.add(root);
      }
    }
    state.toggle.textContent=selectedLabel?'筛选：'+selectedLabel:'筛选类型 · '+total.toLocaleString();
    state.toggle.title=selectedLabel||'展开全部 '+total.toLocaleString()+' 个实体类型';
    state.toggle.disabled=!total;
    if(!total)close(state);
    return state;
  }

  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&active){event.preventDefault();close(active,true);}
  });
  document.addEventListener('workspace:page-changed',()=>close(active));
  document.addEventListener('workspace:scope-changed',()=>close(active));
  window.addEventListener('resize',()=>{if(active)position(active);});
  window.visualViewport?.addEventListener('resize',()=>{if(active)position(active);});
  window.GraphTypeMenu=Object.freeze({mount});
})();
