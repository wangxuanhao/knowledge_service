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
  6. 管理员不能降级/停用自己（否则当场失去管理权限）。

跑完自删：脚本用**迁移角色**删掉自己建的临时账号。应用角色按 0008 的授权没有
DELETE 权限（"禁用优于删除"是刻意取舍），但这个账号从未产生任何业务留痕，
留着只会污染账号清单，所以由脚本自己清干净。
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

    status, session = login(base, args.admin_user, args.admin_password)
    if status != 200 or not session.get('access_token'):
        check('管理员登录', False, f'HTTP {status}：{session.get("detail")}')
        return 1
    admin = session['access_token']
    check('管理员登录', session['user']['role'] == 'admin', f'角色={session["user"]["role"]}')

    status, users = call(base, '/api/users', token=admin)
    names = [item['username'] for item in users.get('items', [])] if status == 200 else []
    check('管理员能读账号清单', status == 200 and args.admin_user in names,
          f'HTTP {status}，账号数={len(names)}')
    check('账号清单不含口令哈希', 'password_hash' not in json.dumps(users), 
          '响应里没有 password_hash 字段' if 'password_hash' not in json.dumps(users) else '泄漏了口令哈希！')

    # 一个项目用来验"能否写入"（管理员建、跑完删）
    status, project = call(base, '/api/projects', 'POST', {'name': f'verify-auth-{uuid4().hex[:6]}'},
                           token=admin)
    project_id = project.get('id')
    check('管理员能建项目（写权限）', status == 201 and bool(project_id), f'HTTP {status}')

    # ── 临时只读账号 ──────────────────────────────────────────────────────────
    temp_user = 'verify_auth_' + uuid4().hex[:6]
    temp_password = 'verify-' + uuid4().hex[:8]
    status, created = call(base, '/api/users', 'POST',
                           {'username': temp_user, 'password': temp_password, 'role': 'viewer',
                            'display_name': '复验临时账号'}, token=admin)
    if status != 201:
        check('管理员建只读账号', False, f'HTTP {status}：{created.get("detail")}')
        return 1
    temp_id = created['id']
    check('管理员建只读账号（可直接指定角色）', created['role'] == 'viewer',
          f'角色={created["role"]}')

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
                            token=admin)
    check('提升为管理员', status == 200 and promoted['user']['role'] == 'admin',
          f'HTTP {status}')
    status, _ = call(base, '/api/projects', 'POST', {'name': f'提升后建的项目-{uuid4().hex[:4]}'},
                     token=viewer)
    check('提升后原令牌立刻有写权限（角色每次回查库）', status == 201, f'HTTP {status}')

    status, _ = call(base, f'/api/users/{temp_id}', 'PATCH', {'role': 'viewer'}, token=admin)
    status2, _ = call(base, '/api/projects', 'POST', {'name': '降级后建的项目'}, token=viewer)
    check('降回只读后写权限立刻收回', status == 200 and status2 == 403,
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
                     token=admin)
    status2, _ = call(base, '/api/projects', token=fresh)
    check('管理员重置口令后该用户令牌立刻失效', status == 200 and status2 == 401,
          f'重置 HTTP {status}，旧令牌 HTTP {status2}')
    status, session = login(base, temp_user, reset_password)
    fresh = session.get('access_token', '')

    # ── 停用 ────────────────────────────────────────────────────────────────
    status, _ = call(base, f'/api/users/{temp_id}', 'PATCH', {'is_active': False}, token=admin)
    status2, denied = call(base, '/api/projects', token=fresh)
    check('停用后令牌立刻失效', status == 200 and status2 == 401
          and denied.get('code') == 'account_disabled', f'HTTP {status2} code={denied.get("code")}')
    status, _ = login(base, temp_user, reset_password)
    check('停用后不能再登录', status == 401, f'HTTP {status}')

    # ── 管理员不能把自己锁在门外 ─────────────────────────────────────────────
    status, me = call(base, '/api/auth/me', token=admin)
    admin_id = me.get('user', {}).get('id')
    status, refused = call(base, f'/api/users/{admin_id}', 'PATCH', {'role': 'viewer'},
                           token=admin)
    check('管理员不能降级自己（409）',
          status == 409 and refused.get('code') == 'cannot_modify_self',
          f'HTTP {status} code={refused.get("code")}')

    # ── 收尾：删掉临时账号与项目 ─────────────────────────────────────────────
    if args.keep:
        print(f'\n（--keep）临时账号 {temp_user} 与项目 {project_id} 保留，请自行清理')
        return _summary()

    if project_id:
        call(base, f'/api/projects/{project_id}', 'DELETE', token=admin)
    try:
        sys.path.insert(0, str(ROOT))
        import psycopg
        from knowledge_service.core.config import load_environment
        from knowledge_service.repository.migrate import resolve_migration_dsn
        load_environment(include_admin=True)
        with psycopg.connect(resolve_migration_dsn()) as conn:
            removed = conn.execute('DELETE FROM users WHERE id=%s', (temp_id,)).rowcount
        check('临时账号已清理', removed == 1, '用迁移角色删除（应用角色无 DELETE 权限是设计）')
    except Exception as exc:                     # 清理失败不影响上面的结论，但要如实说
        check('临时账号已清理', False, f'删除失败：{type(exc).__name__}：{exc}')

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
