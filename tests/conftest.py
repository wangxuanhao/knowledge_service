"""测试全局设置：让单元测试**密闭**，不受开发者本机 `.env` 影响。

为什么需要这个：

`create_app()` 会在装配时调用 `load_environment(ROOT)`，把仓库根 `.env` 载入
进程 —— 这是有意的（任何构造应用的路径都拿到同一套配置，避免「换个启动方式
就连到不同的库」）。但这意味着**测试会继承开发者的 `.env`**，而 `.env` 里
`KG_VECTOR_BACKEND=milvus` / `KG_EMBEDDING_BACKEND=local` / `KG_MILVUS_*` 这类
键会改变检索通道的行为：指向真实 Milvus、或指向还没下载模型的 `data/model`。
实测后果是 12 个用例随机失败（三通道检索返回 0 命中）。

单元测试必须密闭：同一份代码在任何机器上跑出同样的结果。所以这里在最早期
（conftest 导入时，早于任何测试模块）设置 `KG_SKIP_DOTENV=1`，让
`load_environment()` 直接返回。需要真实 PostgreSQL 的 schema 验收
（`tests/service/test_postgres_schema.py`）走的是显式的
`KG_TEST_POSTGRES_DSN` 环境变量，与该开关互不影响。

要在测试里验证 `.env` 加载本身，在用例内 monkeypatch `KG_SKIP_DOTENV` 即可。
"""
import os

# 必须在任何测试模块导入之前设置
os.environ['KG_SKIP_DOTENV'] = '1'

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / 'service'))


# ── 业务测试默认关闭鉴权（只影响 pytest 进程）────────────────────────────────
# 背景：接入认证中间件后，700+ 处既有 API 用例直接发请求会全部 401。这些用例
# 测的是业务功能、本就不验证鉴权。这里在最早期设置 KG_AUTH_DISABLED=1，
# create_app 读到后跳过认证中间件，行为与接入前一致。
# 该变量只在 pytest 进程生效：生产启动脚本/容器从不设置它。
# 鉴权本身由专门的 tests/service/test_auth_rbac.py 显式 auth_disabled=False 覆盖。
os.environ['KG_AUTH_DISABLED'] = '1'

# ── 测试里放开"自助注册"（**只是测试便利**，生产默认关闭）────────────────────
# 生产默认关闭自助注册（api/auth.py: self_registration_allowed，默认 False）：
# 开放注册＝任何能访问服务的人都能开一个能读全部知识的只读账号。
# 但测试里有十几处「注册一个 viewer 拿令牌」的写法，它是最短的造号路径；
# 这一层默认关闭的行为由 test_auth_rbac.py::test_self_registration_is_closed_by_default
# 显式钉住（那个用例 monkeypatch 掉本变量再验 403），所以这里开着不会漏测。
os.environ.setdefault('KG_ALLOW_SELF_REGISTRATION', '1')


@pytest.fixture(scope='session', autouse=True)
def _postgres_session():
    """会话结束清理：删掉槽位 schema 与测试库。"""
    yield
    try:
        import pg_support
        pg_support.teardown()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def postgres_backend(monkeypatch):
    """把测试里的 `Repository(路径)` 接到隔离的 PostgreSQL 槽位。

    为什么用夹具转接而不是改 223 个构造点：

    测试用 `Repository(tmp_path / 'db.sqlite')` 表达两种语义 ——「打开一个库」和
    「重新打开同一个库」。`pg_support.target()` 保留这两点：同一测试内同一路径
    映射到同一槽位（能看到先前写入），不同路径映射到不同槽位（互相隔离）。
    改构造点既费力，又容易在某个用例里把两种语义搅在一起。

    每个测试结束后关闭它打开的连接：PostgreSQL 连接是真实的服务器端资源，
    不像文件句柄那样随对象回收就无声消失，不关会累积到 max_connections。
    """
    import pg_support

    ok, reason = pg_support.available()
    if not ok:
        pytest.skip(f'PostgreSQL 不可用，跳过依赖存储的用例：{reason}')

    from knowledge_service.repository.core import Repository

    original = Repository.__init__
    opened = []

    def patched(self, dsn, *args, **kwargs):
        result = original(self, pg_support.target(dsn), *args, **kwargs)
        opened.append(self)
        return result

    monkeypatch.setattr(Repository, '__init__', patched)
    pg_support.begin_test()
    try:
        yield
    finally:
        for repo in opened:
            try:
                repo.close()
            except Exception:
                pass
