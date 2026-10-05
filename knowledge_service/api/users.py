"""用户管理路由（/api/users）—— 管理员（admin / superadmin）使用。

这一层在中间件之上，专门处理**三级角色之间的越权边界**与「超级管理员保护」。
中间件只回答"是不是管理员、能不能写"，不逐字段判角色；下列规则集中在本文件：

谁能做什么（越权边界）：
  * admin（管理员）：
      - 可建 / 改 / 停用 / 删除 **admin、viewer** 账号；
      - **不能创建超级管理员、不能把任何人提升为超级管理员**；
      - **不能查看或修改超级管理员账号**（超级管理员不在管理员的管辖范围内）。
  * superadmin（超级管理员）：
      - 可管理一切账号，包括把管理员 / 只读账号提升为超级管理员。

超级管理员是系统之根，受**绝对保护**（无论操作者是谁，包括他自己）：
  * 超级管理员账号**不能被降级、不能被停用、不能被删除**；
  * 因此系统永远存在至少一个可登录的最高权限入口，不会被一次误操作锁死；
  * 超级管理员仍可改自己的展示名；改自己口令走 /api/auth/password。

另有一条对所有管理员生效的守卫：
  * 不能降级 / 停用 / 删除**当前登录的自己** —— 那会让当前会话立刻失去权限。

口令永远只以「PBKDF2 自描述哈希」入库，响应里绝不带 password_hash
（UserStore._public 负责剔除）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core.security import hash_password
from ..repository.users_store import ROLES

# 请求模型必须定义在顶层：本文件有 `from __future__ import annotations`，
# FastAPI 靠模块全局作用域解析注解类型，类定义在函数内部会导致 POST body 永远 missing。
class CreateUserBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(default='viewer', pattern='^(superadmin|admin|viewer)$')
    display_name: str = Field(default='', max_length=128)


class UpdateUserBody(BaseModel):
    # 全部可选：None = 这次不动这个字段（局部更新）。
    role: str | None = Field(default=None, pattern='^(superadmin|admin|viewer)$')
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, min_length=6, max_length=128)


def _conflict(detail: str, code: str):
    return JSONResponse(status_code=409, content={'detail': detail, 'code': code})


def _forbidden(detail: str, code: str = 'forbidden'):
    return JSONResponse(status_code=403, content={'detail': detail, 'code': code})


def _bad_request(detail: str, code: str):
    return JSONResponse(status_code=400, content={'detail': detail, 'code': code})


def install(app, *, users):
    """挂载用户管理路由。:param users: UserStore（与主 Repository 共享连接）"""

    router = APIRouter(prefix='/api/users')

    @router.get('')
    def list_users():
        """用户清单（含角色与启用状态），按创建时间排序；永不含口令哈希。"""
        return {'items': users.list_users()}

    @router.post('', status_code=201)
    def create_user(body: CreateUserBody, request: Request):
        """管理员建号。admin 不能直接建超级管理员（越权边界）。"""
        actor = getattr(request.state, 'user', None) or {}
        if actor.get('role') != 'superadmin' and body.role == 'superadmin':
            # 说清"为什么不行"：能开超级管理员＝能自封系统之根，这一级只有
            # 既有超级管理员能授予。
            return _forbidden(
                '只有超级管理员能创建超级管理员账号：当前管理员无权授予系统最高权限。'
                '如确需新增，请让现有超级管理员操作。',
                code='cannot_create_superadmin')
        from ..repository.users_store import UserAlreadyExists
        try:
            row = users.create(body.username, hash_password(body.password),
                               role=body.role, display_name=body.display_name)
        except UserAlreadyExists:
            return _conflict('该用户名已被注册', 'user_exists')
        except ValueError as exc:
            return _bad_request(str(exc), 'invalid_user')
        return row

    @router.patch('/{user_id}')
    def update_user(user_id: str, body: UpdateUserBody, request: Request):
        """改角色 / 停启用 / 改展示名 / 重置口令。越权与保护规则见模块 docstring。"""
        actor = getattr(request.state, 'user', None) or {}
        actor_role = actor.get('role')
        target = users.get_by_id(user_id)
        if target is None:
            return JSONResponse(status_code=404,
                                content={'detail': '用户不存在', 'code': 'user_not_found'})

        is_superadmin_target = target['role'] == 'superadmin'
        # 「降级」语义：目标当前是 superadmin/admin，而新角色把他拉低。
        demoting = body.role is not None and body.role != target['role'] \
            and ROLES.index(body.role) > ROLES.index(target['role'])
        disabling = body.is_active is False and target['is_active']

        # 边界 1：admin 不能修改超级管理员，也不能把人提升为超级管理员。
        if actor_role != 'superadmin':
            if is_superadmin_target:
                return _forbidden(
                    '超级管理员账号不在管理员的管辖范围内：只有超级管理员能查看或修改它。',
                    code='cannot_manage_superadmin')
            if body.role == 'superadmin':
                return _forbidden(
                    '只有超级管理员能把账号提升为超级管理员：当前管理员无权授予系统最高权限。',
                    code='cannot_grant_superadmin')

        # 保护 2：超级管理员账号不能被降级或停用（绝对规则，操作者是他自己也不行）。
        if is_superadmin_target and (demoting or disabling):
            return _conflict(
                '超级管理员账号受系统保护，不能降级或停用：系统必须始终保留至少一个'
                '可登录的最高权限入口。可改展示名，或新建/提升其他账号。',
                code='superadmin_protected')

        # 守卫 3：不能降级或停用当前登录的自己。
        if actor.get('id') == user_id and (demoting or disabling):
            return _conflict(
                '不能降级或停用当前登录的自己：这会让你当场失去管理权限。'
                '如要交出管理员，请用另一个管理员账号操作。',
                code='cannot_modify_self')

        updated = users.update(
            user_id,
            role=body.role,
            is_active=body.is_active,
            display_name=body.display_name,
            password_hash=hash_password(body.password) if body.password else None)
        # 明确回执：改了哪几项（前端据此给"已把 X 设为管理员"这类反馈）。
        changes = [name for name, value in (('角色', body.role),
                                            ('启用状态', body.is_active),
                                            ('展示名', body.display_name),
                                            ('口令', body.password)) if value is not None]
        return {'user': updated, 'changed': changes}

    @router.delete('/{user_id}')
    def delete_user(user_id: str, request: Request):
        """物理删除账号。超级管理员受保护不能删；不能删除当前登录的自己。"""
        actor = getattr(request.state, 'user', None) or {}
        target = users.get_by_id(user_id)
        if target is None:
            return JSONResponse(status_code=404,
                                content={'detail': '用户不存在', 'code': 'user_not_found'})

        # admin 不能删超级管理员（越权边界）。
        if actor.get('role') != 'superadmin' and target['role'] == 'superadmin':
            return _forbidden(
                '超级管理员账号不在管理员的管辖范围内，不能删除。',
                code='cannot_manage_superadmin')
        # 超级管理员账号绝对不能删除（保护系统最高入口）。
        if target['role'] == 'superadmin':
            return _conflict(
                '超级管理员账号受系统保护，不能删除：系统必须始终保留至少一个'
                '可登录的最高权限入口。如不再需要某个账号，请改用「停用」。',
                code='superadmin_protected')
        # 不能删除当前登录的自己（删完当前会话立即失效，且无法恢复）。
        if actor.get('id') == user_id:
            return _conflict(
                '不能删除当前登录的自己：这会让你当场失去账号与会话。'
                '如确需删除，请用另一个管理员账号操作。',
                code='cannot_modify_self')

        users.delete(user_id)
        return {'ok': True, 'detail': '账号已删除'}

    app.include_router(router)
