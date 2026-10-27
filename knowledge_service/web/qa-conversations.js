/* Account-owned server conversations. Local storage contains selection IDs only. */
(function(root) {
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value));
  const failure=(message,status)=>Object.assign(new Error(message),{status});
  function createClient(options={}) {
    const fetcher=options.fetch||((...args)=>(root.Auth?.fetchConversation||root.fetch)(...args));
    const base=project=>'/api/projects/'+encodeURIComponent(project)+'/qa/conversations';
    async function request(url,method,body) {
      const init={method};
      if(body!==undefined){init.headers={'Content-Type':'application/json'};init.body=JSON.stringify(body);}
      const response=await fetcher(url,init);
      if(!response.ok) {
        let message='会话操作失败（'+response.status+'）';
        try {const data=await response.json();if(typeof data.detail==='string')message=data.detail;else if(data.detail)message=JSON.stringify(data.detail);}catch{}
        throw failure(message,response.status);
      }
      return response.status===204?undefined:response.json();
    }
    return {
      list:(project,{limit=50,offset=0,q=''}={})=>request(base(project)+'?limit='+limit+'&offset='+offset+'&q='+encodeURIComponent(q),'GET'),
      create:(project,title)=>request(base(project),'POST',title?{title}:{}),
      get:(project,id)=>request(base(project)+'/'+encodeURIComponent(id),'GET'),
      save:(project,id,body)=>request(base(project)+'/'+encodeURIComponent(id),'PUT',body),
      rename:(project,id,body)=>request(base(project)+'/'+encodeURIComponent(id),'PATCH',body),
      remove:(project,id,{expected_version}={})=>request(base(project)+'/'+encodeURIComponent(id)+'?expected_version='+encodeURIComponent(expected_version),'DELETE')
    };
  }
  const pointerKey=(actor,project)=>'kg_qa_active_v1_'+encodeURIComponent(JSON.stringify([String(actor),String(project)]));
  function createController(options={}) {
    const api=options.api||createClient(options),records=new Map();
    const getActor=options.getActor||(()=>root.Auth?.user()?.id||'auth-disabled');
    let store=options.storage;
    if(store===undefined){try{store=root.localStorage;}catch{}}
    let project='',actor='',scopeEpoch=0,selectionEpoch=0,listEpoch=0,active=null;
    let rows=[],total=0,hasMore=false,query='',pending=0,creating=null,scopeError=null;
    const authReady=options.authReady===undefined?root.__authReady:options.authReady;
    function context(){return {project,actor,scope:scopeEpoch};}
    function current(ctx){return ctx.scope===scopeEpoch&&ctx.project===project&&ctx.actor===actor&&ctx.actor===String(getActor()||'auth-disabled');}
    function isActive(record){return active===record&&current(record.context);}
    function read(key){try{return store?.getItem(key)||null;}catch{return null;}}
    function write(key,value){try{value===null?store?.removeItem(key):store?.setItem(key,value);}catch{}}
    function legacy(){try{const value=JSON.parse(read('kg_qa_v1_'+project)||'null');return Array.isArray(value)&&value.length?value:null;}catch{return null;}}
    function visible(record){return record?{...copy(record.conversation),messages:copy(record.localMessages??record.conversation.messages??[])}:null;}
    function state(){
      const valid=current(context()),selected=valid?active:null;
      return {project,actor,loading:valid&&pending>0,conflict:!!selected?.conflict,
        dirty:!!selected&&selected.localMessages!==null,
        canRetry:!!selected?.error&&selected.localMessages!==null&&!selected.conflict,
        loginRequired:selected?.error?.status===401||scopeError?.status===401,
        saveError:selected?.error?.message||'',activeId:selected?.conversation.id||null,
        conversation:visible(selected),conversations:valid?copy(rows):[],total:valid?total:0,
        hasMore:valid&&hasMore,query,legacyAvailable:valid&&!!project&&!!legacy(),reload:reloadActive,retry:retrySave};
    }
    function emit(){options.onState?.(state());}
    function start(ctx){if(current(ctx)){pending++;emit();}}
    function end(ctx){if(current(ctx)){pending=Math.max(0,pending-1);emit();}}
    function report(error,ctx,record){
      if(record)record.error=error;
      else if(current(ctx))scopeError=error;
      if(error.status===409&&record)record.conflict=true;
      if(current(ctx)&&(!record||isActive(record))){emit();options.onError?.(error.status===409?'会话已在其他设备修改，发生版本冲突。本机未保存内容已保留，请重新加载会话后再继续。':error.message||'会话操作失败');}
    }
    function recordFor(conversation,ctx,{fresh=false}={}){
      const key=JSON.stringify([ctx.actor,ctx.project,conversation.id]);
      let record=records.get(key);
      if(!record){record={context:{...ctx},conversation:copy(conversation),queue:Promise.resolve(),conflict:false,tombstone:false,localMessages:null,localRevision:0,error:null};records.set(key,record);}
      else if(!record.tombstone){record.conversation=copy(conversation);record.context={...ctx};if(fresh){record.conflict=false;record.localMessages=null;record.error=null;}}
      return record;
    }
    function summary(conversation){const {messages,...row}=conversation;return row;}
    function sortRows(){rows.sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')));}
    function updateRow(record){
      if(!current(record.context)||record.tombstone)return;
      const row=summary(record.conversation),index=rows.findIndex(item=>item.id===row.id);
      if(query&&!String(row.title||'').includes(query)){if(index>=0)rows.splice(index,1);}
      else if(index>=0)rows[index]=row;
      else {rows.push(row);total++;}
      sortRows();emit();
    }
    function activate(record){
      active=record;scopeError=null;
      write(pointerKey(actor,project),record?.conversation.id||null);
      options.onActivate?.(copy(record?.localMessages??record?.conversation.messages??[]),visible(record));
      emit();
    }
    async function refresh({append=false}={}) {
      if(!project)return;
      const ctx=context(),requestEpoch=++listEpoch,search=query,offset=append?rows.length:0;
      start(ctx);
      try {
        const result=await api.list(ctx.project,{limit:50,offset,q:search});
        if(!current(ctx)||requestEpoch!==listEpoch||query!==search)return;
        const incoming=result.conversations||[];
        rows=append?[...rows,...incoming.filter(item=>!rows.some(row=>row.id===item.id))]:incoming;
        total=result.total;hasMore=!!result.has_more;scopeError=null;sortRows();emit();
        return result;
      }catch(error){if(requestEpoch===listEpoch)report(error,ctx);throw error;}
      finally{end(ctx);}
    }
    async function switchTo(id,{before=true,reload=false}={}) {
      if(!project)return null;
      if(!current(context()))throw failure('账号已切换，请重新加载项目');
      const ctx=context(),selection=++selectionEpoch;
      const previous=active;if(before)options.onBeforeChange?.();
      active=null;start(ctx);
      try {
        const known=records.get(JSON.stringify([ctx.actor,ctx.project,id]));
        if(known){await known.queue;if(known.tombstone)throw failure('该会话正在删除或已删除');}
        if(!current(ctx)||selection!==selectionEpoch)return null;
        if(known&&!reload&&(known.localMessages!==null||known.conflict)){known.context={...ctx};activate(known);return visible(known);}
        const result=await api.get(ctx.project,id);
        if(!current(ctx)||selection!==selectionEpoch)return null;
        const record=recordFor(result.conversation,ctx,{fresh:true});activate(record);return copy(record.conversation);
      }catch(error){if(current(ctx)&&selection===selectionEpoch){if(previous&&!previous.tombstone)activate(previous);report(error,ctx);}throw error;}
      finally{end(ctx);}
    }
    async function setProject(value) {
      const requested=String(value||''),scope=++scopeEpoch;
      ++selectionEpoch;++listEpoch;options.onBeforeChange?.();
      project=requested;actor=String(getActor()||'auth-disabled');active=null;creating=null;rows=[];query='';total=0;hasMore=false;pending=0;scopeError=null;emit();
      if(!project){options.onActivate?.([],null);return;}
      pending=1;emit();
      try {
        await Promise.resolve(typeof authReady==='function'?authReady():authReady);
        if(scope!==scopeEpoch||project!==requested)return;
        actor=String(getActor()||'auth-disabled');
        const ctx=context();
        await refresh();if(!current(ctx))return;
        const remembered=read(pointerKey(actor,project)),id=remembered||rows[0]?.id;
        if(id){
          try{await switchTo(id,{before:false});}
          catch(error){
            if(error.status!==404||!current(ctx))throw error;
            write(pointerKey(actor,project),null);
            const fallback=rows.find(row=>row.id!==id);
            if(fallback)await switchTo(fallback.id,{before:false});else activate(null);
          }
        }else activate(null);
      }finally{if(scope===scopeEpoch){pending=Math.max(0,pending-1);emit();}}
    }
    function ensureActive() {
      if(project&&!current(context()))return Promise.reject(failure('账号已切换，请重新加载项目'));
      if(active){if(active.tombstone)return Promise.reject(failure('该会话正在删除或已删除'));return Promise.resolve(visible(active));}
      if(creating)return creating.promise;
      if(!project)return Promise.reject(failure('请先选择项目'));
      const ctx=context(),selection=selectionEpoch,token={};start(ctx);
      token.promise=(async()=>{
        try{
          const result=await api.create(ctx.project);
          if(!current(ctx)||selection!==selectionEpoch)throw failure('项目、账号或会话已切换，请重新操作');
          const record=recordFor(result.conversation,ctx);updateRow(record);activate(record);return copy(record.conversation);
        }catch(error){report(error,ctx);throw error;}
        finally{if(creating===token)creating=null;end(ctx);}
      })();creating=token;return token.promise;
    }
    function enqueue(record,operation) {
      if(record.tombstone)return Promise.reject(failure('该会话正在删除或已删除'));
      const ctx={...record.context};
      const promise=record.queue.then(async()=>{
        if(record.conflict)throw failure('会话存在版本冲突，请先重新加载',409);
        try{const result=await operation(record.conversation.version);record.conversation=copy(result.conversation);if(current(ctx))scopeError=null;updateRow(record);return copy(record.conversation);}
        catch(error){report(error,ctx,record);throw error;}
      });
      record.queue=promise.catch(()=>{});return promise;
    }
    function save(messages) {
      const snapshot=copy(messages);
      if(!Array.isArray(snapshot))return Promise.reject(failure('会话消息必须是数组'));
      const record=active;
      if(!record||!isActive(record))return Promise.reject(failure('当前会话尚未加载，请重新选择会话'));
      if(record.tombstone)return Promise.reject(failure('该会话正在删除或已删除'));
      const revision=++record.localRevision;record.localMessages=snapshot;
      return enqueue(record,version=>api.save(record.context.project,record.conversation.id,{expected_version:version,messages:snapshot})).then(conversation=>{
        if(record.localRevision===revision){record.localMessages=null;record.error=null;}
        if(isActive(record))emit();return conversation;
      });
    }
    function retrySave(){if(!active||active.localMessages===null)return Promise.resolve(null);return save(copy(active.localMessages));}
    async function newConversation() {
      if(project&&!current(context()))throw failure('账号已切换，请重新加载项目');
      options.onBeforeChange?.();
      if(active&&!active.tombstone&&!active.conflict&&!active.conversation.message_count&&!(options.getMessages?.()||active.conversation.messages||[]).length){activate(active);return copy(active.conversation);}
      ++selectionEpoch;active=null;creating=null;emit();return ensureActive();
    }
    async function rename(id,title) {
      if(!current(context()))throw failure('账号已切换，请重新加载项目');
      const value=String(title||'').trim();
      if(!value)throw failure('请输入会话名称');
      if(value.length>80)throw failure('会话名称不能超过 80 个字符');
      const ctx=context();let record=records.get(JSON.stringify([ctx.actor,ctx.project,id]));
      if(!record)record=recordFor((await api.get(ctx.project,id)).conversation,ctx);
      if(!current(ctx))throw failure('项目或账号已切换');
      return enqueue(record,version=>api.rename(ctx.project,id,{expected_version:version,title:value}));
    }
    async function remove(id) {
      if(!current(context()))throw failure('账号已切换，请重新加载项目');
      const ctx=context();let record=records.get(JSON.stringify([ctx.actor,ctx.project,id]));
      if(!record){const row=rows.find(item=>item.id===id);if(!row)throw failure('会话不存在',404);record=recordFor({...row,messages:[]},ctx);}
      if(record.tombstone)throw failure('该会话正在删除或已删除');
      const wasActive=active===record,selection=selectionEpoch;
      record.tombstone=true;if(wasActive)options.onBeforeChange?.();start(ctx);
      try {
        await record.queue;await api.remove(ctx.project,id,{expected_version:record.conversation.version});
        if(!current(ctx))return;
        rows=rows.filter(row=>row.id!==id);total=Math.max(0,total-1);
        if(read(pointerKey(actor,project))===id)write(pointerKey(actor,project),null);
        if(wasActive&&active===record&&selection===selectionEpoch){active=null;await refresh();if(rows.length)await switchTo(rows[0].id,{before:false});else activate(null);}else emit();
      }catch(error){record.tombstone=false;report(error,ctx,record);if(current(ctx)&&active===record&&selection===selectionEpoch)activate(record);throw error;}
      finally{end(ctx);}
    }
    async function reloadActive(){return active?switchTo(active.conversation.id,{reload:true}):null;}
    async function search(value){query=String(value||'');return refresh();}
    async function loadMore(){if(hasMore)return refresh({append:true});}
    async function importLegacy(confirmed) {
      if(!current(context()))throw failure('账号已切换，请重新加载项目');
      if(confirmed!==true)throw failure('请先确认这些本机旧记录属于当前账号');
      const messages=legacy();if(!messages)throw failure('没有可导入的本机旧记录');
      const ctx=context();options.onBeforeChange?.();const selection=++selectionEpoch;active=null;start(ctx);
      try {
        const result=await api.create(ctx.project,'导入的本机旧记录');
        const record=recordFor(result.conversation,ctx);
        const saved=await enqueue(record,version=>api.save(ctx.project,record.conversation.id,{expected_version:version,messages:copy(messages)}));
        if(current(ctx)&&selection===selectionEpoch)activate(record);
        return saved;
      }catch(error){report(error,ctx);throw error;}
      finally{end(ctx);}
    }
    async function clearActive() {
      if(!active)return;
      const record=active,ctx=context(),selection=selectionEpoch;
      options.onBeforeChange?.();start(ctx);
      try{
        const saving=save([]);
        // Clear the local history together with its pending empty snapshot. If the
        // request fails, the next question must start from this unsaved clear intent.
        if(current(ctx)&&active===record&&selection===selectionEpoch)options.onActivate?.([],visible(record));
        return await saving;
      }
      finally{end(ctx);}
    }
    return {setProject,ensureActive,save,refresh,search,loadMore,switchTo,newConversation,rename,remove,reloadActive,retrySave,importLegacy,clearActive,
      get loading(){return current(context())&&pending>0;},get activeId(){return active&&isActive(active)?active.conversation.id:null;},get state(){return state();}};
  }
  function mount(options={}) {
    const document=options.document||root.document;
    const byId=id=>document?.getElementById(id);
    const workspace=byId('qa-workspace'),list=byId('qa-session-list');
    if(!workspace||!list)return null;
    const status=byId('qa-session-status'),toggle=byId('qa-sessions-toggle'),backdrop=byId('qa-sessions-backdrop');
    const searchInput=byId('qa-session-search'),more=byId('qa-session-more'),importButton=byId('qa-session-import');
    const dialog=byId('qa-session-dialog'),nameInput=byId('qa-session-name'),renameLabel=byId('qa-session-rename-label');
    const dialogTitle=byId('qa-session-dialog-title'),description=byId('qa-session-dialog-description');
    const cancel=byId('qa-session-dialog-cancel'),confirm=byId('qa-session-dialog-confirm');
    let controller,settleDialog=null,searchTimer=null;
    const view=options.window||root;
    const width=()=>options.viewportWidth??view.innerWidth??1200;
    function historyOpen(open){workspace.dataset.historyOpen=String(open);toggle?.setAttribute('aria-expanded',String(open));if(backdrop)backdrop.hidden=!open||width()>1100;}
    historyOpen(width()>1100);
    function listen(element,event,callback){element?.addEventListener(event,callback);}
    let wasDesktop=width()>1100;
    if(view.addEventListener)listen(view,'resize',()=>{const desktop=width()>1100;if(desktop!==wasDesktop){wasDesktop=desktop;historyOpen(desktop);}});
    function run(operation){Promise.resolve().then(operation).catch(error=>{const message=error.message||'会话操作失败';options.onError?.(message);if(status&&!controller.state.conflict&&!controller.state.canRetry&&!controller.state.loginRequired)status.textContent=message;});}
    function closeNarrow(){if(width()<=1100)historyOpen(false);}
    function showDialog({title,text,action='确认',rename=false,value='',trigger}) {
      if(!dialog||!confirm||!cancel)return Promise.reject(failure('会话确认对话框不可用，请刷新页面'));
      settleDialog?.({confirmed:false});
      dialogTitle.textContent=title;description.textContent=text;confirm.textContent=action;
      if(renameLabel)renameLabel.hidden=!rename;
      if(nameInput){nameInput.value=value;nameInput.setCustomValidity?.('');}
      const focused=trigger||document.activeElement;
      return new Promise(resolve=>{
        const listeners=[];
        function on(element,event,callback){listen(element,event,callback);listeners.push([element,event,callback]);}
        function finish(result){
          if(settleDialog!==finish)return;
          settleDialog=null;
          for(const [element,event,callback] of listeners)element?.removeEventListener(event,callback);
          if(dialog.open)dialog.close();
          if(focused?.isConnected!==false)focused?.focus?.();else toggle?.focus?.();
          resolve(result);
        }
        function submit(event){
          event?.preventDefault();const value=String(nameInput?.value||'').trim();
          if(rename&&(!value||value.length>80)){nameInput?.setCustomValidity?.(!value?'请输入会话名称':'会话名称不能超过 80 个字符');nameInput?.reportValidity?.();nameInput?.focus?.();return;}
          finish({confirmed:true,value});
        }
        settleDialog=finish;
        on(confirm,'click',submit);
        on(cancel,'click',event=>{event.preventDefault();finish({confirmed:false});});
        on(dialog,'cancel',event=>{event.preventDefault();finish({confirmed:false});});
        on(dialog,'close',()=>finish({confirmed:false}));
        on(dialog.querySelector('form'),'submit',submit);
        on(nameInput,'input',()=>nameInput.setCustomValidity?.(''));
        on(nameInput,'keydown',event=>{if(event.key==='Enter'){event.preventDefault();submit(event);}});
        try{dialog.showModal();if(rename){nameInput?.focus?.();nameInput?.select?.();}else cancel.focus();}
        catch(error){finish({confirmed:false});options.onError?.(error.message);}
      });
    }
    async function requestReload(trigger) {
      const result=await showDialog({title:'重新加载会话',text:'重新加载会用服务器记录替换当前未保存内容。确认放弃本机未保存内容并继续？',action:'重新加载',trigger});
      if(result.confirmed)await controller.reloadActive();
    }
    function render(state) {
      const title=byId('qa-active-session-title');if(title)title.textContent=state.conversation?.title||'新会话';
      for(const id of ['qa-session-new','qa-new-chat']){const button=byId(id);if(button)button.disabled=state.loading||!state.project;}
      if(more){more.hidden=!state.hasMore;more.disabled=state.loading;}
      if(importButton){importButton.hidden=!state.legacyAvailable;importButton.disabled=state.loading;}
      if(searchInput&&document.activeElement!==searchInput&&searchInput.value!==state.query)searchInput.value=state.query;
      list.replaceChildren();
      for(const row of state.conversations) {
        const item=document.createElement('li'),selected=row.id===state.activeId;
        item.className='qa-session-item'+(selected?' is-active':'');
        const select=document.createElement('button');select.type='button';select.className='qa-session-select';
        if(selected)select.setAttribute('aria-current','true');
        select.disabled=state.loading;
        const title=document.createElement('span');title.className='qa-session-title';title.textContent=row.title||'新会话';
        const info=document.createElement('small');
        let updated='';if(row.updated_at){const date=new Date(row.updated_at);if(!Number.isNaN(date.getTime()))updated=date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
        info.textContent=(updated?updated+' · ':'')+(Number(row.message_count)||0)+' 条消息';
        select.append(title,info);listen(select,'click',()=>run(async()=>{await controller.switchTo(row.id);closeNarrow();}));
        const actions=document.createElement('details');actions.className='qa-session-actions';
        const menu=document.createElement('summary');menu.textContent='更多操作';menu.setAttribute('aria-label','更多操作：'+(row.title||'新会话'));
        const rename=document.createElement('button');rename.type='button';rename.textContent='重命名';rename.disabled=state.loading;
        listen(rename,'click',()=>{
          actions.open=false;
          const result=showDialog({title:'重命名会话',text:'为这段问答设置一个便于查找的名称。',action:'保存名称',rename:true,value:row.title||'',trigger:rename});
          run(async()=>{const answer=await result;if(answer.confirmed)await controller.rename(row.id,answer.value);});
        });
        const remove=document.createElement('button');remove.type='button';remove.textContent='删除';remove.disabled=state.loading;
        listen(remove,'click',()=>{
          actions.open=false;
          const result=showDialog({title:'删除会话',text:'仅删除“'+(row.title||'新会话')+'”及其问答记录，来源文档保持不变。此操作无法撤销。',action:'删除会话',trigger:remove});
          run(async()=>{if((await result).confirmed)await controller.remove(row.id);});
        });
        actions.append(menu,rename,remove);item.append(select,actions);list.append(item);
      }
      if(status){
        status.replaceChildren();
        if(state.conflict){
          status.textContent='其他设备已修改此会话。本机未保存内容已保留，请重新加载后继续。';
          const reload=document.createElement('button');reload.type='button';reload.className='qa-session-reload';reload.textContent='重新加载会话';
          listen(reload,'click',()=>{const result=requestReload(reload);run(()=>result);});status.append(reload);
        }else if(state.loginRequired){
          status.textContent='登录已失效。本页未保存内容已保留，请在新标签页登录原账号，再回到这里重试。';
          const login=document.createElement('a');login.className='qa-session-login';login.textContent='重新登录';login.href='/login';login.target='_blank';login.rel='noopener noreferrer';status.append(login);
          const retry=document.createElement('button');retry.type='button';retry.className='qa-session-retry';retry.textContent=state.canRetry?'重试保存':'重新加载会话';
          listen(retry,'click',()=>run(()=>state.canRetry?controller.retrySave():controller.setProject(state.project)));status.append(retry);
        }else if(state.canRetry){
          status.textContent='问答尚未保存：'+state.saveError+'。本机内容已保留。';
          const retry=document.createElement('button');retry.type='button';retry.className='qa-session-retry';retry.textContent='重试保存';
          listen(retry,'click',()=>run(()=>controller.retrySave()));status.append(retry);
        }else status.textContent=state.loading?'正在同步会话…':state.conversations.length?'共 '+state.total+' 个会话':state.query?'没有匹配的会话':'暂无会话，发送问题后自动保存';
      }
      options.onState?.(state);
    }
    controller=createController({...options,onState:render,onBeforeChange:()=>{settleDialog?.({confirmed:false});options.onBeforeChange?.();},onError:message=>{if(status&&!controller?.state.conflict&&!controller?.state.canRetry&&!controller?.state.loginRequired)status.textContent=message;options.onError?.(message);}});
    const initialSetProject=controller.setProject;
    controller.setProject=project=>{if(searchTimer!==null){clearTimeout(searchTimer);searchTimer=null;}if(searchInput)searchInput.value='';return initialSetProject(project);};
    listen(toggle,'click',()=>historyOpen(workspace.dataset.historyOpen!=='true'));
    listen(byId('qa-sessions-close'),'click',()=>historyOpen(false));
    listen(backdrop,'click',()=>historyOpen(false));
    for(const id of ['qa-session-new','qa-new-chat'])listen(byId(id),'click',()=>run(async()=>{await controller.newConversation();closeNarrow();}));
    listen(searchInput,'input',()=>{if(searchTimer!==null)clearTimeout(searchTimer);const value=searchInput.value;searchTimer=setTimeout(()=>{searchTimer=null;run(()=>controller.search(value));},180);});
    listen(searchInput,'keydown',event=>{if(event.key==='Enter'){event.preventDefault();if(searchTimer!==null)clearTimeout(searchTimer);searchTimer=null;run(()=>controller.search(searchInput.value));}});
    listen(more,'click',()=>run(()=>controller.loadMore()));
    listen(importButton,'click',()=>{
      const result=showDialog({title:'导入本机旧记录',text:'请确认这些本机旧记录属于当前账号。确认后会复制到当前账号的服务端会话，供其他设备访问；本机旧记录仍会保留。',action:'确认属于当前账号并导入',trigger:importButton});
      run(async()=>{if((await result).confirmed)await controller.importLegacy(true);});
    });
    controller.requestClear=async()=>{
      if(!controller.activeId)return;
      const result=await showDialog({title:'清空当前会话',text:'清空当前会话的全部问答记录，来源文档保持不变。此操作无法撤销。',action:'清空问答'});
      if(result.confirmed)return controller.clearActive();
    };
    render(controller.state);
    return controller;
  }
  const exported={createClient,createController,mount,pointerKey};
  if(typeof module==='object'&&module.exports)module.exports=exported;
  if(root)root.QaConversations=exported;
})(typeof window==='undefined'?globalThis:window);
