/* Shared graph presentation controls. No animation loop or data reload. */
(()=>{
  'use strict';
  function install(){
    const stage=document.getElementById('graph-stage'),heading=stage?.querySelector('.graph-stage-heading');
    if(!heading)return;
    const surface=stage.closest('.search-graph-workspace')||stage;
    const actions=document.createElement('div');actions.className='graph-presentation-actions';
    const modes=heading.querySelector('.full-graph-mode');if(modes)actions.append(modes);
    const toggle=document.createElement('button');toggle.type='button';toggle.id='graph-fullscreen';toggle.className='secondary';
    actions.append(toggle);heading.append(actions);
    let expanded=false,hadNative=false;
    function update(value){
      expanded=value;stage.classList.toggle('is-graph-fullscreen',value);
      surface.classList.toggle('is-graph-fullscreen-workspace',value);
      toggle.textContent=value?'退出全屏':'全屏';toggle.title=toggle.textContent;
      toggle.setAttribute('aria-label',value?'退出图谱全屏':'全屏查看图谱');toggle.setAttribute('aria-pressed',String(value));
    }
    async function leave(){
      update(false);
      if(document.fullscreenElement===surface)try{await document.exitFullscreen();}catch{}
    }
    toggle.addEventListener('click',async()=>{
      if(expanded){await leave();return;}
      update(true);
      // The CSS expansion also works in embedded browsers which deny native
      // fullscreen. Both paths use the renderers' existing resize observers.
      if(document.fullscreenEnabled&&surface.requestFullscreen)try{await surface.requestFullscreen();}catch{}
    });
    document.addEventListener('fullscreenchange',()=>{
      if(document.fullscreenElement===surface){hadNative=true;if(!expanded){leave();return;}update(true);}
      else if(hadNative){hadNative=false;update(false);}
    });
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&expanded){event.preventDefault();leave();}});
    update(false);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
