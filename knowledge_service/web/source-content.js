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
  function sourceInfo(doc={}){
    const primary=doc.parser_metadata?.provenance?.package;
    const filename=String(primary?.filename||primary?.entry_path||doc.original_name||doc.source_file||'source').replace(/\\/g,'/').split('/').pop();
    const extension=filename.includes('.')?filename.split('.').pop().toLowerCase():'';
    const format=String(primary?extension:doc.parser_metadata?.source_format||doc.metadata?.source_format||doc.source_format||doc.format||extension).toLowerCase();
    const mimeType=String(primary?.mime_type||doc.source_mime_type||doc.mime_type||'').split(';')[0].trim().toLowerCase();
    const packaged=Boolean(primary)||[doc.mime_type,doc.source_mime_type,doc.package_mime_type].includes('application/zip');
    const markdown=['md','markdown'].includes(format)||/\.(md|markdown)$/i.test(filename);
    const isPageManifest=Boolean(primary&&(format==='json'||mimeType==='application/json'));
    const binary=!isPageManifest&&(/^(pdf|docx?|odt|pptx?|png|jpe?g|webp|mp4|webm|zip)$/.test(format)||/\.(pdf|docx?|odt|pptx?|png|jpe?g|webp|mp4|webm|zip)$/i.test(filename)||/^(image|video)\//.test(mimeType)||mimeType==='application/pdf');
    const rawExplanation=isPageManifest?'下方是图片页序清单（JSON）；各页原图可在阅读视图查看，完整 ZIP 保留在独立下载入口。':
      binary?'下方是解析后的正文；原始二进制文件请通过下载入口查看。':
      '下方是当前固定版本的'+(markdown?' Markdown':format==='html'||format==='htm'?' HTML':'文本')+'源文件。'+(packaged?'原始 ZIP 知识包保留在独立下载入口。':'');
    return {filename,format,mimeType,packaged,markdown,isPageManifest,binary,rawExplanation};
  }
  function downloadOptions(doc={}){
    const {filename,packaged,markdown,isPageManifest}=sourceInfo(doc);
    const packageName=String(doc.original_name||filename).replace(/\\/g,'/').split('/').pop().replace(/\.[^.]+$/,'')+'.zip';
    return [{suffix:'source',label:isPageManifest?'下载图片页序清单':packaged?(markdown?'下载当前 Markdown':'下载当前资料原件'):'下载固定版本原文件',filename},
      ...(packaged?[{suffix:'package',label:'下载原始 ZIP 知识包',filename:packageName}]:[])];
  }
  function isPdfSource(doc={}){
    const source=sourceInfo(doc);
    return source.format==='pdf'||source.mimeType==='application/pdf'||/\.pdf$/i.test(source.filename);
  }
  function pdfRegions(doc,location,page){
    const width=page?.page_width,height=page?.page_height;
    if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)return [];
    const targets=[...(location?.spans||location?.locations||[]),location].filter(Boolean),regions=[];
    for(const target of targets){
      const block=(doc.blocks||[]).find(item=>(item.block_id||item.id)===target.block_id);
      const asset=(doc.assets||[]).find(item=>item.id===target.asset_id);
      const locations=target.payload?.source_locations||target.source_locations||block?.payload?.source_locations||asset?.provenance?.locations||[];
      for(const native of locations){
        if(Number(native.page_no??native.page_number)!==page.page_number)continue;
        const box=native.bbox;if(!box)continue;
        const {l,r,t,b,coord_origin:origin}=box;
        if(![l,r,t,b].every(Number.isFinite)||l<0||r>width||l>=r||Math.min(t,b)<0||Math.max(t,b)>height)continue;
        if(!['TOPLEFT','BOTTOMLEFT'].includes(origin)||(origin==='TOPLEFT'?t>=b:t<=b))continue;
        // Coordinates refer to the full original PDF page, never an OCR crop.
        regions.push({x:l/width,y:(origin==='BOTTOMLEFT'?height-t:t)/height,width:(r-l)/width,height:Math.abs(b-t)/height});
        if(regions.length>=50)return regions;
      }
    }
    return regions;
  }
  function createSession({host,projectId,versionId,...options}){
    const document=host.ownerDocument,scope=createResourceScope(options),seen=new WeakSet(),images=new Set(),videos=new Set(),pendingSeeks=new Map();
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
      const controls=make('div',undefined,'source-image-controls'),fit=make('button','按宽阅读','secondary'),original=make('button','原始比例','secondary');
      const scale=make('input'),scaleLabel=make('label','缩放 '),output=make('span','100%');
      fit.type=original.type='button';scale.type='range';scale.min='25';scale.max='300';scale.step='25';scale.value='100';scale.setAttribute('aria-label','图片缩放比例');
      scaleLabel.append(scale,output);controls.append(fit,original,scaleLabel);
      const viewport=make('div',undefined,'source-image-viewport'),image=make('img');image.alt=img.alt||'原文图片';image.src=url;
      const setScale=percent=>{image.style.width=Math.round((img.naturalWidth||img.width||1080)*percent/100)+'px';image.style.maxWidth='none';output.textContent=percent+'%';scale.value=String(percent);};
      fit.onclick=()=>{image.style.width='100%';image.style.maxWidth='100%';output.textContent='按宽';};
      original.onclick=()=>setScale(100);scale.addEventListener('input',()=>setScale(Number(scale.value)));fit.onclick();
      viewport.append(image);heading.append(caption,close);dialog.append(heading,controls,viewport);document.body.append(dialog);
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
      for(const media of template.content.querySelectorAll('img,video')){
        media.dataset.assetUrl=media.getAttribute('src')||'';media.removeAttribute('src');
        if(media.tagName==='VIDEO'){media.removeAttribute('poster');media.removeAttribute('autoplay');media.removeAttribute('loop');}
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
        let loading=false;
        const load=()=>{
          if(!scope.active()||loading)return;loading=true;state.textContent='正在读取图片…';
          scope.loadBlob(url,(objectUrl,blob)=>{
            img.onload=()=>{if(scope.active()){state.hidden=true;button.disabled=false;}};
            img.onerror=()=>{scope.release(objectUrl);fail('图片无法显示');};
            img.src=objectUrl;button.onclick=()=>openZoom(button,img,blob);
          },()=>fail('图片读取失败，请下载原文件查看'));
        };
        wrapper._sourceImageLoad=load;
        if(typeof root.IntersectionObserver==='function'){
          observer??=new root.IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){observer.unobserve(entry.target);entry.target._sourceImageLoad?.();}}, {rootMargin:'160px 0px'});
          observer.observe(wrapper);
        }else load();
      }
      for(const missing of host.querySelectorAll('span[data-asset-id]'))missing.classList.add('source-image-unavailable');
      hydrateVideos();
    }
    function focusRegion(assetId,boxes=[]){
      if(!scope.active())return false;
      const img=[...host.querySelectorAll('img[data-asset-id]')].find(node=>node.dataset.assetId===assetId);
      if(!img)return false;
      const region=boxes.find(box=>['x','y','width','height'].every(key=>Number.isFinite(box[key])&&box[key]>=0&&box[key]<=1)&&box.width>0&&box.height>0&&box.x+box.width<=1.000001&&box.y+box.height<=1.000001);
      if(!region){img.scrollIntoView({block:'center'});return true;}
      const wrapper=img.parentNode?.parentNode,canvas=img.closest('.source-image-canvas')||wrapper;
      if(!canvas)return false;
      for(const previous of canvas.querySelectorAll('.source-region-focus'))previous.remove();
      const marker=make('span',undefined,'source-region-focus');
      Object.assign(marker.style,{left:region.x*100+'%',top:region.y*100+'%',width:region.width*100+'%',height:region.height*100+'%'});
      marker.setAttribute('aria-label','已定位原图文字区域');canvas.append(marker);
      const scroll=()=>{if(scope.active())marker.scrollIntoView({block:'center',inline:'nearest'});};
      // Lazy long images need dimensions before a normalized region can scroll.
      if(img.complete&&img.naturalWidth)scroll();
      else{img.addEventListener('load',scroll,{once:true});scope.addCleanup(()=>img.removeEventListener('load',scroll));}
      wrapper?._sourceImageLoad?.();return true;
    }
    async function renderPdf(doc={},page=1,location=null){
      if(!scope.active())return;
      const pageNumber=Number.isInteger(Number(page))&&Number(page)>0?Number(page):1;
      const toolbar=make('div',undefined,'source-downloads'),controls=make('div',undefined,'source-pdf-controls');
      const state=make('p','正在生成 PDF 页面预览…','source-reader-note'),canvas=make('div',undefined,'source-pdf-page'),image=make('img');
      const previous=make('button','上一页','secondary'),next=make('button','下一页','secondary'),input=make('input'),total=make('span','/ — 页');
      previous.type=next.type='button';previous.disabled=next.disabled=true;
      input.type='number';input.min='1';input.step='1';input.value=String(pageNumber);input.setAttribute('aria-label','PDF 页码');
      controls.append(previous,input,total,next);canvas.append(image);canvas.hidden=true;
      state.setAttribute('role','status');image.decoding='async';seen.add(image);images.add(image);
      let request=0,currentPage=pageNumber,count=0,pageUrl=null,originalUrl=null;
      const open=make('a','新窗口打开 PDF');open.href=sourceUrl(projectId,versionId,'source');open.target='_blank';open.rel='noopener noreferrer';
      open.onclick=async event=>{
        event.preventDefault();if(!scope.active())return;
        const popup=root.open?.('','_blank');if(popup)popup.opener=null;
        try{
          if(!originalUrl)originalUrl=scope.objectUrl(await scope.read(sourceUrl(projectId,versionId,'source'),'blob'));
          if(!originalUrl||!scope.active()){popup?.close();return;}
          const target=originalUrl+'#page='+currentPage;
          if(popup)popup.location.href=target;else state.textContent='浏览器阻止了新窗口，请允许弹窗或下载 PDF 原件。';
        }catch(error){popup?.close();if(error.name!=='AbortError'&&scope.active())state.textContent='PDF 原件读取失败：'+error.message;}
      };
      toolbar.append(open);
      for(const option of downloadOptions(doc))toolbar.append(downloadLink({...option,label:option.suffix==='source'?'下载 PDF 原件':option.label}));
      host.classList.add('source-rich-content');host.replaceChildren(toolbar,controls,state,canvas);
      function updateControls(){previous.disabled=!count||currentPage<=1;next.disabled=!count||currentPage>=count;input.max=String(count||10000);input.value=String(currentPage);total.textContent='/ '+(count||'—')+' 页';}
      async function showPage(number){
        if(!scope.active())return;
        if(!Number.isInteger(number)||number<1||number>10000||(count&&number>count)){updateControls();state.textContent='请输入 1 到 '+(count||10000)+' 之间的页码';return;}
        const token=++request;state.textContent='正在读取 PDF 第 '+number+' 页…';canvas.setAttribute('aria-busy','true');
        try{
          const result=await scope.read(sourceUrl(projectId,versionId,'pdf-preview')+'?page='+number,'json');
          if(!scope.active()||token!==request)return;
          if(result.page_number!==number||!Number.isInteger(result.page_count)||result.page_count<number||result.page_count>10000||
            result.mime_type!=='image/png'||!Number.isInteger(result.width)||!Number.isInteger(result.height)||result.width<=0||result.height<=0||
            Math.max(result.width,result.height)>1800||result.width*result.height>3000000||typeof result.image_base64!=='string'||result.image_base64.length>20000000)throw Error('PDF 页面预览数据无效');
          const bytes=Uint8Array.from(root.atob(result.image_base64),character=>character.charCodeAt(0));
          const url=scope.objectUrl(new Blob([bytes],{type:'image/png'}));if(!url)return;
          const oldUrl=pageUrl;pageUrl=url;currentPage=number;count=result.page_count;
          image.width=result.width;image.height=result.height;image.alt=sourceInfo(doc).filename+' · 第 '+number+' 页';
          image.onload=()=>{if(scope.active()&&pageUrl===url){state.textContent='PDF 原件页面预览 · 第 '+number+' / '+count+' 页';canvas.removeAttribute('aria-busy');canvas.querySelectorAll('.source-region-focus')[0]?.scrollIntoView({block:'center',inline:'nearest'});}};
          image.onerror=()=>{scope.release(url);if(scope.active()&&pageUrl===url){canvas.hidden=true;state.textContent='PDF 页面图片无法显示，请下载原件查看';}};
          for(const marker of canvas.querySelectorAll('.source-region-focus'))marker.remove();
          for(const region of pdfRegions(doc,location,result)){
            const marker=make('span',undefined,'source-region-focus');marker.setAttribute('aria-label','已定位 PDF 原页文字区域');
            Object.assign(marker.style,{left:region.x*100+'%',top:region.y*100+'%',width:region.width*100+'%',height:region.height*100+'%'});canvas.append(marker);
          }
          canvas.hidden=false;image.src=url;if(oldUrl)scope.release(oldUrl);updateControls();
        }catch(error){if(error.name!=='AbortError'&&scope.active()&&token===request){canvas.removeAttribute('aria-busy');state.textContent='PDF 页面预览失败：'+error.message+'；可下载原件查看。';updateControls();}}
      }
      previous.onclick=()=>showPage(currentPage-1);next.onclick=()=>showPage(currentPage+1);input.onchange=()=>showPage(Number(input.value));
      scope.addCleanup(()=>{request++;image.onload=null;image.onerror=null;image.removeAttribute('src');});
      await showPage(pageNumber);
    }
    function seekVideo(assetId,startMs){
      if(!scope.active()||!Number.isFinite(startMs)||startMs<0)return false;
      pendingSeeks.set(assetId,startMs);
      const video=[...host.querySelectorAll('video[data-asset-id]')].find(node=>node.dataset.assetId===assetId);
      if(!video)return false;
      const seek=()=>{
        if(!scope.active())return;
        const seconds=pendingSeeks.get(assetId)/1000;
        if(!Number.isFinite(seconds))return;
        video.currentTime=Number.isFinite(video.duration)?Math.min(seconds,Math.max(0,video.duration-.01)):seconds;
        pendingSeeks.delete(assetId);
      };
      if(video.readyState>=1)seek();
      else{video.addEventListener('loadedmetadata',seek,{once:true});scope.addCleanup(()=>video.removeEventListener('loadedmetadata',seek));}
      video.scrollIntoView({block:'center'});return true;
    }
    function hydrateVideos(){
      if(!scope.active())return;
      for(const video of host.querySelectorAll('video')){
        if(seen.has(video))continue;seen.add(video);videos.add(video);
        const url=validatedAssetUrl(video.dataset.assetUrl||video.getAttribute('src'),projectId,versionId);
        video.removeAttribute('src');video.removeAttribute('autoplay');video.removeAttribute('poster');
        video.controls=true;video.preload='metadata';
        const state=make('span','正在读取视频…','source-video-state');state.setAttribute('role','status');video.after(state);
        const fail=message=>{if(scope.active()){state.textContent=message;video.hidden=true;}};
        if(!url){fail('视频未固化或地址不属于当前资料版本');continue;}
        scope.loadBlob(url,(objectUrl,blob)=>{
          if(blob.size>25_000_000){scope.release(objectUrl);fail('视频超过 25 MB 播放上限');return;}
          video.onloadedmetadata=()=>{if(scope.active())state.hidden=true;};
          video.onerror=()=>{scope.release(objectUrl);fail('视频无法播放，请下载原文件查看');};
          video.src=objectUrl;
          const start=pendingSeeks.get(video.dataset.assetId);
          if(start!==undefined)seekVideo(video.dataset.assetId,start);
        },()=>fail('视频读取失败，请下载原文件查看'));
      }
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
      for(const video of videos){video.pause();video.onloadedmetadata=null;video.onerror=null;video.removeAttribute('src');video.load();}
      videos.clear();pendingSeeks.clear();
      images.clear();host.classList.remove('source-rich-content');
    });
    return {...scope,mount,hydrateImages,hydrateVideos,seekVideo,focusRegion,renderPdf,downloadLink,async render(){const html=await scope.read(sourceUrl(projectId,versionId,'rendered'));if(mount(html))hydrateImages();}};
  }
  return {sourceUrl,validatedAssetUrl,createResourceScope,createSession,sourceInfo,downloadOptions,isPdfSource,pdfRegions};
});
