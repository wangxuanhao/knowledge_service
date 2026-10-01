"""对外部模型服务发起 HTTP 调用的统一客户端构造 —— 出口代理策略的唯一出处。

为什么要单独抽出来
------------------

``httpx.Client()`` 默认 ``trust_env=True``，其代理解析是：

    getproxies() = getproxies_environment() or getproxies_registry()

关键在 ``or``：**没有代理环境变量时，它会去读 Windows 注册表里的系统代理**。
本机实测该系统代理（127.0.0.1:7890）对 LLM 端点不可靠：

===============  ==========================================
调用方式          实测结果
===============  ==========================================
直连              HTTP 200/400，0.2s，3/3 稳定
经系统代理        3 次中 2 次 TLS 握手 20s 超时
===============  ==========================================

表现是「有时能跑、有时超时」的偶发故障 —— 最难查的一类，因为它不可复现，
而且错误信息只会显示成「模型提供商不可用」。

顺带一个陷阱：只要 ``NO_PROXY`` 非空，``getproxies_environment()`` 就返回非空，
``or`` 短路，注册表代理**整体**失效（不只是 NO_PROXY 里列出的主机）。
也就是说「设 NO_PROXY」实际等于「全局不读系统代理」，很容易误判。

因此本模块的立场是：**显式决定出口代理，不隐式继承系统设置**。

- 默认：直连。
- 需要代理时：设 ``KG_LLM_PROXY``（例如 ``http://127.0.0.1:7890``）。

``trust_env=False`` 会一并关闭对 ``SSL_CERT_FILE`` / ``.netrc`` 的读取；
本项目的外部调用只依赖 certifi 的系统证书库，不受影响。
"""
import os

import httpx


def outbound_proxy():
    """返回外部调用应当使用的代理；``None`` 表示直连。"""
    return (os.environ.get('KG_LLM_PROXY') or '').strip() or None


def external_client(timeout, **kwargs):
    """构造访问外部模型服务的 ``httpx.Client``。

    所有出口代理决策集中在此，避免各调用点各自 ``httpx.Client()`` 而
    隐式继承系统代理。
    """
    proxy = outbound_proxy()
    if proxy:
        return httpx.Client(timeout=timeout, proxy=proxy, trust_env=False, **kwargs)
    return httpx.Client(timeout=timeout, trust_env=False, **kwargs)
