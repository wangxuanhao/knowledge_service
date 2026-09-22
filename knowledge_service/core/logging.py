"""统一日志配置：终端 + 文件落盘（data/log/）。

配置父 logger ``knowledge_service`` 挂 FileHandler + StreamHandler；所有子 logger
（如 knowledge_service.timing / .qa）靠 propagate 继承，避免重复 handler。
文件按天滚动、保留 30 天；文件存全量 DEBUG，终端级别由 KG_LOG_LEVEL 控制
（默认 INFO，设 DEBUG 时终端也打印详细逻辑）。

幂等：tests 会在一个进程里多次 build app，不能叠加 handler；uvicorn 也不能
把每行打两遍。
"""
import logging
import os

_HANDLER_MARK = '_knowledge_service_timing_handler'


def configure_logging(stream=None):
    """配置统一日志：终端 + 文件落盘（data/log/），多级别（DEBUG/INFO/WARNING/ERROR）。"""
    import logging.handlers
    from pathlib import Path
    parent = logging.getLogger('knowledge_service')
    level = getattr(logging, os.environ.get('KG_LOG_LEVEL', 'INFO').upper(), logging.INFO)
    parent.setLevel(logging.DEBUG)  # 父 logger 放行所有级别，由各 handler 决定实际输出
    parent.propagate = False
    if any(getattr(handler, _HANDLER_MARK, False) for handler in parent.handlers):
        return parent

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)-7s %(name)s · %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # 终端 handler：级别受 KG_LOG_LEVEL 控制（默认 INFO，开发时看关键步骤）
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(fmt)
    setattr(stream_handler, _HANDLER_MARK, True)
    parent.addHandler(stream_handler)

    # 文件 handler：落盘 data/log/service.log，存全量 DEBUG（排查问题时能看清完整链路）
    try:
        # 本文件位于 knowledge_service/core/，仓库根是其上两级
        log_dir = Path(__file__).resolve().parents[2] / 'data' / 'log'
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / 'service.log', when='midnight', backupCount=30, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        setattr(file_handler, _HANDLER_MARK, True)
        parent.addHandler(file_handler)
    except Exception:
        pass  # 落盘失败不阻断服务启动（至少终端可见）

    return parent