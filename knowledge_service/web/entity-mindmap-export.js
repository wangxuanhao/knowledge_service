/* Canonical standalone exports use every loaded occurrence, independent of the camera. */
(function(root,factory){
  const model=typeof module==='object'&&module.exports?require('./entity-mindmap-model.js'):root.EntityMindmapModel;
  const api=factory(root,model);if(typeof module==='object'&&module.exports)module.exports=api;else root.EntityMindmapExport=api;
})(typeof window==='object'?window:globalThis,(root,model)=>{
  const CARD_WIDTH=280,COLUMN_GAP=100,ROW_GAP=28,MARGIN=36,HEADER=100,TEXT_WIDTH=CARD_WIDTH-36;
  const FONT_FAMILY='Microsoft YaHei, PingFang SC, Arial, sans-serif';
  const MAX_DIMENSION=8192,MAX_PIXELS=16000000;
  const xml=value=>Array.from(String(value??'')).filter(char=>{
    const code=char.codePointAt(0);
    return code===9||code===10||code===13||(code>=32&&code<=0xd7ff)||(code>=0xe000&&code<=0xfffd)||code>=0x10000;
  }).join('').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char]));
  function createTextMeasurer(){
    let context;
    try{context=root.document?.createElement('canvas').getContext('2d');}catch{}
    return (value,size,weight)=>{
      if(typeof context?.measureText==='function'){
        context.font=`${weight?600:400} ${size}px ${FONT_FAMILY}`;
        const metrics=context.measureText(value);
        // Advance width alone misses glyphs that extend beyond their advance box.
        const width=Math.max(metrics.width,(metrics.actualBoundingBoxLeft||0)+(metrics.actualBoundingBoxRight||0),metrics.actualBoundingBoxRight||0);
        if(Number.isFinite(width))return width;
      }
      // Node and restricted canvas hosts retain a deterministic, generous glyph bound.
      return Array.from(value).length*size*1.1;
    };
  }
  function wrap(value,widthOf){
    const lines=[];let line='';
    for(const char of String(value??'')){
      if(char==='\n'){lines.push(line);line='';continue;}
      if(line&&widthOf(line+char)>TEXT_WIDTH){lines.push(line);line='';}
      line+=char;
    }
    lines.push(line);return lines;
  }
  function safeColor(value){return /^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(String(value))?String(value):'#548e88';}

  function buildSvg(tree,{labelOf=value=>value,colorFor=()=> '#548e88',truncated=false}={}){
    if(!tree)throw new Error('暂无可导出的脑图');
    const canonical=model.canonicalTree(tree),nodes=[],byId=new Map(),measureText=createTextMeasurer();
    const pending=[{node:canonical,depth:0,parent:null}];
    while(pending.length){
      const {node,depth,parent}=pending.pop();
      const relation=parent!==null?(node.predicate?(labelOf(node.predicate)||node.predicate):'关联实体'):'根实体';
      const record={node,depth,parent,
        relation:wrap(relation,value=>measureText(value,11,true)),
        name:wrap(node.name||'未命名实体',value=>measureText(value,15,true)),
        type:wrap(labelOf(node.type)||node.type||'实体',value=>measureText(value,11,false))};
      record.height=46+record.relation.length*16+record.name.length*22+record.type.length*16;
      record.color=parent!==null?safeColor(colorFor(node.type)):'#15534f';
      nodes.push(record);byId.set(node.id,record);
      for(let i=node.children.length-1;i>=0;i--)pending.push({node:node.children[i],depth:depth+1,parent:node.id});
    }
    // Subtree bands reserve each card's full wrapped height, including long predicates.
    for(let i=nodes.length-1;i>=0;i--){
      const record=nodes[i],children=record.node.children.map(child=>byId.get(child.id));
      const childrenHeight=children.reduce((sum,child)=>sum+child.span,0)+Math.max(0,children.length-1)*ROW_GAP;
      record.span=Math.max(record.height,childrenHeight);
    }
    let maxDepth=0;
    byId.get(canonical.id).top=HEADER;
    for(const record of nodes){
      maxDepth=Math.max(maxDepth,record.depth);
      record.x=MARGIN+record.depth*(CARD_WIDTH+COLUMN_GAP);
      record.y=record.top+(record.span-record.height)/2;
      const children=record.node.children.map(child=>byId.get(child.id));
      const total=children.reduce((sum,child)=>sum+child.span,0)+Math.max(0,children.length-1)*ROW_GAP;
      let top=record.top+(record.span-total)/2;
      for(const child of children){child.top=top;top+=child.span+ROW_GAP;}
    }
    const width=MARGIN*2+CARD_WIDTH+maxDepth*(CARD_WIDTH+COLUMN_GAP);
    const height=HEADER+byId.get(canonical.id).span+MARGIN,count=nodes.length;
    const description=`已加载 ${count} 个节点${truncated?' · 部分结果':''}`;
    const parts=[`<?xml version="1.0" encoding="UTF-8"?>`,
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="diagram-title diagram-description">`,
      '<title id="diagram-title">实体关系脑图</title>',`<desc id="diagram-description">${xml(description)}；脑图路径不表示关系方向</desc>`,
      `<rect width="${width}" height="${height}" fill="#ffffff"/>`,
      `<g font-family="${FONT_FAMILY}">`,
      `<text x="${MARGIN}" y="38" font-size="22" font-weight="700" fill="#173f3d">实体关系脑图</text>`,
      `<text x="${MARGIN}" y="66" font-size="13" fill="#647e7b">${xml(description)}</text>`];
    for(const record of nodes){
      if(record.parent===null)continue;
      const parent=byId.get(record.parent),x1=parent.x+CARD_WIDTH,y1=parent.y+parent.height/2,x2=record.x,y2=record.y+record.height/2;
      const bend=(x1+x2)/2;
      parts.push(`<path data-edge-from="${xml(parent.node.id)}" data-edge-to="${xml(record.node.id)}" d="M${x1} ${y1} C${bend} ${y1} ${bend} ${y2} ${x2} ${y2}" fill="none" stroke="#b6cfcc" stroke-width="1.6"/>`);
    }
    function textLines(lines,x,y,size,lineHeight,color,weight){
      return `<text x="${x}" y="${y}" font-size="${size}" fill="${color}"${weight?' font-weight="600"':''} xml:space="preserve">`+
        lines.map((line,i)=>`<tspan x="${x}" dy="${i?lineHeight:0}">${xml(line)}</tspan>`).join('')+'</text>';
    }
    for(const record of nodes){
      const isRoot=record.parent===null,x=record.x+18;let y=record.y+27;
      parts.push(`<g data-node-id="${xml(record.node.id)}">`,
        `<rect x="${record.x}" y="${record.y}" width="${CARD_WIDTH}" height="${record.height}" rx="12" fill="${isRoot?'#15534f':'#ffffff'}" stroke="${record.color}" stroke-width="1.2"/>`,
        textLines(record.relation,x,y,11,16,isRoot?'#b6dad5':record.color,true));
      y+=record.relation.length*16+8;
      parts.push(textLines(record.name,x,y,15,22,isRoot?'#ffffff':'#173f3d',true));
      y+=record.name.length*22+6;
      parts.push(textLines(record.type,x,y,11,16,isRoot?'#b6dad5':'#647e7b',false),'</g>');
    }
    parts.push('</g></svg>');
    return {svg:parts.join(''),width,height,count};
  }

  function checkCurrent(isCurrent){
    if(isCurrent&&!isCurrent()){
      const error=new Error('脑图已更新，请重新导出');error.name='AbortError';throw error;
    }
  }
  function loadImage(url){
    return new Promise((resolve,reject)=>{
      const image=new root.Image();
      image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('无法读取脑图导出图像，请重试'));
      image.src=url;
    });
  }
  function canvasBlob(canvas){
    return new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('PNG 图像生成失败，请重试')),'image/png'));
  }
  async function download(snapshot,format,{filename='entity-mindmap',isCurrent,labelOf,colorFor}={}){
    checkCurrent(isCurrent);
    if(!snapshot?.tree)throw new Error('暂无可导出的脑图');
    if(format!=='svg'&&format!=='png')throw new Error('不支持的脑图导出格式');
    const result=buildSvg(snapshot.tree,{truncated:snapshot.truncated,labelOf,colorFor});
    checkCurrent(isCurrent);
    if(!root.document||!root.URL?.createObjectURL||!root.Blob)throw new Error('当前环境不支持脑图文件下载');
    let width=result.width,height=result.height,blob=new root.Blob([result.svg],{type:'image/svg+xml;charset=utf-8'});
    if(format==='png'){
      const sourceUrl=root.URL.createObjectURL(blob);
      try{
        const image=await loadImage(sourceUrl);checkCurrent(isCurrent);
        const scale=Math.min(1,MAX_DIMENSION/width,MAX_DIMENSION/height,Math.sqrt(MAX_PIXELS/width/height));
        width=Math.max(1,Math.floor(width*scale));height=Math.max(1,Math.floor(height*scale));
        const canvas=root.document.createElement('canvas');canvas.width=width;canvas.height=height;
        const context=canvas.getContext('2d');
        if(!context)throw new Error('当前浏览器无法生成 PNG 图像');
        context.fillStyle='#ffffff';context.fillRect(0,0,width,height);context.drawImage(image,0,0,width,height);
        blob=await canvasBlob(canvas);checkCurrent(isCurrent);
      }finally{root.URL.revokeObjectURL(sourceUrl);}
    }
    checkCurrent(isCurrent);
    const url=root.URL.createObjectURL(blob);let anchor;
    try{
      anchor=root.document.createElement('a');
      anchor.href=url;anchor.download=String(filename||'entity-mindmap').replace(/\.(?:svg|png)$/i,'')+'.'+format;
      root.document.body.appendChild(anchor);checkCurrent(isCurrent);anchor.click();
    }finally{
      try{anchor?.remove();}finally{root.setTimeout(()=>root.URL.revokeObjectURL(url),0);}
    }
    return {width,height,count:result.count,format,sourceWidth:result.width,sourceHeight:result.height};
  }
  return {buildSvg,download};
});
