"""编号 SQL 迁移执行器（T02）。

规划依据：`docs/2026-09-30-PostgreSQL替换与本体知识服务优化规划.md` §5.1。

设计要点（逐条对应规划）：
  1. `repository/migrations/NNNN_name.sql` 编号迁移 + `schema_migrations`
     版本/校验和表；**已应用迁移的内容被改动即报错**（防止有人在生产偷偷
     改一条旧迁移，导致同一版本号在不同环境对应不同 schema）。
  2. 使用 advisory lock 串行迁移：多个实例/多个人同时执行时只有一个真正跑，
     其余等待后识别为「已应用」而直接返回。
  3. 幂等：重复执行是安全的；单条迁移在**一个事务内**执行，失败整体回滚，
     绝不留下半套 schema。
  4. 迁移使用**迁移角色**（owner/超级用户）凭据；应用角色无 DDL 权限，见
     `docker/postgres/initdb/10-app-role.sh`。
  5. 迁移 SQL 纳入安装包 package-data，源码目录之外安装 wheel 后同样可用
     （规划 §5.1）。
  6. **API 启动不执行迁移**：只调用 `check_schema()` 校验兼容版本，避免所有
     实例同时建表。

用法：
    python -m knowledge_service.repository.migrate            # 应用待执行迁移
    python -m knowledge_service.repository.migrate --check    # 只校验，不改动
    python -m knowledge_service.repository.migrate --status   # 打印版本状态
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import psycopg

from ..core.config import load_environment

# 迁移文件所在目录（随包分发，见 pyproject.toml 的 package-data）
MIGRATIONS_DIR = Path(__file__).resolve().parent / 'migrations'

# advisory lock 键：固定值，保证所有迁移者竞争同一把锁。
# 取值无业务含义，只要全局唯一即可；这里是 "kschema" 的十六进制。
ADVISORY_LOCK_KEY = 0x6B736368656D61

_FILENAME = re.compile(r'^(\d{4})_([A-Za-z0-9_\-]+)\.sql$')


class MigrationError(RuntimeError):
    """迁移失败（编号冲突、校验和不符、SQL 执行失败等）。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def checksum(self) -> str:
        """内容校验和：用**归一化换行**后的字节计算。

        归一化是为避免同一份文件在 Windows/Linux 检出后 CRLF 差异导致
        校验和不同 —— 那种差异没有语义，不该让迁移失败。
        """
        raw = self.path.read_bytes().replace(b'\r\n', b'\n')
        return hashlib.sha256(raw).hexdigest()

    def sql(self) -> str:
        return self.path.read_text(encoding='utf-8').replace('\r\n', '\n')


def discover_migrations(directory: Optional[Path] = None) -> List[Migration]:
    """按编号升序发现迁移文件；编号重复或文件名不合规直接报错。"""
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationError(f'迁移目录不存在：{directory}')

    found: List[Migration] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.glob('*.sql')):
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(
                f'迁移文件名不符合 NNNN_name.sql：{path.name}')
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(
                f'迁移编号重复：{version} 同时出现在 {seen[version]} 与 {path.name}')
        seen[version] = path.name
        found.append(Migration(version=version, name=match.group(2), path=path))

    if not found:
        raise MigrationError(f'迁移目录中没有 .sql 文件：{directory}')
    # 编号必须从 1 开始连续，缺号意味着有人漏提交了文件
    versions = [m.version for m in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f'迁移编号不连续：{versions}')
    return found


def _ensure_bookkeeping(conn) -> None:
    """建立迁移台账表。"""
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     integer PRIMARY KEY,
                name        text NOT NULL,
                checksum    text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
        ''')


def _applied(conn) -> dict[int, tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute('SELECT version, name, checksum FROM schema_migrations')
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def apply_migrations(dsn: str, *, directory: Optional[Path] = None,
                     verbose: bool = True,
                     allow_checksum_mismatch: bool = False) -> dict:
    """应用全部待执行迁移。返回 {'applied': [...], 'current': n}。

    整个过程持有 advisory lock；每条迁移在自己的事务里执行，做到
    「要么这条迁移整体生效，要么完全没发生」。

    ``allow_checksum_mismatch`` 只在**已确认当前文件为权威**的历史遗留场景使用：
    台账里记录的 checksum 与当前文件不一致时，默认报错；显式放行后则按当前文件
    重写台账 checksum 再继续（CLI 的 ``--rewrite-checksums`` 对应此开关）。
    """
    migrations = discover_migrations(directory)

    # autocommit=True：迁移里显式 BEGIN/COMMIT，避免驱动隐式开事务干扰
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(%s)', (ADVISORY_LOCK_KEY,))
        try:
            _ensure_bookkeeping(conn)
            applied = _applied(conn)

            # 已应用的迁移内容一旦变化，默认必须报错而不是默默接受。
            # 但历史遗留可能导致台账 checksum 与当前文件不一致（例如切换后端时迁移文件
            # 在应用之后又被编辑过）。此时需要人工确认：显式 allow_checksum_mismatch
            # 才按当前文件重写台账，否则给出「新旧 checksum + 补救命令」后拒绝。
            for migration in migrations:
                if migration.version in applied:
                    name, checksum = applied[migration.version]
                    if checksum != migration.checksum:
                        if allow_checksum_mismatch:
                            with conn.cursor() as cur:
                                cur.execute(
                                    'UPDATE schema_migrations SET checksum=%s '
                                    'WHERE version=%s',
                                    (migration.checksum, migration.version))
                            if verbose:
                                print(
                                    f'  ! 迁移 {migration.version:04d}（{name}）内容已变化，'
                                    f'已按当前文件重写 checksum：'
                                    f'{checksum[:12]} → {migration.checksum[:12]}')
                        else:
                            raise MigrationError(
                                f'迁移 {migration.version:04d}（{name}）已应用，但内容已变化：\n'
                                f'  台账 checksum={checksum[:12]}…，'
                                f'当前文件={migration.checksum[:12]}…。\n'
                                '迁移文件一经应用不可修改；若确属历史遗留、且已确认当前文件'
                                '为权威，请用 `--rewrite-checksums` 重写台账后继续。')

            pending = [m for m in migrations if m.version not in applied]
            if verbose:
                print(f'当前版本 {max(applied) if applied else 0}，'
                      f'待执行 {len(pending)} 条')

            for migration in pending:
                if verbose:
                    print(f'  → 应用 {migration.version:04d}_{migration.name} ...',
                          end='', flush=True)
                try:
                    with conn.cursor() as cur:
                        cur.execute('BEGIN')
                        cur.execute(migration.sql())
                        cur.execute(
                            'INSERT INTO schema_migrations (version, name, checksum) '
                            'VALUES (%s, %s, %s)',
                            (migration.version, migration.name, migration.checksum))
                        cur.execute('COMMIT')
                except Exception as exc:
                    # 事务已由 BEGIN 开启；失败时显式回滚，保证不残留半套 schema
                    try:
                        with conn.cursor() as cur:
                            cur.execute('ROLLBACK')
                    except Exception:
                        pass
                    raise MigrationError(
                        f'迁移 {migration.version:04d}_{migration.name} 失败：'
                        f'{type(exc).__name__}: {exc}') from exc
                if verbose:
                    print(' ok')

            current = max(_applied(conn)) if _applied(conn) else 0
        finally:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(%s)', (ADVISORY_LOCK_KEY,))

    return {'applied': [m.version for m in pending], 'current': current}


def check_schema(dsn: str, *, directory: Optional[Path] = None) -> dict:
    """只读校验：不执行任何 DDL，供 API 启动时判断 schema 是否兼容。

    API 不该自己建表（规划 §5.1「API 启动只检查兼容版本，不让所有实例同时建表」），
    因此这里只回答「期望版本是多少、实际版本是多少、是否落后」。
    """
    expected = max(m.version for m in discover_migrations(directory))
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            exists = cur.fetchone()[0] is not None
            actual = 0
            if exists:
                cur.execute('SELECT COALESCE(MAX(version), 0) FROM schema_migrations')
                actual = cur.fetchone()[0]
    return {'expected': expected, 'actual': actual,
            'ok': actual == expected, 'pending': actual < expected,
            'migrations_table': bool(exists)}


def resolve_migration_dsn(explicit: Optional[str] = None) -> str:
    """迁移角色 DSN。

    优先 `KG_MIGRATION_DATABASE_URL`（规划 §5.1：迁移角色单独使用），
    其次由 `POSTGRES_USER`/`POSTGRES_PASSWORD` 组装（owner/超级用户）。
    刻意**不回退到应用角色 DSN** —— 应用角色没有 DDL 权限，回退只会给出
    难以理解的权限错误，不如直接给出明确提示。
    """
    candidates = [
        explicit,
        os.environ.get('KG_MIGRATION_DATABASE_URL'),
        os.environ.get('KG_DATABASE_URL'),
    ]
    for candidate in candidates:
        if candidate and candidate.strip():
            dsn = candidate.strip()
            if dsn.lower().startswith(('postgres://', 'postgresql://')) or 'dbname=' in dsn:
                return dsn
            raise MigrationError(f'迁移 DSN 必须是 PostgreSQL 连接串：{dsn!r}')

    user = os.environ.get('POSTGRES_USER', '').strip()
    password = os.environ.get('POSTGRES_PASSWORD', '').strip()
    db_name = os.environ.get('POSTGRES_DB', '').strip()
    if user and password and db_name:
        host = os.environ.get('KG_DATABASE_HOST', '127.0.0.1').strip()
        port = os.environ.get('KG_DATABASE_PORT', '5432').strip()
        return (f'host={host} port={port} dbname={db_name} user={user} '
                f'password={password} application_name=knowledge_service_migrate')

    raise MigrationError(
        '缺少迁移凭据：请设置 KG_MIGRATION_DATABASE_URL，'
        '或提供 POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB。'
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='knowledge_service PostgreSQL 迁移器')
    parser.add_argument('--dsn', help='迁移角色 DSN（覆盖环境变量）')
    parser.add_argument('--check', action='store_true',
                        help='只校验 schema 版本，不执行任何 DDL')
    parser.add_argument('--status', action='store_true',
                        help='打印版本与校验和状态')
    parser.add_argument('--rewrite-checksums', action='store_true',
                        help='已应用迁移内容变化时，按当前文件重写台账 checksum'
                             '（仅在确认当前文件为权威后使用）')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(list(argv) if argv is not None else None)

    # 迁移需要 owner 凭据，故显式放行 POSTGRES_USER/POSTGRES_PASSWORD；
    # 应用进程走 include_admin=False 的默认路径，拿不到超级用户口令。
    load_environment(include_admin=True)

    try:
        dsn = resolve_migration_dsn(args.dsn)
    except MigrationError as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 2

    try:
        if args.check or args.status:
            state = check_schema(dsn)
            applied = _applied_versions(dsn)
            print(f'schema 版本：实际={state["actual"]} 期望={state["expected"]} '
                  f'{"一致" if state["ok"] else "不一致（需执行迁移）"}')
            if args.status:
                for version, (name, checksum) in sorted(applied.items()):
                    print(f'  {version:04d}  {name}  {checksum[:12]}')
            return 0 if state['ok'] else 1

        result = apply_migrations(dsn, verbose=not args.quiet,
                                  allow_checksum_mismatch=args.rewrite_checksums)
        if not args.quiet:
            print(f'完成：当前版本 {result["current"]}，'
                  f'本次应用 {len(result["applied"])} 条')
        return 0
    except MigrationError as exc:
        print(f'迁移失败：{exc}', file=sys.stderr)
        return 1


def _applied_versions(dsn: str) -> dict[int, tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                return {}
        return _applied(conn)


if __name__ == '__main__':
    raise SystemExit(main())
