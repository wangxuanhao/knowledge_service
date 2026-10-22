/* Full entity graph: compact buffers, bounded labels and shared record details. */
(function(root){
  'use strict';
  function hash(value){let n=2166136261;for(const c of String(value)){n^=c.charCodeAt(0);n=Math.imul(n,16777619);}return n>>>0;}
  function point(id){const h=hash(id),j=hash(id+'@y');return [((h&0xffffff)/0xffffff)*1.9-.95,((j&0xffffff)/0xffffff)*1.9-.95];}
  class GraphDataset{
    constructor(){this.records=[];this.edges=[];this.nodeIndex=new Map();this.edgeIndex=new Set();this.positions=new Float32Array(0);}
    get nodeCount(){return this.records.length;}
    get edgeCount(){return this.edges.length;}
    addNodes(rows,positions){
      const additions=[];
      rows.forEach((row,i)=>{if(this.nodeIndex.has(row.id)){const old=this.records[this.nodeIndex.get(row.id)];if(old.version_id!==row.version_id)throw new Error('同一会话收到不一致的实体版本');return;}this.nodeIndex.set(row.id,this.records.length);this.records.push(row);const xy=positions?[positions[i*2],positions[i*2+1]]:point(row.id);additions.push(...xy);});
      if(additions.length){const next=new Float32Array(this.positions.length+additions.length);next.set(this.positions);next.set(additions,this.positions.length);this.positions=next;}
    }
    addEdges(rows){for(const row of rows){if(this.edgeIndex.has(row.id))continue;if(!this.nodeIndex.has(row.subject_id)||!this.nodeIndex.has(row.object_id))throw new Error('关系端点不在同一图谱清单中');this.edgeIndex.add(row.id);this.edges.push(row);}}
    edgePositions(){const result=new Float32Array(this.edgeCount*4);this.edges.forEach((edge,i)=>{const a=this.nodeIndex.get(edge.subject_id)*2,b=this.nodeIndex.get(edge.object_id)*2;result[i*4]=this.positions[a];result[i*4+1]=this.positions[a+1];result[i*4+2]=this.positions[b];result[i*4+3]=this.positions[b+1];});return result;}
  }
  class Camera{
    constructor(){this.x=0;this.y=0;this.scale=1;}
    unit(w,h){return Math.min(w,h)/2*this.scale;}
    worldToScreen(x,y,w,h){const unit=this.unit(w,h);return [w/2+(x-this.x)*unit,h/2-(y-this.y)*unit];}
    screenToWorld(x,y,w,h){const unit=this.unit(w,h);return [(x-w/2)/unit+this.x,-(y-h/2)/unit+this.y];}
    pan(dx,dy,w,h){const unit=this.unit(w,h);this.x-=dx/unit;this.y+=dy/unit;}
    zoomAt(factor,x,y,w,h){const before=this.screenToWorld(x,y,w,h);this.scale=Math.max(.15,Math.min(1000,this.scale*factor));const after=this.screenToWorld(x,y,w,h);this.x+=before[0]-after[0];this.y+=before[1]-after[1];}
  }
  class Renderer{
    constructor(host,{onSelect=()=>{},onError=()=>{}}={}){
      this.host=host;this.dataset=new GraphDataset();this.camera=new Camera();this.onSelect=onSelect;this.onError=onError;this.selected=-1;this.showEdges=true;this.destroyed=false;this.pendingFrame=0;this.durations=[];
      host.innerHTML='<canvas class="fg-gl" aria-label="全量实体图谱，可拖动平移和滚轮缩放" tabindex="0"></canvas><canvas class="fg-labels" aria-hidden="true"></canvas>';
      this.canvas=host.querySelector('.fg-gl');this.labels=host.querySelector('.fg-labels');this.labelContext=this.labels.getContext('2d');
      this.gl=this.canvas.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:false,powerPreference:'high-performance'});
      if(!this.gl)throw new Error('当前浏览器无法创建 WebGL，全量模式不可用。可切回局部探索。');
      this.setupGL();this.abort=new AbortController();const signal=this.abort.signal;
      let drag=null;
      this.canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,originX:e.clientX,originY:e.clientY};this.canvas.setPointerCapture(e.pointerId);},{signal});
      this.canvas.addEventListener('pointermove',e=>{if(!drag)return;this.camera.pan(e.clientX-drag.x,e.clientY-drag.y,this.width,this.height);drag.x=e.clientX;drag.y=e.clientY;this.requestDraw();},{signal});
      this.canvas.addEventListener('pointerup',e=>{if(drag&&Math.hypot(e.clientX-drag.originX,e.clientY-drag.originY)<5){const box=this.canvas.getBoundingClientRect();this.pick(e.clientX-box.left,e.clientY-box.top);}drag=null;},{signal});
      this.canvas.addEventListener('wheel',e=>{e.preventDefault();const box=this.canvas.getBoundingClientRect();this.camera.zoomAt(Math.exp(-e.deltaY*.0015),e.clientX-box.left,e.clientY-box.top,this.width,this.height);this.requestDraw();},{signal,passive:false});
      this.canvas.addEventListener('dblclick',()=>this.reset(),{signal});
      this.canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();this.lost=true;this.onError('GPU 上下文丢失，请恢复图谱或切回局部探索。');},{signal});
      this.canvas.addEventListener('webglcontextrestored',()=>{this.lost=false;this.setupGL();this.sync();},{signal});
      this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(host);this.resize();
    }
    setupGL(){
      const gl=this.gl;
      const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error('GPU 着色器编译失败：'+gl.getShaderInfoLog(s));return s;};
      const vertex=shader(gl.VERTEX_SHADER,'attribute vec2 a_position;uniform vec2 u_center;uniform vec2 u_scale;uniform float u_size;void main(){gl_Position=vec4((a_position-u_center)*u_scale,0.,1.);gl_PointSize=u_size;}');
      const fragment=shader(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec4 u_color;uniform bool u_point;void main(){if(u_point&&distance(gl_PointCoord,vec2(.5))>.5)discard;gl_FragColor=u_color;}');
      this.program=gl.createProgram();gl.attachShader(this.program,vertex);gl.attachShader(this.program,fragment);gl.linkProgram(this.program);gl.deleteShader(vertex);gl.deleteShader(fragment);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error('GPU 绘制程序创建失败');
      gl.useProgram(this.program);this.locations={position:gl.getAttribLocation(this.program,'a_position')};for(const name of ['center','scale','size','color','point'])this.locations[name]=gl.getUniformLocation(this.program,'u_'+name);
      this.nodeBuffer=gl.createBuffer();this.edgeBuffer=gl.createBuffer();this.syncedDataset=null;gl.enableVertexAttribArray(this.locations.position);gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
      // Render every relation into one immutable world-space layer. Camera
      // motion transforms that complete layer instead of overdrawing millions
      // of long transparent segments on each frame. Entity points remain live.
      const cv=shader(gl.VERTEX_SHADER,'attribute vec2 a_position;uniform vec2 u_center;uniform vec2 u_scale;varying vec2 v_uv;void main(){v_uv=a_position*.5+.5;gl_Position=vec4((a_position*1.05-u_center)*u_scale,0.,1.);}');
      const cf=shader(gl.FRAGMENT_SHADER,'precision mediump float;uniform sampler2D u_layer;varying vec2 v_uv;void main(){gl_FragColor=texture2D(u_layer,v_uv);}');
      this.cacheProgram=gl.createProgram();gl.attachShader(this.cacheProgram,cv);gl.attachShader(this.cacheProgram,cf);gl.linkProgram(this.cacheProgram);gl.deleteShader(cv);gl.deleteShader(cf);
      if(!gl.getProgramParameter(this.cacheProgram,gl.LINK_STATUS))throw new Error('完整关系图层创建失败');
      this.cacheLocations={position:gl.getAttribLocation(this.cacheProgram,'a_position'),center:gl.getUniformLocation(this.cacheProgram,'u_center'),scale:gl.getUniformLocation(this.cacheProgram,'u_scale'),layer:gl.getUniformLocation(this.cacheProgram,'u_layer')};
      this.quadBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.quadBuffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
      this.cacheTexture=gl.createTexture();this.cacheFramebuffer=gl.createFramebuffer();this.cachedDataset=null;this.cachedRelations=0;this.cacheSize=Math.min(2048,gl.getParameter(gl.MAX_TEXTURE_SIZE));gl.bindTexture(gl.TEXTURE_2D,this.cacheTexture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,this.cacheSize,this.cacheSize,0,gl.RGBA,gl.UNSIGNED_BYTE,null);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.bindFramebuffer(gl.FRAMEBUFFER,this.cacheFramebuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,this.cacheTexture,0);if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw new Error('完整关系图层显存不足');gl.bindFramebuffer(gl.FRAMEBUFFER,null);
    }
    resize(){if(this.destroyed)return;this.width=Math.max(1,this.host.clientWidth);this.height=Math.max(1,this.host.clientHeight);const dpr=Math.min(devicePixelRatio||1,2);this.dpr=dpr;for(const canvas of [this.canvas,this.labels]){canvas.width=Math.round(this.width*dpr);canvas.height=Math.round(this.height*dpr);canvas.style.width=this.width+'px';canvas.style.height=this.height+'px';}this.requestDraw();}
    setDataset(dataset){this.dataset=dataset;this.selected=-1;this.sync();}
    sync(){
      if(this.destroyed||this.lost)return;const gl=this.gl,d=this.dataset,replace=this.syncedDataset!==d;
      if(replace||this.syncedNodes!==d.nodeCount){gl.bindBuffer(gl.ARRAY_BUFFER,this.nodeBuffer);gl.bufferData(gl.ARRAY_BUFFER,d.positions,gl.STATIC_DRAW);}
      if(replace||this.syncedEdges!==d.edgeCount){gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.bufferData(gl.ARRAY_BUFFER,d.edgePositions(),gl.STATIC_DRAW);this.buildEdgeCache();}
      this.syncedDataset=d;this.syncedNodes=d.nodeCount;this.syncedEdges=d.edgeCount;this.requestDraw();
    }
    buildEdgeCache(){
      const gl=this.gl,l=this.locations,count=this.dataset.edgeCount;
      // Sparse first batches need readable lines. Rebuild only when density
      // crosses a power-of-two bucket; otherwise append the new line segments.
      const bucket=Math.max(0,Math.floor(Math.log2(Math.max(1,count/400))));
      const opacity=Math.max(.018,.5/Math.sqrt(2**bucket));
      const rebuild=this.cachedDataset!==this.dataset||this.cacheOpacity!==opacity||this.cachedRelations>count;
      const first=rebuild?0:this.cachedRelations;
      gl.bindFramebuffer(gl.FRAMEBUFFER,this.cacheFramebuffer);gl.viewport(0,0,this.cacheSize,this.cacheSize);
      if(rebuild){gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);}
      gl.useProgram(this.program);gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform2f(l.center,0,0);gl.uniform2f(l.scale,1/1.05,1/1.05);gl.uniform1i(l.point,false);gl.uniform4f(l.color,.21,.42,.35,opacity);
      // RGB is premultiplied in the texture, while alpha must remain alpha,
      // not alpha squared. draw() composites that texture with ONE below.
      gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
      gl.drawArrays(gl.LINES,first*2,(count-first)*2);gl.bindFramebuffer(gl.FRAMEBUFFER,null);
      // Keep subpixel lines when the world-space layer is minified on screen.
      gl.bindTexture(gl.TEXTURE_2D,this.cacheTexture);gl.generateMipmap(gl.TEXTURE_2D);
      this.cachedDataset=this.dataset;this.cachedRelations=count;this.cacheOpacity=opacity;
    }
    reset(){this.camera=new Camera();this.requestDraw();}
    requestDraw(){if(!this.pendingFrame&&!this.destroyed)this.pendingFrame=requestAnimationFrame(()=>{this.pendingFrame=0;this.draw();});}
    draw(){
      if(this.destroyed||this.lost)return;const started=performance.now(),gl=this.gl,l=this.locations,min=Math.min(this.width,this.height);gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(.967,.979,.971,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.useProgram(this.program);gl.uniform2f(l.center,this.camera.x,this.camera.y);gl.uniform2f(l.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);
      if(this.showEdges&&this.dataset.edgeCount<=4000){
        // Small partial graphs get crisp screen-space lines at every zoom.
        gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform1i(l.point,false);gl.uniform4f(l.color,.21,.42,.35,this.cacheOpacity||.5);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.LINES,0,this.dataset.edgeCount*2);
      }else if(this.showEdges&&this.dataset.edgeCount){const c=this.cacheLocations;gl.useProgram(this.cacheProgram);gl.enableVertexAttribArray(c.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.quadBuffer);gl.vertexAttribPointer(c.position,2,gl.FLOAT,false,0,0);gl.uniform2f(c.center,this.camera.x,this.camera.y);gl.uniform2f(c.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.cacheTexture);gl.uniform1i(c.layer,0);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);}
      gl.useProgram(this.program);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.nodeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform1i(l.point,true);gl.uniform1f(l.size,Math.min(10,Math.max(this.dataset.nodeCount<=800?5:2,Math.sqrt(this.camera.scale)*2))*this.dpr);gl.uniform4f(l.color,.08,.39,.30,.85);gl.drawArrays(gl.POINTS,0,this.dataset.nodeCount);
      if(this.selected>=0){gl.uniform1f(l.size,12*this.dpr);gl.uniform4f(l.color,.91,.49,.12,1);gl.drawArrays(gl.POINTS,this.selected,1);}
      this.drawLabels();this.drawnNodes=this.dataset.nodeCount;this.drawnEdges=this.showEdges?this.dataset.edgeCount:0;this.durations.push(performance.now()-started);if(this.durations.length>600)this.durations.shift();
    }
    drawLabels(){
      const ctx=this.labelContext,d=this.dataset;ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.clearRect(0,0,this.width,this.height);ctx.font='12px "Microsoft YaHei",sans-serif';ctx.fillStyle='#244a3e';
      if(d.nodeCount<=800){
        const occupied=[];
        const label=(i,selected=false)=>{
          const [x,y]=this.camera.worldToScreen(d.positions[i*2],d.positions[i*2+1],this.width,this.height);
          const text=String(d.records[i].text||d.records[i].id).slice(0,45),left=x+8,top=y-19,width=ctx.measureText(text).width;
          if(!selected&&(left<0||left+width>this.width||top<0||top+16>this.height||occupied.some(box=>left<box.right+4&&left+width+4>box.left&&top<box.bottom+4&&top+20>box.top)))return;
          ctx.fillText(text,left,y-7);occupied.push({left,right:left+width,top,bottom:top+16});
        };
        // Selected names are reserved first, including at normal zoom. Other
        // labels are a bounded screen overlay, independent of graph counts.
        if(this.selected>=0)label(this.selected,true);
        for(let i=0;i<d.nodeCount&&occupied.length<50;i++)if(i!==this.selected)label(i);
        return;
      }
      const indices=[];if(this.selected>=0)indices.push(this.selected);
      if(this.camera.scale>8){const stride=Math.max(1,Math.ceil(d.nodeCount/12000));for(let i=0;i<d.nodeCount&&indices.length<70;i+=stride){const [x,y]=this.camera.worldToScreen(d.positions[i*2],d.positions[i*2+1],this.width,this.height);if(x>0&&x<this.width&&y>0&&y<this.height)indices.push(i);}}
      for(const i of indices){const [x,y]=this.camera.worldToScreen(d.positions[i*2],d.positions[i*2+1],this.width,this.height);ctx.fillText(String(d.records[i].text||d.records[i].id).slice(0,45),x+8,y-7);}
    }
    pick(x,y){const started=performance.now(),d=this.dataset,[wx,wy]=this.camera.screenToWorld(x,y,this.width,this.height),r=10/this.camera.unit(this.width,this.height);let best=r*r,index=-1;for(let i=0;i<d.nodeCount;i++){const dx=d.positions[i*2]-wx,dy=d.positions[i*2+1]-wy,dist=dx*dx+dy*dy;if(dist<best){best=dist;index=i;}}this.selected=index;let record=index>=0?d.records[index]:null;if(!record&&this.showEdges){let nearest=(r*.3)**2;for(const edge of d.edges){const a=d.nodeIndex.get(edge.subject_id)*2,b=d.nodeIndex.get(edge.object_id)*2,ax=d.positions[a],ay=d.positions[a+1],dx=d.positions[b]-ax,dy=d.positions[b+1]-ay,length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((wx-ax)*dx+(wy-ay)*dy)/length)):0,dist=(wx-ax-t*dx)**2+(wy-ay-t*dy)**2;if(dist<nearest){nearest=dist;record=edge;}}}this.lastPickMs=performance.now()-started;this.requestDraw();if(record)this.onSelect(record);return record;}
    locate(id){const i=this.dataset.nodeIndex.get(id);if(i===undefined)return false;this.selected=i;this.camera.x=this.dataset.positions[i*2];this.camera.y=this.dataset.positions[i*2+1];this.camera.scale=Math.max(8,this.camera.scale);this.requestDraw();this.onSelect(this.dataset.records[i]);return true;}
    stats(){return {loaded_entities:this.dataset.nodeCount,loaded_relations:this.dataset.edgeCount,drawn_entities:this.drawnNodes||0,drawn_relations:this.drawnEdges||0,relation_layer:'complete-world-cache',cached_relations:this.cachedRelations||0,layout:'stable-id-v1',last_pick_ms:this.lastPickMs||0,gpu:this.gl.getParameter(this.gl.RENDERER),context_lost:!!this.lost};}
    destroy(){if(this.destroyed)return;this.destroyed=true;this.abort.abort();this.observer.disconnect();cancelAnimationFrame(this.pendingFrame);if(!this.lost){this.gl.deleteBuffer(this.nodeBuffer);this.gl.deleteBuffer(this.edgeBuffer);this.gl.deleteBuffer(this.quadBuffer);this.gl.deleteTexture(this.cacheTexture);this.gl.deleteFramebuffer(this.cacheFramebuffer);this.gl.deleteProgram(this.cacheProgram);this.gl.deleteProgram(this.program);}this.host.innerHTML='';this.dataset=new GraphDataset();}
  }
  async function request(url,body,signal){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});const payload=await response.json();if(!response.ok)throw new Error(payload.detail||'全量图读取失败');return payload;}
  class SessionLoader{
    constructor(renderer,{onProgress=()=>{},batchSize=400}={}){
      this.renderer=renderer;this.onProgress=onProgress;this.epoch=0;this.state='idle';
      this.batchSize=Math.max(1,Math.min(500,Math.trunc(batchSize)||400));
    }
    progress(){
      if(this.manifest)this.onProgress({stage:this.state,phase:this.phase,manifest:this.manifest,
        entities:this.dataset.nodeCount,relations:this.dataset.edgeCount,automatic:this.automatic,pause_requested:!!this.pauseRequested});
    }
    cancel(){
      this.epoch++;this.automatic=false;this.pauseRequested=false;this.abort?.abort();this.worker?.terminate();this.worker=null;
      this.pending=null;this.automaticRun=null;this.state='cancelled';this.progress();
    }
    pause(){this.automatic=false;this.pauseRequested=!!this.pending;if(this.state==='loading'&&!this.pending)this.state='paused';this.progress();}
    async begin(project,scope){
      this.cancel();const epoch=this.epoch;this.abort=new AbortController();const signal=this.abort.signal;
      this.manifest=null;this.dataset=new GraphDataset();this.renderer.setDataset(this.dataset);
      this.base='/api/projects/'+encodeURIComponent(project)+'/graph-sessions';this.state='loading';
      try{
        const manifest=await request(this.base,scope,signal);if(epoch!==this.epoch)return null;
        this.manifest=manifest;this.phase=manifest.relation_count?'connected':'entities';this.cursor=null;
        return await this.next();
      }catch(error){if(epoch===this.epoch){this.state='error';this.progress();}throw error;}
    }
    next(){
      if(this.pending)return this.pending;
      if(this.state==='complete')return Promise.resolve(this.manifest);
      if(!this.manifest||!['loading','paused'].includes(this.state))return Promise.reject(new Error('请重新开始图谱加载。'));
      const epoch=this.epoch,signal=this.abort.signal,manifest=this.manifest,kind=this.phase,cursor=this.cursor;
      this.state='loading';this.progress();
      const pending=(async()=>{
        try{
          const page=await request(this.base+'/'+encodeURIComponent(manifest.id)+'/page',{kind,cursor,limit:this.batchSize},signal);
          if(epoch!==this.epoch)return null;
          if(page.session_id!==manifest.id||page.kind!==kind||page.watermark!==manifest.watermark||page.layout_version!==manifest.layout_version)throw new Error('图谱分页与当前会话不一致，请重新开始。');
          const rows=kind==='connected'?page.entities:page.records;
          if(!Array.isArray(rows)||!Array.isArray(page.records))throw new Error('图谱分页缺少实体或关系，请重新开始。');
          // Validate duplicate versions too; only new endpoints need layout work.
          const additions=rows.filter(row=>!this.dataset.nodeIndex.has(row.id));
          const positions=await this.layout(additions,signal);if(epoch!==this.epoch)return null;
          this.dataset.addNodes(additions,positions);this.dataset.addNodes(rows);
          if(kind==='connected')this.dataset.addEdges(page.records);
          this.cursor=page.next_cursor;
          if(!this.cursor){if(kind==='connected'){this.phase='entities';this.cursor=null;}else this.phase='done';}
          this.renderer.sync();
          if(this.phase==='done'){
            if(this.dataset.nodeCount!==manifest.entity_count||this.dataset.edgeCount!==manifest.relation_count)throw new Error('图谱清单计数不一致，请重新加载；未将此图标为完整。');
            this.state='complete';this.automatic=false;
          }else this.state='paused';
          this.pauseRequested=false;this.progress();return manifest;
        }catch(error){if(epoch===this.epoch){this.state='error';this.automatic=false;this.progress();}throw error;}
        finally{if(epoch===this.epoch&&this.pending===pending)this.pending=null;}
      })();
      this.pending=pending;return pending;
    }
    resume(){
      if(this.state==='complete')return Promise.resolve(this.manifest);
      if(this.automaticRun)return this.automaticRun;
      this.automatic=true;this.pauseRequested=false;const epoch=this.epoch;
      const run=(async()=>{
        try{while(this.automatic&&epoch===this.epoch&&this.state!=='complete'){
          await this.next();if(epoch!==this.epoch)return null;
          // Yield between bounded batches so pan, pause and detail remain live.
          if(this.automatic&&this.state!=='complete')await new Promise(resolve=>requestAnimationFrame(resolve));
        }return this.manifest;}
        finally{if(epoch===this.epoch){this.automaticRun=null;this.automatic=false;this.progress();}}
      })();
      this.automaticRun=run;return run;
    }
    async load(project,scope){const manifest=await this.begin(project,scope);if(!manifest)return null;return this.resume();}
    layout(rows,signal){
      if(signal.aborted)return Promise.reject(new DOMException('加载已取消','AbortError'));
      if(!rows.length||typeof Worker==='undefined')return Promise.resolve(null);
      const id=this.layoutSequence=(this.layoutSequence||0)+1;
      return new Promise((resolve,reject)=>{
        let worker;
        try{if(!this.worker)this.worker=new Worker('/assets/full-graph-worker.js?v=redesign-1');worker=this.worker;}catch(error){reject(error);return;}
        const cleanup=()=>{signal.removeEventListener('abort',abort);for(const [type,listener] of [['message',receive],['error',failure],['messageerror',failure]])worker.removeEventListener(type,listener);};
        const fail=error=>{cleanup();worker.terminate();if(this.worker===worker)this.worker=null;reject(error);};
        const abort=()=>fail(new DOMException('加载已取消','AbortError'));
        const failure=event=>fail(new Error(event.message||'图谱布局线程失败，请重新加载。'));
        const receive=event=>{if(event.data?.id!==id)return;const positions=event.data.positions;if(!positions||positions.length!==rows.length*2){fail(new Error('图谱布局结果不完整，请重新加载。'));return;}cleanup();resolve(positions);};
        worker.addEventListener('message',receive);worker.addEventListener('error',failure);worker.addEventListener('messageerror',failure);signal.addEventListener('abort',abort,{once:true});
        try{worker.postMessage({id,ids:rows.map(row=>row.id)});}catch(error){fail(error);}
      });
    }
    destroy(){this.cancel();this.renderer.destroy();}
  }
  root.FullGraph={GraphDataset,Camera,Renderer,SessionLoader,point};
})(typeof window!=='undefined'?window:globalThis);
