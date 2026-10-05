"""认证授权中间件与访问策略。

两层职责分开：
  * is_public_path        哪些路径无需登录（登录/健康检查/静态资源/文档）；
  * requires_write_access 该「方法 + 路径」是否**需要管理员（admin 或 superadmin）**。

三档角色（``users_store.ROLES``）在中间件这一层的边界：
  * viewer     只读：GET/HEAD/OPTIONS 与「只读 POST 白名单」可访问，其余拒绝；
  * admin      可写入，可进入用户管理面；但**对超级管理员账号的操作**由
               ``api/users.py`` 路由层再拦一道（中间件不逐字段判角色）；
  * superadmin 全部放行。

授权策略是 **fail-closed（默认拒绝）**，原因是安全：
  * GET/HEAD/OPTIONS 一律视为只读（例外见 ``_MANAGER_ONLY_PATTERNS``）；
  * PUT/PATCH/DELETE 一律视为写入；
  * POST 不默认放行 —— 本项目大量只读操作（检索/问答/脑图/SPARQL/各类预览）以及
    自助动作（改自己的口令）都是 POST，所以这些必须在 ``_READ_ONLY_POST_PATTERNS``
    里**精确列出**；任何没列出来的 POST 都按写入处理（需要管理员）。这样将来新增的
    写端点，即使忘了加保护，默认也不会被只读用户访问到。

模式匹配的是**实际请求路径**（路由里的 ``{p}`` / ``{draft_id}`` 此时已是真实值），
用一段 ``[^/]+`` 匹配单个路径段。
"""
from __future__ import annotations

import logging
import re

from fastapi import Request
from fastapi.responses import JSONResponse

from ..core.security import TokenError, credential_fingerprint, decode_access_token

LOG = logging.getLogger('knowledge_service')

# 无需登录即可访问的 API 路径（精确匹配）。
PUBLIC_API_PATHS = frozenset({
    '/api/health',
    '/api/auth/login',
})

# 「不需要管理员」的 POST 白名单：命中其中任一模式的 POST，viewer 也可以访问。
# 两类都列在这里，判据是同一条 —— **它不碰项目数据**：
#   1. 只读 POST：检索/问答/图谱/预览/干跑（用 POST 只是为了带 Scope 之类的过滤体）；
#   2. 自助动作：改**自己**的口令（登录了就该能改自己的口令，与写权限无关）。
_READ_ONLY_POST_PATTERNS = (
    # ── auth.py：改自己的口令（任何已登录用户都可做）──
    r'^/api/auth/password$',
    # ── knowledge.py：检索 / 问答 / 图谱 / 查询 / 校验 / SPARQL ──
    r'^/api/projects/[^/]+/records/query$',
    r'^/api/projects/[^/]+/search$',
    r'^/api/projects/[^/]+/qa$',
    r'^/api/projects/[^/]+/qa/stream$',
    r'^/api/projects/[^/]+/graph$',
    r'^/api/projects/[^/]+/graph/semantica$',
    r'^/api/projects/[^/]+/sparql$',
    r'^/api/projects/[^/]+/ontology/validate$',
    # ── parity.py：各类只读视图与「预览/干跑」（不落库）──
    r'^/api/projects/[^/]+/documents/preview$',
    r'^/api/projects/[^/]+/documents/upload/preview$',
    r'^/api/projects/[^/]+/dashboard$',
    r'^/api/projects/[^/]+/sources$',
    r'^/api/projects/[^/]+/subgraph$',
    r'^/api/projects/[^/]+/mindmap$',
    r'^/api/projects/[^/]+/resolve$',
    r'^/api/projects/[^/]+/reclassify/preview$',
    r'^/api/projects/[^/]+/evaluate$',
    # ── evidence.py：查一条记录「来源证据」是纯读取 ──
    r'^/api/projects/[^/]+/records/[^/]+/evidence$',
    # ── workspace.py：链接工作台的只读探索 / 下拉 / 字段发现 ──
    r'^/api/projects/[^/]+/explore$',
    r'^/api/projects/[^/]+/entity-options$',
    r'^/api/projects/[^/]+/metadata/facets$',
    # ── ontology_drafts.py：草案校验（只算结果，不改草案）──
    r'^/api/projects/[^/]+/ontology-drafts/[^/]+/validate$',
)
_READ_ONLY_POST = tuple(re.compile(p) for p in _READ_ONLY_POST_PATTERNS)

# 例外：这几个路径即使方法是 GET 也**需要管理员（admin/superadmin）**。
# 理由：它不是"读知识"，而是"读账号"——属于管理面。GET 默认放行那条规则
# 是为"只读用户能检索/问答/看脑图"服务的，不该顺带把用户清单也开放出去。
_MANAGER_ONLY_PATTERNS = (
    r'^/api/users$',
    r'^/api/users/[^/]+$',
)
_MANAGER_ONLY = tuple(re.compile(p) for p in _MANAGER_ONLY_PATTERNS)


def is_public_path(path: str) -> bool:
    """是否为完全公开的路径（无需登录）。

    非 /api/ 的路径（首页、登录页、/assets、/vendor、接口文档）一律公开，
    由静态资源与 FastAPI 文档自己负责，认证只保护 /api/ 业务接口。
    """
    if not path.startswith('/api/'):
        return True
    return path in PUBLIC_API_PATHS


def requires_write_access(method: str, path: str) -> bool:
    """判断该请求是否需要管理员（admin/superadmin）。未明确识别为只读的，一律算需要。

    名字保持"写权限"是因为它 99% 的用途就是拦写入；但它实际回答的是
    **"要不要管理员"** —— 因此 `/api/users` 这类管理面 GET 也在这里返回 True
    （见 `_MANAGER_ONLY_PATTERNS`）。访问策略只在这一个函数里算，别处不许再判一遍。
    """
    method = method.upper()
    # 管理面例外：方法不是写方法，但同样只有管理员能碰。
    if any(pattern.match(path) for pattern in _MANAGER_ONLY):
        return True
    if method in ('GET', 'HEAD', 'OPTIONS'):
        return False
    if method in ('PUT', 'PATCH', 'DELETE'):
        return True
    if method == 'POST':
        # 命中只读白名单才不需要管理员；其余 POST 默认按写入处理（fail-closed）。
        return not any(pattern.match(path) for pattern in _READ_ONLY_POST)
    # 其它少见方法（如自定义）默认按写入处理，最保守。
    return True


def _bearer_token(request: Request):
    """从 Authorization 头提取 Bearer 令牌；格式不符返回 None。"""
    header = request.headers.get('authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header[len('Bearer '):].strip()
    return token or None


def _unauthorized(detail: str, *, code: str = 'unauthorized'):
    return JSONResponse(status_code=401, content={'detail': detail, 'code': code})


def _forbidden(detail: str):
    return JSONResponse(status_code=403, content={'detail': detail, 'code': 'forbidden'})


def install_auth_middleware(app, *, users, jwt_secret: str):
    """挂载认证 + 授权中间件。

    :param users: UserStore（用于按 token 里的用户 ID 回查，捕获「签发后被禁用」）
    :param jwt_secret: JWT 验签密钥
    """

    @app.middleware('http')
    async def authenticate_and_authorize(request: Request, call_next):
        path = request.url.path
        method = request.method

        # 1) 公开路径直接放行（登录/健康检查/静态资源/文档）。
        if is_public_path(path):
            return await call_next(request)

        # 2) 其余 /api/ 接口必须带有效 Bearer 令牌。
        token = _bearer_token(request)
        if token is None:
            return _unauthorized('未登录或缺少令牌，请先登录', code='not_authenticated')
        try:
            payload = decode_access_token(token, secret=jwt_secret)
        except TokenError as exc:
            return _unauthorized(str(exc), code='invalid_token')

        # 3) 回查数据库：token 有效不代表账号仍有效（可能登录后被禁用/删除，
        #    也可能口令已被改或被管理员重置 —— 后者靠令牌里的口令指纹识别）。
        row = users.get_by_id(payload.get('sub'))
        if row is None or not row['is_active']:
            return _unauthorized('账号不可用或已被禁用', code='account_disabled')
        if payload.get('pw') != credential_fingerprint(row['password_hash']):
            # 口令变了（或令牌签发于引入指纹之前）→ 这枚令牌不再代表当前凭据。
            return _unauthorized('登录状态已失效（口令可能已修改），请重新登录',
                                 code='stale_credentials')

        # 4) 注入精简身份供路由 / /me 使用（不含口令哈希）。
        user = {
            'id': row['id'],
            'username': row['username'],
            'display_name': row['display_name'],
            'role': row['role'],
        }
        request.state.user = user

        # 5) 授权：只读用户访问写入/管理端点时拒绝（403）。
        #    admin 与 superadmin 都通过这一层；两者对 superadmin 账号的差异
        #    在 api/users.py 路由里判，避免访问策略散在两处。
        if row['role'] == 'viewer' and requires_write_access(method, path):
            if any(pattern.match(path) for pattern in _MANAGER_ONLY):
                return _forbidden('用户与权限只有管理员能看，当前账号是只读用户')
            return _forbidden('当前账号为只读用户，仅可检索、问答与查看脑图，不能写入')

        return await call_next(request)
