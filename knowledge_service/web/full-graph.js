/* Full entity graph: compact buffers, bounded labels and shared record details. */
(function(root){
  'use strict';
  const tokens=root.GraphAppearance?.tokens||{background:'#f8fafb',edge:'#849ba5',text:'#28444a',outline:'#ffffff',selected:'#cf8737'};
  const rgb=hex=>new Uint8Array([1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)));
  const fallbackColors=new Map();
  const nodeRGB=record=>{
    if(root.GraphAppearance)return root.GraphAppearance.rgbFor(record);
    const color=root.GraphPalette?.colorFor(record.type||'')||'#0e7c7b';
    if(!fallbackColors.has(color))fallbackColors.set(color,rgb(color));
    return fallbackColors.get(color);
  };
  const theme={};for(const name of ['background','edge','outline','selected'])theme[name]=Array.from(rgb(tokens[name]),value=>value/255);
  function hash(value){let n=2166136261;for(const c of String(value)){n^=c.charCodeAt(0);n=Math.imul(n,16777619);}return n>>>0;}
  function point(id){const h=hash(id),j=hash(id+'@y');return [((h&0xffffff)/0xffffff)*1.9-.95,((j&0xffffff)/0xffffff)*1.9-.95];}
  // A bounded display layout is only used after the manifest certifies a small
  // session. Canonical/worker positions remain untouched for paging and export.
  function relationshipPositions(dataset,width,height){
    const count=dataset.nodeCount,degrees=new Uint32Array(count),links=[];
    for(const edge of [...dataset.edges].sort((a,b)=>String(a.id).localeCompare(String(b.id)))){const a=dataset.nodeIndex.get(edge.subject_id),b=dataset.nodeIndex.get(edge.object_id);if(a!==b){degrees[a]++;degrees[b]++;links.push([a,b]);}}
    const connected=[],isolated=[];for(let i=0;i<count;i++)(degrees[i]?connected:isolated).push(i);
    connected.sort((a,b)=>degrees[b]-degrees[a]||String(dataset.records[a].id).localeCompare(String(dataset.records[b].id)));
    isolated.sort((a,b)=>String(dataset.records[a].id).localeCompare(String(dataset.records[b].id)));
    const availableWidth=Math.max(120,width-100),availableHeight=Math.max(100,height-72),isolateWidth=isolated.length&&connected.length?Math.min(availableWidth*.3,Math.max(120,Math.ceil(isolated.length/Math.max(1,Math.floor(availableHeight/42)))*106)):0;
    const graphWidth=availableWidth-isolateWidth-(isolateWidth?36:0),x=new Float32Array(count),y=new Float32Array(count),dx=new Float32Array(count),dy=new Float32Array(count);
    const ideal=Math.sqrt(Math.max(10000,graphWidth*availableHeight)/Math.max(1,connected.length))*.7;
    connected.forEach((index,order)=>{const angle=order*2.399963229728653,radius=ideal*Math.sqrt(order+1);x[index]=Math.cos(angle)*radius;y[index]=Math.sin(angle)*radius;});
    // Fixed iterations and deterministic seeds make append/reload reproducible;
    // the 200-entity manifest gate keeps pairwise work off the progressive path.
    for(let step=0;step<180&&connected.length>1;step++){
      dx.fill(0);dy.fill(0);
      for(let a=0;a<connected.length;a++)for(let b=a+1;b<connected.length;b++){
        const i=connected[a],j=connected[b],vx=x[i]-x[j],vy=y[i]-y[j],distance=Math.max(1,Math.hypot(vx,vy)),force=ideal*ideal/distance;
        const fx=vx/distance*force,fy=vy/distance*force;dx[i]+=fx;dy[i]+=fy;dx[j]-=fx;dy[j]-=fy;
      }
      for(const [a,b] of links){const vx=x[a]-x[b],vy=y[a]-y[b],distance=Math.max(1,Math.hypot(vx,vy)),force=distance*distance/ideal;const fx=vx/distance*force,fy=vy/distance*force;dx[a]-=fx;dy[a]-=fy;dx[b]+=fx;dy[b]+=fy;}
      const temperature=12*(1-step/180)+.25;
      for(const i of connected){dx[i]-=x[i]*.05;dy[i]-=y[i]*.05;const distance=Math.max(1,Math.hypot(dx[i],dy[i])),move=Math.min(distance,temperature);x[i]+=dx[i]/distance*move;y[i]+=dy[i]/distance*move;}
    }
    if(connected.length){
      let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;for(const i of connected){minX=Math.min(minX,x[i]);maxX=Math.max(maxX,x[i]);minY=Math.min(minY,y[i]);maxY=Math.max(maxY,y[i]);}
      const scale=Math.min(graphWidth/Math.max(1,maxX-minX),availableHeight/Math.max(1,maxY-minY));
      for(const i of connected){x[i]=50+graphWidth/2+(x[i]-(minX+maxX)/2)*scale;y[i]=36+availableHeight/2+(y[i]-(minY+maxY)/2)*scale;}
      // Final screen-space separation protects the readable node diameter when
      // high-degree stars compress nearby leaves during fitting.
      for(let step=0;step<32;step++)for(let a=0;a<connected.length;a++)for(let b=a+1;b<connected.length;b++){
        const i=connected[a],j=connected[b],vx=x[j]-x[i],vy=y[j]-y[i],distance=Math.max(.01,Math.hypot(vx,vy));if(distance>=30)continue;
        const move=(30-distance)/2,fx=vx/distance*move,fy=vy/distance*move;x[i]=Math.max(50,Math.min(50+graphWidth,x[i]-fx));y[i]=Math.max(36,Math.min(36+availableHeight,y[i]-fy));x[j]=Math.max(50,Math.min(50+graphWidth,x[j]+fx));y[j]=Math.max(36,Math.min(36+availableHeight,y[j]+fy));
      }
    }
    const gridLeft=connected.length?width-50-isolateWidth:50,gridWidth=connected.length?isolateWidth:availableWidth,columns=Math.max(1,Math.ceil(Math.sqrt(isolated.length*gridWidth/availableHeight))),rows=Math.ceil(isolated.length/columns);
    isolated.forEach((i,order)=>{x[i]=gridLeft+gridWidth*(order%columns+.5)/columns;y[i]=36+availableHeight*(Math.floor(order/columns)+.5)/Math.max(1,rows);});
    const positions=new Float32Array(count*2),unit=Math.min(width,height)/2;
    for(let i=0;i<count;i++){positions[i*2]=(x[i]-width/2)/unit;positions[i*2+1]=(height/2-y[i])/unit;}
    return positions;
  }
  class GraphDataset{
    constructor({indexTypes=true}={}){this.records=[];this.edges=[];this.nodeIndex=new Map();this.edgeIndex=new Set();this.positions=new Float32Array(0);this.types=new Map();this.nodeTypes=[];this.indexTypes=indexTypes;}
    get nodeCount(){return this.records.length;}
    get edgeCount(){return this.edges.length;}
    addNodes(rows,positions){
      const additions=[];
      rows.forEach((row,i)=>{if(this.nodeIndex.has(row.id)){const old=this.records[this.nodeIndex.get(row.id)];if(old.version_id!==row.version_id)throw new Error('同一会话收到不一致的实体版本');return;}this.nodeIndex.set(row.id,this.records.length);this.records.push(row);const xy=positions?[positions[i*2],positions[i*2+1]]:point(row.id);additions.push(...xy);
        if(this.indexTypes){const type=String(row.type||'');if(!this.types.has(type))this.types.set(type,{nodes:[],edges:[]});const group=this.types.get(type);group.nodes.push(this.records.length-1);this.nodeTypes.push(group);}
      });
      if(additions.length){const next=new Float32Array(this.positions.length+additions.length);next.set(this.positions);next.set(additions,this.positions.length);this.positions=next;}
    }
    addEdges(rows){for(const row of rows){if(this.edgeIndex.has(row.id))continue;const a=this.nodeIndex.get(row.subject_id),b=this.nodeIndex.get(row.object_id);if(a===undefined||b===undefined)throw new Error('关系端点不在同一图谱清单中');this.edgeIndex.add(row.id);this.edges.push(row);
      if(this.indexTypes){const group=this.nodeTypes[a];if(group===this.nodeTypes[b])group.edges.push(this.edges.length-1);}
    }}
    edgePositions(positions=this.positions){const result=new Float32Array(this.edgeCount*4);this.edges.forEach((edge,i)=>{const a=this.nodeIndex.get(edge.subject_id)*2,b=this.nodeIndex.get(edge.object_id)*2;result[i*4]=positions[a];result[i*4+1]=positions[a+1];result[i*4+2]=positions[b];result[i*4+3]=positions[b+1];});return result;}
  }
  // One active projection; append work visits only that type's new indexes.
  // The full session dataset remains authoritative for paging and counts.
  class TypeProjection{
    constructor(source,type){this.source=source;this.type=type;this.dataset=new GraphDataset({indexTypes:false});this.nodes=0;this.edges=0;}
    sync(){
      const source=this.source,group=source.types.get(this.type);if(!group)return;
      const rows=[],positions=new Float32Array((group.nodes.length-this.nodes)*2);
      for(let i=this.nodes;i<group.nodes.length;i++){const index=group.nodes[i],offset=(i-this.nodes)*2;rows.push(source.records[index]);positions[offset]=source.positions[index*2];positions[offset+1]=source.positions[index*2+1];}
      if(rows.length)this.dataset.addNodes(rows,positions);this.nodes=group.nodes.length;
      if(group.edges.length>this.edges)this.dataset.addEdges(group.edges.slice(this.edges).map(index=>source.edges[index]));this.edges=group.edges.length;
    }
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
      this.host=host;this.dataset=new GraphDataset();this.typeFilter=null;this.typeProjection=null;this.camera=new Camera();this.onSelect=onSelect;this.onError=onError;this.selected=-1;this.selectedEdge=-1;this.showEdges=true;this.showLabels=true;this.destroyed=false;this.pendingFrame=0;this.durations=[];
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
      this.invalidateViewCache();this.viewProgram=null;this.viewTexture=null;this.viewLocations=null;this.viewCacheDisabled=false;this.viewCacheError=0;
      this.strokeProgram=null;this.strokeBuffer=null;
      const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error('GPU 着色器编译失败：'+gl.getShaderInfoLog(s));return s;};
      const vertexSource='attribute vec2 a_position;uniform vec2 u_center;uniform vec2 u_scale;uniform float u_size;void main(){gl_Position=vec4((a_position-u_center)*u_scale,0.,1.);gl_PointSize=u_size;}';
      const vertex=shader(gl.VERTEX_SHADER,vertexSource);
      const fragment=shader(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec4 u_color;uniform bool u_point;void main(){if(u_point&&distance(gl_PointCoord,vec2(.5))>.5)discard;gl_FragColor=u_color;}');
      this.program=gl.createProgram();gl.attachShader(this.program,vertex);gl.attachShader(this.program,fragment);gl.linkProgram(this.program);gl.deleteShader(vertex);gl.deleteShader(fragment);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error('GPU 绘制程序创建失败');
      gl.useProgram(this.program);this.locations={position:gl.getAttribLocation(this.program,'a_position')};for(const name of ['center','scale','size','color','point'])this.locations[name]=gl.getUniformLocation(this.program,'u_'+name);
      // Outlines are limited to sparse graphs; dense points and large-session
      // relations keep the simple shader, with no per-vertex color interpolation.
      // Work in physical pixels so the transition stays one pixel wide at any
      // DPR. The sprite has a transparent margin instead of clipping the curve.
      const pv=shader(gl.VERTEX_SHADER,vertexSource),pf=shader(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec4 u_color;uniform highp float u_size;uniform float u_outline;uniform bool u_ring;uniform vec3 u_outlineColor;void main(){float radius=length((gl_PointCoord-vec2(.5))*u_size),outer=(u_size-2.)*.5;float coverage=1.-smoothstep(outer-.5,outer+.5,radius);vec3 color=u_color.rgb;if(u_ring){float inner=outer*.72;coverage*=smoothstep(inner-.5,inner+.5,radius);}else if(u_outline>0.){color=mix(color,u_outlineColor,smoothstep(outer-u_outline-.5,outer-u_outline+.5,radius));}if(coverage<=0.)discard;gl_FragColor=vec4(color,u_color.a*coverage);}');
      this.pointProgram=gl.createProgram();gl.attachShader(this.pointProgram,pv);gl.attachShader(this.pointProgram,pf);gl.linkProgram(this.pointProgram);gl.deleteShader(pv);gl.deleteShader(pf);if(!gl.getProgramParameter(this.pointProgram,gl.LINK_STATUS))throw new Error('实体轮廓绘制程序创建失败');
      this.pointLocations={position:gl.getAttribLocation(this.pointProgram,'a_position')};for(const name of ['center','scale','size','color','ring','outline','outlineColor'])this.pointLocations[name]=gl.getUniformLocation(this.pointProgram,'u_'+name);
      gl.useProgram(this.pointProgram);gl.uniform3f(this.pointLocations.outlineColor,...theme.outline);
      this.nodeBuffer=gl.createBuffer();this.groupedNodeBuffer=gl.createBuffer();this.edgeBuffer=gl.createBuffer();this.nodeRanges=[];this.syncedDataset=null;gl.enableVertexAttribArray(this.locations.position);gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
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
    resize(){if(this.destroyed)return;this.invalidateViewCache();this.width=Math.max(1,this.host.clientWidth);this.height=Math.max(1,this.host.clientHeight);const dpr=Math.min(devicePixelRatio||1,2);this.dpr=dpr;for(const canvas of [this.canvas,this.labels]){canvas.width=Math.round(this.width*dpr);canvas.height=Math.round(this.height*dpr);canvas.style.width=this.width+'px';canvas.style.height=this.height+'px';}if(this.smallSession&&this.dataset.nodeCount)this.sync();else this.requestDraw();}
    get displayDataset(){return this.typeProjection?.dataset||this.dataset;}
    setTypeFilter(type){
      const next=type==null?null:String(type);if(next===(this.typeFilter??null))return;
      this.typeFilter=next;this.typeProjection=next===null?null:new TypeProjection(this.dataset,next);
      this.selected=-1;this.selectedEdge=-1;this.layoutKey=null;this.sync();
    }
    setDataset(dataset){this.dataset=dataset;this.typeFilter=null;this.typeProjection=null;this.selected=-1;this.selectedEdge=-1;this.sessionManifest=null;this.smallSession=false;this.renderPositions=null;this.layoutKey=null;this.sync();}
    setSessionManifest(manifest){
      const small=Number.isInteger(manifest?.entity_count)&&manifest.entity_count<=200&&manifest.entity_count>=0&&Number.isInteger(manifest?.relation_count)&&manifest.relation_count<=400&&manifest.relation_count>=0;
      if(this.smallSession!==small||this.sessionManifest?.id!==manifest?.id){this.layoutKey=null;if(small)this.camera=new Camera();}
      this.sessionManifest=manifest;this.smallSession=small;
    }
    sync(){
      if(this.destroyed||this.lost)return;this.typeProjection?.sync();const gl=this.gl,d=this.displayDataset,replace=this.syncedDataset!==d;
      if(this.smallSession&&(d.nodeCount>200||d.edgeCount>400))this.smallSession=false;
      const key=this.smallSession?[d.nodeCount,d.edgeCount,this.width,this.height].join(':'):null,layoutChanged=replace||key!==this.layoutKey;
      if(layoutChanged)this.renderPositions=key?relationshipPositions(d,this.width,this.height):d.positions;
      if(!key)this.renderPositions=d.positions;this.layoutKey=key;const positions=this.renderPositions;
      if(replace||layoutChanged||this.syncedNodes!==d.nodeCount||this.syncedEdges!==d.edgeCount)this.invalidateViewCache();
      if(replace||layoutChanged||this.syncedNodes!==d.nodeCount){
        gl.bindBuffer(gl.ARRAY_BUFFER,this.nodeBuffer);gl.bufferData(gl.ARRAY_BUFFER,positions,gl.STATIC_DRAW);
        const regroup=replace||layoutChanged||this.syncedNodes>d.nodeCount,first=regroup?0:this.syncedNodes;
        if(regroup){this.nodeGroups=new Map();this.nodeBounds={minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity};}
        // Only new entities are classified in JS. Existing groups are copied
        // with native typed-array operations, never regrouped record by record.
        for(let i=first;i<d.nodeCount;i++){
          const color=nodeRGB(d.records[i]),key=(color[0]<<16)|(color[1]<<8)|color[2];let group=this.nodeGroups.get(key);
          if(!group){group={positions:new Float32Array(32),count:0,color:Array.from(color,value=>value/255)};this.nodeGroups.set(key,group);}
          if(group.count*2===group.positions.length){const grown=new Float32Array(group.positions.length*2);grown.set(group.positions);group.positions=grown;}
          group.positions[group.count*2]=positions[i*2];group.positions[group.count*2+1]=positions[i*2+1];group.count++;
          const bounds=this.nodeBounds,x=positions[i*2],y=positions[i*2+1];bounds.minX=Math.min(bounds.minX,x);bounds.maxX=Math.max(bounds.maxX,x);bounds.minY=Math.min(bounds.minY,y);bounds.maxY=Math.max(bounds.maxY,y);
        }
        const groupedPositions=new Float32Array(d.nodeCount*2);this.nodeRanges=[];let offset=0;
        for(const group of this.nodeGroups.values()){groupedPositions.set(group.positions.subarray(0,group.count*2),offset*2);this.nodeRanges.push({first:offset,count:group.count,color:group.color});offset+=group.count;}
        gl.bindBuffer(gl.ARRAY_BUFFER,this.groupedNodeBuffer);gl.bufferData(gl.ARRAY_BUFFER,groupedPositions,gl.STATIC_DRAW);
      }
      if(replace||layoutChanged||this.syncedEdges!==d.edgeCount){gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.bufferData(gl.ARRAY_BUFFER,d.edgePositions(positions),gl.STATIC_DRAW);if(this.smallSession)this.syncStrokes(positions);if(layoutChanged)this.cachedDataset=null;this.buildEdgeCache();}
      this.syncedDataset=d;this.syncedNodes=d.nodeCount;this.syncedEdges=d.edgeCount;this.requestDraw();
    }
    syncStrokes(positions){
      // The certified small-session gate bounds this at 400 quads. Large and
      // unknown sessions retain their existing compact line/cache path.
      const gl=this.gl;
      if(!this.strokeProgram){
        const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw new Error('关系轮廓着色器编译失败');return shader;};
        const vertex=compile(gl.VERTEX_SHADER,'attribute vec2 a_position;attribute vec2 a_target;attribute vec2 a_corner;uniform vec2 u_center;uniform vec2 u_scale;uniform vec2 u_viewport;uniform float u_width;varying vec2 v_segment;varying float v_length;void main(){vec2 start=(a_position-u_center)*u_scale,end=(a_target-u_center)*u_scale,delta=(end-start)*u_viewport*.5;float span=max(length(delta),.001),padding=u_width*.5+1.;vec2 tangent=delta/span,normal=vec2(-tangent.y,tangent.x);v_segment=vec2(a_corner.x*(span+2.*padding)-padding,a_corner.y*padding);v_length=span;gl_Position=vec4(start+(tangent*v_segment.x+normal*v_segment.y)*2./u_viewport,0.,1.);}');
        const fragment=compile(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec4 u_color;uniform highp float u_width;varying vec2 v_segment;varying float v_length;void main(){float endDistance=max(max(-v_segment.x,v_segment.x-v_length),0.),distance=length(vec2(endDistance,v_segment.y));float coverage=1.-smoothstep(u_width*.5-.5,u_width*.5+.5,distance);if(coverage<=0.)discard;gl_FragColor=vec4(u_color.rgb,u_color.a*coverage);}');
        this.strokeProgram=gl.createProgram();gl.attachShader(this.strokeProgram,vertex);gl.attachShader(this.strokeProgram,fragment);gl.linkProgram(this.strokeProgram);gl.deleteShader(vertex);gl.deleteShader(fragment);if(!gl.getProgramParameter(this.strokeProgram,gl.LINK_STATUS))throw new Error('关系轮廓绘制程序创建失败');
        const p=this.strokeLocations={};for(const name of ['position','target','corner'])p[name]=gl.getAttribLocation(this.strokeProgram,'a_'+name);for(const name of ['center','scale','viewport','width','color'])p[name]=gl.getUniformLocation(this.strokeProgram,'u_'+name);
        this.strokeBuffer=gl.createBuffer();
      }
      const vertices=new Float32Array(this.displayDataset.edgeCount*36),corners=[[0,-1],[1,-1],[0,1],[0,1],[1,-1],[1,1]];
      this.displayDataset.edges.forEach((edge,i)=>{const a=this.displayDataset.nodeIndex.get(edge.subject_id)*2,b=this.displayDataset.nodeIndex.get(edge.object_id)*2;for(let j=0;j<6;j++)vertices.set([positions[a],positions[a+1],positions[b],positions[b+1],...corners[j]],i*36+j*6);});
      gl.bindBuffer(gl.ARRAY_BUFFER,this.strokeBuffer);gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.STATIC_DRAW);
    }
    drawStrokes(first,count,selected=false){
      const gl=this.gl,p=this.strokeLocations,min=Math.min(this.width,this.height);gl.useProgram(this.strokeProgram);gl.disableVertexAttribArray(this.locations.position);gl.disableVertexAttribArray(this.pointLocations.position);gl.disableVertexAttribArray(this.cacheLocations.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.strokeBuffer);
      for(const [offset,name] of ['position','target','corner'].entries()){gl.enableVertexAttribArray(p[name]);gl.vertexAttribPointer(p[name],2,gl.FLOAT,false,24,offset*8);}
      gl.uniform2f(p.center,this.camera.x,this.camera.y);gl.uniform2f(p.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);gl.uniform2f(p.viewport,this.canvas.width,this.canvas.height);gl.uniform1f(p.width,(selected?2:1)*this.dpr);gl.uniform4f(p.color,...(selected?theme.selected:theme.edge),selected?1:this.cacheOpacity||.5);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.TRIANGLES,first*6,count*6);
      for(const name of ['position','target','corner'])gl.disableVertexAttribArray(p[name]);
    }
    invalidateViewCache(){this.viewSnapshot=null;this.viewStableFrames=0;this.usedViewCache=false;}
    sameViewData(snapshot){
      return snapshot.dataset===this.displayDataset&&snapshot.nodes===this.displayDataset.nodeCount&&snapshot.edges===this.displayDataset.edgeCount&&snapshot.node===(this.selected??-1)&&snapshot.edge===(this.selectedEdge??-1)&&snapshot.showEdges===this.showEdges&&snapshot.width===this.width&&snapshot.height===this.height&&snapshot.pixelWidth===this.canvas.width&&snapshot.pixelHeight===this.canvas.height&&snapshot.dpr===this.dpr;
    }
    canCaptureView(){
      const b=this.nodeBounds,w=this.canvas.width,h=this.canvas.height;
      if(this.displayDataset.nodeCount<5000||!b||this.viewCacheDisabled||typeof this.gl.copyTexImage2D!=='function'||w<1||h<1||w>Math.min(2048,this.cacheSize)||h>Math.min(2048,this.cacheSize)||w*h*4>16*1024*1024)return false;
      const unit=this.camera.unit(this.width,this.height),size=Math.min(10,Math.max(2,Math.sqrt(this.camera.scale)*2)),margin=size/2+2;
      // Relations lie between endpoints. Bounds plus sprite/filter margins
      // certify complete geometry; a selected ring gets its own O(1) check.
      const complete=this.width/2+(b.minX-this.camera.x)*unit>=margin&&this.width/2+(b.maxX-this.camera.x)*unit<=this.width-margin&&this.height/2-(b.maxY-this.camera.y)*unit>=margin&&this.height/2-(b.minY-this.camera.y)*unit<=this.height-margin;
      if(!complete)return false;
      if(this.selected>=0){const x=this.width/2+(this.displayDataset.positions[this.selected*2]-this.camera.x)*unit,y=this.height/2-(this.displayDataset.positions[this.selected*2+1]-this.camera.y)*unit,ring=(size+8)/2+2;return x>=ring&&x<=this.width-ring&&y>=ring&&y<=this.height-ring;}
      return true;
    }
    setupViewCache(){
      if(this.viewProgram)return true;const gl=this.gl;let vertex,fragment,program;
      try{
        const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){gl.deleteShader(shader);throw new Error('overview shader');}return shader;};
        vertex=compile(gl.VERTEX_SHADER,'attribute vec2 a_position;uniform vec2 u_scale;uniform vec2 u_offset;varying vec2 v_uv;void main(){v_uv=a_position*.5+.5;gl_Position=vec4(a_position*u_scale+u_offset,0.,1.);}');
        fragment=compile(gl.FRAGMENT_SHADER,'precision mediump float;uniform sampler2D u_layer;varying vec2 v_uv;void main(){gl_FragColor=texture2D(u_layer,v_uv);}');
        program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error('overview program');
        this.viewLocations={position:gl.getAttribLocation(program,'a_position'),scale:gl.getUniformLocation(program,'u_scale'),offset:gl.getUniformLocation(program,'u_offset'),layer:gl.getUniformLocation(program,'u_layer')};
        this.viewTexture=gl.createTexture();gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.viewTexture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);this.viewProgram=program;return true;
      }catch(error){if(program)gl.deleteProgram(program);if(this.viewTexture)gl.deleteTexture(this.viewTexture);this.viewTexture=null;this.viewCacheDisabled=true;return false;}
      finally{if(vertex)gl.deleteShader(vertex);if(fragment)gl.deleteShader(fragment);}
    }
    captureView(){
      if(!this.canCaptureView()||!this.setupViewCache())return;
      const gl=this.gl;gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.viewTexture);
      // The renderer's alpha:false default framebuffer has no alpha channel.
      try{gl.copyTexImage2D(gl.TEXTURE_2D,0,gl.RGB,0,0,this.canvas.width,this.canvas.height,0);const error=gl.getError?.();if(error){this.viewCacheError=error;this.viewCacheDisabled=true;this.invalidateViewCache();return;}}
      catch(error){this.viewCacheDisabled=true;this.invalidateViewCache();return;}
      this.viewSnapshot={dataset:this.displayDataset,nodes:this.displayDataset.nodeCount,edges:this.displayDataset.edgeCount,node:this.selected??-1,edge:this.selectedEdge??-1,showEdges:this.showEdges,width:this.width,height:this.height,pixelWidth:this.canvas.width,pixelHeight:this.canvas.height,dpr:this.dpr,x:this.camera.x,y:this.camera.y,scale:this.camera.scale};
    }
    drawViewCache(){
      const snapshot=this.viewSnapshot;if(!snapshot)return false;
      if(!this.sameViewData(snapshot)){this.invalidateViewCache();return false;}
      const ratio=this.camera.scale/snapshot.scale,min=Math.min(this.width,this.height),halfX=this.width/(min*snapshot.scale),halfY=this.height/(min*snapshot.scale);
      if(ratio<2/3||ratio>1.5||Math.abs(this.camera.x-snapshot.x)>halfX*.35||Math.abs(this.camera.y-snapshot.y)>halfY*.35)return false;
      const gl=this.gl,l=this.viewLocations;gl.useProgram(this.viewProgram);gl.disableVertexAttribArray(this.locations.position);gl.disableVertexAttribArray(this.pointLocations.position);gl.disableVertexAttribArray(this.cacheLocations.position);gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.quadBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform2f(l.scale,ratio,ratio);gl.uniform2f(l.offset,(snapshot.x-this.camera.x)*min/this.width*this.camera.scale,(snapshot.y-this.camera.y)*min/this.height*this.camera.scale);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.viewTexture);gl.uniform1i(l.layer,0);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);this.usedViewCache=true;return true;
    }
    buildEdgeCache(){
      const gl=this.gl,l=this.locations,count=this.displayDataset.edgeCount;
      // Sparse first batches need readable lines. Rebuild only when density
      // crosses a power-of-two bucket; otherwise append the new line segments.
      const bucket=Math.max(0,Math.floor(Math.log2(Math.max(1,count/400))));
      const opacity=Math.max(.018,.5/Math.sqrt(2**bucket));
      const rebuild=this.cachedDataset!==this.displayDataset||this.cacheOpacity!==opacity||this.cachedRelations>count;
      const first=rebuild?0:this.cachedRelations;
      gl.bindFramebuffer(gl.FRAMEBUFFER,this.cacheFramebuffer);gl.viewport(0,0,this.cacheSize,this.cacheSize);
      if(rebuild){gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);}
      gl.useProgram(this.program);if(this.viewLocations)gl.disableVertexAttribArray(this.viewLocations.position);gl.disableVertexAttribArray(this.cacheLocations.position);gl.disableVertexAttribArray(this.pointLocations.position);gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform2f(l.center,0,0);gl.uniform2f(l.scale,1/1.05,1/1.05);gl.uniform1i(l.point,false);gl.uniform4f(l.color,...theme.edge,opacity);
      // RGB is premultiplied in the texture, while alpha must remain alpha,
      // not alpha squared. draw() composites that texture with ONE below.
      gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
      gl.drawArrays(gl.LINES,first*2,(count-first)*2);gl.bindFramebuffer(gl.FRAMEBUFFER,null);
      // Keep subpixel lines when the world-space layer is minified on screen.
      gl.bindTexture(gl.TEXTURE_2D,this.cacheTexture);gl.generateMipmap(gl.TEXTURE_2D);
      this.cachedDataset=this.displayDataset;this.cachedRelations=count;this.cacheOpacity=opacity;
    }
    reset(){this.camera=new Camera();this.requestDraw();}
    zoom(factor){if(!Number.isFinite(factor)||factor<=0)return;this.camera.zoomAt(factor,this.width/2,this.height/2,this.width,this.height);this.requestDraw();}
    requestDraw(){if(!this.pendingFrame&&!this.destroyed)this.pendingFrame=requestAnimationFrame(()=>{this.pendingFrame=0;this.draw();});}
    draw(){
      if(this.destroyed||this.lost)return;const started=performance.now(),gl=this.gl,l=this.locations,min=Math.min(this.width,this.height);gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(...theme.background,1);gl.clear(gl.COLOR_BUFFER_BIT);this.usedViewCache=false;
      if(this.drawViewCache()){this.finishFrame(started);return;}
      if(this.viewLocations)gl.disableVertexAttribArray(this.viewLocations.position);gl.useProgram(this.program);gl.disableVertexAttribArray(this.pointLocations.position);gl.uniform2f(l.center,this.camera.x,this.camera.y);gl.uniform2f(l.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);
      if(this.showEdges&&this.smallSession&&this.strokeBuffer){this.drawStrokes(0,this.displayDataset.edgeCount);
      }else if(this.showEdges&&this.displayDataset.edgeCount<=4000){
        // Small partial graphs get crisp screen-space lines at every zoom.
        gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform1i(l.point,false);gl.uniform4f(l.color,...theme.edge,this.cacheOpacity||.5);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.LINES,0,this.displayDataset.edgeCount*2);
      }else if(this.showEdges&&this.displayDataset.edgeCount){const c=this.cacheLocations;gl.useProgram(this.cacheProgram);gl.disableVertexAttribArray(l.position);gl.enableVertexAttribArray(c.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.quadBuffer);gl.vertexAttribPointer(c.position,2,gl.FLOAT,false,0,0);gl.uniform2f(c.center,this.camera.x,this.camera.y);gl.uniform2f(c.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.cacheTexture);gl.uniform1i(c.layer,0);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);}
      if(this.showEdges&&this.selectedEdge>=0&&this.selectedEdge<this.displayDataset.edgeCount){
        if(this.smallSession&&this.strokeBuffer)this.drawStrokes(this.selectedEdge,1,true);else{
        gl.useProgram(this.program);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.disableVertexAttribArray(this.cacheLocations.position);gl.enableVertexAttribArray(l.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.edgeBuffer);gl.vertexAttribPointer(l.position,2,gl.FLOAT,false,0,0);gl.uniform1i(l.point,false);gl.uniform4f(l.color,...theme.selected,1);gl.drawArrays(gl.LINES,this.selectedEdge*2,2);
        }
      }
      const size=this.smallSession?Math.max(12,Math.min(28,16*Math.sqrt(this.camera.scale))):Math.min(10,Math.max(this.displayDataset.nodeCount<=800?5:2,Math.sqrt(this.camera.scale)*2));
      const sparse=this.displayDataset.nodeCount<=800,p=sparse?this.pointLocations:l;
      gl.useProgram(sparse?this.pointProgram:this.program);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.disableVertexAttribArray(this.cacheLocations.position);gl.disableVertexAttribArray(l.position);gl.enableVertexAttribArray(p.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.groupedNodeBuffer);gl.vertexAttribPointer(p.position,2,gl.FLOAT,false,0,0);gl.uniform2f(p.center,this.camera.x,this.camera.y);gl.uniform2f(p.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);gl.uniform1f(p.size,size*this.dpr+(sparse?2:0));
      if(sparse){gl.uniform1i(p.ring,false);gl.uniform1f(p.outline,size>=5?this.dpr:0);}else gl.uniform1i(l.point,true);
      for(const range of this.nodeRanges){gl.uniform4f(p.color,...range.color,1);gl.drawArrays(gl.POINTS,range.first,range.count);}
      if(this.selected>=0&&this.selected<this.displayDataset.nodeCount){const ring=this.pointLocations;gl.useProgram(this.pointProgram);gl.disableVertexAttribArray(p.position);gl.enableVertexAttribArray(ring.position);gl.bindBuffer(gl.ARRAY_BUFFER,this.nodeBuffer);gl.vertexAttribPointer(ring.position,2,gl.FLOAT,false,0,0);gl.uniform2f(ring.center,this.camera.x,this.camera.y);gl.uniform2f(ring.scale,min/this.width*this.camera.scale,min/this.height*this.camera.scale);gl.uniform1i(ring.ring,true);gl.uniform1f(ring.outline,0);gl.uniform1f(ring.size,(size+8)*this.dpr+2);gl.uniform4f(ring.color,...theme.selected,1);gl.drawArrays(gl.POINTS,this.selected,1);}
      if(this.viewStableFrames++>0)this.captureView();this.finishFrame(started);
    }
    finishFrame(started){this.drawLabels();this.drawnNodes=this.displayDataset.nodeCount;this.drawnEdges=this.showEdges?this.displayDataset.edgeCount:0;this.durations.push(performance.now()-started);if(this.durations.length>600)this.durations.shift();}
    drawLabels(){
      const ctx=this.labelContext,d=this.displayDataset,positions=this.renderPositions||d.positions;ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.clearRect(0,0,this.width,this.height);
      if(this.showLabels===false||this.width<16||this.height<24)return;
      ctx.font='12px "Microsoft YaHei",sans-serif';ctx.fillStyle=tokens.text;
      if(this.smallSession){this.drawSmallLabels(ctx,positions);return;}
      const occupied=[],small=d.nodeCount<=800,budget=small?50:70;
      const label=(i,selected=false)=>{
        const [x,y]=this.camera.worldToScreen(positions[i*2],positions[i*2+1],this.width,this.height);
        if(!selected&&(x<0||x>this.width||y<0||y>this.height))return;
        let text=String(d.records[i].text||d.records[i].id).slice(0,45),width=ctx.measureText(text).width;
        if(width>this.width-8){while(text.length&&ctx.measureText(text+'…').width>this.width-8)text=text.slice(0,-1);text+='…';width=ctx.measureText(text).width;}
        const left=selected?Math.max(4,Math.min(x+8,this.width-width-4)):x+8,top=selected?Math.max(4,Math.min(y-19,this.height-20)):y-19;
        if(!selected&&(left<4||left+width>this.width-4||top<4||top+16>this.height-4||occupied.some(box=>left<box.right+4&&left+width+4>box.left&&top<box.bottom+4&&top+20>box.top)))return;
        ctx.fillText(text,left,top+12);occupied.push({left,right:left+width,top,bottom:top+16});
      };
      // Reserve the selected name first. Sampling and accepted labels stay
      // bounded even when close zoom leaves most candidates off screen.
      if(this.selected>=0&&this.selected<d.nodeCount)label(this.selected,true);
      if(small||this.camera.scale>8){const stride=small?1:Math.max(1,Math.ceil(d.nodeCount/12000));for(let i=0;i<d.nodeCount&&occupied.length<budget;i+=stride)if(i!==this.selected)label(i);}
    }
    drawSmallLabels(ctx,positions){
      const d=this.displayDataset,occupied=[],radius=Math.max(12,Math.min(28,16*Math.sqrt(this.camera.scale)))/2,centers=d.records.map((_,i)=>this.camera.worldToScreen(positions[i*2],positions[i*2+1],this.width,this.height)),order=d.records.map((_,i)=>i);
      if(this.selected>=0){order.splice(this.selected,1);order.unshift(this.selected);}
      const overlaps=(a,b,gap=3)=>a.left<b.right+gap&&a.right+gap>b.left&&a.top<b.bottom+gap&&a.bottom+gap>b.top;
      for(const i of order){
        if(occupied.length>=80)break;const [x,y]=centers[i],selected=i===this.selected;if(!selected&&(x<0||x>this.width||y<0||y>this.height))continue;
        let text=String(d.records[i].text||d.records[i].id).slice(0,45),maxWidth=Math.min(selected?340:142,this.width-12);
        if(ctx.measureText(text).width>maxWidth){while(text.length&&ctx.measureText(text+'…').width>maxWidth)text=text.slice(0,-1);text+='…';}
        const width=ctx.measureText(text).width,candidates=[[x+radius+5,y-8],[x-width/2,y+radius+5],[x-width/2,y-radius-21],[x-radius-width-5,y-8]];
        for(let [left,top] of candidates){
          if(selected){left=Math.max(4,Math.min(left,this.width-width-4));top=Math.max(4,Math.min(top,this.height-20));}
          const box={left,right:left+width,top,bottom:top+16};
          if(!selected&&(left<4||box.right>this.width-4||top<4||box.bottom>this.height-4||occupied.some(other=>overlaps(box,other))||centers.some(([nx,ny],j)=>j!==i&&overlaps(box,{left:nx-radius,right:nx+radius,top:ny-radius,bottom:ny+radius},1))))continue;
          ctx.fillStyle=tokens.background;ctx.fillRect?.(left-2,top-1,width+4,17);ctx.fillStyle=tokens.text;ctx.fillText(text,left,top+12);occupied.push(box);break;
        }
      }
    }
    pick(x,y){const started=performance.now(),d=this.displayDataset,positions=this.renderPositions||d.positions,[wx,wy]=this.camera.screenToWorld(x,y,this.width,this.height),r=(this.smallSession?Math.max(12,Math.min(28,16*Math.sqrt(this.camera.scale)))/2+4:10)/this.camera.unit(this.width,this.height);let best=r*r,index=-1;for(let i=0;i<d.nodeCount;i++){const dx=positions[i*2]-wx,dy=positions[i*2+1]-wy,dist=dx*dx+dy*dy;if(dist<best){best=dist;index=i;}}this.selected=index;this.selectedEdge=-1;let record=index>=0?d.records[index]:null;if(!record&&this.showEdges){let nearest=(r*.3)**2;for(let i=0;i<d.edgeCount;i++){const edge=d.edges[i],a=d.nodeIndex.get(edge.subject_id)*2,b=d.nodeIndex.get(edge.object_id)*2,ax=positions[a],ay=positions[a+1],dx=positions[b]-ax,dy=positions[b+1]-ay,length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((wx-ax)*dx+(wy-ay)*dy)/length)):0,dist=(wx-ax-t*dx)**2+(wy-ay-t*dy)**2;if(dist<nearest){nearest=dist;record=edge;this.selectedEdge=i;}}}this.lastPickMs=performance.now()-started;this.requestDraw();if(record)this.onSelect(record);return record;}
    locate(id){const i=this.displayDataset.nodeIndex.get(id);if(i===undefined)return false;const positions=this.renderPositions||this.displayDataset.positions;this.selected=i;this.selectedEdge=-1;this.camera.x=positions[i*2];this.camera.y=positions[i*2+1];this.camera.scale=Math.max(8,this.camera.scale);this.requestDraw();this.onSelect(this.displayDataset.records[i]);return true;}
    stats(){return {loaded_entities:this.dataset.nodeCount,loaded_relations:this.dataset.edgeCount,drawn_entities:this.drawnNodes||0,drawn_relations:this.drawnEdges||0,relation_layer:'complete-world-cache',cached_relations:this.cachedRelations||0,node_layer:this.usedViewCache?'complete-view-cache':'live-color-groups',cached_entities:this.viewSnapshot?.nodes||0,view_cache_error:this.viewCacheError||0,layout:this.smallSession?'small-relations-v1':'stable-id-v1',canonical_layout:'stable-id-v1',last_pick_ms:this.lastPickMs||0,gpu:this.gl.getParameter(this.gl.RENDERER),context_lost:!!this.lost};}
    destroy(){if(this.destroyed)return;this.destroyed=true;this.abort.abort();this.observer.disconnect();cancelAnimationFrame(this.pendingFrame);if(!this.lost){this.gl.deleteBuffer(this.nodeBuffer);this.gl.deleteBuffer(this.groupedNodeBuffer);this.gl.deleteBuffer(this.edgeBuffer);if(this.strokeBuffer)this.gl.deleteBuffer(this.strokeBuffer);if(this.strokeProgram)this.gl.deleteProgram(this.strokeProgram);this.gl.deleteBuffer(this.quadBuffer);this.gl.deleteTexture(this.cacheTexture);if(this.viewTexture)this.gl.deleteTexture(this.viewTexture);this.gl.deleteFramebuffer(this.cacheFramebuffer);this.gl.deleteProgram(this.cacheProgram);if(this.viewProgram)this.gl.deleteProgram(this.viewProgram);this.gl.deleteProgram(this.pointProgram);this.gl.deleteProgram(this.program);}this.host.innerHTML='';this.dataset=new GraphDataset();this.typeProjection=null;this.typeFilter=null;this.nodeGroups=null;this.nodeRanges=[];this.invalidateViewCache();}
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
  root.FullGraph={GraphDataset,TypeProjection,Camera,Renderer,SessionLoader,point};
})(typeof window!=='undefined'?window:globalThis);
