/* ============================================================================
 * 知识写入 · 纯文件上传入口（ingest-upload.js）
 * ----------------------------------------------------------------------------
 * 2026-10-05：按用户拍板，移除「对话式文本框」——本页只有一件事：
 *   把规则文件拖进来 → 选择「解析模式」→ 点「上传并处理」。
 *
 * 本脚本很轻：上传/解析的真正提交仍由 workbench.js 的 #ingest 绑定统一负责，
 * 队列与校验仍由 ingest-mode.js 负责（依赖方向不变，不另造一套）。
 * 这里只负责：① 顶部一句话说清本页做什么、不做什么；
 *             ② 进入本页时给一次明确的就绪提示。
 * ========================================================================== */
(() => {
  'use strict';

  // 在面板标题下补一句「使用说明」：做什么 / 不做什么，避免再被当成聊天或提示词输入。
  function addPurpose() {
    const panel = document.querySelector('#tab-ingest .document-upload-panel');
    if (!panel || panel.querySelector('.ingest-upload-purpose')) return;
    const p = document.createElement('p');
    p.className = 'ingest-upload-purpose';
    p.textContent = '这一页只做「把文件解析成知识」：拖入规则文件，选好解析模式，点上传并处理。'
      + '知识的修改、删除、合并不在这里 —— 去「知识台账」。';
    // 插到标题区之后、解析模式槽之前，阅读顺序最顺。
    const slot = document.getElementById('ingest-mode-slot');
    if (slot) panel.insertBefore(p, slot); else panel.append(p);
  }

  // 切到本页时触发一次（不自动提交、不轮询，避免后台空转）。
  const tab = document.querySelector('[data-tab="ingest"]');
  if (tab) {
    tab.addEventListener('click', () => {
      addPurpose();
    });
  }

  // 首屏：脚本随页面加载，若已在本页就直接补说明。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addPurpose);
  } else {
    addPurpose();
  }
})();
