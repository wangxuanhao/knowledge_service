/* Group peer task buttons into discoverable navigation sections without changing tab behavior. */
(()=>{
  const nav=document.querySelector('aside nav');if(!nav)return;
  const layers=['structure','instance','consume','run'];   // 与 groups 一一对应：本体/实例/消费/运行
  const groups=[
    // P0 菜单重组（2026-10-04）：侧栏从「探索 / 建模与治理 / 项目与运行」三组，
    // 改成本体层 / 实例层 / 消费层 / 运行层四组——本体层（结构 TBox）与实例层（知识 ABox）
    // 是两个维度，各独立一组。本体三个页面（审核台/编辑台/档案）合成一个「本体建模层」，
    // 候选脑图与其一起并入；「知识审核」「原文数据源」分别并入「知识台账」「知识写入」。
    ['本体层 · 结构',['ontology-model']],
    ['实例层 · 知识',['ingest','records']],
    ['消费层 · 用起来',['search','qa','mindmap']],
    ['运行层 · 跑起来',['runtime']],
  ];
  const buttons=new Map([...nav.querySelectorAll('button[data-tab]')].map(button=>[button.dataset.tab,button]));
  nav.replaceChildren();
  for(const [i,[label,tabs]] of groups.entries()){
    const available=tabs.map(tab=>buttons.get(tab)).filter(Boolean).filter(button=>!button.classList.contains('hidden'));if(!available.length)continue;
    const group=document.createElement('details');group.className='nav-group';group.dataset.layer=layers[i]||'';group.open=available.length===1||available.some(button=>button.classList.contains('active'));
    const summary=document.createElement('summary');summary.textContent=label;group.append(summary,...available);nav.append(group);
    available.forEach(button=>button.addEventListener('click',()=>{group.open=true;document.querySelectorAll('.nav-group').forEach(other=>{if(other!==group)other.open=false;});}));
  }

  // 系统层（独立于上面业务四组）：用户与权限的**菜单入口**。
  // 为什么不用 data-tab：用户管理是「抽屉」不是一个 tab 页面（没有 #tab-xxx 容器），
  // 套 data-tab 会被上面的逻辑收进业务组、点击还会去找不存在的 tab。这里单独建一组，
  // 点击直接打开用户管理抽屉。按钮带 admin-only：只读用户看不到，下方空组逻辑也会整组隐藏。
  const systemGroup=document.createElement('details');
  systemGroup.className='nav-group';systemGroup.dataset.layer='system';systemGroup.open=true;
  const systemSummary=document.createElement('summary');systemSummary.textContent='系统层 · 管理';
  const usersButton=document.createElement('button');
  usersButton.type='button';usersButton.className='admin-only';
  usersButton.textContent='用户与权限';
  usersButton.addEventListener('click',()=>{
    systemGroup.open=true;
    document.querySelectorAll('.nav-group').forEach(other=>{if(other!==systemGroup)other.open=false;});
    if(window.UserAdmin&&window.UserAdmin.open){window.UserAdmin.open();}
    else if(window.Auth&&window.Auth.notice){window.Auth.notice('用户与权限界面没加载成功，请刷新重试。','error');}
  });
  systemGroup.append(systemSummary,usersButton);
  nav.append(systemGroup);

  const resolve=document.getElementById('parse-resolve');
  if(resolve?.parentElement)resolve.parentElement.lastChild.textContent='同名／别名实体消歧（稳定类型 IRI 和有效期需兼容）';
  const governance=document.querySelector('.governance-note');
  if(governance)governance.textContent='Semantica EntityMerger · 稳定类型 IRI 与业务区间兼容时可跨本体版本融合，来源和版本轨迹完整保留。';

  // 只读账号：写入口本身被 .admin-only 藏掉了（CSS），但分组标题还在 —— 于是侧栏会留下
  // "有标题、里面空空"的组，看着像坏了。确知登录者是只读用户时，把这种空组整组收起。
  // 不加 is-admin 分支：管理员的分组本来就该在；登录态未知时不动菜单（那是登录浮层盖着的时刻）。
  const syncReadOnlyGroups=()=>{
    if(!document.body.classList.contains('is-viewer'))return;
    nav.querySelectorAll('.nav-group').forEach(group=>{
      // 统计组内**所有**按钮（不只 data-tab）：系统层的「用户与权限」按钮为避开 tab
      // 机制不带 data-tab，若只数 data-tab 会把它漏掉，导致只读用户仍看到系统层。
      const items=[...group.querySelectorAll('button')];
      if(items.length&&items.every(button=>button.classList.contains('admin-only')))group.hidden=true;
    });
  };
  syncReadOnlyGroups();
  new MutationObserver(syncReadOnlyGroups).observe(document.body,{attributes:true,attributeFilter:['class']});
})();