(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root)root.GraphExploration=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';

  const realNode=node=>Boolean(node&&node.id!=null&&!node.virtual&&node.kind!=='attribute_value');
  const realEdge=edge=>Boolean(edge&&!edge.virtual&&edge.kind!=='attribute_edge'&&edge.type!=='KS_ATTRIBUTE');
  const presentationMaps=['type_labels','class_parents','class_ancestors'];
  const owns=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
  const jsonMap=value=>Boolean(value&&typeof value==='object'&&!Array.isArray(value));
  const directionOf=value=>value==='forward'||value==='backward'?value:'both';
  const budget=(value,fallback)=>Number.isFinite(value)?Math.max(0,Math.floor(value)):fallback;
  const rows=source=>source.records||source.nodes||[];

  function grow(array,required,fill){
    if(array.length>=required)return array;
    let capacity=Math.max(4,array.length);
    while(capacity<required)capacity*=2;
    const expanded=new Int32Array(capacity);
    if(fill===-1)expanded.fill(-1);
    expanded.set(array);
    return expanded;
  }

  // Integer positions refer to the canonical append-only arrays. No record copies
  // or per-node JS adjacency arrays are retained by this index.
  class NeighborIndex{
    constructor(source,options={}){
      this.source=source;
      this.nodeIndex=source.nodeIndex instanceof Map?source.nodeIndex:new Map();
      this._nodes=rows(source);
      this._edges=source.edges||[];
      this._nodesSeen=0;
      this._edgesSeen=0;
      this.version=0;
      this._cursorKey={};
      this.headOut=new Int32Array(0);
      this.headIn=new Int32Array(0);
      this.nextOut=new Int32Array(0);
      this.nextIn=new Int32Array(0);
      this._countOut=new Int32Array(0);
      this._countIn=new Int32Array(0);
      this._countSelf=new Int32Array(0);
      if(!options.defer)this.sync();
    }

    get complete(){
      return rows(this.source)===this._nodes&&(this.source.edges||this._edges)===this._edges&&this._nodesSeen===this._nodes.length&&this._edgesSeen===this._edges.length;
    }

    _slot(id){
      const position=this.nodeIndex.get(id);
      return Number.isInteger(position)&&position>=0&&position<this._nodes.length?position:-1;
    }

    sync(options={}){
      if(rows(this.source)!==this._nodes||(this.source.edges||this._edges)!==this._edges||this._nodes.length<this._nodesSeen||this._edges.length<this._edgesSeen){
        throw new Error('NeighborIndex requires an append-only source; create a new index for a new projection.');
      }
      const nodeLength=this._nodes.length,edgeLength=this._edges.length;
      if(nodeLength===this._nodesSeen&&edgeLength===this._edgesSeen)return this.version;
      const nodeEnd=Math.min(nodeLength,this._nodesSeen+budget(options.maxNodes,Infinity));
      // Edge endpoints can refer to any current node. Finish node indexing
      // first so chunk boundaries cannot permanently discard valid relations.
      const edgeEnd=nodeEnd===nodeLength?Math.min(edgeLength,this._edgesSeen+budget(options.maxEdges,Infinity)):this._edgesSeen;
      if(nodeEnd===this._nodesSeen&&edgeEnd===this._edgesSeen)return this.version;
      this.headOut=grow(this.headOut,nodeLength,-1);
      this.headIn=grow(this.headIn,nodeLength,-1);
      this._countOut=grow(this._countOut,nodeLength,0);
      this._countIn=grow(this._countIn,nodeLength,0);
      this._countSelf=grow(this._countSelf,nodeLength,0);
      for(let i=this._nodesSeen;i<nodeEnd;i++){
        const node=this._nodes[i];
        if(node&&node.id!=null&&!this.nodeIndex.has(node.id))this.nodeIndex.set(node.id,i);
      }
      this.nextOut=grow(this.nextOut,edgeLength,-1);
      this.nextIn=grow(this.nextIn,edgeLength,-1);
      for(let i=this._edgesSeen;i<edgeEnd;i++){
        const edge=this._edges[i];
        if(!realEdge(edge)||edge.id==null)continue;
        const from=this._slot(edge.subject_id),to=this._slot(edge.object_id);
        if(from<0||to<0||!realNode(this._nodes[from])||!realNode(this._nodes[to]))continue;
        this.nextOut[i]=this.headOut[from];this.headOut[from]=i;this._countOut[from]++;
        this.nextIn[i]=this.headIn[to];this.headIn[to]=i;this._countIn[to]++;
        if(from===to)this._countSelf[from]++;
      }
      this._nodesSeen=nodeEnd;this._edgesSeen=edgeEnd;
      return ++this.version;
    }

    _start(slot,direction){
      return direction==='backward'?{phase:1,edge:this.headIn[slot]}:{phase:0,edge:this.headOut[slot]};
    }

    _step(state,slot,direction){
      if(state.edge<0&&state.phase===0&&direction==='both'){
        state.phase=1;state.edge=this.headIn[slot];
      }
      if(state.edge<0)return null;
      const position=state.edge,phase=state.phase;
      state.edge=(phase===0?this.nextOut:this.nextIn)[position];
      const edge=this._edges[position];
      if(!edge)return {skip:true};
      const self=edge.subject_id===edge.object_id;
      if(direction==='both'&&phase===1&&self)return {skip:true};
      const other=phase===0?edge.object_id:edge.subject_id;
      const otherSlot=this._slot(other),node=otherSlot<0?null:this._nodes[otherSlot];
      if(!realEdge(edge)||!realNode(node))return {skip:true};
      return {node,edge,direction:self?'self':phase===0?'out':'in',slot:otherSlot};
    }

    _remaining(state,slot,direction){
      return state.edge>=0||(state.phase===0&&direction==='both'&&this.headIn[slot]>=0);
    }

    page(id,options={}){
      this.sync();
      const direction=directionOf(options.direction),limit=Math.max(1,Math.min(200,budget(options.limit,30)));
      const slot=this._slot(id),cursor=options.cursor;
      const resetCursor=Boolean(cursor&&(cursor.key!==this._cursorKey||cursor.version!==this.version||cursor.id!==id||cursor.direction!==direction));
      const result={items:[],nextCursor:null,count:0,scanned:0,truncated:false,resetCursor};
      if(slot<0||!realNode(this._nodes[slot]))return result;
      result.count=direction==='forward'?this._countOut[slot]:direction==='backward'?this._countIn[slot]:this._countOut[slot]+this._countIn[slot]-this._countSelf[slot];
      const state=cursor&&!resetCursor?{phase:cursor.phase,edge:cursor.edge}:this._start(slot,direction);
      const offset=cursor&&!resetCursor?cursor.offset:0;
      const maxScanned=Math.max(64,limit*4);
      while(result.items.length<limit&&result.scanned<maxScanned){
        const item=this._step(state,slot,direction);
        if(!item)break;
        result.scanned++;
        if(!item.skip)result.items.push({node:item.node,edge:item.edge,direction:item.direction});
      }
      // Incoming self-loop entries are consumed under the same bounded scan cap.
      if(offset+result.items.length<result.count&&this._remaining(state,slot,direction)){
        result.nextCursor={key:this._cursorKey,version:this.version,id,direction,phase:state.phase,edge:state.edge,offset:offset+result.items.length};
        result.truncated=result.scanned>=maxScanned;
      }
      return result;
    }

    focus(id,options={}){
      this.sync();
      const result={nodeIds:new Set(),edgeIds:new Set(),edgeRecords:[],truncated:false,scannedEdges:0};
      const start=this._slot(id);
      if(start<0||!realNode(this._nodes[start]))return result;
      const maxNodes=budget(options.maxNodes,200),maxEdges=budget(options.maxEdges,400),maxScanned=budget(options.maxScannedEdges,2000);
      if(!maxNodes){result.truncated=true;return result;}
      const direction=directionOf(options.direction),hops=options.hops===2?2:1,queue=[[start,0]];
      result.nodeIds.add(id);
      for(let q=0;q<queue.length;q++){
        const [slot,depth]=queue[q];
        if(depth>=hops)continue;
        const state=this._start(slot,direction);
        while(this._remaining(state,slot,direction)){
          if(result.scannedEdges>=maxScanned){result.truncated=true;return result;}
          const item=this._step(state,slot,direction);
          if(!item)break;
          result.scannedEdges++;
          if(item.skip||result.edgeIds.has(item.edge.id))continue;
          const newNode=!result.nodeIds.has(item.node.id);
          if(result.edgeIds.size>=maxEdges||(newNode&&result.nodeIds.size>=maxNodes)){
            result.truncated=true;continue;
          }
          if(newNode){result.nodeIds.add(item.node.id);queue.push([item.slot,depth+1]);}
          result.edgeIds.add(item.edge.id);
          result.edgeRecords.push(item.edge);
        }
      }
      return result;
    }
  }

  function addGraph(target,graph){
    for(const node of graph.nodes||[]){if(node&&node.id!=null&&!target.nodes.has(node.id))target.nodes.set(node.id,node);}
    for(const edge of graph.edges||[]){if(edge&&edge.id!=null&&!target.edges.has(edge.id))target.edges.set(edge.id,edge);}
  }

  class BranchSession{
    constructor(options={}){
      this.maxNodes=budget(options.maxNodes,500);
      this.maxEdges=budget(options.maxEdges,1000);
      this.maxAttributes=budget(options.maxAttributes,200);
      this._epoch=0;
      this._serial=0;
      this.clear();
    }

    clear(){
      this._epoch++;
      this._base={nodes:[],edges:[]};
      this._scopeKey=null;
      this._branches=new Map();
      this._pending=new Map();
    }

    invalidatePending(){
      this._epoch++;
      this._pending.clear();
    }

    reset(graph,seed,scopeKey){
      this.clear();
      this._base=graph&&Array.isArray(graph.nodes)&&Array.isArray(graph.edges)?graph:{nodes:[],edges:[]};
      this._scopeKey=scopeKey;
      this.seed=seed;
      return this.graph();
    }

    // Solve visibility from the fixed base on every merge. A detached cycle has
    // no starting anchor and therefore cannot keep itself alive.
    _merge(replacementSeed,replacementGraph){
      const branches=replacementGraph?new Map(this._branches):this._branches;
      if(replacementGraph)branches.set(replacementSeed,replacementGraph);
      const merged={nodes:new Map(),edges:new Map()},active=new Set();
      addGraph(merged,this._base);
      let changed=true;
      while(changed){
        changed=false;
        for(const [seed,branch] of branches){
          if(active.has(seed)||!realNode(merged.nodes.get(seed)))continue;
          active.add(seed);addGraph(merged,branch);changed=true;
        }
      }
      // First occurrence order remains base, then branch insertion order even
      // when a child branch precedes the branch that makes its anchor visible.
      const ordered={nodes:new Map(),edges:new Map()};addGraph(ordered,this._base);
      for(const [seed,branch] of branches){if(active.has(seed))addGraph(ordered,branch);}
      for(const [id,edge] of ordered.edges){if(!ordered.nodes.has(edge.subject_id)||!ordered.nodes.has(edge.object_id))ordered.edges.delete(id);}
      return {...ordered,active};
    }

    _prune(){
      const merged=this._merge();
      for(const seed of this._branches.keys()){if(!merged.active.has(seed))this._branches.delete(seed);}
      for(const seed of this._pending.keys()){if(!realNode(merged.nodes.get(seed)))this._pending.delete(seed);}
    }

    begin(seed,scopeKey){
      if(scopeKey!==this._scopeKey||!realNode(this._merge().nodes.get(seed)))return null;
      const ticket=Object.freeze({epoch:this._epoch,serial:++this._serial,seed,scopeKey});
      this._pending.set(seed,ticket);
      return ticket;
    }

    _bound(seed,graph,current){
      // Reserve owners rooted after the proposed replacement, so descendants
      // it detaches release their budget while genuinely shared owners remain.
      const surviving=this._merge(seed,graph).active;
      const reserved={nodes:new Map(),edges:new Map()};addGraph(reserved,this._base);
      for(const [anchor,branch] of this._branches){if(anchor!==seed&&surviving.has(anchor))addGraph(reserved,branch);}
      let entities=0,attributes=0,relations=0,attributeEdges=0;
      for(const node of reserved.nodes.values()){if(realNode(node))entities++;else attributes++;}
      for(const edge of reserved.edges.values()){if(realEdge(edge))relations++;else attributeEdges++;}
      const selected={nodes:new Map(),edges:new Map()};
      let truncated=Boolean(graph.truncated||graph.exploration_truncated);
      for(const candidate of graph.nodes){
        if(!candidate||candidate.id==null){truncated=true;continue;}
        if(selected.nodes.has(candidate.id))continue;
        const node=current.nodes.get(candidate.id)||candidate;
        if(!reserved.nodes.has(node.id)){
          if(realNode(node)?entities>=this.maxNodes:attributes>=this.maxAttributes){truncated=true;continue;}
          if(realNode(node))entities++;else attributes++;
          reserved.nodes.set(node.id,node);
        }
        selected.nodes.set(node.id,node);
      }
      for(const candidate of graph.edges){
        if(!candidate||candidate.id==null){truncated=true;continue;}
        if(selected.edges.has(candidate.id))continue;
        const edge=current.edges.get(candidate.id)||candidate;
        if(!reserved.nodes.has(edge.subject_id)||!reserved.nodes.has(edge.object_id)){truncated=true;continue;}
        if(!reserved.edges.has(edge.id)){
          if(realEdge(edge)?relations>=this.maxEdges:attributeEdges>=this.maxAttributes){truncated=true;continue;}
          if(realEdge(edge))relations++;else attributeEdges++;
          reserved.edges.set(edge.id,edge);
        }
        selected.edges.set(edge.id,edge);
      }
      const bounded={nodes:[...selected.nodes.values()],edges:[...selected.edges.values()],truncated};
      if(Array.isArray(graph.records))bounded.records=[...bounded.nodes,...bounded.edges];
      for(const key of presentationMaps){if(owns(graph,key)&&jsonMap(graph[key]))bounded[key]=graph[key];}
      if(owns(graph,'attribute_count'))bounded.attribute_count=graph.attribute_count;
      return bounded;
    }

    accept(ticket,graph){
      if(!ticket||ticket.epoch!==this._epoch||ticket.scopeKey!==this._scopeKey||this._pending.get(ticket.seed)!==ticket)return false;
      if(!graph||!Array.isArray(graph.nodes)||!Array.isArray(graph.edges))return false;
      const current=this._merge();
      if(!realNode(current.nodes.get(ticket.seed)))return false;
      for(const edge of graph.edges){
        const existing=edge&&current.edges.get(edge.id);
        if(existing&&(existing.subject_id!==edge.subject_id||existing.object_id!==edge.object_id))return false;
      }
      const bounded=this._bound(ticket.seed,graph,current);
      this._branches.set(ticket.seed,bounded);
      this._pending.delete(ticket.seed);
      this._prune();
      return true;
    }

    expand(seed,graph,scopeKey){return this.accept(this.begin(seed,scopeKey),graph);}

    collapse(seed){
      const changed=this._branches.delete(seed)||this._pending.has(seed);
      this._pending.delete(seed);
      this._prune();
      return changed;
    }

    has(seed){return this._merge().active.has(seed);}
    canCollapse(seed){return this.has(seed)||this._pending.has(seed);}

    graph(){
      const merged=this._merge(),nodes=[...merged.nodes.values()],edges=[...merged.edges.values()];
      let entities=0,attributes=0,relations=0,attributeEdges=0;
      for(const node of nodes){if(realNode(node))entities++;else attributes++;}
      for(const edge of edges){if(realEdge(edge))relations++;else attributeEdges++;}
      let explorationTruncated=entities>this.maxNodes||relations>this.maxEdges||attributes>this.maxAttributes||attributeEdges>this.maxAttributes;
      let hasRecords=Array.isArray(this._base.records),hasAttributeCount=owns(this._base,'attribute_count');
      const result={...this._base,nodes,edges};
      for(const [seed,branch] of this._branches){
        if(!merged.active.has(seed))continue;
        explorationTruncated=explorationTruncated||branch.truncated;
        hasRecords=hasRecords||Array.isArray(branch.records);
        hasAttributeCount=hasAttributeCount||owns(branch,'attribute_count');
        for(const key of presentationMaps){if(jsonMap(branch[key]))result[key]={...result[key],...branch[key]};}
      }
      if(hasRecords)result.records=[...nodes,...edges];
      if(hasAttributeCount)result.attribute_count=attributes;
      result.truncated=Boolean(this._base.truncated||this._base.exploration_truncated||explorationTruncated);
      result.exploration_truncated=Boolean(this._base.exploration_truncated||explorationTruncated);
      return result;
    }
  }

  return {NeighborIndex,BranchSession};
});
