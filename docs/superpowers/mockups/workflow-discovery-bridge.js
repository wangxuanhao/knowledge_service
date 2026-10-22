const discoveryBridge = (() => {
  const candidates=[
    {key:'type',kind:'class',id:'OnboardingApplication',label:'入驻申请',parent:'Application',source:'商户入驻规范.pdf · r1',description:'商户申请入驻的业务申请。',status:'pending'},
    {key:'mapping',kind:'class',id:'ReviewMaterial',label:'审核材料',parent:'BusinessObject',source:'商户入驻规范.pdf · r1',description:'可能与申请材料同义，需核对语义。',status:'pending'},
    {key:'relation',kind:'relation',id:'submitsMaterial',label:'提交资料',from:'Merchant',to:'Material',source:'商户入驻规范.pdf · r1',description:'商户提交申请所需资料。',status:'pending'},
    {key:'attribute',kind:'attribute',id:'businessLicenseCode',label:'营业执照编号',owner:'Merchant',datatype:'string',source:'商户入驻规范.pdf · r1',description:'业务证照编号。',status:'pending'}
  ];
  const states={pending:'待处理',adopted:'已加入草案',mapped:'已映射',deferred:'已暂缓',rejected:'未采纳'};
  const kinds={class:'类',relation:'业务关系',attribute:'数据属性'};
  const button=(action,label,extra='',style='')=>`<button data-act="bridge-${action}" ${extra} class="${style}">${label}</button>`;
  const options=(rows,value)=>rows.map(x=>`<option value="${esc(x.id)}" ${x.id===value?'selected':''}>${esc(x.label)}</option>`).join('');
  const current=()=>studioUI.context();
  const mode=()=>hasOntology()?(state.upload.graphMode||'controlled'):'discovery';
  const basis=()=>hasOntology()?current().published:null;
  const availableDocs=()=>allDocs().filter(d=>!['failed','queued'].includes(d.state));
  let counter=1;
  let linkMode=null,parentFingerprint='',parentDecisions={};
  function uploadMode(){
    const selected=mode(),published=basis();
    return `<div id="upload-ontology" class="bridge-mode-panel spaced" ${state.upload.purpose==='documents'?'hidden':''}><h3>本批资料的抽取方式</h3><div class="bridge-mode-choices"><label><input name="graph-mode" type="radio" value="controlled" ${selected==='controlled'?'checked':''} ${!published?'disabled':''}><span><strong>按已有本体抽取 · 非开放</strong><small>${published?'本体 v'+published.version+' · 已发布':'暂无已发布本体，暂不可选'}；类型、关系和属性按既有定义映射。无法映射的内容列为待处理。</small></span></label><label><input name="graph-mode" type="radio" value="discovery" ${selected==='discovery'?'checked':''}><span><strong>开放抽取 · 候选发现</strong><small>${published?'已有术语优先复用，新类、关系和属性进入候选。':'先生成候选，再建立首版本体。'}经过映射、校验和审核后发布。</small></span></label></div><div class="bridge-two-dimensions"><strong>本批抽取方式 ≠ 本体能否继续编辑</strong><span>两种方式都保留后续手工建模和“发现父类”；发布后通过新草案新增或修改，再审核发布。</span></div><p class="note">${published?'本体发布：'+esc(published.id):'当前项目尚无已发布本体。本次采用开放发现，先产生候选。'}${selected==='controlled'?' 本批固定此依据，不自动切换为开放抽取。':' 开放抽取与本体发布分别记录，候选不直接成为正式知识。'}</p>${button('upload-basis','查看本次依据','', 'link small')}</div>`;
  }
  function installUpload(){
    const original=uploadDialog;
    uploadDialog=function(){
      let html=original();
      if(state.upload.step===2)html=html.replace(/<div id="upload-ontology"[\s\S]*?<\/div>/,uploadMode());
      if(state.upload.step===3&&state.upload.purpose==='graph'){
        const pub=basis(),discovery=mode()==='discovery';
        html=html.replace(/<dt>用途<\/dt><dd>[\s\S]*?<\/dd>/,`<dt>用途</dt><dd>图谱增强 · ${discovery?'开放抽取，候选确认后继续本体建模':'按已有本体抽取（非开放）'}</dd>`);
        html=html.replace(/<dt>(本体依据|知识治理)<\/dt><dd>[\s\S]*?<\/dd>/,`<dt>${discovery?'发现基线':'本体依据'}</dt><dd>${pub?'本体 v'+pub.version+' · '+esc(pub.id):'无已发布本体，从候选建立首版'}</dd>${discovery?'<dt>后续流程</dt><dd>来源候选 → 映射/编辑 → 草案 → 校验/审核 → 发布</dd>':''}`);
      }
      return html;
    };
    const originalOpen=openUpload;
    openUpload=function(){originalOpen();state.upload.graphMode=hasOntology()?'controlled':'discovery';};
  }
  function startDialog(docId){
    if(!hasOntology())return modal('建立项目本体','从资料发现，或手工定义首版结构','<p>新项目先开放发现，再核对候选并建立本体。</p><div class="callout spaced">文件已在资料库时可复用解析产物；不要求重新上传。</div>','<button data-act="close-dialog">返回</button>'+button('upload-discovery','添加资料并开放发现','','primary'));
    const c=current(),docs=availableDocs();
    return modal('从资料继续本体建模','已有本体仍可持续演进',`<div class="callout">当前发布：<strong>v${c.published.version}</strong> · ${esc(c.published.id)}<br>复用已有类、关系和属性，新增内容进入独立发现任务及草案。</div><h3 class="spaced">选择发现来源</h3><div class="bridge-source-list">${docs.map(d=>`<label><input type="checkbox" data-bridge-source="${d.id}" ${d.id===(docId||'onboard')?'checked':''}><span><strong>${esc(d.name)}</strong><small>文档修订 r${d.revision} · ${esc(d.knowledge)}</small></span></label>`).join('')}</div>${!docs.length?'<p>暂无解析完成的资料，请先添加文件。</p>':''}<label class="bridge-label spaced">发现任务名称<input id="bridge-run-name" value="补充本体 · 资料发现"></label><div class="bridge-step-row"><span>1 来源候选</span><span>2 映射与编辑</span><span>3 校验、审核与发布</span></div><p class="note">当前草案 ${esc(c.draft.id)} 的 ${c.draft.changes} 项变更保留。正式实现中，独立发现草案与现有草案分别管理，合入时检查冲突。</p><p class="bridge-preview-note">本地草图演示：精确来源选择、基线预期值和跨草案合入仍需补产品接口。</p>`,`<button data-act="close-dialog">取消</button>${button('start-run','创建示例发现任务','', 'primary')}`,true);
  }
  function candidateDialog(){
    if(!hasOntology())return startDialog();
    const c=current();
    const ids=new Set([...c.classes.map(x=>x.id),...c.relations.map(x=>x.id),...c.classes.flatMap(x=>x.properties.map(p=>p.id))]);
    candidates.forEach(x=>{if(x.status==='adopted'&&!ids.has(x.id))x.status='pending';});
    return modal('发现候选 · 继续本体建模',`当前草案 ${c.draft.id} · 基线 ${c.draft.baseId}`,`<div class="bridge-step-row"><span>来源候选</span><span>映射 / 编辑 / 暂缓</span><span>加入草案后统一审核</span></div><p class="note">类型层的术语映射与实体实例融合分别处理。这里不会合并实体记录。</p><div class="bridge-candidates">${candidates.map(x=>`<article><div class="row between"><span>${badge(kinds[x.kind])}</span>${badge(states[x.status],x.status==='pending'?'warn':x.status==='adopted'||x.status==='mapped'?'good':'')}</div><h3>${esc(x.label)}</h3><p>${esc(x.description)}</p><small class="muted">${esc(x.source)}</small>${x.status==='mapped'?`<p>复用术语：<strong>${esc(x.targetLabel)}</strong> · ${esc(x.target)}</p>`:''}${x.reason?`<p class="small">处理原因：${esc(x.reason)}</p>`:''}<div class="bridge-candidate-actions">${['pending','deferred'].includes(x.status)?`${button('edit-candidate','编辑 / 新建术语',`data-key="${x.key}"`)}${button('map-candidate','映射已有术语',`data-key="${x.key}"`)}${button('defer','暂缓',`data-key="${x.key}"`)}${button('reject','不采纳…',`data-key="${x.key}"`,'link')}`:x.status==='rejected'?button('reset','重新处理',`data-key="${x.key}"`):x.status==='adopted'?button('view-editor','去草案继续编辑 →','','link'):'<span class="small muted">映射决定将随草案审核，不改写已有术语。</span>'}</div></article>`).join('')}</div><p class="bridge-preview-note">以下是本地示例决策；产品端需保存来源、映射、原因与预期修订。未运行的语义校验不标通过。</p>`,`<button data-act="close-dialog">返回</button>${button('view-editor','进入草案编辑','','primary')}`,true);
  }
  function editCandidate(x,mapping){
    const c=current(),targets=x.kind==='class'?c.classes:x.kind==='relation'?c.relations:c.classes.flatMap(o=>o.properties.filter(p=>!p.inactive).map(p=>({...p,label:o.label+' / '+p.label})));
    const label=(title,body)=>`<label class="bridge-label">${title}${body}</label>`;
    let fields=mapping?label('选择已有术语',`<select id="bridge-map-target">${options(targets,x.kind==='class'?'Material':targets[0]?.id)}</select>`):label('显示名称',`<input id="bridge-label" value="${esc(x.label)}">`)+label('规范名称',`<input id="bridge-id" value="${esc(x.id)}">`)+label('说明',`<textarea id="bridge-description" rows="2">${esc(x.description)}</textarea>`);
    if(!mapping&&x.kind==='class')fields+=label('父类',`<select id="bridge-parent"><option value="">无父类</option>${options(c.classes,x.parent)}</select>`);
    if(!mapping&&x.kind==='relation')fields+=label('起点类型',`<select id="bridge-from">${options(c.classes,x.from)}</select>`)+label('终点类型',`<select id="bridge-to">${options(c.classes,x.to)}</select>`);
    if(!mapping&&x.kind==='attribute')fields+=label('所属类',`<select id="bridge-owner">${options(c.classes,x.owner)}</select>`)+label('值类型',`<select id="bridge-datatype">${['string','integer','decimal','date','boolean'].map(t=>`<option>${t}</option>`).join('')}</select>`);
    fields+=label('处理原因',`<input id="bridge-reason" placeholder="说明采纳或映射的依据">`);
    return modal(mapping?'映射到已有术语':'编辑发现候选',`${x.label} · ${x.source}`,`<div class="bridge-form">${fields}</div>${mapping?'<p class="note spaced">明确复用既有 IRI，不因为名称相似就自动新建或自动合并实体。</p>':''}`,`${button('candidates','返回候选')}${button(mapping?'confirm-map':'adopt',mapping?'保存示例映射':'加入当前示例草案',`data-key="${x.key}"`,'primary')}`);
  }
  function coverage(){
    const rows=[
      ['上传与发现','已有本体受控抽取 / 基于已有本体继续发现 / 无本体首次发现','上传第二步可操作；精确来源和基线参数待接线'],
      ['继续建模','从已上传资料发起、查看发现任务、定位候选','已补本地任务及候选流'],
      ['候选决策','编辑类/关系/属性、映射已有术语、暂缓、不采纳及原因','已补本地交互；跨草案合入待接线'],
      ['本体编辑','名称说明、多继承、关系端点、属性值型/数量、撤销重做','可操作'],
      ['发现父类','基于当前草案的已有类建议继承关系、保留已有边、去重/防环、审核后发布','已补独立入口和本地建议采纳；实际模型/服务未调用'],
      ['手工插入与连线','新建类/子类/关系/属性、画布选两端改父类或连业务关系','已补插入菜单与两步连线；先写草案'],
      ['抽取方式与建模','开放/非开放只约束本批抽取，发布后仍可开草案新增和修改','两种方式可切换；不以选项锁定项目建模能力'],
      ['草案治理','校验、影响、逐项/安全批量审核、提交/撤回、关闭、重基线、来源刷新','发布与部分校验可演示；逐项审核/关闭等尚缺完整操作'],
      ['术语生命周期','类/关系/属性停用、依赖影响、历史字段恢复','属性停用/恢复可演示；类与关系停用尚缺草图操作'],
      ['源码与导入','Turtle 编辑/导入、保留未支持表达、RDF 导出','查看/导出可操作；源码写入与导入差异尚缺草图'],
      ['版本与迁移','历史只读、版本比较、历史新草案、关联知识、受控迁移','历史操作可演示；迁移预演/执行仅说明'],
      ['实体治理','编辑/创建、关系/属性、别名、冲突选值、复核、撤销','可操作；独立属性/关系的完整修订与删除仍需补草图'],
      ['资料与任务','原文、提取结果、处理记录、重试、取消、索引状态/修复','主要流程可演示；完整修复动作尚未覆盖'],
      ['消融评测','固定输入/配置、对照组、逐问题证据、运行/取消/重试/导出','配置/报告结构；真实运行后端待建设']
    ];
    return modal('操作覆盖核对','本页是设计检查清单',`<p>页面收拢不等于删减能力。以下如实区分草图操作、说明入口和待建设链路。</p><div class="table-wrap spaced"><table class="bridge-coverage"><thead><tr><th>工作区</th><th>应保留操作</th><th>本轮草图状态</th></tr></thead><tbody>${rows.map(row=>'<tr>'+row.map(cell=>'<td>'+esc(cell)+'</td>').join('')+'</tr>').join('')}</tbody></table></div>`,'<span></span><button data-act="close-dialog">关闭</button>',true);
  }
  function enqueue(name,sourceIds,baseline,files){
    const sourceManifest=sourceIds.map(id=>{const doc=documents.find(d=>d.id===id);return {id,revision:doc?.revision,name:doc?.name};});
    const task={id:'bridge-discovery-'+counter++,project:state.project,name,source:sourceManifest.map(x=>x.name+' · r'+x.revision).join('、'),type:'本体发现',status:'queued',stage:'等待解析或发现候选',time:'刚刚',progress:0,graphMode:'discovery',basis:baseline,sourceIds,sourceManifest,discovery:true};
    jobs.unshift(task);return task;
  }
  function handleAction(act,b){
    if(act==='nav'){linkMode=null;return false;}
    if(act==='studio-select'&&linkMode){
      if(linkMode.kind==='parent'){
        const child=linkMode.child,parent=b.dataset.id;linkMode=null;
        openDialog('bridge-confirm-parent',{child,parent,fingerprint:current().draft.fingerprint});render();
      }else if(!linkMode.from){linkMode.from=b.dataset.id;toast('已选起点，请再点终点类型。');}
      else{const from=linkMode.from,to=b.dataset.id;linkMode=null;openDialog('studio-add-relation',{from,to});render();}
      return true;
    }
    if(act==='studio-candidates'||act==='draft'){openDialog('bridge-candidates');return true;}
    if(act==='upload-submit'){
      const u=state.upload;if(u.purpose!=='graph'||mode()!=='discovery')return false;
      const baseline=basis(),ids=[];
      u.files.forEach((file,i)=>{const id='bridge-source-'+Date.now()+'-'+i;ids.push(id);documents.unshift({id,project:state.project,name:file.name,format:file.name.split('.').pop().toUpperCase(),size:file.size,platform:file.platform||u.platform,business:u.business,audience:u.audience,revision:1,ontology:'开放发现',city:'',state:'queued',knowledge:'等待开放发现',knowledgeTone:'',index:'未开始',indexTone:'',time:'刚刚',valid:u.valid,until:u.until,published:u.published});});
      enqueue('本体发现批次 · '+ids.length+' 份文件',ids,baseline);closeDialog();navigate('jobs');toast('已创建示例发现任务，当前本体保持不变。');return true;
    }
    if(!act.startsWith('bridge-'))return false;
    const key=b.dataset.key,x=candidates.find(c=>c.key===key);
    switch(act){
      case'bridge-start':openDialog('bridge-start',{doc:b.dataset.doc});break;
      case'bridge-candidates':openDialog('bridge-candidates');break;
      case'bridge-coverage':openDialog('bridge-coverage');break;
      case'bridge-scale':openDialog('bridge-scale');break;
      case'bridge-insert':openDialog('bridge-insert');break;
      case'bridge-child':{const c=current();if(!c.draft.editable||c.selection.kind!=='class'){toast('请先选中可编辑的类型。');break;}openDialog('studio-add-class',{parent:c.selection.id});break;}
      case'bridge-link-parent':{const c=current();if(!c.draft.editable||c.selection.kind!=='class'){toast('请先进入草案并选中一个子类。');break;}linkMode={kind:'parent',child:c.selection.id};render();toast('再点一个类型作为父类，确认后写入草案。');break;}
      case'bridge-link-relation':if(!current().draft.editable){toast('请先进入可编辑草案。');break;}linkMode={kind:'relation',from:null};render();toast('请依次点起点和终点类型。');break;
      case'bridge-cancel-link':linkMode=null;render();break;
      case'bridge-confirm-parent':{const d=state.dialog,result=studioUI.applyParent(d.child,d.parent,d.fingerprint);if(!result.ok){toast(result.message);break;}closeDialog();render();toast('继承关系已加入草案，原有父类继续保留。');break;}
      case'bridge-parents':parentFingerprint=current().draft.fingerprint;openDialog('bridge-parents');break;
      case'bridge-return-draft':closeDialog(false);studioUI.handleAction('studio-edit',{dataset:{}});break;
      case'bridge-adopt-parent':{const result=studioUI.applyParent(b.dataset.child,b.dataset.parent,parentFingerprint);if(!result.ok){toast(result.message);break;}parentDecisions[b.dataset.child]='adopted';parentFingerprint=current().draft.fingerprint;openDialog('bridge-parents');render();break;}
      case'bridge-reject-parent':parentDecisions[b.dataset.child]='rejected';openDialog('bridge-parents');break;
      case'bridge-upload-basis':toast(mode()==='discovery'?'开放发现复用当前发布结构，候选另行映射、校验和审核。':'受控提取按选定已发布本体写入，不修改本体结构。');break;
      case'bridge-upload-discovery':openUpload();state.upload.graphMode='discovery';drawDialog();break;
      case'bridge-start-run':{
        const ids=[...document.querySelectorAll('[data-bridge-source]:checked')].map(i=>i.dataset.bridgeSource);
        if(!ids.length){toast('请至少选择一份已解析资料。');break;}
        enqueue($('bridge-run-name').value.trim()||'继续本体发现',ids,basis());closeDialog();navigate('jobs');toast('已创建独立示例发现任务；已有本体与草案变更保留。');break;
      }
      case'bridge-complete':{
        const task=jobs.find(j=>j.id===b.dataset.job);if(!task||task.project!==state.project)break;
        task.status='completed';task.stage='示例候选已准备，等待映射与审核';task.progress=100;
        task.sourceIds?.forEach(id=>{const doc=documents.find(d=>d.id===id);if(doc){doc.state='review';doc.knowledge='发现候选待确认';doc.knowledgeTone='warn';}});
        if(hasOntology())candidates.forEach(x=>{x.source='示例输入：'+task.source;});
        openDialog('bridge-candidates');break;
      }
      case'bridge-edit-candidate':openDialog('bridge-edit',{key});break;
      case'bridge-map-candidate':openDialog('bridge-map',{key});break;
      case'bridge-adopt':{
        const value=id=>$(id)?.value.trim()||'';
        const item={...x,id:value('bridge-id'),label:value('bridge-label'),description:value('bridge-description'),parent:value('bridge-parent'),from:value('bridge-from'),to:value('bridge-to'),owner:value('bridge-owner'),datatype:value('bridge-datatype')};
        const result=studioUI.applyCandidate(item);if(!result.ok){toast(result.message);break;}
        Object.assign(x,item,{status:'adopted',reason:value('bridge-reason')});openDialog('bridge-candidates');render();toast('候选已加入当前示例草案，尚未发布。');break;
      }
      case'bridge-confirm-map':{
        const target=$('bridge-map-target');if(!target?.value||!$('bridge-reason').value.trim()){toast('请选择已有术语并说明映射原因。');break;}
        x.status='mapped';x.target=target.value;x.targetLabel=target.selectedOptions[0].textContent;x.reason=$('bridge-reason').value.trim();openDialog('bridge-candidates');break;
      }
      case'bridge-defer':x.status='deferred';openDialog('bridge-candidates');break;
      case'bridge-reject':openDialog('bridge-reject',{key});break;
      case'bridge-confirm-reject':if(!$('bridge-reason').value.trim()){toast('请填写不采纳原因。');break;}x.status='rejected';x.reason=$('bridge-reason').value.trim();openDialog('bridge-candidates');break;
      case'bridge-reset':x.status='pending';x.reason='';openDialog('bridge-candidates');break;
      case'bridge-view-editor':navigate('ontology');break;
    }
    return true;
  }
  function handleChange(target){
    if(target.name==='graph-mode'){state.upload.graphMode=target.value;drawDialog();return true;}
    if(target.name==='purpose'&&state.dialog?.kind==='upload'){state.upload.purpose=target.value;drawDialog();return true;}
    return false;
  }
  function renderDialog(d){
    if(d.kind==='bridge-start')return startDialog(d.doc);
    if(d.kind==='bridge-candidates')return candidateDialog();
    if(d.kind==='bridge-coverage')return coverage();
    if(d.kind==='bridge-scale')return modal('检索、编辑与 15 万实体验收','设计要求与当前验证状态',`<div class="callout warn">要求同时满足全库 15 万实体可检索、同一图谱加载并绘制 15 万实体。当前草图与小图测试没有证明这两项。</div><div class="table-wrap spaced"><table class="bridge-coverage"><thead><tr><th>工作区</th><th>应具备能力</th><th>当前状态</th></tr></thead><tbody><tr><td>本体建模</td><td>类、关系类型、属性和约束编辑</td><td>结构示例；类型卡片不等于 15 万实体实例</td></tr><tr><td>结果列表</td><td>全库查询、分页、按 ID 定位</td><td>草图仅含少量示例；需验证原生与降级检索一致性</td></tr><tr><td>局部探索</td><td>选中实体的邻域、关系、证据和编辑</td><td>局部展示；不能据此声称已加载全库</td></tr><tr><td>全量大图</td><td>15 万实体同图、拾取、缩放、共享详情与编辑</td><td>必做能力；全量数据接口、WebGL 和容量实测尚未交付</td></tr></tbody></table></div><p class="note spaced">已有图性能文档主要验证 500 节点 / 1500 边。所有数据集、边数、硬件、加载耗时、实际帧率与内存必须单独记录；聚合图和只加载前 N 条都不算全量验收。</p><p class="note spaced">完整编辑、来源/版本、融合/撤销不能因换渲染器而减少。当前原型检查不代替产品 API 与容量验收。</p>`,'<span></span><button data-act="close-dialog">关闭</button>',true);
    if(d.kind==='bridge-insert'){
      const c=current(),disabled=!c.draft.editable,childDisabled=disabled||c.selection.kind!=='class';
      const owner=c.classes.find(x=>x.id===(c.selection.owner||c.selection.id))?.id||c.classes[0]?.id||'';
      return modal('插入到本体草案','新增结构仍需校验、审核和发布',`<div class="studio-governance-menu"><button data-act="studio-add-class" ${disabled?'disabled':''}><strong>新建类型</strong><span>新增概念，可设置父类；也可先建上级类再连接既有类。</span></button>${button('child','<strong>为选中类型添加子类</strong><span>新建类型并预填当前类为父类，可继续修改。</span>',childDisabled?'disabled':'')}<button data-act="studio-add-relation" ${disabled?'disabled':''}><strong>新建业务关系</strong><span>填写关系名称及起点、终点。</span></button><button data-act="studio-add-attribute" data-owner="${esc(owner)}" ${disabled?'disabled':''}><strong>新增数据属性</strong><span>设置所属类、值类型与约束。</span></button></div>${disabled?'<p class="note spaced">发布/历史版本只读，创建草案后即可继续新增和修改。</p>':''}`,'<span></span><button data-act="close-dialog">关闭</button>');
    }
    if(d.kind==='bridge-confirm-parent'){
      const c=current(),label=id=>c.classes.find(x=>x.id===id)?.label||id;
      return modal('确认添加父类',label(d.child)+' → '+label(d.parent),'<p>保留原有父类，补充这条继承关系。提交前检查重复、自引用与继承环。</p>', '<button data-act="close-dialog">取消</button>'+button('confirm-parent','加入草案','','primary'));
    }
    if(d.kind==='bridge-parents'){
      const c=current(),suggestions=[['Material','BusinessObject'],['Rule','BusinessObject'],['Application','BusinessObject']];
      if(!c.draft.editable)return modal('发布后继续建模','发布快照只读，新的修改进入草案','<p>创建或继续草案后，可以新增类型、关系和属性，也可以运行父类发现；审核发布后形成新的发布记录。</p>','<button data-act="close-dialog">返回</button>'+button('return-draft','创建 / 继续草案','','primary'));
      const body=`<div class="callout">在当前已有类之间发现继承关系，保留已有父类。需要新增一个上级概念时，从“插入 → 新建类型”创建，再连接父子类。</div><p class="bridge-preview-note">以下是本地示例建议，未调用模型。产品端已有发现父类接口；会核对草案修订、基线与继承环，审核发布后生效。</p><div class="bridge-candidates">${suggestions.map(([child,parent])=>{const a=c.classes.find(x=>x.id===child),p=c.classes.find(x=>x.id===parent),exists=a?.parents.includes(parent),status=exists?'已有继承关系':parentDecisions[child]==='rejected'?'本次不采纳':'待核对';return `<article><h3>${esc(a?.label||child)} → ${esc(p?.label||parent)}</h3><p>${status}</p><div class="bridge-candidate-actions">${button('adopt-parent','采纳到草案',`data-child="${child}" data-parent="${parent}" ${exists||!c.draft.editable||parentDecisions[child]==='rejected'?'disabled':''}`)}${button('reject-parent','不采纳',`data-child="${child}" ${exists||!c.draft.editable||parentDecisions[child]==='rejected'?'disabled':''}`)}</div></article>`;}).join('')}</div>${!c.draft.editable?'<p class="note spaced">当前查看只读发布。返回编辑器，先创建 / 继续草案即可发现和调整父类。</p>':''}`;
      return modal('发现父类','当前草案已有类的层级建议',body,'<button data-act="close-dialog">返回</button>'+button('view-editor','进入草案编辑','','primary'),true);
    }
    if(d.kind==='bridge-edit'||d.kind==='bridge-map')return editCandidate(candidates.find(x=>x.key===d.key),d.kind==='bridge-map');
    if(d.kind==='bridge-reject')return modal('不采纳候选',candidates.find(x=>x.key===d.key).label,'<label class="bridge-label">原因<textarea id="bridge-reason" rows="3" placeholder="说明与业务范围、语义或来源不符之处"></textarea></label>',button('candidates','返回')+button('confirm-reject','保存决定',`data-key="${d.key}"`,'primary'));
    return null;
  }
  function install(){
    installUpload();
    const originalSearch=renderSearch;
    renderSearch=function(){return `<details class="bridge-scale-note"><summary>示例视图 · 15 万实体全量模式尚未验证</summary><p>检索结果、局部探索和全量大图分别验收；本页小样例不代表真实容量或编辑能力已全部接通。</p>${button('scale','查看能力与容量要求','','link small')}</details>`+originalSearch();};
    const originalDocument=documentDialog;
    documentDialog=function(id,tab){let html=originalDocument(id,tab);const d=allDocs().find(x=>x.id===id);if(d&&!['failed','queued'].includes(d.state))html=html.replace('<button data-act="go-qa">',button('start','从此资料继续建模',`data-doc="${d.id}"`)+ '<button data-act="go-qa">');return html;};
    const originalJob=jobDialog;
    jobDialog=function(id){let html=originalJob(id),j=jobs.find(x=>x.id===id);if(j&&(j.discovery||id==='discover')){const action=j.status==='completed'?button('candidates','处理发现候选','','primary'):button('complete','演示完成并处理候选',`data-job="${j.id}"`,'primary');html=html.replace('<button data-act="close-dialog">关闭</button>','<button data-act="close-dialog">关闭</button>'+action);if(j.basis)html=html.replace('<dl class="detail-meta">',`<div class="callout spaced">发现基线：v${j.basis.version} · ${esc(j.basis.id)}。当前发布保持不变。</div><dl class="detail-meta">`);}return html;};
  }
  function augment(){
    if(state.page==='ontology'&&hasOntology()){
      $('heading-actions').innerHTML=button('parents','发现父类')+button('start','从资料继续建模','','primary');
      const tools=document.querySelector('.studio-top-tools .studio-tool-cluster');
      if(tools){const c=current();tools.insertAdjacentHTML('beforeend',button('insert','＋ 插入',!c.draft.editable?'disabled':'')+button('link-parent','改父类（连线）',!c.draft.editable?'disabled':'')+button('link-relation','连关系',!c.draft.editable?'disabled':'')+(linkMode?button('cancel-link','取消连线','','link'):''));}
    }
    if(!document.querySelector('[data-act="bridge-coverage"]'))document.querySelector('.preview-shortcuts')?.insertAdjacentHTML('beforeend',button('coverage','操作覆盖'));
    if(!document.querySelector('[data-act="bridge-scale"]'))document.querySelector('.preview-shortcuts')?.insertAdjacentHTML('beforeend',button('scale','规模与能力'));
  }
  return {handleAction,handleChange,renderDialog,install,augment};
})();
