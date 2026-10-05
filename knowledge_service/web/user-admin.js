/* ============================================================================
   user-admin.js —— 「用户与权限」抽屉（管理员 / 超级管理员）+ 「修改我的口令」
   ----------------------------------------------------------------------------
   这一页管什么、不管什么（页面上也这么写，不用猜）：
     * 管：谁能登录（账号）、谁能写（角色）、谁被停用；以及你改自己的口令。
     * 不管：项目里的知识、本体、版本 —— 那些在「知识台账」「本体建模层」做。

   三档角色，口径以后端为准（api/users.py + security_middleware.py）：
     · superadmin 超级管理员：能管理一切账号（含管理员），系统之根；
     · admin      管理员：能写入、建模发布，能管理 admin/viewer，但不能碰超管；
     · viewer     只读用户：只能检索、问答、看脑图。

   前端这里只"显示真相 + 发动作"，不自己编权限：
     · 角色 / 状态来自 GET /api/users，不在本地推算；
     · 按**当前操作者**身份隐藏他无权做的动作（管理员不摆"超管"选项）；
     · 超管行受保护：不摆降级 / 停用 / 删除，服务端也会拒绝（双保险）；
     · 自己那行不摆降级 / 停用 / 删除（会当场失去权限），改自己口令用左下角。
   ============================================================================ */
(function () {
    'use strict';

    var ROLE_TEXT = { superadmin: '超级管理员', admin: '管理员', viewer: '只读用户' };
    var ROLE_HINT = {
        superadmin: '系统最高权限：可写入、建模发布，并管理所有账号（含管理员）',
        admin: '可写入知识、建模发布，管理普通账号；不能管理超级管理员',
        viewer: '只能检索、问答、看脑图，不能写入'
    };
    // 角色等级：数字越小权限越高，用于判断"提升 / 降级"。
    var ROLE_RANK = { superadmin: 0, admin: 1, viewer: 2 };

    var drawer = null;        // 抽屉宿主
    var rowsHost = null;      // 账号清单 tbody
    var flashHost = null;     // 一句话回执
    var users = [];           // 最近一次读到的账号清单（渲染用）

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function el(tag, cls, html) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (html != null) node.innerHTML = html;
        return node;
    }

    function currentUser() { return (window.Auth && window.Auth.user()) || null; }
    function isSuper() { return (window.Auth && window.Auth.isSuperAdmin && window.Auth.isSuperAdmin()); }

    // 所有请求走 window.fetch —— auth.js 已补丁（自动带令牌，401 会跳登录页）。
    function api(path, options) {
        return window.fetch(path, options).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                return { ok: resp.ok, status: resp.status, data: data };
            });
        });
    }

    function flash(text, tone) {
        if (!flashHost) return;
        flashHost.textContent = text || '';
        flashHost.className = 'ua-flash' + (tone ? ' is-' + tone : '');
    }

    // 服务端错误文案优先用中文 detail，其次给状态码兜底。
    function failure(result, fallback) {
        var detail = result && result.data && result.data.detail;
        return detail || (fallback + '（HTTP ' + (result ? result.status : '?') + '）');
    }

    // ── 抽屉骨架 ───────────────────────────────────────────────────────────────
    function build() {
        drawer = el('div', 'ua-overlay');
        drawer.innerHTML =
            '<section class="ua-card" role="dialog" aria-modal="true" aria-labelledby="ua-title">' +
            '  <header class="ua-head">' +
            '    <div>' +
            '      <h2 id="ua-title">用户与权限</h2>' +
            '      <p class="ua-sub">这里管<b>账号与角色</b>，不管项目里的知识与本体。' +
            '        <b>超级管理员</b>：系统最高权限，能管理所有账号；' +
            '        <b>管理员</b>：能写入、建模发布，管理普通账号；' +
            '        <b>只读用户</b>：只能检索、问答、看脑图。' +
            '        给账号改口令或停用后，他<b>已登录的会话立刻失效</b>。</p>' +
            '    </div>' +
            '    <button type="button" class="ua-close" data-ua-close aria-label="关闭">×</button>' +
            '  </header>' +
            '  <p class="ua-flash" data-ua-flash></p>' +
            '  <section class="ua-create">' +
            '    <h3>新建账号</h3>' +
            '    <div class="ua-form">' +
            '      <label>用户名<input data-ua-new-name placeholder="登录名，统一转小写" autocomplete="off"></label>' +
            '      <label>初始口令<input data-ua-new-pass type="password" placeholder="至少 6 位" autocomplete="new-password"></label>' +
            '      <label>角色<select data-ua-new-role></select></label>' +
            '      <label>展示名（选填）<input data-ua-new-display placeholder="侧栏里显示的名字" autocomplete="off"></label>' +
            '      <button type="button" class="ua-primary" data-ua-create>创建账号</button>' +
            '    </div>' +
            '    <p class="ua-note">账号不提供自助注册，统一在这里开通。能开<b>超级管理员</b>的' +
            '      只有现有超级管理员；管理员可建管理员与只读账号。</p>' +
            '  </section>' +
            '  <section class="ua-list">' +
            '    <h3>账号清单 <small data-ua-count></small></h3>' +
            '    <table class="ua-table">' +
            '      <thead><tr><th>用户名</th><th>展示名</th><th>角色</th><th>状态</th>' +
            '        <th>创建时间</th><th class="ua-actions-col">动作</th></tr></thead>' +
            '      <tbody data-ua-rows></tbody>' +
            '    </table>' +
            '  </section>' +
            '</section>';
        document.body.appendChild(drawer);
        flashHost = drawer.querySelector('[data-ua-flash]');
        rowsHost = drawer.querySelector('[data-ua-rows]');
        drawer.querySelector('[data-ua-close]').addEventListener('click', close);
        drawer.addEventListener('click', function (event) {
            if (event.target === drawer) close();    // 点遮罩空白处关闭
        });
        drawer.querySelector('[data-ua-create]').addEventListener('click', createUser);
    }

    // 按操作者身份填充"新建账号"角色下拉：管理员不摆"超级管理员"（后端也会拒）。
    function fillRoleOptions() {
        var select = drawer.querySelector('[data-ua-new-role]');
        var options = isSuper()
            ? [['viewer', '只读用户 —— 可检索、问答、看脑图'],
               ['admin', '管理员 —— 可写入，并管理普通账号'],
               ['superadmin', '超级管理员 —— 系统最高权限']]
            : [['viewer', '只读用户 —— 可检索、问答、看脑图'],
               ['admin', '管理员 —— 可写入，并管理普通账号']];
        select.innerHTML = options.map(function (pair) {
            return '<option value="' + pair[0] + '">' + pair[1] + '</option>';
        }).join('');
    }

    function open() {
        if (!drawer) build();
        fillRoleOptions();
        drawer.hidden = false;
        flash('');
        load();
    }

    function close() { if (drawer) drawer.hidden = true; }

    // ── 读清单 ────────────────────────────────────────────────────────────────
    function load() {
        rowsHost.innerHTML = '<tr><td colspan="6" class="ua-empty">读取中…</td></tr>';
        api('/api/users').then(function (result) {
            if (!result.ok) {
                rowsHost.innerHTML = '';
                rowsHost.appendChild(el('tr', '', '<td colspan="6" class="ua-empty">'
                    + esc(failure(result, '读不到账号清单')) + '</td>'));
                return;
            }
            users = result.data.items || [];
            render();
        });
    }

    function formatTime(value) {
        if (!value) return '—';
        var date = new Date(value);
        if (isNaN(date.getTime())) return String(value);
        return date.toLocaleString('zh-CN', { hour12: false });
    }

    // 返回目标账号"相邻一档"的角色：超管↔管理员↔只读，用于单一按钮切换。
    function nextRole(role) {
        if (role === 'viewer') return 'admin';
        if (role === 'admin') return isSuper() ? 'superadmin' : 'viewer';
        return 'admin';   // superadmin → admin（但超管行受保护，不会真的摆降级）
    }

    function render() {
        var me = currentUser() || {};
        drawer.querySelector('[data-ua-count]').textContent = '共 ' + users.length + ' 个';
        rowsHost.innerHTML = '';
        users.forEach(function (item) {
            var isSelf = item.id === me.id;
            var tr = document.createElement('tr');
            if (isSelf) tr.className = 'is-self';

            tr.appendChild(el('td', 'ua-name', esc(item.username)
                + (isSelf ? '<small class="ua-self-tag">（你）</small>' : '')));
            tr.appendChild(el('td', '', esc(item.display_name || '—')));
            tr.appendChild(el('td', '', '<span class="ua-role is-' + esc(item.role) + '" title="'
                + esc(ROLE_HINT[item.role] || '') + '">' + esc(ROLE_TEXT[item.role] || item.role) + '</span>'));
            tr.appendChild(el('td', '', item.is_active
                ? '<span class="ua-state is-on">启用</span>'
                : '<span class="ua-state is-off">已停用</span>'));
            tr.appendChild(el('td', 'ua-time', esc(formatTime(item.created_at))));

            tr.appendChild(el('td', 'ua-actions-col'));
            renderActions(tr.lastChild, item, isSelf);
            rowsHost.appendChild(tr);
        });
    }

    // 按"目标账号 + 是不是自己 + 操作者身份"渲染动作按钮。
    function renderActions(host, item, isSelf) {
        // ① 超管行受系统保护：谁都不能降级 / 停用 / 删除（含他自己）。
        if (item.role === 'superadmin') {
            host.appendChild(el('span', 'ua-self-note',
                '超级管理员受系统保护：不能降级、停用或删除，系统始终保留这个最高入口。'));
            return;
        }
        // ② 自己那行（管理员/只读）不摆降级 / 停用 / 删除，会当场失去权限。
        if (isSelf) {
            host.appendChild(el('span', 'ua-self-note',
                '不能改自己：会当场失去管理权限。改自己口令请用左下角「改口令」。'));
            appendResetPassword(host, item);
            return;
        }
        // ③ 普通账号（admin/viewer，且不是自己）：
        //    角色切换按钮：只摆"升到相邻一档 / 降到相邻一档"。管理员不摆"升超管"。
        var target = nextRole(item.role);
        var promoting = ROLE_RANK[target] < ROLE_RANK[item.role];
        // 管理员视角下，不允许把人往 superadmin 提（nextRole 已保证只有超管会返回 superadmin）。
        host.appendChild(actionButton(
            promoting ? '设为' + ROLE_TEXT[target] : '降为' + ROLE_TEXT[target],
            promoting ? ROLE_HINT[target] : '降级后他将不能再写入或建项目（已登录会话立刻按新角色生效）',
            function () {
                if (!promoting && !window.confirm(
                    '把「' + item.username + '」降为' + ROLE_TEXT[target] + '？\n\n'
                    + '他立刻不能再写入或建项目。')) { return; }
                patchUser(item, { role: target },
                    (promoting ? '已把「' : '已把「') + item.username + (promoting ? '」设为' : '」降为') + ROLE_TEXT[target]);
            }));
        // 停用 / 启用
        host.appendChild(actionButton(
            item.is_active ? '停用' : '启用',
            item.is_active ? '停用后他已登录的会话立刻失效，也不能再登录（账号与历史保留）' : '重新允许他登录',
            function () {
                if (item.is_active && !window.confirm(
                    '停用「' + item.username + '」？\n\n他已登录的会话会立刻失效，也不能再登录。')) { return; }
                patchUser(item, { is_active: !item.is_active }, item.is_active
                    ? '已停用「' + item.username + '」'
                    : '已重新启用「' + item.username + '」');
            }));
        appendResetPassword(host, item);
        // 删除：物理抹除。超管不在此路径（已在上面拦截）；管理员可删普通账号。
        host.appendChild(actionButton('删除', '物理删除该账号（不同于停用：账号与登录名一并移除）',
            function () {
                if (!window.confirm('删除账号「' + item.username + '」？\n\n'
                    + '这是物理删除，登录名会被释放，且无法恢复。如只是想阻止登录，建议改用「停用」。')) { return; }
                deleteUser(item);
            }));
    }

    function appendResetPassword(host, item) {
        host.appendChild(actionButton('重置口令', '给他换新口令（原口令作废，他已登录的会话立刻失效）',
            function () {
                var next = window.prompt('给「' + item.username + '」设置新口令（至少 6 位）：');
                if (next == null) return;
                if (next.length < 6) { flash('口令至少 6 位', 'error'); return; }
                patchUser(item, { password: next },
                    '已重置「' + item.username + '」的口令，他已登录的会话失效');
            }));
    }

    function actionButton(label, title, handler) {
        var button = el('button', 'ua-action', esc(label));
        button.type = 'button';
        button.title = title;
        button.addEventListener('click', handler);
        return button;
    }

    function patchUser(item, payload, successText) {
        flash('提交中…');
        api('/api/users/' + encodeURIComponent(item.id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (result) {
            if (!result.ok) { flash(failure(result, '操作没成功'), 'error'); return; }
            flash(successText, 'good');
            load();
        }).catch(function () { flash('网络异常，请稍后重试', 'error'); });
    }

    function deleteUser(item) {
        flash('提交中…');
        api('/api/users/' + encodeURIComponent(item.id), { method: 'DELETE' })
            .then(function (result) {
                if (!result.ok) { flash(failure(result, '删除没成功'), 'error'); return; }
                flash('已删除账号「' + item.username + '」', 'good');
                load();
            }).catch(function () { flash('网络异常，请稍后重试', 'error'); });
    }

    function createUser() {
        var username = drawer.querySelector('[data-ua-new-name]').value.trim();
        var password = drawer.querySelector('[data-ua-new-pass]').value;
        var role = drawer.querySelector('[data-ua-new-role]').value;
        var displayName = drawer.querySelector('[data-ua-new-display]').value.trim();
        if (!username) { flash('请填写用户名', 'error'); return; }
        if (password.length < 6) { flash('口令至少 6 位', 'error'); return; }
        var button = drawer.querySelector('[data-ua-create]');
        button.disabled = true;
        flash('创建中…');
        api('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: username, password: password, role: role, display_name: displayName
            })
        }).then(function (result) {
            if (!result.ok) { flash(failure(result, '创建没成功'), 'error'); return; }
            flash('已创建「' + result.data.username + '」（' + ROLE_TEXT[result.data.role] + '）', 'good');
            drawer.querySelector('[data-ua-new-name]').value = '';
            drawer.querySelector('[data-ua-new-pass]').value = '';
            drawer.querySelector('[data-ua-new-display]').value = '';
            load();
        }).catch(function () { flash('网络异常，请稍后重试', 'error'); })
          .finally(function () { button.disabled = false; });
    }

    // ── 修改我的口令 ──────────────────────────────────────────────────────────
    var pwDialog = null;

    function buildPasswordDialog() {
        pwDialog = el('div', 'ua-overlay');
        pwDialog.innerHTML =
            '<section class="ua-card ua-card-narrow" role="dialog" aria-modal="true" aria-labelledby="ua-pw-title">' +
            '  <header class="ua-head"><div>' +
            '    <h2 id="ua-pw-title">修改我的口令</h2>' +
            '    <p class="ua-sub">只改<b>自己</b>的口令。改完：这台设备会换上新令牌继续用，' +
            '      <b>其他设备上的登录立刻失效</b>（口令是"当前登录凭据"的一部分，这是故意的）。</p>' +
            '  </div><button type="button" class="ua-close" data-ua-pw-close aria-label="关闭">×</button></header>' +
            '  <p class="ua-flash" data-ua-pw-flash></p>' +
            '  <div class="ua-form ua-form-column">' +
            '    <label>当前口令<input data-ua-pw-current type="password" autocomplete="current-password"></label>' +
            '    <label>新口令（至少 6 位）<input data-ua-pw-new type="password" autocomplete="new-password"></label>' +
            '    <label>再输一次新口令<input data-ua-pw-again type="password" autocomplete="new-password"></label>' +
            '    <div class="ua-pw-actions">' +
            '      <button type="button" class="ua-primary" data-ua-pw-save>保存新口令</button>' +
            '      <button type="button" class="ua-action" data-ua-pw-close>取消</button>' +
            '    </div>' +
            '  </div>' +
            '</section>';
        document.body.appendChild(pwDialog);
        pwDialog.querySelectorAll('[data-ua-pw-close]').forEach(function (node) {
            node.addEventListener('click', function () { pwDialog.hidden = true; });
        });
        pwDialog.addEventListener('click', function (event) {
            if (event.target === pwDialog) pwDialog.hidden = true;
        });
        pwDialog.querySelector('[data-ua-pw-save]').addEventListener('click', savePassword);
        pwDialog.querySelector('[data-ua-pw-again]').addEventListener('keydown', function (event) {
            if (event.key === 'Enter') savePassword();
        });
    }

    function pwFlash(text, tone) {
        var host = pwDialog.querySelector('[data-ua-pw-flash]');
        host.textContent = text || '';
        host.className = 'ua-flash' + (tone ? ' is-' + tone : '');
    }

    function openPasswordChange() {
        if (!pwDialog) buildPasswordDialog();
        pwDialog.hidden = false;
        pwFlash('');
        ['[data-ua-pw-current]', '[data-ua-pw-new]', '[data-ua-pw-again]'].forEach(function (selector) {
            pwDialog.querySelector(selector).value = '';
        });
        pwDialog.querySelector('[data-ua-pw-current]').focus();
    }

    function savePassword() {
        var me = currentUser() || {};
        var currentPassword = pwDialog.querySelector('[data-ua-pw-current]').value;
        var next = pwDialog.querySelector('[data-ua-pw-new]').value;
        var again = pwDialog.querySelector('[data-ua-pw-again]').value;
        if (!currentPassword) { pwFlash('请填写当前口令', 'error'); return; }
        if (next.length < 6) { pwFlash('新口令至少 6 位', 'error'); return; }
        if (next !== again) { pwFlash('两次输入的新口令不一致', 'error'); return; }
        var button = pwDialog.querySelector('[data-ua-pw-save]');
        button.disabled = true;
        pwFlash('提交中…');
        api('/api/auth/password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_password: currentPassword, new_password: next })
        }).then(function (result) {
            if (!result.ok) { pwFlash(failure(result, '修改没成功'), 'error'); return null; }
            // 旧令牌因口令指纹变化失效：用新口令换一枚新令牌，避免"改完就被踢下线"。
            return api('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: me.username, password: next })
            }).then(function (login) {
                if (login.ok && window.Auth && window.Auth.adoptSession) {
                    window.Auth.adoptSession(login.data);
                    pwFlash('口令已更新：其他设备上的登录已失效。', 'good');
                } else {
                    pwFlash('口令已更新，请重新登录。', 'good');
                    if (window.Auth && window.Auth.logout) window.Auth.logout();
                }
            });
        }).catch(function () { pwFlash('网络异常，请稍后重试', 'error'); })
          .finally(function () { button.disabled = false; });
    }

    window.UserAdmin = {
        open: open,
        close: close,
        openPasswordChange: openPasswordChange
    };
})();
