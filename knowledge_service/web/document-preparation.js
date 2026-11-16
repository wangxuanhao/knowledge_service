/* Staged file preparation: uploading never starts deep parsing or publication. */
(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.DocumentPreparation=api;
})(typeof window==='object'?window:globalThis,root=>{
  const clone=value=>JSON.parse(JSON.stringify(value));
  const id=()=>root.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2);
  const labels={staged:'待解析',queued:'排队中',running:'正在解析',completed:'解析完成',reused:'已复用解析',failed:'解析失败',cancelling:'正在取消',cancelled:'已取消',stale:'需要重新解析',published:'已提交处理',uploading:'正在上传'};
  const stages={parsing:'解析正文',parse:'解析正文',ocr:'文字识别',asr:'语音转写',transcribing:'语音转写',chunking:'生成切片',preparing:'准备文件',preparing_source:'整理原件',preparing_text:'整理正文',vectorizing:'计算切片向量',saving_result:'保存解析结果',completed:'完成',failed:'失败',queued:'等待执行'};
  function progressLabel(item){
    const p=item.progress;
    const stageLabel=stages[item.stage]||({recovered:'恢复任务',interrupted:'等待恢复',timeout:'处理超时'})[item.stage];
    return [labels[item.status]||'处理中',['running','queued','cancelling'].includes(item.status)?stageLabel:'',
      item.vectorization?.status==='ready'?'切片向量已准备':item.vectorization?.status==='failed'?'向量待重试（提交后自动处理）':item.vectorization?.status==='pending'?'向量待更新（提交后自动处理）':'',
      p&&Number.isFinite(p.completed)&&p.total>0?`${p.completed} / ${p.total} ${p.unit||''}`:''].filter(Boolean).join(' · ');
  }
  function processingCopy(mode){
    return ({
      discovery:{label:'本体构建',action:'开始构建本体',done:'已提交本体构建任务',description:'发现知识结构，生成待确认的本体候选；后续在本体治理中确认草案。'},
      ontology:{label:'知识抽取',action:'开始知识抽取',done:'已提交知识抽取任务',description:'按已发布本体抽取实体、关系和属性。'},
      documents:{label:'文档检索',action:'开始建立检索索引',done:'已提交检索索引任务',description:'为资料建立关键词和语义检索索引。'}
    })[mode]||{label:'提交处理',action:'开始处理',done:'已提交处理任务',description:'按所选方式处理资料。'};
  }
  function sourceLabel(chunk){
    const metadata=chunk.metadata||{},payloads=(chunk.source_spans||[]).map(span=>span.payload||{});
    const locations=[metadata,...payloads,...payloads.flatMap(p=>p.source_locations||[])];
    const pages=[...new Set(locations.map(p=>p.page_no).filter(p=>Number.isSafeInteger(p)&&p>0))].sort((a,b)=>a-b);
    const media=locations.find(p=>Number.isFinite(p.start_ms)&&Number.isFinite(p.end_ms));
    const time=ms=>{const seconds=Math.floor(ms/1000);return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');};
    return [pages.length?'第 '+pages.join('、')+' 页':'',media?time(media.start_ms)+'–'+time(media.end_ms):''].filter(Boolean).join(' · ');
  }
  const unset=value=>value==null||value===''||value?.mode==='unknown';
  function applyFields(original,fields,mode='overwrite'){
    const result=clone(original||{});
    for(const [key,value] of Object.entries(fields)){
      if(key==='applicability'){
        result.applicability={...(result.applicability||{})};
        for(const [dimension,scope] of Object.entries(value))if(mode==='overwrite'||unset(result.applicability[dimension]))result.applicability[dimension]=clone(scope);
      }else if(mode==='overwrite'||unset(result[key]))result[key]=clone(value);
    }
    return result;
  }
  async function request(path,body,method=body===undefined?'GET':'POST',headers={}){
    const multipart=typeof FormData!=='undefined'&&body instanceof FormData;
    const response=await root.fetch(path,{method,headers:{...(!multipart&&body!==undefined?{'Content-Type':'application/json'}:{}),...headers},
      body:body===undefined?undefined:multipart?body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok){const detail=data.detail||data;throw Object.assign(Error(typeof detail==='string'?detail:detail.message||JSON.stringify(detail)),{status:response.status});}
    return data;
  }
  function uploadRequest(path,form,onProgress,key){
    if(!root.XMLHttpRequest)return request(path,form,'POST',{'Idempotency-Key':key});
    return new Promise((resolve,reject)=>{
      const url=new URL(path,root.location.origin);
      if(url.origin!==root.location.origin||!url.pathname.startsWith('/api/')){reject(Error('上传地址无效'));return;}
      const xhr=new root.XMLHttpRequest();xhr.open('POST',url.pathname);
      const token=root.localStorage?.getItem('kg_token');
      if(token)xhr.setRequestHeader('Authorization','Bearer '+token);
      xhr.setRequestHeader('Idempotency-Key',key);
      xhr.upload.onprogress=event=>{if(event.lengthComputable)onProgress?.(event.loaded,event.total);};
      xhr.onerror=()=>reject(Error('上传连接中断，请重试该文件。'));
      xhr.onabort=()=>reject(Error('上传已取消。'));
      xhr.onload=()=>{
        try{const data=JSON.parse(xhr.responseText),detail=data.detail||data;
          if(xhr.status>=200&&xhr.status<300)resolve(data);
          else reject(Object.assign(Error(typeof detail==='string'?detail:detail.message||JSON.stringify(detail)),{status:xhr.status}));
        }catch{reject(Error('上传服务返回了无效结果。'));}
      };
      xhr.send(form);
    });
  }
  function createSession({project,request:send=request,upload:sendUpload,onChange=()=>{},sessionId=null}={}){
    const base='/api/projects/'+encodeURIComponent(project)+'/preparations';
    let data={id:sessionId,items:[]},creating=null,disposed=false;
    const notify=()=>{if(!disposed)onChange(clone(data));};
    const accept=result=>{if(!disposed){data=result;notify();}return result;};
    const ensure=async()=>{
      if(disposed)throw Error('当前上传草稿已切换。');
      if(data.id)return data;
      if(!creating)creating=send(base,{defaults:{document_time_mode:'auto'}},'POST').then(accept).finally(()=>{creating=null;});
      return creating;
    };
    const url=async suffix=>base+'/'+encodeURIComponent((await ensure()).id)+suffix;
    const versions=ids=>Object.fromEntries(data.items.filter(i=>ids.includes(i.id)).map(i=>[i.id,i.metadata_version]));
    async function action(name,ids,extra={}){
      const path=await url('/'+name);
      const body={item_ids:ids,...(name==='cancel'?{}:{versions:versions(ids)}),
        ...(['parse','retry','publish'].includes(name)?{idempotency_key:id()}:{}),...extra};
      const result=await send(path,body,'POST');
      if(!disposed)accept(result.session||result);return result;
    }
    return {
      ensure,snapshot:()=>clone(data),
      async restore(){return accept(await send(await url(''),undefined,'GET'));},
      async configureSettings(settings,expectedVersion=data.settings_version||0){return accept(await send(await url('/settings'),{settings,expected_version:expectedVersion},'PATCH'));},
      async upload(file,name=file.name,onProgress,key=id()){
        const path=await url('/files');if(disposed)return;
        const form=new FormData();form.append('file',file,name);
        const result=await (sendUpload?sendUpload(path,form,onProgress,key):send(path,form,'POST',{'Idempotency-Key':key}));
        if(!disposed){const at=data.items.findIndex(i=>i.id===result.id);if(at<0)data.items.push(result);else data.items[at]=result;notify();}
        return result;
      },
      async configure(itemId,options,expectedVersion){
        const item=data.items.find(i=>i.id===itemId);
        const result=await send(await url('/items/'+encodeURIComponent(itemId)),{options,expected_version:expectedVersion??item?.metadata_version},'PATCH');
        if(!disposed){const at=data.items.findIndex(i=>i.id===itemId);if(at>=0)data.items[at]=result;notify();}return result;
      },
      bulk:(ids,fields,mode,expectedVersions)=>action('bulk',ids,{fields,mode:mode==='fill'?'fill_missing':'overwrite',...(expectedVersions?{versions:expectedVersions}:{})}),
      parse:(ids)=>action('parse',ids),retry:ids=>action('retry',ids),cancel:ids=>action('cancel',ids),
      publish:(ids,settings,independentIds=[])=>action('publish',ids,{settings,settings_version:data.settings_version||0,independent_item_ids:independentIds,result_versions:Object.fromEntries(data.items.filter(i=>ids.includes(i.id)).map(i=>[i.id,i.chunk_version]))}),
      async remove(itemId){return accept(await send(await url('/items/'+encodeURIComponent(itemId)),undefined,'DELETE'));},
      async result(itemId){return send(await url('/items/'+encodeURIComponent(itemId)+'/result'),undefined,'GET');},
      async preview(itemId){return send(await url('/items/'+encodeURIComponent(itemId)+'/preview'),undefined,'GET');},
      async original(itemId){return url('/items/'+encodeURIComponent(itemId)+'/original');},
      dispose(){disposed=true;data={id:null,items:[]};},
    };
  }
  return {createSession,progressLabel,sourceLabel,processingCopy,applyFields,uploadRequest,labels,request};
});
