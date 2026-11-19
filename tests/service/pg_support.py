"""PostgreSQL 测试隔离：schema 槽位池 + 事务内 DELETE 重置。

## 隔离方案的本机实测（22 表 + 61 索引 + 9 触发器）

========================================  ===========
`CREATE DATABASE ... TEMPLATE`             约 800ms
`CREATE SCHEMA` + 重放 DDL                 约 560ms
`TRUNCATE` 全部 22 表                      约 500ms
`DELETE FROM` 全部 22 表（单事务）          约 9ms
==========================================

测试里有 221 个 `Repository(...)` 构造点。按前三种方案各自要再加 100~180s，
而整套测试当前只用 68s —— 直接拖垮反馈速度。所以：

* **建 schema 只做常数次**（槽位池），DDL 的约 560ms 被摊薄成一次性成本；
* **每次测试的重置用 DELETE**（约 9ms），不用 TRUNCATE。

## 为什么 TRUNCATE 这么慢

不是 fsync（实测关掉 `synchronous_commit` 无效），而是**按关系计费**：
单表约 15ms，22 表约 330~500ms；改走 DELETE 就避开了文件级操作。

## 重置为什么需要 `session_replication_role = replica`

`ontology_history_repairs` 的 DELETE 被不可变触发器**无条件拒绝**（连维护开关
也不放行，见 `0001_core.sql` 的 `ontology_history_repairs_delete_immutable`），
而 `ontology_operations` / `ontology_review_decisions` 走 GUC 开关。逐个区分太脆，
`replica` 一次性关掉用户触发器，22 张表走同一条清空路径。这需要超管权限 ——
测试进程本来就要建库建 schema，已经有 DDL 权限。

## 隔离怎么表达

隔离信息编码进 DSN 的 `search_path`：

    postgresql://…?options=-csearch_path%3Dkgtest_01,public

好处是**不依赖任何全局状态**：连接一旦建立看到的就是自己的 schema，不需要测试
代码记得去 `SET`，也不会因为连接复用而串到别的测试。

`public` 必须留着 —— `pg_trgm` 的操作符类 `gin_trgm_ops` 装在 `public`，
去掉它会直接让 `CREATE INDEX ... gin_trgm_ops` 报 "operator class does not exist"。

## 同一个测试内的"同路径"语义

测试用 `Repository(同一路径)` 表达"重新打开同一个库"（例如验证升级/重开），
用不同路径表达"两个互相隔离的库"。槽位以**路径**为键、以测试为周期回收，
因此这两类语义都保持不变。
"""
from __future__ import annotations

import os
import threading
from uuid import uuid4

import psycopg
from psycopg import sql

from knowledge_service.repository.migrate import apply_migrations

TEST_DB = os.environ.get('KG_TEST_POSTGRES_DB') or f'knowledge_test_{uuid4().hex}'
POOL_SIZE = 4      # 单个测试内可能同时需要的库数量；绝大多数测试只用 1 个

# 迁移台账：属于"环境"而不是"测试数据"，重置时必须保留。
_BOOKKEEPING_TABLES = frozenset({'schema_migrations'})

_lock = threading.RLock()
_dsn_cache: dict[str, str] = {}
_admin_conn = None
_owned_database: tuple[str, int, psycopg.Connection] | None = None  # name, OID, maintenance connection
_slots: dict[int, tuple[str, list[str]]] = {}   # 槽位号 -> (schema, 表名)
_binding: dict[str, int] = {}                   # 本次测试内 路径 -> 槽位号
_ensured = False


def available() -> tuple[bool, str]:
    """测试基建是否可用。返回 `(是否可用, 不可用原因)`。

    只使用显式配置的测试实例，不读取仓库 `.env`。测试库必须由本进程新建。
    """
    try:
        ensure_test_database()
        return True, ''
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def admin_dsn(dbname: str | None = None) -> str:
    """测试基建用的管理员 DSN（结果缓存，避免每次重解析 .env）。

    测试要建库/建 schema/重置，这些都是 DDL，应用角色 `knowledge_app` 没有权限，
    因此这里用管理员凭据 —— 仅限测试进程，不进应用运行路径。
    """
    if dbname is not None:
        return psycopg.conninfo.make_conninfo(admin_dsn(), dbname=dbname)
    key = TEST_DB
    cached = _dsn_cache.get(key)
    if cached:
        return cached
    explicit = os.environ.get('KG_TEST_POSTGRES_ADMIN_DSN')
    if explicit:
        params = psycopg.conninfo.conninfo_to_dict(explicit)
        if not ((params.get('host') or params.get('hostaddr')) and params.get('port')):
            raise RuntimeError('KG_TEST_POSTGRES_ADMIN_DSN must specify the test host and port')
        configured = os.environ.get('KG_TEST_POSTGRES_DB')
        if configured and params.get('dbname') not in (None, configured):
            raise RuntimeError('KG_TEST_POSTGRES_DB conflicts with KG_TEST_POSTGRES_ADMIN_DSN')
        params['dbname'] = params.get('dbname') or TEST_DB
    else:
        keys = dict(host='KG_DATABASE_HOST', port='KG_DATABASE_PORT',
                    user='POSTGRES_USER', password='POSTGRES_PASSWORD')
        if not all(os.environ.get(key) for key in keys.values()):
            raise RuntimeError('Configure a dedicated PostgreSQL test host, port and credentials, '
                               'or set KG_TEST_POSTGRES_ADMIN_DSN')
        params = {option: os.environ[key] for option, key in keys.items()}
        params['dbname'] = TEST_DB
    value = psycopg.conninfo.make_conninfo(**params)
    _dsn_cache[key] = value
    return value


def _conn():
    """复用的管理连接（建一条连接约 15ms，不值得每次重开）。"""
    global _admin_conn
    if _admin_conn is None or _admin_conn.closed:
        if _owned_database is None:
            raise RuntimeError('No test database created by this process')
        name, oid, _ = _owned_database
        conn = psycopg.connect(admin_dsn(), autocommit=True)
        try:
            if conn.info.dbname != name or _database_oid(conn, name) != oid:
                raise RuntimeError('Test DSN no longer points to the created database')
        except BaseException:
            conn.close()
            raise
        _admin_conn = conn
    return _admin_conn


def _database_oid(conn, name):
    row = conn.execute('SELECT oid FROM pg_database WHERE datname = %s', (name,)).fetchone()
    return row[0] if row else None


def ensure_test_database() -> None:
    """Never adopt an existing database, even if its name looks like a test DB."""
    global _ensured, _owned_database
    with _lock:
        if _ensured:
            return
        if _owned_database is None:
            name = psycopg.conninfo.conninfo_to_dict(admin_dsn())['dbname']
            if not 0 < len(name.encode('utf-8')) <= 63:
                raise ValueError('Test database name must fit in a PostgreSQL identifier')
            admin = psycopg.connect(admin_dsn('postgres'), autocommit=True)
            try:
                try:
                    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                except psycopg.errors.DuplicateDatabase as exc:
                    raise RuntimeError('Test database already exists; choose an unused name') from exc
                oid = _database_oid(admin, name)
                if oid is None:
                    raise RuntimeError('Could not verify identity of created test database')
                # Track ownership even if setup fails. Never reconnect to a
                # fallback maintenance endpoint during cleanup.
                _owned_database = name, oid, admin
            except BaseException:
                admin.close()
                raise
        name, _, admin = _owned_database
        # Both extensions must be visible from every slot's search_path.
        _conn().execute('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public')
        _conn().execute('CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public')
        admin.execute(sql.SQL('ALTER DATABASE {} SET synchronous_commit = off').format(sql.Identifier(name)))
        _ensured = True


def _create_slot(index: int) -> tuple[str, list[str]]:
    schema = f'kgtest_{index:02d}'
    conn = _conn()
    conn.execute(f'CREATE SCHEMA "{schema}"')
    # 用**同一个迁移器**建表，而不是自己重放 DDL：建表逻辑只有一处，
    # 新迁移会自动进入测试环境，不会再出现"测试环境少一个迁移"。
    # _slot_dsn 把 search_path 放进 DSN，迁移与后续连接落在同一个槽位。
    apply_migrations(_slot_dsn(schema), verbose=False)
    tables = [row[0] for row in conn.execute(
        'SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY 1',
        (schema,)).fetchall()]
    if not tables:
        raise RuntimeError(f'槽位 {schema} 建表后仍为空，迁移可能没有生效')
    return schema, tables


def _reset_slot(schema: str, tables: list[str]) -> None:
    """把一个槽位的**业务数据**清空；交给下个测试前必须做。

    `schema_migrations` 不清：它是迁移台账，不是测试数据。清掉之后
    `Repository` 的 schema 校验会（正确地）认为迁移没跑过而拒绝启动。
    """
    conn = _conn()
    conn.execute('BEGIN')
    try:
        conn.execute(f'SET LOCAL search_path TO "{schema}", public')
        # 关掉用户触发器：否则 ontology_history_repairs（无条件拒绝 DELETE）
        # 会让整次重置失败；operations/decisions 另走 GUC 开关，replica 一并覆盖。
        conn.execute('SET LOCAL session_replication_role = replica')
        for table in tables:
            if table in _BOOKKEEPING_TABLES:
                continue
            conn.execute(f'DELETE FROM "{table}"')
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise


def _slot_dsn(schema: str) -> str:
    return psycopg.conninfo.make_conninfo(
        admin_dsn(), options=f'-csearch_path={schema},public')


def target(path) -> str:
    """把测试给的"库路径"映射成一个隔离的 PostgreSQL DSN。

    同一个测试内同一路径 → 同一槽位（保留"重新打开同一个库"的语义）；
    不同路径 → 不同槽位（保留"两个隔离的库"的语义）。
    """
    with _lock:
        ensure_test_database()
        key = str(path)
        bound = _binding.get(key)
        if bound is not None:
            return _slot_dsn(_slots[bound][0])

        taken = set(_binding.values())
        capacity = max(POOL_SIZE, max(_slots, default=0))
        index = next((i for i in range(1, capacity + 1) if i not in taken), None)
        if index is None:
            # 该测试同时需要多于 POOL_SIZE 个库：扩容而不是复用 —— 复用会让
            # 两个本应互相隔离的库串数据，那是最难查的一类测试失败。
            index = max(_slots) + 1
        if index not in _slots:
            _slots[index] = _create_slot(index)
        schema, tables = _slots[index]
        _reset_slot(schema, tables)
        _binding[key] = index
        return _slot_dsn(schema)


def begin_test() -> None:
    """每个测试开始前调用：释放全部槽位绑定。"""
    with _lock:
        _binding.clear()


def teardown() -> None:
    """Drop only a database this process created and can still identify."""
    global _admin_conn, _ensured, _owned_database
    with _lock:
        owned = _owned_database
        try:
            if _admin_conn is not None and not _admin_conn.closed:
                _admin_conn.close()
            if owned is not None:
                name, oid, admin = owned
                if _database_oid(admin, name) == oid:
                    # Report leaked clients instead of force-disconnecting them.
                    admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        finally:
            if owned is not None:
                owned[2].close()
            _owned_database = None
            _admin_conn = None
            _slots.clear()
            _binding.clear()
            _ensured = False
