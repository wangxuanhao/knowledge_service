/* Project management view. No native browser prompt/confirm APIs. */
(function(){
  const root = $('projects-root');
  if(!root) return;
  let projectsCache = [];
  let renderEpoch = 0;

  /* ---- custom modal (overlay + panel), ESC and click-outside close ---- */
  function openModal({title, body, footer}){
    const overlay = document.createElement('div');
    overlay.className = 'projects-modal-overlay';
    overlay.innerHTML = `
      <div class="projects-modal management-drawer" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="projects-modal-header">
          <h3 class="projects-modal-title">${esc(title)}</h3>
          <button type="button" class="projects-modal-close" aria-label="关闭">×</button>
        </div>
        <div class="projects-modal-body">${body}</div>
        ${footer ? `<div class="projects-modal-footer">${footer}</div>` : ''}
      </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(()=>overlay.classList.add('active'));
    const close = ()=>{
      overlay.classList.remove('active');
      setTimeout(()=>overlay.remove(), 200);
      document.removeEventListener('keydown', onKey);
    };
    const onKey = e=>{ if(e.key==='Escape') close(); };
    document.addEventListener('keydown', onKey);
    overlay.querySelector('.projects-modal-close').onclick = close;
    overlay.addEventListener('click', e=>{ if(e.target===overlay) close(); });
    return {overlay, close};
  }

  /* ---- 复制项目 ID：排查链路时直接用（接口路径/日志/向量库分区都是这个 ID） ---- */
  async function copyProjectId(button){
    const id = button.dataset.copyId;
    try{
      if(!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(id);
      const text = button.textContent;
      button.textContent = '已复制';
      setTimeout(()=>{ button.textContent = text; }, 1500);
    }catch{
      // 非安全上下文（http 下 clipboard 可能不可用）→ 退回选中文本，让用户 Ctrl+C
      const code = button.parentElement.querySelector('code');
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      status('已选中项目 ID，请按 Ctrl+C 复制');
    }
  }

  /* ---- project card ---- */
  function projectCard(p){
    const isCurrent = p.id === current;
    const counts = p.counts || {};
    return `
      <article class="project-card ${isCurrent ? 'current' : ''}">
        <div class="project-card-head">
          <h3>${esc(p.name)}</h3>
          ${isCurrent ? '<span class="project-current-badge">当前项目</span>' : ''}
        </div>
        <p class="project-created">创建于 ${esc(p.created_at || '—')} · ${Number.isInteger(p.ontology_publications)?(p.ontology_publications?'本体已发布 · '+p.ontology_publications+' 个发布快照':'尚未发布本体'):'前往本体建模层查看发布状态'}</p>
        <p class="project-id" title="项目 ID：接口路径、日志、向量库分区都用它定位">
          <span>项目 ID</span>
          <code>${esc(p.id)}</code>
          <button type="button" class="project-id-copy" data-copy-id="${esc(p.id)}" aria-label="复制项目 ID">复制</button>
        </p>
        <div class="project-counts">
          <div class="project-count"><strong>${counts.documents ?? counts.document ?? '待获取'}</strong><span>文档</span></div>
          <div class="project-count"><strong>${counts.entities ?? counts.entity ?? '待获取'}</strong><span>实体</span></div>
          <div class="project-count"><strong>${counts.relations ?? counts.relation ?? '待获取'}</strong><span>关系</span></div>
          <div class="project-count"><strong>${counts.chunks ?? counts.chunk ?? '待获取'}</strong><span>片段</span></div>
        </div>
        <div class="project-actions">
          <button data-switch="${esc(p.id)}">进入项目</button>
          <button data-rename="${esc(p.id)}" class="secondary">设置</button>

        </div>
      </article>`;
  }

  /* ---- actions ---- */
  async function switchProject(id){
    try{
      await projects();
      $('project').value = id;
      $('project').dispatchEvent(new Event('change'));
      const name = $('project').selectedOptions[0]?.textContent || id;
      status('已切换到项目：' + name);
      const chosen=projectsCache.find(item=>item.id===id);window.KnowledgeTabs.open((chosen?.counts?.documents||0)>0?'qa':'sources');
    }catch(e){
      status(e.message, true);
    }
  }

  function openRename(id){
    const project = projectsCache.find(p => p.id === id);
    if(!project) return;
    const {overlay, close} = openModal({
      title: '项目设置',
      body: `<label>项目名称<input id="projects-rename-input" maxlength="200"></label><p class="subtle">处理方式在每次上传时选择。</p><div class="project-settings-links"><button id="project-index-config" class="secondary">项目向量化</button><button id="project-overview" class="secondary">项目概览</button><button id="project-backup" class="secondary">知识备份</button></div><hr><button id="project-remove" class="danger">删除项目…</button>`,
      footer: `<button id="projects-rename-confirm">保存</button><button class="secondary" id="projects-rename-cancel">取消</button>`
    });
    const field = overlay.querySelector('#projects-rename-input');
    overlay.querySelector('#project-index-config').onclick=()=>{close();window.ProjectVectorIndex.open(id).catch(e=>status(e.message,true));};
    overlay.querySelector('#project-remove').onclick=()=>{close();openDelete(id);};
    for(const [button,view] of [['project-overview','overview'],['project-backup','snapshots']])overlay.querySelector('#'+button).onclick=async()=>{await projects();$('project').value=id;$('project').dispatchEvent(new Event('change'));close();window.RuntimeView.show(view,{load:false});window.KnowledgeTabs.open('runtime');};
    field.value = project.name;
    field.focus();
    field.select();
    overlay.querySelector('#projects-rename-cancel').onclick = close;
    overlay.querySelector('#projects-rename-confirm').onclick = async ()=>{
      const name = field.value.trim();
      if(!name){ status('请输入项目名称', true); return; }
      const button = overlay.querySelector('#projects-rename-confirm');
      button.disabled = true;
      status('处理中…');
      try{
        await api('/api/projects/' + encodeURIComponent(id), {name}, 'PUT');
        await projects();
        status('项目已重命名：' + name);
        close();
        await renderProjects();
      }catch(e){
        status(e.message, true);
        button.disabled = false;
      }
    };
  }

  function openDelete(id){
    const project = projectsCache.find(p => p.id === id);
    if(!project) return;
    const {overlay, close} = openModal({
      title: '删除项目',
      body: `
        <div class="projects-delete-warning">删除不可恢复，将同时删除该项目全部记录、本体与产物。请输入项目名称后删除。</div>
        <label>项目名称<input id="projects-delete-input" maxlength="200" placeholder="输入项目名称以确认"></label>
`,
      footer: `<button id="projects-delete-confirm-btn" class="danger" disabled>删除</button><button class="secondary" id="projects-delete-cancel">取消</button>`
    });
    const input = overlay.querySelector('#projects-delete-input');
    const confirmBtn = overlay.querySelector('#projects-delete-confirm-btn');
    const update = ()=>{
      const typed = input.value.trim() === project.name;
      confirmBtn.disabled = !typed;
    };
    input.addEventListener('input', update);
    overlay.querySelector('#projects-delete-cancel').onclick = close;
    confirmBtn.onclick = async ()=>{
      confirmBtn.disabled = true;
      status('处理中…');
      try{
        const r = await api('/api/projects/' + encodeURIComponent(id), undefined, 'DELETE');
        await projects();
        if(current === id){
          window.WorkspaceContext?.store.removeProject(id);
          try{localStorage.removeItem('kg_qa_v1_'+id);sessionStorage.removeItem('knowledge-project');}catch{}
          $('project').value = [...$('project').options].find(option=>option.value)?.value||'';
          $('project').dispatchEvent(new Event('change'));
          $('dashboard').textContent = '';
        }
        const deleted = r.deleted || {};
        const cleaned=r.cleaned||r.cleared||{};const pending=['milvus','neo4j'].filter(target=>cleaned[target]!==true);
        status(`项目已删除：${project.name}（记录 ${deleted.records ?? 0} · 本体 ${deleted.ontologies ?? 0} · 产物 ${deleted.artifacts ?? 0}）${pending.length?'；派生投影清理待核验：'+pending.join('、'):'；派生投影清理已确认'}`);
        close();
        await renderProjects();
      }catch(e){
        status(e.message, true);
        confirmBtn.disabled = false;
      }
    };
  }

  /* ---- create project (modal: 头部只有一个“创建项目”入口，弹窗内“创建”是提交动作，
     与重命名/删除弹窗交互保持一致，避免“入口按钮 + 展开表单里再一个创建按钮”的重复) ---- */
  function openCreate(){
    const {overlay, close} = openModal({
      title: '创建项目',
      body: `
        <label>项目名称<input id="create-name" maxlength="200" placeholder="新项目名称"></label>
        <p class="subtle">先创建空项目，再添加文件。处理时可选仅文档、按已有本体抽取或开放发现。</p>`,
      footer: `<button id="create-project">创建</button><button class="secondary" id="cancel-project">取消</button>`
    });
    const nameField = overlay.querySelector('#create-name');
    nameField.focus();
    overlay.querySelector('#cancel-project').onclick = close;
    overlay.querySelector('#create-project').onclick = async ()=>{
      const button = overlay.querySelector('#create-project');
      const name = nameField.value.trim();
      if(!name){ status('请输入项目名称', true); return; }
      button.disabled = true;
      status('处理中…');
      try{
        const p = await api('/api/projects', window.WorkspaceContext.projectPayload(name));
        await projects();
        $('project').value = p.id;
        $('project').dispatchEvent(new Event('change'));
        status('项目已创建：' + name);
        close();
        window.KnowledgeTabs.open('sources');
      }catch(e){
        status(e.message, true);
        button.disabled = false;
      }
    };
  }

  /* ---- render ---- */
  async function renderProjects(){
    const epoch = ++renderEpoch;
    try{
      await (window.Auth&&window.Auth.whenReady?window.Auth.whenReady():Promise.resolve());
      if(epoch !== renderEpoch) return;
      const result = await api('/api/projects', undefined, 'GET');
      if(epoch !== renderEpoch) return;
      projectsCache = result.projects || [];
      const projects = projectsCache;
      root.innerHTML = `
        <div class="projects-header">
          <div>
            <h2>项目管理</h2>
            <p class="subtle">共 ${projects.length} 个项目 · 创建、重命名或删除项目</p>
          </div>
          <div class="row"><button id="new-project">＋ 创建项目</button></div>
        </div>
        <input id="projects-find" placeholder="查找项目名称" aria-label="查找项目" style="margin-bottom:16px;width:100%">
        ${projects.length
          ? `<div class="projects-grid">${projects.map(projectCard).join('')}</div>`
          : `<div class="projects-empty"><div class="projects-empty-icon">📂</div><div class="projects-empty-title">暂无项目</div><div class="projects-empty-text">点击“＋ 创建项目”创建第一个知识项目</div></div>`}
      `;
      $('new-project').onclick = openCreate;
      $('projects-find').oninput=()=>{const query=$('projects-find').value.trim().toLowerCase();root.querySelectorAll('.project-card').forEach(card=>{card.hidden=!card.querySelector('h3').textContent.toLowerCase().includes(query);});};
      root.querySelectorAll('[data-switch]').forEach(b=>b.onclick=()=>switchProject(b.dataset.switch));
      root.querySelectorAll('[data-rename]').forEach(b=>b.onclick=()=>openRename(b.dataset.rename));
      root.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>openDelete(b.dataset.delete));
      root.querySelectorAll('[data-copy-id]').forEach(b=>b.onclick=()=>copyProjectId(b));
    }catch(e){
      if(epoch !== renderEpoch) return;
      root.innerHTML = `<div class="panel"><p class="subtle">加载项目列表失败：${esc(e.message)}</p></div>`;
    }
  }

  // Navigation owns project-card reads. The shared project selector has its own
  // startup inventory; a hidden management page must not request it again.
  window.ProjectsView={refresh:renderProjects,create:openCreate};
})();
