/* Read only, version-pinned source inspection. */
(() => {
  function boxStyle(box){
    const values=['x','y','width','height'].map(k=>box[k]);
    if(values.some(v=>!Number.isFinite(v)||v<0||v>1)||box.x+box.width>1.000001||box.y+box.height>1.000001)return null;
    return {left:box.x*100+'%',top:box.y*100+'%',width:box.width*100+'%',height:box.height*100+'%'};
  }
  const sourceUrl=(project,version,suffix='')=>'/api/projects/'+encodeURIComponent(project)+'/source-documents/'+encodeURIComponent(version)+(suffix?'/'+suffix:'');
  function tableRowRange(payload,total){
    const start=payload?.table_start_row,end=payload?.table_end_row;
    if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||start>=end||end>total)return null;
    return [start,end];
  }
  function characterRange(text,start,end){
    const points=Array.from(text);
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||start>=end||end>points.length)return null;
    // Stored Python offsets count Unicode code points; DOM offsets count UTF-16 units.
    return [points.slice(0,start).join('').length,points.slice(0,end).join('').length];
  }
  function ocrProgress(run={}){
    const details=run.processing_details||{},tiles=Array.isArray(details.tiles)?details.tiles:[];
    const total=Math.max(0,Number(details.tiles_total)||tiles.length),completed=Math.max(0,Number(details.tiles_completed??tiles.filter(tile=>tile.status==='completed').length)||0);
    const failed=tiles.filter(tile=>run.status!=='ignored'&&['failed','error','pending'].includes(tile.status)).map((tile,index)=>{
      let region=tile.region||tile.bounds||tile.box;
      if(Array.isArray(region)&&region.length===4&&details.width>0&&details.height>0){
        const [left,top,right,bottom]=region;
        region={x:left/details.width,y:top/details.height,width:(right-left)/details.width,height:(bottom-top)/details.height};
      }
      return {...tile,index:tile.ordinal??tile.index??tile.tile_index??index,region:region&&boxStyle(region)?region:null,message:tile.error?.message||tile.public_error||''};
    });
    const incomplete=run.status!=='ignored'&&(run.status==='partial'||(total>0&&completed<total)||failed.length>0);
    return {total,completed,failed,incomplete,label:run.status==='processing'?'识别中':run.status==='pending'?'待识别':incomplete?'部分完成':run.status==='completed'?'识别成功':null};
  }
  function pdfPage(doc,location){
    const firstPage=values=>values.map(Number).find(page=>Number.isInteger(page)&&page>0);
    const pages=value=>value?[value.page_number,value.page_no,value.page,...(Array.isArray(value.source_locations)?value.source_locations.flatMap(item=>[item.page_number,item.page_no,item.page]):[])]:[];
    // Evidence spans are already clipped by locate; their native-page mapping
    // must win over a chunk-wide page list or a less specific image location.
    const spans=location?.spans||location?.locations||[],targets=[...spans,location].filter(Boolean);
    for(const target of targets){
      const block=(doc.blocks||[]).find(item=>(item.block_id||item.id)===target.block_id);
      const page=firstPage([...pages(target.payload),...pages(target),...pages(block?.payload)]);if(page)return page;
    }
    for(const target of targets){
      const asset=(doc.assets||[]).find(item=>item.id===target.asset_id),provenance=asset?.provenance;
      const page=firstPage([...pages(provenance),...(provenance?.locations||[]).flatMap(pages)]);if(page)return page;
    }
    return firstPage(targets.flatMap(target=>target.page_numbers||[]))||1;
  }
  if(typeof module!=='undefined')module.exports={boxStyle,sourceUrl,tableRowRange,characterRange,ocrProgress,pdfPage};
  if(typeof window==='undefined')return;
  const make=(tag,text,className='')=>{const n=document.createElement(tag);n.textContent=text??'';n.className=className;return n;};
  const labels={heading:'标题',paragraph:'段落',table:'表格',list:'列表',code:'代码',blockquote:'引用',image:'图片',image_ocr:'图片 OCR',completed:'识别成功',partial:'部分完成',failed:'识别失败',pending:'待识别',processing:'识别中',low_confidence:'置信度不足',needs_attention:'需要处理',available:'已固化',missing:'缺失',ignored:'已人工忽略',external_unfrozen:'外部图片，未固化'};
  const dialog=make('dialog','','source-reader');dialog.id='source-reader';dialog.setAttribute('aria-labelledby','source-reader-title');
  const header=make('header'),title=make('h2','固定版本原文');title.id='source-reader-title';
  const close=make('button','关闭 ×','secondary');close.type='button';close.onclick=()=>dialog.close();
  header.append(title,close);
  const note=make('p','','source-reader-note'),tabs=make('nav','','source-reader-tabs'),body=make('article','','source-reader-body');tabs.setAttribute('aria-label','来源视图');body.tabIndex=0;
  dialog.append(header,note,tabs,body);document.body.append(dialog);
  let controller=null,serial=0,viewSerial=0,returnFocus=null,selectedTab='reading',content=null,restoreFocus=true;
  function clearContent(){content?.dispose();content=null;}
  dialog.addEventListener('close',()=>{controller?.abort();serial++;viewSerial++;clearContent();if(restoreFocus&&returnFocus?.isConnected)returnFocus.focus();restoreFocus=true;});
  function invalidate(){restoreFocus=false;if(dialog.open)dialog.close();else{controller?.abort();clearContent();serial++;viewSerial++;}}
  document.getElementById('project')?.addEventListener('change',invalidate);
  document.addEventListener('workspace:scope-changed',invalidate);
  const tabButtons={};
  for(const [id,label] of [['reading','阅读视图'],['parsed','解析视图'],['raw','原始视图']]){
    const b=make('button',label,'secondary');b.type='button';tabs.append(b);tabButtons[id]=b;
  }
  async function get(url,signal,raw=false){const response=await fetch(url,{signal});if(!response.ok)throw Error('来源读取失败（HTTP '+response.status+'）');return raw?response.text():response.json();}
  function overlay(asset,boxes,project,version){
    const figure=make('figure','','source-reader-image'),canvas=make('div','','source-image-canvas'),img=make('img');img.alt=asset.alt_text||asset.original_path||'原图';img.dataset.assetId=asset.id;img.dataset.assetUrl=sourceUrl(project,version,'assets/'+encodeURIComponent(asset.id));if(asset.width>0&&asset.height>0){img.width=asset.width;img.height=asset.height;}canvas.append(img);
    for(const box of boxes){const style=boxStyle(box);if(!style)continue;const mark=make('span','','source-ocr-box');Object.assign(mark.style,style);mark.title=box.text||'OCR 文字区域';canvas.append(mark);}
    figure.append(canvas,make('figcaption',asset.alt_text||'图片 OCR 来源'));return figure;
  }
  function highlightText(host,normalized,start,end,focusText){
    const range=characterRange(normalized,start,end);if(!host||!range)return null;
    let ranges=[range];
    if(focusText){
      const evidence=normalized.slice(...range),mentions=[];let index=0;
      while((index=evidence.indexOf(focusText,index))!==-1&&mentions.length<50){mentions.push([range[0]+index,range[0]+index+focusText.length]);index+=focusText.length;}
      if(mentions.length)ranges=mentions;
    }
    // Inline formatting divides text nodes; lists and tables add layout whitespace / delimiters.
    // Verify the complete projection before touching the DOM, so repeated text cannot drift.
    const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT),segments=[];let offset=0,n;
    while((n=walker.nextNode())){
      const value=n.textContent;if(!value)continue;
      const index=normalized.indexOf(value,offset);
      if(/^\s*$/.test(value)&&(index<0||!/^[\s|]*$/.test(normalized.slice(offset,index))))continue;
      if(index<0||!/^[\s|]*$/.test(normalized.slice(offset,index)))return null;
      segments.push({node:n,start:index,end:index+value.length});offset=index+value.length;
    }
    if(!/^[\s|]*$/.test(normalized.slice(offset)))return null;
    const marks=[];
    for(const segment of segments.reverse())for(const [left,right] of [...ranges].reverse()){
      const a=Math.max(left,segment.start)-segment.start,b=Math.min(right,segment.end)-segment.start;
      if(a>=b)continue;
      segment.node.splitText(b);const selected=segment.node.splitText(a),mark=make('mark',selected.textContent,'source-highlight');
      selected.replaceWith(mark);marks.unshift(mark);
    }
    return marks[0]||null;
  }
  async function open({projectId,versionId,location=null,assertionId=null,chunkVersionId=null,focusText=null}){
    controller?.abort();clearContent();controller=new AbortController();const {signal}=controller,token=++serial;
    if(!dialog.open)returnFocus=document.activeElement;restoreFocus=true;body.textContent='正在加载固定版本原文…';note.textContent='';
    for(const button of Object.values(tabButtons))button.disabled=true;
    if(!dialog.open)dialog.showModal();
    try{
      let doc=await get(sourceUrl(projectId,versionId),signal);if(token!==serial)return;
      title.textContent=window.SourceContent.isPdfSource(doc)?window.SourceContent.sourceInfo(doc).filename:doc.original_name||doc.title||'固定版本原文';const versionNote='固定版本 '+(doc.version||versionId)+' · '+(doc.sha256||doc.source_object?.sha256||'');note.textContent=versionNote;
      if(!location&&(assertionId||chunkVersionId)){
        const query=new URLSearchParams();if(assertionId)query.set('assertion_id',assertionId);if(chunkVersionId)query.set('chunk_version_id',chunkVersionId);
        location=await get(sourceUrl(projectId,versionId,'locate')+'?'+query,signal);
      }
      if(token!==serial||!dialog.open)return;
      const documentSignal=signal;
      let parsed=doc.blocks||null,rendered=null,raw=null;
      async function show(tab){
        if(token!==serial||!dialog.open)return;
        selectedTab=tab;clearContent();const display=++viewSerial;
        const accepts=()=>token===serial&&display===viewSerial&&selectedTab===tab&&dialog.open;
        const view=window.SourceContent.createSession({host:body,projectId,versionId,signal:documentSignal,isCurrent:accepts});content=view;
        const signal=view.signal;
        body.textContent='正在加载…';note.textContent=versionNote;for(const [k,b] of Object.entries(tabButtons))b.setAttribute('aria-pressed',String(k===tab));
        try{
          if(tab==='raw'){
            const source=window.SourceContent.sourceInfo(doc),binary=source.binary;
            if(raw===null){raw=binary?doc.source_text||'该格式请下载原文件查看。':await get(sourceUrl(projectId,versionId,'source'),signal,true);}
            if(!accepts())return;
            const downloads=make('div','','source-downloads'),downloadItems=window.SourceContent.downloadOptions(doc);
            for(const option of downloadItems)downloads.append(view.downloadLink(option));
            const explanation=make('p',source.rawExplanation,'source-reader-note');
            body.replaceChildren(downloads,explanation,make('pre',raw));
          }else if(tab==='reading'){
            if(window.SourceContent.isPdfSource(doc)){
              await view.renderPdf(doc,pdfPage(doc,location),location);
              if(!accepts())return;
            }else{
              rendered??=await get(sourceUrl(projectId,versionId,'rendered'),signal,true);if(!accepts())return;
              view.mount(rendered);
            }
          }else{
            doc=await get(sourceUrl(projectId,versionId),signal);parsed=doc.blocks||[];
            if(!accepts())return;body.replaceChildren();
            for(const chunk of doc.chunks||[]){
              const part=make('details','','source-parsed-block');
              part.dataset.chunkVersionId=chunk.version_id;
              part.append(make('summary','模型片段 · '+chunk.id+' · 固定版本 '+chunk.version_id));
              const kind=chunk.metadata?.source_kind;
              part.append(make('p',kind==='video_asr'?'视频语音转写；点击下方语音时段定位原视频。':kind==='image_ocr'?'图片 OCR 文字；抽取时会附加识别误差提醒。':'Markdown / 文档正文片段'),make('pre',chunk.text));
              part.append(make('small','来源块：'+(chunk.spans||[]).map(s=>s.block_id||s.ocr_block_id||s.asr_segment_id).filter(Boolean).join('、')));
              body.append(part);
            }
            for(const block of parsed){const section=make('section','','source-parsed-block');section.dataset.blockId=block.block_id||block.id;
              section.append(make('h3',(labels[block.type||block.kind]||block.type||block.kind)+' · 行 '+block.source_start_line+'–'+block.source_end_line),make('pre',block.normalized_text));
              if(block.payload?.rows)section.append(make('pre',JSON.stringify(block.payload.rows,null,2)));body.append(section);}
            for(const asset of doc.assets||[]){
              const section=make('section','','source-parsed-block');section.append(make('h3',asset.original_path||'图片'),make('p','附件状态：'+(labels[asset.status]||asset.status||'未处理')));
              if(asset.provenance?.media_type==='video'){
                if(asset.kind==='external'){section.append(make('p','外部视频，未固化。请提供本地视频附件。'));body.append(section);continue;}
                const result=await get(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/asr'),signal);
                if(!accepts())return;
                const run=(result.runs||[]).find(item=>item.id===location?.asr_run_id)||result.run||{status:'pending'};
                section.append(make('p','ASR 状态：'+(labels[run.status]||run.status)),make('p',(run.engine||'SenseVoice / FSMN-VAD')+' '+(run.engine_version||'')));
                for(const segment of run.segments||result.segments||[]){
                  const seek=make('button',(segment.start_ms/1000).toFixed(2)+'–'+(segment.end_ms/1000).toFixed(2)+' 秒 · '+segment.normalized_text,'secondary source-asr-segment');seek.type='button';
                  seek.onclick=()=>{location={asset_id:asset.id,start_ms:segment.start_ms,end_ms:segment.end_ms,asr_run_id:run.id,asr_segment_id:segment.id,tab:'reading'};show('reading');};section.append(seek);
                }
                if(run.public_error)section.append(make('p',run.public_error));
                if(run.status==='failed'||run.status==='pending'){
                  const retry=make('button','重试语音转写','secondary');retry.type='button';
                  retry.onclick=async()=>{retry.disabled=true;try{await api(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/asr/retry'),{});await show('parsed');}catch(e){if(accepts()){retry.disabled=false;status(e.message,true);}}};section.append(retry);
                }
                body.append(section);continue;
              }
              if(asset.status==='missing'){
                section.append(make('p','缺失图片，请补传完整知识包。若已确认该引用不参与知识提取，可以明确忽略。'));
                const ignore=make('button','该引用不参与知识提取，忽略并继续','secondary');
                ignore.onclick=async()=>{const reason=prompt('请输入忽略缺失引用的原因（将保存操作者与时间）');if(!reason?.trim())return;try{await api(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/ocr/ignore'),{reason});await show('parsed');}catch(e){status(e.message,true);}};
                section.append(ignore);body.append(section);continue;
              }
              if(asset.kind==='external'){section.append(make('p','外部图片，未固化'));body.append(section);continue;}
              const result=await get(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/ocr'),signal);
              if(!accepts())return;
              const pinnedRunId=location?.ocr_run_id||location?.boxes?.[0]?.ocr_run_id;
              let run=(result.runs||[]).find(r=>r.id===pinnedRunId)||result.run||result;
              if(asset.status==='ignored')run={...run,status:'ignored',error_code:null,public_error:null};
              const progress=ocrProgress(run),boxes=run.blocks||result.blocks||[];
              section.append(make('p','OCR 状态：'+(progress.label||labels[run.status]||run.status||'未处理')),make('p',(run.engine||'图片 OCR')+' '+(run.engine_version||'')+' · 平均置信度 '+(run.average_confidence??'未知')));
              section.append(make('p','单次 OCR 重试只更新识别结果；更新检索切片或图谱需在资料详情重新处理。','source-reader-note'));
              if(progress.total)section.append(make('p',`已处理 ${progress.completed} / 总计 ${progress.total}`,'source-ocr-progress'));
              if(progress.incomplete&&!['processing','pending'].includes(run.status))section.append(make('p','识别覆盖不完整；已保留成功区域的文字，请定位失败区域并重试。','source-ocr-warning'));
              const locateRegion=(region,text)=>{location={asset_id:asset.id,ocr_run_id:run.id,boxes:region?[{...region,text}]:[],tab:'reading'};show('reading');};
              for(const [index,box] of boxes.entries()){
                const text=box.text||box.normalized_text||'';if(!text)continue;
                const locate=make('button',text,'secondary source-ocr-segment');locate.type='button';locate.setAttribute('aria-label','定位原图文字 '+(index+1));
                locate.onclick=()=>locateRegion(box,text);section.append(locate);
              }
              if(!boxes.length)section.append(make('pre',run.normalized_text||result.normalized_text||''));
              for(const tile of progress.failed){
                const region=tile.region;
                const locate=make('button','定位未完成区域 '+(tile.index+1)+(tile.message?' · '+tile.message:''),'secondary source-ocr-segment source-ocr-warning');locate.type='button';
                locate.onclick=()=>locateRegion(region,'未完成的 OCR 区域');section.append(locate);
              }
              section.append(overlay(asset,boxes,projectId,versionId));
              if(run.status!=='processing'&&(progress.incomplete||['failed','low_confidence','needs_attention','pending'].includes(run.status)||run.error_code)){
                section.append(make('p',run.public_error||'OCR 需要人工处理。'));
                const retry=make('button',progress.incomplete?'重试未完成 OCR 区域':'重试 OCR','secondary');retry.type='button';retry.onclick=async()=>{retry.disabled=true;try{await api(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/ocr/retry'),{});if(accepts()){status('识别结果已更新；如需更新检索或图谱，请在资料详情重新处理');await show('parsed');}}catch(e){if(accepts()){retry.disabled=false;status(e.message,true);}}};
                const ignore=make('button','该图片无知识文字，忽略并继续','secondary');ignore.onclick=async()=>{const reason=prompt('请输入忽略原因（将保存操作者与时间）');if(!reason?.trim())return;try{await api(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/ocr/ignore'),{reason});await show('parsed');}catch(e){status(e.message,true);}};section.append(retry,ignore);
              }
              body.append(section);
            }
          }
          if(!accepts())return;
          if(['source_mapping_unavailable','mapping_unavailable'].includes(location?.code)||location?.status==='mapping_unavailable')note.textContent+=' · 历史数据没有原文映射';
          const targets=location?.spans||location?.locations||[location].filter(Boolean);
          let firstFocus=null,precise=false;
          if(tab==='parsed')for(const chunk of doc.chunks||[]){
            const matched=targets.filter(target=>target.chunk_version_id===chunk.version_id);
            if(!matched.length)continue;
            const part=[...body.querySelectorAll('details[data-chunk-version-id]')].find(n=>n.dataset.chunkVersionId===chunk.version_id);
            if(!part)continue;part.open=true;part.classList.add('source-context');
            for(const target of matched){const mark=location?.precision==='chunk'?null:highlightText(part.querySelector('pre'),chunk.text,target.chunk_start_char,target.chunk_end_char,focusText);if(mark){firstFocus??=mark;precise=true;}}
            firstFocus??=part;
          }
          for(const target of targets){
            const id=target.block_id||target.id;
            const anchor=id?[...body.querySelectorAll('[data-block-id],[id]')].find(n=>n.dataset.blockId===id||n.id===id):null;
            if(!anchor)continue;
            const rows=[...anchor.querySelectorAll('tbody tr')],range=tableRowRange(target.payload,rows.length);
            let focus=anchor;
            if(range&&location?.precision!=='evidence'){for(const row of rows.slice(...range)){row.classList.add('source-context');
                const cStart=target.payload.table_start_column,cEnd=target.payload.table_end_column;
                if(Number.isInteger(cStart)&&Number.isInteger(cEnd))for(const cell of [...row.children].slice(cStart,cEnd))cell.classList.add('source-context');}
              focus=rows[range[0]];
            }else{
              const textHost=tab==='parsed'?anchor.querySelector('pre'):anchor;
              const block=(doc.blocks||[]).find(b=>b.block_id===id);
              const start=target.block_start_char,end=target.block_end_char;
              const mark=block&&location?.precision!=='chunk'?highlightText(textHost,block.normalized_text,start,end,focusText):null;
              if(mark){focus=mark;precise=true;}else anchor.classList.add('source-context');
            }
            firstFocus??=focus;
          }
          if(firstFocus){note.textContent+=precise?' · 已定位证据文字':' · 已定位来源块（未保存精确文字范围）';firstFocus.scrollIntoView({block:'center'});}
          if(location?.asset_id&&tab==='reading'){
            const asset=(doc.assets||[]).find(a=>a.id===location.asset_id);
            if(asset&&asset.provenance?.media_type!=='video'){
              const boxes=location.boxes||location.ocr_blocks||[],figure=overlay(asset,boxes,projectId,versionId);
              const existing=[...body.querySelectorAll('img[data-asset-id]')].find(n=>n.dataset.assetId===asset.id);
              if(existing)existing.replaceWith(figure);else body.append(figure);
              const result=await get(sourceUrl(projectId,versionId,'assets/'+encodeURIComponent(asset.id)+'/ocr'),signal);
              if(!accepts())return;
              const runId=location.ocr_run_id||boxes[0]?.ocr_run_id;
              const run=(result.runs||[]).find(r=>r.id===runId)||result.run||{};
              figure.append(make('p',(run.engine||'图片 OCR')+' '+(run.engine_version||'')+' · 平均置信度 '+(run.average_confidence??'未知')),make('pre',boxes.map(b=>b.text||b.normalized_text||'').join('\n')));
              // Hydration below loads the original before focusing its exact region.
            }
          }
          view.hydrateImages();
          if(tab==='reading'&&location?.asset_id&&!Number.isFinite(location.start_ms))view.focusRegion(location.asset_id,location.boxes||location.ocr_blocks||[]);
          if(tab==='reading'&&location?.asset_id&&Number.isFinite(location.start_ms))view.seekVideo(location.asset_id,location.start_ms);
        }catch(e){if(e.name!=='AbortError'&&accepts()){view.dispose();body.textContent=e.message;}}
      }
      for(const [id,b] of Object.entries(tabButtons)){
        b.onclick=()=>show(id);
        b.disabled=false;
      }
      await show(location?.tab==='parsed'?'parsed':'reading');
    }catch(e){if(e.name!=='AbortError'&&token===serial)body.textContent=e.message;}
  }
  window.SourceReader={open,dispose:invalidate,boxStyle,sourceUrl,tableRowRange,characterRange,ocrProgress,pdfPage};
})();
