"""认证路由（/api/auth/*）：登录、当前登录用户、改自己口令。

权限边界（刻意收紧，与用户管理面分工）：
  * **不再提供自助注册**。账号创建统一收进「用户与权限」（``api/users.py``，仅管理员）。
    留着公开注册，等于任何能访问本服务的人都能给自己开一个"能读全部知识"的账号，
    而且要多维护一个开关；现在只有一个开号入口，口径不会打架。
  * 首个超级管理员由迁移后启动时的「种子」保证（空库播种；老库把种子账号提升），
    后续账号由管理员在「用户与权限」里创建。
  * 登录成功签发 JWT；前端把它放在 ``Authorization: Bearer ...`` 里
    （``web/auth.js`` 用补丁 fetch 统一注入），并在无令牌 / 令牌失效时跳独立登录页。
  * /me 只读返回当前身份，供前端决定显示哪些菜单；真正的写拦截在中间件，
    前端隐藏按钮只是体验，不是安全边界。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core.security import (
    create_access_token,
    credential_fingerprint,
    hash_password,
    verify_password,
)
from ..repository.users_store import UserStore

LOG = logging.getLogger('knowledge_service')


# 请求模型必须定义在模块顶层：本文件有 `from __future__ import annotations`，
# FastAPI 靠模块全局作用域解析注解类型，类定义在函数内部会导致 POST body 永远 missing。
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class PasswordChange(BaseModel):
    # 必须带当前口令：令牌泄漏时，光有令牌不能直接把口令改掉顶掉真主。
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


def install(app, *, users: UserStore, jwt_secret: str, ttl_seconds: int):
    """挂载认证路由。

    :param users: 用户仓储（与主 Repository 共享连接）
    :param jwt_secret: JWT 签名密钥
    :param ttl_seconds: 访问令牌有效期（秒）
    """

    router = APIRouter(prefix='/api/auth')

    def token_for(row) -> dict:
        """根据用户行签发令牌并组装登录响应。"""
        token = create_access_token(
            user_id=row['id'], username=row['username'], role=row['role'],
            secret=jwt_secret, ttl_seconds=ttl_seconds,
            # 令牌里带上口令哈希指纹：改口令后旧令牌立刻作废（见 core.security）。
            fingerprint=credential_fingerprint(row['password_hash']))
        return {
            'access_token': token,
            'token_type': 'bearer',
            'user': {
                'id': row['id'],
                'username': row['username'],
                'display_name': row['display_name'],
                'role': row['role'],
            },
        }

    @router.post('/login')
    def login(body: Credentials):
        """校验口令并签发令牌。用户名或口令错误统一返回 401（不区分，防枚举）。"""
        row = users.get_by_username(body.username)
        # 用户不存在 / 被禁用 / 口令错，都回同一句，避免借此判断账号是否存在。
        if row is None or not row['is_active'] \
                or not verify_password(body.password, row['password_hash']):
            return JSONResponse(
                status_code=401,
                content={'detail': '用户名或口令不正确',
                         'code': 'invalid_credentials'})
        return token_for(row)

    @router.get('/me')
    def me(request: Request):
        """返回当前登录用户。到达这里说明已通过认证中间件并注入了身份。"""
        user = getattr(request.state, 'user', None)
        if user is None:
            return JSONResponse(status_code=401, content={'detail': '未登录'})
        return {'user': user}

    @router.post('/password')
    def change_password(body: PasswordChange, request: Request):
        """改**自己**的口令（三种角色都可以改自己的）。别人的口令由管理员重置。

        三条口径：
          1. 必须带当前口令 —— 否则一枚泄漏的令牌就能改口令、把真主顶掉；
          2. 改完立即作废该用户的全部旧令牌（令牌带口令指纹，见 core.security），
             所以"改了口令，别处还登着"不会有残留；
          3. 不改任何别人的账号（那是 /api/users 的 PATCH，只有管理员能调）。
        """
        user = getattr(request.state, 'user', None)
        if user is None:
            return JSONResponse(status_code=401, content={'detail': '未登录'})
        row = users.get_by_id(user['id'])
        if row is None or not row['is_active']:
            return JSONResponse(status_code=401,
                                content={'detail': '账号不可用', 'code': 'account_disabled'})
        if not verify_password(body.current_password, row['password_hash']):
            return JSONResponse(
                status_code=400,
                content={'detail': '当前口令不正确', 'code': 'bad_current_password'})
        users.update(row['id'], password_hash=hash_password(body.new_password))
        LOG.info('用户 %s 修改了自己的口令', row['username'])
        return {'ok': True,
                'detail': '口令已更新：其他设备上的登录已失效，请用新口令重新登录。'}

    app.include_router(router)
