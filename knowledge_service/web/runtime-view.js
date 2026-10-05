/* 「项目与运行」：把原来四个平铺菜单（项目管理 / 项目总览 / 后台任务 / 快照·评测）合并成一页。
 *
 * 为什么合并（D2）：这四页各自回答的其实是同一个问题 —— "这个项目跑得怎么样"。
 * 平铺成四个菜单的代价是用户要在四个页签之间来回找（用户原话："界面不要一直是平铺的"），
 * 而它们又都不是日常动作：都不改知识、不改本体结构。
 *
 * 一屏一问：每屏只回答一个问题，所以用分区页签，而不是把四块并排堆在首屏。
 *   项目设置   → 我有哪些项目（新建 / 改名 / 删除 / 切当前项目）
 *   运行总览   → 这个项目现在有多少知识、缺什么
 *   后台任务   → 解析任务跑到哪一步了
 *   快照与评测 → 存档 / 恢复 / 按标准答案打分
 *
 * 它不做什么（把边界写下来，免得这一页又长成第二个"什么都在这里"的页面）：
 *   不改具体知识（去「知识台账」）、不改分类（去「本体建模层」）、不审别人的请求（去「本体建模层」）。
 *
 * 数据加载：进入某个分区才拉该分区的数据（首屏冷启动不产生任何请求），
 * 各模块用 RuntimeView.on(view, fn) 注册自己的加载函数 —— 加载口径留在各自模块里，
 * 这里只负责"什么时候该拉"，避免把四份加载逻辑复制到这个文件里。
 */
(()=>{
  const VIEWS=['project','overview','jobs','snapshots'];
  const DEFAULT='overview';
  const LABELS={project:'项目设置',overview:'运行总览',jobs:'后台任务',snapshots:'快照与评测'};
  // 面板 id 沿用原来的四个 id：既有的"当前是否在这一屏"判断（如 tab-jobs / tab-dashboard）
  // 不用改，改动面收在导航与显隐这一层。
  const PANEL_IDS={project:'tab-projects',overview:'tab-dashboard',jobs:'tab-jobs',snapshots:'tab-evaluation'};
  const tab=document.querySelector('[data-tab="runtime"]');
  const page=document.getElementById('tab-runtime');
  if(!tab||!page)return;
  const buttons=new Map([...page.querySelectorAll('[data-runtime-view]')].map(button=>[button.dataset.runtimeView,button]));
  const panels=new Map(VIEWS.map(view=>[view,document.getElementById(PANEL_IDS[view])]));
  const loaders=new Map();
  let currentView=DEFAULT;
  const notify=(message,error)=>{if(typeof status==='function')status(message,error);else console.warn(message);};

  /* 加载失败不阻断切换：说一句人话，用户可以在该分区自己的「刷新」按钮上重试。
     一个分区可以有多个加载函数（例如「运行总览」既要统计又要 Neo4j 同步状态），
     所以注册表存的是列表 —— 后来的把先来的覆盖掉，就会有一块数据永远读不出来。 */
  function run(view){
    const entries=loaders.get(view)||[];
    for(const loader of entries){
      try{Promise.resolve(loader()).catch(error=>notify(`${LABELS[view]}加载失败：${error.message}`,true));}
      catch(error){notify(`${LABELS[view]}加载失败：${error.message}`,true);}
    }
  }

  function show(view,{load=true}={}){
    if(!VIEWS.includes(view))view=DEFAULT;
    currentView=view;
    // 恒有一个分区是选中的（aria-pressed），并且同一时刻只有一个面板可见（一屏一问）。
    for(const [name,button] of buttons)button.setAttribute('aria-pressed',String(name===view));
    for(const [name,panel] of panels){if(panel)panel.classList.toggle('hidden',name!==view);}
    // 注意别写成 data-runtime-view：那个属性名已经被分区按钮占用了，
    // 写在页面上会让 [data-runtime-view] 多匹配到一个元素（契约测试第一版就踩了这个）。
    page.dataset.activeView=view;
    const title=document.getElementById('title');
    if(title)title.textContent=`项目与运行 · ${LABELS[view]}`;
    if(load)run(view);
    return view;
  }

  window.RuntimeView={
    views:VIEWS.slice(),
    get current(){return currentView;},
    show,
    on(view,loader){
      const entries=loaders.get(view)||[];
      entries.push(loader);
      loaders.set(view,entries);
    },
  };

  // 侧边栏进入「项目与运行」：回到上次看的分区并刷新它（第一次进来是运行总览）。
  tab.addEventListener('click',()=>show(currentView));
  for(const [view,button] of buttons)button.addEventListener('click',()=>show(view));
  // 初始状态以 index.html 里的标记为准（总览的 aria-pressed=true、只有它的面板不带 hidden），
  // 这里**不**去动 DOM：启动时就显隐面板会让 storage.js 以为「总览正在显示」，
  // 于是在用户还没点过这一页的时候就先发一次 Neo4j 检查请求。
})();
