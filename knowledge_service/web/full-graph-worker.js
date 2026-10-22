'use strict';
// Kept separate from the UI thread so large batches can acquire stable positions.
function hash(value){let n=2166136261;for(const c of String(value)){n^=c.charCodeAt(0);n=Math.imul(n,16777619);}return n>>>0;}
self.onmessage=event=>{const {id,ids}=event.data;const positions=new Float32Array(ids.length*2);ids.forEach((value,i)=>{positions[i*2]=((hash(value)&0xffffff)/0xffffff)*1.9-.95;positions[i*2+1]=((hash(value+'@y')&0xffffff)/0xffffff)*1.9-.95;});self.postMessage({id,positions},[positions.buffer]);};
