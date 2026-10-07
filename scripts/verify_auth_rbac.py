# -*- coding: utf-8 -*-
"""认证 / 角色权限（RBAC）的真机复验 —— 对**真实运行中的服务**逐项打勾。

为什么需要它：16+14 条 pytest 用的是 TestClient 与 mock API，能证明"代码按约定回答"，
但证明不了"真实那台服务、真实那套账号、真实那枚令牌"上权限恰好是这么走的。
这个脚本对活服务发真请求，每一项都先算出期望值，再和服务端的实际回答对照。

验什么（每条都对应一个真实风险）：
  1. 未登录：业务接口一律 401；公开路径（/api/health）200；
  2. 一次完整的角色生命周期：建号(viewer) → 登录 → 提升为 admin → 写入真的能过
     → 降回 viewer → 写入被拒 → 停用 → 手里的令牌立刻失效且不能再登录；
  3. 拒绝理由要**说对话**：只读用户看用户清单时不能说"不能写入"（他在读，没在写）；
  4. 「查一条记录的证据」是只读动作：只读用户应走到业务层（404），**不是** 403
     —— 这是本轮修掉的越权白名单缺口；
  5. 改口令立刻作废该用户全部旧令牌（口令指纹），管理员重置口令同理；
  6. 三档角色的越权边界（本轮重点）：
       * 管理员不能创建超级管理员，也不能查看 / 修改 / 停用 / 删除超级管理员；
       * 超级管理员账号不能被降级 / 停用 / 删除（谁都不行，包括他自己）；
       * 管理员 / 超级管理员都不能降级或删除**当前登录的自己**（会当场失去权限）。

跑完自删：脚本用**超管令牌调 DELETE 接口**删掉自己建的临时账号 —— 顺带验证
「删除账号」这条产品路径真的通（应用角色对 users 有 DELETE：0001 的
ALTER DEFAULT PRIVILEGES 已授予 SELECT / INSERT / UPDATE / DELETE）。
用法：python scripts/verify_auth_rbac.py [--url http://127.0.0.1:8100] [--keep]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = '✅', '❌', '⚠️'
results: list[tuple[str, bool | None, str]] = []


def check(label: str, ok: bool | None, detail: str = '') -> None:
    results.append((label, ok, detail))
    mark = SKIP if ok is None else (PASS if ok else FAIL)
    print(f'{mark} {label}' + (f' —— {detail}' if detail else ''))


def call(base: str, path: str, method: str = 'GET', body=None, token: str | None = None):
    """发一次请求，返回 (状态码, 解码后的 JSON 或 {}）。"""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data:
        request.add_header('Content-Type', 'application/json')
    if token:
        request.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode('utf-8') or '{}'
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode('utf-8') or '{}'
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {'raw': raw[:200]}
    except urllib.error.URLError as error:
        return 0, {'detail': f'连不上服务：{error.reason}'}


def login(base: str, username: str, password: str):
    return call(base, '/api/auth/login', 'POST', {'username': username, 'password': password})


def main() -> int:
    parser = argparse.ArgumentParser(description='认证与权限的真机复验')
    parser.add_argument('--url', default='http://127.0.0.1:8100')
    parser.add_argument('--admin-user', default='admin')
    parser.add_argument('--admin-password', default='admin123',
                        help='初始管理员口令（默认值只适用于本地未改口令的库）')
    parser.add_argument('--keep', action='store_true', help='保留临时账号（默认跑完自删）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    status, health = call(base, '/api/health')
    check('服务在线且 /api/health 无需登录', status == 200 and health.get('status') == 'ok',
          f'HTTP {status}')

    status, _ = call(base, '/api/projects')
    check('未登录读业务接口被拒（401）', status == 401, f'HTTP {status}')
    status, _ = call(base, '/api/auth/register', 'POST', {'username': 'x', 'password': 'x123456'})
    check('自助注册入口已移除（未登录被拦，401）', status == 401, f'HTTP {status}')

    status, session = login(base, args.admin_user, args.admin_password)
    if status != 200 or not session.get('access_token'):
        check('种子账号登录', False, f'HTTP {status}：{session.get("detail")}')
        return _summary()
    superadmin = session['access_token']
    superadmin_id = session['user']['id']
    check('种子账号是超级管理员（老库会被就地提升）',
          session['user']['role'] == 'superadmin', f'角色={session["user"]["role"]}')

    status, users = call(base, '/api/users', token=superadmin)
    names = [item['username'] for item in users.get('items', [])] if status == 200 else []
    check('超级管理员能读账号清单', status == 200 and args.admin_user in names,
          f'HTTP {status}，账号数={len(names)}')
    check('账号清单不含口令哈希', 'password_hash' not in json.dumps(users), 
          '响应里没有 password_hash 字段' if 'password_hash' not in json.dumps(users) else '泄漏了口令哈希！')

    # 一个项目用来验"能否写入"（管理员建、跑完删）
    status, project = call(base, '/api/projects', 'POST', {'name': f'verify-auth-{uuid4().hex[:6]}'},
                           token=superadmin)
    project_id = project.get('id')
    check('超级管理员能建项目（写权限）', status == 201 and bool(project_id), f'HTTP {status}')

    # ── 临时账号：一个只读、一个管理员（分别验两条路径）──────────────────────
    temp_user = 'verify_auth_' + uuid4().hex[:6]
    temp_password = 'verify-' + uuid4().hex[:8]
    status, created = call(base, '/api/users', 'POST',
                           {'username': temp_user, 'password': temp_password, 'role': 'viewer',
                            'display_name': '复验临时账号'}, token=superadmin)
    if status != 201:
        check('超级管理员建只读账号', False, f'HTTP {status}：{created.get("detail")}')
        return _summary()
    temp_id = created['id']
    check('超级管理员建只读账号（可直接指定角色）', created['role'] == 'viewer',
          f'角色={created["role"]}')

    temp_admin_user = 'verify_mgr_' + uuid4().hex[:6]
    temp_admin_password = 'verify-' + uuid4().hex[:8]
    status, created_admin = call(base, '/api/users', 'POST',
                                 {'username': temp_admin_user, 'password': temp_admin_password,
                                  'role': 'admin', 'display_name': '复验临时管理员'},
                                 token=superadmin)
    temp_admin_id = created_admin.get('id')
    check('超级管理员建管理员账号', status == 201 and created_admin.get('role') == 'admin',
          f'HTTP {status} 角色={created_admin.get("role")}')

    status, session = login(base, temp_admin_user, temp_admin_password)
    admin = session.get('access_token', '')
    check('新建的管理员能登录', status == 200, f'HTTP {status}')

    # ── 三档越权边界（管理员 vs 超级管理员）────────────────────────────────
    status, refused = call(base, '/api/users', 'POST',
                           {'username': 'verify_should_fail_' + uuid4().hex[:4],
                            'password': 'whatever123', 'role': 'superadmin'}, token=admin)
    check('管理员不能创建超级管理员（403）',
          status == 403 and refused.get('code') == 'cannot_create_superadmin',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{superadmin_id}', 'PATCH', {'role': 'viewer'},
                           token=admin)
    check('管理员不能降级超级管理员（403）',
          status == 403 and refused.get('code') == 'cannot_manage_superadmin',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{superadmin_id}', 'PATCH', {'is_active': False},
                           token=admin)
    check('管理员不能停用超级管理员（403）',
          status == 403 and refused.get('code') == 'cannot_manage_superadmin',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{superadmin_id}', 'DELETE', token=admin)
    check('管理员不能删除超级管理员（403）',
          status == 403 and refused.get('code') == 'cannot_manage_superadmin',
          f'HTTP {status} code={refused.get("code")}')

    spare_user = 'verify_spare_' + uuid4().hex[:4]
    status, spare = call(base, '/api/users', 'POST',
                         {'username': spare_user, 'password': 'spare12345', 'role': 'viewer'},
                         token=admin)
    spare_viewer_id = spare.get('id') if status == 201 else None
    check('管理员能建只读账号（越权边界只挡超管）', status == 201, f'HTTP {status}')

    status, session = login(base, temp_user, temp_password)
    viewer = session.get('access_token', '')
    check('新建的账号能登录', status == 200, f'HTTP {status}')

    status, _ = call(base, '/api/projects', token=viewer)
    check('只读用户能读项目清单', status == 200, f'HTTP {status}')

    status, denied = call(base, '/api/projects', 'POST', {'name': '越权项目'}, token=viewer)
    check('只读用户不能建项目（403）', status == 403, f'HTTP {status}：{denied.get("detail")}')

    status, users_denied = call(base, '/api/users', token=viewer)
    reason = str(users_denied.get('detail', ''))
    check('只读用户看不了账号清单（403）', status == 403, f'HTTP {status}：{reason}')
    check('拒绝理由说的是"管理员"而不是"不能写入"',
          '管理员' in reason and '不能写入' not in reason, f'理由：{reason}')

    # 只读用户点「查看来源证据」：这是读，必须走到业务层（不存在的记录 → 404）
    status, _ = call(base, f'/api/projects/{project_id}/records/no-such-record/evidence', 'POST',
                     {}, token=viewer)
    check('只读用户能查来源证据（不再是 403 越权拦截）', status == 404,
          f'HTTP {status}（404=走到业务层，403=被授权层误拦）')

    status, searched = call(base, f'/api/projects/{project_id}/search', 'POST',
                            {'query': '退款'}, token=viewer)
    check('只读用户能检索（只读 POST 白名单）', status == 200 and 'hits' in searched,
          f'HTTP {status}')

    # ── 提升 / 降级 / 停用即刻生效 ───────────────────────────────────────────
    status, promoted = call(base, f'/api/users/{temp_id}', 'PATCH', {'role': 'admin'},
                            token=superadmin)
    check('提升为管理员', status == 200 and promoted['user']['role'] == 'admin',
          f'HTTP {status}')
    status, _ = call(base, '/api/projects', 'POST', {'name': f'提升后建的项目-{uuid4().hex[:4]}'},
                     token=viewer)
    check('提升后原令牌立刻有写权限（角色每次回查库）', status == 201, f'HTTP {status}')

    status, _ = call(base, f'/api/users/{temp_id}', 'PATCH', {'role': 'viewer'}, token=admin)
    status2, _ = call(base, '/api/projects', 'POST', {'name': '降级后建的项目'}, token=viewer)
    check('管理员能降级另一个管理员，写权限立刻收回', status == 200 and status2 == 403,
          f'改角色 HTTP {status}，写入 HTTP {status2}')

    # ── 改口令 / 重置口令立刻作废旧令牌 ──────────────────────────────────────
    new_password = 'changed-' + uuid4().hex[:8]
    status, changed = call(base, '/api/auth/password', 'POST',
                           {'current_password': temp_password, 'new_password': new_password},
                           token=viewer)
    check('用户能改自己的口令', status == 200, f'HTTP {status}')
    status, stale = call(base, '/api/projects', token=viewer)
    check('改口令后旧令牌立刻失效', status == 401 and stale.get('code') == 'stale_credentials',
          f'HTTP {status} code={stale.get("code")}')
    status, old_login = login(base, temp_user, temp_password)
    check('旧口令不能再登录', status == 401, f'HTTP {status}')

    status, session = login(base, temp_user, new_password)
    fresh = session.get('access_token', '')
    check('新口令能登录', status == 200, f'HTTP {status}')

    reset_password = 'reset-' + uuid4().hex[:8]
    status, _ = call(base, f'/api/users/{temp_id}', 'PATCH', {'password': reset_password},
                     token=superadmin)
    status2, _ = call(base, '/api/projects', token=fresh)
    check('超级管理员重置口令后该用户令牌立刻失效', status == 200 and status2 == 401,
          f'重置 HTTP {status}，旧令牌 HTTP {status2}')
    status, session = login(base, temp_user, reset_password)
    fresh = session.get('access_token', '')

    # ── 停用 ────────────────────────────────────────────────────────────────
    status, _ = call(base, f'/api/users/{temp_id}', 'PATCH', {'is_active': False},
                     token=superadmin)
    status2, denied = call(base, '/api/projects', token=fresh)
    check('停用后令牌立刻失效', status == 200 and status2 == 401
          and denied.get('code') == 'account_disabled', f'HTTP {status2} code={denied.get("code")}')
    status, _ = login(base, temp_user, reset_password)
    check('停用后不能再登录', status == 401, f'HTTP {status}')

    # ── 谁都不能把自己锁在门外（含超级管理员）────────────────────────────────
    status, refused = call(base, f'/api/users/{superadmin_id}', 'PATCH', {'role': 'viewer'},
                           token=superadmin)
    check('超级管理员不能被降级（409，含自己）',
          status == 409 and refused.get('code') == 'superadmin_protected',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{superadmin_id}', 'PATCH', {'is_active': False},
                           token=superadmin)
    check('超级管理员不能被停用（409，含自己）',
          status == 409 and refused.get('code') == 'superadmin_protected',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{superadmin_id}', 'DELETE', token=superadmin)
    check('超级管理员不能被删除（409，含自己）',
          status == 409 and refused.get('code') == 'superadmin_protected',
          f'HTTP {status} code={refused.get("code")}')

    status, me = call(base, '/api/auth/me', token=admin)
    admin_id = me.get('user', {}).get('id')
    status, refused = call(base, f'/api/users/{admin_id}', 'PATCH', {'role': 'viewer'},
                           token=admin)
    check('管理员不能降级/停用自己（409）',
          status == 409 and refused.get('code') == 'cannot_modify_self',
          f'HTTP {status} code={refused.get("code")}')

    status, refused = call(base, f'/api/users/{admin_id}', 'DELETE', token=admin)
    check('管理员不能删除自己（409）',
          status == 409 and refused.get('code') == 'cannot_modify_self',
          f'HTTP {status} code={refused.get("code")}')

    # ── 收尾：删掉临时账号与项目（走产品路径 DELETE，顺带验证它真能用）──────
    if args.keep:
        print(f'\n（--keep）临时账号 {temp_user} / {temp_admin_user} 与项目 {project_id} '
              f'保留，请自行清理')
        return _summary()

    if project_id:
        call(base, f'/api/projects/{project_id}', 'DELETE', token=superadmin)
    for uid, name in ((temp_id, temp_user), (temp_admin_id, temp_admin_user),
                      (spare_viewer_id, spare_user)):
        if not uid:
            continue
        status, _ = call(base, f'/api/users/{uid}', 'DELETE', token=superadmin)
        check(f'删除临时账号走产品路径（{name}）', status == 200, f'HTTP {status}')
    status, users = call(base, '/api/users', token=superadmin)
    left = [item['username'] for item in users.get('items', [])]
    check('清理后清单里不再有临时账号',
          not any(n in left for n in (temp_user, temp_admin_user, spare_user)),
          f'剩余账号数={len(left)}')

    return _summary()


def _summary() -> int:
    failed = [label for label, ok, _ in results if ok is False]
    skipped = [label for label, ok, _ in results if ok is None]
    print(f'\n合计 {len(results)} 项：通过 {len(results) - len(failed) - len(skipped)}，'
          f'失败 {len(failed)}，跳过 {len(skipped)}')
    for label in failed:
        print(f'  {FAIL} {label}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
