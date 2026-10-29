/* Shared bounded exploration controls. Adapters keep renderer and request ownership. */
(function(root){
  'use strict';
  const entity=row=>row&&(!row.kind||row.kind==='entity')&&!row.virtual;
  function createController({onChange=()=>{}}={}){
    let context=null,emptyMode='local',selected=null,opened=false,focused=false,hops=1,direction='both',page={items:[],count:0,nextCursor:null};
    let cursor=null,history=[],error='',busy=false,loading=false,generation=0,pageRequest=0,focusRequest=0,operation=0,focusInfo=null;
    const pending=new Set();
    const track=promise=>{pending.add(promise);promise.finally(()=>pending.delete(promise));return promise;};
    const snapshot=()=>({mode:context?.mode||emptyMode,open:opened,selected,focused,hops,direction,page,error,busy,loading,focusInfo,
      canExpand:Boolean(selected&&context?.adapter?.expand),canCollapse:Boolean(selected&&context?.adapter?.collapse&&(busy||context.adapter.canCollapse?.(selected.id)))});
    const emit=()=>onChange(snapshot());
    async function loadPage(){
      const serial=++pageRequest,version=generation,id=selected?.id,adapter=context?.adapter;
      if(!opened||!id||!adapter){loading=false;page={items:[],count:0,nextCursor:null};emit();return;}
      loading=true;emit();
      try{
        const value=await adapter.neighbors(id,{direction,limit:30,cursor});
        if(version!==generation||serial!==pageRequest||!opened)return;
        if(value.resetCursor){cursor=null;history=[];}
        page=value;error='';
      }catch(failure){if(version===generation&&serial===pageRequest)error=failure.message;}
      finally{if(version===generation&&serial===pageRequest){loading=false;emit();}}
    }
    async function focusNow(){
      const serial=++focusRequest;
      if(!focused||!selected||!context?.adapter)return;
      const version=generation,id=selected.id,adapter=context.adapter;
      try{
        const value=await adapter.focus(id,{hops,direction,maxNodes:200,maxEdges:400,maxScannedEdges:2000});
        if(version!==generation||serial!==focusRequest||!focused||selected?.id!==id)return;
        focusInfo={nodes:value.nodeIds.size,edges:value.edgeIds.size,truncated:Boolean(value.truncated)};
        adapter.setFocus(value);emit();
      }catch(failure){if(version===generation&&serial===focusRequest){focused=false;focusInfo=null;adapter.setFocus(null);error=failure.message;emit();}}
    }
    const refresh=()=>track(Promise.all([loadPage(),focusNow()]));
    function select(row){
      const next=entity(row)?row:null;if(next?.id===selected?.id)return;
      generation++;pageRequest++;operation++;busy=false;loading=false;focusInfo=null;context?.adapter?.setFocus(null);
      selected=next;cursor=null;history=[];page={items:[],count:0,nextCursor:null};error='';
      if(!selected)focused=false;emit();refresh();
    }
    function setContext(next){
      const boundary=context&&(context.key!==next.key||context.mode!==next.mode);
      const changed=!context||boundary||context.revision!==next.revision;
      if(boundary){context.adapter?.setFocus(null);focused=false;focusInfo=null;}
      context=next;emptyMode=next.mode;
      if(changed){generation++;pageRequest++;operation++;busy=false;loading=false;cursor=null;history=[];page={items:[],count:0,nextCursor:null};error='';}
      const row=entity(next.selected)?next.selected:null,selectionChanged=row?.id!==selected?.id;
      if(selectionChanged){select(row);return;}
      selected=row;if(!selected)focused=false;emit();if(changed)refresh();
    }
    async function expand(){
      if(!selected||!context?.adapter?.expand||busy)return;
      const serial=++operation,adapter=context.adapter,id=selected.id;busy=true;error='';emit();
      try{await adapter.expand(id);}catch(failure){if(serial===operation)error=failure.message;}
      finally{if(serial===operation){busy=false;emit();}}
    }
    function collapse(){
      if(!selected||!context?.adapter?.collapse)return;
      operation++;busy=false;error='';context.adapter.collapse(selected.id);emit();
    }
    return {snapshot,setContext,select,refresh,expand,collapse,
      open(){opened=true;emit();return track(loadPage());},
      close(){opened=false;loading=false;pageRequest++;emit();},
      next(){if(!page.nextCursor)return Promise.resolve();history.push(cursor);cursor=page.nextCursor;return track(loadPage());},
      previous(){if(!history.length)return Promise.resolve();cursor=history.pop();return track(loadPage());},
      first(){cursor=null;history=[];return track(loadPage());},
      setDirection(value){if(!['both','forward','backward'].includes(value))return Promise.resolve();direction=value;cursor=null;history=[];return refresh();},
      setHops(value){if(![1,2].includes(Number(value)))return Promise.resolve();hops=Number(value);emit();return track(focusNow());},
      toggleFocus(){if(!selected)return Promise.resolve();focused=!focused;error='';if(!focused){context?.adapter?.setFocus(null);focusInfo=null;}emit();return track(focusNow());},
      locate(row){if(entity(row))context?.adapter?.select(row);},
      clear({mode=context?.mode||emptyMode}={}){generation++;pageRequest++;focusRequest++;operation++;context?.adapter?.setFocus(null);context=null;emptyMode=mode;selected=null;opened=false;focused=false;focusInfo=null;busy=false;loading=false;cursor=null;history=[];page={items:[],count:0,nextCursor:null};error='';emit();},
      idle(){return Promise.all([...pending]);},
      canPrevious:()=>history.length>0};
  }
  let panel,button,controller;
  const text=(tag,value,className)=>{const el=document.createElement(tag);el.textContent=value;if(className)el.className=className;return el;};
  function render(value){
    if(!panel)return;
    const stage=document.getElementById('graph-stage'),toolbar=stage.querySelector((stage.dataset.graphMode||value.mode)==='full'?'.full-graph-toolbar':'.graph-tool-actions');
    if(toolbar&&button.parentElement!==toolbar)toolbar.append(button);
    panel.hidden=!value.open;button.setAttribute('aria-expanded',String(value.open));button.classList.toggle('is-active',value.open);
    if(!value.open)return;
    panel.querySelector('[data-explorer="anchor"]').textContent=value.selected?.text||value.selected?.id||'先选择一个实体';
    panel.querySelector('[data-explorer="scope"]').textContent=value.mode==='full'?'当前已加载画布 · 不代表全库关联':'当前局部画布 · 展开仍受范围与预算限制';
    const focus=panel.querySelector('[data-explorer="focus"]');focus.disabled=!value.selected;focus.setAttribute('aria-pressed',String(value.focused));focus.textContent=value.focused?'退出聚焦':'聚焦邻域';
    panel.querySelector('[data-explorer="hops"]').value=String(value.hops);panel.querySelector('[data-explorer="direction"]').value=value.direction;
    const expand=panel.querySelector('[data-explorer="expand"]');expand.disabled=!value.canExpand||value.busy;expand.textContent=value.busy?'正在展开…':value.mode==='full'?'转入局部展开':'展开此分支';
    panel.querySelector('[data-explorer="collapse"]').disabled=!value.canCollapse;
    panel.querySelector('[data-explorer="previous"]').disabled=!controller.canPrevious();panel.querySelector('[data-explorer="next"]').disabled=!value.page.nextCursor;
    const note=value.error||(value.loading?'正在读取邻居…':value.selected?`${value.page.count||0} 条已加载关联${value.page.truncated?' · 列表扫描已达上限':''}${value.focused&&value.focusInfo?` · 聚焦 ${value.focusInfo.nodes} 实体 / ${value.focusInfo.edges} 关系${value.focusInfo.truncated?'（已达聚焦上限）':''}`:value.focused?' · 正在准备聚焦…':''}`:'选择实体后查看邻居、展开分支或聚焦。');
    panel.querySelector('[data-explorer="status"]').textContent=note;
    const list=panel.querySelector('[data-explorer="list"]');list.replaceChildren();
    for(const item of value.page.items||[]){
      const row=text('button','','graph-neighbor-row');row.type='button';row.title=item.node.id;
      row.append(text('strong',item.node.text||item.node.id),text('span',`${item.direction==='in'?'←':item.direction==='self'?'↻':'→'} ${item.edge.type_label||root.labelOf?.(item.edge.type)||item.edge.type||'关系'} · ${item.node.type_label||root.labelOf?.(item.node.type)||item.node.type||'未分类'}`));
      row.onclick=()=>controller.locate(item.node);list.append(row);
    }
  }
  controller=createController({onChange:render});
  function install(){
    const stage=document.getElementById('graph-stage');if(!stage)return;
    button=text('button','邻居','secondary graph-tool-button graph-neighbors-toggle');button.id='graph-neighbors';button.type='button';button.setAttribute('aria-controls','graph-explorer');button.setAttribute('aria-expanded','false');
    button.onclick=()=>controller.snapshot().open?controller.close():controller.open();
    panel=document.createElement('section');panel.id='graph-explorer';panel.className='graph-explorer';panel.hidden=true;panel.setAttribute('role','region');panel.setAttribute('aria-labelledby','graph-explorer-title');
    panel.innerHTML='<header><h3 id="graph-explorer-title">邻居探索</h3><button type="button" data-explorer="close" aria-label="关闭邻居探索">×</button></header><strong class="graph-explorer-anchor" data-explorer="anchor"></strong><p class="graph-explorer-scope" data-explorer="scope"></p><div class="graph-explorer-options"><label>方向<select data-explorer="direction" aria-label="邻居与聚焦方向"><option value="both">双向</option><option value="forward">沿箭头</option><option value="backward">逆箭头</option></select></label><label>聚焦范围<select data-explorer="hops" aria-label="聚焦跳数"><option value="1">1 跳</option><option value="2">2 跳</option></select></label><button type="button" data-explorer="focus" aria-pressed="false">聚焦邻域</button></div><div class="graph-explorer-branches"><button type="button" data-explorer="expand">展开此分支</button><button type="button" data-explorer="collapse">收起此分支</button></div><p data-explorer="status" role="status"></p><div data-explorer="list" class="graph-neighbor-list"></div><footer><button type="button" data-explorer="previous">上一页</button><button type="button" data-explorer="next">下一页</button></footer>';
    stage.append(panel);
    const resizeTools=()=>{stage.classList.toggle('graph-explorer-compact',stage.clientWidth<760);stage.classList.toggle('graph-explorer-narrow',stage.clientWidth<380);};
    resizeTools();if(typeof ResizeObserver!=='undefined')new ResizeObserver(resizeTools).observe(stage);
    const control=key=>panel.querySelector(`[data-explorer="${key}"]`);
    control('close').onclick=()=>{controller.close();button.focus();};
    for(const key of ['focus','expand','collapse','previous','next'])control(key).onclick=()=>controller[key==='focus'?'toggleFocus':key]();
    control('direction').onchange=event=>controller.setDirection(event.target.value);control('hops').onchange=event=>controller.setHops(event.target.value);
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();controller.close();button.focus();}});
    render(controller.snapshot());
  }
  root.GraphExplorer={...controller,createController};
  if(typeof module!=='undefined')module.exports={createController};
  if(typeof document!=='undefined'){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();}
})(globalThis);
