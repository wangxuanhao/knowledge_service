/* ============================================================================
   login.js —— 独立登录页逻辑（login.html 专用）
   ----------------------------------------------------------------------------
   做什么：
     1. 进入页面先探测是否已登录（/api/auth/me）：已有有效会话就直接进主页，
        不必让已登录用户再填一次口令；
     2. 提交用户名 + 口令到 /api/auth/login，成功后存令牌（localStorage.kg_token）
        并跳转到主页 /（可带 ?next= 指定回跳地址，做了白名单限制防开放重定向）；
     3. 失败时在卡片内红字显示服务端给出的中文原因，不跳转。
   不做什么：不加载工作台业务代码、不提供自助注册（开号统一在「用户与权限」）。
   ============================================================================ */
(function () {
    'use strict';

    var TOKEN_KEY = 'kg_token';

    function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } }
    function setToken(t) { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} }

    // 只允许站内相对路径回跳：拒绝 //evil.com、https://evil.com 这类开放重定向。
    function safeNext() {
        var raw = new URLSearchParams(location.search).get('next') || '/';
        if (raw.charAt(0) === '/' && raw.charAt(1) !== '/') return raw;
        return '/';
    }

    function goHome() { window.location.replace(safeNext()); }

    function setError(msg) {
        var host = document.getElementById('login-error');
        if (host) host.textContent = msg || '';
    }

    // 已登录就直接进主页。401 / 网络异常都留在登录页（不弹窗打扰）。
    function redirectIfLoggedIn() {
        var token = getToken();
        if (!token) return;
        fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
            .then(function (resp) { if (resp.ok) goHome(); })
            .catch(function () {});
    }

    function submit(event) {
        event.preventDefault();
        var username = document.getElementById('login-username').value.trim();
        var password = document.getElementById('login-password').value;
        if (!username) { setError('请填写用户名'); return; }
        if (password.length < 6) { setError('口令至少 6 位'); return; }

        var button = document.getElementById('login-submit');
        button.disabled = true;
        setError('');
        fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: username, password: password })
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; })
                .then(function (data) { return { ok: resp.ok, data: data }; });
        }).then(function (r) {
            if (!r.ok) {
                setError((r.data && r.data.detail) || '登录失败，请稍后重试');
                return;
            }
            setToken(r.data.access_token);
            goHome();
        }).catch(function () {
            setError('网络异常，请稍后重试');
        }).finally(function () { button.disabled = false; });
    }

    document.getElementById('login-form').addEventListener('submit', submit);
    redirectIfLoggedIn();
})();
