'use strict';
importScripts('full-graph.js?v=workspace-layout-20261124-3');

self.onmessage=event=>{
  const {id,ids}=event.data,positions=new Float32Array(ids.length*2);
  ids.forEach((value,i)=>positions.set(self.FullGraph.point(value),i*2));
  self.postMessage({id,positions},[positions.buffer]);
};
