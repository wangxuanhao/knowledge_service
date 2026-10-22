// Local, disposable management sketch. No account or project API is called.
const managementUI = (() => {
  const projects=[{id:'main',name:'业务规则资料库'},{id:'empty',name:'新项目'}];
  const people=[
    {id:'self',username:'lin',display_name:'林知远',role:'admin',is_active:true},
    {id:'chen',username:'chen',display_name:'陈晓',role:'admin',is_active:true},
    {id:'wu',username:'wu',display_name:'吴宁',role:'viewer',is_active:false},
    {id:'root',username:'system',display_name:'系统管理员',role:'superadmin',is_active:true}
  ];
  const roleNames={viewer:'只读用户',admin:'管理员',superadmin:'超级管理员'};
  const local={projectQuery:'',personQuery:'',role:'all',status:'all',sequence:1};
  const isManagement=()=>['projects','people'].includes(state.page);
  const projectById=id=>projects.find(p=>p.id===id);
  const personById=id=>people.find(p=>p.id===id);
  const canEdit=p=>p&&p.role!=='superadmin'; // This sketch uses the existing admin role.
  const canRemove=p=>canEdit(p)&&p.id!=='self';
  const btn=(action,text,extra='',style='')=>`<button data-act="mg-${action}" class="${style}" ${extra}>${text}</button>`;
  const errorHost='<p id="mg-error" class="mg-error" role="alert" hidden></p>';
  const foot=(action,text)=>`<button data-act="close-dialog">取消</button>${btn(action,text,'','primary')}`;
  const docsFor=id=>documents.filter(d=>(d.project||'main')===id);
  const projectMatches=()=>projects.filter(p=>p.name.toLowerCase().includes(local.projectQuery.toLowerCase()));
  const personMatches=()=>people.filter(p=>(p.username+' '+p.display_name).toLowerCase().includes(local.personQuery.toLowerCase())&&(local.role==='all'||p.role===local.role)&&(local.status==='all'||String(p.is_active)===local.status));
  function managementNav(){
    let host=$('management-nav');
    if(!host){host=document.createElement('div');host.id='management-nav';host.setAttribute('aria-label','管理导航');$('sidebar').insertBefore(host,$('sidebar').querySelector('.sidebar-footer'));}
    host.innerHTML=`<div class="nav-label">管理</div>${[['projects','▣','项目管理'],['people','♧','人员管理']].map(([page,icon,label])=>`<button class="nav-button ${state.page===page?'active':''}" data-act="nav" data-page="${page}" ${state.page===page?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</button>`).join('')}`;
    $('sidebar').querySelector('.sidebar-footer .small').textContent='管理员';
    if(!$('mg-shortcut'))$('sidebar').closest('.shell').previousElementSibling.querySelector('.preview-shortcuts').insertAdjacentHTML('beforeend',btn('open-projects','看项目与人员','id="mg-shortcut"'));
  }
  function projectRows(){
    const rows=projectMatches();
    if(!rows.length)return `<div class="empty"><h3>${projects.length?'没有匹配的项目':'还没有项目'}</h3><p>${projects.length?'换个名称再试，或清除查找条件。':'创建项目后，从资料库添加文件并建立知识。'}</p>${projects.length?btn('clear-projects','清除查找'):btn('new-project','创建项目','','primary')}</div>`;
    return `<div class="table-wrap"><table class="mg-table"><thead><tr><th>项目</th><th>来源资料</th><th>本体</th><th>操作</th></tr></thead><tbody>${rows.map(p=>`<tr data-project-row="${esc(p.id)}"><td><div class="mg-name"><span class="mg-project-mark" aria-hidden="true">▣</span><div>${btn('project-detail',esc(p.name),`data-id="${esc(p.id)}"`,'link mg-title')}<span class="file-sub">${p.id===state.project?'当前项目':'独立的资料与知识空间'}</span></div>${p.id===state.project?badge('当前','good'):''}</div></td><td>${docsFor(p.id).length} 份资料</td><td>${p.id==='main'?badge('v'+studioUI.context().published.version+' · 已发布','good'):badge('尚未建立')}</td><td><div class="mg-row-actions">${btn('enter-project','进入项目',`data-id="${esc(p.id)}"`)}${btn('project-detail','设置',`data-id="${esc(p.id)}"`,'ghost')}</div></td></tr>`).join('')}</tbody></table></div>`;
  }
  function renderProjects(){
    return `<div class="mg-workspace"><div class="mg-overline">PROJECTS <span>项目是资料、本体和知识的工作边界</span></div><div class="toolbar mg-toolbar"><input aria-label="查找项目" placeholder="查找项目名称" data-mg-field="projectQuery" value="${esc(local.projectQuery)}"><span id="mg-count" class="muted small">${projectMatches().length} / ${projects.length} 个项目</span></div><div id="mg-results">${projectRows()}</div><div class="mg-context"><span aria-hidden="true">↗</span><p>进入项目后，在资料库、本体建模层和检索与图谱中开展工作。<br><span class="muted">左上角用于快速切换；这里集中创建项目和管理设置。</span></p></div></div>`;
  }
  function personRows(){
    const rows=personMatches();
    if(!rows.length)return `<div class="empty"><h3>没有匹配的账号</h3><p>调整姓名、角色或账号状态后再查找。</p>${btn('clear-people','清除条件')}</div>`;
    return `<div class="table-wrap"><table class="mg-table"><thead><tr><th>人员 / 登录账号</th><th>系统角色</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(p=>`<tr data-person-row="${esc(p.id)}"><td><div class="mg-name"><span class="avatar" aria-hidden="true">${esc((p.display_name||p.username)[0])}</span><div><strong class="mg-title">${esc(p.display_name||p.username)}</strong><span class="file-sub">${esc(p.username)}${p.id==='self'?' · 当前账号':''}</span></div></div></td><td>${badge(roleNames[p.role],p.role==='superadmin'?'info':'')}</td><td>${badge(p.is_active?'已启用':'已停用',p.is_active?'good':'')}</td><td>${canEdit(p)?btn('person-detail','编辑',`data-id="${esc(p.id)}"`):'<span class="mg-protected">受系统保护</span>'}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function renderPeople(){
    return `<div class="mg-workspace"><div class="mg-overline">PEOPLE <span>管理登录账号、系统角色与启用状态</span></div><div class="toolbar mg-toolbar"><input aria-label="查找人员" placeholder="查找姓名或登录账号" data-mg-field="personQuery" value="${esc(local.personQuery)}"><select aria-label="按系统角色筛选" data-mg-field="role"><option value="all">全部角色</option>${Object.entries(roleNames).map(([id,label])=>`<option value="${id}" ${local.role===id?'selected':''}>${label}</option>`).join('')}</select><select aria-label="按账号状态筛选" data-mg-field="status"><option value="all">全部状态</option><option value="true" ${local.status==='true'?'selected':''}>已启用</option><option value="false" ${local.status==='false'?'selected':''}>已停用</option></select><span id="mg-count" class="muted small">${personMatches().length} / ${people.length} 人</span></div><div id="mg-results">${personRows()}</div><div class="mg-context"><span aria-hidden="true">◎</span><p>角色目前作用于整个系统。管理员可以管理普通账号，只读用户使用知识。<br><span class="muted">“只允许访问指定项目”的成员与权限能力尚未接通，当前不会暗示已有项目隔离。</span></p></div><details class="mg-role-guide"><summary>查看角色权限与账号保护规则</summary><div class="table-wrap"><table><thead><tr><th>角色</th><th>权限范围</th><th>保护规则</th></tr></thead><tbody><tr><td>只读用户</td><td>切换项目、读取与检索知识</td><td>不提供项目、账号或知识写入操作</td></tr><tr><td>管理员</td><td>管理项目、知识及普通账号</td><td>不能管理或授予超级管理员</td></tr><tr><td>超级管理员</td><td>系统管理，包括授予最高角色</td><td>账号不能被降级、停用或删除</td></tr></tbody></table></div></details></div>`;
  }
  function refreshResults(){
    if(!isManagement())return;
    $('mg-results').innerHTML=state.page==='projects'?projectRows():personRows();
    $('mg-count').textContent=state.page==='projects'?`${projectMatches().length} / ${projects.length} 个项目`:`${personMatches().length} / ${people.length} 人`;
  }
  function showError(message){$('mg-error').hidden=false;$('mg-error').textContent=message;}
  function renderDialog(d){
    if(!d.kind.startsWith('mg-'))return null;
    const p=projectById(d.id),u=personById(d.id);
    if(d.kind==='mg-new-project')return modal('创建项目','先建立工作空间，再添加资料',`<div class="mg-form"><label>项目名称<input id="mg-project-name" maxlength="200" placeholder="例如：商户规则知识库" autocomplete="off"></label><p class="note">新项目从空资料库开始；是否开放抽取，在添加资料时按批次选择。发布本体后仍可继续建模。</p>${errorHost}</div>`,foot('save-new-project','创建并进入'),true);
    if(d.kind==='mg-project'&&p)return modal('项目设置',p.name,`<div class="mg-form"><label>项目名称<input id="mg-project-name" maxlength="200" value="${esc(p.name)}"></label><dl class="mg-summary"><dt>来源资料</dt><dd>${docsFor(p.id).length} 份</dd><dt>本体状态</dt><dd>${p.id==='main'?'v'+studioUI.context().published.version+' · 已发布，可继续建模':'尚未建立，可从资料发现或手工建模'}</dd><dt>人员与权限</dt><dd>沿用全局系统角色；暂未提供独立项目成员配置</dd></dl>${errorHost}<div class="mg-drawer-section"><h3>项目工作</h3>${btn('project-overview','查看项目概览 →',`data-id="${esc(p.id)}"`,'link')}${btn('project-backups','知识备份与恢复 →',`data-id="${esc(p.id)}"`,'link')}</div><div class="mg-drawer-section mg-danger-zone"><h3>删除项目</h3><p>会删除该项目的资料与知识，需要再次确认。</p>${btn('delete-project','删除项目…',`data-id="${esc(p.id)}"`,'danger')}</div></div>`,foot('save-project','保存名称'),true);
    if(d.kind==='mg-new-person')return modal('添加人员','创建系统登录账号',`<div class="mg-form"><label>登录账号<input id="mg-username" maxlength="64" autocomplete="off" placeholder="输入唯一登录名"></label><label>展示名<input id="mg-display-name" maxlength="128" autocomplete="off" placeholder="姓名或工作称呼，可选"></label><label>系统角色<select id="mg-person-role"><option value="viewer">只读用户</option><option value="admin">管理员</option></select></label><p class="note">角色作用于整个系统。当前管理员不能授予超级管理员。</p><label>初始口令<input id="mg-password" type="password" minlength="6" maxlength="128" autocomplete="new-password" placeholder="6–128 个字符"></label><p class="small muted">创建完成后由管理员线下交付登录信息；此处不发送邀请。</p>${errorHost}</div>`,foot('save-new-person','创建账号'),true);
    if(d.kind==='mg-person'&&canEdit(u))return modal('编辑人员',`${u.display_name||u.username} · ${u.username}`,`<div class="mg-form"><label>登录账号<input value="${esc(u.username)}" disabled></label><label>展示名<input id="mg-display-name" maxlength="128" value="${esc(u.display_name)}"></label><label>系统角色<select id="mg-person-role" ${u.id==='self'?'disabled':''}>${['viewer','admin'].map(r=>`<option value="${r}" ${u.role===r?'selected':''}>${roleNames[r]}</option>`).join('')}</select></label><p class="note">${u.id==='self'?'当前登录账号不能降级、停用或删除。自己的口令通过个人账号入口修改。':'更改系统角色后，账号的操作范围随之改变；降为只读后不能再写入。'}</p>${errorHost}${canRemove(u)?`<div class="mg-drawer-section"><h3>账号操作</h3><div class="mg-account-actions">${btn('toggle-person',u.is_active?'停用账号…':'启用账号',`data-id="${esc(u.id)}"`)}${btn('reset-password','重置口令…',`data-id="${esc(u.id)}"`)}</div><p class="small muted">停用保留账号，阻止登录；重置口令后旧会话失效。</p></div><div class="mg-drawer-section mg-danger-zone"><h3>删除账号</h3><p>物理删除并释放登录名。如只是暂停使用，请选择停用。</p>${btn('delete-person','删除账号…',`data-id="${esc(u.id)}"`,'danger')}</div>`:''}</div>`,foot('save-person','保存更改'),true);
    if(d.kind==='mg-reset-password'&&canRemove(u))return modal('重置口令',u.username,`<div class="mg-form"><label>新口令<input id="mg-password" type="password" minlength="6" maxlength="128" autocomplete="new-password"></label><label>确认新口令<input id="mg-password-confirm" type="password" maxlength="128" autocomplete="new-password"></label><p class="note">正式功能重置后该账号的旧会话失效。草图只演示交互，不存储口令。</p>${errorHost}</div>`,foot('save-password','确认重置'));
    if(d.kind==='mg-confirm')return modal(d.title,d.subtitle,`<p>${esc(d.message)}</p>${d.confirmName?`<label class="spaced">输入“${esc(d.confirmName)}”确认<input id="mg-confirm-name" autocomplete="off"></label>`:''}${errorHost}`,`<button data-act="close-dialog">取消</button>${btn('confirm',d.button,'','danger')}`);
    return null;
  }
  function selectProject(id,page='sources'){
    const p=projectById(id);if(!p)return;
    state.project=p.id;state.projectName=p.name;state.messages=[];navigate(page);
  }
  function finish(message){closeDialog(false);render();toast(message+'（本地演示）。');}
  function confirmAction(data){openDialog('mg-confirm',data);}
  function handleAction(act,el){
    if(act==='manage-projects'){navigate('projects');return true;}
    if(act==='switch-project'){selectProject(el.dataset.project,el.dataset.project==='main'?'qa':'sources');return true;}
    if(act==='delete-project'){openDialog('mg-project',{id:state.project});return true;}
    if(act==='save-settings'){openDialog('mg-project',{id:state.project});return true;}
    if(!act.startsWith('mg-'))return false;
    const id=el.dataset.id,d=state.dialog,p=projectById(id),u=personById(id);
    switch(act.slice(3)){
      case'open-projects':navigate('projects');break;
      case'new-project':openDialog('mg-new-project');break;
      case'project-detail':if(p)openDialog('mg-project',{id});break;
      case'enter-project':selectProject(id);break;
      case'project-overview':selectProject(id,'overview');break;
      case'project-backups':selectProject(id,'backups');break;
      case'clear-projects':local.projectQuery='';render();break;
      case'clear-people':local.personQuery='';local.role='all';local.status='all';render();break;
      case'save-new-project':case'save-project':{
        const name=$('mg-project-name').value.trim();if(!name){showError('请填写项目名称。');break;}
        if(act==='mg-save-new-project'){const item={id:'project-'+local.sequence++,name};projects.push(item);local.projectQuery='';selectProject(item.id);toast('示例项目已创建，下一步添加资料（本地演示）。');}
        else{const target=projectById(d.id);if(!target)break;target.name=name;if(target.id===state.project)state.projectName=name;finish('项目名称已更新');}break;
      }
      case'delete-project':if(p)confirmAction({operation:'delete-project',id,title:'删除项目？',subtitle:p.name,message:`将删除“${p.name}”及其资料与知识。草图仅删除本地示例；正式操作不可恢复。`,confirmName:p.name,button:'删除项目'});break;
      case'new-person':openDialog('mg-new-person');break;
      case'person-detail':if(canEdit(u))openDialog('mg-person',{id});break;
      case'save-new-person':{
        const username=$('mg-username').value.trim(),password=$('mg-password').value,role=$('mg-person-role').value;
        if(!username){showError('请填写登录账号。');break;}
        if(people.some(x=>x.username===username)){showError('该登录账号已存在。');break;}
        if(password.length<6||password.length>128){showError('口令需要 6–128 个字符。');break;}
        if(!['viewer','admin'].includes(role)){showError('当前管理员不能授予这个角色。');break;}
        people.push({id:'person-'+local.sequence++,username,display_name:$('mg-display-name').value.trim(),role,is_active:true});
        local.personQuery='';local.role='all';local.status='all';finish('账号已添加');break;
      }
      case'save-person':{
        const target=personById(d.id),role=$('mg-person-role').value;if(!canEdit(target))break;
        if(!['viewer','admin'].includes(role)||(target.id==='self'&&role!==target.role)){showError('不能更改当前账号的角色或授予最高权限。');break;}
        const display=$('mg-display-name').value.trim();
        if(target.role!==role&&role==='viewer'){confirmAction({operation:'save-person',id:target.id,role,display,title:'将该账号设为只读？',subtitle:target.username,message:'更改后将不能写入知识、管理项目或管理账号。',button:'确认降为只读'});break;}
        target.display_name=display;target.role=role;finish('人员信息已更新');break;
      }
      case'toggle-person':if(canRemove(u)){if(u.is_active)confirmAction({operation:'disable-person',id,title:'停用账号？',subtitle:u.username,message:'停用后无法登录，已有会话失效；账号与历史保留。',button:'确认停用'});else{u.is_active=true;finish('账号已启用');}}break;
      case'delete-person':if(canRemove(u))confirmAction({operation:'delete-person',id,title:'删除账号？',subtitle:u.username,message:'此操作物理删除账号并释放登录名，无法恢复。只需暂停使用时可选择停用。',confirmName:u.username,button:'删除账号'});break;
      case'reset-password':if(canRemove(u))openDialog('mg-reset-password',{id});break;
      case'save-password':if(canRemove(personById(d.id))){const password=$('mg-password').value;if(password.length<6||password.length>128){showError('口令需要 6–128 个字符。');break;}if(password!==$('mg-password-confirm').value){showError('两次输入的口令不一致。');break;}finish('已演示口令重置，未更改真实账号');}break;
      case'confirm':{
        if(d.confirmName&&$('mg-confirm-name').value!==d.confirmName){showError('输入内容与确认名称不一致。');break;}
        if(d.operation==='delete-project'){
          const target=projectById(d.id);if(!target)break;projects.splice(projects.indexOf(target),1);
          if(state.project===d.id){const next=projects[0];state.project=next?.id||'__none__';state.projectName=next?.name||'未选择项目';state.messages=[];}
          for(const k of Object.keys(state.filters))if(k.startsWith(d.id+':'))delete state.filters[k];
          closeDialog(false);navigate('projects');toast('示例项目已删除（本地演示）。');break;
        }
        const target=personById(d.id);if(!canRemove(target))break;
        if(d.operation==='disable-person')target.is_active=false;
        if(d.operation==='delete-person')people.splice(people.indexOf(target),1);
        if(d.operation==='save-person'){target.role=d.role;target.display_name=d.display;}
        finish('账号更改已确认');break;
      }
    }
    return true;
  }
  function handleChange(el){
    const field=el.dataset.mgField;if(!field||!Object.hasOwn(local,field))return false;
    local[field]=el.value;refreshResults();return true;
  }
  function install(){
    titles.projects=['管理','项目管理','集中管理项目，进入各自的资料与知识工作空间。'];
    titles.people=['管理','人员管理','管理系统账号、角色和登录状态。'];
    const previous=render;
    render=function(){
      if(!projectById(state.project)&&!isManagement())state.page='projects';
      if(isManagement()){
        renderNav();$('breadcrumb').textContent='工作空间 / 管理';$('page-title').textContent=titles[state.page][1];$('page-subtitle').textContent=titles[state.page][2];
        $('heading-actions').innerHTML=state.page==='projects'?btn('new-project','＋ 创建项目','','primary'):btn('new-person','＋ 添加人员','','primary');
        state.filterOpen=false;renderFilter();$('page-content').innerHTML=state.page==='projects'?renderProjects():renderPeople();
      }else previous();
      managementNav();if(!projectById(state.project))$('nav').innerHTML='';
    };
    renderSettings=function(){return `<div class="surface mg-current-settings"><h3>${esc(state.projectName)}</h3><p class="muted">项目名称、备份入口和删除操作集中在项目设置抽屉中。</p>${btn('project-detail','打开项目设置',`data-id="${esc(state.project)}"`,'primary')}${btn('open-projects','返回项目管理','','ghost')}</div>`;};
    projectMenu=function(){const menu=$('project-menu'),opened=menu.hidden;menu.hidden=!opened;$('project-trigger').setAttribute('aria-expanded',String(opened));menu.innerHTML=`<small class="mg-menu-label">快速切换项目</small>${projects.map(p=>`<button data-act="switch-project" data-project="${esc(p.id)}">${p.id===state.project?'✓ ':''}${esc(p.name)}</button>`).join('')}${projectById(state.project)?'<hr><button data-act="nav" data-page="overview">当前项目概览</button><button data-act="nav" data-page="backups">知识备份</button>':''}<hr><button data-act="manage-projects">全部项目与管理 →</button>`;};
    if(['#projects','#people'].includes(location.hash))state.page=location.hash.slice(1);
  }
  return {install,renderDialog,handleAction,handleChange};
})();
