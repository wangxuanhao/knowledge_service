/* Displayed releases are pinned into submissions; drafts never become extraction input. */
(() => {
  'use strict';
  function releaseLabel(release) {
    if (!release) return '实际本体版本未知';
    if (release.mixed) return '涉及多个本体版本';
    const label = Number.isInteger(release.version) && release.version > 0
      ? `本体 v${release.version}` : '本体版本号未知';
    return label + (release.version_reused ? ' · 标注修订' : '');
  }

  function createController({readVersions, readDraftState, onChange = () => {}}) {
    let generation = 0;
    let current = {projectId: '', mode: '', status: 'idle', release: null};
    const state = () => ({...current});
    async function refresh(projectId, mode) {
      const ticket = ++generation;
      current = {projectId, mode, status: projectId && mode === 'ontology' ? 'loading' : 'idle',
        release: null, openDraftCount: null, draftError: '', error: ''};
      onChange(state());
      if (current.status === 'idle') return state();
      const [versions, drafts] = await Promise.allSettled([
        readVersions(projectId), readDraftState(projectId),
      ]);
      if (ticket !== generation) return state();
      if (versions.status === 'fulfilled' && Array.isArray(versions.value?.versions)) {
        current.release = versions.value.versions.at(-1) || null;
        current.status = current.release?.id ? 'ready' : 'empty';
      } else {
        current.status = 'error';
        current.error = versions.reason?.message || '本体版本响应无效';
      }
      if (drafts.status === 'fulfilled' && Number.isInteger(drafts.value?.open_draft_count)) {
        current.openDraftCount = drafts.value.open_draft_count;
      } else current.draftError = drafts.reason?.message || '草案状态响应无效';
      onChange(state());
      return state();
    }
    function settings(projectId, mode) {
      if (mode !== 'ontology') return {};
      if (projectId !== current.projectId || current.mode !== mode || current.status !== 'ready') {
        throw Error('尚未取得可用的已发布本体，请先发布本体或刷新本体版本后再提交。');
      }
      return {ontology_id: current.release.id};
    }
    return {refresh, state, settings};
  }

  async function resultRelease(projectId, assertions, pinned, readVersions) {
    const ids = [...new Set((assertions || []).map(row => row.ontology_id || row.payload?.ontology_id).filter(Boolean))];
    const id = ids.length ? ids[0] : pinned?.id;
    if (!id) return null;
    if (ids.length > 1) return {id: ids.join('、'), mixed: true};
    if (pinned?.id === id && Number.isInteger(pinned.version)) return {...pinned};
    try {
      const data = await readVersions(projectId);
      return data.versions.find(item => item.id === id) || {id};
    } catch { return {id}; }
  }

  const helpers = {createController, releaseLabel, resultRelease};
  if (typeof module !== 'undefined') module.exports = helpers;
  if (typeof window === 'undefined') return;
  const base = project => '/api/projects/' + encodeURIComponent(project);
  const readVersions = project => api(base(project) + '/ontologies', undefined, 'GET');
  let host, modeControl;
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text || ''; return n; };
  const controller = createController({readVersions,
    readDraftState: project => api(base(project) + '/ontology-drafts/stage-availability', undefined, 'GET'),
    onChange: render});
  const projectId = () => document.getElementById('project')?.value || '';
  function refresh() { return controller.refresh(projectId(), modeControl?.value || ''); }
  function render(s) {
    if (!host) return;
    host.hidden = s.mode !== 'ontology';
    host.replaceChildren();
    if (host.hidden) return;
    const headline = node('strong');
    headline.textContent = !s.projectId ? '请先选择项目' : s.status === 'loading'
      ? '正在读取本次提取的本体版本…' : s.status === 'ready'
        ? '本次提取依据：' + releaseLabel(s.release) + ' · 已发布'
        : s.status === 'empty' ? '尚无已发布本体，请先完成本体发布'
          : '本体版本读取失败：' + s.error;
    host.dataset.state = s.status;
    host.append(headline);
    if (s.release) {
      const date = new Date(s.release.created_at);
      const time = Number.isNaN(date.getTime()) ? s.release.created_at || '未知' : date.toLocaleString('zh-CN', {hour12: false});
      host.append(node('p', '发布时间：' + time));
      host.append(node('p', '提交后固定使用此版本；可刷新以获取最新发布版本。'));
      if (s.release.note) host.append(node('p', '发布说明：' + s.release.note));
      const details = node('details');
      details.append(node('summary', '查看本体'));
      details.append(node('p', '版本 ID：' + s.release.id));
      for (const [key, label] of [['classes', '类'], ['relations', '关系'], ['attributes', '属性']]) {
        const items = s.release.summary?.[key] || [];
        details.append(node('p', `${label}（${items.length}）：` + (items.map(item => item.label_zh || item.label || item.name || item.id).join('、') || '暂无')));
      }
      host.append(details);
    }
    if (s.openDraftCount > 0) host.append(node('p', `有 ${s.openDraftCount} 份未发布草案，尚未生效；` +
      (s.release ? `本次仍使用${releaseLabel(s.release)}。` : s.status === 'empty'
        ? '请完成审核并发布后再提取。' : '请先恢复本体版本读取。')));
    if (s.draftError) host.append(node('p', '未发布草案状态读取失败：' + s.draftError));
    const actions = node('div'); actions.className = 'ingest-ontology-actions';
    const reload = node('button', '刷新版本'); reload.type = 'button'; reload.className = 'secondary';
    reload.disabled = !s.projectId || s.status === 'loading'; reload.addEventListener('click', refresh);
    const view = node('button', '前往本体建模'); view.type = 'button'; view.className = 'secondary';
    view.disabled = !s.projectId;
    view.addEventListener('click', () => window.KnowledgeNavigation.open('ontology-model', {projectId: s.projectId})
      .catch(error => status(error.message, true)));
    actions.append(reload, view); host.append(actions);
  }
  function mount(mode, after) {
    modeControl = mode;
    host = node('section'); host.id = 'ingest-ontology-version'; host.className = 'ingest-ontology-version';
    host.setAttribute('aria-live', 'polite'); after.after(host);
    document.addEventListener('ontology-model:published', event => {
      if (event.detail?.projectId === projectId()) refresh();
    });
    document.querySelector('[data-tab="ingest"]')?.addEventListener('click', refresh);
    window.addEventListener('focus', refresh);
    refresh();
  }
  window.IngestOntology = {...helpers, mount, refresh, settings: controller.settings, state: controller.state,
    resolveResult: (project, assertions, pinned) => resultRelease(project, assertions, pinned, readVersions)};
})();
