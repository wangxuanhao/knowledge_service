"""加载本地配置，不记录凭据，也不覆盖 shell 环境变量。"""
import os
from pathlib import Path


def load_environment(root=None):
    from dotenv import dotenv_values
    root = Path(root or Path(__file__).resolve().parents[1])
    local = root / '.env'
    reference = root / '.env.example'
    loaded = []
    # 兼容用户 .env.example 中已有的 Neo4j 配置。
    # 该文件只接受 Neo4j 相关键，不接受示例模型/提供商值。
    for path, prefix in ((local, 'KG_'), (reference, 'KG_NEO4J_')):
        if not path.is_file():
            continue
        for key, value in dotenv_values(path, interpolate=False).items():
            if key.startswith(prefix) and value and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded
