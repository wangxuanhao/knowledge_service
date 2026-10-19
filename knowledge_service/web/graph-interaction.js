/* View state is separate from facts and ontology revisions. */
(function(root){
  function createSession(clock=()=>Date.now()){
    let start=null,moved=false,suppressUntil=0;
    const positions=new Map();
    return {
      press(x,y){start=[x,y];moved=false;},
      move(x,y){if(start&&Math.hypot(x-start[0],y-start[1])>5)moved=true;},
      release(){const dragged=Boolean(start)&&moved;if(dragged)suppressUntil=clock()+300;start=null;return dragged;},
      canClick(){return !moved&&clock()>=suppressUntil||!start&&clock()>=suppressUntil;},
      remember(project,nodes){
        if(!positions.has(project))positions.set(project,new Map());
        for(const node of nodes)if(Number.isFinite(node.x)&&Number.isFinite(node.y))positions.get(project).set(node.id,[node.x,node.y]);
      },
      position(project,id){const point=positions.get(project)?.get(id);return point&&[...point];},
      clear(){positions.clear();start=null;moved=false;suppressUntil=0;},
    };
  }
  function fitViewport(nodes,width,height){
    const valid=nodes.filter(n=>Number.isFinite(n.x)&&Number.isFinite(n.y));
    if(!valid.length||width<=0||height<=0)return null;
    const xs=valid.map(n=>n.x),ys=valid.map(n=>n.y);
    const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
    return {center:[(minX+maxX)/2,(minY+maxY)/2],zoom:Math.max(.05,Math.min(2,(width-100)/Math.max(100,maxX-minX),(height-100)/Math.max(100,maxY-minY)))};
  }
  function createPanQueue({apply,schedule=fn=>root.requestAnimationFrame(fn),cancel=id=>root.cancelAnimationFrame(id)}){
    let dx=0,dy=0,frame=null,epoch=0;
    function flush(){
      if(frame!==null){cancel(frame);frame=null;}
      const x=dx,y=dy;dx=0;dy=0;
      if(x||y)apply(x,y);
    }
    return {
      move(x,y){
        if(!Number.isFinite(x)||!Number.isFinite(y)||(!x&&!y))return;
        dx+=x;dy+=y;
        if(frame===null){const stamp=epoch;frame=schedule(()=>{if(stamp!==epoch)return;frame=null;flush();});}
      },
      flush,
      clear(){epoch++;if(frame!==null)cancel(frame);frame=null;dx=0;dy=0;},
    };
  }
  root.GraphInteraction={createSession,fitViewport,createPanQueue};
  if(typeof module!=='undefined')module.exports=root.GraphInteraction;
})(globalThis);
