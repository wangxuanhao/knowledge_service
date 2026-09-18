"""两层诊断机制；不涉及提示词、响应体或凭据。

1. 任务内阶段追踪（``event`` / ``reporting`` / ``stage`` / ``redact``）。
   正在运行的摄取任务把一个回调推入 ContextVar，使每一步都落到任务回执和作业日志上。
2. 读路径阶段计时（``timed`` / ``warn_scan`` / ``configure_logging``）。
   读端点不在任务内运行，因此其成本改由日志记录。为什么读路径需要独立通道，
   参见 ``timed`` 上的说明。
"""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import re
import time

_report = ContextVar('ingest_report', default=None)


def redact(value):
    text = str(value)
    for name, secret in os.environ.items():
        if secret and len(secret)>3 and any(k in name.upper() for k in ('KEY','TOKEN','PASSWORD','SECRET')):
            text = text.replace(secret, '<REDACTED>')
    return re.sub(r'(?i)(bearer\s+|api[_-]?key[=:]\s*)[^\s,;]+', r'\1<REDACTED>', text)


def event(message, percent=None):
    callback = _report.get()
    if callback:
        callback(redact(message), percent)


@contextmanager
def reporting(callback):
    token = _report.set(callback)
    try:
        yield
    finally:
        _report.reset(token)


@contextmanager
def stage(name, percent=None):
    started = time.monotonic()
    event(name+' · 开始', percent)
    try:
        yield
    except Exception as exc:
        # 保留异常类型以维持因果链，不暴露供应商返回体。
        chain=[]; current=exc
        while current is not None and len(chain)<5:
            chain.append(type(current).__name__)
            current=current.__cause__
        event(f'{name} · 失败 · {time.monotonic()-started:.1f}s · '+ ' ← '.join(chain))
        raise
    else:
        event(f'{name} · 完成 · {time.monotonic()-started:.1f}s')


# ── Read-path stage timing ────────────────────────────────────────────────────
# 为什么需要第二个通道：几乎每个读端点都汇聚到 KnowledgeService.scoped，
# 它会为项目的*每个*系统版本请求 Repository.query，然后才在 Python 中过滤。
# 因此成本与整个项目成正比，而不是与答案成正比——对单个种子节点的 POST /subgraph
# 只返回约 6 KB，却仍要付出全量扫描外加每个载荷的 JSON 解码。
# GET /timeline 看似很快，只因为它是纯 SQL 聚合、从不触碰 scoped。
# 这种不匹配从外部不可见，因此每条读路径现在都会上报它读了多少、花了多久。
#
# 用 KG_LOG_LEVEL=INFO 启用（通过 python -m knowledge_service 启动时的默认值）；
# 设 KG_SLOW_MS=200 可只查看慢于 200 ms 的阶段。
LOG = logging.getLogger('knowledge_service.timing')
_HANDLER_MARK = '_knowledge_service_timing_handler'

# 如此规模的扫描意味着该请求为整个项目买单；值得给出告警。
SCAN_WARNING_ROWS = 2000


def configure_logging(stream=None):
    """配置统一日志：终端 + 文件落盘（data/log/），多级别（DEBUG/INFO/WARNING/ERROR）。

    配置父 logger ``knowledge_service`` 挂 FileHandler + StreamHandler；所有子 logger
    （如 knowledge_service.timing / .qa）靠 propagate 继承，避免重复 handler。
    文件按天滚动、保留 30 天；文件存全量 DEBUG，终端级别由 KG_LOG_LEVEL 控制
    （默认 INFO，设 DEBUG 时终端也打印详细逻辑）。

    幂等：tests 会在一个进程里多次 build app，不能叠加 handler；uvicorn 也不能
    把每行打两遍。
    """
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
        log_dir = Path(__file__).resolve().parents[1] / 'data' / 'log'
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


# timed 日志字段的中文映射（把内部英文字段名翻译成中文，日志更可读）
_FIELD_ZH = {
    'rows': '扫描', 'kept': '保留', 'raw': '原始', 'live': '存活', 'decoded': '解码',
    'embeddings': '向量', 'kinds': '类型', 'filtered': '过滤', 'seeded': '种子',
    'hops': '跳数', 'candidates': '候选', 'drafts': '草案', 'limit': '上限',
    'kind': '类型', 'scoped': '按项目', 'vectors': '向量',
}


@contextmanager
def timed(label, level=logging.INFO, **fields):
    """给一段读路径计时；额外关键字字段会附加到日志行。

    例子：
        with timed('存储查询', embeddings=True) as record:
            rows = fetch()
            record['rows'] = len(rows)   # 日志会打印「存储查询 · 12.3 ms · 扫描=453」
    """
    started = time.perf_counter()
    record = dict(fields)
    try:
        yield record
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        try:
            floor = max(0.0, float(os.environ.get('KG_SLOW_MS', '0')))
        except ValueError:
            floor = 0.0
        if elapsed >= floor:
            detail = ' '.join(f'{_FIELD_ZH.get(key, key)}={value}' for key, value in record.items())
            LOG.log(level, '%s · %.1f ms%s', label, elapsed, f' · {detail}' if detail else '')


def warn_scan(label, rows, threshold=SCAN_WARNING_ROWS):
    """标记规模表明该请求为整个项目买单的读取。"""
    if rows >= threshold:
        LOG.warning(
            '%s · 本次读取 %d 行（阈值 %d）· 该查询没有 kinds/limit 下推，'
            '成本与项目全量版本历史成正比，而不是与返回结果成正比',
            label, rows, threshold)
