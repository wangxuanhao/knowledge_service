"""用户存储域（users 表）。

与其他 Store 一样：不自己开连接，复用主 ``Repository`` 的 ``_db`` / ``_lock``，
SQL 用 ``?`` 占位符（由 ``pg_engine`` 翻译成 PostgreSQL 的 ``%s``）。

应用层在这里统一做用户名规范化（去首尾空白 + 转小写），作为唯一出口，
保证同名不会因为大小写或空格被注册成两个账号。

角色为三档（口径唯一出处，其余模块只引用这里的常量）：
  * superadmin 超级管理员：系统之根，不可被降级 / 停用 / 删除；
  * admin      管理员：可写入、建模发布、管理普通账号；
  * viewer     只读用户：只能检索、问答、看脑图。
"""
from __future__ import annotations

from uuid import uuid4

from ..core.time import utc_now

#: 三档角色（顺序即权限从高到低），新增角色只改这里。
ROLES = ('superadmin', 'admin', 'viewer')

#: 可以进入「用户与权限」管理面的角色：超级管理员与管理员。
MANAGER_ROLES = ('superadmin', 'admin')


def normalize_username(username: str) -> str:
    """用户名规范化：去首尾空白并转小写。非法输入抛 ``ValueError``。"""
    if not isinstance(username, str):
        raise ValueError('用户名必须是文本')
    normalized = username.strip().lower()
    if not normalized:
        raise ValueError('用户名不能为空')
    if len(normalized) > 64:
        raise ValueError('用户名过长（最多 64 个字符）')
    return normalized


class UserAlreadyExists(ValueError):
    """用户名已被注册。"""


class UserStore:
    """用户 CRUD；与主 Repository 共享连接和锁。"""

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    @staticmethod
    def _public(row) -> dict:
        """把行转成对外字典，并剔除绝不能外泄的口令哈希。"""
        item = dict(row)
        item.pop('password_hash', None)
        return item

    def create(self, username: str, password_hash: str, *,
               role: str = 'viewer', display_name: str = '') -> dict:
        """创建用户。用户名重复抛 ``UserAlreadyExists``。"""
        if role not in ROLES:
            raise ValueError(f'角色必须是 {ROLES} 之一')
        username = normalize_username(username)
        if self.get_by_username(username) is not None:
            raise UserAlreadyExists('该用户名已被注册')
        now = utc_now()
        # 多语句写入放在同一把锁内，与项目里其它写路径的并发约定保持一致。
        with self._lock:
            self._db.execute(
                '''INSERT INTO users
                   (id, username, password_hash, display_name, role,
                    is_active, created_at, updated_at)
                   VALUES (?,?,?,?,?,true,?,?)''',
                (str(uuid4()), username, password_hash,
                 display_name or username, role, now, now))
        return self._public(self.get_by_username(username))

    def get_by_username(self, username: str):
        """按用户名取**完整行**（含 password_hash，仅供登录校验）；不存在返回 None。"""
        username = normalize_username(username)
        return self._db.execute(
            'SELECT * FROM users WHERE username=?', (username,)).fetchone()

    def get_by_id(self, user_id: str):
        """按用户 ID 取完整行；不存在返回 None。"""
        return self._db.execute(
            'SELECT * FROM users WHERE id=?', (user_id,)).fetchone()

    def count(self) -> int:
        """用户总数（供装配套判断是否需要播种首个超级管理员）。"""
        return self._db.execute('SELECT count(*) FROM users').fetchone()[0]

    # ── 管理面（仅供管理员路由调用）────────────────────────────────────────────

    def list_users(self) -> list:
        """全部用户（**剔除口令哈希**），按创建时间排序 —— 列表要稳定，不能随行序跳。"""
        rows = self._db.execute(
            'SELECT * FROM users ORDER BY created_at, username').fetchall()
        return [self._public(row) for row in rows]

    def count_active_superadmins(self) -> int:
        """启用中的超级管理员数量。

        用于「不能把最后一个超级管理员降级 / 停用 / 删除」这条守卫：否则一次
        误操作会把权限体系的最终入口抹掉，再没有任何界面能救回来（只能改库）。
        """
        return self._db.execute(
            "SELECT count(*) FROM users "
            "WHERE role='superadmin' AND is_active=true").fetchone()[0]

    def update(self, user_id: str, *, role: str = None, is_active: bool = None,
               display_name: str = None, password_hash: str = None):
        """局部更新用户；只写显式传入的字段。返回更新后的**公开**字典。

        ``None`` 表示"这次不动它"，因此不能把字段清空（display_name 允许空串）。
        没有任何字段需要改时直接返回当前值，不产生空 UPDATE。
        """
        if role is not None and role not in ROLES:
            raise ValueError(f'角色必须是 {ROLES} 之一')
        row = self.get_by_id(user_id)
        if row is None:
            return None
        fields, values = [], []
        for column, value in (('role', role), ('is_active', is_active),
                              ('display_name', display_name),
                              ('password_hash', password_hash)):
            if value is not None:
                fields.append(f'{column}=?')
                values.append(value)
        if fields:
            fields.append('updated_at=?')
            values.extend([utc_now(), user_id])
            with self._lock:
                self._db.execute(
                    f'UPDATE users SET {", ".join(fields)} WHERE id=?', tuple(values))
        return self._public(self.get_by_id(user_id))

    def delete(self, user_id: str) -> bool:
        """物理删除一个账号。删除成功返回 True，账号不存在返回 False。

        与「停用」的分工：停用保留账号与历史操作的 actor 追溯，是日常首选；
        删除用于开错号、测试号这类确实要抹掉的场景。**超级管理员行不允许删除**
        （路由层也会先拦一道），否则最终入口可能被清空。
        """
        row = self.get_by_id(user_id)
        if row is None:
            return False
        with self._lock:
            self._db.execute('DELETE FROM users WHERE id=?', (user_id,))
        return True
