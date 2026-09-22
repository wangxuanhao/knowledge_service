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
      <div class="projects-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
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
        <p class="project-created">创建于 ${esc(p.created_at || '—')}</p>
        <p class="project-id" title="项目 ID：接口路径、日志、向量库分区都用它定位">
          <span>项目 ID</span>
          <code>${esc(p.id)}</code>
          <button type="button" class="project-id-copy" data-copy-id="${esc(p.id)}" aria-label="复制项目 ID">复制</button>
        </p>
        <div class="project-counts">
          <div class="project-count"><strong>${counts.documents ?? 0}</strong><span>文档</span></div>
          <div class="project-count"><strong>${counts.entities ?? 0}</strong><span>实体</span></div>
          <div class="project-count"><strong>${counts.relations ?? 0}</strong><span>关系</span></div>
          <div class="project-count"><strong>${counts.chunks ?? 0}</strong><span>片段</span></div>
        </div>
        <div class="project-actions">
          <button data-switch="${esc(p.id)}">切换</button>
          <button data-rename="${esc(p.id)}" class="secondary">重命名</button>
          <button data-delete="${esc(p.id)}" class="danger">删除</button>
        </div>
      </article>`;
  }

  /* ---- actions ---- */
  async function switchProject(id){
    try{
      await projects();
      $('project').value = id;
      $('project').onchange();
      const name = $('project').selectedOptions[0]?.textContent || id;
      status('已切换到项目：' + name);
      await renderProjects();
    }catch(e){
      status(e.message, true);
    }
  }

  function openRename(id){
    const project = projectsCache.find(p => p.id === id);
    if(!project) return;
    const {overlay, close} = openModal({
      title: '重命名项目',
      body: `<label>项目名称<input id="projects-rename-input" maxlength="200"></label>`,
      footer: `<button id="projects-rename-confirm">保存</button><button class="secondary" id="projects-rename-cancel">取消</button>`
    });
    const field = overlay.querySelector('#projects-rename-input');
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
        <div class="projects-delete-warning">删除不可恢复，将同时删除该项目全部记录、本体与产物。请输入项目名称或勾选确认后删除。</div>
        <label>项目名称<input id="projects-delete-input" maxlength="200" placeholder="输入项目名称以确认"></label>
        <div class="projects-confirm-row">
          <input type="checkbox" id="projects-delete-confirm">
          <label for="projects-delete-confirm">我确认删除该项目</label>
        </div>`,
      footer: `<button id="projects-delete-confirm-btn" class="danger" disabled>删除</button><button class="secondary" id="projects-delete-cancel">取消</button>`
    });
    const input = overlay.querySelector('#projects-delete-input');
    const checkbox = overlay.querySelector('#projects-delete-confirm');
    const confirmBtn = overlay.querySelector('#projects-delete-confirm-btn');
    const update = ()=>{
      const typed = input.value.trim() === project.name;
      confirmBtn.disabled = !(typed || checkbox.checked);
    };
    input.addEventListener('input', update);
    checkbox.addEventListener('change', update);
    overlay.querySelector('#projects-delete-cancel').onclick = close;
    confirmBtn.onclick = async ()=>{
      confirmBtn.disabled = true;
      status('处理中…');
      try{
        const r = await api('/api/projects/' + encodeURIComponent(id), undefined, 'DELETE');
        await projects();
        if(current === id){
          current = '';
          $('project').value = '';
          $('project').onchange();
          $('dashboard').textContent = '';
        }
        const deleted = r.deleted || {};
        status(`项目已删除：${project.name}（记录 ${deleted.records ?? 0} · 本体 ${deleted.ontologies ?? 0} · 产物 ${deleted.artifacts ?? 0}）`);
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
        <label>知识建模方式<select id="project-ontology-mode"><option value="ontology">加载默认本体 · 直接构建正式图谱</option><option value="discovery">开放本体发现 · 先抽取候选再归纳</option><option value="documents">仅文档检索 · 不抽取图谱</option></select></label>`,
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
        const mode = overlay.querySelector('#project-ontology-mode').value;
        const p = await api('/api/projects', {name, ontology_mode:mode, use_default_ontology:mode==='ontology'});
        await projects();
        $('project').value = p.id;
        $('project').onchange();
        status('项目已创建：' + name);
        close();
        await renderProjects();
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
          <button id="new-project">＋ 创建项目</button>
        </div>
        ${projects.length
          ? `<div class="projects-grid">${projects.map(projectCard).join('')}</div>`
          : `<div class="projects-empty"><div class="projects-empty-icon">📂</div><div class="projects-empty-title">暂无项目</div><div class="projects-empty-text">点击“＋ 创建项目”创建第一个知识项目</div></div>`}
      `;
      $('new-project').onclick = openCreate;
      root.querySelectorAll('[data-switch]').forEach(b=>b.onclick=()=>switchProject(b.dataset.switch));
      root.querySelectorAll('[data-rename]').forEach(b=>b.onclick=()=>openRename(b.dataset.rename));
      root.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>openDelete(b.dataset.delete));
      root.querySelectorAll('[data-copy-id]').forEach(b=>b.onclick=()=>copyProjectId(b));
    }catch(e){
      if(epoch !== renderEpoch) return;
      root.innerHTML = `<div class="panel"><p class="subtle">加载项目列表失败：${esc(e.message)}</p></div>`;
    }
  }

  /* re-render whenever the 项目管理 tab is shown; also render once on load */
  document.querySelector('[data-tab="projects"]').addEventListener('click', renderProjects);
  renderProjects();
})();
