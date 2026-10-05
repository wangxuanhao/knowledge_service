/* ============================================================================
   auth.js —— 前端认证：令牌管理 / fetch 自动鉴权 / 独立登录页跳转 / 按角色显隐
   ----------------------------------------------------------------------------
   设计要点：
   1. 令牌存 localStorage（kg_token），并补丁 window.fetch：同源 /api/ 请求
      自动带上 Authorization: Bearer，业务代码（app.js 的 api() 等）无需改动；
   2. 启动门控：主页脚本先等 window.__authReady。auth.js 在确认登录成功后才
      resolve 它；**无令牌或令牌失效时整页跳到独立登录页 /login**，不用浮层；
   3. 任何请求返回 401（且本来带了令牌）时清空令牌并跳登录页（掉线统一处理）；
   4. 三档角色对应的 body 类：
        is-superadmin 仅超级管理员；
        is-admin      超级管理员或管理员（能写入，CSS 据此显示 .admin-only）；
        is-viewer     只读用户。
      真正拦截在后端中间件，前端隐藏只是体验。
   5. 服务端没启用鉴权（测试桩 / KG_AUTH_DISABLED=1，health.auth_enabled 为假）
      时直接放行，不跳登录页。
   ============================================================================ */
(function () {
    'use strict';

    var TOKEN_KEY = 'kg_token';

    // ── 令牌存取 ────────────────────────────────────────────────────────────
    function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } }
    function setToken(t) { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} }
    function clearToken() { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} }

    var currentUser = null;
    // 服务端是否真的启用鉴权：null=还不知道，true=启用（要先登录），false=没启用。
    // 只有**确知启用**才要求登录 —— 测试桩与 KG_AUTH_DISABLED=1 的起法没有这个字段，
    // 若按"未知也要登录"处理，这些页面会永远被跳去登录页（没有账号可登）。
    var authEnforced = null;
    var policyProbe = null;

    // ── 跳独立登录页 ──────────────────────────────────────────────────────────
    // 带上当前路径作为 next，登录后回到他本来要去的页面。
    function goLogin() {
        var here = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.replace('/login?next=' + here);
    }

    // ── 补丁全局 fetch：自动注入鉴权头 + 集中处理 401 ────────────────────────
    var originalFetch = window.fetch.bind(window);
    function isApiRequest(url) {
        // 既认相对路径（'/api/...'），也认同源绝对地址（'http://host/api/...'）。
        if (!url) return false;
        if (url.indexOf('/api/') === 0) return true;
        try {
            var parsed = new URL(url, window.location.href);
            return parsed.origin === window.location.origin
                && parsed.pathname.indexOf('/api/') === 0;
        } catch (e) { return false; }
    }
    window.fetch = function (input, init) {
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        var isApi = isApiRequest(url);
        var token = getToken();
        // 登录接口本身不带令牌（否则会被当成"掉线"误处理）。
        if (token && isApi && url.indexOf('/api/auth/login') === -1) {
            init = init || {};
            var headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
            if (!headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + token);
            init.headers = headers;
        }
        return originalFetch(input, init).then(function (response) {
            // 401 统一处理：令牌失效 / 过期 / 被改口令 / 被停用 → 清登录态并跳登录页。
            // 只在"本来带着令牌"时才算掉线，避免未登录首屏的杂散 401 触发跳转循环。
            if (response.status === 401 && isApi && getToken()) {
                clearToken();
                goLogin();
            }
            return response;
        });
    };

    // ── 角色与界面 ────────────────────────────────────────────────────────────
    function canWrite(user) { return !!user && (user.role === 'admin' || user.role === 'superadmin'); }

    function applyRole(user) {
        currentUser = user;
        var writer = canWrite(user);
        var isSuper = !!user && user.role === 'superadmin';
        // is-admin 表示"能写入"（超管也算）；is-superadmin 单独标出最高权限。
        document.body.classList.toggle('is-admin', writer);
        document.body.classList.toggle('is-superadmin', isSuper);
        document.body.classList.toggle('is-viewer', !writer);
        renderSession(user);
    }

    function roleBadge(role) {
        if (role === 'superadmin') return { cls: 'is-superadmin', text: '超级管理员' };
        if (role === 'admin') return { cls: 'is-admin', text: '管理员' };
        return { cls: 'is-viewer', text: '只读用户' };
    }

    function renderSession(user) {
        var box = document.getElementById('session');
        if (!box) { return; }
        if (!user) { box.hidden = true; return; }
        var badge = roleBadge(user.role);
        var name = user.display_name || user.username;
        box.hidden = false;
        box.innerHTML =
            '<div class="session-who"><span class="session-role ' + badge.cls + '">' + badge.text + '</span>' +
            '<span title="' + escapeHtml(user.username) + '">' + escapeHtml(name) + '</span></div>' +
            // 只读账号要有一句"我不能做什么"：只藏按钮，用户会以为功能坏了。
            '<p class="session-hint">只读账号：能检索、问答、看脑图；写入入口已隐藏，'
            + '要写入请联系管理员调整你的角色。</p>' +
            '<div class="session-actions">' +
            // id 不能用 auth-password/auth-username（避免与其它表单重名，历史上踩过）。
            '  <button type="button" class="session-btn" id="auth-open-password">改口令</button>' +
            // 管理员可见（超管 + 管理员）：后端同样只放行这两类。
            '  <button type="button" class="session-btn admin-only" id="auth-open-users">用户与权限</button>' +
            '  <button type="button" class="session-btn session-logout" id="auth-logout">退出登录</button>' +
            '</div>';
        document.getElementById('auth-logout').addEventListener('click', logout);
        document.getElementById('auth-open-password').addEventListener('click', function () {
            if (window.UserAdmin && window.UserAdmin.openPasswordChange) {
                window.UserAdmin.openPasswordChange();
            } else {
                notice('改口令界面没加载成功（user-admin.js 未加载），请刷新重试。', 'error');
            }
        });
        var usersBtn = document.getElementById('auth-open-users');
        if (usersBtn) {
            usersBtn.addEventListener('click', function () {
                if (window.UserAdmin && window.UserAdmin.open) {
                    window.UserAdmin.open();
                } else {
                    notice('用户与权限界面没加载成功（user-admin.js 未加载），请刷新重试。', 'error');
                }
            });
        }
    }

    // 页面内轻提示：复用 app.js 的 noticeCard，独立加载时降级为控制台一句。
    function notice(text, tone) {
        if (typeof window.noticeCard === 'function') { window.noticeCard(text, { tone: tone }); return; }
        if (window.console) console.warn(text);
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function logout() {
        clearToken();
        currentUser = null;
        // 退出后直接去登录页（整页跳转，不残留上一个身份的界面状态）。
        goLogin();
    }

    // ── __authReady：控制 app.js 启动时机 ─────────────────────────────────────
    var readyResolve = null;
    function prepareReady() {
        window.__authReady = new Promise(function (resolve) { readyResolve = resolve; });
    }
    function resolveReady() { if (readyResolve) readyResolve(); readyResolve = null; }
    prepareReady();

    // 业务模块等待登录就绪：
    //   1. 先确保策略探针已发出（本函数可能早于 bootstrap 被调用）；
    //   2. 只有确知服务端启用鉴权才等 __authReady，否则直接放行；
    //   3. 调用时**现读** window.__authReady（跳转/重登不会在主页发生，但保持口径）。
    function whenReady() {
        loadAuthPolicy();
        var settle = policyProbe ? policyProbe.catch(function () {}) : Promise.resolve();
        return settle.then(function () {
            if (authEnforced !== true) return;        // 未启用 / 读不到策略 → 放行
            var ready = window.__authReady;
            return (ready && typeof ready.then === 'function') ? ready : undefined;
        });
    }

    // ── 给其它模块用的最小接口（user-admin.js 依赖它读身份、写回新会话）─────────
    window.Auth = {
        whenReady: whenReady,
        user: function () { return currentUser; },
        isAdmin: function () { return canWrite(currentUser); },
        isSuperAdmin: function () { return !!currentUser && currentUser.role === 'superadmin'; },
        logout: logout,
        // 改完口令后换上新令牌：否则手上的令牌因口令指纹变化立刻失效，变成"改完就掉线"。
        adoptSession: function (data) {
            if (!data || !data.access_token) return;
            setToken(data.access_token);
            applyRole(data.user);
        },
        notice: notice
    };

    // 读服务端认证策略（/api/health 是公开路径，无需令牌）。读不到保持"未知"。
    function loadAuthPolicy() {
        if (policyProbe) return policyProbe;          // 幂等：只探一次
        policyProbe = originalFetch('/api/health').then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (typeof data.auth_enabled === 'boolean') authEnforced = data.auth_enabled;
            }).catch(function () {});
        return policyProbe;
    }

    // ── 启动：先探策略，再决定放行还是校验登录 ────────────────────────────────
    function bootstrap() {
        loadAuthPolicy().then(function () {
            // 服务端明确没启用鉴权：按"完全放开"渲染，与接入认证前一致。
            if (authEnforced === false) { releaseWithoutAuth(); return; }
            checkSession();
        });
    }

    // 服务端没启用鉴权：写入口都显示，不摆会话区（没有登录用户），直接放行业务。
    function releaseWithoutAuth() {
        document.body.classList.add('is-admin');
        var box = document.getElementById('session');
        if (box) box.hidden = true;
        resolveReady();
    }

    // 校验登录态：无令牌跳登录页；有令牌回查 /me，通过才放行，否则跳登录页。
    function checkSession() {
        var token = getToken();
        if (!token) { goLogin(); return; }
        originalFetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
            .then(function (resp) {
                return resp.json().catch(function () { return {}; })
                    .then(function (d) { return { ok: resp.ok, d: d }; });
            })
            .then(function (r) {
                if (r.ok && r.d.user) {
                    applyRole(r.d.user);
                    resolveReady();
                } else {
                    clearToken();
                    goLogin();
                }
            })
            .catch(function () { clearToken(); goLogin(); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap);
    } else {
        bootstrap();
    }
})();
