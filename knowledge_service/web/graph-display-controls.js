/* Keep the existing retrieval checkbox authoritative while presenting a state button. */
(function(){
  'use strict';
  function install(){
    const input=document.getElementById('ontology-expansion');
    const toggle=document.getElementById('ontology-expansion-toggle');
    if(!input||!toggle)return;
    const state=toggle.querySelector('[data-expansion-state]');
    const sync=()=>{
      toggle.setAttribute('aria-pressed',String(input.checked));
      if(state)state.textContent=input.checked?'开':'关';
    };
    toggle.addEventListener('click',()=>{
      input.checked=!input.checked;
      input.dispatchEvent(new Event('change',{bubbles:true}));
      sync();
    });
    input.addEventListener('change',sync);
    sync();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
