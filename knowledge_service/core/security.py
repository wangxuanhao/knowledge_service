"""认证安全原语：口令哈希与 JWT 签发/校验（只用 Python 标准库）。

刻意不引入 passlib / pyjwt：
  * 口令哈希用标准库 ``hashlib.pbkdf2_hmac``（PBKDF2-HMAC-SHA256），足够满足
    「加盐 + 高迭代 + 可自描述存储」的需求；
  * JWT 就是 ``base64url(header).base64url(payload)`` 加一段 HMAC-SHA256，
    手写二十行即可，避免为一个签名引入整条依赖。

两个自存储/自校验的格式都把参数写进字符串头，换算法或迭代次数时旧数据仍可识别：
  * 口令哈希：``pbkdf2_sha256$<迭代>$<盐b64>$<摘要b64>``
  * JWT：固定 HS256，载荷里带 iat/exp，校验时强制验签 + 校验过期。

安全注意（实现里逐条落实）：
  1. 每个口令用独立随机盐（``secrets.token_bytes``），不用用户名等可猜值；
  2. 校验口令用 ``hmac.compare_digest`` 做恒定时间比较，防计时侧信道；
  3. JWT 密钥只从配置（``KG_JWT_SECRET``）来；未配置时由装配方生成进程级随机
     密钥并告警 —— 绝不把一个写死的弱密钥编译进代码。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# ── 口令哈希 ─────────────────────────────────────────────────────────────────

_ALGORITHM = 'pbkdf2_sha256'
_HASH_NAME = 'sha256'
_ITERATIONS = 240_000          # OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议量级
_SALT_BYTES = 16


def _b64encode(raw: bytes) -> str:
    """无填充的 base64url 编码（JWT / 口令哈希统一用它，字符串更紧凑）。"""
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _b64decode(text: str) -> bytes:
    """补回填充后做 base64url 解码。"""
    padding = '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """给明文口令生成自描述的 PBKDF2 哈希串。"""
    if not isinstance(password, str) or not password:
        raise ValueError('口令不能为空')
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        _HASH_NAME, password.encode('utf-8'), salt, iterations)
    return f'{_ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}'


def verify_password(password: str, stored: str) -> bool:
    """恒定时间校验口令是否匹配存储串；存储串格式非法时返回 False（不抛错）。"""
    try:
        algorithm, iter_text, salt_text, digest_text = stored.split('$')
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iter_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        _HASH_NAME, password.encode('utf-8'), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# ── JWT（HS256）───────────────────────────────────────────────────────────────

class TokenError(ValueError):
    """令牌无效：签名不符 / 格式非法 / 已过期。"""


def _sign(signing_input: bytes, secret: bytes) -> bytes:
    return hmac.new(secret, signing_input, hashlib.sha256).digest()


def credential_fingerprint(password_hash: str) -> str:
    """口令哈希的短指纹 —— 用来让「改了口令 ⇒ 旧登录立刻失效」。

    为什么需要它：令牌是自证式的（验签通过即认），所以改口令**不会**自动
    让已签发的令牌作废 —— 口令泄漏后受害者改了口令，窃取者手里那枚令牌
    还能再用满一个有效期。把哈希的指纹写进载荷、校验时与库中当前值比一下，
    改口令就等于把那批令牌全部作废，且不需要加列、也不需要多查一次库。
    指纹是"哈希的哈希"，不是凭据本身，放进（未加密的）载荷不泄漏可用秘密。
    """
    digest = hashlib.sha256((password_hash or '').encode('utf-8')).hexdigest()
    return digest[:16]


def create_access_token(*, user_id: str, username: str, role: str, secret: str,
                        ttl_seconds: int, fingerprint: str = '') -> str:
    """签发一个 HS256 JWT。载荷含身份、角色、签发与过期时间（Unix 秒）。

    ``fingerprint``＝口令哈希指纹（见 ``credential_fingerprint``）；中间件验签后
    还会与库中当前值比对，因此改口令/重置口令会立刻作废旧令牌。
    """
    now = int(time.time())
    header = {'alg': 'HS256', 'typ': 'JWT'}
    payload = {
        'sub': user_id,
        'username': username,
        'role': role,
        'pw': fingerprint,
        'iat': now,
        'exp': now + int(ttl_seconds),
    }
    head_b64 = _b64encode(json.dumps(header, separators=(',', ':')).encode())
    pay_b64 = _b64encode(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f'{head_b64}.{pay_b64}'.encode('ascii')
    sig_b64 = _b64encode(_sign(signing_input, secret.encode('utf-8')))
    return f'{head_b64}.{pay_b64}.{sig_b64}'


def decode_access_token(token: str, *, secret: str) -> dict:
    """校验签名与过期时间，返回载荷；任一不满足抛 ``TokenError``。"""
    parts = token.split('.')
    if len(parts) != 3:
        raise TokenError('令牌格式不正确')
    head_b64, pay_b64, sig_b64 = parts
    signing_input = f'{head_b64}.{pay_b64}'.encode('ascii')
    expected_sig = _sign(signing_input, secret.encode('utf-8'))
    try:
        provided_sig = _b64decode(sig_b64)
    except (ValueError, TypeError):
        raise TokenError('令牌签名不正确')
    # 先恒定时间验签，再解载荷——签名不符绝不继续信任内容。
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise TokenError('令牌签名不正确')
    try:
        # JWT 头这里只需确认算法是我们签发的 HS256，拒绝 "alg:none" 之类伪造。
        header = json.loads(_b64decode(head_b64))
        payload = json.loads(_b64decode(pay_b64))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise TokenError('令牌内容无法解析')
    if header.get('alg') != 'HS256':
        raise TokenError('不支持的令牌算法')
    if int(payload.get('exp', 0)) < int(time.time()):
        raise TokenError('登录已过期，请重新登录')
    if not payload.get('sub'):
        raise TokenError('令牌缺少身份')
    return payload


def resolve_jwt_secret() -> tuple[str, bool]:
    """取 JWT 签名密钥，返回 ``(secret, ephemeral)``。

    优先 ``KG_JWT_SECRET``。未配置时生成一条进程级随机密钥（ephemeral=True）：
      * 好处：零配置即可登录，且绝无写死弱密钥；
      * 代价：服务重启后旧令牌全部失效，需要重新登录。装配方应据此打一条告警，
        提醒生产环境显式设置 ``KG_JWT_SECRET``。
    """
    secret = (os.environ.get('KG_JWT_SECRET') or '').strip()
    if secret:
        if len(secret) < 16:
            raise ValueError('KG_JWT_SECRET 过短（至少 16 个字符），请改用更长的随机串')
        return secret, False
    return secrets.token_urlsafe(48), True
