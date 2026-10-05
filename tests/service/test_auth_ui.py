# -*- coding: utf-8 -*-
"""登录 / 三级角色 / 用户管理的真浏览器契约（系统 Edge，headless）。

本轮认证从「浮层」改为「独立登录页」，所以契约也拆成两块：

A. 独立登录页（/login，加载真实 login.html / login.js）：
   - 已登录会话访问 /login 会自动进主页；
   - 口令错误留在登录页并显示中文原因；
   - 登录成功存令牌并回跳（?next= 只允许站内相对路径，防开放重定向）。

B. 工作台（/，加载真实 auth.js / user-admin.js）：
   - 无令牌整页跳到 /login（不再用浮层）；
   - 登录态回查通过才放行；角色决定写入口显隐（viewer 看不到写入口和用户管理）；
   - 运行中收到 401（带令牌时）清令牌并跳回 /login；
   - 「用户与权限」抽屉：超级管理员行受保护、自己那行不给降/停/删、
     管理员不出现「超级管理员」选项。

为什么必须真浏览器：令牌注入发生在被补丁的 window.fetch 里、写入口显隐是
body 类 + CSS 的联合结果、未登录跳转是导航行为 —— 这些读源码都证明不了。

做法：本地 HTTP 源（localStorage 在 file:// 下不可用）。/assets/* 指向仓库
web/ 真文件；/api/* 由注入的 fetch 桩应答，测试全程不碰真实服务端。
"""
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

# 各页面共享的服务端桩脚本：必须在真实脚本之前装好（真实 auth.js/login.js 会再包一层 fetch）。
# 通过 window.__preset 覆写初始值（在本脚本之前注入）。
MOCK_SCRIPT = """
window.__calls = [];
window.__mock = Object.assign({
  token: 'tok-first',
  meStatus: 200,
  meUser: {id: 'u-admin', username: 'admin', display_name: '系统管理员', role: 'superadmin'},
  loginStatus: 200,
  loginDetail: '用户名或口令不正确',
  loginUser: {id: 'u-admin', username: 'admin', display_name: '系统管理员', role: 'superadmin'},
  users: [],
  health: {status: 'ok', auth_enabled: true},
  force401: []
}, window.__preset || {});
// 令牌只在**首次文档**注入（用 sessionStorage 记一次）：
// 否则 401 跳到 /login 后，本 init script 又把令牌写回去，登录页会误判"已登录"再弹回 /。
if (window.__preset && window.__preset.initialToken
    && !sessionStorage.getItem('__token_injected')) {
  try {
    localStorage.setItem('kg_token', window.__preset.initialToken);
    sessionStorage.setItem('__token_injected', '1');
  } catch (e) {}
}
window.fetch = async (input, options) => {
  options = options || {};
  const url = typeof input === 'string' ? input : input.url;
  const absolute = new URL(url, window.location.href);
  const path = absolute.pathname;
  const method = options.method || 'GET';
  const headers = {};
  const raw = options.headers || {};
  if (typeof raw.forEach === 'function') raw.forEach((v, k) => { headers[String(k).toLowerCase()] = v; });
  else Object.keys(raw).forEach(k => { headers[String(k).toLowerCase()] = raw[k]; });
  window.__calls.push({path, url: absolute.href, method, headers,
                       body: options.body ? JSON.parse(options.body) : null});
  const reply = (body, status) => new Response(JSON.stringify(body),
    {status: status || 200, headers: {'Content-Type': 'application/json'}});
  if (window.__mock.force401.some(p => path.startsWith(p))) {
    return reply({detail: '登录状态已失效（口令可能已修改），请重新登录', code: 'stale_credentials'}, 401);
  }
  if (path === '/api/health') return reply(window.__mock.health);
  if (path === '/api/auth/login') {
    if (window.__mock.loginStatus !== 200) return reply({detail: window.__mock.loginDetail}, window.__mock.loginStatus);
    return reply({access_token: window.__mock.token, token_type: 'bearer', user: window.__mock.loginUser});
  }
  if (path === '/api/auth/me') {
    if (window.__mock.meStatus !== 200) return reply({detail: '未登录'}, window.__mock.meStatus);
    return reply({user: window.__mock.meUser});
  }
  if (path === '/api/users' && method === 'GET') return reply({items: window.__mock.users});
  if (path.indexOf('/api/users/') === 0) return reply({ok: true});
  return reply({});
};
"""

# 独立登录页宿主：结构对齐真实 login.html 的关键 id。
LOGIN_SHELL = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/assets/auth.css">
</head><body class="login-body"><main class="login-page">
<section class="auth-card login-card">
  <div class="auth-brand">知序<small>KNOWLEDGE SERVICE</small></div>
  <h1>登录</h1>
  <form id="login-form">
    <label for="login-username">用户名</label>
    <input id="login-username" name="username">
    <label for="login-password">口令</label>
    <input id="login-password" name="password" type="password">
    <p class="auth-error" id="login-error"></p>
    <button type="submit" class="auth-submit" id="login-submit">登 录</button>
  </form>
</section></main>
<script>%s</script>
<script src="/assets/login.js"></script>
</body></html>""" % MOCK_SCRIPT

# 工作台宿主：保留两个 admin-only 导航项作为"viewer 看不到写入口"的探针。
WORKBENCH_SHELL = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/assets/auth.css">
</head><body>
<aside>
  <nav>
    <button data-tab="search">检索与交互图谱</button>
    <button data-tab="ingest" class="admin-only">知识写入</button>
    <button data-tab="ontology-model" class="admin-only">本体建模层</button>
  </nav>
  <div id="session" class="session-box"></div>
</aside>
<main><p id="page">工作台</p></main>
<script>%s</script>
<script src="/assets/auth.js"></script>
<script src="/assets/user-admin.js"></script>
<script>
// 模拟 app.js：业务启动必须等 __authReady。
window.__appStarted = false;
window.__authReady.then(() => { window.__appStarted = true; });
</script>
</body></html>""" % MOCK_SCRIPT


class _Handler(SimpleHTTPRequestHandler):
    """路由：/login 给登录页宿主，/ 给工作台宿主，/assets/* 映射仓库 web/ 真文件。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def translate_path(self, path):
        if path.startswith('/assets/'):
            path = '/' + path[len('/assets/'):]
        return super().translate_path(path)

    def do_GET(self):
        route = self.path.split('?')[0]
        if route in ('/', '/index.html'):
            self._serve(WORKBENCH_SHELL)
            return
        if route == '/login':
            self._serve(LOGIN_SHELL)
            return
        return super().do_GET()

    def _serve(self, html):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope='module')
def browser():
    """普通 launch 的 Edge：各测试再用 new_context() 拿**完全隔离**的上下文。

    不用 module 级共享的 persistent context —— 那会让所有测试共用同一 origin 的
    localStorage：上一个登录测试存的令牌，会让后面的登录页自动跳主页、工作台
    不跳登录（表现为 fill 超时 / 等待 URL 超时），而且这是真实的跨用例污染，
    不是产品缺陷。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(EDGE))
        try:
            yield browser
        finally:
            browser.close()


# 记录本模块打开的上下文，结束时统一关闭（避免上下文对象累积）。
_open_contexts = []


def _open(browser, site, route='/', mock=None):
    context = browser.new_context()
    _open_contexts.append(context)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    if mock:
        page.add_init_script('window.__preset = ' + json.dumps(mock) + ';')
    page.goto(site + route)
    page.__errors = errors
    return page


def _calls(page, path, method=None):
    return [c for c in page.evaluate('window.__calls')
            if c['path'] == path and (method is None or c['method'] == method)]


# ============================================================================
# A. 独立登录页
# ============================================================================

def test_login_page_submits_credentials_and_goes_home(browser, site):
    page = _open(browser, site, '/login')
    page.fill('#login-username', 'admin')
    page.fill('#login-password', 'admin123')
    # mock 在本地就把登录处理掉（不产生真实网络响应），所以判据是：跳转回 / 且令牌已写入。
    page.click('#login-submit')
    page.wait_for_url('**/', timeout=8000)
    assert page.evaluate("localStorage.getItem('kg_token')") == 'tok-first'


def test_login_page_wrong_password_stays_and_explains(browser, site):
    page = _open(browser, site, '/login', mock={'loginStatus': 401})
    page.fill('#login-username', 'admin')
    page.fill('#login-password', 'bad-pass')
    page.click('#login-submit')
    page.wait_for_function("() => document.getElementById('login-error').textContent.length > 0")
    assert page.locator('#login-error').inner_text().strip() == '用户名或口令不正确'
    # 仍停在 /login（URL 没变），且没存令牌。
    assert '/login' in page.url
    assert page.evaluate("localStorage.getItem('kg_token')") is None
    assert page.__errors == [], page.__errors


def test_login_page_validates_locally_before_request(browser, site):
    page = _open(browser, site, '/login')
    page.fill('#login-username', 'admin')
    page.fill('#login-password', '123')   # 少于 6 位
    page.click('#login-submit')
    assert '口令至少 6 位' in page.locator('#login-error').inner_text()
    assert _calls(page, '/api/auth/login') == [], '本地校验没过不许发请求'


def test_login_page_redirects_home_if_already_logged_in(browser, site):
    # 令牌在页面脚本运行前就位：进 /login 应自动跳到主页。
    page = _open(browser, site, '/login', mock={'initialToken': 'tok-first'})
    page.wait_for_url('**/', timeout=8000)
    assert '/login' not in page.url


# ============================================================================
# B. 工作台：未登录跳转 / 角色显隐 / 401 跳回
# ============================================================================

def test_workbench_without_token_redirects_to_login(browser, site):
    page = _open(browser, site, '/')
    page.wait_for_url('**/login?**', timeout=8000)
    # next 带上了原始路径，登录后能回到工作台。
    assert 'next=%2F' in page.url


def test_workbench_with_valid_session_starts_and_shows_write_entries(browser, site):
    # 令牌在页面脚本运行前就位，单次 goto / 即进入（不先被重定向到 /login）。
    page = _open(browser, site, '/', mock={'initialToken': 'tok-first'})
    page.wait_for_function('() => window.__appStarted === true')
    assert page.evaluate("document.body.classList.contains('is-admin')")
    assert page.locator('button.admin-only').first.is_visible()
    # 会话区显示超管徽章与「用户与权限」。
    assert '超级管理员' in page.locator('#session').inner_text()
    assert page.locator('#auth-open-users').is_visible()
    assert page.__errors == [], page.__errors


def test_workbench_viewer_hides_write_entries_and_user_admin(browser, site):
    viewer = {'id': 'u-alice', 'username': 'alice', 'display_name': '小艾', 'role': 'viewer'}
    page = _open(browser, site, '/',
                 mock={'meUser': viewer, 'initialToken': 'tok-first'})
    page.wait_for_function('() => window.__appStarted === true')
    assert page.evaluate("document.body.classList.contains('is-viewer')")
    assert page.locator('button.admin-only').first.is_hidden(), 'viewer 不该看到写入口'
    assert page.locator('#auth-open-users').count() == 0 or \
           page.locator('#auth-open-users').is_hidden(), '用户管理是管理面'
    assert page.locator('#auth-open-password').is_visible(), '改自己口令两种角色都可以'
    assert '写入入口已隐藏' in page.locator('#session').inner_text()


def test_workbench_401_while_working_redirects_back_to_login(browser, site):
    page = _open(browser, site, '/', mock={'initialToken': 'tok-first'})
    page.wait_for_function('() => window.__appStarted === true')
    # 让之后的业务请求统一 401，再发一次。
    page.evaluate("window.__mock.force401 = ['/api/projects']")
    page.evaluate("fetch('/api/projects')")
    page.wait_for_url('**/login**', timeout=8000)
    assert page.evaluate("localStorage.getItem('kg_token')") is None, '令牌必须被清掉'


# ============================================================================
# C. 「用户与权限」抽屉
# ============================================================================

SUPER_ROW = {'id': 'u-admin', 'username': 'admin', 'display_name': '系统管理员',
             'role': 'superadmin', 'is_active': True, 'created_at': '2026-10-05T02:00:00+00:00'}
ALICE_ROW = {'id': 'u-alice', 'username': 'alice', 'display_name': '小艾',
             'role': 'viewer', 'is_active': True, 'created_at': '2026-10-05T03:00:00+00:00'}
BOSS_ROW = {'id': 'u-boss', 'username': 'boss', 'display_name': '老板',
            'role': 'admin', 'is_active': True, 'created_at': '2026-10-05T04:00:00+00:00'}


def _open_drawer(browser, site, *, me=SUPER_ROW, users):
    # /me 返回操作者身份；令牌在页面脚本运行前就位，单次 goto / 即进入工作台，
    # 不会先被重定向到 /login（那会让后续 evaluate 撞上导航、上下文销毁）。
    page = _open(browser, site, '/',
                 mock={'meUser': me, 'users': users, 'initialToken': 'tok-first'})
    page.wait_for_function('() => window.__appStarted === true')
    page.click('#auth-open-users')
    page.wait_for_selector('.ua-overlay:not([hidden])')
    return page


def test_drawer_superadmin_row_is_protected(browser, site):
    page = _open_drawer(browser, site, users=[SUPER_ROW, ALICE_ROW])
    super_row = page.locator('.ua-table tbody tr', has_text='admin')
    # 超管行不出现降/停/删按钮，只给一句保护说明。
    assert '超级管理员受系统保护' in super_row.inner_text()
    for label in ('降为', '停用', '删除'):
        assert super_row.locator('.ua-action', has_text=label).count() == 0


def test_drawer_self_row_has_no_demote_disable_delete(browser, site):
    # 用一个 admin 身份登录，清单里包含他自己（boss）。
    page = _open_drawer(browser, site, me=BOSS_ROW, users=[SUPER_ROW, BOSS_ROW, ALICE_ROW])
    self_row = page.locator('.ua-table tbody tr.is-self')
    assert self_row.count() == 1
    assert '不能改自己' in self_row.inner_text()
    for label in ('降为', '停用', '删除'):
        assert self_row.locator('.ua-action', has_text=label).count() == 0
    assert self_row.locator('.ua-action', has_text='重置口令').count() == 1


def test_drawer_admin_sees_no_superadmin_option(browser, site):
    # 管理员视角：新建账号下拉与提升动作都不出现「超级管理员」。
    page = _open_drawer(browser, site, me=BOSS_ROW, users=[SUPER_ROW, BOSS_ROW, ALICE_ROW])
    options = page.locator('[data-ua-new-role] option').all_inner_texts()
    joined = ''.join(options)
    assert '超级管理员' not in joined, '管理员不能开超管账号'
    # alice 是 viewer：其提升动作只到「管理员」，不到超管。
    alice_row = page.locator('.ua-table tbody tr', has_text='alice')
    assert alice_row.locator('.ua-action', has_text='设为管理员').count() == 1
    assert alice_row.locator('.ua-action', has_text='超级管理员').count() == 0


def test_drawer_superadmin_sees_superadmin_option(browser, site):
    page = _open_drawer(browser, site, me=SUPER_ROW,
                        users=[SUPER_ROW, BOSS_ROW, ALICE_ROW])
    options = ''.join(page.locator('[data-ua-new-role] option').all_inner_texts())
    assert '超级管理员' in options
    # admin 行可被提升为超管。
    boss_row = page.locator('.ua-table tbody tr', has_text='boss')
    assert boss_row.locator('.ua-action', has_text='设为超级管理员').count() == 1


def test_drawer_says_what_it_manages_and_normal_account_gets_delete(browser, site):
    page = _open_drawer(browser, site, users=[SUPER_ROW, ALICE_ROW])
    assert '不管项目里的知识与本体' in page.locator('.ua-sub').inner_text()
    alice_row = page.locator('.ua-table tbody tr', has_text='alice')
    assert alice_row.locator('.ua-action', has_text='删除').count() == 1
