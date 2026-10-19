const maintenanceUI = (() => {
  const originalQuality=renderQuality;
  const entities=[
    {id:'M001',name:'华星商户',type:'商户',iri:'urn:demo:Merchant',revision:2,ontology:'v3',platform:'平台 A',valid:'2026-01-01',aliases:['华星店'],values:{'商户编号':'DEMO-M001','联系邮箱':'shop-a@example.invalid','状态':'已登记'},sources:['退款处理说明.md r2'],relations:['提交 → 退款申请'],deleted:false,merged:false},
    {id:'M002',name:'华星门店',type:'商户',iri:'urn:demo:Merchant',revision:1,ontology:'v4',platform:'平台 A',valid:'2026-01-01',aliases:['华星商户（门店）'],values:{'商户编号':'DEMO-M001','联系邮箱':'shop-b@example.invalid','状态':'已登记'},sources:['商户入驻规范.pdf r1'],relations:['适用 → 规则条款'],deleted:false,merged:false},
    {id:'A001',name:'退款申请',type:'退款申请',iri:'urn:demo:RefundApplication',revision:1,ontology:'v3',platform:'平台 A',valid:'2026-09-01',aliases:[],values:{'申请编号':'DEMO-A001','提交时间':'2026-10-16 10:00','说明':'示例申请信息'},sources:['退款处理说明.md r2'],relations:['需要 → 申请材料'],deleted:false,merged:false},
  ];
  const m={tab:'records',kind:'实体',showDeleted:false,operations:[],review:'pending',query:'',quality:'extraction',keep:'M001',drop:'M002',winner:'',ack:false,ablationView:'config',experiment:'规则资料 · 检索能力对照',variants:new Set(['hybrid','graph','ontology','full'])};
  const entity=id=>entities.find(e=>e.id===id);
  const clone=value=>JSON.parse(JSON.stringify(value));
  const enabledEntities=()=>hasOntology()?entities.filter(e=>!e.merged&&(m.showDeleted||!e.deleted)):[];
  const tabs=(active)=>`<div class="maint-tabs" aria-label="台账工作区">${[['records','知识维护'],['governance','融合与消歧'],['history','操作历史']].map(([id,label])=>`<button data-act="maint-tab" data-tab="${id}" class="${active===id?'active':''}">${label}</button>`).join('')}</div>`;
  function renderRecords(){
    if(m.tab==='governance')return tabs(m.tab)+governance();
    if(m.tab==='history')return tabs(m.tab)+history();
    const f=currentFilter();
    const rows=enabledEntities().filter(e=>(!f.platform||e.platform===f.platform)&&(!m.query||[e.name,e.id,...e.aliases].some(v=>v.includes(m.query))));
    return `${tabs(m.tab)}${chips()}<div class="toolbar"><div class="maint-kinds">${['实体','关系','属性','原文片段'].map(k=>`<button data-act="maint-kind" data-kind="${k}" class="${m.kind===k?'selected':''}">${k}</button>`).join('')}</div><button class="primary push" data-act="maint-new-entity" ${!hasOntology()?"disabled":""}>＋ 新增实体</button></div><div class="toolbar"><input data-maint-query="true" aria-label="搜索实体名称或别名" placeholder="搜索名称、别名或记录标识…" value="${esc(m.query)}"><label class="row small"><input type="checkbox" data-maint-deleted="true" ${m.showDeleted?'checked':''}>包含已停用记录</label><button class="link small push" data-act="maint-versions">本体归属与系统历史 →</button></div><div id="maint-rows">${recordTable(rows)}</div><p class="note spaced">实体、关系和属性分别维护。详情中保留来源与修订；融合、别名和撤销集中在同一台账。</p>`;
  }
  function recordTable(rows){
    if(m.kind!=='实体'){
      const items=m.kind==='关系'?rows.flatMap(e=>e.relations.map(r=>({name:e.name+' → '+r,type:'关系',source:e.sources[0],entity:e.id,revision:e.revision}))):m.kind==='属性'?rows.flatMap(e=>Object.entries(e.values).map(([k,v])=>({name:e.name+' · '+k+'：'+v,type:'属性',source:e.sources[0],entity:e.id,revision:e.revision}))):[{name:'退款处理说明 · 第 2 节',type:'原文片段',source:'退款处理说明.md r2',entity:'A001',revision:2}];
      return `<div class="table-wrap"><table class="maint-table" aria-label="${m.kind}列表"><thead><tr><th>记录</th><th>种类</th><th>来源</th><th>修订</th><th></th></tr></thead><tbody>${items.map(i=>`<tr><td>${esc(i.name)}</td><td>${i.type}</td><td class="muted small">${esc(i.source)}</td><td class="mono">r${i.revision}</td><td><button class="link small" data-act="maint-open-entity" data-id="${i.entity}">查看所属对象</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    return `<div class="table-wrap"><table class="maint-table" aria-label="实体维护清单"><thead><tr><th>实体名称</th><th>类型</th><th>属性摘要</th><th>来源</th><th>修订 / 本体依据</th><th></th></tr></thead><tbody>${rows.length?rows.map(e=>`<tr><td><button class="link file-title" data-act="maint-open-entity" data-id="${e.id}">${esc(e.name)}</button><span class="file-sub">${e.id}${e.aliases.length?' · 别名 '+e.aliases.map(esc).join('、'):''}</span>${e.deleted?badge('已停用'):''}</td><td>${e.type}</td><td><div class="maint-field-summary">${Object.entries(e.values).slice(0,2).map(([k,v])=>`${esc(k)}：${esc(v)}`).join('<br>')}</div></td><td class="small">${e.sources.length} 份来源</td><td><span class="mono">知识 r${e.revision}</span><span class="file-sub">依据本体 ${e.ontology}</span></td><td><button class="ghost small" data-act="maint-edit-entity" data-id="${e.id}" ${e.deleted?'disabled':''}>编辑</button></td></tr>`).join(''):'<tr><td colspan="6"><div class="empty">当前范围没有实体记录。</div></td></tr>'}</tbody></table></div><div class="table-foot"><span>当前演示清单 ${rows.length} 个实体</span><span>点击对象查看属性、关系、原文与历史</span></div>`;
  }
  function governance(){
    const a=entity(m.keep),b=entity(m.drop);
    return `<div class="maint-governance"><div class="toolbar"><input aria-label="查找名称或别名" id="maint-resolve" placeholder="按名称或别名查重，例如：华星"><button data-act="maint-resolve">查重</button><button class="ghost push" data-act="maint-review">待消歧复核 ${m.review==='pending'?'· 1':''}</button></div><section class="surface"><div class="maint-candidate-head"><div><span class="maint-subtle-label">疑似重复组 · 示例候选</span><h3 style="margin-top:8px">同一商户的两种写法</h3></div>${badge(a.merged||b.merged?'已处理':'待人工核对',a.merged||b.merged?'good':'warn')}</div><p class="muted small">评分和同名用于发现候选；是否属于同一对象，仍需核对类型、范围、来源和属性。</p><div class="maint-pair"><article><span class="maint-subtle-label">保留实体</span><h3>${esc(a.name)}</h3><p>${a.id} · ${a.type} · 知识 r${a.revision} · 本体 ${a.ontology}</p><p>${a.platform} · 2026-01-01 起</p><button class="link small" data-act="maint-open-entity" data-id="${a.id}">核对属性与来源 →</button></article><article><span class="maint-subtle-label">待并入实体</span><h3>${esc(b.name)}</h3><p>${b.id} · ${b.type} · 知识 r${b.revision} · 本体 ${b.ontology}</p><p>${b.platform} · 2026-01-01 起</p><button class="link small" data-act="maint-open-entity" data-id="${b.id}">核对属性与来源 →</button></article></div><div class="maint-compatible"><span>✓ 稳定类型兼容</span><span>✓ 业务区间一致</span><span>✓ 适用范围相同</span></div><div class="row between"><button class="link small" data-act="maint-separate" ${m.review==='separate'?'disabled':''}>确认保持独立</button><button class="primary" data-act="maint-merge-preview" ${a.merged||b.merged?'disabled':''}>比较属性并预览融合 →</button></div></section><p class="note">不同本体发布身份可以在满足兼容条件时融合。合并前明确处理单值属性冲突，保留断言、来源、旧修订和可检查的操作记录。</p></div>`;
  }
  function history(){
    return `<div class="row between spaced"><h3>治理操作记录</h3><span class="muted small">撤销需核对后续修改和预期修订</span></div><div class="table-wrap spaced"><table class="maint-operations" aria-label="治理操作历史"><thead><tr><th>操作</th><th>对象 / 说明</th><th>结果</th><th>操作</th></tr></thead><tbody>${m.operations.length?m.operations.map((op,i)=>`<tr><td>${esc(op.type)}</td><td>${esc(op.label)}<div class="muted">${esc(op.detail||'保留来源与历史记录')}</div></td><td>${badge(op.undone?'已撤销':'已记录',op.undone?'':'good')}</td><td>${op.before&&!op.undone?`<button class="link small" data-act="maint-undo" data-op="${i}">检查并撤销</button>`:'<span class="muted small">保留审计</span>'}</td></tr>`).join(''):'<tr><td colspan="4"><div class="empty">还没有本次演示操作。可以先编辑实体、维护别名或完成一次融合。</div></td></tr>'}</tbody></table></div><p class="note spaced">撤销产生新修订，审计历史继续保留。存在后续依赖冲突时，不能用恢复快照覆盖新修改。</p>`;
  }
  function entityDetails(e,tab){
    const actions=`<div class="maint-object-actions"><button class="primary" data-act="maint-edit-entity" data-id="${e.id}" ${e.deleted?'disabled':''}>编辑实体</button><button data-act="maint-add-relation" data-id="${e.id}" ${e.deleted?'disabled':''}>新增关系</button><button data-act="maint-add-property" data-id="${e.id}" ${e.deleted?'disabled':''}>新增属性</button><button data-act="maint-alias" data-id="${e.id}">别名</button><button class="${e.deleted?'':'danger'}" data-act="${e.deleted?'maint-restore':'maint-delete'}" data-id="${e.id}">${e.deleted?'恢复记录':'软删除…'}</button></div>`;
    let body=`${actions}<div class="row">${badge(e.type)}${badge('知识修订 r'+e.revision)}${badge('依据本体 '+e.ontology)}${e.deleted?badge('已停用','warn'):''}</div><div class="maint-tabs maint-detail-tabs">${['属性','关联','证据','修订历史'].map(t=>`<button data-act="maint-entity-tab" data-id="${e.id}" data-tab="${t}" class="${tab===t?'active':''}">${t}</button>`).join('')}</div>`;
    if(tab==='属性')body+=`<dl class="maint-inspector-grid"><dt>记录标识</dt><dd class="mono">${e.id}</dd><dt>稳定类型</dt><dd class="mono">${esc(e.iri)}</dd><dt>适用范围</dt><dd>${e.platform} · 商户</dd><dt>业务有效期</dt><dd>${e.valid} 起，结束时间未设</dd><dt>别名</dt><dd>${esc(e.aliases.join('、')||'暂无')}</dd></dl><div class="divider"></div><h3>属性值</h3><div class="maint-properties">${Object.entries(e.values).map(([k,v])=>`<div class="maint-property-row"><span>${esc(k)}</span><span>${esc(v)}</span><small>独立属性记录</small></div>`).join('')}</div>`;
    else if(tab==='关联')body+=`<div class="mini-list">${e.relations.map(r=>`<div><span>${esc(r)}</span><button class="link small" data-act="maint-edit-relation" data-id="${e.id}" data-relation="${esc(r)}">维护关系 →</button></div>`).join('')}</div><p class="note spaced">关系有自己的记录标识、预期修订和证据，修改不直接覆盖实体的全部关联。</p>`;
    else if(tab==='证据')body+=`<div class="mini-list">${e.sources.map(s=>`<div><span class="small">${esc(s)}</span><button class="link small" data-act="citation" data-doc="${s.includes('入驻')?'onboard':'refund'}">查看原文 →</button></div>`).join('')}</div><p class="note spaced">多个来源通过断言支持同一规范事实，融合后来源仍分别保留。</p>`;
    else body+=`<div class="timeline-item"><span class="revision-tag">r${e.revision}</span><div><h3>当前知识修订</h3><p>依据本体 ${e.ontology} · ${e.sources.length} 个来源</p></div></div>${e.revision>1?`<div class="timeline-item"><span class="revision-tag">r${e.revision-1}</span><div><h3>历史修订</h3><p>旧记录保留，恢复会创建新修订。</p><button class="link small" data-act="maint-preview-restore" data-id="${e.id}">查看恢复说明 →</button></div></div>`:''}<button class="link small spaced" data-act="version-relations">本体、来源与索引的关系 →</button>`;
    return modal(esc(e.name),'实体实例 · 属性 / 关系 / 来源 / 修订',body,`<button class="link small" data-act="maint-start-merge" data-id="${e.id}">查重与融合 →</button><button data-act="close-dialog">关闭</button>`,true);
  }
  function editEntity(e){
    const isNew=!e;
    return modal(isNew?'新增实体':'编辑实体',isNew?'选择正式本体类型，填写对象信息':`${e.id} · 预期修订 r${e.revision}` ,`<div class="maint-form" data-maint-entity-form="${e?.id||''}" data-revision="${e?.revision||0}"><label>实体名称<input name="entity-name" value="${esc(e?.name||'')}" required></label><label>实体类型<select name="entity-type"><option ${e?.type==='商户'?'selected':''}>商户</option><option ${e?.type==='退款申请'?'selected':''}>退款申请</option></select></label>${!isNew?'<p class="muted small">可选类型来自该记录绑定的本体；跨版本迁移另走受控重分类。</p>':''}<label>联系邮箱<input type="email" name="entity-email" value="${esc(e?.values['联系邮箱']||'')}"></label><label>说明<textarea rows="3" name="entity-note">${esc(e?.values['说明']||'')}</textarea></label><label>修改原因<input name="entity-reason" placeholder="说明这次新增或纠正的依据"></label></div><div class="callout spaced">本草图使用本地示例对象。正式提交仍需本体校验、预期版本、来源与当前权限。</div>`,`<button data-act="close-dialog">取消</button><button class="primary" data-act="maint-save-entity">${isNew?'创建示例实体':'保存新修订'}</button>`);
  }
  function mergeDialog(){
    const a=entity(m.keep),b=entity(m.drop);
    const compatible=a.iri===b.iri&&a.valid===b.valid&&a.platform===b.platform&&!a.deleted&&!b.deleted;
    return modal('融合预览：核对对象与冲突',`${a.name} ← ${b.name}` ,`<div class="maint-pair"><article><span class="maint-subtle-label">保留实体</span><h3>${esc(a.name)}</h3><p>${a.id} · r${a.revision} · 本体 ${a.ontology}</p><p>${a.sources.length} 个来源 · ${a.relations.length} 条关联</p></article><article><span class="maint-subtle-label">并入后停用</span><h3>${esc(b.name)}</h3><p>${b.id} · r${b.revision} · 本体 ${b.ontology}</p><p>${b.sources.length} 个来源 · ${b.relations.length} 条关联</p></article></div><div class="callout spaced">${compatible?'✓ 稳定类型、业务有效区间与适用范围兼容。不同本体版本可在这些条件满足时融合。':'类型、时间或适用范围不兼容，不能提交融合。'}</div><div class="maint-conflict"><h3>单值属性冲突：联系邮箱</h3><p class="muted small">该属性最多保留一个值，请明确选择；来源断言不会因为选值而被抹去。</p><div class="maint-winner"><label><input type="radio" name="maint-winner" value="${a.id}" ${m.winner===a.id?'checked':''}><span>${esc(a.values['联系邮箱'])}<small class="file-sub">保留实体的属性记录</small></span></label><label><input type="radio" name="maint-winner" value="${b.id}" ${m.winner===b.id?'checked':''}><span>${esc(b.values['联系邮箱'])}<small class="file-sub">并入实体的属性记录</small></span></label></div></div><dl class="maint-inspector-grid spaced"><dt>别名</dt><dd>汇集两对象名称与既有别名</dd><dt>关联关系</dt><dd>依治理规则重定向至保留实体</dd><dt>证据</dt><dd>各来源和旧修订分别保留</dd><dt>撤销</dt><dd>记录操作，后续版本/依赖允许时可撤销</dd></dl><label class="maint-ack"><input type="checkbox" name="maint-ack" ${m.ack?'checked':''}>我已核对对象身份、冲突选值和融合影响。</label>`,`<button data-act="close-dialog">返回核对</button><button class="primary" data-act="maint-confirm-merge" ${!compatible||!m.winner||!m.ack?'disabled':''}>确认融合</button>`);
  }
  function renderDialog(d){
    if(!d.kind.startsWith('maint-'))return null;
    const e=entity(d.entityId);
    if(d.kind==='maint-entity'&&e)return entityDetails(e,d.tab||'属性');
    if(d.kind==='maint-edit')return editEntity(e);
    if(d.kind==='maint-merge')return mergeDialog();
    if(d.kind==='maint-alias')return modal('维护别名',e.name,`<label class="small">别名（用逗号分隔）<input id="maint-alias-value" value="${esc(e.aliases.join('，'))}"></label><p class="note spaced">别名只用于候选定位和已确认身份的对齐。同名或多精确匹配需要显式复核。</p>`,`<button data-act="close-dialog">取消</button><button class="primary" data-act="maint-save-alias" data-id="${e.id}">保存别名</button>`);
    if(d.kind==='maint-property')return modal('新增属性',e.name,`<div class="maint-form"><label>本体数据属性<select id="maint-property-name"><option>说明</option><option>状态</option><option>联系邮箱</option></select></label><label>值<input id="maint-property-value"></label></div><p class="muted small spaced">实际表单按本体数据类型和约束生成，此处展示示例字段。</p>`,`<button data-act="close-dialog">取消</button><button class="primary" data-act="maint-save-property" data-id="${e.id}">保存属性</button>`);
    if(d.kind==='maint-relation')return modal(d.existing?'维护关系':'新增关系',e.name,`<div class="maint-form"><label>关系类型<select id="maint-relation-type"><option>提交</option><option>需要</option><option>适用</option></select></label><label>目标实体<select id="maint-relation-target">${entities.filter(v=>v.id!==e.id&&!v.deleted&&!v.merged).map(v=>`<option value="${v.id}">${esc(v.name)} · ${v.type}</option>`).join('')}</select></label><label>来源说明<textarea id="maint-relation-evidence" rows="2" placeholder="填写或选择原文依据"></textarea></label></div><p class="note spaced">服务端仍验证关系端点、domain/range、有效区间及预期修订。</p>`,`<button data-act="close-dialog">取消</button><button class="primary" data-act="maint-save-relation" data-id="${e.id}">保存关系</button>`);
    if(d.kind==='maint-delete')return modal('软删除实体？',e.name,'<p>关联知识与来源断言依服务规则处理，旧修订和审计保留。删除不是物理清除，恢复也需要检查当前版本与依赖。</p>',`<button data-act="close-dialog">取消</button><button class="danger" data-act="maint-confirm-delete" data-id="${e.id}">确认软删除</button>`);
    if(d.kind==='maint-review')return modal('待消歧复核','候选身份需要人工决定',`<div class="maint-review-row"><div><h3>华星商户 / 华星门店</h3><p>同一类型、同一范围，名称相近但联系邮箱不同。</p>${badge(m.review==='pending'?'待复核':m.review==='separate'?'保持独立':m.review==='rejected'?'已驳回':'已合并',m.review==='pending'?'warn':'good')}</div></div><p class="note spaced">“保持独立”保留两个实体；“驳回候选”驳回本次匹配建议，不删除来源。选择合并仍需处理冲突和确认影响。</p>`,`<button data-act="maint-reject">驳回候选</button><div class="row"><button data-act="maint-separate">保持独立</button><button class="primary" data-act="maint-merge-preview">核对后融合</button></div>`,true);
    if(d.kind==='maint-ablation-config')return modal('新建消融实验配置','先冻结输入，再定义单因素对照',`<div class="maint-form"><label>实验名称<input id="maint-experiment-name" value="${esc(m.experiment)}"></label><label>问题集<select><option>规则资料示例 · 3 个固定问题</option></select></label><label>资料与时间范围<input readonly value="示例 manifest：来源 r2 / r1，本体 v4，索引 g12"></label><label>生成配置<input readonly value="相同模型、prompt、top-k 与上下文预算"></label></div><div class="callout spaced">权限与来源撤回资格在执行、读报告和导出时重新检查。冻结资料清单不等于冻结访问权。</div>`,`<button data-act="close-dialog">取消</button><button class="primary" data-act="maint-save-experiment">保存示例配置</button>`);
    if(d.kind==='maint-ablation-case')return modal('逐问题对照','问题、期望证据与实际执行记录',`<h3>${esc(d.question)}</h3><div class="ablation-case"><p>期望证据：来源文档的固定修订与对应条款；需要人工标注。</p><p>逐组展示：命中来源、排序、入模证据、答案、引用、资格排除与降级。</p><p>当前尚未执行实验，不展示虚构回答差异或分数。</p></div><div class="callout spaced">错误类型：漏证据 / 引用不支持 / 平台越界 / 时点错误 / 应拒答未拒答。模型裁判的意见单列，争议交人工复核。</div>`,'<span></span><button data-act="close-dialog">关闭</button>',true);
    return modal('操作详情','示例流程',d.body||'<p>此操作保留修订与来源记录。</p>','<span></span><button data-act="close-dialog">关闭</button>');
  }
  const variants=[
    ['keyword','关键词基线','不使用向量、图邻域与本体扩展','已有检索'],
    ['semantic','向量基线','固定编码器、相同证据范围','已有检索'],
    ['hybrid','混合检索基线','词法与向量融合，不加图上下文','已有检索'],
    ['graph','混合 + 图谱局部增强','范围内实体、邻域与路径实际进入上下文','需接通正常问答'],
    ['ontology','混合 + 本体辅助','明确定义词项扩展和本体上下文','已有部分辅助'],
    ['full','完整增强 / 去图 / 去本体','逐次只关闭一个指定环节','新增实验契约'],
    ['community','社区 Global / DRIFT','需要范围合格的社区报告','后续能力'],
  ];
  function quality(){
    const head=`<div class="maint-tabs" aria-label="评测类型"><button data-act="maint-quality-tab" data-tab="extraction" class="${m.quality==='extraction'?'active':''}">抽取质量</button><button data-act="maint-quality-tab" data-tab="ablation" class="${m.quality==='ablation'?'active':''}">检索与问答消融</button></div>`;
    return head+(m.quality==='extraction'?originalQuality():ablation());
  }
  function ablation(){
    if(m.ablationView==='report')return ablationReport();
    return `<div class="ablation-head"><div><h2>${esc(m.experiment)}</h2><p>固定资料与问题，一次改变一个能力，核对证据与回答差异。</p></div><button data-act="maint-ablation-new">新建实验配置</button></div><div class="ablation-status">执行后端待接通。本页展示实验配置与报告结构，当前没有真实实验指标。</div><div class="ablation-freeze"><div><small>资料 / 本体</small><span>来源固定修订 · 本体 v4</span></div><div><small>索引 / 编码器</small><span>g12 · 相同编码器配置</span></div><div><small>问题 / 范围</small><span>固定 3 问 · 显式平台与时点</span></div><div><small>生成模型</small><span>各组固定相同模型与 prompt</span></div><div><small>预算</small><span>相同 top-k 与上下文预算</span></div><div><small>测量条件</small><span>记录缓存冷热、延迟与用量</span></div></div><div class="surface"><div class="ablation-panel-title"><h3>选择对照组</h3><span class="muted small">能力状态与实验状态分别记录</span></div><div class="table-wrap"><table class="ablation-table"><thead><tr><th>对照组</th><th>真正改变的环节</th><th>接入状况</th></tr></thead><tbody>${variants.map(([id,name,description,availability])=>`<tr class="${id==='community'?'ablation-disabled':''}"><td><label class="ablation-checkbox"><input type="checkbox" data-maint-variant="${id}" ${m.variants.has(id)?'checked':''} ${id==='community'?'disabled':''}>${name}</label></td><td>${description}</td><td>${badge(availability,id==='community'?'':id==='keyword'||id==='semantic'||id==='hybrid'?'good':'warn')}</td></tr>`).join('')}</tbody></table></div><div class="row between spaced"><p class="muted small">关闭图谱需停止图证据读取和入模，画布显隐不参与实验。</p><button class="primary" data-act="maint-ablation-report">预览报告结构 →</button></div></div><p class="note spaced">融合/消歧前后对照使用隔离的实验派生图，不撤销生产融合记录；先完成查询侧对照，再接构建侧实验。</p>`;
  }
  function ablationReport(){
    const selected=variants.filter(([id])=>m.variants.has(id));
    return `<div class="ablation-head"><div><button class="link small" data-act="maint-ablation-back">← 返回实验配置</button><h2 style="margin-top:9px">对照报告 · 尚未运行</h2><p>${esc(m.experiment)}</p></div>${badge('报告结构预览','warn')}</div><div class="ablation-report"><section class="surface"><h3>证据、质量与开销</h3><p class="muted small">没有实测或标注时保留“待测”，不记成 0 或满分。</p><div class="table-wrap spaced"><table class="ablation-table" aria-label="消融报告结构"><thead><tr><th>对照组</th><th>证据 Recall@k</th><th>引用支持性</th><th>范围 / 时点</th><th>P50 / P95</th><th>用量 / 成本</th></tr></thead><tbody>${selected.map(([,name])=>`<tr><td>${name}</td><td class="ablation-metric-pending">待标注 / 运行</td><td class="ablation-metric-pending">待核对</td><td class="ablation-metric-pending">待核对</td><td class="ablation-metric-pending">待测</td><td class="ablation-metric-pending">待收据</td></tr>`).join('')}</tbody></table></div></section><section class="surface"><h3>逐问题配对核对</h3>${['退款申请需要哪些材料？','平台 A 与平台 B 的条款有哪些差异？','历史资料能否支持当前规则结论？'].map((question,i)=>`<div class="ablation-case"><div class="row between"><span>${i+1}. ${question}</span><button class="link small" data-act="maint-ablation-case" data-question="${question}">看逐组证据 →</button></div><p>命中来源与修订 / 实际策略 / 答案与引用 / 错误或拒答原因</p></div>`).join('')}</section><p class="note">每组都记录真实执行和降级情况；失去权限或被撤回的来源不会因为旧 manifest 而继续暴露。</p></div>`;
  }
  function recordOperation(type,label,detail,before){
    const expected=before?Object.fromEntries(before.map(v=>[v.id,entity(v.id)?.revision])):null;
    m.operations.unshift({type,label,detail,before,expected,undone:false});
  }
  function revise(e,apply,type='编辑实体',detail='保存为新知识修订'){
    const before=clone(e);apply();e.revision++;recordOperation(type,e.name,detail,[before]);
  }
  function handleAction(act,b){
    if(act==='fact-detail'){const chosen=entities.find(e=>e.name===b.dataset.fact)||entity('A001');openDialog('maint-entity',{entityId:chosen.id});return true;}
    if(!act.startsWith('maint-'))return false;
    const e=entity(b.dataset.id);
    switch(act){
      case'maint-tab':m.tab=b.dataset.tab;render();break;
      case'maint-kind':m.kind=b.dataset.kind;render();break;
      case'maint-open-entity':openDialog('maint-entity',{entityId:e.id});break;
      case'maint-entity-tab':openDialog('maint-entity',{entityId:e.id,tab:b.dataset.tab});break;
      case'maint-new-entity':openDialog('maint-edit',{});break;
      case'maint-edit-entity':openDialog('maint-edit',{entityId:e.id});break;
      case'maint-save-entity':{
        const form=$('dialog-host').querySelector('[data-maint-entity-form]');const value=name=>form.querySelector(`[name="${name}"]`).value.trim();
        const name=value('entity-name');if(!name){toast('请填写实体名称。');break;}const current=entity(form.dataset.maintEntityForm);
        if(current){if(current.revision!==Number(form.dataset.revision)){toast('预期修订已变化，请重新核对。');break;}revise(current,()=>{current.name=name;current.type=value('entity-type');current.iri=current.type==='商户'?'urn:demo:Merchant':'urn:demo:RefundApplication';if(value('entity-email'))current.values['联系邮箱']=value('entity-email');if(value('entity-note'))current.values['说明']=value('entity-note');});}
        else {const type=value('entity-type');const item={id:'E'+String(entities.length+1).padStart(3,'0'),name,type,iri:type==='商户'?'urn:demo:Merchant':'urn:demo:RefundApplication',revision:1,ontology:'v'+state.currentOntology,platform:'平台 A',valid:'2026-01-01',aliases:[],values:{'说明':value('entity-note')||'人工新增示例对象'},sources:['人工录入与修改说明'],relations:[],deleted:false,merged:false};if(value('entity-email'))item.values['联系邮箱']=value('entity-email');entities.push(item);recordOperation('新增实体',name,'示例对象创建，后续仍需真实来源与本体校验');}
        closeDialog();render();toast('示例实体已保存，原修订继续保留。');break;
      }
      case'maint-alias':openDialog('maint-alias',{entityId:e.id});break;
      case'maint-save-alias':revise(e,()=>{e.aliases=[...new Set($('maint-alias-value').value.split(/[,，]/).map(v=>v.trim()).filter(Boolean))];},'维护别名');closeDialog();render();toast('已保存示例别名。');break;
      case'maint-add-property':openDialog('maint-property',{entityId:e.id});break;
      case'maint-save-property':{const name=$('maint-property-name').value,value=$('maint-property-value').value.trim();if(!value){toast('请填写属性值。');break;}revise(e,()=>{e.values[name]=value;},'新增/修订属性');closeDialog();render();toast('示例属性已保存。');break;}
      case'maint-add-relation':openDialog('maint-relation',{entityId:e.id});break;
      case'maint-edit-relation':openDialog('maint-relation',{entityId:e.id,existing:b.dataset.relation});break;
      case'maint-save-relation':{const rel=$('maint-relation-type').value,target=entity($('maint-relation-target').value),existing=state.dialog.existing;if(!target){toast('请先选择有效目标实体。');break;}revise(e,()=>{if(existing)e.relations=e.relations.filter(v=>v!==existing);e.relations.push(rel+' → '+target.name);},'关系维护');closeDialog();render();toast('示例关系已保存，真实服务仍需端点与本体校验。');break;}
      case'maint-delete':openDialog('maint-delete',{entityId:e.id});break;
      case'maint-confirm-delete':revise(e,()=>{e.deleted=true;},'软删除');closeDialog();render();toast('示例实体已停用，旧修订保留。');break;
      case'maint-restore':revise(e,()=>{e.deleted=false;},'恢复记录');closeDialog();render();toast('示例恢复已产生新修订。');break;
      case'maint-start-merge':m.tab='governance';closeDialog();navigate('records');break;
      case'maint-resolve':m.tab='governance';toast('示例查重找到名称/别名相近的两个候选，请继续核对身份。');break;
      case'maint-merge-preview':m.winner='';m.ack=false;openDialog('maint-merge');break;
      case'maint-confirm-merge':{
        const a=entity(m.keep),drop=entity(m.drop);if(!m.winner||!m.ack){toast('请先选择冲突属性并核对影响。');break;}
        if(a.iri!==drop.iri||a.valid!==drop.valid||a.platform!==drop.platform){toast('类型、业务区间或适用范围不兼容。');break;}
        const before=clone([a,drop]);const winner=entity(m.winner).values['联系邮箱'];a.aliases=[...new Set([...a.aliases,drop.name,...drop.aliases])];a.sources=[...new Set([...a.sources,...drop.sources])];a.relations=[...new Set([...a.relations,...drop.relations])];a.values['联系邮箱']=winner;a.revision++;drop.merged=true;drop.revision++;m.review='merged';recordOperation('实体融合',a.name+' ← '+drop.name,'冲突属性显式选值；别名、来源和关系保留',before);closeDialog();m.tab='records';render();toast('示例融合完成，可在操作历史检查并撤销。');break;
      }
      case'maint-review':openDialog('maint-review');break;
      case'maint-separate':m.review='separate';recordOperation('消歧复核','华星商户 / 华星门店','保持独立，不删除任一对象或来源');closeDialog();render();toast('示例复核结果：保持独立。');break;
      case'maint-reject':m.review='rejected';recordOperation('消歧复核','华星商户 / 华星门店','驳回本次候选建议，保留来源');closeDialog();render();toast('已驳回示例候选建议。');break;
      case'maint-undo':{const op=m.operations[Number(b.dataset.op)];if(!op||op.undone||!op.before)break;if(op.before.some(v=>entity(v.id)?.revision!==op.expected[v.id])){toast('相关对象已有后续修订，不能覆盖新修改；请先核对依赖。');break;}for(const prior of op.before){const current=entity(prior.id),next=current.revision+1;Object.assign(current,clone(prior),{revision:next});}op.undone=true;if(op.type==='实体融合')m.review='pending';recordOperation('撤销'+op.type,op.label,'恢复为新修订，保留原操作审计');render();toast('示例操作已撤销为新修订。');break;}
      case'maint-preview-restore':openDialog('maint-simple',{body:'<p>选择明确的历史修订，核对当前预期版本和关联影响；正式恢复会产生新修订，不覆写旧记录。</p>'});break;
      case'maint-versions':openDialog('maint-simple',{body:'<p>知识台账默认包含全部本体归属。可独立选择本体发布身份和系统历史时点；业务生效时点另外设置。</p><button class="link small spaced" data-act="version-relations">查看版本关系 →</button>'});break;
      case'maint-quality-tab':m.quality=b.dataset.tab;render();break;
      case'maint-ablation-new':openDialog('maint-ablation-config');break;
      case'maint-save-experiment':m.experiment=$('maint-experiment-name').value.trim()||m.experiment;closeDialog();render();toast('已保存示例配置；当前没有实际运行指标。');break;
      case'maint-ablation-report':if(!m.variants.size){toast('请至少选择一个对照组。');break;}m.ablationView='report';render();break;
      case'maint-ablation-back':m.ablationView='config';render();break;
      case'maint-ablation-case':openDialog('maint-ablation-case',{question:b.dataset.question});break;
    }
    return true;
  }
  function handleChange(target){
    if(target.name==='maint-winner'){m.winner=target.value;const button=$('dialog-host').querySelector('[data-act="maint-confirm-merge"]');if(button)button.disabled=!m.winner||!m.ack;return true;}
    if(target.name==='maint-ack'){m.ack=target.checked;const button=$('dialog-host').querySelector('[data-act="maint-confirm-merge"]');if(button)button.disabled=!m.winner||!m.ack;return true;}
    if(target.dataset.maintDeleted!==undefined){m.showDeleted=target.checked;render();return true;}
    if(target.dataset.maintVariant){if(target.checked)m.variants.add(target.dataset.maintVariant);else m.variants.delete(target.dataset.maintVariant);return true;}
    return false;
  }
  function handleInput(target){if(target.dataset.maintQuery!==undefined){m.query=target.value;const f=currentFilter(),rows=enabledEntities().filter(e=>(!f.platform||e.platform===f.platform)&&(!m.query||[e.name,e.id,...e.aliases].some(v=>v.includes(m.query))));$('maint-rows').innerHTML=recordTable(rows);return true;}return false;}
  return {renderRecords,renderQuality:quality,handleAction,handleChange,handleInput,renderDialog};
})();
