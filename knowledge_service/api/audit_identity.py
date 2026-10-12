"""Server-owned audit identity at the HTTP boundary."""
from fastapi import HTTPException, Request


def audit_identity(request: Request):
    user = getattr(request.state, 'user', None)
    if user:
        return {key: str(user.get(key) or '')
                for key in ('id', 'username', 'display_name')}
    if getattr(request.app.state, 'auth_disabled', False):
        return {'id': 'system:auth-disabled', 'username': 'auth-disabled',
                'display_name': '未启用认证'}
    raise HTTPException(status_code=401, detail='缺少已验证的审计身份')


def audit_actor(request: Request):
    identity = audit_identity(request)
    return (f"{identity['display_name'] or identity['username']} "
            f"({identity['username']}; user:{identity['id']})")
