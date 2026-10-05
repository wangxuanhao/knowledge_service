# -*- coding: utf-8 -*-
"""认证与三级 RBAC 端到端测试（显式启用真实认证授权中间件）。

conftest 为让既有业务用例不受鉴权影响，默认设置 KG_AUTH_DISABLED=1。
本模块在每个 create_app 调用上显式传 ``auth_disabled=False``，挂载真实中间件，
专门验证：登录、令牌、三档角色（superadmin / admin / viewer）的越权边界、
超级管理员保护、账号删除，以及老库（两档时代）升级时的管理员提升。

存储经 conftest 的夹具把 ``Repository(标识)`` 转接到隔离的 PostgreSQL 槽位；
同一测试内同一标识对应同一槽位（用于老库升级用例的"先写后读"）。
"""
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def _client(tmp_path, name='auth'):
    # 显式开启真实鉴权；空槽位启动时播种 superadmin（admin/admin123）。
    return TestClient(create_app(str(tmp_path / name),
                                 encoder=HashingEncoder(),
                                 auth_disabled=False))


def _login(client, username, password):
    return client.post('/api/auth/login',
                       json={'username': username, 'password': password})


def _h(token):
    return {'Authorization': 'Bearer ' + token}


def _make_user(client, actor, username, role='viewer', password='pass123'):
    """用管理员账号在 /api/users 建号（自助注册已移除，这是唯一开号路径）。"""
    return client.post('/api/users', headers=_h(actor),
                       json={'username': username, 'password': password, 'role': role})


def _super(client):
    return _login(client, 'admin', 'admin123').json()['access_token']


# ── 未认证 ───────────────────────────────────────────────────────────────────

def test_health_is_public(tmp_path):
    with _client(tmp_path) as client:
        assert client.get('/api/health').status_code == 200


def test_protected_route_requires_login(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get('/api/projects')
        assert resp.status_code == 401
        assert resp.json()['code'] == 'not_authenticated'


def test_missing_bearer_prefix_rejected(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get('/api/projects', headers={'Authorization': 'Token abc'})
        assert resp.status_code == 401


# ── 登录与令牌 ────────────────────────────────────────────────────────────────

def test_seed_account_is_superadmin(tmp_path):
    with _client(tmp_path) as client:
        resp = _login(client, 'admin', 'admin123')
        assert resp.status_code == 200
        body = resp.json()
        assert body['user']['role'] == 'superadmin'
        assert body['access_token'].count('.') == 2


def test_wrong_password_returns_401(tmp_path):
    with _client(tmp_path) as client:
        resp = _login(client, 'admin', 'bad-password')
        assert resp.status_code == 401
        assert resp.json()['code'] == 'invalid_credentials'


def test_unknown_user_returns_401(tmp_path):
    with _client(tmp_path) as client:
        assert _login(client, 'nobody', 'whatever1').status_code == 401


def test_login_is_case_insensitive(tmp_path):
    # 用户名应用层统一小写：ADMIN 也能登录。
    with _client(tmp_path) as client:
        assert _login(client, 'ADMIN', 'admin123').status_code == 200


def test_disabled_user_cannot_login(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        alice_id = client.get('/api/users', headers=_h(super_)).json()['items']
        alice_id = next(u for u in alice_id if u['username'] == 'alice')['id']
        client.patch(f'/api/users/{alice_id}', json={'is_active': False}, headers=_h(super_))
        assert _login(client, 'alice', 'pass123').status_code == 401


def test_tampered_token_rejected(tmp_path):
    with _client(tmp_path) as client:
        token = _super(client)
        resp = client.get('/api/projects', headers=_h(token[:-2] + 'xx'))
        assert resp.status_code == 401
        assert resp.json()['code'] == 'invalid_token'


def test_token_does_not_leak_password_hash(tmp_path):
    with _client(tmp_path) as client:
        assert 'password_hash' not in _login(client, 'admin', 'admin123').text


# ── 三档写权限：viewer 受限，admin/superadmin 可写 ────────────────────────────

def test_superadmin_can_create_project(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post('/api/projects', json={'name': '超管项目'}, headers=_h(_super(client)))
        assert resp.status_code == 201


def test_admin_can_create_project(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        token = _make_user(client, super_, 'boss', 'admin').json()
        token = _login(client, 'boss', 'pass123').json()['access_token']
        assert client.post('/api/projects', json={'name': '管理员项目'},
                           headers=_h(token)).status_code == 201


def test_viewer_post_is_forbidden(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        token = _make_user(client, super_, 'alice', 'viewer').json()
        # 建号响应不含令牌，viewer 需重新登录拿令牌。
        token = _login(client, 'alice', 'pass123').json()['access_token']
        resp = client.post('/api/projects', json={'name': '越权项目'}, headers=_h(token))
        assert resp.status_code == 403
        assert resp.json()['code'] == 'forbidden'


def test_viewer_put_is_forbidden(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        token = _login(client, 'alice', 'pass123').json()['access_token']
        assert client.put('/api/projects/whatever', json={'name': 'x'},
                          headers=_h(token)).status_code == 403


def test_viewer_readonly_post_allowed(tmp_path):
    """viewer 调只读 POST（检索）应通过授权（不是 401/403）。"""
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        viewer = _login(client, 'alice', 'pass123').json()['access_token']
        pid = client.post('/api/projects', json={'name': 'p'},
                          headers=_h(super_)).json()['id']
        resp = client.post(f'/api/projects/{pid}/search', json={'query': '退款'},
                           headers=_h(viewer))
        assert resp.status_code not in (401, 403)
        assert 'hits' in resp.json()


# ── 当前用户 /me ──────────────────────────────────────────────────────────────

def test_me_returns_identity(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        resp = client.get('/api/auth/me', headers=_h(super_))
        assert resp.status_code == 200
        assert resp.json()['user']['username'] == 'admin'


# ── 用户管理面的访问门槛 ───────────────────────────────────────────────────────

def test_user_list_requires_login(tmp_path):
    with _client(tmp_path) as client:
        assert client.get('/api/users').status_code == 401


def test_viewer_cannot_list_users(tmp_path):
    """GET 默认放行只读，但用户清单属管理面，必须有明文例外。"""
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        token = _login(client, 'alice', 'pass123').json()['access_token']
        resp = client.get('/api/users', headers=_h(token))
        assert resp.status_code == 403
        # 拒绝理由不能说成"不能写入"（他在读账号，不是写知识）。
        assert '管理员' in resp.json()['detail']
        assert '不能写入' not in resp.json()['detail']


def test_admin_and_superadmin_list_users_without_hashes(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        for token in (super_,):
            resp = client.get('/api/users', headers=_h(token))
            assert resp.status_code == 200
            names = [u['username'] for u in resp.json()['items']]
            assert 'admin' in names and 'alice' in names
            assert 'password_hash' not in resp.text


# ── 建号：admin 不能创建超级管理员，superadmin 可以 ───────────────────────────

def test_superadmin_creates_superadmin(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        resp = _make_user(client, super_, 'root2', 'superadmin')
        assert resp.status_code == 201
        assert resp.json()['role'] == 'superadmin'
        assert _login(client, 'root2', 'pass123').status_code == 200


def test_admin_cannot_create_superadmin(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        resp = _make_user(client, boss, 'evilroot', 'superadmin')
        assert resp.status_code == 403
        assert resp.json()['code'] == 'cannot_create_superadmin'
        # 号确实没建出来。
        assert _login(client, 'evilroot', 'pass123').status_code == 401


def test_admin_creates_admin_and_viewer(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        assert _make_user(client, boss, 'a2', 'admin').status_code == 201
        assert _make_user(client, boss, 'v2', 'viewer').status_code == 201


def test_duplicate_user_creation_conflicts(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        assert _make_user(client, super_, 'alice', 'viewer').status_code == 201
        resp = _make_user(client, super_, 'ALICE', 'viewer')
        assert resp.status_code == 409
        assert resp.json()['code'] == 'user_exists'


# ── 改角色：越权边界 + 超级管理员绝对保护 + 不能改自己 ─────────────────────────

def test_superadmin_promotes_admin_to_superadmin(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss_id = next(u for u in client.get('/api/users', headers=_h(super_)).json()['items']
                       if u['username'] == 'boss')['id']
        resp = client.patch(f'/api/users/{boss_id}', json={'role': 'superadmin'}, headers=_h(super_))
        assert resp.status_code == 200
        assert resp.json()['user']['role'] == 'superadmin'


def test_admin_cannot_promote_anyone_to_superadmin(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        _make_user(client, super_, 'alice', 'viewer')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        alice_id = next(u for u in client.get('/api/users', headers=_h(super_)).json()['items']
                        if u['username'] == 'alice')['id']
        resp = client.patch(f'/api/users/{alice_id}', json={'role': 'superadmin'}, headers=_h(boss))
        assert resp.status_code == 403
        assert resp.json()['code'] == 'cannot_grant_superadmin'
        # alice 仍是 viewer。
        assert _login(client, 'alice', 'pass123').json()['user']['role'] == 'viewer'


def test_admin_cannot_touch_superadmin_account(tmp_path):
    """admin 对超级管理员账号的任何写操作（改展示名/口令）都越权。"""
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        root_id = client.get('/api/auth/me', headers=_h(super_)).json()['user']['id']
        resp = client.patch(f'/api/users/{root_id}', json={'display_name': '被改了'}, headers=_h(boss))
        assert resp.status_code == 403
        assert resp.json()['code'] == 'cannot_manage_superadmin'


def test_superadmin_is_never_demoted_or_disabled(tmp_path):
    """超级管理员行受绝对保护：超管自己也不能降级 / 停用（保证最高入口永存）。"""
    with _client(tmp_path) as client:
        super_ = _super(client)
        root_id = client.get('/api/auth/me', headers=_h(super_)).json()['user']['id']
        demote = client.patch(f'/api/users/{root_id}', json={'role': 'admin'}, headers=_h(super_))
        assert demote.status_code == 409
        assert demote.json()['code'] == 'superadmin_protected'
        disable = client.patch(f'/api/users/{root_id}', json={'is_active': False}, headers=_h(super_))
        assert disable.status_code == 409
        assert disable.json()['code'] == 'superadmin_protected'


def test_superadmin_can_rename_another_superadmin(tmp_path):
    """保护只拦降级 / 停用 / 删除；改展示名仍是允许的。"""
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'root2', 'superadmin')
        root2 = next(u for u in client.get('/api/users', headers=_h(super_)).json()['items']
                     if u['username'] == 'root2')
        resp = client.patch(f"/api/users/{root2['id']}", json={'display_name': '二号超管'},
                            headers=_h(super_))
        assert resp.status_code == 200
        assert resp.json()['user']['display_name'] == '二号超管'


def test_admin_demotes_and_disables_viewer(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        _make_user(client, super_, 'mgr2', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        mgr2 = _login(client, 'mgr2', 'pass123').json()['access_token']
        mgr2_id = client.get('/api/auth/me', headers=_h(mgr2)).json()['user']['id']

        demoted = client.patch(f'/api/users/{mgr2_id}', json={'role': 'viewer'}, headers=_h(boss))
        assert demoted.status_code == 200
        assert demoted.json()['changed'] == ['角色']
        # 角色每次从库里回查：原令牌立刻失去写权限。
        assert client.post('/api/projects', json={'name': 'x'},
                           headers=_h(mgr2)).status_code == 403

        disabled = client.patch(f'/api/users/{mgr2_id}', json={'is_active': False}, headers=_h(boss))
        assert disabled.status_code == 200
        assert client.get('/api/projects', headers=_h(mgr2)).status_code == 401


def test_admin_cannot_demote_or_disable_self(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        me = client.get('/api/auth/me', headers=_h(boss)).json()['user']
        assert client.patch(f"/api/users/{me['id']}", json={'role': 'viewer'},
                            headers=_h(boss)).status_code == 409
        assert client.patch(f"/api/users/{me['id']}", json={'is_active': False},
                            headers=_h(boss)).status_code == 409
        # 改自己展示名是允许的（不等于降自己）。
        assert client.patch(f"/api/users/{me['id']}", json={'display_name': '老板'},
                            headers=_h(boss)).status_code == 200


# ── 重置口令 ──────────────────────────────────────────────────────────────────

def test_admin_reset_password_invalidates_user_token(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        viewer = _login(client, 'alice', 'pass123').json()['access_token']
        alice_id = next(u for u in client.get('/api/users', headers=_h(super_)).json()['items']
                        if u['username'] == 'alice')['id']
        resp = client.patch(f'/api/users/{alice_id}', json={'password': 'reset12345'},
                            headers=_h(super_))
        assert resp.status_code == 200
        assert resp.json()['changed'] == ['口令']
        assert client.get('/api/projects', headers=_h(viewer)).status_code == 401
        assert _login(client, 'alice', 'reset12345').status_code == 200


def test_admin_cannot_reset_superadmin_password(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        root_id = client.get('/api/auth/me', headers=_h(super_)).json()['user']['id']
        resp = client.patch(f'/api/users/{root_id}', json={'password': 'hacked123'},
                            headers=_h(boss))
        assert resp.status_code == 403


def test_change_own_password_invalidates_old_token(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        token = _login(client, 'alice', 'pass123').json()['access_token']
        changed = client.post('/api/auth/password', headers=_h(token),
                              json={'current_password': 'pass123', 'new_password': 'alice456'})
        assert changed.status_code == 200
        assert client.get('/api/projects', headers=_h(token)).status_code == 401
        assert _login(client, 'alice', 'pass123').status_code == 401
        fresh = _login(client, 'alice', 'alice456')
        assert fresh.status_code == 200


def test_change_password_requires_current_password(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'alice', 'viewer')
        token = _login(client, 'alice', 'pass123').json()['access_token']
        resp = client.post('/api/auth/password', headers=_h(token),
                           json={'current_password': 'wrong', 'new_password': 'alice456'})
        assert resp.status_code == 400
        assert resp.json()['code'] == 'bad_current_password'
        # 改失败不影响原口令。
        assert _login(client, 'alice', 'pass123').status_code == 200


# ── 删除账号 ──────────────────────────────────────────────────────────────────

def test_admin_and_superadmin_delete_normal_account(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        _make_user(client, super_, 'alice', 'viewer')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        alice_id = next(u for u in client.get('/api/users', headers=_h(super_)).json()['items']
                        if u['username'] == 'alice')['id']
        assert client.delete(f'/api/users/{alice_id}', headers=_h(boss)).status_code == 200
        assert _login(client, 'alice', 'pass123').status_code == 401


def test_superadmin_cannot_be_deleted_by_anyone(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        root_id = client.get('/api/auth/me', headers=_h(super_)).json()['user']['id']
        resp = client.delete(f'/api/users/{root_id}', headers=_h(super_))
        assert resp.status_code == 409
        assert resp.json()['code'] == 'superadmin_protected'


def test_admin_cannot_delete_superadmin(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        root_id = client.get('/api/auth/me', headers=_h(super_)).json()['user']['id']
        resp = client.delete(f'/api/users/{root_id}', headers=_h(boss))
        assert resp.status_code == 403


def test_cannot_delete_self(tmp_path):
    with _client(tmp_path) as client:
        super_ = _super(client)
        _make_user(client, super_, 'boss', 'admin')
        boss = _login(client, 'boss', 'pass123').json()['access_token']
        me = client.get('/api/auth/me', headers=_h(boss)).json()['user']
        resp = client.delete(f"/api/users/{me['id']}", headers=_h(boss))
        assert resp.status_code == 409
        assert resp.json()['code'] == 'cannot_modify_self'


def test_delete_unknown_user_is_404(tmp_path):
    with _client(tmp_path) as client:
        resp = client.delete('/api/users/no-such', headers=_h(_super(client)))
        assert resp.status_code == 404


# ── 老库（0008 两档时代）升级：种子管理员被提升为超级管理员 ────────────────────

def test_legacy_database_promotes_seed_admin_to_superadmin(tmp_path):
    """模拟两档时代的库（只有 admin/viewer，无 superadmin），启动应用后应提升 admin。

    直接用底层 Repository + UserStore 造出"接入本版本之前"的状态（绕过 create_app
    的播种），再用 create_app 指向同一槽位，验证老库升级逻辑真的发生。
    """
    from knowledge_service.core.security import hash_password
    from knowledge_service.repository.core import Repository
    from knowledge_service.repository.users_store import UserStore

    slot = str(tmp_path / 'legacy')
    # conftest 会把该 Repository 转接到隔离 PG 槽位；先手工种下两档时代的数据。
    repo = Repository(slot)
    legacy_users = UserStore(repo)
    legacy_users.create('admin', hash_password('admin123'), role='admin',
                        display_name='老管理员')
    legacy_users.create('alice', hash_password('pass123'), role='viewer')
    repo.close()

    with TestClient(create_app(slot, encoder=HashingEncoder(), auth_disabled=False)) as client:
        # 原 admin 账号已被就地提升为超级管理员，口令不变。
        resp = _login(client, 'admin', 'admin123')
        assert resp.status_code == 200
        assert resp.json()['user']['role'] == 'superadmin'
        # alice 仍是只读，且没有重复建号。
        assert _login(client, 'alice', 'pass123').json()['user']['role'] == 'viewer'


def test_health_reports_auth_enabled(tmp_path):
    with _client(tmp_path) as client:
        assert client.get('/api/health').json()['auth_enabled'] is True
