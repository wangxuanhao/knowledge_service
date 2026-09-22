"""加载本地配置，不记录凭据，也不覆盖 shell 环境变量。

只从仓库根 `.env` 读取（全部 `KG_` 前缀键）；`.env.example` 仅是
参考模板，不作为配置源加载——避免模板里的示例值被当成真实配置。
shell 环境变量优先级最高：已存在的键不会被 `.env` 覆盖。
"""
import os
from pathlib import Path


def load_environment(root=None):
    from dotenv import dotenv_values
    root = Path(root or Path(__file__).resolve().parents[2])
    local = root / '.env'
    loaded = []
    if not local.is_file():
        return loaded
    for key, value in dotenv_values(local, interpolate=False).items():
        if key.startswith('KG_') and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
