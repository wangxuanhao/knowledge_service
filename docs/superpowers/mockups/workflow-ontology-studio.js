const studioUI = (() => {
  'use strict';
  const NS = 'https://example.org/business#';
  const copy = value => JSON.parse(JSON.stringify(value));
  const initial = {
    classes: [
      {id:'BusinessObject',label:'业务对象',description:'业务处理中可被识别和引用的对象。',parent:'',color:'sage',properties:[{id:'objectName',label:'名称',datatype:'string',required:true,maxCount:1,description:'对象的显示名称。'}]},
      {id:'Merchant',label:'商户',description:'提交业务申请的经营主体。',parent:'BusinessObject',color:'teal',properties:[{id:'merchantCode',label:'商户编号',datatype:'string',required:true,maxCount:1,description:'业务系统分配的商户编号。'},{id:'phone',label:'联系电话',datatype:'string',required:false,maxCount:1,description:'旧版联系电话，已停用。',inactive:true}]},
      {id:'Application',label:'申请',description:'商户为办理一项业务提交的申请。',parent:'BusinessObject',color:'teal',properties:[{id:'submittedAt',label:'提交时间',datatype:'dateTime',required:true,maxCount:1,description:'申请提交的业务时间。'}]},
      {id:'RefundApplication',label:'退款申请',description:'申请退还一笔交易款项的业务申请。',parent:'Application',color:'green',properties:[{id:'refundAmount',label:'退款金额',datatype:'decimal',required:true,maxCount:1,description:'退款申请的金额，以元计。'}]},
      {id:'Rule',label:'规则',description:'约定适用条件、处理方式与有效期的业务规则。',parent:'',color:'amber',properties:[{id:'effectiveDate',label:'生效日期',datatype:'date',required:false,maxCount:1,description:'规则开始适用的日期。'}]},
      {id:'Material',label:'申请材料',description:'办理申请时提供的证明材料。',parent:'',color:'blue',properties:[{id:'materialTitle',label:'材料名称',datatype:'string',required:true,maxCount:1,description:'证明材料的名称。'}]}
    ],
    relations: [
      {id:'submits',label:'提交',description:'商户提交业务申请。',from:'Merchant',to:'Application',maxCount:null,inverseMaxCount:1,required:false},
      {id:'governedBy',label:'遵循',description:'申请遵循适用的业务规则。',from:'Application',to:'Rule',maxCount:null,inverseMaxCount:null,required:false},
      {id:'requires',label:'需要材料',description:'退款申请关联所需的申请材料。',from:'RefundApplication',to:'Material',maxCount:null,inverseMaxCount:null,required:false}
    ]
  };
  initial.classes.forEach(c=>{c.parents=c.parent?[c.parent]:[];delete c.parent;});
  const parents=c=>c?.parents||[];
  const old = copy(initial);
  old.classes.find(c=>c.id==='Merchant').properties.find(p=>p.id==='phone').inactive=false;
  old.classes.find(c=>c.id==='Merchant').properties.find(p=>p.id==='phone').description='商户用于联系的电话号码。';
  old.classes.find(c=>c.id==='RefundApplication').properties=[];
  const older=copy(old);older.classes.find(c=>c.id==='Application').description='业务申请记录。';
  let versions=[
    {id:'ont-20261016-v4-a',version:4,date:'2026-10-16 13:30',summary:'增加退款金额；停用联系电话',draft:'d6',data:copy(initial)},
    {id:'ont-20261015-v3-b',version:3,date:'2026-10-15 17:20',summary:'修订申请说明 · 标注修订',draft:'d5',data:copy(old)},
    {id:'ont-20261015-v3-a',version:3,date:'2026-10-15 10:00',summary:'发布申请、规则与材料结构',draft:'d4',data:copy(older)}
  ];
  let draft=copy(initial),base=versions[0],mode='draft',viewVersion=null,draftId='d7';
  let selection={kind:'class',id:'RefundApplication'},tab='classes',query='',display='cards',graphTab='graph',tray=false;
  let expanded=new Set(['RefundApplication']),past=[],future=[],validation=null,validationFingerprint='',counter=8,installed=false,drag=null,skipClick=false;
  let positions={BusinessObject:[330,68],Merchant:[110,238],Application:[330,238],Rule:[550,238],RefundApplication:[220,426],Material:[520,426]};
  const data=()=>mode==='draft'?draft:(viewVersion||versions[0]).data;
  const editable=()=>mode==='draft';
  const cls=(id,d=data())=>d.classes.find(c=>c.id===id);
  const name=id=>cls(id)?.label||id;
  const fragment=id=>encodeURIComponent(id).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());
  const iristr=id=>NS+fragment(id);
  const safeFragment=id=>!!id&&!/[\s\u0000-\u001f\u007f#/?]/u.test(id);
  const btn=(act,label,extra='',style='')=>`<button data-act="studio-${act}" ${extra}${style?` class="${style}"`:''}>${label}</button>`;
  const choices=(list,value)=>list.map(([id,label])=>`<option value="${esc(id)}" ${(Array.isArray(value)?value.includes(id):String(value)===String(id))?'selected':''}>${esc(label)}</option>`).join('');
  const field=(label,key,value,opts={})=>`<label class="studio-field">${esc(label)}${opts.textarea?`<textarea rows="3" data-field="studio-${key}" ${opts.readonly?'readonly':''}>${esc(value)}</textarea>`:opts.options?`<select data-field="studio-${key}" ${opts.multiple?'multiple size="4"':''} ${opts.readonly?'disabled':''}>${choices(opts.options,value)}</select>`:`<input data-field="studio-${key}" value="${esc(value??'')}" ${opts.readonly?'readonly':''} ${opts.type?`type="${opts.type}"`:''} ${opts.placeholder?`placeholder="${esc(opts.placeholder)}"`:''}>`}${opts.hint?`<span class="studio-field-note">${opts.hint}</span>`:''}</label>`;
  const parentField=c=>field('父类（可多选）','class-parents',parents(c),{options:data().classes.filter(x=>x.id!==c.id).map(x=>[x.id,x.label]),multiple:true,readonly:!editable(),hint:'按住 Ctrl / ⌘ 多选；取消选择移除继承。'});
  const check=(key,label,value,disabled=false)=>`<label class="studio-check"><input type="checkbox" data-field="studio-${key}" ${value?'checked':''} ${disabled?'disabled':''}>${esc(label)}</label>`;
  const kindLabel=k=>({class:'类',relation:'关系类型',attribute:'数据属性',inheritance:'继承'}[k]||k);
  const selectedLabel=()=>selection.kind==='relation'?data().relations.find(r=>r.id===selection.id)?.label:selection.kind==='attribute'?cls(selection.owner)?.properties.find(p=>p.id===selection.id)?.label:name(selection.id);
  function repaint(){render();}
  function invalidate(){validation=null;validationFingerprint='';}
  function mutate(label,fn){if(!editable()){toast('已发布版本只读，请先创建草案。');return false;}past.push({data:copy(draft),label});if(past.length>50)past.shift();future=[];fn(draft);invalidate();return true;}
  function differences(a,b){
    const out=[];const compare=(kind,before,after,owner)=>{
      const ids=new Set([...before,...after].map(x=>x.id));
      for(const id of ids){const x=before.find(v=>v.id===id),y=after.find(v=>v.id===id);const title=y?.label||x?.label||id;
        if(!x||!y){out.push({kind,id,owner,title,field:!x?'新增':'移除',before:x?'已有术语':'—',after:y?'新增术语':'—'});continue;}
        const fields=kind==='class'?['label','description','parents']:kind==='relation'?['label','description','from','to','maxCount','inverseMaxCount','required']:['label','description','datatype','required','maxCount','values','inactive'];
        const labels={label:'名称',description:'说明',parents:'父类',from:'起点',to:'终点',maxCount:'最大数量',inverseMaxCount:'反向最大数量',required:'必填',datatype:'值类型',values:'枚举',inactive:'停用状态'};
        for(const f of fields)if(JSON.stringify(x[f]??null)!==JSON.stringify(y[f]??null))out.push({kind,id,owner,title,field:labels[f],before:format(x[f],f),after:format(y[f],f)});
        if(kind==='class')compare('attribute',x.properties||[],y.properties||[],id);
      }
    };compare('class',a.classes,b.classes);compare('relation',a.relations,b.relations);return out;
  }
  function format(v,k){if(k==='required')return v?'必填':'可选';if(k==='inactive')return v?'已停用':'启用';if(k==='maxCount'||k==='inverseMaxCount')return v??'不限';if(k==='parents')return (v||[]).map(id=>name(id)).join('、')||'无';if(k==='from'||k==='to')return name(v)||'无';return Array.isArray(v)?v.join('、'):v||'—';}
  const changes=()=>differences(base.data,draft);
  function localChecks(){
    const errors=[];const d=draft;const iris=new Set();
    for(const c of d.classes){if(!c.label.trim())errors.push({kind:'class',id:c.id,message:'类名称不能为空。'});if(iris.has(c.id))errors.push({kind:'class',id:c.id,message:'术语 IRI 重复。'});iris.add(c.id);
      const visit=(id,path)=>{if(path.has(id)){errors.push({kind:'class',id:c.id,message:'父类形成继承环，请调整父类。'});return;}const p=cls(id,d);if(!p){errors.push({kind:'class',id:c.id,message:'所选父类不存在。'});return;}const next=new Set(path);next.add(id);parents(p).forEach(id=>visit(id,next));};parents(c).forEach(id=>visit(id,new Set([c.id])));
      for(const p of c.properties){if(!p.label.trim())errors.push({kind:'attribute',owner:c.id,id:p.id,message:'属性名称不能为空。'});if(p.maxCount!==null&&(!Number.isInteger(p.maxCount)||p.maxCount<1))errors.push({kind:'attribute',owner:c.id,id:p.id,message:'最大数量必须是大于零的整数或留空。'});}
    }
    for(const r of d.relations)if(!r.label.trim()||!cls(r.from,d)||!cls(r.to,d))errors.push({kind:'relation',id:r.id,message:'关系名称和起止类型必须完整。'});
    return errors;
  }
  function validate(){validation=localChecks();validationFingerprint=JSON.stringify(draft);return validation;}
  function inherited(c,d=data(),seen=new Set()){if(!c||seen.has(c.id))return [];seen.add(c.id);const rows=parents(c).flatMap(id=>{const p=cls(id,d);return p?[...p.properties.filter(v=>!v.inactive).map(v=>({...v,owner:p.id})),...inherited(p,d,seen)]:[];});return rows.filter((p,i)=>rows.findIndex(x=>x.id===p.id)===i);}
  function props(c){return c.properties.filter(p=>!p.inactive);}
  function propRows(c,full=false){const list=full?props(c):props(c).slice(0,2);return list.map(p=>`<button class="studio-prop-line" data-act="studio-attribute" data-owner="${esc(c.id)}" data-id="${esc(p.id)}"><span>${esc(p.label)}${p.required?'<i aria-label="必填">*</i>':''}</span><code>${esc(p.datatype)}</code></button>`).join('')+(!props(c).length?'<span class="studio-no-props">暂无直接属性</span>':'')+(inherited(c).length?`<div class="studio-inherited">↳ ${inherited(c).length} 项继承属性 · ${esc(parents(c).map(id=>name(id)).join('、'))}</div>`:'');}
  function catalog(){
    let entries='';const d=data();const match=x=>!query||(x.label+' '+x.id+' '+(x.properties||[]).map(p=>p.label).join(' ')).toLowerCase().includes(query.toLowerCase());
    if(tab==='classes')entries=d.classes.filter(match).map(c=>`<article class="studio-catalog-card ${selection.id===c.id&&['class','inheritance'].includes(selection.kind)?'selected':''}"><div class="studio-catalog-title"><button class="studio-expand" data-act="studio-expand" data-id="${esc(c.id)}" aria-expanded="${expanded.has(c.id)}" aria-label="${expanded.has(c.id)?'收起':'展开'}${esc(c.label)}">${expanded.has(c.id)?'⌄':'›'}</button><button class="studio-catalog-select" data-act="studio-select" data-id="${esc(c.id)}"><span class="studio-type-mark ${c.color||'teal'}">◇</span><strong>${esc(c.label)}</strong><small>${props(c).length}</small></button></div>${expanded.has(c.id)?`<div class="studio-catalog-props">${propRows(c,true)}</div>`:''}</article>`).join('');
    if(tab==='relations')entries=d.relations.filter(match).map(r=>`<button class="studio-catalog-relation ${selection.kind==='relation'&&selection.id===r.id?'selected':''}" data-act="studio-edge" data-id="${esc(r.id)}"><strong>↗ ${esc(r.label)}</strong><span>${esc(name(r.from))} → ${esc(name(r.to))}</span><small>${r.maxCount===1?'目标 ≤ 1':'目标多值'} · ${r.required?'必需':'可选'}</small></button>`).join('');
    if(tab==='attributes')entries=d.classes.flatMap(c=>c.properties.map(p=>({...p,owner:c.id}))).filter(match).map(p=>`<button class="studio-catalog-relation ${selection.kind==='attribute'&&selection.id===p.id?'selected':''}" data-act="studio-attribute" data-id="${esc(p.id)}" data-owner="${esc(p.owner)}"><strong>${esc(p.label)} ${p.inactive?'<span class="studio-retired">停用</span>':''}</strong><span>${esc(name(p.owner))} · xsd:${esc(p.datatype)}</span><small>${p.required?'必填':'可选'} · ${p.maxCount?`最多 ${p.maxCount} 个`:'多值'}</small></button>`).join('');
    return `<aside class="studio-catalog" aria-label="本体术语目录"><div class="studio-catalog-head"><h3>术语目录</h3>${btn('add-class','＋','aria-label="添加类" '+(!editable()?'disabled':''),'studio-icon')}</div><label class="studio-search"><span aria-hidden="true">⌕</span><input id="studio-search" data-field="studio-search" value="${esc(query)}" placeholder="搜索类型、属性…" aria-label="搜索本体术语"></label><div class="studio-catalog-tabs" role="tablist">${[['classes','类',d.classes.length],['relations','关系',d.relations.length],['attributes','属性',d.classes.reduce((n,c)=>n+c.properties.length,0)]].map(([k,t,n])=>btn('tab',`${t} <span>${n}</span>`,`data-tab="${k}" role="tab" aria-selected="${tab===k}"`,tab===k?'active':'')).join('')}</div><div class="studio-catalog-scroll">${entries||'<div class="studio-empty-inline">没有匹配术语</div>'}</div><div class="studio-catalog-bottom">${btn(tab==='relations'?'add-relation':tab==='attributes'?'add-attribute':'add-class',tab==='relations'?'＋ 添加关系':tab==='attributes'?'＋ 添加属性':'＋ 添加类',!editable()?'disabled':'')}<button class="studio-discovery" data-act="studio-candidates">发现候选 <span>3</span> →</button></div></aside>`;
  }
  function diagram(){
    const d=data();const edges=d.relations.map(r=>({...r,kind:'relation'})).concat(d.classes.flatMap(c=>parents(c).map(parent=>({id:c.id,label:'继承',from:c.id,to:parent,kind:'inheritance'}))));
    const svg=edges.map(r=>{const a=positions[r.from]||[330,250],b=positions[r.to]||[330,250];const selected=selection.id===r.id&&selection.kind===r.kind;const lx=(a[0]+b[0])/2,ly=(a[1]+b[1])/2,dx=b[0]-a[0],dy=b[1]-a[1],distance=Math.hypot(dx,dy)||1;const inset=display==='circles'?37/distance:Math.min(86/(Math.abs(dx)||.001),55/(Math.abs(dy)||.001));const fraction=Math.min(.43,inset),path=`M${a[0]+dx*fraction} ${a[1]+dy*fraction} L${b[0]-dx*fraction} ${b[1]-dy*fraction}`,labelWidth=Math.max(42,r.label.length*13+14);return `<g class="studio-edge ${r.kind} ${selected?'selected':''}" data-act="studio-edge" data-id="${esc(r.id)}" data-kind="${r.kind}" role="button" tabindex="0" aria-label="${esc(name(r.from)+' '+r.label+' '+name(r.to))}"><path class="studio-edge-hit" d="${path}"/><path class="studio-edge-line" d="${path}" marker-end="url(#studio-${r.kind==='inheritance'?'inherit':'arrow'})"/><rect x="${lx-labelWidth/2}" y="${ly-13}" width="${labelWidth}" height="25" rx="5"/><text x="${lx}" y="${ly+4}" text-anchor="middle">${esc(r.label)}</text></g>`;}).join('');
    const nodes=d.classes.map((c,i)=>{const pos=positions[c.id]||(positions[c.id]=[110+(i%3)*220,100+Math.floor(i/3)*170]);const sel=selection.id===c.id&&selection.kind==='class';return `<div class="studio-node ${display==='circles'?'circle':'card'} ${c.color||'teal'} ${sel?'selected':''}" data-studio-node="${esc(c.id)}" style="left:${pos[0]/6.6}%;top:${pos[1]/5.2}%"><button class="studio-node-title" data-act="studio-select" data-id="${esc(c.id)}"><span class="studio-node-symbol">◇</span><strong>${esc(c.label)}</strong>${display==='cards'?`<small>${props(c).length} 属性</small>`:''}</button>${display==='cards'?`<div class="studio-node-props">${propRows(c)}${props(c).length>2?`<button class="studio-more-props" data-act="studio-select" data-id="${esc(c.id)}">另 ${props(c).length-2} 项 →</button>`:''}</div>`:`<span class="studio-circle-caption">${esc(c.label)}</span>`}</div>`;}).join('');
    return `<div class="studio-graph ${display}" id="studio-graph" aria-label="类继承与业务关系图"><svg viewBox="0 0 660 520" preserveAspectRatio="none" aria-label="关系连线"><defs><marker id="studio-arrow" markerWidth="9" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="#628679" stroke-width="1.5"/></marker><marker id="studio-inherit" markerWidth="11" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0 0 L9 5 L0 10 Z" fill="#fff" stroke="#8d9b91"/></marker></defs>${svg}</svg>${nodes}<div class="studio-graph-legend"><span><i></i>业务关系</span><span><i class="dashed"></i>类继承</span></div><div class="studio-layout-tools">${btn('layout','⊞','title="整理画布，不修改本体" aria-label="整理画布"')}${btn('layout-save','⌑','title="保存画布布局" aria-label="保存画布布局"')}</div></div>`;
  }
  function turtle(d=data()){
    const literal=v=>'"'+String(v).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\n/g,'\\n')+'"';
    const lines=['@prefix biz: <'+NS+'> .','@prefix owl: <http://www.w3.org/2002/07/owl#> .','@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .','@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .','@prefix sh: <http://www.w3.org/ns/shacl#> .',''];
    d.classes.forEach(c=>{lines.push(`biz:${fragment(c.id)} a owl:Class ;`, `  rdfs:label ${literal(c.label)}@zh${parents(c).length?' ;':' .'}${parents(c).length?'\n  rdfs:subClassOf '+parents(c).map(id=>'biz:'+fragment(id)).join(', ')+' .':''}`,'');props(c).forEach(p=>{lines.push(`biz:${fragment(p.id)} a owl:DatatypeProperty ;`,`  rdfs:label ${literal(p.label)}@zh ; rdfs:domain biz:${fragment(c.id)} ;`,`  rdfs:range xsd:${p.datatype} .`,`biz:${fragment(c.id)}Shape a sh:NodeShape ; sh:targetClass biz:${fragment(c.id)} ;`,`  sh:property [ sh:path biz:${fragment(p.id)}${p.required?' ; sh:minCount 1':''}${p.maxCount?' ; sh:maxCount '+p.maxCount:''} ] .`,'');});});
    d.relations.forEach(r=>lines.push(`biz:${fragment(r.id)} a owl:ObjectProperty ;`,`  rdfs:label ${literal(r.label)}@zh ;`,`  rdfs:domain biz:${fragment(r.from)} ; rdfs:range biz:${fragment(r.to)} .`,''));return lines.join('\n');
  }
  function graph(){return `<section class="studio-canvas-panel"><div class="studio-canvas-bar"><div class="studio-view-tabs">${btn('graph-tab','图形','data-tab="graph" '+`aria-pressed="${graphTab==='graph'}"`,graphTab==='graph'?'active':'')}${btn('graph-tab','RDF','data-tab="rdf" '+`aria-pressed="${graphTab==='rdf'}"`,graphTab==='rdf'?'active':'')}</div><div class="studio-canvas-options">${graphTab==='graph'?`<label class="studio-display-select"><span class="sr-only">节点展示方式</span><select data-field="studio-display"><option value="cards" ${display==='cards'?'selected':''}>类型卡片</option><option value="circles" ${display==='circles'?'selected':''}>紧凑圆点</option></select></label>${btn('add-relation','＋ 关系',!editable()?'disabled':'','studio-small')}`:`<span class="studio-subtle">Turtle</span>${btn('export','下载 RDF','','studio-small')}`}</div></div>${graphTab==='graph'?diagram():`<div class="studio-source"><div class="studio-source-caption">${badge(editable()?'当前草案':'发布快照',editable()?'warn':'good')}<span>IRI 与标准语义保留</span></div><pre tabindex="0" aria-label="本体 RDF 源码">${esc(turtle())}</pre></div>`}<div class="studio-canvas-foot"><span>${data().classes.length} 个类 · ${data().relations.length} 个关系类型</span><button class="link" data-act="studio-inspector">${esc(selectedLabel()||'术语详情')} →</button></div></section>`;}
  function inspector(){
    const ro=!editable(),c=cls(selection.owner||selection.id);let body='';
    if(selection.kind==='relation'){
      const r=data().relations.find(r=>r.id===selection.id);if(!r)return '<div class="studio-inspector-empty">请选择一个术语。</div>';
      body=`<div class="studio-inspector-flow">${esc(name(r.from))}<span>— ${esc(r.label)} →</span>${esc(name(r.to))}</div>${field('名称','relation-label',r.label,{readonly:ro})}${field('稳定 IRI','relation-iri',iristr(r.id),{readonly:true})}<div class="studio-field-pair">${field('起点类','relation-from',r.from,{options:data().classes.map(c=>[c.id,c.label]),readonly:ro})}${field('终点类','relation-to',r.to,{options:data().classes.map(c=>[c.id,c.label]),readonly:ro})}</div>${field('说明','relation-description',r.description,{textarea:true,readonly:ro})}<div class="studio-section-label">参与和数量约束</div>${check('relation-required','起点至少关联一个目标',r.required,ro)}${field('每个起点的目标数量','relation-max',r.maxCount??'',{type:'number',readonly:ro,placeholder:'留空表示不限'})}${field('每个目标的起点数量','relation-inverse',r.inverseMaxCount??'',{type:'number',readonly:ro,placeholder:'留空表示不限'})}<p class="studio-field-note">关系方向由起点指向终点；多值不代表必填。</p>${!ro?btn('save-relation','保存关系','','primary studio-save'):''}`;
    }else if(selection.kind==='attribute'){
      const p=c?.properties.find(p=>p.id===selection.id);if(!p)return '<div class="studio-inspector-empty">属性不存在。</div>';
      body=`<div class="studio-inspector-flow">${esc(c.label)}<span>的数据属性</span>${p.inactive?badge('已停用','warn'):''}</div>${attributeFields(p,ro)}${!ro?`<div class="studio-inspector-buttons">${btn('save-attribute','保存属性','','primary')}${p.inactive?btn('restore','恢复术语…'):btn('retire','停用…','','danger')}</div>`:''}`;
    }else if(selection.kind==='inheritance'){
      body=c?`<div class="studio-hierarchy-symbol">${esc(c.label)}<span>⇡</span>${esc(parents(c).map(id=>name(id)).join('、'))}</div><p class="studio-semantic-tag">rdfs:subClassOf</p>${field('子类','inherit-child',c.label,{readonly:true})}${parentField(c)}<p class="studio-field-note">继承类的定义与适用属性，不是两条实例记录间的业务关系。</p>${!ro?btn('save-parent','保存继承','','primary studio-save'):''}`:'';
    }else if(c){
      body=`${field('中文名称','class-label',c.label,{readonly:ro})}${field('规范名称','class-name',c.id,{readonly:true})}${field('稳定 IRI','class-iri',iristr(c.id),{readonly:true,hint:'名称调整保留术语身份'})}${field('说明','class-description',c.description,{textarea:true,readonly:ro})}${parentField(c)}${!ro?btn('save-class','保存类型','','primary studio-save'):''}<div class="studio-section-label studio-with-action"><span>直接属性 · ${props(c).length}</span>${btn('add-attribute','＋',`data-owner="${esc(c.id)}" aria-label="为${esc(c.label)}添加属性" ${ro?'disabled':''}`,'studio-icon')}</div><div class="studio-inspector-properties">${propRows(c,true)}</div>${inherited(c).length?`<div class="studio-section-label">继承属性 · ${inherited(c).length}</div>${inherited(c).map(p=>`<button class="studio-inherit-row" data-act="studio-attribute" data-owner="${esc(p.owner)}" data-id="${esc(p.id)}"><span>${esc(p.label)}<small>来自 ${esc(name(p.owner))}</small></span><code>${esc(p.datatype)}</code></button>`).join('')}`:''}${c.properties.some(p=>p.inactive)?`<details class="studio-retired-list"><summary>停用属性</summary>${c.properties.filter(p=>p.inactive).map(p=>`<button class="link" data-act="studio-attribute" data-owner="${esc(c.id)}" data-id="${esc(p.id)}">${esc(p.label)} →</button>`).join('')}</details>`:''}`;
    }
    return `<div class="studio-inspector-content" data-studio-form><div class="studio-inspector-heading"><span class="studio-kicker">${kindLabel(selection.kind)}</span>${badge(ro?'只读':'草案内编辑',ro?'':'good')}</div><h3>${esc(selectedLabel())}</h3>${body}<div class="studio-governance-links">${btn('governance-entry','高级约束 / 源码 →','data-entry="constraints"')}${btn('governance-entry','关联知识与重分类 →','data-entry="knowledge"')}</div></div>`;
  }
  function attributeFields(p,ro=false){return `${field('属性名称','attr-label',p.label,{readonly:ro})}${field('稳定 IRI','attr-iri',iristr(p.id),{readonly:true})}${field('值类型','attr-datatype',p.datatype,{options:['string','integer','decimal','double','date','dateTime','boolean'].map(t=>[t,'xsd:'+t]),readonly:ro})}${field('说明','attr-description',p.description||'',{textarea:true,readonly:ro})}<div class="studio-section-label">约束</div>${check('attr-required','必填 · minCount = 1',p.required,ro)}${field('最大数量','attr-max',p.maxCount??'',{type:'number',readonly:ro,placeholder:'留空表示不限'})}${field('允许值（可选）','attr-values',(p.values||[]).join('、'),{readonly:ro,placeholder:'用顿号或逗号分隔'})}`;}
  function changeList(list){return list.length?list.map((x,i)=>`<button class="studio-change-row" data-act="studio-change-select" data-index="${i}"><span class="studio-change-icon">${x.field==='新增'?'+':'↺'}</span><span><strong>${esc(x.title)} <small>${esc(x.field)}</small></strong><span><del>${esc(x.before)}</del> → <ins>${esc(x.after)}</ins></span></span><span class="studio-change-arrow">↗</span></button>`).join(''):'<div class="studio-empty-inline">尚无语义变更。移动节点不会产生本体变更。</div>';}
  function trayView(){const n=changes().length;return `<section class="studio-change-tray ${tray?'expanded':''}"><div class="studio-tray-bar">${btn('tray',`<span class="studio-tray-indicator">${n}</span>本次变更 <span class="studio-subtle">${tray?'⌄':'⌃'}</span>`,`aria-expanded="${tray}"`,'studio-tray-toggle')}<div class="studio-tray-status">${validation?badge(validation.length?validation.length+' 项字段问题':'字段检查无问题',validation.length?'bad':'good'):'<span>尚未检查</span>'}<span class="studio-semantic-pending">语义校验未运行</span></div><div class="studio-tray-actions">${btn('validate','检查草案',!editable()?'disabled':'')}${btn('publish','审核与发布',!editable()||!n?'disabled':'','primary')}</div></div>${tray?`<div class="studio-tray-content">${changeList(changes())}${validation?.length?`<div class="studio-validation-list">${validation.map((e,i)=>btn('issue',`! ${esc(e.message)}`,`data-index="${i}"`)).join('')}</div>`:''}</div>`:''}</section>`;}
  function viewRender(){
    if(!hasOntology())return `<div class="surface empty"><h2>从业务结构开始</h2><p>导入已有本体，或从资料发现候选术语。</p>${btn('empty-info','查看本体建模流程','','primary')}</div>`;
    const current=mode==='draft'?base:(viewVersion||versions[0]);
    return `<div class="studio"><div class="studio-statusbar"><div class="studio-status-main"><span class="studio-status-orbit">◇</span><div><strong>${mode==='draft'?'本体草案 '+draftId:'本体 v'+current.version}</strong><span>${mode==='draft'?'基线 v'+base.version+' · '+base.id:current.id}</span></div>${badge(mode==='draft'?'草案':current===versions[0]?'当前已发布':'历史只读',mode==='draft'?'warn':'good')}</div><div class="studio-status-actions">${mode==='draft'?`<span class="studio-saved">● 已保存在此预览</span>${btn('published','查看已发布')}`:btn('edit','创建 / 继续草案','','primary')}${btn('versions','版本记录 ↗')}</div></div><div class="studio-top-tools"><div class="studio-tool-cluster">${btn('undo','↶',`title="撤销语义编辑" aria-label="撤销语义编辑" ${!editable()||!past.length?'disabled':''}`,'studio-icon')}${btn('redo','↷',`title="重做语义编辑" aria-label="重做语义编辑" ${!editable()||!future.length?'disabled':''}`,'studio-icon')}<span class="studio-tool-divider"></span><span>${editable()?'编辑结构':'发布快照 · 只读'}</span></div><div class="studio-tool-cluster">${btn('restore','恢复术语…',!editable()?'disabled':'')}${btn('source','RDF 源码')}${btn('governance','治理 ⌄')}${btn('inspector','详情 ☷','','studio-open-inspector')}</div></div><div class="studio-workarea">${catalog()}${graph()}<aside class="studio-inspector" aria-label="选中术语检查器">${inspector()}</aside></div>${trayView()}</div>`;
  }
  function dialogRefresh(){if(state.dialog?.kind?.startsWith('studio-'))openDialog(state.dialog.kind,{...state.dialog});}
  const read=(key,fallback='')=>document.querySelector(`#dialog-host [data-field="studio-${key}"]`)||document.querySelector(`.studio-inspector [data-field="studio-${key}"]`);
  const val=(key,fallback='')=>read(key)?.value??fallback;
  const checked=key=>!!read(key)?.checked;
  const max=key=>val(key).trim()===''?null:Number(val(key));
  function refreshAfterSave(message){closeDialog(false);repaint();toast(message);}
  function pick(sel,drawer=true){selection=sel;if(sel.kind==='class')expanded.add(sel.id);repaint();if(drawer&&window.innerWidth<=1280)openDialog('studio-inspector');}
  function saveClass(parentOnly=false){const c=cls(selection.id,draft);if(!c)return;const selectedParents=[...(read('class-parents')?.selectedOptions||[])].map(o=>o.value),label=val('class-label',c.label).trim(),description=val('class-description',c.description);if(!label){toast('请填写类型名称。');return;}const prev=copy(c);c.parents=selectedParents;const cycle=localChecks().find(e=>e.id===c.id&&e.message.includes('继承'));Object.assign(c,prev);if(cycle){toast(cycle.message);return;}mutate('编辑类型',d=>Object.assign(cls(c.id,d),parentOnly?{parents:selectedParents}:{parents:selectedParents,label,description}));refreshAfterSave('已更新草案；稳定 IRI 保持不变。');}
  function saveAttribute(){const d=state.dialog;const owner=d?.kind==='studio-add-attribute'?val('attr-owner'):selection.owner;const c=cls(owner,draft);if(!c)return;const label=val('attr-label').trim(),datatype=val('attr-datatype','string'),mx=max('attr-max');if(!label){toast('请填写属性名称。');return;}if(mx!==null&&(!Number.isInteger(mx)||mx<1)){toast('最大数量需为正整数，或留空。');return;}const creating=d?.kind==='studio-add-attribute',id=creating?val('attr-name').trim():selection.id;if(creating&&!safeFragment(id)){toast('规范名称可用中文；请避开空白、#、/ 和 ?。');return;}if(creating&&draft.classes.some(c=>c.properties.some(p=>p.id===id))){toast('该属性 IRI 已存在，请选择其他规范名称。');return;}const update={label,datatype,maxCount:mx,required:checked('attr-required'),description:val('attr-description'),values:val('attr-values').split(/[、,，]/).map(v=>v.trim()).filter(Boolean)};mutate(creating?'新增属性':'编辑属性',()=>{if(creating)c.properties.push({id,...update});else Object.assign(c.properties.find(p=>p.id===id),update);});selection={kind:'attribute',owner,id};refreshAfterSave('数据属性已保存到草案。');}
  function saveRelation(){const creating=state.dialog?.kind==='studio-add-relation',id=creating?val('relation-name').trim():selection.id,label=val('relation-label').trim(),from=val('relation-from'),to=val('relation-to'),mx=max('relation-max'),inverse=max('relation-inverse');if(!label||!from||!to){toast('请填写关系名称、起点和终点。');return;}if([mx,inverse].some(n=>n!==null&&(!Number.isInteger(n)||n<1))){toast('数量上限需为正整数或留空。');return;}if(creating&&(!safeFragment(id)||draft.relations.some(r=>r.id===id))){toast('请填写不重复的关系规范名称。');return;}const update={id,label,from,to,maxCount:mx,inverseMaxCount:inverse,required:checked('relation-required'),description:val('relation-description')};mutate(creating?'新增关系':'编辑关系',d=>{if(creating)d.relations.push(update);else Object.assign(d.relations.find(r=>r.id===id),update);});selection={kind:'relation',id};refreshAfterSave('关系与端点已更新。');}
  function newDraft(v){base=v;draft=copy(v.data);draftId='d'+(++counter);mode='draft';past=[];future=[];selection={kind:'class',id:'Application'};invalidate();closeDialog(false);repaint();toast('已基于 '+v.id+' 创建草案，当前发布保持不变。');}
  function handleAction(act,b){
    if(!act?.startsWith('studio-'))return false;const a=act.slice(7);
    switch(a){
      case'tab':tab=b.dataset.tab;repaint();break;
      case'expand':expanded.has(b.dataset.id)?expanded.delete(b.dataset.id):expanded.add(b.dataset.id);repaint();break;
      case'select':if(skipClick){skipClick=false;break;}pick({kind:'class',id:b.dataset.id});break;
      case'edge':pick({kind:b.dataset.kind==='inheritance'?'inheritance':'relation',id:b.dataset.id});break;
      case'attribute':pick({kind:'attribute',owner:b.dataset.owner,id:b.dataset.id});break;
      case'inspector':openDialog('studio-inspector');break;
      case'graph-tab':graphTab=b.dataset.tab;repaint();break;
      case'source':graphTab='rdf';repaint();break;
      case'add-class':if(editable())openDialog('studio-add-class');break;
      case'add-attribute':if(editable())openDialog('studio-add-attribute',{owner:b.dataset.owner||selection.owner||cls(selection.id)?.id||'Application'});break;
      case'add-relation':if(editable())openDialog('studio-add-relation',{from:b.dataset.from,to:b.dataset.to});break;
      case'create-class':{const label=val('new-label').trim(),id=val('new-name').trim(),selectedParents=[...(read('new-parents')?.selectedOptions||[])].map(o=>o.value);if(!label||!safeFragment(id)){toast('请填写名称；规范名称可用中文，请避开空白、#、/ 和 ?。');break;}if(draft.classes.some(c=>c.id===id)){toast('这个类的 IRI 已存在。');break;}mutate('新增类',d=>d.classes.push({id,label,parents:selectedParents,description:val('new-description'),color:'teal',properties:[]}));positions[id]=[330,410];selection={kind:'class',id};expanded.add(id);refreshAfterSave('新类型已加入草案。');break;}
      case'save-class':saveClass();break;
      case'save-parent':saveClass(true);break;
      case'save-attribute':saveAttribute();break;
      case'save-relation':saveRelation();break;
      case'undo':if(editable()&&past.length){const previous=past.pop();future.push({data:copy(draft),label:previous.label});draft=previous.data;invalidate();if(!cls(selection.owner||selection.id)&&selection.kind!=='relation')selection={kind:'class',id:'Application'};repaint();toast('已撤销：'+previous.label+'。画布布局保持不变。');}break;
      case'redo':if(editable()&&future.length){const next=future.pop();past.push({data:copy(draft),label:next.label});draft=next.data;invalidate();repaint();toast('已重做：'+next.label);}break;
      case'tray':tray=!tray;repaint();break;
      case'change-select':{const c=changes()[Number(b.dataset.index)];if(c){closeDialog(false);pick({kind:c.kind,id:c.id,owner:c.owner});}break;}
      case'issue':{const issue=validation?.[Number(b.dataset.index)];if(issue)pick(issue);break;}
      case'validate':validate();tray=true;repaint();toast(validation.length?'发现 '+validation.length+' 项字段问题。':'本地字段检查无问题；尚未执行 RDF / SHACL 语义校验。');break;
      case'publish':validate();if(validation.length){tray=true;repaint();toast('请先处理草案字段问题。');}else openDialog('studio-publish');break;
      case'confirm-publish':{if(!checked('publish-review')){toast('请先确认已核对变更与影响。');break;}if(validationFingerprint!==JSON.stringify(draft)||localChecks().length){toast('草案发生变化，请重新检查。');break;}const v={id:'ont-preview-v'+(versions[0].version+1)+'-'+counter,version:versions[0].version+1,date:'本次预览',summary:changes().map(x=>x.title+'·'+x.field).slice(0,3).join('；'),draft:draftId,data:copy(draft)};versions.unshift(v);base=v;mode='published';viewVersion=null;state.currentOntology=v.version;past=[];future=[];invalidate();closeDialog(false);repaint();toast('已演示发布 v'+v.version+'。已有知识继续保留原本体依据。');break;}
      case'published':mode='published';viewVersion=null;repaint();break;
      case'edit':{const target=viewVersion||versions[0];if(changes().length&&base.id===target.id){mode='draft';viewVersion=null;repaint();}else if(changes().length)openDialog('studio-new-draft',{version:target.id});else newDraft(target);break;}
      case'versions':openDialog('studio-versions',{version:versions[0].id,tab:'overview'});break;
      case'version-select':openDialog('studio-versions',{version:b.dataset.id,tab:'overview'});break;
      case'version-tab':openDialog('studio-versions',{version:state.dialog.version,tab:b.dataset.tab});break;
      case'view-version':viewVersion=versions.find(v=>v.id===state.dialog.version)||versions[0];mode='history';closeDialog(false);repaint();toast('正在只读查看 '+viewVersion.id+'；当前发布指针未改变。');break;
      case'draft-version':{const v=versions.find(v=>v.id===state.dialog.version)||versions[0];if(changes().length)openDialog('studio-new-draft',{version:v.id});else newDraft(v);break;}
      case'confirm-new-draft':newDraft(versions.find(v=>v.id===state.dialog.version)||versions[0]);break;
      case'restore':if(editable())openDialog('studio-restore',{source:'ont-20261015-v3-b'});break;
      case'restore-apply':{const source=versions.find(v=>v.id===val('restore-source'))||versions[1],sourceProp=cls('Merchant',source.data).properties.find(p=>p.id==='phone');const selected=['label','description','datatype','required','maxCount'].filter(k=>checked('restore-'+k));if(!selected.length){toast('请至少选择一个恢复字段。');break;}mutate('恢复术语',d=>{const c=cls('Merchant',d);let p=c.properties.find(p=>p.id==='phone');if(!p){p={id:'phone',label:'联系电话',datatype:'string',required:false,maxCount:null};c.properties.push(p);}selected.forEach(k=>p[k]=sourceProp[k]);p.inactive=false;p.restoredFrom=source.id;p.restoredFields=selected;});selection={kind:'attribute',owner:'Merchant',id:'phone'};refreshAfterSave('已按所选来源与字段恢复到草案。');break;}
      case'retire':openDialog('studio-retire');break;
      case'confirm-retire':{const c=cls(selection.owner,draft);mutate('停用数据属性',()=>c.properties.find(p=>p.id===selection.id).inactive=true);refreshAfterSave('属性已在草案中停用，历史定义保留。');break;}
      case'candidates':openDialog('studio-candidates');break;
      case'governance':openDialog('studio-governance');break;
      case'governance-entry':openDialog('studio-governance',{entry:b.dataset.entry});break;
      case'governance-source':graphTab='rdf';closeDialog(false);repaint();break;
      case'governance-versions':openDialog('studio-versions',{version:base.id,tab:'diff'});break;
      case'adopt-candidate':if(!draft.classes.some(c=>c.id==='OnboardingApplication')){mutate('采纳发现候选',d=>d.classes.push({id:'OnboardingApplication',label:'入驻申请',description:'商户申请入驻的业务申请。',parents:['Application'],color:'teal',properties:[]}));positions.OnboardingApplication=[420,423];selection={kind:'class',id:'OnboardingApplication'};refreshAfterSave('候选已加入草案，尚未正式发布。');}else toast('这项候选已在草案中。');break;
      case'layout':positions={BusinessObject:[330,68],Merchant:[110,238],Application:[330,238],Rule:[550,238],RefundApplication:[220,426],Material:[520,426]};repaint();toast('画布已整理，未产生语义变更。');break;
      case'layout-save':toast('画布布局已保留在此预览，不产生本体发布。');break;
      case'export':{const blob=new Blob([turtle()],{type:'text/turtle;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='business-ontology-'+(editable()?draftId:'v'+(viewVersion||versions[0]).version)+'.ttl';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出当前本体视图。');break;}
      case'empty-info':openDialog('studio-empty');break;
      default:return false;
    }return true;
  }
  function handleChange(target){
    const key=target.dataset?.field;
    if(!key?.startsWith('studio-'))return false;
    if(key==='studio-display'){
      if(display===target.value)return true;
      display=target.value;repaint();
    }else if(key==='studio-search'){
      // Replacing the focused input can emit change during blur. An unchanged
      // query must not re-enter render while its DOM is being replaced.
      if(query===target.value)return true;
      query=target.value;const start=target.selectionStart;repaint();
      const input=$('studio-search');input?.focus();input?.setSelectionRange(start,start);
    }
    return true;
  }
  function renderDialog(dialog){
    if(!dialog?.kind?.startsWith('studio-'))return null;
    const k=dialog.kind;const cancel='<button data-act="close-dialog">取消</button>';let title='',subtitle='',body='',footer='',drawer=false;
    if(k==='studio-inspector'){title=selectedLabel()||'术语详情';subtitle=editable()?draftId+' · 基线 '+base.id:'只读发布快照';body=inspector();drawer=true;}
    if(k==='studio-add-class'){title='添加类型';subtitle='为一类业务对象定义名称与父类';body=`<div data-studio-form>${field('中文名称','new-label','',{placeholder:'例如：入驻申请'})}${field('规范名称','new-name','',{placeholder:'例如：OnboardingApplication',hint:'IRI 前缀 '+esc(NS)})}${field('父类（可多选）','new-parents',dialog.parent?[dialog.parent]:[],{options:draft.classes.map(c=>[c.id,c.label]),multiple:true})}${field('说明','new-description','',{textarea:true})}</div>`;footer=cancel+btn('create-class','加入草案','','primary');}
    if(k==='studio-add-attribute'){title='添加数据属性';subtitle='属性与值类型随草案一起保存';const owner=dialog.owner||'Application';body=`<div data-studio-form>${field('所属类','attr-owner',owner,{options:draft.classes.map(c=>[c.id,c.label])})}${field('规范名称','attr-name','',{placeholder:'例如：reviewCode'})}${attributeFields({id:'待定义',label:'',datatype:'string',description:'',required:false,maxCount:1})}</div>`;footer=cancel+btn('save-attribute','加入草案','','primary');}
    if(k==='studio-add-relation'){title='添加业务关系';subtitle='类继承请在类型的父类字段中设置';body=`<div data-studio-form>${field('关系名称','relation-label','',{placeholder:'例如：审核'})}${field('规范名称','relation-name','',{placeholder:'例如：reviews'})}<div class="studio-field-pair">${field('起点类','relation-from',dialog.from||'Merchant',{options:draft.classes.map(c=>[c.id,c.label])})}${field('终点类','relation-to',dialog.to||'Application',{options:draft.classes.map(c=>[c.id,c.label])})}</div>${field('说明','relation-description','',{textarea:true})}${check('relation-required','起点至少关联一个目标',false)}${field('每个起点的目标数量','relation-max','',{type:'number',placeholder:'留空表示不限'})}${field('每个目标的起点数量','relation-inverse','',{type:'number',placeholder:'留空表示不限'})}</div>`;footer=cancel+btn('save-relation','加入草案','','primary');}
    if(k==='studio-versions'){
      const v=versions.find(v=>v.id===dialog.version)||versions[0],diff=differences(v.data,versions[0].data);title='本体版本记录';subtitle='查看历史不会切换当前发布';drawer=true;
      body=`<div class="studio-version-list">${versions.map((x,i)=>`<button data-act="studio-version-select" data-id="${esc(x.id)}" class="studio-version-card ${v.id===x.id?'selected':''}"><span class="studio-version-number">v${x.version}</span><span><strong>${esc(x.summary)}</strong><small>${esc(x.date)} · 来源 ${esc(x.draft)}</small><code>${esc(x.id)}</code></span>${i===0?badge('当前','good'):''}</button>`).join('')}</div><div class="studio-dialog-tabs">${btn('version-tab','结构概览','data-tab="overview"',dialog.tab!=='diff'?'active':'')}${btn('version-tab','与当前发布比较','data-tab="diff"',dialog.tab==='diff'?'active':'')}</div>${dialog.tab==='diff'?`<div class="studio-version-diff"><p>${esc(v.id)} → ${esc(versions[0].id)}</p>${diff.length?diff.map(x=>`<div class="studio-version-diff-row"><strong>${esc(x.title)} · ${esc(x.field)}</strong><span><del>${esc(x.before)}</del> → ${esc(x.after)}</span></div>`).join(''):'<p class="studio-empty-inline">这就是当前发布，无结构差异。</p>'}</div>`:`<dl class="studio-version-meta"><dt>稳定发布 ID</dt><dd>${esc(v.id)}</dd><dt>来源草案</dt><dd>${esc(v.draft)}</dd><dt>结构</dt><dd>${v.data.classes.length} 个类，${v.data.relations.length} 个关系类型</dd><dt>查看状态</dt><dd>历史快照只读</dd></dl><div class="studio-version-types">${v.data.classes.map(c=>`<span>◇ ${esc(c.label)} <small>${props(c).length} 属性</small></span>`).join('')}</div>`}`;footer=btn('view-version','只读查看此版本')+btn('draft-version','从此版本创建草案','','primary');
    }
    if(k==='studio-new-draft'){title='以这个版本创建新草案？';subtitle=dialog.version;body=`<p>现有草案 ${esc(draftId)} 有 ${changes().length} 项未发布变更。继续会在此预览中替换它。</p><div class="callout warn">当前已发布本体及历史知识依据保持不变。</div>`;footer=cancel+btn('confirm-new-draft','创建新草案','','primary');}
    if(k==='studio-restore'){title='恢复术语';subtitle='联系电话 · '+iristr('phone');drawer=true;body=`<div class="studio-restore-object"><span class="studio-type-mark amber">↺</span><div><strong>商户 / 联系电话</strong><span>在当前基线已停用</span></div></div>${field('恢复来源发布','restore-source','ont-20261015-v3-b',{options:versions.filter(v=>v.version===3).map(v=>[v.id,'v'+v.version+' · '+v.id])})}<div class="studio-section-label">选择恢复字段</div><div class="studio-restore-fields">${[['label','中文名称','联系电话'],['description','说明','商户用于联系的电话号码。'],['datatype','值类型','xsd:string'],['required','必填约束','可选'],['maxCount','最大数量','1']].map(([key,label,value])=>`<label><input type="checkbox" data-field="studio-restore-${key}" checked><span><strong>${label}</strong><small>${value}</small></span></label>`).join('')}</div><p class="studio-field-note">稳定 IRI 保留；仅恢复所选字段。确认后先进入草案，发布前可继续检查。</p>`;footer=cancel+btn('restore-apply','恢复所选字段','','primary');}
    if(k==='studio-publish'){title='审核与发布';subtitle=draftId+' · 基线 '+base.id;body=`<div class="studio-publish-summary"><div><span>本次变更</span><strong>${changes().length} 项</strong></div><div><span>本地字段检查</span><strong>${validation?.length?'有问题':'无问题'}</strong></div><div><span>语义校验</span><strong class="studio-warn">未运行</strong></div></div>${base.id!==versions[0].id?'<div class="callout warn">此草案来自历史发布，请核对与当前本体之间的差异及影响。</div>':''}<div class="studio-publish-changes">${changeList(changes())}</div><div class="studio-impact"><h3>发布影响</h3><p>后续处理使用新发布。已有知识保留原本体依据；重分类需另行确认。索引随后按任务同步。</p><span>本次无候选事实物化计划</span></div><div class="studio-demo-validation">当前仅运行本地字段检查；RDF / SHACL、实例影响与审批为演示状态。</div>${check('publish-review','已核对变更与影响，继续预览发布',false)}`;footer=cancel+btn('confirm-publish','确认演示发布','','primary');}
    if(k==='studio-retire'){title='停用这个属性？';subtitle=selectedLabel();body=`<p>停用后不再用于新建表单，原 IRI 和历史定义保留。该变更先保存在草案。</p>${field('停用原因','retire-reason','',{textarea:true})}`;footer=cancel+btn('confirm-retire','在草案中停用','','danger');}
    if(k==='studio-candidates'){title='发现候选';subtitle='商户入驻规范.pdf · 先确认映射，再纳入草案';drawer=true;body=`<div class="studio-candidate"><div>${badge('候选类','warn')}<h3>入驻申请</h3><p>建议父类：申请</p></div>${btn('adopt-candidate',draft.classes.some(c=>c.id==='OnboardingApplication')?'已加入草案':'采纳为子类',!editable()||draft.classes.some(c=>c.id==='OnboardingApplication')?'disabled':'','primary')}</div><div class="studio-candidate"><div>${badge('待映射','warn')}<h3>审核材料</h3><p>与“申请材料”可能同义，待人工确认。</p></div></div><div class="studio-candidate"><div>${badge('待复核','warn')}<h3>提交资料</h3><p>需确认起点和终点类型。</p></div></div>`;footer='<span></span><button data-act="close-dialog">返回建模</button>';}
    if(k==='studio-governance'){title='本体治理';subtitle=draftId+' · '+base.id;drawer=true;const entries=[['rebase','基线对齐','比较当前发布，核对后重放草案命令'],['refresh','刷新来源','对比来源新修订，保留已确认映射'],['batch','安全批量审核','只批准前置检查通过的候选'],['constraints','高级约束 / 源码','查看 SHACL 形状和标准 RDF 表达'],['knowledge','关联知识与受控重分类','按本体发布身份定位既有事实']];
      if(!dialog.entry)body='<div class="studio-governance-menu">'+entries.map(([id,label,note])=>btn('governance-entry','<strong>'+label+'</strong><span>'+note+'</span>','data-entry="'+id+'"')).join('')+'</div>';
      else if(dialog.entry==='rebase')body='<h3>基线对齐</h3><dl class="studio-version-meta"><dt>草案基线</dt><dd>'+esc(base.id)+'</dd><dt>当前发布</dt><dd>'+esc(versions[0].id)+'</dd><dt>待重放变更</dt><dd>'+changes().length+' 项</dd></dl><p class="note">先核对发布差异与命令冲突；对齐不修改正式知识。</p>'+btn('governance-versions','查看基线差异 →');
      else if(dialog.entry==='refresh')body='<h3>刷新发现来源</h3>'+field('来源资料','refresh-source','商户入驻规范.pdf · r1',{readonly:true})+field('待比较修订','refresh-target','r2',{options:[['r2','r2 · 新修订']]})+'<div class="callout spaced">原映射与已确认决定保留；新增与冲突分别复核。</div>'+btn('candidates','查看待复核候选 →');
      else if(dialog.entry==='batch')body='<h3>安全批量审核</h3><div class="mini-list spaced"><div>'+check('batch-safe','入驻申请 · 名称 / IRI / 父类检查完整',true)+'</div><div><span>审核材料 · 映射冲突</span>'+badge('转单项复核','warn')+'</div><div><span>提交资料 · 关系端点未确认</span>'+badge('转单项复核','warn')+'</div></div><p class="note">冲突和高风险项不进入安全批次。</p>'+btn('adopt-candidate','将安全候选加入草案',!editable()?'disabled':'','primary');
      else if(dialog.entry==='constraints')body='<h3>高级约束 / 源码</h3><p class="muted">'+esc(selectedLabel())+' · '+kindLabel(selection.kind)+'</p><div class="mini-list"><div><span>字段约束</span><span>minCount / maxCount / XSD 值类型</span></div><div><span>复杂形状与未支持表达</span><span>保留于标准图</span></div></div><p class="note spaced">语法、SHACL 与业务影响分别报告；未运行的检查不标通过。</p>'+btn('governance-source','打开 RDF 源码 →');
      else body='<h3>关联知识与受控重分类</h3><p class="muted">知识按实际依据发布归属。</p><div class="mini-list"><div><span>退款申请 · r1</span><span>依据 v3 · ont-20261015-v3-b</span></div><div><span>申请商户 · r2</span><span>依据 v3 · ont-20261015-v3-b</span></div></div>'+field('目标本体发布','reclassify-target',versions[0].id,{options:versions.map(v=>[v.id,'v'+v.version+' · '+v.id])})+'<div class="callout">受控重分类先核对记录修订、类型映射与约束；冲突逐项说明，不自动改写历史依据。</div>';
      footer='<span></span><button data-act="close-dialog">返回编辑器</button>';}
    if(k==='studio-empty'){title='建立项目本体';body='<div class="version-steps"><div class="version-step"><strong>1 · 导入标准本体或从资料发现</strong><p>保留原始标准图、来源及未支持表达。</p></div><div class="version-step"><strong>2 · 整理类型、属性和关系</strong><p>所有编辑进入草案，候选经过映射和冲突处理。</p></div><div class="version-step"><strong>3 · 校验、审核与发布</strong><p>正式发布后用于新的受控处理。</p></div></div>';footer='<span></span><button data-act="close-dialog">关闭</button>';}
    return modal(esc(title),esc(subtitle),`<div class="studio-dialog">${body}</div>`,footer,drawer);
  }
  function install(){if(installed)return;installed=true;
    document.addEventListener('keydown',e=>{if(state.page!=='ontology'||state.dialog)return;if(e.target.closest('input,textarea,select,[contenteditable]'))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();handleAction(e.shiftKey?'studio-redo':'studio-undo',{dataset:{}});}if((e.key==='Enter'||e.key===' ')&&e.target.matches('.studio-edge')){e.preventDefault();handleAction('studio-edge',e.target);}});
    document.addEventListener('pointerdown',e=>{const node=e.target.closest('[data-studio-node]');if(!node||e.target.closest('.studio-prop-line')||e.button!==0)return;const graph=node.closest('.studio-graph'),box=graph.getBoundingClientRect();drag={id:node.dataset.studioNode,start:[e.clientX,e.clientY],pos:copy(positions[node.dataset.studioNode]),box,node,moved:false};});
    document.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.start[0],dy=e.clientY-drag.start[1];if(Math.abs(dx)+Math.abs(dy)>6)drag.moved=true;if(!drag.moved)return;const pos=[Math.max(80,Math.min(580,drag.pos[0]+dx/drag.box.width*660)),Math.max(65,Math.min(450,drag.pos[1]+dy/drag.box.height*520))];positions[drag.id]=pos;drag.node.style.left=pos[0]/6.6+'%';drag.node.style.top=pos[1]/5.2+'%';drag.node.classList.add('dragging');});
    document.addEventListener('pointerup',()=>{if(!drag)return;if(drag.moved){skipClick=true;repaint();setTimeout(()=>skipClick=false,100);}drag=null;});
  }
  function context(){return {published:{id:versions[0].id,version:versions[0].version},draft:{id:draftId,baseId:base.id,editable:editable(),changes:changes().length,fingerprint:JSON.stringify(draft)},selection:copy(selection),classes:copy(draft.classes),relations:copy(draft.relations)};}
  function applyParent(childId,parentId,fingerprint){
    if(!editable()||base.id!==versions[0].id)return {ok:false,message:'请先进入基于当前发布的可编辑草案。'};
    if(fingerprint!==JSON.stringify(draft))return {ok:false,message:'草案已变化，请重新核对父类建议。'};
    const child=cls(childId,draft),parent=cls(parentId,draft);
    if(!child||!parent||childId===parentId)return {ok:false,message:'请选择不同且有效的父类和子类。'};
    if(child.parents.includes(parentId))return {ok:false,message:'已有这条继承关系，不重复添加。'};
    const previous=[...child.parents];child.parents.push(parentId);
    const cycle=localChecks().some(x=>x.message.includes('继承环'));child.parents=previous;
    if(cycle)return {ok:false,message:'这条建议会形成继承环，不能加入草案。'};
    mutate('补充父类建议',()=>child.parents.push(parentId));selection={kind:'class',id:childId};
    return {ok:true};
  }
  function applyCandidate(candidate){
    if(!editable())return {ok:false,message:'当前是只读版本，请先创建或继续草案。'};
    if(base.id!==versions[0].id)return {ok:false,message:'草案基线不是当前发布，请先完成基线对齐。'};
    const ids=new Set([...draft.classes.map(c=>c.id),...draft.relations.map(r=>r.id),...draft.classes.flatMap(c=>c.properties.map(p=>p.id))]);
    if(!candidate.label.trim()||!safeFragment(candidate.id)||ids.has(candidate.id))return {ok:false,message:'请填写名称和不重复的规范名称。'};
    if(candidate.kind==='class'&&candidate.parent&&!cls(candidate.parent,draft))return {ok:false,message:'请选择有效的父类。'};
    if(candidate.kind==='relation'&&(!cls(candidate.from,draft)||!cls(candidate.to,draft)))return {ok:false,message:'请先定义关系的起点和终点类型。'};
    if(candidate.kind==='attribute'&&!cls(candidate.owner,draft))return {ok:false,message:'请先定义属性的所属类。'};
    mutate('采纳发现候选',d=>{
      if(candidate.kind==='class')d.classes.push({id:candidate.id,label:candidate.label,description:candidate.description||'',parents:candidate.parent?[candidate.parent]:[],color:'teal',properties:[]});
      if(candidate.kind==='relation')d.relations.push({id:candidate.id,label:candidate.label,description:candidate.description||'',from:candidate.from,to:candidate.to,maxCount:null,inverseMaxCount:null,required:false});
      if(candidate.kind==='attribute')cls(candidate.owner,d).properties.push({id:candidate.id,label:candidate.label,description:candidate.description||'',datatype:candidate.datatype||'string',required:false,maxCount:1});
    });
    selection=candidate.kind==='attribute'?{kind:'attribute',id:candidate.id,owner:candidate.owner}:{kind:candidate.kind,id:candidate.id};
    if(candidate.kind==='class'){positions[candidate.id]=[420,423];expanded.add(candidate.id);}
    return {ok:true};
  }
  return {render:viewRender,handleAction,handleChange,renderDialog,install,context,applyCandidate,applyParent};
})();
