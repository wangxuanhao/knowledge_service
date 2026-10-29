/* Shared fixed-version content, authorized images and owned browser resources. */
(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.SourceContent=api;
})(typeof window==='object'?window:globalThis,root=>{
  const sourceUrl=(project,version,suffix='')=>'/api/projects/'+encodeURIComponent(project)+'/source-documents/'+encodeURIComponent(version)+(suffix?'/'+suffix:'');
  function validatedAssetUrl(value,project,version,origin=root.location?.origin){
    if(typeof value!=='string'||!value||!origin||value.includes('\\')||/(?:^|\/)(?:\.|%2e){1,2}(?:\/|[?#]|$)/i.test(value))return null;
    try{
      const base=new URL(origin),url=new URL(value,base),prefix=sourceUrl(project,version,'assets/');
      if(url.origin!==base.origin||!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||!url.pathname.startsWith(prefix))return null;
      const id=url.pathname.slice(prefix.length),decoded=decodeURIComponent(id);
      if(!id||/[\/\\]/.test(decoded)||['.','..'].includes(decoded))return null;
      return url.pathname;
    }catch{return null;}
  }
  const aborted=()=>Object.assign(Error('来源读取已取消'),{name:'AbortError'});
  function createResourceScope({signal:parentSignal,isCurrent=()=>true,fetcher=(...args)=>root.fetch(...args),urlApi=root.URL,maxConcurrent=3}={}){
    const controller=new AbortController(),urls=new Set(),cleanups=new Set(),queue=[];
    let disposed=false,running=0;
    const active=()=>!disposed&&!controller.signal.aborted&&isCurrent();
    const release=url=>{if(urls.delete(url))urlApi.revokeObjectURL(url);};
    function objectUrl(blob){
      if(!active())return null;
      const url=urlApi.createObjectURL(blob);urls.add(url);
      if(!active()){release(url);return null;}return url;
    }
    function dispose(){
      if(disposed)return;disposed=true;controller.abort();parentSignal?.removeEventListener('abort',dispose);
      for(const job of queue.splice(0))job.resolve(null);
      for(const cleanup of cleanups)cleanup();cleanups.clear();
      for(const url of [...urls])release(url);
    }
    function addCleanup(cleanup){if(disposed)cleanup();else cleanups.add(cleanup);return ()=>cleanups.delete(cleanup);}
    async function read(url,kind='text'){
      if(!active())throw aborted();
      // All source and asset reads pass through auth.js's authorized fetch wrapper.
      // A redirect must never turn a validated internal URL into an external read.
      const response=await fetcher(url,{signal:controller.signal,redirect:'error'});
      if(!active())throw aborted();
      if(!response.ok)throw Error('来源读取失败（HTTP '+response.status+'）');
      const value=await response[kind]();if(!active())throw aborted();return value;
    }
    const limit=Number.isInteger(maxConcurrent)?Math.max(1,Math.min(maxConcurrent,6)):3;
    function pump(){
      while(active()&&running<limit&&queue.length){
        const job=queue.shift();running++;
        (async()=>{try{
          const blob=await read(job.url,'blob'),url=objectUrl(blob);
          if(url&&active())job.ready(url,blob);return url;
        }catch(error){if(error.name!=='AbortError'&&active())job.failed?.(error);return null;}
        })().then(job.resolve).finally(()=>{running--;pump();});
      }
      if(!active())for(const job of queue.splice(0))job.resolve(null);
    }
    function loadBlob(url,ready,failed){
      if(!active())return Promise.resolve(null);
      return new Promise(resolve=>{queue.push({url,ready,failed,resolve});pump();});
    }
    parentSignal?.addEventListener('abort',dispose,{once:true});if(parentSignal?.aborted)dispose();
    return {active,signal:controller.signal,dispose,addCleanup,read,loadBlob,objectUrl,release};
  }
  function downloadOptions(doc={}){
    const name=doc.original_name||doc.source_file||'source',filename=name.replace(/\\/g,'/').split('/').pop(),format=doc.format||doc.source_format;
    const markdown=['md','markdown'].includes(String(format).toLowerCase())||/\.(md|markdown)$/i.test(name);
    const packaged=markdown&&(doc.mime_type==='application/zip'||doc.source_mime_type==='application/zip'||doc.package_mime_type==='application/zip');
    return [{suffix:'source',label:packaged?'下载当前 Markdown':'下载固定版本原文件',filename},
      ...(packaged?[{suffix:'package',label:'下载原始 ZIP 知识包',filename:filename.replace(/\.(md|markdown)$/i,'')+'.zip'}]:[])];
  }
  function createSession({host,projectId,versionId,...options}){
    const document=host.ownerDocument,scope=createResourceScope(options),seen=new WeakSet(),images=new Set();
    const make=(tag,text,css='')=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(css)node.className=css;return node;};
    let observer=null,zoom=null;
    const closeZoom=(restore=true)=>{
      if(!zoom)return;const current=zoom;zoom=null;
      current.image.removeAttribute('src');scope.release(current.url);
      if(current.dialog.open)current.dialog.close();current.dialog.remove();
      if(restore&&scope.active()&&current.trigger.isConnected)current.trigger.focus();
    };
    function openZoom(trigger,img,blob){
      if(!scope.active())return;closeZoom(false);const url=scope.objectUrl(blob);if(!url)return;
      const dialog=make('dialog',undefined,'source-image-zoom');dialog.setAttribute('aria-label','图片全屏查看');
      const heading=make('header'),caption=make('p',img.alt||'原文图片'),close=make('button','关闭图片 ×','secondary');close.type='button';
      const image=make('img');image.alt=img.alt||'原文图片';image.src=url;
      heading.append(caption,close);dialog.append(heading,image);document.body.append(dialog);
      zoom={dialog,image,url,trigger};
      close.onclick=()=>closeZoom();dialog.addEventListener('cancel',event=>{event.preventDefault();closeZoom();});dialog.addEventListener('close',()=>{if(zoom?.dialog===dialog)closeZoom();});
      dialog.showModal();close.focus();
    }
    function mount(html){
      if(!scope.active())return false;
      if(typeof html!=='string'||!html.trim())throw Error('图文原文为空');
      // This endpoint is server-sanitized. Parse detached so no image can request
      // bytes before its project/version URL is validated and authorized.
      const template=document.createElement('template');template.innerHTML=html;
      for(const node of template.content.querySelectorAll('script,style,link,meta,iframe,object,embed,source'))node.remove();
      for(const node of template.content.querySelectorAll('*'))for(const name of node.getAttributeNames()){
        if(/^on/i.test(name)||['srcset','autofocus'].includes(name))node.removeAttribute(name);
      }
      for(const img of template.content.querySelectorAll('img')){
        img.dataset.assetUrl=img.getAttribute('src')||'';img.removeAttribute('src');
      }
      for(const link of template.content.querySelectorAll('a')){
        const href=link.getAttribute('href')||'';
        if(!/^(https?:|mailto:|#|\/)/i.test(href))link.removeAttribute('href');
        link.setAttribute('rel','noopener noreferrer');
      }
      host.classList.add('source-rich-content');host.replaceChildren(template.content.cloneNode(true));return true;
    }
    function hydrateImages(){
      if(!scope.active())return;
      for(const img of host.querySelectorAll('img')){
        if(seen.has(img))continue;seen.add(img);images.add(img);
        const value=img.dataset.assetUrl||img.getAttribute('src'),url=validatedAssetUrl(value,projectId,versionId);
        img.removeAttribute('src');img.removeAttribute('srcset');img.decoding='async';
        const wrapper=make('span',undefined,'source-content-media'),button=make('button',undefined,'source-image-open'),state=make('span','图片待加载','source-image-state');
        button.type='button';button.disabled=true;button.setAttribute('aria-label','放大图片：'+(img.alt||'原文图片'));state.setAttribute('aria-live','polite');
        img.replaceWith(wrapper);button.append(img);wrapper.append(button,state);
        // OCR boxes must remain positioned against the image, without a caption
        // changing their normalized canvas height. Existing figcaptions survive.
        if(img.alt&&!wrapper.closest('figure')&&!wrapper.closest('.source-image-canvas'))wrapper.append(make('span',img.alt,'source-image-caption'));
        const fail=message=>{if(!scope.active())return;img.hidden=true;button.hidden=true;state.hidden=false;state.textContent=message+(img.alt?' · '+img.alt:'');wrapper.classList.add('source-image-failed');};
        if(!url){fail('图片未固化或地址不属于当前资料版本');continue;}
        const load=()=>{
          if(!scope.active())return;state.textContent='正在读取图片…';
          scope.loadBlob(url,(objectUrl,blob)=>{
            img.onload=()=>{if(scope.active()){state.hidden=true;button.disabled=false;}};
            img.onerror=()=>{scope.release(objectUrl);fail('图片无法显示');};
            img.src=objectUrl;button.onclick=()=>openZoom(button,img,blob);
          },()=>fail('图片读取失败，请下载原文件查看'));
        };
        if(typeof root.IntersectionObserver==='function'){
          observer??=new root.IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){observer.unobserve(entry.target);entry.target._sourceImageLoad?.();}}, {rootMargin:'160px 0px'});
          wrapper._sourceImageLoad=load;observer.observe(wrapper);
        }else load();
      }
      for(const missing of host.querySelectorAll('span[data-asset-id]'))missing.classList.add('source-image-unavailable');
    }
    function downloadLink({suffix,label,filename}){
      const link=make('a',label);link.href=sourceUrl(projectId,versionId,suffix);link.download=filename;
      link.onclick=async event=>{
        event.preventDefault();if(!scope.active())return;
        try{
          const blob=await scope.read(link.href,'blob'),url=scope.objectUrl(blob);if(!url)return;
          const temporary=make('a');temporary.href=url;temporary.download=filename;document.body.append(temporary);temporary.click();temporary.remove();
          setTimeout(()=>scope.release(url),1000);
        }catch(error){if(error.name!=='AbortError'&&scope.active()){
          let failure=link.parentNode.querySelector('.source-download-error');if(!failure){failure=make('span',undefined,'source-download-error');failure.setAttribute('role','status');link.after(failure);}failure.textContent='下载失败，请重试（'+error.message+'）';
        }}
      };return link;
    }
    scope.addCleanup(()=>{
      observer?.disconnect();closeZoom(false);
      for(const img of images){img.onload=null;img.onerror=null;img.removeAttribute('src');}
      images.clear();host.classList.remove('source-rich-content');
    });
    return {...scope,mount,hydrateImages,downloadLink,async render(){const html=await scope.read(sourceUrl(projectId,versionId,'rendered'));if(mount(html))hydrateImages();}};
  }
  return {sourceUrl,validatedAssetUrl,createResourceScope,createSession,downloadOptions};
});
