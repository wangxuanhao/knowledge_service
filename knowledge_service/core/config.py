"""加载本地配置，不记录凭据，也不覆盖 shell 环境变量。

只从仓库根 `.env` 读取；`.env.example` 仅是参考模板，不作为配置源加载
——避免模板里的示例值被当成真实配置。shell 环境变量优先级最高：
已存在的键不会被 `.env` 覆盖。

读取范围（两段，都是有意的）：
  1. 全部 `KG_` 前缀键：应用自身的配置。
  2. 仅 `POSTGRES_DB` / `POSTGRES_APP_USER` / `POSTGRES_APP_PASSWORD` 三个键：
     用于在未显式提供 `KG_DATABASE_URL` 时组装应用角色的 DSN。
     **刻意不加载 `POSTGRES_PASSWORD`（超级用户口令）** —— 应用角色无 DDL 权限，
     超级用户口令不应进入应用进程环境（规划 §5.1 / §9.1 最小权限）。
"""
import os
from pathlib import Path

# 允许从 .env 进入**应用进程**的 POSTGRES_ 键（白名单，不放行超级用户口令）
POSTGRES_APP_KEYS = frozenset({
    'POSTGRES_DB',
    'POSTGRES_APP_USER',
    'POSTGRES_APP_PASSWORD',
})

# 只有**迁移器**这类需要 DDL 权限的命令行工具才放行 owner 凭据
POSTGRES_ADMIN_KEYS = frozenset({
    'POSTGRES_USER',
    'POSTGRES_PASSWORD',
})


def load_environment(root=None, include_admin=False, keys=None):
    """加载 `.env`。

    `include_admin=True` 仅由迁移器（`repository/migrate.py`）使用：
    建表/建索引需要 owner 权限，而应用进程不应持有超级用户口令。
    默认 False，保证 API / worker 进程里不存在 `POSTGRES_PASSWORD`。

    `keys` 只加载指定的一组键，用于「**只要连接信息，不要业务开关**」的调用方
    （目前是测试基建 `tests/service/pg_support.py`）。显式给出 `keys` 时会忽略
    `KG_SKIP_DOTENV`：该开关的用意是"不让本机 `.env` 改变**业务行为**"，
    而数据库连接信息属于基础设施 —— 测试必须拿到真实凭据才能跑，
    同时不能因为加载 `.env` 而把 `KG_VECTOR_BACKEND` 之类业务开关带进来。
    """
    # `KG_SKIP_DOTENV=1` 整体跳过加载：供**测试套件**保持密闭（见 tests/conftest.py）。
    # 单元测试不能随开发者本机 .env 内容改变行为 —— 一旦 .env 注入
    # KG_VECTOR_BACKEND / KG_EMBEDDING_BACKEND，检索通道会指向真实 Milvus
    # 或缺失的模型目录，表现为随机失败。
    if keys is None and os.environ.get('KG_SKIP_DOTENV', '').strip() == '1':
        return []

    from dotenv import dotenv_values
    root = Path(root or Path(__file__).resolve().parents[2])
    local = root / '.env'
    loaded = []
    if not local.is_file():
        return loaded
    if keys is not None:
        allowed = frozenset(keys)
        def accepted(key):
            return key in allowed
    else:
        allowed = POSTGRES_APP_KEYS | (POSTGRES_ADMIN_KEYS if include_admin else frozenset())
        def accepted(key):
            return key.startswith('KG_') or key in allowed
    for key, value in dotenv_values(local, interpolate=False).items():
        if not value or key in os.environ:
            continue
        if accepted(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded
