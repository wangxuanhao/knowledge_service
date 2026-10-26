/* One owned work drawer at a time; keep original controls, handlers and drafts. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.WorkspaceDrawers=api.createManager(root.document);
})(typeof window==='object'?window:globalThis,()=>{
  function createManager(document){
    let active=null;
    function create({id,title,description='',content,onClose=()=>{},dismissOnBackdrop=true}){
      const node=(tag,css,text)=>{const e=document.createElement(tag);if(css)e.className=css;if(text!==undefined)e.textContent=text;return e;};
      const dialog=node('dialog','workspace-drawer'),heading=node('header','workspace-drawer-heading');
      dialog.id=id;const titleNode=typeof title==='string'?node('h2',null,title):title;
      if(!titleNode.id)titleNode.id=id+'-title';dialog.setAttribute('aria-labelledby',titleNode.id);
      const labels=node('div'),closeButton=node('button','secondary workspace-drawer-close','关闭 ×');closeButton.type='button';
      labels.append(titleNode);
      if(description){const text=node('p','subtle',description);text.id=id+'-description';dialog.setAttribute('aria-describedby',text.id);labels.append(text);}
      heading.append(labels,closeButton);
      const body=node('div','workspace-drawer-body');if(content)body.append(content);
      dialog.append(heading,body);document.body.append(dialog);
      let trigger=null,isOpen=false,restoreFocus=true;
      function finished(){
        if(!isOpen)return;isOpen=false;if(active===instance)active=null;
        onClose();if(restoreFocus&&trigger?.isConnected)trigger.focus?.();
      }
      const instance={dialog,body,
        open(options={}){
          if(active===instance&&isOpen)return instance;
          active?.close({restoreFocus:false});trigger=options.trigger||document.activeElement;
          restoreFocus=true;isOpen=true;active=instance;dialog.showModal();closeButton.focus?.();return instance;
        },
        close(options={}){if(!isOpen)return;restoreFocus=options.restoreFocus!==false;dialog.close();finished();},
      };
      closeButton.onclick=()=>instance.close();
      dialog.addEventListener('cancel',event=>{event.preventDefault();instance.close();});
      dialog.addEventListener('close',()=>{if(!dialog.open)finished();});
      dialog.addEventListener('click',event=>{
        if(!dismissOnBackdrop||event.target!==dialog)return;const box=dialog.getBoundingClientRect();
        if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)instance.close();
      });
      return instance;
    }
    return {create,close(options){active?.close(options);},get active(){return active;}};
  }
  return {createManager};
});
