"""PostgreSQL 连接适配层与方言翻译：让既有 SQL 只需改方言、不用改结构。

## 只做两件全局机械改写

1. 占位符 `?` → `%s`
2. 字面 `%` → `%%`

其余方言点（`INSERT OR IGNORE`、`json_extract`、`rowid`、`record_fts` 查询、
`BEGIN IMMEDIATE` 等）**逐个就地改**，因为每个都需要判断，不适合正则批量替换 ——
批量改一处错就是运行时才炸，而且读代码的人看不出发生过什么。

## 为什么 `%` 必须转义，且必须有参数

实测 psycopg（本机 3.3.6）：

===================================  ==============  ==============
调用形式                              单个 `%`        双 `%%`
===================================  ==============  ==============
`execute(sql)`（无参数）              按字面走        **保持 `%%`**（错）
`execute(sql, ())`（空元组）          **报错**        `%%` → `%` ✓
`execute(sql, params)`（有参数）      **报错**        `%%` → `%` ✓
===================================  ==============  ==============

所以适配层统一：**一律 `execute(sql, params or ())`，且一律把 `%` 转义为 `%%`**。
规则唯一，不存在"这条语句转义了、那条忘了"的灰色地带。

## 残留 SQLite 语法要报错，不能猜

`PRAGMA` / `sqlite_master` / `json_extract` / `OR IGNORE` / `bm25(` / `rowid` 等
一旦漏改，PG 会抛出语焉不详的语法错误，排查成本很高。这里在做之前就先拦下来，
并指明是哪个构造、该改什么。
"""
from __future__ import annotations

import re
import threading
from typing import Any, Iterable, Mapping, Sequence

import psycopg

#: 一旦出现在 SQL 里就说明有地方漏改 —— 逐条列出以便直接定位。
_SQLITE_LEFTOVERS = (
    (r'\bPRAGMA\b', 'PRAGMA 是 SQLite 专有；PG 用 pg_catalog / information_schema'),
    (r'\bsqlite_master\b', 'sqlite_master 是 SQLite 专有；PG 用 pg_catalog.pg_class'),
    (r'\bjson_extract\s*\(', "改用 (col::jsonb->>'key')"),
    (r'\bjson_valid\s*\(', '改用 (col IS JSON)'),
    (r'\bjson_remove\s*\(', '改用 (col::jsonb - \'key\')::text'),
    (r'\bINSERT\s+OR\s+IGNORE\b', '改用 ON CONFLICT ... DO NOTHING'),
    (r'\bINSERT\s+OR\s+REPLACE\b', '改用 ON CONFLICT (...) DO UPDATE'),
    (r'\blast_insert_rowid\s*\(', '改用 RETURNING id'),
    (r'\bbm25\s*\(', 'SQLite FTS5 专有；PG 用 similarity()/ts_rank()'),
    (r'\bmatch\s+record_fts\b|\brecord_fts\s+match\b', 'FTS5 MATCH；PG 用 trgm 的 LIKE/%'),
    (r'\bstrftime\s*\(', '改用 to_char'),
    (r'\bIFNULL\s*\(', '改用 COALESCE'),
    (r'\bGROUP_CONCAT\s*\(', '改用 string_agg'),
    (r'\bGLOB\b', '改用 LIKE / 正则'),
    (r'\bBEGIN\s+(IMMEDIATE|EXCLUSIVE)\b', 'PG 只支持裸 BEGIN'),
    (r'\bAUTOINCREMENT\b', '改用 GENERATED ... AS IDENTITY'),
    (r'\bWITHOUT\s+ROWID\b', 'PG 无此语法'),
    (r'\browid\b', 'PG 无隐式行号；需要显式 identity 列'),
)

_LEFTOVER_PATTERNS = tuple((re.compile(rx, re.I), msg) for rx, msg in _SQLITE_LEFTOVERS)


class DialectError(RuntimeError):
    """SQL 里还有未迁移的 SQLite 构造。"""


def _replace_placeholders(sql: str) -> str:
    """把**引号外**的 `?` 换成 `%s`。

    不能直接 `str.replace`：SQL 里可以合法出现字面问号（如 `LIKE 'why?'`），
    替换进去会把字符串常量改坏，而且这种错误只在特定数据下才暴露。

    引号内的 `%` 仍然要转义 —— psycopg 是对**整条语句**做格式处理的，
    不区分是否在字符串常量里；这正是"先转义 % 、再换占位符"顺序的原因。
    """
    out: list[str] = []
    index = 0
    size = len(sql)
    in_string = False
    while index < size:
        char = sql[index]
        if in_string:
            if char == "'":
                if index + 1 < size and sql[index + 1] == "'":
                    out.append("''")        # 转义后的单引号，字符串继续
                    index += 2
                    continue
                in_string = False
            out.append(char)
            index += 1
            continue
        if char == "'":
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == '-' and index + 1 < size and sql[index + 1] == '-':
            stop = sql.find('\n', index)
            stop = size if stop < 0 else stop
            out.append(sql[index:stop])     # 行注释原样保留
            index = stop
            continue
        if char == '?':
            out.append('%s')
            index += 1
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def translate(sql: str) -> str:
    """把 `?` 占位符风格的 SQL 转成 PostgreSQL 可执行的形式。

    顺序很关键：先转义字面 `%`，再把 `?` 换成 `%s`。反过来会把刚插入的 `%s`
    也转义成 `%%s`，查询会静默返回错误结果。
    """
    problems = ((rx.search(sql), msg) for rx, msg in _LEFTOVER_PATTERNS)
    found = [msg for hit, msg in problems if hit]
    if found:
        head = sql.strip().splitlines()[0][:90]
        raise DialectError(
            'SQL 中还残留 SQLite 构造（需就地修改，不能由适配层猜）：'
            + '；'.join(dict.fromkeys(found))
            + f'    ｜ 语句：{head}')

    escaped = sql.replace('%', '%%')
    return _replace_placeholders(escaped)


class SqliteishConnection:
    """把 psycopg 连接包装成 sqlite3 连接的样子。

    需要提供 sqlite3 的这几个成员，因为 `Repository` 及其各 Store 全程按它们编写：

    * ``execute()`` / ``executemany()``
    * ``commit()`` / ``rollback()``
    * ``in_transaction``
    * ``create_function()`` —— PG 侧用 GUC 表达同一语义，这里是 no-op
    * ``row_factory`` —— 行由连接级 row_factory 产出，赋值被忽略

    连接以 ``autocommit=True`` 建立：事务边界由显式的 BEGIN/COMMIT 控制，
    与 T02 连接层（``connection.py``）的取舍一致，避免 psycopg 在语句外隐式
    开启事务造成 idle-in-transaction。
    """

    def __init__(self, dsn: str, *, statement_timeout_ms: int = 60000,
                 lock_timeout_ms: int = 15000):
        from .connection import _register_type_adapters

        self._conn = psycopg.connect(dsn, autocommit=True)
        # 用过滤内部列的行工厂（见 _public_row_factory 的说明）
        self._conn.row_factory = _public_row_factory
        with self._conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
            cur.execute(f"SET lock_timeout = {int(lock_timeout_ms)}")
        _register_type_adapters(self._conn)

    # ── sqlite3 兼容面 ────────────────────────────────────────────────────
    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()):
        cur = self._conn.cursor()
        cur.execute(translate(sql), params or ())
        return cur

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]):
        cur = self._conn.cursor()
        cur.executemany(translate(sql), seq)
        return cur

    def executescript(self, script: str):
        """按语句切分执行（PG 不支持一次多条），用于 DDL 场景。"""
        cur = self._conn.cursor()
        cur.execute(translate(script))
        return cur

    def commit(self):
        # autocommit 连接下的显式提交：只在处于事务中时发出。
        if self.in_transaction:
            self._conn.cursor().execute('COMMIT')

    def rollback(self):
        if self.in_transaction:
            self._conn.cursor().execute('ROLLBACK')

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def create_function(self, *args, **kwargs):
        """PG 侧用 plpgsql 函数 + GUC 表达同一语义，本方法无需注册。"""

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        # 行对象由连接级 row_factory 决定（同时支持 row['col'] 与 row[0]）。
        # 赋其它行工厂会被静默忽略，这里显式说明以免误解。
        pass

    def close(self):
        self._conn.close()

    @property
    def raw(self):
        return self._conn


_TRANSLATE_CACHE: dict[str, str] = {}
_TRANSLATE_LOCK = threading.Lock()


#: 仅供 SQL 内部排序使用、**不进入应用契约**的列。
#:
#: `seq` 是 0002 迁移加的、仅供内部排序用的 identity 列。业务代码里
#: 大量 `dict(row)`（例如各 Store 的导出与列表方法），若不过滤，导出快照与
#: API 响应会**静默多出一个字段** —— 这是没有经过设计的契约变更。
#: 过滤放在行工厂这一处，调用点零改动，也只有一处需要审计。
_INTERNAL_COLUMNS = frozenset({'seq'})


def _public_row_factory(cursor):
    """行工厂：产出只含对外列的行对象（`row['col']` / `row[0]` 都支持）。"""
    from .connection import Row

    names = tuple(column.name for column in (cursor.description or ()))
    keep = [index for index, name in enumerate(names) if name not in _INTERNAL_COLUMNS]
    public_names = tuple(names[index] for index in keep)

    def make(values: Sequence[Any]) -> Row:
        return Row([values[index] for index in keep], public_names)

    return make


def translate_cached(sql: str) -> str:
    """SQL 文本在进程内高度重复，缓存翻译结果省掉每语句的正则开销。"""
    hit = _TRANSLATE_CACHE.get(sql)
    if hit is not None:
        return hit
    value = translate(sql)
    with _TRANSLATE_LOCK:
        if len(_TRANSLATE_CACHE) < 4096:
            _TRANSLATE_CACHE[sql] = value
    return value
