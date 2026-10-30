/* Bound interactive requests, including response consumption, and relay cancellation. */
(function(root){
  'use strict';
  function create({signal:parent,timeoutMs=45000,message='请求等待超时，请重试。'}={}){
    const controller=new AbortController();
    const cancelled=()=>Object.assign(new Error('请求已取消'),{name:'AbortError'});
    const reason=()=>controller.signal.reason||cancelled();
    const relay=()=>controller.abort(parent.reason||cancelled());
    if(parent?.aborted)relay();else parent?.addEventListener('abort',relay,{once:true});
    function wait(value,overrides={}){
      const pending=Promise.resolve(value);
      if(controller.signal.aborted){pending.catch(()=>{});return Promise.reject(reason());}
      let timer,onAbort;
      const interrupted=new Promise((_,reject)=>{
        onAbort=()=>reject(reason());
        controller.signal.addEventListener('abort',onAbort,{once:true});
        const duration=overrides.timeoutMs??timeoutMs;
        if(duration>0){
          timer=setTimeout(()=>controller.abort(Object.assign(
            new Error(overrides.message||message),{name:'TimeoutError',code:'request_timeout'})),duration);
          timer?.unref?.();
        }
      });
      return Promise.race([pending,interrupted]).finally(()=>{
        if(timer!==undefined)clearTimeout(timer);
        controller.signal.removeEventListener('abort',onAbort);
      });
    }
    return {signal:controller.signal,wait,close(){parent?.removeEventListener('abort',relay);}};
  }
  async function run(task,options){
    const request=create(options);
    try{
      if(request.signal.aborted)throw request.signal.reason;
      return await request.wait(task(request.signal));
    }finally{request.close();}
  }
  const api={create,run};
  root.RequestDeadline=api;
  if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
