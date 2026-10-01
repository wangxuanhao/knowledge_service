"""PostgreSQL 连接池与工作单元（unit-of-work）。

规划依据：`docs/2026-09-30-PostgreSQL替换与本体知识服务优化规划.md` §5.1 / §5.3。

设计要点（逐条对应规划）：
  1. psycopg 3 + `psycopg_pool`，沿用同步服务形态；不把全站改成 async。
  2. 每个请求 / worker unit-of-work **独享借出的连接**，不跨线程共享
     connection/cursor；事务边界显式。
  3. 池预算按实例数相加（默认 min=2 / max=10）；`acquire` 超时、
     `statement_timeout`、`lock_timeout`、`idle_in_transaction_session_timeout`
     全部显式设置，超时映射为明确异常。
  4. 应用角色无 DDL/superuser 权限 —— 迁移与应用分开（见 `migrate.py`）。

兼容性策略（决定 T03 能否低成本落地）：
  现有调用方 `row['col']`（按列名）与 `row[0]`（按下标）两种取值
  并存，且大量 `json.loads(row['payload'])`。为了不改 285 个调用点：
    * `Row` 同时支持按列名与按下标取值，并实现 `keys()` / `dict()`；
    * `jsonb` 在**读取时还原为字符串**，
      写入时接受 str 或 dict/list。这样 SQL 层仍可用 `->>`、`@>` 做下推过滤，
      而业务层 `json.loads(...)` 完全不用改。
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from psycopg import errors as pg_errors
from psycopg import sql as pg_sql
from psycopg.rows import BaseRowFactory
from psycopg.types.datetime import TimestamptzLoader
from psycopg.types.json import Jsonb, JsonbLoader
from psycopg_pool import ConnectionPool, PoolTimeout

# 必须导入**顶层** psycopg：下面的 DatabaseError/IntegrityError 别名在模块级
# 立即求值，缺少这个导入会让本模块一被导入就 NameError（而不是等到用的时候）。
import psycopg  # noqa: E402

# ── 默认参数（规划 §5.1 的初始预算；实测后调参，不在代码里写死最优值）──────────
DEFAULT_POOL_MIN = 2
DEFAULT_POOL_MAX = 10
DEFAULT_ACQUIRE_TIMEOUT = 5.0        # 借连接等待上限（秒）
DEFAULT_STATEMENT_TIMEOUT_MS = 15_000  # 单条语句上限
DEFAULT_LOCK_TIMEOUT_MS = 5_000        # 等锁上限（发布/写入用短事务）
DEFAULT_IDLE_TX_TIMEOUT_MS = 60_000    # 事务内空闲上限，防连接被占死


class DatabaseError(RuntimeError):
    """数据库不可用 / 配置错误。规划 §5.1：配置错误直接失败，不静默回退。"""


class UnitOfWorkClosed(DatabaseError):
    """在已结束的工作单元上继续执行 SQL。"""


def resolve_dsn(explicit: Optional[str] = None) -> str:
    """解析 PostgreSQL DSN。

    优先级：显式入参 > `KG_DATABASE_URL` > 由 `POSTGRES_*` 组装。

    规划 §5.1：旧 `KG_DATABASE` 若仍配置为 SQLite 路径，必须**明确拒绝**并给出
    迁移提示，绝不能把它当数据库路径使用（否则会静默建出本地 SQLite 文件）。
    """
    dsn = (explicit or os.environ.get('KG_DATABASE_URL') or '').strip()
    if dsn:
        _reject_sqlite(dsn)
        return dsn

    # 允许从 compose 的 POSTGRES_* 变量组装，便于本地开发
    app_user = os.environ.get('POSTGRES_APP_USER', '').strip()
    app_password = os.environ.get('POSTGRES_APP_PASSWORD', '').strip()
    db_name = os.environ.get('POSTGRES_DB', '').strip()
    if app_user and app_password and db_name:
        host = os.environ.get('KG_DATABASE_HOST', '127.0.0.1').strip()
        port = os.environ.get('KG_DATABASE_PORT', '5432').strip()
        # libpq 连接串形式：避免口令里的特殊字符破坏 URL 解析
        return (f"host={host} port={port} dbname={db_name} "
                f"user={app_user} password={app_password} "
                f"application_name=knowledge_service")

    legacy = os.environ.get('KG_DATABASE', '').strip()
    hint = (
        '缺少 PostgreSQL 配置：请设置 KG_DATABASE_URL，'
        '或同时提供 POSTGRES_APP_USER / POSTGRES_APP_PASSWORD / POSTGRES_DB。'
    )
    if legacy:
        raise DatabaseError(
            f'{hint}\n'
            f'检测到旧的 KG_DATABASE={legacy!r} —— 本项目已不再支持 SQLite，'
            f'该变量不会被当作数据库路径使用。请改用 KG_DATABASE_URL。'
        )
    raise DatabaseError(hint)


def _reject_sqlite(dsn: str) -> None:
    lowered = dsn.lower()
    if lowered.startswith('sqlite') or lowered.endswith('.sqlite') or '.sqlite' in lowered:
        raise DatabaseError(
            f'KG_DATABASE_URL 指向 SQLite（{dsn!r}），本项目已切换为 PostgreSQL-only。'
            '请提供 postgresql:// 连接串；旧 SQLite 数据不迁移（规划 §1.2）。'
        )
    if not lowered.startswith(('postgres://', 'postgresql://')) and 'dbname=' not in lowered:
        raise DatabaseError(f'无法识别的 PostgreSQL DSN：{dsn!r}')


# ── 行对象：同时支持按列名与按下标取值 ────────────────────────────────────────
# ── 数据库错误类型：驱动无关的单一出处 ────────────────────────────────────────
#
# 应用层不应直接依赖某个驱动的异常类型：一旦换库那些处理器**永远不会触发**，
# 于是
#   1) `discovery_run_store` / `provenance_store` 里"把主键碰撞转成领域冲突"
#      的转换会失效，原始驱动异常泄漏给调用方；
#   2) `retrieval.py` 的关键词降级通道不捕获 DB 错误，一次查询失败会让整个
#      检索抛错，而不是按设计降级。
# 因此在这里给出唯一出处，运行时代码只引用这两个名字，不直接依赖驱动。
DatabaseError = psycopg.Error
IntegrityError = psycopg.errors.IntegrityError


class Row:
    """同时支持 `row['col']` 与 `row[0]` 的行对象。

    现有调用方两种取值方式混用（例如 `fetchone()[0]` 与 `row['payload']`），
    这里统一支持，避免 T03 阶段大规模改写调用点引入回归。
    """

    __slots__ = ('_values', '_names', '_index')

    def __init__(self, values: Sequence[Any], names: Sequence[str]):
        self._values = tuple(values)
        self._names = tuple(names)
        self._index = {name: i for i, name in enumerate(self._names)}

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._index[key]]
            except KeyError:
                raise KeyError(key) from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key) -> bool:
        return key in self._index

    def __repr__(self) -> str:
        pairs = ', '.join(f'{n}={v!r}' for n, v in zip(self._names, self._values))
        return f'<Row {pairs}>'

    def keys(self) -> tuple:
        return self._names

    def get(self, key, default=None):
        return self._values[self._index[key]] if key in self._index else default

    def items(self):
        return zip(self._names, self._values)

    def to_dict(self) -> dict:
        return dict(zip(self._names, self._values))

    # `dict(row)` 在现有代码里被大量使用，这里显式支持
    def __eq__(self, other):
        if isinstance(other, Row):
            return self._names == other._names and self._values == other._values
        if isinstance(other, (tuple, list)):
            return self._values == tuple(other)
        return NotImplemented

    def __hash__(self):  # 保持与元组一致的可哈希性（测试里有把行放进 set 的用法）
        return hash(self._values)


def _row_factory(cursor) -> Callable[[Sequence[Any]], Row]:
    """psycopg3 行工厂：返回 rowmaker（接收 values 的序列，返回 Row）。"""
    names = tuple(column.name for column in (cursor.description or ()))

    def make(values: Sequence[Any]) -> Row:
        return Row(values, names)

    return make


def _load_jsonb_as_text(value):
    """jsonb → 字符串。"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _canonical_timestamp(value):
    """datetime → `utc_now()` 同格式字符串（UTC + 微秒 + Z）。

    读取边界统一规范化，保证 `row['created_at']` 与迁移前**逐字节相同**，
    业务层的时间字符串比较、序列化、测试断言都不需要改。
    """
    return (value.astimezone(timezone.utc)
            .isoformat(timespec='microseconds').replace('+00:00', 'Z'))


class JsonbAsTextLoader(JsonbLoader):
    """jsonb 读取结果还原为字符串。

    现有业务代码对 payload/metadata 一律 `json.loads(str)`；若返回 dict，
    285 个调用点会全部炸掉。故在读取边界还原成字符串。

    注意：jsonb 会**规范化键序并去重**，字符串无法与原样字节一一对应。
    因此**不透明载荷（会被哈希/原样往返的 JSON）在 DDL 中仍用 text**；
    只有确实需要在 SQL 内做 JSON 过滤的列才用 jsonb。
    """

    def load(self, data):
        return _load_jsonb_as_text(super().load(data))


class TimestamptzAsTextLoader(TimestamptzLoader):
    """timestamptz → `utc_now()` 同格式字符串。"""

    def load(self, data):
        return _canonical_timestamp(super().load(data))


_JSONB_OID_CACHE: dict = {}


def get_jsonb_oid(conn) -> Optional[int]:
    """查询并缓存 jsonb 的类型 OID（每个进程只需查一次）。"""
    cached = _JSONB_OID_CACHE.get('oid')
    if cached is not None:
        return cached
    from psycopg.types import TypeInfo

    info = TypeInfo.fetch(conn, 'jsonb')
    if info is None:
        return None
    _JSONB_OID_CACHE['oid'] = info.oid
    return info.oid


def _register_type_adapters(conn) -> None:
    """为单个连接注册读写适配，使 PG 读取行为符合既有业务契约。

    两处适配，都是为了**不改业务代码**（规划 §4「优化不可跨越的底线」）：

    1. `timestamptz` → 规范 ISO 字符串：时间列的读写行为与迁移前逐字节一致。
    2. `jsonb` → 字符串：`json.loads(row['payload'])` 这类调用点无需改动。

    **不覆盖 `str` 的 dumper** —— 那会让所有字符串参数被当成 jsonb，
    破坏普通文本列。写 jsonb 列有两种安全方式：
      (a) 直接传字符串，PostgreSQL 走赋值转换；
      (b) 需要 `@>` / `->` 等运算符时，SQL 里显式写 `%s::jsonb`。
    """
    conn.adapters.register_loader('timestamptz', TimestamptzAsTextLoader)
    oid = get_jsonb_oid(conn)
    if oid is not None:
        conn.adapters.register_loader(oid, JsonbAsTextLoader)


class Session:
    """一次 unit-of-work 的数据库会话（独享一个连接）。

    用法：
        with database.unit_of_work() as session:
            session.execute('SELECT ...')
            with session.transaction():     # 显式事务
                session.execute('INSERT ...')

    未显式开启事务时，psycopg 处于 autocommit 语义（每条语句自成一个事务），
    这与调用方习惯的“默认手动事务”不同 —— 因此 `Repository` 层必须用
    `transaction()` 显式包裹多语句写入（T03/T04 的原子性要求）。
    """

    def __init__(self, conn, *, statement_timeout_ms: int, lock_timeout_ms: int):
        self._conn = conn
        self._depth = 0
        self._closed = False
        self._apply_timeouts(statement_timeout_ms, lock_timeout_ms)

    # ── 生命周期 ──
    def _ensure_open(self) -> None:
        if self._closed:
            raise UnitOfWorkClosed('工作单元已结束，不能继续执行 SQL')

    def _apply_timeouts(self, statement_timeout_ms: int, lock_timeout_ms: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute('SELECT set_config(%s, %s, false)',
                        ('statement_timeout', f'{int(statement_timeout_ms)}ms'))
            cur.execute('SELECT set_config(%s, %s, false)',
                        ('lock_timeout', f'{int(lock_timeout_ms)}ms'))
            cur.execute('SELECT set_config(%s, %s, false)',
                        ('idle_in_transaction_session_timeout',
                         f'{int(DEFAULT_IDLE_TX_TIMEOUT_MS)}ms'))
        self._conn.commit()

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> 'Session':
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ── SQL 执行 ──
    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()):
        self._ensure_open()
        cur = self._conn.cursor(row_factory=_row_factory)
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]):
        self._ensure_open()
        cur = self._conn.cursor(row_factory=_row_factory)
        cur.executemany(sql, params_seq)
        return cur

    def execute_raw(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()):
        """返回原生游标（不包装行）。用于 `COPY` 等特殊路径。"""
        self._ensure_open()
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    @contextmanager
    def transaction(self):
        """显式事务；支持嵌套（内层用 SAVEPOINT）。

        连接本身处于 `autocommit=True`：这样游离的读语句各自成事务，
        不会把连接挂在 idle-in-transaction（规划 §5.1 的池健康要求）。
        代价是最外层事务必须**显式发 BEGIN** —— 这也是这里这么写的原因：
        只有真正处于事务块内，内层的 SAVEPOINT 才合法。

        规划的原子性要求：正式事实、断言、活动、账本、当前投影、outbox
        必须在同一个事务内提交；嵌套场景（服务层调用 store 层）用保存点实现
        局部回滚而不牵连外层。
        """
        self._ensure_open()
        if self._depth == 0:
            self._depth = 1
            with self._conn.cursor() as cur:
                cur.execute('BEGIN')
            try:
                yield self
            except BaseException:
                with self._conn.cursor() as cur:
                    cur.execute('ROLLBACK')
                self._depth = 0
                raise
            else:
                with self._conn.cursor() as cur:
                    cur.execute('COMMIT')
                self._depth = 0
            return

        # 嵌套：使用保存点，名字按深度生成，避免并发/重入冲突
        name = pg_sql.Identifier(f'sp_{self._depth}')
        self._depth += 1
        with self._conn.cursor() as cur:
            cur.execute(pg_sql.SQL('SAVEPOINT {}').format(name))
        try:
            yield self
        except BaseException:
            with self._conn.cursor() as cur:
                cur.execute(pg_sql.SQL('ROLLBACK TO SAVEPOINT {}').format(name))
            self._depth -= 1
            raise
        else:
            with self._conn.cursor() as cur:
                cur.execute(pg_sql.SQL('RELEASE SAVEPOINT {}').format(name))
            self._depth -= 1

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    @property
    def raw(self):
        """底层连接。仅迁移器/诊断使用，业务代码不应直接碰。"""
        return self._conn

    # ── 便捷读取 ──
    def fetchone(self, sql: str, params=()):
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params=()):
        return self.execute(sql, params).fetchall()

    def scalar(self, sql: str, params=()):
        row = self.fetchone(sql, params)
        return None if row is None else row[0]


class Database:
    """进程内唯一的连接池持有者。"""

    def __init__(self, dsn: Optional[str] = None, *,
                 min_size: int = DEFAULT_POOL_MIN,
                 max_size: int = DEFAULT_POOL_MAX,
                 acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
                 statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
                 lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
                 open_pool: bool = True):
        self.dsn = resolve_dsn(dsn)
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
        self._pool = ConnectionPool(
            conninfo=self.dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=acquire_timeout,
            kwargs={'row_factory': _row_factory, 'autocommit': True},
            configure=_register_type_adapters,
            open=open_pool,
            name='knowledge_service',
        )

    # ── 池生命周期 ──
    def open(self, timeout: float = 15.0) -> 'Database':
        self._pool.open(wait=True, timeout=timeout)
        return self

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> 'Database':
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    @contextmanager
    def unit_of_work(self) -> Session:
        """借出一个连接，构成一个工作单元。退出时归还并结束会话。"""
        try:
            conn = self._pool.getconn(timeout=self._pool.timeout)
        except PoolTimeout as exc:
            raise DatabaseError(
                f'连接池已耗尽（{self._pool.max_size} 个连接在 {self._pool.timeout}s 内未释放）'
            ) from exc
        session = Session(conn, statement_timeout_ms=self.statement_timeout_ms,
                          lock_timeout_ms=self.lock_timeout_ms)
        try:
            yield session
        finally:
            session.close()
            try:
                if not conn.closed:
                    conn.rollback()      # 归还前清理未提交事务，避免污染下一个使用者
            finally:
                self._pool.putconn(conn)

    # ── 健康与诊断（供 /health/ready 使用，规划 §9.1）──
    def health(self) -> dict:
        try:
            with self.unit_of_work() as session:
                version = session.scalar('SELECT version()')
                schema = session.scalar(
                    'SELECT COALESCE(MAX(version), 0) FROM schema_migrations')
            return {'ok': True, 'server': version.split(',')[0] if version else None,
                    'schema_version': schema}
        except Exception as exc:      # 健康检查不允许抛出，只报告
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    def __repr__(self) -> str:
        return f'<Database pool={self._pool.min_size}..{self._pool.max_size}>'


# ── 进程级单例（懒加载，便于测试注入）────────────────────────────────────────
_PROCESS_DATABASE: Optional[Database] = None
_PROCESS_LOCK = threading.Lock()


def process_database() -> Database:
    """取得本进程共享的 Database。

    API 与 worker 各自是独立进程，连接总预算 = 每进程 max_size × 实例数
    （规划 §5.1：总连接数按实例数相加，留出维护连接）。
    """
    global _PROCESS_DATABASE
    with _PROCESS_LOCK:
        if _PROCESS_DATABASE is None:
            _PROCESS_DATABASE = Database()
            _PROCESS_DATABASE.open()
        return _PROCESS_DATABASE


def reset_process_database() -> None:
    """关闭并清空进程级单例（测试 teardown / 配置热切换用）。"""
    global _PROCESS_DATABASE
    with _PROCESS_LOCK:
        if _PROCESS_DATABASE is not None:
            _PROCESS_DATABASE.close()
            _PROCESS_DATABASE = None


__all__ = [
    'Database', 'DatabaseError', 'Row', 'Session', 'UnitOfWorkClosed',
    'process_database', 'reset_process_database', 'resolve_dsn',
    'DEFAULT_POOL_MIN', 'DEFAULT_POOL_MAX', 'DEFAULT_ACQUIRE_TIMEOUT',
]
