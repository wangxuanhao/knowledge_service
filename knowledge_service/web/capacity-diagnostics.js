/* Opt-in, passive browser evidence. Load only for ?capacity_audit=1. */
(function(root){
  'use strict';
  const rounded=value=>Number.isFinite(value)?Math.round(value*1000)/1000:null;
  const enabled=search=>new URLSearchParams(search||'').get('capacity_audit')==='1';
  function createSeries(limit){
    return {values:[],limit,cursor:0,count:0,total:0,min:Infinity,max:-Infinity,add(value){
      if(!Number.isFinite(value)||value<0)return;
      this.count++;this.total+=value;this.min=Math.min(this.min,value);this.max=Math.max(this.max,value);
      if(this.values.length<this.limit)this.values.push(value);else{this.values[this.cursor]=value;this.cursor=(this.cursor+1)%this.limit;}
    }};
  }
  function summarize(series){
    const sorted=series.values.slice().sort((a,b)=>a-b),at=q=>sorted.length?rounded(sorted[Math.max(0,Math.ceil(q*sorted.length)-1)]):null;
    return {count:series.count,retained:sorted.length,retention:'most recent samples',min_ms:rounded(series.min),max_ms:rounded(series.max),mean_ms:series.count?rounded(series.total/series.count):null,p50_ms:at(.5),p95_ms:at(.95),p99_ms:at(.99)};
  }
  function auditDataset(dataset){
    const nodes=dataset.records||[],edges=dataset.edges||[],ids=new Set(),edgeIds=new Set();
    for(const row of nodes)ids.add(row.id);
    let dangling=0;for(const row of edges){edgeIds.add(row.id);if(!ids.has(row.subject_id)||!ids.has(row.object_id))dangling++;}
    return {entities:nodes.length,relations:edges.length,unique_entities:ids.size,unique_relations:edgeIds.size,duplicate_entity_ids:nodes.length-ids.size,duplicate_relation_ids:edges.length-edgeIds.size,dangling_relations:dangling};
  }
  function safeResource(entry,origin){
    let url;try{url=new URL(entry.name,origin);}catch(_){return null;}
    if(url.origin!==origin||!/^https?:$/.test(url.protocol))return null;
    return {path:url.pathname,duration_ms:rounded(entry.duration),transfer_bytes:entry.transferSize||0,encoded_bytes:entry.encodedBodySize||0,decoded_bytes:entry.decodedBodySize||0,status:entry.responseStatus||null,initiator:entry.initiatorType||null};
  }
  function observeMethod(target,name,hooks){
    const original=target[name];if(typeof original!=='function'||original.capacityObserved)return;
    function invoke(kind,self,args){try{return hooks[kind]?.apply(self,args);}catch(_){/* Diagnostics cannot change product control flow. */}}
    function observed(...args){
      const token=invoke('before',this,args);let result;
      try{result=original.apply(this,args);}catch(error){invoke('error',this,[error,token]);throw error;}
      if(result&&typeof result.then==='function')result.then(value=>invoke('after',this,[value,token]),error=>invoke('error',this,[error,token]));
      else invoke('after',this,[result,token]);
      return result;
    }
    observed.capacityObserved=true;target[name]=observed;
  }
  const api={enabled,createSeries,summarize,auditDataset,safeResource,observeMethod};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(!root||!enabled(root.location?.search))return;
  const document=root.document;if(!document)return;
  function install(){
    if(document.getElementById('capacity-audit'))return;
    const performance=root.performance,now=()=>performance.now();
    const widget=document.createElement('details');widget.id='capacity-audit';
    widget.setAttribute('aria-label','容量诊断（仅此 URL 开启）');
    widget.style.cssText='position:fixed;bottom:8px;right:8px;z-index:9999;max-width:min(600px,90vw);max-height:70vh;overflow:auto;background:#fff;color:#173b2c;border:1px solid #7c978a;border-radius:6px;padding:7px;font:12px/1.45 monospace;box-shadow:0 2px 12px #0002';
    widget.innerHTML='<summary id="capacity-audit-summary">容量诊断：等待图谱</summary><p>被动记录真实操作；仅当前 URL 开启。明细只含聚合计数。</p><div><button type="button" id="capacity-audit-start">重新开始测量</button> <button type="button" id="capacity-audit-stop">停止测量</button> <button type="button" id="capacity-audit-export">导出 JSON</button></div><pre id="capacity-audit-report" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:52vh;overflow:auto" aria-label="容量诊断 JSON"></pre>';
    document.body.appendChild(widget);
    const summary=widget.querySelector('summary'),pre=widget.querySelector('pre');
    let state,loaderRuns=new WeakMap(),lastFrame=null,activeUntil=0,pointerDown=null,pendingSelection=null,detailObserver=null,observedDetail=null,report=null,lastReportAt=-Infinity;
    const seenCanvases=new WeakSet();
    function reset(){
      const timestamp=now();state={running:true,started:timestamp,started_utc:new Date().toISOString(),stopped:null,lastVisibilityAt:timestamp,wasVisible:document.visibilityState==='visible',visibleMs:0,hiddenMs:0,
        series:{active_frames:createSeries(70000),idle_frames:createSeries(100000),draw:createSeries(5000),sync:createSeries(5000),selection:createSeries(2000),longtask:createSeries(2000)},
        runs:[],nextRun:1,events:{pointerdown:0,pointermove:0,pointerup:0,wheel:0,keydown:0},interactionWindows:0,selectionAttempts:0,selectionMisses:0,selectionTimeouts:0,
        contextLost:0,contextRestored:0,errors:[],resourceCount:0,resourceBytes:0,resources:new Map(),recentResources:[],resourceSeries:createSeries(5000),evidenceSamples:0,completeChecks:0,mismatchChecks:0,heapFirst:null,heapLast:null,heapMax:null,diagnosticMs:0};
      loaderRuns=new WeakMap();lastFrame=null;activeUntil=0;pointerDown=null;pendingSelection=null;
    }
    function visibilityTime(){const timestamp=now(),duration=timestamp-state.lastVisibilityAt;if(state.running){if(state.wasVisible)state.visibleMs+=duration;else state.hiddenMs+=duration;}state.lastVisibilityAt=timestamp;state.wasVisible=document.visibilityState==='visible';}
    function stats(renderer){if(!renderer)return null;try{return renderer.stats();}catch(_){return null;}}
    function currentRun(loader){return loaderRuns.get(loader);}
    function visible(renderer){return document.visibilityState==='visible'&&root.FullGraphView?.mode==='full'&&renderer?.canvas?.getClientRects().length>0;}
    function noteError(error){if(state.running&&state.errors.length<12)state.errors.push({elapsed_ms:rounded(now()-state.started),kind:error?.name||'Error'});}
    function updateRun(loader){
      const run=currentRun(loader);if(!state.running||!run)return;
      run.state=loader.state;run.phase=loader.phase||null;
      if(loader.manifest)run.manifest={entities:loader.manifest.entity_count,relations:loader.manifest.relation_count,project_id:loader.manifest.project_id||null};
      if(loader.state==='complete'&&run.all_loaded_ms===null)run.all_loaded_ms=rounded(now()-run.started);
    }
    function afterDraw(renderer){
      if(!state.running)return;
      const loader=root.FullGraphView?.loader,run=loader&&currentRun(loader);
      if(!run||loader.renderer!==renderer)return;
      updateRun(loader);run.draw_calls++;
      const drawn=stats(renderer);if(!drawn)return;
      const hasVisibleData=drawn.loaded_entities>0&&drawn.drawn_entities===drawn.loaded_entities&&drawn.drawn_relations===drawn.loaded_relations;
      if(run.first_visible_ms===null&&!run.presentationPending&&hasVisibleData&&visible(renderer)){
        run.presentationPending=true;
        root.requestAnimationFrame(()=>{if(!state.running||!state.runs.includes(run))return;run.presentationPending=false;if(run.first_visible_ms===null&&visible(renderer)&&renderer.dataset===loader.dataset)run.first_visible_ms=rounded(now()-run.started);});
      }
      if(run.complete||loader.state!=='complete'||!visible(renderer)||drawn.drawn_entities!==run.manifest?.entities||drawn.drawn_relations!==run.manifest?.relations)return;
      const started=now();run.counts=drawn;run.validation=auditDataset(renderer.dataset);run.final_audit_ms=rounded(now()-started);
      // One real destructive read per completed load; never poll/drain getError.
      try{run.webgl_error=renderer.gl.getError();run.webgl_error_probe='one getError after final draw; remaining error queue not drained';}catch(error){run.webgl_error=null;noteError(error);}
      run.all_drawn_ms=rounded(now()-run.started);run.complete=true;
      const d=run.validation,m=run.manifest;
      run.counts_match=d.unique_entities===m.entities&&d.unique_relations===m.relations&&d.entities===m.entities&&d.relations===m.relations&&d.dangling_relations===0;
      state.diagnosticMs+=now()-started;
    }
    function hookClasses(){
      const graph=root.FullGraph;if(!graph)return;
      observeMethod(graph.SessionLoader.prototype,'begin',{
        before(){if(!state.running)return null;const run={number:state.nextRun++,started:now(),state:'starting',phase:null,manifest:null,first_batch_ms:null,first_visible_ms:null,all_loaded_ms:null,all_drawn_ms:null,complete:false,resume_calls:0,next_calls:0,draw_calls:0,sync_calls:0,resume_ms:null};state.runs.push(run);if(state.runs.length>20)state.runs.shift();loaderRuns.set(this,run);return run;},
        after(value,run){if(run){run.first_batch_ms=rounded(now()-run.started);updateRun(this);}},error(error,run){if(run){run.state=this.state;run.error_kind=error.name;}noteError(error);}
      });
      observeMethod(graph.SessionLoader.prototype,'resume',{
        before(){const run=currentRun(this);if(!state.running||!run)return null;run.resume_calls++;return {run,started:now()};},
        after(value,token){if(token){token.run.resume_ms=rounded(now()-token.started);updateRun(this);}},error:noteError
      });
      observeMethod(graph.SessionLoader.prototype,'next',{before(){const run=currentRun(this);if(state.running&&run)run.next_calls++;},after(){updateRun(this);},error:noteError});
      observeMethod(graph.SessionLoader.prototype,'progress',{after(){updateRun(this);}});
      observeMethod(graph.Renderer.prototype,'sync',{
        before(){return state.running?now():null;},after(value,started){if(started!==null){state.series.sync.add(now()-started);const run=currentRun(root.FullGraphView?.loader);if(run)run.sync_calls++;}},error:noteError
      });
      observeMethod(graph.Renderer.prototype,'draw',{
        before(){return state.running?now():null;},after(value,started){if(started!==null){state.series.draw.add(now()-started);afterDraw(this);}},error:noteError
      });
      observeMethod(graph.Renderer.prototype,'pick',{after(record){if(!state.running||!pendingSelection)return;if(record)pendingSelection.expected_id=record.id;else{state.selectionMisses++;pendingSelection=null;}}});
    }
    function canvasEvent(event){
      if(!state.running||!event.isTrusted||!event.target?.matches?.('canvas.fg-gl')||document.visibilityState!=='visible')return;
      if(event.type==='pointermove'&&!event.buttons)return;
      const timestamp=now();if(timestamp>activeUntil)state.interactionWindows++;activeUntil=timestamp+500;state.events[event.type]++;
      if(event.type==='pointerdown')pointerDown={started:timestamp,x:event.clientX,y:event.clientY,canvas:event.target};
      if(event.type==='pointerup'){
        if(pointerDown&&pointerDown.canvas===event.target&&Math.hypot(pointerDown.x-event.clientX,pointerDown.y-event.clientY)<5){
          state.selectionAttempts++;pendingSelection={started:pointerDown.started,expected_id:null,mutationSeen:false,scheduled:false};
        }pointerDown=null;
      }
    }
    function checkDetail(){
      const pending=pendingSelection,detail=observedDetail;
      if(!state.running||!pending||!pending.expected_id||!pending.mutationSeen||pending.scheduled||document.visibilityState!=='visible'||!detail?.getClientRects().length||detail.dataset.recordId!==pending.expected_id||!detail.querySelector('.evidence-identity h3'))return;
      pending.scheduled=true;
      root.requestAnimationFrame(()=>{if(!state.running||pendingSelection!==pending)return;if(document.visibilityState==='visible'&&detail.getClientRects().length&&detail.dataset.recordId===pending.expected_id){state.series.selection.add(now()-pending.started);pendingSelection=null;}else pending.scheduled=false;});
    }
    function discover(){
      hookClasses();const canvas=root.FullGraphView?.renderer?.canvas;
      if(canvas&&!seenCanvases.has(canvas)){seenCanvases.add(canvas);canvas.addEventListener('webglcontextlost',()=>{if(state.running)state.contextLost++;},{passive:true});canvas.addEventListener('webglcontextrestored',()=>{if(state.running)state.contextRestored++;},{passive:true});}
      const detail=document.getElementById('graph-detail');
      if(detail&&detail!==observedDetail){detailObserver?.disconnect();observedDetail=detail;detailObserver=new MutationObserver(()=>{if(pendingSelection)pendingSelection.mutationSeen=true;checkDetail();});detailObserver.observe(detail,{childList:true,subtree:true,attributes:true,attributeFilter:['data-record-id','hidden','class']});}
      if(pendingSelection&&now()-pendingSelection.started>15000){state.selectionTimeouts++;pendingSelection=null;}
      checkDetail();
    }
    function resource(entry){
      if(!state.running||entry.startTime<state.started)return;const item=safeResource(entry,root.location.origin);if(!item||!item.path.startsWith('/api/'))return;
      state.resourceCount++;state.resourceBytes+=item.transfer_bytes;state.resourceSeries.add(item.duration_ms);
      let key=item.path.replace(/\/projects\/[^/]+/,'/projects/:project').replace(/\/graph-sessions\/[^/]+/,'/graph-sessions/:session');
      if(!state.resources.has(key)&&state.resources.size>=40)key='other same-origin API';
      if(!state.resources.has(key))state.resources.set(key,{durations:createSeries(128),transfer_bytes:0,statuses:{}});
      const group=state.resources.get(key);group.durations.add(item.duration_ms);group.transfer_bytes+=item.transfer_bytes;group.statuses[item.status||'unavailable']=(group.statuses[item.status||'unavailable']||0)+1;
      state.recentResources.push(item);if(state.recentResources.length>8)state.recentResources.shift();
    }
    const observers={longtasks:false,resources:false};
    function observePerformance(type,callback){try{if(!root.PerformanceObserver?.supportedEntryTypes?.includes(type))return;const observer=new root.PerformanceObserver(list=>{for(const entry of list.getEntries())callback(entry);});observer.observe({type,buffered:false});observers[type==='longtask'?'longtasks':'resources']=true;}catch(_){/* Explicitly unavailable in report. */}}
    function frame(timestamp){
      if(state.running&&document.visibilityState==='visible'){
        if(lastFrame!==null){const series=timestamp<=activeUntil?state.series.active_frames:state.series.idle_frames;series.add(timestamp-lastFrame);}lastFrame=timestamp;
      }else lastFrame=null;
      root.requestAnimationFrame(frame);
    }
    function reportRun(run){const copy={...run,elapsed_ms:run.all_drawn_ms??rounded((state.stopped||now())-run.started)};delete copy.started;delete copy.presentationPending;return copy;}
    function publish(){
      const timestamp=now();if(timestamp-lastReportAt<2000)return;lastReportAt=timestamp;const diagnosticStarted=timestamp;visibilityTime();
      const renderer=root.FullGraphView?.renderer,loader=root.FullGraphView?.loader,current=stats(renderer),heap=performance.memory?.usedJSHeapSize;
      if(state.running){state.evidenceSamples++;if(loader?.state==='complete'&&current){state.completeChecks++;if(current.loaded_entities!==loader.manifest?.entity_count||current.loaded_relations!==loader.manifest?.relation_count||current.drawn_entities!==loader.manifest?.entity_count||current.drawn_relations!==loader.manifest?.relation_count)state.mismatchChecks++;}if(Number.isFinite(heap)){if(state.heapFirst===null)state.heapFirst=heap;state.heapLast=heap;state.heapMax=Math.max(state.heapMax||0,heap);}}
      const complete=state.runs.filter(run=>run.complete),first=createSeries(20),batch=createSeries(20),loaded=createSeries(20),all=createSeries(20);for(const run of complete){if(run.first_visible_ms!==null)first.add(run.first_visible_ms);if(run.first_batch_ms!==null)batch.add(run.first_batch_ms);if(run.all_loaded_ms!==null)loaded.add(run.all_loaded_ms);all.add(run.all_drawn_ms);}
      report={schema:'capacity-audit-v1',source:'opt-in passive product instrumentation',running:state.running,started_utc:state.started_utc,updated_utc:new Date().toISOString(),
        timing_definitions:{first_visible:'begin invocation to next visible rAF after a nonempty complete draw of the current partial dataset',first_batch:'begin invocation to its original promise resolving',all_loaded:'begin invocation to loader state complete',all_drawn:'begin invocation to final draw, unique-ID audit and one GL error probe',active_frames:'visible rAF intervals inside 500 ms after trusted canvas pointer/wheel/key input',selection:'trusted pointerdown/click to next visible rAF with the selected record identity in #graph-detail; source/version follow-up requests excluded'},
        elapsed_seconds:rounded(((state.stopped||timestamp)-state.started)/1000),visible_seconds:rounded(state.visibleMs/1000),hidden_seconds:rounded(state.hiddenMs/1000),current_visibility:document.visibilityState,mode:root.FullGraphView?.mode||null,loader_state:loader?.state||null,current,
        completed_loads:complete.length,loads:state.runs.map(reportRun),load_distribution:{first_visible:summarize(first),first_batch:summarize(batch),all_loaded:summarize(loaded),all_drawn:summarize(all)},
        frames:{active:summarize(state.series.active_frames),idle:summarize(state.series.idle_frames),trusted_input_events:{...state.events},interaction_windows:state.interactionWindows,quiet_window_ms:500},
        renderer_calls:{scope:'CPU method wall time; GPU completion time is unavailable',draw:summarize(state.series.draw),sync:summarize(state.series.sync)},selection:{latency:summarize(state.series.selection),attempts:state.selectionAttempts,empty_picks:state.selectionMisses,timeouts:state.selectionTimeouts,pending:!!pendingSelection},
        longtasks:{available:observers.longtasks,durations:summarize(state.series.longtask)},memory:{source:Number.isFinite(heap)?'performance.memory.usedJSHeapSize':'unavailable',first_js_heap_bytes:state.heapFirst,last_js_heap_bytes:state.heapLast,max_js_heap_bytes:state.heapMax,browser_rss:'unavailable from product JavaScript',cdp_metrics:'not collected'},
        stability:{evidence_samples:state.evidenceSamples,complete_graph_checks:state.completeChecks,count_mismatch_checks:state.mismatchChecks,context_lost_events:state.contextLost,context_restored_events:state.contextRestored,errors:state.errors},
        http:{available:observers.resources,scope:'same-origin /api ResourceTiming entries observed since measurement start; no headers or response bodies',count:state.resourceCount,transfer_bytes:state.resourceBytes,duration:summarize(state.resourceSeries),groups:Array.from(state.resources,([path,value])=>({path,duration:summarize(value.durations),transfer_bytes:value.transfer_bytes,statuses:value.statuses})),recent:state.recentResources},
        overhead:{instrumentation_ms_observed:rounded(state.diagnosticMs),scope:'Opt-in wrappers; one extra visible rAF loop; 250 ms discovery; DOM JSON at most every 2 seconds; exact one-time O(entities + relations) audit per completed load; one GL error consumption per completed load. Percentiles use bounded recent samples; min/mean/max/count cover all observations. Overhead includes measured report/audit work only, not all observer overhead.',raw_numeric_sample_cap:194248,max_retained_loads:20}};
      summary.textContent='容量诊断 '+(state.running?'记录中':'已停止')+' · 实体 '+(current?.loaded_entities??'—')+'/'+(current?.drawn_entities??'—')+' · 关系 '+(current?.loaded_relations??'—')+'/'+(current?.drawn_relations??'—')+' · 活动帧 '+state.series.active_frames.count+' · 完整加载 '+complete.length;
      pre.textContent=JSON.stringify(report,null,2);state.diagnosticMs+=now()-diagnosticStarted;
    }
    widget.querySelector('#capacity-audit-start').addEventListener('click',reset);
    widget.querySelector('#capacity-audit-stop').addEventListener('click',()=>{visibilityTime();state.stopped=now();state.running=false;lastFrame=null;});
    widget.querySelector('#capacity-audit-export').addEventListener('click',()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),anchor=document.createElement('a');anchor.href=url;anchor.download='capacity-audit-'+Date.now()+'.json';anchor.click();root.setTimeout(()=>URL.revokeObjectURL(url),1000);});
    document.addEventListener('visibilitychange',()=>{visibilityTime();lastFrame=null;});
    for(const type of ['pointerdown','pointermove','pointerup','wheel','keydown'])document.addEventListener(type,canvasEvent,{capture:true,passive:true});
    root.addEventListener('error',event=>noteError(event.error));root.addEventListener('unhandledrejection',event=>noteError(event.reason));
    reset();observePerformance('longtask',entry=>{if(state.running&&entry.startTime>=state.started)state.series.longtask.add(entry.duration);});observePerformance('resource',resource);
    discover();publish();root.setInterval(discover,250);root.setInterval(publish,2000);root.requestAnimationFrame(frame);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})(typeof window!=='undefined'?window:null);
